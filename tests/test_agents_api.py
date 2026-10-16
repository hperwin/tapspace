#!/usr/bin/env python3
"""Agents API: card owners, goal cards, change notes with a shared read mark, and the links seam.

Runs server.py in-process on an ephemeral port with a throwaway data dir: never :4747, never the
live data, never a real push. Stdlib only.
    python3 tests/test_agents_api.py -v        (or: python3 -m unittest discover -s tests -v)
"""
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

    def test_newest_claim_wins(self):
        a, b, s = name("first"), name("second"), sid()
        join(a, session=s)
        join(b, session=s)
        self.assertEqual(served(post(session=s))["owner"], b)

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
