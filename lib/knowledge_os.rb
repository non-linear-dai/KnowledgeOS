# frozen_string_literal: true

require_relative "knowledge_os/errors"
require_relative "knowledge_os/config"
require_relative "knowledge_os/frontmatter"
require_relative "knowledge_os/registry"
require_relative "knowledge_os/database"
require_relative "knowledge_os/ledger"
require_relative "knowledge_os/validator"
require_relative "knowledge_os/compiler"
require_relative "knowledge_os/deterministic_engine"
require_relative "knowledge_os/connector"
require_relative "knowledge_os/service"
require_relative "knowledge_os/api"
require_relative "knowledge_os/cli"

module KnowledgeOS
  VERSION = "0.1.0"
end

