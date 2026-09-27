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
      KnowledgeOS::API.new(service: service, server: server)
      response = FakeResponse.new
      request = Struct.new(:query, :body).new({}, nil)
      server.routes.fetch("/v1/studio").call(request, response)
      payload = JSON.parse(response.body)

      assert_equal 200, response.status
      assert_equal "3.1", payload.dig("data", "contract_version")
      assert_equal 32, payload.dig("data", "definitions").length
      assert_equal "git_authored", payload.dig("source_status", "class")
    ensure
      service&.close
    end
  end
end
