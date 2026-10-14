"""The committed corpus stays at its PREREG-pinned hash (Amendment A2; D107 source-of-truth guard).

`attacks/corpus.yaml` is the hand-maintained source of truth since the D61/D65 re-pin; the generator
in `legacy/gen_corpus.py` produces only the PRE-A2 version. This test is the backstop that the frozen
corpus has not drifted from the PREREG Amendment A2 pin.
"""
import hashlib

from mcpredteam.config import repo_root

# PREREG.md Amendment A2 (D61/D65 re-pin): attacks/corpus.yaml SHA-256.
PINNED_A2 = "60d7dabeb300af4f7b78b46d36f474f04d5de4627e6b8a96329390b26a3a116d"


def test_corpus_matches_prereg_a2_pin():
    root = repo_root()
    digest = hashlib.sha256((root / "attacks" / "corpus.yaml").read_bytes()).hexdigest()
    assert digest == PINNED_A2, f"attacks/corpus.yaml drifted from the A2 pin: {digest}"
    # the same hash is recorded in PREREG (Amendment A2) -- keep test and prereg in sync.
    assert PINNED_A2 in (root / "PREREG.md").read_text()
