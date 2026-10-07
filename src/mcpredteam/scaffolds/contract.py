"""The runner ↔ scaffold-adapter contract (DECISIONS.md D12).

The runner writes `trial.json` into a fresh trial directory and launches the
adapter (an independent uv project) as a subprocess with that path. The adapter
writes `adapter_result.json` and `llm.jsonl` back into the same directory. The
stdio proxy reads the same `trial.json` to learn its trial context.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

MODES = ("record", "replay")
TRANSPORTS = ("stdio", "streamable-http")


@dataclass
class TrialSpec:
    run_id: str
    trial_id: str
    task_id: str
    instruction: str
    era: str  # forced era: "2025-11-25" (handshake-only) or "2026-07-28" (stateless-only)
    transport: str  # "stdio" | "streamable-http"
    scaffold: str
    model: str
    mode: str  # "record" | "replay"
    trial_dir: str
    servers: list[str]
    # per server: {"url": ...} for streamable-http, {"command": ..., "args": [...], "cwd": ...} for stdio
    mcp: dict[str, dict] = field(default_factory=dict)
    recording_dir: str | None = None  # replay: the recorded trial to serve from
    base_url: str = "https://api.anthropic.com/v1/"
    model_settings: dict = field(default_factory=dict)
    max_turns: int = 12
    attack_config: dict | None = None  # frozen corpus config to inject; None = control (no-attack) arm
    prompts: list[str] = field(default_factory=list)  # per-turn instructions; falls back to [instruction]

    def __post_init__(self) -> None:
        if self.mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}, got {self.mode!r}")
        if self.transport not in TRANSPORTS:
            raise ValueError(f"transport must be one of {TRANSPORTS}, got {self.transport!r}")
        if self.mode == "replay" and not self.recording_dir:
            raise ValueError("replay needs recording_dir")

    @property
    def path(self) -> Path:
        return Path(self.trial_dir) / "trial.json"

    def write(self) -> Path:
        Path(self.trial_dir).mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(asdict(self), indent=2) + "\n")
        return self.path

    @classmethod
    def read(cls, path: Path) -> TrialSpec:
        return cls(**json.loads(Path(path).read_text()))
