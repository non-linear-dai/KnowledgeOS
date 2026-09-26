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
      reviewed = service.review_changeset(id: proposal["id"], reviewer: "reviewer:test", decision: "approved")["data"]

      assert_equal "review_required", proposal["status"]
      assert_equal "approved", reviewed["status"]
      assert_equal "China", service.get("org:acme")["data"]["attrs"]["country"]
      assert service.ledger.verify!["valid"]
      service.close
    end
  end
end
