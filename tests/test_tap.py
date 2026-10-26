"""tap.py's side of the wire: identity headers, card owner, goal cards, doctor counters, taps and tap-watch.

Runs tap.py as a child process against a stub HTTP server on a free loopback port (never 4747)
and checks exactly what tap.py sends and prints; tap-watch runs in process with its answers handed out in turn
and its 5 s sleep stubbed. Stdlib only:
    python3 -m unittest discover -s tests -v
TAP_PY overrides which tap.py runs (default: the one next to this tests/ folder).
"""
import contextlib
import http.server
import importlib.util
import io
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock

TAP = pathlib.Path(os.environ.get("TAP_PY") or pathlib.Path(__file__).resolve().parents[1] / "tap.py")
SID = "aaaaaaaa-1111-2222-3333-444444444444"
PNG = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00"
       b"\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N\x00\x00"
       b"\x00\x00IEND\xaeB`\x82")


def item(key, phase, word, status, last="tap", ask=None):
    """One /api/taps item: the fields tap.py reads."""
    return {"key": key, "title": "Lineup " + key[-1], "phase": phase, "phase_word": word, "status": status,
            "messages": [{"who": last, "body": f"{last} wrote on {key}"}], "ask": ask}


class Stub(http.server.BaseHTTPRequestHandler):
    """Records every request; answers from `answers` (path -> (code, json or raw bytes)), else 200 {"ok": true}."""
    seen = []
    answers = {}

    def log_message(self, *a):
        pass

    def _serve(self):
        n = int(self.headers.get("content-length") or 0)
        raw = self.rfile.read(n) if n else b""
        path = self.path.split("?")[0]
        Stub.seen.append({"method": self.command, "path": path, "url": self.path,
                          "headers": {k.lower(): v for k, v in self.headers.items()},
                          "body": json.loads(raw) if raw else None})
        code, obj = Stub.answers.get(path, (200, {"ok": True}))
        out = obj if isinstance(obj, bytes) else json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    do_GET = do_POST = _serve


class TapWire(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Stub)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.url = f"http://127.0.0.1:{cls.srv.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def setUp(self):
        Stub.seen.clear()
        Stub.answers.clear()
        self.tmp = tempfile.TemporaryDirectory()
        self.data = pathlib.Path(self.tmp.name)
        self.png = self.data / "shot.png"
        self.png.write_bytes(PNG)

    def tearDown(self):
        self.tmp.cleanup()

    def tap(self, *args, **env):
        """Run tap.py with a clean env: no inherited Claude, Codex or TapSpace variables."""
        e = {k: v for k, v in os.environ.items()
             if not k.startswith(("CLAUDE_", "CODEX_COMPANION_", "TAPSPACE_"))}
        e.update(TAPSPACE_URL=self.url, TAPSPACE_DATA=str(self.data), **env)
        return subprocess.run([sys.executable, str(TAP), *args], env=e,
                              capture_output=True, text=True, timeout=30)

    def mine(self):
        """CLAUDE_* env as a Claude Code Bash child sees it: this test process is tap.py's parent."""
        return {"CLAUDE_CODE_SESSION_ID": SID, "CLAUDE_PID": str(os.getpid())}

    def posts(self, path):
        return [s["body"] for s in Stub.seen if s["method"] == "POST" and s["path"] == path]

    # identity

    def test_identity_headers_when_claude_is_an_ancestor(self):
        r = self.tap("status", "abc", **self.mine())
        self.assertEqual(r.returncode, 0, r.stderr)
        h = Stub.seen[-1]["headers"]
        self.assertEqual(h.get("x-claude-session"), SID)
        self.assertEqual(h.get("x-claude-pid"), str(os.getpid()))

    def test_no_identity_for_a_stale_inherited_pid(self):
        r = self.tap("status", "abc", CLAUDE_CODE_SESSION_ID=SID, CLAUDE_PID="1")
        self.assertEqual(r.returncode, 0, r.stderr)
        h = Stub.seen[-1]["headers"]
        self.assertNotIn("x-claude-session", h)
        self.assertNotIn("x-claude-pid", h)

    def test_no_identity_outside_claude(self):
        self.tap("status", "abc")
        self.assertFalse({"x-claude-session", "x-claude-pid"} & set(Stub.seen[-1]["headers"]))

    def test_malformed_session_id_is_never_sent(self):
        self.tap("status", "abc", CLAUDE_CODE_SESSION_ID="not a session!", CLAUDE_PID=str(os.getpid()))
        h = Stub.seen[-1]["headers"]
        self.assertNotIn("x-claude-session", h)
        self.assertEqual(h.get("x-claude-pid"), str(os.getpid()))

    def test_codex_companion_id_when_the_claude_id_is_absent(self):
        self.tap("status", "abc", CODEX_COMPANION_SESSION_ID=SID, CLAUDE_PID=str(os.getpid()))
        self.assertEqual(Stub.seen[-1]["headers"].get("x-claude-session"), SID)

    def test_listen_carries_identity(self):
        Stub.answers["/api/chat/listen"] = (200, {"pending": [{"id": 7, "body": "hello"}]})
        r = self.tap("listen", "--as", "Claude · test", "--timeout", "5", **self.mine())
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("[7] hello", r.stdout)
        self.assertEqual(Stub.seen[-1]["headers"].get("x-claude-session"), SID)

    def test_messaging_token_is_never_sent(self):
        self.tap("status", "abc", CLAUDE_CODE_MESSAGING_TOKEN="secret-xyz", **self.mine())
        self.assertNotIn("secret-xyz", json.dumps(Stub.seen[-1]))

    # post

    def test_post_prints_the_bare_id_and_sends_no_owner_by_default(self):
        Stub.answers["/api/post"] = (200, {"ok": True, "id": "3f7de0b7af", "version": 1})
        r = self.tap("post", "--agent", "T2-cards", "--title", "Card", "--image", str(self.png), **self.mine())
        self.assertEqual(r.stdout, "3f7de0b7af\n")
        body = self.posts("/api/post")[-1]
        self.assertNotIn("owner", body)
        self.assertEqual(body["agent"], "T2-cards")
        self.assertEqual(Stub.seen[-1]["headers"].get("x-claude-session"), SID)

    def test_post_owner_flag(self):
        Stub.answers["/api/post"] = (200, {"ok": True, "id": "3f7de0b7af", "version": 1})
        self.tap("post", "--agent", "T2-cards", "--title", "Card", "--image", str(self.png),
                 "--owner", "Claude · main")
        self.assertEqual(self.posts("/api/post")[-1]["owner"], "Claude · main")

    # goal cards

    def test_goal_sends_name_goal_steps_and_a_one_based_current(self):
        Stub.answers["/api/agents/progress"] = (200, {"ok": True, "progress": {"current": 1}})
        r = self.tap("goal", "--as", "A", "Ship the thing", "--steps", " Audit | Spec |Build|", "--current", "2")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.posts("/api/agents/progress")[-1],
                         {"name": "A", "goal": "Ship the thing", "steps": ["Audit", "Spec", "Build"], "current": 1})
        self.assertTrue(json.loads(r.stdout)["ok"])

    def test_goal_alone_and_steps_alone(self):
        self.tap("goal", "--as", "A", "New goal")
        self.tap("goal", "--as", "A", "--steps", "One|Two")
        self.assertEqual(self.posts("/api/agents/progress"),
                         [{"name": "A", "goal": "New goal"}, {"name": "A", "steps": ["One", "Two"]}])

    def test_goal_with_nothing_to_change_sends_nothing(self):
        r = self.tap("goal", "--as", "A")
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(Stub.seen, [])

    def test_step_forms(self):
        for args in (["next"], ["done"], ["3"], ["Build", "--note", "2 of 4 lanes"], ["--note", ""]):
            self.tap("step", "--as", "A", *args)
        self.assertEqual(self.posts("/api/agents/progress"), [
            {"name": "A", "step": "next"},
            {"name": "A", "step": "done"},
            {"name": "A", "current": 2},
            {"name": "A", "step": "Build", "note": "2 of 4 lanes"},
            {"name": "A", "note": ""},
        ])

    def test_step_with_nothing_to_change_sends_nothing(self):
        r = self.tap("step", "--as", "A")
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(Stub.seen, [])

    def test_as_defaults_to_tapspace_agent(self):
        self.tap("step", "next", TAPSPACE_AGENT="Claude · main")
        self.assertEqual(self.posts("/api/agents/progress")[-1]["name"], "Claude · main")

    def test_refused_change_exits_1_and_prints_the_reason(self):
        Stub.answers["/api/agents/progress"] = (400, {"error": "set a goal and steps first"})
        r = self.tap("step", "--as", "A", "next")
        self.assertEqual(r.returncode, 1)
        self.assertIn("set a goal and steps first", r.stdout)

    def test_old_server_without_the_endpoint_exits_1(self):
        Stub.answers["/api/agents/progress"] = (404, b"<html>404 Not Found</html>")
        r = self.tap("step", "--as", "A", "next")
        self.assertEqual(r.returncode, 1)
        self.assertIn("HTTP 404", r.stdout)

    def test_progress_prints_the_stored_card(self):
        card = {"goal": "G", "steps": ["a", "b"], "current": 1, "note": None, "updated": 1.0}
        Stub.answers["/api/agents"] = (200, {"agents": [{"name": "A", "progress": card}, {"name": "B"}]})
        r = self.tap("progress", "--as", "A")
        self.assertEqual((r.returncode, json.loads(r.stdout)), (0, card))
        r = self.tap("progress", "--as", "B")
        self.assertEqual((r.returncode, json.loads(r.stdout)), (0, None))
        self.assertEqual(self.tap("progress", "--as", "Nobody").returncode, 1)

    # stop

    def test_stop_posts_the_name_and_exits_on_the_answer(self):
        Stub.answers["/api/agents/stop"] = (200, {"ok": True, "stopped": [4242], "tmux": ["master-x"]})
        r = self.tap("stop", "--as", "Claude · main")
        self.assertEqual((r.returncode, json.loads(r.stdout)["stopped"]), (0, [4242]), r.stderr)
        self.assertEqual(self.posts("/api/agents/stop"), [{"name": "Claude · main"}])
        Stub.answers["/api/agents/stop"] = (409, {"ok": False, "error": "no live Claude session to end for this agent"})
        r = self.tap("stop", "--as", "Claude · main")
        self.assertEqual(r.returncode, 1)
        self.assertIn("no live Claude session", r.stdout)

    def test_stop_never_defaults_to_tapspace_agent(self):
        r = self.tap("stop", TAPSPACE_AGENT="Claude · main")
        self.assertEqual(r.returncode, 2)
        self.assertEqual(Stub.seen, [])

    # restart

    def test_restart_posts_the_name_and_exits_on_the_answer(self):
        Stub.answers["/api/agents/restart"] = (200, {"ok": True, "agent": "Muddi", "how": "muddi"})
        r = self.tap("restart", "--as", "Muddi")
        self.assertEqual((r.returncode, json.loads(r.stdout)["how"]), (0, "muddi"), r.stderr)
        self.assertEqual(self.posts("/api/agents/restart"), [{"name": "Muddi"}])
        Stub.answers["/api/agents/restart"] = (409, {"ok": False, "error": "it is still running"})
        r = self.tap("restart", "--as", "Muddi")
        self.assertEqual(r.returncode, 1)
        self.assertIn("it is still running", r.stdout)

    def test_restart_never_defaults_to_tapspace_agent(self):
        r = self.tap("restart", TAPSPACE_AGENT="Muddi")
        self.assertEqual(r.returncode, 2)
        self.assertEqual(Stub.seen, [])

    # taps

    def test_taps_prints_each_question_under_its_tap(self):
        ask = {"text": "Ship the beta look? (Yes, Not yet)", "url": "u", "at": "2026-10-21T21:00:00Z"}
        Stub.answers["/api/taps"] = (200, {"fetched": 1.0, "error": "", "items": [
            item("A/b#1", "enroute", "en route", "needs-you", ask=ask),
            item("A/b#2", "landed", "ready for sign-off", "flying")]})
        r = self.tap("taps")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.splitlines(), [
            "A/b#1  [en route]  Lineup 1", "    asks: Ship the beta look? (Yes, Not yet)",
            "    last: tap wrote on A/b#1",
            "A/b#2  [ready for sign-off]  Lineup 2", "    last: tap wrote on A/b#2"])

    def test_taps_says_first_why_the_list_may_be_out_of_date(self):
        why = "Flightdeck's board can't be reached right now. This list may be out of date."
        Stub.answers["/api/taps"] = (200, {"fetched": 1.0, "error": why,
                                           "items": [item("A/b#2", "landed", "ready for sign-off", "flying")]})
        r = self.tap("taps")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.splitlines()[:2], [why, "A/b#2  [ready for sign-off]  Lineup 2"])

    # doctor

    def test_doctor_counts_owners_cards_notes_links_and_sessions(self):
        Stub.answers["/api/state"] = (200, {"tickets": [{"status": "waiting", "owner": "A"},
                                                        {"status": "changes", "owner": None}]})
        scanner = {"messages": 4, "pending": 0, "sessions": 3, "claimed": 3, "heard": 1, "passes": 9, "errors": 0}
        Stub.answers["/api/activity"] = (200, {"rows": [], "sessions": {"working": 1, "recent": 2},
                                               "links": scanner})
        Stub.answers["/api/agents"] = (200, {
            "agents": [{"name": "A", "progress": {"goal": "G"}, "notes": [{"ticket": "t"}]}, {"name": "B"}],
            "links": [{"a": "A", "b": "B", "active": True}, {"a": "A", "b": "C", "active": False}]})
        (self.data / "sessions.json").write_text(json.dumps(
            {"s1": {"name": "A"}, "s2": {"name": "A"}, "s3": {"name": "B"}, "campaigns": {"c": {"A": 2}}}))
        r = self.tap("doctor")
        self.assertEqual(r.returncode, 0, r.stderr)
        d = json.loads(r.stdout)
        self.assertEqual({k: d[k] for k in ("live_cards", "waiting_on_you", "cards_with_owner", "agents",
                                            "goal_cards", "notes", "links", "links_active", "claimed_sessions")},
                         {"live_cards": 2, "waiting_on_you": 1, "cards_with_owner": 1, "agents": 2,
                          "goal_cards": 1, "notes": 1, "links": 2, "links_active": 1,
                          "claimed_sessions": {"sessions": 3, "agents": 2}})
        self.assertEqual(d["link_scanner"], scanner)

    def test_doctor_reads_a_nested_sessions_file(self):
        Stub.answers["/api/state"] = (200, {"tickets": []})
        Stub.answers["/api/activity"] = (200, {"rows": [], "sessions": {}})
        (self.data / "sessions.json").write_text(json.dumps(
            {"sessions": {"s1": {"name": "A"}}, "campaigns": {}}))
        self.assertEqual(json.loads(self.tap("doctor").stdout)["claimed_sessions"], {"sessions": 1, "agents": 1})

    def test_doctor_survives_an_unexpected_sessions_file(self):
        Stub.answers["/api/state"] = (200, {"tickets": []})
        Stub.answers["/api/activity"] = (200, {"rows": [], "sessions": {}})
        (self.data / "sessions.json").write_text("[1, 2]")
        r = self.tap("doctor")
        self.assertEqual((r.returncode, json.loads(r.stdout)["claimed_sessions"]), (0, None))

    def test_doctor_on_an_old_server(self):
        Stub.answers["/api/state"] = (200, {"tickets": [{"status": "waiting"}]})
        Stub.answers["/api/activity"] = (200, {"rows": [], "sessions": {}})
        Stub.answers["/api/agents"] = (200, {"agents": [{"name": "A"}]})
        r = self.tap("doctor")
        self.assertEqual(r.returncode, 0, r.stderr)
        d = json.loads(r.stdout)
        self.assertEqual((d["cards_with_owner"], d["goal_cards"], d["links"], d["claimed_sessions"],
                          d["link_scanner"]), (0, 0, 0, None, None))


    # pair

    def fake_tailscale(self):
        """A tailscale CLI on PATH that is logged in and writes down every call it gets."""
        bin_ = self.data / "bin"
        bin_.mkdir()
        log = self.data / "tailscale.log"
        status = json.dumps({"BackendState": "Running", "Self": {"DNSName": "mac.tail1234.ts.net."}})
        exe = bin_ / "tailscale"
        exe.write_text(f"#!/bin/sh\necho \"$@\" >> '{log}'\n[ \"$1\" = status ] && echo '{status}'\nexit 0\n")
        exe.chmod(0o755)
        return {"PATH": f"{bin_}{os.pathsep}{os.environ['PATH']}"}, log

    def test_pair_opens_a_public_funnel_and_off_closes_it(self):
        (self.data / "token").write_text("tok\n")
        env, log = self.fake_tailscale()
        port = self.srv.server_address[1]
        r = self.tap("pair", **env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn(f"funnel --bg {port}", log.read_text().splitlines())
        self.assertEqual(json.loads((self.data / "remote.json").read_text()),
                         {"kind": "tailscale", "url": "https://mac.tail1234.ts.net", "port": port, "public": True})
        self.assertIn("glint://pair?url=https%3A%2F%2Fmac.tail1234.ts.net&token=tok", r.stdout)
        self.assertIn("Route: Tailscale Funnel, a public URL (https://mac.tail1234.ts.net)", r.stdout)
        r = self.tap("pair", "--off", **env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("funnel --https=443 off", log.read_text().splitlines())
        self.assertFalse((self.data / "remote.json").exists())
        self.assertIn("Route closed.", r.stdout)


class TapWatch(unittest.TestCase):
    """tap-watch in process: _call hands out /api/taps answers in turn (the last one repeats; an exception in
    the list is raised) and the 5 s sleep returns at once, so a case takes milliseconds and a miss ends in 5 s."""

    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("tap_watch_under_test", TAP)
        cls.tap = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.tap)

    def watch(self, *answers):
        left = list(answers)

        def call(*a, **k):
            x = left.pop(0) if len(left) > 1 else left[0]
            if isinstance(x, Exception):
                raise x
            return x
        out = io.StringIO()
        with mock.patch.object(self.tap, "_call", side_effect=call), mock.patch("time.sleep"), \
                mock.patch.object(sys, "argv", ["tap.py", "tap-watch", "--timeout", "5"]), \
                contextlib.redirect_stdout(out):
            code = self.tap.main()
        return code, out.getvalue()

    def test_a_new_question_wakes_it_and_is_printed(self):
        q1 = {"text": "Ship the beta look? (Yes, Not yet)", "url": "u", "at": "2026-10-21T21:00:00Z"}
        q2 = dict(q1, text="Which build goes first? (16, 17)")
        code, out = self.watch(
            {"fetched": 1.0, "items": [item("A/b#1", "holding", "needs you", "needs-you", last="you", ask=q1),
                                       item("A/b#2", "landed", "ready for sign-off", "flying")]},
            {"fetched": 1.0, "items": [item("A/b#1", "holding", "needs you", "needs-you", last="you", ask=q2),
                                       item("A/b#2", "done", "shipped", "landed")]})
        self.assertEqual(code, 0)
        self.assertIn("== A/b#1 [needs you] Lineup 1\n    asks: Which build goes first? (16, 17)\n"
                      "you wrote on A/b#1\n", out)
        self.assertIn("== A/b#2 [shipped] Lineup 2\ntap wrote on A/b#2\n", out)
        self.assertEqual(out.count("asks:"), 1)

    def test_a_server_down_or_still_loading_is_not_news(self):
        a = {"fetched": 1.0, "items": [item("A/b#1", "landed", "ready for sign-off", "flying"),
                                       item("A/b#2", "landed", "ready for sign-off", "flying")]}
        b = {"fetched": 1.0, "items": [item("A/b#1", "landed", "ready for sign-off", "flying"),
                                       item("A/b#2", "done", "aborted", "landed")]}
        code, out = self.watch(ConnectionRefusedError(), a, {"fetched": 0.0, "items": [], "error": ""}, b)
        self.assertEqual(code, 0)
        self.assertNotIn("A/b#1", out)
        self.assertIn("== A/b#2 [aborted] Lineup 2\n", out)


class DaemonEnv(unittest.TestCase):
    def test_server_env_drops_every_session_identity(self):
        spec = importlib.util.spec_from_file_location("tap_under_test", TAP)
        tap = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tap)
        env = {"PATH": "/bin", "HOME": "/h", "TAPSPACE_URL": "u", **{k: "x" for k in tap.SESSION_ENV}}
        self.assertEqual(tap._daemon_env(env), {"PATH": "/bin", "HOME": "/h", "TAPSPACE_URL": "u"})


if __name__ == "__main__":
    unittest.main()
