from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import shutil
from typing import Any
from uuid import uuid4
from xml.etree import ElementTree

import ezdxf

from .bh_trace import TraceEvent
from .layered_dxf import render_scene_dxf
from .layered_scene import StageScene, scene_from_event
from .layered_svg import render_scene_svg


ARCHIVE_CATEGORIES = frozenset(
    {"intermediate", "final", "reference", "comparison", "corpus"}
)
_SAFE_COMPONENT = re.compile(r"^[A-Za-z0-9_.\-\u4e00-\u9fff]+$")


def _safe_component(value: str, label: str) -> str:
    if value in {"", ".", ".."} or not _SAFE_COMPONENT.fullmatch(value):
        raise ValueError(f"Unsafe {label} path component: {value!r}")
    return value


def _sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True, slots=True)
class ArtifactItem:
    sample_id: str
    stage_id: str
    artifact_id: str
    sequence: int
    status: str
    hypothesis_id: str | None
    category: str
    dxf_path: Path
    svg_path: Path
    json_path: Path | None
    dxf_sha256: str
    svg_sha256: str
    json_sha256: str | None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        for key in ("dxf_path", "svg_path", "json_path"):
            value = payload[key]
            payload[key] = value.as_posix() if value is not None else None
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> ArtifactItem:
        data = dict(payload)
        for key in ("dxf_path", "svg_path", "json_path"):
            if data.get(key) is not None:
                data[key] = Path(data[key])
        return cls(**data)


@dataclass(frozen=True, slots=True)
class ArchiveValidationReport:
    missing: list[Path]
    hash_mismatches: list[str]
    mirror_mismatches: list[str]
    parse_errors: list[str]
    orphans: list[Path]

    @property
    def ok(self) -> bool:
        return not (
            self.missing
            or self.hash_mismatches
            or self.mirror_mismatches
            or self.parse_errors
            or self.orphans
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "missing": [item.as_posix() for item in self.missing],
            "hash_mismatches": list(self.hash_mismatches),
            "mirror_mismatches": list(self.mirror_mismatches),
            "parse_errors": list(self.parse_errors),
            "orphans": [item.as_posix() for item in self.orphans],
        }


class LayeredArchive:
    def __init__(self, root: Path, items: list[ArtifactItem] | None = None):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.items: list[ArtifactItem] = list(items or ())

    def _paths(
        self, scene: StageScene, category: str, filename: str
    ) -> tuple[Path, Path, Path]:
        if category not in ARCHIVE_CATEGORIES:
            raise ValueError(f"Unknown archive category: {category}")
        sample_id = _safe_component(scene.sample_id, "sample")
        stage_id = _safe_component(scene.stage_id, "stage")
        filename = _safe_component(filename, "artifact filename")
        if scene.hypothesis_id is not None:
            hypothesis = _safe_component(scene.hypothesis_id, "hypothesis")
        else:
            hypothesis = None

        if category == "intermediate":
            suffix = Path(sample_id) / stage_id
            if hypothesis:
                suffix /= hypothesis
            suffix /= filename
        elif category == "corpus":
            suffix = Path(filename)
        else:
            suffix = Path(sample_id) / filename

        dxf_path = Path("dxf") / category / suffix.with_suffix(".dxf")
        svg_path = Path("svg") / category / suffix.with_suffix(".svg")
        if category == "corpus":
            json_path = Path("json") / "corpus" / suffix.with_suffix(".json")
        else:
            json_suffix = Path(sample_id) / stage_id
            if hypothesis:
                json_suffix /= hypothesis
            json_path = Path("json") / json_suffix / Path(filename).with_suffix(".json")
        return dxf_path, svg_path, json_path

    def _temporary(self, target: Path) -> Path:
        return target.with_name(f".{target.stem}.{uuid4().hex}.tmp{target.suffix}")

    def _write_json_atomic(self, path: Path, payload: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._temporary(path)
        try:
            temporary.write_text(
                json.dumps(
                    payload,
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                    allow_nan=False,
                )
                + "\n",
                encoding="utf-8",
            )
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    def write_event(self, event: TraceEvent) -> ArtifactItem:
        return self.write_scene_pair(
            scene_from_event(event),
            category="intermediate",
            json_payload=event.to_dict(),
        )

    def write_scene_pair(
        self,
        scene: StageScene,
        *,
        category: str,
        dxf_source: Path | None = None,
        json_payload: Any | None = None,
        filename: str | None = None,
    ) -> ArtifactItem:
        stem = filename or f"{scene.sequence:04d}-{scene.artifact_id}"
        dxf_relative, svg_relative, json_relative = self._paths(scene, category, stem)
        dxf_path = self.root / dxf_relative
        svg_path = self.root / svg_relative
        json_path = self.root / json_relative
        dxf_path.parent.mkdir(parents=True, exist_ok=True)
        svg_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_dxf = self._temporary(dxf_path)
        temporary_svg = self._temporary(svg_path)
        try:
            if dxf_source is None:
                render_scene_dxf(scene, temporary_dxf)
            else:
                shutil.copyfile(dxf_source, temporary_dxf)
            render_scene_svg(scene, temporary_svg)
            dxf_document = ezdxf.readfile(temporary_dxf)
            audit = dxf_document.audit()
            if audit.errors:
                raise ValueError(
                    f"DXF audit failed for {dxf_relative.as_posix()}: {audit.errors}"
                )
            ElementTree.parse(temporary_svg)
            os.replace(temporary_dxf, dxf_path)
            os.replace(temporary_svg, svg_path)
        finally:
            temporary_dxf.unlink(missing_ok=True)
            temporary_svg.unlink(missing_ok=True)

        stored_json_path: Path | None = None
        json_digest: str | None = None
        if json_payload is not None:
            self._write_json_atomic(json_path, json_payload)
            stored_json_path = json_relative
            json_digest = _sha256(json_path)

        item = ArtifactItem(
            sample_id=scene.sample_id,
            stage_id=scene.stage_id,
            artifact_id=scene.artifact_id,
            sequence=scene.sequence,
            status=scene.status,
            hypothesis_id=scene.hypothesis_id,
            category=category,
            dxf_path=dxf_relative,
            svg_path=svg_relative,
            json_path=stored_json_path,
            dxf_sha256=_sha256(dxf_path),
            svg_sha256=_sha256(svg_path),
            json_sha256=json_digest,
        )
        self.items = [
            existing
            for existing in self.items
            if not (
                existing.category == item.category
                and existing.dxf_path == item.dxf_path
                and existing.svg_path == item.svg_path
            )
        ]
        self.items.append(item)
        self.items.sort(key=lambda entry: (entry.sample_id, entry.sequence, entry.category))
        return item

    def validate(self) -> ArchiveValidationReport:
        missing: list[Path] = []
        hash_mismatches: list[str] = []
        mirror_mismatches: list[str] = []
        parse_errors: list[str] = []
        declared: set[Path] = set()
        for item in self.items:
            declared.update({item.dxf_path, item.svg_path})
            if item.json_path is not None:
                declared.add(item.json_path)
            dxf_suffix = item.dxf_path.with_suffix("").parts[1:]
            svg_suffix = item.svg_path.with_suffix("").parts[1:]
            if dxf_suffix != svg_suffix:
                mirror_mismatches.append(
                    f"{item.dxf_path.as_posix()} != {item.svg_path.as_posix()}"
                )
            expected = (
                (item.dxf_path, item.dxf_sha256),
                (item.svg_path, item.svg_sha256),
                *(
                    ((item.json_path, item.json_sha256),)
                    if item.json_path
                    else ()
                ),
            )
            for relative, digest in expected:
                path = self.root / relative
                if not path.exists():
                    missing.append(relative)
                elif digest is not None and _sha256(path) != digest:
                    hash_mismatches.append(relative.as_posix())
            dxf_path = self.root / item.dxf_path
            if dxf_path.exists():
                try:
                    if ezdxf.readfile(dxf_path).audit().errors:
                        parse_errors.append(f"DXF audit: {item.dxf_path.as_posix()}")
                except Exception as exc:
                    parse_errors.append(f"DXF parse: {item.dxf_path.as_posix()}: {exc}")
            svg_path = self.root / item.svg_path
            if svg_path.exists():
                try:
                    ElementTree.parse(svg_path)
                except Exception as exc:
                    parse_errors.append(f"SVG parse: {item.svg_path.as_posix()}: {exc}")

        actual: set[Path] = set()
        for directory in ("dxf", "svg", "json"):
            root = self.root / directory
            if root.exists():
                actual.update(path.relative_to(self.root) for path in root.rglob("*") if path.is_file())
        orphans = sorted(actual - declared, key=lambda path: path.as_posix())
        return ArchiveValidationReport(
            missing=sorted(set(missing), key=lambda path: path.as_posix()),
            hash_mismatches=sorted(set(hash_mismatches)),
            mirror_mismatches=sorted(set(mirror_mismatches)),
            parse_errors=sorted(set(parse_errors)),
            orphans=orphans,
        )
