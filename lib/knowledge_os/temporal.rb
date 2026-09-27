# frozen_string_literal: true

require "time"

module KnowledgeOS
  module Temporal
    module_function

    def instant(value)
      raise ValidationError, 'timestamps require an explicit UTC offset' unless value.to_s.match?(/(?:Z|[+-]\d{2}:\d{2})\z/)
      Time.iso8601(value.to_s).utc.iso8601(6)
    rescue ArgumentError
      raise ValidationError, "invalid timestamp: #{value}"
    end

    def normalize(value)
      result = value.to_h.transform_values { |time| time.nil? || time.to_s.empty? ? nil : instant(time) }
      if result['valid_from'] && result['valid_to'] && result['valid_from'] >= result['valid_to']
        raise ValidationError, "valid_to must be after valid_from"
      end
      result
    end
  end

  class SnapshotStore
    def initialize(database)
      @database = database
    end

    def record(id, card)
      data = card.merge('assertion_history' => @database.execute('SELECT * FROM assertion WHERE node_id = ? ORDER BY id', [id]),
                        'edge_history' => @database.execute('SELECT * FROM edge WHERE src = ? ORDER BY predicate,dst,rel_id,valid_from', [id]))
      json = JSON.generate(data)
      hash = Digest::SHA256.hexdigest(json)
      previous = @database.first('SELECT content_hash FROM entity_snapshot WHERE node_id = ? ORDER BY recorded_at DESC LIMIT 1', [id])
      return if previous && previous['content_hash'] == hash
      @database.execute('INSERT INTO entity_snapshot(node_id,recorded_at,content_hash,snapshot_json) VALUES(?,?,?,?)',
                        [id, Time.now.utc.iso8601(6), hash, json])
    end

    def at(id, time)
      row = @database.first('SELECT snapshot_json,recorded_at FROM entity_snapshot WHERE node_id = ? AND recorded_at <= ? ORDER BY recorded_at DESC LIMIT 1', [id, Temporal.instant(time)])
      data = row && JSON.parse(row['snapshot_json']).merge('recorded_at' => row['recorded_at'])
      data && !data['deleted'] ? data : nil
    end

    def all_at(time)
      rows = @database.execute(<<~SQL, [Temporal.instant(time)])
        SELECT s.snapshot_json FROM entity_snapshot s
        JOIN (SELECT node_id, MAX(recorded_at) AS at FROM entity_snapshot WHERE recorded_at<=? GROUP BY node_id) latest
          ON s.node_id=latest.node_id AND s.recorded_at=latest.at
      SQL
      rows.map { |row| JSON.parse(row['snapshot_json']) }.reject { |item| item['deleted'] }
    end
  end
end
