Create a private, Desktop-only ChatGPT plugin named **FactoHolic Script Engine** with one local MCP app. The local server is already implemented in `C:\factoholic` and exposes MCP tools/resources over stdio.

A ready Agent Plugins 1.0 ZIP is built at `C:\factoholic\dist\factoholic-script-engine-chatgpt-0.1.0.zip`. It bundles the server code, local stdio configuration, workflow skill, and contract. It does not contain `scripts/` or `MASTER_INSTRUCTIONS.md`; use their local paths below. If using the ZIP, retain its local MCP configuration and do not replace it with a hosted server.

Configure the app to launch:

- Command: `C:\factoholic\.venv\Scripts\python.exe`
- Arguments: `-m factoholic_script_engine.server`
- Working directory: `C:\factoholic`
- Environment:
  - `FACTOHOLIC_DATASET_DIR=C:\factoholic\scripts`
  - `FACTOHOLIC_STATE_DIR=C:\Users\Administrator\AppData\Local\factoholic-script-engine`
  - `FACTOHOLIC_MASTER_INSTRUCTIONS=C:\factoholic\MASTER_INSTRUCTIONS.md`
  - `FACTOHOLIC_WORKFLOW_CONTRACT=C:\factoholic\WORKFLOW_CONTRACT.md`

Use the local MCP app capability supported by ChatGPT Desktop. Do not create a remote server, public tunnel, or web-hosted dataset. Keep the plugin private and Desktop-only. Describe the tools as local reference search, resumable full-corpus learning, and workflow tools. Explain that transcript results are sent to the connected model when it reads them, and every outline or script save requires its own complete fresh reading cycle. Do not add a UI or extra actions that write to the original `scripts` folder.
