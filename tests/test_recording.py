import pytest

from mcpredteam.proxy.recording import ReplayDivergence, UpstreamRecorder, UpstreamReplayer


def call(i, name, args, meta=None):
    params = {"name": name, "arguments": args}
    if meta:
        params["_meta"] = meta
    return {"jsonrpc": "2.0", "id": i, "method": "tools/call", "params": params}


def reply(i, text):
    return {"jsonrpc": "2.0", "id": i, "result": {"content": [{"type": "text", "text": text}]}}


def test_replay_matches_by_method_and_params_and_rewrites_ids(tmp_path):
    path = tmp_path / "upstream.notes.jsonl"
    rec = UpstreamRecorder(path)
    rec.record(call(1, "read_note", {"note_id": "n1"}, meta={"v": "a"}), [reply(1, "first")])
    rec.record(call(2, "read_note", {"note_id": "n1"}), [reply(2, "second")])
    rep = UpstreamReplayer(path)
    # different request id and _meta still match, in recorded order
    status, out = rep.respond(call(77, "read_note", {"note_id": "n1"}, meta={"v": "zzz"}))
    assert status == 200
    assert out[0]["id"] == 77 and out[0]["result"]["content"][0]["text"] == "first"
    assert rep.respond(call(78, "read_note", {"note_id": "n1"}))[1][0]["result"]["content"][0]["text"] == "second"
    assert rep.unused() == []


def test_replay_diverges_loudly_on_unrecorded_args(tmp_path):
    path = tmp_path / "upstream.notes.jsonl"
    UpstreamRecorder(path).record(call(1, "read_note", {"note_id": "n1"}), [reply(1, "x")])
    with pytest.raises(ReplayDivergence):
        UpstreamReplayer(path).respond(call(2, "read_note", {"note_id": "n2"}))
