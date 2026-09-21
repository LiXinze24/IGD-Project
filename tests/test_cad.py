"""Optional geometric checks for the shipped stepped shaft."""

import copy
import importlib.util
import math
import tempfile
import unittest
from pathlib import Path

from igd.errors import ValidationError
from igd.geometry import DEFAULT_PARAMETERS, check_geometry, create_demo_geometry, shaft_source

HAS_CADQUERY = importlib.util.find_spec("cadquery") is not None


@unittest.skipUnless(HAS_CADQUERY, "Install the cad extra for geometric checks")
class GeometryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.model = create_demo_geometry()

    def test_dimensions_and_both_keyways(self):
        report = check_geometry(self.model, DEFAULT_PARAMETERS)
        self.assertEqual(report["solid_count"], 1)
        self.assertAlmostEqual(report["shaft_length_mm"], 233.75, places=6)
        self.assertEqual(report["keyway_count"], 2)
        self.assertTrue(report["keyway_probes"])
        solid = self.model.val()
        # Independent stations, clearances and volume of the reference geometry.
        for z, radius in ((6, 30), (60, 35), (140, 30), (210, 27.5)):
            self.assertTrue(solid.isInside((radius - 0.01, 0, z)))
            self.assertFalse(solid.isInside((radius + 0.01, 0, z)))
        self.assertAlmostEqual(report["volume_mm3"], 732650.9317925, places=3)
        self.assertFalse(solid.isInside((0, 32, 65.95)))
        self.assertTrue(solid.isInside((0, 28.9, 65.95)))
        self.assertFalse(solid.isInside((0, 26, 211.75)))
        self.assertTrue(solid.isInside((0, 22.49, 211.75)))
        for z in (179.25, 185, 230.75):
            self.assertTrue(solid.isInside((0, 26, z)))
        last = report["keyways"][1]
        self.assertAlmostEqual(last["start_margin_mm"], 12.35)
        self.assertAlmostEqual(last["end_margin_mm"], 5)

    def test_shipped_source_and_step_round_trip(self):
        import cadquery as cq
        parameters = copy.deepcopy(DEFAULT_PARAMETERS)
        parameters["segment_lengths_mm"] = [20, 100, 70, 60]
        parameters["keyways"][1]["end_margin_mm"] = 8
        namespace = {}
        # Execute only the trusted source exported from the shipped builder.
        exec(compile(shaft_source(parameters), "<built-in-shaft>", "exec"), namespace)
        model = namespace["result"]
        checks = check_geometry(model, parameters)
        self.assertAlmostEqual(checks["shaft_length_mm"], 250, places=6)
        self.assertEqual(checks["keyway_count"], 2)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "shaft.step"
            cq.exporters.export(model, str(path))
            imported = cq.importers.importStep(str(path))
            imported_checks = check_geometry(imported, parameters)
            self.assertTrue(math.isclose(checks["volume_mm3"], imported_checks["volume_mm3"], rel_tol=1e-6))

    def test_missing_keyway_is_detected(self):
        import cadquery as cq
        filler = cq.Workplane("XY", origin=(0, 0, 182.4)).circle(27.5).extrude(51.35)
        filled = self.model.union(filler)
        with self.assertRaisesRegex(ValidationError, "keyway is missing"):
            check_geometry(filled, DEFAULT_PARAMETERS)

    def test_displaced_keyway_is_detected(self):
        parameters = copy.deepcopy(DEFAULT_PARAMETERS)
        parameters["keyways"][1]["end_margin_mm"] = 12
        moved = create_demo_geometry(parameters)
        with self.assertRaisesRegex(ValidationError, "axial boundary|shorter"):
            check_geometry(moved, DEFAULT_PARAMETERS)
