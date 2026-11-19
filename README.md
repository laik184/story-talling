# FactoHolic MCP

Local stdio MCP server and its deterministic corpus-search, evidence, profile-validation, and Writer DNA modules. Runtime corpus files and SQLite indexes are intentionally not included; place this `script_dna` folder inside the existing `C:\factoholic` workspace, which provides `scripts/` and the local indexes.

## Run locally

From `C:\factoholic\script_dna`, install `requirements.txt` and run `python mcp_server.py`. The server uses stdio only and does not start a public HTTP endpoint or tunnel. Set `PYTHONPATH` to the `script_dna` folder when launching it from an MCP client.

The corpus and Writer DNA databases are maintained by the local FactoHolic workspace. Do not commit transcript files, SQLite databases, generated evidence, or reports to this repository.
