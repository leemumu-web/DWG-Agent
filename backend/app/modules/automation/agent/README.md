# Agent data foundation

`models/` owns the three delivered tables, `memory.py` enforces bounded session
history, `schemas.py` defines run/step/memory contracts, and `routes.py` exposes
the authenticated HTTP boundary.

Delivered today:

- MySQL models for runs, steps and bounded session history;
- tested TTL and message-count enforcement for session history;
- authenticated, owner/project-scoped HTTP read contracts;
- an explicit `503 AGENT_DISABLED` response while `AGENT_ENABLED=false`.

Not delivered: an Agent executor, LLM call, LangGraph graph, MCP tool registry, task dispatch or
output production. Enabling the setting alone does not provide those capabilities; the current
write route only creates the existing queued metadata row and is retained for contract
compatibility.
