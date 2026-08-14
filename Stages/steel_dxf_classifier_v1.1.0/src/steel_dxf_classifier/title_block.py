from __future__ import annotations

from .model import TextFact, TitleCandidate
from .profile import parse_profile


TITLE_PROFILE_LABELS = frozenset(
    {
        "截面",
        "截面型材",
        "截面规格",
        "规格",
        "PROFILE",
        "SECTION",
        "PROFILE/SIZE",
    }
)


def _normalized_label(text: str) -> str:
    return text.replace(" ", "").upper()


def _title_region_labels(facts: list[TextFact]) -> list[TextFact]:
    if not facts:
        return []
    min_x = min(fact.x for fact in facts)
    max_x = max(fact.x for fact in facts)
    min_y = min(fact.y for fact in facts)
    max_y = max(fact.y for fact in facts)
    width = max(max_x - min_x, 1.0)
    height = max(max_y - min_y, 1.0)
    labels: list[TextFact] = []
    for fact in facts:
        if _normalized_label(fact.normalized) not in TITLE_PROFILE_LABELS:
            continue
        relative_x = (fact.x - min_x) / width
        relative_y = (fact.y - min_y) / height
        # 0.55/0.65：标题栏区域定位的工程约定——文本位于图纸右 55% 或
        # 上 65% 才视为右上信息表标签；放太宽会把正文误配为标签，太窄会漏检。
        if relative_x >= 0.55 or relative_y >= 0.65:
            labels.append(fact)
    return labels


def find_title_candidates(
    facts: list[TextFact],
) -> tuple[list[TextFact], list[TitleCandidate]]:
    labels = _title_region_labels(facts)
    if not labels:
        return [], []

    page_width = max(max(fact.x for fact in facts) - min(fact.x for fact in facts), 1.0)
    page_height = max(max(fact.y for fact in facts) - min(fact.y for fact in facts), 1.0)
    candidates: list[TitleCandidate] = []
    seen: set[tuple[int, int, str]] = set()
    for label_index, label in enumerate(labels):
        for value_index, value in enumerate(facts):
            if value is label or value.block_path != label.block_path:
                continue
            profile = parse_profile(value.normalized)
            if profile is None:
                continue
            scale = max(label.height, value.height, 1.0)
            delta_x = value.x - label.x
            delta_y = value.y - label.y
            direction: str | None = None
            distance = 0.0
            # 标签-取值配对门限（倍数基于字高 scale，均设页面比例兜底）：
            #   below —— 值在标签正下方 30 倍字高内、横向偏差 8 倍字高内；
            #   right —— 值在标签右侧 60 倍字高内、纵向偏差 3 倍字高内。
            # 门限太宽造成 TITLE_VALUE_CONFLICT 误配，太窄造成漏检。
            if (
                delta_y < 0
                and -delta_y <= max(30.0 * scale, 0.20 * page_height)
                and abs(delta_x) <= max(8.0 * scale, 0.12 * page_width)
            ):
                direction = "below"
                distance = abs(delta_x) / (8.0 * scale) + (-delta_y) / (3.0 * scale)
            elif (
                delta_x > 0
                and delta_x <= max(60.0 * scale, 0.25 * page_width)
                and abs(delta_y) <= max(3.0 * scale, 0.04 * page_height)
            ):
                direction = "right"
                distance = delta_x / (8.0 * scale) + abs(delta_y) / (3.0 * scale)
            if direction is None:
                continue
            key = (label_index, value_index, direction)
            if key in seen:
                continue
            seen.add(key)
            candidates.append(
                TitleCandidate(
                    label=label,
                    value=value,
                    profile=profile,
                    direction=direction,
                    normalized_distance=distance,
                )
            )
    candidates.sort(
        key=lambda candidate: (
            candidate.normalized_distance,
            candidate.profile.part_type,
            candidate.value.normalized,
            candidate.value.x,
            candidate.value.y,
        )
    )
    return labels, candidates
