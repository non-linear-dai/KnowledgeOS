# frozen_string_literal: true

module KnowledgeOS
  class ConstraintEngine
    def initialize(definitions)
      @definitions = definitions
    end

    def validate_document!(data, path)
      @definitions.each do |definition|
        Array(definition.dig("document", "unique")).each do |constraint|
          values = dig_path(data, constraint.fetch("path"))
          next unless values.is_a?(Array)
          key = constraint.fetch("key")
          selected = values.map { |item| item.is_a?(Hash) ? item[key] : nil }
          selected = selected.reject { |value| value.nil? || value.to_s.empty? } if constraint["ignore_blank"]
          duplicate = selected.group_by(&:itself).find { |_value, occurrences| occurrences.length > 1 }&.first
          raise ValidationError, "#{path}: duplicate #{key} #{duplicate} in #{constraint['path']}" if duplicate
        end
      end
      true
    end

    private

    def dig_path(value, path)
      path.to_s.split(".").reduce(value) { |memo, key| memo.is_a?(Hash) ? memo[key] : nil }
    end
  end
end
