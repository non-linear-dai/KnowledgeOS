# frozen_string_literal: true

require "digest"
require "json"

module KnowledgeOS
  # Provider-neutral facade for agents and language models. It prepares grounded
  # task packets, gates tool calls through domain policy, and validates model
  # output without persisting model-authored content.
  class AgentService
    PROTOCOL_VERSION = "knowledgeos.agent.v1"

    TOOL_DEFINITIONS = {
      "resolve" => ["Resolve an entity or concept by id, key, label, or alias.", %w[query]],
      "get" => ["Read a compiled entity card.", %w[id]],
      "query" => ["Run a registered query template; arbitrary SQL is never accepted.", %w[template]],
      "neighbors" => ["Traverse typed graph relations from one node.", %w[id]],
      "history" => ["Read temporal assertions and audit references for a node.", %w[id]],
      "search" => ["Search compiled semantic content.", %w[query]],
      "calculate" => ["Execute a registered deterministic model and retain its trace.", %w[model_id inputs]],
      "explain" => ["Explain the source, provenance, and trace of a node or assertion.", %w[target]],
      "context" => ["Build a domain C-R-L-T-P context packet.", %w[id]],
      "propose" => ["Create a governed ChangeSet; this does not mutate authored truth.", %w[actor target_source patch reason]],
      "review" => ["Read the open governance and maintenance review queue.", []]
    }.freeze

    def initialize(service:, registry:)
      @service = service
      @registry = registry
    end

    def capabilities(domain: nil)
      packs = domain ? [@registry.domain(domain)] : @registry.domains.values
      allowed = packs.flat_map { |pack| Array(pack.dig("tool_policy", "allow")) }.uniq
      {
        "protocol_version" => PROTOCOL_VERSION,
        "registry_fingerprint" => registry_fingerprint,
        "domains" => packs.map { |pack| domain_capability(pack) },
        "tools" => tool_contracts(allowed),
        "response_contract" => response_schema,
        "durable_writes" => "changeset_only"
      }
    end

    def prepare(question:, domain:, target: nil, query: nil, skill: nil, as_of: nil, max_items: 25)
      question = question.to_s.strip
      raise ValidationError, "agent question is required" if question.empty?

      pack = @registry.domain(domain)
      selected_skill = select_skill(pack, skill)
      max_items = [[max_items.to_i, 1].max, 100].min
      resolved_target, matches = resolve_target(target, query || question, pack)
      evidence, context_data = build_evidence(resolved_target, matches, pack, as_of, max_items)
      request = {
        "protocol_version" => PROTOCOL_VERSION,
        "registry_fingerprint" => registry_fingerprint,
        "question" => question,
        "domain" => pack.fetch("id"),
        "skill" => selected_skill,
        "target" => resolved_target,
        "as_of" => as_of,
        "reasoning_plan" => reasoning_plan(pack, selected_skill),
        "context" => context_data,
        "evidence" => evidence,
        "available_tools" => tool_contracts(Array(pack.dig("tool_policy", "allow"))),
        "instructions" => response_instructions,
        "output_schema" => response_schema
      }
      request["request_id"] = "agent-request:#{digest(request)}"
      request["request_hash"] = digest(request)
      request
    end

    def finalize(request, model_output, tool_results: [])
      request = stringify(request)
      output = stringify(model_output)
      raise ValidationError, "agent request must be an object" unless request.is_a?(Hash)
      raise ValidationError, "model output must be an object" unless output.is_a?(Hash)
      validate_request!(request)
      validate_output_identity!(request, output)

      raise ValidationError, "model output answer must be a string" unless output["answer"].is_a?(String)
      answer = output["answer"].strip
      raise ValidationError, "model output answer is required" if answer.empty?
      allowed_output_keys = %w[protocol_version request_id answer claims knowledge_gaps recommended_actions]
      extra_output_keys = output.keys - allowed_output_keys
      raise ValidationError, "model output has unsupported fields: #{extra_output_keys.join(', ')}" unless extra_output_keys.empty?
      raise ValidationError, "model output claims must be an array" unless output["claims"].is_a?(Array)
      raise ValidationError, "model output knowledge_gaps must be an array" if output.key?("knowledge_gaps") && !output["knowledge_gaps"].is_a?(Array)
      if output.key?("recommended_actions") && !output["recommended_actions"].is_a?(Array)
        raise ValidationError, "model output recommended_actions must be an array"
      end
      claims = output["claims"]
      tool_evidence = validated_tool_evidence(tool_results, domain: request["domain"])
      evidence_index = (Array(request["evidence"]) + tool_evidence).each_with_object({}) { |item, out| out[item["ref"]] = item }
      used_refs = []
      claims.each_with_index do |claim, index|
        raise ValidationError, "claim #{index} must be an object" unless claim.is_a?(Hash)
        extra_claim_keys = claim.keys - %w[statement evidence_refs confidence]
        raise ValidationError, "claim #{index} has unsupported fields: #{extra_claim_keys.join(', ')}" unless extra_claim_keys.empty?
        raise ValidationError, "claim #{index} statement is required" if claim["statement"].to_s.strip.empty?
        raise ValidationError, "claim #{index} evidence_refs must be an array" unless claim["evidence_refs"].is_a?(Array)
        refs = claim["evidence_refs"]
        raise ValidationError, "claim #{index} must cite evidence" if refs.empty?
        unknown = refs.reject { |ref| evidence_index.key?(ref) }
        raise ValidationError, "claim #{index} cites unknown evidence: #{unknown.join(', ')}" unless unknown.empty?
        confidence = claim["confidence"]
        unless confidence.is_a?(Numeric) && confidence >= 0 && confidence <= 1
          raise ValidationError, "claim #{index} confidence must be between 0 and 1"
        end
        used_refs.concat(refs)
      end

      {
        "protocol_version" => PROTOCOL_VERSION,
        "request_id" => request["request_id"],
        "domain" => request["domain"],
        "skill_id" => request.dig("skill", "id"),
        "answer" => answer,
        "claims" => claims,
        "knowledge_gaps" => Array(output["knowledge_gaps"]),
        "recommended_actions" => Array(output["recommended_actions"]),
        "grounding" => {
          "valid" => true,
          "claim_count" => claims.length,
          "cited_evidence_refs" => used_refs.uniq,
          "available_evidence_count" => evidence_index.length
        },
        "write_performed" => false
      }
    end

    def invoke(domain:, operation:, arguments: {})
      pack = @registry.domain(domain)
      operation = operation.to_s
      allowed = Array(pack.dig("tool_policy", "allow"))
      raise ValidationError, "operation #{operation} is not allowed for domain #{domain}" unless allowed.include?(operation)
      args = stringify(arguments || {})

      result = case operation
      when "resolve" then @service.resolve(args.fetch("query"), scope: args["scope"])
      when "get" then @service.get(args.fetch("id"), include_history: args.fetch("include_history", false))
      when "query" then @service.query(args.fetch("template"), args.fetch("params", {}))
      when "neighbors"
        @service.neighbors(args.fetch("id"), relation_types: args["relation_types"], depth: args.fetch("depth", 1), as_of: args["as_of"])
      when "history" then @service.history(args.fetch("id"), predicate: args["predicate"], range: args["range"])
      when "search" then @service.search(args.fetch("query"), filters: args.fetch("filters", {}), mode: args.fetch("mode", "hybrid"))
      when "calculate" then invoke_calculate(pack, args)
      when "explain" then @service.explain(args.fetch("target"), field_or_assertion: args["item"])
      when "context"
        requested_domain = args.fetch("domain", domain)
        raise ValidationError, "context domain must match the tool policy domain" unless requested_domain == domain
        @service.context(args.fetch("id"), domain: domain, max_items: args.fetch("max_items", 25), as_of: args["as_of"])
      when "propose"
        @service.propose(actor: args.fetch("actor"), target_source: args.fetch("target_source"), patch: args.fetch("patch"),
                         reason: args.fetch("reason"), risk: args.fetch("risk", "normal"), title: args["title"],
                         operations: args.fetch("operations", []))
      when "review" then @service.review(priority: args["priority"], limit: args.fetch("limit", 100))
      else
        raise ValidationError, "unsupported agent operation: #{operation}"
      end
      if operation == "calculate"
        result["quality_status"] ||= {}
        result["quality_status"]["agent_evidence_ref"] = "derived:#{result.dig('data', 'run_id')}"
      end
      result
    end

    private

    def domain_capability(pack)
      {
        "id" => pack["id"], "label" => pack["label"], "concept_scopes" => Array(pack["concept_scopes"]),
        "default_skill" => pack["default_skill"], "workflow" => Array(pack["workflow"]),
        "retrieval_profile" => @registry.retrieval_profiles[pack["id"]],
        "skills" => @registry.skills_for_domain(pack["id"]),
        "tool_policy" => pack["tool_policy"]
      }
    end

    def tool_contracts(operations)
      operations.uniq.map do |operation|
        definition = TOOL_DEFINITIONS[operation]
        next unless definition
        properties = definition[1].each_with_object({}) { |name, out| out[name] = {} }
        {
          "name" => operation, "description" => definition[0],
          "input_schema" => { "type" => "object", "required" => definition[1], "properties" => properties,
                              "additionalProperties" => true },
          "side_effect" => operation == "propose" ? "creates_changeset" : "read_only"
        }
      end.compact
    end

    def select_skill(pack, requested)
      id = requested.to_s.strip
      id = pack["default_skill"].to_s if id.empty?
      skill = @registry.skill(id)
      raise ValidationError, "skill #{id} does not belong to domain #{pack['id']}" unless skill["domain"] == pack["id"]
      skill
    end

    def resolve_target(target, query, pack)
      needle = target.to_s.strip
      unless needle.empty?
        exact = @service.database.first("SELECT id, type FROM node WHERE id = ?", needle)
        if exact
          unless Array(pack["concept_scopes"]).include?(exact["type"])
            raise ValidationError, "target type #{exact['type']} is outside domain #{pack['id']} scope"
          end
          return [exact["id"], []]
        end
        matches = @service.resolve(needle)["data"].select { |item| Array(pack["concept_scopes"]).include?(item["type"]) }
        raise NotFoundError, "agent target not found: #{needle}" if matches.empty?
        return [matches.first["id"], matches]
      end

      profile = @registry.retrieval_profiles[pack["id"]] || {}
      matches = @service.search(query.to_s, filters: {}, mode: profile.fetch("mode", "hybrid"))["data"].select do |item|
        Array(pack["concept_scopes"]).include?(item["type"])
      end
      [matches.length == 1 ? matches.first["id"] : nil, matches.first(10)]
    end

    def build_evidence(target, matches, pack, as_of, max_items)
      evidence = []
      context_data = { "search_matches" => matches }
      matches.each { |item| add_node_evidence(evidence, item["id"]) }
      return [evidence.uniq { |item| item["ref"] }, context_data] unless target

      context = @service.context(target, domain: pack["id"], max_items: max_items, as_of: as_of)["data"]
      context_data["crltp"] = context
      add_node_evidence(evidence, target)
      assertions = @service.query("current_assertions", { "node_id" => target })["data"]
      priorities = Array(pack.dig("retrieval", "predicate_priority"))
      assertions.sort_by! { |item| priorities.index(item["predicate"]) || priorities.length }
      assertions.first(max_items).each do |item|
        evidence << {
          "ref" => item["id"], "kind" => "assertion", "subject" => item["node_id"],
          "predicate" => item["predicate"], "value" => item["value"], "temporal" => item["temporal"],
          "provenance" => item["provenance"], "confidence" => item.dig("epistemic", "confidence")
        }
      end
      Array(context.dig("relations", "edges")).first(max_items).each_with_index do |edge, index|
        evidence << {
          "ref" => "relation:#{digest(edge)[0, 20]}", "kind" => "relation", "value" => edge,
          "source_refs" => [edge["source_path"]].compact, "ordinal" => index
        }
      end
      [evidence.uniq { |item| item["ref"] }, context_data]
    end

    def add_node_evidence(evidence, id)
      row = @service.database.first("SELECT id, type, label, attrs_json, lifecycle, source_class, source_path, source_hash FROM node WHERE id = ?", id)
      return unless row
      evidence << {
        "ref" => "node:#{row['id']}", "kind" => "node", "subject" => row["id"], "type" => row["type"],
        "label" => row["label"], "attrs" => JSON.parse(row["attrs_json"]), "lifecycle" => row["lifecycle"],
        "source_class" => row["source_class"], "source_refs" => [row["source_path"]].compact,
        "source_hash" => row["source_hash"]
      }
    end

    def reasoning_plan(pack, skill)
      {
        "framework" => "C-R-L-T-P", "domain_workflow" => Array(pack["workflow"]),
        "skill_id" => skill["id"], "skill_instructions" => skill.dig("agent", "instructions"),
        "deterministic_models" => Array(skill["deterministic_models"]), "output_contract" => skill["output"]
      }
    end

    def response_instructions
      [
        "Return one JSON object matching output_schema.",
        "Use only evidence refs present in this request or returned by agent/invoke; every claim must cite at least one ref.",
        "Separate missing information into knowledge_gaps and do not invent values.",
        "Use calculate for arithmetic, unit-sensitive values, schedules, and policy decisions.",
        "Recommendations are not executed. Durable changes require a separate ChangeSet operation.",
        "Do not include hidden chain-of-thought; provide concise claims and citations instead."
      ]
    end

    def response_schema
      {
        "$schema" => "https://json-schema.org/draft/2020-12/schema", "type" => "object",
        "required" => %w[protocol_version request_id answer claims],
        "properties" => {
          "protocol_version" => { "const" => PROTOCOL_VERSION }, "request_id" => { "type" => "string" },
          "answer" => { "type" => "string", "minLength" => 1 },
          "claims" => { "type" => "array", "items" => { "type" => "object", "required" => %w[statement evidence_refs confidence],
            "properties" => { "statement" => { "type" => "string" }, "evidence_refs" => { "type" => "array", "items" => { "type" => "string" }, "minItems" => 1 },
                              "confidence" => { "type" => "number", "minimum" => 0, "maximum" => 1 } }, "additionalProperties" => false } },
          "knowledge_gaps" => { "type" => "array", "items" => { "type" => "string" } },
          "recommended_actions" => { "type" => "array", "items" => { "type" => "object" } }
        },
        "additionalProperties" => false
      }
    end

    def validate_request!(request)
      raise ValidationError, "agent protocol_version mismatch" unless request["protocol_version"] == PROTOCOL_VERSION
      raise ValidationError, "agent registry changed; prepare a new request" unless request["registry_fingerprint"] == registry_fingerprint
      expected_hash = request.delete("request_hash")
      actual_hash = digest(request)
      request["request_hash"] = expected_hash
      raise ValidationError, "agent request integrity check failed" unless expected_hash == actual_hash
      @registry.domain(request.fetch("domain"))
      current_skill = @registry.skill(request.dig("skill", "id"))
      unless current_skill["domain"] == request["domain"]
        raise ValidationError, "agent request skill does not match its domain"
      end
    end

    def validate_output_identity!(request, output)
      raise ValidationError, "model output must be an object" unless output.is_a?(Hash)
      raise ValidationError, "model output protocol_version mismatch" unless output["protocol_version"] == PROTOCOL_VERSION
      raise ValidationError, "model output request_id mismatch" unless output["request_id"] == request["request_id"]
    end

    def invoke_calculate(pack, args)
      model_id = args.fetch("model_id")
      permitted = Array(pack["required_models"])
      raise ValidationError, "model #{model_id} is not allowed for domain #{pack['id']}" unless permitted.include?(model_id)
      @service.calculate(model_id, args.fetch("inputs"), scenario: args.fetch("scenario", "default"))
    end

    def validated_tool_evidence(results, domain:)
      raise ValidationError, "tool_results must be an array" unless results.is_a?(Array)
      permitted_models = Array(@registry.domain(domain)["required_models"])
      results.map.with_index do |envelope, index|
        item = stringify(envelope)
        raise ValidationError, "tool result #{index} must be an object" unless item.is_a?(Hash)
        run_id = item.dig("data", "run_id")
        next if run_id.to_s.empty?
        row = @service.database.first("SELECT * FROM derived_result WHERE run_id = ?", run_id)
        raise ValidationError, "unknown deterministic tool run: #{run_id}" unless row
        unless permitted_models.include?(row["model_id"])
          raise ValidationError, "tool run model #{row['model_id']} is not allowed for domain #{domain}"
        end
        expected_ref = "derived:#{run_id}"
        supplied_ref = item.dig("quality_status", "agent_evidence_ref")
        raise ValidationError, "tool result evidence reference mismatch" unless supplied_ref == expected_ref
        {
          "ref" => expected_ref, "kind" => "deterministic_result", "model_id" => row["model_id"],
          "model_version" => row["model_version"], "scenario" => row["scenario"],
          "output" => JSON.parse(row["output_json"]), "trace" => JSON.parse(row["trace_json"]),
          "calculated_at" => row["calculated_at"]
        }
      end.compact
    end

    def registry_fingerprint
      payload = {
        "ontology" => @registry.ontology,
        "predicates" => @registry.predicates.transform_values { |item| item.reject { |key, _| key.start_with?("_") } },
        "models" => @registry.models.transform_values { |item| item.reject { |key, _| key.start_with?("_") } },
        "domains" => @registry.domains.transform_values { |item| item.reject { |key, _| key.start_with?("_") } },
        "skills" => @registry.skills
      }
      digest(payload)
    end

    def digest(value)
      Digest::SHA256.hexdigest(JSON.generate(canonical(value)))
    end

    def canonical(value)
      case value
      when Hash
        value.keys.map(&:to_s).sort.each_with_object({}) do |key, output|
          original = value.key?(key) ? key : value.keys.find { |candidate| candidate.to_s == key }
          output[key] = canonical(value[original])
        end
      when Array then value.map { |item| canonical(item) }
      else value
      end
    end

    def stringify(value)
      Frontmatter.stringify(value || {})
    end
  end
end
