from __future__ import annotations

from pathlib import Path

import pytest

from factoholic_script_engine.config import EngineConfig
from factoholic_script_engine.engine import ScriptEngine


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def full_engine(tmp_path_factory: pytest.TempPathFactory) -> ScriptEngine:
    state = tmp_path_factory.mktemp("factoholic-state")
    return ScriptEngine(EngineConfig(
        dataset_dir=ROOT / "scripts",
        state_dir=state,
        instructions_file=ROOT / "MASTER_INSTRUCTIONS.md",
        workflow_file=ROOT / "WORKFLOW_CONTRACT.md",
    ))
