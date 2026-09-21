"""Parameterized stepped shaft with two axial keyways."""

from __future__ import annotations

import inspect
import math

from .errors import ValidationError
from .models import fields, number, require_object


DEFAULT_PARAMETERS = {
    "segment_diameters_mm": [60.0, 70.0, 60.0, 55.0],
    "segment_lengths_mm": [16.95, 96.0, 69.45, 51.35],
    "keyways": [
        {"segment": 2, "length_mm": 22.0, "width_mm": 14.0,
         "depth_mm": 6.0, "end_margin_mm": 36.0},
        {"segment": 4, "length_mm": 34.0, "width_mm": 10.0,
         "depth_mm": 5.0, "end_margin_mm": 5.0},
    ],
}


def validate_parameters(value: dict) -> dict:
    require_object(value, "shaft parameters")
    fields(value, set(DEFAULT_PARAMETERS), set(), "shaft parameters")
    result = {}
    for name in ("segment_diameters_mm", "segment_lengths_mm"):
        if not isinstance(value[name], list) or len(value[name]) != 4:
            raise ValidationError(f"{name} must contain four dimensions")
        result[name] = [number(v, name, 0.01, 10_000) for v in value[name]]
    if not isinstance(value["keyways"], list) or len(value["keyways"]) != 2:
        raise ValidationError("The shaft example requires two keyways")
    result["keyways"] = []
    used_segments = set()
    key_fields = ("segment", "length_mm", "width_mm", "depth_mm", "end_margin_mm")
    for entry in value["keyways"]:
        require_object(entry, "keyway")
        fields(entry, set(key_fields), set(), "keyway")
        segment = entry["segment"]
        if type(segment) is not int or not 1 <= segment <= 4:
            raise ValidationError("Keyway segment must be an integer from 1 to 4")
        if segment in used_segments:
            raise ValidationError("Each keyway must occupy a different shaft segment")
        used_segments.add(segment)
        key = {"segment": segment}
        for name in key_fields[1:]:
            key[name] = number(entry[name], name, 0.01, 10_000)
        diameter = result["segment_diameters_mm"][segment - 1]
        length = result["segment_lengths_mm"][segment - 1]
        if key["width_mm"] > key["length_mm"]:
            raise ValidationError("Keyway length must be at least its width")
        if key["length_mm"] + key["end_margin_mm"] >= length:
            raise ValidationError("The keyway must leave material at both segment ends")
        if key["width_mm"] >= diameter or key["depth_mm"] >= diameter / 2:
            raise ValidationError("Keyway width and depth must fit the shaft cross-section")
        radius = diameter / 2
        edge_drop = radius - math.sqrt(radius ** 2 - (key["width_mm"] / 2) ** 2)
        if key["depth_mm"] <= edge_drop:
            raise ValidationError("Keyway depth must reach below the full slot width")
        result["keyways"].append(key)
    return result


def _build_shaft(parameters):
    """Build four contiguous shaft segments and cut two closed-end keyways."""
    import cadquery as cq

    p = parameters
    diameters = p["segment_diameters_mm"]
    lengths = p["segment_lengths_mm"]
    result = None
    segment_ends = []
    z = 0.0
    for diameter, length in zip(diameters, lengths):
        segment = (cq.Workplane("XY", origin=(0, 0, z))
                   .circle(diameter / 2).extrude(length))
        result = segment if result is None else result.union(segment)
        z += length
        segment_ends.append(z)

    for key in p["keyways"]:
        index = key["segment"] - 1
        center_z = segment_ends[index] - key["end_margin_mm"] - key["length_mm"] / 2
        # ZX has a +Y normal. Cut inward from outside the cylinder.
        cutter = (cq.Workplane("ZX", origin=(0, diameters[index] / 2 + 1.0, center_z))
                  .slot2D(key["length_mm"], key["width_mm"])
                  .extrude(-(key["depth_mm"] + 1.0)))
        result = result.cut(cutter)
    return result.clean()


def create_demo_geometry(parameters: dict | None = None):
    return _build_shaft(validate_parameters(DEFAULT_PARAMETERS if parameters is None else parameters))


def shaft_source(parameters: dict) -> str:
    """Export the shipped builder as standalone CadQuery source."""
    p = validate_parameters(parameters)
    return ("# Dimensions are in millimetres; axial coordinates start at zero.\n"
            "import cadquery as cq\n\n" + inspect.getsource(_build_shaft)
            + "\nparameters = " + repr(p) + "\nresult = _build_shaft(parameters)\n")


def check_geometry(model, parameters: dict) -> dict:
    """Check topology, shaft dimensions and independent probes of both keyways."""
    import cadquery as cq

    p = validate_parameters(parameters)
    solids = model.solids().vals()
    if len(solids) != 1 or not solids[0].isValid():
        raise ValidationError("The shaft must be one valid solid of positive volume")
    solid = solids[0]
    # Explicit integration accuracy keeps curved-face volume checks stable after STEP I/O.
    volume = solid.Volume(tol=1e-8)
    if not math.isfinite(volume) or volume <= 0:
        raise ValidationError("The shaft must have finite positive volume")
    total = sum(p["segment_lengths_mm"])
    bounds = solid.BoundingBox()
    if not math.isclose(bounds.zmin, 0, abs_tol=1e-6) or not math.isclose(bounds.zlen, total, abs_tol=1e-6):
        raise ValidationError("Shaft axial length does not match its parameters")

    def inside(x, y, z):
        return solid.isInside(cq.Vector(x, y, z), 1e-6)

    z = 0.0
    ends = []
    for diameter, length in zip(p["segment_diameters_mm"], p["segment_lengths_mm"]):
        midpoint = z + length / 2
        radius = diameter / 2
        # Probe the underside, away from the +Y keyways.
        if not inside(0, -radius + 0.001, midpoint):
            raise ValidationError("A shaft segment is undersized")
        if inside(0, -radius - 0.001, midpoint):
            raise ValidationError("A shaft segment is oversized")
        z += length
        ends.append(z)

    keyway_checks = []
    for key in p["keyways"]:
        index = key["segment"] - 1
        radius = p["segment_diameters_mm"][index] / 2
        end = ends[index] - key["end_margin_mm"]
        start = end - key["length_mm"]
        center = (start + end) / 2
        floor = radius - key["depth_mm"]
        epsilon = min(0.001, key["depth_mm"] / 10)
        if inside(0, floor + epsilon, center):
            raise ValidationError("A keyway is missing or too shallow")
        if not inside(0, floor - epsilon, center):
            raise ValidationError("A keyway is deeper than requested")
        half_width = key["width_mm"] / 2
        surface = math.sqrt(radius ** 2 - half_width ** 2)
        probe_y = (floor + surface) / 2
        for sign in (-1, 1):
            if inside(sign * (half_width - 0.001), probe_y, center):
                raise ValidationError("A keyway is narrower than requested")
            if not inside(sign * (half_width + 0.001), probe_y, center):
                raise ValidationError("A keyway is wider than requested")
        segment_start = ends[index] - p["segment_lengths_mm"][index]
        clearance = start - segment_start
        edge_epsilon = min(0.001, clearance / 2, key["end_margin_mm"] / 2)
        for edge, direction in ((start, 1), (end, -1)):
            if inside(0, floor + epsilon, edge + direction * edge_epsilon):
                raise ValidationError("A keyway is shorter than requested")
            if not inside(0, floor + epsilon, edge - direction * edge_epsilon):
                raise ValidationError("A keyway crosses its axial boundary")
        keyway_checks.append({"segment": key["segment"], "start_z_mm": start,
                              "end_z_mm": end, "end_margin_mm": key["end_margin_mm"],
                              "start_margin_mm": clearance})
    return {"solid_validity": True, "solid_count": 1, "volume_mm3": volume,
            "shaft_length_mm": bounds.zlen, "keyway_count": len(p["keyways"]),
            "shaft_segment_probes": True, "keyway_probes": True, "keyways": keyway_checks}
