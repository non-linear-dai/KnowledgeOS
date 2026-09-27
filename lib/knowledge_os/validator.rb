# frozen_string_literal: true

require "time"

module KnowledgeOS
  class Validator
    LIFECYCLES = %w[draft active deprecated merged retired].freeze
    ASSERTION_STATUSES = %w[proposed confirmed superseded rejected stale].freeze

    def initialize(registry)
      @registry = registry
      @policy_engine = PolicyEngine.new(registry: registry)
      @schema_validator = SchemaValidator.new
      @constraint_engine = ConstraintEngine.new(registry.constraints)
    end

    def validate_document!(document, assertions: nil)
      data = document.data
      schema = @registry.schemas["canonical-node.schema"] || @registry.schemas["canonical-node"]
      @schema_validator.validate!(data, schema) if schema
      @constraint_engine.validate_document!(data, document.path)
      base = required_hash(data, "base", document.path)
      node = required_hash(base, "node", document.path)
      knowledge = required_hash(data, "knowledge", document.path)

      %w[id kind type key label].each { |key| required_value(node, key, document.path) }
      raise ValidationError, "#{document.path}: unknown ontology type #{node['type']}" unless @registry.type?(node["type"])

      lifecycle = required_hash(base, "lifecycle", document.path)["state"]
      raise ValidationError, "#{document.path}: invalid lifecycle #{lifecycle}" unless LIFECYCLES.include?(lifecycle)

      attrs = knowledge["attrs"] || {}
      assertions = assertions || knowledge["assertions"] || []
      relations = knowledge["relations"] || []
      raise ValidationError, "#{document.path}: attrs must be a mapping" unless attrs.is_a?(Hash)
      raise ValidationError, "#{document.path}: assertions must be a list" unless assertions.is_a?(Array)
      raise ValidationError, "#{document.path}: relations must be a list" unless relations.is_a?(Array)

      attrs.each { |predicate, value| validate_attr!(document.path, predicate, value) }
      assertions.each { |item| validate_assertion!(document.path, item) }
      relations.each { |item| validate_relation!(document.path, item, source_type: node["type"]) }
      validate_concept_shape!(document.path, node["type"], attrs, assertions)
      validate_cardinality!(document.path, node["type"], attrs, assertions)
      validate_logic_refs!(document.path, knowledge["logic_refs"] || [])
      true
    end

    def validate_assertion!(path, assertion)
      raise ValidationError, "#{path}: assertion must be a mapping" unless assertion.is_a?(Hash)
      %w[id predicate value].each { |key| required_value(assertion, key, path) }
      predicate = predicate!(path, assertion["predicate"])
      mode = predicate.dig("storage", "mode")
      unless %w[assertion external].include?(mode)
        raise ValidationError, "#{path}: #{predicate['id']} is registered as #{mode}, not assertion"
      end
      validate_value!(path, predicate, assertion["value"])

      epistemic = assertion["epistemic"] || {}
      status = epistemic["status"] || "proposed"
      raise ValidationError, "#{path}: invalid assertion status #{status}" unless ASSERTION_STATUSES.include?(status)

      if status == "confirmed"
        errors = @policy_engine.provenance_errors(predicate["id"], assertion)
        unless errors.empty?
          raise ValidationError, "#{path}: assertion #{assertion['id']} #{errors.join('; ')}"
        end
      end
      validate_time!(path, assertion["temporal"] || {}, predicate)
      true
    end

    def validate_relation!(path, relation, source_type: nil)
      raise ValidationError, "#{path}: relation must be a mapping" unless relation.is_a?(Hash)
      %w[predicate target].each { |key| required_value(relation, key, path) }
      raise ValidationError, "#{path}: unknown relation #{relation['predicate']}" unless @registry.relation?(relation["predicate"])
      definition = @registry.relation(relation["predicate"])
      connections = Array(definition["connections"])
      if source_type && !connections.empty? && connections.none? { |item| item["source_type"] == source_type }
        raise ValidationError, "#{path}: relation #{relation['predicate']} does not allow source type #{source_type}"
      end
      mode = definition.fetch("mode", "simple")
      if mode == "reified" && relation["id"].to_s.empty?
        raise ValidationError, "#{path}: reified relation #{relation['predicate']} requires an id"
      end
      if mode == "simple" && !relation["id"].to_s.empty?
        raise ValidationError, "#{path}: simple relation #{relation['predicate']} cannot declare a reification id"
      end
    end

    private

    def validate_concept_shape!(path, type, attrs, assertions)
      concept = @registry.concept(type)
      bindings = Array(concept && concept["properties"])
      return if bindings.empty?

      used = attrs.keys.map(&:to_s) + assertions.map { |item| item["predicate"].to_s }
      allowed = bindings.map { |item| item["predicate"].to_s }
      (used - allowed).each do |predicate|
        raise ValidationError, "#{path}: predicate #{predicate} is not declared for concept #{type}"
      end
      bindings.select { |item| item["required"] }.each do |binding|
        next if used.include?(binding["predicate"].to_s)
        raise ValidationError, "#{path}: concept #{type} requires predicate #{binding['predicate']}"
      end
    end

    def validate_logic_refs!(path, logic_refs)
      raise ValidationError, "#{path}: logic_refs must be a list" unless logic_refs.is_a?(Array)
      logic_refs.each do |id|
        raise ValidationError, "#{path}: unknown logic model #{id}" unless @registry.model(id)
      end
    end

    def validate_cardinality!(path, type, attrs, assertions)
      concept = @registry.concept(type)
      Array(concept && concept["properties"]).each do |binding|
        predicate_id = binding.fetch("predicate").to_s
        predicate = @registry.predicate(predicate_id)
        cardinality = binding.fetch("cardinality", "inherit")
        cardinality = predicate.dig("value", "cardinality") if cardinality == "inherit"
        next unless cardinality == "one"
        count = (attrs.key?(predicate_id) ? 1 : 0) + assertions.count { |item| item["predicate"].to_s == predicate_id }
        raise ValidationError, "#{path}: #{predicate_id} allows at most one value" if count > 1
      end
    end

    def validate_attr!(path, id, value)
      predicate = predicate!(path, id)
      mode = predicate.dig("storage", "mode")
      raise ValidationError, "#{path}: #{id} is registered as #{mode}, not attr" unless mode == "attr"
      validate_value!(path, predicate, value)
    end

    def predicate!(path, id)
      normalized = @registry.normalize_predicate(id)
      predicate = @registry.predicate(normalized)
      raise ValidationError, "#{path}: unknown predicate #{id}" unless predicate
      predicate
    end

    def validate_value!(path, predicate, raw)
      value = raw.is_a?(Hash) && raw.key?("literal") ? raw["literal"] : raw
      value = raw["ref"] if raw.is_a?(Hash) && raw.key?("ref")
      type = predicate.dig("value", "type")
      valid = case type
              when "string", "text", "date", "enum", "node_ref" then value.is_a?(String)
              when "number", "quantity" then value.is_a?(Numeric)
              when "boolean" then value == true || value == false
              when "json" then true
              else false
              end
      raise ValidationError, "#{path}: #{predicate['id']} expects #{type}" unless valid
      allowed = Array(predicate.dig("value", "allowed"))
      if !allowed.empty? && !allowed.include?(value)
        raise ValidationError, "#{path}: #{predicate['id']} must be one of #{allowed.join(', ')}"
      end
    end

    def validate_time!(path, temporal, predicate)
      required = predicate.dig("policy", "temporal") == "required"
      if required && !temporal.values_at("observed_at", "valid_from", "valid_to").any?
        raise ValidationError, "#{path}: #{predicate['id']} requires temporal metadata"
      end
      temporal.each do |key, value|
        next if value.nil? || value.to_s.empty?
        Time.iso8601(value.to_s)
      rescue ArgumentError
        raise ValidationError, "#{path}: invalid #{key} timestamp #{value}"
      end
    end

    def required_hash(parent, key, path)
      value = parent[key]
      raise ValidationError, "#{path}: #{key} must be a mapping" unless value.is_a?(Hash)
      value
    end

    def required_value(parent, key, path)
      value = parent[key]
      raise ValidationError, "#{path}: missing #{key}" if value.nil? || value.to_s.empty?
      value
    end
  end
end
