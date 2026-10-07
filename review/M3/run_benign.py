"""Reproducible driver for the M3 benign-suite run on a scaffold (SPEC §5.2).

Runs every benign task once per transport, no attacks, at the frozen Sonnet
settings, under a hard spend cap. Era is forced by `--era` (LangGraph is
stateful-only, D6, so its only era is 2025-11-25). Writes `runs/<run-id>/`.

    uv run python review/M3/run_benign.py \
        --scaffold langgraph --era 2025-11-25 --budget 4

The API key is read from the environment by `config.anthropic_api_key()`; it is
never printed, logged, or written to a result file.
"""

from __future__ import annotations

import argparse
import asyncio
import json

from mcpredteam.config import ANTHROPIC_API, anthropic_api_key, repo_root
from mcpredteam.proxy.relay import HANDSHAKE, STATELESS
from mcpredteam.runner.evaluate import run_benign
from mcpredteam.runner.tasks import load_tasks

ERAS = {"2025-11-25": HANDSHAKE, "2026-07-28": STATELESS}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scaffold", default="langgraph")
    ap.add_argument("--model", default="claude-sonnet-5-5")
    ap.add_argument("--era", default="2025-11-25", choices=sorted(ERAS))
    ap.add_argument("--transports", nargs="+", default=["stdio", "streamable-http"])
    ap.add_argument("--budget", type=float, required=True, help="hard USD spend cap")
    ap.add_argument("--base-url", default=None,
                    help="model endpoint origin; claude-agent-sdk defaults to the native Anthropic API")
    ap.add_argument("--limit", type=int, default=None, help="cap the number of tasks (smoke runs)")
    ap.add_argument("--run-id", default=None)
    args = ap.parse_args()

    base_url = args.base_url
    if base_url is None and args.scaffold == "claude-agent-sdk":
        # Native Anthropic Messages API, recorded through the D15 proxy (PREREG Amendment A1, D39).
        base_url = ANTHROPIC_API

    tasks = load_tasks()
    if args.limit:
        tasks = tasks[:args.limit]
    run_id = args.run_id or f"m3-{args.scaffold}-benign"
    summary = asyncio.run(run_benign(
        run_id, tasks, args.transports,
        scaffold=args.scaffold, model=args.model, era=ERAS[args.era],
        api_key=anthropic_api_key(), budget_usd=args.budget,
        out_root=repo_root() / "runs", base_url=base_url,
    ))
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
