# frozen_string_literal: true

require "json"
require "digest"
require "time"

module KnowledgeOS
  class Connector
    def initialize(config:, registry:, database:, ledger:)
      @config = config
      @registry = registry
      @database = database
      @ledger = ledger
      @audit = AuditCoordinator.new(database: database, ledger: ledger)
      @policy_engine = PolicyEngine.new(registry: registry)
    end

    def ingest_ndjson(input_path, mapping_path, isolate_errors: false)
      mapping = Frontmatter.load_yaml(mapping_path)
      source_system = mapping.fetch("source_system")
      source_object = mapping.fetch("source_object")
      records = Frontmatter.load_ndjson(input_path)
      counters = { "records" => records.length, "ingested" => 0, "skipped" => 0, "rejected" => [] }

      records.each do |record|
        record_id = nil
        record_id = fetch_field(record, mapping.fetch("record_id_field")).to_s
        version = mapping["version_field"] ? fetch_field(record, mapping["version_field"]).to_s : nil
        source_hash = Digest::SHA256.hexdigest(JSON.generate(canonical(record)))
        source_ref_id = [source_system, source_object, record_id].join(":")
        existing = @database.first("SELECT source_hash, source_version, source_timestamp FROM source_ref WHERE id = ?", source_ref_id)
        if existing && existing["source_hash"] == source_hash && existing["source_version"].to_s == version.to_s
          counters["skipped"] += 1
          next
        end
        if existing && mapping['timestamp_field']
          incoming = Temporal.instant(fetch_field(record, mapping['timestamp_field']))
          if incoming < Temporal.instant(existing['source_timestamp'])
            counters['skipped'] += 1
            next
          end
          if incoming == Temporal.instant(existing['source_timestamp']) && source_hash != existing['source_hash']
            numeric_newer = version.to_s.match?(/\A\d+\z/) && existing['source_version'].to_s.match?(/\A\d+\z/) && version.to_i > existing['source_version'].to_i
            raise ConflictError, 'conflicting connector version at the same timestamp' unless numeric_newer
          end
        end
        ingest_record!(mapping, record, record_id, version, source_hash, source_ref_id)
        counters["ingested"] += 1
      rescue ValidationError => e
        raise unless isolate_errors
        counters['rejected'] << { 'record_id' => record_id, 'error' => e.message }
      end
      counters
    end

    def replay!
      @database.execute('SELECT * FROM connector_record ORDER BY observed_at, source_ref_id, CAST(version AS INTEGER)').each do |row|
        mapping = JSON.parse(row['mapping_json'])
        record = JSON.parse(row['record_json'])
        ingest_record!(mapping, record, fetch_field(record, mapping.fetch('record_id_field')).to_s,
                       row['version'], row['source_hash'], row['source_ref_id'], replay: true)
      end
    end

    private

    def ingest_record!(mapping, record, record_id, version, source_hash, source_ref_id, replay: false)
      now = Time.now.utc.iso8601(6)
      timestamp = mapping["timestamp_field"] ? Temporal.instant(fetch_field(record, mapping["timestamp_field"])) : now
      authority = mapping.fetch("authority_class", mapping.fetch("source_system"))
      node_map = mapping.fetch("node")
      node_id = [node_map.fetch("id_prefix"), fetch_field(record, node_map.fetch("id_field"))].join(":")
      if mapping['deleted_field'] && record[mapping['deleted_field']] == true
        ingest_deletion!(mapping, record, node_id, source_ref_id, source_hash, version, timestamp, replay)
        return
      end
      label = fetch_field(record, node_map.fetch("label_field")).to_s
      natural_key = fetch_field(record, node_map.fetch("key_field")).to_s
      validate_record!(mapping, record, node_id, natural_key, label, source_ref_id, source_hash, timestamp)

      @database.transaction do
        unless replay
          @database.execute('INSERT OR IGNORE INTO connector_record(source_ref_id,source_hash,version,observed_at,record_json,mapping_json) VALUES(?,?,?,?,?,?)',
                            [source_ref_id, source_hash, version, timestamp, JSON.generate(record), JSON.generate(mapping)])
        end
        @database.execute(
          <<~SQL,
            INSERT INTO source_ref(id, source_system, source_object, source_record_id, source_timestamp,
              source_version, source_hash, authority_class, locator, captured_at, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET source_timestamp=excluded.source_timestamp,
              source_version=excluded.source_version, source_hash=excluded.source_hash,
              authority_class=excluded.authority_class, locator=excluded.locator,
              captured_at=excluded.captured_at, metadata_json=excluded.metadata_json
          SQL
          [source_ref_id, mapping["source_system"], mapping["source_object"], record_id, timestamp,
           version, source_hash, authority, mapping["locator_prefix"].to_s + record_id, now, JSON.generate({})]
        )
        ensure_node!(node_id, node_map, natural_key, label, authority, source_ref_id, source_hash, now)
        ingest_attrs!(node_id, mapping["attrs"] || {}, record, source_ref_id, authority, source_hash, timestamp)
        ingest_assertions!(node_id, mapping["assertions"] || {}, record, source_ref_id, source_hash, timestamp)
        Compiler.new(config: @config, registry: @registry, database: @database, ledger: @ledger).refresh_nodes!([node_id]) unless replay
        @audit.stage(
          event_type: "connector_ingest", actor: "connector", target_id: node_id, source_ref: source_ref_id,
          after_hash: source_hash, reason: "enterprise source delta",
          payload: { "source_system" => mapping["source_system"], "source_object" => mapping["source_object"],
                     "record_id" => record_id, "version" => version },
          event_id: Digest::SHA256.hexdigest("connector:#{source_ref_id}:#{version}:#{source_hash}")
        )
      end
      @audit.flush! unless replay
    end

    def ensure_node!(id, mapping, key, label, authority, source_ref_id, source_hash, now)
      if (existing = @database.first("SELECT id,type,source_class FROM node WHERE id = ?", id))
        raise ValidationError, "connector identity type conflict for #{id}" unless existing['type'] == mapping['type']
        if existing['source_class'] != 'git_authored'
          @database.execute("UPDATE node SET label=?, source_hash=?, compiled_at=?, lifecycle='active' WHERE id=?", [label, source_hash, now, id])
        end
        return
      end
      raise ValidationError, "unknown connector node type #{mapping['type']}" unless @registry.type?(mapping["type"])
      @database.execute(
        <<~SQL,
          INSERT INTO node(id, kind, type, natural_key, label, aliases_json, tags_json, attrs_json,
            lifecycle, revision, source_class, source_path, source_hash, narrative, compiled_at, key_namespace)
          VALUES (?, ?, ?, ?, ?, '[]', '[]', '{}', 'active', 1, ?, ?, ?, '', ?, ?)
        SQL
        [id, mapping.fetch("kind", "entity"), mapping["type"], key, label, authority,
         "connector:#{source_ref_id}", source_hash, now, mapping.fetch('key_namespace', source_ref_id.split(':').first)]
      )
    end

    def ingest_attrs!(node_id, attrs, record, source_ref_id, authority, source_hash, observed_at)
      attrs.each do |predicate_id, field|
        predicate_id = @registry.normalize_predicate(predicate_id)
        predicate = @registry.predicate(predicate_id)
        raise ValidationError, "unknown connector predicate #{predicate_id}" unless predicate
        raise ValidationError, "#{predicate_id} is not an attr" unless predicate.dig("storage", "mode") == "attr"
        value = fetch_field(record, field)
        ValueContract.validate!(predicate, value, database: @database)
        @database.execute(
          <<~SQL,
            INSERT INTO attribute_candidate(node_id, predicate, source_ref_id, source_class, value_json, observed_at, source_hash)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(node_id, predicate, source_ref_id) DO UPDATE SET value_json=excluded.value_json,
              observed_at=excluded.observed_at, source_hash=excluded.source_hash, source_class=excluded.source_class
          SQL
          [node_id, predicate_id, source_ref_id, authority, JSON.generate(value), observed_at, source_hash]
        )
        recompute_effective_attr!(node_id, predicate_id)
      end
    end

    def ingest_assertions!(node_id, assertions, record, source_ref_id, source_hash, observed_at)
      assertions.each do |predicate_id, definition|
        predicate_id = @registry.normalize_predicate(predicate_id)
        predicate = @registry.predicate(predicate_id)
        raise ValidationError, "unknown connector predicate #{predicate_id}" unless predicate
        value = assertion_value(predicate, definition, record)
        assertion_id = Digest::SHA256.hexdigest([source_ref_id, predicate_id, source_hash].join(":"))
        tier = predicate.dig("policy", "provenance_tier") || "B"
        prior = @database.first(
          "SELECT id FROM assertion WHERE node_id = ? AND predicate = ? AND source_path = ? AND status = 'confirmed' ORDER BY observed_at DESC LIMIT 1",
          [node_id, predicate_id, "connector:#{source_ref_id}"]
        )
        if prior && prior["id"] != assertion_id && predicate.dig('policy', 'write') == 'auto'
          @database.execute("UPDATE assertion SET status = 'superseded', temperature = 'warm', valid_to = ? WHERE id = ?", [observed_at, prior["id"]])
        end
        status = predicate.dig("policy", "write") == "auto" ? "confirmed" : "proposed"
        temperature = @policy_engine.assertion_temperature(predicate_id: predicate_id, status: status,
                                                           observed_at: observed_at, valid_to: nil)
        @database.execute(
          <<~SQL,
            INSERT INTO assertion(id, node_id, predicate, value_json, qualifiers_json, observed_at, valid_from, valid_to,
              assertion_kind, status, confidence, evidence_refs_json, source_refs_json, supersedes,
              provenance_tier, temperature, source_path, source_hash)
            VALUES (?, ?, ?, ?, '{}', ?, ?, NULL, ?, ?, 1.0, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO NOTHING
          SQL
          [assertion_id, node_id, predicate_id, JSON.generate(value),
           observed_at, observed_at, definition.fetch("kind", "measurement"),
           status, JSON.generate(["snapshot:#{source_hash}"]), JSON.generate([source_ref_id]), prior && prior["id"], tier,
           temperature, "connector:#{source_ref_id}", source_hash]
        )
      end
    end

    def assertion_value(predicate, definition, record)
      raw = fetch_field(record, definition.fetch('field'))
      value = raw.is_a?(Hash) ? raw.dup : { 'type' => predicate.dig('value', 'type'), 'literal' => raw }
      value['unit'] ||= definition['unit'] || (definition['unit_field'] && fetch_field(record, definition['unit_field']))
      value.delete('unit') if value['unit'].nil?
      ValueContract.normalize(predicate, value)
    end

    def ingest_deletion!(mapping, record, node_id, source_ref_id, hash, version, timestamp, replay)
      @database.transaction do
        unless replay
          @database.execute('INSERT OR IGNORE INTO connector_record(source_ref_id,source_hash,version,observed_at,record_json,mapping_json) VALUES(?,?,?,?,?,?)',
                            [source_ref_id, hash, version, timestamp, JSON.generate(record), JSON.generate(mapping)])
        end
        @database.execute('UPDATE source_ref SET source_hash=?,source_version=?,source_timestamp=? WHERE id=?', [hash, version, timestamp, source_ref_id])
        @database.execute('DELETE FROM attribute_candidate WHERE source_ref_id=?', [source_ref_id])
        @database.execute("UPDATE assertion SET status='superseded',temperature='warm',valid_to=? WHERE source_path=? AND (valid_to IS NULL OR valid_to='')", [timestamp, "connector:#{source_ref_id}"])
        node = @database.first('SELECT * FROM node WHERE id=?', [node_id])
        if node
          attrs = JSON.parse(node['attrs_json'])
          (mapping['attrs'] || {}).each_key do |predicate|
            candidates = @database.execute('SELECT * FROM attribute_candidate WHERE node_id=? AND predicate=?', [node_id, predicate])
            if candidates.empty?
              attrs.delete(predicate)
              @database.execute('UPDATE node SET attrs_json=? WHERE id=?', [JSON.generate(attrs), node_id])
            else
              recompute_effective_attr!(node_id, predicate)
              attrs = JSON.parse(@database.first('SELECT attrs_json FROM node WHERE id=?', [node_id])['attrs_json'])
            end
          end
          @database.execute("UPDATE node SET lifecycle='retired' WHERE id=? AND source_path=?", [node_id, "connector:#{source_ref_id}"])
          Compiler.new(config: @config, registry: @registry, database: @database, ledger: @ledger).refresh_nodes!([node_id]) unless replay
        end
        @audit.stage(event_type: 'connector_delete', actor: 'connector', target_id: node_id,
                     source_ref: source_ref_id, after_hash: hash, payload: { 'version' => version },
                     event_id: Digest::SHA256.hexdigest("connector_delete:#{source_ref_id}:#{version}:#{hash}"))
      end
      @audit.flush! unless replay
    end

    def validate_record!(mapping, record, node_id, key, label, source_ref, hash, timestamp)
      attrs = (mapping['attrs'] || {}).to_h { |id, field| [id, fetch_field(record, field)] }
      existing = @database.first('SELECT attrs_json FROM node WHERE id=?', [node_id])
      attrs = JSON.parse(existing['attrs_json']).merge(attrs) if existing
      assertions = (mapping['assertions'] || {}).map do |id, definition|
        predicate = @registry.predicate(id)
        raise ValidationError, "unknown connector predicate #{id}" unless predicate
        value = assertion_value(predicate, definition, record)
        ValueContract.validate!(predicate, value, database: @database)
        { 'id' => "#{source_ref}:#{id}:#{hash}", 'predicate' => id, 'value' => value,
          'temporal' => { 'observed_at' => timestamp, 'valid_from' => timestamp },
          'epistemic' => { 'status' => predicate.dig('policy', 'write') == 'auto' ? 'confirmed' : 'proposed' },
          'provenance' => { 'source_refs' => [source_ref], 'evidence_refs' => ["snapshot:#{hash}"], 'origin' => 'connector' } }
      end
      data = { 'base' => { 'schema' => { 'ckm' => '3.0' }, 'node' => { 'id' => node_id, 'type' => mapping.dig('node', 'type'), 'kind' => mapping.dig('node', 'kind') || 'entity', 'key' => key, 'label' => label },
                          'lifecycle' => { 'state' => 'active' }, 'version' => { 'entity_revision' => 1 } },
               'knowledge' => { 'attrs' => attrs, 'assertions' => assertions, 'relations' => [], 'logic_refs' => [] } }
      Validator.new(@registry).validate_document!(ParsedDocument.new(data: data, body: '', path: "connector:#{source_ref}"))
    end

    def recompute_effective_attr!(node_id, predicate_id)
      node = @database.first("SELECT attrs_json FROM node WHERE id = ?", node_id)
      attrs = JSON.parse(node["attrs_json"])
      policy_id = @registry.predicate(predicate_id).dig("policy", "authority")
      policy = @registry.authority_policies[policy_id] || {}
      candidates = @database.execute(
        "SELECT * FROM attribute_candidate WHERE node_id = ? AND predicate = ?",
        [node_id, predicate_id]
      )
      chosen = candidates.sort_by { |item| [authority_rank(policy, item["source_class"]), item["source_ref_id"]] }.first
      attrs[predicate_id] = JSON.parse(chosen["value_json"])
      @database.execute("UPDATE node SET attrs_json = ? WHERE id = ?", [JSON.generate(attrs), node_id])
    end

    def authority_rank(policy, source_class)
      %w[primary secondary supporting].each_with_index do |group, group_index|
        index = Array(policy[group]).index(source_class)
        return group_index * 100 + index if index
      end
      1_000
    end


    def fetch_field(record, dotted)
      dotted.to_s.split(".").reduce(record) do |memo, key|
        raise ValidationError, "connector field not found: #{dotted}" unless memo.is_a?(Hash) && memo.key?(key)
        memo[key]
      end
    end

    def canonical(value)
      case value
      when Hash then value.keys.sort.each_with_object({}) { |key, out| out[key] = canonical(value[key]) }
      when Array then value.map { |item| canonical(item) }
      else value
      end
    end

    def clean(row)
      row.each_with_object({}) { |(key, value), out| out[key] = value if key.is_a?(String) }
    end
  end
end
