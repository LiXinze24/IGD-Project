"""Build a source ZIP from an explicit allowlist of public project files."""

from __future__ import annotations

import argparse
import hashlib
import re
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ROOT_FILES = (
    ".gitignore", ".env.example", "pyproject.toml", "README.md",
    "README.zh-CN.md", "LICENSE", "CITATION.cff", "MANIFEST.in",
)
EXTRA_FILES = (
    'docs/assets/shaft-preview.png',
    'training/llamafactory/README.md',
    'training/llamafactory/qwen2_5coder_lora_s1_sft.yaml',
    'training/llamafactory/dataset_info.example.json',
)
DIRECTORIES = {
    "src/igd": {".py"},
    "tests": {".py"},
    "tools": {".py"},
    "docs": {".md"},
    "schemas": {".json"},
    "examples": {".py", ".json"},
    ".github/workflows": {".yml"},
}
# Only credential-shaped literals are rejected; configuration variable names are allowed.
SECRET_PATTERNS = (
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{30,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bapp-[A-Za-z0-9]{24,}\b"),
)


def public_files(root: Path) -> list[Path]:
    root = root.resolve()
    result = []
    for name in ROOT_FILES + EXTRA_FILES:
        path = root / name
        if not path.is_file():
            raise ValueError(f"Missing release file: {name}")
        result.append(path)
    for folder, extensions in DIRECTORIES.items():
        directory = root / folder
        if not directory.is_dir():
            raise ValueError(f"Missing release directory: {folder}")
        result.extend(path for path in directory.rglob("*")
                      if path.is_file() and path.suffix in extensions
                      and not any(part.startswith(".") or part == "__pycache__"
                                  for part in path.relative_to(directory).parts))
    for path in result:
        if path.is_symlink() or not path.resolve().is_relative_to(root):
            raise ValueError("Source release does not accept links outside the project")
        if path.suffix == ".png":
            if not path.read_bytes().startswith(b"\x89PNG\r\n\x1a\n"):
                raise ValueError(f"Invalid release image: {path.relative_to(root)}")
            continue
        content = path.read_text(encoding="utf-8")
        if any(pattern.search(content) for pattern in SECRET_PATTERNS):
            raise ValueError(f"Credential-shaped content in {path.relative_to(root)}")
    return sorted(set(result), key=lambda path: path.relative_to(root).as_posix())


def package(root: Path = ROOT) -> Path:
    root = root.resolve()
    version_text = (root / "src/igd/__init__.py").read_text(encoding="utf-8")
    match = re.search(r'__version__ = "([0-9]+\.[0-9]+\.[0-9]+)"', version_text)
    if not match:
        raise ValueError("Missing package version")
    version = match.group(1)
    files = public_files(root)
    prefix = f"igd-{version}"
    destination = root / "dist"
    destination.mkdir(exist_ok=True)
    archive = destination / f"{prefix}-source.zip"
    temporary = archive.with_suffix(".zip.tmp")
    with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for path in files:
            name = prefix + "/" + path.relative_to(root).as_posix()
            # Fixed metadata makes equal source trees produce equal archives.
            info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            bundle.writestr(info, path.read_bytes())
    temporary.replace(archive)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    archive.with_suffix(".zip.sha256").write_text(f"{digest}  {archive.name}\n", encoding="ascii")
    return archive


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    path = package()
    print(f"Created {path}")
