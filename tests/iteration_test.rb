# frozen_string_literal: true

require_relative 'test_helper'

class IterationTest < Minitest::Test
  include WorkspaceHelper

  def proposal(service, **options)
    service.propose(**{ actor: 'agent:test', target_source: 'knowledge/entities/org-acme.md',
                       patch: { 'op' => 'replace', 'path' => '/base/node/label', 'value' => 'Approved name' },
                       reason: 'review regression' }.merge(options))['data']
  end

  def test_control_changes_invalidate_documents_and_failed_rebuild_preserves_projection
    with_workspace do |config|
      compiler = KnowledgeOS::Compiler.new(config: config)
      compiler.compile(rebuild: true)
      old_card = compiler.database.first('SELECT card_json FROM entity_card WHERE node_id=?', ['org:acme'])['card_json']
      ontology = config.ontology_dir.join('core.yaml')
      ontology.write(ontology.read.sub('predicate: lead_time_days, required: false', 'predicate: lead_time_days, required: true'))
      [false, true].each do |rebuild|
        assert_raises(KnowledgeOS::ValidationError) { compiler.compile(rebuild: rebuild) }
        assert_equal 5, compiler.database.first('SELECT count(*) AS n FROM node')['n']
        assert_equal old_card, compiler.database.first('SELECT card_json FROM entity_card WHERE node_id=?', ['org:acme'])['card_json']
      end
    ensure
      compiler&.close
    end
  end

  def test_unchanged_compile_does_not_rewrite_cards_or_vectors
    with_workspace do |config|
      compiler = KnowledgeOS::Compiler.new(config: config)
      compiler.compile(rebuild: true)
      before = compiler.database.execute('SELECT node_id,updated_at FROM entity_card ORDER BY node_id')
      compiler.compile
      assert_equal before, compiler.database.execute('SELECT node_id,updated_at FROM entity_card ORDER BY node_id')
    ensure
      compiler&.close
    end
  end

  def test_governance_pending_audit_and_connector_records_survive_deleted_index
    with_workspace do |config|
      service = compiled_service(config)
      id = proposal(service)['id']
      connector = KnowledgeOS::Connector.new(config: config, registry: service.registry, database: service.database, ledger: service.ledger)
      connector.ingest_ndjson(config.root.join('connectors/examples/erp-suppliers.ndjson'), config.root.join('connectors/examples/erp-suppliers.mapping.yaml'))
      service.database.transaction { KnowledgeOS::AuditCoordinator.new(database: service.database, ledger: service.ledger).stage(event_type: 'pending_test', actor: 'test', event_id: 'pending-1') }
      service.close
      FileUtils.mv(config.index_path, config.runtime_dir.join('old.index.db'))
      service = compiled_service(config)
      assert_equal id, service.changesets['data'].first['id']
      assert_equal 'Singapore', service.get('org:acme')['data']['attrs']['country']
      assert_equal 1, service.ledger.events(event_type: 'pending_test').length
      assert service.ledger.verify!['valid']
      assert config.state_path.file?
    ensure
      service&.close
    end
  end

  def test_unrelated_content_cannot_publish_but_exact_approved_patch_can
    with_workspace do |config|
      service = compiled_service(config)
      id = proposal(service)['id']
      service.review_changeset(id: id, reviewer: 'reviewer', decision: 'approved')
      path = config.knowledge_dir.join('entities/org-acme.md')
      original = path.read
      path.write(original + "\nUnrelated content.\n")
      assert_raises(KnowledgeOS::ConflictError) { service.publish_changeset(id: id, publisher: 'publisher', source_revision: "sha256:#{Digest::SHA256.file(path).hexdigest}") }
      assert_equal 'approved', service.changesets['data'].first['status']
      path.write(original.sub('label: Acme Industrial Systems', 'label: Approved name'))
      revision = "sha256:#{Digest::SHA256.file(path).hexdigest}"
      2.times { assert_equal 'published', service.publish_changeset(id: id, publisher: 'publisher', source_revision: revision)['data']['status'] }
      assert_equal 1, service.ledger.events(event_type: 'changeset_published').length
      assert_equal 'Approved name', service.get('org:acme')['data']['label']
    ensure
      service&.close
    end
  end

  def test_concurrent_review_has_exactly_one_winner_and_proposal_retry_is_idempotent
    with_workspace do |config|
      service = compiled_service(config)
      first = proposal(service, idempotency_key: 'request-1')
      assert_equal first, proposal(service, idempotency_key: 'request-1')
      assert_raises(KnowledgeOS::ConflictError) { proposal(service, idempotency_key: 'request-1', reason: 'different') }
      responses = Queue.new
      %w[approved rejected].map do |decision|
        Thread.new do
          begin
            service.review_changeset(id: first['id'], reviewer: decision, decision: decision)
            responses << :success
          rescue KnowledgeOS::ValidationError
            responses << :conflict
          end
        end
      end.each(&:join)
      assert_equal [:conflict, :success], 2.times.map { responses.pop }.sort
      assert_equal 1, service.database.first('SELECT lock_version FROM changeset WHERE id=?', [first['id']])['lock_version']
    ensure
      service&.close
    end
  end

  def test_timestamps_are_equivalent_and_intervals_are_half_open
    with_workspace do |config|
      service = compiled_service(config)
      times = %w[2026-09-20T00:00:00Z 2026-09-19T17:00:00-07:00]
      assert_equal [1, 1], times.map { |time| service.context('project:atlas', domain: 'pm', as_of: time)['data']['assertions'].length }
      service.database.execute("UPDATE edge SET valid_to='2026-09-20T00:00:00Z' WHERE src='project:atlas'")
      assert_empty service.neighbors('project:atlas', as_of: times.last)['data']['edges']
      historical = service.context('project:atlas', domain: 'pm', as_of: times.first)
      assert_empty historical['data']['entity_card']['attrs']
      assert_includes historical['knowledge_gaps'], 'historical_attributes_unavailable'
    ensure
      service&.close
    end
  end

  def test_long_running_service_refreshes_expiration_without_restart
    with_workspace do |config|
      compiler = KnowledgeOS::Compiler.new(config: config)
      compiler.compile(rebuild: true)
      compiler.close
      time = Time.utc(2026, 9, 21)
      service = KnowledgeOS::Service.new(config: config, clock: -> { time })
      assert_equal 1, service.get('project:atlas')['data']['current_assertions'].length
      time = Time.utc(2027, 9, 21)
      assert_empty service.get('project:atlas')['data']['current_assertions']
    ensure
      service&.close
    end
  end

  def test_schema_rejects_unknown_keywords_and_enforces_bounds_combinators_and_pointers
    validator = KnowledgeOS::SchemaValidator.new
    assert_raises(KnowledgeOS::ValidationError) { validator.validate!(99, { 'type' => 'number', 'maximum' => 1 }) }
    assert_raises(KnowledgeOS::ConfigurationError) { validator.validate!({}, { 'unevaluatedProperties' => false }) }
    assert_raises(KnowledgeOS::ValidationError) { validator.validate!([1], { 'type' => 'array', 'maxItems' => 0 }) }
    assert_raises(KnowledgeOS::ValidationError) { validator.validate!('bad', { 'type' => 'string', 'format' => 'date' }) }
    assert validator.validate!('ok', { 'oneOf' => [{ 'type' => 'string' }, { 'type' => 'number' }] })
    assert validator.validate!('ok', { '$ref' => '#/$defs/a~1b', '$defs' => { 'a/b' => { 'const' => 'ok' } } })
  end

  def test_units_decimal_and_model_currency_validation
    predicate = { 'id' => 'amount', 'value' => { 'type' => 'quantity' } }
    assert_raises(KnowledgeOS::ValidationError) { KnowledgeOS::ValueContract.validate!(predicate, 3) }
    assert_equal BigDecimal('1'), KnowledgeOS::ValueContract.model_number({ 'literal' => 60, 'unit' => 'minute_per_unit' }, 'hour_per_unit')
    assert_raises(KnowledgeOS::ValidationError) { KnowledgeOS::ValueContract.model_number({ 'literal' => 60, 'unit' => 'kg' }, 'hour_per_unit') }
    with_workspace do |config|
      service = compiled_service(config)
      inputs = { 'material_cost' => { 'literal' => '100.10', 'unit' => 'USD_per_unit' },
                 'labor_rate' => { 'literal' => '20', 'unit' => 'CNY_per_hour' }, 'labor_hours' => 1, 'overhead' => 0 }
      assert_raises(KnowledgeOS::ValidationError) { service.calculate('cost_rollup', inputs) }
    ensure
      service&.close
    end
  end

  def test_multivalued_attributes_and_external_assertion_schema_share_validation
    multi = { 'id' => 'tags', 'storage' => { 'mode' => 'attr' }, 'value' => { 'type' => 'string', 'cardinality' => 'many' } }
    assert KnowledgeOS::ValueContract.validate!(multi, ['a', 'b'])
    assert_raises(KnowledgeOS::ValidationError) { KnowledgeOS::ValueContract.validate!(multi, ['a', 5]) }
    with_workspace do |config|
      validator = KnowledgeOS::Validator.new(KnowledgeOS::Registry.new(config))
      document = KnowledgeOS::Frontmatter.parse(config.knowledge_dir.join('entities/project-atlas.md'))
      assertion = Marshal.load(Marshal.dump(document.data['knowledge']['assertions'].first))
      assertion['epistemic']['confidence'] = 2
      assert_raises(KnowledgeOS::ValidationError) { validator.validate_document!(document, assertions: [assertion]) }
    end
  end

  def test_connector_rejects_bad_values_and_obeys_embedding_policy
    with_workspace do |config|
      service = compiled_service(config)
      connector = KnowledgeOS::Connector.new(config: config, registry: service.registry, database: service.database, ledger: service.ledger)
      input = config.root.join('connectors/examples/erp-suppliers.ndjson')
      mapping = config.root.join('connectors/examples/erp-suppliers.mapping.yaml')
      valid = input.read
      input.write(valid.sub('"lead_time_days":42', '"lead_time_days":"NOT_A_QUANTITY"'))
      assert_raises(KnowledgeOS::ValidationError) { connector.ingest_ndjson(input, mapping) }
      assert_equal 0, service.database.first('SELECT count(*) AS n FROM connector_record')['n']
      input.write(valid)
      connector.ingest_ndjson(input, mapping)
      assert_equal 'calendar_days', service.history('org:acme')['data']['assertions'].find { |a| a['predicate'] == 'lead_time_days' }['value']['unit']
      before = service.database.first('SELECT vector_json FROM node_embedding WHERE node_id=?', ['org:acme'])['vector_json']
      input.write(valid.sub('"lead_time_days":42', '"lead_time_days":987654').sub('"version":"17"', '"version":"18"'))
      connector.ingest_ndjson(input, mapping)
      assert_equal before, service.database.first('SELECT vector_json FROM node_embedding WHERE node_id=?', ['org:acme'])['vector_json']
      input.write(valid)
      assert_raises(KnowledgeOS::ConflictError) { connector.ingest_ndjson(input, mapping) }
    ensure
      service&.close
    end
  end

  def test_business_keys_are_scoped_and_ontology_source_paths_are_preserved
    with_workspace do |config|
      service = compiled_service(config)
      service.database.execute("UPDATE node SET natural_key='ACME' WHERE id='project:atlas'")
      assert_equal 2, service.database.execute("SELECT id FROM node WHERE natural_key='ACME'").length
      ontology = config.ontology_dir.join('extension.yaml')
      ontology.write("concept_types:\n  - id: lab_sample\n    label: Laboratory sample\n    properties: []\n")
      registry = KnowledgeOS::Registry.new(config)
      definition = registry.studio_catalog['definitions'].find { |item| item['id'] == 'lab_sample' }
      assert_equal 'control/ontology/extension.yaml', definition['source_path']
    ensure
      service&.close
    end
  end

  def test_backup_restore_is_verified_and_never_overwrites_live_state
    with_workspace do |config|
      service = compiled_service(config)
      id = proposal(service)['id']
      recovery = KnowledgeOS::Recovery.new(config)
      backup = config.root.join('backup')
      assert_raises(KnowledgeOS::ConflictError) { recovery.backup(backup) }
      service.close
      service = nil
      manifest = recovery.backup(backup)
      assert_equal %w[knowledge.ledger.db knowledge.state.db], manifest['files'].keys.sort
      assert_raises(KnowledgeOS::ConflictError) { recovery.restore(backup) }
      FileUtils.mv(config.runtime_dir, config.root.join('old-runtime'))
      recovery.restore(backup)
      service = compiled_service(config)
      assert_equal id, service.changesets['data'].first['id']
      assert service.ledger.verify!['valid']
    ensure
      service&.close
    end
  end

  def test_legacy_governance_table_migrates_without_losing_approval
    with_workspace do |config|
      service = compiled_service(config)
      id = proposal(service)['id']
      service.review_changeset(id: id, reviewer: 'reviewer', decision: 'approved')
      service.close
      service = nil
      old = SQLite3::Database.new(config.index_path.to_s)
      old.execute('ATTACH DATABASE ? AS durable', [config.state_path.to_s])
      ddl = old.get_first_value("SELECT sql FROM durable.sqlite_master WHERE type='table' AND name='changeset'")
      old.execute(ddl)
      old.execute('INSERT INTO main.changeset SELECT * FROM durable.changeset')
      old.execute('DROP TABLE durable.changeset')
      old.close
      service = KnowledgeOS::Service.new(config: config)
      assert_equal 'approved', service.changesets['data'].first['status']
      assert_equal id, service.changesets['data'].first['id']
      assert_nil service.database.first("SELECT name FROM main.sqlite_master WHERE type='table' AND name='changeset'")
    ensure
      service&.close
    end
  end

  def test_recorded_time_preserves_attributes_and_graph_after_later_updates
    with_workspace do |config|
      service = compiled_service(config)
      cutoff = Time.now.utc.iso8601(6)
      source = config.knowledge_dir.join('entities/org-acme.md')
      source.write(source.read.sub('country: China', 'country: Japan'))
      compiler = KnowledgeOS::Compiler.new(config: config, registry: service.registry, database: service.database, ledger: service.ledger)
      compiler.compile
      current = service.get('org:acme')['data']
      old = service.context('org:acme', domain: 'industry', as_of: '2026-09-27T00:00:00Z', recorded_as_of: cutoff)['data']
      assert_equal 'Japan', current['attrs']['country']
      assert_equal 'China', old['entity_card']['attrs']['country']
      refute_empty old['relations']['edges']
      assert old['relations']['nodes'].all? { |node| !node['missing'] }
    ensure
      service&.close
    end
  end

  def test_connector_deletion_reverts_to_authored_attributes_and_survives_rebuild
    with_workspace do |config|
      service = compiled_service(config)
      connector = KnowledgeOS::Connector.new(config: config, registry: service.registry, database: service.database, ledger: service.ledger)
      input = config.root.join('connectors/examples/erp-suppliers.ndjson')
      mapping = config.root.join('connectors/examples/erp-suppliers.mapping.yaml')
      mapping.write(mapping.read + "\ndeleted_field: deleted\n")
      connector.ingest_ndjson(input, mapping)
      record = JSON.parse(input.read)
      record['deleted'] = true
      record['version'] = '18'
      record['updated_at'] = '2026-09-26T08:00:00Z'
      input.write(JSON.generate(record) + "\n")
      connector.ingest_ndjson(input, mapping)
      assert_equal 'China', service.get('org:acme')['data']['attrs']['country']
      compiler = KnowledgeOS::Compiler.new(config: config, registry: service.registry, database: service.database, ledger: service.ledger)
      compiler.compile(rebuild: true)
      assert_equal 'China', service.get('org:acme')['data']['attrs']['country']
      assert_equal 1, service.ledger.events(event_type: 'connector_delete').length
    ensure
      service&.close
    end
  end

  def test_ontology_studio_patch_binds_the_actual_definition
    with_workspace do |config|
      service = compiled_service(config)
      original = service.studio['data']['definitions'].find { |item| item['id'] == 'organization' }
      after = Marshal.load(Marshal.dump(original))
      after['label'] = 'Reviewed organization'
      after['bindings'] = original['bindings'].map { |binding| binding.merge('predicateId' => binding['predicate_id']) }
      operations = [{ 'type' => 'update', 'targetId' => 'organization', 'targetKind' => 'concept', 'before' => original, 'after' => after }]
      id = proposal(service, target_source: original['source_path'], patch: { 'op' => 'studio_batch' }, operations: operations)['id']
      service.review_changeset(id: id, reviewer: 'reviewer', decision: 'approved')
      file = config.root.join(original['source_path'])
      file.write(file.read.sub('label: Organization', 'label: Reviewed organization'))
      result = service.publish_changeset(id: id, publisher: 'publisher', source_revision: "sha256:#{Digest::SHA256.file(file).hexdigest}")
      assert_equal 'published', result['data']['status']
    ensure
      service&.close
    end
  end
end
