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
    return tmp_path


@pytest.fixture
def service(workspace):
    instance = Service(Config(workspace))
    instance.compile(rebuild=True)
    try:
        yield instance
    finally:
        instance.close()
