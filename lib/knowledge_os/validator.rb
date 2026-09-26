# frozen_string_literal: true

require "time"

module KnowledgeOS
  class Validator
    LIFECYCLES = %w[draft active deprecated merged retired].freeze
    ASSERTION_STATUSES = %w[proposed confirmed superseded rejected stale].freeze

    def initialize(registry)
      @registry = registry
    end

    def validate_document!(document)
      data = document.data
      base = required_hash(data, "base", document.path)
      node = required_hash(base, "node", document.path)
      knowledge = required_hash(data, "knowledge", document.path)

      %w[id kind type key label].each { |key| required_value(node, key, document.path) }
      raise ValidationError, "#{document.path}: unknown ontology type #{node['type']}" unless @registry.type?(node["type"])

      lifecycle = required_hash(base, "lifecycle", document.path)["state"]
      raise ValidationError, "#{document.path}: invalid lifecycle #{lifecycle}" unless LIFECYCLES.include?(lifecycle)

      attrs = knowledge["attrs"] || {}
      assertions = knowledge["assertions"] || []
      relations = knowledge["relations"] || []
      raise ValidationError, "#{document.path}: attrs must be a mapping" unless attrs.is_a?(Hash)
      raise ValidationError, "#{document.path}: assertions must be a list" unless assertions.is_a?(Array)
      raise ValidationError, "#{document.path}: relations must be a list" unless relations.is_a?(Array)

      attrs.each { |predicate, value| validate_attr!(document.path, predicate, value) }
      assertions.each { |item| validate_assertion!(document.path, item) }
      relations.each { |item| validate_relation!(document.path, item) }
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

      tier = predicate.dig("policy", "provenance_tier") || "C"
      provenance = assertion["provenance"] || {}
      if tier == "A" && status == "confirmed"
        sources = Array(provenance["source_refs"])
        evidence = Array(provenance["evidence_refs"])
        if sources.empty? || evidence.empty?
          raise ValidationError, "#{path}: Tier A confirmed assertion #{assertion['id']} requires source_refs and evidence_refs"
        end
      elsif tier == "B" && status == "confirmed" && Array(provenance["source_refs"]).empty?
        raise ValidationError, "#{path}: Tier B confirmed assertion #{assertion['id']} requires source_refs"
      end
      validate_time!(path, assertion["temporal"] || {}, predicate)
      true
    end

    def validate_relation!(path, relation)
      raise ValidationError, "#{path}: relation must be a mapping" unless relation.is_a?(Hash)
      %w[predicate target].each { |key| required_value(relation, key, path) }
      raise ValidationError, "#{path}: unknown relation #{relation['predicate']}" unless @registry.relation?(relation["predicate"])
    end

    private

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

