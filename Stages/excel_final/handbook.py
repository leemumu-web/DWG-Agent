"""Steel Profile Handbook — MySQL-backed theoretical weight lookup.

Queries the hardware_handbook MariaDB for accurate GB/T-standard
theoretical weights.  Falls back to computational formulas when DB is
unavailable or a spec is not found.
"""

from __future__ import annotations

import re
import time
import logging
from typing import Any

import pymysql

from config import DB_CONFIG, MISC_WEIGHTS

log = logging.getLogger(__name__)

# Module-level singleton — initialized by pipeline
_db: SteelHandbookDB | None = None  # noqa: F821  (forward ref resolved at runtime)


class SteelHandbookDB:
    """MySQL-backed steel profile theoretical weight lookup.

    Queries the hardware_handbook database for accurate theoretical
    weights per GB/T and other national standards.  Falls back to
    computational formulas when DB is unavailable or spec not found.
    """

    def __init__(self, config: dict, max_retries: int = 3):
        self.config = config
        self._cache: dict[str, tuple[float | None, str]] = {}
        self.stats = {"found": 0, "missed": 0, "computational": 0, "misc": 0}
        self._missed_specs: set[str] = set()
        self._found_sources: dict[str, int] = {}

        last_error = None
        for attempt in range(max_retries):
            try:
                self.conn = pymysql.connect(**config)
                self._db_available = True
                log.info(
                    "五金手册数据库连接成功: %s@%s/%s",
                    config["user"], config["host"], config["database"],
                )
                return
            except pymysql.Error as e:
                last_error = e
                if attempt < max_retries - 1:
                    delay = 2 ** attempt
                    log.warning(
                        "数据库连接尝试 %d/%d 失败，%ds 后重试: %s",
                        attempt + 1, max_retries, delay, e,
                    )
                    time.sleep(delay)

        log.error("无法连接五金手册数据库 (已重试 %d 次): %s", max_retries, last_error)
        log.error("请确保 MariaDB 正在运行: sudo systemctl start mariadb")
        log.error("将仅使用公式计算和杂项表作为回退方案。")
        self.conn = None
        self._db_available = False

    def close(self):
        """Close the database connection."""
        if self.conn is not None:
            try:
                self.conn.close()
                log.info("五金手册数据库连接已关闭。")
            except Exception:
                pass

    def _ensure_connected(self) -> bool:
        """Check connection is alive; attempt reconnect if dropped."""
        if not self._db_available or self.conn is None:
            return False
        try:
            self.conn.ping()
            return True
        except (pymysql.Error, AttributeError):
            log.warning("数据库连接已断开，尝试重连...")
            try:
                self.conn = pymysql.connect(**self.config)
                self._db_available = True
                log.info("数据库重连成功。")
                return True
            except pymysql.Error as e:
                log.error("数据库重连失败: %s", e)
                self._db_available = False
                return False

    def lookup(self, spec_str: str) -> tuple[float | None, str]:
        """Look up theoretical weight (kg/m) for a steel profile spec.

        Returns (weight_kg_per_m, source_description).
        Weight is None if not found.
        """
        if not spec_str or not isinstance(spec_str, str):
            return (None, "empty_spec")

        s = spec_str.strip()
        if not s:
            return (None, "empty_spec")

        # ── Quick-reject non-profile strings ──
        # Bolt grades (TS10.9, HS10.9, M20, M22, M24, etc.)
        if re.match(r"^(TS|HS)\s*\d+\.?\d*$", s, re.I):
            self._cache[s] = (None, "螺栓等级(跳过)")
            return self._cache[s]
        if re.match(r"^M\d+", s, re.I):
            self._cache[s] = (None, "螺栓规格(跳过)")
            return self._cache[s]
        # Weld studs (STUD, 栓钉)
        if re.match(r"^(STUD|stud|栓钉)$", s, re.I):
            self._cache[s] = (None, "栓钉(跳过)")
            return self._cache[s]
        # Summary / bogus text
        if any(kw in s for kw in ["合计", "总计", "总重", "None", "nan"]):
            self._cache[s] = (None, "摘要文本(跳过)")
            return self._cache[s]
        # Single letters or very short non-numeric
        if len(s) <= 2 and not s.isdigit() and not s.replace('.', '').isdigit():
            self._cache[s] = (None, "无效规格(跳过)")
            return self._cache[s]

        if s in self._cache:
            return self._cache[s]

        # 1) Try DB lookup with normalized candidates
        for candidate in _normalize_spec_for_db(s):
            result = self._query_material_lookup(candidate)
            if result is None:
                result = self._query_specific_tables(candidate)
            if result is not None:
                weight, category = result
                source = f"{category}:{candidate}"
                self._cache[s] = (weight, source)
                self.stats["found"] += 1
                self._found_sources[source] = self._found_sources.get(source, 0) + 1
                return self._cache[s]

        # 2) Computational fallbacks
        weight = self._computational_fallback(s)
        if weight is not None:
            source = "公式计算"
            self._cache[s] = (weight, source)
            self.stats["computational"] += 1
            self._found_sources[source] = self._found_sources.get(source, 0) + 1
            return self._cache[s]

        # 3) Misc weights
        upper = s.upper()
        if upper in MISC_WEIGHTS:
            w = MISC_WEIGHTS[upper]
            source = f"杂项:{upper}"
            self._cache[s] = (w, source)
            self.stats["misc"] += 1
            self._found_sources[source] = self._found_sources.get(source, 0) + 1
            return self._cache[s]

        # 4) Not found
        self._cache[s] = (None, "未查到")
        self.stats["missed"] += 1
        self._missed_specs.add(s)
        return self._cache[s]

    def _query_material_lookup(self, spec: str) -> tuple[float, str] | None:
        if not self._db_available or not self._ensure_connected():
            return None
        try:
            with self.conn.cursor() as cur:
                cur.execute(
                    "SELECT category, weight FROM material_lookup WHERE spec = %s "
                    "ORDER BY FIELD(category, '圆钢', '螺纹钢', '方钢', '角钢', "
                    "'槽钢', '工字钢', 'H型钢', 'T型钢', '钢管', '方管', "
                    "'扁钢', '花纹板(圆豆)', '花纹板(菱形)', '花纹板(扁豆)', "
                    "'高频焊', 'W型钢', '角钢(美标)', 'H型钢(美标)', 'U型槽钢(美标)') LIMIT 1",
                    (spec,),
                )
                row = cur.fetchone()
                if row:
                    weight = float(row[1])
                    if weight <= 0 or weight > 2000:
                        log.warning("数据库返回异常比重值: %s → %s kg/m (已忽略)", spec, weight)
                        return None
                    return (weight, str(row[0]))
        except (pymysql.Error, ValueError, TypeError) as e:
            log.warning("数据库查询失败 ('%s'): %s", spec, e)
        return None

    def _query_specific_tables(self, spec: str) -> tuple[float, str] | None:
        if not self._db_available or not self._ensure_connected():
            return None

        def _safe_weight(val) -> float | None:
            if val is None:
                return None
            w = float(val)
            if w <= 0 or w > 2000:
                return None
            return w

        try:
            with self.conn.cursor() as cur:
                # Bare number: rebar / round bar
                m = re.match(r"^(\d+\.?\d*)$", spec)
                if m:
                    dia = float(m.group(1))
                    cur.execute("SELECT weight FROM rebar WHERE dia = %s", (dia,))
                    row = cur.fetchone()
                    if row and _safe_weight(row[0]):
                        return (_safe_weight(row[0]), "螺纹钢(规格表)")
                    cur.execute(
                        "SELECT round_weight FROM round_square_bar WHERE dia_or_side = %s",
                        (dia,),
                    )
                    row = cur.fetchone()
                    if row and _safe_weight(row[0]):
                        return (_safe_weight(row[0]), "圆钢(规格表)")

                # T型钢
                cur.execute(
                    "SELECT weight_2010, weight_2005, weight_98 FROM t_beam WHERE spec = %s",
                    (spec,),
                )
                row = cur.fetchone()
                if row:
                    w = _safe_weight(row[0]) or _safe_weight(row[1]) or _safe_weight(row[2])
                    if w:
                        return (w, "T型钢(规格表)")

                # 高频焊H型钢
                cur.execute("SELECT weight FROM hfw_pipe WHERE spec = %s", (spec,))
                row = cur.fetchone()
                if row and _safe_weight(row[0]):
                    return (_safe_weight(row[0]), "高频焊H型钢(规格表)")

                # 钢管
                cur.execute("SELECT weight FROM steel_pipe WHERE spec = %s", (spec,))
                row = cur.fetchone()
                if row and _safe_weight(row[0]):
                    return (_safe_weight(row[0]), "钢管(规格表)")

                # 方管
                cur.execute("SELECT weight FROM square_tube WHERE spec = %s", (spec,))
                row = cur.fetchone()
                if row and _safe_weight(row[0]):
                    return (_safe_weight(row[0]), "方管(规格表)")

                # 扁钢
                cur.execute("SELECT weight FROM flat_steel WHERE spec = %s", (spec,))
                row = cur.fetchone()
                if row and _safe_weight(row[0]):
                    return (_safe_weight(row[0]), "扁钢(规格表)")

                # 槽钢
                cur.execute("SELECT weight FROM channel WHERE spec = %s", (spec,))
                row = cur.fetchone()
                if row and _safe_weight(row[0]):
                    return (_safe_weight(row[0]), "槽钢(规格表)")

                # 工字钢
                cur.execute("SELECT weight FROM i_beam WHERE spec = %s", (spec,))
                row = cur.fetchone()
                if row and _safe_weight(row[0]):
                    return (_safe_weight(row[0]), "工字钢(规格表)")

                # 角钢
                cur.execute("SELECT weight FROM angle WHERE spec = %s", (spec,))
                row = cur.fetchone()
                if row and _safe_weight(row[0]):
                    return (_safe_weight(row[0]), "角钢(规格表)")

                # H型钢
                cur.execute(
                    "SELECT weight_2010, weight_2005, weight_98 FROM h_beam WHERE spec = %s",
                    (spec,),
                )
                row = cur.fetchone()
                if row:
                    w = _safe_weight(row[0]) or _safe_weight(row[1]) or _safe_weight(row[2])
                    if w:
                        return (w, "H型钢(规格表)")

                # H型钢(美标)
                cur.execute("SELECT weight FROM h_beam_us WHERE spec = %s", (spec,))
                row = cur.fetchone()
                if row and _safe_weight(row[0]):
                    return (_safe_weight(row[0]), "H型钢(美标·规格表)")

        except (pymysql.Error, ValueError, TypeError) as e:
            log.debug("特定表查询失败 ('%s'): %s", spec, e)
        return None

    def _computational_fallback(self, spec: str) -> float | None:
        s = spec.strip()

        # PL plate: thickness * width → density(kg/m) = t * w * 7.85 / 1000
        # Pattern: PLt*w or -t*w or just t*w (bare numbers from flat steel)
        m = re.match(r"^(?:PL|-)?\s*(\d+\.?\d*)\s*\*\s*(\d+\.?\d*)$", s, re.I)
        if m:
            t, w = float(m.group(1)), float(m.group(2))
            return round(t * w * 7.85 / 1000, 3)

        # Round pipe: (OD - WT) * WT * 0.02466
        m = re.match(r"^(?:D|PIP|P|φ)\s*(\d+\.?\d*)\s*\*\s*(\d+\.?\d*)$", s)
        if m:
            od, t = float(m.group(1)), float(m.group(2))
            return round((od - t) * t * 0.02466, 3)

        # Square/rectangular tube
        m = re.match(r"^方管\s*(\d+\.?\d*)\s*\*\s*(\d+\.?\d*)\s*\*\s*(\d+\.?\d*)$", s)
        if m:
            a, b, t = float(m.group(1)), float(m.group(2)), float(m.group(3))
            return round(2 * (a + b - 2 * t) * t * 7.85 / 1000, 3)

        # Bare diameter with estimated wall thickness
        m = re.match(r"^(?:D|PIP|P|φ)\s*(\d+\.?\d*)$", s)
        if m:
            od = float(m.group(1))
            t = 3.0 if od <= 30 else (3.5 if od <= 60 else (4.0 if od <= 100 else 6.0))
            return round((od - t) * t * 0.02466, 3)

        # Pure number (bare thickness for plates that lost their width context)
        # Assume 1mm width strip → minimal density, better than nothing
        m = re.match(r"^(\d+\.?\d*)$", s)
        if m:
            t = float(m.group(1))
            # Thickness-only plate: if t >= 6mm, likely a plate spec without width
            if t >= 3:
                return round(t * 7.85, 3)  # kg/m² per mm thickness
            return None

        return None

    def log_stats(self):
        total = sum(self.stats.values())
        log.info("五金手册查询统计: 共 %d 次查询", total)
        log.info("  ✓ 数据库精确命中: %d", self.stats["found"])
        log.info("  ≈ 公式计算:       %d", self.stats["computational"])
        log.info("  ~ 杂项查表:       %d", self.stats["misc"])
        log.info("  ✗ 未查到:         %d", self.stats["missed"])
        if self._missed_specs:
            missed_list = sorted(self._missed_specs)
            log.warning("未查到的规格 (%d 种): %s", len(missed_list), missed_list[:40])
        if self._found_sources:
            log.info("数据来源分布 (top 10):")
            for src, cnt in sorted(self._found_sources.items(), key=lambda x: -x[1])[:10]:
                log.info("  %s: %d 次", src, cnt)


# ── Spec normalization for DB lookup ───────────────────────────

def _normalize_spec_for_db(spec: str) -> list[str]:
    """Generate candidate spec strings for database lookup.

    The DB stores specs in standard formats ([10, φ60*3.5), while
    Tekla uses variants (C10, D60*3.5, etc.).  Returns candidates
    in priority order.
    """
    s = spec.strip()
    candidates: list[str] = []
    seen: set[str] = set()

    def add(c: str):
        if c and c not in seen:
            seen.add(c)
            candidates.append(c)

    add(s)

    # Channel: C20a → [20A
    m = re.match(r"^C\s*(\d+\.?\d*)\s*([a-cA-C]?)$", s)
    if m:
        add(f"[{m.group(1)}{m.group(2).upper()}")

    # Pipe: D/IP/PIP + OD*WT → φOD*WT
    m = re.match(r"^(?:D|PIP|P)\s*(\d+\.?\d*\*\d+\.?\d*)$", s)
    if m:
        add(f"φ{m.group(1)}")

    # Rebar / round bar: D8/D19 → d=8mm, 8, φ8
    m = re.match(r"^D\s*(\d+\.?\d*)$", s)
    if m:
        dia = m.group(1)
        add(f"d={dia}mm")
        add(dia)
        add(f"φ{dia}")

    # Pipe: P/φ + bare diameter
    m = re.match(r"^(?:P|φ)\s*(\d+\.?\d*)$", s)
    if m:
        dia = m.group(1)
        add(f"φ{dia}")
        add(dia)

    # Square tube: 方管A*B*C → □A*B*C
    m = re.match(r"^方管\s*(\d+\.?\d*)\s*\*\s*(\d+\.?\d*)\s*\*\s*(\d+\.?\d*)$", s)
    if m:
        a, b, c = m.group(1), m.group(2), m.group(3)
        add(f"□{a}*{b}*{c}")
        if a == b:
            add(f"□{a}*{c}")

    # H-beam: HN/HW/HM/HT → H
    m = re.match(r"^(HN|HW|HM|HT)\s*(\d+.*)$", s, re.IGNORECASE)
    if m:
        add(f"H{m.group(2)}")

    # HA prefix → H
    m = re.match(r"^HA\s*(\d+.*)$", s)
    if m:
        add(f"H{m.group(1)}")

    # HI prefix: dimensional → H-beam; traditional → I-beam
    if re.search(r"\*", s):
        m = re.match(r"^HI\s*(\d+.*)$", s)
        if m:
            add(f"H{m.group(1)}")
    else:
        m = re.match(r"^HI\s*(\d+.*)$", s)
        if m:
            add(f"I{m.group(1)}")

    # LH → H
    m = re.match(r"^LH\s*(\d+.*)$", s, re.IGNORECASE)
    if m:
        add(f"H{m.group(1)}")

    # T型钢: TN/TW/TM → T
    m = re.match(r"^(TN|TW|TM)\s*(\d+.*)$", s, re.IGNORECASE)
    if m:
        add(f"T{m.group(2)}")

    # W型钢: normalize spaces
    m = re.match(r"^W\s*(\d+)\s*X\s*(\d+)$", s, re.IGNORECASE)
    if m:
        add(f"W{m.group(1)} X {m.group(2)}")

    # Uppercase variant
    upper_s = s.upper()
    if upper_s != s:
        add(upper_s)

    # Strip leading zeros
    m = re.match(r"^0(\d+\.?\d*)$", s)
    if m:
        add(m.group(1))

    # PL plate → flat steel / bare numbers for DB lookup
    # PL10*2000 → "10*2000", "10*2000"
    # NOTE: deliberately do NOT add thickness-only candidate ("10")
    # because it would falsely match 圆钢/方钢 round-bar entries.
    m = re.match(r"^(?:PL|-)\s*(\d+\.?\d*)\s*\*\s*(\d+\.?\d*)$", s, re.I)
    if m:
        t, w = m.group(1), m.group(2)
        add(f"{t}*{w}")               # for flat_steel lookup
        add(m.group(0).upper())        # uppercase variant

    # Bare t*w (already-parsed plate spec) → same candidates
    m = re.match(r"^(\d+\.?\d*)\s*\*\s*(\d+\.?\d*)$", s)
    if m:
        t, w = m.group(1), m.group(2)
        add(f"PL{t}*{w}")              # reconstruct PL prefix form
        # No thickness-only fallback here either


    return candidates


# ── Public API ─────────────────────────────────────────────────

def lookup_steel_weight(spec_str: str) -> tuple[float | None, str]:
    """Look up theoretical weight per meter (kg/m) for a steel profile.

    Returns (weight_kg_per_m, source_description).
    """
    global _db
    if _db is None:
        log.warning("五金手册数据库未初始化，尝试连接...")
        _db = SteelHandbookDB(DB_CONFIG)
    return _db.lookup(spec_str)


def init_handbook(config: dict | None = None) -> SteelHandbookDB:
    """Initialize the handbook DB connection.  Called once at pipeline start."""
    global _db
    _db = SteelHandbookDB(config or DB_CONFIG)
    return _db


def get_handbook() -> SteelHandbookDB | None:
    """Return the current handbook singleton (may be None)."""
    return _db


def close_handbook() -> None:
    """Close the handbook DB connection if open."""
    global _db
    if _db is not None:
        _db.close()
        _db = None
