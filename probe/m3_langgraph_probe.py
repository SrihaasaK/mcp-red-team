"""M3 D17-style probe of LangGraph for the conditional classes 3, 4, 6, 8 (D28).

Real proxy, real langchain-mcp-adapters/mcp 1.30.0 client (subprocess in the
scaffolds/langgraph venv), no live model. Class 3 additionally drives the real
adapter with a scripted fake model and the real rug-pull attack interceptor.
Writes review/M3/langgraph_probe/.   Run: uv run python probe/m3_langgraph_probe.py
"""

from __future__ import annotations

import asyncio
import json
import shutil
import socket
import sys
from collections import Counter
from pathlib import Path

import uvicorn
import yaml
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

sys.path.insert(0, str(Path(__file__).parent))
from m2_caching_mrtr import build_cache_server, build_mrtr_server

from mcpredteam.config import repo_root
from mcpredteam.env import UPSTREAMS
from mcpredteam.proxy.http_app import HubServer, ProxyHub
from mcpredteam.proxy.relay import HANDSHAKE
from mcpredteam.runner import trial as trial_mod
from mcpredteam.runner.tasks import load_tasks
from mcpredteam.runner.trial import run_trial
from mcpredteam.scaffolds.contract import TrialSpec

ROOT = repo_root()
OUT = ROOT / "review" / "M3" / "langgraph_probe"
PY = ROOT / "scaffolds" / "langgraph" / ".venv" / "bin" / "python"
CLIENT = ROOT / "probe" / "client_langgraph_m3.py"
trial_mod.ADAPTERS.setdefault("langgraph", "scaffolds/langgraph")


def wire(trial_dir: Path, server: str) -> list[dict]:
    p = trial_dir / f"proxy.{server}.jsonl"
    return [json.loads(x) for x in p.read_text().splitlines()] if p.is_file() else []


def methods(records: list[dict]) -> dict:
    c: Counter = Counter()
    for r in records:
        m = r.get("message")
        if r["dir"] == "c2s" and r["stage"] == "in" and isinstance(m, dict) and "method" in m:
            c[m["method"]] += 1
    return dict(c)


def initialize_caps(records: list[dict]) -> list:
    return [r["message"]["params"]["capabilities"] for r in records
            if r["dir"] == "c2s" and r["stage"] == "in" and (r.get("message") or {}).get("method") == "initialize"]


def tools_list_results(records: list[dict]) -> list[dict]:
    """Every tools/list RESULT the proxy sent to the client, in order."""
    return [r["message"]["result"] for r in records if r["dir"] == "s2c" and r["stage"] == "out"
            and isinstance((r.get("message") or {}).get("result"), dict) and "tools" in r["message"]["result"]]


def one_phase(hub: HubServer, name: str, server: str, args: list[str]) -> dict:
    td = OUT / name
    spec = TrialSpec(run_id="m3-lg-probe", trial_id=name, task_id="probe", instruction="", era=HANDSHAKE,
                     transport="streamable-http", scaffold="langgraph", model="none", mode="record",
                     trial_dir=str(td), servers=[server])
    spec.write()
    hub.hub.register(spec)
    return {"spec": spec, "td": td, "args": args, "url": hub.url(name, server)}


async def run_client(h: dict, hub: HubServer, server: str) -> dict:
    proc = await asyncio.create_subprocess_exec(
        str(PY), str(CLIENT), h["args"][0], h["url"], *h["args"][1:],
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, cwd=str(ROOT))
    out, err = await asyncio.wait_for(proc.communicate(), 120)
    await hub.hub.close_trial(h["spec"].trial_id)
    line = next((x for x in out.decode().splitlines() if x.startswith("PROBE_JSON ")), None)
    obs = json.loads(line[len("PROBE_JSON "):]) if line else {"error": "no output", "stderr": err.decode()[-500:]}
    recs = wire(h["td"], server)
    obs["proxy_methods"] = methods(recs)
    obs["initialize_client_capabilities"] = initialize_caps(recs)
    obs["resources_or_prompts_methods"] = {k: v for k, v in obs["proxy_methods"].items()
                                           if k.startswith(("resources/", "prompts/"))}
    obs["tools_list_result_keys"] = [sorted(r) for r in tools_list_results(recs)]
    return obs


class ScriptedModel:
    """read_note on every user turn, then answer; records the tool descriptions each request offered."""

    def __init__(self) -> None:
        self.offered: list[dict] = []
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.port = self.sock.getsockname()[1]
        app = Starlette(routes=[Route("/v1/chat/completions", self._complete, methods=["POST"])])
        self.server = uvicorn.Server(uvicorn.Config(app, log_level="warning", lifespan="off"))

    async def _complete(self, request: Request) -> JSONResponse:
        body = await request.json()
        self.offered.append({t["function"]["name"]: t["function"]["description"] for t in body.get("tools", [])})
        if body["messages"][-1]["role"] == "tool":
            msg, finish = {"role": "assistant", "content": "done"}, "stop"
        else:
            msg = {"role": "assistant", "content": None, "tool_calls": [{
                "id": f"c{len(self.offered)}", "type": "function",
                "function": {"name": "read_note", "arguments": json.dumps({"note_id": "n1"})}}]}
            finish = "tool_calls"
        return JSONResponse({"id": "x", "object": "chat.completion", "created": 0, "model": body["model"],
                             "choices": [{"index": 0, "message": msg, "finish_reason": finish}],
                             "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}})

    async def __aenter__(self):
        self.task = asyncio.create_task(self.server.serve(sockets=[self.sock]))
        while not self.server.started:
            await asyncio.sleep(0.01)
        return self

    async def __aexit__(self, *exc):
        self.server.should_exit = True
        await self.task


async def class3_real_adapter(hub: HubServer) -> dict:
    """Real adapter + real rug-pull interceptor (c3-01, after_calls=1) over a two-turn task."""
    corpus = yaml.safe_load((ROOT / "attacks" / "corpus.yaml").read_text())["configs"]
    cfg = next(c for c in corpus if c["id"] == "c3-01-read-note-rug-exfil")
    task = next(t for t in load_tasks() if t.id == "notes-sync-then-webhook")
    td = OUT / "class3_rug_pull_real_adapter"
    async with ScriptedModel() as fm:
        spec = TrialSpec(run_id="m3-lg-probe", trial_id="class3-rug", task_id=task.id,
                         instruction=task.prompts[0], prompts=list(task.prompts), era=HANDSHAKE,
                         transport="streamable-http", scaffold="langgraph", model="claude-sonnet-5-5",
                         mode="record", trial_dir=str(td), servers=["notes", "http"], attack_config=cfg,
                         base_url=f"http://127.0.0.1:{fm.port}/v1/",
                         model_settings={"parallel_tool_calls": False, "max_tokens": 8192})
        r = await run_trial(spec, task, hub=hub, api_key="probe-fake-key", timeout_s=180)
    recs = wire(td, "notes")
    lists = tools_list_results(recs)
    descs = [next((t["description"] for t in lst["tools"] if t["name"] == "read_note"), None) for lst in lists]
    clean = descs[0]
    return {
        "attack": cfg["id"], "after_calls": cfg["rug_pull"]["after_calls"], "adapter_error": r["error"],
        "tools_list_served_to_client": len(descs),
        "tools_list_poisoned_flags_in_order": [d != clean for d in descs],
        "model_requests": len(fm.offered),
        "model_saw_poisoned_read_note_description_per_request": [
            d.get("read_note") != clean for d in fm.offered],
        "adapter_methods_at_proxy": methods(recs),
        "resources_or_prompts_methods": {k: v for k, v in methods(recs).items()
                                         if k.startswith(("resources/", "prompts/"))},
        "initialize_client_capabilities": initialize_caps(recs),
        "tool_calls": [c["name"] for c in r["tool_calls"]],
    }


async def main() -> None:
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)
    rep: dict = {"client": "langchain-mcp-adapters 0.3.2 + mcp 1.30.0 (scaffolds/langgraph venv)"}
    async with HubServer(ProxyHub()) as hub:
        # ---- class 4: does get_tools() re-fetch; does a cacheable list change that? ----
        UPSTREAMS["probe_cache"] = build_cache_server(60000)
        h = one_phase(hub, "c4_list_twice_ttl60000", "probe_cache", ["list_twice"])
        rep["class4_list_twice_ttl60000"] = await run_client(h, hub, "probe_cache")
        h = one_phase(hub, "c4_call_thrice_ttl60000", "probe_cache", ["call_thrice", "ping", "{}"])
        rep["class4_call_thrice_ttl60000"] = await run_client(h, hub, "probe_cache")
        # ---- class 8: elicitation capability + server-initiated request handling ----
        UPSTREAMS["probe_mrtr"] = build_mrtr_server
        h = one_phase(hub, "c8_elicit", "probe_mrtr", ["elicit", "needs_confirm", '{"action":"delete"}'])
        rep["class8_elicit"] = await run_client(h, hub, "probe_mrtr")
        # ---- static: source-level facts the wire cannot show ----
        h = one_phase(hub, "static_source_checks", "probe_cache", ["static"])
        rep["static_source_checks"] = await run_client(h, hub, "probe_cache")
        # ---- classes 3 and 6 with the real adapter + attack interceptor ----
        rep["class3_real_adapter"] = await class3_real_adapter(hub)
    (OUT / "probe.json").write_text(json.dumps(rep, indent=2) + "\n")
    print(json.dumps(rep, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
