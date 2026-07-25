from __future__ import annotations

from dataclasses import dataclass
from html import escape
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
from typing import Any
from urllib.parse import unquote, urlsplit

from . import __version__
from .bh_trace import STAGE_REGISTRY


@dataclass(frozen=True, slots=True)
class SiteValidationReport:
    missing: list[str]
    external: list[str]
    escaping: list[str]

    @property
    def ok(self) -> bool:
        return not (self.missing or self.external or self.escaping)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "missing": list(self.missing),
            "external": list(self.external),
            "escaping": list(self.escaping),
        }


@dataclass(slots=True)
class _ArtifactView:
    category: str
    stage_id: str
    artifact_id: str
    sequence: int
    status: str
    hypothesis_id: str | None
    dxf_path: str | None = None
    svg_path: str | None = None
    json_path: str | None = None
    title_zh: str = ""
    summary_zh: str = ""


def _safe_sample_id(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_.\-\u4e00-\u9fff]+", value) or value in {".", ".."}:
        raise ValueError(f"Unsafe sample ID for site output: {value!r}")
    return value


def _infer_path_fields(path: str) -> tuple[str, str, str | None, str, int]:
    parts = Path(path).parts
    category = parts[1] if parts and parts[0] in {"dxf", "svg"} and len(parts) > 1 else "intermediate"
    stage_id = next((part for part in parts if re.match(r"^\d{2}_", part)), "00_input_provenance")
    stage_index = parts.index(stage_id) if stage_id in parts else -1
    hypothesis = (
        parts[stage_index + 1]
        if stage_index >= 0
        and stage_index + 1 < len(parts) - 1
        and parts[stage_index + 1].startswith("assembly-")
        else None
    )
    stem = Path(path).stem
    match = re.match(r"^(\d+)-(.+)$", stem)
    return category, stage_id, hypothesis, match.group(2) if match else stem, int(match.group(1)) if match else 0


def _load_explanation(root: Path, record: _ArtifactView) -> None:
    if not record.json_path:
        return
    path = root / record.json_path
    if not path.exists():
        return
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return
    record.title_zh = str(payload.get("title_zh", ""))
    record.summary_zh = str(payload.get("summary_zh", ""))


def _artifact_views(sample: dict[str, Any], artifact_root: Path) -> list[_ArtifactView]:
    grouped: dict[tuple[str, str, str | None, str, int], _ArtifactView] = {}
    for item in sample.get("artifacts", []):
        if isinstance(item, str):
            category, stage_id, hypothesis, artifact_id, sequence = _infer_path_fields(item)
            key = (category, stage_id, hypothesis, artifact_id, sequence)
            record = grouped.setdefault(
                key,
                _ArtifactView(category, stage_id, artifact_id, sequence, "observed", hypothesis),
            )
            suffix = Path(item).suffix.lower()
            if suffix == ".dxf":
                record.dxf_path = item
            elif suffix == ".svg":
                record.svg_path = item
            elif suffix == ".json":
                record.json_path = item
            continue
        stage_id = str(item.get("stage_id", "00_input_provenance"))
        category = str(item.get("category", "intermediate"))
        hypothesis = item.get("hypothesis_id")
        artifact_id = str(item.get("artifact_id", "artifact"))
        sequence = int(item.get("sequence", 0))
        key = (category, stage_id, hypothesis, artifact_id, sequence)
        grouped[key] = _ArtifactView(
            category=category,
            stage_id=stage_id,
            artifact_id=artifact_id,
            sequence=sequence,
            status=str(item.get("status", "observed")),
            hypothesis_id=str(hypothesis) if hypothesis is not None else None,
            dxf_path=item.get("dxf_path"),
            svg_path=item.get("svg_path"),
            json_path=item.get("json_path"),
        )
    result = sorted(
        grouped.values(),
        key=lambda item: (
            item.stage_id,
            item.hypothesis_id or "",
            item.sequence,
            item.category,
            item.artifact_id,
        ),
    )
    for record in result:
        _load_explanation(artifact_root, record)
    return result


def _relative_link(page: Path, artifact_root: Path, target: str) -> str:
    return Path(os.path.relpath(artifact_root / target, page.parent)).as_posix()


def _artifact_card(page: Path, artifact_root: Path, item: _ArtifactView) -> str:
    links = []
    for label, target in (("JSON", item.json_path), ("DXF", item.dxf_path), ("SVG", item.svg_path)):
        if target:
            links.append(
                f'<a href="{escape(_relative_link(page, artifact_root, target), quote=True)}">{label}</a>'
            )
    preview = ""
    if item.svg_path:
        preview = (
            '<object class="artifact-preview" type="image/svg+xml" '
            f'data="{escape(_relative_link(page, artifact_root, item.svg_path), quote=True)}">'
            "SVG 预览不可用</object>"
        )
    title = item.title_zh or item.artifact_id
    summary = item.summary_zh or "该产物由算法拥有此状态的函数直接发出。"
    return (
        f'<article class="artifact-card status-{escape(item.status)}" id="artifact-{escape(item.category)}-{item.sequence}-{escape(item.artifact_id)}">'
        f'<header><span class="sequence">#{item.sequence:04d}</span>'
        f'<h4>{escape(title)}</h4><span class="badge">{escape(item.category)} / {escape(item.status)}</span></header>'
        f'<p>{escape(summary)}</p>{preview}<nav class="artifact-links">{" ".join(links)}</nav></article>'
    )


def _sample_page(sample: dict[str, Any], site_root: Path, artifact_root: Path) -> None:
    sample_id = _safe_sample_id(str(sample["sample_id"]))
    page = site_root / "samples" / sample_id / "index.html"
    page.parent.mkdir(parents=True, exist_ok=True)
    records = _artifact_views(sample, artifact_root)
    candidates = sorted(
        {
            *[str(item) for item in sample.get("candidates", [])],
            *[item.hypothesis_id for item in records if item.hypothesis_id],
        }
    )
    selected = str(sample.get("selected_hypothesis", ""))
    sidebar = []
    sections = []
    stage_status = sample.get("stage_status", {})
    for stage in STAGE_REGISTRY:
        stage_number = stage.stage_id[:2]
        sidebar.append(
            f'<a href="#{escape(stage.stage_id)}"><span>{stage_number}</span>{escape(stage.title_zh)}</a>'
        )
        stage_records = [item for item in records if item.stage_id == stage.stage_id]
        content: list[str] = []
        if stage.stage_id == "05_candidate_lowering" and candidates:
            for candidate in candidates:
                candidate_records = [item for item in stage_records if item.hypothesis_id == candidate]
                cards = "".join(_artifact_card(page, artifact_root, item) for item in candidate_records)
                if not cards:
                    cards = '<p class="empty">该候选没有专用几何事件；状态由候选终态 JSON 说明。</p>'
                open_attribute = " open" if candidate == selected else ""
                content.append(
                    f'<details class="candidate"{open_attribute}><summary>{escape(candidate)}'
                    f' <span>{len(candidate_records)} 个产物</span></summary>{cards}</details>'
                )
            content.extend(
                _artifact_card(page, artifact_root, item)
                for item in stage_records
                if item.hypothesis_id is None
            )
        else:
            content.extend(_artifact_card(page, artifact_root, item) for item in stage_records)
        if not content:
            content.append('<p class="empty">此阶段为 N/A 或尚无语料级专用产物。</p>')
        status = str(stage_status.get(stage.stage_id, "corpus" if stage_number == "15" else "not_applicable"))
        sections.append(
            f'<section class="stage" id="{escape(stage.stage_id)}">'
            f'<header><span class="stage-number">{stage_number}</span><div><h2>{escape(stage.title_zh)}</h2>'
            f'<p>{escape(stage.description_zh)}</p></div><span class="badge">{escape(status)}</span></header>'
            + "".join(content)
            + "</section>"
        )
    css_link = Path(os.path.relpath(site_root / "assets/site.css", page.parent)).as_posix()
    js_link = Path(os.path.relpath(site_root / "assets/site.js", page.parent)).as_posix()
    index_link = Path(os.path.relpath(site_root / "index.html", page.parent)).as_posix()
    document = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{escape(sample_id)} 分层语义档案</title><link rel="stylesheet" href="{escape(css_link, quote=True)}"></head>
<body><header class="topbar"><a href="{escape(index_link, quote=True)}">← 语料总览</a><div><h1>{escape(sample_id)}</h1>
<p>自动拆板算法逐层语义、DXF/SVG 镜像产物与人工核验</p></div></header>
<div class="sample-layout"><aside class="stage-nav">{''.join(sidebar)}</aside><main>{''.join(sections)}</main></div>
<script src="{escape(js_link, quote=True)}"></script></body></html>
"""
    page.write_text(document, encoding="utf-8")


def _index_page(manifest: dict[str, Any], site_root: Path) -> None:
    samples = sorted(manifest.get("samples", []), key=lambda item: str(item["sample_id"]))
    headers = "".join(
        f'<th title="{escape(stage.description_zh, quote=True)}">{stage.stage_id[:2]} {escape(stage.title_zh)}</th>'
        for stage in STAGE_REGISTRY
    )
    rows = []
    for sample in samples:
        sample_id = _safe_sample_id(str(sample["sample_id"]))
        statuses = sample.get("stage_status", {})
        cells = []
        for stage in STAGE_REGISTRY:
            status = str(
                statuses.get(
                    stage.stage_id,
                    "observed" if stage.stage_id == "13_corpus_summary" and manifest.get("corpus_artifacts") else "not_applicable",
                )
            )
            count = sum(
                1
                for item in sample.get("artifacts", [])
                if isinstance(item, dict) and item.get("stage_id") == stage.stage_id
            )
            cells.append(
                f'<td class="status-{escape(status)}" data-status="{escape(status)}">{escape(status)}<small>{count}</small></td>'
            )
        rows.append(
            f'<tr data-sample="{escape(sample_id)}"><th><a href="samples/{escape(sample_id, quote=True)}/index.html">'
            f'{escape(sample_id)}</a></th>{"".join(cells)}</tr>'
        )
    corpus_links: list[str] = []
    index_page = site_root / "index.html"
    for artifact in manifest.get("corpus_artifacts", []):
        for label, key in (("阶段 13 JSON", "json_path"), ("阶段 13 DXF", "dxf_path"), ("阶段 13 SVG", "svg_path")):
            target = artifact.get(key)
            if target:
                corpus_links.append(
                    f'<a href="{escape(_relative_link(index_page, site_root.parent, target), quote=True)}">{label}</a>'
                )
    document = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>BH 分层语义语料</title><link rel="stylesheet" href="assets/site.css"></head><body>
<header class="hero"><p class="eyebrow">STEEL DXF SPLIT v{__version__}</p><h1>BH 拆板分层语义语料</h1>
<p>每个单元格对应真实算法阶段；点击样本查看 JSON、DXF、SVG 与人工核验。</p>
<nav class="artifact-links">{''.join(corpus_links)}</nav>
<label>筛选样本 <input id="sample-filter" type="search" placeholder="例如 BH-001"></label></header>
<main class="matrix-wrap"><table class="corpus-matrix"><thead><tr><th>样本</th>{headers}</tr></thead><tbody>{''.join(rows)}</tbody></table></main>
<section class="stage-key"><h2>固定阶段</h2>{''.join(f'<article><b>{s.stage_id[:2]} {escape(s.title_zh)}</b><p>{escape(s.description_zh)}</p></article>' for s in STAGE_REGISTRY)}</section>
<script src="assets/site.js"></script></body></html>
"""
    (site_root / "index.html").write_text(document, encoding="utf-8")


SITE_CSS = """
:root{color-scheme:light;--ink:#152238;--muted:#607086;--line:#d9e1ea;--paper:#f4f7fa;--card:#fff;--accent:#0d6b62}*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;background:var(--paper);color:var(--ink);font-family:Inter,"Noto Sans CJK SC","Source Han Sans CN",sans-serif}.hero,.topbar{padding:2rem clamp(1rem,4vw,4rem);background:#102a43;color:#fff}.hero h1,.topbar h1{margin:.25rem 0}.hero p,.topbar p{color:#d9e8f5}.eyebrow{letter-spacing:.18em;font-size:.75rem}.hero input{margin-left:.8rem;padding:.65rem;border:0;border-radius:.35rem}.matrix-wrap{overflow:auto;padding:1.25rem}.corpus-matrix{border-collapse:separate;border-spacing:3px;min-width:1700px;width:100%}.corpus-matrix th,.corpus-matrix td{padding:.55rem;border-radius:.3rem;background:#fff;text-align:center;font-size:.76rem}.corpus-matrix thead th{position:sticky;top:0;z-index:2}.corpus-matrix tbody th{position:sticky;left:0;z-index:1}.corpus-matrix small{display:block;color:var(--muted)}.status-observed,.status-selected{background:#d7f3e8!important}.status-not_applicable{background:#eef2f6!important;color:#697789}.status-failed,.status-rejected{background:#fee2e2!important;color:#991b1b}.stage-key{padding:1rem clamp(1rem,4vw,4rem);display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:.8rem}.stage-key h2{grid-column:1/-1}.stage-key article,.artifact-card,.stage{background:var(--card);border:1px solid var(--line);border-radius:.6rem;padding:1rem}.sample-layout{display:grid;grid-template-columns:220px minmax(0,1fr);gap:1rem;max-width:1600px;margin:auto;padding:1rem}.stage-nav{position:sticky;top:1rem;align-self:start;display:grid;gap:.25rem;max-height:95vh;overflow:auto}.stage-nav a{display:grid;grid-template-columns:2.2rem 1fr;padding:.55rem;color:var(--ink);text-decoration:none;border-radius:.35rem}.stage-nav a:hover{background:#e3edf5}.stage{margin-bottom:1rem}.stage>header,.artifact-card>header{display:flex;align-items:center;gap:.8rem}.stage>header div{flex:1}.stage h2,.artifact-card h4{margin:0}.stage-number{font-size:1.7rem;font-weight:800;color:var(--accent)}.badge{margin-left:auto;padding:.2rem .45rem;border-radius:2rem;background:#e8eef4;font-size:.75rem}.artifact-card{margin:.75rem 0}.artifact-preview{display:block;width:100%;min-height:360px;border:1px solid var(--line);background:#fff}.artifact-links{display:flex;gap:.5rem;margin-top:.6rem}.artifact-links a,.topbar a{color:#087f74}.topbar a{color:#9fe7dd}.candidate{border:1px solid var(--line);border-radius:.5rem;padding:.6rem;margin:.6rem 0}.candidate summary{cursor:pointer;font-weight:700}.candidate summary span{font-weight:400;color:var(--muted)}.empty{color:var(--muted)}@media(max-width:850px){.sample-layout{grid-template-columns:1fr}.stage-nav{position:static;grid-template-columns:repeat(2,1fr)}}
""".strip()


SITE_JS = """
(() => { const input=document.querySelector('#sample-filter'); if(!input)return; input.addEventListener('input',()=>{const q=input.value.trim().toLowerCase();document.querySelectorAll('tbody tr[data-sample]').forEach(row=>{row.hidden=!row.dataset.sample.toLowerCase().includes(q);});}); })();
""".strip()


def build_site(manifest: dict[str, Any], site_root: Path) -> Path:
    site_root = Path(site_root)
    artifact_root = site_root.parent
    (site_root / "assets").mkdir(parents=True, exist_ok=True)
    (site_root / "assets/site.css").write_text(SITE_CSS + "\n", encoding="utf-8")
    (site_root / "assets/site.js").write_text(SITE_JS + "\n", encoding="utf-8")
    _index_page(manifest, site_root)
    for sample in manifest.get("samples", []):
        _sample_page(sample, site_root, artifact_root)
    return site_root / "index.html"


class _LinkCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        for name, value in attrs:
            if name in {"href", "src", "data"} and value:
                self.links.append(value)


def validate_site_links(site_root: Path, artifact_root: Path) -> SiteValidationReport:
    site_root = Path(site_root).resolve()
    artifact_root = Path(artifact_root).resolve()
    missing: list[str] = []
    external: list[str] = []
    escaping: list[str] = []
    for page in sorted(site_root.rglob("*.html")):
        parser = _LinkCollector()
        parser.feed(page.read_text(encoding="utf-8"))
        for raw in parser.links:
            if raw.startswith("#"):
                continue
            parsed = urlsplit(raw)
            location = f"{page.relative_to(site_root).as_posix()}: {raw}"
            if parsed.scheme or parsed.netloc:
                external.append(location)
                continue
            target_text = unquote(parsed.path)
            if not target_text:
                continue
            target = (page.parent / target_text).resolve()
            if not target.is_relative_to(artifact_root):
                escaping.append(location)
                continue
            if not target.exists():
                missing.append(location)
    return SiteValidationReport(
        missing=sorted(set(missing)),
        external=sorted(set(external)),
        escaping=sorted(set(escaping)),
    )
