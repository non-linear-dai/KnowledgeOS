# frozen_string_literal: true

require "digest"
require "json"
require "open3"
require "shellwords"
require "time"

module KnowledgeOS
  class SourceEnvelope
    KINDS = %w[text file web audio meeting_minutes image video email chat].freeze
    KIND_ALIASES = {
      "voice" => "audio", "transcript" => "audio", "meeting" => "meeting_minutes",
      "minutes" => "meeting_minutes", "meeting_notes" => "meeting_minutes"
    }.freeze

    def self.build(input, profile:, clock: -> { Time.now.utc })
      input = Frontmatter.stringify(input || {})
      return validate_existing(input, profile) if input["version"] == "1.0" && input["segments"].is_a?(Array)

      kind = KIND_ALIASES.fetch(input["kind"].to_s, input["kind"].to_s)
      raise ValidationError, "unsupported extraction source kind: #{kind}" unless allowed_kinds(profile).include?(kind)
      content = normalize_text(input["content"])
      raise ValidationError, "source content is required after materialization" if content.empty?
      max_chars = profile.fetch("max_source_chars", 500_000).to_i
      raise ValidationError, "source content exceeds #{max_chars} characters" if content.length > max_chars

      locator = input["locator"].to_s.strip
      locator = "inline:#{kind}" if locator.empty?
      captured_at = normalize_time(input["captured_at"] || clock.call.iso8601(6), "captured_at")
      content_hash = Digest::SHA256.hexdigest(content)
      source_id = input["id"].to_s.strip
      source_id = "source:#{kind}:#{Digest::SHA256.hexdigest([locator, content_hash].join(":"))[0, 24]}" if source_id.empty?
      segment_size = profile.fetch("segment_chars", 4_000).to_i
      segment_size = 4_000 if segment_size < 1

      {
        "version" => "1.0", "id" => source_id, "kind" => kind, "locator" => locator,
        "mime_type" => blank_to_nil(input["mime_type"]), "content_hash" => content_hash,
        "captured_at" => captured_at, "metadata" => input["metadata"].is_a?(Hash) ? input["metadata"] : {},
        "segments" => segments(content, segment_size)
      }
    end

    def self.validate_existing(input, profile)
      kind = input["kind"].to_s
      raise ValidationError, "unsupported extraction source kind: #{kind}" unless allowed_kinds(profile).include?(kind)
      %w[id locator content_hash captured_at].each do |field|
        raise ValidationError, "source envelope missing #{field}" if input[field].to_s.empty?
      end
      normalize_time(input["captured_at"], "captured_at")
      content = input["segments"].map.with_index do |segment, index|
        raise ValidationError, "source segment #{index} must be an object" unless segment.is_a?(Hash)
        raise ValidationError, "source segment #{index} is missing text" if segment["text"].to_s.empty?
        segment["text"].to_s
      end.join
      max_chars = profile.fetch("max_source_chars", 500_000).to_i
      raise ValidationError, "source content exceeds #{max_chars} characters" if content.length > max_chars
      input
    end

    def self.segments(content, size)
      result = []
      offset = 0
      while offset < content.length
        limit = [offset + size, content.length].min
        boundary = limit
        if limit < content.length
          paragraph = content.rindex("\n\n", limit)
          boundary = paragraph + 2 if paragraph && paragraph >= offset + (size / 2)
        end
        chunk = content[offset...boundary]
        text = chunk.to_s.strip
        offset = boundary
        unless text.empty?
          start_at = content.index(text, offset - chunk.length) || (offset - chunk.length)
          end_at = start_at + text.length
          result << { "id" => format("segment:%04d", result.length + 1), "text" => text,
                      "start" => start_at, "end" => end_at }
        end
      end
      if result.empty?
        result << { "id" => "segment:0001", "text" => content, "start" => 0, "end" => content.length }
      end
      result
    end

    def self.normalize_text(value)
      value.to_s.encode("UTF-8", invalid: :replace, undef: :replace).gsub("\u0000", "").strip
    end

    def self.normalize_time(value, label)
      Time.iso8601(value.to_s).utc.iso8601(6)
    rescue ArgumentError
      raise ValidationError, "invalid source #{label}: #{value}"
    end

    def self.blank_to_nil(value)
      value.to_s.strip.empty? ? nil : value.to_s
    end

    def self.allowed_kinds(profile)
      configured = Array(profile["source_types"]).map(&:to_s).reject(&:empty?)
      configured.empty? ? KINDS : configured
    end

    private_class_method :segments, :normalize_text, :normalize_time, :blank_to_nil, :allowed_kinds
  end

  class SourceLoader
    TEXT_EXTENSIONS = %w[.txt .md .markdown .csv .tsv .json .yaml .yml .html .htm .xml .vtt .srt].freeze

    def initialize(profile:, materializer: nil)
      @profile = profile
      @materializer = materializer
    end

    def load(input)
      source = Frontmatter.stringify(input || {})
      return SourceEnvelope.build(source, profile: @profile) if source["version"] == "1.0"
      return SourceEnvelope.build(source, profile: @profile) unless source["content"].to_s.empty?

      locator = source["locator"].to_s
      kind = SourceEnvelope::KIND_ALIASES.fetch(source["kind"].to_s, source["kind"].to_s)
      if %w[file meeting_minutes].include?(kind) && readable_text_file?(locator)
        source["content"] = File.binread(locator)
      elsif @materializer
        materialized = Frontmatter.stringify(@materializer.materialize(source))
        source["content"] = materialized.fetch("content")
        source["mime_type"] ||= materialized["mime_type"]
        source["metadata"] = (source["metadata"] || {}).merge(materialized["metadata"] || {})
      else
        raise ValidationError, "#{kind} source requires content or a materializer adapter"
      end
      SourceEnvelope.build(source, profile: @profile)
    rescue Errno::ENOENT, Errno::EACCES => e
      raise ValidationError, "cannot read source #{locator}: #{e.message}"
    end

    private

    def readable_text_file?(locator)
      File.file?(locator) && TEXT_EXTENSIONS.include?(File.extname(locator).downcase)
    end
  end

  class JsonCommand
    def initialize(command, timeout_seconds: 120)
      @argv = command.is_a?(Array) ? command : Shellwords.split(command.to_s)
      raise ValidationError, "adapter command is required" if @argv.empty?
      @timeout_seconds = timeout_seconds.to_i
    end

    def call(payload)
      stdout_text = ""
      stderr_text = ""
      status = nil
      Open3.popen3(*@argv) do |stdin, stdout, stderr, wait_thread|
        stdin.write(JSON.generate(payload))
        stdin.close
        stdout_reader = Thread.new { stdout.read }
        stderr_reader = Thread.new { stderr.read }
        unless wait_thread.join(@timeout_seconds)
          Process.kill("TERM", wait_thread.pid) rescue nil
          wait_thread.join(2)
          Process.kill("KILL", wait_thread.pid) rescue nil if wait_thread.alive?
          raise ValidationError, "adapter command timed out after #{@timeout_seconds}s"
        end
        stdout_text = stdout_reader.value
        stderr_text = stderr_reader.value
        status = wait_thread.value
      end
      unless status.success?
        detail = stderr_text.to_s.strip[0, 500]
        raise ValidationError, "adapter command failed (#{status.exitstatus}): #{detail}"
      end
      parse_json(stdout_text)
    rescue Errno::ENOENT => e
      raise ValidationError, "adapter command not found: #{e.message}"
    end

    private

    def parse_json(text)
      value = text.to_s.strip
      fenced = value.match(/\A```(?:json)?\s*(.*?)\s*```\z/m)
      value = fenced[1] if fenced
      parsed = JSON.parse(value)
      raise ValidationError, "adapter output must be a JSON object" unless parsed.is_a?(Hash)
      Frontmatter.stringify(parsed)
    rescue JSON::ParserError => e
      raise ValidationError, "adapter returned invalid JSON: #{e.message}"
    end
  end

  class CommandMaterializer
    def initialize(command, timeout_seconds: 120)
      @command = JsonCommand.new(command, timeout_seconds: timeout_seconds)
    end

    def materialize(source)
      result = @command.call({ "protocol_version" => "knowledgeos.materialization.v1", "source" => source })
      raise ValidationError, "materializer output is missing content" if result["content"].to_s.empty?
      result
    end
  end

  class CommandModelAdapter
    def initialize(command, timeout_seconds: 120)
      @command = JsonCommand.new(command, timeout_seconds: timeout_seconds)
    end

    def extract(request)
      @command.call(request)
    end
  end

  class StaticModelAdapter
    def initialize(output)
      @output = Frontmatter.stringify(output)
    end

    def extract(_request)
      Marshal.load(Marshal.dump(@output))
    end
  end

  class ExtractionContract
    def initialize(registry:, database:, profile:)
      @registry = registry
      @database = database
      @profile = profile
    end

    def build(source)
      fingerprint = registry_fingerprint
      {
        "protocol_version" => @profile.fetch("protocol_version", "knowledgeos.extraction.v1"),
        "registry_fingerprint" => fingerprint,
        "instructions" => instructions,
        "canonical_schema" => @registry.schemas["canonical-node.schema"] || @registry.schemas["canonical-node"],
        "ontology" => ontology_contract,
        "existing_entities" => existing_entities,
        "source" => source,
        "output_schema" => output_schema(fingerprint)
      }
    end

    def registry_fingerprint
      payload = {
        "ontology" => @registry.ontology,
        "predicates" => @registry.predicates.transform_values { |item| item.reject { |key, _| key.start_with?("_") } },
        "schemas" => @registry.schemas,
        "policies" => { "authority" => @registry.authority_policies,
                        "provenance" => @registry.provenance_policies },
        "profile" => @profile.reject { |key, _| key.start_with?("_") }
      }
      Digest::SHA256.hexdigest(JSON.generate(canonical(payload)))
    end

    private

    def instructions
      [
        "Extract only facts explicitly supported by source segments.",
        "Use only concept, predicate, and relation identifiers present in this request.",
        "Set existing_id only for an exact existing entity match; otherwise omit it.",
        "Cite segment ids and short exact evidence quotes for every entity.",
        "Return JSON only and copy protocol_version and registry_fingerprint exactly.",
        "Do not mark assertions confirmed; KnowledgeOS creates proposed candidates deterministically."
      ]
    end

    def ontology_contract
      concepts = Array(@registry.ontology["concept_types"]).map do |concept|
        bindings = Array(concept["properties"]).map do |binding|
          predicate = @registry.predicate(binding["predicate"])
          {
            "predicate" => binding["predicate"], "required_for_create" => binding.fetch("required", false),
            "storage" => predicate.dig("storage", "mode"), "value_type" => predicate.dig("value", "type"),
            "cardinality" => predicate.dig("value", "cardinality"),
            "description" => predicate.dig("semantics", "description")
          }
        end
        { "id" => concept["id"], "label" => concept["label"], "description" => concept["description"],
          "properties" => bindings, "relations" => relations_for(concept["id"]) }
      end
      { "concepts" => concepts }
    end

    def relations_for(type)
      Array(@registry.ontology["relation_types"]).each_with_object([]) do |relation, output|
        endpoints = Array(relation["connections"]).select { |item| item["source_type"] == type }
        next if endpoints.empty?
        output << { "id" => relation["id"], "mode" => relation.fetch("mode", "simple"),
                    "target_types" => endpoints.map { |item| item["target_type"] }.uniq }
      end
    end

    def existing_entities
      limit = @profile.fetch("existing_entity_limit", 500).to_i
      @database.execute("SELECT id, type, natural_key, key_namespace, label, aliases_json FROM node ORDER BY id LIMIT ?", [limit]).map do |row|
        { "id" => row["id"], "type" => row["type"], "key" => row["natural_key"], 'key_namespace' => row['key_namespace'],
          "label" => row["label"], "aliases" => JSON.parse(row["aliases_json"]) }
      end
    end

    def output_schema(fingerprint)
      variants = Array(@registry.ontology["concept_types"]).map { |concept| entity_schema(concept) }
      {
        "$schema" => "https://json-schema.org/draft/2020-12/schema", "type" => "object",
        "required" => %w[protocol_version registry_fingerprint entities],
        "properties" => {
          "protocol_version" => { "const" => @profile.fetch("protocol_version", "knowledgeos.extraction.v1") },
          "registry_fingerprint" => { "const" => fingerprint },
          "entities" => { "type" => "array", "items" => { "oneOf" => variants } },
          "unmapped_facts" => { "type" => "array", "items" => evidence_fact_schema }
        },
        "additionalProperties" => false
      }
    end

    def entity_schema(concept)
      bindings = Array(concept["properties"])
      attrs = bindings.select { |item| @registry.predicate(item["predicate"]).dig("storage", "mode") == "attr" }
      assertion_bindings = bindings.select { |item| %w[assertion external].include?(@registry.predicate(item["predicate"]).dig("storage", "mode")) }
      relation_ids = relations_for(concept["id"]).map { |item| item["id"] }
      properties = {
        "type" => { "const" => concept["id"] }, "existing_id" => { "type" => "string" },
        "suggested_id" => { "type" => "string" }, "key" => { "type" => "string" },
        'key_namespace' => { 'type' => 'string', 'minLength' => 1 },
        "label" => { "type" => "string", "minLength" => 1 },
        "aliases" => { "type" => "array", "items" => { "type" => "string" } },
        "attrs" => { "type" => "object",
                     "properties" => attrs.each_with_object({}) { |item, out| out[item["predicate"]] = value_schema(@registry.predicate(item["predicate"])) },
                     "additionalProperties" => false },
        "assertions" => assertion_bindings.empty? ? { "type" => "array", "maxItems" => 0 } :
          { "type" => "array", "items" => { "oneOf" => assertion_bindings.map { |item| assertion_schema(item["predicate"]) } } },
        "relations" => relation_ids.empty? ? { "type" => "array", "maxItems" => 0 } :
          { "type" => "array", "items" => relation_schema(relation_ids) },
        "confidence" => { "type" => "number", "minimum" => 0, "maximum" => 1 },
        "evidence" => { "type" => "array", "items" => evidence_schema }
      }
      { "type" => "object", "required" => %w[type label attrs assertions relations confidence evidence],
        "properties" => properties, "additionalProperties" => false }
    end

    def assertion_schema(predicate_id)
      predicate = @registry.predicate(predicate_id)
      value = if %w[quantity currency].include?(predicate.dig('value', 'type'))
                value_schema(predicate)
              else
                { 'oneOf' => [value_schema(predicate), { 'type' => 'object', 'required' => ['literal'], 'properties' => { 'literal' => value_schema(predicate) }, 'additionalProperties' => true }] }
              end
      { "type" => "object", "required" => %w[predicate value],
        "properties" => {
          "predicate" => { "const" => predicate_id },
          'value' => value,
          "qualifiers" => { "type" => "object" }, "temporal" => { "type" => "object" },
          "confidence" => { "type" => "number", "minimum" => 0, "maximum" => 1 },
          "evidence" => { "type" => "array", "items" => evidence_schema }
        }, "additionalProperties" => false }
    end

    def relation_schema(ids)
      { "type" => "object", "required" => %w[predicate target],
        "properties" => { "predicate" => { "enum" => ids }, "target" => { "type" => "string" },
                          "temporal" => { "type" => "object" }, "weight" => { "type" => "number" },
                          "evidence" => { "type" => "array", "items" => evidence_schema } },
        "additionalProperties" => false }
    end

    def evidence_schema
      { "type" => "object", "required" => %w[segment_id quote],
        "properties" => { "segment_id" => { "type" => "string" },
                          "quote" => { "type" => "string", "minLength" => 1 } },
        "additionalProperties" => false }
    end

    def evidence_fact_schema
      { "type" => "object", "required" => %w[text reason evidence],
        "properties" => { "text" => { "type" => "string" }, "reason" => { "type" => "string" },
                          "evidence" => { "type" => "array", "items" => evidence_schema } },
        "additionalProperties" => false }
    end

    def value_schema(predicate)
      if %w[quantity currency].include?(predicate.dig('value', 'type'))
        numeric = predicate.dig('value', 'type') == 'currency' ? { 'type' => ['number', 'string'] } : { 'type' => 'number' }
        object = { 'type' => 'object', 'required' => ['literal', 'unit'],
                   'properties' => { 'literal' => numeric, 'unit' => { 'type' => 'string', 'minLength' => 1 } }, 'additionalProperties' => true }
        return predicate.dig('value', 'default_unit') ? { 'oneOf' => [numeric, object] } : object
      end
      schema = case predicate.dig("value", "type")
      when "string", "text", "enum", "node_ref" then { "type" => "string" }
      when 'date' then { 'type' => 'string', 'format' => 'date' }
      when 'datetime' then { 'type' => 'string', 'format' => 'date-time' }
      when 'decimal' then { 'type' => ['number', 'string'] }
      when "number", "quantity" then { "type" => "number" }
      when "boolean" then { "type" => "boolean" }
      else {}
      end
      allowed = Array(predicate.dig("value", "allowed"))
      schema["enum"] = allowed unless allowed.empty?
      schema
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
  end

  class CandidateBuilder
    ID_PATTERN = /\A[a-z][a-z0-9_-]*:[a-z0-9][a-z0-9._-]*\z/.freeze

    def initialize(registry:, database:, profile:, request:)
      @registry = registry
      @database = database
      @profile = profile
      @request = request
      @source = request.fetch("source")
      @validator = Validator.new(registry)
    end

    def build(model_output)
      output = Frontmatter.stringify(model_output || {})
      validate_root!(output)
      entities = output["entities"]
      plans, rejected = prepare_plans(entities)
      known_types = plans.each_with_object({}) { |plan, index| index[plan["id"]] = plan["type"] }
      candidates = []
      plans.each do |plan|
        begin
          candidates << build_candidate(plan, known_types)
        rescue ValidationError, KeyError => e
          rejected << rejection(plan["index"], plan["entity"], e.message)
        end
      end
      {
        "protocol_version" => @request["protocol_version"],
        "registry_fingerprint" => @request["registry_fingerprint"],
        "source" => source_reference,
        "candidates" => candidates,
        "rejected" => rejected,
        "unmapped_facts" => Array(output["unmapped_facts"]),
        "write_performed" => false
      }
    end

    private

    def validate_root!(output)
      raise ValidationError, "model output must be an object" unless output.is_a?(Hash)
      unless output["protocol_version"] == @request["protocol_version"]
        raise ValidationError, "model output protocol_version does not match extraction request"
      end
      unless output["registry_fingerprint"] == @request["registry_fingerprint"]
        raise ValidationError, "model output registry_fingerprint does not match extraction request"
      end
      raise ValidationError, "model output entities must be an array" unless output["entities"].is_a?(Array)
      if output.key?("unmapped_facts") && !output["unmapped_facts"].is_a?(Array)
        raise ValidationError, "model output unmapped_facts must be an array"
      end
    end

    def prepare_plans(entities)
      plans = []
      rejected = []
      reserved = {}
      entities.each_with_index do |raw, index|
        begin
          entity = Frontmatter.stringify(raw)
          raise ValidationError, "entity must be an object" unless entity.is_a?(Hash)
          validate_entity_shape!(entity)
          type = entity["type"].to_s
          raise ValidationError, "unknown concept type #{type}" unless @registry.type?(type)
          raise ValidationError, "entity label is required" if entity["label"].to_s.strip.empty?
          validate_confidence!(entity.fetch("confidence", 0.0))
          validate_evidence!(entity["evidence"])
          existing = resolve_existing(entity, type)
          id = existing ? existing["id"] : new_id(entity, type)
          if reserved.key?(id)
            raise ValidationError, "duplicate extracted entity identity #{id}"
          end
          reserved[id] = true
          plans << { "index" => index, "entity" => entity, "type" => type, "id" => id, "existing" => existing }
        rescue ValidationError, KeyError => e
          rejected << rejection(index, raw, e.message)
        end
      end
      [plans, rejected]
    end

    def build_candidate(plan, known_types)
      entity = plan["entity"]
      existing = plan["existing"]
      type = plan["type"]
      allowed = allowed_predicates(type)
      attrs = Frontmatter.stringify(entity["attrs"] || {})
      assertions = Array(entity["assertions"]).map { |item| Frontmatter.stringify(item) }
      relations = Array(entity["relations"]).map { |item| Frontmatter.stringify(item) }
      attrs.each_key { |id| validate_bound_predicate!(id, allowed, "attr") }
      assertions.each { |item| validate_bound_predicate!(item["predicate"], allowed, "assertion") }
      proposed_assertions = assertions.map { |item| canonical_assertion(plan["id"], item, entity["confidence"], entity["evidence"]) }
      proposed_relations = relations.map { |item| canonical_relation(type, item, known_types) }

      base, current_attrs, current_assertions, current_relations = existing_state(plan)
      merged_attrs = current_attrs.merge(attrs)
      merged_assertions = append_unique_assertions(current_assertions, proposed_assertions)
      merged_relations = append_unique_relations(current_relations, proposed_relations)
      base["node"]["label"] = entity["label"].to_s.strip
      base["node"]["aliases"] = (Array(base["node"]["aliases"]) + Array(entity["aliases"])).map(&:to_s).reject(&:empty?).uniq
      document = {
        "base" => base,
        "knowledge" => { "attrs" => merged_attrs, "assertions" => merged_assertions,
                         "relations" => merged_relations, "logic_refs" => [] },
        "external" => { "assertions_ref" => nil }
      }
      parsed = ParsedDocument.new(data: document, body: "", path: "(extraction-candidate:#{plan['id']})")
      @validator.validate_document!(parsed)
      operations = existing ? update_operations(existing, entity, attrs, current_attrs, proposed_assertions,
                                                current_assertions, proposed_relations, current_relations) :
                              [{ "op" => "add", "path" => "/", "value" => document }]
      action = existing ? (operations.empty? ? "no_change" : "update_instance") : "create_instance"
      {
        "action" => action, "target_id" => plan["id"],
        "target_source" => existing ? existing["source_path"] : suggested_source(plan["id"]),
        "publication_route" => existing && existing["source_class"] != "git_authored" ?
          "upstream_source_system" : "governed_changeset",
        "confidence" => entity["confidence"].to_f, "operations" => operations,
        "evidence" => Array(entity["evidence"]),
        "attribute_candidates" => attribute_candidates(plan["id"], attrs, current_attrs, entity["evidence"]),
        "relation_candidates" => relation_candidates(proposed_relations, relations, entity["evidence"]),
        "proposed_instance" => document,
        "validation" => { "valid" => true, "registry_fingerprint" => @request["registry_fingerprint"] },
        "write_policy" => "candidate_only"
      }
    end

    def resolve_existing(entity, type)
      if entity["existing_id"] && !entity["existing_id"].to_s.empty?
        row = @database.first("SELECT * FROM node WHERE id = ?", [entity["existing_id"]])
        raise ValidationError, "existing_id not found: #{entity['existing_id']}" unless row
        raise ValidationError, "existing_id type mismatch: #{row['type']} != #{type}" unless row["type"] == type
        raise ValidationError, 'existing_id namespace mismatch' if entity['key_namespace'] && entity['key_namespace'] != row['key_namespace']
        return row
      end
      terms = [entity["key"], entity["label"]] + Array(entity["aliases"])
      normalized = terms.compact.map { |value| value.to_s.strip.downcase }.reject(&:empty?).uniq
      matches = @database.execute("SELECT * FROM node WHERE type = ?", [type]).select do |row|
        next false if entity['key_namespace'] && entity['key_namespace'] != row['key_namespace']
        values = [row["natural_key"], row["label"]] + JSON.parse(row["aliases_json"])
        !(values.compact.map { |value| value.to_s.strip.downcase } & normalized).empty?
      end
      raise ValidationError, "ambiguous existing entity match: #{matches.map { |row| row['id'] }.join(', ')}" if matches.length > 1
      matches.first
    end

    def validate_entity_shape!(entity)
      %w[type label attrs assertions relations confidence evidence].each do |field|
        raise ValidationError, "entity is missing #{field}" unless entity.key?(field)
      end
      raise ValidationError, "entity attrs must be an object" unless entity["attrs"].is_a?(Hash)
      raise ValidationError, "entity assertions must be an array" unless entity["assertions"].is_a?(Array)
      raise ValidationError, "entity relations must be an array" unless entity["relations"].is_a?(Array)
      raise ValidationError, "entity aliases must be an array" if entity.key?("aliases") && !entity["aliases"].is_a?(Array)
      raise ValidationError, "entity evidence must be an array" unless entity["evidence"].is_a?(Array)
      entity["assertions"].each do |item|
        raise ValidationError, "assertion candidate must be an object" unless item.is_a?(Hash)
        raise ValidationError, "assertion candidate is missing predicate" if item["predicate"].to_s.empty?
        raise ValidationError, "assertion candidate is missing value" unless item.key?("value")
        raise ValidationError, "assertion qualifiers must be an object" if item.key?("qualifiers") && !item["qualifiers"].is_a?(Hash)
        raise ValidationError, "assertion temporal must be an object" if item.key?("temporal") && !item["temporal"].is_a?(Hash)
        raise ValidationError, "assertion evidence must be an array" if item.key?("evidence") && !item["evidence"].is_a?(Array)
      end
      entity["relations"].each do |item|
        raise ValidationError, "relation candidate must be an object" unless item.is_a?(Hash)
        raise ValidationError, "relation candidate is missing predicate" if item["predicate"].to_s.empty?
        raise ValidationError, "relation candidate is missing target" if item["target"].to_s.empty?
        raise ValidationError, "relation temporal must be an object" if item.key?("temporal") && !item["temporal"].is_a?(Hash)
        raise ValidationError, "relation evidence must be an array" if item.key?("evidence") && !item["evidence"].is_a?(Array)
      end
    end

    def new_id(entity, type)
      suggested = entity["suggested_id"].to_s
      unless suggested.empty?
        raise ValidationError, "invalid suggested_id #{suggested}" unless suggested.match?(ID_PATTERN)
        expected_prefix = @profile.fetch("identity_prefixes", {}).fetch(type, type)
        unless suggested.split(":", 2).first == expected_prefix
          raise ValidationError, "suggested_id prefix must be #{expected_prefix} for #{type}"
        end
        raise ValidationError, "suggested_id already exists: #{suggested}" if @database.first("SELECT id FROM node WHERE id = ?", [suggested])
        return suggested
      end
      prefix = @profile.fetch("identity_prefixes", {}).fetch(type, type)
      seed = entity["key"].to_s.empty? ? entity["label"].to_s : entity["key"].to_s
      slug = seed.downcase.gsub(/[^a-z0-9]+/, "-").gsub(/\A-+|-+\z/, "")
      slug = Digest::SHA256.hexdigest(seed)[0, 12] if slug.empty?
      id = "#{prefix}:#{slug}"
      id = "#{prefix}:#{Digest::SHA256.hexdigest(entity['key_namespace'])[0, 12]}-#{slug}" if entity['key_namespace']
      raise ValidationError, "generated identity already exists; provide existing_id: #{id}" if @database.first("SELECT id FROM node WHERE id = ?", [id])
      id
    end

    def existing_state(plan)
      existing = plan["existing"]
      unless existing
        key = plan["entity"]["key"].to_s
        key = plan["id"].split(":", 2).last.upcase if key.empty?
        base = { "schema" => { "ckm" => canonical_schema_version },
                 "node" => { "id" => plan["id"], "kind" => "entity", "type" => plan["type"],
                             "key" => key, "label" => plan["entity"]["label"], "aliases" => [] },
                 "classification" => { "tags" => [] }, "lifecycle" => { "state" => "draft" },
                 "version" => { "entity_revision" => 1 } }
        base['node']['key_namespace'] = plan['entity']['key_namespace'] if plan['entity']['key_namespace']
        return [base, {}, [], []]
      end
      base = { "schema" => { "ckm" => canonical_schema_version },
               "node" => { "id" => existing["id"], "kind" => existing["kind"], "type" => existing["type"],
                           "key" => existing["natural_key"], "label" => existing["label"],
                           "aliases" => JSON.parse(existing["aliases_json"]) },
               "classification" => { "tags" => JSON.parse(existing["tags_json"]) },
               "lifecycle" => { "state" => existing["lifecycle"] },
               "version" => { "entity_revision" => existing["revision"] } }
      base['node']['key_namespace'] = existing['key_namespace'] unless existing['key_namespace'].to_s.empty?
      [base, JSON.parse(existing["attrs_json"]), existing_assertions(existing["id"]), existing_relations(existing["id"])]
    end

    def existing_assertions(node_id)
      @database.execute("SELECT * FROM assertion WHERE node_id = ? ORDER BY id", [node_id]).map do |row|
        { "id" => row["id"], "predicate" => row["predicate"], "value" => JSON.parse(row["value_json"]),
          "qualifiers" => JSON.parse(row["qualifiers_json"]),
          "temporal" => { "observed_at" => row["observed_at"], "valid_from" => row["valid_from"], "valid_to" => row["valid_to"] },
          "epistemic" => { "assertion_kind" => row["assertion_kind"], "status" => row["status"], "confidence" => row["confidence"] },
          "provenance" => { "evidence_refs" => JSON.parse(row["evidence_refs_json"]),
                            "source_refs" => JSON.parse(row["source_refs_json"]) },
          "version" => { "supersedes" => row["supersedes"] } }
      end
    end

    def existing_relations(node_id)
      @database.execute("SELECT * FROM edge WHERE src = ? ORDER BY predicate, dst", [node_id]).map do |row|
        relation = { "predicate" => row["predicate"], "target" => row["dst"] }
        relation["id"] = row["rel_id"] unless row["rel_id"].to_s.empty?
        temporal = { "valid_from" => row["valid_from"], "valid_to" => row["valid_to"] }.reject { |_, value| value.to_s.empty? }
        relation["temporal"] = temporal unless temporal.empty?
        relation["weight"] = row["weight"] if row["weight"]
        relation
      end
    end

    def canonical_assertion(node_id, item, entity_confidence, inherited_evidence)
      predicate_id = item["predicate"].to_s
      predicate = @registry.predicate(predicate_id)
      raw_value = item["value"]
      value = ValueContract.normalize(predicate, raw_value.is_a?(Hash) ? raw_value : { "type" => predicate.dig("value", "type"), "literal" => raw_value })
      evidence = Array(item["evidence"])
      evidence = Array(inherited_evidence) if evidence.empty?
      validate_evidence!(evidence)
      confidence = item.key?("confidence") ? item["confidence"] : entity_confidence
      validate_confidence!(confidence)
      observed_at = item.dig("temporal", "observed_at") || @source["captured_at"]
      id_payload = [node_id, predicate_id, value, item["qualifiers"] || {}, @source["content_hash"]]
      {
        "id" => "assertion:extract:#{Digest::SHA256.hexdigest(JSON.generate(id_payload))[0, 32]}",
        "predicate" => predicate_id, "value" => value, "qualifiers" => item["qualifiers"] || {},
        "temporal" => (item["temporal"] || {}).merge("observed_at" => observed_at),
        "epistemic" => { "assertion_kind" => "extracted_claim", "status" => "proposed", "confidence" => confidence.to_f },
        "provenance" => { "source_refs" => [@source["id"]], "evidence_refs" => evidence_refs(evidence) },
        "version" => { "supersedes" => nil }
      }
    end

    def canonical_relation(source_type, item, known_types)
      predicate = item["predicate"].to_s
      target = item["target"].to_s
      raise ValidationError, "relation target is required" if target.empty?
      definition = @registry.relation(predicate)
      raise ValidationError, "unknown relation #{predicate}" unless definition
      target_type = known_types[target]
      unless target_type
        row = @database.first("SELECT type FROM node WHERE id = ?", [target])
        target_type = row && row["type"]
      end
      raise ValidationError, "relation target is not an existing or extracted entity: #{target}" unless target_type
      connections = Array(definition["connections"])
      if target_type && connections.none? { |edge| edge["source_type"] == source_type && edge["target_type"] == target_type }
        raise ValidationError, "relation #{predicate} does not allow #{source_type} -> #{target_type}"
      end
      validate_evidence!(item["evidence"]) if item["evidence"]
      relation = { "predicate" => predicate, "target" => target }
      relation["temporal"] = item["temporal"] if item["temporal"]
      relation["weight"] = item["weight"] if item.key?("weight")
      relation
    end

    def allowed_predicates(type)
      Array(@registry.concept(type)["properties"]).each_with_object({}) do |binding, output|
        predicate = @registry.predicate(binding["predicate"])
        output[binding["predicate"]] = predicate.dig("storage", "mode")
      end
    end

    def validate_bound_predicate!(id, allowed, expected)
      mode = allowed[id.to_s]
      raise ValidationError, "predicate #{id} is not declared for the concept" unless mode
      valid = expected == "attr" ? mode == "attr" : %w[assertion external].include?(mode)
      raise ValidationError, "predicate #{id} is registered as #{mode}, not #{expected}" unless valid
    end

    def validate_confidence!(value)
      number = Float(value)
      raise ValidationError, "confidence must be between 0 and 1" unless number.between?(0.0, 1.0)
      minimum = @profile.fetch("minimum_confidence", 0.0).to_f
      raise ValidationError, "confidence #{number} is below profile minimum #{minimum}" if number < minimum
    rescue ArgumentError, TypeError
      raise ValidationError, "confidence must be numeric"
    end

    def validate_evidence!(items)
      raise ValidationError, "evidence must be an array" unless items.is_a?(Array)
      evidence = items
      if @profile.fetch("require_evidence", true) && evidence.empty?
        raise ValidationError, "evidence is required"
      end
      segments = @source["segments"].each_with_object({}) { |segment, output| output[segment["id"]] = segment["text"] }
      evidence.each do |item|
        raise ValidationError, "evidence item must be an object" unless item.is_a?(Hash)
        segment = segments[item["segment_id"]]
        raise ValidationError, "unknown evidence segment #{item['segment_id']}" unless segment
        quote = item["quote"].to_s.strip
        raise ValidationError, "evidence quote is required" if quote.empty?
        raise ValidationError, "evidence quote is not present in #{item['segment_id']}" unless segment.include?(quote)
      end
    end

    def evidence_refs(evidence)
      Array(evidence).map { |item| "#{@source['id']}##{item['segment_id']}" }.uniq
    end

    def append_unique_assertions(current, proposed)
      signatures = current.map { |item| assertion_signature(item) }
      current + proposed.reject { |item| signatures.include?(assertion_signature(item)) }
    end

    def append_unique_relations(current, proposed)
      signatures = current.map { |item| relation_signature(item) }
      current + proposed.reject { |item| signatures.include?(relation_signature(item)) }
    end

    def update_operations(existing, entity, attrs, current_attrs, assertions, current_assertions, relations, current_relations)
      operations = []
      if entity["label"].to_s.strip != existing["label"]
        operations << { "op" => "replace", "path" => "/base/node/label", "value" => entity["label"].to_s.strip }
      end
      current_aliases = JSON.parse(existing["aliases_json"])
      aliases = (current_aliases + Array(entity["aliases"]).map(&:to_s)).reject(&:empty?).uniq
      operations << { "op" => "replace", "path" => "/base/node/aliases", "value" => aliases } if aliases != current_aliases
      attrs.each do |predicate, value|
        next if current_attrs.key?(predicate) && current_attrs[predicate] == value
        op = current_attrs.key?(predicate) ? "replace" : "add"
        operations << { "op" => op, "path" => "/knowledge/attrs/#{json_pointer(predicate)}", "value" => value }
      end
      current_assertion_signatures = current_assertions.map { |item| assertion_signature(item) }
      assertions.each do |assertion|
        next if current_assertion_signatures.include?(assertion_signature(assertion))
        operations << { "op" => "add", "path" => "/knowledge/assertions/-", "value" => assertion }
      end
      current_relation_signatures = current_relations.map { |item| relation_signature(item) }
      relations.each do |relation|
        next if current_relation_signatures.include?(relation_signature(relation))
        operations << { "op" => "add", "path" => "/knowledge/relations/-", "value" => relation }
      end
      operations
    end

    def attribute_candidates(node_id, attrs, current_attrs, evidence)
      authority = @profile.fetch("source_authority", {}).fetch(@source["kind"], @source["kind"])
      attrs.each_with_object([]) do |(predicate, value), output|
        next if current_attrs.key?(predicate) && current_attrs[predicate] == value
        output << { "node_id" => node_id, "predicate" => predicate, "value" => value,
                    "source_ref_id" => @source["id"], "source_class" => authority,
                    "observed_at" => @source["captured_at"], "source_hash" => @source["content_hash"],
                    "evidence_refs" => evidence_refs(evidence), "evidence" => Array(evidence) }
      end
    end

    def relation_candidates(canonical_relations, extracted_relations, inherited_evidence)
      canonical_relations.each_with_index.map do |relation, index|
        evidence = Array(extracted_relations[index]["evidence"])
        evidence = Array(inherited_evidence) if evidence.empty?
        { "relation" => relation, "source_ref_id" => @source["id"],
          "source_hash" => @source["content_hash"], "evidence_refs" => evidence_refs(evidence),
          "evidence" => evidence }
      end
    end

    def assertion_signature(item)
      JSON.generate([item["predicate"], item["value"], item["qualifiers"] || {}])
    end

    def relation_signature(item)
      JSON.generate([item["predicate"], item["target"], item["temporal"] || {}])
    end

    def json_pointer(value)
      value.to_s.gsub("~", "~0").gsub("/", "~1")
    end

    def source_reference
      @source.reject { |key, _| key == "segments" }.merge(
        "authority_class" => @profile.fetch("source_authority", {}).fetch(@source["kind"], @source["kind"]),
        "segment_count" => @source["segments"].length
      )
    end

    def suggested_source(id)
      "knowledge/entities/#{id.tr(':', '-')}.md"
    end

    def canonical_schema_version
      schema = @registry.schemas["canonical-node.schema"] || @registry.schemas["canonical-node"] || {}
      schema.dig("properties", "base", "properties", "schema", "properties", "ckm", "const") || "3.0"
    end

    def rejection(index, entity, message)
      label = entity.is_a?(Hash) ? (entity["label"] || entity[:label]) : nil
      { "index" => index, "label" => label, "errors" => [message] }
    end
  end

  class ExtractionPipeline
    def initialize(registry:, database:, profile_id: "default")
      @registry = registry
      @database = database
      @profile_id = profile_id
    end

    def prepare(source, materializer: nil)
      @registry.reload!
      profile = current_profile
      envelope = SourceLoader.new(profile: profile, materializer: materializer).load(source)
      ExtractionContract.new(registry: @registry, database: @database, profile: profile).build(envelope)
    end

    def finalize(request, model_output)
      @registry.reload!
      request = Frontmatter.stringify(request)
      profile = current_profile
      contract = ExtractionContract.new(registry: @registry, database: @database, profile: profile)
      unless request["registry_fingerprint"] == contract.registry_fingerprint
        raise ValidationError, "extraction request is stale because the ontology or extraction profile changed"
      end
      CandidateBuilder.new(registry: @registry, database: @database, profile: profile, request: request).build(model_output)
    end

    def run(source, model_adapter:, materializer: nil)
      request = prepare(source, materializer: materializer)
      output = model_adapter.extract(request)
      finalize(request, output)
    end

    private

    def current_profile
      profile = @registry.extraction_profiles[@profile_id]
      raise ConfigurationError, "extraction profile not found: #{@profile_id}" unless profile
      profile
    end
  end
end
