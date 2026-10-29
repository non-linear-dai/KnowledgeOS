"""Extraction protocol v2 baseline: schema, worked examples and the portable extraction skill."""
import copy
import json
from pathlib import Path

import jsonschema
import pytest

REPO = Path(__file__).resolve().parents[1]
EXAMPLES = REPO / "docs/examples/extraction-v2"


def _examples():
    return {path.name: json.loads(path.read_text(encoding="utf-8")) for path in sorted(EXAMPLES.glob("*.json"))}


def _anchors(value):
    if isinstance(value, dict):
        if {"block_id", "start", "end", "quote"} <= set(value):
            yield value
        for item in value.values():
            yield from _anchors(item)
    elif isinstance(value, list):
        for item in value:
            yield from _anchors(item)


@pytest.fixture
def validator(service):
    schema = service.registry.schemas["extraction-v2"]
    return jsonschema.Draft202012Validator(schema, format_checker=jsonschema.FormatChecker())


def test_v2_examples_match_exactly_one_message_type(service, validator):
    schema = service.registry.schemas["extraction-v2"]
    examples = _examples()
    assert {message["message_type"] for message in examples.values()} == {
        "contract_request", "extraction_contract", "source_document", "source_receipt",
        "extraction_submission", "validation_report"}
    for name, message in examples.items():
        assert not list(validator.iter_errors(message)), name
        branches = [branch for branch in schema["oneOf"]
                    if jsonschema.Draft202012Validator({**schema, "oneOf": [branch]}).is_valid(message)]
        assert len(branches) == 1, name


def test_v2_example_anchors_are_exact_block_slices():
    examples = _examples()
    blocks = {block["id"]: block["text"] for block in examples["03-source-document.json"]["blocks"]}
    submission = examples["05-extraction-submission.json"]
    anchors = list(_anchors(submission)) + list(_anchors(examples["03-source-document.json"]))
    assert anchors
    for anchor in anchors:
        assert blocks[anchor["block_id"]][anchor["start"]:anchor["end"]] == anchor["quote"]
    for instance in submission["instances"]:
        for field in instance["fields"]:
            quotes = [a["quote"] for a in field["evidence"] if a.get("role", "value") == "value"]
            assert any(field["value"]["raw"] in quote for quote in quotes)


def test_v2_schema_rejects_values_without_field_evidence(validator):
    submission = copy.deepcopy(_examples()["05-extraction-submission.json"])
    assert validator.is_valid(submission)
    broken = copy.deepcopy(submission)
    del broken["instances"][0]["fields"][0]["evidence"]
    assert not validator.is_valid(broken)
    broken = copy.deepcopy(submission)
    broken["instances"][0]["local_id"] = "org-1"
    assert not validator.is_valid(broken)
    broken = copy.deepcopy(submission)
    broken["instances"][0]["fields"][0]["value"]["normalized"] = "Acme"
    assert not validator.is_valid(broken)


def test_extraction_skill_is_portable_and_model_neutral(service):
    bundle = service.agent_skill("document-instance-extraction")["data"]
    assert set(bundle["files"]) == {"SKILL.md", "contract.yaml"}
    definition = bundle["definition"]
    assert definition["kind"] == "extraction"
    assert definition["protocol"]["name"] == "knowledgeos.extraction.v2"
    assert (REPO / definition["protocol"]["schema"]).is_file()
    assert definition["output"]["writes_durable_knowledge"] is False
    assert definition["self_check"]["require_precheck_before_propose"] is True
