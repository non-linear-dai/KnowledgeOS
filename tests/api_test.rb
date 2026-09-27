# frozen_string_literal: true

require_relative "test_helper"
class APITest < Minitest::Test
  include WorkspaceHelper

  class FakeServer
    attr_reader :routes

    def initialize
      @routes = {}
    end

    def mount_proc(path, &block)
      routes[path] = block
    end

    def config
      { Port: 0 }
    end

    def shutdown; end
  end

  class FakeResponse
    attr_accessor :status, :body

    def initialize
      @headers = {}
    end

    def []=(key, value)
      @headers[key] = value
    end
  end

  def test_studio_endpoint_exposes_ui_contract
    with_workspace do |config|
      service = KnowledgeOS::Service.new(config: config)
      server = FakeServer.new
      KnowledgeOS::API.new(service: service, server: server, auth: KnowledgeOS::AccessControl.disabled)
      response = FakeResponse.new
      request = Struct.new(:query, :body).new({}, nil)
      server.routes.fetch("/v1/studio").call(request, response)
      payload = JSON.parse(response.body)

      assert_equal 200, response.status
      assert_equal "3.5", payload.dig("data", "contract_version")
      assert_equal 36, payload.dig("data", "definitions").length
      assert_equal "git_authored", payload.dig("source_status", "class")
    ensure
      service&.close
    end
  end

  def test_agent_skill_api_returns_portable_two_file_package
    with_workspace do |config|
      service = KnowledgeOS::Service.new(config: config)
      server = FakeServer.new
      KnowledgeOS::API.new(service: service, server: server, auth: KnowledgeOS::AccessControl.disabled)
      response = FakeResponse.new
      request = Struct.new(:query, :body).new({ "id" => "project-risk-review" }, nil)
      server.routes.fetch("/v1/agent/skill").call(request, response)
      payload = JSON.parse(response.body)

      assert_equal 200, response.status
      assert_includes payload.dig("data", "files", "SKILL.md"), "# Project Risk Review"
      assert_includes payload.dig("data", "files", "contract.yaml"), "domain: pm"
    ensure
      service&.close
    end
  end

  def test_agent_api_prepares_and_validates_a_grounded_response
    with_workspace do |config|
      service = compiled_service(config)
      server = FakeServer.new
      KnowledgeOS::API.new(service: service, server: server, auth: KnowledgeOS::AccessControl.disabled)
      request_response = FakeResponse.new
      request_http = Struct.new(:query, :body).new({}, JSON.generate(
        "question" => "What is the current Atlas risk?", "domain" => "pm", "target" => "project:atlas"
      ))
      server.routes.fetch("/v1/agent/request").call(request_http, request_response)
      agent_request = JSON.parse(request_response.body).fetch("data")
      model_output = {
        "protocol_version" => agent_request["protocol_version"], "request_id" => agent_request["request_id"],
        "answer" => "Atlas is currently assessed at medium risk.",
        "claims" => [{ "statement" => "The recorded risk is medium.",
                        "evidence_refs" => ["assertion:atlas-risk-2026-09"], "confidence" => 0.9 }]
      }
      response = FakeResponse.new
      http = Struct.new(:query, :body).new({}, JSON.generate("request" => agent_request, "model_output" => model_output))
      server.routes.fetch("/v1/agent/respond").call(http, response)
      payload = JSON.parse(response.body)

      assert_equal 200, request_response.status
      assert_equal 200, response.status
      assert_equal "project-risk-review", payload.dig("data", "skill_id")
      assert_equal true, payload.dig("data", "grounding", "valid")
      assert_equal false, payload.dig("data", "write_performed")
    ensure
      service&.close
    end
  end

  def test_agent_invoke_api_rejects_tools_outside_domain_policy
    with_workspace do |config|
      service = compiled_service(config)
      server = FakeServer.new
      KnowledgeOS::API.new(service: service, server: server, auth: KnowledgeOS::AccessControl.disabled)
      response = FakeResponse.new
      request = Struct.new(:query, :body).new({}, JSON.generate(
        "domain" => "cost", "operation" => "propose", "arguments" => {}
      ))
      server.routes.fetch("/v1/agent/invoke").call(request, response)

      assert_equal 422, response.status
      assert_match(/not allowed/, JSON.parse(response.body)["error"])
    ensure
      service&.close
    end
  end


  def test_extraction_two_phase_api_is_model_provider_independent
    with_workspace do |config|
      service = compiled_service(config)
      server = FakeServer.new
      KnowledgeOS::API.new(service: service, server: server, auth: KnowledgeOS::AccessControl.disabled)
      source = { "kind" => "text", "locator" => "inline:api", "captured_at" => "2026-09-27T00:00:00Z",
                 "content" => "Acme Industrial Systems is an organization." }
      request_response = FakeResponse.new
      request_http = Struct.new(:query, :body).new({}, JSON.generate("source" => source))
      server.routes.fetch("/v1/extraction/request").call(request_http, request_response)
      extraction_request = JSON.parse(request_response.body).fetch("data")
      model_output = {
        "protocol_version" => extraction_request["protocol_version"],
        "registry_fingerprint" => extraction_request["registry_fingerprint"],
        "entities" => [{ "type" => "organization", "existing_id" => "org:acme",
                          "label" => "Acme Industrial Systems", "aliases" => [], "attrs" => {},
                          "assertions" => [], "relations" => [], "confidence" => 0.9,
                          "evidence" => [{ "segment_id" => "segment:0001",
                                          "quote" => "Acme Industrial Systems is an organization" }] }],
        "unmapped_facts" => []
      }
      candidates_response = FakeResponse.new
      candidates_http = Struct.new(:query, :body).new({}, JSON.generate(
        "request" => extraction_request, "model_output" => model_output
      ))
      server.routes.fetch("/v1/extraction/candidates").call(candidates_http, candidates_response)
      payload = JSON.parse(candidates_response.body)

      assert_equal 200, request_response.status
      assert_equal 200, candidates_response.status
      assert_equal "no_change", payload.dig("data", "candidates", 0, "action")
      assert_equal false, payload.dig("data", "write_performed")
    ensure
      service&.close
    end
  end


  def test_extraction_api_does_not_read_server_local_paths
    with_workspace do |config|
      service = KnowledgeOS::Service.new(config: config)
      server = FakeServer.new
      KnowledgeOS::API.new(service: service, server: server, auth: KnowledgeOS::AccessControl.disabled)
      response = FakeResponse.new
      request = Struct.new(:query, :body).new({}, JSON.generate(
        "source" => { "kind" => "file", "locator" => config.root.join("README.md").to_s }
      ))
      server.routes.fetch("/v1/extraction/request").call(request, response)

      assert_equal 422, response.status
      assert_match(/materialized content/, JSON.parse(response.body)["error"])
    ensure
      service&.close
    end
  end
end
