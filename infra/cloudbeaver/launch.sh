#!/bin/bash
set -euo pipefail

workspace="/opt/cloudbeaver/workspace"
configuration="${workspace}/GlobalConfiguration/.dbeaver"

mkdir -p "${workspace}/.data" "$configuration"
cp /opt/dwg-cloudbeaver/data-sources.json "${configuration}/data-sources.json"
cp /opt/dwg-cloudbeaver/data-sources-permissions.json \
  "${configuration}/data-sources-permissions.json"
chown -R "${DBEAVER_UID}:${DBEAVER_GID}" "$workspace"

exec /opt/cloudbeaver/launch-product.sh "$@"
