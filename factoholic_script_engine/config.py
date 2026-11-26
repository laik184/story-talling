"""Environment-backed local paths and server configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _user_state_dir() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "factoholic-script-engine"
    return Path.home() / ".factoholic-script-engine"


@dataclass(frozen=True, slots=True)
class EngineConfig:
    dataset_dir: Path
    state_dir: Path
    instructions_file: Path
    workflow_file: Path
    dataset_creator: str | None = None

    @property
    def database_file(self) -> Path:
        return self.state_dir / "factoholic.sqlite3"

    @classmethod
    def from_env(cls) -> "EngineConfig":
        project_root = Path(__file__).resolve().parent.parent
        dataset = os.environ.get("FACTOHOLIC_DATASET_DIR")
        state = os.environ.get("FACTOHOLIC_STATE_DIR")
        instructions = os.environ.get("FACTOHOLIC_MASTER_INSTRUCTIONS")
        workflow = os.environ.get("FACTOHOLIC_WORKFLOW_CONTRACT")
        creator = os.environ.get("FACTOHOLIC_DATASET_CREATOR", "").strip() or None
        return cls(
            dataset_dir=Path(dataset).expanduser() if dataset else project_root / "scripts",
            state_dir=Path(state).expanduser() if state else _user_state_dir(),
            instructions_file=Path(instructions).expanduser() if instructions else project_root / "MASTER_INSTRUCTIONS.md",
            workflow_file=Path(workflow).expanduser() if workflow else project_root / "WORKFLOW_CONTRACT.md",
            dataset_creator=creator,
        )

    def resolved(self) -> "EngineConfig":
        return EngineConfig(
            dataset_dir=self.dataset_dir.expanduser().resolve(),
            state_dir=self.state_dir.expanduser().resolve(),
            instructions_file=self.instructions_file.expanduser().resolve(),
            workflow_file=self.workflow_file.expanduser().resolve(),
            dataset_creator=self.dataset_creator,
        )
