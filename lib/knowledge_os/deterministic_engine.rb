# frozen_string_literal: true

require "bigdecimal"
require "bigdecimal/util"
require "json"
require "digest"
require "securerandom"
require "time"

module KnowledgeOS
  class DeterministicEngine
    def initialize(config:, database:, ledger:)
      @config = config
      @database = database
      @ledger = ledger
      @audit = AuditCoordinator.new(database: database, ledger: ledger)
    end

    def calculate(model_id, inputs, scenario: "default")
      model = load_model(model_id)
      validate_inputs!(model, inputs)
      normalized = normalize_inputs(model, inputs)
      currencies = inputs.values.select { |v| v.is_a?(Hash) }.map { |v| v['currency'] || v['unit'].to_s[/\A([A-Z]{3})_/, 1] }.compact.uniq
      raise ValidationError, 'mixed currencies require an explicit exchange-rate model' if currencies.length > 1
      if !currencies.empty? && model.fetch('inputs').any? { |input| input['unit'].to_s.start_with?('currency_') && !(inputs[input['id']] || inputs[input['id'].to_sym]).is_a?(Hash) }
        raise ValidationError, 'all monetary inputs must declare currency when using typed currency values'
      end
      input_hash = Digest::SHA256.hexdigest(JSON.generate([normalized.transform_values(&:to_s), currencies, model]))
      existing = @database.first(
        "SELECT * FROM derived_result WHERE model_id = ? AND model_version = ? AND input_hash = ? AND scenario = ?",
        [model_id, model.fetch("version").to_s, input_hash, scenario]
      )
      return decode(existing) if existing

      trace = []
      output = evaluate(model.fetch("formula"), normalized, trace)
      precision = Integer(model.fetch("precision", 2))
      rounded = output.round(precision, BigDecimal::ROUND_HALF_UP)
      now = Time.now.utc.iso8601(6)
      run_id = SecureRandom.uuid
      result = { "value" => rounded.to_s("F"), "unit" => model["output_unit"], "precision" => precision }
      result['currency'] = currencies.first unless currencies.empty?
      result["classification"] = classify(model, rounded) if model["bands"]
      id = Digest::SHA256.hexdigest([model_id, model["version"], input_hash, scenario].join(":"))
      @database.transaction do
        @database.execute(
          <<~SQL,
            INSERT INTO derived_result(id, model_id, model_version, input_hash, scenario, output_json, trace_json, run_id, calculated_at, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'current')
          SQL
          [id, model_id, model["version"].to_s, input_hash, scenario, JSON.generate(result), JSON.generate(trace), run_id, now]
        )
        @audit.stage(
          event_type: "model_run", actor: "rule", target_id: id,
          after_hash: Digest::SHA256.hexdigest(JSON.generate(result)), reason: "deterministic model execution",
          payload: { "model_id" => model_id, "version" => model["version"].to_s, "input_hash" => input_hash,
                     "scenario" => scenario, "run_id" => run_id, "result" => result, "trace" => trace }
        )
      end
      @audit.flush!
      decode(@database.first("SELECT * FROM derived_result WHERE id = ?", id))
    end

    private

    def load_model(id)
      path = @config.models_dir.join("#{id}.yaml")
      raise NotFoundError, "model not found: #{id}" unless path.file?
      model = Frontmatter.load_yaml(path)
      raise ConfigurationError, "#{path}: id mismatch" unless model["id"] == id
      model
    end

    def validate_inputs!(model, inputs)
      Array(model["inputs"]).each do |input|
        id = input.fetch("id")
        raise ValidationError, "missing model input: #{id}" unless inputs.key?(id) || inputs.key?(id.to_sym)
        raw = inputs.key?(id) ? inputs[id] : inputs[id.to_sym]
        input["type"] == "date" ? Time.iso8601(raw.to_s) : ValueContract.model_number(raw, input['unit'])
      rescue ArgumentError
        raise ValidationError, "invalid #{input['type'] || 'number'} model input: #{id}"
      end
    end

    def normalize_inputs(model, inputs)
      Array(model["inputs"]).sort_by { |input| input.fetch("id") }.each_with_object({}) do |input, output|
        id = input.fetch("id")
        raw = inputs.key?(id) ? inputs[id] : inputs[id.to_sym]
        output[id] = input["type"] == "date" ? Time.iso8601(raw.to_s).utc : ValueContract.model_number(raw, input['unit'])
      end
    end

    def evaluate(node, inputs, trace)
      raise ConfigurationError, "formula node must be a mapping" unless node.is_a?(Hash)
      op = node.fetch("op")
      value = case op
              when "input"
                inputs.fetch(node.fetch("id"))
              when "const"
                decimal(node.fetch("value"))
              when "add", "sum"
                Array(node.fetch("args")).map { |child| evaluate(child, inputs, trace) }.reduce(BigDecimal("0"), :+)
              when "multiply"
                Array(node.fetch("args")).map { |child| evaluate(child, inputs, trace) }.reduce(BigDecimal("1"), :*)
              when "subtract"
                args = Array(node.fetch("args")).map { |child| evaluate(child, inputs, trace) }
                args.drop(1).reduce(args.fetch(0), :-)
              when "divide"
                left = evaluate(node.fetch("left"), inputs, trace)
                right = evaluate(node.fetch("right"), inputs, trace)
                raise ValidationError, "division by zero" if right.zero?
                left / right
              when "date_diff_days"
                start_time = evaluate(node.fetch("start"), inputs, trace)
                end_time = evaluate(node.fetch("end"), inputs, trace)
                unless start_time.is_a?(Time) && end_time.is_a?(Time)
                  raise ConfigurationError, "date_diff_days requires date inputs"
                end
                BigDecimal(((end_time - start_time) / 86_400).to_s)
              else
                raise ConfigurationError, "unsupported deterministic operation: #{op}"
              end
      trace << { "op" => op, "value" => trace_value(value) }
      value
    end

    def trace_value(value)
      value.is_a?(BigDecimal) ? value.to_s("F") : value.iso8601
    end

    def classify(model, output)
      Array(model["bands"]).each do |band|
        minimum = band.key?("min") ? decimal(band["min"]) : nil
        maximum = band.key?("max") ? decimal(band["max"]) : nil
        next if minimum && output < minimum
        next if maximum && output > maximum
        return band.fetch("id")
      end
      raise ConfigurationError, "model #{model['id']} bands do not cover output #{output.to_s('F')}"
    end

    def decimal(value)
      BigDecimal(value.to_s)
    rescue ArgumentError
      raise ValidationError, "not a numeric model input: #{value.inspect}"
    end

    def decode(row)
      {
        "id" => row["id"], "model_id" => row["model_id"], "model_version" => row["model_version"],
        "input_hash" => row["input_hash"], "scenario" => row["scenario"],
        "output" => JSON.parse(row["output_json"]), "trace" => JSON.parse(row["trace_json"]),
        "run_id" => row["run_id"], "calculated_at" => row["calculated_at"], "status" => row["status"]
      }
    end
  end
end
