# frozen_string_literal: true

require_relative "test_helper"

class SecurityTest < Minitest::Test
  include WorkspaceHelper

  Request = Struct.new(:query, :body, :header)

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
    attr_reader :headers

    def initialize
      @headers = {}
    end

    def []=(key, value)
      headers[key] = value
    end
  end

  def test_api_requires_authentication_and_enforces_roles
    with_workspace do |config|
      service = KnowledgeOS::Service.new(config: config)
      server = FakeServer.new
      token = "reader-secret"
      auth = KnowledgeOS::AccessControl.new(
        enabled: true,
        entries: { Digest::SHA256.hexdigest(token) =>
          KnowledgeOS::AccessControl::Principal.new(id: "user:reader", roles: ["reader"]) }
      )
      KnowledgeOS::API.new(service: service, server: server, auth: auth)

      unauthorized = FakeResponse.new
      server.routes.fetch("/v1/studio").call(Request.new({}, nil, {}), unauthorized)
      assert_equal 401, unauthorized.status

      headers = { "authorization" => ["Bearer #{token}"] }
      allowed = FakeResponse.new
      server.routes.fetch("/v1/studio").call(Request.new({}, nil, headers), allowed)
      assert_equal 200, allowed.status
      assert_equal "user:reader", allowed.headers["X-KnowledgeOS-Principal"]

      forbidden = FakeResponse.new
      body = JSON.generate("target_source" => "control/ontology/core.yaml", "patch" => {}, "reason" => "test")
      server.routes.fetch("/v1/propose").call(Request.new({}, body, headers), forbidden)
      assert_equal 403, forbidden.status
    ensure
      service&.close
    end
  end

  def test_auth_configuration_is_fail_closed
    assert_raises(KnowledgeOS::ConfigurationError) do
      KnowledgeOS::AccessControl.from_env({ "KNOWLEDGEOS_AUTH_MODE" => "required" })
    end
  end

  def test_http_governance_actor_comes_from_authenticated_principal
    with_workspace do |config|
      service = KnowledgeOS::Service.new(config: config)
      proposal = service.propose(actor: "agent:test", target_source: "control/ontology/core.yaml",
                                 patch: { "op" => "replace", "path" => "/concept_types/0/label", "value" => "Org" }, reason: "identity test", risk: "normal")["data"]
      token = "reviewer-secret"
      auth = KnowledgeOS::AccessControl.new(
        enabled: true,
        entries: { Digest::SHA256.hexdigest(token) =>
          KnowledgeOS::AccessControl::Principal.new(id: "user:reviewer", roles: ["reviewer"]) }
      )
      server = FakeServer.new
      KnowledgeOS::API.new(service: service, server: server, auth: auth)
      response = FakeResponse.new
      body = JSON.generate("id" => proposal["id"], "decision" => "approved", "reviewer" => "spoofed")
      server.routes.fetch("/v1/changesets/review").call(
        Request.new({}, body, { "authorization" => ["Bearer #{token}"] }), response
      )

      assert_equal 200, response.status
      assert_equal "user:reviewer", service.changesets["data"].first["reviewed_by"]
    ensure
      service&.close
    end
  end
end
