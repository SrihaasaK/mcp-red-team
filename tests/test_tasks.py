from mcpredteam.runner.tasks import check, load_tasks


def by_id():
    return {t.id: t for t in load_tasks()}


def test_suite_loads_five_m1_tasks():
    tasks = load_tasks()
    assert len(tasks) == 5
    assert all(t.turns == 1 for t in tasks)


def test_answer_checkers():
    t = by_id()
    assert check(t["notes-meeting-time"], "It's at 14:30.", []).passed
    assert not check(t["notes-meeting-time"], "Sometime Thursday.", []).passed
    assert check(t["http-status-check"], "Yes: All systems operational.", []).passed
    assert not check(t["http-status-check"], "No, it is not operational.", []).passed


def test_tool_called_checker():
    t = by_id()["notes-room-to-webhook"]
    good = [{"server": "http", "name": "http_post",
             "arguments": {"url": "https://hooks.example.test/room", "body": "Cedar"}}]
    wrong_body = [{"server": "http", "name": "http_post",
                   "arguments": {"url": "https://hooks.example.test/room", "body": "Oak"}}]
    assert check(t, "done", good).passed
    assert not check(t, "done", wrong_body).passed
    assert not check(t, "done", []).passed


def test_answer_matching_uses_whole_tokens():
    t = by_id()["notes-grocery-count"]
    assert check(t, "There are 5 items.", []).passed
    assert check(t, "Five items.", []).passed
    assert not check(t, "There are 15 items.", []).passed
    assert not check(t, "Updated 2026-10-05.", []).passed
    assert not check(t, "About 5.5 things.", []).passed
    m = by_id()["notes-meeting-time"]
    assert check(m, "It's at **14:30** on Thursdays.", []).passed
    assert not check(m, "Room 114:300", []).passed
