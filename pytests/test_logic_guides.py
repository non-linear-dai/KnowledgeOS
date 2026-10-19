"""The UI's teaching examples are executable contracts, not illustrative pseudocode."""
import json
from pathlib import Path

import pytest

from knowledge_os.logic_workbench import LogicWorkbench, contains_expected


EXAMPLES = json.loads((Path(__file__).resolve().parents[1] /
                       "prototype/ontology-studio/app/rules/logic-examples.json").read_text())


@pytest.mark.parametrize("kind", EXAMPLES)
def test_workbench_teaching_examples_match_actual_engine_without_durable_writes(service, kind):
    workbench = LogicWorkbench(service)
    definition = EXAMPLES[kind]
    before_changes = service.db.first("SELECT count(*) AS n FROM changeset")["n"]
    before_runs = service.db.first("SELECT count(*) AS n FROM durable.logic_run")["n"]
    result = workbench.preview(kind, definition, definition["test_cases"][0]["input"])
    assert result["tests"]["passed"], result["tests"]
    assert len(result["tests"]["cases"]) == len(definition["test_cases"])
    assert contains_expected(result["result"], definition["test_cases"][0]["expected"])
    assert service.db.first("SELECT count(*) AS n FROM changeset")["n"] == before_changes
    assert service.db.first("SELECT count(*) AS n FROM durable.logic_run")["n"] == before_runs
