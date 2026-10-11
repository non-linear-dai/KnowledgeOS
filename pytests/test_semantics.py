import copy
import hashlib
import http.client
import json
import threading
from pathlib import Path

import pytest
import yaml
from rdflib import Graph, RDF, RDFS
from rdflib.compare import isomorphic
from rdflib.namespace import PROV, SH, SKOS

from knowledge_os import __version__, __contract_version__
from knowledge_os.api import AccessControl, KnowledgeServer
from knowledge_os.core import Config, ConflictError, Registry, ValidationError, frontmatter, digest
from knowledge_os.connector import Connector
from knowledge_os.governance import ChangePlan
from knowledge_os.semantic_contract import SemanticContract, validate_profile, DEFAULT_PROFILE
from knowledge_os.recovery import backup, restore


def ontology_packet(service, identifier='inspection_report'):
    material = '检验报告记录一次质量检验的结论和说明。'
    request = service.ontology_request(goal='建立检验报告对象', material=material)['data']
    after = {'id': identifier, 'kind': 'concept', 'label': '检验报告', 'description': material, 'lifecycle': 'draft',
             'sourcePath': 'control/ontology/core.yaml', 'config': {}, 'bindings': [{'predicateId': 'summary', 'required': False, 'cardinality': 'inherit', 'group': 'other'}]}
    output = {'protocol_version': request['protocol_version'], 'request_id': request['request_id'],
              'operations': [{'operation': {'type': 'create', 'targetKind': 'concept', 'targetId': identifier, 'after': after},
                              'evidence': [{'segment_id': 'material:1', 'quote': material}]}]}
    return request, output


def unchanged_files(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for folder in ('control', 'knowledge') for p in (root / folder).rglob('*') if p.is_file()}


def test_versions_iri_roundtrip_and_legacy_registry(workspace):
    assert (__version__, __contract_version__) == ('4.0.0', '4.0')
    registry = Registry(Config(workspace)); contract = SemanticContract(registry)
    for kind in ('concept', 'predicate', 'relation', 'entity', 'assertion', 'source'):
        identifier = '组织:a/b #1%'
        assert contract.decode_iri(contract.iri(kind, identifier)) == {'kind': kind, 'id': identifier}
    assert contract.iri('concept', 'same') != contract.iri('entity', 'same')
    with pytest.raises(ValidationError):
        contract.decode_iri('urn:knowledgeos:entity/org:raw')
    (workspace / 'control/semantics/profile.yaml').unlink()
    assert Registry(Config(workspace)).semantic_profile == DEFAULT_PROFILE
    with pytest.raises(ValidationError):
        validate_profile({**DEFAULT_PROFILE, 'remote_imports': True})


def test_rdf_exports_equivalent_and_skos_classes_distinct(service):
    contract = SemanticContract(service.registry)
    ttl = Graph().parse(data=contract.export('turtle')['content'], format='turtle')
    ld = Graph().parse(data=contract.export('json-ld')['content'], format='json-ld')
    assert isomorphic(ttl, ld)
    cls = contract.iri('concept', 'organization'); term = contract.iri('term', 'core/organization')
    assert (cls, RDF.type, RDFS.Class) in ttl
    assert (term, RDF.type, SKOS.Concept) in ttl
    assert (cls, RDF.type, SKOS.Concept) not in ttl
    assert (term, contract.k.describesClass, cls) in ttl
    assert list(ttl.objects(contract.iri('shape', 'organization/legal_name'), SH.minCount))
    assert not list(ttl.triples((None, RDFS.domain, None)))


def test_generated_shape_and_shared_compiler_gate(service, workspace):
    document, _ = frontmatter(workspace / 'knowledge/entities/org-acme.md')
    contract = SemanticContract(service.registry)
    good = contract.validate_document(document, document['knowledge']['assertions'])
    assert good['conforms']
    document['knowledge']['attrs'].pop('legal_name')
    assert not contract.validate_document(document, document['knowledge']['assertions'])['conforms']
    with pytest.raises(ValidationError, match='requires predicate legal_name'):
        service.semantic_validate(document=document)


def test_projection_connector_gate_is_atomic(service, workspace):
    mapping = yaml.safe_load((workspace / 'connectors/examples/erp-suppliers.mapping.yaml').read_text())
    mapping['node']['id_prefix'] = 'new-org'; mapping['attrs'].pop('legal_name')
    record = json.loads((workspace / 'connectors/examples/erp-suppliers.ndjson').read_text().splitlines()[0])
    with pytest.raises(ValidationError, match='requires predicate legal_name'):
        Connector(service).ingest_record(mapping, record, 'new', '1', digest(record), 'erp:supplier:new')
    assert service.db.first("SELECT count(*) AS n FROM connector_record")['n'] == 0
    assert service.db.first("SELECT count(*) AS n FROM source_ref WHERE id='erp:supplier:new'")['n'] == 0
    assert service.ledger.verify()['valid']


def test_provenance_preserves_native_facts_and_has_no_invented_agents(service):
    contract = SemanticContract(service.registry)
    result = service.semantic_export('turtle', 'provenance', 'org:acme')['data']
    graph = Graph().parse(data=result['content'], format='turtle')
    assert list(graph.triples((None, PROV.wasDerivedFrom, None)))
    assert list(graph.triples((None, contract.k.provenanceTier, None)))
    assert not list(graph.triples((None, PROV.wasGeneratedBy, None)))
    assert not list(graph.subjects(RDF.type, PROV.Agent))


def test_ontology_assistance_end_to_end_and_independent_review(service, workspace):
    request, output = ontology_packet(service)
    files = unchanged_files(workspace)
    before = service.db.first('SELECT count(*) AS n FROM semantic_revision')['n']
    preview = service.ontology_preview(request=request, model_output=output)['data']
    assert preview['impact']['compatible'] and preview['impact']['candidate_counts']['entities'] == 5
    assert files == unchanged_files(workspace)
    assert service.db.first('SELECT count(*) AS n FROM semantic_revision')['n'] == before
    proposal = service.ontology_propose(request=request, model_output=output, actor='agent:modeler', reason='新增检验建模', idempotency_key='modeling-1')['data']
    again = service.ontology_propose(request=request, model_output=output, actor='agent:modeler', reason='新增检验建模', idempotency_key='modeling-1')['data']
    assert again['id'] == proposal['id']
    with pytest.raises(ValidationError, match='independent reviewer'):
        service.review_changeset(id=proposal['id'], reviewer='agent:modeler', decision='approved')
    service.review_changeset(id=proposal['id'], reviewer='human:reviewer', decision='approved')
    applied = service.apply_changeset(id=proposal['id'], publisher='human:publisher')['data']
    service.publish_changeset(id=proposal['id'], publisher='human:publisher', source_revision=applied['source_revision'])
    assert 'inspection_report' in {c['id'] for c in service.registry.ontology['concept_types']}
    assert service.db.first('SELECT count(*) AS n FROM semantic_revision')['n'] == before + 1
    assert service.ledger.verify()['valid']
    service.compile(rebuild=True)
    assert service.db.first('SELECT count(*) AS n FROM semantic_revision')['n'] == before + 1


def test_ontology_evidence_integrity_and_stale_request(service, workspace):
    request, output = ontology_packet(service)
    bad = copy.deepcopy(output); bad['operations'][0]['evidence'][0]['quote'] = '无依据的猜测'
    with pytest.raises(ValidationError, match='quote supplied material'):
        service.ontology_preview(request=request, model_output=bad)
    tampered = {**request, 'goal': 'changed'}
    with pytest.raises(ValidationError, match='integrity'):
        service.ontology_preview(request=tampered, model_output=output)
    path = workspace / 'control/ontology/core.yaml'; path.write_text(path.read_text() + '\n')
    service.refresh_registry()
    with pytest.raises(ConflictError, match='stale'):
        service.ontology_preview(request=request, model_output=output)


def test_impact_detects_connector_break_and_keeps_live_projection(service, workspace):
    Connector(service).ingest_ndjson(workspace / 'connectors/examples/erp-suppliers.ndjson', workspace / 'connectors/examples/erp-suppliers.mapping.yaml')
    source = next(d for d in service.studio()['data']['definitions'] if d['id'] == 'country')
    after = {**source, 'sourcePath': source['source_path'], 'config': {**source['config'], 'value_type': 'boolean'}}
    operation = {'type': 'update', 'targetKind': 'predicate', 'targetId': 'country', 'before': source, 'after': after}
    files = unchanged_files(workspace)
    result = service.semantic_impact(target_source=source['source_path'], patch={'op': 'studio_batch'}, operations=[operation])['data']
    assert not result['compatible'] and result['migration_required']
    assert files == unchanged_files(workspace)
    assert service.get('org:acme')['data']['attrs']['country'] == 'Singapore'


def test_vocab_changeset_version_cycle_and_publication(service, workspace):
    vocabulary = copy.deepcopy(service.semantics()['data']['vocabularies']['core'])
    vocabulary['version'] = '1.0.1'; vocabulary['terms'][0]['alt_labels']['zh'].append('公司')
    proposal = service.vocabulary_propose(vocabulary=vocabulary, actor='human:editor', reason='补充公司别名', base_revision=service.registry.fingerprint)['data']
    assert '公司' not in service.registry.vocabularies['core']['terms'][0]['alt_labels']['zh']
    service.review_changeset(id=proposal['id'], reviewer='human:reviewer', decision='approved')
    revision = service.apply_changeset(id=proposal['id'], publisher='human:publisher')['data']['source_revision']
    service.publish_changeset(id=proposal['id'], publisher='human:publisher', source_revision=revision)
    assert service.registry.vocabularies['core']['version'] == '1.0.1'
    with pytest.raises(ValidationError, match='higher semantic version'):
        service.vocabulary_propose(vocabulary=vocabulary, actor='human:editor', reason='重试', base_revision=service.registry.fingerprint)
    cyclic = copy.deepcopy(vocabulary); cyclic['version'] = '1.0.2'; cyclic['terms'][0]['broader'] = ['organization']
    with pytest.raises(ValidationError, match='acyclic'):
        service.vocabulary_propose(vocabulary=cyclic, actor='human:editor', reason='循环', base_revision=service.registry.fingerprint)


def test_batch_preview_atomic_manual_decisions_and_staleness(service):
    request, output = ontology_packet(service)
    one = service.ontology_propose(request=request, model_output=output, actor='agent:modeler', reason='第一项')['data']['id']
    request2, output2 = ontology_packet(service, 'inspection_result')
    two = service.ontology_propose(request=request2, model_output=output2, actor='agent:modeler', reason='第二项')['data']['id']
    blocked = service.review_batch_preview(ids=[one, two], reviewer='agent:modeler', decision='approved')['data']
    assert not blocked['can_review']
    with pytest.raises(ValidationError, match='blockers'):
        service.review_batch(ids=[one, two], reviewer='agent:modeler', decision='approved', preview_hash=blocked['preview_hash'], note='确认')
    assert all(c['status'] == 'review_required' for c in service.changesets()['data'])
    preview = service.review_batch_preview(ids=[one, two], reviewer='human:reviewer', decision='approved')['data']
    assert preview['can_review'] and preview['automatic_approval'] is False
    service.review_changeset(id=one, reviewer='human:reviewer', decision='changes_requested')
    with pytest.raises(ConflictError, match='preview changed'):
        service.review_batch(ids=[one, two], reviewer='human:reviewer', decision='approved', preview_hash=preview['preview_hash'], note='确认')
    assert next(c for c in service.changesets()['data'] if c['id'] == two)['status'] == 'review_required'
    preview = service.review_batch_preview(ids=[two], reviewer='human:reviewer', decision='approved')['data']
    service.review_batch(ids=[two], reviewer='human:reviewer', decision='approved', preview_hash=preview['preview_hash'], note='已核对定义与依据')
    assert service.ledger.verify()['valid']


def test_competency_regression_and_skill_acceptance(service, workspace):
    assert service.semantic_evaluate()['data']['conforms']
    request = service.agent_request(question='What does Acme do?', domain='industry', target='org:acme')['data']
    assert request['semantic_contract']['vocabularies']['core']
    output = {'protocol_version': request['protocol_version'], 'request_id': request['request_id'], 'answer': 'Evidence is missing.', 'claims': []}
    with pytest.raises(ValidationError, match='success criteria'):
        service.agent_respond(request=request, model_output=output)
    output['claims'] = [{'statement': 'Acme is an organization.', 'evidence_refs': ['node:org:acme'], 'confidence': 0.9}]
    assert service.agent_respond(request=request, model_output=output)['data']['acceptance']['passed']


def test_backup_manifest_revision_retained_and_future_rejected(service, workspace, tmp_path):
    service.close(); folder = tmp_path / 'backup'; manifest = backup(Config(workspace), folder)
    assert manifest['application_version'] == '4.0.0' and manifest['durable_schema_version'] == 4
    manifest['durable_schema_version'] = 99; (folder / 'manifest.json').write_text(json.dumps(manifest))
    with pytest.raises(ValidationError, match='newer'):
        restore(Config(workspace), folder)


def test_semantic_api_permissions_identity_and_version(service):
    tokens = {'reader-token': {'principal': 'reader', 'roles': ['reader']}, 'agent-token': {'principal': 'agent', 'roles': ['agent']}}
    auth = AccessControl.from_env({'KNOWLEDGEOS_AUTH_TOKENS': json.dumps(tokens)})
    server = KnowledgeServer(('127.0.0.1', 0), service, auth); thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    def call(method, path, token='reader-token', body=None):
        conn = http.client.HTTPConnection('127.0.0.1', server.server_port)
        conn.request(method, path, json.dumps(body) if body else None, {'Authorization': 'Bearer '+token, 'Content-Type': 'application/json'})
        result = conn.getresponse(); payload = json.loads(result.read()); status = result.status; conn.close(); return status, payload
    try:
        assert call('GET', '/health')[1]['version'] == '4.0.0'
        assert call('GET', '/v1/semantics')[0] == 200
        assert call('POST', '/v1/ontology/request', body={'goal': 'model', 'material': 'material'})[0] == 403
        request, output = ontology_packet(service)
        status, data = call('POST', '/v1/ontology/propose', 'agent-token', {'request': request, 'model_output': output, 'actor': 'spoof', 'reason': 'Model'})
        assert status == 200
        assert service.db.first('SELECT actor FROM changeset WHERE id=?', (data['data']['id'],))['actor'] == 'agent'
        assert call('POST', '/v1/changesets/review-batch/preview', 'agent-token', {'ids': [data['data']['id']], 'decision': 'approved'})[0] == 403
    finally:
        server.shutdown(); server.server_close(); thread.join()


def test_single_json_array_is_one_semantic_value(service, workspace):
    definition = {'id': 'inspection_payload', 'label': 'Payload', 'value': {'type': 'json', 'cardinality': 'one'}, 'storage': {'mode': 'attr'}}
    (workspace / 'control/predicates/inspection_payload.yaml').write_text(yaml.safe_dump(definition))
    path = workspace / 'control/ontology/core.yaml'; ontology = yaml.safe_load(path.read_text())
    next(c for c in ontology['concept_types'] if c['id'] == 'organization')['properties'].append({'predicate': 'inspection_payload', 'required': False, 'cardinality': 'one'})
    path.write_text(yaml.safe_dump(ontology)); service.compile()
    document, _ = frontmatter(workspace / 'knowledge/entities/org-acme.md')
    document['knowledge']['attrs']['inspection_payload'] = [{'passed': True}, {'passed': False}]
    assert service.semantic_validate(document=document)['data']['conforms']


def test_old_client_enum_updates_preserve_allowed_values(service):
    original = next(d for d in service.studio()['data']['definitions'] if d['id'] == 'risk_level')
    after = {**original, 'sourcePath': original['source_path'], 'label': 'Risk label', 'config': {k: v for k, v in original['config'].items() if k != 'allowed'}}
    operation = {'type': 'update', 'targetKind': 'predicate', 'targetId': 'risk_level', 'before': original, 'after': after}
    documents = ChangePlan(service).staged_documents(original['source_path'], {'op': 'studio_batch'}, [operation])
    assert documents[original['source_path']]['data']['value']['allowed'] == ['low', 'medium', 'high', 'critical']


def test_agent_can_register_predicate_and_bind_concept_in_same_changeset(service):
    request, output = ontology_packet(service)
    predicate = next(d for d in service.studio()['data']['definitions'] if d['id'] == 'summary')
    after = {**predicate, 'id': 'inspection_result', 'label': '检验结论', 'sourcePath': 'control/predicates/inspection_result.yaml'}
    output['operations'].insert(0, {'operation': {'type': 'create', 'targetKind': 'predicate', 'targetId': 'inspection_result', 'after': after}, 'evidence': output['operations'][0]['evidence']})
    output['operations'][1]['operation']['after']['bindings'] = [{'predicateId': 'inspection_result', 'required': True, 'cardinality': 'inherit', 'group': 'other'}]
    assert service.ontology_preview(request=request, model_output=output)['data']['impact']['compatible']
    proposal = service.ontology_propose(request=request, model_output=output, actor='agent:modeler', reason='创建属性并绑定概念')['data']
    service.review_changeset(id=proposal['id'], reviewer='human:reviewer', decision='approved')
    revision = service.apply_changeset(id=proposal['id'], publisher='human:publisher')['data']['source_revision']
    service.publish_changeset(id=proposal['id'], publisher='human:publisher', source_revision=revision)
    assert 'inspection_result' in service.registry.predicates
    metadata = json.loads(service.db.first('SELECT proposal_metadata_json FROM changeset WHERE id=?', (proposal['id'],))['proposal_metadata_json'])
    assert metadata['source_segments'] == request['segments']
    assert service.ledger.verify()['valid']


def test_unknown_policy_cannot_silently_weaken_provenance(service, workspace):
    path = workspace / 'control/predicates/unit_cost.yaml'
    definition = yaml.safe_load(path.read_text()); definition['policy']['provenance_tier'] = 'unknown'; path.write_text(yaml.safe_dump(definition))
    fingerprint = service.registry.fingerprint
    with pytest.raises(ValidationError, match='unregistered provenance_tier'):
        service.refresh_registry()
    assert service.registry.fingerprint == fingerprint


def test_incremental_control_change_preserves_connector_authority_and_checks_projection(service, workspace):
    Connector(service).ingest_ndjson(workspace / 'connectors/examples/erp-suppliers.ndjson', workspace / 'connectors/examples/erp-suppliers.mapping.yaml')
    predicate_path = workspace / 'control/predicates/country.yaml'
    original = yaml.safe_load(predicate_path.read_text()); original['label'] = '国家或地区'; predicate_path.write_text(yaml.safe_dump(original))
    service.compile()
    assert service.get('org:acme')['data']['attrs']['country'] == 'Singapore'
    original['value']['type'] = 'boolean'; predicate_path.write_text(yaml.safe_dump(original))
    with pytest.raises(ValidationError, match='expects boolean'):
        service.compile()
    assert service.get('org:acme')['data']['attrs']['country'] == 'Singapore'
    assert service.ledger.verify()['valid']
