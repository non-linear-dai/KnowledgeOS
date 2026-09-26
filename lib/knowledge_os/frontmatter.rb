# frozen_string_literal: true

require "yaml"
require "json"
require "date"

module KnowledgeOS
  ParsedDocument = Struct.new(:data, :body, :path, keyword_init: true)

  module Frontmatter
    module_function

    def parse(path)
      content = File.read(path, mode: "r:BOM|UTF-8")
      match = content.match(/\A---\s*\n(.*?)\n---\s*\n?/m)
      raise ValidationError, "#{path}: missing YAML frontmatter" unless match

      data = YAML.safe_load(
        match[1],
        permitted_classes: [Date, Time],
        permitted_symbols: [],
        aliases: false
      ) || {}
      raise ValidationError, "#{path}: frontmatter must be a mapping" unless data.is_a?(Hash)

      ParsedDocument.new(data: stringify(data), body: content[match[0].length..] || "", path: path.to_s)
    rescue Psych::Exception => e
      raise ValidationError, "#{path}: invalid YAML: #{e.message}"
    end

    def load_yaml(path)
      value = YAML.safe_load(
        File.read(path, mode: "r:BOM|UTF-8"),
        permitted_classes: [Date, Time],
        permitted_symbols: [],
        aliases: false
      )
      stringify(value || {})
    rescue Psych::Exception => e
      raise ConfigurationError, "#{path}: invalid YAML: #{e.message}"
    end

    def load_ndjson(path)
      values = []
      File.foreach(path, encoding: "bom|utf-8").with_index(1) do |line, number|
        next if line.strip.empty?

        begin
          values << stringify(JSON.parse(line))
        rescue JSON::ParserError => e
          raise ValidationError, "#{path}:#{number}: invalid NDJSON: #{e.message}"
        end
      end
      values
    end

    def stringify(value)
      case value
      when Hash
        value.each_with_object({}) { |(key, item), out| out[key.to_s] = stringify(item) }
      when Array
        value.map { |item| stringify(item) }
      when Date, Time
        value.iso8601
      else
        value
      end
    end
  end
end
