# frozen_string_literal: true

require "json"
require "digest"

module KnowledgeOS
  class SemanticIndex
    MODEL_ID = "knowledgeos-hash-embedding-v1"
    DIMENSIONS = 192

    def self.vector(text)
      values = Array.new(DIMENSIONS, 0.0)
      tokens(text).each do |token|
        digest = Digest::SHA256.digest(token)
        index = digest.byteslice(0, 4).unpack1("L>") % DIMENSIONS
        sign = digest.getbyte(4).even? ? 1.0 : -1.0
        values[index] += sign
      end
      norm = Math.sqrt(values.sum { |item| item * item })
      norm.zero? ? values : values.map { |item| item / norm }
    end

    def self.tokens(text)
      normalized = text.to_s.downcase
      words = normalized.scan(/[\p{L}\p{N}_:-]+/)
      han = normalized.scan(/\p{Han}+/).flat_map do |sequence|
        chars = sequence.chars
        chars + chars.each_cons(2).map(&:join)
      end
      (words + han).reject(&:empty?)
    end

    def self.cosine(left, right)
      left.zip(right).sum { |a, b| a.to_f * b.to_f }
    end

    def initialize(database:)
      @database = database
    end

    def index(node_id:, text:, source_hash:)
      @database.execute(
        <<~SQL,
          INSERT INTO node_embedding(node_id, model_id, dimensions, vector_json, source_hash)
          VALUES (?, ?, ?, ?, ?)
          ON CONFLICT(node_id) DO UPDATE SET model_id=excluded.model_id, dimensions=excluded.dimensions,
            vector_json=excluded.vector_json, source_hash=excluded.source_hash
        SQL
        [node_id, MODEL_ID, DIMENSIONS, JSON.generate(self.class.vector(text)), source_hash]
      )
    end

    def search(query, type: nil, limit: 50)
      query_vector = self.class.vector(query)
      sql = <<~SQL
        SELECT e.node_id, e.vector_json, n.id, n.type, n.label, n.lifecycle, n.source_class
        FROM node_embedding e JOIN node n ON n.id = e.node_id
      SQL
      binds = []
      if type
        sql += " WHERE n.type = ?"
        binds << type
      end
      @database.execute(sql, binds).map do |row|
        clean(row).merge("vector_score" => self.class.cosine(query_vector, JSON.parse(row["vector_json"])))
                  .reject { |key, _| key == "vector_json" || key == "node_id" }
      end.select { |row| row["vector_score"] > 0.05 }
         .sort_by { |row| -row["vector_score"] }.first(limit)
    end

    private

    def clean(row)
      row.each_with_object({}) { |(key, value), out| out[key] = value if key.is_a?(String) }
    end
  end
end
