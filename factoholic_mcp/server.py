"""Local-only stdio entry point."""
from __future__ import annotations
import argparse
import logging
import sys
from .server_tools import create_server
from .service import StorytellingService

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="FactoHolic Storytelling MCP server (local stdio)")
    parser.add_argument("--transport", choices=("stdio",), default="stdio")
    args = parser.parse_args(argv)
    logging.basicConfig(stream=sys.stderr, level=logging.WARNING)
    create_server(StorytellingService()).run(transport=args.transport)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
