"""Harness settings read from the repo's `.env` (SPEC §3).

Keys come only from `.env`: never from the process environment, and never
exported to a shell. A missing key fails fast with a clear message. Scaffold
subprocesses receive the key from the runner in their own environment only.
"""

from __future__ import annotations

import datetime
import json
import urllib.error
import urllib.request
from pathlib import Path

ANTHROPIC_API = "https://api.anthropic.com"
ANTHROPIC_VERSION = "2023-06-01"


class MissingKeyError(RuntimeError):
    """Raised when a required key is absent from `.env`."""


def repo_root(start: Path | None = None) -> Path:
    """Walk up from `start` (default: cwd) to the directory holding this project's pyproject."""
    here = (start or Path.cwd()).resolve()
    for d in (here, *here.parents):
        pp = d / "pyproject.toml"
        if pp.is_file() and 'name = "mcpredteam"' in pp.read_text():
            return d
    raise FileNotFoundError("not inside the mcpredteam repo (no matching pyproject.toml found)")


def read_dotenv(path: Path | None = None) -> dict[str, str]:
    path = path or repo_root() / ".env"
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        values[name.strip()] = value.strip().strip('"').strip("'").strip()
    return values


def anthropic_api_key(path: Path | None = None) -> str:
    path = path or repo_root() / ".env"
    key = read_dotenv(path).get("ANTHROPIC_API_KEY", "")
    if not key:
        raise MissingKeyError(
            f"ANTHROPIC_API_KEY is missing or empty in {path}. "
            "Copy .env.example to .env and set it (SPEC §3)."
        )
    if any(ch.isspace() for ch in key):
        raise MissingKeyError(f"ANTHROPIC_API_KEY in {path} contains whitespace; fix the .env line.")
    return key


def check_models(path: Path | None = None) -> dict:
    """Free check: GET /v1/models. Returns status and model ids; never the key."""
    req = urllib.request.Request(
        f"{ANTHROPIC_API}/v1/models?limit=100",
        headers={"x-api-key": anthropic_api_key(path), "anthropic-version": ANTHROPIC_VERSION},
    )
    checked_at = datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds")
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            body, status = json.load(resp), resp.status
    except urllib.error.HTTPError as err:
        return {"checked_at": checked_at, "status": err.code, "error": err.read().decode()[:300]}
    return {
        "checked_at": checked_at,
        "status": status,
        "model_ids": [m["id"] for m in body.get("data", [])],
    }


if __name__ == "__main__":
    print(json.dumps(check_models(), indent=2))
