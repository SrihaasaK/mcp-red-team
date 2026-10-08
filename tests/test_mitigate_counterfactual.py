"""Offline counterfactual plumbing (M4-T1; D53) over COMMITTED recordings (CI-safe; runs/ is
gitignored). Rich coarse-vs-fine tables come from the eval script over the full run; this test
pins the reconstruction + per-kind evaluation + pinning mismatch detection on real recordings."""

from __future__ import annotations

import asyncio
from collections import Counter

from mcpredteam.config import repo_root
from mcpredteam.mitigate import pins as pinmod
from mcpredteam.mitigate.counterfactual import KINDS, evaluate, reconstruct_events

REC = repo_root() / "tests" / "recordings" / "m2-dev"
C3 = REC / "atk__c3-03-read-message-rug-hijack__opus__2"       # 3 calls, 1 sink (mail->mail)
C2 = REC / "atk__c2-01-read-note-desc-exfil__opus__0"          # class-2: poisoned read_note desc
CTL = REC / "ctl__files-atlas-owner__opus__0"                  # benign control


def test_reconstruct_events_orders_calls_and_results():
    evs = reconstruct_events(C3)
    kinds = Counter(e["kind"] for e in evs)
    assert kinds["call"] == 3 and kinds["result"] == 3 and kinds["tools_list"] >= 1
    # timestamps are non-decreasing (global order across servers)
    ts = [e["ts"] for e in evs]
    assert ts == sorted(ts)


def test_evaluate_all_kinds_wellformed():
    pins = asyncio.run(pinmod.clean_pins())
    for kind in KINDS:
        r = evaluate(C3, kind, pins)
        assert r["kind"] == kind
        assert r["sink_calls"] >= 0 and r["blocked_calls"] >= 0
        assert r["blocked_calls"] <= r["sink_calls"] or kind == "pinning"  # pinning blocks any tool


def test_coarse_and_fine_allow_same_server_flow():
    # c3-03 reads mail then sends mail: a same-server flow -> neither coarse nor fine blocks.
    for kind in ("taint-coarse", "taint-fine"):
        r = evaluate(C3, kind)
        assert r["sink_calls"] == 1 and r["blocked_calls"] == 0


def test_benign_control_has_no_coarse_blocks():
    # benign tasks use only allowlisted cross-server pairs by construction (D58).
    assert evaluate(CTL, "taint-coarse")["blocked_calls"] == 0


def test_pinning_detects_poisoned_description_on_class2():
    # the class-2 recording poisons read_note's description; pinning flags the mismatch even
    # though this trial made no call to it (blocked_calls counts only executed calls to it).
    pins = asyncio.run(pinmod.clean_pins())
    evs = reconstruct_events(C2)
    poisoned = next(e for e in evs if e["kind"] == "tools_list" and e["server"] == "notes")
    assert pinmod.mismatch(pins, "notes", "read_note", poisoned["tools"]["read_note"])
    assert not pinmod.mismatch(pins, "notes", "list_notes", poisoned["tools"]["list_notes"])
