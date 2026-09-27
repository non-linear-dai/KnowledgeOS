# frozen_string_literal: true

require "digest"
require "json"
require "open3"
require "pathname"

module KnowledgeOS
  class PublicationVerifier
    GIT_ROOTS = %w[knowledge control connectors].freeze

    def initialize(config:, registry:, database:, ledger:)
      @config = config
      @registry = registry
      @database = database
      @ledger = ledger
    end

    def current_revision(target_source)
      paths = source_entries(target_source)
      return digest_paths(paths) unless paths.empty?
      source = upstream_source(target_source)
      source && (source["source_version"].to_s.empty? ? source["source_hash"] : source["source_version"])
    end

    def expectation(target_source, patch, operations)
      paths = source_entries(target_source)
      return { 'upstream_hash' => patch['expected_source_hash'] } if paths.empty? && patch.is_a?(Hash) && patch['expected_source_hash']
      ChangePlan.new(@config, @registry).prepare(paths, patch, operations)
    end

    def verify!(target_source:, source_revision:, base_revision: nil, expected: nil)
      paths = source_entries(target_source)
      unless paths.empty?
        actual = current_revision(target_source)
        unless secure_compare(actual, source_revision) || git_revision_matches?(source_revision, paths)
          raise ValidationError, "source_revision does not match the current Git-authored source"
        end
        if base_revision && secure_compare(base_revision, actual)
          raise ValidationError, "target source has not changed since the ChangeSet was proposed"
        end
        ChangePlan.new(@config, @registry).verify!(paths, expected)
        validate_and_compile!
        ChangePlan.new(@config, @registry).verify!(source_entries(target_source), expected)
        raise ConflictError, 'source changed during compilation' unless actual == current_revision(target_source)
        return { "kind" => "git_authored", "verified_revision" => source_revision,
                 "paths" => paths.map { |relative, _path| relative } }
      end

      source = upstream_source(target_source)
      raise ValidationError, "target_source cannot be verified: #{target_source}" unless source
      unless expected && expected['upstream_hash'] == source['source_hash']
        raise ConflictError, 'upstream result does not match approved content hash'
      end
      accepted = [source["source_version"], source["source_hash"]].compact.map(&:to_s)
      raise ValidationError, "source_revision does not match the authoritative source record" unless accepted.include?(source_revision.to_s)
      if base_revision && secure_compare(base_revision, source_revision)
        raise ValidationError, "authoritative source has not changed since the ChangeSet was proposed"
      end
      { "kind" => "upstream_source_system", "verified_revision" => source_revision,
        "source_ref" => source["id"] }
    end

    private

    def source_entries(target_source)
      values = target_source.to_s.split(",").map(&:strip).reject(&:empty?)
      return [] if values.empty?
      values.map do |value|
        relative = Pathname.new(value)
        return [] if relative.absolute? || !GIT_ROOTS.include?(relative.each_filename.first)
        return [] unless relative.cleanpath == relative
        path = @config.root.join(relative).cleanpath
        allowed_root = @config.root.join(relative.each_filename.first).realpath.to_s
        if path.exist?
          return [] unless path.file? && path.realpath.to_s.start_with?(allowed_root + File::SEPARATOR)
          [relative.to_s, path]
        else
          parent = path.parent
          return [] unless parent.exist? && (parent.realpath.to_s == allowed_root || parent.realpath.to_s.start_with?(allowed_root + File::SEPARATOR))
          [relative.to_s, nil]
        end
      end
    rescue Errno::ENOENT
      []
    end

    def upstream_source(target_source)
      @database.first("SELECT * FROM source_ref WHERE id = ? OR locator = ?", [target_source, target_source])
    end

    def git_revision_matches?(revision, paths)
      return false unless @config.root.join(".git").exist?
      _commit, commit_status = Open3.capture2e("git", "-C", @config.root.to_s, "rev-parse", "--verify", "#{revision}^{commit}")
      return false unless commit_status.success?
      paths.all? do |relative, current_path|
        stdout, status = Open3.capture2e("git", "-C", @config.root.to_s, "show", "#{revision}:#{relative}")
        if current_path
          status.success? && Digest::SHA256.hexdigest(stdout) == Digest::SHA256.file(current_path).hexdigest
        else
          !status.success?
        end
      end
    rescue Errno::ENOENT
      false
    end

    def digest_paths(paths)
      manifest = paths.sort.to_h do |relative, path|
        [relative, path ? Digest::SHA256.file(path).hexdigest : "__missing__"]
      end
      return "sha256:#{manifest.values.first}" if manifest.length == 1 && manifest.values.first != "__missing__"
      "sha256:#{Digest::SHA256.hexdigest(JSON.generate(manifest))}"
    end

    def validate_and_compile!
      compiler = Compiler.new(config: @config, registry: @registry, database: @database, ledger: @ledger)
      compiler.compile(rebuild: false)
    end

    def secure_compare(left, right)
      left = left.to_s
      right = right.to_s
      return false unless left.bytesize == right.bytesize
      left.bytes.zip(right.bytes).reduce(0) { |memo, pair| memo | (pair[0] ^ pair[1]) }.zero?
    end
  end
end
