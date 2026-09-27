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
      @audit = AuditCoordinator.new(database: @database, ledger: @ledger)
      @audit.flush!
      @engine = DeterministicEngine.new(config: config, database: database, ledger: ledger)
      @agent_service = AgentService.new(service: self, registry: registry)
      @policy_engine = PolicyEngine.new(registry: registry)
      @semantic_index = SemanticIndex.new(database: database)
      @publication_verifier = PublicationVerifier.new(config: config, registry: registry,
                                                      database: database, ledger: ledger)
      refresh_temperatures!
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

    def control_plane
      envelope(registry.control_plane, source: { "class" => "git_authored", "roots" => ["control", "connectors"] })
    end

    def agent_capabilities(domain: nil)
      data = @agent_service.capabilities(domain: domain)
      envelope(data, source: { "class" => "git_authored", "roots" => ["control/domains", "control/skills"] },
               quality: { "registry_fingerprint" => data["registry_fingerprint"], "write_performed" => false })
    end

    def agent_skill(id:)
      data = registry.skill_bundle(id)
      envelope(data, source: { "class" => "git_authored", "path" => data.dig("package", "path") },
               quality: { "portable" => true, "required_files" => data["files"].keys, "write_performed" => false })
    end

    def agent_request(question:, domain:, target: nil, query: nil, skill: nil, as_of: nil, max_items: 25)
      data = @agent_service.prepare(question: question, domain: domain, target: target, query: query, skill: skill,
                                    as_of: as_of, max_items: max_items)
      envelope(data, source: { "class" => "compiled_projection", "domain" => domain },
               temporal: { "as_of" => as_of || "current" },
               quality: { "grounding_evidence" => data["evidence"].length, "write_performed" => false },
               gaps: Array(data.dig("context", "crltp", "entity_card", "knowledge_gaps")),
               traces: data["evidence"].map { |item| item["ref"] })
    end

    def agent_respond(request:, model_output:, tool_results: [])
      data = @agent_service.finalize(request, model_output, tool_results: tool_results)
      envelope(data, source: { "class" => "model_output", "request_id" => data["request_id"] },
               quality: data["grounding"].merge("write_performed" => false),
               gaps: data["knowledge_gaps"], traces: data.dig("grounding", "cited_evidence_refs"))
    end

    def agent_invoke(domain:, operation:, arguments: {})
      @agent_service.invoke(domain: domain, operation: operation, arguments: arguments)
    end

    def extraction_request(source:, profile: "default", materializer: nil)
      request = ExtractionPipeline.new(registry: registry, database: database, profile_id: profile)
                                  .prepare(source, materializer: materializer)
      envelope(request,
               source: { "class" => request.dig("source", "kind"),
                         "source_id" => request.dig("source", "id"),
                         "content_hash" => request.dig("source", "content_hash") },
               quality: { "registry_fingerprint" => request["registry_fingerprint"],
                          "write_performed" => false })
    end

    def extraction_candidates(request:, model_output:, profile: "default")
      result = ExtractionPipeline.new(registry: registry, database: database, profile_id: profile)
                                 .finalize(request, model_output)
      envelope(result,
               source: result["source"],
               quality: { "valid_candidates" => result["candidates"].length,
                          "rejected_candidates" => result["rejected"].length,
                          "registry_fingerprint" => result["registry_fingerprint"],
                          "write_performed" => false },
               gaps: result["unmapped_facts"], traces: [result.dig("source", "id")])
    end

    def extract_candidates(source:, model_adapter:, profile: "default", materializer: nil)
      pipeline = ExtractionPipeline.new(registry: registry, database: database, profile_id: profile)
      request = pipeline.prepare(source, materializer: materializer)
      extraction_candidates(request: request, model_output: model_adapter.extract(request), profile: profile)
    end

    def studio
      data = registry.studio_catalog.merge("changesets" => changesets["data"])
      envelope(data, source: { "class" => "git_authored", "roots" => ["control", "connectors"] },
               quality: { "registry_valid" => true })
    end

    def changesets(status: nil, limit: 100)
      limit = [[limit.to_i, 1].max, 500].min
      sql = "SELECT * FROM changeset"
      binds = []
      if status
        sql += " WHERE status = ?"
        binds << status
      end
      sql += " ORDER BY created_at DESC LIMIT ?"
      binds << limit
      data = database.execute(sql, binds).map do |row|
        clean(row).merge("patch" => JSON.parse(row["patch_json"]),
                         "operations" => JSON.parse(row["operations_json"] || "[]"),
                         "publication" => row["publication_json"] && JSON.parse(row["publication_json"]))
                  .reject { |key, _| %w[patch_json operations_json publication_json].include?(key) }
      end
      envelope(data)
    end

    def get(id, include_history: false)
      card = database.first("SELECT card_json, updated_at FROM entity_card WHERE node_id = ?", id)
      raise NotFoundError, "node not found: #{id}" unless card
      data = JSON.parse(card["card_json"])
      data["current_assertions"] = database.execute(
        "SELECT * FROM assertion WHERE node_id = ? AND temperature = 'hot' AND status = 'confirmed' ORDER BY predicate, id", [id]
      ).map { |row| card_assertion(row) }
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
      events = ledger.events(target_id: id, limit: 100)
      node = database.first("SELECT source_path FROM node WHERE id = ?", [id])
      if node && node["source_path"]
        events = (events + ledger.events(target_id: node["source_path"], limit: 100))
                 .uniq { |event| event["event_id"] }
                 .sort_by { |event| -event["sequence"].to_i }.first(100)
      end
      data = { "assertions" => assertion_rows(sql, binds), "events" => events }
      envelope(data)
    end

    def search(query, filters: {}, mode: "hybrid")
      type = filters && filters["type"]
      mode = mode.to_s
      allowed = %w[keyword vector hybrid hybrid_research structured_first temporal_graph_first]
      raise ValidationError, "unsupported search mode: #{mode}" unless allowed.include?(mode)
      lexical = keyword_search(query, type)
      vector = query.to_s.strip.empty? ? [] : @semantic_index.search(query, type: type, limit: 50)
      rows = case mode
             when "keyword" then lexical
             when "vector" then vector
             else hybrid_rows(lexical, vector)
             end
      envelope(rows, source: { "mode" => mode, "fts" => database.fts_enabled,
                               "vector" => !vector.empty?, "vector_model" => SemanticIndex::MODEL_ID,
                               "plan" => retrieval_plan(mode) })
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
      profile = registry.retrieval_profiles[domain] || {}
      assertions = if as_of
                     assertions_as_of(id, as_of)
                   elsif profile["allow_cold_by_default"]
                     assertion_rows("node_id = ? AND status = 'confirmed'", [id])
                   else
                     assertion_rows("node_id = ? AND temperature = 'hot' AND status = 'confirmed'", [id])
                   end
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
      card = card.merge("current_assertions" => assertions, "as_of" => as_of) if as_of
      envelope({ "domain" => domain, "workflow" => pack["workflow"], "steps" => steps,
                 "retrieval_profile" => profile, "entity_card" => card,
                 "assertions" => ordered, "relations" => relations },
               temporal: { "as_of" => as_of || "current", "assertion_projection" => as_of ? "historical" : "current" },
               gaps: gaps)
    end

    def propose(actor:, target_source:, patch:, reason:, risk: "normal", title: nil, operations: [])
      raise ValidationError, "risk must be low, normal, or high" unless %w[low normal high].include?(risk)
      raise ValidationError, "actor is required" if actor.to_s.strip.empty?
      raise ValidationError, "target_source is required" if target_source.to_s.strip.empty?
      raise ValidationError, "reason is required" if reason.to_s.strip.empty?
      id = SecureRandom.uuid
      now = Time.now.utc.iso8601(6)
      status = risk == "low" ? "proposed" : "review_required"
      base_revision = @publication_verifier.current_revision(target_source)
      database.transaction do
        database.execute(
          "INSERT INTO changeset(id, actor, title, target_source, risk, status, patch_json, operations_json, reason, created_at, base_revision) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
          [id, actor, title, target_source, risk, status, JSON.generate(patch), JSON.generate(operations), reason, now, base_revision]
        )
        @audit.stage(event_type: "agent_proposal", actor: actor, target_id: id, source_ref: target_source,
                     before_hash: base_revision, reason: reason,
                     payload: { "actor" => actor, "risk" => risk, "patch" => patch })
      end
      @audit.flush!
      envelope({ "id" => id, "status" => status, "target_source" => target_source,
                 "base_revision" => base_revision })
    end

    def review(priority: nil, limit: 100)
      sql = "SELECT * FROM review_item WHERE status = 'open'"
      binds = []
      if priority
        sql += " AND priority = ?"
        binds << priority
      end
      sql += " ORDER BY CASE priority WHEN 'P0' THEN 0 WHEN 'P1' THEN 1 WHEN 'P2' THEN 2 ELSE 3 END, severity DESC, created_at LIMIT ?"
      binds << @policy_engine.review_limit(limit)
      items = database.execute(sql, binds).map do |row|
        clean(row).merge("details" => JSON.parse(row["details_json"])).reject { |key, _| key == "details_json" }
      end
      envelope(items)
    end

    def review_changeset(id:, reviewer:, decision:, note: nil)
      raise ValidationError, "decision must be approved, rejected, or changes_requested" unless %w[approved rejected changes_requested].include?(decision)
      row = database.first("SELECT * FROM changeset WHERE id = ?", id)
      raise NotFoundError, "changeset not found: #{id}" unless row
      unless %w[proposed review_required].include?(row["status"])
        raise ValidationError, "changeset in #{row['status']} cannot be reviewed"
      end
      now = Time.now.utc.iso8601(6)
      review_note = note.to_s.strip
      review_note = "governance decision: #{decision}" if review_note.empty?
      database.transaction do
        database.execute("UPDATE changeset SET status = ?, reviewed_by = ?, reviewed_at = ?, review_note = ? WHERE id = ?", [decision, reviewer, now, review_note, id])
        @audit.stage(event_type: "changeset_#{decision}", actor: reviewer, target_id: id,
                     source_ref: row["target_source"], reason: "review decision",
                     payload: { "reviewer" => reviewer })
      end
      @audit.flush!
      envelope({ "id" => id, "status" => decision, "note" => "Approval records governance only; apply the patch to the real source of truth, then compile." })
    end

    def publish_changeset(id:, publisher:, source_revision:)
      row = database.first("SELECT * FROM changeset WHERE id = ?", id)
      raise NotFoundError, "changeset not found: #{id}" unless row
      raise ValidationError, "changeset must be approved before publication" unless row["status"] == "approved"
      raise ValidationError, "source_revision is required" if source_revision.to_s.empty?
      verification = @publication_verifier.verify!(target_source: row["target_source"],
                                                    source_revision: source_revision,
                                                    base_revision: row["base_revision"])
      now = Time.now.utc.iso8601(6)
      database.transaction do
        database.execute("UPDATE changeset SET status = 'published', published_by = ?, published_at = ?, source_revision = ?, publication_json = ? WHERE id = ?",
                         [publisher, now, source_revision, JSON.generate(verification), id])
        @audit.stage(event_type: "changeset_published", actor: publisher, target_id: id,
                     source_ref: row["target_source"], before_hash: row["base_revision"], after_hash: source_revision,
                     reason: "source of truth updated, verified, and compiled",
                     payload: { "publisher" => publisher, "source_revision" => source_revision,
                                "verification" => verification })
      end
      @audit.flush!
      envelope({ "id" => id, "status" => "published", "source_revision" => source_revision,
                 "verification" => verification })
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

    def keyword_search(query, type)
      return fallback_search(query, type).map { |row| clean(row) } if !database.fts_enabled || query.to_s.strip.empty?
      database.execute(
        <<~SQL,
          SELECT n.id, n.type, n.label, n.lifecycle, n.source_class, bm25(node_fts) AS keyword_score
          FROM node_fts JOIN node n ON n.id = node_fts.node_id
          WHERE node_fts MATCH ? #{type ? 'AND n.type = ?' : ''}
          ORDER BY keyword_score LIMIT 50
        SQL
        type ? [fts_query(query), type] : [fts_query(query)]
      ).map { |row| clean(row) }
    rescue SQLite3::SQLException
      fallback_search(query, type).map { |row| clean(row) }
    end

    def hybrid_rows(lexical, vector)
      combined = {}
      lexical.each_with_index do |row, index|
        combined[row["id"]] = row.merge("score" => 0.55 / (index + 1))
      end
      vector.each_with_index do |row, index|
        current = combined[row["id"]] || row
        combined[row["id"]] = current.merge(row).merge("score" => current.fetch("score", 0.0) + 0.45 / (index + 1))
      end
      combined.values.sort_by { |row| -row["score"] }.first(50)
    end

    def retrieval_plan(mode)
      case mode
      when "keyword" then ["fts_or_label"]
      when "vector" then ["deterministic_embedding"]
      when "structured_first" then ["structured_identity", "fts", "deterministic_embedding"]
      when "temporal_graph_first" then ["temporal_filter", "graph_context", "fts", "deterministic_embedding"]
      else ["fts", "deterministic_embedding", "rank_fusion"]
      end
    end

    def assertions_as_of(node_id, as_of)
      Time.iso8601(as_of.to_s)
      rows = assertion_rows(
        <<~SQL, [node_id, as_of, as_of, as_of]
          node_id = ? AND status IN ('confirmed','superseded','stale')
          AND (valid_from IS NULL OR valid_from = '' OR valid_from <= ?)
          AND (valid_to IS NULL OR valid_to = '' OR valid_to > ?)
          AND (observed_at IS NULL OR observed_at = '' OR observed_at <= ?)
        SQL
      )
      superseded = rows.map { |item| item["supersedes"] }.compact
      rows.reject { |item| superseded.include?(item["id"]) }
    rescue ArgumentError
      raise ValidationError, "invalid as_of timestamp: #{as_of}"
    end

    def fts_query(query)
      query.to_s.scan(/[\p{L}\p{N}_:-]+/).map { |term| '"' + term.gsub('"', '""') + '"' }.join(" OR ")
    end

    def provenance_summary(assertions)
      counts = assertions.group_by { |item| item["provenance_tier"] || item.dig("provenance", "tier") }.transform_values(&:length)
      { "tiers" => counts, "checked" => assertions.length }
    end

    def refresh_temperatures!
      database.execute("SELECT id, predicate, status, observed_at, valid_to, temperature FROM assertion").each do |row|
        temperature = @policy_engine.assertion_temperature(
          predicate_id: row["predicate"], status: row["status"], observed_at: row["observed_at"],
          valid_to: row["valid_to"]
        )
        database.execute("UPDATE assertion SET temperature = ? WHERE id = ?", [temperature, row["id"]]) if temperature != row["temperature"]
      end
    end

    def card_assertion(row)
      {
        "id" => row["id"], "predicate" => row["predicate"], "value" => JSON.parse(row["value_json"]),
        "observed_at" => row["observed_at"], "valid_from" => row["valid_from"], "valid_to" => row["valid_to"],
        "assertion_kind" => row["assertion_kind"], "status" => row["status"], "confidence" => row["confidence"],
        "provenance_tier" => row["provenance_tier"], "temperature" => row["temperature"]
      }
    end

    def escape_like(value)
      value.to_s.gsub(/[\\%_]/) { |char| "\\#{char}" }
    end

    def edge_key(edge)
      %w[src predicate dst rel_id valid_from].map { |key| edge[key] }.join("|")
    end

    def clean(row)
      row.each_with_object({}) { |(key, value), out| out[key] = value if key.is_a?(String) }
    end
  end
end
