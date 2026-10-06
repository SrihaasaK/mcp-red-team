"""Probe client: OpenAI Agents SDK MCP client (no LLM). Lists tools twice."""
import asyncio, os, sys, traceback
from agents.mcp import MCPServerStreamableHttp, MCPServerStreamableHttpParams

async def run(url: str) -> None:
    s = MCPServerStreamableHttp(
        params=MCPServerStreamableHttpParams(url=url),
        cache_tools_list=False, client_session_timeout_seconds=10,
    )
    await s.connect()
    t1 = await s.list_tools()
    await s.list_tools()
    names = sorted(getattr(t, "name", str(t)) for t in t1)
    print(f"OPENAI_OK tools={names} list_tools_calls=2", flush=True)
    try:
        await s.cleanup()
    except Exception:
        pass

async def main() -> None:
    try:
        await asyncio.wait_for(run(sys.argv[1]), timeout=25)
    except Exception:
        print("OPENAI_ERR", flush=True); traceback.print_exc(); sys.stderr.flush()
    sys.stdout.flush(); sys.stderr.flush()
    os._exit(0)

asyncio.run(main())
