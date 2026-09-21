"""Write one fresh run directory containing validated results and source files."""

import json
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .models import Status, identifier


def prepare_output_root(output_root: Path) -> Path:
    """Check output storage before starting potentially billable workflow calls."""
    root = output_root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryFile(dir=root):
        pass
    return root


def save_report(report: dict, output_root: Path) -> Path:
    name = datetime.now(timezone.utc).strftime("run-%Y%m%dT%H%M%SZ-") + uuid.uuid4().hex[:12]
    directory = output_root.resolve() / name
    directory.mkdir(parents=True, exist_ok=False)
    artifacts = directory / "artifacts"
    artifacts.mkdir()
    for task_id, record in report["tasks"].items():
        identifier(task_id)
        if record["status"] != Status.SUCCEEDED.value:
            continue
        payload = record["result"]["payload"]
        path = artifacts / (task_id + ".json")
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
        if "cadquery_code" in payload:
            (artifacts / (task_id + ".py")).write_text(payload["cadquery_code"], encoding="utf-8")
    text = json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    temporary = directory / "run.json.tmp"
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(directory / "run.json")
    return directory

