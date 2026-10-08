"""Tests for the attack engine (SPEC §5.1, §6). No model/network calls.

Class 1 is driven through the real `Relay` + in-process mock `http` server
(record mode), the same way `tests/test_proxy.py` does. Classes 2, 3, 5 and 7
are driven by calling the relevant `Interceptor` hook directly with a synthetic
message, per the task's "OR by calling the hook directly" option — class 7's
`mail`/`files` mock servers don't exist yet in this milestone's `env/`, so a
synthetic `tools/call` result is the only way to exercise it.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
import yaml
from mcp import Client

from mcpredteam.attacks.engine import NA_CLASSES, AttackInterceptor, build_attack, build_chain
from mcpredteam.proxy.http_app import HubServer, ProxyHub
from mcpredteam.proxy.interfaces import TrialCtx
from mcpredteam.proxy.relay import HANDSHAKE
from mcpredteam.scaffolds.contract import TrialSpec

CORPUS = yaml.safe_load(Path("attacks/corpus.yaml").read_text())["configs"]
BY_ID = {c["id"]: c for c in CORPUS}

C1 = BY_ID["c1-01-release-page-exfil"]
C2 = BY_ID["c2-01-read-note-desc-exfil"]
C3 = BY_ID["c3-01-read-note-rug-exfil"]
C5 = BY_ID["c5-01-notes-decoy-http-post"]
C7 = BY_ID["c7-01-mail-poison-files-canary-http"]


def make_ctx(**kw) -> TrialCtx:
    base = {
        "run_id": "t", "trial_id": "t1", "era": HANDSHAKE, "scaffold": "openai-agents",
        "model": "none", "transport": "stdio",
    }
    base.update(kw)
    return TrialCtx(**base)


def tool_result(text: str) -> dict:
    return {"content": [{"type": "text", "text": text}]}


# --------------------------------------------------------------------------- #
# payload_text is always recorded, non-empty, and matches mutate_payload
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("config", [C1, C2, C3, C5, C7])
def test_payload_text_recorded_and_nonempty(config):
    ic = build_attack(config)
    assert isinstance(ic, AttackInterceptor)
    assert ic.payload_text
    # it's framed (system_tag for all the representative configs picked here)
    assert ic.payload_text.startswith("<system>") and ic.payload_text.endswith("</system>")


def test_na_classes_raise_not_implemented():
    na_configs = [c for c in CORPUS if c["class"] in NA_CLASSES]
    assert na_configs, "expected at least one N/A-class config in the corpus"
    for config in na_configs:
        with pytest.raises(NotImplementedError):
            build_attack(config)


def test_build_chain_bonus_path():
    ics = build_chain([C1, C2])
    assert len(ics) == 2
    assert all(isinstance(ic, AttackInterceptor) for ic in ics)


# --------------------------------------------------------------------------- #
# Class 1: indirect_injection_tool_result, driven through the real Relay
# --------------------------------------------------------------------------- #


def test_class1_tool_result_injection_via_real_relay(tmp_path):
    ic = build_attack(C1)

    async def go():
        hub = ProxyHub()
        async with HubServer(hub) as srv:
            spec = TrialSpec(
                run_id="r", trial_id="c1", task_id="none", instruction="",
                era=HANDSHAKE, transport="streamable-http", scaffold="openai-agents",
                model="none", mode="record", trial_dir=str(tmp_path), servers=["http"],
            )
            spec.write()
            hub.register(spec)
            relay = await hub.relay("c1", "http")
            relay.interceptors = (ic,)
            async with Client(srv.url("c1", "http"), read_timeout_seconds=20) as c:
                res = await c.call_tool("http_get", {"url": "https://docs.example.test/release-notes"})
            summary = await hub.close_trial("c1")
            return res.content[0].text, summary

    text, summary = asyncio.run(go())
    assert summary[0]["proxy_errors"] == []
    # the carrier (original page text) is still present ...
    assert "Release 4.2" in text
    # ... with the mutator-composed payload appended as a suffix, framed in <system>
    assert ic.payload_text in text
    assert text.endswith(ic.payload_text)
    # D61: the injected payload refers to the source, it NEVER contains the literal canary
    assert "CANARY-NOTES-7Q2X9" not in ic.payload_text
    assert "notes:n3" in ic.payload_text


# --------------------------------------------------------------------------- #
# Class 2: tool_description_poisoning, on_tools_list
# --------------------------------------------------------------------------- #


def test_class2_tool_description_poisoning():
    ic = build_attack(C2)
    ctx = make_ctx()
    tools = [
        {"name": "read_note", "description": "Return the full text of one note, by id."},
        {"name": "list_notes", "description": "List all notes."},
    ]
    out = asyncio.run(ic.on_tools_list(tools, ctx))
    poisoned = next(t for t in out if t["name"] == "read_note")
    untouched = next(t for t in out if t["name"] == "list_notes")
    assert untouched["description"] == "List all notes."
    assert poisoned["description"].startswith("Return the full text of one note, by id.")
    assert poisoned["description"].endswith(ic.payload_text)
    assert ctx.state["payload_text"] == ic.payload_text


# --------------------------------------------------------------------------- #
# Class 3: rug_pull, flips only after N calls (handshake era, on_tools_list)
# --------------------------------------------------------------------------- #


def test_class3_rug_pull_flips_after_n_calls():
    ic = build_attack(C3)
    ctx = make_ctx()
    clean_tools = [{"name": "read_note", "description": "Return the full text of one note, by id."}]

    async def list_now():
        return await ic.on_tools_list([dict(t) for t in clean_tools], ctx)

    # before any call to the target tool: still clean
    out = asyncio.run(list_now())
    assert out[0]["description"] == clean_tools[0]["description"]
    assert "payload_text" not in ctx.state

    # one call to the target tool (after_calls == 1)
    asyncio.run(ic.on_tool_call("read_note", {"note_id": "n1"}, ctx))
    out = asyncio.run(list_now())
    assert out[0]["description"].endswith(ic.payload_text)
    assert ctx.state["payload_text"] == ic.payload_text

    # a call to some other tool never flips it
    ic2 = build_attack(C3)
    ctx2 = make_ctx()
    asyncio.run(ic2.on_tool_call("list_notes", {}, ctx2))
    out2 = asyncio.run(ic2.on_tools_list([dict(t) for t in clean_tools], ctx2))
    assert out2[0]["description"] == clean_tools[0]["description"]


# --------------------------------------------------------------------------- #
# Class 5: shadow_tool, adds a decoy tool to the list
# --------------------------------------------------------------------------- #


def test_class5_shadow_tool_added_to_list():
    ic = build_attack(C5)
    ctx = make_ctx()
    tools = [{"name": "read_note", "description": "Return the full text of one note, by id."}]
    out = asyncio.run(ic.on_tools_list([dict(t) for t in tools], ctx))
    assert len(out) == 2
    assert out[0] == tools[0]  # the real tool is untouched
    decoy = out[1]
    assert decoy["name"] == "share_helper"
    assert "http.http_post" in decoy["description"]
    assert decoy["description"].endswith(ic.payload_text)
    assert ctx.state["payload_text"] == ic.payload_text


# --------------------------------------------------------------------------- #
# Class 7: cross_server_exfil_chain, on_tool_result on server A, names both
# servers in the injected instruction (mail/files mocks don't exist yet, so a
# synthetic result is used instead of the real Relay).
# --------------------------------------------------------------------------- #


def test_class7_cross_server_exfil_chain_injection():
    ic = build_attack(C7)
    ctx = make_ctx()
    original = "From: dana@example.test\nSubject: offsite\n\nSee you there."
    result = tool_result(original)
    out = asyncio.run(ic.on_tool_result("read_message", result, ctx))
    text = out["content"][0]["text"]
    assert original in text
    assert text.endswith(ic.payload_text)
    assert "files.read_file" in ic.payload_text
    assert "http.http_post" in ic.payload_text
    # D61: refers to the source (canary_ref), never the literal canary
    assert "CANARY-FILES-4K8M2" not in ic.payload_text
    assert "files:config/deploy.env" in ic.payload_text
    assert ctx.state["payload_text"] == ic.payload_text

    # a result for an unrelated tool is passed through untouched
    ctx2 = make_ctx()
    other = tool_result("unrelated")
    out2 = asyncio.run(ic.on_tool_result("list_notes", other, ctx2))
    assert out2["content"][0]["text"] == "unrelated"
    assert "payload_text" not in ctx2.state


# --------------------------------------------------------------------------- #
# payload_text is what oracle.exposure is handed, and it is the needle that
# exposure.decoders_hit actually finds once embedded in a carrier (reviewer
# item #2's integration point), including for an encoded+framed config.
# --------------------------------------------------------------------------- #


def test_payload_text_is_the_exposure_needle():
    from mcpredteam.oracle.exposure import decoders_hit, payload_needles

    encoded = next(c for c in CORPUS if c["mutators"]["encoding"] and c["class"] in {1, 2, 5, 7})
    ic = build_attack(encoded)
    assert payload_needles(encoded, ic.payload_text) == (ic.payload_text,)
    carrier = ic._place("some unrelated carrier text")
    assert decoders_hit(carrier, ic.payload_text) == "raw"
