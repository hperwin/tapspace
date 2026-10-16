#!/usr/bin/env python3
"""Agents API: card owners, goal cards, change notes with a shared read mark, and the links seam.

Runs server.py in-process on an ephemeral port with a throwaway data dir: never :4747, never the
live data, never a real push. Stdlib only.
    python3 tests/test_agents_api.py -v        (or: python3 -m unittest discover -s tests -v)
"""
import base64
import importlib.util
import io
import json
import os
import pathlib
import re
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
TMP = tempfile.TemporaryDirectory(prefix="tapspace-agents-api-")
PNG = ("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk"
       "YPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==")
TOKEN = "test-bearer-token"
REMOTE = {"X-Forwarded-For": "203.0.113.7", "Authorization": "Bearer " + TOKEN}   # the phone through a tunnel


def _load_server():
    """server.py as a fresh module whose paths point at our data dir (they are fixed at import)."""
    env = {"TAPSPACE_DATA": str(pathlib.Path(TMP.name) / "data"),
           "TAPSPACE_APNS_DIR": str(pathlib.Path(TMP.name) / "no-apns"),    # no key: a post never pushes
           "TAPSPACE_PORT": "0"}
    saved = {k: os.environ.get(k) for k in env}
    os.environ.update(env)
    sys.path.insert(0, str(ROOT))                 # server.py imports qr and links from its folder
    try:
        spec = importlib.util.spec_from_file_location("tapspace_server_under_test", ROOT / "server.py")
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


def setUpModule():
    global SRV, BASE
    S.DATA.mkdir(parents=True)
    S.SHOTS.mkdir()
    S.INBOX.touch()
    S._save({"tickets": []})
    S.TOKEN = TOKEN
    SRV = S.ThreadingHTTPServer(("127.0.0.1", 0), S.H)
    S.PORT = SRV.server_address[1]
    assert S.PORT != 4747
    BASE = f"http://127.0.0.1:{S.PORT}"
    threading.Thread(target=SRV.serve_forever, daemon=True).start()
    threading.Thread(target=S._agents_watch, daemon=True).start()


def tearDownModule():
    SRV.shutdown()
    SRV.server_close()
    TMP.cleanup()


def call(path, body=None, headers=None, ctype="application/json"):
    """(status, json) for a GET (body None) or a POST."""
    data = None if body is None else json.dumps(body).encode()
    h = {**({"content-type": ctype} if data is not None else {}), **(headers or {})}
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


def sid():
    return str(uuid.uuid4())


def name(tag):
    return f"Claude · test · {tag} · {uuid.uuid4().hex[:4]}"


def ident(session=None, pid=None):
    return {**({"X-Claude-Session": session} if session else {}),
            **({"X-Claude-Pid": str(pid)} if pid else {})}


def join(who, session=None, pid=None, headers=None):
    q = urllib.parse.urlencode({"agent": who, "where": "~/scratch"})
    return call("/api/chat/listen?" + q, headers={**ident(session, pid), **(headers or {})})


def post(label=None, campaign="", session=None, pid=None, owner=None, video=False, headers=None):
    body = {"agent": label or "L9-" + uuid.uuid4().hex[:6], "title": "A card", "campaign": campaign}
    body.update({"video_b64": PNG, "video_ext": "mp4"} if video else {"image_b64": PNG})
    if owner:
        body["owner"] = owner
    code, r = call("/api/post", body, headers={**ident(session, pid), **(headers or {})})
    assert code == 200, r
    return r["id"]


def served(tid):
    return call("/api/ticket/" + tid)[1]


def stored(tid):
    return next(t for t in json.loads(S.STATE.read_text())["tickets"] if t["id"] == tid)


def row(who):
    return next(r for r in call("/api/agents")[1]["agents"] if r["name"] == who)


def decide(tid, action="change", message="Tighten the header spacing"):
    return call("/api/decide", {"id": tid, "action": action, "message": message})


def progress(who, **body):
    return call("/api/agents/progress", {"name": who, **body})


def quiet(who, secs):
    """who last listened secs ago (busy answering, or gone)."""
    with S._lock:
        a = S._agents()
        a[who]["listen"] = time.time() - secs
        S._agents_write(a)


class ProgressChangeTests(unittest.TestCase):
    """The pure rule behind POST /api/agents/progress."""

    def base(self):
        return S._progress_change(None, {"goal": "Ship the thing", "steps": ["Audit", "Spec", "Build"]})

    def test_first_publish_starts_at_the_first_step(self):
        p = self.base()
        self.assertEqual(list(p), ["goal", "steps", "current", "note", "updated"])
        self.assertEqual((p["current"], p["note"]), (0, None))

    def test_moves(self):
        p = self.base()
        self.assertEqual(S._progress_change(p, {"step": "next"})["current"], 1)
        self.assertEqual(S._progress_change(p, {"step": "build"})["current"], 2)
        self.assertEqual(S._progress_change(p, {"step": 3})["current"], 2)        # numbers count from 1
        self.assertEqual(S._progress_change(p, {"step": "2"})["current"], 1)
        self.assertEqual(S._progress_change(p, {"step": "done"})["current"], 3)   # done == len(steps)
        self.assertEqual(S._progress_change(p, {"current": 3})["current"], 3)

    def test_note_clears_on_a_move_unless_set(self):
        p = S._progress_change(self.base(), {"note": "2 of 9 lanes"})
        self.assertEqual(p["note"], "2 of 9 lanes")
        self.assertIsNone(S._progress_change(p, {"step": "next"})["note"])
        self.assertEqual(S._progress_change(p, {"step": "next", "note": "on it"})["note"], "on it")
        self.assertEqual(S._progress_change(p, {"goal": "New words"})["note"], "2 of 9 lanes")

    def test_new_steps_start_over(self):
        p = S._progress_change(self.base(), {"step": "done"})
        self.assertEqual(S._progress_change(p, {"steps": "Fix|Check"})["current"], 0)

    def test_refusals(self):
        for old, body in [(None, {"goal": "Only a goal"}),
                          (None, {"steps": ["a"]}),
                          (None, {"goal": "G", "steps": [str(i) for i in range(13)]}),
                          (None, {"goal": "G", "steps": ["a", "A"]}),
                          (None, {"goal": "Fast \u2014 and loose", "steps": ["a"]}),
                          (self.base(), {"current": 4}),
                          (self.base(), {"current": True}),
                          (self.base(), {"step": "nowhere"}),
                          (self.base(), {"note": "x" * 81})]:
            with self.subTest(body=body), self.assertRaises(ValueError):
                S._progress_change(old, body)

    def test_clear(self):
        self.assertIsNone(S._progress_change(self.base(), {"clear": True}))


class ProgressApiTests(unittest.TestCase):

    def test_publish_and_serve(self):
        a = name("plan")
        join(a)
        code, r = progress(a, goal="Glitch-free texting", steps=["Audit", "Spec", "Build"])
        self.assertEqual((code, r["ok"]), (200, True))
        got = row(a)["progress"]
        self.assertEqual((got["goal"], got["steps"], got["current"], got["note"]),
                         ("Glitch-free texting", ["Audit", "Spec", "Build"], 0, None))
        self.assertIsInstance(got["updated"], float)
        self.assertEqual(progress(a, step="next", note="3 of 7 specs")[1]["progress"]["current"], 1)
        self.assertEqual(row(a)["progress"]["note"], "3 of 7 specs")

    def test_agent_key_is_accepted_too(self):
        a = name("alias")
        join(a)
        self.assertEqual(call("/api/agents/progress", {"agent": a, "goal": "G", "steps": ["x"]})[0], 200)

    def test_unknown_agent_and_first_publish_rules(self):
        self.assertEqual(progress("Claude · nobody here", goal="G", steps=["x"])[0], 404)
        a = name("first")
        join(a)
        code, r = progress(a, goal="G")
        self.assertEqual(code, 400)
        self.assertIn("goal and steps", r["error"])

    def test_local_json_same_origin_only(self):
        a = name("guard")
        join(a)
        body = {"name": a, "goal": "G", "steps": ["x"]}
        self.assertEqual(call("/api/agents/progress", body, headers=REMOTE)[0], 403)
        self.assertEqual(call("/api/agents/progress", body, ctype="text/plain")[0], 403)
        self.assertEqual(call("/api/agents/progress", body, headers={"Origin": "https://example.com"})[0], 403)
        self.assertIsNone(row(a)["progress"])

    def test_relay_and_new_car_keep_it(self):
        a = name("relay")
        join(a, session=sid())
        progress(a, goal="G", steps=["x", "y"], current=1)
        call("/api/agents/relay", {"agent": a})
        join(a, session=sid())
        self.assertEqual(row(a)["progress"]["current"], 1)

    def test_clear(self):
        a = name("clear")
        join(a)
        progress(a, goal="G", steps=["x"])
        self.assertIsNone(progress(a, clear=True)[1]["progress"])
        self.assertIsNone(row(a)["progress"])


class OwnerTests(unittest.TestCase):

    def test_session_owner_and_the_session_is_never_served(self):
        a, s = name("own"), sid()
        join(a, session=s)
        tid = post(session=s)
        t = served(tid)
        self.assertEqual((t["owner"], t["owner_how"]), (a, "session"))
        self.assertNotIn("session", t)
        self.assertNotIn("session", next(x for x in call("/api/state")[1]["tickets"] if x["id"] == tid))
        self.assertEqual(stored(tid)["session"], s)

    def test_relay_cars_share_one_owner(self):
        a, s1, s2 = name("train"), sid(), sid()
        join(a, session=s1)
        call("/api/agents/relay", {"agent": a}, headers=ident(s1))
        join(a, session=s2)
        self.assertEqual(served(post(session=s1))["owner"], a)     # a lane still running in the old car
        self.assertEqual(served(post(session=s2))["owner"], a)

    def test_another_name_in_the_session_never_takes_it_while_its_name_listens(self):
        a, b, s = name("held"), name("lane"), sid()     # b: an agent inside a's Claude process
        join(a, session=s)
        join(b, session=s)
        call("/api/chat/reply", {"agent": b, "body": "lane check"}, headers=ident(s))
        call("/api/agents/title", {"agent": b, "title": "Lane check"}, headers=ident(s))
        self.assertEqual(served(post(session=s))["owner"], a)
        quiet(a, S.CLAIM_MOVE + 5)                        # a stops listening (answering, or gone)
        call("/api/chat/reply", {"agent": b, "body": "lane check"}, headers=ident(s))
        call("/api/agents/title", {"agent": b, "title": "Lane check"}, headers=ident(s))
        self.assertEqual(served(post(session=s))["owner"], a)     # reply and title never move a claim
        join(b, session=s)
        self.assertEqual(served(post(session=s))["owner"], b)     # a listen does, once a is quiet
        join(a, session=s)
        self.assertEqual(served(post(session=s))["owner"], b)     # and b now holds it while it listens
        self.assertEqual(S._claims()["sessions"][s]["how"], "listen")

    def test_relay_moves_a_claim(self):
        a, b, s = name("car"), name("next"), sid()
        join(a, session=s)
        join(b)
        call("/api/agents/relay", {"agent": b}, headers=ident(s))
        self.assertEqual(served(post(session=s))["owner"], b)

    def test_reply_or_title_claims_an_unclaimed_session(self):
        a, b, s1, s2 = name("reply"), name("title"), sid(), sid()
        join(a)
        join(b)
        call("/api/chat/reply", {"agent": a, "body": "back"}, headers=ident(s1))
        call("/api/agents/title", {"agent": b, "title": "Title claim"}, headers=ident(s2))
        self.assertEqual((served(post(session=s1))["owner"], served(post(session=s2))["owner"]), (a, b))

    def test_claimed_later_resolves_on_read_without_a_write(self):
        c, s = name("late"), sid()
        tid = post(session=s)
        self.assertIsNone(served(tid)["owner"])
        join(c, session=s)
        self.assertEqual((served(tid)["owner"], served(tid)["owner_how"]), (c, "session"))
        self.assertIsNone(stored(tid)["owner"])

    def test_same_process_after_a_new_session_id(self):
        a = name("pid")
        join(a, session=sid(), pid=os.getpid())
        t = served(post(session=sid(), pid=os.getpid()))
        self.assertEqual((t["owner"], t["owner_how"]), (a, "pid"))
        with S._lock:                      # a reused pid: whoever holds it now started at another time
            c = S._claims()
            for v in c["sessions"].values():
                if v.get("pid") == os.getpid():
                    v["pid_start"] = "Mon Jan  1 00:00:00 2001"
            S._claims_write(c)
        self.assertIsNone(served(post(session=sid(), pid=os.getpid()))["owner"])

    def test_explicit_owner(self):
        a = name("explicit")
        join(a)
        call("/api/agents/title", {"agent": a, "title": "Explicit owner test"})
        self.assertEqual(served(post(owner=a))["owner_how"], "explicit")
        self.assertEqual(served(post(owner="explicit OWNER test"))["owner"], a)
        self.assertIsNone(served(post(owner="Nobody by this name"))["owner"])

    def test_campaign_learned_from_a_proven_post(self):
        a, s = name("camp"), sid()
        join(a, session=s)
        camp = "camp-" + uuid.uuid4().hex[:6]
        post(campaign=camp, session=s)
        tid = post(campaign=camp)                         # a lane outside the master's process
        self.assertEqual((served(tid)["owner"], served(tid)["owner_how"]), (a, "campaign"))
        self.assertIsNone(stored(tid)["owner"])
        b, s2 = name("camp2"), sid()
        join(b, session=s2)
        post(campaign=camp, session=s2)                   # two masters in one campaign: no guessing
        self.assertIsNone(served(tid)["owner"])

    def test_exact_title_label(self):
        a = name("label")
        join(a)
        call("/api/agents/title", {"agent": a, "title": "Label match test"})
        t = served(post(label="Label match test"))
        self.assertEqual((t["owner"], t["owner_how"]), (a, "title"))

    def test_update_fills_and_explicit_corrects(self):
        a, b, s = name("upd"), name("upd2"), sid()
        join(a, session=s)
        join(b)
        tid = post()
        self.assertIsNone(stored(tid)["owner"])
        call("/api/update", {"id": tid, "image_b64": PNG}, headers=ident(s))
        self.assertEqual((stored(tid)["owner"], stored(tid)["owner_how"]), (a, "session"))
        call("/api/update", {"id": tid, "image_b64": PNG, "owner": b})
        self.assertEqual((stored(tid)["owner"], stored(tid)["owner_how"]), (b, "explicit"))

    def test_identity_headers_from_a_remote_caller_are_ignored(self):
        a, s = name("remote"), sid()
        join(a, session=s)
        tid = post(session=s, headers=REMOTE)
        self.assertEqual((stored(tid)["owner"], stored(tid)["session"]), (None, None))
        s2 = sid()
        join(a, session=s2, headers=REMOTE)
        self.assertNotIn(s2, S._claims()["sessions"])

    def test_decide_pins_the_owner_and_the_inbox_names_it(self):
        a = name("pin")
        join(a)
        call("/api/agents/title", {"agent": a, "title": "Pin owner test"})
        tid = post(label="Pin owner test")
        self.assertIsNone(stored(tid)["owner"])
        decide(tid)
        self.assertEqual((stored(tid)["owner"], stored(tid)["owner_how"]), (a, "title"))
        last = json.loads(S.INBOX.read_text().splitlines()[-1])
        self.assertEqual((last["ticket"], last["owner"]), (tid, a))


class ClearAllTests(unittest.TestCase):

    def test_a_master_whose_claude_still_runs_keeps_its_row(self):
        busy, ended, idle = name("busy"), name("ended"), name("idle")
        join(busy, session=sid(), pid=os.getpid())        # this process stands in for its claude
        join(ended, session=sid(), pid=os.getpid())
        join(idle)
        with S._lock:                                     # ended's claude exited; its pid is someone else's now
            c = S._claims()
            for v in c["sessions"].values():
                if v["name"] == ended:
                    v["pid_start"] = "Mon Jan  1 00:00:00 2001"
            S._claims_write(c)
        progress(busy, goal="Ship the rail", steps=["Build", "Review"])
        call("/api/agents/read", {"name": busy})
        for who in (busy, ended, idle):
            quiet(who, 120)                               # none listened in the last 30 s
        self.assertEqual(call("/api/chat/clear", {})[0], 200)
        kept = S._agents()
        self.assertEqual((kept[busy]["progress"]["goal"], bool(kept[busy]["notes_read"])), ("Ship the rail", True))
        self.assertNotIn(ended, kept)
        self.assertNotIn(idle, kept)
        self.assertEqual(row(busy)["progress"]["steps"], ["Build", "Review"])


class NotesTests(unittest.TestCase):

    def owned(self):
        a, s = name("notes"), sid()
        join(a, session=s)
        return a, s

    def test_lifecycle(self):
        a, s = self.owned()
        tid = post(session=s)
        self.assertEqual(row(a)["notes"], [])
        decide(tid, message="Less white, more glass")
        n = row(a)["notes"]
        self.assertEqual([{k: x[k] for k in ("ticket", "version", "title", "note", "thumb")} for x in n],
                         [{"ticket": tid, "version": 1, "title": "A card", "note": "Less white, more glass",
                           "thumb": f"/shots/{tid}/1.png"}])
        call("/api/update", {"id": tid, "image_b64": PNG}, headers=ident(s))     # a new version clears it
        self.assertEqual(row(a)["notes"], [])
        decide(tid, message="Closer")                                            # a note on v2 is back
        self.assertEqual([(x["version"], x["note"]) for x in row(a)["notes"]], [(2, "Closer")])
        code, r = call("/api/agents/read", {"name": a})
        self.assertEqual(code, 200)
        self.assertIsInstance(r["notes_read"], float)
        self.assertEqual((row(a)["notes"], row(a)["notes_read"]), ([], r["notes_read"]))
        other = post(session=s)
        decide(other, message="One more")                                         # a later note shows
        self.assertEqual([x["ticket"] for x in row(a)["notes"]], [other])

    def test_approve_and_archive_close_notes(self):
        a, s = self.owned()
        t1, t2 = post(session=s), post(session=s)
        decide(t1)
        decide(t2)
        decide(t1, "approve", "")
        call("/api/archive", {"id": t2})
        self.assertEqual(row(a)["notes"], [])

    def test_video_note_has_no_thumb(self):
        a, s = self.owned()
        tid = post(session=s, video=True)
        decide(tid)
        self.assertEqual(row(a)["notes"][0]["thumb"], "")

    def test_newest_first_at_most_eight(self):
        a, s = self.owned()
        ids = [post(session=s) for _ in range(10)]
        for tid in ids:
            decide(tid)
        self.assertEqual([n["ticket"] for n in row(a)["notes"]], ids[::-1][:8])

    def test_read_mark_is_not_agent_activity(self):
        a, s = self.owned()
        seen = json.loads(S.AGENTS.read_text())[a]["seen"]
        call("/api/agents/read", {"name": a})
        self.assertEqual(json.loads(S.AGENTS.read_text())[a]["seen"], seen)

    def test_read_route_guard(self):
        a, s = self.owned()
        self.assertEqual(call("/api/agents/read", {"name": "Claude · nobody here"})[0], 404)
        self.assertEqual(call("/api/agents/read", {"name": a}, headers={"Origin": "https://example.com"})[0], 403)
        self.assertEqual(call("/api/agents/read", {"name": a}, ctype="text/plain")[0], 403)
        self.assertEqual(call("/api/agents/read", {"name": a}, headers=REMOTE)[0], 200)   # the phone

    def test_wait_wakes_agents_when_a_note_lands(self):
        a, s = self.owned()
        tid = post(session=s)
        cur = call("/api/wait?since=0&timeout=0")[1]["cursor"]
        decide(tid)
        seen, deadline = set(), time.time() + 5
        while "agents" not in seen and time.time() < deadline:
            r = call(f"/api/wait?since={cur}&timeout=3")[1]
            seen.update(r["changed"])
            cur = r["cursor"]
        self.assertIn("agents", seen)


class StopTests(unittest.TestCase):
    """POST /api/agents/stop over a fake registry: a fake process table and tmux, and kill functions
    that only record. Fake pids sit above macOS's highest pid (99999), so nothing real is ever named."""
    START = "Thu Oct 15 17:00:00 2026"

    def setUp(self):
        self.saved = {k: getattr(S, k) for k in ("_procs", "_tmux_panes", "_kill_pid", "_kill_tmux", "STOP_GRACE")}
        me = os.getpid()
        self.table = {me: (1, self.START, "python3", "S")}     # this server, at the top of its own tree
        self.panes, self.calls, self.stubborn = [], [], set()
        S._procs = lambda: dict(self.table)
        S._tmux_panes = lambda: list(self.panes)
        S._kill_pid = self.kill_pid
        S._kill_tmux = self.kill_tmux
        S.STOP_GRACE = 0.3
        self.log, self.stdout = io.StringIO(), sys.stdout       # the server logs each stop to stdout (server.log)
        sys.stdout = self.log

    def tearDown(self):
        sys.stdout = self.stdout
        for k, v in self.saved.items():
            setattr(S, k, v)

    def kill_pid(self, pid, sig):
        self.calls.append(("signal", pid, sig))
        if pid not in self.table:
            return False
        if sig == S.signal.SIGKILL or pid not in self.stubborn:
            self.table.pop(pid)
        return True

    def kill_tmux(self, *args):
        self.calls.append(("tmux",) + args)
        target = args[-1]
        gone = [p for p in self.panes if (p[0] if args[0] == "kill-session" else p[2]) == target]
        for p in gone:
            self.panes.remove(p)
            for pid in [x for x in self.table if self.table[x][0] == p[3]] + [p[3]]:
                self.table.pop(pid, None)
        return bool(gone)

    def master(self, tag, pid, comm="claude", ppid=1, sessions=1):
        """A joined agent whose sessions claim pid, and that pid in the process table."""
        a = name(tag)
        join(a)
        call("/api/chat", {"agent": a, "body": "hello"})
        call("/api/chat/reply", {"agent": a, "body": "hi"})
        self.table[pid] = (ppid, self.START, comm, "S")
        sids = [sid() for _ in range(sessions)]
        with S._lock:
            c = S._claims()
            for s in sids:
                c["sessions"][s] = {"name": a, "pid": pid, "pid_start": self.START, "first": time.time(),
                                    "seen": time.time(), "how": "listen"}
            S._claims_write(c)
        return a, sids

    def names(self):
        return {r["name"] for r in call("/api/agents")[1]["agents"]}

    def stop(self, who, **kw):
        return call("/api/agents/stop", {"name": who}, **kw)

    def test_sigterm_path_ends_it_archives_the_thread_and_marks_the_row(self):
        a, sids = self.master("term", 990001, sessions=2)
        self.assertIn(a, self.names())
        code, r = self.stop(a)
        self.assertEqual((code, r), (200, {"ok": True, "stopped": [990001], "tmux": []}))
        self.assertEqual(self.calls, [("signal", 990001, S.signal.SIGTERM)])
        self.assertNotIn(a, self.names())
        self.assertEqual(call("/api/chat?agent=" + urllib.parse.quote(a))[1]["messages"], [])
        stored_row = json.loads(S.AGENTS.read_text())[a]
        self.assertIsInstance(stored_row["stopped"], float)
        self.assertEqual(sorted(stored_row["stopped_sessions"]), sorted(sids))
        slug = re.sub(r"[^A-Za-z0-9]+", "-", a).strip("-")
        chat_arch = [f for f in (S.DATA / "archive").glob("chat-*.jsonl") if slug in f.name]
        agent_arch = [f for f in (S.DATA / "archive").glob("agents-*.json") if slug in f.name]
        self.assertEqual((len(chat_arch), len(agent_arch)), (1, 1))
        rows = [json.loads(x) for x in chat_arch[0].read_text().splitlines()]
        self.assertEqual([(x["role"], x["body"]) for x in rows],
                         [("you", "hello"), ("master", "hi"), ("system", S.STOP_LINE)])
        self.assertNotIn("stopped", json.loads(agent_arch[0].read_text())[a])
        line = self.log.getvalue().strip().splitlines()[-1]
        self.assertIn(" stop " + json.dumps(a, ensure_ascii=False), line)
        self.assertIn('"stopped": [990001]', line)

    def test_tmux_session_is_killed_when_it_holds_only_that_pane(self):
        a, _ = self.master("tmux", 990002, ppid=990003)
        self.table[990003] = (1, self.START, "tmux", "S")
        self.panes.append(("$41", "ts5-unit-1", "%41", 990002))
        code, r = self.stop(a)
        self.assertEqual((code, r["stopped"], r["tmux"]), (200, [990002], ["ts5-unit-1"]))
        self.assertEqual(self.calls, [("tmux", "kill-session", "-t", "$41")])
        self.assertNotIn(a, self.names())

    def test_a_shared_tmux_session_loses_only_that_pane(self):
        a, _ = self.master("pane", 990004)
        self.table[990005] = (1, self.START, "zsh", "S")
        self.panes += [("$42", "shared", "%42", 990004), ("$42", "shared", "%43", 990005)]
        code, r = self.stop(a)
        self.assertEqual((code, r["stopped"]), (200, [990004]))
        self.assertEqual(self.calls, [("tmux", "kill-pane", "-t", "%42")])
        self.assertIn(990005, self.table)

    def test_sigkill_follows_when_sigterm_is_ignored(self):
        a, _ = self.master("stubborn", 990006)
        self.stubborn.add(990006)
        code, r = self.stop(a)
        self.assertEqual((code, r["stopped"]), (200, [990006]))
        self.assertEqual(self.calls, [("signal", 990006, S.signal.SIGTERM), ("signal", 990006, S.signal.SIGKILL)])

    def test_a_process_that_is_not_claude_is_refused_and_never_signalled(self):
        a, _ = self.master("sleep", 990007, comm="/bin/sleep")
        code, r = self.stop(a)
        self.assertEqual((code, r["ok"], r["refused"]), (409, False, [990007]))
        self.assertIn("no live Claude session", r["error"])
        self.assertEqual(self.calls, [])
        self.assertIn(a, self.names())
        self.assertIsNone(json.loads(S.AGENTS.read_text())[a].get("stopped"))
        self.assertIn('"why": "not a claude process"', self.log.getvalue())

    def test_the_servers_own_tree_is_refused(self):
        me = os.getpid()
        a, _ = self.master("above", 990008)
        self.table[me] = (990008, self.START, "python3", "S")         # the server runs under that claude
        self.assertEqual(self.stop(a)[0], 409)
        b, _ = self.master("below", 990009, ppid=me)                   # a claude the server itself started
        self.assertEqual(self.stop(b)[0], 409)
        c, _ = self.master("pane", 990010)
        self.panes.append(("$44", "server-pane", "%44", 990008))       # the server's own tmux pane
        self.table[990010] = (990008, self.START, "claude", "S")
        self.assertEqual(self.stop(c)[0], 409)
        self.assertEqual(self.calls, [])

    def test_unknown_gone_or_reused_pids_end_nothing(self):
        self.assertEqual(self.stop("Claude · nobody here"), (404, {"ok": False, "error": "no agent by that name is in the list"}))
        a = name("nosession")
        join(a)
        self.assertEqual(self.stop(a)[0], 409)
        b, _ = self.master("gone", 990011)
        self.table.pop(990011)
        self.assertEqual(self.stop(b)[0], 409)
        c, _ = self.master("reused", 990012)
        self.table[990012] = (1, "Fri Oct 16 09:00:00 2026", "claude", "S")    # another claude holds the pid now
        self.assertEqual(self.stop(c)[0], 409)
        self.assertEqual(self.calls, [])
        self.assertTrue({a, b, c} <= self.names())

    def test_a_stopped_agent_is_not_stopped_twice_and_takes_no_messages(self):
        a, _ = self.master("twice", 990013)
        self.assertEqual(self.stop(a)[0], 200)
        self.assertEqual(self.stop(a)[0], 404)
        self.assertEqual(call("/api/chat", {"agent": a, "body": "are you there"}),
                         (404, {"error": "that agent was spun down"}))

    def test_route_guard(self):
        a, _ = self.master("guard", 990014)
        self.assertEqual(self.stop(a, ctype="text/plain")[0], 403)
        self.assertEqual(self.stop(a, headers={"Origin": "https://example.com"})[0], 403)
        self.assertEqual(self.stop(a, headers={"X-Forwarded-For": "203.0.113.7"})[0], 401)
        self.assertEqual(self.stop("bad\nname")[0], 404)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.stop(a, headers=REMOTE)[0], 200)        # the phone, with the token

    def test_stale_heartbeats_keep_it_off_and_a_new_session_brings_it_back(self):
        a, sids = self.master("back", 990015)
        self.stop(a)
        join(a, session=sids[0], pid=990015)        # the ended session's leftover listener
        join(a)                                     # a listen that names no session
        self.assertNotIn(a, self.names())
        join(a, session=sid())                      # a genuinely new session under that NAME
        self.assertIn(a, self.names())
        self.assertNotIn("stopped", json.loads(S.AGENTS.read_text())[a])
        self.assertEqual(call("/api/chat?agent=" + urllib.parse.quote(a))[1]["messages"], [])

    def test_clear_all_keeps_the_stopped_mark(self):
        a, sids = self.master("cleared", 990017)
        self.stop(a)
        quiet(a, 120)
        self.assertEqual(call("/api/chat/clear", {})[0], 200)
        self.assertIsInstance(S._agents()[a]["stopped"], float)
        join(a, session=sids[0], pid=990017)        # the ended session's leftover listener, after Clear all
        self.assertNotIn(a, self.names())

    def test_wait_wakes_agents_and_chat(self):
        a, _ = self.master("wake", 990016)
        cur = call("/api/wait?since=0&timeout=0")[1]["cursor"]
        self.stop(a)
        r = call(f"/api/wait?since={cur}&timeout=0")[1]
        self.assertLessEqual({"agents", "chat"}, set(r["changed"]))


class LinksSeamTests(unittest.TestCase):

    def test_agents_payload_carries_links_from_links_py(self):
        a = name("link")
        join(a)
        asked = []
        fake = [{"a": a, "b": a + " peer", "last": time.time(), "count": 2, "summary": "Pushed the tip",
                 "active": True, "from": a}]
        old = S.links.rows
        S.links.rows = lambda names, now=None: (asked.append(set(names)), fake)[1]
        try:
            d = call("/api/agents")[1]
        finally:
            S.links.rows = old
        self.assertEqual(d["links"], fake)
        self.assertIn({r["name"] for r in d["agents"]}, asked)

    def test_activity_carries_the_link_counters(self):
        d = call("/api/activity")[1]
        self.assertLessEqual({"rows", "sessions", "now", "links"}, set(d))
        self.assertIsInstance(d["links"], dict)


GH_SHIM = '''#!%s
"""Fake gh for TapButtonTests: one open tap of hperwin's; records every call; never reaches GitHub."""
import json, os, sys
a = sys.argv[1:]
with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "gh-argv.jsonl"), "a") as f:
    f.write(json.dumps(a) + "\\n")
if a[:2] == ["issue", "view"]:
    print(json.dumps({"author": {"login": "hperwin"}, "title": "A tap", "body": "- [x] it works",
                      "labels": [{"name": "flightdeck"}], "state": "OPEN", "comments": [],
                      "url": "https://github.com/hperwin/fu-fixture/issues/7", "createdAt": "2026-10-16T19:00:00Z",
                      "closedAt": None, "stateReason": None}))
elif a[:2] == ["api", "user"]:
    print("hperwin")
elif a[:1] == ["api"] and "/labels/" in a[1]:
    print("flightdeck:fly")
elif a[:2] not in (["issue", "close"], ["issue", "edit"]):
    sys.exit("fake gh: unexpected call " + " ".join(a))
''' % sys.executable


class TapButtonTests(unittest.TestCase):
    """POST /api/taps/fly and /api/taps/accept are Hayden's buttons: the server names the caller by the
    process that owns the other end of its socket (a fake lsof answer and a fake process table here) and
    refuses any caller under a claude or codex process. gh is a fake that only records."""
    START = "Fri Oct 16 13:00:00 2026"
    KEY = "hperwin/fu-fixture#7"

    def setUp(self):
        self.saved = {k: getattr(S, k) for k in ("_socket_owners", "_procs", "GH")}
        self.saved_me = S.TAPS["me"]
        shim = pathlib.Path(TMP.name) / "gh-shim"
        shim.mkdir(exist_ok=True)
        (shim / "gh").write_text(GH_SHIM)
        (shim / "gh").chmod(0o755)
        self.log = shim / "gh-argv.jsonl"
        self.log.unlink(missing_ok=True)
        S.GH, S.TAPS["me"] = str(shim / "gh"), ""
        self.owners, self.table = set(), {}
        S._socket_owners = lambda host, port: None if self.owners is None else set(self.owners)
        S._procs = lambda: dict(self.table)

    def tearDown(self):
        for k, v in self.saved.items():
            setattr(S, k, v)
        S.TAPS["me"] = self.saved_me
        S.HOT.pop(self.KEY, None)

    def peer(self, *chain):
        """The socket's owner and the processes above it, nearest first, each a comm path; launchd on top."""
        pids = [990100 + i for i in range(len(chain))]
        for i, (pid, comm) in enumerate(zip(pids, chain)):
            self.table[pid] = (pids[i + 1] if i + 1 < len(pids) else 1, self.START, comm, "S")
        self.owners = {pids[0]}

    def writes(self):
        rows = [json.loads(x) for x in self.log.read_text().splitlines()] if self.log.exists() else []
        return [r[:2] for r in rows if r[:2] in (["issue", "close"], ["issue", "edit"])]

    def press(self, act, headers=None):
        return call("/api/taps/" + act, {"key": self.KEY}, headers=headers)

    def test_a_caller_under_claude_or_codex_is_refused_and_nothing_is_written(self):
        self.peer("/usr/bin/curl", "/bin/zsh", "claude", "/Applications/Ghostty.app/Contents/MacOS/ghostty")
        code, r = self.press("accept")
        self.assertEqual(code, 403)
        self.assertIn("inside claude (pid 990102)", r["error"])
        self.assertEqual(self.press("fly")[0], 403)
        self.table.clear()
        self.peer("/usr/bin/curl", "/bin/bash", "/Applications/ChatGPT.app/Contents/Resources/codex-cli/bin/codex")
        code, r = self.press("accept", headers={"Origin": BASE})
        self.assertEqual(code, 403)
        self.assertIn("inside codex", r["error"])
        self.assertEqual(self.writes(), [])

    def test_the_page_and_the_mac_app_are_allowed(self):
        self.peer("/Applications/Dia.app/Contents/Frameworks/ArcCore.framework/Helpers/Browser Helper.app/"
                  "Contents/MacOS/Browser Helper", "/Applications/Dia.app/Contents/MacOS/Dia")
        self.assertEqual(self.press("accept", headers={"Origin": BASE}), (200, {"ok": True}))
        self.table.clear()
        self.peer("/Applications/TapSpace.app/Contents/MacOS/TapSpace")
        self.assertEqual(self.press("fly"), (200, {"ok": True}))
        self.assertEqual(self.writes(), [["issue", "close"], ["issue", "edit"]])

    def test_the_phone_route_is_allowed(self):
        self.peer("/opt/homebrew/bin/ngrok")
        self.assertEqual(self.press("accept", headers=REMOTE), (200, {"ok": True}))
        self.assertEqual(self.press("fly", headers={"X-Forwarded-For": "203.0.113.7"})[0], 401)   # no bearer
        self.owners = set()      # tailscaled running as root: lsof cannot list it, the connection stays open
        self.assertEqual(self.press("fly", headers=REMOTE), (200, {"ok": True}))
        self.assertEqual(self.writes(), [["issue", "close"], ["issue", "edit"]])

    def test_the_claude_session_headers_are_still_refused(self):
        self.peer("/Applications/TapSpace.app/Contents/MacOS/TapSpace")
        for act in ("accept", "fly"):
            code, r = self.press(act, headers=ident(sid(), 990999))
            self.assertEqual(code, 403)
            self.assertIn("tap.py inside a Claude session", r["error"])
        self.assertEqual(self.writes(), [])

    def test_an_unknown_caller_is_refused(self):
        self.owners = None                                         # lsof failed
        self.assertIn("could not tell", self.press("accept")[1]["error"])
        self.owners = {990500}                                     # the owner exited before ps read the table
        self.assertIn("could not tell", self.press("accept")[1]["error"])
        self.assertEqual(self.writes(), [])

    def test_a_client_that_hung_up_is_refused(self):
        import socket
        srv = socket.create_server(("127.0.0.1", 0))
        with srv, socket.create_connection(srv.getsockname()) as c:
            a, _ = srv.accept()
            with a:
                peer = c.getsockname()
                self.assertEqual(S._agent_caller(a, peer), "")                  # still open: another user's
                c.close()
                time.sleep(0.05)
                self.assertIn("hung up", S._agent_caller(a, peer))

    def test_lsof_names_the_owner_of_a_real_connection(self):
        import socket
        S._socket_owners = self.saved["_socket_owners"]
        srv = socket.create_server(("127.0.0.1", 0))
        with srv, socket.create_connection(srv.getsockname()) as c:
            a, _ = srv.accept()
            with a:
                self.assertEqual(S._socket_owners(*c.getsockname()), {os.getpid()})


class ProofCardTests(unittest.TestCase):
    """A landed tap's proof images become Visuals cards on their own (_proof_cards, run after every tap
    refresh): one card per image, never a second one, a replaced image is a new version, and a card
    already posted with the same bytes is adopted. The proof proxy is a fake that records each fetch."""

    def setUp(self):
        self.saved = (S._proof_image, S.TAPS["items"])
        self.key = f"Org/repo-{uuid.uuid4().hex[:6]}#12"
        self.images, self.fetches = {}, []

        def image(key, i):
            self.fetches.append(i)
            if self.images.get(i) is None:
                raise RuntimeError("GitHub did not hand it over")
            return self.images[i], "image/png", f"https://github.com/user-attachments/assets/{i}"
        S._proof_image = image

    def tearDown(self):
        S._proof_image, S.TAPS["items"] = self.saved

    def tap(self, phase, *proof):
        """Serves one tap whose proof is [(alt, v, bytes)], the way _tap and _proof shape it."""
        self.images.update({i: raw for i, (_, _, raw) in enumerate(proof)})
        S.TAPS["items"] = [{"key": self.key, "title": "Lineup cards read Lineup: Team", "phase": phase,
                            "proof": [{"url": f"/api/taps/proof?key=k&i={i}&v={v}", "alt": alt}
                                      for i, (alt, v, _) in enumerate(proof)]}]

    def cards(self):
        live = [t for t in call("/api/state")[1]["tickets"] if (t.get("proof") or {}).get("key") == self.key]
        return sorted(live, key=lambda t: t["proof"]["i"])

    def shot(self, t, v):
        return (S.SHOTS / t["id"] / f"{v}.png").read_bytes()

    def test_each_image_once_however_many_refreshes(self):
        a, b = os.urandom(64), os.urandom(64)
        self.tap("landed", ("MLB page reads Lineup: Orioles", "e6527aab", a), ("", "b91677f5", b))
        for _ in range(3):
            S._proof_cards()
        c = self.cards()
        self.assertEqual([(t["version"], t["status"], t["title"]) for t in c],
                         [(1, "waiting", "MLB page reads Lineup: Orioles"), (1, "waiting", "Lineup cards read Lineup: Team")])
        self.assertEqual((c[0]["agent"], c[0]["campaign"], c[0]["versions"][0]["ext"]),
                         (self.key.split("/")[1], self.key, "png"))
        self.assertEqual((self.shot(c[0], 1), self.shot(c[1], 1)), (a, b))
        self.assertEqual(sorted(self.fetches), [0, 1])          # each image fetched once, then known

    def test_a_replaced_image_is_a_new_version_of_its_card(self):
        a, b, b2 = os.urandom(64), os.urandom(64), os.urandom(64)
        self.tap("landed", ("one", "11111111", a), ("two", "22222222", b))
        S._proof_cards()
        first = self.cards()
        self.assertEqual(decide(first[1]["id"], "approve")[0], 200)
        self.tap("landed", ("one", "11111111", a), ("two, retaken", "33333333", b2))
        S._proof_cards()
        S._proof_cards()
        c = self.cards()
        self.assertEqual([t["id"] for t in c], [t["id"] for t in first])
        self.assertEqual([(t["version"], t["status"]) for t in c], [(1, "waiting"), (2, "waiting")])
        self.assertEqual((c[1]["title"], self.shot(c[1], 1), self.shot(c[1], 2)), ("two, retaken", b, b2))

    def test_only_a_landed_tap(self):
        for phase in ("proposed", "enroute", "holding", "done", "closed"):
            self.tap(phase, ("one", "11111111", os.urandom(64)))
            S._proof_cards()
        self.assertEqual((self.cards(), self.fetches), ([], []))

    def test_an_image_github_does_not_hand_over_is_tried_again(self):
        self.tap("landed", ("one", "11111111", None))
        S._proof_cards()
        self.assertEqual(self.cards(), [])
        self.images[0] = os.urandom(64)
        S._proof_cards()
        self.assertEqual(len(self.cards()), 1)

    def test_a_card_posted_by_hand_with_the_same_bytes_is_adopted(self):
        a = os.urandom(64)
        code, r = call("/api/post", {"agent": "Lineup titles tap", "title": "MLB lineup card", "campaign": "Simulator tap",
                                     "image_b64": base64.b64encode(a).decode()})
        self.assertEqual(code, 200)
        self.tap("landed", ("MLB page", "11111111", a), ("Soccer page", "22222222", os.urandom(64)))
        S._proof_cards()
        S._proof_cards()
        c = self.cards()
        self.assertEqual([(t["id"], t["title"], t["version"]) for t in c][0], (r["id"], "MLB lineup card", 1))
        self.assertEqual(len(c), 2)
        self.assertEqual(stored(r["id"])["proof"], {"key": self.key, "i": 0, "v": "11111111"})

    def test_the_lead_owns_it_and_hears_the_notes(self):
        self.tap("landed", ("one", "11111111", os.urandom(64)))
        S._proof_cards()
        self.assertEqual((self.cards()[0]["owner"], self.cards()[0]["owner_how"]), (None, None))
        lead = name("lead")
        join(lead)
        S._set_lead(lead)
        try:
            t = self.cards()[0]
            self.assertEqual((t["owner"], t["owner_how"]), (lead, "lead"))
            self.assertEqual(decide(t["id"])[0], 200)
            event = json.loads(S.INBOX.read_text().splitlines()[-1])
            self.assertEqual((event["ticket"], event["owner"], event["campaign"]), (t["id"], lead, self.key))
        finally:
            with S._lock:
                a = S._agents()
                a[lead].pop("role", None)
                S._agents_write(a)


class CompatTests(unittest.TestCase):

    def test_change_kinds_unchanged(self):
        self.assertEqual(S.KINDS, ("agents", "chat", "taps", "cards"))

    def test_rows_and_tickets_keep_their_fields(self):
        a = name("compat")
        join(a)
        r = row(a)
        self.assertLessEqual({"name", "state", "seen", "first", "where", "last", "count", "last_master", "title"},
                             set(r))
        self.assertEqual((r["progress"], r["notes"], r["notes_read"]), (None, [], None))
        t = served(post(label="L1-compat", campaign="compat-only"))
        self.assertLessEqual({"id", "agent", "title", "campaign", "created", "version", "versions", "status",
                              "decisions"}, set(t))
        self.assertEqual(t["agent"], "L1-compat")

    def test_post_answer_shape(self):
        code, r = call("/api/post", {"agent": "L1-x", "title": "t", "image_b64": PNG})
        self.assertEqual((code, set(r)), (200, {"ok", "id", "version"}))

    def test_old_guarded_paths_still_refuse_an_empty_body(self):
        self.assertEqual(call("/api/chat", {"agent": "x"}), (400, {"error": "empty"}))


if __name__ == "__main__":
    unittest.main()
