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
      when "review" then with_service { |service| print_json(service.review(priority: option("--priority"), limit: option("--limit", 100).to_i)) }
      when "propose" then propose
      when "review-changeset" then review_changeset
      when "verify-ledger" then verify_ledger
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
      with_service { |service| print_json(service.context(id, domain: domain, as_of: option("--as-of"))) }
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
      print_json(connector.ingest_ndjson(input, mapping))
      0
    ensure
      service.close if service
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
      warn "KnowledgeOS API listening on http://#{bind}:#{port}"
      API.new(service: Service.new(config: config), bind: bind, port: port).start
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
          review [--priority P0]         List maintenance exceptions
          propose ...                    Create an Agent ChangeSet
          review-changeset ID ...        Record a governance decision
          verify-ledger                  Verify the immutable hash chain
          serve [--bind HOST --port N]   Start the local JSON API
      TEXT
    end
  end
end
