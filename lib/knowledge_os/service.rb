# frozen_string_literal: true

require "json"
require "digest"
require "securerandom"
require "time"

module KnowledgeOS
  class Service
    attr_reader :config, :registry, :database, :ledger

    def initialize(config: Config.new)
      @config = config
      @config.ensure_runtime!
      @registry = Registry.new(config)
      @database = Database.new(config.index_path)
      @ledger = Ledger.new(config.ledger_path)
      @engine = DeterministicEngine.new(config: config, database: database, ledger: ledger)
    end

    def close
      database.close
      ledger.close
    end

    def resolve(query, scope: nil)
      needle = query.to_s.strip
      rows = database.execute(
        <<~SQL,
          SELECT id, type, natural_key, label, aliases_json, lifecycle, source_class
          FROM node
          WHERE id = ? OR natural_key = ? OR lower(label) = lower(?) OR lower(label) LIKE lower(?)
          ORDER BY CASE WHEN id = ? THEN 0 WHEN natural_key = ? THEN 1 WHEN lower(label) = lower(?) THEN 2 ELSE 3 END, label
          LIMIT 20
        SQL
        [needle, needle, needle, "%#{escape_like(needle)}%", needle, needle, needle]
      )
      rows = rows.select { |row| row["type"] == scope } if scope
      envelope(rows.map { |row| clean(row).merge("aliases" => JSON.parse(row["aliases_json"])).reject { |key, _| key == "aliases_json" } })
    end

    def get(id, include_history: false)
      card = database.first("SELECT card_json, updated_at FROM entity_card WHERE node_id = ?", id)
      raise NotFoundError, "node not found: #{id}" unless card
      data = JSON.parse(card["card_json"])
      data["card_updated_at"] = card["updated_at"]
      data["history"] = history(id)["data"] if include_history
      envelope(data, gaps: Array(data["knowledge_gaps"]))
    end

    def query(template, params = {})
      data = case template.to_s
             when "nodes_by_type"
               required = params.fetch("type")
               database.execute("SELECT id, type, label, lifecycle, source_class FROM node WHERE type = ? ORDER BY label", required).map { |row| clean(row) }
             when "current_assertions"
               required = params.fetch("node_id")
               assertion_rows("node_id = ? AND temperature = 'hot' AND status = 'confirmed'", [required])
             when "review_queue"
               review(priority: params["priority"], limit: params.fetch("limit", 100))["data"]
             else
               raise ValidationError, "unknown query template: #{template}"
             end
      envelope(data)
    end

    def neighbors(id, relation_types: nil, depth: 1, as_of: nil)
      depth = [[depth.to_i, 1].max, 5].min
      types = Array(relation_types).compact
      visited = { id => 0 }
      frontier = [id]
      edges = []
      depth.times do |level|
        break if frontier.empty?
        next_frontier = []
        frontier.each do |node_id|
          sql = "SELECT * FROM edge WHERE (src = ? OR dst = ?)"
          binds = [node_id, node_id]
          unless types.empty?
            sql += " AND predicate IN (#{(['?'] * types.length).join(',')})"
            binds.concat(types)
          end
          if as_of
            sql += " AND (valid_from = '' OR valid_from <= ?) AND (valid_to = '' OR valid_to >= ?)"
            binds.concat([as_of, as_of])
          end
          database.execute(sql, binds).each do |row|
            edge = clean(row)
            edges << edge unless edges.any? { |existing| edge_key(existing) == edge_key(edge) }
            other = row["src"] == node_id ? row["dst"] : row["src"]
            next if visited.key?(other)
            visited[other] = level + 1
            next_frontier << other
          end
        end
        frontier = next_frontier
      end
      nodes = visited.keys.map do |node_id|
        row = database.first("SELECT id, type, label, lifecycle FROM node WHERE id = ?", node_id)
        row ? clean(row).merge("distance" => visited[node_id]) : { "id" => node_id, "distance" => visited[node_id], "missing" => true }
      end
      envelope({ "nodes" => nodes, "edges" => edges, "as_of" => as_of })
    end

    def history(id, predicate: nil, range: nil)
      sql = "node_id = ?"
      binds = [id]
      if predicate
        sql += " AND predicate = ?"
        binds << predicate
      end
      if range && range[0]
        sql += " AND COALESCE(observed_at, valid_from, '') >= ?"
        binds << range[0]
      end
      if range && range[1]
        sql += " AND COALESCE(observed_at, valid_from, '') <= ?"
        binds << range[1]
      end
      data = { "assertions" => assertion_rows(sql, binds), "events" => ledger.events(target_id: id, limit: 100) }
      envelope(data)
    end

    def search(query, filters: {}, mode: "hybrid")
      type = filters && filters["type"]
      rows = if database.fts_enabled && !query.to_s.strip.empty?
               begin
                 database.execute(
                   <<~SQL,
                     SELECT n.id, n.type, n.label, n.lifecycle, n.source_class, bm25(node_fts) AS score
                     FROM node_fts JOIN node n ON n.id = node_fts.node_id
                     WHERE node_fts MATCH ? #{type ? 'AND n.type = ?' : ''}
                     ORDER BY score LIMIT 50
                   SQL
                   type ? [fts_query(query), type] : [fts_query(query)]
                 )
               rescue SQLite3::SQLException
                 fallback_search(query, type)
               end
             else
               fallback_search(query, type)
             end
      envelope(rows.map { |row| clean(row) }, source: { "mode" => mode, "fts" => database.fts_enabled, "vector" => false })
    end

    def explain(target, field_or_assertion: nil)
      assertion = database.first("SELECT * FROM assertion WHERE id = ?", field_or_assertion || target)
      if assertion
        data = decode_assertion(assertion)
        data["ledger_events"] = ledger.events(target_id: assertion["id"], limit: 50)
        return envelope(data, traces: Array(data.dig("provenance", "source_refs")))
      end
      node = database.first("SELECT * FROM node WHERE id = ?", target)
      raise NotFoundError, "target not found: #{target}" unless node
      data = clean(node)
      data["attrs"] = JSON.parse(node["attrs_json"])
      data["ledger_events"] = ledger.events(target_id: target, limit: 50)
      envelope(data, source: { "source_class" => node["source_class"], "source_path" => node["source_path"], "source_hash" => node["source_hash"] })
    end

    def calculate(model_id, inputs, scenario: "default")
      result = @engine.calculate(model_id, inputs, scenario: scenario)
      envelope(result, traces: [result["run_id"]])
    end

    def context(id, domain:, max_items: 25, as_of: nil)
      pack = registry.domain(domain)
      card = get(id)["data"]
      relations = neighbors(id, relation_types: pack.dig("retrieval", "relation_types"), depth: pack.dig("retrieval", "depth") || 1, as_of: as_of)["data"]
      assertions = Array(card["current_assertions"])
      ordered = assertions.sort_by do |item|
        priorities = Array(pack.dig("retrieval", "predicate_priority"))
        index = priorities.index(item["predicate"])
        index || priorities.length
      end.first(max_items.to_i)
      steps = [
        { "dimension" => "C", "action" => "resolve", "result" => id },
        { "dimension" => "R", "action" => "traverse", "result" => relations["edges"].length },
        { "dimension" => "L", "action" => "select_logic", "result" => Array(pack["required_models"]) },
        { "dimension" => "T", "action" => "filter", "result" => as_of || "current" },
        { "dimension" => "P", "action" => "verify", "result" => provenance_summary(ordered) }
      ]
      gaps = Array(card["knowledge_gaps"])
      envelope({ "domain" => domain, "workflow" => pack["workflow"], "steps" => steps,
                 "entity_card" => card, "assertions" => ordered, "relations" => relations }, gaps: gaps)
    end

    def propose(actor:, target_source:, patch:, reason:, risk: "normal")
      id = SecureRandom.uuid
      now = Time.now.utc.iso8601(6)
      status = risk == "low" ? "proposed" : "review_required"
      database.execute(
        "INSERT INTO changeset(id, actor, target_source, risk, status, patch_json, reason, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [id, actor, target_source, risk, status, JSON.generate(patch), reason, now]
      )
      event = ledger.append(event_type: "agent_proposal", actor: "agent", target_id: id, source_ref: target_source,
                            reason: reason, payload: { "actor" => actor, "risk" => risk, "patch" => patch })
      mirror_event(event)
      envelope({ "id" => id, "status" => status, "target_source" => target_source })
    end

    def review(priority: nil, limit: 100)
      sql = "SELECT * FROM review_item WHERE status = 'open'"
      binds = []
      if priority
        sql += " AND priority = ?"
        binds << priority
      end
      sql += " ORDER BY CASE priority WHEN 'P0' THEN 0 WHEN 'P1' THEN 1 WHEN 'P2' THEN 2 ELSE 3 END, severity DESC, created_at LIMIT ?"
      binds << limit.to_i
      items = database.execute(sql, binds).map do |row|
        clean(row).merge("details" => JSON.parse(row["details_json"])).reject { |key, _| key == "details_json" }
      end
      envelope(items)
    end

    def review_changeset(id:, reviewer:, decision:)
      raise ValidationError, "decision must be approved or rejected" unless %w[approved rejected].include?(decision)
      row = database.first("SELECT * FROM changeset WHERE id = ?", id)
      raise NotFoundError, "changeset not found: #{id}" unless row
      now = Time.now.utc.iso8601(6)
      database.execute("UPDATE changeset SET status = ?, reviewed_by = ?, reviewed_at = ? WHERE id = ?", [decision, reviewer, now, id])
      event = ledger.append(event_type: "changeset_#{decision}", actor: "human", target_id: id,
                            source_ref: row["target_source"], reason: "review decision",
                            payload: { "reviewer" => reviewer })
      mirror_event(event)
      envelope({ "id" => id, "status" => decision, "note" => "Approval records governance only; apply the patch to the real source of truth, then compile." })
    end

    private

    def envelope(data, source: {}, temporal: {}, quality: {}, conflicts: [], gaps: [], traces: [])
      { "data" => data, "source_status" => source, "temporal_status" => temporal,
        "quality_status" => quality, "conflicts" => conflicts,
        "knowledge_gaps" => gaps, "trace_refs" => traces }
    end

    def assertion_rows(where, binds)
      database.execute("SELECT * FROM assertion WHERE #{where} ORDER BY COALESCE(valid_from, observed_at, '') DESC, id", binds).map { |row| decode_assertion(row) }
    end

    def decode_assertion(row)
      {
        "id" => row["id"], "node_id" => row["node_id"], "predicate" => row["predicate"],
        "value" => JSON.parse(row["value_json"]), "qualifiers" => JSON.parse(row["qualifiers_json"]),
        "temporal" => { "observed_at" => row["observed_at"], "valid_from" => row["valid_from"], "valid_to" => row["valid_to"] },
        "epistemic" => { "assertion_kind" => row["assertion_kind"], "status" => row["status"], "confidence" => row["confidence"] },
        "provenance" => { "tier" => row["provenance_tier"], "evidence_refs" => JSON.parse(row["evidence_refs_json"]), "source_refs" => JSON.parse(row["source_refs_json"]) },
        "supersedes" => row["supersedes"], "temperature" => row["temperature"]
      }
    end

    def fallback_search(query, type)
      sql = "SELECT id, type, label, lifecycle, source_class FROM node WHERE (lower(label) LIKE lower(?) OR lower(narrative) LIKE lower(?))"
      binds = ["%#{escape_like(query)}%", "%#{escape_like(query)}%"]
      if type
        sql += " AND type = ?"
        binds << type
      end
      sql += " ORDER BY label LIMIT 50"
      database.execute(sql, binds)
    end

    def fts_query(query)
      query.to_s.scan(/[\p{L}\p{N}_:-]+/).map { |term| '"' + term.gsub('"', '""') + '"' }.join(" OR ")
    end

    def provenance_summary(assertions)
      counts = assertions.group_by { |item| item["provenance_tier"] }.transform_values(&:length)
      { "tiers" => counts, "checked" => assertions.length }
    end

    def escape_like(value)
      value.to_s.gsub(/[\\%_]/) { |char| "\\#{char}" }
    end

    def edge_key(edge)
      %w[src predicate dst rel_id valid_from].map { |key| edge[key] }.join("|")
    end

    def mirror_event(event)
      database.execute(
        "INSERT OR IGNORE INTO audit_event_ref(event_id, event_type, target_id, ledger_sequence, timestamp) VALUES (?, ?, ?, ?, ?)",
        [event["event_id"], event["event_type"], event["target_id"], event["sequence"], event["timestamp"]]
      )
    end

    def clean(row)
      row.each_with_object({}) { |(key, value), out| out[key] = value if key.is_a?(String) }
    end
  end
end

