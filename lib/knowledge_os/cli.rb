# frozen_string_literal: true

require "optparse"
require "json"

module KnowledgeOS
  class CLI
    def self.run(argv)
      new(argv).run
    end

    def initialize(argv)
      @argv = argv.dup
      @root = Dir.pwd
      extract_root!
    end

    def run
      command = @argv.shift || "help"
      case command
      when "doctor" then doctor
      when "control" then with_service { |service| print_json(service.control_plane) }
      when "rebuild" then compile(true)
      when "compile" then compile(false)
      when "get" then with_service { |service| print_json(service.get(required_arg("id"), include_history: flag?("--history"))) }
      when "resolve" then with_service { |service| print_json(service.resolve(required_arg("query"))) }
      when "search" then with_service { |service| print_json(service.search(required_arg("query"))) }
      when "neighbors" then neighbors
      when "history" then with_service { |service| print_json(service.history(required_arg("id"))) }
      when "explain" then with_service { |service| print_json(service.explain(required_arg("target"))) }
      when "context" then context
      when "calculate" then calculate
      when "ingest" then ingest
      when "extract-contract" then extract_contract
      when "extract" then extract
      when "agent-capabilities" then agent_capabilities
      when "agent-skill" then agent_skill
      when "agent-request" then agent_request
      when "agent-respond" then agent_respond
      when "agent-invoke" then agent_invoke
      when "review" then with_service { |service| print_json(service.review(priority: option("--priority"), limit: option("--limit", 100).to_i)) }
      when "propose" then propose
      when "review-changeset" then review_changeset
      when "publish-changeset" then publish_changeset
      when "verify-ledger" then verify_ledger
      when 'backup' then print_json(Recovery.new(config).backup(required_arg('new backup directory'))); 0
      when 'restore' then print_json(Recovery.new(config).restore(required_arg('backup directory'))); 0
      when "serve" then serve
      when "version", "--version", "-v" then puts KnowledgeOS::VERSION
      when "help", "--help", "-h" then puts help
      else
        warn "unknown command: #{command}\n\n#{help}"
        2
      end
    rescue Error, KeyError, JSON::ParserError, OptionParser::ParseError => e
      warn "error: #{e.message}"
      1
    end

    private

    def config
      @config ||= Config.new(@root)
    end

    def with_service
      service = Service.new(config: config)
      yield service
      0
    ensure
      service.close if service
    end

    def compile(rebuild)
      compiler = Compiler.new(config: config)
      print_json(compiler.compile(rebuild: rebuild))
      0
    ensure
      compiler.close if compiler
    end

    def doctor
      checks = {
        "root" => config.root.to_s,
        "ruby" => RUBY_VERSION,
        "sqlite" => SQLite3::SQLITE_VERSION,
        "control_dir" => config.control_dir.directory?,
        "knowledge_dir" => config.knowledge_dir.directory?,
        "predicates" => Registry.new(config).predicates.length
      }
      healthy = checks.values_at("control_dir", "knowledge_dir").all?
      print_json(checks.merge("healthy" => healthy))
      healthy ? 0 : 1
    end

    def neighbors
      id = required_arg("id")
      depth = option("--depth", 1).to_i
      types = option("--relations")
      with_service { |service| print_json(service.neighbors(id, depth: depth, relation_types: types && types.split(","))) }
    end

    def context
      id = required_arg("id")
      domain = option("--domain") || raise(ValidationError, "--domain is required")
      with_service { |service| print_json(service.context(id, domain: domain, as_of: option("--as-of"), recorded_as_of: option('--recorded-as-of'))) }
    end

    def calculate
      model = required_arg("model id")
      inputs = JSON.parse(option("--inputs") || raise(ValidationError, "--inputs JSON is required"))
      scenario = option("--scenario", "default")
      with_service { |service| print_json(service.calculate(model, inputs, scenario: scenario)) }
    end

    def ingest
      input = option("--input") || raise(ValidationError, "--input is required")
      mapping = option("--mapping") || raise(ValidationError, "--mapping is required")
      service = Service.new(config: config)
      connector = Connector.new(config: config, registry: service.registry, database: service.database, ledger: service.ledger)
      print_json(connector.ingest_ndjson(input, mapping, isolate_errors: flag?('--isolate-errors')))
      0
    ensure
      service.close if service
    end

    def extract_contract
      source, profile, materializer = extraction_options
      with_service do |service|
        print_json(service.extraction_request(source: source, profile: profile, materializer: materializer))
      end
    end

    def extract
      model_command = option("--model-command")
      model_response = option("--model-response")
      unless [model_command, model_response].compact.length == 1
        raise ValidationError, "provide exactly one of --model-command or --model-response"
      end
      source, profile, materializer = extraction_options
      adapter = if model_command
                  CommandModelAdapter.new(model_command, timeout_seconds: option("--timeout", 120).to_i)
                else
                  StaticModelAdapter.new(JSON.parse(File.read(model_response)))
                end
      with_service do |service|
        print_json(service.extract_candidates(source: source, model_adapter: adapter,
                                              profile: profile, materializer: materializer))
      end
    rescue Errno::ENOENT, Errno::EACCES => e
      raise ValidationError, "cannot read model response: #{e.message}"
    end

    def extraction_options
      kind = option("--source-type") || raise(ValidationError, "--source-type is required")
      source_path = option("--source")
      locator = option("--locator") || source_path
      content_file = option("--content-file")
      content = content_file && File.read(content_file)
      materializer_command = option("--materializer-command")
      materializer = materializer_command && CommandMaterializer.new(
        materializer_command, timeout_seconds: option("--materializer-timeout", 120).to_i
      )
      source = { "kind" => kind, "locator" => locator, "content" => content,
                 "mime_type" => option("--mime-type"), "captured_at" => option("--captured-at"),
                 "metadata" => {} }.reject { |_, value| value.nil? }
      [source, option("--profile", "default"), materializer]
    rescue Errno::ENOENT, Errno::EACCES => e
      raise ValidationError, "cannot read source content: #{e.message}"
    end

    def agent_capabilities
      with_service { |service| print_json(service.agent_capabilities(domain: option("--domain"))) }
    end

    def agent_skill
      with_service { |service| print_json(service.agent_skill(id: required_arg("skill id"))) }
    end

    def agent_request
      question = required_arg("question")
      domain = option("--domain") || raise(ValidationError, "--domain is required")
      with_service do |service|
        print_json(service.agent_request(question: question, domain: domain, target: option("--target"),
                                         query: option("--query"), skill: option("--skill"),
                                         as_of: option("--as-of"), max_items: option("--max-items", 25).to_i))
      end
    end

    def agent_respond
      request_file = option("--request") || raise(ValidationError, "--request is required")
      response_file = option("--model-response") || raise(ValidationError, "--model-response is required")
      request = JSON.parse(File.read(request_file))
      request = request["data"] if request["data"].is_a?(Hash)
      model_output = JSON.parse(File.read(response_file))
      tool_results_file = option("--tool-results")
      tool_results = tool_results_file ? JSON.parse(File.read(tool_results_file)) : []
      with_service do |service|
        print_json(service.agent_respond(request: request, model_output: model_output, tool_results: tool_results))
      end
    rescue Errno::ENOENT, Errno::EACCES => e
      raise ValidationError, "cannot read agent JSON: #{e.message}"
    end

    def agent_invoke
      operation = required_arg("operation")
      domain = option("--domain") || raise(ValidationError, "--domain is required")
      arguments = JSON.parse(option("--arguments", "{}"))
      with_service { |service| print_json(service.agent_invoke(domain: domain, operation: operation, arguments: arguments)) }
    end

    def propose
      actor = option("--actor") || raise(ValidationError, "--actor is required")
      source = option("--target-source") || raise(ValidationError, "--target-source is required")
      patch = JSON.parse(option("--patch") || raise(ValidationError, "--patch JSON is required"))
      reason = option("--reason") || raise(ValidationError, "--reason is required")
      risk = option("--risk", "normal")
      with_service { |service| print_json(service.propose(actor: actor, target_source: source, patch: patch, reason: reason, risk: risk)) }
    end

    def review_changeset
      id = required_arg("changeset id")
      reviewer = option("--reviewer") || raise(ValidationError, "--reviewer is required")
      decision = option("--decision") || raise(ValidationError, "--decision is required")
      with_service { |service| print_json(service.review_changeset(id: id, reviewer: reviewer, decision: decision)) }
    end

    def publish_changeset
      id = required_arg("changeset id")
      publisher = option("--publisher") || raise(ValidationError, "--publisher is required")
      source_revision = option("--source-revision") || raise(ValidationError, "--source-revision is required")
      with_service { |service| print_json(service.publish_changeset(id: id, publisher: publisher, source_revision: source_revision)) }
    end

    def verify_ledger
      ledger = Ledger.new(config.ledger_path)
      print_json(ledger.verify!)
      0
    ensure
      ledger.close if ledger
    end

    def serve
      bind = option("--bind", "127.0.0.1")
      port = option("--port", 8787).to_i
      auth = AccessControl.from_env
      warn "KnowledgeOS API listening on http://#{bind}:#{port}"
      API.new(service: Service.new(config: config), bind: bind, port: port, auth: auth).start
      0
    end

    def extract_root!
      index = @argv.index("--root")
      return unless index
      @root = @argv.fetch(index + 1)
      @argv.slice!(index, 2)
    end

    def option(name, default = nil)
      index = @argv.index(name)
      return default unless index
      value = @argv[index + 1]
      raise ValidationError, "#{name} requires a value" unless value
      @argv.slice!(index, 2)
      value
    end

    def flag?(name)
      index = @argv.index(name)
      return false unless index
      @argv.delete_at(index)
      true
    end

    def required_arg(label)
      value = @argv.shift
      raise ValidationError, "missing #{label}" if value.to_s.empty?
      value
    end

    def print_json(value)
      puts JSON.pretty_generate(value)
    end

    def help
      <<~TEXT
        Usage: bin/knowledgeos [--root PATH] COMMAND [options]

        Commands:
          doctor                         Validate the local runtime
          control                        Read the complete control-plane contract
          rebuild                        Rebuild the disposable SQLite index
          compile                        Incrementally compile changed knowledge
          get ID [--history]             Read an Entity Card
          resolve QUERY                  Resolve a concept or entity
          search QUERY                   Search authored semantic content
          neighbors ID [--depth N]       Traverse relations
          history ID                     Read assertion and ledger history
          explain TARGET                 Explain source and trace
          context ID --domain DOMAIN     Build a C-R-L-T-P Context Pack
          calculate MODEL --inputs JSON  Run a deterministic model
          ingest --input FILE --mapping FILE
          extract-contract --source-type TYPE [--source FILE|--content-file FILE]
                                         Build a provider-neutral, ontology-derived extraction request
          extract --source-type TYPE ... (--model-command CMD|--model-response FILE)
                                         Generate validated create/update candidates without writing
          agent-capabilities [--domain D] Discover domain skills, governed tools, and response schema
          agent-skill ID                   Read a portable SKILL.md + contract.yaml bundle
          agent-request QUESTION --domain D [--target ID] [--skill ID]
                                         Build a grounded, provider-neutral reasoning request
          agent-respond --request FILE --model-response FILE [--tool-results FILE]
                                         Validate citations and normalize model output
          agent-invoke OP --domain D --arguments JSON
                                         Invoke one domain-allowlisted KnowledgeOS tool
          review [--priority P0]         List maintenance exceptions
          propose ...                    Create an Agent ChangeSet
          review-changeset ID ...        Record a governance decision
          publish-changeset ID ...       Record source publication after approval
          verify-ledger                  Verify the immutable hash chain
          backup NEW_DIRECTORY           Back up durable state and ledger (services stopped)
          restore BACKUP_DIRECTORY       Restore into an empty runtime, then run rebuild
          serve [--bind HOST --port N]   Start the authenticated JSON API (requires auth env)
      TEXT
    end
  end
end
