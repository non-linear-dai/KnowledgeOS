# frozen_string_literal: true

require_relative "test_helper"
require "rbconfig"

class ExtractionTest < Minitest::Test
  include WorkspaceHelper

  def test_web_content_generates_a_valid_create_candidate_without_writes
    with_workspace do |config|
      service = compiled_service(config)
      before = service.database.first("SELECT count(*) AS count FROM node")["count"]
      request = service.extraction_request(source: {
        "kind" => "web", "locator" => "https://example.test/tesla",
        "captured_at" => "2026-09-27T00:00:00Z",
        "content" => "Tesla Inc. is a United States electric vehicle and energy company."
      })["data"]
      output = model_output(request, [
        {
          "type" => "organization", "suggested_id" => "org:tesla-inc", "key" => "TESLA-INC",
          "label" => "Tesla Inc.", "aliases" => ["Tesla"],
          "attrs" => { "legal_name" => "Tesla Inc.", "country" => "United States" },
          "assertions" => [{ "predicate" => "finding",
                              "value" => "Tesla is an electric vehicle and energy company." }],
          "relations" => [], "confidence" => 0.91,
          "evidence" => [{ "segment_id" => "segment:0001",
                            "quote" => "Tesla Inc. is a United States electric vehicle and energy company." }]
        }
      ])

      result = service.extraction_candidates(request: request, model_output: output)
      candidate = result.dig("data", "candidates", 0)

      assert_equal "create_instance", candidate["action"]
      assert_equal "org:tesla-inc", candidate["target_id"]
      assert candidate.dig("validation", "valid")
      assert_equal "web", candidate.dig("attribute_candidates", 0, "source_class")
      assert_equal false, result.dig("quality_status", "write_performed")
      assert_equal before, service.database.first("SELECT count(*) AS count FROM node")["count"]
      assert_equal 0, service.database.first("SELECT count(*) AS count FROM changeset")["count"]
      service.close
    end
  end

  def test_existing_entity_generates_incremental_operations
    with_workspace do |config|
      service = compiled_service(config)
      request = service.extraction_request(source: {
        "kind" => "meeting_minutes", "locator" => "meeting://supplier-review/42",
        "captured_at" => "2026-09-27T08:00:00Z",
        "content" => "Acme Industrial Systems Ltd. confirmed that its primary country is Singapore."
      })["data"]
      output = model_output(request, [
        {
          "type" => "organization", "existing_id" => "org:acme", "label" => "Acme Industrial Systems",
          "aliases" => [], "attrs" => { "country" => "Singapore" }, "assertions" => [], "relations" => [],
          "confidence" => 0.95,
          "evidence" => [{ "segment_id" => "segment:0001", "quote" => "primary country is Singapore" }]
        }
      ])

      candidate = service.extraction_candidates(request: request, model_output: output).dig("data", "candidates", 0)
      operation = candidate["operations"].find { |item| item["path"] == "/knowledge/attrs/country" }

      assert_equal "update_instance", candidate["action"]
      assert_equal "replace", operation["op"]
      assert_equal "Singapore", operation["value"]
      assert_equal "China", service.get("org:acme").dig("data", "attrs", "country")
      assert_equal "knowledge/entities/org-acme.md", candidate["target_source"]
      service.close
    end
  end

  def test_candidate_cannot_bypass_current_concept_shape
    with_workspace do |config|
      service = compiled_service(config)
      request = service.extraction_request(source: {
        "kind" => "text", "locator" => "inline:invalid",
        "content" => "Tesla Inc. has a compact company summary."
      })["data"]
      output = model_output(request, [
        { "type" => "organization", "suggested_id" => "org:tesla", "label" => "Tesla Inc.",
          "aliases" => [], "attrs" => { "legal_name" => "Tesla Inc.", "summary" => "Not bound" },
          "assertions" => [], "relations" => [], "confidence" => 0.8,
          "evidence" => [{ "segment_id" => "segment:0001", "quote" => "Tesla Inc." }] }
      ])

      result = service.extraction_candidates(request: request, model_output: output)["data"]

      assert_empty result["candidates"]
      assert_match(/summary is not declared/, result.dig("rejected", 0, "errors", 0))
      assert_equal false, result["write_performed"]
      service.close
    end
  end

  def test_registry_changes_invalidate_old_requests_and_appear_in_new_contracts
    with_workspace do |config|
      service = compiled_service(config)
      source = { "kind" => "text", "locator" => "inline:test", "content" => "Acme website is https://acme.test." }
      old_request = service.extraction_request(source: source)["data"]
      predicate = <<~YAML
        id: website
        label: Website
        value: { type: string, cardinality: one }
        storage: { mode: attr }
        semantics: { description: Official website. }
        policy:
          temporal: none
          evidence: optional
          history: git_only
          embedding: false
          write: reviewed
          freshness: stable
          authority: git_authored
          provenance_tier: C
        status: { lifecycle: active, equivalent_to: }
      YAML
      config.predicates_dir.join("website.yaml").write(predicate)
      ontology_path = config.ontology_dir.join("core.yaml")
      ontology_path.write(ontology_path.read.sub(
        "      - { predicate: country, required: false, cardinality: inherit, group: profile }",
        "      - { predicate: country, required: false, cardinality: inherit, group: profile }\n" \
        "      - { predicate: website, required: false, cardinality: inherit, group: profile }"
      ))
      schema_path = config.schemas_dir.join("canonical-node.schema.json")
      schema_path.write(schema_path.read.sub('"const": "3.0"', '"const": "3.1"'))

      stale_output = model_output(old_request, [])
      error = assert_raises(KnowledgeOS::ValidationError) do
        service.extraction_candidates(request: old_request, model_output: stale_output)
      end
      assert_match(/request is stale/, error.message)

      new_request = service.extraction_request(source: source)["data"]
      organization = new_request.dig("ontology", "concepts").find { |item| item["id"] == "organization" }
      assert_includes organization["properties"].map { |item| item["predicate"] }, "website"
      assert_equal "3.1", new_request.dig("canonical_schema", "properties", "base", "properties",
                                           "schema", "properties", "ckm", "const")
      refute_equal old_request["registry_fingerprint"], new_request["registry_fingerprint"]
      service.close
    end
  end

  def test_audio_materializer_and_command_model_use_provider_neutral_json_protocol
    with_workspace do |config|
      service = compiled_service(config)
      materializer = Class.new do
        def materialize(_source)
          { "content" => "The Atlas program is a governed project.", "mime_type" => "text/plain",
            "metadata" => { "transcriber" => "test" } }
        end
      end.new
      script = <<~'RUBY'
        request = JSON.parse(STDIN.read)
        source = request.fetch("source")
        output = {
          "protocol_version" => request.fetch("protocol_version"),
          "registry_fingerprint" => request.fetch("registry_fingerprint"),
          "entities" => [{
            "type" => "project", "existing_id" => "project:atlas", "label" => "Project Atlas",
            "aliases" => [], "attrs" => {}, "assertions" => [], "relations" => [], "confidence" => 0.9,
            "evidence" => [{ "segment_id" => source.fetch("segments").first.fetch("id"),
                              "quote" => "Atlas program is a governed project" }]
          }],
          "unmapped_facts" => []
        }
        STDOUT.write(JSON.generate(output))
      RUBY
      adapter = KnowledgeOS::CommandModelAdapter.new([RbConfig.ruby, "-rjson", "-e", script])
      result = service.extract_candidates(
        source: { "kind" => "audio", "locator" => "file:///meeting.m4a" },
        model_adapter: adapter, materializer: materializer
      )

      assert_equal "audio", result.dig("data", "source", "kind")
      assert_equal "test", result.dig("data", "source", "metadata", "transcriber")
      assert_equal "no_change", result.dig("data", "candidates", 0, "action")
      service.close
    end
  end

  private

  def model_output(request, entities)
    { "protocol_version" => request["protocol_version"],
      "registry_fingerprint" => request["registry_fingerprint"],
      "entities" => entities, "unmapped_facts" => [] }
  end
end
