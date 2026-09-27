# frozen_string_literal: true

require_relative "test_helper"

class ServiceTest < Minitest::Test
  include WorkspaceHelper

  def test_crltp_context_and_graph_traversal
    with_workspace do |config|
      service = compiled_service(config)
      context = service.context("org:acme", domain: "industry")["data"]

      assert_equal %w[C R L T P], context["steps"].map { |step| step["dimension"] }
      assert_includes context["relations"]["nodes"].map { |node| node["id"] }, "market:industrial-automation"
      assert_equal "industry", context["domain"]
      service.close
    end
  end

  def test_deterministic_model_is_traced_and_idempotent
    with_workspace do |config|
      service = compiled_service(config)
      inputs = { "material_cost" => 100, "labor_hours" => 1.5, "labor_rate" => 20, "overhead" => 8 }
      first = service.calculate("cost_rollup", inputs)["data"]
      second = service.calculate("cost_rollup", inputs)["data"]

      assert_equal "138.0", first["output"]["value"]
      assert_equal first["id"], second["id"]
      assert_equal first["run_id"], second["run_id"]
      refute_empty first["trace"]
      service.close
    end
  end

  def test_enterprise_connector_is_idempotent_and_does_not_write_markdown
    with_workspace do |config|
      service = compiled_service(config)
      connector = KnowledgeOS::Connector.new(config: config, registry: service.registry, database: service.database, ledger: service.ledger)
      input = config.root.join("connectors/examples/erp-suppliers.ndjson")
      mapping = config.root.join("connectors/examples/erp-suppliers.mapping.yaml")

      first = connector.ingest_ndjson(input, mapping)
      second = connector.ingest_ndjson(input, mapping)
      card = service.get("org:acme")["data"]

      assert_equal 1, first["ingested"]
      assert_equal 1, second["skipped"]
      assert_equal "Singapore", card["attrs"]["country"]
      assert_equal "Acme ERP Legal Name Pte. Ltd.", card["attrs"]["legal_name"]
      assert_equal 42, card["current_assertions"].find { |item| item["predicate"] == "lead_time_days" }["value"]["literal"]
      assert_equal 5, config.knowledge_dir.glob("**/*.md").length
      service.close
    end
  end

  def test_changeset_records_governance_without_mutating_truth
    with_workspace do |config|
      service = compiled_service(config)
      proposal = service.propose(actor: "agent:test", target_source: "knowledge/entities/org-acme.md",
                                 patch: { "op" => "replace", "path" => "/knowledge/attrs/country", "value" => "CN" },
                                 reason: "normalize country", risk: "normal")["data"]
      reviewed = service.review_changeset(id: proposal["id"], reviewer: "reviewer:test", decision: "approved", note: "contract checked")["data"]

      assert_equal "review_required", proposal["status"]
      assert_equal "approved", reviewed["status"]
      assert_equal "contract checked", service.changesets["data"].first["review_note"]
      assert_equal "China", service.get("org:acme")["data"]["attrs"]["country"]
      assert service.ledger.verify!["valid"]
      service.close
    end
  end

  def test_control_plane_exposes_complete_non_instance_contract
    with_workspace do |config|
      service = KnowledgeOS::Service.new(config: config)
      control = service.control_plane["data"]

      assert_equal "3.5", control["contract_version"]
      assert_equal 7, control.dig("ontology", "concept_types").length
      assert_equal 6, control.dig("ontology", "relation_types").length
      assert_equal 9, control["predicates"].length
      assert_equal %w[authority freshness maintenance provenance], control["policies"].keys.sort
      assert_equal %w[cost_rollup project_risk_score schedule_variance], control["models"].keys.sort
      assert_equal %w[cost industry pm], control["domains"].keys.sort
      assert_equal %w[cost industry pm], control["retrieval_profiles"].keys.sort
      assert_equal ["erp-suppliers"], control["connectors"].keys
      assert_equal ["default"], control["extraction_profiles"].keys
      assert_equal 1, control.dig("extensions", "constraints").length
      assert_equal 1, control.dig("extensions", "rules").length
      assert_equal 3, control.dig("extensions", "skills").length
      service.close
    end
  end

  def test_studio_contract_maps_every_control_plane_definition
    with_workspace do |config|
      service = KnowledgeOS::Service.new(config: config)
      studio = service.studio["data"]

      assert_equal "3.5", studio["contract_version"]
      assert_equal 36, studio["definitions"].length
      assert_equal 7, studio.dig("coverage", "concept")
      assert_equal 6, studio.dig("coverage", "relation")
      assert_equal 9, studio.dig("coverage", "predicate")
      assert_equal "changeset_only", studio.dig("capabilities", "durable_writes")
      assert_equal [], studio["changesets"]

      supplies = studio["definitions"].find { |item| item["id"] == "supplies" }
      assert_equal "reifiable", supplies["relation_mode"]
      assert_equal "organization", supplies.dig("endpoints", 0, "source_concept_id")
      assert_equal "relation", supplies.dig("reification", "node_type")

      risk = studio["definitions"].find { |item| item["id"] == "risk_level" }
      assert_equal "A", risk.dig("config", "provenance_tier")
      assert_equal "control/predicates/risk_level.yaml", risk["source_path"]
      service.close
    end
  end

  def test_agent_request_selects_domain_skill_and_returns_grounded_evidence
    with_workspace do |config|
      service = compiled_service(config)
      request = service.agent_request(question: "What is Acme's industry position?", domain: "industry",
                                      target: "org:acme")["data"]

      assert_equal "knowledgeos.agent.v1", request["protocol_version"]
      assert_equal "industry-evidence-brief", request.dig("skill", "id")
      assert_equal "C-R-L-T-P", request.dig("reasoning_plan", "framework")
      assert_match(/decision-oriented research brief/, request.dig("reasoning_plan", "skill_instructions"))
      assert_includes request["evidence"].map { |item| item["ref"] }, "assertion:acme-finding-2026-01"
      assert_includes request["available_tools"].map { |item| item["name"] }, "search"
      assert_equal false, service.agent_capabilities(domain: "industry").dig("quality_status", "write_performed")
      service.close
    end
  end

  def test_agent_skill_bundle_is_independently_copyable
    with_workspace do |config|
      service = KnowledgeOS::Service.new(config: config)
      bundle = service.agent_skill(id: "industry-evidence-brief")

      assert_equal "portable-skill-directory", bundle.dig("data", "package", "format")
      assert_equal %w[SKILL.md contract.yaml], bundle.dig("data", "files").keys
      assert_match(/name: industry-evidence-brief/, bundle.dig("data", "files", "SKILL.md"))
      assert_match(/knowledgeos.skill-contract.v1/, bundle.dig("data", "files", "contract.yaml"))
      assert_equal true, bundle.dig("quality_status", "portable")
      service.close
    end
  end

  def test_agent_response_requires_known_evidence_references
    with_workspace do |config|
      service = compiled_service(config)
      request = service.agent_request(question: "Assess Atlas risk", domain: "pm", target: "project:atlas")["data"]
      output = {
        "protocol_version" => request["protocol_version"], "request_id" => request["request_id"],
        "answer" => "Atlas has a medium risk assessment.",
        "claims" => [{ "statement" => "Atlas risk is medium.",
                        "evidence_refs" => ["assertion:atlas-risk-2026-09"], "confidence" => 0.9 }],
        "knowledge_gaps" => [], "recommended_actions" => []
      }

      response = service.agent_respond(request: request, model_output: output)["data"]
      assert_equal true, response.dig("grounding", "valid")
      assert_equal false, response["write_performed"]

      output["claims"][0]["evidence_refs"] = ["assertion:invented"]
      assert_raises(KnowledgeOS::ValidationError) do
        service.agent_respond(request: request, model_output: output)
      end
      service.close
    end
  end

  def test_agent_tool_invocation_enforces_domain_policy_and_deterministic_models
    with_workspace do |config|
      service = compiled_service(config)
      result = service.agent_invoke(
        domain: "cost", operation: "calculate",
        arguments: { "model_id" => "cost_rollup", "inputs" => {
          "material_cost" => 100, "labor_hours" => 1.5, "labor_rate" => 20, "overhead" => 8
        } }
      )

      assert_equal "138.0", result.dig("data", "output", "value")
      assert_equal "derived:#{result.dig('data', 'run_id')}", result.dig("quality_status", "agent_evidence_ref")
      assert_raises(KnowledgeOS::ValidationError) do
        service.agent_invoke(domain: "pm", operation: "search", arguments: { "query" => "Atlas" })
      end
      assert_raises(KnowledgeOS::ValidationError) do
        service.agent_invoke(domain: "industry", operation: "calculate",
                             arguments: { "model_id" => "cost_rollup", "inputs" => {} })
      end
      service.close
    end
  end

  def test_agent_response_accepts_verified_deterministic_tool_evidence
    with_workspace do |config|
      service = compiled_service(config)
      request = service.agent_request(question: "Calculate the unit cost", domain: "cost",
                                      target: "product:servo-module")["data"]
      tool_result = service.agent_invoke(
        domain: "cost", operation: "calculate",
        arguments: { "model_id" => "cost_rollup", "inputs" => {
          "material_cost" => 100, "labor_hours" => 1.5, "labor_rate" => 20, "overhead" => 8
        } }
      )
      evidence_ref = tool_result.dig("quality_status", "agent_evidence_ref")
      output = {
        "protocol_version" => request["protocol_version"], "request_id" => request["request_id"],
        "answer" => "The calculated unit cost is 138.0 currency units per unit.",
        "claims" => [{ "statement" => "The deterministic roll-up result is 138.0.",
                        "evidence_refs" => [evidence_ref], "confidence" => 1.0 }]
      }

      response = service.agent_respond(request: request, model_output: output, tool_results: [tool_result])["data"]
      assert_includes response.dig("grounding", "cited_evidence_refs"), evidence_ref
      assert_equal true, response.dig("grounding", "valid")
      service.close
    end
  end

  def test_pm_schedule_and_risk_models_are_deterministic
    with_workspace do |config|
      service = compiled_service(config)
      schedule = service.agent_invoke(
        domain: "pm", operation: "calculate",
        arguments: { "model_id" => "schedule_variance", "inputs" => {
          "baseline_finish" => "2026-10-01T00:00:00Z", "forecast_finish" => "2026-10-06T00:00:00Z"
        } }
      )
      risk = service.agent_invoke(
        domain: "pm", operation: "calculate",
        arguments: { "model_id" => "project_risk_score", "inputs" => {
          "schedule_delay_days" => 5, "blocked_dependencies" => 1, "high_risk_items" => 1
        } }
      )

      assert_equal "5.0", schedule.dig("data", "output", "value")
      assert_equal "calendar_days", schedule.dig("data", "output", "unit")
      assert_equal "6.0", risk.dig("data", "output", "value")
      assert_equal "high", risk.dig("data", "output", "classification")
    ensure
      service&.close
    end
  end

  def test_changeset_publication_requires_approval_and_source_revision
    with_workspace do |config|
      service = KnowledgeOS::Service.new(config: config)
      proposal = service.propose(actor: "agent:test", title: "Control change", target_source: "control/ontology/core.yaml",
                                 patch: { "op" => "replace", "path" => "/concept_types/0/label", "value" => "Org" },
                                 operations: [{ "type" => "update", "target" => "organization" }],
                                 reason: "exercise governed publication", risk: "normal")["data"]

      assert_raises(KnowledgeOS::ValidationError) do
        service.publish_changeset(id: proposal["id"], publisher: "publisher:test", source_revision: "abc123")
      end
      service.review_changeset(id: proposal["id"], reviewer: "reviewer:test", decision: "approved")
      assert_raises(KnowledgeOS::ValidationError) do
        service.publish_changeset(id: proposal["id"], publisher: "publisher:test", source_revision: "abc123")
      end
      source_path = config.control_dir.join("ontology/core.yaml")
      source_path.write(source_path.read.sub("label: Organization", "label: Org"))
      revision = "sha256:#{Digest::SHA256.file(source_path).hexdigest}"
      published = service.publish_changeset(id: proposal["id"], publisher: "publisher:test", source_revision: revision)["data"]
      stored = service.changesets["data"].find { |item| item["id"] == proposal["id"] }

      assert_equal "published", published["status"]
      assert_equal revision, stored["source_revision"]
      assert_equal "git_authored", stored.dig("publication", "kind")
      assert_equal [{ "type" => "update", "target" => "organization" }], stored["operations"]
      assert service.ledger.verify!["valid"]
      service.close
    end
  end

  def test_changeset_state_machine_rejects_invalid_transitions
    with_workspace do |config|
      service = KnowledgeOS::Service.new(config: config)
      proposal = service.propose(actor: "agent:test", target_source: "control/ontology/core.yaml",
                                 patch: { "op" => "test" }, reason: "state transition test",
                                 risk: "normal")["data"]
      service.review_changeset(id: proposal["id"], reviewer: "reviewer:test", decision: "rejected")

      error = assert_raises(KnowledgeOS::ValidationError) do
        service.review_changeset(id: proposal["id"], reviewer: "reviewer:test", decision: "approved")
      end
      assert_match(/cannot be reviewed/, error.message)
    ensure
      service&.close
    end
  end

  def test_pending_audit_outbox_is_recovered_idempotently
    with_workspace do |config|
      service = KnowledgeOS::Service.new(config: config)
      audit = KnowledgeOS::AuditCoordinator.new(database: service.database, ledger: service.ledger)
      service.database.transaction do
        audit.stage(event_type: "recovery_test", actor: "test", target_id: "target:1",
                    event_id: "audit-recovery-test")
      end
      service.close

      recovered = KnowledgeOS::Service.new(config: config)
      row = recovered.database.first("SELECT status FROM audit_outbox WHERE event_id = ?", ["audit-recovery-test"])
      assert_equal "delivered", row["status"]
      assert_equal 1, recovered.ledger.events(event_type: "recovery_test").length
      assert recovered.ledger.verify!["valid"]
    ensure
      recovered&.close
    end
  end

  def test_verified_publication_supports_a_deleted_git_authored_source
    with_workspace do |config|
      source = config.knowledge_dir.join("entities/temporary-project.md")
      source.write(<<~MARKDOWN)
        ---
        base:
          schema: { ckm: "3.0" }
          node: { id: "project:temporary", kind: entity, type: project, key: TEMP, label: Temporary Project, aliases: [] }
          classification: { tags: [] }
          lifecycle: { state: active }
          version: { entity_revision: 1 }
        knowledge:
          attrs: { summary: "Temporary publication fixture." }
          assertions: []
          relations: []
          logic_refs: []
        external: { assertions_ref: }
        ---
      MARKDOWN
      service = compiled_service(config)
      proposal = service.propose(actor: "agent:test", target_source: "knowledge/entities/temporary-project.md",
                                 patch: { "op" => "delete" }, operations: [{ "type" => "delete" }],
                                 reason: "remove temporary source", risk: "normal")["data"]
      service.review_changeset(id: proposal["id"], reviewer: "reviewer:test", decision: "approved")
      FileUtils.rm(source)
      verifier = KnowledgeOS::PublicationVerifier.new(config: config, registry: service.registry,
                                                       database: service.database, ledger: service.ledger)
      revision = verifier.current_revision("knowledge/entities/temporary-project.md")
      published = service.publish_changeset(id: proposal["id"], publisher: "publisher:test",
                                            source_revision: revision)["data"]

      assert_equal "published", published["status"]
      assert_nil service.database.first("SELECT id FROM node WHERE id = ?", ["project:temporary"])
    ensure
      service&.close
    end
  end
end
