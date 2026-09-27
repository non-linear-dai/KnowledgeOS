# frozen_string_literal: true

module KnowledgeOS
  # The approved artifact is the expected semantic content of every target file.
  # Formatting-only changes are allowed; unrelated semantic/body edits are not.
  class ChangePlan
    def initialize(config, registry)
      @config, @registry = config, registry
    end

    def prepare(paths, patch, operations)
      return nil if paths.empty?
      expected = paths.to_h { |relative, file| [relative, read(file)] }
      if patch.is_a?(Hash) && patch['op'] == 'studio_batch'
        raise ValidationError, 'studio batch requires operations' if operations.empty?
        operations.each { |operation| apply_studio!(expected, operation) }
      elsif patch.is_a?(Hash) && patch['op'] == 'delete'
        expected.transform_values! { nil }
      else
        changes = patch.is_a?(Array) ? patch : [patch]
        return nil unless changes.all? { |item| item.is_a?(Hash) && %w[add replace remove].include?(item['op']) && item['path'] }
        raise ValidationError, 'multi-file patches require studio operations' unless expected.length == 1
        document = expected.values.first
        raise ValidationError, 'new documents require a root add patch' unless document || changes.first['path'] == ''
        document ||= { 'data' => nil, 'body' => '' }
        changes.each { |change| document['data'] = apply_pointer(document['data'], change) }
        expected[expected.keys.first] = document
      end
      expected.transform_values { |document| signature(document) }
    end

    def verify!(paths, expected)
      raise ValidationError, 'ChangeSet has no verifiable expected result; submit a new proposal' unless expected
      actual = paths.to_h { |relative, file| [relative, signature(read(file))] }
      raise ConflictError, 'source differs from the approved ChangeSet result' unless actual == expected
      true
    end

    private

    def read(file)
      return nil unless file
      if file.extname == '.md'
        document = Frontmatter.parse(file)
        { 'data' => document.data, 'body' => document.body }
      elsif %w[.yaml .yml].include?(file.extname)
        { 'data' => Frontmatter.load_yaml(file), 'body' => '' }
      elsif file.extname == '.json'
        { 'data' => JSON.parse(file.read), 'body' => '' }
      else
        { 'data' => file.read, 'body' => '' }
      end
    end

    def signature(document)
      document && Digest::SHA256.hexdigest(JSON.generate(canonical(document)))
    end

    def canonical(value)
      case value
      when Hash then value.keys.sort.to_h { |key| [key, canonical(value[key])] }
      when Array then value.map { |item| canonical(item) }
      else value
      end
    end

    def apply_pointer(document, operation)
      path = operation.fetch('path')
      return operation['op'] == 'remove' ? nil : operation.fetch('value') if path == ''
      raise ValidationError, 'JSON pointer must start with /' unless path.start_with?('/')
      keys = path.delete_prefix('/').split('/').map { |key| key.gsub('~1', '/').gsub('~0', '~') }
      parent = keys[0...-1].reduce(document) { |memo, key| memo.is_a?(Array) ? memo.fetch(Integer(key)) : memo.fetch(key) }
      key = parent.is_a?(Array) ? (keys.last == '-' ? parent.length : Integer(keys.last)) : keys.last
      if operation['op'] != 'add'
        exists = parent.is_a?(Array) ? key >= 0 && key < parent.length : parent.key?(key)
        raise ValidationError, 'patch target does not exist' unless exists
      end
      if operation['op'] == 'remove'
        parent.is_a?(Array) ? parent.delete_at(key) : parent.delete(key)
      elsif parent.is_a?(Array) && operation['op'] == 'add'
        raise ValidationError, 'invalid array insertion' unless key.between?(0, parent.length)
        parent.insert(key, operation.fetch('value'))
      else
        parent[key] = operation.fetch('value')
      end
      document
    rescue KeyError, IndexError, ArgumentError, NoMethodError => e
      raise ValidationError, "invalid JSON patch: #{e.message}"
    end

    def apply_studio!(documents, operation)
      after, before = operation['after'], operation['before']
      definition = after || before
      raise ValidationError, 'studio operation requires a complete before/after definition' unless definition
      path = definition['sourcePath'] || definition['source_path']
      raise ValidationError, 'operation source is outside ChangeSet targets' unless documents.key?(path)
      kind = operation['targetKind'] || operation['target_kind'] || definition['kind']
      id = operation['targetId'] || operation['target_id'] || definition['id']
      catalog = @registry.studio_catalog['definitions'].find { |item| item['id'] == id && item['kind'] == kind }
      if operation['type'] == 'create'
        raise ConflictError, 'definition already exists' if catalog
      else
        raise ConflictError, 'definition no longer exists' unless catalog
        raise ConflictError, 'definition source changed' unless catalog['source_path'] == path
      end
      if %w[concept relation].include?(kind)
        key = kind == 'concept' ? 'concept_types' : 'relation_types'
        documents[path] ||= { 'data' => { key => [] }, 'body' => '' }
        entries = documents[path]['data'][key] ||= []
        original = entries.find { |item| item['id'] == id } || {}
        entries.reject! { |item| item['id'] == id }
        entries << ontology_definition(after, original, kind) unless operation['type'] == 'delete'
        # Keep existing order; ordering itself is part of the reviewed file.
        original_order = read(@config.root.join(path))&.dig('data', key)&.map { |item| item['id'] } || []
        entries.sort_by! { |item| original_order.index(item['id']) || original_order.length }
      elsif kind == 'predicate'
        if operation['type'] == 'delete'
          documents[path] = nil
        else
          original = documents[path]&.fetch('data') || {}
          config = after.fetch('config')
          policy = config.reject { |key, _| %w[value_type cardinality storage_mode equivalent_to].include?(key) }
          data = original.merge('id' => id, 'label' => after['label'],
            'semantics' => (original['semantics'] || {}).merge('description' => after['description']),
            'value' => (original['value'] || {}).merge('type' => config['value_type'], 'cardinality' => config['cardinality']),
            'storage' => { 'mode' => config['storage_mode'] }, 'policy' => policy,
            'status' => { 'lifecycle' => after['lifecycle'], 'equivalent_to' => config['equivalent_to'] })
          documents[path] = { 'data' => data, 'body' => '' }
        end
      else
        raise ValidationError, "unsupported editable definition kind #{kind}"
      end
    end

    def bindings(items)
      Array(items).map { |item| { 'predicate' => item['predicateId'] || item['predicate_id'], 'required' => item.fetch('required', false), 'cardinality' => item.fetch('cardinality', 'inherit'), 'group' => item.fetch('group', 'other') } }
    end

    def ontology_definition(item, original, kind)
      result = original.merge('id' => item['id'], 'label' => item['label'], 'description' => item['description'],
                              'status' => (original['status'] || {}).merge('lifecycle' => item['lifecycle']))
      if kind == 'concept'
        result['properties'] = bindings(item['bindings'])
      else
        result['mode'] = item['relationMode'] || item['relation_mode']
        result['connections'] = Array(item['endpoints']).map { |edge| { 'source_type' => edge['sourceConceptId'], 'target_type' => edge['targetConceptId'], 'source_cardinality' => edge['sourceCardinality'], 'target_cardinality' => edge['targetCardinality'] } }
        if (reification = item['reification'])
          result['reification'] = { 'node_type' => reification['nodeType'], 'identity' => reification['identity'], 'properties' => bindings(reification['properties']) }
        else
          result.delete('reification')
        end
      end
      result
    end
  end
end
