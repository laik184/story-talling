"""Human-only, explicit terminal review; no model or MCP approval shortcut."""
from __future__ import annotations

import argparse
import json
import sys

from .store import WriterDnaLibrary


def review_from_tty(library: WriterDnaLibrary, dna_id: str) -> dict:
    if not sys.stdin.isatty():
        raise RuntimeError("review requires an interactive terminal so a human can decide")
    candidate = library.get(dna_id)
    if not candidate or candidate.get("state") != "B":
        raise ValueError("B analyzed candidate not found")
    print(json.dumps(candidate, ensure_ascii=False, indent=2))
    reviewer = input("Human reviewer name: ").strip()
    decision = input("Decision [APPROVE/REJECT/NEEDS_REVISION]: ").strip().upper()
    reason = input("Decision reason: ").strip()
    print(f"Confirm {decision} for {dna_id}; this creates an auditable review record.")
    if input("Type YES to confirm: ").strip() != "YES":
        raise RuntimeError("review cancelled; no state changed")
    return library.review_candidate(dna_id, decision, reviewer, reason, "human")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dna_id")
    args = parser.parse_args(argv)
    try:
        print(json.dumps(review_from_tty(WriterDnaLibrary(), args.dna_id), ensure_ascii=False, indent=2))
        return 0
    except (ValueError, RuntimeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
