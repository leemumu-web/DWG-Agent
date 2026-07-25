# CloudBeaver deployment

This partition preconfigures the Apache-2.0 CloudBeaver Community database
navigator behind the platform Nginx gateway.

- `runtime.conf` replaces the image server configuration, enables trusted
  reverse-proxy authentication and disables anonymous/custom connections.
- `launch.sh` creates the named-volume workspace structure before copying the
  connection and permission templates, then hands control to the image launcher.
- `initial-data.conf` declares the `dba-admin` and `dba-reader` teams.
- `data-sources.json` declares separate scoped MySQL connections.
- `data-sources-permissions.json` maps each team to only its matching connection.

No public port is published. Credentials are environment substitutions from the
ignored `.env.docker`; they must not be committed. Database grants remain the
authoritative write boundary even if UI configuration drifts.
