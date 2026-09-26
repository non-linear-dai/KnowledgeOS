# frozen_string_literal: true

module KnowledgeOS
  class Registry
    attr_reader :predicates, :ontology, :authority_policies, :freshness_policies,
                :provenance_policies, :maintenance_policy

    def initialize(config)
      @config = config
      reload!
    end

    def reload!
      @predicates = load_keyed(@config.predicates_dir, "id")
      @ontology = merge_yaml(@config.ontology_dir)
      @authority_policies = keyed_policy("authority.yaml")
      @freshness_policies = keyed_policy("freshness.yaml")
      @provenance_policies = keyed_policy("provenance.yaml")
      @maintenance_policy = policy("maintenance.yaml")
      self
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

    def domain(id)
      path = @config.domains_dir.join(id.to_s, "pack.yaml")
      raise NotFoundError, "domain pack not found: #{id}" unless path.file?

      Frontmatter.load_yaml(path)
    end

    private

    def item_id(item)
      item.is_a?(Hash) ? item["id"] : item.to_s
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

    def merge_yaml(dir)
      return {} unless dir.directory?

      dir.glob("**/*.{yaml,yml}").sort.each_with_object({}) do |path, output|
        Frontmatter.load_yaml(path).each do |key, value|
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

