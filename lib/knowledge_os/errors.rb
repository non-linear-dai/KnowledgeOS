# frozen_string_literal: true

module KnowledgeOS
  class Error < StandardError; end
  class ValidationError < Error; end
  class NotFoundError < Error; end
  class IntegrityError < Error; end
  class ConfigurationError < Error; end
  class AuthenticationError < Error; end
  class AuthorizationError < Error; end
  class MethodNotAllowedError < Error; end
end
