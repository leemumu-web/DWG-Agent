# Automation execution contracts

This directory is deliberately non-executable. It replaces one-line `agents`, `mcp_client` and
`integrations/zwcad` placeholder modules with a machine-readable capability status and written
boundary.

The target architecture still requires a real Agent runtime, MCP client/tool adapter and an
authenticated Windows Node Agent/CAM Runner with leases and fencing tokens. Until those products
exist, no Celery task is registered under `tasks_agent`, `tasks_cad` or `tasks_dispatch`, and no
configuration value may be interpreted as proof of implementation.
