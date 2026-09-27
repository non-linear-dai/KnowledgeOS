# frozen_string_literal: true

require "webrick"
require "json"

module KnowledgeOS
  class API
    def initialize(service:, bind: "127.0.0.1", port: 8787, server: nil)
      @service = service
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
        respond(response, 200, { "status" => "ok", "root" => @service.config.root.to_s, "nodes" => count })
      end
      @server.mount_proc("/v1/resolve") { |request, response| dispatch(response) { @service.resolve(param(request, "query"), scope: request.query["scope"]) } }
      @server.mount_proc("/v1/control") { |_request, response| dispatch(response) { @service.control_plane } }
      @server.mount_proc("/v1/studio") { |_request, response| dispatch(response) { @service.studio } }
      @server.mount_proc("/v1/changesets") do |request, response|
        dispatch(response) { @service.changesets(status: request.query["status"], limit: request.query.fetch("limit", 100)) }
      end
      @server.mount_proc("/v1/changesets/review") do |request, response|
        dispatch(response) do
          body = json_body(request)
          @service.review_changeset(id: body.fetch("id"), reviewer: body.fetch("reviewer"), decision: body.fetch("decision"), note: body["note"])
        end
      end
      @server.mount_proc("/v1/changesets/publish") do |request, response|
        dispatch(response) do
          body = json_body(request)
          @service.publish_changeset(id: body.fetch("id"), publisher: body.fetch("publisher"), source_revision: body.fetch("source_revision"))
        end
      end
      @server.mount_proc("/v1/get") { |request, response| dispatch(response) { @service.get(param(request, "id"), include_history: request.query["history"] == "true") } }
      @server.mount_proc("/v1/search") { |request, response| dispatch(response) { @service.search(param(request, "query"), filters: { "type" => request.query["type"] }.compact) } }
      @server.mount_proc("/v1/neighbors") do |request, response|
        dispatch(response) do
          @service.neighbors(param(request, "id"), relation_types: csv(request.query["relation_types"]),
                             depth: request.query.fetch("depth", 1), as_of: request.query["as_of"])
        end
      end
      @server.mount_proc("/v1/history") { |request, response| dispatch(response) { @service.history(param(request, "id"), predicate: request.query["predicate"]) } }
      @server.mount_proc("/v1/explain") { |request, response| dispatch(response) { @service.explain(param(request, "target"), field_or_assertion: request.query["item"]) } }
      @server.mount_proc("/v1/context") do |request, response|
        dispatch(response) { @service.context(param(request, "id"), domain: param(request, "domain"), as_of: request.query["as_of"]) }
      end
      @server.mount_proc("/v1/query") do |request, response|
        dispatch(response) do
          body = json_body(request)
          @service.query(body.fetch("template"), body.fetch("params", {}))
        end
      end
      @server.mount_proc("/v1/calculate") do |request, response|
        dispatch(response) do
          body = json_body(request)
          @service.calculate(body.fetch("model_id"), body.fetch("inputs"), scenario: body.fetch("scenario", "default"))
        end
      end
      @server.mount_proc("/v1/propose") do |request, response|
        dispatch(response, success: 201) do
          body = json_body(request)
          @service.propose(actor: body.fetch("actor"), target_source: body.fetch("target_source"),
                           patch: body.fetch("patch"), reason: body.fetch("reason"), risk: body.fetch("risk", "normal"),
                           title: body["title"], operations: body.fetch("operations", []))
        end
      end
      @server.mount_proc("/v1/review") { |request, response| dispatch(response) { @service.review(priority: request.query["priority"], limit: request.query.fetch("limit", 100)) } }
    end

    def dispatch(response, success: 200)
      respond(response, success, yield)
    rescue NotFoundError => e
      respond(response, 404, { "error" => e.message })
    rescue KeyError, ValidationError => e
      respond(response, 422, { "error" => e.message })
    rescue StandardError => e
      respond(response, 500, { "error" => e.message, "type" => e.class.name })
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
      JSON.parse(request.body)
    rescue JSON::ParserError => e
      raise ValidationError, "invalid JSON body: #{e.message}"
    end

    def csv(value)
      value.to_s.empty? ? nil : value.split(",")
    end
  end
end
