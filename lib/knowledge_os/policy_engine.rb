# frozen_string_literal: true

require "time"

module KnowledgeOS
  class PolicyEngine
    def initialize(registry:, clock: -> { Time.now.utc })
      @registry = registry
      @clock = clock
    end

    def assertion_temperature(predicate_id:, status:, observed_at: nil, valid_from: nil, valid_to: nil)
      return "warm" unless status == "confirmed"
      return 'warm' if timestamp(observed_at) && timestamp(observed_at) > now
      return "warm" if timestamp(valid_from) && timestamp(valid_from) > now
      return "warm" if timestamp(valid_to) && timestamp(valid_to) <= now

      age = age_days(observed_at)
      stale_after = freshness_days(predicate_id)
      retention = @registry.maintenance_policy.dig("hot_warm_cold", "warm_retention_days").to_i
      return "cold" if age && retention.positive? && age > retention
      return "warm" if age && stale_after && age > stale_after
      "hot"
    end

    def stale?(predicate_id, observed_at)
      age = age_days(observed_at)
      limit = freshness_days(predicate_id)
      age && limit && age > limit
    end

    def priority_for(score)
      configured = @registry.maintenance_policy.fetch("priority", {})
      configured.sort_by { |_id, item| -item.fetch("minimum_score", 0).to_i }
                .find { |_id, item| score.to_i >= item.fetch("minimum_score", 0).to_i }&.first || "P3"
    end

    def review_limit(requested)
      requested = [[requested.to_i, 1].max, 1_000].min
      budgets = @registry.maintenance_policy.fetch("budgets", {})
      [requested, budgets.fetch("daily_review_items", requested).to_i,
       budgets.fetch("weekly_review_items", requested).to_i].select(&:positive?).min || 0
    end

    def provenance_errors(predicate_id, assertion)
      predicate = @registry.predicate(predicate_id)
      tier = predicate.dig("policy", "provenance_tier") || "C"
      policy = @registry.provenance_policies[tier] || {}
      provenance = assertion["provenance"] || {}
      errors = []
      if policy["source_ref_required"] && Array(provenance["source_refs"]).empty?
        errors << "#{tier} provenance requires source_refs"
      end
      if policy["locator_required"]
        refs = Array(provenance["source_refs"])
        unless refs.any? { |ref| ref.to_s.match?(/\A[a-z][a-z0-9+.-]*:/i) }
          errors << "#{tier} provenance requires a source locator"
        end
      end
      if policy["evidence_or_snapshot_required"] && Array(provenance["evidence_refs"]).empty?
        errors << "#{tier} provenance requires evidence or snapshot refs"
      end
      if policy["origin_required"] && Array(provenance["source_refs"]).empty? && provenance["origin"].to_s.empty?
        errors << "#{tier} provenance requires an origin"
      end
      errors
    end

    private

    def freshness_days(predicate_id)
      predicate = @registry.predicate(predicate_id)
      id = predicate && predicate.dig("policy", "freshness")
      raw = id && @registry.freshness_policies.dig(id, "stale_after_days")
      raw.nil? ? nil : raw.to_i
    end

    def age_days(value)
      time = timestamp(value)
      time ? ((now - time) / 86_400).floor : nil
    end

    def timestamp(value)
      return nil if value.nil? || value.to_s.empty?
      Time.iso8601(value.to_s)
    rescue ArgumentError
      nil
    end

    def now
      value = @clock.call
      value.respond_to?(:utc) ? value.utc : Time.iso8601(value.to_s).utc
    end
  end
end
