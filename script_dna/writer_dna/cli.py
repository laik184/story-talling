"""Local Writer DNA initialization, lexical search, get, rebuild and review entrypoint."""
from __future__ import annotations

import argparse
import json

from .review_cli import review_from_tty
from .store import WriterDnaLibrary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("init")
    sub.add_parser("rebuild-index")
    search = sub.add_parser("search")
    search.add_argument("--query", default="")
    search.add_argument("--category", choices=("hooks", "language", "humor", "story", "ending"))
    search.add_argument("--status", default="approved")
    search.add_argument("--source")
    search.add_argument("--dna-id")
    search.add_argument("--limit", type=int, default=10)
    get = sub.add_parser("get"); get.add_argument("dna_id")
    review = sub.add_parser("review"); review.add_argument("dna_id")
    args = parser.parse_args(argv)
    library = WriterDnaLibrary()
    try:
        if args.action == "init": result = {"initialized": True, "path": str(library.path)}
        elif args.action == "rebuild-index": result = library.rebuild_search_index()
        elif args.action == "search": result = library.search(args.query,args.category,args.status,args.source,args.dna_id,args.limit)
        elif args.action == "get": result = library.get(args.dna_id) or {"found": False}
        else: result = review_from_tty(library, args.dna_id)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
