"""Probe client: LangGraph langchain-mcp-adapters MultiServerMCPClient (no LLM)."""
import asyncio, os, sys, traceback
from langchain_mcp_adapters.client import MultiServerMCPClient

async def run(url: str) -> None:
    client = MultiServerMCPClient({"probe": {"url": url, "transport": "streamable_http"}})
    t1 = await client.get_tools()
    await client.get_tools()
    names = sorted(getattr(t, "name", str(t)) for t in t1)
    print(f"LANGGRAPH_OK tools={names} get_tools_calls=2", flush=True)

async def main() -> None:
    try:
        await asyncio.wait_for(run(sys.argv[1]), timeout=25)
    except Exception:
        print("LANGGRAPH_ERR", flush=True); traceback.print_exc(); sys.stderr.flush()
    sys.stdout.flush(); sys.stderr.flush()
    os._exit(0)

asyncio.run(main())
