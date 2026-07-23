from __future__ import annotations

import re

from .models import Candidate, Evidence, ParseWarning

_LABEL_VALUE = re.compile(r"^\s*([^:：]+?)\s*[:：]\s*(.*?)\s*$")
_PART_SPLIT = re.compile(r"[、,，;；\s]+")
_MATERIAL_LABELS = {"材质", "材料", "牌号", "材料牌号"}
_PROJECT_LABELS = {"项目编号", "项目号", "工程编号", "工程号"}
_PART_LABELS = {"零件编号", "零件号", "件号", "构件编号"}
_KNOWN_LABELS = _MATERIAL_LABELS | _PROJECT_LABELS | _PART_LABELS
_MATERIAL_TOKEN = re.compile(
    r"(?<![A-Z0-9])Q\d{3}[A-Z]{1,3}(?:[-+][A-Z0-9]+)*(?![A-Z0-9])",
    re.IGNORECASE,
)
_PART_TOKEN = re.compile(
    r"(?<![A-Z0-9])(?=[A-Z0-9-]*[A-Z])(?=[A-Z0-9-]*\d)"
    r"[A-Z0-9]+(?:-[A-Z0-9]+)+(?![A-Z0-9])",
    re.IGNORECASE,
)
_NON_PART_PREFIXES = {"DATE", "DWG", "ISO", "REV"}
_CHINESE_TEXT = re.compile(r"[\u3400-\u9fff]")
_SHORT_CHINESE_ANNOTATION = re.compile(r"^[\u3400-\u9fff]{2,3}$")
_MAX_PROJECT_LENGTH = 128
_METADATA_SEPARATORS = " \t:：,，;；()（）[]【】"


def _append(target: dict[str, Candidate], value: str, evidence: Evidence) -> None:
    cleaned = value.strip()
    if cleaned:
        target.setdefault(cleaned, Candidate(value=cleaned)).evidence.append(evidence)


def _append_project(
    target: dict[str, Candidate], value: str, evidence: Evidence
) -> bool:
    cleaned = value.strip()
    if len(cleaned) > _MAX_PROJECT_LENGTH:
        return False
    _append(target, cleaned, evidence)
    return True


def _material_matches(text: str) -> list[re.Match[str]]:
    return list(_MATERIAL_TOKEN.finditer(text))


def _overlaps(span: tuple[int, int], occupied: list[tuple[int, int]]) -> bool:
    start, end = span
    return any(start < occupied_end and occupied_start < end for occupied_start, occupied_end in occupied)


def _part_matches(
    text: str, material_matches: list[re.Match[str]]
) -> list[re.Match[str]]:
    occupied = [match.span() for match in material_matches]
    matches: list[re.Match[str]] = []
    for match in _PART_TOKEN.finditer(text):
        if _overlaps(match.span(), occupied):
            continue
        value = match.group(0)
        if value.split("-", 1)[0].upper() in _NON_PART_PREFIXES:
            continue
        matches.append(match)
    return matches


def _metadata_remainder(
    text: str,
    material_matches: list[re.Match[str]],
    part_matches: list[re.Match[str]],
) -> str:
    characters = list(text)
    metadata_spans = [match.span() for match in material_matches + part_matches]
    for start, end in metadata_spans:
        characters[start:end] = " " * (end - start)
        prefix_end = len(text[:start].rstrip(_METADATA_SEPARATORS))
        for label in sorted(_KNOWN_LABELS, key=len, reverse=True):
            label_start = prefix_end - len(label)
            if label_start >= 0 and text[label_start:prefix_end] == label:
                characters[label_start:prefix_end] = " " * len(label)
                break
    return "".join(characters).strip(_METADATA_SEPARATORS)


def classify(items: list[Evidence]):
    materials: dict[str, Candidate] = {}
    projects: dict[str, Candidate] = {}
    parts: dict[str, Candidate] = {}
    has_encoding_anomaly = False
    has_unrecognized_label = False
    has_oversized_project_title = False
    for evidence in items:
        if "�" in evidence.raw_text or "�" in evidence.normalized_text or r"\M+" in evidence.normalized_text:
            has_encoding_anomaly = True
        match = _LABEL_VALUE.match(evidence.normalized_text)
        if not match:
            text = evidence.normalized_text
            material_matches = _material_matches(text)
            for material_match in material_matches:
                _append(materials, material_match.group(0).upper(), evidence)
            part_matches = _part_matches(text, material_matches)
            for part_match in part_matches:
                _append(parts, part_match.group(0), evidence)
            project_text = _metadata_remainder(text, material_matches, part_matches)
            if (
                not project_text
                or project_text in _KNOWN_LABELS
                or _SHORT_CHINESE_ANNOTATION.fullmatch(project_text)
            ):
                continue
            if _CHINESE_TEXT.search(project_text):
                if not _append_project(projects, project_text, evidence):
                    has_oversized_project_title = True
            continue
        label, value = match.groups()
        if label in _MATERIAL_LABELS:
            matches = _material_matches(value)
            if matches:
                for material_match in matches:
                    _append(materials, material_match.group(0).upper(), evidence)
            else:
                _append(materials, value.upper(), evidence)
        elif label in _PROJECT_LABELS:
            if not _append_project(projects, value, evidence):
                has_oversized_project_title = True
        elif label in _PART_LABELS:
            for part_no in _PART_SPLIT.split(value):
                _append(parts, part_no, evidence)
        elif label not in _KNOWN_LABELS:
            has_unrecognized_label = True
    warnings: list[ParseWarning] = []
    if has_encoding_anomaly:
        warnings.append(ParseWarning("ENCODING_ANOMALY", "图纸文字存在无法完整解码的内容"))
    if has_unrecognized_label:
        warnings.append(ParseWarning("UNRECOGNIZED_LABEL", "图纸中存在未识别的字段标签"))
    if has_oversized_project_title:
        warnings.append(
            ParseWarning(
                "PROJECT_TITLE_TOO_LONG",
                "图纸项目标题超过 128 个字符，请人工填写",
            )
        )
    if len(materials) > 1:
        warnings.append(ParseWarning("MATERIAL_CANDIDATES_CONFLICT", "图纸中存在多个材质候选"))
    if len(projects) > 1:
        warnings.append(ParseWarning("PROJECT_CANDIDATES_CONFLICT", "图纸中存在多个项目编号候选"))
    return list(materials.values()), list(projects.values()), list(parts.values()), warnings
