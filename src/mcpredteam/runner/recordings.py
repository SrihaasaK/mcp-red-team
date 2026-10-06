"""Export a recorded run as a portable, sanitized fixture (e.g. tests/recordings/).

Drops captured stderr, rewrites absolute paths to placeholders, and refuses to
finish if any file still holds a home-directory path or key material
(SPEC §3: no secrets or personal data in fixtures).
Usage: python -m mcpredteam.runner.recordings RUN_DIR DEST
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

from mcpredteam.config import repo_root

SKIP = {"adapter.stderr.txt"}
FORBIDDEN = ("/Users/", "/home/", "sk-ant-")


def export_run(run_dir: Path, dest: Path) -> list[str]:
    run_dir, dest, root = Path(run_dir).resolve(), Path(dest).resolve(), repo_root()
    if dest.exists():
        raise FileExistsError(f"{dest} exists; remove it first")
    for trial in sorted(p for p in run_dir.iterdir() if p.is_dir()):
        out = dest / trial.name
        out.mkdir(parents=True)
        for f in trial.iterdir():
            if f.name in SKIP:
                continue
            text = f.read_text()
            if f.name == "trial.json":
                spec = json.loads(text)
                spec["trial_dir"] = f"<repo>/runs/{run_dir.name}/{trial.name}"
                for server, cfg in spec["mcp"].items():
                    if "url" in cfg:
                        spec["mcp"][server] = {"url": f"http://127.0.0.1:<port>/t/{trial.name}/{server}/mcp"}
                    else:
                        spec["mcp"][server] = {"command": "<project python>", "cwd": "<repo>",
                                               "args": [str(a).replace(str(trial), "<trial_dir>") for a in cfg["args"]]}
                text = json.dumps(spec, indent=2) + "\n"
            (out / f.name).write_text(text.replace(str(root), "<repo>"))
    if (run_dir / "summary.json").is_file():
        shutil.copy(run_dir / "summary.json", dest / "summary.json")
    leaks = [str(p) for p in dest.rglob("*") if p.is_file() and any(s in p.read_text() for s in FORBIDDEN)]
    if leaks:
        shutil.rmtree(dest)
        raise ValueError(f"export aborted, sensitive strings in: {leaks}")
    return sorted(p.name for p in dest.iterdir() if p.is_dir())


if __name__ == "__main__":
    print(f"exported {len(export_run(Path(sys.argv[1]), Path(sys.argv[2])))} trials")
