"""Environment loading without interpolation, implicit file search or key logging."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from .errors import ValidationError
from .models import nonempty_text, number


def load_environment(path: Path | None = None) -> dict[str, str]:
    values = {}
    if path is not None:
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            key, separator, value = line.partition("=")
            key, value = key.strip(), value.strip()
            if not separator or not re.fullmatch(r"[A-Z][A-Z0-9_]*", key) or key in values:
                raise ValidationError("Invalid or duplicate entry in environment file")
            if value.startswith(('"', "'")):
                if len(value) < 2 or value[-1] != value[0]:
                    raise ValidationError("Unclosed quote in environment file")
                value = value[1:-1]
            values[key] = value
    values.update(os.environ)
    return values


@dataclass(frozen=True)
class Endpoint:
    url: str
    api_key: str = field(repr=False)
    user: str = "igd-client"
    timeout_seconds: float = 180.0
    input_key: str = "request"
    result_path: str = "data.outputs.result"

    def __post_init__(self):
        try:
            url = urlsplit(self.url)
            port = url.port
        except (ValueError, TypeError):
            raise ValidationError("Invalid Dify endpoint URL") from None
        local = url.hostname in {"localhost", "127.0.0.1", "::1"}
        if not url.hostname or url.username or url.password or url.query or url.fragment:
            raise ValidationError("Dify URL must have a host and no credentials, query or fragment")
        if url.scheme != "https" and not (url.scheme == "http" and local):
            raise ValidationError("Dify endpoints require HTTPS; local loopback endpoints may use HTTP")
        if not re.search(r"/workflows/(?:[A-Za-z0-9_-]+/)?run$", url.path):
            raise ValidationError("Dify URL must point to a workflow run API endpoint")
        if port is not None and port <= 0:
            raise ValidationError("Invalid Dify endpoint port")
        nonempty_text(self.api_key, "Dify API key")
        if any(ord(ch) < 32 or ord(ch) == 127 for ch in self.api_key) or self.api_key.strip() == "replace-me":
            raise ValidationError("Set a valid Dify API key")
        nonempty_text(self.user, "Dify user")
        number(self.timeout_seconds, "timeout_seconds", 1, 600)
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", self.input_key):
            raise ValidationError("Invalid Dify input key")
        if not re.fullmatch(r"data\.outputs(?:\.[A-Za-z_][A-Za-z0-9_]*)*", self.result_path):
            raise ValidationError("Result path must identify data.outputs or a nested output field")

    @classmethod
    def from_environment(cls, role: str, environment: dict[str, str]) -> Endpoint:
        if role not in {"TP", "PD", "PM", "PA"}:
            raise ValidationError("Unknown Dify role")
        prefix = f"IGD_DIFY_{role}_"
        api_key = environment.get(prefix + "API_KEY", "")
        if not api_key:
            raise ValidationError(f"Set {prefix}API_KEY for the {role} workflow")
        base = environment.get("IGD_DIFY_BASE_URL", "https://api.dify.ai/v1").rstrip("/")
        try:
            timeout = float(environment.get("IGD_DIFY_TIMEOUT_SECONDS", "180"))
        except ValueError:
            raise ValidationError("Invalid Dify timeout") from None
        return cls(
            url=environment.get(prefix + "URL", base + "/workflows/run"),
            api_key=api_key,
            user=environment.get("IGD_DIFY_USER", "igd-client"),
            timeout_seconds=timeout,
            input_key=environment.get(prefix + "INPUT_KEY", "request"),
            result_path=environment.get(prefix + "RESULT_PATH", "data.outputs.result"),
        )

