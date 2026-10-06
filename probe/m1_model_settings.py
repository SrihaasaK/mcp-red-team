"""M1 settings probe (DECISIONS.md D21): thinking and temperature on Sonnet 5.5.

Sends one tiny prompt with varied settings through Anthropic's OpenAI-compatible
endpoint (the D13 path) and, for comparison, the native Messages API. Hidden
thinking shows up as completion tokens far above the visible reply. Writes
review/M1/model_settings_probe.json: statuses, usage and short replies; no headers.
The key is read from .env by the harness loader only.
"""

from __future__ import annotations

import datetime
import json
import urllib.error
import urllib.request
from pathlib import Path

from mcpredteam.config import ANTHROPIC_VERSION, anthropic_api_key, repo_root

MODEL = "claude-sonnet-5-5"
PROMPT = [{"role": "user", "content": "Reply with exactly: OK"}]
COMPAT = "https://api.anthropic.com/v1/chat/completions"
NATIVE = "https://api.anthropic.com/v1/messages"

COMPAT_CASES = {
    "C1 default": {},
    "C2 temperature=0": {"temperature": 0},
    "C3 thinking disabled": {"thinking": {"type": "disabled"}},
    "C4 thinking between_tools": {"thinking": {"type": "between_tools"}},
    "C5 temperature=0 + between_tools": {"temperature": 0, "thinking": {"type": "between_tools"}},
    "C6 effort low (output_config)": {"output_config": {"effort": "low"}},
}
REASONING = [{"role": "user", "content": "How many positive integers below 1000 are divisible by 7 or by 11 "
               "but not by 77? Reply with only the number."}]
REASONING_COMPAT = {
    "R-C1 default": {},
    "R-C4 thinking between_tools": {"thinking": {"type": "between_tools"}},
    "R-C6 effort low (output_config)": {"output_config": {"effort": "low"}},
    "R-C7 explicit adaptive": {"thinking": {"type": "adaptive"}},
    "R-C8 explicit effort high": {"output_config": {"effort": "high"}},
    "R-C9 explicit adaptive + effort high": {"thinking": {"type": "adaptive"}, "output_config": {"effort": "high"}},
}
REASONING_NATIVE = {
    "R-N1 default": {},
    "R-N4 thinking between_tools": {"thinking": {"type": "between_tools"}},
    "R-N6 effort low (output_config)": {"output_config": {"effort": "low"}},
    "R-N7 explicit adaptive": {"thinking": {"type": "adaptive"}},
    "R-N9 explicit adaptive + effort high": {"thinking": {"type": "adaptive"}, "output_config": {"effort": "high"}},
}
NATIVE_CASES = {
    "N1 default": {},
    "N2 temperature=0": {"temperature": 0},
    "N3 thinking disabled": {"thinking": {"type": "disabled"}},
}


def post(url: str, headers: dict, body: dict) -> tuple[int, dict]:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def main() -> None:
    key = anthropic_api_key()
    out = {"checked_at": datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds"),
           "model": MODEL, "prompt": PROMPT[0]["content"], "compat": {}, "native": {}}
    ch = {"authorization": f"Bearer {key}", "content-type": "application/json"}
    nh = {"x-api-key": key, "anthropic-version": ANTHROPIC_VERSION, "content-type": "application/json"}
    out["reasoning_prompt"] = REASONING[0]["content"]
    compat_runs = [(n, e, PROMPT) for n, e in COMPAT_CASES.items()] + [(n, e, REASONING) for n, e in REASONING_COMPAT.items()]
    native_runs = [(n, e, PROMPT) for n, e in NATIVE_CASES.items()] + [(n, e, REASONING) for n, e in REASONING_NATIVE.items()]
    for name, extra, prompt in compat_runs:
        status, body = post(COMPAT, ch, {"model": MODEL, "messages": prompt, "max_tokens": 4096, **extra})
        choice = (body.get("choices") or [{}])[0]
        out["compat"][name] = {
            "sent": extra, "status": status,
            "error": (body.get("error") or {}).get("message"),
            "reply": ((choice.get("message") or {}).get("content") or "")[:80],
            "finish_reason": choice.get("finish_reason"),
            "usage": body.get("usage"),
        }
    for name, extra, prompt in native_runs:
        status, body = post(NATIVE, nh, {"model": MODEL, "messages": prompt, "max_tokens": 4096, **extra})
        blocks = body.get("content") or []
        out["native"][name] = {
            "sent": extra, "status": status,
            "error": (body.get("error") or {}).get("message"),
            "block_types": [b.get("type") for b in blocks],
            "reply": " ".join(b.get("text", "") for b in blocks if b.get("type") == "text")[:80],
            "stop_reason": body.get("stop_reason"),
            "usage": body.get("usage"),
        }
    dest = repo_root() / "review" / "M1" / "model_settings_probe.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(out, indent=2) + "\n")
    for api in ("compat", "native"):
        for name, r in out[api].items():
            u = r["usage"] or {}
            toks = (f"in={u.get('prompt_tokens', u.get('input_tokens'))} "
                    f"out={u.get('completion_tokens', u.get('output_tokens'))}")
            extra = f" blocks={r['block_types']}" if api == "native" else f" finish={r['finish_reason']}"
            print(f"{name:<36} {r['status']}  {toks:<16} reply={r['reply']!r}{extra}"
                  + (f"\n{'':<38}error: {r['error'][:150]}" if r["error"] else ""))
    print(f"\nwritten: {dest.relative_to(repo_root())}")


if __name__ == "__main__":
    main()
