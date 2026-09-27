# frozen_string_literal: true

module KnowledgeOS
  class SchemaValidator
    def validate!(value, schema, path = "$", root = schema)
      return true unless schema.is_a?(Hash)
      if schema["$ref"]
        reference = schema["$ref"]
        unless reference.start_with?("#/")
          raise ConfigurationError, "unsupported schema reference #{reference} at #{path}"
        end
        resolved = reference.sub("#/", "").split("/").reduce(root) { |memo, key| memo && memo[key] }
        raise ConfigurationError, "unresolved schema reference #{reference} at #{path}" unless resolved
        return validate!(value, resolved, path, root)
      end
      validate_type!(value, schema["type"], path) if schema.key?("type")
      if schema.key?("const") && value != schema["const"]
        raise ValidationError, "#{path} must equal #{schema['const'].inspect}"
      end
      if schema["enum"] && !schema["enum"].include?(value)
        raise ValidationError, "#{path} must be one of #{schema['enum'].join(', ')}"
      end
      if value.is_a?(Hash)
        Array(schema["required"]).each do |key|
          raise ValidationError, "#{path}.#{key} is required" unless value.key?(key)
        end
        properties = schema.fetch("properties", {})
        value.each do |key, item|
          child = properties[key]
          if !child && schema["additionalProperties"] == false
            raise ValidationError, "#{path}.#{key} is not allowed"
          end
          validate!(item, child, "#{path}.#{key}", root) if child
        end
      elsif value.is_a?(Array) && schema["items"]
        value.each_with_index { |item, index| validate!(item, schema["items"], "#{path}[#{index}]", root) }
        if schema["minItems"] && value.length < schema["minItems"].to_i
          raise ValidationError, "#{path} requires at least #{schema['minItems']} items"
        end
      elsif value.is_a?(String) && schema["minLength"] && value.length < schema["minLength"].to_i
        raise ValidationError, "#{path} is too short"
      elsif value.is_a?(Numeric) && schema["minimum"] && value < schema["minimum"]
        raise ValidationError, "#{path} must be at least #{schema['minimum']}"
      end
      true
    end

    private

    def validate_type!(value, expected, path)
      types = Array(expected)
      valid = types.any? do |type|
        case type
        when "object" then value.is_a?(Hash)
        when "array" then value.is_a?(Array)
        when "string" then value.is_a?(String)
        when "integer" then value.is_a?(Integer)
        when "number" then value.is_a?(Numeric)
        when "boolean" then value == true || value == false
        when "null" then value.nil?
        else true
        end
      end
      raise ValidationError, "#{path} must be #{types.join(' or ')}" unless valid
    end
  end
end
