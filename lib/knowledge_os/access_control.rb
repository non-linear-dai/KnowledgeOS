# frozen_string_literal: true

require "json"
require "digest"

module KnowledgeOS
  class AccessControl
    Principal = Struct.new(:id, :roles, keyword_init: true)

    ROLE_PERMISSIONS = {
      "reader" => %w[read],
      "agent" => %w[read agent extract propose],
      "reviewer" => %w[read review],
      "publisher" => %w[read publish],
      "admin" => %w[read agent extract propose review publish]
    }.freeze

    def self.from_env(env = ENV)
      mode = env.fetch("KNOWLEDGEOS_AUTH_MODE", "required")
      return disabled if mode == "disabled"
      raise ConfigurationError, "KNOWLEDGEOS_AUTH_MODE must be required or disabled" unless mode == "required"

      raw = env["KNOWLEDGEOS_AUTH_TOKENS"].to_s
      raise ConfigurationError, "KNOWLEDGEOS_AUTH_TOKENS is required when authentication is enabled" if raw.empty?
      parsed = JSON.parse(raw)
      raise ConfigurationError, "KNOWLEDGEOS_AUTH_TOKENS must be a JSON object" unless parsed.is_a?(Hash)
      entries = parsed.map do |token, definition|
        definition = { "principal" => definition, "roles" => ["reader"] } if definition.is_a?(String)
        raise ConfigurationError, "authentication token definition must be an object" unless definition.is_a?(Hash)
        principal = definition["principal"].to_s.strip
        roles = Array(definition["roles"]).map(&:to_s)
        raise ConfigurationError, "authentication principal is required" if principal.empty?
        unknown = roles - ROLE_PERMISSIONS.keys
        raise ConfigurationError, "unknown authentication roles: #{unknown.join(', ')}" unless unknown.empty?
        [Digest::SHA256.hexdigest(token.to_s), Principal.new(id: principal, roles: roles.freeze)]
      end.to_h
      raise ConfigurationError, "at least one authentication token is required" if entries.empty?
      new(entries: entries, enabled: true)
    rescue JSON::ParserError => e
      raise ConfigurationError, "invalid KNOWLEDGEOS_AUTH_TOKENS JSON: #{e.message}"
    end

    def self.disabled
      new(entries: {}, enabled: false)
    end

    attr_reader :enabled

    def initialize(entries:, enabled:)
      @entries = entries
      @enabled = enabled
    end

    def authenticate(request)
      return Principal.new(id: "local:anonymous", roles: ["admin"]) unless enabled

      value = authorization_header(request)
      scheme, token = value.to_s.split(/\s+/, 2)
      raise AuthenticationError, "Bearer token required" unless scheme&.casecmp("Bearer")&.zero? && !token.to_s.empty?
      principal = @entries[Digest::SHA256.hexdigest(token)]
      raise AuthenticationError, "invalid Bearer token" unless principal
      principal
    end

    def authorize!(principal, permission)
      permissions = principal.roles.flat_map { |role| ROLE_PERMISSIONS.fetch(role, []) }.uniq
      raise AuthorizationError, "principal #{principal.id} is not allowed to #{permission}" unless permissions.include?(permission.to_s)
      true
    end

    private

    def authorization_header(request)
      if request.respond_to?(:header)
        Array(request.header["authorization"]).first
      elsif request.respond_to?(:[])
        request["Authorization"]
      end
    end
  end
end
