"""Build a SANITIZED held-out recordings bundle for public release (reproduction asset).

$0, offline. Produces a tarball sufficient to rebuild review/M4/heldout-static/aggregate.json
byte-identically (see rebuild_from_release.py). Contents (all sanitized):
  frontier/cells.jsonl, frontier/records.jsonl, frontier/summary.json  (frontier static aggregate inputs)
  ladder/cells.jsonl                                                   (ladder static exploratory)
  frontier/<trial dirs for the answer-hijack SUCCESSES>                (the only per-trial recordings the
                                                                        aggregate hydrates, for the D55
                                                                        refusal-quote audit)

SANITIZATION (CLAUDE.md #7):
  * Canaries are already SYNTHETIC (repo invariant); nothing real is present.
  * Absolute paths `<repo>/runs/m4-heldout-static/...` are redacted to `<RECORDINGS>/...` everywhere
    (records.jsonl trial_dir -> relative; inside every copied recording file). These paths carry a
    local username and never feed the oracle, so redaction cannot change a verdict.
  * SECRET SCAN (hard gate): the staged bundle is scanned for any residual absolute home path and for
    common secret patterns; the build ABORTS if anything is found. .env is never read or copied.

    uv run python -I review/M4/heldout-static/make_release_bundle.py
"""
import json
import re
import shutil
import tarfile
from pathlib import Path

from mcpredteam.config import repo_root

ROOT = repo_root()
FRONTIER = ROOT / "runs" / "m4-heldout-static"
LADDER = ROOT / "runs" / "m4-heldout-ladder-static"
STAGE = ROOT / "runs" / "_release_stage" / "heldout-static-recordings"
TARBALL = ROOT / "runs" / "_release_stage" / "heldout-static-recordings.tar.gz"

ABS_PREFIX = str(FRONTIER) + "/"          # <repo>/runs/m4-heldout-static/
SECRET_PATTERNS = [
    re.compile(r"/Users/[^/\s\"]+/"),                 # any absolute home path (incl. the author's)
    re.compile(r"sk-[A-Za-z0-9]{20,}"),               # OpenAI-style key
    re.compile(r"sk-ant-[A-Za-z0-9-]{20,}"),          # Anthropic key
    re.compile(r"AKIA[0-9A-Z]{16}"),                  # AWS access key id
    re.compile(r"(?i)(api[_-]?key|secret|password|token)\s*[:=]\s*[\"']?[A-Za-z0-9/\+]{16,}"),
]


def _redact(text: str) -> str:
    # strip the absolute prefix (so paths become relative to the bundle root), then any stray abs path.
    return text.replace(ABS_PREFIX, "").replace(str(FRONTIER), "")


def _copy_redacted(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(_redact(src.read_text()))


def main() -> None:
    if STAGE.exists():
        shutil.rmtree(STAGE)
    (STAGE / "frontier").mkdir(parents=True)
    (STAGE / "ladder").mkdir(parents=True)

    _copy_redacted(FRONTIER / "cells.jsonl", STAGE / "frontier" / "cells.jsonl")
    _copy_redacted(FRONTIER / "summary.json", STAGE / "frontier" / "summary.json")
    _copy_redacted(FRONTIER / "records.jsonl", STAGE / "frontier" / "records.jsonl")
    _copy_redacted(LADDER / "cells.jsonl", STAGE / "ladder" / "cells.jsonl")

    records = [json.loads(l) for l in (FRONTIER / "records.jsonl").read_text().splitlines() if l.strip()]
    hijack = [r for r in records if r["attack_rule"] == "answer_hijack" and r["attack_success"]
              and not r.get("error")]
    copied = 0
    for r in hijack:
        rel = r["trial_dir"].replace(ABS_PREFIX, "")
        base = FRONTIER / rel
        for f in base.rglob("*"):
            if f.is_file():
                _copy_redacted(f, STAGE / "frontier" / rel / f.relative_to(base))
        copied += 1

    (STAGE / "README.txt").write_text(
        "MCP Red Team - held-out static recordings (sanitized rebuild bundle).\n"
        "Synthetic canaries only; absolute paths redacted to <RECORDINGS>/; secret-scanned.\n"
        "Rebuild the committed aggregate from this bundle (byte-identical):\n"
        "  uv run python -I review/M4/heldout-static/rebuild_from_release.py <extracted-dir>\n")

    # --- SECRET SCAN (hard gate) ---
    hits = []
    for f in STAGE.rglob("*"):
        if not f.is_file():
            continue
        txt = f.read_text(errors="replace")
        for pat in SECRET_PATTERNS:
            for m in pat.finditer(txt):
                hits.append(f"{f.relative_to(STAGE)}: {pat.pattern} -> {m.group(0)[:40]!r}")
    if hits:
        raise SystemExit("SECRET SCAN FAILED - refusing to package:\n" + "\n".join(hits[:50]))

    TARBALL.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(TARBALL, "w:gz") as tar:
        tar.add(STAGE, arcname="heldout-static-recordings")
    nfiles = sum(1 for _ in STAGE.rglob("*") if _.is_file())
    print(f"staged {nfiles} files ({copied} hijack-success trial dirs); secret scan CLEAN")
    print("tarball:", TARBALL, f"({TARBALL.stat().st_size/1e6:.2f} MB)")


if __name__ == "__main__":
    main()
