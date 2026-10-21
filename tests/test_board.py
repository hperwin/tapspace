"""board.py against a fake Flightdeck door on a free loopback port: no Keychain, no network.

Run from the repo root:  python3 -B -m unittest tests.test_board -v
"""
import contextlib
import http.server
import io
import json
import os
import pathlib
import queue
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock
from urllib.parse import parse_qs, urlsplit

sys.dont_write_bytecode = True
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import board  # noqa: E402

KEY = "fdk_test_0123456789_key"
REASON = "the key 0123456789 was revoked"
HANG = object()
FAST = dict(SILENCE=0.4, COALESCE=0.15, BACKOFF_BASE=0.05, BACKOFF_MAX=0.4, JITTER=0.0, CRED_EVERY=0.15,
            UNREACHABLE_AFTER=0.4, HOLD=0.4, READ_MAX=3, READ_WINDOW=0.5, GET_TIMEOUT=2.0, READ_TIMEOUT=0.5,
            DEAD_RETRY=1.0)


def row(n, phase="landed", label="✔ ready for sign-off", repo="hperwin/fixture-app"):
    """A fleet row in the live shape (October 21 capture), fixture repo."""
    return {"id": f"pfixture{n:04d}", "journey": f"https://github.com/{repo.lower()}/issues/{n}",
            "handle": f"{repo.lower()}#{n}", "repo": repo, "number": n, "title": f"Fixture outcome {n}",
            "headline": f"Fixture outcome {n}", "status": "ready", "key": "ready", "label": label, "phase": phase,
            "note": "the pilot is done; the owner signs off", "why": "every goal is proven and it waits on you",
            "waiting": None, "event": "landed", "ask": None,
            "goals": {"total": 1, "proven": 1, "standard": 2, "line": "1 goal + 2 standard"},
            "goalList": [{"text": "It works", "checked": True, "standard": False}],
            "pilot": {"flight": "f-uuid", "posture": "landed", "thinking": None, "lane": "mac", "flying": True},
            "issueState": "open", "read": True}


def sse(event, obj, n=1):
    return f"id: {n}\nevent: {event}\ndata: {json.dumps(obj)}\n\n".encode()


def change(kind, r=None, journey=None):
    return sse("change", {"change": kind, "journey": journey or r["journey"], "row": r,
                          "event": r and r["event"], "at": "2026-10-21T21:35:20.081Z"})


class Fake(http.server.BaseHTTPRequestHandler):
    """Fake Flightdeck door. fleet answers `rows` (or the next of `fleet`); a stream opens with the next of `opens`
    (200 by default), writes the snapshot of `stream_rows` (else `rows`) chunked like the live door, then whatever
    push() gives the newest stream: None ends it, HANG goes silent with the socket open. Each stream reads its own
    queue, so a stream a test left behind never takes a frame. `reads` maps a journey to its read answer (200), a
    (code, body) pair, or HANG (accepted, never answered); else 404. A bearer in `dead` is refused 401 revoked;
    `down` answers every call 503."""
    protocol_version = "HTTP/1.1"
    rows, stream_rows, fleet, opens, dead, reads, seen, streams = [], None, [], [], set(), {}, [], []
    down = False
    closing = threading.Event()

    @staticmethod
    def push(frame):
        Fake.streams[-1].put(frame)

    def log_message(self, *a):
        pass

    def answer(self, code, obj):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(b)))
        self.send_header("connection", "close")
        self.end_headers()
        self.wfile.write(b)

    def chunk(self, b):
        self.wfile.write(b"%x\r\n" % len(b) + b + b"\r\n")
        self.wfile.flush()

    def do_GET(self):
        u = urlsplit(self.path)
        verb = u.path.removeprefix("/api/v1/journeys/")
        bearer = (self.headers.get("authorization") or "").removeprefix("Bearer ")
        Fake.seen.append((time.monotonic(), verb, u.query, bearer))
        if bearer in Fake.dead:
            return self.answer(401, {"ok": False, "kind": "revoked", "reason": REASON})
        if Fake.down:
            return self.answer(503, {"ok": False, "reason": "board-down"})
        if verb == "fleet":
            code, obj = Fake.fleet.pop(0) if Fake.fleet else (200, {"ok": True, "count": len(Fake.rows), "rows": Fake.rows})
            return self.answer(code, obj)
        if verb == "read":
            a = Fake.reads.get(parse_qs(u.query).get("journey", [""])[0])
            if a is HANG:
                Fake.closing.wait(1.5)                          # accepted, never answered
                return
            if a is None:
                return self.answer(404, {"ok": False, "kind": "unknown-handle"})
            return self.answer(*(a if isinstance(a, tuple) else (200, a)))
        if verb != "stream":
            return self.answer(404, {"ok": False})
        code, obj = Fake.opens.pop(0) if Fake.opens else (200, None)
        if code != 200:
            return self.answer(code, obj)
        q = queue.Queue()
        Fake.streams.append(q)
        newest = lambda: not Fake.closing.is_set() and Fake.streams and Fake.streams[-1] is q
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.send_header("transfer-encoding", "chunked")
        self.send_header("connection", "close")
        self.end_headers()
        rows = Fake.rows if Fake.stream_rows is None else Fake.stream_rows
        snap = sse("snapshot", {"count": len(rows), "rows": rows, "events": [], "at": "2026-10-21T21:35:20.081Z"}, 344)
        try:
            self.chunk(snap[:len(snap) // 2])               # the live data line spans chunks
            self.chunk(snap[len(snap) // 2:])
            while newest():
                try:
                    f = q.get(timeout=0.02)
                except queue.Empty:
                    continue
                if f is None:
                    break
                while f is HANG and newest():                  # silent, socket open, until a newer stream
                    time.sleep(0.02)
                if f is HANG:
                    return
                self.chunk(f)
            self.chunk(b"")
        except OSError:
            pass


def until(pred, timeout=5.0, what="condition"):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        v = pred()
        if v:
            return v
        time.sleep(0.01)
    raise AssertionError("timed out waiting for " + what)


_GUARDS = []


def setUpModule():
    """No test reads the real Keychain or inherits a board URL from the shell."""
    _GUARDS[:] = [mock.patch.dict(os.environ), mock.patch.object(board, "SECURITY", "/nonexistent/security")]
    for g in _GUARDS:
        g.start()
    for k in ("TAPSPACE_BOARD_URL", "TAPSPACE_BOARD_KEY"):
        os.environ.pop(k, None)


def tearDownModule():
    for g in reversed(_GUARDS):
        g.stop()


class Pure(unittest.TestCase):
    def test_frames_skip_keepalives_and_join_multiline_data(self):
        lines = iter([b": ping\n", b": beat\n", b"\n", b"id: 344\n", b"event: snapshot\n", b'data: {"a":\n',
                      b"data:1}\r\n", b"\r\n", b"retry: 5\n", b"event: change\n", b"id: 9\n", b"\n",
                      b"data: x\n", b"\n", b"event: change\n", b"data: cut"])
        self.assertEqual(list(board.frames(lambda: next(lines, b""))),
                         [("snapshot", '{"a":\n1}'), ("message", "x")])

    def test_refusals_are_read_by_kind_not_by_status(self):
        j = lambda k: json.dumps({"ok": False, "kind": k, "reason": REASON}).encode()
        for (status, body), want in {(200, b"{}"): "ok", (401, j("unknown")): "cred", (401, b"not json"): "cred",
                                     (403, j("signin-revoked")): "cred", (403, j("signin-unchecked")): "cred",
                                     (403, j("wrong-journey")): "fail", (429, j("slow-down")): "slow",
                                     (503, b'{"ok":false,"reason":"board-down"}'): "fail"}.items():
            with self.subTest(status=status, body=body):
                self.assertEqual(board._verdict(status, body), want)

    def test_backoff_doubles_from_two_seconds_to_a_minute_plus_jitter(self):
        b = board.Board(cred=lambda: None)
        for fails, low in [(1, 2), (2, 4), (3, 8), (4, 16), (5, 32), (6, 60), (9, 60)]:
            for _ in range(50):
                self.assertTrue(low <= b._delay(fails) < low + 1, (fails, b._delay(fails)))

    def test_keychain_cred_keeps_only_the_newest_flightdeck_token_base_and_expiry(self):
        blob = {"claudeAiOauth": {"accessToken": "sk-not-this"}, "mcpOAuth": {
            "flightdeck|old": {"accessToken": "fdk_old", "serverUrl": "https://old.example:10000/mcp", "expiresAt": 1},
            "flightdeck|new": {"accessToken": "fdk_new", "serverUrl": "https://fd.example:10000/mcp",
                               "expiresAt": 1794773212945, "refreshToken": "fdr_never"},
            "other|x": {"accessToken": "zzz", "serverUrl": "https://x/mcp", "expiresAt": 9e15}}}
        with tempfile.TemporaryDirectory() as d:
            shim = pathlib.Path(d) / "security"
            for out, rc, want in [(json.dumps(blob), 0, {"token": "fdk_new", "base": "https://fd.example:10000",
                                                          "exp": 1794773212.945}),
                                  ("", 44, None), ("not json", 0, None), (json.dumps({"mcpOAuth": {}}), 0, None)]:
                with self.subTest(rc=rc, out=out[:20]):
                    shim.write_text(f"#!/bin/sh\ncat <<'EOF'\n{out}\nEOF\nexit {rc}\n")
                    shim.chmod(0o755)
                    with mock.patch.object(board, "SECURITY", str(shim)):
                        self.assertEqual(board.keychain_cred(), want)

    def test_enabled_only_on_the_default_data_dir_or_with_a_board_url(self):
        for env, want in [({}, True), ({"TAPSPACE_DATA": "/tmp/x"}, False),
                          ({"TAPSPACE_DATA": "/tmp/x", "TAPSPACE_BOARD_URL": "http://127.0.0.1:9"}, True)]:
            with self.subTest(env=env), mock.patch.dict(os.environ, env):
                if "TAPSPACE_DATA" not in env:
                    os.environ.pop("TAPSPACE_DATA", None)
                self.assertEqual(board.enabled(), want)


class Wire(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Fake)
        cls.srv.daemon_threads = True
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.url = f"http://127.0.0.1:{cls.srv.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        Fake.closing.set()
        cls.srv.shutdown()
        cls.srv.server_close()

    def setUp(self):
        p = mock.patch.multiple(board, **FAST)
        p.start()
        self.addCleanup(p.stop)
        Fake.rows, Fake.stream_rows, Fake.fleet, Fake.opens, Fake.dead, Fake.reads = [row(1), row(2), row(3)], None, [], [], set(), {}
        Fake.seen, Fake.down, Fake.streams = [], False, []
        self.snaps, self.b = [], None

    def tearDown(self):
        if self.b:
            self.b.stop()

    def make(self, **kw):
        kw.setdefault("poll_every", 0.1)
        if "cred" not in kw:
            kw.setdefault("base", self.url)
            kw.setdefault("key", KEY)
        self.b = board.Board(on_change=lambda: self.snaps.append(self.b.snapshot()), **kw)
        return self.b

    def live(self, n=3):
        until(lambda: self.b.snapshot()["state"] == "live" and len(self.b.snapshot()["rows"]) == n, what=f"live with {n}")

    def calls(self, verb, after=0.0):
        return [s for s in Fake.seen if s[1].split("?")[0] == verb and s[0] >= after]

    def test_a_given_base_and_key_never_read_the_keychain(self):
        hit = []
        with mock.patch.object(board, "keychain_cred", lambda: hit.append(1)):
            self.make().start()
            self.live()
            self.b.stop()
            Fake.seen = []
            self.b = board.Board(base=self.url, poll_every=0.1)
            self.b.start()
            until(lambda: self.b.snapshot()["state"] == "signed-out")
        self.assertEqual(hit, [])
        self.assertEqual(Fake.seen, [])
        self.assertTrue(all(s[3] == KEY for s in self.calls("stream")))

    def test_snapshot_then_changes_are_keyed_by_lower_case_journey(self):
        self.make().start()
        self.live()
        s = self.b.snapshot()
        self.assertEqual(set(s["rows"]), {row(n)["journey"] for n in (1, 2, 3)})
        self.assertEqual((s["loaded"], s["error"]), (True, ""))
        self.assertGreater(s["at"], 0)
        r9 = row(9, repo="hperwin/Fixture-App")
        Fake.push(change("added", r9, journey="https://github.com/hperwin/Fixture-App/issues/9"))
        until(lambda: "https://github.com/hperwin/fixture-app/issues/9" in self.b.snapshot()["rows"])
        r1 = dict(row(1), label="✈ en route", phase="en route")
        Fake.push(change("changed", r1))
        until(lambda: self.b.snapshot()["rows"][r1["journey"]]["phase"] == "en route")
        n = len(self.snaps)
        Fake.push(change("changed", r1))                        # the same row again
        Fake.push(b": ping\n\n")
        Fake.push(sse("message", {"x": 1}) + b"event: change\ndata: {not json\n\n")
        Fake.push(change("removed", journey="https://github.com/hperwin/fixture-app/issues/404"))
        Fake.push(change("added", row(10)))
        until(lambda: row(10)["journey"] in self.b.snapshot()["rows"])
        self.assertEqual(len(self.snaps), n + 1)                       # only row 10 moved anything
        self.assertEqual(self.calls("fleet"), [])                      # an unknown removal asks nothing

    def test_removed_frames_wait_one_window_and_the_fleet_decides(self):
        self.make().start()
        self.live()
        Fake.rows = [row(1), row(3)]                                   # the truth: 2 left, 3 stays
        t = time.monotonic()
        Fake.push(change("removed", row(2)))
        Fake.push(change("removed", row(3)))
        until(lambda: len(self.b.snapshot()["rows"]) == 2)
        got = self.calls("fleet")
        self.assertEqual(len(got), 1)
        self.assertGreaterEqual(got[0][0] - t, board.COALESCE * 0.9)
        self.assertEqual(set(self.b.snapshot()["rows"]), {row(1)["journey"], row(3)["journey"]})
        self.assertTrue(all(row(3)["journey"] in s["rows"] for s in self.snaps if s["loaded"]))

    def test_a_failed_removal_check_keeps_the_rows_and_reconnects(self):
        self.make().start()
        self.live()
        Fake.fleet = [(503, {"ok": False, "reason": "board-down"})]
        Fake.rows = [row(1), row(3)]
        Fake.push(change("removed", row(2)))
        until(lambda: len(self.calls("stream")) == 2, what="a reconnect")
        until(lambda: len(self.b.snapshot()["rows"]) == 2 and self.b.snapshot()["state"] == "live")
        seen = [s for s in self.snaps if s["loaded"]]
        gone = next(i for i, s in enumerate(seen) if row(2)["journey"] not in s["rows"])
        self.assertEqual(seen[gone]["state"], "live")                 # it left with the fresh snapshot, not before
        self.assertIn("connecting", [s["state"] for s in seen[:gone]])

    def test_a_zero_count_snapshot_is_confirmed_by_the_fleet_first(self):
        self.make().start()
        self.live()
        Fake.stream_rows = []
        Fake.fleet = [(200, {"ok": True, "count": 3, "rows": Fake.rows})]
        Fake.push(None)
        until(lambda: len(self.calls("stream")) == 2 and len(self.calls("fleet")) == 1, what="confirm")
        until(lambda: self.b.snapshot()["state"] == "live")
        self.assertEqual(len(self.b.snapshot()["rows"]), 3)
        Fake.rows = []
        Fake.push(None)
        until(lambda: self.b.snapshot()["rows"] == {} and self.b.snapshot()["state"] == "live", what="confirmed empty")
        self.assertEqual(len(self.calls("fleet")), 2)
        self.assertTrue(all(len(s["rows"]) == 3 for s in self.snaps[:-1] if s["rows"]))

    def test_a_dead_key_keeps_the_rows_and_a_new_token_reconnects_at_once(self):
        tok, reads = ["fdk_test_old_9f3c"], []
        cred = lambda: (reads.append(1), {"token": tok[0], "base": self.url, "exp": 0})[1]
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            self.make(cred=cred).start()
            self.live()
            Fake.dead.add(tok[0])
            tok[0] = "fdk_test_new_7a1e"                                # Claude Code refreshed
            for n in (1, 2, 3):
                Fake.push(change("removed", row(n)))
            until(lambda: any(s[3] == tok[0] for s in self.calls("stream")), what="a stream on the new token")
            self.live()
        self.assertEqual([s[3] for s in self.calls("fleet")], ["fdk_test_old_9f3c"])
        self.assertEqual(len(reads), 2)
        self.assertTrue(all(len(s["rows"]) == 3 for s in self.snaps if s["loaded"]))
        blob = json.dumps(self.snaps)
        for secret in ("fdk_test_old_9f3c", "fdk_test_new_7a1e", REASON, "0123456789"):
            self.assertNotIn(secret, blob)
        self.assertEqual((out.getvalue(), err.getvalue()), ("", ""))

    def test_a_key_that_stays_dead_reads_ran_out_and_is_reread_every_minute(self):
        tok, reads = ["fdk_dead"], []
        Fake.dead = {"fdk_dead"}
        self.make(cred=lambda: (reads.append(1), {"token": tok[0], "base": self.url, "exp": 0})[1]).start()
        until(lambda: self.b.snapshot()["state"] == "expired")
        until(lambda: len(reads) >= 4, what="re-reads")
        s = self.b.snapshot()
        self.assertEqual((s["error"], s["loaded"]), (board.RAN_OUT, False))
        self.assertEqual(len([x for x in Fake.seen if x[3] == "fdk_dead"]), 1)
        tok[0] = "fdk_fresh"
        self.live()
        self.assertEqual(self.b.snapshot()["error"], "")

    def test_a_cut_off_is_a_403_and_a_wrong_journey_is_not_a_key_problem(self):
        Fake.opens = [(403, {"ok": False, "kind": "signin-revoked", "reason": REASON})]
        self.make().start()
        until(lambda: self.b.snapshot()["state"] == "expired")
        self.b.stop()
        Fake.seen = []
        Fake.opens = [(403, {"ok": False, "kind": "wrong-journey", "reason": REASON})]
        self.make().start()
        self.live()
        self.assertEqual(len(self.calls("stream")), 2)

    def test_a_refused_key_is_tried_again_every_five_minutes_and_still_reads_ran_out(self):
        """A2.6: a refusal can reverse (the sign-in list put back, a check the board could not make), so the same
        key is tried again every DEAD_RETRY; until it answers, the sentence stays the sign-in one."""
        cut = (403, {"ok": False, "kind": "signin-unchecked", "reason": REASON})
        Fake.opens = [cut, cut]                                        # refused twice, then the same key works
        self.make().start()
        self.live()
        opens = self.calls("stream")
        self.assertEqual([s[3] for s in opens], [KEY] * 3)
        for a, b in zip(opens, opens[1:]):
            self.assertGreaterEqual(b[0] - a[0], board.DEAD_RETRY * 0.9)
            self.assertLess(b[0] - a[0], board.DEAD_RETRY + board.CRED_EVERY + 0.3)
        self.assertEqual([s["state"] for s in self.snaps], ["connecting", "expired", "live"])

    def test_without_a_live_key_it_never_dials_and_says_why(self):
        for cred, state, words in [(lambda: None, "signed-out", board.NO_KEY),
                                   (lambda: {"token": KEY, "base": "", "exp": 0}, "signed-out", board.NO_KEY),
                                   (lambda: {"token": KEY, "base": self.url, "exp": time.time() - 1}, "expired", board.RAN_OUT)]:
            with self.subTest(state=state, words=words):
                self.make(cred=cred).start()
                until(lambda: self.b.snapshot()["state"] == state)
                time.sleep(0.3)
                self.assertEqual(self.b.snapshot()["error"], words)
                self.assertEqual(Fake.seen, [])
                self.b.stop()

    def test_a_drop_reconnects_with_backoff_and_polls_the_fleet_meanwhile(self):
        self.make().start()
        self.live()
        Fake.opens = [(503, {"ok": False, "reason": "board-down"})] * 5
        Fake.rows = [row(1), dict(row(2), phase="holding", label="● needs you"), row(3)]
        t = time.monotonic()
        Fake.push(None)
        until(lambda: self.b.snapshot()["state"] == "polling", what="polling")
        self.assertEqual(self.b.snapshot()["rows"][row(2)["journey"]]["phase"], "holding")
        self.live()
        back = time.monotonic()
        opens = [s[0] for s in self.calls("stream", t)]
        gaps = [b - a for a, b in zip([t] + opens, opens)]
        want = [0.05, 0.1, 0.2, 0.4, 0.4, 0.4]
        self.assertEqual(len(gaps), 6)
        for g, w in zip(gaps, want):
            self.assertGreaterEqual(g, w * 0.9)
            self.assertLess(g, w + 0.3)
        polls = [s[0] for s in self.calls("fleet", t)]
        self.assertGreaterEqual(len(polls), 5)
        self.assertGreaterEqual(polls[0] - t, 0.1 * 0.9)
        self.assertTrue(all(b - a >= 0.1 * 0.9 for a, b in zip(polls, polls[1:])))
        time.sleep(0.4)
        self.assertEqual(self.calls("fleet", back + 0.05), [])

    def test_a_silent_stream_reconnects_and_keepalives_hold_it_open(self):
        self.make().start()
        self.live()
        for _ in range(8):
            Fake.push(b": ping\n\n")
            time.sleep(0.1)
        self.assertEqual(len(self.calls("stream")), 1)                # 0.8 s of pings outlived a 0.4 s watchdog
        Fake.push(HANG)
        t = time.monotonic() - 0.1                                     # the last ping went out at most 0.1 s ago
        until(lambda: len(self.calls("stream")) == 2, what="the watchdog")
        self.assertGreaterEqual(self.calls("stream")[1][0] - t, board.SILENCE * 0.9)
        self.live()
        self.assertTrue(all(len(s["rows"]) == 3 for s in self.snaps if s["loaded"]))

    def test_an_unreachable_board_says_so_after_a_minute_and_keeps_the_rows(self):
        self.make().start()
        self.live()
        Fake.down = True
        t = time.monotonic()
        Fake.push(None)
        until(lambda: self.b.snapshot()["error"] == board.CANT_REACH, what="the sentence")
        self.assertGreaterEqual(time.monotonic() - t, board.UNREACHABLE_AFTER)
        s = self.b.snapshot()
        self.assertEqual((s["state"], len(s["rows"])), ("connecting", 3))
        Fake.down = False
        until(lambda: self.b.snapshot()["state"] in ("polling", "live") and self.b.snapshot()["error"] == "")

    def test_a_429_holds_every_call(self):
        slow = {"ok": False, "kind": "slow-down", "reason": "the key 0123456789 has made more than 120 calls this minute"}
        Fake.opens = [(429, slow)]
        b = self.make()
        b.start()
        until(lambda: len(Fake.seen) == 1)
        time.sleep(0.05)
        self.assertIsNone(b.read(row(1)["journey"]))                   # held: refused without a call
        self.live()
        self.assertEqual([s[1] for s in Fake.seen], ["stream", "stream"])
        self.assertGreaterEqual(Fake.seen[1][0] - Fake.seen[0][0], board.HOLD * 0.9)
        Fake.reads = {row(1)["journey"]: (429, slow)}                  # a read is refused 429 while live
        Fake.rows = [row(1), row(3)]
        t = time.monotonic()
        self.assertIsNone(b.read(row(1)["journey"]))
        Fake.push(change("removed", row(2)))

        def checked():
            Fake.push(b": ping\n\n")                                   # the door pings; feed the 0.4 s watchdog
            return len(b.snapshot()["rows"]) == 2
        until(checked, what="the held removal check")
        self.assertGreaterEqual(self.calls("fleet")[0][0] - t, board.HOLD * 0.9)   # the check waited out the hold
        self.assertEqual((len(self.calls("fleet")), len(self.calls("stream")), b.snapshot()["state"]), (1, 2, "live"))
        idle = board.Board(base=self.url, key=KEY, poll_every=0.1)    # never started: drive one backoff by hand
        idle._reread()
        now = time.monotonic()
        idle._poll_at, idle._hold_until = now - 0.01, now + 10        # a poll is due, but a hold is on
        n, cpu = len(Fake.seen), time.process_time()
        self.assertFalse(idle._down(now + 0.3))
        self.assertLess(time.process_time() - cpu, 0.1)                # it slept through the hold, never spun
        self.assertEqual(len(Fake.seen), n)

    def test_read_is_paced_and_none_on_any_failure(self):
        j = row(2)["journey"]
        ans = {"ok": True, "journey": j, "state": "closed", "status": "aborted", "event": "discarded", "phase": "done",
               "board": {"status": "aborted", "phase": "done", "label": "⊘ aborted", "note": "closed as not planned"}}
        hung = "https://github.com/hperwin/fixture-app/issues/408"
        Fake.reads = {j: ans, hung: HANG}
        b = self.make()
        self.assertIsNone(b.read(j))                                   # not started: no key yet
        b.start()
        self.live()
        t = time.monotonic()
        got = [b.read(j) for _ in range(3)]
        self.assertLess(time.monotonic() - t, 0.3)
        self.assertEqual(got, [ans] * 3)
        self.assertIsNone(b.read(j))                                   # a 4th inside the window: refused at once
        self.assertLess(time.monotonic() - t, 0.3)
        self.assertEqual(len(self.calls("read")), 3)
        until(lambda: b.read("https://github.com/hperwin/fixture-app/issues/404") is None and len(self.calls("read")) == 4,
              what="a slot after the window")
        self.assertGreaterEqual(self.calls("read")[3][0] - t, board.READ_WINDOW * 0.9)
        time.sleep(max(0.0, self.calls("read")[2][0] + board.READ_WINDOW + 0.02 - time.monotonic()))  # slots free
        w = time.monotonic()
        self.assertIsNone(b.read(hung))                                # an answer that never comes
        took = time.monotonic() - w
        self.assertGreaterEqual(took, board.READ_TIMEOUT * 0.9)
        self.assertLess(took, board.READ_TIMEOUT + 0.3)                # its own bound, well under GET_TIMEOUT
        self.assertEqual([parse_qs(s[2])["journey"][0] for s in self.calls("read")],
                         [j] * 3 + ["https://github.com/hperwin/fixture-app/issues/404", hung])

    def test_a_refused_read_rereads_the_key(self):
        tok, reads = ["fdk_r_old"], []
        b = self.make(cred=lambda: (reads.append(1), {"token": tok[0], "base": self.url, "exp": 0})[1])
        b.start()
        self.live()
        Fake.dead.add("fdk_r_old")
        tok[0] = "fdk_r_new"
        self.assertIsNone(b.read(row(1)["journey"]))
        until(lambda: any(s[3] == "fdk_r_new" for s in self.calls("stream")), what="reconnect on new token")
        self.assertEqual(len(reads), 2)

    def test_stop_ends_both_threads_and_closes_the_stream(self):
        b = self.make()
        b.start()
        b.start()
        self.live()
        self.assertEqual(sum(t.name == "board" and t.is_alive() for t in threading.enumerate()), 1)
        t = time.monotonic()
        b.stop()
        self.assertLess(time.monotonic() - t, 1.0)
        self.assertFalse(b._thread.is_alive() or b._pump_thread.is_alive())
        self.assertEqual(b.snapshot()["state"], "off")
        n = len(self.snaps)
        time.sleep(0.3)
        self.assertEqual(len(self.snaps), n)


if __name__ == "__main__":
    unittest.main()
