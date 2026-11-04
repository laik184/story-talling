# FactoHolic Storytelling MCP

Local stdio adapter exposing seven read-only storytelling tools.

Run inside the existing C:\factoholic workspace with Python 3.11 or newer. The adapter imports local engine modules from 02_research_engine and 04_creative_engine; those data and engine files are not included in this repository.

Install: python -m pip install -r requirements.txt
Set PYTHONPATH to C:\factoholic\14_mcp_server, then run: python -m factoholic_mcp --transport stdio

The server makes no model or paid API calls. It exposes stdio only and creates no HTTP listener, public endpoint, or tunnel. Corpus and database files are not included.
