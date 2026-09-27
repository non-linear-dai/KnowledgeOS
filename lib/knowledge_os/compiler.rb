# frozen_string_literal: true

require "json"
require "digest"
require "time"
require "set"

module KnowledgeOS
  class Compiler
    attr_reader :config, :registry, :database, :ledger

    def initialize(config:, registry: nil, database: nil, ledger: nil, clock: -> { Time.now.utc })
      @config = config
      @config.ensure_runtime!
      @registry = registry || Registry.new(config)
      @database = database || Database.new(config.index_path)
      @ledger = ledger || Ledger.new(config.ledger_path)
      @validator = Validator.new(@registry)
      @policy_engine = PolicyEngine.new(registry: @registry, clock: clock)
      @audit = AuditCoordinator.new(database: @database, ledger: @ledger)
      @semantic_index = SemanticIndex.new(database: @database)
    end

    def compile(rebuild: false)
      registry.reload!
      database.reset_projection! if rebuild
      paths = entity_paths
      present = paths.map { |path| relative(path) }.to_set
      changed = []
      skipped = []

      database.transaction do
        remove_deleted_sources!(present) unless rebuild
        paths.each do |path|
          document = Frontmatter.parse(path)
          assertions = external_assertions(document)
          full_hash = source_hash(document, assertions)
          rel = relative(path)
          prior = database.first("SELECT value FROM metadata WHERE key = ?", "source:#{rel}")
          if !rebuild && prior && prior["value"] == full_hash
            skipped << rel
            next
          end

          @validator.validate_document!(document, assertions: assertions)
          project_document!(document, assertions, full_hash)
          database.execute(
            "INSERT INTO metadata(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            ["source:#{rel}", full_hash]
          )
          changed << rel
        end
        validate_projected_relations!
        reclassify_assertions!
        rebuild_cards!
        scan_maintenance!
        generation = Time.now.utc.iso8601(6)
        database.execute(
          "INSERT INTO metadata(key, value) VALUES('index_generation', ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
          [generation]
        )
        changed.each do |rel|
          hash = database.first("SELECT value FROM metadata WHERE key = ?", "source:#{rel}")["value"]
          @audit.stage(
            event_type: "git_publication", actor: "compiler", target_id: rel, source_ref: rel,
            after_hash: hash, reason: rebuild ? "index rebuild" : "incremental compile",
            payload: { "source_path" => rel },
            event_id: Digest::SHA256.hexdigest("git_publication:#{rel}:#{hash}")
          )
        end
        @audit.stage(
          event_type: rebuild ? "index_rebuild" : "index_compile", actor: "compiler",
          target_id: "knowledge.index.db", reason: rebuild ? "rebuild requested" : "incremental compile requested",
          payload: { "changed" => changed, "skipped" => skipped, "files" => paths.length },
          event_id: rebuild ? nil : Digest::SHA256.hexdigest("index_compile:#{changed.sort.join(',')}:#{generation}")
        )
      end
      @audit.flush!
      { "files" => paths.length, "changed" => changed, "skipped" => skipped, "rebuild" => rebuild }
    end

    def close
      database.close
      ledger.close
    end

    private

    def entity_paths
      return [] unless config.knowledge_dir.directory?
      config.knowledge_dir.glob("**/*.md").sort
    end

    def relative(path)
      path.relative_path_from(config.root).to_s
    end

    def external_assertions(document)
      inline = Array(document.data.dig("knowledge", "assertions"))
      reference = document.data.dig("external", "assertions_ref")
      return inline if reference.nil? || reference.to_s.empty?
      raise ValidationError, "#{document.path}: assertions_ref must be relative" if Pathname.new(reference).absolute?

      resolved = Pathname.new(document.path).dirname.join(reference).cleanpath
      root = config.knowledge_dir.realpath.to_s
      unless resolved.exist? && resolved.realpath.to_s.start_with?(root + File::SEPARATOR)
        raise ValidationError, "#{document.path}: assertions_ref escapes knowledge root or is missing"
      end
      inline + Frontmatter.load_ndjson(resolved)
    end

    def source_hash(document, assertions)
      Digest::SHA256.hexdigest(JSON.generate(canonical(document.data)) + document.body + JSON.generate(canonical(assertions)))
    end

    def project_document!(document, external_assertions, hash)
      data = document.data
      base = data["base"]
      node = base["node"]
      knowledge = data["knowledge"]
      rel = relative(Pathname.new(document.path))
      now = Time.now.utc.iso8601(6)
      attrs = normalize_attrs(knowledge["attrs"] || {})

      old = database.first("SELECT id FROM node WHERE source_path = ?", rel)
      database.execute("DELETE FROM node WHERE id = ?", old["id"]) if old && old["id"] != node["id"]
      database.execute(
        <<~SQL,
          INSERT INTO node (
            id, kind, type, natural_key, label, aliases_json, tags_json, attrs_json,
            lifecycle, revision, source_class, source_path, source_hash, narrative, compiled_at
          ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
          ON CONFLICT(id) DO UPDATE SET
            kind=excluded.kind, type=excluded.type, natural_key=excluded.natural_key,
            label=excluded.label, aliases_json=excluded.aliases_json, tags_json=excluded.tags_json,
            attrs_json=excluded.attrs_json, lifecycle=excluded.lifecycle, revision=excluded.revision,
            source_class=excluded.source_class, source_path=excluded.source_path,
            source_hash=excluded.source_hash, narrative=excluded.narrative, compiled_at=excluded.compiled_at
        SQL
        [node["id"], node["kind"], node["type"], node["key"], node["label"],
         database.json(Array(node["aliases"])), database.json(Array(base.dig("classification", "tags"))),
         database.json(attrs), base.dig("lifecycle", "state"), base.dig("version", "entity_revision") || 1,
         "git_authored", rel, hash, document.body, now]
      )
      sync_git_attr_candidates!(node["id"], attrs, rel, hash, now)
      recompute_effective_attrs!(node["id"])
      database.execute("DELETE FROM assertion WHERE source_path = ?", rel)
      database.execute("DELETE FROM edge WHERE source_path = ?", rel)
      external_assertions.each { |assertion| project_assertion!(node["id"], assertion, rel, hash) }
      Array(knowledge["relations"]).each { |relation| project_relation!(node["id"], relation, rel) }
    end

    def normalize_attrs(attrs)
      attrs.each_with_object({}) do |(predicate, value), out|
        out[registry.normalize_predicate(predicate)] = value
      end
    end

    def project_assertion!(node_id, assertion, source_path, source_hash)
      predicate_id = registry.normalize_predicate(assertion["predicate"])
      predicate = registry.predicate(predicate_id)
      temporal = assertion["temporal"] || {}
      epistemic = assertion["epistemic"] || {}
      provenance = assertion["provenance"] || {}
      version = assertion["version"] || {}
      status = epistemic["status"] || "proposed"
      tier = predicate.dig("policy", "provenance_tier") || "C"
      database.execute(
        <<~SQL,
          INSERT INTO assertion (
            id, node_id, predicate, value_json, qualifiers_json, observed_at, valid_from, valid_to,
            assertion_kind, status, confidence, evidence_refs_json, source_refs_json, supersedes,
            provenance_tier, temperature, source_path, source_hash
          ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
          ON CONFLICT(id) DO UPDATE SET
            node_id=excluded.node_id, predicate=excluded.predicate, value_json=excluded.value_json,
            qualifiers_json=excluded.qualifiers_json, observed_at=excluded.observed_at,
            valid_from=excluded.valid_from, valid_to=excluded.valid_to,
            assertion_kind=excluded.assertion_kind, status=excluded.status,
            confidence=excluded.confidence, evidence_refs_json=excluded.evidence_refs_json,
            source_refs_json=excluded.source_refs_json, supersedes=excluded.supersedes,
            provenance_tier=excluded.provenance_tier, temperature=excluded.temperature,
            source_path=excluded.source_path, source_hash=excluded.source_hash
        SQL
        [assertion["id"], node_id, predicate_id, database.json(assertion["value"]),
         database.json(assertion["qualifiers"] || {}), temporal["observed_at"], temporal["valid_from"], temporal["valid_to"],
         epistemic["assertion_kind"] || "claim", status, epistemic["confidence"],
         database.json(Array(provenance["evidence_refs"])), database.json(Array(provenance["source_refs"])),
         version["supersedes"], tier,
         @policy_engine.assertion_temperature(predicate_id: predicate_id, status: status,
                                              observed_at: temporal["observed_at"], valid_to: temporal["valid_to"]),
         source_path, source_hash]
      )
    end

    def project_relation!(node_id, relation, source_path)
      temporal = relation["temporal"] || {}
      database.execute(
        <<~SQL,
          INSERT OR REPLACE INTO edge(src, predicate, dst, rel_id, valid_from, valid_to, weight, source_path)
          VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        SQL
        [node_id, relation["predicate"], relation["target"], relation["id"] || "",
         temporal["valid_from"] || "", temporal["valid_to"] || "", relation["weight"], source_path]
      )
    end

    def validate_projected_relations!
      database.execute(<<~SQL).each do |row|
        SELECT e.src, source.type AS source_type, e.predicate, e.dst, target.type AS target_type
        FROM edge e
        LEFT JOIN node source ON source.id = e.src
        LEFT JOIN node target ON target.id = e.dst
      SQL
        raise ValidationError, "relation #{row['src']} -> #{row['dst']} has a missing endpoint" unless row["source_type"] && row["target_type"]
        definition = registry.relation(row["predicate"])
        connections = Array(definition && definition["connections"])
        next if connections.empty?
        next if connections.any? { |item| item["source_type"] == row["source_type"] && item["target_type"] == row["target_type"] }
        raise ValidationError, "relation #{row['predicate']} does not allow #{row['source_type']} -> #{row['target_type']} (#{row['src']} -> #{row['dst']})"
      end
      registry.ontology.fetch("relation_types", []).each do |definition|
        predicate = definition.fetch("id")
        Array(definition["connections"]).each do |connection|
          if connection["source_cardinality"] == "one"
            violating = database.first(
              <<~SQL, [predicate, connection["source_type"], connection["target_type"]]
                SELECT e.dst, count(DISTINCT e.src) AS count
                FROM edge e JOIN node s ON s.id=e.src JOIN node t ON t.id=e.dst
                WHERE e.predicate=? AND s.type=? AND t.type=? GROUP BY e.dst HAVING count(DISTINCT e.src) > 1 LIMIT 1
              SQL
            )
            raise ValidationError, "relation #{predicate} violates source cardinality one for #{violating['dst']}" if violating
          end
          if connection["target_cardinality"] == "one"
            violating = database.first(
              <<~SQL, [predicate, connection["source_type"], connection["target_type"]]
                SELECT e.src, count(DISTINCT e.dst) AS count
                FROM edge e JOIN node s ON s.id=e.src JOIN node t ON t.id=e.dst
                WHERE e.predicate=? AND s.type=? AND t.type=? GROUP BY e.src HAVING count(DISTINCT e.dst) > 1 LIMIT 1
              SQL
            )
            raise ValidationError, "relation #{predicate} violates target cardinality one for #{violating['src']}" if violating
          end
        end
        next unless definition["reification"]
        if definition.fetch("mode", "simple") == "reified"
          missing = database.first("SELECT src, dst FROM edge WHERE predicate = ? AND rel_id = '' LIMIT 1", [predicate])
          raise ValidationError, "reified relation #{predicate} requires an identity for #{missing['src']} -> #{missing['dst']}" if missing
        end
        database.execute("SELECT * FROM edge WHERE predicate = ? AND rel_id <> ''", [predicate]).each do |edge|
          node = database.first("SELECT type FROM node WHERE id = ?", [edge["rel_id"]])
          expected = definition.dig("reification", "node_type")
          raise ValidationError, "relation #{predicate} reification #{edge['rel_id']} must be a #{expected} node" unless node && node["type"] == expected
          attrs = JSON.parse(database.first("SELECT attrs_json FROM node WHERE id = ?", [edge["rel_id"]])["attrs_json"])
          asserted = database.execute("SELECT DISTINCT predicate FROM assertion WHERE node_id = ?", [edge["rel_id"]]).map { |row| row["predicate"] }
          Array(definition.dig("reification", "properties")).select { |item| item["required"] }.each do |binding|
            id = binding.fetch("predicate")
            unless attrs.key?(id) || asserted.include?(id)
              raise ValidationError, "relation #{predicate} reification #{edge['rel_id']} requires predicate #{id}"
            end
          end
        end
      end
    end

    def reclassify_assertions!
      database.execute("SELECT id, predicate, status, observed_at, valid_to FROM assertion").each do |row|
        value = @policy_engine.assertion_temperature(
          predicate_id: row["predicate"], status: row["status"],
          observed_at: row["observed_at"], valid_to: row["valid_to"]
        )
        database.execute("UPDATE assertion SET temperature = ? WHERE id = ?", [value, row["id"]])
      end
    end

    def rebuild_cards!
      database.execute("DELETE FROM entity_card")
      database.execute("DELETE FROM node_fts") if database.fts_enabled
      database.execute("SELECT * FROM node ORDER BY id").each do |node|
        assertions = database.execute(
          "SELECT * FROM assertion WHERE node_id = ? AND temperature = 'hot' AND status = 'confirmed' ORDER BY predicate, id",
          [node["id"]]
        ).map { |row| assertion_hash(row) }
        relations = database.execute(
          "SELECT src, predicate, dst, NULLIF(rel_id, '') AS rel_id, NULLIF(valid_from, '') AS valid_from, NULLIF(valid_to, '') AS valid_to, weight FROM edge WHERE src = ? ORDER BY predicate, dst",
          [node["id"]]
        )
        gaps = knowledge_gaps(node, assertions)
        card = {
          "id" => node["id"], "type" => node["type"], "label" => node["label"],
          "attrs" => JSON.parse(node["attrs_json"]), "current_assertions" => assertions,
          "key_relations" => clean_rows(relations), "source_class" => node["source_class"],
          "knowledge_gaps" => gaps
        }
        now = Time.now.utc.iso8601(6)
        database.execute("INSERT INTO entity_card(node_id, card_json, updated_at) VALUES (?, ?, ?)", [node["id"], database.json(card), now])
        if database.fts_enabled
          body = [node["narrative"], searchable_assertions(assertions)].join("\n")
          database.execute("INSERT INTO node_fts(node_id, label, body) VALUES (?, ?, ?)", [node["id"], node["label"], body])
        end
        semantic_text = [node["label"], node["narrative"], searchable_assertions(assertions)].join("\n")
        @semantic_index.index(node_id: node["id"], text: semantic_text, source_hash: node["source_hash"])
      end
    end

    def searchable_assertions(assertions)
      assertions.select do |item|
        registry.predicate(item["predicate"]).dig("policy", "embedding") == true
      end.map { |item| item["value"].values.compact.join(" ") }.join("\n")
    end

    def knowledge_gaps(node, assertions)
      gaps = []
      gaps << "no_current_assertions" if assertions.empty?
      gaps << "draft_node" if node["lifecycle"] == "draft"
      gaps
    end

    def scan_maintenance!
      now = Time.now.utc.iso8601(6)
      managed = %w[broken_relation provenance stale_assertion deprecated_predicate authority_conflict assertion_externalization]
      database.execute("DELETE FROM review_item WHERE status = 'open' AND kind IN (#{(['?'] * managed.length).join(',')})", managed)
      database.execute(<<~SQL).each do |row|
        SELECT e.src, e.predicate, e.dst
        FROM edge e LEFT JOIN node n ON n.id = e.dst
        WHERE n.id IS NULL
      SQL
        review!("broken_relation", row["src"], rule_score("broken_relation", 55), "relation target does not exist", row, now)
      end
      database.execute("SELECT * FROM assertion WHERE status = 'confirmed'").each do |row|
        assertion = { "provenance" => { "source_refs" => JSON.parse(row["source_refs_json"]),
                                          "evidence_refs" => JSON.parse(row["evidence_refs_json"]) } }
        errors = @policy_engine.provenance_errors(row["predicate"], assertion)
        review!("provenance", row["id"], rule_score("provenance", 95), errors.join("; "), row, now) unless errors.empty?
        if @policy_engine.stale?(row["predicate"], row["observed_at"])
          review!("stale_assertion", row["id"], rule_score("stale_assertion", 60), "assertion exceeds its freshness policy", row, now)
        end
        predicate = registry.predicate(row["predicate"])
        if predicate && predicate.dig("status", "lifecycle") == "deprecated"
          review!("deprecated_predicate", row["id"], rule_score("deprecated_predicate", 70), "assertion uses a deprecated predicate", row, now)
        end
      end
      scan_externalization!(now)
      scan_attr_conflicts!(now)
    end

    def scan_externalization!(now)
      policy = registry.maintenance_policy.fetch("assertion_externalization", {})
      inline_limit = policy.fetch("inline_count", 0).to_i
      byte_limit = policy.fetch("frontmatter_bytes", 0).to_i
      historical_limit = policy.fetch("historical_count", 0).to_i
      database.execute("SELECT source_path, count(*) AS count FROM assertion WHERE source_path LIKE 'knowledge/%' GROUP BY source_path").each do |row|
        path = config.root.join(row["source_path"])
        too_many = inline_limit.positive? && row["count"].to_i > inline_limit
        too_large = byte_limit.positive? && path.file? && path.size > byte_limit
        historical = database.first(
          "SELECT count(*) AS count FROM assertion WHERE source_path = ? AND temperature <> 'hot'", [row["source_path"]]
        )["count"].to_i
        too_historical = historical_limit.positive? && historical > historical_limit
        next unless too_many || too_large || too_historical
        review!("assertion_externalization", row["source_path"], rule_score("assertion_externalization", 45),
                "authored assertions exceed the configured externalization threshold",
                row.merge("historical_count" => historical), now)
      end
    end

    def sync_git_attr_candidates!(node_id, attrs, source_path, source_hash, observed_at)
      source_ref_id = "git:#{source_path}"
      database.execute("DELETE FROM attribute_candidate WHERE node_id = ? AND source_ref_id = ?", [node_id, source_ref_id])
      attrs.each do |predicate, value|
        database.execute(
          <<~SQL,
            INSERT INTO attribute_candidate(node_id, predicate, source_ref_id, source_class, value_json, observed_at, source_hash)
            VALUES (?, ?, ?, 'git_authored', ?, ?, ?)
          SQL
          [node_id, predicate, source_ref_id, database.json(value), observed_at, source_hash]
        )
      end
    end

    def recompute_effective_attrs!(node_id)
      grouped = database.execute("SELECT * FROM attribute_candidate WHERE node_id = ?", node_id).group_by { |row| row["predicate"] }
      attrs = grouped.each_with_object({}) do |(predicate, candidates), out|
        policy_id = registry.predicate(predicate).dig("policy", "authority")
        policy = registry.authority_policies[policy_id] || {}
        chosen = candidates.sort_by { |item| [authority_rank(policy, item["source_class"]), item["source_ref_id"]] }.first
        out[predicate] = JSON.parse(chosen["value_json"])
      end
      database.execute("UPDATE node SET attrs_json = ? WHERE id = ?", [database.json(attrs), node_id])
    end

    def scan_attr_conflicts!(now)
      rows = database.execute("SELECT * FROM attribute_candidate ORDER BY node_id, predicate, source_ref_id")
      rows.group_by { |row| [row["node_id"], row["predicate"]] }.each do |(node_id, predicate), candidates|
        next if candidates.length < 2
        policy_id = registry.predicate(predicate).dig("policy", "authority")
        policy = registry.authority_policies[policy_id] || {}
        best_rank = candidates.map { |item| authority_rank(policy, item["source_class"]) }.min
        peers = candidates.select { |item| authority_rank(policy, item["source_class"]) == best_rank }
        next if peers.map { |item| item["value_json"] }.uniq.length < 2
        review!("authority_conflict", "#{node_id}:#{predicate}", rule_score("authority_conflict", 80), "equal-authority attribute values conflict",
                { "node_id" => node_id, "predicate" => predicate,
                  "candidates" => peers.map { |item| clean_row(item) } }, now)
      end
    end

    def authority_rank(policy, source_class)
      %w[primary secondary supporting].each_with_index do |group, group_index|
        index = Array(policy[group]).index(source_class)
        return group_index * 100 + index if index
      end
      1_000
    end

    def rule_score(id, default)
      registry.rules.each do |definition|
        value = definition.dig("review_scores", id)
        return value.to_i unless value.nil?
      end
      default
    end

    def review!(kind, target, severity, reason, details, now)
      id = Digest::SHA256.hexdigest([kind, target, reason].join(":"))
      priority = @policy_engine.priority_for(severity)
      database.execute(
        <<~SQL,
          INSERT INTO review_item(id, kind, target_id, severity, priority, reason, details_json, status, created_at, updated_at)
          VALUES (?, ?, ?, ?, ?, ?, ?, 'open', ?, ?)
          ON CONFLICT(id) DO UPDATE SET severity=excluded.severity, priority=excluded.priority,
            reason=excluded.reason, details_json=excluded.details_json, updated_at=excluded.updated_at
        SQL
        [id, kind, target, severity, priority, reason, database.json(clean_row(details)), now, now]
      )
    end

    def remove_deleted_sources!(present)
      database.execute("SELECT key FROM metadata WHERE key LIKE 'source:%'").each do |row|
        rel = row["key"].sub(/\Asource:/, "")
        next if present.include?(rel)
        database.execute("DELETE FROM node WHERE source_path = ?", rel)
        database.execute("DELETE FROM metadata WHERE key = ?", row["key"])
      end
    end

    def assertion_hash(row)
      {
        "id" => row["id"], "predicate" => row["predicate"], "value" => JSON.parse(row["value_json"]),
        "observed_at" => row["observed_at"], "valid_from" => row["valid_from"], "valid_to" => row["valid_to"],
        "assertion_kind" => row["assertion_kind"], "status" => row["status"], "confidence" => row["confidence"],
        "provenance_tier" => row["provenance_tier"], "temperature" => row["temperature"]
      }
    end

    def clean_rows(rows)
      rows.map { |row| clean_row(row) }
    end

    def clean_row(row)
      row.each_with_object({}) { |(key, value), out| out[key] = value if key.is_a?(String) }
    end

    def canonical(value)
      case value
      when Hash
        value.keys.map(&:to_s).sort.each_with_object({}) do |key, out|
          original = value.key?(key) ? key : value.keys.find { |candidate| candidate.to_s == key }
          out[key] = canonical(value[original])
        end
      when Array
        value.map { |item| canonical(item) }
      else
        value
      end
    end
  end
end
