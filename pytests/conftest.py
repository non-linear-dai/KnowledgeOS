from pathlib import Path
import shutil

import pytest

from knowledge_os.core import Config
from knowledge_os.service import Service


REPO = Path(__file__).resolve().parents[1]


@pytest.fixture
def workspace(tmp_path):
    for name in ("control", "knowledge", "connectors"):
        shutil.copytree(REPO / name, tmp_path / name)
    # Expert packages are authored in the local workspace too. Tests create
    # their own packages; never import a developer's live template/decision
    # catalogue into fixtures that assert the five canonical seed entities.
    for name in ("templates", "operations", "decisions"):
        directory = tmp_path / "knowledge" / name
        if directory.exists():
            shutil.rmtree(directory)
        directory.mkdir()
    # Decision model tests likewise publish their own versioned graphs.
    directory = tmp_path / "control/decision-models"
    if directory.exists():
        shutil.rmtree(directory)
    directory.mkdir()
    return tmp_path


@pytest.fixture
def service(workspace):
    instance = Service(Config(workspace))
    instance.compile(rebuild=True)
    try:
        yield instance
    finally:
        instance.close()
