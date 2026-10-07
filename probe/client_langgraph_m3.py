"""M3 D17-style probe client: the REAL langchain-mcp-adapters 0.3.2 / mcp 1.30.0 client.

Run with the scaffolds/langgraph venv. Each phase is one proxy trial driven by
probe/m3_langgraph_probe.py; this process only exercises the client and prints
a JSON observation. No model is involved.

  python client_langgraph_m3.py list_twice <url>
  python client_langgraph_m3.py call_thrice <url> <tool> <json-args>
  python client_langgraph_m3.py elicit <url> <tool> <json-args>
"""

import asyncio
import json
import sys

from langchain_mcp_adapters.client import MultiServerMCPClient


def desc(tools) -> dict:
    return {t.name: t.description for t in tools}


async def main() -> dict:
    phase, url = sys.argv[1], sys.argv[2]
    client = MultiServerMCPClient({"probe": {"transport": "streamable_http", "url": url}})
    out: dict = {"phase": phase}
    if phase == "static":
        import inspect
        from importlib.metadata import version

        import langchain_mcp_adapters.client as lc_client
        import langchain_mcp_adapters.tools as lc_tools
        from mcp.client import session as sess
        src = inspect.getsource(sess._default_elicitation_callback)
        out["mcp_version"] = version("mcp")
        out["default_elicitation_callback_source"] = src.strip().splitlines()[-6:]
        out["client_session_advertises_elicitation_only_if_callback_given"] = (
            "elicitation_callback is not _default_elicitation_callback" in inspect.getsource(sess.ClientSession))
        out["tools_module_mcp_list_calls"] = [w for w in ("list_tools", "list_resources", "list_prompts")
                                              if w in inspect.getsource(lc_tools)]
        out["client_class_methods_for_resources_prompts"] = [
            m for m in ("get_resources", "get_prompt") if hasattr(lc_client.MultiServerMCPClient, m)]
        out["note"] = "get_resources/get_prompt exist but get_tools() and the adapter never call them"
        out["tools_module_resource_or_prompt_calls"] = [w for w in ("list_resources", "list_prompts", "read_resource")
                                                        if w in inspect.getsource(lc_tools)]
        out["mcp_client_has_ttl_or_cache_hint_handling"] = [
            w for w in ("ttlMs", "ttl_ms", "cacheScope", "cache_scope") if w in inspect.getsource(sess)]
        return out
    if phase == "list_twice":
        out["first_get_tools"] = sorted(desc(await client.get_tools()))
        out["second_get_tools"] = sorted(desc(await client.get_tools()))
    elif phase == "call_thrice":
        tool_name, args = sys.argv[3], json.loads(sys.argv[4])
        tools = await client.get_tools()
        tool = next(t for t in tools if t.name == tool_name)
        out["description_at_build"] = tool.description
        results = []
        for _ in range(3):
            results.append(str(await tool.ainvoke(args))[:80])
            out.setdefault("description_on_tool_object_after_each_call", []).append(tool.description)
        out["results"] = results
        fresh = next(t for t in await client.get_tools() if t.name == tool_name)
        out["description_after_fresh_get_tools"] = fresh.description
    elif phase == "elicit":
        tool_name, args = sys.argv[3], json.loads(sys.argv[4])
        tools = await client.get_tools()
        tool = next(t for t in tools if t.name == tool_name)
        try:
            out["result"] = str(await asyncio.wait_for(tool.ainvoke(args), 20))[:300]
            out["error"] = None
        except BaseException as exc:  # noqa: BLE001 - report the root cause
            root = exc
            while isinstance(root, BaseExceptionGroup) and root.exceptions:
                root = root.exceptions[0]
            out["error"] = f"{type(root).__name__}: {root}"[:300]
    return out


if __name__ == "__main__":
    print("PROBE_JSON " + json.dumps(asyncio.run(main())))
