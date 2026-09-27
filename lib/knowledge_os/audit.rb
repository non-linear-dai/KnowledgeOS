# frozen_string_literal: true

require "json"
require "securerandom"
require "time"

module KnowledgeOS
  class AuditCoordinator
    def initialize(database:, ledger:)
      @database = database
      @ledger = ledger
    end

    def stage(event_type:, actor:, target_id: nil, source_ref: nil, before_hash: nil,
              after_hash: nil, reason: nil, payload: {}, event_id: nil, timestamp: nil)
      event = {
        "event_id" => event_id || SecureRandom.uuid,
        "event_type" => event_type,
        "actor" => actor,
        "target_id" => target_id,
        "source_ref" => source_ref,
        "before_hash" => before_hash,
        "after_hash" => after_hash,
        "reason" => reason,
        "payload" => payload,
        "timestamp" => timestamp || Time.now.utc.iso8601(6)
      }
      @database.execute(
        "INSERT OR IGNORE INTO audit_outbox(event_id, event_json, status, created_at) VALUES (?, ?, 'pending', ?)",
        [event["event_id"], JSON.generate(event), event["timestamp"]]
      )
      event
    end

    def flush!
      delivered = 0
      @database.execute("SELECT event_id, event_json FROM audit_outbox WHERE status = 'pending' ORDER BY created_at, event_id").each do |row|
        event = JSON.parse(row["event_json"])
        ledger_event = @ledger.append(
          event_type: event.fetch("event_type"), actor: event.fetch("actor"), target_id: event["target_id"],
          source_ref: event["source_ref"], before_hash: event["before_hash"], after_hash: event["after_hash"],
          reason: event["reason"], payload: event.fetch("payload", {}), event_id: event.fetch("event_id"),
          timestamp: event.fetch("timestamp")
        )
        @database.transaction do
          mirror!(ledger_event)
          @database.execute(
            "UPDATE audit_outbox SET status = 'delivered', delivered_at = ?, last_error = NULL WHERE event_id = ?",
            [Time.now.utc.iso8601(6), event.fetch("event_id")]
          )
        end
        delivered += 1
      rescue StandardError => e
        @database.execute("UPDATE audit_outbox SET last_error = ? WHERE event_id = ?", [e.message, row["event_id"]])
        raise
      end
      reconcile!
      delivered
    end

    def reconcile!
      @ledger.connection.execute("SELECT event_id, event_type, target_id, sequence, timestamp FROM event ORDER BY sequence").each do |event|
        mirror!(event)
      end
      true
    end

    private

    def mirror!(event)
      @database.execute(
        "INSERT OR IGNORE INTO audit_event_ref(event_id, event_type, target_id, ledger_sequence, timestamp) VALUES (?, ?, ?, ?, ?)",
        [event["event_id"], event["event_type"], event["target_id"], event["sequence"], event["timestamp"]]
      )
    end
  end
end
