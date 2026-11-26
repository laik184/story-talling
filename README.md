# FactoHolic Script Engine

Local-first MCP server for the prepared Markdown transcript corpus in `scripts/`. Python and SQLite do the parsing, incremental indexing, full-text retrieval, provenance checks, and persistent workflow storage. The connected model provides semantic analysis and writing; the server does not claim that keyword matches are semantic judgments.

## What is here

- `MASTER_INSTRUCTIONS.md`: editable copy of the supplied GeoAxis writing instructions, kept separate from the technical contract.
- `WORKFLOW_CONTRACT.md`: instructions for how a connected model uses the MCP safely and in the requested sequence.
- `scripts/`: original source files. The indexer reads them and never edits them.
- `factoholic_script_engine/`: MCP transport, parser, index, validation, and workflow services.
- SQLite data and session state: `%LOCALAPPDATA%\factoholic-script-engine` by default, outside the transcript directory.

## Install and run locally

Python 3.11 or later is required. From this folder:

```powershell
uv sync --extra dev
uv run factoholic-script-engine
```

The server uses MCP `stdio` and does not open a network listener. On each sync it reads and hashes discovered Markdown files, then reparses only files whose content changed. This detects edits even if file size and modification time were preserved. To point to another authorized folder, set `FACTOHOLIC_DATASET_DIR`. To move local database/session state, set `FACTOHOLIC_STATE_DIR`.

Useful commands:

```powershell
uv run factoholic-script-engine --help
uv run pytest
```

No paid API or model key is used. A client may send the selected transcript content to its cloud model when it reads tool results; review that client's data controls before connecting the corpus.

## Claude Desktop (Windows)

This project includes a Claude Desktop Extension bundle. Build it from the project root:

```powershell
uv run python tools/build_mcpb.py
```

The output is `dist/factoholic-script-engine-0.1.0.mcpb`. The build uses the official MCPB packer through `npx` if `mcpb` is not already on `PATH`, so Node.js/npm is needed to build from source. The bundle includes the Python MCP server and workflow contract. It does not include the `scripts/` corpus or `MASTER_INSTRUCTIONS.md`; the extension asks for their local paths. Claude's current Desktop Extensions flow requires the `uv` executable to launch this package.

In Claude Desktop, open **Settings → Desktop app → Manage Extensions → Advanced settings**, turn on **Extension Developer**, then choose **Install Extension**. Select the generated `.mcpb` file. Set the transcript folder, state folder and editable Master Instructions file in the extension's configuration.

The package uses the current MCP Bundle manifest format. Claude Desktop is not installed on this machine, so installation in the live client was not tested. A manual config example for older Desktop versions is available at `clients/claude_desktop_config.example.json`.

## ChatGPT Desktop

OpenAI's current documentation describes local MCP apps as a **Desktop-only plugin** capability. Create a private plugin in ChatGPT's Plugin Creator and run its MCP app on the same computer. Custom MCP server connections through ChatGPT web use HTTPS or Secure MCP Tunnel; a local process is not directly connectable from web.

Build the ChatGPT Agent Plugins 1.0 package with:

```powershell
uv run python tools/build_chatgpt_plugin.py
```

The output is `dist/factoholic-script-engine-chatgpt-0.1.0.zip`. It bundles the local MCP server and workflow skill, while excluding `scripts/` and `MASTER_INSTRUCTIONS.md`. Its `mcp.json` uses the current workspace paths and the bundled server directory. If you move the project or virtual environment, update those paths before rebuilding.

In ChatGPT Desktop, install **Plugin Creator**, start a chat with `@plugin-creator`, and give it the prepared request in `clients/CHATGPT_PLUGIN_CREATOR_PROMPT.md`. That prompt configures a local MCP app to launch the workspace server:

- Command: `C:\\factoholic\\.venv\\Scripts\\python.exe`
- Arguments: `-m factoholic_script_engine.server`
- Working directory: `C:\\factoholic`
- Environment: `FACTOHOLIC_DATASET_DIR=C:\\factoholic\\scripts`; `FACTOHOLIC_STATE_DIR=C:\\Users\\Administrator\\AppData\\Local\\factoholic-script-engine`; `FACTOHOLIC_MASTER_INSTRUCTIONS=C:\\factoholic\\MASTER_INSTRUCTIONS.md`; `FACTOHOLIC_WORKFLOW_CONTRACT=C:\\factoholic\\WORKFLOW_CONTRACT.md`

The prepared Plugin Creator request is in `clients/CHATGPT_PLUGIN_CREATOR_PROMPT.md`. If your workspace offers plugin ZIP upload and your role has permission, use **Admin console → Plugins → Add → Upload plugin**. A local stdio plugin runs on ChatGPT Desktop; uploading a package does not make its local server available on web or mobile. The Windows Start menu entry named “ChatGPT” resolved in the available package inventory to `OpenAI.Codex` version `26.1120.6357.0`; live Plugin Creator compatibility could not be verified from that package inventory. Claude Desktop was not installed. Do not use a public tunnel for this corpus unless you explicitly choose and secure that deployment.

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `FACTOHOLIC_DATASET_DIR` | `<project>/scripts` | Authorized Markdown corpus root. Symlinks are skipped and path traversal is not accepted by tools. |
| `FACTOHOLIC_STATE_DIR` | `%LOCALAPPDATA%\factoholic-script-engine` | Local SQLite database and session state. |
| `FACTOHOLIC_MASTER_INSTRUCTIONS` | `<project>/MASTER_INSTRUCTIONS.md` | Editable writing instructions, re-read and hashed on each retrieval. |
| `FACTOHOLIC_WORKFLOW_CONTRACT` | `<project>/WORKFLOW_CONTRACT.md` | Technical workflow contract. |
| `FACTOHOLIC_DATASET_CREATOR` | unset | Operator-verified default creator for files without explicit creator metadata. Required before humor search can attribute such files to FactoHolic. |

Creator attribution is not inferred from titles, ranks, or style. The supplied sample transcripts do not carry an explicit creator field. Only set `FACTOHOLIC_DATASET_CREATOR=FactoHolic` after confirming the provenance of the full configured collection.

## MCP surface

The server exposes tools for instruction/workflow retrieval, dataset overview and refresh, corpus and caption search, full transcript reading, Script DNA retrieval, evidence-backed language/humor search, reference-analysis saving, persistent corpus-learning batches, separate delivery/AI-inspection/analysis/evidence-validation coverage, pause/resume, topic-specific learning search, language-bank retrieval, source validation, session/context management, gated outline saving and explicit approval, gated script saving, deterministic validation, and audit reporting.

Resources:

- `factoholic://instructions/master`
- `factoholic://workflow/contract`
- `factoholic://dataset/overview`
- `factoholic://transcript/{transcript_id}`

Indexing does not count as AI reading. Every new outline and every new script session creates its own fresh full-corpus reading cycle; script cycles are also required after outline approval. `get_next_corpus_learning_batch` delivers original transcript contents in context-limited resumable batches. The connected AI submits semantic observations and topic-specific patterns; the server checks cited evidence against captions. Status and audit tools distinguish delivered files, AI-reported inspected files, submitted analyses, evidence-validated analyses, pending files, and failures. The hard output gate blocks saving until all currently accessible files in that cycle have passed delivery, attestation, analysis, evidence validation, and source-hash checks. Existing appended AI analysis stays separate from captions and must be freshly reviewed against them.

## Verification

Run `uv run pytest`. The tests cover the real Markdown parser/indexer and persistence/validation behavior. The MCP integration smoke test launches the actual stdio server and uses the real local `scripts/` corpus to search, read, approve an outline, save a script, and validate its evidence. No test edits source transcripts.

## Official references checked

- [Official MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk): Python 3.11+, standard `stdio` and Streamable HTTP transports; this project uses local `stdio`.
- [Claude Help: local MCP servers on Claude Desktop](https://support.claude.com/en/articles/10949351-getting-started-with-local-mcp-servers-on-claude-desktop).
- [OpenAI Help: Plugins in ChatGPT](https://help.openai.com/en/articles/20001256-plugins-in-chatgpt): local MCP apps run on ChatGPT Desktop; remote custom MCP connections use HTTPS or Secure MCP Tunnel.
- [OpenAI Help: connecting custom MCP servers](https://help.openai.com/en/articles/12584461-connecting-custom-mcp-servers-in-chatgpt).
