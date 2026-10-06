"""Self-test: drive the logging server with the in-venv mcp 2.x Client.

Confirms the logger captures era signals before real scaffolds are pointed at it.
Connects, lists tools TWICE (client-cache behavior), and calls a tool.
"""

from __future__ import annotations

import asyncio
import sys

import mcp


async def main(url: str, mode: str) -> None:
    last = None
    for attempt in range(40):  # retry while server boots (asyncio sleep, not shell sleep)
        try:
            kwargs = {} if mode == "auto" else {"mode": mode}
            async with mcp.Client(url, **kwargs) as client:
                t1 = await client.list_tools()
                t2 = await client.list_tools()
                r = await client.call_tool("echo", {"text": "canary-123"})
                names = sorted(t.name for t in t1.tools)
                print(f"CONNECTED mode={mode} tools={names} list_calls=2 "
                      f"echo_result={getattr(r, 'content', r)!r:.80}")
                return
        except Exception as e:  # noqa: BLE001
            last = e
            await asyncio.sleep(0.25)
    print(f"FAILED to connect after retries: {type(last).__name__}: {last}", file=sys.stderr)
    sys.exit(2)


if __name__ == "__main__":
    url = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:9777/mcp"
    mode = sys.argv[2] if len(sys.argv) > 2 else "auto"
    asyncio.run(main(url, mode))
