"""Create and validate a Claude Desktop MCPB without bundling the corpus."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_SOURCE = ROOT / "packaging" / "claude-mcpb"
OUTPUT = ROOT / "dist" / "factoholic-script-engine-0.1.0.mcpb"


def copy_bundle_source(target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    for name in ("manifest.json", "pyproject.toml", "mcpb_entry.py", ".mcpbignore"):
        shutil.copy2(PACKAGE_SOURCE / name, target / name)
    shutil.copy2(ROOT / "WORKFLOW_CONTRACT.md", target / "WORKFLOW_CONTRACT.md")
    shutil.copytree(
        ROOT / "factoholic_script_engine",
        target / "factoholic_script_engine",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        dirs_exist_ok=True,
    )


def pack(source: Path, output: Path, use_npx: bool) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    executable = shutil.which("mcpb")
    command = [executable, "pack", str(source), str(output)] if executable else None
    if command is None and use_npx:
        npx = shutil.which("npx")
        if npx:
            command = [npx, "--yes", "@anthropic-ai/mcpb@2.1.2", "pack", str(source), str(output)]
    if command is None:
        raise RuntimeError("The MCPB packer was not found. Install Node.js and run: npx --yes @anthropic-ai/mcpb@2.1.2 pack <source> <output>")
    subprocess.run(command, cwd=ROOT, check=True)


def verify_bundle(path: Path) -> list[str]:
    with zipfile.ZipFile(path) as archive:
        names = [name for name in archive.namelist() if not name.endswith("/")]
        required = {"manifest.json", "pyproject.toml", "mcpb_entry.py", "WORKFLOW_CONTRACT.md"}
        missing = sorted(required - set(names))
        if missing:
            raise RuntimeError(f"MCPB is missing required files: {missing}")
        if any(name.startswith("scripts/") or "/scripts/" in name for name in names):
            raise RuntimeError("The MCPB unexpectedly contains the transcript corpus.")
        if "MASTER_INSTRUCTIONS.md" in names:
            raise RuntimeError("The bundle must read the operator-selected editable instructions file, not ship a stale copy.")
        source_files = [name for name in names if name.startswith("factoholic_script_engine/") and name.endswith(".py")]
        if not source_files:
            raise RuntimeError("The MCPB does not contain the server package.")
        allowed_roots = {"manifest.json", "pyproject.toml", "mcpb_entry.py", "WORKFLOW_CONTRACT.md", "factoholic_script_engine"}
        unexpected = sorted(name for name in names if name.split("/", 1)[0] not in allowed_roots)
        if unexpected:
            raise RuntimeError(f"The MCPB contains unexpected files: {unexpected}")
        return names


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--source-dir", type=Path, help="Use an existing prepared bundle source directory.")
    parser.add_argument("--no-npx", action="store_true", help="Do not use npx to obtain the official packer.")
    args = parser.parse_args()
    args.output = args.output.resolve()
    if args.source_dir:
        source = args.source_dir.resolve()
    else:
        source = ROOT / "dist" / "mcpb-source"
        copy_bundle_source(source)
    pack(source, args.output, use_npx=not args.no_npx)
    names = verify_bundle(args.output)
    print(f"Created {args.output} ({args.output.stat().st_size} bytes; {len(names)} files)")
    print("Verified: package includes the server and workflow contract, and excludes scripts/ and MASTER_INSTRUCTIONS.md.")


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, subprocess.CalledProcessError, zipfile.BadZipFile) as error:
        print(f"MCPB build failed: {error}", file=sys.stderr)
        raise SystemExit(1) from error
