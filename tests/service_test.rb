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

      assert_equal "3.1", control["contract_version"]
      assert_equal 7, control.dig("ontology", "concept_types").length
      assert_equal 6, control.dig("ontology", "relation_types").length
      assert_equal 9, control["predicates"].length
      assert_equal %w[authority freshness maintenance provenance], control["policies"].keys.sort
      assert_equal ["cost_rollup"], control["models"].keys
      assert_equal %w[cost industry pm], control["domains"].keys.sort
      assert_equal %w[cost industry pm], control["retrieval_profiles"].keys.sort
      assert_equal ["erp-suppliers"], control["connectors"].keys
      service.close
    end
  end

  def test_studio_contract_maps_every_control_plane_definition
    with_workspace do |config|
      service = KnowledgeOS::Service.new(config: config)
      studio = service.studio["data"]

      assert_equal "3.1", studio["contract_version"]
      assert_equal 32, studio["definitions"].length
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
      published = service.publish_changeset(id: proposal["id"], publisher: "publisher:test", source_revision: "abc123")["data"]
      stored = service.changesets["data"].find { |item| item["id"] == proposal["id"] }

      assert_equal "published", published["status"]
      assert_equal "abc123", stored["source_revision"]
      assert_equal [{ "type" => "update", "target" => "organization" }], stored["operations"]
      assert service.ledger.verify!["valid"]
      service.close
    end
  end
end
