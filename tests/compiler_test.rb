# frozen_string_literal: true

require_relative "test_helper"

class CompilerTest < Minitest::Test
  include WorkspaceHelper

  def test_rebuild_and_incremental_compile_are_idempotent
    with_workspace do |config|
      compiler = KnowledgeOS::Compiler.new(config: config)
      first = compiler.compile(rebuild: true)
      second = compiler.compile(rebuild: false)

      assert_equal 5, first["files"]
      assert_equal 5, first["changed"].length
      assert_empty second["changed"]
      assert_equal 5, second["skipped"].length
      assert_equal 5, compiler.database.first("SELECT count(*) AS count FROM node")["count"]
      assert_equal 6, compiler.database.first("SELECT count(*) AS count FROM assertion")["count"]
      compiler.close
    end
  end

  def test_external_assertions_and_temperature_are_projected
    with_workspace do |config|
      service = compiled_service(config)
      history = service.history("market:industrial-automation")["data"]["assertions"]

      assert_equal 2, history.length
      market_size = history.find { |item| item["predicate"] == "market_size" }
      assert_equal "warm", market_size["temperature"]
      assert_equal "USD_billion", market_size["value"]["unit"]
      service.close
    end
  end

  def test_all_reference_domains_share_the_same_node_table
    with_workspace do |config|
      service = compiled_service(config)
      %w[organization market product project task].each do |type|
        rows = service.query("nodes_by_type", "type" => type)["data"]
        refute_empty rows
      end
      tables = service.database.execute("SELECT name FROM sqlite_master WHERE type='table'").map { |row| row["name"] }
      refute tables.any? { |name| name =~ /cost|industry|project_node/ }
      service.close
    end
  end

  def test_concept_shape_and_logic_refs_are_compiler_contracts
    with_workspace do |config|
      path = config.knowledge_dir.join("entities/org-acme.md")
      source = path.read.sub("country: China", "country: China\n    summary: Not declared for organizations")
      path.write(source)
      compiler = KnowledgeOS::Compiler.new(config: config)

      error = assert_raises(KnowledgeOS::ValidationError) { compiler.compile(rebuild: true) }
      assert_match(/summary is not declared for concept organization/, error.message)
      compiler.close
    end
  end
end
