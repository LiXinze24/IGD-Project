"""Run the CLI from a source checkout without an installation step."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from igd.cli import main
raise SystemExit(main())
