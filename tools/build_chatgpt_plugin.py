"""Build a local ChatGPT Desktop Agent Plugin ZIP without bundling user data."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_SOURCE = ROOT / "packaging" / "chatgpt-desktop-plugin"
OUTPUT = ROOT / "dist" / "factoholic-script-engine-chatgpt-0.1.0.zip"


def copy_package(target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    shutil.copy2(PACKAGE_SOURCE / "plugin.json", target / "plugin.json")
    shutil.copy2(PACKAGE_SOURCE / "mcp.json", target / "mcp.json")
    shutil.copytree(PACKAGE_SOURCE / "skills", target / "skills", dirs_exist_ok=True)
    server = target / "server"
    server.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / "WORKFLOW_CONTRACT.md", server / "WORKFLOW_CONTRACT.md")
    shutil.copytree(
        ROOT / "factoholic_script_engine",
        server / "factoholic_script_engine",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        dirs_exist_ok=True,
    )


def validate_package(root: Path) -> list[Path]:
    plugin = json.loads((root / "plugin.json").read_text(encoding="utf-8"))
    mcp = json.loads((root / "mcp.json").read_text(encoding="utf-8"))
    if plugin.get("$schema") != "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json":
        raise ValueError("plugin.json has the wrong Agent Plugins schema declaration.")
    if plugin.get("name") != "factoholic-script-engine" or plugin.get("version") != "0.1.0":
        raise ValueError("plugin.json identity or version does not match this package.")
    if not re.fullmatch(r"\d+\.\d+\.\d+", plugin["version"]):
        raise ValueError("plugin.json version must use strict semantic-version syntax.")
    interface = plugin.get("extensions", {}).get("com.openai", {}).get("interface", {})
    if len(interface.get("shortDescription", "")) > 30:
        raise ValueError("The OpenAI shortDescription must be at most 30 characters.")
    required_interface = ("displayName", "shortDescription", "longDescription", "developerName", "category")
    if any(not isinstance(interface.get(field), str) or not interface[field].strip() for field in required_interface):
        raise ValueError("plugin.json is missing a required OpenAI interface field.")
    if not isinstance(interface.get("capabilities"), list) or any(not isinstance(item, str) or not item.strip() for item in interface["capabilities"]):
        raise ValueError("plugin.json interface.capabilities must be an array of nonblank strings.")
    if mcp.get("$schema") != "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json":
        raise ValueError("mcp.json has the wrong Agent Plugins MCP schema declaration.")
    if set(mcp) != {"$schema", "mcpServers"}:
        raise ValueError("mcp.json must contain only $schema and mcpServers at the root.")
    servers = mcp.get("mcpServers", {})
    server = servers.get("factoholic-script-engine", {})
    if server.get("type") != "stdio" or not server.get("command") or not server.get("args"):
        raise ValueError("mcp.json does not define the expected local stdio server.")
    if set(server) - {"type", "command", "args", "env", "cwd"}:
        raise ValueError("mcp.json contains unsupported stdio server fields.")
    if not str(server.get("cwd", "")).startswith("${PLUGIN_ROOT}/server"):
        raise ValueError("mcp.json must use the bundled server directory as its working directory.")
    skill_files = list((root / "skills").glob("*/SKILL.md"))
    if not skill_files:
        raise ValueError("The plugin needs at least one usable skill or MCP server.")
    module_files = list((root / "server" / "factoholic_script_engine").glob("*.py"))
    if not module_files or not (root / "server" / "WORKFLOW_CONTRACT.md").is_file():
        raise ValueError("The local server package or workflow contract is missing.")
    if (root / "server" / "scripts").exists() or (root / "server" / "MASTER_INSTRUCTIONS.md").exists():
        raise ValueError("The package must not contain transcript data or a stale Master Instructions copy.")
    skill_text = skill_files[0].read_text(encoding="utf-8")
    if not skill_text.startswith("---\nname: ") or "\ndescription: " not in skill_text:
        raise ValueError("The plugin skill needs valid name and description frontmatter.")
    allowed_roots = {"plugin.json", "mcp.json", "skills", "server"}
    files = sorted(
        path for path in root.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"
    )
    unexpected = sorted(path.relative_to(root).as_posix() for path in files if path.relative_to(root).parts[0] not in allowed_roots)
    if unexpected:
        raise ValueError(f"Plugin source directory has unexpected files: {unexpected}")
    return files


def write_zip(root: Path, output: Path, files: list[Path]) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in files:
            archive.write(path, path.relative_to(root).as_posix())


def verify_zip(path: Path) -> list[str]:
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        required = {"plugin.json", "mcp.json", "server/WORKFLOW_CONTRACT.md"}
        missing = sorted(required - set(names))
        if missing:
            raise ValueError(f"Plugin ZIP is missing files: {missing}")
        if any(name.startswith("scripts/") or "/scripts/" in name for name in names):
            raise ValueError("Plugin ZIP unexpectedly contains the transcript corpus.")
        if any(name == "MASTER_INSTRUCTIONS.md" or name.endswith("/MASTER_INSTRUCTIONS.md") for name in names):
            raise ValueError("Plugin ZIP must load the editable Master Instructions from the configured local path.")
        if not any(name.startswith("skills/") and name.endswith("/SKILL.md") for name in names):
            raise ValueError("Plugin ZIP is missing its workflow skill.")
        return names


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--source-dir", type=Path, help="Use an existing prepared plugin directory.")
    args = parser.parse_args()
    output = args.output.resolve()
    if args.source_dir:
        source = args.source_dir.resolve()
    else:
        source = ROOT / "dist" / "chatgpt-plugin-source"
        copy_package(source)
    files = validate_package(source)
    write_zip(source, output, files)
    names = verify_zip(output)
    print(f"Created {output} ({output.stat().st_size} bytes; {len(names)} files)")
    print("Verified: local stdio configuration and workflow skill included; dataset and Master Instructions excluded.")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, zipfile.BadZipFile) as error:
        print(f"ChatGPT plugin build failed: {error}", file=sys.stderr)
        raise SystemExit(1) from error
