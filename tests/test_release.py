import json
import re
import tempfile
import unittest
import zipfile
from pathlib import Path

from tools.package_release import DIRECTORIES, EXTRA_FILES, ROOT_FILES, package, public_files

ROOT = Path(__file__).resolve().parents[1]


class ReleaseTests(unittest.TestCase):
    def test_allowlist_excludes_private_and_generated_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ROOT_FILES + EXTRA_FILES:
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                if path.suffix == ".png":
                    path.write_bytes(b"\x89PNG\r\n\x1a\n")
                else:
                    path.write_text("", encoding="utf-8")
            for name in DIRECTORIES:
                (root / name).mkdir(parents=True, exist_ok=True)
            (root / "src/igd/__init__.py").write_text('__version__ = "0.1.0"\n', encoding="utf-8")
            for name in (
                ".env", ".private/workflow.yml", "outputs/run.json",
                "src/igd/__pycache__/bad.py", "data/deepcad_cq_train.json",
                "checkpoints/adapter_config.json", "models/config.json",
                "training/llamafactory/data/private.json",
                "training/llamafactory/local_training.yaml",
            ):
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("private-data", encoding="utf-8")
            files = public_files(root)
            self.assertNotIn(root / ".env", files)
            self.assertFalse(any(b"private-data" in p.read_bytes() for p in files))
            archive = package(root)
            before = archive.read_bytes()
            self.assertEqual(package(root).read_bytes(), before)
            with zipfile.ZipFile(archive) as bundle:
                self.assertIsNone(bundle.testzip())
                self.assertIn("igd-0.1.0/.env.example", bundle.namelist())
                for name in EXTRA_FILES:
                    self.assertIn("igd-0.1.0/" + name, bundle.namelist())
                self.assertFalse(any("/outputs/" in name or name.endswith("/.env") for name in bundle.namelist()))

    def test_credential_literals_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ROOT_FILES + EXTRA_FILES:
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                if path.suffix == ".png":
                    path.write_bytes(b"\x89PNG\r\n\x1a\n")
                else:
                    path.write_text("", encoding="utf-8")
            for name in DIRECTORIES:
                (root / name).mkdir(parents=True, exist_ok=True)
            (root / "src/igd/key.py").write_text('TOKEN = "sk-' + 'a' * 30 + '"', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Credential-shaped"):
                public_files(root)

    def test_documentation_local_links_and_json_examples(self):
        for path in [
            ROOT / "README.md", ROOT / "README.zh-CN.md",
            *list((ROOT / "docs").glob("*.md")),
            ROOT / "training/llamafactory/README.md",
        ]:
            text = path.read_text(encoding="utf-8")
            for link in re.findall(r"\]\(([^)]+)\)", text):
                if "://" not in link and not link.startswith("#"):
                    self.assertTrue((path.parent / link.split("#")[0]).exists(), f"{path.name}: {link}")
            for document in re.findall(r"```json\n(.*?)\n```", text, flags=re.S):
                json.loads(document)
        for path in [*(ROOT / "schemas").glob("*.json"), ROOT / "training/llamafactory/dataset_info.example.json"]:
            json.loads(path.read_text(encoding="utf-8"))
