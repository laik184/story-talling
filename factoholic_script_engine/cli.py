"""Command entry point for the stdio server and local diagnostics."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

from . import __version__
from .config import EngineConfig
from .engine import ScriptEngine
from .server import build_server


def _claude_config() -> dict[str, object]:
    python = Path(sys.executable).resolve()
    project_root = Path(__file__).resolve().parent.parent
    dataset = Path(os.environ.get("FACTOHOLIC_DATASET_DIR", str(project_root / "scripts"))).resolve()
    state = Path(os.environ.get("FACTOHOLIC_STATE_DIR", str(EngineConfig.from_env().state_dir))).resolve()
    return {
        "mcpServers": {
            "factoholic-script-engine": {
                "command": str(python),
                "args": ["-m", "factoholic_script_engine.server"],
                "cwd": str(project_root),
                "env": {"FACTOHOLIC_DATASET_DIR": str(dataset), "FACTOHOLIC_STATE_DIR": str(state)},
            }
        }
    }


def main() -> None:
    parser = argparse.ArgumentParser(prog="factoholic-script-engine", description="Run or inspect the local FactoHolic MCP server.")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--refresh-index", action="store_true", help="Refresh the local SQLite index and exit.")
    parser.add_argument("--show-overview", action="store_true", help="Print the current dataset overview and exit.")
    parser.add_argument("--print-claude-config", action="store_true", help="Print a Claude Desktop mcpServers entry and exit.")
    args = parser.parse_args()
    if args.print_claude_config:
        print(json.dumps(_claude_config(), ensure_ascii=False, indent=2))
        return
    engine = ScriptEngine()
    if args.refresh_index:
        print(json.dumps(engine.refresh_index(), ensure_ascii=False, indent=2))
        return
    if args.show_overview:
        print(json.dumps(engine.get_dataset_overview(refresh=False), ensure_ascii=False, indent=2))
        return
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    build_server(engine).run(transport="stdio")
