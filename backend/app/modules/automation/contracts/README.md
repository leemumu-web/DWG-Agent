# Automation execution contracts

`schemas.py` defines the frozen capability-status model. `interface.py` returns
the current Agent/MCP/ZWCAD capability set and the draft Windows node endpoint
contract without performing network or CAD execution.

This directory is deliberately non-executable. It replaces one-line `agents`, `mcp_client` and
`integrations/zwcad` placeholder modules with a machine-readable capability status and written
boundary.

The target architecture still requires a real Agent runtime, MCP client/tool adapter and an
authenticated Windows Node Agent/CAM Runner with leases and fencing tokens. Until those products
exist, no Celery task is registered under `tasks_agent`, `tasks_cad` or `tasks_dispatch`, and no
configuration value may be interpreted as proof of implementation.

This boundary must not gain a fake client or success path: an implementation
requires authentication, timeout/recovery, persistence and external acceptance
evidence in the owning product.
