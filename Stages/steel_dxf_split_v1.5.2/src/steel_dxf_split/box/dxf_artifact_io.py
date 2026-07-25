"""Deterministic DXF serialization shared by direct and derived artifacts."""

from __future__ import annotations

import uuid
from pathlib import Path
from threading import RLock

import ezdxf
from ezdxf._options import options as ezdxf_options

from . import __version__

_DXF_EXPORT_LOCK = RLock()
_GUID_NAMESPACE = uuid.UUID("52ba3378-19b7-522e-b28d-f191a1202e48")


def _replace_header_guid(
    pairs: list[tuple[str, str]],
    variable: str,
    value: str,
) -> None:
    for index in range(0, len(pairs) - 1):
        code, name = pairs[index]
        if code.strip() != "9" or name.strip() != variable:
            continue
        if pairs[index + 1][0].strip() != "2":
            raise ValueError(f"unexpected DXF group code for {variable}")
        pairs[index + 1] = (pairs[index + 1][0], value)
        return
    raise ValueError(f"DXF header variable {variable} was not found")


def _canonicalize_classes_section(pairs: list[tuple[str, str]]) -> None:
    start: int | None = None
    end: int | None = None
    for index in range(len(pairs) - 1):
        if (
            pairs[index][0].strip() == "0"
            and pairs[index][1].strip() == "SECTION"
            and pairs[index + 1][0].strip() == "2"
            and pairs[index + 1][1].strip() == "CLASSES"
        ):
            start = index + 2
            break
    if start is None:
        return
    for index in range(start, len(pairs)):
        if pairs[index][0].strip() == "0" and pairs[index][1].strip() == "ENDSEC":
            end = index
            break
    if end is None:
        raise ValueError("DXF CLASSES section has no ENDSEC record")
    records: list[list[tuple[str, str]]] = []
    current: list[tuple[str, str]] = []
    for pair in pairs[start:end]:
        if pair[0].strip() == "0" and pair[1].strip() == "CLASS":
            if current:
                records.append(current)
            current = [pair]
        else:
            current.append(pair)
    if current:
        records.append(current)

    def class_name(record: list[tuple[str, str]]) -> str:
        return next(
            (value.strip() for code, value in record if code.strip() == "1"), ""
        )

    flattened = [
        pair
        for record in sorted(records, key=lambda item: (class_name(item), item))
        for pair in record
    ]
    rebuilt = pairs[:start] + flattened + pairs[end:]
    pairs[:] = rebuilt


def _ascii_dxf_unicode_transport(value: str) -> str:
    """Encode non-ASCII DXF strings as AutoCAD ``\\U+XXXX`` escapes.

    An R2007 DXF is valid UTF-8, but some Windows CAD importers still follow
    the legacy ``$DWGCODEPAGE`` header.  Restricting delivered bytes to ASCII
    makes the UTF-8 and legacy code-page interpretations identical, while the
    standardized DXF Unicode escape keeps the displayed text unchanged.
    """

    if value.isascii():
        return value
    encoded: list[str] = []
    for character in value:
        if character.isascii():
            encoded.append(character)
            continue
        units = character.encode("utf-16-be")
        encoded.extend(
            f"\\U+{int.from_bytes(units[index : index + 2], 'big'):04X}"
            for index in range(0, len(units), 2)
        )
    return "".join(encoded)


def _write_windows_cad_dxf(path: Path, pairs: list[tuple[str, str]]) -> None:
    """Write the same LF/ASCII DXF transport as the proven BH writer."""

    rendered = (
        "\n".join(
            _ascii_dxf_unicode_transport(item) for pair in pairs for item in pair
        )
        + "\n"
    )
    raw = rendered.encode("ascii", "strict")
    if not raw.isascii() or b"\r" in raw or b"\n" not in raw:
        raise ValueError("production DXF is not an ASCII LF transport")
    path.write_bytes(raw)


def save_deterministic_dxf(
    document: ezdxf.document.Drawing,
    path: Path,
    *,
    artifact_fingerprint: str,
) -> None:
    """Save stable bytes with purpose-bound deterministic R2007 GUIDs."""

    if not artifact_fingerprint:
        raise ValueError("deterministic DXF artifact fingerprint may not be empty")
    with _DXF_EXPORT_LOCK:
        previous = ezdxf_options.write_fixed_meta_data_for_testing
        metadata = document.ezdxf_metadata()
        identity = f"box-dxf-split {__version__} deterministic"
        metadata["CREATED_BY_EZDXF"] = identity
        metadata["WRITTEN_BY_EZDXF"] = identity
        ezdxf_options.write_fixed_meta_data_for_testing = True
        try:
            document.saveas(path)
        finally:
            ezdxf_options.write_fixed_meta_data_for_testing = previous
        lines = path.read_text(encoding="utf-8").splitlines()
        if len(lines) % 2:
            raise ValueError("DXF text does not contain complete group-code pairs")
        pairs = [(lines[index], lines[index + 1]) for index in range(0, len(lines), 2)]
        fingerprint_guid = (
            "{"
            + str(
                uuid.uuid5(
                    _GUID_NAMESPACE,
                    artifact_fingerprint + ":document",
                )
            ).upper()
            + "}"
        )
        version_guid = (
            "{"
            + str(
                uuid.uuid5(
                    _GUID_NAMESPACE,
                    artifact_fingerprint + f":version:{__version__}",
                )
            ).upper()
            + "}"
        )
        _replace_header_guid(pairs, "$FINGERPRINTGUID", fingerprint_guid)
        _replace_header_guid(pairs, "$VERSIONGUID", version_guid)
        _canonicalize_classes_section(pairs)
        _write_windows_cad_dxf(path, pairs)
