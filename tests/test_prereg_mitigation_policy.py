"""PREREG Amendment A6 (D84) is the single source of truth for the frozen mitigation policy:
re-derive every value A6 pins from the CODE/artifacts and assert equality, and re-check the
EXISTING frozen-suite pins against what PREREG states. A drift fails loudly (CLAUDE.md #4).

Additive (new file; no existing test changed). If a CURRENT artifact hash no longer matches
PREREG, that is a reportable finding — the test FAILS rather than being adjusted.
"""
from __future__ import annotations

import hashlib
import re

import yaml

from mcpredteam.config import repo_root
from mcpredteam.mitigate import taint as taintmod
from mcpredteam.mitigate.allowlist import D58_SNAPSHOT, derive_allowlist
from mcpredteam.proxy.recording import canonical

ROOT = repo_root()
PREREG = (ROOT / "PREREG.md").read_text()
HEAD = PREREG.split("## Amendment")[0]  # the pre-amendment head holds the "Frozen suite" table
HEX = r"([0-9a-f]{64})"

ALLOWLIST_PAIRS = {("files", "mail"), ("http", "files"), ("mail", "files"),
                   ("mail", "http"), ("notes", "http"), ("notes", "mail")}
POLICY_FILES = ["allowlist", "taint", "patterns", "pins", "interceptors", "block", "store"]


def _sha_file(rel: str) -> str:
    return hashlib.sha256((ROOT / rel).read_bytes()).hexdigest()


def _hex_after(label: str, text: str = PREREG) -> str:
    """The first 64-hex that follows `label` on the same line of `text`."""
    m = re.search(re.escape(label) + r"[^\n]*?`" + HEX + r"`", text)
    assert m, f"no hash found after {label!r}"
    return m.group(1)


# (i) A6's policy-file hash table (parsed out of PREREG) must equal the current files.
def test_a6_policy_hashes_match_code():
    table = dict(re.findall(r"`(src/mcpredteam/mitigate/[\w./]+\.py)`\s*\|\s*`" + HEX + r"`", PREREG))
    expected = {f"src/mcpredteam/mitigate/{f}.py" for f in POLICY_FILES}
    assert set(table) == expected, (sorted(table), sorted(expected))
    for path, want in table.items():
        assert _sha_file(path) == want, (path, _sha_file(path), want)


# (ii) taint k/unit/segmentation + the coarse allowlist, as A6 states.
def test_a6_fine_taint_k_unit_segmentation():
    assert taintmod.K == 8
    assert taintmod.SEP == "\x00"
    # default unit is CHARACTER k-grams over lower-cased text (k=8)
    assert taintmod.k_grams("ABCDEFGHIJ") == {"abcdefgh", "bcdefghi", "cdefghij"}
    # segmentation: a k-gram may NOT straddle the SEP join, but within one segment it must match
    assert not taintmod.shares_kgram("aaaaaaaa\x00bbbbbbbb", "aaaabbbb")
    assert taintmod.shares_kgram("aaaaaaaa\x00bbbbbbbb", "aaaaaaaa")


def test_a6_coarse_allowlist_six_pairs():
    assert set(D58_SNAPSHOT) == ALLOWLIST_PAIRS
    assert set(derive_allowlist()) == ALLOWLIST_PAIRS


# (iii) the EXISTING frozen-suite pins still match PREREG (corpus at its A2 re-pin). A mismatch
# is a finding: the test fails, it is not adjusted.
def test_existing_frozen_suite_pins_still_match_prereg():
    assert _sha_file("tasks/benign.yaml") == _hex_after("`tasks/benign.yaml`", HEAD)
    assert _sha_file("attacks/split.yaml") == _hex_after("`attacks/split.yaml`", HEAD)
    assert _sha_file("src/mcpredteam/attacks/mutators.py") == \
        _hex_after("`src/mcpredteam/attacks/mutators.py`", HEAD)
    # corpus.yaml: the authoritative hash is the A2 re-pin ("... -> NEW `<hash>`", the hash on the
    # next line), which supersedes the Frozen-suite table row.
    a2_new = re.search(r"NEW\s*`" + HEX + r"`", PREREG)
    assert a2_new, "A2 corpus re-pin (NEW) not found"
    assert _sha_file("attacks/corpus.yaml") == a2_new.group(1)
    # split mapping = SHA-256 of the canonical id->arm JSON.
    split = yaml.safe_load((ROOT / "attacks" / "split.yaml").read_text())["split"]
    got = hashlib.sha256(canonical(split).encode("utf-8")).hexdigest()
    assert got == _hex_after("split mapping", HEAD)
