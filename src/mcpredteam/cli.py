"""`mcpr` command-line entry point.

M1 implements `proxy` (stdio face), `run --matrix` (live record), and `replay`.
`report` lands at M2.
"""

from __future__ import annotations

import asyncio
import datetime
import json
from pathlib import Path

import typer

from mcpredteam.proxy.relay import ERAS

app = typer.Typer(
    name="mcpr",
    help="MCP Red Team — dynamic red-team harness for MCP agents.",
    no_args_is_help=True,
    add_completion=False,
)

# Harness model settings for the OpenAI Agents SDK adapter (DECISIONS.md D20, D21).
# Temperature is rejected by these models and seed is ignored, so neither is sent.
# Effort is pinned to each model's documented default; thinking stays at the
# OpenAI-compatible endpoint's default (adaptive is not available there).
DEFAULT_MODEL_SETTINGS = {"parallel_tool_calls": False, "max_tokens": 8192}
PINNED_EFFORT = {"claude-sonnet-5-5": "high", "claude-opus-5-5": "medium"}


def model_settings(model: str) -> dict:
    settings = dict(DEFAULT_MODEL_SETTINGS)
    if model in PINNED_EFFORT:
        settings["extra_body"] = {"output_config": {"effort": PINNED_EFFORT[model]}}
    return settings


@app.command()
def proxy(
    stdio: bool = typer.Option(False, "--stdio", help="Serve one MCP server over stdio."),
    trial_dir: Path = typer.Option(None, "--trial-dir", help="Trial directory holding trial.json."),
    server: str = typer.Option(None, "--server", help="Upstream mock to relay (notes, http)."),
) -> None:
    """Run the transparent MCP proxy over stdio. The Streamable HTTP hub runs inside `mcpr run`."""
    if not (stdio and trial_dir and server):
        typer.secho("usage: mcpr proxy --stdio --trial-dir DIR --server NAME", fg=typer.colors.YELLOW, err=True)
        raise typer.Exit(code=2)
    from mcpredteam.proxy.stdio import serve

    asyncio.run(serve(trial_dir, server))


@app.command()
def run(
    matrix: bool = typer.Option(False, "--matrix", help="Run every task × era × transport combination."),
    task: list[str] = typer.Option(None, "--task", help="Task id(s); default all."),
    era: list[str] = typer.Option(None, "--era", help=f"Forced era(s) from {ERAS}; default both."),
    transport: list[str] = typer.Option(None, "--transport", help="stdio and/or streamable-http; default both."),
    model: str = typer.Option("claude-sonnet-5-5", "--model"),
    scaffold: str = typer.Option("openai-agents", "--scaffold"),
    budget: float = typer.Option(5.0, "--budget", help="Stop starting trials once this much USD is spent."),
    run_id: str = typer.Option(None, "--run-id"),
) -> None:
    """Run benign tasks live through the proxy, recording everything for replay."""
    if not matrix:
        typer.secho("pass --matrix (filter with --task/--era/--transport)", fg=typer.colors.YELLOW, err=True)
        raise typer.Exit(code=2)
    from mcpredteam.runner.matrix import run_matrix
    from mcpredteam.runner.tasks import load_tasks

    tasks = [t for t in load_tasks() if not task or t.id in task]
    settings = model_settings(model)
    rid = run_id or "m1-" + datetime.datetime.now(datetime.UTC).strftime("%Y%m%dT%H%M%SZ")
    summary = asyncio.run(run_matrix(
        run_id=rid, tasks=tasks, eras=era or list(ERAS),
        transports=transport or ["streamable-http", "stdio"], scaffold=scaffold, model=model,
        model_settings=settings, budget_usd=budget,
    ))
    typer.echo(json.dumps(summary, indent=2))


@app.command()
def replay(
    run_dir: Path = typer.Argument(..., help="A recorded run directory (runs/<run_id>)."),
    out: Path = typer.Option(None, "--out", help="Where to write the replay trials."),
) -> None:
    """Replay a recorded run with zero live calls and check replay equality (SPEC §9.1)."""
    from mcpredteam.runner.matrix import replay_run

    summary = asyncio.run(replay_run(run_dir, out))
    typer.echo(json.dumps({k: v for k, v in summary.items() if k != "comparisons"}, indent=2))
    if summary["trials"] == 0 or summary["equal"] != summary["trials"]:
        raise typer.Exit(code=1)


@app.command()
def report() -> None:
    """Render ASR / exposure / utility tables and fill <<...>> (M2+, SPEC §8)."""
    typer.secho("not implemented yet — lands at M2 (SPEC §12).", fg=typer.colors.YELLOW, err=True)
    raise typer.Exit(code=1)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
