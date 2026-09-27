# frozen_string_literal: true

require 'date'
require 'uri'
require 'bigdecimal'

module KnowledgeOS
  # This is an explicit subset, not an implicit claim of full Draft support.
  class SchemaValidator
    KEYWORDS = %w[$schema $id $ref $defs title description default examples type const enum properties required additionalProperties items minItems maxItems uniqueItems minLength maxLength pattern format minimum maximum exclusiveMinimum exclusiveMaximum multipleOf oneOf anyOf allOf not].freeze
    TYPES = %w[object array string integer number boolean null].freeze

    def check_schema!(schema)
      return if schema == true || schema == false
      raise ConfigurationError, 'schema must be an object or boolean' unless schema.is_a?(Hash)
      unknown = schema.keys - KEYWORDS
      raise ConfigurationError, "unsupported schema keywords: #{unknown.join(', ')}" unless unknown.empty?
      raise ConfigurationError, 'unsupported schema type' unless (Array(schema['type']) - TYPES).empty?
      if schema['format'] && !%w[date date-time uri].include?(schema['format'])
        raise ConfigurationError, "unsupported format #{schema['format']}"
      end
      %w[properties $defs].each { |key| schema.fetch(key, {}).each_value { |child| check_schema!(child) } }
      %w[items additionalProperties not].each { |key| check_schema!(schema[key]) if schema.key?(key) }
      %w[oneOf anyOf allOf].each { |key| Array(schema[key]).each { |child| check_schema!(child) } }
      if schema['$ref'] && !schema['$ref'].start_with?('#/')
        raise ConfigurationError, 'only local JSON pointers are supported'
      end
      Regexp.new(schema['pattern']) if schema['pattern']
      true
    rescue RegexpError => e
      raise ConfigurationError, e.message
    end

    def validate!(value, schema, path = '$', root = schema, depth = 0)
      check_schema!(root) if depth.zero?
      raise ValidationError, "#{path}: schema recursion limit" if depth > 100
      return true if schema == true
      raise ValidationError, "#{path} is forbidden" if schema == false
      if schema['$ref']
        resolved = schema['$ref'].delete_prefix('#/').split('/').reduce(root) { |memo, key| memo && memo[key.gsub('~1', '/').gsub('~0', '~')] }
        raise ConfigurationError, "unresolved reference #{schema['$ref']}" unless resolved == false || resolved
        validate!(value, resolved, path, root, depth + 1)
      end
      types = Array(schema['type'])
      unless types.empty? || types.any? { |type| type_matches?(value, type) }
        raise ValidationError, "#{path} must be #{types.join(' or ')}"
      end
      raise ValidationError, "#{path}: const mismatch" if schema.key?('const') && value != schema['const']
      raise ValidationError, "#{path}: enum mismatch" if schema['enum'] && !schema['enum'].include?(value)
      %w[allOf anyOf oneOf].each do |key|
        next unless schema[key]
        count = schema[key].count { |child| matches?(value, child, path, root, depth) }
        valid = key == 'allOf' ? count == schema[key].length : key == 'oneOf' ? count == 1 : count > 0
        raise ValidationError, "#{path}: #{key} mismatch" unless valid
      end
      raise ValidationError, "#{path}: not mismatch" if schema.key?('not') && matches?(value, schema['not'], path, root, depth)
      case value
      when Hash
        Array(schema['required']).each { |key| raise ValidationError, "#{path}.#{key} is required" unless value.key?(key) }
        value.each do |key, item|
          child = schema.fetch('properties', {}).fetch(key, schema.fetch('additionalProperties', true))
          validate!(item, child, "#{path}.#{key}", root, depth + 1)
        end
      when Array
        bounds!(value.length, schema, 'minItems', 'maxItems', path)
        raise ValidationError, "#{path}: duplicate items" if schema['uniqueItems'] && value.uniq.length != value.length
        value.each_with_index { |item, i| validate!(item, schema['items'], "#{path}[#{i}]", root, depth + 1) } if schema.key?('items')
      when String
        bounds!(value.length, schema, 'minLength', 'maxLength', path)
        raise ValidationError, "#{path}: pattern mismatch" if schema['pattern'] && !Regexp.new(schema['pattern']).match?(value)
        case schema['format']
        when 'date' then raise ValidationError, "#{path}: invalid date" unless value.match?(/\A\d{4}-\d{2}-\d{2}\z/) && Date.iso8601(value)
        when 'date-time' then Temporal.instant(value)
        when 'uri' then raise ValidationError, "#{path}: absolute URI required" unless URI.parse(value).absolute?
        end
      when Numeric
        raise ValidationError, "#{path}: finite number required" unless value.finite?
        bounds!(value, schema, 'minimum', 'maximum', path)
        raise ValidationError, "#{path}: exclusive minimum" if schema.key?('exclusiveMinimum') && value <= schema['exclusiveMinimum']
        raise ValidationError, "#{path}: exclusive maximum" if schema.key?('exclusiveMaximum') && value >= schema['exclusiveMaximum']
        if schema['multipleOf']
          step = BigDecimal(schema['multipleOf'].to_s)
          raise ConfigurationError, 'multipleOf must be positive' unless step.positive?
          raise ValidationError, "#{path}: multipleOf mismatch" unless (BigDecimal(value.to_s) % step).zero?
        end
      end
      true
    rescue ArgumentError, URI::InvalidURIError => e
      raise ValidationError, "#{path}: #{e.message}"
    end

    private

    def matches?(value, schema, path, root, depth)
      validate!(value, schema, path, root, depth + 1)
    rescue ValidationError
      false
    end

    def bounds!(value, schema, lower, upper, path)
      raise ValidationError, "#{path}: below #{lower}" if schema.key?(lower) && value < schema[lower]
      raise ValidationError, "#{path}: above #{upper}" if schema.key?(upper) && value > schema[upper]
    end

    def type_matches?(value, type)
      case type
      when 'object' then value.is_a?(Hash)
      when 'array' then value.is_a?(Array)
      when 'string' then value.is_a?(String)
      when 'integer' then value.is_a?(Integer)
      when 'number' then value.is_a?(Numeric)
      when 'boolean' then value == true || value == false
      when 'null' then value.nil?
      end
    end
  end
end
