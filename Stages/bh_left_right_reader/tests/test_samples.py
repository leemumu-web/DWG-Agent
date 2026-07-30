from __future__ import annotations

import os
from pathlib import Path
from tempfile import NamedTemporaryFile, TemporaryDirectory
import unittest

from bh_reader.analyzer import BHAnalyzer
from bh_reader.dxf_ascii import read_ascii_dxf
from bh_reader.dxf_ezdxf import read_ezdxf
from bh_reader.model import DrawingData, Primitive


REAL_PRE_DXF_DIRECTORY = Path(os.environ.get(
    "BH_READER_PRE_DXF_DIRECTORY",
    "/home/Creeken/Paper/CAD_research/所有的dxf/BH拆板前后数据/BH_拆板前_dxf",
))
PROJECT1_DXF_DIRECTORY = Path(os.environ.get(
    "BH_READER_PROJECT1_DXF_DIRECTORY",
    "/home/Creeken/Paper/CAD_research/所有的dxf/项目1/项目1_BH_dxf",
))
LARGE_CORPUS_DXF_DIRECTORY = Path(os.environ.get(
    "BH_READER_LARGE_CORPUS_DXF_DIRECTORY",
    "/home/Creeken/Paper/CAD_research/所有的dxf/集散中心框架3~5层1批加工图中铁建区域（安徽齐顺）/分类1/BH",
))


class SampleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.analyzer = BHAnalyzer()

    @staticmethod
    def _drawing(
        name: str,
        lines: list[tuple[tuple[float, float], tuple[float, float]]],
        spec_text: str,
        nominal_length: float | None = None,
    ) -> DrawingData:
        primitives = [Primitive("LINE", "Part", [a, b], "FRONT") for a, b in lines]
        spec = Primitive("TEXT", "OtherObjectType", [(0.0, -100.0)], "TABLE", text=spec_text)
        texts = [spec]
        primitives.append(spec)
        if nominal_length is not None:
            length = Primitive(
                "TEXT", "OtherObjectType", [(10.0, -100.0)], "TABLE", text=f"{nominal_length:g}"
            )
            texts.append(length)
            primitives.append(length)
        return DrawingData(Path(name), primitives, texts, "synthetic")

    @staticmethod
    def _roles(result):
        return {item.role: (item.left_safe, item.right_safe) for item in result.measurements}

    def _sample_dxf(self, directory: Path, name: str) -> Path:
        path = directory / name
        if not path.exists():
            self.skipTest(f"real DXF is not available: {path}")
        return path

    def _pre_dxf(self, name: str) -> Path:
        return self._sample_dxf(REAL_PRE_DXF_DIRECTORY, name)

    def _project1_dxf(self, name: str) -> Path:
        return self._sample_dxf(PROJECT1_DXF_DIRECTORY, name)

    def _large_dxf(self, name: str) -> Path:
        return self._sample_dxf(LARGE_CORPUS_DXF_DIRECTORY, name)

    def _all_supplied_dxf(self) -> list[Path]:
        directories = (
            REAL_PRE_DXF_DIRECTORY,
            PROJECT1_DXF_DIRECTORY,
            LARGE_CORPUS_DXF_DIRECTORY,
        )
        missing = [str(directory) for directory in directories if not directory.is_dir()]
        if missing:
            self.skipTest("real DXF directories are not available: " + ", ".join(missing))
        return sorted(
            (path for directory in directories for path in directory.glob("*.dxf")),
            key=lambda path: str(path),
        )

    def test_all_supplied_drawings_complete_without_skip(self):
        paths = self._all_supplied_dxf()
        self.assertEqual(len(paths), 199)
        results = [self.analyzer.analyze(read_ascii_dxf(path)) for path in paths]
        self.assertTrue(all(result.status == "OK" for result in results))
        self.assertTrue(all(result.measurements for result in results))

    def test_all_supplied_drawings_match_between_ascii_and_ezdxf(self):
        # The two supported readers are independent implementations.  Compare
        # every production-facing field, including evidence text, on all real
        # supplied drawings so a parser-specific DXF interpretation cannot
        # silently alter the released left/right setback result.
        paths = self._all_supplied_dxf()
        self.assertEqual(len(paths), 199)
        for path in paths:
            ascii_result = self.analyzer.analyze(read_ascii_dxf(path))
            ezdxf_result = self.analyzer.analyze(read_ezdxf(path))
            self.assertEqual(ascii_result.file_name, ezdxf_result.file_name, msg=path.name)
            self.assertEqual(ascii_result.part_number, ezdxf_result.part_number, msg=path.name)
            self.assertEqual(ascii_result.specification, ezdxf_result.specification, msg=path.name)
            self.assertEqual(ascii_result.status, ezdxf_result.status, msg=path.name)
            self.assertEqual(ascii_result.confidence, ezdxf_result.confidence, msg=path.name)
            self.assertEqual(ascii_result.measurements, ezdxf_result.measurements, msg=path.name)

    def test_internal_vertical_line_does_not_split_flange(self):
        lines = [
            ((0, 0), (10000, 0)), ((0, 20), (10000, 20)),
            ((0, 580), (10000, 580)), ((0, 600), (10000, 600)),
            ((0, 0), (0, 600)), ((10000, 0), (10000, 600)),
            ((5000, 0), (5000, 20)), ((5000, 580), (5000, 600)),
        ]
        result = self.analyzer.analyze(
            self._drawing("internal_lines.dxf", lines, "BH600*250*12*20", 10000)
        )
        self.assertEqual(result.status, "OK")
        plate = result.diagnostics["plate_identification"]
        self.assertEqual(plate["upper_flange_count"], 1)
        self.assertEqual(plate["lower_flange_count"], 1)
        self.assertEqual(set(self._roles(result)), {"腹", "翼"})

        # A transverse detail line can cross the complete flange thickness at
        # one X position, but it is not part of either physical plate outline.
        # Diagnostic/visual ownership must therefore exclude both internal
        # lines instead of colouring them as flange material.
        spec = self.analyzer._extract_spec(self._drawing(
            "internal_lines.dxf", lines, "BH600*250*12*20", 10000
        ).texts)
        front, _, _ = self.analyzer._step1_locate_main_view(
            self._drawing("internal_lines.dxf", lines, "BH600*250*12*20", 10000),
            spec,
        )
        self.assertIsNotNone(front)
        assert front is not None
        internal_ids = {
            index
            for index, segment in enumerate(front.segments)
            if abs(segment.a[0] - 5000.0) < 1e-9
            and abs(segment.b[0] - 5000.0) < 1e-9
        }
        flange = result.diagnostics["flange_analysis"]
        self.assertTrue(internal_ids)
        self.assertTrue(internal_ids.isdisjoint(flange["lower"]["selected_segment_ids"]))
        self.assertTrue(internal_ids.isdisjoint(flange["upper"]["selected_segment_ids"]))

    def test_incidental_intersections_are_not_owned_by_flange_plates(self):
        length = 4000.0
        outline = [
            ((0, 0), (length, 0)), ((0, 20), (length, 20)),
            ((0, 0), (0, 20)), ((length, 0), (length, 20)),
            ((0, 580), (length, 580)), ((0, 600), (length, 600)),
            ((0, 580), (0, 600)), ((length, 580), (length, 600)),
            ((0, 20), (0, 580)), ((length, 20), (length, 580)),
        ]
        incidental = [
            # Internal full-thickness detail cuts only briefly form a pair.
            ((1500, 20), (1520, 0)),
            ((2500, 580), (2520, 600)),
            # A web/detail diagonal merely touches each flange inner surface.
            ((1000, 20), (2000, 580)),
        ]
        drawing = self._drawing(
            "incidental_flange_hits.dxf",
            outline + incidental,
            "BH600*250*12*20",
            length,
        )
        result = self.analyzer.analyze(drawing)
        self.assertEqual(result.status, "OK", msg=str(result.warnings))
        spec = self.analyzer._extract_spec(drawing.texts)
        front, _, _ = self.analyzer._step1_locate_main_view(drawing, spec)
        self.assertIsNotNone(front)
        assert front is not None
        incidental_points = {
            frozenset((tuple(a), tuple(b))) for a, b in incidental
        }
        incidental_ids = {
            index
            for index, segment in enumerate(front.segments)
            if frozenset((tuple(segment.a), tuple(segment.b))) in incidental_points
        }
        self.assertEqual(len(incidental_ids), len(incidental))
        flange = result.diagnostics["flange_analysis"]
        self.assertTrue(incidental_ids.isdisjoint(flange["lower"]["selected_segment_ids"]))
        self.assertTrue(incidental_ids.isdisjoint(flange["upper"]["selected_segment_ids"]))
        for side in ("lower", "upper"):
            selected = {
                int(value) for value in flange[side]["selected_segment_ids"]
            }
            cap_ids = {
                segment_id
                for segment_id in selected
                if abs(front.segments[segment_id].b[0] - front.segments[segment_id].a[0]) < 1e-9
                and abs(
                    abs(front.segments[segment_id].b[1] - front.segments[segment_id].a[1])
                    - 20.0
                ) < 1e-9
            }
            self.assertEqual(len(cap_ids), 2, msg=f"{side}: {selected}")

    def test_disconnected_flange_material_is_output_piece_by_piece(self):
        lines = [
            # lower two pieces
            ((0, 0), (4000, 0)), ((0, 20), (4000, 20)),
            ((0, 0), (0, 20)), ((4000, 0), (4000, 20)),
            ((6000, 0), (10000, 0)), ((6000, 20), (10000, 20)),
            ((6000, 0), (6000, 20)), ((10000, 0), (10000, 20)),
            # upper two pieces
            ((0, 580), (4000, 580)), ((0, 600), (4000, 600)),
            ((0, 580), (0, 600)), ((4000, 580), (4000, 600)),
            ((6000, 580), (10000, 580)), ((6000, 600), (10000, 600)),
            ((6000, 580), (6000, 600)), ((10000, 580), (10000, 600)),
            # web extents
            ((0, 20), (0, 580)), ((10000, 20), (10000, 580)),
        ]
        result = self.analyzer.analyze(
            self._drawing("four_flange.dxf", lines, "BH600*250*12*20", 10000)
        )
        self.assertEqual(result.status, "OK", msg=str(result.warnings))
        actual = self._roles(result)
        self.assertEqual(actual["腹"], (0, 0))
        self.assertEqual(actual["翼-1"], (0, 6000))
        self.assertEqual(actual["翼-2"], (6000, 0))

    def test_visual_flange_ownership_is_clipped_at_each_physical_piece(self):
        from bh_reader.model import LocalSegment
        from bh_reader.visualize import _clip_segment_to_piece_x

        shared_source = LocalSegment((0.0, 20.0), (100.0, 20.0), "Part", "FRONT")
        first = _clip_segment_to_piece_x(shared_source, 0.0, 40.0)
        second = _clip_segment_to_piece_x(shared_source, 60.0, 100.0)
        self.assertEqual(first, ((0.0, 20.0), (40.0, 20.0)))
        self.assertEqual(second, ((60.0, 20.0), (100.0, 20.0)))
        self.assertLess(first[1][0], second[0][0])

    def test_real_multi_piece_samples_are_processed(self):
        expected = {
            "2b1-cb-18_拆板前.dxf": ["腹", "翼-1", "翼-2"],
            "2b1-cb-35_拆板前.dxf": ["腹", "翼-1", "翼-2"],
        }
        for name, expected_roles in expected.items():
            path = self._pre_dxf(name)
            result = self.analyzer.analyze(read_ascii_dxf(path))
            self.assertEqual(result.status, "OK", msg=str(result.warnings))
            plate = result.diagnostics["plate_identification"]
            self.assertEqual((plate["upper_flange_count"], plate["lower_flange_count"]), (2, 2))
            self.assertEqual([item.role for item in result.measurements], expected_roles)

    def test_tapered_plate_caps_keep_the_outermost_material_point(self):
        # A plate starts at its outer-face tip even when its inner face begins
        # later and the end cap is diagonal.  Requiring a complete-thickness
        # cross-section at the tip would manufacture a setback that is not in
        # the source geometry.
        length = 4000.0
        bevel = 50.0
        lines = [
            # lower flange: outer face reaches both global view boundaries
            ((0, 0), (length, 0)),
            ((bevel, 20), (length - bevel, 20)),
            ((0, 0), (bevel, 20)),
            ((length - bevel, 20), (length, 0)),
            # upper flange: the mirrored generic case
            ((0, 600), (length, 600)),
            ((bevel, 580), (length - bevel, 580)),
            ((0, 600), (bevel, 580)),
            ((length - bevel, 580), (length, 600)),
            # web ends are independently inset
            ((10, 20), (10, 580)),
            ((length - 10, 20), (length - 10, 580)),
        ]
        result = self.analyzer.analyze(
            self._drawing("tapered_plate_caps.dxf", lines, "BH600*250*12*20", length)
        )
        self.assertEqual(result.status, "OK", msg=str(result.warnings))
        self.assertEqual(self._roles(result), {"腹": (10, 10), "翼": (0, 0)})

        # The same bounding-box rule must preserve a genuinely shorter plate;
        # this prevents the cap fix from degenerating into a blanket zero.
        inset_lines = [
            ((0, 0), (length, 0)),
            ((0, 20), (length, 20)),
            ((0, 0), (0, 20)),
            ((length, 0), (length, 20)),
            ((100, 600), (length - 200, 600)),
            ((150, 580), (length - 250, 580)),
            ((100, 600), (150, 580)),
            ((length - 250, 580), (length - 200, 600)),
            ((0, 20), (0, 580)),
            ((length, 20), (length, 580)),
        ]
        inset_result = self.analyzer.analyze(
            self._drawing(
                "inset_tapered_plate_caps.dxf", inset_lines, "BH600*250*12*20", length
            )
        )
        self.assertEqual(inset_result.status, "OK", msg=str(inset_result.warnings))
        self.assertEqual(
            self._roles(inset_result),
            {"腹": (0, 0), "上翼": (100, 200), "下翼": (0, 0)},
        )

    def test_confirmed_real_plate_caps_are_not_reported_as_setbacks(self):
        # Confirmed against the source main views: 4t1-cb-67 has flange
        # contours reaching both member ends, and the lower second flange of
        # 2b1-cb-35 reaches the right member boundary through its end contour.
        cases = (
            (
                self._large_dxf("BYSJ@零件图@4t1-cb-67_拆板前.dxf"),
                {"腹": (10, 10), "翼": (0, 0)},
            ),
            (
                self._pre_dxf("2b1-cb-35_拆板前.dxf"),
                {
                    "腹": (0, 0),
                    "翼-1": (0, 5744),
                    "翼-2": (5770, 0),
                },
            ),
        )
        for path, expected in cases:
            for reader in (read_ascii_dxf, read_ezdxf):
                result = self.analyzer.analyze(reader(path))
                self.assertEqual(result.status, "OK", msg=f"{path.name}: {result.warnings}")
                self.assertEqual(self._roles(result), expected, msg=f"{reader.__name__}: {path.name}")

    def test_matching_multi_piece_flange_pairs_merge_piece_by_piece(self):
        path = self._pre_dxf("2b1-cb-18_拆板前.dxf")
        result = self.analyzer.analyze(read_ascii_dxf(path))
        self.assertEqual(result.status, "OK", msg=str(result.warnings))
        # The upper and lower two-piece traces have equal endpoints and shape.
        # They are two physical flange pieces, not four distinct pieces.
        self.assertEqual(
            self._roles(result),
            {"腹": (0, 0), "翼-1": (0, 7183), "翼-2": (6983, 0)},
        )

    def test_matching_endpoints_but_different_full_shape_do_not_merge(self):
        # Both flanges have equal left/right endpoints and equal net end-to-end
        # height delta.  The upper flange nevertheless has a 60 mm central
        # crown, so it is not the same physical plate shape as the flat lower
        # flange and must not be collapsed into a single “翼” row.
        lines = [
            # flat lower flange, 20 mm thick
            ((0, 0), (1000, 0)), ((0, 20), (1000, 20)),
            ((0, 0), (0, 20)), ((1000, 0), (1000, 20)),
            # crowned upper flange, still 20 mm thick and with equal endpoints
            ((0, 600), (500, 660)), ((500, 660), (1000, 600)),
            ((0, 580), (500, 640)), ((500, 640), (1000, 580)),
            ((0, 580), (0, 600)), ((1000, 580), (1000, 600)),
            # physical web boundaries
            ((0, 20), (0, 580)), ((1000, 20), (1000, 580)),
        ]
        result = self.analyzer.analyze(
            self._drawing("different_full_flange_shape.dxf", lines, "BH600*250*12*20", 1000)
        )
        self.assertEqual(result.status, "OK", msg=str(result.warnings))
        self.assertEqual(set(self._roles(result)), {"腹", "上翼", "下翼"})

    def test_ascii_and_ezdxf_normalize_zero_shape_evidence_identically(self):
        # This real drawing used to produce a textual-only backend difference:
        # one parser emitted a tiny negative floating point centre delta as
        # “-0.000 mm”.  The read result and its trace evidence must be stable
        # across the two officially supported backends.
        path = self._large_dxf("BYSJ@零件图@3t1-cb-8_拆板前.dxf")
        ascii_result = self.analyzer.analyze(read_ascii_dxf(path))
        ezdxf_result = self.analyzer.analyze(read_ezdxf(path))
        self.assertEqual(ascii_result.status, "OK")
        self.assertEqual(ezdxf_result.status, "OK")
        ascii_rows = [
            (item.role, item.left_raw, item.right_raw, item.left_safe, item.right_safe, item.evidence)
            for item in ascii_result.measurements
        ]
        ezdxf_rows = [
            (item.role, item.left_raw, item.right_raw, item.left_safe, item.right_safe, item.evidence)
            for item in ezdxf_result.measurements
        ]
        self.assertEqual(ascii_rows, ezdxf_rows)

    def test_real_nonmatching_flange_profiles_are_not_merged(self):
        # These five drawings have matching (or nearly matching) horizontal
        # setbacks but visibly different upper/lower longitudinal profiles.
        # They were reviewed in the v1.2.2 shape-audit figures; grouping them
        # as a single “翼” would discard the required plate distinction.
        samples = (
            (self._pre_dxf, "2b2-cb-10_拆板前.dxf", {"腹", "上翼", "下翼"}),
            (self._pre_dxf, "z-4-cb-42_拆板前.dxf", {"腹", "上翼", "下翼"}),
            (self._large_dxf, "BYSJ@零件图@3t1-cb-12_拆板前.dxf", {"腹", "上翼", "下翼"}),
            (self._large_dxf, "BYSJ@零件图@3t1-cb-135_拆板前.dxf", {"腹", "上翼", "下翼"}),
            (self._large_dxf, "BYSJ@零件图@4t1-cb-59_拆板前.dxf", {"腹", "上翼", "下翼"}),
        )
        for resolver, name, expected_roles in samples:
            result = self.analyzer.analyze(read_ascii_dxf(resolver(name)))
            self.assertEqual(result.status, "OK", msg=f"{name}: {result.warnings}")
            self.assertEqual(set(self._roles(result)), expected_roles, msg=name)

    def test_uncertain_one_sample_endpoint_does_not_invent_unequal_flange(self):
        path = self._pre_dxf("2b1-cb-26_拆板前.dxf")
        result = self.analyzer.analyze(read_ascii_dxf(path))
        self.assertEqual(result.status, "OK")
        # The upper closure line extends beyond the inner flange face.  It still
        # covers the complete flange thickness and is recovered as the same
        # physical endpoint as the lower flange.
        self.assertEqual(set(self._roles(result)), {"腹", "翼"})
        self.assertEqual(self._roles(result)["翼"], (0, 432))

    def test_internal_full_thickness_line_far_from_material_end_is_rejected(self):
        length = 1200.0
        physical_end = 1000.0
        lines = [
            # lower and upper material continue to x=1000 but have no closure
            ((0, 0), (physical_end, 0)), ((0, 30), (physical_end, 30)),
            ((0, 1470), (physical_end, 1470)), ((0, 1500), (physical_end, 1500)),
            # internal construction lines span full thickness at x=900
            ((900, 0), (900, 30)), ((900, 1470), (900, 1500)),
            # main-view/web bounds
            ((0, 0), (0, 1500)), ((length, 30), (length, 1470)),
        ]
        result = self.analyzer.analyze(
            self._drawing("internal_covering_line.dxf", lines, "BH1500*500*30*30", length)
        )
        self.assertEqual(result.status, "OK", msg=str(result.warnings))
        wing = self._roles(result)["翼"]
        # The internal x=900 line would yield right-in 300 mm and is unsafe.
        # Rejection keeps the result near the true 200 mm boundary.
        self.assertLess(wing[1], 220)

    def test_flange_end_line_may_extend_past_inner_face(self):
        # The upper end line covers the full 30 mm flange thickness but
        # continues another 20 mm into the web/notch region.  This is still
        # the physical flange endpoint and must be recovered exactly rather
        # than replaced by a one-sample outward fallback.
        length = 1032.175754
        flange_end = 600.0
        lines = [
            # lower flange
            ((0, 0), (flange_end, 0)), ((0, 30), (flange_end, 30)),
            ((flange_end, 0), (flange_end, 30)),
            # upper flange
            ((0, 1470), (flange_end, 1470)), ((0, 1500), (flange_end, 1500)),
            # closure extends below the inner face to y=1450
            ((flange_end, 1450), (flange_end, 1500)),
            # member/web boundaries
            ((0, 0), (0, 1500)), ((length, 30), (length, 1470)),
        ]
        result = self.analyzer.analyze(
            self._drawing(
                "extended_flange_end.dxf", lines, "BH1500*500*30*30", length
            )
        )
        self.assertEqual(result.status, "OK", msg=str(result.warnings))
        self.assertEqual(self._roles(result)["翼"], (0, 432))

    def test_small_real_flange_inset_is_recovered_before_flush_fallback(self):
        # An endpoint can be only a few millimetres inside the global member
        # boundary.  Its exact closure line must win over the sampling guard;
        # otherwise 1 mm and 2 mm are incorrectly emitted as zero.
        length = 4000.0
        lines = [
            # lower flange and web are flush with the member
            ((0, 0), (length, 0)), ((0, 20), (length, 20)),
            ((0, 0), (0, 20)), ((length, 0), (length, 20)),
            # upper flange has physical 1 mm / 2 mm setbacks
            ((1, 580), (length - 2, 580)), ((1, 600), (length - 2, 600)),
            ((1, 580), (1, 600)), ((length - 2, 580), (length - 2, 600)),
            ((0, 20), (0, 580)), ((length, 20), (length, 580)),
        ]
        result = self.analyzer.analyze(
            self._drawing("small_flange_inset.dxf", lines, "BH600*250*12*20", length)
        )
        self.assertEqual(result.status, "OK", msg=str(result.warnings))
        self.assertEqual(
            self._roles(result),
            {"腹": (0, 0), "上翼": (1, 2), "下翼": (0, 0)},
        )

    def test_near_internal_flange_line_cannot_override_missing_flush_closure(self):
        # The flange material itself starts at the main-view left boundary,
        # but its outer closure line is absent.  A nearby full-thickness
        # construction line must not become a fabricated 1 mm setback just
        # because it is aligned with the sampled material boundary.
        length = 4000.0
        lines = [
            # Both flanges are geometrically flush, with no x=0 closure line.
            ((0, 0), (length, 0)), ((0, 20), (length, 20)),
            ((length, 0), (length, 20)),
            ((0, 580), (length, 580)), ((0, 600), (length, 600)),
            ((length, 580), (length, 600)),
            # These are internal construction lines, not material endpoints.
            ((1, 0), (1, 20)), ((1, 580), (1, 600)),
            ((0, 20), (0, 580)), ((length, 20), (length, 580)),
        ]
        result = self.analyzer.analyze(
            self._drawing("near_internal_flange_line.dxf", lines, "BH600*250*12*20", length)
        )
        self.assertEqual(result.status, "OK", msg=str(result.warnings))
        self.assertEqual(self._roles(result), {"腹": (0, 0), "翼": (0, 0)})

    def test_fragmented_flange_surface_does_not_turn_internal_line_into_endpoint(self):
        # The same unsafe situation remains when DXF happens to fragment both
        # continuous flange surfaces at the internal-line X coordinate.
        # Endpoint attachment alone is not enough: material still exists on
        # the outer side of x=1, so this cannot be a physical plate end.
        length = 4000.0
        lines = [
            ((0, 0), (1, 0)), ((1, 0), (length, 0)),
            ((0, 20), (1, 20)), ((1, 20), (length, 20)),
            ((length, 0), (length, 20)),
            ((0, 580), (1, 580)), ((1, 580), (length, 580)),
            ((0, 600), (1, 600)), ((1, 600), (length, 600)),
            ((length, 580), (length, 600)),
            ((1, 0), (1, 20)), ((1, 580), (1, 600)),
            ((0, 20), (0, 580)), ((length, 20), (length, 580)),
        ]
        result = self.analyzer.analyze(
            self._drawing("fragmented_internal_flange_line.dxf", lines, "BH600*250*12*20", length)
        )
        self.assertEqual(result.status, "OK", msg=str(result.warnings))
        self.assertEqual(self._roles(result), {"腹": (0, 0), "翼": (0, 0)})

    def test_parameterized_realistic_insets_and_near_web_boundaries(self):
        """Exercise 400 deterministic valid BH geometries around the fix.

        Each case has exact vector closure lines for a non-flush flange and
        full-depth transverse web end lines inside the rule-defined end
        window.  It combines short 1--3 mm flange setbacks, larger ordinary
        setbacks, both upper/lower flange orientations, and two member
        lengths.  This protects the geometry contract without random samples.
        """
        flange_lefts = (1.0, 3.0, 17.0, 95.0, 333.0)
        flange_rights = (2.0, 7.0, 29.0, 113.0, 457.0)

        def plate(y_low: float, y_high: float, left: float, right: float):
            return [
                ((left, y_low), (right, y_low)),
                ((left, y_high), (right, y_high)),
                ((left, y_low), (left, y_high)),
                ((right, y_low), (right, y_high)),
            ]

        cases = 0
        for length in (4000.0, 8000.0):
            web_boundaries = (
                (0.0, length),
                (50.0, length - 50.0),
                (250.0, length - 250.0),
                (0.09 * length, length - 0.09 * length),
            )
            for inset_side in ("upper", "lower"):
                for left in flange_lefts:
                    for right in flange_rights:
                        for web_left, web_right in web_boundaries:
                            if inset_side == "upper":
                                lines = (
                                    plate(0.0, 20.0, 0.0, length)
                                    + plate(580.0, 600.0, left, length - right)
                                )
                                expected = {
                                    "腹": (int(web_left), int(length - web_right)),
                                    "上翼": (int(left), int(right)),
                                    "下翼": (0, 0),
                                }
                            else:
                                lines = (
                                    plate(0.0, 20.0, left, length - right)
                                    + plate(580.0, 600.0, 0.0, length)
                                )
                                expected = {
                                    "腹": (int(web_left), int(length - web_right)),
                                    "上翼": (0, 0),
                                    "下翼": (int(left), int(right)),
                                }
                            lines.extend(
                                [
                                    ((web_left, 20.0), (web_left, 580.0)),
                                    ((web_right, 20.0), (web_right, 580.0)),
                                ]
                            )
                            result = self.analyzer.analyze(
                                self._drawing(
                                    f"parameterized-{cases}.dxf",
                                    lines,
                                    "BH600*250*12*20",
                                    length,
                                )
                            )
                            self.assertEqual(result.status, "OK", msg=str(result.warnings))
                            self.assertEqual(self._roles(result), expected)
                            cases += 1
        self.assertEqual(cases, 400)

    def test_2b1_cb_26_recovers_exact_common_flange_endpoint(self):
        path = self._pre_dxf("2b1-cb-26_拆板前.dxf")
        result = self.analyzer.analyze(read_ascii_dxf(path))
        self.assertEqual(result.status, "OK", msg=str(result.warnings))
        self.assertEqual(self._roles(result)["翼"], (0, 432))
        plate = result.diagnostics["plate_identification"]
        upper = plate["upper_flange_pieces"][0]
        lower = plate["lower_flange_pieces"][0]
        self.assertAlmostEqual(upper["right_offset_mm"], lower["right_offset_mm"], places=3)
        self.assertNotIn("采样步长", " | ".join(upper["evidence"]))

    def test_tapered_flange_long_edge_is_not_web_end(self):
        length = 2383.0
        web_right = 2148.0
        lines = [
            # lower flange
            ((0, 0), (length, 0)), ((0, 40), (length, 40)),
            ((length, 0), (length, 40)),
            # tapered upper flange: these long sloping lines cross the web core
            # but are longitudinal flange boundaries, not web ends
            ((0, 1460), (length, 628)), ((0, 1500), (length, 668)),
            ((length, 628), (length, 668)),
            # member left boundary and physical web right end
            ((0, 0), (0, 1500)), ((web_right, 40), (web_right, 710)),
        ]
        result = self.analyzer.analyze(
            self._drawing(
                "tapered_web_end.dxf", lines, "BH1500-750*500*30*40", length
            )
        )
        self.assertEqual(result.status, "OK", msg=str(result.warnings))
        self.assertEqual(self._roles(result)["腹"], (0, 235))

    def test_sloped_web_end_uses_the_physical_boundary_bbox(self):
        # The web end is a sloped physical boundary.  Its leftmost material X
        # is at the upper endpoint (100), not where that line enters an
        # artificially trimmed web-core band.  This is the generic form of the
        # 3t1-cb-4 failure and protects the bbox rule without sample constants.
        length = 4000.0
        lines = [
            ((0, 0), (length, 0)), ((0, 20), (length, 20)),
            ((0, 0), (0, 20)), ((length, 0), (length, 20)),
            ((0, 580), (length, 580)), ((0, 600), (length, 600)),
            ((0, 580), (0, 600)), ((length, 580), (length, 600)),
            # Two source edges describing a 12 mm web-thickness projection.
            ((300, 20), (100, 580)),
            ((312, 20), (112, 580)),
            ((length - 10, 20), (length - 10, 580)),
        ]
        result = self.analyzer.analyze(
            self._drawing("sloped_web_bbox.dxf", lines, "BH600*250*12*20", length)
        )
        self.assertEqual(result.status, "OK", msg=str(result.warnings))
        self.assertEqual(self._roles(result)["腹"], (100, 10))
        web = result.diagnostics["plate_identification"]["web"]
        self.assertAlmostEqual(web["left_offset_mm"], 100.0, places=6)

    def test_web_bbox_stops_at_the_flange_inner_surfaces(self):
        # A cutting line may continue through the flange bands.  Its complete
        # source LINE bbox is then wider than the web plate itself.  Ownership
        # is bounded by the actual flange inner surfaces (Y=20..580), not by
        # either the artificial core margin or the source extension in flanges.
        length = 4000.0
        lines = [
            ((0, 0), (length, 0)), ((0, 20), (length, 20)),
            ((0, 0), (0, 20)), ((length, 0), (length, 20)),
            ((0, 580), (length, 580)), ((0, 600), (length, 600)),
            ((0, 580), (0, 600)), ((length, 580), (length, 600)),
            # At the real web limits these edges span X=106.667..293.333
            # and X=118.667..305.333; source endpoints X=100/300 lie in
            # the flange bands and do not belong to the web plate bbox.
            ((300, 0), (100, 600)),
            ((312, 0), (112, 600)),
            ((length - 10, 20), (length - 10, 580)),
        ]
        result = self.analyzer.analyze(
            self._drawing("web_edge_extended_into_flange.dxf", lines, "BH600*250*12*20", length)
        )
        self.assertEqual(result.status, "OK", msg=str(result.warnings))
        self.assertEqual(self._roles(result)["腹"], (106, 10))
        web = result.diagnostics["plate_identification"]["web"]
        self.assertAlmostEqual(web["left_offset_mm"], 106.6666666667, places=6)

    def test_tapered_web_bbox_follows_local_flange_inner_surfaces(self):
        # A global median web band is wrong for a tapered BH.  Construct one
        # sloped web end that meets the lower inner surface at X=300 and the
        # locally sloped upper inner surface at X=100, then extend the same
        # source line through both flange bands.  The web plate bbox starts at
        # the local intersection X=100, not at a median-height clip.
        length = 2383.0
        upper_inner_at_100 = 1460.0 + (628.0 - 1460.0) * 100.0 / length
        dy = upper_inner_at_100 - 40.0
        lower_extension = -40.0 / dy
        upper_extension = 40.0 / dy
        source_start = (300.0 - 200.0 * lower_extension, 0.0)
        source_end = (100.0 - 200.0 * upper_extension, upper_inner_at_100 + 40.0)
        lines = [
            ((0, 0), (length, 0)), ((0, 40), (length, 40)),
            ((0, 0), (0, 40)), ((length, 0), (length, 40)),
            ((0, 1460), (length, 628)), ((0, 1500), (length, 668)),
            ((0, 1460), (0, 1500)), ((length, 628), (length, 668)),
            (source_start, source_end),
            ((2148, 40), (2148, 710)),
        ]
        result = self.analyzer.analyze(
            self._drawing(
                "tapered_local_web_bbox.dxf", lines, "BH1500-750*500*30*40", length
            )
        )
        self.assertEqual(result.status, "OK", msg=str(result.warnings))
        self.assertEqual(self._roles(result)["腹"], (100, 235))
        web = result.diagnostics["plate_identification"]["web"]
        self.assertAlmostEqual(web["left_offset_mm"], 100.0, places=5)

    def test_parameterized_sloped_web_bboxes_do_not_depend_on_core_clipping(self):
        # Sweep direction, angle, setback and member length.  The expected
        # values come only from each paired source boundary's X bbox, so this
        # guards the rule rather than memorising either real drawing.
        left_edges = ((60.0, 240.0), (240.0, 60.0), (0.0, 320.0), (320.0, 0.0))
        right_insets = ((70.0, 260.0), (260.0, 70.0), (0.0, 340.0), (340.0, 0.0))
        cases = 0
        for length in (4000.0, 8000.0):
            for left_bottom, left_top in left_edges:
                for right_bottom_inset, right_top_inset in right_insets:
                    right_bottom = length - right_bottom_inset
                    right_top = length - right_top_inset
                    lines = [
                        ((0, 0), (length, 0)), ((0, 20), (length, 20)),
                        ((0, 0), (0, 20)), ((length, 0), (length, 20)),
                        ((0, 580), (length, 580)), ((0, 600), (length, 600)),
                        ((0, 580), (0, 600)), ((length, 580), (length, 600)),
                        ((left_bottom, 20), (left_top, 580)),
                        ((left_bottom + 12, 20), (left_top + 12, 580)),
                        ((right_bottom, 20), (right_top, 580)),
                        ((right_bottom - 12, 20), (right_top - 12, 580)),
                        # A distant stiffener remains irrelevant to both ends.
                        ((0.5 * length, 20), (0.5 * length, 580)),
                    ]
                    result = self.analyzer.analyze(
                        self._drawing(
                            f"sloped-web-sweep-{cases}.dxf",
                            lines,
                            "BH600*250*12*20",
                            length,
                        )
                    )
                    self.assertEqual(result.status, "OK", msg=str(result.warnings))
                    expected_left = int(min(left_bottom, left_top))
                    expected_right = int(length - max(right_bottom, right_top))
                    self.assertEqual(
                        self._roles(result)["腹"],
                        (expected_left, expected_right),
                        msg=f"case={cases}",
                    )
                    cases += 1
        self.assertEqual(cases, 32)

    def test_3t1_cb_4_plate_ownership_and_bboxes(self):
        path = self._large_dxf("BYSJ@零件图@3t1-cb-4_拆板前.dxf")
        for reader in (read_ascii_dxf, read_ezdxf):
            drawing = reader(path)
            result = self.analyzer.analyze(drawing)
            self.assertEqual(result.status, "OK", msg=f"{reader.__name__}: {result.warnings}")
            self.assertEqual(
                self._roles(result),
                {"腹": (115, 14), "上翼": (0, 0), "下翼": (689, 0)},
                msg=reader.__name__,
            )

            spec = self.analyzer._extract_spec(drawing.texts)
            front, _, _ = self.analyzer._step1_locate_main_view(drawing, spec)
            self.assertIsNotNone(front)
            assert front is not None
            flange = result.diagnostics["flange_analysis"]
            lower_handles = {
                front.segments[index].source_handle
                for index in flange["lower"]["selected_segment_ids"]
            }
            upper_handles = {
                front.segments[index].source_handle
                for index in flange["upper"]["selected_segment_ids"]
            }
            # B8 is the diagonal web boundary.  B6/DE and E3/F9/102 are
            # isolated or redundant detail fragments, not flange outlines.
            self.assertTrue({"B8", "B6", "DE"}.isdisjoint(lower_handles))
            self.assertTrue({"E3", "F9", "102"}.isdisjoint(upper_handles))

    def test_distant_full_height_internal_line_is_not_web_boundary(self):
        # Without a near-end web boundary, a full-height distant line is
        # indistinguishable from an internal stiffener using geometry alone.
        # It must therefore not create an unsafe large web setback.
        length = 4000.0
        lines = [
            ((0, 0), (length, 0)), ((0, 20), (length, 20)),
            ((0, 0), (0, 20)), ((length, 0), (length, 20)),
            ((0, 580), (length, 580)), ((0, 600), (length, 600)),
            ((0, 580), (0, 600)), ((length, 580), (length, 600)),
            ((2000, 20), (2000, 580)),
        ]
        result = self.analyzer.analyze(
            self._drawing("internal_web_stiffener.dxf", lines, "BH600*250*12*20", length)
        )
        self.assertEqual(result.status, "OK", msg=str(result.warnings))
        self.assertEqual(self._roles(result), {"腹": (0, 0), "翼": (0, 0)})
        evidence = next(item.evidence for item in result.measurements if item.role == "腹")
        self.assertIn("齐平保守回退", evidence)

    def test_2b1_cb_40_is_normal_three_plate_tapered_bh(self):
        path = self._pre_dxf("2b1-cb-40_拆板前.dxf")
        result = self.analyzer.analyze(read_ascii_dxf(path))
        self.assertEqual(result.status, "OK")
        plate = result.diagnostics["plate_identification"]
        self.assertEqual(plate["upper_flange_count"], 1)
        self.assertEqual(plate["lower_flange_count"], 1)
        # The tapered upper flange reaches the member end, but the web ends
        # earlier.  The outermost physical web end is approximately
        # 235.076 mm from the right member boundary, so the safe value is 235.
        self.assertEqual(self._roles(result), {"腹": (0, 235), "上翼": (0, 0), "下翼": (0, 0)})
        web = plate["web"]
        self.assertAlmostEqual(web["right_offset_mm"], 235.075757857688, places=3)

    def test_unequal_upper_lower_flange_are_named_explicitly(self):
        path = self._pre_dxf("3b2-cb-86_拆板前.dxf")
        result = self.analyzer.analyze(read_ascii_dxf(path))
        self.assertEqual(result.status, "OK")
        # The paired sloped web-end entities reach X=3517.592/3501.589.
        # Reading their complete source bbox gives 150.874 mm to the member
        # right boundary; clipping them to the web core manufactured 160 mm.
        self.assertEqual(
            self._roles(result),
            {"腹": (0, 150), "上翼": (0, 360), "下翼": (0, 0)},
        )

    def test_original_w3_regression(self):
        expected = {
            "w3-cb-5.dxf": {"腹": (13, 0), "翼": (0, 0)},
            "w3-cb-7.dxf": {"腹": (63, 0), "翼": (0, 0)},
            "w3-cb-9.dxf": {"腹": (0, 78), "翼": (0, 0)},
            "w3-cb-17.dxf": {"腹": (28, 28), "翼": (0, 0)},
        }
        for name, target in expected.items():
            result = self.analyzer.analyze(read_ascii_dxf(self._project1_dxf(name)))
            self.assertEqual(self._roles(result), target)

    def test_known_dimension_and_bevel_regression(self):
        expected = {
            "2t2-cb-37_拆板前.dxf": ("腹", 10, 60),
            "h-3-cb-53_拆板前.dxf": ("翼", 0, 2379),
            # The physical flange closure is exactly 235 mm from the view
            # boundary.  The old 234.238 mm value was a sampling fallback, not
            # an actual plate point.
            "3t2-cb-6_拆板前.dxf": ("翼", 235, 0),
        }
        for name, (role, left, right) in expected.items():
            result = self.analyzer.analyze(read_ascii_dxf(self._pre_dxf(name)))
            item = next(value for value in result.measurements if value.role == role)
            self.assertEqual((item.left_safe, item.right_safe), (left, right))

    def test_horizontal_rule_does_not_rotate_member(self):
        lines = [
            ((0, 0), (100, 10)), ((0, 20), (100, 30)),
            ((0, 580), (100, 590)), ((0, 600), (100, 610)),
            ((0, 0), (0, 600)), ((100, 10), (100, 610)),
        ]
        drawing = self._drawing("horizontal_rule.dxf", lines, "BH600*250*12*20", 100)
        candidates = self.analyzer._view_candidates(drawing.primitives, strict_layers=True, spec=None)
        self.assertEqual(candidates[0].axis, (1.0, 0.0))
        self.assertAlmostEqual(candidates[0].length, 100.0)

    def test_scaled_coordinates_are_converted_to_millimetres(self):
        lines = [
            ((10, 0), (100, 0)), ((10, 2), (100, 2)),
            ((10, 58), (100, 58)), ((10, 60), (100, 60)),
            ((10, 0), (10, 2)), ((100, 0), (100, 2)),
            ((10, 58), (10, 60)), ((100, 58), (100, 60)),
            ((0, 2), (0, 58)), ((80, 2), (80, 58)),
        ]
        result = self.analyzer.analyze(
            self._drawing("scaled_1_to_10.dxf", lines, "BH600*250*12*20", 1000)
        )
        self.assertEqual(result.status, "OK")
        self.assertEqual(result.diagnostics["units"]["coordinate_unit_to_mm"], 10.0)
        self.assertEqual(self._roles(result), {"腹": (0, 200), "翼": (100, 0)})

    def test_title_scale_is_not_coordinate_scale(self):
        lines = [
            ((100, 0), (1000, 0)), ((100, 20), (1000, 20)),
            ((100, 580), (1000, 580)), ((100, 600), (1000, 600)),
            ((100, 0), (100, 20)), ((1000, 0), (1000, 20)),
            ((100, 580), (100, 600)), ((1000, 580), (1000, 600)),
            ((0, 20), (0, 580)), ((800, 20), (800, 580)),
        ]
        drawing = self._drawing("paper_scale.dxf", lines, "BH600*250*12*20", 1000)
        scale_text = Primitive("TEXT", "OtherObjectType", [(30, -100)], "TABLE", text="1:10")
        drawing.texts.append(scale_text)
        drawing.primitives.append(scale_text)
        drawing.insunits_code = 4
        drawing.insunits_name = "millimetre"
        drawing.header_unit_to_mm = 1.0
        result = self.analyzer.analyze(drawing)
        self.assertEqual(result.status, "OK")
        self.assertEqual(result.diagnostics["units"]["coordinate_unit_to_mm"], 1.0)
        self.assertFalse(result.diagnostics["units"]["title_scale_used_for_coordinate_conversion"])

    def test_unverified_scale_blocks_output(self):
        lines = [
            ((10, 0), (137, 0)), ((10, 2), (137, 2)),
            ((10, 58), (137, 58)), ((10, 60), (137, 60)),
            ((0, 2), (0, 58)), ((100, 2), (100, 58)),
        ]
        result = self.analyzer.analyze(
            self._drawing("bad_scale.dxf", lines, "BH600*250*12*20", 1000)
        )
        self.assertEqual(result.status, "ERROR_UNIT_SCALE_UNVERIFIED")
        self.assertFalse(result.measurements)

    def test_safe_outputs_never_exceed_raw_values(self):
        for path in self._all_supplied_dxf():
            result = self.analyzer.analyze(read_ascii_dxf(path))
            for item in result.measurements:
                self.assertLessEqual(item.left_safe, item.left_raw + 1e-12, msg=path.name)
                self.assertLessEqual(item.right_safe, item.right_raw + 1e-12, msg=path.name)

    def test_conservative_floor(self):
        self.assertEqual(self.analyzer._safe_integer(13.999), 13)
        self.assertEqual(self.analyzer._safe_integer(2379.999999), 2379)
        self.assertEqual(self.analyzer._safe_integer(14.0), 14)
        self.assertEqual(self.analyzer._safe_integer(-0.01), 0)

    def test_ascii_polyline_outline_is_usable_bh_geometry(self):
        def polyline(points: list[tuple[int, int]]) -> str:
            records = ["0", "POLYLINE", "8", "Part", "66", "1", "70", "1"]
            for x_value, y_value in points:
                records.extend(["0", "VERTEX", "8", "Part", "10", str(x_value), "20", str(y_value)])
            records.extend(["0", "SEQEND"])
            return "\n".join(records)

        payload = "\n".join([
            "0", "SECTION", "2", "HEADER", "9", "$INSUNITS", "70", "4", "0", "ENDSEC",
            "0", "SECTION", "2", "ENTITIES",
            polyline([(0, 0), (1000, 0), (1000, 20), (0, 20)]),
            polyline([(0, 580), (1000, 580), (1000, 600), (0, 600)]),
            polyline([(0, 20), (1000, 20), (1000, 580), (0, 580)]),
            "0", "TEXT", "8", "OtherObjectType", "10", "0", "20", "-100", "1", "BH600*250*12*20",
            "0", "TEXT", "8", "OtherObjectType", "10", "10", "20", "-100", "1", "1000",
            "0", "ENDSEC", "0", "EOF", "",
        ])
        with NamedTemporaryFile(suffix=".dxf") as temporary:
            temporary.write(payload.encode("ascii"))
            temporary.flush()
            result = self.analyzer.analyze(read_ascii_dxf(Path(temporary.name)))
        self.assertEqual(result.status, "OK", msg=str(result.warnings))
        self.assertEqual(self._roles(result), {"腹": (0, 0), "翼": (0, 0)})

    def test_bulged_lwpolyline_flange_end_uses_true_outer_x(self):
        """A bulge cap is material, not the straight chord between its vertices.

        Each flange ends in a semicircular LWPOLYLINE bulge from X=1000 to
        the physical X=1010.  The web reaches X=1010 as the member datum.
        Dropping group-code 42 turns the bulge into a chord at X=1000 and
        produces an unsafe fabricated 10 mm flange right setback.
        """
        import ezdxf

        with TemporaryDirectory() as directory:
            path = Path(directory) / "bulged_flange_end.dxf"
            document = ezdxf.new("R2000")
            document.header["$INSUNITS"] = 4
            space = document.modelspace()
            for points in (
                # Positive bulge from (1000, y) to (1000, y + 20) is a
                # right-facing semicircle with exact X maximum 1010 mm.
                [(0, 0, 0, 0, 0), (1000, 0, 0, 0, 1), (1000, 20, 0, 0, 0), (0, 20, 0, 0, 0)],
                [(0, 580, 0, 0, 0), (1000, 580, 0, 0, 1), (1000, 600, 0, 0, 0), (0, 600, 0, 0, 0)],
                [(0, 20), (1010, 20), (1010, 580), (0, 580)],
            ):
                space.add_lwpolyline(
                    points, format="xyseb", close=True, dxfattribs={"layer": "Part"}
                )
            space.add_text(
                "BH600*250*12*20",
                dxfattribs={"layer": "OtherObjectType", "height": 10},
            ).set_placement((0, -100))
            space.add_text(
                "1010", dxfattribs={"layer": "OtherObjectType", "height": 10}
            ).set_placement((10, -100))
            document.saveas(path)

            for reader in (read_ascii_dxf, read_ezdxf):
                drawing = reader(path)
                flange_outlines = [
                    primitive
                    for primitive in drawing.primitives
                    if primitive.kind == "LWPOLYLINE"
                    and primitive.layer == "Part"
                    and max(point[1] for point in primitive.points) - min(point[1] for point in primitive.points) <= 20.0
                ]
                self.assertEqual(len(flange_outlines), 2)
                self.assertEqual(
                    [max(point[0] for point in outline.points) for outline in flange_outlines],
                    [1010.0, 1010.0],
                    msg=reader.__name__,
                )
                result = self.analyzer.analyze(drawing)
                self.assertEqual(result.status, "OK", msg=f"{reader.__name__}: {result.warnings}")
                self.assertEqual(self._roles(result), {"腹": (0, 0), "翼": (0, 0)})

    def test_bulge_direction_and_legacy_polyline_preserve_safe_flange_ends(self):
        """Both signed bulges and both DXF polyline entities keep outer ends."""
        import ezdxf

        cases = {
            "right": (
                [(0, 0, 0, 0, 0), (1000, 0, 0, 0, 1), (1000, 20, 0, 0, 0), (0, 20, 0, 0, 0)],
                [(0, 580, 0, 0, 0), (1000, 580, 0, 0, 1), (1000, 600, 0, 0, 0), (0, 600, 0, 0, 0)],
            ),
            "left": (
                [(10, 0, 0, 0, -1), (10, 20, 0, 0, 0), (1010, 20, 0, 0, 0), (1010, 0, 0, 0, 0)],
                [(10, 580, 0, 0, -1), (10, 600, 0, 0, 0), (1010, 600, 0, 0, 0), (1010, 580, 0, 0, 0)],
            ),
        }

        def add_outline(space, points, *, legacy: bool):
            if not legacy:
                return space.add_lwpolyline(
                    points, format="xyseb", close=True, dxfattribs={"layer": "Part"}
                )
            outline = space.add_polyline2d(
                [(point[0], point[1]) for point in points],
                close=True,
                dxfattribs={"layer": "Part"},
            )
            for vertex, point in zip(outline.vertices, points, strict=True):
                vertex.dxf.bulge = point[4]
            return outline

        with TemporaryDirectory() as directory:
            for side, (lower, upper) in cases.items():
                for legacy in (False, True):
                    path = Path(directory) / f"{side}_{'polyline' if legacy else 'lwpolyline'}.dxf"
                    document = ezdxf.new("R2000")
                    document.header["$INSUNITS"] = 4
                    space = document.modelspace()
                    add_outline(space, lower, legacy=legacy)
                    add_outline(space, upper, legacy=legacy)
                    space.add_lwpolyline(
                        [(0, 20), (1010, 20), (1010, 580), (0, 580)],
                        close=True,
                        dxfattribs={"layer": "Part"},
                    )
                    space.add_text(
                        "BH600*250*12*20",
                        dxfattribs={"layer": "OtherObjectType", "height": 10},
                    ).set_placement((0, -100))
                    space.add_text(
                        "1010", dxfattribs={"layer": "OtherObjectType", "height": 10}
                    ).set_placement((10, -100))
                    document.saveas(path)
                    for reader in (read_ascii_dxf, read_ezdxf):
                        result = self.analyzer.analyze(reader(path))
                        self.assertEqual(
                            result.status,
                            "OK",
                            msg=f"{side} legacy={legacy} {reader.__name__}: {result.warnings}",
                        )
                        self.assertEqual(
                            self._roles(result),
                            {"腹": (0, 0), "翼": (0, 0)},
                            msg=f"{side} legacy={legacy} {reader.__name__}",
                        )

    def test_arc_crossing_cardinal_angle_keeps_exact_horizontal_envelope(self):
        # The physical rightmost/leftmost points are exactly (+100, 0) and
        # (-100, 0).  A fixed-degree sampler whose points happen to miss 0°
        # or 180° underestimates the global X envelope, which could make a
        # right or left setback unsafe.
        payload = "\n".join([
            "0", "SECTION", "2", "HEADER", "9", "$INSUNITS", "70", "4", "0", "ENDSEC",
            "0", "SECTION", "2", "ENTITIES",
            "0", "ARC", "8", "Part", "10", "0", "20", "0", "40", "100", "50", "350", "51", "20",
            "0", "ARC", "8", "Part", "10", "0", "20", "0", "40", "100", "50", "160", "51", "200",
            "0", "ENDSEC", "0", "EOF", "",
        ])
        with NamedTemporaryFile(suffix=".dxf") as temporary:
            temporary.write(payload.encode("ascii"))
            temporary.flush()
            for reader in (read_ascii_dxf, read_ezdxf):
                drawing = reader(Path(temporary.name))
                arcs = [item for item in drawing.primitives if item.kind == "ARC"]
                self.assertEqual(len(arcs), 2)
                self.assertAlmostEqual(max(point[0] for point in arcs[0].points), 100.0, places=9)
                self.assertAlmostEqual(min(point[0] for point in arcs[1].points), -100.0, places=9)

    def test_insert_rotated_arc_keeps_global_horizontal_extremum(self):
        """A source arc must retain extrema after its block placement transform.

        The local 200°..350° arc is inserted at 17°, so its global course
        crosses 0° at a non-cardinal local angle (343°).  Transforming only
        local cardinal samples loses the global physical right edge and can
        fabricate a positive right setback.
        """
        payload = "\n".join([
            "0", "SECTION", "2", "HEADER", "9", "$INSUNITS", "70", "4", "0", "ENDSEC",
            "0", "SECTION", "2", "BLOCKS",
            "0", "BLOCK", "8", "0", "2", "ARC_BLOCK", "70", "0", "10", "0", "20", "0", "3", "ARC_BLOCK", "1", "",
            "0", "ARC", "8", "Part", "10", "0", "20", "0", "40", "100", "50", "200", "51", "350",
            "0", "ENDBLK",
            "0", "ENDSEC", "0", "SECTION", "2", "ENTITIES",
            "0", "INSERT", "8", "Part", "2", "ARC_BLOCK", "10", "0", "20", "0", "41", "1", "42", "1", "50", "17",
            "0", "ENDSEC", "0", "EOF", "",
        ])
        with NamedTemporaryFile(suffix=".dxf") as temporary:
            temporary.write(payload.encode("ascii"))
            temporary.flush()
            for reader in (read_ascii_dxf, read_ezdxf):
                drawing = reader(Path(temporary.name))
                arcs = [item for item in drawing.primitives if item.kind == "ARC"]
                self.assertEqual(len(arcs), 1, msg=reader.__name__)
                self.assertAlmostEqual(
                    max(point[0] for point in arcs[0].points),
                    100.0,
                    places=9,
                    msg=reader.__name__,
                )

    def test_insert_rotated_bulge_keeps_global_horizontal_extremum(self):
        """The same transform rule applies to a bulge-derived source arc."""
        import ezdxf

        with TemporaryDirectory() as directory:
            path = Path(directory) / "rotated_bulge_block.dxf"
            document = ezdxf.new("R2000")
            document.header["$INSUNITS"] = 4
            block = document.blocks.new("BULGE_BLOCK")
            # Local arc from 200° to 350°: bulge=tan(150°/4).  A 17° INSERT
            # makes its global rightmost point occur at local 343°, not one
            # of the original cardinal samples.
            block.add_lwpolyline(
                [
                    (-93.96926207859084, -34.20201433256687, 0, 0, 0.7673269879789604),
                    (98.4807753012208, -17.364817766693033, 0, 0, 0),
                ],
                format="xyseb",
                dxfattribs={"layer": "Part"},
            )
            document.modelspace().add_blockref(
                "BULGE_BLOCK", (0, 0), dxfattribs={"rotation": 17}
            )
            document.saveas(path)
            for reader in (read_ascii_dxf, read_ezdxf):
                drawing = reader(path)
                polylines = [
                    item for item in drawing.primitives if item.kind == "LWPOLYLINE"
                ]
                self.assertEqual(len(polylines), 1, msg=reader.__name__)
                self.assertAlmostEqual(
                    max(point[0] for point in polylines[0].points),
                    100.0,
                    places=9,
                    msg=reader.__name__,
                )

    def test_non_uniform_insert_with_arc_fails_closed_in_both_readers(self):
        """A circular source under anisotropic INSERT scale is an ellipse.

        It must not yield an apparently exact BH answer when one backend
        cannot represent the transformed source curve.  The reader is allowed
        to reject it, but never to silently continue from incomplete geometry.
        """
        import ezdxf

        with TemporaryDirectory() as directory:
            path = Path(directory) / "non_uniform_arc_block.dxf"
            document = ezdxf.new("R2000")
            document.header["$INSUNITS"] = 4
            block = document.blocks.new("ARC_BLOCK")
            block.add_arc(
                center=(0, 0), radius=100, start_angle=200, end_angle=350,
                dxfattribs={"layer": "Part"},
            )
            document.modelspace().add_blockref(
                "ARC_BLOCK", (0, 0), dxfattribs={"xscale": 2, "yscale": 1}
            )
            document.saveas(path)
            for reader in (read_ascii_dxf, read_ezdxf):
                result = self.analyzer.analyze(reader(path))
                self.assertEqual(
                    result.status,
                    "ERROR_DXF_PARSE_INCOMPLETE",
                    msg=f"{reader.__name__}: {result.warnings}",
                )
                self.assertTrue(
                    any("non-uniform" in warning for warning in result.warnings),
                    msg=f"{reader.__name__}: {result.warnings}",
                )

    def test_native_ellipse_fails_closed_in_both_readers(self):
        """An unsupported native ELLIPSE cannot disappear into an OK result."""
        import ezdxf

        with TemporaryDirectory() as directory:
            path = Path(directory) / "native_ellipse.dxf"
            document = ezdxf.new("R2000")
            document.header["$INSUNITS"] = 4
            document.modelspace().add_ellipse(
                center=(0, 0), major_axis=(100, 0), ratio=0.5,
                dxfattribs={"layer": "Part"},
            )
            document.saveas(path)
            for reader in (read_ascii_dxf, read_ezdxf):
                result = self.analyzer.analyze(reader(path))
                self.assertEqual(
                    result.status,
                    "ERROR_DXF_PARSE_INCOMPLETE",
                    msg=f"{reader.__name__}: {result.warnings}",
                )
                self.assertTrue(
                    any("ELLIPSE" in warning for warning in result.warnings),
                    msg=f"{reader.__name__}: {result.warnings}",
                )

    def test_part_layer_spline_fails_closed_in_both_readers(self):
        """An unsupported material-layer curve must not be silently dropped."""
        import ezdxf

        with TemporaryDirectory() as directory:
            path = Path(directory) / "part_spline.dxf"
            document = ezdxf.new("R2000")
            document.header["$INSUNITS"] = 4
            document.modelspace().add_spline(
                fit_points=[(0, 0), (50, 20), (100, 0)],
                dxfattribs={"layer": "Part"},
            )
            document.saveas(path)
            for reader in (read_ascii_dxf, read_ezdxf):
                result = self.analyzer.analyze(reader(path))
                self.assertEqual(
                    result.status,
                    "ERROR_DXF_PARSE_INCOMPLETE",
                    msg=f"{reader.__name__}: {result.warnings}",
                )
                self.assertTrue(
                    any("SPLINE" in warning for warning in result.warnings),
                    msg=f"{reader.__name__}: {result.warnings}",
                )

    def test_malformed_part_hatch_recovery_fails_closed_in_both_readers(self):
        """A DXF library exception must become a controlled incomplete parse."""
        import ezdxf

        with TemporaryDirectory() as directory:
            path = Path(directory) / "malformed_part_hatch.dxf"
            document = ezdxf.new("R2000")
            document.header["$INSUNITS"] = 4
            document.modelspace().add_hatch(dxfattribs={"layer": "Part"})
            document.saveas(path)
            for reader in (read_ascii_dxf, read_ezdxf):
                result = self.analyzer.analyze(reader(path))
                self.assertEqual(
                    result.status,
                    "ERROR_DXF_PARSE_INCOMPLETE",
                    msg=f"{reader.__name__}: {result.warnings}",
                )
                self.assertTrue(
                    any("HATCH" in warning or "recovery" in warning for warning in result.warnings),
                    msg=f"{reader.__name__}: {result.warnings}",
                )

    def test_part_layer_nondefault_ocs_fails_closed_in_both_readers(self):
        """The reader does not silently interpret non-XY OCS as world XY."""
        import ezdxf

        with TemporaryDirectory() as directory:
            path = Path(directory) / "part_nondefault_ocs.dxf"
            document = ezdxf.new("R2000")
            document.header["$INSUNITS"] = 4
            document.modelspace().add_lwpolyline(
                [(0, 0), (100, 0), (100, 20), (0, 20)],
                close=True,
                dxfattribs={"layer": "Part", "extrusion": (0, 1, 0)},
            )
            document.saveas(path)
            for reader in (read_ascii_dxf, read_ezdxf):
                result = self.analyzer.analyze(reader(path))
                self.assertEqual(
                    result.status,
                    "ERROR_DXF_PARSE_INCOMPLETE",
                    msg=f"{reader.__name__}: {result.warnings}",
                )
                self.assertTrue(
                    any("OCS" in warning for warning in result.warnings),
                    msg=f"{reader.__name__}: {result.warnings}",
                )

    def test_part_arc_circle_nondefault_ocs_fails_closed(self):
        """Circular OCS cannot silently fall through a polyline-only guard."""
        import ezdxf

        with TemporaryDirectory() as directory:
            for name in ("arc", "circle"):
                path = Path(directory) / f"part_{name}_nondefault_ocs.dxf"
                document = ezdxf.new("R2000")
                document.header["$INSUNITS"] = 4
                attributes = {"layer": "Part", "extrusion": (0, 1, 0)}
                if name == "arc":
                    document.modelspace().add_arc(
                        center=(0, 0), radius=100, start_angle=0, end_angle=90,
                        dxfattribs=attributes,
                    )
                else:
                    document.modelspace().add_circle(
                        center=(0, 0), radius=100, dxfattribs=attributes
                    )
                document.saveas(path)
                for reader in (read_ascii_dxf, read_ezdxf):
                    result = self.analyzer.analyze(reader(path))
                    self.assertEqual(
                        result.status,
                        "ERROR_DXF_PARSE_INCOMPLETE",
                        msg=f"{name} {reader.__name__}: {result.warnings}",
                    )
                    self.assertTrue(
                        any("OCS" in warning for warning in result.warnings),
                        msg=f"{name} {reader.__name__}: {result.warnings}",
                    )

    def test_nonfinite_part_ocs_fails_closed_in_both_readers(self):
        """NaN or infinity must not compare equal to the default OCS."""
        import ezdxf

        with TemporaryDirectory() as directory:
            for name, extrusion_x in {"nan": float("nan"), "inf": float("inf")}.items():
                path = Path(directory) / f"part_nonfinite_ocs_{name}.dxf"
                document = ezdxf.new("R2000")
                document.header["$INSUNITS"] = 4
                document.modelspace().add_lwpolyline(
                    [(0, 0), (100, 0), (100, 20), (0, 20)],
                    close=True,
                    dxfattribs={"layer": "Part", "extrusion": (extrusion_x, 0, 1)},
                )
                document.saveas(path)
                for reader in (read_ascii_dxf, read_ezdxf):
                    result = self.analyzer.analyze(reader(path))
                    self.assertEqual(
                        result.status,
                        "ERROR_DXF_PARSE_INCOMPLETE",
                        msg=f"{name} {reader.__name__}: {result.warnings}",
                    )
                    self.assertTrue(
                        any("OCS" in warning for warning in result.warnings),
                        msg=f"{name} {reader.__name__}: {result.warnings}",
                    )

    def test_part_insert_nondefault_ocs_fails_closed_in_both_readers(self):
        """An INSERT placement plane cannot be treated as world XY."""
        import ezdxf

        with TemporaryDirectory() as directory:
            path = Path(directory) / "part_insert_nondefault_ocs.dxf"
            document = ezdxf.new("R2000")
            document.header["$INSUNITS"] = 4
            block = document.blocks.new("PLANE_BLOCK")
            block.add_line((0, 0), (100, 0))
            document.modelspace().add_blockref(
                "PLANE_BLOCK", (0, 0),
                dxfattribs={"layer": "Part", "extrusion": (0, 1, 0)},
            )
            document.saveas(path)
            for reader in (read_ascii_dxf, read_ezdxf):
                result = self.analyzer.analyze(reader(path))
                self.assertEqual(
                    result.status,
                    "ERROR_DXF_PARSE_INCOMPLETE",
                    msg=f"{reader.__name__}: {result.warnings}",
                )
                self.assertTrue(
                    any("INSERT" in warning and "OCS" in warning for warning in result.warnings),
                    msg=f"{reader.__name__}: {result.warnings}",
                )

    def test_part_insert_inherits_zero_layer_spline_as_material(self):
        """Layer 0 block children inherit a material INSERT's layer."""
        import ezdxf

        with TemporaryDirectory() as directory:
            path = Path(directory) / "part_insert_zero_layer_spline.dxf"
            document = ezdxf.new("R2000")
            document.header["$INSUNITS"] = 4
            block = document.blocks.new("SPLINE_BLOCK")
            block.add_spline(fit_points=[(0, 0), (50, 20), (100, 0)])
            document.modelspace().add_blockref(
                "SPLINE_BLOCK", (0, 0), dxfattribs={"layer": "Part"}
            )
            document.saveas(path)
            for reader in (read_ascii_dxf, read_ezdxf):
                result = self.analyzer.analyze(reader(path))
                self.assertEqual(
                    result.status,
                    "ERROR_DXF_PARSE_INCOMPLETE",
                    msg=f"{reader.__name__}: {result.warnings}",
                )
                self.assertTrue(
                    any("SPLINE" in warning for warning in result.warnings),
                    msg=f"{reader.__name__}: {result.warnings}",
                )

    def test_part_insert_inherits_zero_layer_nonuniform_arc_as_material(self):
        """Layer 0 circular children inherit a material INSERT's transform risk."""
        import ezdxf

        with TemporaryDirectory() as directory:
            path = Path(directory) / "part_insert_zero_layer_nonuniform_arc.dxf"
            document = ezdxf.new("R2000")
            document.header["$INSUNITS"] = 4
            block = document.blocks.new("ARC_BLOCK")
            block.add_arc(center=(0, 0), radius=100, start_angle=200, end_angle=350)
            document.modelspace().add_blockref(
                "ARC_BLOCK", (0, 0),
                dxfattribs={"layer": "Part", "xscale": 2, "yscale": 1},
            )
            document.saveas(path)
            for reader in (read_ascii_dxf, read_ezdxf):
                result = self.analyzer.analyze(reader(path))
                self.assertEqual(
                    result.status,
                    "ERROR_DXF_PARSE_INCOMPLETE",
                    msg=f"{reader.__name__}: {result.warnings}",
                )
                self.assertTrue(
                    any("ARC" in warning or "ELLIPSE" in warning for warning in result.warnings),
                    msg=f"{reader.__name__}: {result.warnings}",
                )

    def test_part_insert_missing_block_fails_closed_in_both_readers(self):
        """A material INSERT with no definition can hide source boundaries."""
        payload = "\n".join([
            "0", "SECTION", "2", "HEADER", "9", "$INSUNITS", "70", "4", "0", "ENDSEC",
            "0", "SECTION", "2", "ENTITIES",
            "0", "INSERT", "8", "Part", "2", "MISSING_BLOCK", "10", "0", "20", "0",
            "0", "ENDSEC", "0", "EOF", "",
        ])
        with NamedTemporaryFile(suffix=".dxf") as temporary:
            temporary.write(payload.encode("ascii"))
            temporary.flush()
            for reader in (read_ascii_dxf, read_ezdxf):
                result = self.analyzer.analyze(reader(Path(temporary.name)))
                self.assertEqual(
                    result.status,
                    "ERROR_DXF_PARSE_INCOMPLETE",
                    msg=f"{reader.__name__}: {result.warnings}",
                )
                self.assertTrue(
                    any("missing block" in warning for warning in result.warnings),
                    msg=f"{reader.__name__}: {result.warnings}",
                )

    def test_part_minsert_array_fails_closed_in_both_readers(self):
        """A reader that expands one MINSERT cell must not claim full geometry."""
        import ezdxf

        with TemporaryDirectory() as directory:
            path = Path(directory) / "part_minsert_array.dxf"
            document = ezdxf.new("R2000")
            document.header["$INSUNITS"] = 4
            block = document.blocks.new("ARRAY_BLOCK")
            block.add_line((0, 0), (100, 0), dxfattribs={"layer": "Part"})
            document.modelspace().add_blockref(
                "ARRAY_BLOCK", (0, 0),
                dxfattribs={
                    "layer": "Part",
                    "column_count": 2,
                    "column_spacing": 1000,
                },
            )
            document.saveas(path)
            for reader in (read_ascii_dxf, read_ezdxf):
                result = self.analyzer.analyze(reader(path))
                self.assertEqual(
                    result.status,
                    "ERROR_DXF_PARSE_INCOMPLETE",
                    msg=f"{reader.__name__}: {result.warnings}",
                )
                self.assertTrue(
                    any("MINSERT" in warning for warning in result.warnings),
                    msg=f"{reader.__name__}: {result.warnings}",
                )

    def test_nonpart_insert_with_explicit_part_child_fails_closed(self):
        """A parent decoration layer cannot hide an explicit Part child."""
        import ezdxf

        with TemporaryDirectory() as directory:
            for name, attributes, expected in (
                (
                    "ocs",
                    {"layer": "OtherObjectType", "extrusion": (0, 1, 0)},
                    "OCS",
                ),
                (
                    "array",
                    {
                        "layer": "OtherObjectType",
                        "column_count": 2,
                        "column_spacing": 1000,
                    },
                    "MINSERT",
                ),
            ):
                path = Path(directory) / f"nonpart_insert_explicit_part_{name}.dxf"
                document = ezdxf.new("R2000")
                document.header["$INSUNITS"] = 4
                block = document.blocks.new("EXPLICIT_PART_BLOCK")
                block.add_line((0, 0), (100, 0), dxfattribs={"layer": "Part"})
                document.modelspace().add_blockref(
                    "EXPLICIT_PART_BLOCK", (0, 0), dxfattribs=attributes
                )
                document.saveas(path)
                for reader in (read_ascii_dxf, read_ezdxf):
                    result = self.analyzer.analyze(reader(path))
                    self.assertEqual(
                        result.status,
                        "ERROR_DXF_PARSE_INCOMPLETE",
                        msg=f"{name} {reader.__name__}: {result.warnings}",
                    )
                    self.assertTrue(
                        any(expected in warning for warning in result.warnings),
                        msg=f"{name} {reader.__name__}: {result.warnings}",
                    )

    def test_part_polyline_modes_fails_closed_in_both_readers(self):
        """3D, curve-fit and mesh POLYLINE flags are not 2D source edges."""
        for name, flags in (("curve_fit", 2), ("three_d", 8), ("mesh", 16)):
            payload = "\n".join([
                "0", "SECTION", "2", "HEADER", "9", "$INSUNITS", "70", "4", "0", "ENDSEC",
                "0", "SECTION", "2", "ENTITIES",
                "0", "POLYLINE", "8", "Part", "70", str(flags),
                "0", "VERTEX", "8", "Part", "10", "0", "20", "0",
                "0", "VERTEX", "8", "Part", "10", "100", "20", "0",
                "0", "SEQEND", "0", "ENDSEC", "0", "EOF", "",
            ])
            with NamedTemporaryFile(suffix=".dxf") as temporary:
                temporary.write(payload.encode("ascii"))
                temporary.flush()
                for reader in (read_ascii_dxf, read_ezdxf):
                    result = self.analyzer.analyze(reader(Path(temporary.name)))
                    self.assertEqual(
                        result.status,
                        "ERROR_DXF_PARSE_INCOMPLETE",
                        msg=f"{name} {reader.__name__}: {result.warnings}",
                    )
                    self.assertTrue(
                        any("POLYLINE" in warning for warning in result.warnings),
                        msg=f"{name} {reader.__name__}: {result.warnings}",
                    )

    def test_part_polyline_width_fails_closed_in_both_readers(self):
        """A centerline with width is not the physical outer material edge."""
        import ezdxf

        with TemporaryDirectory() as directory:
            for name in ("lightweight", "legacy"):
                path = Path(directory) / f"part_width_{name}.dxf"
                document = ezdxf.new("R2000")
                document.header["$INSUNITS"] = 4
                if name == "lightweight":
                    document.modelspace().add_lwpolyline(
                        [(0, 0), (100, 0)],
                        dxfattribs={"layer": "Part", "const_width": 80},
                    )
                else:
                    polyline = document.modelspace().add_polyline2d(
                        [(0, 0), (100, 0)], dxfattribs={"layer": "Part"}
                    )
                    polyline.dxf.default_start_width = 80
                    polyline.dxf.default_end_width = 80
                document.saveas(path)
                for reader in (read_ascii_dxf, read_ezdxf):
                    result = self.analyzer.analyze(reader(path))
                    self.assertEqual(
                        result.status,
                        "ERROR_DXF_PARSE_INCOMPLETE",
                        msg=f"{name} {reader.__name__}: {result.warnings}",
                    )
                    self.assertTrue(
                        any("width" in warning for warning in result.warnings),
                        msg=f"{name} {reader.__name__}: {result.warnings}",
                    )

    def test_part_lwpolyline_missing_vertex_coordinate_fails_closed(self):
        """A declared lightweight vertex cannot gain an invented zero Y."""
        import ezdxf

        with TemporaryDirectory() as directory:
            path = Path(directory) / "part_lwpolyline_missing_y.dxf"
            document = ezdxf.new("R2000")
            document.header["$INSUNITS"] = 4
            document.modelspace().add_lwpolyline(
                [(0, 0), (100, 20)], dxfattribs={"layer": "Part"}
            )
            document.saveas(path)
            payload = path.read_text(encoding="utf-8")
            malformed = "\n 10\n100.0\n 20\n20.0\n"
            self.assertIn(malformed, payload)
            path.write_text(payload.replace(malformed, "\n 10\n100.0\n", 1), encoding="utf-8")
            for reader in (read_ascii_dxf, read_ezdxf):
                result = self.analyzer.analyze(reader(path))
                self.assertEqual(
                    result.status,
                    "ERROR_DXF_PARSE_INCOMPLETE",
                    msg=f"{reader.__name__}: {result.warnings}",
                )
                self.assertTrue(
                    any("LWPOLYLINE" in warning for warning in result.warnings),
                    msg=f"{reader.__name__}: {result.warnings}",
                )

    def test_negative_part_arc_radius_fails_closed_in_both_readers(self):
        """A negative DXF radius is malformed, not a circle with abs(radius)."""
        payload = "\n".join([
            "0", "SECTION", "2", "HEADER", "9", "$INSUNITS", "70", "4", "0", "ENDSEC",
            "0", "SECTION", "2", "ENTITIES",
            "0", "ARC", "8", "Part", "10", "0", "20", "0", "40", "-100", "50", "0", "51", "90",
            "0", "ENDSEC", "0", "EOF", "",
        ])
        with NamedTemporaryFile(suffix=".dxf") as temporary:
            temporary.write(payload.encode("ascii"))
            temporary.flush()
            for reader in (read_ascii_dxf, read_ezdxf):
                result = self.analyzer.analyze(reader(Path(temporary.name)))
                self.assertEqual(
                    result.status,
                    "ERROR_DXF_PARSE_INCOMPLETE",
                    msg=f"{reader.__name__}: {result.warnings}",
                )
                self.assertTrue(
                    any("radius" in warning for warning in result.warnings),
                    msg=f"{reader.__name__}: {result.warnings}",
                )

    def test_raw_material_source_fields_are_not_ezdxf_defaults(self):
        """Recovered defaults cannot replace omitted material source fields."""
        cases = {
            "dimension": ["0", "DIMENSION", "8", "Part"],
            "line_start_y": ["0", "LINE", "8", "Part", "10", "0", "11", "100", "21", "0"],
            "line_end_y": ["0", "LINE", "8", "Part", "10", "0", "20", "0", "11", "100"],
            "arc_radius": ["0", "ARC", "8", "Part", "10", "0", "20", "0", "50", "0", "51", "90"],
            "circle_radius": ["0", "CIRCLE", "8", "Part", "10", "0", "20", "0"],
        }
        for name, entity in cases.items():
            payload = "\n".join([
                "0", "SECTION", "2", "HEADER", "9", "$INSUNITS", "70", "4", "0", "ENDSEC",
                "0", "SECTION", "2", "ENTITIES", *entity,
                "0", "ENDSEC", "0", "EOF", "",
            ])
            with NamedTemporaryFile(suffix=".dxf") as temporary:
                temporary.write(payload.encode("ascii"))
                temporary.flush()
                for reader in (read_ascii_dxf, read_ezdxf):
                    result = self.analyzer.analyze(reader(Path(temporary.name)))
                    self.assertEqual(
                        result.status,
                        "ERROR_DXF_PARSE_INCOMPLETE",
                        msg=f"{name} {reader.__name__}: {result.warnings}",
                    )

    def test_non_part_unsupported_geometry_does_not_block_bh(self):
        """Decorative unsupported curves do not erase otherwise valid BH data."""
        import ezdxf

        lines = [
            ((0, 0), (1000, 0)), ((1000, 0), (1000, 20)),
            ((1000, 20), (0, 20)), ((0, 20), (0, 0)),
            ((0, 580), (1000, 580)), ((1000, 580), (1000, 600)),
            ((1000, 600), (0, 600)), ((0, 600), (0, 580)),
            ((0, 20), (0, 580)), ((1000, 20), (1000, 580)),
        ]
        with TemporaryDirectory() as directory:
            path = Path(directory) / "non_part_ellipse.dxf"
            document = ezdxf.new("R2000")
            document.header["$INSUNITS"] = 4
            space = document.modelspace()
            for start, end in lines:
                space.add_line(start, end, dxfattribs={"layer": "Part"})
            space.add_ellipse(
                center=(500, -100), major_axis=(40, 0), ratio=0.5,
                dxfattribs={"layer": "OtherObjectType"},
            )
            space.add_text(
                "BH600*250*12*20",
                dxfattribs={"layer": "OtherObjectType", "height": 10},
            ).set_placement((0, -200))
            space.add_text(
                "1000", dxfattribs={"layer": "OtherObjectType", "height": 10}
            ).set_placement((10, -200))
            document.saveas(path)
            for reader in (read_ascii_dxf, read_ezdxf):
                result = self.analyzer.analyze(reader(path))
                self.assertEqual(result.status, "OK", msg=f"{reader.__name__}: {result.warnings}")
                self.assertEqual(self._roles(result), {"腹": (0, 0), "翼": (0, 0)})
                unsupported = result.diagnostics["unsupported_source_entities"]
                self.assertEqual(len(unsupported), 1)
                self.assertEqual(unsupported[0]["kind"], "ELLIPSE")
                self.assertEqual(unsupported[0]["layer"], "OtherObjectType")

    def test_broad_view_fallback_fails_closed_on_layer_zero_spline(self):
        """Without Part-layer evidence, an ignored curve may be a plate edge."""
        import ezdxf

        lines = [
            ((0, 0), (1000, 0)), ((1000, 0), (1000, 20)),
            ((1000, 20), (0, 20)), ((0, 20), (0, 0)),
            ((0, 580), (1000, 580)), ((1000, 580), (1000, 600)),
            ((1000, 600), (0, 600)), ((0, 600), (0, 580)),
            ((0, 20), (0, 580)), ((1000, 20), (1000, 580)),
        ]
        with TemporaryDirectory() as directory:
            path = Path(directory) / "fallback_layer0_spline.dxf"
            document = ezdxf.new("R2000")
            document.header["$INSUNITS"] = 4
            space = document.modelspace()
            for start, end in lines:
                space.add_line(start, end)  # Layer 0 deliberately forces broad fallback.
            space.add_spline([(100, 300), (300, 350), (500, 300)])
            space.add_text(
                "BH600*250*12*20",
                dxfattribs={"layer": "OtherObjectType", "height": 10},
            ).set_placement((0, -200))
            space.add_text(
                "1000", dxfattribs={"layer": "OtherObjectType", "height": 10}
            ).set_placement((10, -200))
            document.saveas(path)
            for reader in (read_ascii_dxf, read_ezdxf):
                result = self.analyzer.analyze(reader(path))
                self.assertEqual(
                    result.status,
                    "ERROR_DXF_PARSE_INCOMPLETE",
                    msg=f"{reader.__name__}: {result.warnings}",
                )
                self.assertEqual(
                    result.diagnostics["unsupported_source_entities"][0]["kind"],
                    "SPLINE",
                )

    def test_malformed_non_part_line_is_audited_consistently(self):
        """A bad decorative line is auditable, but cannot diverge by backend."""
        import ezdxf

        lines = [
            ((0, 0), (1000, 0)), ((1000, 0), (1000, 20)),
            ((1000, 20), (0, 20)), ((0, 20), (0, 0)),
            ((0, 580), (1000, 580)), ((1000, 580), (1000, 600)),
            ((1000, 600), (0, 600)), ((0, 600), (0, 580)),
            ((0, 20), (0, 580)), ((1000, 20), (1000, 580)),
        ]
        with TemporaryDirectory() as directory:
            path = Path(directory) / "malformed_non_part_line.dxf"
            document = ezdxf.new("R2000")
            document.header["$INSUNITS"] = 4
            space = document.modelspace()
            for start, end in lines:
                space.add_line(start, end, dxfattribs={"layer": "Part"})
            space.add_line(
                (111, -111), (222, -123), dxfattribs={"layer": "OtherObjectType"}
            )
            space.add_text(
                "BH600*250*12*20",
                dxfattribs={"layer": "OtherObjectType", "height": 10},
            ).set_placement((0, -200))
            space.add_text(
                "1000", dxfattribs={"layer": "OtherObjectType", "height": 10}
            ).set_placement((10, -200))
            document.saveas(path)
            payload = path.read_text(encoding="utf-8")
            malformed = "\n 21\n-123.0\n"
            self.assertIn(malformed, payload)
            path.write_text(payload.replace(malformed, "\n", 1), encoding="utf-8")
            for reader in (read_ascii_dxf, read_ezdxf):
                result = self.analyzer.analyze(reader(path))
                self.assertEqual(result.status, "OK", msg=f"{reader.__name__}: {result.warnings}")
                unsupported = result.diagnostics["unsupported_source_entities"]
                self.assertTrue(
                    any(item["kind"] == "LINE" and item["layer"] == "OtherObjectType" for item in unsupported),
                    msg=f"{reader.__name__}: {unsupported}",
                )

    def test_non_finite_or_unbounded_bulge_fails_closed_in_both_readers(self):
        """Malformed bulge data must not silently collapse back to a chord."""
        import ezdxf

        cases = {
            "nan": float("nan"),
            "too_large": 1_000_001.0,
        }
        with TemporaryDirectory() as directory:
            for name, bulge in cases.items():
                path = Path(directory) / f"invalid_bulge_{name}.dxf"
                document = ezdxf.new("R2000")
                document.header["$INSUNITS"] = 4
                document.modelspace().add_lwpolyline(
                    [(0, 0, 0, 0, bulge), (100, 0, 0, 0, 0)],
                    format="xyseb",
                    dxfattribs={"layer": "Part"},
                )
                document.saveas(path)
                for reader in (read_ascii_dxf, read_ezdxf):
                    result = self.analyzer.analyze(reader(path))
                    self.assertEqual(
                        result.status,
                        "ERROR_DXF_PARSE_INCOMPLETE",
                        msg=f"{name} {reader.__name__}: {result.warnings}",
                    )
                    self.assertTrue(
                        any("bulge" in warning for warning in result.warnings),
                        msg=f"{name} {reader.__name__}: {result.warnings}",
                    )

    def test_ascii_non_numeric_bulge_fails_closed_instead_of_becoming_chord(self):
        """ASCII must not convert group-code 42 text to a zero bulge."""
        payload = "\n".join([
            "0", "SECTION", "2", "HEADER", "9", "$ACADVER", "1", "AC1015", "9", "$INSUNITS", "70", "4", "0", "ENDSEC",
            "0", "SECTION", "2", "ENTITIES",
            "0", "LWPOLYLINE", "100", "AcDbEntity", "8", "Part", "100", "AcDbPolyline", "90", "2", "70", "0",
            "10", "0", "20", "0", "42", "not-a-number", "10", "100", "20", "0",
            "0", "ENDSEC", "0", "EOF", "",
        ])
        with NamedTemporaryFile(suffix=".dxf") as temporary:
            temporary.write(payload.encode("ascii"))
            temporary.flush()
            result = self.analyzer.analyze(read_ascii_dxf(Path(temporary.name)))
            self.assertEqual(result.status, "ERROR_DXF_PARSE_INCOMPLETE")
            self.assertTrue(any("invalid bulge" in warning for warning in result.warnings))

    def test_large_closing_bulge_is_not_dropped(self):
        """A valid >180° bulge at the final closed vertex remains material."""
        import ezdxf
        from math import sqrt

        # The closing segment from (100, 100) to (0, 0) has bulge=2.  Its
        # 253.74° source arc has exact left envelope x=12.5-sqrt(7812.5),
        # well outside the three stored vertices.  This catches both a
        # ``abs(bulge) <= 1`` implementation and loss of the closing segment.
        points = [(0, 0, 0, 0, 0), (100, 0, 0, 0, 0), (100, 100, 0, 0, 2)]
        expected_left = 12.5 - sqrt(7812.5)
        with TemporaryDirectory() as directory:
            for legacy in (False, True):
                path = Path(directory) / f"closing_bulge_{legacy}.dxf"
                document = ezdxf.new("R2000")
                document.header["$INSUNITS"] = 4
                space = document.modelspace()
                if legacy:
                    outline = space.add_polyline2d(
                        [(point[0], point[1]) for point in points],
                        close=True,
                        dxfattribs={"layer": "Part"},
                    )
                    for vertex, point in zip(outline.vertices, points, strict=True):
                        vertex.dxf.bulge = point[4]
                else:
                    space.add_lwpolyline(
                        points, format="xyseb", close=True,
                        dxfattribs={"layer": "Part"},
                    )
                document.saveas(path)
                for reader in (read_ascii_dxf, read_ezdxf):
                    drawing = reader(path)
                    outlines = [
                        item for item in drawing.primitives
                        if item.kind in {"LWPOLYLINE", "POLYLINE"}
                    ]
                    self.assertEqual(len(outlines), 1, msg=f"{legacy} {reader.__name__}")
                    self.assertAlmostEqual(
                        min(point[0] for point in outlines[0].points),
                        expected_left,
                        places=9,
                        msg=f"{legacy} {reader.__name__}",
                    )


if __name__ == "__main__":
    unittest.main()
