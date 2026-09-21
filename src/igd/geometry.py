"""Parameterized gear-shaft geometry with rounded-slot tooth profiles."""

from __future__ import annotations

import inspect
import math

from .errors import ValidationError
from .models import fields, number, require_object


DEFAULT_PARAMETERS = {
    "segment_diameters_mm": [40.0, 57.0, 40.0, 32.0],
    "segment_lengths_mm": [25.0, 48.0, 55.0, 60.0],
    "tooth_count": 25,
    "tooth_length_mm": 8.5,
    "tooth_width_mm": 4.0,
    "gear_face_width_mm": 37.0,
    "gear_start_offset_mm": 5.0,
    "keyway_length_mm": 30.0,
    "keyway_width_mm": 10.0,
    "keyway_depth_mm": 4.0,
    "keyway_end_margin_mm": 5.0,
}


def validate_parameters(value: dict) -> dict:
    require_object(value, "gear-shaft parameters")
    fields(value, set(DEFAULT_PARAMETERS), set(), "gear-shaft parameters")
    result = {}
    for name in ("segment_diameters_mm", "segment_lengths_mm"):
        if not isinstance(value[name], list) or len(value[name]) != 4:
            raise ValidationError(f"{name} must contain four dimensions")
        result[name] = [number(v, name, 0.01, 10_000) for v in value[name]]
    if type(value["tooth_count"]) is not int or not 3 <= value["tooth_count"] <= 200:
        raise ValidationError("tooth_count must be an integer from 3 to 200")
    result["tooth_count"] = value["tooth_count"]
    for name in DEFAULT_PARAMETERS:
        if name in result:
            continue
        minimum = 0.0 if name in {"gear_start_offset_mm", "keyway_end_margin_mm"} else 0.01
        result[name] = number(value[name], name, minimum, 10_000)
    d = result["segment_diameters_mm"]
    h = result["segment_lengths_mm"]
    if result["gear_start_offset_mm"] + result["gear_face_width_mm"] > h[1]:
        raise ValidationError("The gear face must fit within the second shaft segment")
    if not result["tooth_width_mm"] <= result["tooth_length_mm"] < d[1]:
        raise ValidationError("Require tooth width <= tooth length < gear root diameter")
    if result["tooth_width_mm"] >= d[1] * math.sin(math.pi / result["tooth_count"]):
        raise ValidationError("Tooth width exceeds the available circumferential spacing")
    if not result["keyway_width_mm"] <= result["keyway_length_mm"]:
        raise ValidationError("Keyway length must be at least its width")
    if result["keyway_length_mm"] + result["keyway_end_margin_mm"] > h[3]:
        raise ValidationError("The keyway must fit within the fourth shaft segment")
    if result["keyway_width_mm"] >= d[3] or result["keyway_depth_mm"] >= d[3] / 2:
        raise ValidationError("Keyway width and depth must fit the shaft cross-section")
    return result


def _build_gear_shaft(parameters):
    """Build four contiguous shaft segments, a tooth array and one keyway."""
    import cadquery as cq

    p = parameters
    diameters = p["segment_diameters_mm"]
    lengths = p["segment_lengths_mm"]
    result = None
    z = 0.0
    for diameter, length in zip(diameters, lengths):
        segment = (cq.Workplane("XY", origin=(0, 0, z))
                   .circle(diameter / 2).extrude(length))
        result = segment if result is None else result.union(segment)
        z += length

    tooth_z = lengths[0] + p["gear_start_offset_mm"]
    tooth = (cq.Workplane("XY", origin=(0, 0, tooth_z))
             .slot2D(p["tooth_length_mm"], p["tooth_width_mm"])
             .extrude(p["gear_face_width_mm"])
             .translate((diameters[1] / 2, 0, 0)))
    for index in range(p["tooth_count"]):
        result = result.union(tooth.rotate((0, 0, 0), (0, 0, 1),
                                          index * 360.0 / p["tooth_count"]))

    key_z = z - p["keyway_end_margin_mm"] - p["keyway_length_mm"] / 2
    # The ZX workplane normal points towards +Y. Start outside the surface.
    cutter = (cq.Workplane("ZX", origin=(0, diameters[3] / 2 + 1.0, key_z))
              .slot2D(p["keyway_length_mm"], p["keyway_width_mm"])
              .extrude(-(p["keyway_depth_mm"] + 1.0)))
    return result.cut(cutter).clean()


def create_demo_geometry(parameters: dict | None = None):
    return _build_gear_shaft(validate_parameters(DEFAULT_PARAMETERS if parameters is None else parameters))


def gear_shaft_source(parameters: dict) -> str:
    """Export the trusted shipped builder as standalone CadQuery source."""
    p = validate_parameters(parameters)
    return ("# Dimensions are in millimetres; rounded-slot tooth profiles.\n"
            "import cadquery as cq\n\n" + inspect.getsource(_build_gear_shaft)
            + "\nparameters = " + repr(p) + "\nresult = _build_gear_shaft(parameters)\n")


def check_geometry(model, parameters: dict) -> dict:
    """Check solid topology and independent feature probes on the built shape."""
    import cadquery as cq

    p = validate_parameters(parameters)
    solids = model.solids().vals()
    if len(solids) != 1 or not solids[0].isValid() or solids[0].Volume() <= 0:
        raise ValidationError("Gear shaft must be one valid solid of positive volume")
    solid = solids[0]
    total = sum(p["segment_lengths_mm"])
    bounds = solid.BoundingBox()
    if not math.isclose(bounds.zmin, 0, abs_tol=1e-6) or not math.isclose(bounds.zlen, total, abs_tol=1e-6):
        raise ValidationError("Gear-shaft axial length does not match its parameters")

    def inside(x, y, z):
        return solid.isInside(cq.Vector(x, y, z), 1e-6)

    z = 0.0
    for index, (diameter, length) in enumerate(zip(p["segment_diameters_mm"], p["segment_lengths_mm"])):
        midpoint = z + length / 2
        # Check the gear root in a tooth gap; check other segments away from the keyway.
        angle = math.pi / p["tooth_count"] if index == 1 else -math.pi / 2
        inner, outer = diameter / 2 - 0.001, diameter / 2 + 0.001
        if not inside(inner * math.cos(angle), inner * math.sin(angle), midpoint):
            raise ValidationError("A shaft segment is undersized")
        if inside(outer * math.cos(angle), outer * math.sin(angle), midpoint):
            raise ValidationError("A shaft segment is oversized")
        z += length
    tooth_z = p["segment_lengths_mm"][0] + p["gear_start_offset_mm"] + p["gear_face_width_mm"] / 2
    root_radius = p["segment_diameters_mm"][1] / 2
    sample_radius = root_radius + p["tooth_length_mm"] * 0.4
    for index in range(p["tooth_count"]):
        angle = index * 2 * math.pi / p["tooth_count"]
        gap = angle + math.pi / p["tooth_count"]
        if not inside(sample_radius * math.cos(angle), sample_radius * math.sin(angle), tooth_z):
            raise ValidationError("A tooth is missing from the circular array")
        if inside(sample_radius * math.cos(gap), sample_radius * math.sin(gap), tooth_z):
            raise ValidationError("Adjacent teeth do not have the expected gap")
    key_z = total - p["keyway_end_margin_mm"] - p["keyway_length_mm"] / 2
    radius = p["segment_diameters_mm"][3] / 2
    if inside(0, radius - p["keyway_depth_mm"] / 2, key_z):
        raise ValidationError("The keyway has not been cut")
    if not inside(0, radius - p["keyway_depth_mm"] - 0.001, key_z):
        raise ValidationError("The keyway is deeper than requested")
    return {"solid_validity": True, "solid_count": 1, "volume_mm3": solid.Volume(),
            "shaft_length_mm": bounds.zlen, "tooth_count": p["tooth_count"],
            "tooth_and_gap_probes": True, "shaft_segment_probes": True, "keyway_probes": True}
