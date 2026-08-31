"""Standalone Tekla XBOX closed-box DXF splitting Stage.

Self-contained by design: the vendored ``box`` core, its four top-level
helper modules and ``manufacturing_decision`` are byte-pinned copies; this
package never imports the BH/BOX ``steel_dxf_split`` distribution, and the
original package stays untouched.
"""

from __future__ import annotations

__version__ = "0.1.0"

from .contracts import (
    XBOX_EXPORT_PROFILE,
    XboxSourceContract,
    XboxSplitError,
    member_name,
)
from .release import (
    load_verified_xbox_release_attestation,
    production_implementation_fingerprint,
    write_xbox_protected_runtime_manifest,
    write_xbox_release_attestation,
)

__all__ = [
    "XBOX_EXPORT_PROFILE",
    "__version__",
    "load_verified_xbox_release_attestation",
    "member_name",
    "production_implementation_fingerprint",
    "write_xbox_protected_runtime_manifest",
    "write_xbox_release_attestation",
    "XboxSourceContract",
    "XboxSplitError",
]
