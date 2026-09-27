# frozen_string_literal: true

require "webrick"
require "json"

module KnowledgeOS
  class API
    def initialize(service:, bind: "127.0.0.1", port: 8787, server: nil, auth: nil)
      @service = service
      @auth = auth || AccessControl.from_env
      @server = server || WEBrick::HTTPServer.new(
        BindAddress: bind,
        Port: port,
        AccessLog: [],
        Logger: WEBrick::Log.new($stderr, WEBrick::Log::WARN)
      )
      mount_routes
    end

    def start(install_signal_handlers: true)
      if install_signal_handlers
        trap("INT") { @server.shutdown }
        trap("TERM") { @server.shutdown }
      end
      @server.start
    ensure
      @service.close
    end

    def shutdown
      @server.shutdown
    end

    def port
      @server.config[:Port]
    end

    private

    def mount_routes
      @server.mount_proc("/health") do |_request, response|
        count = @service.database.first("SELECT count(*) AS count FROM node")["count"]
        respond(response, 200, { "status" => "ok", "nodes" => count })
      end
      @server.mount_proc("/v1/resolve") { |request, response| dispatch(response, request: request) { @service.resolve(param(request, "query"), scope: request.query["scope"]) } }
      @server.mount_proc("/v1/control") { |request, response| dispatch(response, request: request) { @service.control_plane } }
      @server.mount_proc("/v1/studio") { |request, response| dispatch(response, request: request) { @service.studio } }
      @server.mount_proc("/v1/agent/capabilities") do |request, response|
        dispatch(response, request: request) { @service.agent_capabilities(domain: request.query["domain"]) }
      end
      @server.mount_proc("/v1/agent/skill") do |request, response|
        dispatch(response, request: request) { @service.agent_skill(id: param(request, "id")) }
      end
      @server.mount_proc("/v1/agent/request") do |request, response|
        dispatch(response, request: request, permission: "agent") do
          require_method!(request, "POST")
          body = json_body(request)
          @service.agent_request(question: body.fetch("question"), domain: body.fetch("domain"), target: body["target"],
                                 query: body["query"], skill: body["skill"], as_of: body["as_of"],
                                 max_items: body.fetch("max_items", 25))
        end
      end
      @server.mount_proc("/v1/agent/respond") do |request, response|
        dispatch(response, request: request, permission: "agent") do
          require_method!(request, "POST")
          body = json_body(request)
          @service.agent_respond(request: body.fetch("request"), model_output: body.fetch("model_output"),
                                 tool_results: body.fetch("tool_results", []))
        end
      end
      @server.mount_proc("/v1/agent/invoke") do |request, response|
        dispatch(response, request: request, permission: "agent") do |principal|
          require_method!(request, "POST")
          body = json_body(request)
          arguments = body.fetch("arguments", {})
          arguments = arguments.merge("actor" => principal.id) if body.fetch("operation") == "propose"
          @service.agent_invoke(domain: body.fetch("domain"), operation: body.fetch("operation"),
                                arguments: arguments)
        end
      end
      @server.mount_proc("/v1/extraction/request") do |request, response|
        dispatch(response, request: request, permission: "extract") do
          require_method!(request, "POST")
          body = json_body(request)
          source = body.fetch("source")
          unless source["content"] || source["segments"]
            raise ValidationError, "API extraction sources must include materialized content or a source envelope"
          end
          @service.extraction_request(source: source, profile: body.fetch("profile", "default"))
        end
      end
      @server.mount_proc("/v1/extraction/candidates") do |request, response|
        dispatch(response, request: request, permission: "extract") do
          require_method!(request, "POST")
          body = json_body(request)
          @service.extraction_candidates(request: body.fetch("request"), model_output: body.fetch("model_output"),
                                         profile: body.fetch("profile", "default"))
        end
      end
      @server.mount_proc("/v1/changesets") do |request, response|
        dispatch(response, request: request) { @service.changesets(status: request.query["status"], limit: request.query.fetch("limit", 100)) }
      end
      @server.mount_proc("/v1/changesets/review") do |request, response|
        dispatch(response, request: request, permission: "review") do |principal|
          require_method!(request, "POST")
          body = json_body(request)
          @service.review_changeset(id: body.fetch("id"), reviewer: principal.id, decision: body.fetch("decision"), note: body["note"])
        end
      end
      @server.mount_proc("/v1/changesets/publish") do |request, response|
        dispatch(response, request: request, permission: "publish") do |principal|
          require_method!(request, "POST")
          body = json_body(request)
          @service.publish_changeset(id: body.fetch("id"), publisher: principal.id, source_revision: body.fetch("source_revision"))
        end
      end
      @server.mount_proc("/v1/get") { |request, response| dispatch(response, request: request) { @service.get(param(request, "id"), include_history: request.query["history"] == "true") } }
      @server.mount_proc("/v1/search") { |request, response| dispatch(response, request: request) { @service.search(param(request, "query"), filters: { "type" => request.query["type"] }.compact, mode: request.query.fetch("mode", "hybrid")) } }
      @server.mount_proc("/v1/neighbors") do |request, response|
        dispatch(response, request: request) do
          @service.neighbors(param(request, "id"), relation_types: csv(request.query["relation_types"]),
                             depth: request.query.fetch("depth", 1), as_of: request.query["as_of"])
        end
      end
      @server.mount_proc("/v1/history") { |request, response| dispatch(response, request: request) { @service.history(param(request, "id"), predicate: request.query["predicate"]) } }
      @server.mount_proc("/v1/explain") { |request, response| dispatch(response, request: request) { @service.explain(param(request, "target"), field_or_assertion: request.query["item"]) } }
      @server.mount_proc("/v1/context") do |request, response|
        dispatch(response, request: request) { @service.context(param(request, "id"), domain: param(request, "domain"), as_of: request.query["as_of"]) }
      end
      @server.mount_proc("/v1/query") do |request, response|
        dispatch(response, request: request) do
          require_method!(request, "POST")
          body = json_body(request)
          @service.query(body.fetch("template"), body.fetch("params", {}))
        end
      end
      @server.mount_proc("/v1/calculate") do |request, response|
        dispatch(response, request: request, permission: "agent") do
          require_method!(request, "POST")
          body = json_body(request)
          @service.calculate(body.fetch("model_id"), body.fetch("inputs"), scenario: body.fetch("scenario", "default"))
        end
      end
      @server.mount_proc("/v1/propose") do |request, response|
        dispatch(response, success: 201, request: request, permission: "propose") do |principal|
          require_method!(request, "POST")
          body = json_body(request)
          @service.propose(actor: principal.id, target_source: body.fetch("target_source"),
                           patch: body.fetch("patch"), reason: body.fetch("reason"), risk: body.fetch("risk", "normal"),
                           title: body["title"], operations: body.fetch("operations", []))
        end
      end
      @server.mount_proc("/v1/review") { |request, response| dispatch(response, request: request, permission: "review") { @service.review(priority: request.query["priority"], limit: request.query.fetch("limit", 100)) } }
    end

    def dispatch(response, success: 200, request:, permission: "read")
      principal = @auth.authenticate(request)
      @auth.authorize!(principal, permission)
      response["X-KnowledgeOS-Principal"] = principal.id
      respond(response, success, yield(principal))
    rescue AuthenticationError => e
      response["WWW-Authenticate"] = 'Bearer realm="KnowledgeOS"'
      respond(response, 401, { "error" => e.message })
    rescue AuthorizationError => e
      respond(response, 403, { "error" => e.message })
    rescue MethodNotAllowedError => e
      respond(response, 405, { "error" => e.message })
    rescue NotFoundError => e
      respond(response, 404, { "error" => e.message })
    rescue KeyError, ValidationError => e
      respond(response, 422, { "error" => e.message })
    rescue StandardError => e
      warn "KnowledgeOS API error: #{e.class}: #{e.message}"
      respond(response, 500, { "error" => "internal server error" })
    end

    def respond(response, status, object)
      response.status = status
      response["Content-Type"] = "application/json; charset=utf-8"
      response.body = JSON.generate(object)
    end

    def param(request, name)
      value = request.query[name]
      raise ValidationError, "missing parameter: #{name}" if value.to_s.empty?
      value.to_s.encode("UTF-8", invalid: :replace, undef: :replace).strip
    end

    def json_body(request)
      raise ValidationError, "JSON body required" if request.body.to_s.empty?
      raise ValidationError, "JSON body exceeds 1 MiB" if request.body.to_s.bytesize > 1_048_576
      JSON.parse(request.body)
    rescue JSON::ParserError => e
      raise ValidationError, "invalid JSON body: #{e.message}"
    end

    def require_method!(request, expected)
      return unless request.respond_to?(:request_method)
      actual = request.request_method.to_s.upcase
      raise MethodNotAllowedError, "#{expected} required" unless actual == expected
    end

    def csv(value)
      value.to_s.empty? ? nil : value.split(",")
    end
  end
end
