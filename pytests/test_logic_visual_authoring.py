"""Business labels and visual per-field bindings retain the existing engine contracts."""
import copy
import json
from pathlib import Path

import pytest

from knowledge_os.logic_workbench import LogicWorkbench
from test_decision_source_examples import source_examples
from test_logic_workbench import publish


EXAMPLES = json.loads((Path(__file__).resolve().parents[1] /
                       "prototype/ontology-studio/app/rules/logic-examples.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("kind", EXAMPLES)
def test_business_labels_do_not_change_logic_or_regression_expectations(service, kind):
    definition = copy.deepcopy(EXAMPLES[kind])
    for index, item in enumerate(definition.get("inputs", [])):
        item["label"] = f"业务输入 {index + 1}"
    for index, item in enumerate(definition.get("outputs", []) if kind == "decision_table" else []):
        item["label"] = f"业务结果 {index + 1}"
    for index, item in enumerate(definition.get("nodes", [])):
        item["label"] = f"业务步骤 {index + 1}"
    for index, case in enumerate(definition["test_cases"]):
        case["label"] = f"业务验证案例 {index + 1}"
    assert LogicWorkbench(service).preview(kind, definition)["tests"]["passed"]


def test_visual_field_bindings_compile_publish_and_run_the_existing_decision_chain(service, source_examples):
    _, model = source_examples
    definition = copy.deepcopy(model)
    definition["id"] = "visual_size_decision"
    definition["nodes"][0]["bindings"] = {"size": {"source": "facts", "path": ["scenario_1"]}}
    definition["input_fields"] = [{"id": "scenario_1", "label": "零件尺寸", "type": "number"}]
    for case in definition["test_cases"]:
        case["input"]["scenario_1"] = case["input"]["facts"]["size"]
    definition["nodes"][0]["label"] = "按尺寸选型"
    definition["output_labels"] = {"duration": "预计加工工时", "energy": "预计能耗"}
    workbench = LogicWorkbench(service)
    assert workbench.preview("decision_model", definition)["tests"]["passed"]
    publish(service, "decision_model", definition)
    service.compile(rebuild=True)
    ref = "decision_model:visual_size_decision@1.0.0"
    resolved = LogicWorkbench(service).resolve(ref)[1]
    assert resolved["nodes"][0]["label"] == "按尺寸选型"
    assert resolved["output_labels"]["energy"] == "预计能耗"
    assert resolved["input_fields"][0]["label"] == "零件尺寸"
    assert LogicWorkbench(service).tests("decision_model", resolved)["passed"]
    assert service.ledger.verify()["valid"]
