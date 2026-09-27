# frozen_string_literal: true

require "sqlite3"
require "json"
require "fileutils"
require "monitor"

module KnowledgeOS
  class Database
    attr_reader :connection, :fts_enabled

    def initialize(path)
      @lock = Monitor.new
      @savepoint_sequence = 0
      FileUtils.mkdir_p(File.dirname(path.to_s))
      @lease = RuntimeLease.acquire(File.dirname(path.to_s))
      @connection = SQLite3::Database.new(path.to_s)
      @connection.results_as_hash = true
      @connection.busy_timeout = 5_000
      @connection.execute("PRAGMA foreign_keys = ON")
      # Attached durable state and projections commit atomically via SQLite's
      # super-journal. WAL does not guarantee atomic commits across databases.
      @connection.execute("PRAGMA journal_mode = DELETE")
      @connection.execute("PRAGMA synchronous = FULL")
      @connection.execute("ATTACH DATABASE ? AS durable", [File.join(File.dirname(path.to_s), "knowledge.state.db")])
      @connection.execute("PRAGMA durable.journal_mode = DELETE")
      @connection.execute("PRAGMA durable.synchronous = FULL")
      migrate!
    end

    def close
      connection.close
      @lease.close
    end

    def synchronize(&block)
      @lock.synchronize(&block)
    end

    def transaction
      synchronize do
        if connection.transaction_active?
          @savepoint_sequence = (@savepoint_sequence || 0) + 1
          name = "nested_#{@savepoint_sequence}"
          connection.execute("SAVEPOINT #{name}")
          begin
            result = yield
            connection.execute("RELEASE SAVEPOINT #{name}")
            result
          rescue Exception
            connection.execute("ROLLBACK TO SAVEPOINT #{name}")
            connection.execute("RELEASE SAVEPOINT #{name}")
            raise
          end
        else
          connection.transaction(:immediate) { yield }
        end
      end
    end

    def execute(sql, binds = [])
      synchronize { connection.execute(sql, binds) }
    end

    def first(sql, binds = [])
      synchronize { connection.get_first_row(sql, binds) }
    end

    def reset_projection!
      transaction do
        %w[assertion edge source_ref audit_event_ref entity_card attribute_candidate node_embedding node].each do |table|
          execute("DELETE FROM #{table}")
        end
        execute("DELETE FROM node_fts") if fts_enabled
        execute("DELETE FROM metadata WHERE key LIKE 'source:%'")
      end
    end

    def json(value)
      JSON.generate(value)
    end

    private

    def migrate!
      connection.execute_batch <<~SQL
        CREATE TABLE IF NOT EXISTS metadata (
          key TEXT PRIMARY KEY,
          value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS node (
          id TEXT PRIMARY KEY,
          kind TEXT NOT NULL,
          type TEXT NOT NULL,
          natural_key TEXT,
          label TEXT NOT NULL,
          aliases_json TEXT NOT NULL DEFAULT '[]',
          tags_json TEXT NOT NULL DEFAULT '[]',
          attrs_json TEXT NOT NULL DEFAULT '{}',
          lifecycle TEXT NOT NULL,
          revision INTEGER NOT NULL DEFAULT 1,
          source_class TEXT NOT NULL,
          source_path TEXT,
          source_hash TEXT NOT NULL,
          narrative TEXT NOT NULL DEFAULT '',
          compiled_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_node_type ON node(type);

        CREATE TABLE IF NOT EXISTS assertion (
          id TEXT PRIMARY KEY,
          node_id TEXT NOT NULL,
          predicate TEXT NOT NULL,
          value_json TEXT NOT NULL,
          qualifiers_json TEXT NOT NULL DEFAULT '{}',
          observed_at TEXT,
          valid_from TEXT,
          valid_to TEXT,
          assertion_kind TEXT NOT NULL,
          status TEXT NOT NULL,
          confidence REAL,
          evidence_refs_json TEXT NOT NULL DEFAULT '[]',
          source_refs_json TEXT NOT NULL DEFAULT '[]',
          supersedes TEXT,
          provenance_tier TEXT NOT NULL,
          temperature TEXT NOT NULL,
          source_path TEXT,
          source_hash TEXT NOT NULL,
          FOREIGN KEY(node_id) REFERENCES node(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_assertion_node_predicate ON assertion(node_id, predicate);
        CREATE INDEX IF NOT EXISTS idx_assertion_temperature ON assertion(temperature, status);

        CREATE TABLE IF NOT EXISTS edge (
          src TEXT NOT NULL,
          predicate TEXT NOT NULL,
          dst TEXT NOT NULL,
          rel_id TEXT NOT NULL DEFAULT '',
          valid_from TEXT NOT NULL DEFAULT '',
          valid_to TEXT NOT NULL DEFAULT '',
          weight REAL,
          source_path TEXT,
          PRIMARY KEY(src, predicate, dst, rel_id, valid_from)
        );
        CREATE INDEX IF NOT EXISTS idx_edge_dst ON edge(dst, predicate);

        CREATE TABLE IF NOT EXISTS source_ref (
          id TEXT PRIMARY KEY,
          source_system TEXT NOT NULL,
          source_object TEXT,
          source_record_id TEXT,
          source_timestamp TEXT,
          source_version TEXT,
          source_hash TEXT,
          authority_class TEXT,
          locator TEXT,
          captured_at TEXT NOT NULL,
          metadata_json TEXT NOT NULL DEFAULT '{}'
        );

        CREATE TABLE IF NOT EXISTS attribute_candidate (
          node_id TEXT NOT NULL,
          predicate TEXT NOT NULL,
          source_ref_id TEXT NOT NULL,
          source_class TEXT NOT NULL,
          value_json TEXT NOT NULL,
          observed_at TEXT,
          source_hash TEXT NOT NULL,
          PRIMARY KEY(node_id, predicate, source_ref_id),
          FOREIGN KEY(node_id) REFERENCES node(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS derived_result (
          id TEXT PRIMARY KEY,
          model_id TEXT NOT NULL,
          model_version TEXT NOT NULL,
          input_hash TEXT NOT NULL,
          scenario TEXT NOT NULL DEFAULT 'default',
          output_json TEXT NOT NULL,
          trace_json TEXT NOT NULL,
          run_id TEXT NOT NULL,
          calculated_at TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'current',
          UNIQUE(model_id, model_version, input_hash, scenario)
        );

        CREATE TABLE IF NOT EXISTS audit_event_ref (
          event_id TEXT PRIMARY KEY,
          event_type TEXT NOT NULL,
          target_id TEXT,
          ledger_sequence INTEGER NOT NULL,
          timestamp TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS audit_outbox (
          event_id TEXT PRIMARY KEY,
          event_json TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'pending',
          created_at TEXT NOT NULL,
          delivered_at TEXT,
          last_error TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_audit_outbox_status ON audit_outbox(status, created_at);

        CREATE TABLE IF NOT EXISTS node_embedding (
          node_id TEXT PRIMARY KEY,
          model_id TEXT NOT NULL,
          dimensions INTEGER NOT NULL,
          vector_json TEXT NOT NULL,
          source_hash TEXT NOT NULL,
          FOREIGN KEY(node_id) REFERENCES node(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS entity_card (
          node_id TEXT PRIMARY KEY,
          card_json TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          FOREIGN KEY(node_id) REFERENCES node(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS review_item (
          id TEXT PRIMARY KEY,
          kind TEXT NOT NULL,
          target_id TEXT,
          severity INTEGER NOT NULL,
          priority TEXT NOT NULL,
          reason TEXT NOT NULL,
          details_json TEXT NOT NULL DEFAULT '{}',
          status TEXT NOT NULL DEFAULT 'open',
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS changeset (
          id TEXT PRIMARY KEY,
          actor TEXT NOT NULL,
          title TEXT,
          target_source TEXT NOT NULL,
          risk TEXT NOT NULL,
          status TEXT NOT NULL,
          patch_json TEXT NOT NULL,
          operations_json TEXT NOT NULL DEFAULT '[]',
          reason TEXT NOT NULL,
          created_at TEXT NOT NULL,
          reviewed_by TEXT,
          reviewed_at TEXT,
          review_note TEXT,
          published_by TEXT,
          published_at TEXT,
          source_revision TEXT
        );
      SQL

      ensure_column!("changeset", "title", "TEXT")
      ensure_column!("changeset", "operations_json", "TEXT NOT NULL DEFAULT '[]'")
      ensure_column!("changeset", "review_note", "TEXT")
      ensure_column!("changeset", "published_by", "TEXT")
      ensure_column!("changeset", "published_at", "TEXT")
      ensure_column!("changeset", "source_revision", "TEXT")
      ensure_column!("changeset", "base_revision", "TEXT")
      ensure_column!("changeset", "publication_json", "TEXT")
      ensure_column!("changeset", "expected_json", "TEXT")
      ensure_column!("changeset", "lock_version", "INTEGER NOT NULL DEFAULT 0")
      ensure_column!("changeset", "request_key", "TEXT")
      ensure_column!("node", "key_namespace", "TEXT NOT NULL DEFAULT ''")
      connection.execute("DROP INDEX IF EXISTS idx_node_key")
      connection.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_node_scoped_key ON node(key_namespace, type, natural_key) WHERE natural_key IS NOT NULL")
      migrate_durable_state!

      begin
        connection.execute <<~SQL
          CREATE VIRTUAL TABLE IF NOT EXISTS node_fts USING fts5(
            node_id UNINDEXED,
            label,
            body,
            tokenize='unicode61'
          )
        SQL
        @fts_enabled = true
      rescue SQLite3::SQLException
        @fts_enabled = false
      end
    end

    def ensure_column!(table, column, definition)
      columns = connection.execute("PRAGMA table_info(#{table})").map { |row| row["name"] }
      connection.execute("ALTER TABLE #{table} ADD COLUMN #{column} #{definition}") unless columns.include?(column)
    end

    def migrate_durable_state!
      transaction do
        %w[changeset audit_outbox derived_result review_item].each do |table|
          ddl = connection.get_first_value("SELECT sql FROM main.sqlite_master WHERE type='table' AND name=?", [table])
          connection.execute(ddl.sub(/CREATE TABLE\s+#{table}/i, "CREATE TABLE IF NOT EXISTS durable.#{table}"))
          # Upgrade existing state stores using the canonical main table shape.
          main_columns = connection.execute("PRAGMA main.table_info(#{table})")
          state_columns = connection.execute("PRAGMA durable.table_info(#{table})").map { |row| row['name'] }
          main_columns.each do |column|
            next if state_columns.include?(column['name'])
            definition = column['type']
            definition += " DEFAULT #{column['dflt_value']}" if column['dflt_value']
            connection.execute("ALTER TABLE durable.#{table} ADD COLUMN #{column['name']} #{definition}")
          end
          names = main_columns.map { |row| row['name'] }.join(',')
          connection.execute("INSERT OR IGNORE INTO durable.#{table}(#{names}) SELECT #{names} FROM main.#{table}")
          connection.execute("DROP TABLE main.#{table}")
        end
        connection.execute_batch <<~SQL
          CREATE UNIQUE INDEX IF NOT EXISTS durable.idx_changeset_request ON changeset(actor, request_key) WHERE request_key IS NOT NULL;
          CREATE INDEX IF NOT EXISTS durable.idx_outbox_pending ON audit_outbox(status, created_at);
          CREATE TABLE IF NOT EXISTS durable.schema_migration(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL);
          INSERT OR IGNORE INTO durable.schema_migration VALUES(1, strftime('%Y-%m-%dT%H:%M:%fZ','now'));
          CREATE TABLE IF NOT EXISTS durable.connector_record(
            source_ref_id TEXT NOT NULL, source_hash TEXT NOT NULL, version TEXT,
            observed_at TEXT NOT NULL, record_json TEXT NOT NULL, mapping_json TEXT NOT NULL,
            PRIMARY KEY(source_ref_id, source_hash)
          );
          CREATE TABLE IF NOT EXISTS durable.entity_snapshot(
            node_id TEXT NOT NULL, recorded_at TEXT NOT NULL, content_hash TEXT NOT NULL,
            snapshot_json TEXT NOT NULL, PRIMARY KEY(node_id, recorded_at)
          );
        SQL
      end
    end
  end
end
