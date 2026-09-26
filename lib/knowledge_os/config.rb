# frozen_string_literal: true

require "pathname"

module KnowledgeOS
  class Config
    attr_reader :root

    def initialize(root = ENV.fetch("KNOWLEDGEOS_ROOT", Dir.pwd))
      @root = Pathname.new(root).expand_path
    end

    def control_dir; root.join("control"); end
    def knowledge_dir; root.join("knowledge"); end
    def runtime_dir; root.join("runtime"); end
    def predicates_dir; control_dir.join("predicates"); end
    def ontology_dir; control_dir.join("ontology"); end
    def policies_dir; control_dir.join("policies"); end
    def models_dir; control_dir.join("models"); end
    def domains_dir; control_dir.join("domains"); end
    def index_path; runtime_dir.join("knowledge.index.db"); end
    def ledger_path; runtime_dir.join("knowledge.ledger.db"); end

    def ensure_runtime!
      runtime_dir.mkpath
      runtime_dir.join("cache").mkpath
      runtime_dir.join("indexes").mkpath
    end
  end
end
