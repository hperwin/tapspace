#!/usr/bin/env python3
"""Calls: what reaches the reviewer (GET /api/calls, the Windows app's cards) and the pushes that follow the same
decision (_announce): needs you (a card that waits on him, the lead's too; a waiting visual; a tap holding, landed or
asking) and finished (a goal card at done, a tap that shipped). A plain reply, a card that asks nothing and a step that
is not the last never call.

Runs server.py in-process on an ephemeral port with a throwaway data dir: never :4747, never the live data, never a
real push (_push is a recorder here). The agents watch is not started: each test runs _announce itself. Stdlib only.
    python3 tests/test_calls.py -v        (or: python3 -m unittest discover -s tests -v)
"""
import importlib.util
import json
import os
import pathlib
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
import uuid

sys.dont_write_bytecode = True
ROOT = pathlib.Path(__file__).resolve().parent.parent
TMP = tempfile.TemporaryDirectory(prefix="tapspace-calls-")
PNG = ("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk"
       "YPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==")
TOKEN = "test-bearer-token"
REMOTE = {"X-Forwarded-For": "203.0.113.7", "Authorization": "Bearer " + TOKEN}   # a client through a tunnel


def _load_server():
    """server.py as a fresh module whose paths point at our data dir (they are fixed at import)."""
    env = {"TAPSPACE_DATA": str(pathlib.Path(TMP.name) / "data"),
           "TAPSPACE_APNS_DIR": str(pathlib.Path(TMP.name) / "no-apns"),
           "TAPSPACE_PORT": "0"}
    saved = {k: os.environ.get(k) for k in env}
    os.environ.update(env)
    sys.path.insert(0, str(ROOT))
    try:
        spec = importlib.util.spec_from_file_location("tapspace_server_calls_under_test", ROOT / "server.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    assert mod.DATA == pathlib.Path(env["TAPSPACE_DATA"]) and mod.DATA != ROOT / "data"
    return mod


S = _load_server()
SRV = None
BASE = ""
PUSHED = []


def setUpModule():
    global SRV, BASE
    S.DATA.mkdir(parents=True)
    S.SHOTS.mkdir()
    S.INBOX.touch()
    S._save({"tickets": []})
    S.TOKEN = TOKEN
    S._push = lambda title, body, kind="replies", **ref: PUSHED.append({"title": title, "body": body, "kind": kind, **ref})
    SRV = S.ThreadingHTTPServer(("127.0.0.1", 0), S.H)
    S.PORT = SRV.server_address[1]
    assert S.PORT != 4747
    BASE = f"http://127.0.0.1:{S.PORT}"
    threading.Thread(target=SRV.serve_forever, daemon=True).start()


def tearDownModule():
    SRV.shutdown()
    SRV.server_close()
    TMP.cleanup()


def call(path, body=None, headers=None):
    data = None if body is None else json.dumps(body).encode()
    h = {**({"content-type": "application/json"} if data is not None else {}), **(headers or {})}
    try:
        with urllib.request.urlopen(urllib.request.Request(BASE + path, data=data, headers=h), timeout=10) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        with e:
            raw = e.read()
        try:
            return e.code, json.loads(raw or b"null")
        except ValueError:
            return e.code, None


def name(tag):
    return f"Claude · calls · {tag} · {uuid.uuid4().hex[:4]}"


def join(who):
    assert call("/api/chat/listen?" + urllib.parse.urlencode({"agent": who, "where": "~/scratch"}))[0] == 200


def card(who, c, images=0):
    code, r = call("/api/chat/reply", {"agent": who, "card": c,
                                       "images": [{"b64": PNG, "ext": "png"} for _ in range(images)]})
    assert code == 200, r
    return r["message"]


def reply(who, body):
    code, r = call("/api/chat/reply", {"agent": who, "body": body})
    assert code == 200, r
    return r["message"]


def answer(who, a):
    code, r = call("/api/chat", {"agent": who, "nonce": uuid.uuid4().hex, "answer": a})
    assert code == 200, r
    return r["message"]


def goal(who, steps="Audit|Build|PR open"):
    assert call("/api/agents/progress", {"name": who, "goal": "Ship the thing", "steps": steps})[0] == 200


def step(who, to="next"):
    assert call("/api/agents/progress", {"name": who, "step": to})[0] == 200


def calls(headers=None):
    code, r = call("/api/calls", headers=headers)
    assert code == 200, r
    return r["calls"]


def mine(who):
    return [c for c in calls() if c.get("key") == "agent:" + who]


def tick():
    """One pass of the agents watch's announce, as it runs every second."""
    chat = S._chat_rows()
    with S._lock:
        tickets = S._load()["tickets"]
    S._announce(S._calls(S._agent_rows(chat), S.TAPS["items"], chat, tickets, S._owner_index()), bool(S.TAPS["fetched"]))


def tap(key, phase, last="2026-10-24T19:00:00Z", status=None, ask=None, outcome=None):
    """A TAPS item as _item serves it."""
    t = {"key": key, "repo": key.split("#")[0], "number": int(key.split("#")[1]), "title": "A tap — for calls",
         "url": "https://github.com/" + key.replace("#", "/issues/"), "phase": phase, "phase_word": S.PHASE_WORD[phase],
         "status": status or {"done": "landed", "holding": "needs-you", "proposed": "boarding"}.get(phase, "flying"),
         "last": last, "ask": ask, "goals": [], "messages": [], "proof": []}
    if outcome:
        t["outcome"] = outcome
    return t


ASK = {"line": "Pick the stack", "ask": {"q": "Which stack?", "checks": ["Electron", "WPF"]}}


class Decision(unittest.TestCase):
    """_calls through the routes: what calls and what never does."""

    def setUp(self):
        S.TAPS.update(items=[], fetched=0.0)
        self.who = name("d")
        join(self.who)

    def test_plain_reply_and_a_card_that_asks_nothing_never_call(self):
        reply(self.who, "Working on it, 3 of 5 done")
        card(self.who, {"line": "Halfway there", "visual": {"kind": "progress", "done": 3, "total": 5}})
        self.assertEqual(mine(self.who), [])

    def test_a_question_calls_until_it_is_answered(self):
        row = card(self.who, ASK)
        [c] = mine(self.who)
        self.assertEqual((c["id"], c["kind"], c["from"], c["card"], c["line"]),
                         (f"card:{row['id']}", "needs_you", "master", row["id"], "Pick the stack"))
        self.assertEqual(c["ask"], {"q": "Which stack?", "text": False,
                                    "checks": [{"id": "c1", "label": "Electron"}, {"id": "c2", "label": "WPF"}]})
        self.assertEqual(c["images"], [])
        answer(self.who, {"card": row["id"], "checks": ["c2"]})
        self.assertEqual(mine(self.who), [])

    def test_only_the_newest_card_calls(self):
        card(self.who, ASK)
        row = card(self.who, {"line": "Never mind, picked Electron"})
        self.assertEqual(mine(self.who), [])
        row2 = card(self.who, dict(ASK, line="One more"))
        self.assertEqual([c["id"] for c in mine(self.who)], [f"card:{row2['id']}"])
        self.assertNotEqual(row["id"], row2["id"])

    def test_images_call_until_each_has_a_verdict(self):
        row = card(self.who, {"line": "Two looks", "images": [{"label": "Light"}, {}]}, images=2)
        [c] = mine(self.who)
        self.assertEqual([(i["i"], i["label"]) for i in c["images"]], [(0, "Light"), (1, "Image 2")])
        self.assertTrue(c["images"][0]["url"].startswith("/chat-media/"))
        self.assertNotIn("ask", c)
        answer(self.who, {"card": row["id"], "image": 0, "verdict": "approve"})
        self.assertEqual([i["i"] for i in mine(self.who)[0]["images"]], [1])
        answer(self.who, {"card": row["id"], "image": 1, "verdict": "change"})
        self.assertEqual(mine(self.who), [])

    def test_the_lead_calls_too(self):
        S._set_lead(self.who)
        try:
            row = card(self.who, ASK)
            self.assertEqual([c["id"] for c in mine(self.who)], [f"card:{row['id']}"])
            self.assertNotIn("agent:" + self.who, [f["key"] for f in call("/api/agents")[1]["fleet"]])  # unlike the fleet
        finally:
            with S._lock:
                a = S._agents()
                a[self.who].pop("role", None)
                S._agents_write(a)

    def test_only_the_last_step_finishes(self):
        goal(self.who)
        step(self.who)
        step(self.who)
        self.assertEqual(mine(self.who), [])
        step(self.who)
        [c] = mine(self.who)
        self.assertEqual((c["kind"], c["line"], c["steps"]), ("finished", "Ship the thing", ["Audit", "Build", "PR open"]))
        self.assertTrue(c["id"].startswith(f"done:{self.who}:"))
        step(self.who, "done")                     # a note or a repeat keeps the same id
        self.assertEqual(mine(self.who)[0]["id"], c["id"])

    def test_a_finished_goal_stops_calling_after_a_while(self):
        goal(self.who, "One")
        step(self.who, "done")
        self.assertEqual(len(mine(self.who)), 1)
        with S._lock:
            a = S._agents()
            a[self.who]["progress"]["updated"] -= S.FINISHED_KEEP + 1
            S._agents_write(a)
        self.assertEqual(mine(self.who), [])

    def test_a_visual_calls_until_decided(self):
        code, r = call("/api/post", {"agent": "T1-lane", "title": "Card slide", "image_b64": PNG, "owner": self.who})
        self.assertEqual(code, 200, r)
        tid = r["id"]
        [c] = [c for c in calls() if c.get("ticket") == tid]
        self.assertEqual((c["id"], c["from"], c["key"], c["line"], c["url"], c["media"]),
                         (f"visual:{tid}:1", "visual", "agent:" + self.who, "Card slide", f"/shots/{tid}/1.png", "image"))
        call("/api/update", {"id": tid, "image_b64": PNG})
        self.assertEqual([c["id"] for c in calls() if c.get("ticket") == tid], [f"visual:{tid}:2"])
        call("/api/decide", {"id": tid, "action": "approve"})
        self.assertEqual([c for c in calls() if c.get("ticket") == tid], [])

    def test_a_visual_and_its_picture_are_served_and_nothing_outside(self):
        tid = call("/api/post", {"agent": "lane", "title": "Pic", "image_b64": PNG})[1]["id"]
        url = [c for c in calls() if c.get("ticket") == tid][0]["url"]
        with urllib.request.urlopen(BASE + url, timeout=10) as r:
            self.assertEqual((r.status, r.headers["content-type"]), (200, "image/png"))
        for bad in ("/shots/../tickets.json", f"/shots/{tid}/../../token", "/chat-media/../token"):
            self.assertEqual(call(bad)[0], 404, bad)
        call("/api/decide", {"id": tid, "action": "approve"})

    def test_served_through_a_tunnel_with_the_token_only(self):
        self.assertEqual(call("/api/calls", headers={"X-Forwarded-For": "203.0.113.7"})[0], 401)
        code, r = call("/api/calls", headers=REMOTE)
        self.assertEqual(code, 200)
        self.assertEqual(set(r), {"calls", "taps", "time"})


class Taps(unittest.TestCase):
    """Taps call while they wait on him (holding, landed, a pilot's question) and finish when they ship."""

    def test_tap_phases(self):
        q = {"text": "Ship it? (Yes, No)", "url": "https://github.com/a/app/issues/1", "at": "2026-10-24T19:30:00Z"}
        taps = [tap("a/app#1", "holding", ask=q), tap("a/app#2", "landed"), tap("a/app#3", "enroute"),
                tap("a/app#4", "enroute", status="needs-you", ask=dict(q, at="2026-10-24T19:40:00Z")),
                tap("a/app#5", "done", outcome="shipped"), tap("a/app#6", "done", outcome="aborted"),
                tap("a/app#7", "proposed")]
        got = {c["key"]: c for c in S._calls([], taps, [], [], S._owner_index())}
        self.assertEqual(set(got), {"a/app#1", "a/app#2", "a/app#4", "a/app#5"})
        self.assertEqual((got["a/app#1"]["id"], got["a/app#1"]["kind"], got["a/app#1"]["line"], got["a/app#1"]["board"]),
                         ("tap:a/app#1:ask:2026-10-24T19:30:00Z", "needs_you", "Ship it? (Yes, No)", True))
        self.assertEqual(got["a/app#2"]["accept"], {"tap": "a/app#2", "title": "A tap, for calls"})
        self.assertFalse(got["a/app#4"]["board"])
        self.assertEqual((got["a/app#5"]["kind"], got["a/app#5"]["id"]), ("finished", "done:a/app#5"))

    def test_newest_first(self):
        taps = [tap("a/app#1", "landed", last="2026-10-24T18:00:00Z"), tap("a/app#2", "landed", last="2026-10-24T19:00:00Z")]
        self.assertEqual([c["key"] for c in S._calls([], taps, [], [], S._owner_index())], ["a/app#2", "a/app#1"])


class Announce(unittest.TestCase):
    """_announce: each call pushes once, in its class; a boot and the first taps load never push old calls."""

    def setUp(self):
        S._ANNOUNCED.update(ids=None, taps=False)
        S._CALL_NEW.clear()
        S.TAPS.update(items=[], fetched=0.0)
        PUSHED.clear()
        self.who = name("a")
        join(self.who)
        tick()                                      # the boot pass: learns, pushes nothing
        self.assertEqual(PUSHED, [])

    def test_a_question_pushes_once_as_needs_you_and_a_reply_as_replies(self):
        reply(self.who, "Plain words")
        self.assertEqual([p["kind"] for p in PUSHED], ["replies"])
        PUSHED.clear()
        cur = call("/api/wait?since=0&timeout=0")[1]["cursor"]
        row = card(self.who, ASK)
        self.assertEqual(PUSHED, [])                # the card's own push is its call, not a reply
        tick()
        tick()
        title = S._title(S._agents()[self.who])
        self.assertEqual(PUSHED, [{"title": title, "body": "Which stack?", "kind": "needs_you", "key": "agent:" + self.who}])
        [c] = mine(self.who)
        self.assertAlmostEqual(c["new"], time.time(), delta=5)
        self.assertIn("calls", call(f"/api/wait?since={cur}&timeout=0")[1]["changed"])
        answer(self.who, {"card": row["id"], "checks": ["c1"]})
        tick()
        self.assertEqual(len(PUSHED), 1)

    def test_a_card_that_asks_nothing_pushes_as_a_reply(self):
        card(self.who, {"line": "Halfway"})
        tick()
        self.assertEqual([(p["kind"], p["body"]) for p in PUSHED], [("replies", "Halfway")])

    def test_the_last_step_pushes_finished(self):
        goal(self.who, "One|Two")
        step(self.who)
        tick()
        self.assertEqual(PUSHED, [])
        step(self.who)
        tick()
        self.assertEqual([(p["kind"], p["body"]) for p in PUSHED], [("finished", "Done: Ship the thing")])

    def test_a_visual_pushes_as_needs_you_with_its_id(self):
        tid = call("/api/post", {"agent": "lane", "title": "Tray badge", "image_b64": PNG})[1]["id"]
        self.assertEqual(PUSHED, [])
        tick()
        self.assertEqual(PUSHED, [{"title": "New visual", "body": "Tray badge", "kind": "needs_you", "id": tid}])
        call("/api/decide", {"id": tid, "action": "approve"})

    def test_calls_there_at_boot_are_not_new(self):
        S._ANNOUNCED.update(ids=None, taps=False)
        card(self.who, ASK)
        tick()
        self.assertEqual(PUSHED, [])
        self.assertIsNone(mine(self.who)[0]["new"])

    def test_the_first_taps_load_learns_its_calls_without_a_push(self):
        S.TAPS.update(items=[tap("a/app#2", "landed")], fetched=time.time())
        card(self.who, ASK)                         # a card in the same pass still pushes
        tick()
        self.assertEqual([p["kind"] for p in PUSHED], ["needs_you"])
        self.assertEqual(PUSHED[0]["key"], "agent:" + self.who)
        S.TAPS["items"] = [tap("a/app#2", "landed"), tap("a/app#3", "landed")]
        tick()
        self.assertEqual([p.get("key") for p in PUSHED[1:]], ["a/app#3"])

    def test_an_old_pilot_question_never_pushes(self):
        S.TAPS.update(items=[], fetched=time.time())
        tick()
        old = {"text": "Which way?", "url": "u", "at": "2026-10-01T00:00:00Z"}
        new = {"text": "Which way?", "url": "u", "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        S.TAPS["items"] = [tap("a/app#8", "enroute", status="needs-you", ask=old),
                           tap("a/app#9", "enroute", status="needs-you", ask=new)]
        tick()
        self.assertEqual([p["key"] for p in PUSHED], ["a/app#9"])


if __name__ == "__main__":
    unittest.main()
