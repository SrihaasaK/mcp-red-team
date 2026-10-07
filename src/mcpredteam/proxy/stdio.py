"""stdio face of the proxy: the agent launches one of these per MCP server.

Usage: python -m mcpredteam.proxy.stdio --trial-dir DIR --server NAME
It reads DIR/trial.json for the trial context. stdout carries only protocol
messages, as newline-delimited JSON; everything else goes to the trial log.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from mcpredteam.attacks.engine import build_attack
from mcpredteam.proxy.http_app import ctx_for
from mcpredteam.proxy.relay import Relay, jsonrpc_error
from mcpredteam.scaffolds.contract import TrialSpec


async def serve(trial_dir: Path, server: str) -> None:
    spec = TrialSpec.read(trial_dir / "trial.json")
    interceptors = (build_attack(spec.attack_config),) if spec.attack_config else ()
    relay = Relay(
        server=server,
        ctx=ctx_for(spec),
        mode=spec.mode,
        trial_dir=Path(spec.trial_dir),
        recording_dir=Path(spec.recording_dir) if spec.recording_dir else None,
        interceptors=interceptors,
    )
    await relay.start()
    loop = asyncio.get_running_loop()
    reader = asyncio.StreamReader(limit=16 * 1024 * 1024)  # default 64 KiB is too small for big messages
    await loop.connect_read_pipe(lambda: asyncio.StreamReaderProtocol(reader), sys.stdin)
    out = sys.stdout.buffer

    def send(message: dict) -> None:
        out.write(json.dumps(message, ensure_ascii=False).encode() + b"\n")
        out.flush()

    try:
        while line := await reader.readline():
            line = line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                send(jsonrpc_error(None, -32700, "Parse error"))
                continue
            reply = await relay.handle(message)
            if reply.body is not None:
                send(reply.body)
    finally:
        await relay.close()


def main() -> None:
    ap = argparse.ArgumentParser(prog="mcpredteam.proxy.stdio")
    ap.add_argument("--trial-dir", required=True, type=Path)
    ap.add_argument("--server", required=True)
    args = ap.parse_args()
    asyncio.run(serve(args.trial_dir, args.server))


if __name__ == "__main__":
    main()
