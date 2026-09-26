# frozen_string_literal: true

require "sqlite3"
require "json"
require "digest"
require "securerandom"
require "time"
require "fileutils"

module KnowledgeOS
  class Ledger
    GENESIS_HASH = "0" * 64

    attr_reader :connection

    def initialize(path)
      FileUtils.mkdir_p(File.dirname(path.to_s))
      @connection = SQLite3::Database.new(path.to_s)
      @connection.results_as_hash = true
      @connection.busy_timeout = 5_000
      migrate!
    end

    def close
      connection.close
    end

    def append(event_type:, actor:, target_id: nil, source_ref: nil, before_hash: nil,
               after_hash: nil, reason: nil, payload: {}, event_id: nil, timestamp: nil)
      timestamp ||= Time.now.utc.iso8601(6)
      event_id ||= SecureRandom.uuid
      existing = connection.get_first_row("SELECT * FROM event WHERE event_id = ?", event_id)
      return clean(existing) if existing

      connection.transaction(:immediate) do
        previous = connection.get_first_row("SELECT event_hash FROM event ORDER BY sequence DESC LIMIT 1")
        prev_hash = previous ? previous["event_hash"] : GENESIS_HASH
        canonical = {
          "event_id" => event_id,
          "event_type" => event_type,
          "actor" => actor,
          "target_id" => target_id,
          "source_ref" => source_ref,
          "before_hash" => before_hash,
          "after_hash" => after_hash,
          "reason" => reason,
          "payload" => canonicalize(payload),
          "timestamp" => timestamp
        }
        event_hash = Digest::SHA256.hexdigest(prev_hash + JSON.generate(canonicalize(canonical)))
        connection.execute(
          <<~SQL,
            INSERT INTO event (
              event_id, event_type, actor, target_id, source_ref, before_hash,
              after_hash, reason, payload_json, timestamp, prev_hash, event_hash
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
          SQL
          [event_id, event_type, actor, target_id, source_ref, before_hash, after_hash,
           reason, JSON.generate(canonical["payload"]), timestamp, prev_hash, event_hash]
        )
      end
      clean(connection.get_first_row("SELECT * FROM event WHERE event_id = ?", event_id))
    end

    def verify!
      previous_hash = GENESIS_HASH
      count = 0
      connection.execute("SELECT * FROM event ORDER BY sequence ASC").each do |row|
        count += 1
        canonical = {
          "event_id" => row["event_id"],
          "event_type" => row["event_type"],
          "actor" => row["actor"],
          "target_id" => row["target_id"],
          "source_ref" => row["source_ref"],
          "before_hash" => row["before_hash"],
          "after_hash" => row["after_hash"],
          "reason" => row["reason"],
          "payload" => JSON.parse(row["payload_json"]),
          "timestamp" => row["timestamp"]
        }
        expected = Digest::SHA256.hexdigest(previous_hash + JSON.generate(canonicalize(canonical)))
        unless row["prev_hash"] == previous_hash && row["event_hash"] == expected
          raise IntegrityError, "ledger hash mismatch at sequence #{row['sequence']}"
        end
        previous_hash = row["event_hash"]
      end
      { "valid" => true, "events" => count, "head_hash" => previous_hash }
    end

    def events(target_id: nil, event_type: nil, limit: 100)
      clauses = []
      binds = []
      if target_id
        clauses << "target_id = ?"
        binds << target_id
      end
      if event_type
        clauses << "event_type = ?"
        binds << event_type
      end
      sql = "SELECT * FROM event"
      sql += " WHERE #{clauses.join(' AND ')}" unless clauses.empty?
      sql += " ORDER BY sequence DESC LIMIT ?"
      binds << limit.to_i
      connection.execute(sql, binds).map { |row| clean(row) }
    end

    private

    def migrate!
      connection.execute_batch <<~SQL
        PRAGMA journal_mode = WAL;
        CREATE TABLE IF NOT EXISTS event (
          sequence INTEGER PRIMARY KEY AUTOINCREMENT,
          event_id TEXT NOT NULL UNIQUE,
          event_type TEXT NOT NULL,
          actor TEXT NOT NULL,
          target_id TEXT,
          source_ref TEXT,
          before_hash TEXT,
          after_hash TEXT,
          reason TEXT,
          payload_json TEXT NOT NULL DEFAULT '{}',
          timestamp TEXT NOT NULL,
          prev_hash TEXT NOT NULL,
          event_hash TEXT NOT NULL UNIQUE
        );
        CREATE INDEX IF NOT EXISTS idx_event_target ON event(target_id, sequence DESC);
        CREATE INDEX IF NOT EXISTS idx_event_type ON event(event_type, sequence DESC);
        CREATE TRIGGER IF NOT EXISTS event_no_update
          BEFORE UPDATE ON event BEGIN SELECT RAISE(ABORT, 'ledger is append-only'); END;
        CREATE TRIGGER IF NOT EXISTS event_no_delete
          BEFORE DELETE ON event BEGIN SELECT RAISE(ABORT, 'ledger is append-only'); END;
      SQL
    end

    def canonicalize(value)
      case value
      when Hash
        value.keys.map(&:to_s).sort.each_with_object({}) do |key, out|
          original_key = value.key?(key) ? key : value.keys.find { |candidate| candidate.to_s == key }
          out[key] = canonicalize(value[original_key])
        end
      when Array
        value.map { |item| canonicalize(item) }
      else
        value
      end
    end

    def clean(row)
      return nil unless row

      row.each_with_object({}) do |(key, value), out|
        next unless key.is_a?(String)
        out[key] = key == "payload_json" ? JSON.parse(value) : value
      end
    end
  end
end
