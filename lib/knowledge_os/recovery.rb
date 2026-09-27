# frozen_string_literal: true

require 'fileutils'
require 'digest'
require 'json'

module KnowledgeOS
  module RuntimeLease
    module_function

    def acquire(directory, exclusive: false)
      FileUtils.mkdir_p(directory)
      file = File.open(File.join(directory, '.state.lock'), 'a')
      unless file.flock((exclusive ? File::LOCK_EX : File::LOCK_SH) | File::LOCK_NB)
        file.close
        raise ConflictError, 'runtime is in use; stop services before backup or restore'
      end
      file
    end
  end

  class Recovery
    FILES = %w[knowledge.state.db knowledge.ledger.db].freeze

    def initialize(config)
      @config = config
    end

    def backup(destination)
      destination = Pathname.new(destination).expand_path
      raise ValidationError, 'backup destination already exists' if destination.exist?
      lease = RuntimeLease.acquire(@config.runtime_dir, exclusive: true)
      FILES.each { |name| raise NotFoundError, "missing durable file #{name}; initialize the service first" unless @config.runtime_dir.join(name).file? }
      destination.mkpath
      hashes = FILES.to_h do |name|
        source = SQLite3::Database.new(@config.runtime_dir.join(name).to_s)
        target = SQLite3::Database.new(destination.join(name).to_s)
        backup = SQLite3::Backup.new(target, 'main', source, 'main')
        begin
          status = backup.step(-1)
          raise IntegrityError, "backup failed for #{name}: #{status}" unless status == SQLite3::Constants::ErrorCode::DONE
        ensure
          backup.finish
          target.close
          source.close
        end
        [name, Digest::SHA256.file(destination.join(name)).hexdigest]
      end
      manifest = { 'format' => 'knowledgeos.backup.v1', 'files' => hashes, 'authored_fingerprint' => authored_fingerprint,
                   'created_at' => Time.now.utc.iso8601(6) }
      destination.join('manifest.json').write(JSON.pretty_generate(manifest))
      manifest
    ensure
      lease&.close
    end

    def restore(source)
      source = Pathname.new(source).expand_path
      manifest = JSON.parse(source.join('manifest.json').read)
      raise ValidationError, 'unsupported backup format' unless manifest['format'] == 'knowledgeos.backup.v1'
      raise ConflictError, 'restore requires the same authored knowledge/control checkout' unless manifest['authored_fingerprint'] == authored_fingerprint
      FILES.each do |name|
        raise IntegrityError, "backup checksum mismatch: #{name}" unless Digest::SHA256.file(source.join(name)).hexdigest == manifest.fetch('files').fetch(name)
      end
      lease = RuntimeLease.acquire(@config.runtime_dir, exclusive: true)
      unless @config.runtime_dir.glob('knowledge.*.db*').empty?
        raise ConflictError, 'restore requires an empty runtime; existing databases will not be overwritten'
      end
      FILES.each { |name| FileUtils.cp(source.join(name), @config.runtime_dir.join(name)) }
      { 'restored' => FILES, 'next_step' => 'rebuild the disposable index, then verify-ledger' }
    ensure
      lease&.close
    end

    private

    def authored_fingerprint
      paths = %w[control knowledge connectors].flat_map { |dir| @config.root.join(dir).glob('**/*').select(&:file?) }.sort
      Digest::SHA256.hexdigest(JSON.generate(paths.map { |path| [path.relative_path_from(@config.root).to_s, Digest::SHA256.file(path).hexdigest] }))
    end
  end
end
