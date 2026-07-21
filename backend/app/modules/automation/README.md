# Automation module

This module separates delivered persistence/API foundations from execution contracts that are
not implemented yet.

- `agent/` owns the `agent_memory`, `agent_runs` and `agent_run_steps` MySQL tables, session-memory
  helpers and the existing HTTP routes. With `AGENT_ENABLED=false`, all four routes deliberately
  return `503 AGENT_DISABLED`.
- `contracts/` describes the intended Agent/MCP/ZWCAD/Windows boundaries. It does not contain an
  executor, Celery task, network client or CAD adapter.

The reserved `agent` and `cad` queue names are deployment seams only. Queue presence and settings
such as `MODEL_API_KEY`, `MCP_CAD_COMMAND` or `CAD_WORKER_API_BASE` do not make these capabilities
available.
