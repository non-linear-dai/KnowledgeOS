# frozen_string_literal: true

require_relative "test_helper"

class PolicyTest < Minitest::Test
  include WorkspaceHelper

  def test_freshness_policy_reclassifies_assertions_and_creates_review_items
    with_workspace do |config|
      compiler = KnowledgeOS::Compiler.new(config: config, clock: -> { Time.utc(2027, 1, 20) })
      compiler.compile(rebuild: true)
      row = compiler.database.first("SELECT temperature FROM assertion WHERE id = ?", ["assertion:atlas-risk-2026-09"])
      review = compiler.database.first("SELECT kind FROM review_item WHERE target_id = ?", ["assertion:atlas-risk-2026-09"])

      assert_equal "warm", row["temperature"]
      assert_equal "stale_assertion", review["kind"]
    ensure
      compiler&.close
    end
  end

  def test_context_projects_assertions_at_the_requested_time
    with_workspace do |config|
      service = compiled_service(config)
      before = service.context("project:atlas", domain: "pm", as_of: "2026-09-19T00:00:00Z")
      after = service.context("project:atlas", domain: "pm", as_of: "2026-09-21T00:00:00Z")

      assert_empty before.dig("data", "assertions")
      assert_equal ["assertion:atlas-risk-2026-09"], after.dig("data", "assertions").map { |item| item["id"] }
      assert_equal "historical", after.dig("temporal_status", "assertion_projection")
    ensure
      service&.close
    end
  end

  def test_validator_enforces_enum_cardinality_and_reified_identity
    with_workspace do |config|
      ontology_path = config.ontology_dir.join("core.yaml")
      ontology_path.write(ontology_path.read.sub(
        "{ predicate: task_status, required: true, cardinality: inherit, group: governance }",
        "{ predicate: task_status, required: true, cardinality: one, group: governance }"
      ))
      registry = KnowledgeOS::Registry.new(config)
      validator = KnowledgeOS::Validator.new(registry)
      document = KnowledgeOS::Frontmatter.parse(config.knowledge_dir.join("entities/task-atlas-m1.md"))
      duplicate = Marshal.load(Marshal.dump(document.data.dig("knowledge", "assertions", 0)))
      duplicate["id"] = "assertion:duplicate-status"
      document.data.dig("knowledge", "assertions") << duplicate

      error = assert_raises(KnowledgeOS::ValidationError) { validator.validate_document!(document) }
      assert_match(/allows at most one/, error.message)

      invalid = duplicate.merge("id" => "assertion:bad-enum", "value" => { "type" => "enum", "literal" => "invented" })
      assert_raises(KnowledgeOS::ValidationError) { validator.validate_assertion!("test", invalid) }

      relation_error = assert_raises(KnowledgeOS::ValidationError) do
        validator.validate_relation!("test", { "predicate" => "threatens", "target" => "project:atlas" }, source_type: "relation")
      end
      assert_match(/requires an id/, relation_error.message)
    end
  end

  def test_hybrid_and_vector_search_use_the_semantic_index
    with_workspace do |config|
      service = compiled_service(config)
      result = service.search("industrial automation portfolio", mode: "vector")

      assert_equal true, result.dig("source_status", "vector")
      assert_equal "knowledgeos-hash-embedding-v1", result.dig("source_status", "vector_model")
      assert_includes result["data"].map { |item| item["id"] }, "org:acme"
    ensure
      service&.close
    end
  end
end
