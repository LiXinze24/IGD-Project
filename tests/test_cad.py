"""Optional geometric checks for the shipped gear shaft."""

import importlib.util
import math
import tempfile
import unittest
from pathlib import Path

from igd.errors import ValidationError
from igd.geometry import DEFAULT_PARAMETERS, check_geometry, create_demo_geometry, gear_shaft_source

HAS_CADQUERY = importlib.util.find_spec("cadquery") is not None


@unittest.skipUnless(HAS_CADQUERY, "Install the cad extra for geometric checks")
class GeometryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.model = create_demo_geometry()

    def test_dimensions_teeth_and_keyway(self):
        report = check_geometry(self.model, DEFAULT_PARAMETERS)
        self.assertEqual(report["solid_count"], 1)
        self.assertAlmostEqual(report["shaft_length_mm"], 188, places=6)
        self.assertEqual(report["tooth_count"], 25)
        self.assertTrue(report["keyway_probes"])
        solid = self.model.val()
        # Independent dimensions at axial stations outside the toothed face.
        for z, radius in ((10, 20), (27, 28.5), (100, 20), (135, 16)):
            self.assertTrue(solid.isInside((radius - 0.01, 0, z)))
            self.assertFalse(solid.isInside((radius + 0.01, 0, z)))
        self.assertFalse(solid.isInside((0, 14, 168)))
        self.assertTrue(solid.isInside((0, 11.9, 168)))
        self.assertTrue(solid.isInside((0, 15, 186)))

    def test_shipped_source_and_step_round_trip(self):
        import cadquery as cq
        parameters = DEFAULT_PARAMETERS | {
            "segment_lengths_mm": [30, 55, 50, 70], "tooth_count": 20,
            "keyway_end_margin_mm": 8,
        }
        # Execute only the trusted source exported from the shipped local builder.
        namespace = {}
        exec(compile(gear_shaft_source(parameters), "<built-in-gear-shaft>", "exec"), namespace)
        model = namespace["result"]
        checks = check_geometry(model, parameters)
        self.assertAlmostEqual(checks["shaft_length_mm"], 205, places=6)
        self.assertEqual(checks["tooth_count"], 20)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "gear_shaft.step"
            cq.exporters.export(model, str(path))
            imported = cq.importers.importStep(str(path))
            imported_checks = check_geometry(imported, parameters)
            self.assertTrue(math.isclose(checks["volume_mm3"], imported_checks["volume_mm3"], rel_tol=1e-6))

    def test_missing_tooth_is_detected(self):
        import cadquery as cq
        cutter = cq.Workplane("XY").box(20, 4.2, 37).translate((38.5, 0, 48.5))
        damaged = self.model.cut(cutter)
        with self.assertRaisesRegex(ValidationError, "tooth is missing"):
            check_geometry(damaged, DEFAULT_PARAMETERS)
