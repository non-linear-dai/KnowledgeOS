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
      @semantic_index = SemanticIndex.new(database: database)
    end

    def ingest_ndjson(input_path, mapping_path)
      mapping = Frontmatter.load_yaml(mapping_path)
      source_system = mapping.fetch("source_system")
      source_object = mapping.fetch("source_object")
      records = Frontmatter.load_ndjson(input_path)
      counters = { "records" => records.length, "ingested" => 0, "skipped" => 0 }

      records.each do |record|
        record_id = fetch_field(record, mapping.fetch("record_id_field")).to_s
        version = mapping["version_field"] ? fetch_field(record, mapping["version_field"]).to_s : nil
        source_hash = Digest::SHA256.hexdigest(JSON.generate(canonical(record)))
        source_ref_id = [source_system, source_object, record_id].join(":")
        existing = @database.first("SELECT source_hash, source_version FROM source_ref WHERE id = ?", source_ref_id)
        if existing && existing["source_hash"] == source_hash && existing["source_version"].to_s == version.to_s
          counters["skipped"] += 1
          next
        end
        ingest_record!(mapping, record, record_id, version, source_hash, source_ref_id)
        counters["ingested"] += 1
      end
      counters
    end

    private

    def ingest_record!(mapping, record, record_id, version, source_hash, source_ref_id)
      now = Time.now.utc.iso8601(6)
      timestamp = mapping["timestamp_field"] ? fetch_field(record, mapping["timestamp_field"]).to_s : now
      authority = mapping.fetch("authority_class", mapping.fetch("source_system"))
      node_map = mapping.fetch("node")
      node_id = [node_map.fetch("id_prefix"), fetch_field(record, node_map.fetch("id_field"))].join(":")
      label = fetch_field(record, node_map.fetch("label_field")).to_s
      natural_key = fetch_field(record, node_map.fetch("key_field")).to_s

      @database.transaction do
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
        rebuild_card!(node_id, now)
        @audit.stage(
          event_type: "connector_ingest", actor: "connector", target_id: node_id, source_ref: source_ref_id,
          after_hash: source_hash, reason: "enterprise source delta",
          payload: { "source_system" => mapping["source_system"], "source_object" => mapping["source_object"],
                     "record_id" => record_id, "version" => version },
          event_id: Digest::SHA256.hexdigest("connector:#{source_ref_id}:#{version}:#{source_hash}")
        )
      end
      @audit.flush!
    end

    def ensure_node!(id, mapping, key, label, authority, source_ref_id, source_hash, now)
      return if @database.first("SELECT id FROM node WHERE id = ?", id)
      raise ValidationError, "unknown connector node type #{mapping['type']}" unless @registry.type?(mapping["type"])
      @database.execute(
        <<~SQL,
          INSERT INTO node(id, kind, type, natural_key, label, aliases_json, tags_json, attrs_json,
            lifecycle, revision, source_class, source_path, source_hash, narrative, compiled_at)
          VALUES (?, ?, ?, ?, ?, '[]', '[]', '{}', 'active', 1, ?, ?, ?, '', ?)
        SQL
        [id, mapping.fetch("kind", "entity"), mapping["type"], key, label, authority,
         "connector:#{source_ref_id}", source_hash, now]
      )
    end

    def ingest_attrs!(node_id, attrs, record, source_ref_id, authority, source_hash, observed_at)
      attrs.each do |predicate_id, field|
        predicate_id = @registry.normalize_predicate(predicate_id)
        predicate = @registry.predicate(predicate_id)
        raise ValidationError, "unknown connector predicate #{predicate_id}" unless predicate
        raise ValidationError, "#{predicate_id} is not an attr" unless predicate.dig("storage", "mode") == "attr"
        value = fetch_field(record, field)
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
        value = fetch_field(record, definition.fetch("field"))
        assertion_id = Digest::SHA256.hexdigest([source_ref_id, predicate_id, source_hash].join(":"))
        tier = predicate.dig("policy", "provenance_tier") || "B"
        prior = @database.first(
          "SELECT id FROM assertion WHERE node_id = ? AND predicate = ? AND source_path = ? AND status = 'confirmed' ORDER BY observed_at DESC LIMIT 1",
          [node_id, predicate_id, "connector:#{source_ref_id}"]
        )
        if prior && prior["id"] != assertion_id
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
          [assertion_id, node_id, predicate_id, JSON.generate({ "type" => predicate.dig("value", "type"), "literal" => value }),
           observed_at, observed_at, definition.fetch("kind", "measurement"),
           status, JSON.generate(["snapshot:#{source_hash}"]), JSON.generate([source_ref_id]), prior && prior["id"], tier,
           temperature, "connector:#{source_ref_id}", source_hash]
        )
      end
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

    def rebuild_card!(node_id, now)
      node = @database.first("SELECT * FROM node WHERE id = ?", node_id)
      assertions = @database.execute(
        "SELECT id, predicate, value_json, observed_at, valid_from, valid_to, assertion_kind, status, confidence, provenance_tier, temperature FROM assertion WHERE node_id = ? AND temperature = 'hot' AND status = 'confirmed'",
        [node_id]
      ).map do |row|
        clean(row).merge("value" => JSON.parse(row["value_json"])).reject { |key, _| key == "value_json" }
      end
      relations = @database.execute(
        "SELECT src, predicate, dst, NULLIF(rel_id, '') AS rel_id, NULLIF(valid_from, '') AS valid_from, NULLIF(valid_to, '') AS valid_to, weight FROM edge WHERE src = ? ORDER BY predicate, dst",
        [node_id]
      ).map { |row| clean(row) }
      card = { "id" => node_id, "type" => node["type"], "label" => node["label"],
               "attrs" => JSON.parse(node["attrs_json"]), "current_assertions" => assertions,
               "key_relations" => relations, "source_class" => node["source_class"], "knowledge_gaps" => [] }
      @database.execute(
        "INSERT INTO entity_card(node_id, card_json, updated_at) VALUES (?, ?, ?) ON CONFLICT(node_id) DO UPDATE SET card_json=excluded.card_json, updated_at=excluded.updated_at",
        [node_id, JSON.generate(card), now]
      )
      @semantic_index.index(node_id: node_id, text: [node["label"], assertions.map { |item| item["value"] }].join("\n"),
                            source_hash: node["source_hash"])
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
