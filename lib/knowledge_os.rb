# frozen_string_literal: true

require "json"

require_relative "knowledge_os/errors"
require_relative "knowledge_os/config"
require_relative "knowledge_os/frontmatter"
require_relative "knowledge_os/registry"
require_relative "knowledge_os/recovery"
require_relative "knowledge_os/database"
require_relative "knowledge_os/ledger"
require_relative "knowledge_os/audit"
require_relative "knowledge_os/access_control"
require_relative "knowledge_os/schema_validator"
require_relative "knowledge_os/constraint_engine"
require_relative "knowledge_os/policy_engine"
require_relative "knowledge_os/semantic_index"
require_relative "knowledge_os/temporal"
require_relative "knowledge_os/value_contract"
require_relative "knowledge_os/validator"
require_relative "knowledge_os/extraction"
require_relative "knowledge_os/compiler"
require_relative "knowledge_os/publication_verifier"
require_relative "knowledge_os/change_plan"
require_relative "knowledge_os/deterministic_engine"
require_relative "knowledge_os/connector"
require_relative "knowledge_os/agent_service"
require_relative "knowledge_os/service"
require_relative "knowledge_os/api"
require_relative "knowledge_os/cli"

module KnowledgeOS
  VERSION = "0.1.0"
end
