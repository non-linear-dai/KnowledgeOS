# frozen_string_literal: true

module KnowledgeOS
  class Registry
    TOOL_OPERATIONS = %w[
      resolve get query neighbors history search calculate explain context
      extraction_request extraction_candidates propose review
      agent_capabilities agent_skill agent_request agent_respond agent_invoke
    ].freeze

    attr_reader :predicates, :ontology, :authority_policies, :freshness_policies,
                :provenance_policies, :maintenance_policy, :models, :domains,
                :retrieval_profiles, :schemas, :connectors, :extraction_profiles,
                :constraints, :rules, :skills

    def initialize(config)
      @config = config
      reload!
    end

    def reload!
      previous = instance_variables.to_h { |name| [name, instance_variable_get(name)] }
      before = source_fingerprint
      @predicates = load_keyed(@config.predicates_dir, "id")
      @ontology = merge_yaml(@config.ontology_dir)
      @authority_policies = keyed_policy("authority.yaml")
      @freshness_policies = keyed_policy("freshness.yaml")
      @provenance_policies = keyed_policy("provenance.yaml")
      @maintenance_policy = policy("maintenance.yaml")
      @models = load_keyed(@config.models_dir, "id")
      @domains = load_domains
      @retrieval_profiles = load_retrieval_profiles
      @schemas = load_json_files(@config.schemas_dir)
      @connectors = load_connector_mappings
      @extraction_profiles = load_keyed(@config.extraction_dir, "id")
      @constraints = load_documents(@config.constraints_dir)
      @rules = load_documents(@config.rules_dir)
      @skills = load_skill_packages(@config.skills_dir)
      validate_control_plane!
      after = source_fingerprint
      raise ConflictError, 'control sources changed while loading; retry' unless before == after
      @fingerprint = after
      self
    rescue StandardError
      previous.each { |name, value| instance_variable_set(name, value) }
      raise
    end

    def fingerprint
      @fingerprint
    end

    def source_fingerprint
      paths = (@config.control_dir.glob("**/*").select(&:file?) + @config.connectors_dir.glob('**/*.mapping.{yaml,yml}')).sort
      Digest::SHA256.hexdigest(paths.map { |path| [path.relative_path_from(@config.root).to_s, Digest::SHA256.file(path).hexdigest] }.to_json)
    end

    def snapshot
      instance_variables.to_h { |name| [name, instance_variable_get(name)] }
    end

    def restore!(snapshot)
      snapshot.each { |name, value| instance_variable_set(name, value) }
    end

    def predicate(id)
      predicates[id.to_s]
    end

    def normalize_predicate(id)
      current = predicate(id)
      return id.to_s unless current

      status = current.fetch("status", {})
      status["lifecycle"] == "deprecated" && status["equivalent_to"] ? status["equivalent_to"] : id.to_s
    end

    def type?(id)
      Array(ontology["concept_types"]).any? { |item| item_id(item) == id.to_s }
    end

    def relation?(id)
      Array(ontology["relation_types"]).any? { |item| item_id(item) == id.to_s }
    end

    def concept(id)
      Array(ontology["concept_types"]).find { |item| item_id(item) == id.to_s }
    end

    def relation(id)
      Array(ontology["relation_types"]).find { |item| item_id(item) == id.to_s }
    end

    def model(id)
      models[id.to_s]
    end

    def domain(id)
      domains[id.to_s] || raise(NotFoundError, "domain pack not found: #{id}")
    end

    def skill(id)
      skills.find { |item| item["id"] == id.to_s } || raise(NotFoundError, "skill not found: #{id}")
    end

    def skills_for_domain(id)
      skills.select { |item| item["domain"] == id.to_s }
    end

    def skill_bundle(id)
      definition = skill(id)
      package_dir = @config.root.join(definition.dig("package", "path"))
      files = %w[SKILL.md contract.yaml].each_with_object({}) do |name, output|
        path = package_dir.join(name)
        raise ConfigurationError, "skill #{id}: package file missing: #{name}" unless path.file?
        output[name] = path.read
      end
      { "id" => definition["id"], "package" => definition["package"],
        "definition" => definition, "files" => files }
    end

    def control_plane
      {
        "contract_version" => "3.5",
        "ontology" => ontology,
        "predicates" => predicates.transform_values { |item| without_internal(item) },
        "policies" => {
          "authority" => authority_policies,
          "freshness" => freshness_policies,
          "provenance" => provenance_policies,
          "maintenance" => maintenance_policy
        },
        "models" => models.transform_values { |item| without_internal(item) },
        "domains" => domains.transform_values { |item| without_internal(item) },
        "retrieval_profiles" => retrieval_profiles,
        "schemas" => schemas,
        "connectors" => connectors,
        "extraction_profiles" => extraction_profiles.transform_values { |item| without_internal(item) },
        "extensions" => {
          "constraints" => constraints,
          "rules" => rules,
          "skills" => skills
        },
        "capabilities" => {
          "tool_operations" => TOOL_OPERATIONS,
          "durable_writes" => "changeset_only"
        }
      }
    end

    def studio_catalog
      definitions = []
      definitions.concat(Array(ontology["concept_types"]).map { |item| studio_concept(item) })
      definitions.concat(Array(ontology["relation_types"]).map { |item| studio_relation(item) })
      definitions.concat(predicates.values.map { |item| studio_predicate(item) })
      definitions.concat(studio_policies)
      definitions.concat(models.values.map { |item| studio_model(item) })
      definitions.concat(domains.values.map { |item| studio_domain(item) })
      definitions.concat(connectors.values.map { |item| studio_connector(item) })
      definitions.concat(schemas.map { |name, item| studio_schema(name, item) })

      {
        "contract_version" => "3.5",
        "definitions" => definitions,
        "coverage" => definitions.group_by { |item| item["kind"] }.transform_values(&:length),
        "extensions" => {
          "constraints" => constraints.length,
          "rules" => rules.length,
          "skills" => skills.length
        },
        "capabilities" => {
          "tool_operations" => TOOL_OPERATIONS,
          "durable_writes" => "changeset_only",
          "review_decisions" => %w[approved rejected changes_requested]
        }
      }
    end

    private

    def studio_base(id:, kind:, label:, description:, lifecycle:, source_path:, config:, read_only: false)
      {
        "id" => id,
        "kind" => kind,
        "label" => label.to_s,
        "description" => description.to_s,
        "lifecycle" => lifecycle || "active",
        "source_path" => source_path,
        "refs" => 1,
        "files" => [source_path],
        "config" => config,
        "read_only" => read_only
      }
    end

    def studio_concept(item)
      studio_base(
        id: item.fetch("id"), kind: "concept", label: item.fetch("label", item["id"]),
        description: item["description"], lifecycle: item.dig("status", "lifecycle"),
        source_path: item.fetch("_source_path"), config: { "schema_support" => "canonical_node" }
      ).merge(
        "bindings" => Array(item["properties"]).map do |binding|
          {
            "predicate_id" => binding.fetch("predicate"),
            "required" => binding.fetch("required", false),
            "cardinality" => binding.fetch("cardinality", "inherit"),
            "group" => binding.fetch("group", "other")
          }
        end
      )
    end

    def studio_relation(item)
      mode = item.fetch("mode", "simple")
      output = studio_base(
        id: item.fetch("id"), kind: "relation", label: item.fetch("label", item["id"]),
        description: item["description"], lifecycle: item.dig("status", "lifecycle"),
        source_path: item.fetch("_source_path"),
        config: { "domain_range" => Array(item["connections"]).empty? ? "undeclared" : "declared", "relation_mode" => mode, "schema_support" => "canonical_node" }
      ).merge(
        "relation_mode" => mode,
        "endpoints" => Array(item["connections"]).map do |endpoint|
          {
            "source_concept_id" => endpoint.fetch("source_type"),
            "target_concept_id" => endpoint.fetch("target_type"),
            "source_cardinality" => endpoint.fetch("source_cardinality", "many"),
            "target_cardinality" => endpoint.fetch("target_cardinality", "many")
          }
        end
      )
      reification = item["reification"]
      return output unless reification

      output["reification"] = {
        "node_type" => reification.fetch("node_type"),
        "identity" => reification.fetch("identity", "optional"),
        "properties" => Array(reification["properties"]).map do |binding|
          {
            "predicate_id" => binding.fetch("predicate"),
            "required" => binding.fetch("required", false),
            "cardinality" => binding.fetch("cardinality", "inherit"),
            "group" => binding.fetch("group", "other")
          }
        end
      }
      output
    end

    def studio_predicate(item)
      source_path = relative_path(item.fetch("_path"))
      config = {
        "value_type" => item.dig("value", "type"),
        "cardinality" => item.dig("value", "cardinality"),
        "storage_mode" => item.dig("storage", "mode")
      }.merge(item.fetch("policy", {})).merge("equivalent_to" => item.dig("status", "equivalent_to"))
      studio_base(
        id: item.fetch("id"), kind: "predicate", label: item.fetch("label", item["id"]),
        description: item.dig("semantics", "description"), lifecycle: item.dig("status", "lifecycle"),
        source_path: source_path, config: config
      )
    end

    def studio_policies
      [
        ["authority", "Authority policies", "Source priority and conflict handling.", authority_policies, "control/policies/authority.yaml"],
        ["freshness", "Freshness policies", "Freshness windows for stable and operational knowledge.", freshness_policies, "control/policies/freshness.yaml"],
        ["provenance", "Provenance policies", "Evidence and source requirements for provenance tiers.", provenance_policies, "control/policies/provenance.yaml"],
        ["maintenance", "Maintenance policy", "Review budgets, priority thresholds, storage tiers, and externalization limits.", maintenance_policy, "control/policies/maintenance.yaml"]
      ].map do |id, label, description, config, source_path|
        studio_base(id: "policy:#{id}", kind: "policy", label: label, description: description,
                    lifecycle: "active", source_path: source_path, config: config, read_only: true)
      end
    end

    def studio_model(item)
      model_id = item.fetch("id")
      config = without_internal(item).merge("model_id" => model_id)
      studio_base(
        id: "model:#{model_id}", kind: "model", label: item.fetch("label", item["id"]),
        description: item["description"], lifecycle: "active", source_path: relative_path(item.fetch("_path")),
        config: config, read_only: true
      )
    end

    def studio_domain(item)
      id = item.fetch("id")
      config = without_internal(item).merge("retrieval_profile" => retrieval_profiles[id])
      studio_base(
        id: "domain:#{id}", kind: "domain", label: item.fetch("label", id),
        description: "Domain workflow, retrieval, model, and tool policy.", lifecycle: "active",
        source_path: relative_path(item.fetch("_path")), config: config, read_only: true
      ).merge("concept_scopes" => Array(item["concept_scopes"]))
    end

    def studio_connector(item)
      name = item.fetch("_name")
      studio_base(
        id: "connector:#{name.tr('-', '_')}", kind: "connector", label: name,
        description: "Deterministic mapping from #{item['source_system']} #{item['source_object']} into the canonical contract.",
        lifecycle: "active", source_path: relative_path(item.fetch("_path")), config: without_internal(item), read_only: true
      )
    end

    def studio_schema(name, item)
      schema_id = name.sub(/\.schema\z/, "").tr("-", "_")
      base = item.dig("properties", "base") || {}
      node = base.dig("properties", "node") || {}
      knowledge = item.dig("properties", "knowledge") || {}
      config = {
        "version" => base.dig("properties", "schema", "properties", "ckm", "const"),
        "required_root" => Array(item["required"]),
        "required_base" => Array(base["required"]),
        "required_node" => Array(node["required"]),
        "required_knowledge" => Array(knowledge["required"]),
        "external_assertions" => !item.dig("properties", "external", "properties", "assertions_ref").nil?
      }
      studio_base(
        id: "schema:#{schema_id}", kind: "schema", label: item.fetch("title", name),
        description: item["description"], lifecycle: "active", source_path: "control/schemas/#{name}.json",
        config: config, read_only: true
      )
    end

    def relative_path(path)
      Pathname.new(path).relative_path_from(@config.root).to_s
    end

    def item_id(item)
      item.is_a?(Hash) ? item["id"] : item.to_s
    end

    def validate_control_plane!
      validate_ontology!
      validate_models!
      validate_extensions!
      predicates.each_value do |item|
        policy = item.fetch("policy", {})
        validate_reference!(authority_policies, policy["authority"], "authority policy", item["id"])
        validate_reference!(freshness_policies, policy["freshness"], "freshness policy", item["id"])
        validate_reference!(provenance_policies, policy["provenance_tier"], "provenance policy", item["id"])
        unless %w[attr assertion external].include?(item.dig("storage", "mode"))
          raise ConfigurationError, "predicate #{item['id']}: invalid storage mode #{item.dig('storage', 'mode')}"
        end
        unless %w[one many temporal_many].include?(item.dig("value", "cardinality"))
          raise ConfigurationError, "predicate #{item['id']}: invalid cardinality #{item.dig('value', 'cardinality')}"
        end
        unless %w[auto reviewed].include?(policy["write"])
          raise ConfigurationError, "predicate #{item['id']}: invalid write policy #{policy['write']}"
        end
      end
      domains.each_value do |item|
        Array(item["concept_scopes"]).each { |id| raise ConfigurationError, "domain #{item['id']}: unknown concept #{id}" unless type?(id) }
        Array(item.dig("retrieval", "relation_types")).each { |id| raise ConfigurationError, "domain #{item['id']}: unknown relation #{id}" unless relation?(id) }
        Array(item.dig("retrieval", "predicate_priority")).each { |id| raise ConfigurationError, "domain #{item['id']}: unknown predicate #{id}" unless predicate(id) }
        Array(item["required_models"]).each { |id| raise ConfigurationError, "domain #{item['id']}: unknown model #{id}" unless model(id) }
        Array(item.dig("tool_policy", "allow")).each { |id| raise ConfigurationError, "domain #{item['id']}: unknown tool operation #{id}" unless TOOL_OPERATIONS.include?(id) }
      end
      validate_skills!
      connectors.each_value do |item|
        type = item.dig("node", "type")
        raise ConfigurationError, "connector #{item['_name']}: unknown node type #{type}" unless type?(type)
        (item.fetch("attrs", {}).keys + item.fetch("assertions", {}).keys).each do |id|
          raise ConfigurationError, "connector #{item['_name']}: unknown predicate #{id}" unless predicate(id)
        end
      end
      extraction_profiles.each_value do |item|
        Array(item["source_types"]).each do |kind|
          unless kind.to_s.match?(/\A[a-z][a-z0-9_]*\z/)
            raise ConfigurationError, "extraction profile #{item['id']}: invalid source type #{kind}"
          end
        end
      end
      true
    end

    def validate_skills!
      ids = {}
      skills.each do |item|
        id = item["id"].to_s
        raise ConfigurationError, "skill is missing id" if id.empty?
        raise ConfigurationError, "duplicate skill: #{id}" if ids[id]
        ids[id] = true
        unless item["format"] == "knowledgeos.skill-contract.v1"
          raise ConfigurationError, "skill #{id}: unsupported contract format #{item['format']}"
        end
        unless item.dig("package", "entrypoint") == "SKILL.md"
          raise ConfigurationError, "skill #{id}: entrypoint must be SKILL.md"
        end
        unless item["entrypoint"] == "SKILL.md"
          raise ConfigurationError, "skill #{id}: contract entrypoint must be SKILL.md"
        end
        if item.dig("agent", "name") != id
          raise ConfigurationError, "skill #{id}: SKILL.md name must match contract id"
        end
        if item.dig("agent", "instructions").to_s.strip.empty?
          raise ConfigurationError, "skill #{id}: SKILL.md instructions are empty"
        end
        domain_id = item["domain"].to_s
        pack = domains[domain_id] || raise(ConfigurationError, "skill #{id}: unknown domain #{domain_id}")
        allowed = Array(pack.dig("tool_policy", "allow"))
        Array(item["tools"]).each do |tool|
          raise ConfigurationError, "skill #{id}: tool #{tool} is not allowed by domain #{domain_id}" unless allowed.include?(tool)
        end
        Array(item["deterministic_models"]).each do |model_id|
          raise ConfigurationError, "skill #{id}: unknown model #{model_id}" unless model(model_id)
        end
      end
      domains.each_value do |pack|
        default = pack["default_skill"].to_s
        next if default.empty?
        selected = skills.find { |item| item["id"] == default }
        raise ConfigurationError, "domain #{pack['id']}: unknown default skill #{default}" unless selected
        raise ConfigurationError, "domain #{pack['id']}: default skill belongs to #{selected['domain']}" unless selected["domain"] == pack["id"]
      end
    end

    def validate_ontology!
      concept_ids = Array(ontology["concept_types"]).map { |item| item_id(item) }
      relation_ids = Array(ontology["relation_types"]).map { |item| item_id(item) }
      raise ConfigurationError, "duplicate ontology concept id" unless concept_ids.uniq.length == concept_ids.length
      raise ConfigurationError, "duplicate ontology relation id" unless relation_ids.uniq.length == relation_ids.length

      Array(ontology["concept_types"]).each do |item|
        Array(item["properties"]).each do |binding|
          raise ConfigurationError, "concept #{item_id(item)}: unknown predicate #{binding['predicate']}" unless predicate(binding["predicate"])
          unless %w[inherit one many temporal_many].include?(binding.fetch("cardinality", "inherit"))
            raise ConfigurationError, "concept #{item_id(item)}: invalid property cardinality #{binding['cardinality']}"
          end
        end
      end
      Array(ontology["relation_types"]).each do |item|
        mode = item.fetch("mode", "simple")
        raise ConfigurationError, "relation #{item_id(item)}: invalid mode #{mode}" unless %w[simple reifiable reified].include?(mode)
        Array(item["connections"]).each do |endpoint|
          %w[source_type target_type].each do |field|
            raise ConfigurationError, "relation #{item_id(item)}: unknown #{field} #{endpoint[field]}" unless concept_ids.include?(endpoint[field])
          end
          %w[source_cardinality target_cardinality].each do |field|
            value = endpoint.fetch(field, "many")
            raise ConfigurationError, "relation #{item_id(item)}: invalid #{field} #{value}" unless %w[one many].include?(value)
          end
        end
        reification = item["reification"]
        if mode != "simple" && !reification
          raise ConfigurationError, "relation #{item_id(item)}: #{mode} mode requires reification metadata"
        end
        if mode == "simple" && reification
          raise ConfigurationError, "relation #{item_id(item)}: simple mode cannot declare reification metadata"
        end
        next unless reification
        raise ConfigurationError, "relation #{item_id(item)}: unknown reification node type #{reification['node_type']}" unless concept_ids.include?(reification["node_type"])
        Array(reification["properties"]).each do |binding|
          raise ConfigurationError, "relation #{item_id(item)}: unknown reification predicate #{binding['predicate']}" unless predicate(binding["predicate"])
        end
      end
    end

    def validate_models!
      models.each_value do |model|
        raise ConfigurationError, "model #{model['id']}: version is required" if model["version"].to_s.empty?
        input_ids = Array(model["inputs"]).map { |input| input["id"].to_s }
        raise ConfigurationError, "model #{model['id']}: duplicate input id" unless input_ids.uniq.length == input_ids.length
        unless model.fetch("rounding", "half_up") == "half_up"
          raise ConfigurationError, "model #{model['id']}: unsupported rounding #{model['rounding']}"
        end
        validate_formula!(model.fetch("formula"), input_ids, model["id"])
        Array(model["bands"]).each do |band|
          raise ConfigurationError, "model #{model['id']}: band id is required" if band["id"].to_s.empty?
        end
      end
    end

    def validate_formula!(node, input_ids, model_id)
      raise ConfigurationError, "model #{model_id}: formula node must be a mapping" unless node.is_a?(Hash)
      op = node["op"]
      allowed = %w[input const add sum multiply subtract divide date_diff_days]
      raise ConfigurationError, "model #{model_id}: unsupported operation #{op}" unless allowed.include?(op)
      if op == "input"
        raise ConfigurationError, "model #{model_id}: unknown formula input #{node['id']}" unless input_ids.include?(node["id"].to_s)
      end
      Array(node["args"]).each { |child| validate_formula!(child, input_ids, model_id) }
      %w[left right start end].each do |key|
        validate_formula!(node[key], input_ids, model_id) if node[key]
      end
    end

    def validate_extensions!
      constraints.each do |definition|
        unless definition["format"] == "knowledgeos.constraints.v1" && !definition["id"].to_s.empty?
          raise ConfigurationError, "invalid constraint definition #{definition['id']}"
        end
      end
      rules.each do |definition|
        unless definition["format"] == "knowledgeos.maintenance-rules.v1" && !definition["id"].to_s.empty?
          raise ConfigurationError, "invalid rule definition #{definition['id']}"
        end
      end
    end

    def validate_reference!(registry, id, label, owner)
      raise ConfigurationError, "predicate #{owner}: unknown #{label} #{id}" unless registry.key?(id)
    end

    def load_keyed(dir, key)
      return {} unless dir.directory?

      dir.glob("**/*.{yaml,yml}").sort.each_with_object({}) do |path, output|
        item = Frontmatter.load_yaml(path)
        id = item[key]
        raise ConfigurationError, "#{path}: missing #{key}" if id.to_s.empty?
        raise ConfigurationError, "duplicate #{key}: #{id}" if output.key?(id)

        output[id] = item.merge("_path" => path.to_s)
      end
    end

    def load_domains
      return {} unless @config.domains_dir.directory?
      @config.domains_dir.glob("*/pack.yaml").sort.each_with_object({}) do |path, output|
        item = Frontmatter.load_yaml(path)
        id = item.fetch("id")
        raise ConfigurationError, "duplicate domain: #{id}" if output.key?(id)
        output[id] = item.merge("_path" => path.to_s)
      end
    end

    def load_retrieval_profiles
      return {} unless @config.domains_dir.directory?
      @config.domains_dir.glob("*/retrieval_profile.yaml").sort.each_with_object({}) do |path, output|
        output[path.dirname.basename.to_s] = Frontmatter.load_yaml(path)
      end
    end

    def load_json_files(dir)
      return {} unless dir.directory?
      dir.glob("**/*.json").sort.each_with_object({}) do |path, output|
        schema = JSON.parse(path.read)
        SchemaValidator.new.check_schema!(schema)
        output[path.basename(".json").to_s] = schema
      end
    end

    def load_connector_mappings
      return {} unless @config.connectors_dir.directory?
      @config.connectors_dir.glob("**/*.mapping.{yaml,yml}").sort.each_with_object({}) do |path, output|
        item = Frontmatter.load_yaml(path)
        name = path.basename.sub_ext("").sub_ext("").to_s
        output[name] = item.merge("_name" => name, "_path" => path.to_s)
      end
    end

    def load_documents(dir)
      return [] unless dir.directory?
      dir.glob("**/*.{yaml,yml,json}").sort.map do |path|
        path.extname == ".json" ? JSON.parse(path.read) : Frontmatter.load_yaml(path)
      end
    end

    def load_skill_packages(dir)
      return [] unless dir.directory?

      dir.children.select(&:directory?).sort.map do |package_dir|
        contract_path = package_dir.join("contract.yaml")
        skill_path = package_dir.join("SKILL.md")
        raise ConfigurationError, "#{package_dir}: missing contract.yaml" unless contract_path.file?
        raise ConfigurationError, "#{package_dir}: missing SKILL.md" unless skill_path.file?

        contract = Frontmatter.load_yaml(contract_path)
        document = Frontmatter.parse(skill_path)
        package_name = package_dir.basename.to_s
        id = contract["id"].to_s
        raise ConfigurationError, "#{contract_path}: missing id" if id.empty?
        raise ConfigurationError, "skill package directory #{package_name} must match id #{id}" unless package_name == id

        description = document.data["description"].to_s.strip
        raise ConfigurationError, "#{skill_path}: missing description" if description.empty?
        contract.merge(
          "description" => description,
          "package" => {
            "format" => "portable-skill-directory",
            "path" => relative_path(package_dir),
            "entrypoint" => "SKILL.md",
            "contract" => "contract.yaml"
          },
          "agent" => {
            "name" => document.data["name"],
            "metadata" => document.data.fetch("metadata", {}),
            "instructions" => document.body.strip
          }
        )
      rescue ValidationError => e
        raise ConfigurationError, e.message
      end
    end

    def without_internal(item)
      item.reject { |key, _| key.start_with?("_") }
    end

    def merge_yaml(dir)
      return {} unless dir.directory?

      dir.glob("**/*.{yaml,yml}").sort.each_with_object({}) do |path, output|
        Frontmatter.load_yaml(path).each do |key, value|
          if %w[concept_types relation_types].include?(key) && value.is_a?(Array)
            value = value.map { |item| item.merge("_source_path" => relative_path(path)) }
          end
          if output[key].is_a?(Array) && value.is_a?(Array)
            output[key] += value
          elsif output[key].is_a?(Hash) && value.is_a?(Hash)
            output[key] = output[key].merge(value)
          else
            output[key] = value
          end
        end
      end
    end

    def policy(name)
      path = @config.policies_dir.join(name)
      path.file? ? Frontmatter.load_yaml(path) : {}
    end

    def keyed_policy(name)
      data = policy(name)
      items = data.values.find { |value| value.is_a?(Array) } || []
      items.each_with_object({}) { |item, out| out[item.fetch("id")] = item }
    end
  end
end
