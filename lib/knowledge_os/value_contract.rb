# frozen_string_literal: true

require 'bigdecimal'
require 'date'

module KnowledgeOS
  module ValueContract
    module_function

    def validate!(predicate, raw, database: nil)
      raise ValidationError, 'unknown predicate in value contract' unless predicate
      if predicate.dig('storage', 'mode') == 'attr' && predicate.dig('value', 'cardinality') == 'many'
        raise ValidationError, "#{predicate['id']} requires an array" unless raw.is_a?(Array)
        scalar = predicate.merge('value' => predicate['value'].merge('cardinality' => 'one'))
        raw.each { |item| validate!(scalar, item, database: database) }
        return true
      end
      type = predicate.dig('value', 'type')
      value = raw.is_a?(Hash) ? raw.fetch('literal', raw.fetch('ref', raw)) : raw
      if raw.is_a?(Hash) && raw['type'] && raw['type'] != type
        raise ValidationError, "#{predicate['id']}: value type does not match predicate"
      end
      valid = case type
              when 'string', 'text', 'enum' then value.is_a?(String)
              when 'number', 'quantity' then value.is_a?(Numeric) && value.finite?
              when 'decimal', 'currency' then decimal(value).finite?
              when 'boolean' then value == true || value == false
              when 'date' then value.is_a?(String) && value.match?(/\A\d{4}-\d{2}-\d{2}\z/) && Date.iso8601(value)
              when 'datetime' then Temporal.instant(value)
              when 'node_ref'
                value.is_a?(String) && (!database || database.first('SELECT id FROM node WHERE id = ?', [value]))
              when 'json' then true
              else false
              end
      raise ValidationError, "#{predicate['id']} expects #{type}" unless valid
      if %w[quantity currency].include?(type)
        unit = raw.is_a?(Hash) ? raw['unit'] : nil
        unit ||= predicate.dig('value', 'default_unit')
        raise ValidationError, "#{predicate['id']} requires a unit" if unit.to_s.empty?
        allowed = Array(predicate.dig('value', 'units'))
        raise ValidationError, "#{predicate['id']}: unsupported unit #{unit}" if !allowed.empty? && !allowed.include?(unit)
      end
      allowed = Array(predicate.dig('value', 'allowed'))
      raise ValidationError, "#{predicate['id']} must be one of #{allowed.join(', ')}" if !allowed.empty? && !allowed.include?(value)
      true
    rescue ArgumentError => e
      raise ValidationError, "#{predicate['id']}: #{e.message}"
    end

    def normalize(predicate, raw)
      validate!(predicate, raw)
      if raw.is_a?(Array) && predicate.dig('storage', 'mode') == 'attr' && predicate.dig('value', 'cardinality') == 'many'
        scalar = predicate.merge('value' => predicate['value'].merge('cardinality' => 'one'))
        return raw.map { |item| normalize(scalar, item) }
      end
      return raw unless %w[quantity currency decimal].include?(predicate.dig('value', 'type'))
      result = raw.is_a?(Hash) ? raw.dup : { 'literal' => raw }
      result['type'] = predicate.dig('value', 'type')
      result['unit'] ||= predicate.dig('value', 'default_unit') if predicate.dig('value', 'type') != 'decimal'
      result['literal'] = decimal(result['literal']).to_s('F') if %w[decimal currency].include?(predicate.dig('value', 'type'))
      result
    end

    def decimal(value)
      raise ValidationError, 'decimal value must be a number or decimal string' unless value.is_a?(Numeric) || value.is_a?(String)
      number = BigDecimal(value.to_s)
      raise ValidationError, 'finite decimal required' unless number.finite?
      number
    rescue ArgumentError
      raise ValidationError, 'invalid decimal'
    end

    def model_number(raw, expected_unit)
      return decimal(raw) unless raw.is_a?(Hash)
      unit = raw.fetch('unit') { raise ValidationError, 'typed model input requires unit' }
      if raw['currency'] && unit[/\A([A-Z]{3})_/, 1] && raw['currency'] != unit[/\A([A-Z]{3})_/, 1]
        raise ValidationError, 'currency disagrees with unit'
      end
      # Only explicit, deterministic conversions; currencies are never exchanged implicitly.
      conversions = { ['minute_per_unit', 'hour_per_unit'] => [1, 60],
                      ['hour_per_unit', 'minute_per_unit'] => [60, 1],
                      ['hour', 'calendar_days'] => [1, 24],
                      ['calendar_days', 'hour'] => [24, 1] }
      factor = unit == expected_unit ? [1, 1] : conversions[[unit, expected_unit]]
      if expected_unit.to_s.start_with?('currency_') && unit.match?(/\A[A-Z]{3}_/)
        factor = [1, 1] if unit.sub(/\A[A-Z]{3}_/, 'currency_') == expected_unit
      end
      raise ValidationError, "incompatible units #{unit} and #{expected_unit}" unless factor
      decimal(raw.fetch('literal')) * factor[0] / factor[1]
    end
  end
end
