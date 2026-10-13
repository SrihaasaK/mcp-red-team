"""Rebuild review/M4/heldout-static/aggregate.json from a released recordings bundle and assert it is
BYTE-IDENTICAL to the committed aggregate. $0, offline.

Usage (after downloading + extracting the release asset heldout-static-recordings.tar.gz):
    uv run python -I review/M4/heldout-static/rebuild_from_release.py <extracted-dir>

<extracted-dir> is the directory containing `frontier/` and `ladder/` (i.e. the extracted
`heldout-static-recordings/`). The committed aggregator (build_aggregate.py) is run unchanged against
the bundle; this script only points its input roots at the bundle and diffs the result.
"""
import importlib.util
import json
import sys
import tempfile
from pathlib import Path

from mcpredteam.config import repo_root

ROOT = repo_root()
COMMITTED = ROOT / "review" / "M4" / "heldout-static" / "aggregate.json"


def _load_build_aggregate(frontier: Path, ladder: Path, out_dir: Path):
    path = ROOT / "review" / "M4" / "heldout-static" / "build_aggregate.py"
    spec = importlib.util.spec_from_file_location("heldout_build_aggregate", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.FRONTIER, mod.LADDER, mod.OUT = frontier, ladder, out_dir  # point inputs at the bundle
    return mod


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    bundle = Path(sys.argv[1]).resolve()
    src_frontier, src_ladder = bundle / "frontier", bundle / "ladder"
    if not (src_frontier / "cells.jsonl").exists():
        raise SystemExit(f"not a recordings bundle (missing frontier/cells.jsonl): {bundle}")

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        work_frontier = tmp / "frontier"
        work_frontier.mkdir(parents=True)
        # copy the aggregate-input jsonls; rewrite records.jsonl trial_dir (relative in the bundle)
        # to absolute bundle paths so the aggregator hydrates the hijack recordings in place.
        (work_frontier / "cells.jsonl").write_text((src_frontier / "cells.jsonl").read_text())
        (work_frontier / "summary.json").write_text((src_frontier / "summary.json").read_text())
        rows = []
        for line in (src_frontier / "records.jsonl").read_text().splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            if not Path(r["trial_dir"]).is_absolute():
                r["trial_dir"] = str(src_frontier / r["trial_dir"])
            rows.append(json.dumps(r))
        (work_frontier / "records.jsonl").write_text("\n".join(rows) + "\n")

        out_dir = tmp / "out"
        out_dir.mkdir()
        ba = _load_build_aggregate(work_frontier, src_ladder, out_dir)
        ba.main()
        rebuilt = (out_dir / "aggregate.json").read_text()

    committed = COMMITTED.read_text()
    if rebuilt == committed:
        print("OK: rebuilt aggregate.json is BYTE-IDENTICAL to the committed one.")
    else:
        rl, cl = rebuilt.splitlines(), committed.splitlines()
        for i, (a, b) in enumerate(zip(rl, cl)):
            if a != b:
                raise SystemExit(f"MISMATCH at line {i + 1}:\n  rebuilt:   {a}\n  committed: {b}")
        raise SystemExit(f"MISMATCH: length differs (rebuilt {len(rl)} vs committed {len(cl)} lines)")


if __name__ == "__main__":
    main()
