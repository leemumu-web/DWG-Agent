from __future__ import annotations

import re

from .models import Candidate, Evidence, ParseWarning

_LABEL_VALUE = re.compile(r"^\s*([^:：]+?)\s*[:：]\s*(.*?)\s*$")
_PART_SPLIT = re.compile(r"[、,，;；\s]+")
_MATERIAL_LABELS = {"材质", "材料", "牌号", "材料牌号"}
_PROJECT_LABELS = {"项目编号", "项目号", "工程编号", "工程号"}
_PART_LABELS = {"零件编号", "零件号", "件号", "构件编号"}
_KNOWN_LABELS = _MATERIAL_LABELS | _PROJECT_LABELS | _PART_LABELS
_UNLABELLED_MATERIAL = re.compile(r"^Q\d{3}[A-Z](?:[-+][A-Z0-9]+)*$", re.IGNORECASE)
# Conservative fallback for prefixes verified in production drawings.  More
# varied identifiers remain supported when an explicit part label exists.
_UNLABELLED_PART = re.compile(r"^(?:NJBZ?|NYDL)-\d{2,3}-\d{1,2}$", re.IGNORECASE)
_PROJECT_TITLE = re.compile(r"^(?=.*[\u3400-\u9fff]).*(?<!\d)\d{3}\s*计划.*$")
_MAX_PROJECT_LENGTH = 128


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


def classify(items: list[Evidence]):
    materials: dict[str, Candidate] = {}; projects: dict[str, Candidate] = {}; parts: dict[str, Candidate] = {}
    has_encoding_anomaly = False
    has_unrecognized_label = False
    has_unrecognized_text = False
    has_oversized_project_title = False
    for evidence in items:
        if "�" in evidence.raw_text or "�" in evidence.normalized_text or r"\M+" in evidence.normalized_text:
            has_encoding_anomaly = True
        match = _LABEL_VALUE.match(evidence.normalized_text)
        if not match:
            text = evidence.normalized_text
            if _UNLABELLED_MATERIAL.fullmatch(text):
                _append(materials, text.upper(), evidence)
            elif _UNLABELLED_PART.fullmatch(text):
                _append(parts, text, evidence)
            elif _PROJECT_TITLE.fullmatch(text):
                if not _append_project(projects, text, evidence):
                    has_oversized_project_title = True
            else:
                has_unrecognized_text = True
            continue
        label, value = match.groups()
        if label in _MATERIAL_LABELS:
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
    if has_unrecognized_text:
        warnings.append(ParseWarning("UNRECOGNIZED_TEXT", "图纸中存在未识别的普通文字"))
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
