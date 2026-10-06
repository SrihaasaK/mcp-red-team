"""`mcpr` command-line entry point (M0: stubs only).

Subcommands are registered so `mcpr --help` lists the surface, but each body
raises until its milestone lands: proxy/run/replay at M1+, report at M2+.
"""

from __future__ import annotations

import typer

app = typer.Typer(
    name="mcpr",
    help="MCP Red Team — dynamic red-team harness for MCP agents.",
    no_args_is_help=True,
    add_completion=False,
)

_NOT_YET = "not implemented yet — lands at {ms} (SPEC §12). M0 ships skeleton only."


@app.command()
def proxy() -> None:
    """Run the transparent MCP proxy (M1)."""
    typer.secho(_NOT_YET.format(ms="M1"), fg=typer.colors.YELLOW, err=True)
    raise typer.Exit(code=1)


@app.command()
def run(matrix: bool = typer.Option(False, "--matrix", help="Run the trial matrix.")) -> None:
    """Run benign tasks / attack trials through the proxy (M1+/M2)."""
    typer.secho(_NOT_YET.format(ms="M1-M2"), fg=typer.colors.YELLOW, err=True)
    raise typer.Exit(code=1)


@app.command()
def replay() -> None:
    """Replay a recorded session with zero live calls (M1, SPEC §9.1)."""
    typer.secho(_NOT_YET.format(ms="M1"), fg=typer.colors.YELLOW, err=True)
    raise typer.Exit(code=1)


@app.command()
def report() -> None:
    """Render ASR / exposure / utility tables and fill <<...>> (M2+, SPEC §8)."""
    typer.secho(_NOT_YET.format(ms="M2+"), fg=typer.colors.YELLOW, err=True)
    raise typer.Exit(code=1)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
