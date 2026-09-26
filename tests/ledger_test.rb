# frozen_string_literal: true

require_relative "test_helper"

class LedgerTest < Minitest::Test
  include WorkspaceHelper

  def test_ledger_is_hash_chained_and_append_only
    Dir.mktmpdir("knowledgeos-ledger") do |dir|
      ledger = KnowledgeOS::Ledger.new(File.join(dir, "ledger.db"))
      ledger.append(event_type: "test", actor: "system", target_id: "node:1", payload: { "b" => 2, "a" => 1 })
      ledger.append(event_type: "test", actor: "system", target_id: "node:2")

      result = ledger.verify!
      assert result["valid"]
      assert_equal 2, result["events"]
      assert_raises(SQLite3::ConstraintException) { ledger.connection.execute("UPDATE event SET reason = 'tamper' WHERE sequence = 1") }
      assert_raises(SQLite3::ConstraintException) { ledger.connection.execute("DELETE FROM event WHERE sequence = 1") }
      ledger.close
    end
  end

  def test_duplicate_event_id_is_idempotent
    Dir.mktmpdir("knowledgeos-ledger") do |dir|
      ledger = KnowledgeOS::Ledger.new(File.join(dir, "ledger.db"))
      first = ledger.append(event_type: "compile", actor: "compiler", event_id: "stable-id")
      second = ledger.append(event_type: "compile", actor: "compiler", event_id: "stable-id")
      assert_equal first["event_hash"], second["event_hash"]
      assert_equal 1, ledger.verify!["events"]
      ledger.close
    end
  end
end

