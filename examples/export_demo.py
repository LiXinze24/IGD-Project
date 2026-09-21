"""Export and validate the shipped stepped shaft using CadQuery."""

import argparse
import json
import math
from pathlib import Path

import cadquery as cq

from igd.geometry import DEFAULT_PARAMETERS, check_geometry, create_demo_geometry, validate_parameters
from igd.models import loads_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("outputs/demo-cad"))
    parser.add_argument("--parameters", type=Path)
    args = parser.parse_args()
    p = validate_parameters(loads_json(args.parameters.read_text(encoding="utf-8-sig"))
                            if args.parameters else DEFAULT_PARAMETERS)
    model = create_demo_geometry(p)
    checks = check_geometry(model, p)
    args.output.mkdir(parents=True, exist_ok=True)
    path = args.output / "shaft.step"
    cq.exporters.export(model, str(path))
    imported = cq.importers.importStep(str(path))
    imported_checks = check_geometry(imported, p)
    if not math.isclose(imported_checks["volume_mm3"], checks["volume_mm3"], rel_tol=1e-6):
        raise RuntimeError("STEP round trip changed the solid volume")
    checks["step_round_trip"] = True
    (args.output / "geometry-checks.json").write_text(
        json.dumps(checks, indent=2) + "\n", encoding="utf-8")
    print(f"Checked and exported the stepped shaft to {args.output.resolve()}")


if __name__ == "__main__":
    main()
