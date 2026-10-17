"""tap.py card, listen's answer line and tap-read's proof numbers.

Runs tap.py as a child process against a stub HTTP server on a free loopback port (never 4747)
and checks exactly what tap.py sends and prints. Stdlib only:
    python3 -m unittest discover -s tests -v
TAP_PY overrides which tap.py runs (default: the one next to this tests/ folder).
"""
import base64
import http.server
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import threading
import unittest

TAP = pathlib.Path(os.environ.get("TAP_PY") or pathlib.Path(__file__).resolve().parents[1] / "tap.py")
SID = "aaaaaaaa-1111-2222-3333-444444444444"
PNG = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00"
       b"\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N\x00\x00"
       b"\x00\x00IEND\xaeB`\x82")
TAPS_OK = (200, {"fetched": 1.0, "items": [{"key": "hperwin/x#7", "proof": [{"url": "u0", "alt": "a"},
                                                                            {"url": "u1", "alt": "b"}]}]})
CARD_OK = (200, {"ok": True, "message": {"id": 9, "card": {"v": 1}}})


class Stub(http.server.BaseHTTPRequestHandler):
    """Records every request; answers from `answers` (path -> (code, json or raw bytes), or a list of them
    handed out in turn, the last one repeating), else 200 {"ok": true}."""
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
        ans = Stub.answers.get(path, (200, {"ok": True}))
        if isinstance(ans, list):
            ans = ans.pop(0) if len(ans) > 1 else ans[0]
        code, obj = ans
        out = obj if isinstance(obj, bytes) else json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    do_GET = do_POST = _serve


class TapCards(unittest.TestCase):
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
        Stub.answers.update({"/api/taps": TAPS_OK, "/api/chat/reply": CARD_OK})
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

    def calls(self):
        return [(s["method"], s["path"]) for s in Stub.seen]

    def card(self, *args, **env):
        """tap.py card --as A ...: the run and the card it posted (None when nothing was posted)."""
        r = self.tap("card", "--as", "A", *args, **env)
        sent = self.posts("/api/chat/reply")
        return r, (sent[-1]["card"] if sent else None)

    # card

    def test_card_sends_the_line_visual_items_images_and_ask(self):
        r = self.tap("card", "--as", "A", "Three taps moved", "--progress", "3/5:3 of 5 landed",
                     "--item", "hperwin/x#7=Fixed", "--item", "Glint app", "--image", f"{self.png}=Grid",
                     "--ask", "Which first?", "--check", "Lineup", "--check", "Box score", "--text")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.posts("/api/chat/reply"), [{
            "agent": "A", "images": [{"b64": base64.b64encode(PNG).decode(), "ext": "png"}],
            "card": {"line": "Three taps moved",
                     "visual": {"kind": "progress", "done": 3, "total": 5, "label": "3 of 5 landed"},
                     "items": [{"ref": "hperwin/x#7", "note": "Fixed"}, {"ref": "Glint app"}],
                     "ask": {"q": "Which first?", "checks": ["Lineup", "Box score"], "text": True},
                     "images": [{"label": "Grid"}]}}])
        self.assertNotIn("body", self.posts("/api/chat/reply")[0])     # the server writes the body: the line
        self.assertEqual(len(r.stdout.splitlines()), 1)
        self.assertEqual(json.loads(r.stdout)["message"]["id"], 9)

    def test_an_item_may_carry_a_short_name(self):
        r, card = self.card("Line", "--item", "hperwin/x#7|Beta look=Ready for your look", "--item", "Glint app| Cards ",
                            "--item", "x#8=Fixed | nothing to do", "--item", "Muddi", "--item", "x#9| =a=b")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(card["items"], [{"ref": "hperwin/x#7", "name": "Beta look", "note": "Ready for your look"},
                                         {"ref": "Glint app", "name": "Cards"},
                                         {"ref": "x#8", "note": "Fixed | nothing to do"},   # a | after the = is the note's
                                         {"ref": "Muddi"},
                                         {"ref": "x#9", "note": "a=b"}])
        self.assertEqual(self.calls(), [("POST", "/api/chat/reply")])
        self.assertIn("REF[|name][=note]", self.tap("card", "--help").stdout)

    def test_a_card_without_taps_never_reads_them(self):
        r, card = self.card("Spec done", "--face", "Muddi", "--word", "Done")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(card, {"line": "Spec done", "face": "Muddi", "visual": {"kind": "word", "text": "Done"}})
        self.assertEqual(self.calls(), [("POST", "/api/chat/reply")])

    def test_visual_forms(self):
        cases = [
            (["--progress", "3/5"], {"kind": "progress", "done": 3, "total": 5}),
            (["--timeline", " Audit | Spec |Build|", "--at", "2"],
             {"kind": "timeline", "steps": ["Audit", "Spec", "Build"], "at": 1}),
            (["--timeline", "Audit|Spec|Build", "--at", "done"],
             {"kind": "timeline", "steps": ["Audit", "Spec", "Build"], "at": 3}),
            (["--timeline", "Audit|Spec|Build", "--at", "spec"],
             {"kind": "timeline", "steps": ["Audit", "Spec", "Build"], "at": 1}),
            (["--timeline", "Audit|Spec|Build"], {"kind": "timeline", "steps": ["Audit", "Spec", "Build"], "at": 0}),
            (["--graph", "1,2.5,4:visits"], {"kind": "graph", "points": [1, 2.5, 4], "label": "visits"}),
            (["--graph", "3, 5,8"], {"kind": "graph", "points": [3, 5, 8]}),
            (["--word", "Landed"], {"kind": "word", "text": "Landed"}),
        ]
        for flags, visual in cases:
            with self.subTest(flags=flags):
                r, card = self.card("Line", *flags)
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertEqual(card["visual"], visual)

    def test_bad_flags_send_nothing(self):
        for flags in (["--progress", "3of5"], ["--graph", "1,x"], ["--graph", "1,nan"], ["--graph", "1,1e999"],
                      ["--at", "2"], ["--timeline", "a|b", "--at", "9"], ["--check", "A"], ["--text"],
                      ["--image", "/no/such.png"], ["--proof", "hperwin/x#7:0"], ["--proof", "x#7:one"],
                      ["--accept", " "]):
            with self.subTest(flags=flags):
                r, _ = self.card("Line", *flags)
                self.assertEqual(r.returncode, 1, r.stderr)
                self.assertTrue(r.stderr.strip())
        r, _ = self.card("Line", "--word", "X", "--progress", "1/2")
        self.assertEqual(r.returncode, 2)
        self.assertEqual(self.tap("card", "Line").returncode, 2)       # no --as and no TAPSPACE_AGENT
        self.assertEqual(Stub.seen, [])

    def test_ask_alone_is_a_text_box(self):
        _, card = self.card("Line", "--ask", "Q?")
        self.assertEqual(card["ask"], {"q": "Q?", "checks": [], "text": True})
        _, card = self.card("Line", "--ask", "Q?", "--check", "A")
        self.assertEqual(card["ask"], {"q": "Q?", "checks": ["A"], "text": False})

    def test_proof_numbers_and_short_keys(self):
        p0, p1 = {"proof": {"key": "hperwin/x#7", "i": 0}}, {"proof": {"key": "hperwin/x#7", "i": 1}}
        r = self.tap("card", "--as", "A", "Line", "--proof", "x#7")
        self.assertEqual(r.returncode, 0, r.stderr)
        sent = self.posts("/api/chat/reply")[-1]
        self.assertEqual(sent["images"], [p0, p1])
        self.assertNotIn("images", sent["card"])
        self.assertEqual(self.calls(), [("GET", "/api/taps"), ("POST", "/api/chat/reply")])
        self.tap("card", "--as", "A", "Line", "--proof", "hperwin/x#7:2")
        self.assertEqual(self.posts("/api/chat/reply")[-1]["images"], [p1])
        self.tap("card", "--as", "A", "Line", "--image", f"{self.png}=Grid", "--proof", "x#7:1")
        sent = self.posts("/api/chat/reply")[-1]
        self.assertEqual(sent["images"], [{"b64": base64.b64encode(PNG).decode(), "ext": "png"}, p0])
        self.assertEqual(sent["card"]["images"], [{"label": "Grid"}, {}])

    def test_unknown_or_short_proof_never_posts(self):
        r = self.tap("card", "--as", "A", "Line", "--proof", "y#1")
        self.assertEqual(r.returncode, 1)
        self.assertIn("tap.py taps", r.stdout)
        r = self.tap("card", "--as", "A", "Line", "--proof", "x#7:3")
        self.assertEqual(r.returncode, 1)
        self.assertIn("has 2 proof images", r.stdout)
        self.assertEqual(self.posts("/api/chat/reply"), [])

    def test_accept_puts_the_taps_full_key_on_the_card(self):
        r, card = self.card("Lineup card landed", "--accept", "x#7", "--proof", "x#7")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(card["action"], {"kind": "tap_accept", "tap": "hperwin/x#7"})
        self.assertEqual(self.calls(), [("GET", "/api/taps"), ("POST", "/api/chat/reply")])   # one look-up for both
        _, card = self.card("Line", "--accept", "HPERWIN/X#7")
        self.assertEqual(card["action"], {"kind": "tap_accept", "tap": "hperwin/x#7"})
        _, card = self.card("Line", "--word", "Landed")
        self.assertNotIn("action", card)

    def test_accept_of_no_single_tap_never_posts(self):
        r, card = self.card("Line", "--accept", "y#1")
        self.assertEqual((r.returncode, card), (1, None))
        self.assertIn("--accept: none of your taps is y#1", r.stdout)
        Stub.answers["/api/taps"] = (200, {"fetched": 1.0, "items": [{"key": "hperwin/x#7"}, {"key": "other/x#7"}]})
        r, card = self.card("Line", "--accept", "x#7")
        self.assertEqual((r.returncode, card), (1, None))
        self.assertIn("more than one of your taps is x#7", r.stdout)

    def test_retries_while_the_taps_load(self):
        Stub.answers["/api/taps"] = [(200, {"fetched": 0, "items": []}), TAPS_OK]
        r = self.tap("card", "--as", "A", "Line", "--proof", "x#7")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.calls(), [("GET", "/api/taps"), ("GET", "/api/taps"), ("POST", "/api/chat/reply")])
        Stub.seen.clear()
        Stub.answers["/api/chat/reply"] = [(503, {"error": "taps are still loading", "retry": True}), CARD_OK]
        r = self.tap("card", "--as", "A", "Line", "--item", "x#7")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(len(self.posts("/api/chat/reply")), 2)
        self.assertEqual(r.stdout.splitlines(), [json.dumps(CARD_OK[1])])

    def test_refused_card_exits_1_and_prints_the_reason(self):
        Stub.answers["/api/chat/reply"] = (400, {"error": "item: none of your taps is z#1"})
        r = self.tap("card", "--as", "A", "Line", "--item", "z#1")
        self.assertEqual(r.returncode, 1)
        self.assertIn("none of your taps is z#1", r.stdout)
        self.assertEqual(len(self.posts("/api/chat/reply")), 1)

    def test_an_old_server_stores_a_plain_reply(self):
        Stub.answers["/api/chat/reply"] = (200, {"ok": True, "message": {"id": 9, "body": ""}})
        r = self.tap("card", "--as", "A", "Line", "--image", str(self.png))
        self.assertEqual(r.returncode, 1)
        self.assertIn("no update cards", r.stderr)

    def test_as_defaults_and_identity(self):
        self.tap("card", "Line", TAPSPACE_AGENT="Claude · main")
        self.assertEqual(self.posts("/api/chat/reply")[-1]["agent"], "Claude · main")
        self.tap("card", "--as", "A", "Line", **self.mine())
        self.assertEqual(Stub.seen[-1]["headers"].get("x-claude-session"), SID)

    # listen

    def test_listen_prints_each_answer_as_one_json_line(self):
        ans = {"card": 814, "image": 0, "verdict": "approve", "line": "Three taps moved", "label": "Grid",
               "file": "/abs/data/chat-media/ab12cd34ef56.png"}
        Stub.answers["/api/chat/listen"] = (200, {"pending": [{"id": 815, "body": "Approved: Grid", "answer": ans},
                                                              {"id": 816, "body": "hello"}]})
        r = self.tap("listen", "--as", "Lane T2 test", "--timeout", "5")
        self.assertEqual(r.returncode, 0, r.stderr)
        lines = r.stdout.splitlines()
        self.assertEqual(lines[0], "[815] Approved: Grid")
        self.assertTrue(lines[1].startswith("    answer: "))
        self.assertEqual(json.loads(lines[1][len("    answer: "):]), ans)
        self.assertEqual(lines[2:], ["[816] hello"])

    # tap-read

    def test_tap_read_numbers_the_proof(self):
        tap = {"key": "hperwin/x#7", "title": "Lineup card", "url": "https://github.com/hperwin/x/issues/7",
               "status": "landed", "phase_word": "Landed", "goals": [{"done": True, "text": "Card shows lineups"}],
               "messages": [], "proof": [{"url": "u0", "alt": "MLB page"}, {"url": "u1", "alt": ""}]}
        Stub.answers["/api/taps"] = (200, {"fetched": 1.0, "items": [tap]})
        r = self.tap("tap-read", "hperwin/x#7")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("  [x] Card shows lineups\n  proof 1: MLB page\n  proof 2: image\n", r.stdout)
        del tap["proof"]                                                 # a server from before proof
        r = self.tap("tap-read", "hperwin/x#7")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("proof", r.stdout)



class AcceptShowsTheProof(unittest.TestCase):
    """tap.py card --accept KEY with no --image or --proof: the card shows that tap's own proof images, since a
    decision card is mostly the picture. TapCards' stub server and helpers, without running its cases again."""
    setUpClass = classmethod(TapCards.setUpClass.__func__)
    tearDownClass = classmethod(TapCards.tearDownClass.__func__)
    setUp, tearDown, tap, card, posts, calls = (TapCards.setUp, TapCards.tearDown, TapCards.tap, TapCards.card,
                                                TapCards.posts, TapCards.calls)
    P0, P1 = {"proof": {"key": "hperwin/x#7", "i": 0}}, {"proof": {"key": "hperwin/x#7", "i": 1}}

    def test_accept_alone_sends_every_proof_image(self):
        r, card = self.card("The lineup fix is ready. Accept it?", "--accept", "x#7")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.posts("/api/chat/reply")[-1]["images"], [self.P0, self.P1])
        self.assertEqual(card["action"], {"kind": "tap_accept", "tap": "hperwin/x#7"})
        self.assertNotIn("images", card)                 # no labels sent: the server labels them from the captions
        self.assertEqual(self.calls(), [("GET", "/api/taps"), ("POST", "/api/chat/reply")])   # one look-up

    def test_pictures_given_are_the_only_pictures(self):
        self.card("Line", "--accept", "x#7", "--proof", "x#7:2")
        self.assertEqual(self.posts("/api/chat/reply")[-1]["images"], [self.P1])
        _, card = self.card("Line", "--accept", "x#7", "--image", f"{self.png}=Grid")
        self.assertEqual(self.posts("/api/chat/reply")[-1]["images"], [{"b64": base64.b64encode(PNG).decode(), "ext": "png"}])
        self.assertEqual(card["images"], [{"label": "Grid"}])

    def test_a_tap_with_no_proof_still_gets_its_card(self):
        Stub.answers["/api/taps"] = (200, {"fetched": 1.0, "items": [{"key": "hperwin/x#7"}]})
        r, card = self.card("Line", "--accept", "x#7")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.posts("/api/chat/reply")[-1]["images"], [])
        self.assertEqual(card["action"], {"kind": "tap_accept", "tap": "hperwin/x#7"})

    def test_no_accept_no_pictures(self):
        r, card = self.card("Line", "--item", "x#7")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.posts("/api/chat/reply")[-1]["images"], [])
        self.assertEqual(self.calls(), [("POST", "/api/chat/reply")])


if __name__ == "__main__":
    unittest.main()
