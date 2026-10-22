#!/usr/bin/env python3
"""Update cards: the card and answer rules, the reply and chat routes, heard, looks, the fleet, focus, and an
Accept pressed on a card.

Runs server.py in-process on an ephemeral port with a throwaway data dir: never :4747, never the live data,
never a real push, never GitHub (gh is a fake that records). Stdlib only.
    python3 tests/test_cards.py -v        (or: python3 -m unittest discover -s tests -v)
"""
import base64
import importlib.util
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
TMP = tempfile.TemporaryDirectory(prefix="tapspace-cards-")
PNG = ("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk"
       "YPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==")
PNG_BYTES = base64.b64decode(PNG)
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
        spec = importlib.util.spec_from_file_location("tapspace_server_cards_under_test", ROOT / "server.py")
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


def row(who):
    return next(r for r in call("/api/agents")[1]["agents"] if r["name"] == who)


def ids(listened):
    return [r["id"] for r in listened[1]["pending"]]


def media():
    return sorted(p.name for p in (S.MEDIA.glob("*") if S.MEDIA.exists() else []))


def thread(who):
    return call("/api/chat?agent=" + urllib.parse.quote(who))[1]["messages"]


def say(who, body):
    """The reviewer writes to who; the new row's id."""
    code, r = call("/api/chat", {"agent": who, "body": body})
    assert code == 200, r
    return r["message"]["id"]


def send(who, card, images=None, **extra):
    """who sends a card: (status, json)."""
    return call("/api/chat/reply", {"agent": who, "images": images or [], "card": card, **extra})


def sent(who, card, images=None):
    """who sends a card that must be stored; its row."""
    code, r = send(who, card, images)
    assert code == 200, r
    return r["message"]


def answer(who, a, nonce=None, **extra):
    return call("/api/chat", {"agent": who, "nonce": nonce or uuid.uuid4().hex, "answer": a, **extra})


def inbox():
    return [json.loads(x) for x in S.INBOX.read_text().splitlines()]


B64 = {"b64": PNG, "ext": "png"}
TAP = {"key": "hperwin/fixture-app#7", "repo": "hperwin/fixture-app", "number": 7, "initial": "F",
       "title": "Lineup card — fixed", "url": "https://github.com/hperwin/fixture-app/issues/7", "status": "flying",
       "goals": [{"text": "a", "done": True}, {"text": "b", "done": True}, {"text": "c", "done": False}],
       "messages": [], "replying": False, "created": "2026-10-16T19:00:00Z", "closed": None,
       "last": "2026-10-16T20:00:00Z", "phase": "landed", "phase_word": "Landed", "ask": None,
       "fly_requested": False,
       "proof": [{"url": "/api/taps/proof?key=hperwin%2Ffixture-app%237&i=0&v=1a2b3c4d", "alt": "MLB page reads Lineup"},
                 {"url": "/api/taps/proof?key=hperwin%2Ffixture-app%237&i=1&v=5e6f7a8b", "alt": "Image"}]}
TAP_TITLE = "Lineup card, fixed"         # the server's own words for TAP's title (the long dash reads as a comma)
AG = {"Muddi": {"role": "lead", "title": "Muddi"}, "Claude · t · 1": {"title": "Chat update cards", "look": 4}}


def tap(key, phase, last, title="A tap", goals=(1, 3)):
    """A TAPS item as _item serves it, with done of total goals checked."""
    return {"key": key, "repo": key.split("#")[0], "number": int(key.split("#")[1]), "title": title,
            "status": "flying", "phase": phase, "phase_word": S.PHASE_WORD[phase], "last": last,
            "goals": [{"text": str(i), "done": i < goals[0]} for i in range(goals[1])], "messages": [], "proof": []}


GH_SHIM = '''#!%s
"""Fake gh for the Accept tests: one open tap of hperwin's; records every call; never reaches GitHub."""
import json, os, sys
a = sys.argv[1:]
with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "gh-argv.jsonl"), "a") as f:
    f.write(json.dumps(a) + "\\n")
if a[:2] == ["issue", "view"]:
    print(json.dumps({"author": {"login": "hperwin"}, "title": "A tap", "body": "- [x] it works",
                      "labels": [{"name": "flightdeck"}], "state": "OPEN", "comments": [],
                      "url": "https://github.com/hperwin/fixture-app/issues/7", "createdAt": "2026-10-16T19:00:00Z",
                      "closedAt": None, "stateReason": None}))
elif a[:2] == ["api", "user"]:
    print("hperwin")
elif a[:1] == ["api"] and "/labels/" in a[1]:
    print("flightdeck:fly")
elif a[:2] not in (["issue", "close"], ["issue", "edit"]):
    sys.exit("fake gh: unexpected call " + " ".join(a))
''' % sys.executable

GH_SAY = '''#!%s
"""Fake gh for the say route: hperwin's tap, each comment the next url; records every call; never reaches GitHub."""
import json, os, sys
a = sys.argv[1:]
log = os.path.join(os.path.dirname(os.path.abspath(__file__)), "gh-argv.jsonl")
with open(log, "a") as f:
    f.write(json.dumps(a) + "\\n")
if a[:2] == ["issue", "view"]:
    print(json.dumps({"author": {"login": "hperwin"}, "labels": [{"name": "flightdeck"}]}))
elif a[:2] == ["issue", "comment"]:
    print("https://github.com/hperwin/fixture-say/issues/9#issuecomment-%%d" %% sum(1 for _ in open(log)))
elif a[:2] == ["api", "user"]:
    print("hperwin")
else:
    sys.exit("fake gh: unexpected call " + " ".join(a))
''' % sys.executable


class Taps(unittest.TestCase):
    """One landed tap of the reviewer's is served (TAP), its proof images come from a fake _proof_image, pushes
    are recorded, and gh is a fake that records every call (gh-argv.jsonl, absent until gh is first run)."""

    def setUp(self):
        self.saved = {k: getattr(S, k) for k in ("_proof_image", "_push", "GH", "CARD_MAX")}
        self.saved_taps = dict(S.TAPS)
        S.TAPS.update(items=[dict(TAP)], fetched=time.time(), error="", me="")
        self.proofs, self.pushes = [], []

        def proof_image(key, i):
            self.proofs.append((key, i))
            if i >= 2:
                raise LookupError("no such image")
            return PNG_BYTES, "image/png", "src"
        S._proof_image = proof_image
        S._push = lambda *a, **k: self.pushes.append((a, k))
        shim = pathlib.Path(TMP.name) / "gh-shim"
        shim.mkdir(exist_ok=True)
        (shim / "gh").write_text(GH_SHIM)
        (shim / "gh").chmod(0o755)
        self.gh_log = shim / "gh-argv.jsonl"
        self.gh_log.unlink(missing_ok=True)
        S.GH = str(shim / "gh")
        self.inbox_at = len(inbox())

    def tearDown(self):
        for k, v in self.saved.items():
            setattr(S, k, v)
        S.TAPS.clear()
        S.TAPS.update(self.saved_taps)
        S.HOT.pop(TAP["key"], None)

    def gh_calls(self):
        return [json.loads(x) for x in self.gh_log.read_text().splitlines()] if self.gh_log.exists() else []

    def new_inbox(self):
        return inbox()[self.inbox_at:]


class CardRuleTests(unittest.TestCase):
    """The pure rule behind a card send (_card)."""

    def card(self, raw, n=0, sender="Muddi", taps=(TAP,)):
        return S._card(raw, sender, n, AG, None if taps is None else list(taps))

    def test_a_full_card(self):
        raw = {"line": "Three taps moved, two need your eyes", "face": "Muddi",
               "visual": {"kind": "progress", "done": 3, "total": 5, "label": "3 of 5 landed"},
               "items": [{"ref": "fixture-app#7", "note": "Lineup card fixed"}, {"ref": "Chat update cards"}],
               "images": [{"label": "Lineup card"}, {}],
               "ask": {"q": "Which ships first?", "checks": ["Lineup", "Box score"], "text": True}}
        self.assertEqual(self.card(raw, 2), {
            "v": 1, "line": "Three taps moved, two need your eyes", "face": "agent:Muddi",
            "visual": {"kind": "progress", "done": 3, "total": 5, "label": "3 of 5 landed"},
            "items": [{"ref": "hperwin/fixture-app#7", "note": "Lineup card fixed"}, {"ref": "agent:Claude · t · 1"}],
            "images": [{"label": "Lineup card"}, {}],
            "ask": {"q": "Which ships first?", "checks": [{"id": "c1", "label": "Lineup"}, {"id": "c2", "label": "Box score"}],
                    "text": True}})

    def test_face_defaults(self):
        # the face is the card's one subject (A2): given, else the one item, else none; never the sender
        self.assertEqual(self.card({"line": "L", "items": [{"ref": TAP["key"]}]})["face"], TAP["key"])
        self.assertEqual(self.card({"line": "L", "items": [{"ref": "Chat update cards"}]})["face"], "agent:Claude · t · 1")
        self.assertIsNone(self.card({"line": "L"})["face"])
        self.assertIsNone(self.card({"line": "L"}, sender="Claude · t · 1")["face"])
        self.assertIsNone(self.card({"line": "L", "face": "  "})["face"])
        self.assertIsNone(self.card({"line": "L", "face": None})["face"])
        two = {"line": "L", "items": [{"ref": TAP["key"]}, {"ref": "Muddi"}]}
        self.assertIsNone(self.card(two)["face"])
        self.assertIsNone(self.card(two, sender="Claude · t · 1")["face"])
        self.assertIsNone(self.card({"line": "L", "action": {"kind": "tap_accept", "tap": TAP["key"]}})["face"])
        self.assertEqual(self.card({**two, "face": "project:tapspace"})["face"], "project:tapspace")
        self.assertEqual(self.card({"line": "L", "face": "agent:Muddi"})["face"], "agent:Muddi")
        self.assertEqual(self.card({"line": "L", "face": "MUDDI"}, sender="Claude · t · 1")["face"], "agent:Muddi")
        self.assertEqual(self.card({"line": "L", "face": "chat UPDATE cards"})["face"], "agent:Claude · t · 1")

    def test_tap_refs(self):
        self.assertEqual(self.card({"line": "L", "face": "HPERWIN/Fixture-App#7"})["face"], TAP["key"])
        self.assertEqual(self.card({"line": "L", "face": "fixture-app#7"})["face"], TAP["key"])
        twins = [dict(TAP, key="a/x#1"), dict(TAP, key="b/x#1")]
        with self.assertRaisesRegex(ValueError, "more than one"):
            self.card({"line": "L", "face": "x#1"}, taps=twins)
        with self.assertRaisesRegex(ValueError, "none of your taps"):
            self.card({"line": "L", "face": "none#9"})

    def test_project_ref(self):
        self.assertEqual(self.card({"line": "L", "face": "project:tapspace"})["face"], "project:tapspace")
        self.assertEqual(self.card({"line": "L", "face": "project:hperwin/tapspace"})["face"], "project:hperwin/tapspace")
        with self.assertRaises(ValueError):
            self.card({"line": "L", "face": "project:"})

    def test_while_taps_load(self):
        with self.assertRaises(S._Refused) as e:
            self.card({"line": "L", "items": [{"ref": "fixture-app#7"}]}, taps=None)
        self.assertEqual(e.exception.code, 503)
        self.assertEqual(S._refusal(e.exception)[1]["retry"], True)
        self.assertEqual(self.card({"line": "L", "items": [{"ref": "Muddi"}]}, taps=None)["face"], "agent:Muddi")

    def test_visual_forms(self):
        v = lambda x: self.card({"line": "L", "visual": x})["visual"]
        self.assertEqual(v({"kind": "progress", "done": 0, "total": 1, "label": " 0 of  1 "}),
                         {"kind": "progress", "done": 0, "total": 1, "label": "0 of 1"})
        self.assertEqual(v({"kind": "timeline", "steps": ["Audit", "Spec", "Build"]}),
                         {"kind": "timeline", "steps": ["Audit", "Spec", "Build"], "at": 0})
        self.assertEqual(v({"kind": "timeline", "steps": ["Audit", "Spec", "Build"], "at": 3})["at"], 3)
        self.assertEqual(v({"kind": "graph", "points": [3, 5.5, -8, 13]}), {"kind": "graph", "points": [3, 5.5, -8, 13]})
        self.assertEqual(v({"kind": "word", "text": "Landed"}), {"kind": "word", "text": "Landed"})
        self.assertEqual(v({"kind": "word", "text": "x" * 16})["text"], "x" * 16)        # the cap is 16 (ruling)

    def test_one_action_the_accept_of_a_known_tap(self):
        self.assertEqual(self.card({"line": "L", "action": {"kind": "tap_accept", "tap": "fixture-app#7"}})["action"],
                         {"kind": "tap_accept", "tap": TAP["key"]})
        self.assertNotIn("action", self.card({"line": "L"}))
        with self.assertRaises(S._Refused) as e:
            self.card({"line": "L", "action": {"kind": "tap_accept", "tap": TAP["key"]}}, taps=None)
        self.assertEqual(e.exception.code, 503)
        for act in [{"kind": "tap_fly", "tap": TAP["key"]}, {"tap": TAP["key"]}, "tap_accept", [],
                    {"kind": "tap_accept"}, {"kind": "tap_accept", "tap": "Muddi"},
                    {"kind": "tap_accept", "tap": "project:tapspace"}, {"kind": "tap_accept", "tap": "none#9"},
                    {"kind": "tap_accept", "tap": "hperwin/other#7"}]:
            with self.subTest(action=act), self.assertRaises(ValueError):
                self.card({"line": "L", "action": act})

    def test_refusals(self):
        long = "x" * 25
        for raw, n in [("a line", 0), ({}, 0), ({"line": 'say "hi"'}, 0), ({"line": "x" * 121}, 0),
                       ({"line": "Fast — and loose"}, 0), ({"line": "L", "items": [{"ref": "Muddi"}] * 9}, 0),
                       ({"line": "L", "items": [{"ref": "Muddi"}, {"ref": "agent:Muddi"}]}, 0),
                       ({"line": "L", "items": [{"ref": "Muddi", "note": "x" * 81}]}, 0),
                       ({"line": "L", "items": [{"ref": "Nobody here"}]}, 0),
                       ({"line": "L", "items": "Muddi"}, 0),
                       ({"line": "L", "visual": {"kind": "bar"}}, 0),
                       ({"line": "L", "visual": {"kind": "progress", "done": 4, "total": 3}}, 0),
                       ({"line": "L", "visual": {"kind": "progress", "done": 0, "total": 0}}, 0),
                       ({"line": "L", "visual": {"kind": "progress", "done": 1, "total": 1000}}, 0),
                       ({"line": "L", "visual": {"kind": "progress", "done": True, "total": 3}}, 0),
                       ({"line": "L", "visual": {"kind": "progress", "done": 1.5, "total": 3}}, 0),
                       ({"line": "L", "visual": {"kind": "progress", "done": 1, "total": 3, "label": "x" * 41}}, 0),
                       ({"line": "L", "visual": {"kind": "timeline", "steps": ["a"]}}, 0),
                       ({"line": "L", "visual": {"kind": "timeline", "steps": list("abcdefgh")}}, 0),
                       ({"line": "L", "visual": {"kind": "timeline", "steps": ["a", long]}}, 0),
                       ({"line": "L", "visual": {"kind": "timeline", "steps": ["a", "b", "c"], "at": 4}}, 0),
                       ({"line": "L", "visual": {"kind": "timeline", "steps": ["a", "b", "c"], "at": -1}}, 0),
                       ({"line": "L", "visual": {"kind": "graph", "points": [1]}}, 0),
                       ({"line": "L", "visual": {"kind": "graph", "points": list(range(25))}}, 0),
                       ({"line": "L", "visual": {"kind": "graph", "points": [1, float("nan")]}}, 0),
                       ({"line": "L", "visual": {"kind": "graph", "points": [1, float("inf")]}}, 0),
                       ({"line": "L", "visual": {"kind": "graph", "points": [1, "3"]}}, 0),
                       ({"line": "L", "visual": {"kind": "graph", "points": [1, True]}}, 0),
                       ({"line": "L", "visual": {"kind": "word", "text": "x" * 17}}, 0),
                       ({"line": "L", "images": [{}, {}]}, 1),
                       ({"line": "L", "images": [{"label": "a <b>"}]}, 1),
                       ({"line": "L", "ask": {"checks": ["a"]}}, 0),
                       ({"line": "L", "ask": {"q": "Q?", "checks": list("abcdefg")}}, 0),
                       ({"line": "L", "ask": {"q": "Q?", "checks": ["A", "a"]}}, 0),
                       ({"line": "L", "ask": {"q": "Q?", "text": "yes"}}, 0),
                       ({"line": "L", "ask": {"q": "Q?", "checks": [], "text": False}}, 0)]:
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                self.card(raw, n)

    def test_an_item_may_carry_a_short_name(self):
        c = self.card({"line": "L", "items": [{"ref": "fixture-app#7", "name": "  Beta   look ", "note": "Ready"},
                                              {"ref": "Muddi", "name": "  "}, {"ref": "Chat update cards", "name": None}]})
        self.assertEqual(c["items"], [{"ref": TAP["key"], "name": "Beta look", "note": "Ready"}, {"ref": "agent:Muddi"},
                                      {"ref": "agent:Claude · t · 1"}])
        self.assertEqual(self.card({"line": "L", "items": [{"ref": "Muddi", "name": "x" * 40}]})["items"][0]["name"], "x" * 40)
        for bad in ("x" * 41, 'The "beta" look', "Beta — look", "Beta <look>"):
            with self.subTest(name=bad), \
                    self.assertRaisesRegex(ValueError, "^item name: one plain line up to 40 characters$"):
                self.card({"line": "L", "items": [{"ref": "Muddi", "name": bad}]})


STORED = {"v": 1, "line": "Fleet moved", "face": "agent:Muddi",
          "faces": {"agent:Muddi": {"kind": "lead", "look": 0, "title": "Muddi", "state": "here"},
                    TAP["key"]: {"kind": "tap", "title": TAP_TITLE, "initial": "F", "phase": "landed"}},
          "items": [], "images": [{"label": "Lineup card"}, {"label": "Box score"}],
          "ask": {"q": "Which ships first?", "checks": [{"id": "c1", "label": "Lineup"}, {"id": "c2", "label": "Box score"}],
                  "text": True}}
CARD_ROW = {"id": 540, "agent": "Muddi", "role": "master", "images": ["/chat-media/aaaaaaaaaaaa.png",
                                                                      "/chat-media/bbbbbbbbbbbb.png"], "card": STORED}
ACCEPT_ROW = {**CARD_ROW, "id": 541, "images": ["/chat-media/cccccccccccc.png"],
              "card": {**STORED, "images": [{"label": "Lineup card"}], "ask": None,
                       "action": {"kind": "tap_accept", "tap": TAP["key"]}}}


class AnswerRuleTests(unittest.TestCase):
    """The pure rules behind an answer (_answer) and the body the server writes for it (_answer_line)."""

    def both(self, raw, row=CARD_ROW):
        a = S._answer(raw, row)
        return a, S._answer_line(a, row["card"])

    def test_image_answers_and_their_lines(self):
        self.assertEqual(self.both({"card": 540, "image": 0, "verdict": "approve"}),
                         ({"card": 540, "image": 0, "verdict": "approve"}, "Approved: Lineup card"))
        self.assertEqual(self.both({"card": 540, "image": 1, "verdict": "change", "note": " make it\nbigger "}),
                         ({"card": 540, "image": 1, "verdict": "change", "note": "make it\nbigger"},
                          "Not quite: Box score: make it / bigger"))
        self.assertEqual(self.both({"card": 540, "image": 0, "verdict": "change", "note": ""}),
                         ({"card": 540, "image": 0, "verdict": "change"}, "Not quite: Lineup card"))

    def test_ask_answers_and_their_lines(self):
        self.assertEqual(self.both({"card": 540, "checks": ["c2", "c1", "c2"], "text": "ship it"}),
                         ({"card": 540, "checks": ["c1", "c2"], "text": "ship it"},
                          "Which ships first? Lineup, Box score. ship it"))
        self.assertEqual(self.both({"card": 540, "checks": [], "text": "ship\nit"})[1],
                         "Which ships first? (no boxes ticked) ship / it")
        self.assertEqual(self.both({"card": 540, "checks": ["c2"]})[1], "Which ships first? Box score.")
        text_only = {**CARD_ROW, "card": {**STORED, "ask": {"q": "Anything else?", "checks": [], "text": True}}}
        self.assertEqual(self.both({"card": 540, "text": "no"}, text_only),
                         ({"card": 540, "checks": [], "text": "no"}, "Anything else? no"))

    def test_the_accept_and_its_not_quite(self):
        self.assertEqual(self.both({"card": 541, "action": "deny", "note": "not the\nright score"}, ACCEPT_ROW),
                         ({"card": 541, "action": "deny", "note": "not the\nright score"},
                          f"Not quite: {TAP_TITLE}: not the / right score"))
        self.assertEqual(self.both({"card": 541, "action": "deny"}, ACCEPT_ROW)[1], f"Not quite: {TAP_TITLE}")
        self.assertEqual(S._answer_line({"card": 541, "action": "accept"}, ACCEPT_ROW["card"]), f"Accepted: {TAP_TITLE}")

    def test_the_accept_names_its_tap_by_the_cards_short_name(self):
        def lines(items, title=TAP_TITLE):
            c = {**ACCEPT_ROW["card"], "items": items,
                 "faces": {**STORED["faces"], TAP["key"]: {**STORED["faces"][TAP["key"]], "title": title}}}
            return (S._answer_line({"card": 541, "action": "deny", "note": "the score\nis small"}, c),
                    S._answer_line({"card": 541, "action": "accept"}, c))
        named = {"ref": TAP["key"], "kind": "tap", "title": TAP_TITLE, "name": "Beta look", "note": "Ready"}
        self.assertEqual(lines([{"ref": "agent:Muddi", "name": "Lead"}, named]),
                         ("Not quite: Beta look: the score / is small", "Accepted: Beta look"))
        long = "Simulator screenshots of Snapp show the app the way beta testers see it"
        for items in ([], [{**named, "name": "  "}], [{k: v for k, v in named.items() if k != "name"}],
                      [{**named, "ref": "hperwin/other#2"}]):       # no item, a blank name, no name, another tap's
            with self.subTest(items=items):
                self.assertEqual(lines(items, long), ("Not quite: Simulator screenshots of Snapp show...: the score "
                                                      "/ is small", "Accepted: Simulator screenshots of Snapp show..."))

    def test_refusals(self):
        text_only = {**CARD_ROW, "card": {**STORED, "ask": {"q": "Anything else?", "checks": [], "text": True}}}
        no_box = {**CARD_ROW, "card": {**STORED, "ask": {"q": "Pick one", "checks": [{"id": "c1", "label": "A"}],
                                                          "text": False}}}
        silent = {**CARD_ROW, "card": {**STORED, "ask": None}}
        for raw, row_ in [({"card": 540, "image": 0, "verdict": "approve"}, None),
                          ({"card": "540", "image": 0, "verdict": "approve"}, CARD_ROW),
                          ({"card": True, "image": 0, "verdict": "approve"}, CARD_ROW),
                          ("approve", CARD_ROW),
                          ({"card": 540, "image": 2, "verdict": "approve"}, CARD_ROW),
                          ({"card": 540, "image": -1, "verdict": "approve"}, CARD_ROW),
                          ({"card": 540, "image": "0", "verdict": "approve"}, CARD_ROW),
                          ({"card": 540, "image": 0, "verdict": "yes"}, CARD_ROW),
                          ({"card": 540, "image": 0, "verdict": "approve", "note": "nice"}, CARD_ROW),
                          ({"card": 540, "image": 0, "verdict": "change", "note": "x" * 2001}, CARD_ROW),
                          ({"card": 540, "image": 0, "verdict": "change", "note": 5}, CARD_ROW),
                          ({"card": 540, "image": 0, "verdict": "approve", "checks": ["c1"]}, CARD_ROW),
                          ({"card": 540, "checks": ["c9"]}, CARD_ROW),
                          ({"card": 540, "checks": "c1"}, CARD_ROW),
                          ({"card": 540, "checks": ["c1"], "text": "and this"}, no_box),
                          ({"card": 540}, CARD_ROW),
                          ({"card": 540, "checks": ["c1"]}, silent),
                          ({"card": 540, "checks": [], "text": "  "}, text_only),
                          ({"card": 540, "action": "deny"}, CARD_ROW),
                          ({"card": 541, "action": "accept"}, ACCEPT_ROW),
                          ({"card": 541, "action": "approve"}, ACCEPT_ROW),
                          ({"card": 541, "action": "deny", "image": 0}, ACCEPT_ROW),
                          ({"card": 541, "action": "deny", "verdict": "change"}, ACCEPT_ROW),
                          ({"card": 541, "action": "deny", "note": "x" * 2001}, ACCEPT_ROW)]:
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                S._answer(raw, row_)

    def test_accept_goes_through_the_taps_own_button(self):
        with self.assertRaisesRegex(ValueError, "/api/taps/accept"):
            S._answer({"card": 541, "action": "accept"}, ACCEPT_ROW)


class CardFillTests(unittest.TestCase):
    """The pure fill a stored card gets (_card_fill): faces, items as they stand, was, from, image labels."""
    SERVED = [{"name": "Muddi", "state": "here", "progress": None},
              {"name": "Claude · t · 1", "state": "thinking",
               "progress": {"goal": "G", "steps": ["Audit", "Spec", "Build"], "current": 2}}]

    def fill(self, raw, thread=(), sources=(), n=0, served=None, taps=(TAP,)):
        return S._card_fill(S._card(raw, "Muddi", n, AG, list(taps)), list(sources),
                            self.SERVED if served is None else served, AG, list(taps), list(thread))

    def earlier(self, items, face="agent:Muddi", visual=None, cid=1):
        return {"id": cid, "agent": "Muddi", "role": "master",
                "card": {"v": 1, "line": "x", "face": face, "items": items, "images": [],
                         **({"visual": visual} if visual else {})}}

    def test_faces_items_and_images(self):
        raw = {"line": "L", "face": "Muddi", "images": [{"label": "Given"}],
               "items": [{"ref": TAP["key"], "note": "Fixed"}, {"ref": "Claude · t · 1"}, {"ref": "project:hperwin/tapspace"}]}
        c = self.fill(raw, sources=[None, {"key": TAP["key"], "i": 0}, {"key": TAP["key"], "i": 1}], n=3)
        self.assertEqual(c["faces"], {
            "agent:Muddi": {"kind": "lead", "look": 0, "title": "Muddi", "state": "here"},
            TAP["key"]: {"kind": "tap", "title": TAP_TITLE, "initial": "F", "phase": "landed"},
            "agent:Claude · t · 1": {"kind": "master", "look": 4, "title": "Chat update cards", "state": "thinking"},
            "project:hperwin/tapspace": {"kind": "project", "title": "tapspace", "initial": "T"}})
        self.assertEqual(c["items"], [
            {"ref": TAP["key"], "note": "Fixed", "kind": "tap", "title": TAP_TITLE, "phase": "landed", "word": "Landed",
             "goals": [2, 3]},
            {"ref": "agent:Claude · t · 1", "kind": "master", "title": "Chat update cards", "state": "thinking",
             "progress": {"current": 2, "steps": ["Audit", "Spec", "Build"]}},
            {"ref": "project:hperwin/tapspace", "kind": "project", "title": "tapspace"}])
        self.assertEqual(c["images"], [{"label": "Given"},
                                       {"label": "MLB page reads Lineup", "from": {"tap": TAP["key"], "i": 0}},
                                       {"label": "Image 3", "from": {"tap": TAP["key"], "i": 1}}])
        self.assertEqual((c["v"], c["line"], c["face"]), (1, "L", "agent:Muddi"))
        self.assertNotIn("visual", c)
        self.assertNotIn("ask", c)

    def test_a_master_the_rail_no_longer_serves_still_has_a_face(self):
        c = self.fill({"line": "L", "items": [{"ref": "Claude · t · 1"}]}, served=[])
        self.assertEqual(c["faces"]["agent:Claude · t · 1"]["state"], "away")

    def test_was_comes_from_the_latest_earlier_card_that_showed_it(self):
        steps = ["Audit", "Spec", "Build"]
        a = self.earlier([{"ref": TAP["key"], "kind": "tap", "phase": "enroute", "word": "Flying", "goals": [1, 3]},
                          {"ref": "agent:Claude · t · 1", "kind": "master", "state": "here",
                           "progress": {"current": 0, "steps": steps}}], cid=1)
        b = self.earlier([{"ref": "agent:Claude · t · 1", "kind": "master", "state": "starting",
                           "progress": {"current": 1, "steps": steps}}], cid=2)
        c = self.fill({"line": "L", "items": [{"ref": TAP["key"]}, {"ref": "Claude · t · 1"},
                                              {"ref": "project:tapspace"}]}, thread=[a, b])
        tap_, master, project = c["items"]
        self.assertEqual(tap_["was"], {"phase": "enroute", "word": "Flying", "goals": [1, 3]})
        self.assertEqual(master["was"], {"state": "starting", "current": 1})
        self.assertNotIn("was", project)
        self.assertNotIn("was", self.fill({"line": "L", "items": [{"ref": TAP["key"]}]})["items"][0])

    def test_a_master_with_new_steps_keeps_only_its_state(self):
        old = self.earlier([{"ref": "agent:Claude · t · 1", "kind": "master", "state": "here",
                             "progress": {"current": 1, "steps": ["Audit", "Spec"]}}])
        c = self.fill({"line": "L", "items": [{"ref": "Claude · t · 1"}]}, thread=[old])
        self.assertEqual(c["items"][0]["was"], {"state": "here"})

    def test_visual_from(self):
        prog = lambda d, t: {"kind": "progress", "done": d, "total": t}
        muddi = lambda v: {"line": "L", "face": "Muddi", "visual": v}
        last = self.earlier([], visual=prog(4, 5))
        self.assertEqual(self.fill(muddi(prog(2, 3)), thread=[last])["visual"],
                         {"kind": "progress", "done": 2, "total": 3, "from": 3})        # clamped to the new total
        self.assertEqual(self.fill(muddi(prog(4, 5)), thread=[self.earlier([], visual=prog(1, 5))])["visual"]["from"], 1)
        other_face = self.earlier([], face="agent:Claude · t · 1", visual=prog(1, 5))
        self.assertNotIn("from", self.fill(muddi(prog(2, 5)), thread=[other_face])["visual"])
        word = self.earlier([], visual={"kind": "word", "text": "Landed"}, cid=2)
        self.assertNotIn("from", self.fill(muddi(prog(2, 5)), thread=[last, word])["visual"])
        self.assertNotIn("from", self.fill(muddi(prog(2, 5)))["visual"])
        # cards with no face (several items or none) follow each other, never a card that had one
        faceless = self.earlier([], face=None, visual=prog(1, 5))
        self.assertEqual(self.fill({"line": "L", "visual": prog(3, 5)}, thread=[faceless])["visual"]["from"], 1)
        self.assertNotIn("from", self.fill({"line": "L", "visual": prog(3, 5)}, thread=[last])["visual"])
        self.assertNotIn("from", self.fill(muddi(prog(3, 5)), thread=[faceless])["visual"])

    def test_the_accept_names_its_tap_in_faces(self):
        c = self.fill({"line": "Ready to accept", "action": {"kind": "tap_accept", "tap": "fixture-app#7"}})
        self.assertEqual(c["action"], {"kind": "tap_accept", "tap": TAP["key"]})
        self.assertEqual(c["faces"], {TAP["key"]: {"kind": "tap", "title": TAP_TITLE, "initial": "F", "phase": "landed"}})
        self.assertIsNone(c["face"])
        self.assertEqual(c["items"], [])

    def test_a_card_with_no_subject_names_only_its_items(self):
        c = self.fill({"line": "L", "items": [{"ref": TAP["key"]}, {"ref": "Claude · t · 1"}, {"ref": "project:tapspace"}]})
        self.assertIsNone(c["face"])
        self.assertEqual(set(c["faces"]), {TAP["key"], "agent:Claude · t · 1", "project:tapspace"})   # not the sender
        self.assertEqual(self.fill({"line": "L"})["faces"], {})
        self.assertEqual(set(self.fill({"line": "L", "items": [{"ref": TAP["key"]}]})["faces"]), {TAP["key"]})

    def test_a_name_rides_beside_the_title_it_stands_in_for(self):
        c = self.fill({"line": "L", "items": [{"ref": TAP["key"], "name": "Lineup", "note": "Fixed"},
                                              {"ref": "Claude · t · 1", "name": "Cards"}]})
        self.assertEqual(c["items"], [
            {"ref": TAP["key"], "name": "Lineup", "note": "Fixed", "kind": "tap", "title": TAP_TITLE, "phase": "landed",
             "word": "Landed", "goals": [2, 3]},
            {"ref": "agent:Claude · t · 1", "name": "Cards", "kind": "master", "title": "Chat update cards",
             "state": "thinking", "progress": {"current": 2, "steps": ["Audit", "Spec", "Build"]}}])
        self.assertEqual(c["faces"][TAP["key"]]["title"], TAP_TITLE)        # faces keep the title; clients pick the name


class CardApiTests(Taps):
    """POST /api/chat/reply with a card: stored, served, pushed, refused whole."""

    def test_a_card_is_stored_served_and_pushed(self):
        a = name("card")
        join(a)
        say(a, "How is it going?")
        before = thread(a)
        line = "Three taps moved, two need your eyes"
        code, r = send(a, {"line": line, "images": [{"label": "Grid"}]}, images=[B64])
        self.assertEqual(code, 200, r)
        m = r["message"]
        self.assertEqual(set(m), {"id", "agent", "role", "body", "ts", "images", "card"})
        self.assertEqual((m["agent"], m["role"], m["body"]), (a, "master", line))
        self.assertRegex(m["images"][0], r"^/chat-media/[0-9a-f]{12}\.png$")
        self.assertEqual((S.DATA / m["images"][0].lstrip("/")).read_bytes(), PNG_BYTES)
        self.assertEqual(m["card"]["images"], [{"label": "Grid"}])
        self.assertEqual(thread(a), before + [m])                   # served verbatim; nothing earlier changed
        self.assertEqual(self.pushes, [((row(a)["title"], line), {"key": "agent:" + a})])

    def test_the_face_is_the_subject_never_the_sender(self):
        a, b = name("subject"), name("other")
        join(a)
        join(b)
        many = sent(a, {"line": "Three moved", "items": [{"ref": TAP["key"]}, {"ref": b}, {"ref": "project:tapspace"}]})
        self.assertIsNone(many["card"]["face"])
        self.assertEqual(set(many["card"]["faces"]), {TAP["key"], "agent:" + b, "project:tapspace"})
        one = sent(a, {"line": "Lineup landed", "items": [{"ref": "fixture-app#7"}]})
        self.assertEqual((one["card"]["face"], set(one["card"]["faces"])), (TAP["key"], {TAP["key"]}))
        own = sent(a, {"line": "About me", "face": a})                       # a given face still leads
        self.assertEqual(own["card"]["face"], "agent:" + a)
        self.assertEqual(thread(a), [many, one, own])                       # no face is stored and served as null

    def test_a_sent_body_is_kept_on_one_line(self):
        a = name("body")
        join(a)
        self.assertEqual(sent(a, {"line": "L"})["body"], "L")
        code, r = call("/api/chat/reply", {"agent": a, "body": "a\n\nb  c", "card": {"line": "L"}})
        self.assertEqual((code, r["message"]["body"], r["message"]["card"]["line"]), (200, "a / b c", "L"))

    def test_card_only_post_and_the_old_empty_rule(self):
        a = name("empty")
        join(a)
        self.assertEqual(call("/api/chat/reply", {"agent": a, "card": {"line": "Only a card"}})[0], 200)
        self.assertEqual(call("/api/chat", {"agent": "x"}), (400, {"error": "empty"}))
        self.assertEqual(call("/api/chat/reply", {"agent": a, "answer": {"card": 1, "image": 0, "verdict": "approve"}}),
                         (400, {"error": "empty"}))
        self.assertEqual(call("/api/chat", {"agent": a, "card": {"line": "Not here"}}), (400, {"error": "empty"}))

    def test_only_from_this_mac(self):
        a = name("mac")
        join(a)
        for kw, err in [({"headers": REMOTE}, "only from this Mac"), ({"ctype": "text/plain"}, "forbidden"),
                        ({"headers": {"Origin": "https://example.com"}}, "forbidden")]:
            with self.subTest(kw=kw):
                code, r = call("/api/chat/reply", {"agent": a, "card": {"line": "L"}}, **kw)
                self.assertEqual((code, r["error"]), (403, err))
        self.assertEqual(thread(a), [])

    def test_a_refused_card_writes_nothing(self):
        a = name("refused")
        join(a)
        say(a, "hello")
        files, rows = media(), thread(a)
        code, r = send(a, {"line": "Say \"hi\""})
        self.assertEqual(code, 400)
        self.assertIn("line: one plain line", r["error"])
        S.CARD_MAX = 50
        code, r = send(a, {"line": "L"}, images=[B64])
        self.assertEqual(code, 400)
        self.assertIn("over", r["error"])
        S.CARD_MAX = self.saved["CARD_MAX"]
        code, r = send(a, {"line": "L"}, images=[B64, {"ext": "bmp", "b64": PNG}])
        self.assertEqual(code, 400)
        self.assertIn("unsupported image type", r["error"])
        self.assertEqual((media(), thread(a)), (files, rows))
        self.assertEqual(self.pushes, [])

    def test_proof_images_are_copied_into_chat_media(self):
        a = name("proof")
        join(a)
        m = sent(a, {"line": "Proof is in"}, images=[{"proof": {"key": TAP["key"], "i": 0}}])
        self.assertRegex(m["images"][0], r"^/chat-media/[0-9a-f]{12}\.png$")
        self.assertEqual((S.DATA / m["images"][0].lstrip("/")).read_bytes(), PNG_BYTES)
        self.assertEqual(m["card"]["images"], [{"label": "MLB page reads Lineup", "from": {"tap": TAP["key"], "i": 0}}])
        self.assertEqual(self.proofs, [(TAP["key"], 0)])
        self.assertEqual(self.gh_calls(), [])

    def test_proof_while_taps_load_and_when_github_fails(self):
        a = name("loading")
        join(a)
        files, rows = media(), thread(a)
        S.TAPS["fetched"] = 0
        code, r = send(a, {"line": "L"}, images=[{"proof": {"key": TAP["key"], "i": 0}}])
        self.assertEqual((code, r.get("retry")), (503, True))
        self.assertIn("still loading", r["error"])
        S.TAPS["fetched"] = time.time()
        code, r = send(a, {"line": "L"}, images=[{"proof": {"key": TAP["key"], "i": 5}}])
        self.assertEqual(code, 400)
        self.assertIn("tap.py tap-read", r["error"])

        def boom(key, i):
            raise RuntimeError("boom")
        S._proof_image = boom
        code, r = send(a, {"line": "L"}, images=[B64, {"proof": {"key": TAP["key"], "i": 0}}])
        self.assertEqual(code, 502)
        self.assertIn("GitHub did not serve it", r["error"])
        self.assertEqual(send(a, {"line": "L"}, images=[{"proof": {"key": "nope", "i": 0}}])[0], 400)
        self.assertEqual(send(a, {"line": "L"}, images=[{"proof": {"key": TAP["key"], "i": "0"}}])[0], 400)
        self.assertEqual((media(), thread(a)), (files, rows))

    def test_a_card_wakes_chat_and_agents(self):
        a = name("wake")
        join(a)
        cur = call("/api/wait?since=0&timeout=0")[1]["cursor"]
        sent(a, {"line": "L"})
        self.assertLessEqual({"chat", "agents"}, set(call(f"/api/wait?since={cur}&timeout=0")[1]["changed"]))

    def test_a_card_with_an_accept(self):
        a = name("accept")
        join(a)
        m = sent(a, {"line": "The lineup fix is ready to accept", "items": [{"ref": TAP["key"]}],
                     "action": {"kind": "tap_accept", "tap": "fixture-app#7"}},
                 images=[{"proof": {"key": TAP["key"], "i": 0}}])
        self.assertEqual(m["card"]["action"], {"kind": "tap_accept", "tap": TAP["key"]})
        self.assertEqual(m["card"]["faces"][TAP["key"]]["title"], TAP_TITLE)
        rows = thread(a)
        code, r = send(a, {"line": "L", "action": {"kind": "tap_accept", "tap": "someone/else#1"}})
        self.assertEqual(code, 400)
        self.assertIn("action tap", r["error"])
        self.assertEqual(thread(a), rows)
        self.assertEqual(self.gh_calls(), [])

    def test_a_tap_item_carries_its_initial(self):
        S.TAPS["me"] = "hperwin"
        iss = {"author": {"login": "hperwin"}, "title": "T", "body": "- [ ] open", "labels": [{"name": "flightdeck"}],
               "state": "OPEN", "comments": [], "url": "u", "createdAt": "2026-10-16T19:00:00Z", "closedAt": None}
        gh = S._residue("SnappStats/snapp-ios#1583", iss)
        self.assertEqual(S._item("SnappStats/snapp-ios#1583", {"phase": "landed", "label": "✔ ready for sign-off",
                                                               "title": "T", "goalList": [], "read": True},
                                 gh, None)["initial"], "S")
        self.assertEqual(self.gh_calls(), [])

    def test_a_short_name_is_stored_beside_the_title(self):
        a = name("short")
        join(a)
        m = sent(a, {"line": "The beta look is ready",
                     "items": [{"ref": "fixture-app#7", "name": "Beta look", "note": "Ready for your look"}]})
        it = m["card"]["items"][0]
        self.assertEqual((it["name"], it["title"], it["note"]), ("Beta look", TAP_TITLE, "Ready for your look"))
        self.assertEqual(m["card"]["face"], TAP["key"])                     # the one item is the subject
        self.assertEqual(thread(a), [m])
        for bad in ("x" * 41, 'The "beta" look'):
            with self.subTest(name=bad):
                code, r = send(a, {"line": "L", "items": [{"ref": TAP["key"], "name": bad}]})
                self.assertEqual((code, r), (400, {"error": "item name: one plain line up to 40 characters"}))
        self.assertEqual(thread(a), [m])


class AnswerApiTests(Taps):
    """POST /api/chat with an answer: the server writes its body, dedupes by nonce, never reaches gh."""

    def card_with_images(self, a, labels=("Lineup card",), ask=None, proof=False):
        images = [{"proof": {"key": TAP["key"], "i": 0}}] if proof else [B64] * len(labels)
        c = {"line": "Fleet moved", "images": [{"label": x} for x in labels]}
        if ask:
            c["ask"] = ask
        return sent(a, c, images=images)

    def test_approve_writes_the_body_and_the_inbox_line(self):
        a = name("approve")
        join(a)
        c = self.card_with_images(a)
        code, r = answer(a, {"card": c["id"], "image": 0, "verdict": "approve"}, nonce="n1", body="ignored")
        self.assertEqual(code, 200, r)
        m = r["message"]
        self.assertEqual((m["role"], m["body"], m["nonce"], m["answer"]),
                         ("you", "Approved: Lineup card", "n1", {"card": c["id"], "image": 0, "verdict": "approve"}))
        self.assertNotIn("images", m)
        last = inbox()[-1]
        self.assertEqual({k: last[k] for k in ("kind", "agent", "id", "message", "images", "answer")},
                         {"kind": "tapspace-chat", "agent": a, "id": m["id"], "message": "Approved: Lineup card",
                          "images": [], "answer": m["answer"]})
        code, r = answer(a, {"card": c["id"], "image": 0, "verdict": "change", "note": "bigger\nscore"})
        self.assertEqual((code, r["message"]["body"]), (200, "Not quite: Lineup card: bigger / score"))

    def test_a_retry_with_the_same_nonce_gets_the_same_row(self):
        a = name("retry")
        join(a)
        c = self.card_with_images(a)
        first = answer(a, {"card": c["id"], "image": 0, "verdict": "approve"}, nonce="same")[1]["message"]
        again = answer(a, {"card": c["id"], "image": 0, "verdict": "approve"}, nonce="same")[1]["message"]
        self.assertEqual(first, again)
        self.assertEqual(sum(1 for r in thread(a) if r.get("answer")), 1)

    def test_refusals(self):
        a, b = name("ans"), name("other")
        join(a)
        join(b)
        mine, theirs = self.card_with_images(a), self.card_with_images(b)
        plain = call("/api/chat/reply", {"agent": a, "body": "a plain reply"})[1]["message"]
        rows = thread(a)
        code, r = answer(a, {"card": theirs["id"], "image": 0, "verdict": "approve"})
        self.assertEqual(code, 400)
        self.assertIn("no card by that id", r["error"])
        self.assertEqual(answer(a, {"card": plain["id"], "image": 0, "verdict": "approve"})[0], 400)
        code, r = answer(a, {"card": mine["id"], "image": 0, "verdict": "approve"}, images=[B64])
        self.assertEqual((code, r["error"]), (400, "an answer carries no images"))
        self.assertEqual(answer(a, {"card": mine["id"], "image": 3, "verdict": "approve"})[0], 400)
        self.assertEqual(thread(a), rows)
        self.assertEqual(answer("Claude · nobody here", {"card": mine["id"], "image": 0, "verdict": "approve"}),
                         (404, {"error": "no agent by that name has joined"}))

    def test_the_phone_may_answer(self):
        a = name("phone")
        join(a)
        c = self.card_with_images(a)
        code, r = call("/api/chat", {"agent": a, "nonce": "p1", "answer": {"card": c["id"], "image": 0, "verdict": "approve"}},
                       headers=REMOTE)
        self.assertEqual((code, r["message"]["body"]), (200, "Approved: Lineup card"))

    def test_an_ask_answer(self):
        a = name("ask")
        join(a)
        c = sent(a, {"line": "Ready?", "ask": {"q": "Which ships first?", "checks": ["Lineup", "Box score"], "text": True}})
        code, r = answer(a, {"card": c["id"], "checks": ["c2"], "text": "box score first"})
        self.assertEqual((code, r["message"]["body"], r["message"]["answer"]),
                         (200, "Which ships first? Box score. box score first",
                          {"card": c["id"], "checks": ["c2"], "text": "box score first"}))

    def test_approving_a_landed_taps_proof_makes_no_gh_call(self):
        # R5: an Approve on a landed tap's proof image is an image approval, never the tap's Accept
        a = name("r5")
        join(a)
        c = self.card_with_images(a, proof=True)
        self.assertEqual(c["card"]["images"][0]["from"], {"tap": TAP["key"], "i": 0})
        self.assertEqual(answer(a, {"card": c["id"], "image": 0, "verdict": "approve"})[0], 200)
        self.assertEqual(answer(a, {"card": c["id"], "image": 0, "verdict": "change", "note": "crop it"})[0], 200)
        self.assertFalse(self.gh_log.exists())                     # zero gh calls of any kind
        self.assertEqual([e for e in self.new_inbox() if e["kind"] == "tapspace-tap-accept"], [])
        self.assertEqual(S.TAPS["items"][0]["phase"], "landed")

    def test_not_quite_on_a_cards_accept(self):
        a = name("deny")
        join(a)
        c = sent(a, {"line": "The lineup fix is ready to accept", "action": {"kind": "tap_accept", "tap": TAP["key"]}},
                 images=[{"proof": {"key": TAP["key"], "i": 0}}])
        code, r = answer(a, {"card": c["id"], "action": "deny", "note": "the score\nis wrong"})
        self.assertEqual(code, 200, r)
        self.assertEqual((r["message"]["body"], r["message"]["answer"]),
                         (f"Not quite: {TAP_TITLE}: the score / is wrong",
                          {"card": c["id"], "action": "deny", "note": "the score\nis wrong"}))
        rows = thread(a)
        code, r = answer(a, {"card": c["id"], "action": "accept"})        # Accept is never a chat message
        self.assertEqual(code, 400)
        self.assertIn("/api/taps/accept", r["error"])
        self.assertEqual(thread(a), rows)
        got = join(a)[1]["pending"]
        self.assertEqual(got[-1]["answer"], {"card": c["id"], "action": "deny", "note": "the score\nis wrong",
                                             "line": "The lineup fix is ready to accept", "tap": TAP["key"]})
        self.assertFalse(self.gh_log.exists())

    def test_not_quite_names_the_tap_as_the_card_does(self):
        a = name("denyname")
        join(a)
        c = sent(a, {"line": "The beta look is ready. Accept it?", "items": [{"ref": "fixture-app#7", "name": "Beta look"}],
                     "action": {"kind": "tap_accept", "tap": TAP["key"]}}, images=[{"proof": {"key": TAP["key"], "i": 0}}])
        code, r = answer(a, {"card": c["id"], "action": "deny", "note": "Not yet, the score is small"})
        self.assertEqual((code, r["message"]["body"]), (200, "Not quite: Beta look: Not yet, the score is small"))
        self.assertEqual(join(a)[1]["pending"][-1]["body"], "Not quite: Beta look: Not yet, the score is small")


class AcceptFromCardTests(Taps):
    """POST /api/taps/accept {key, card}: the same caller check as today, the card checked before gh is called,
    and once GitHub has the Accept, the card's answer row in its thread. The socket's owner and the process
    table are fakes, like TapButtonTests; gh is the recording fake."""
    START = "Fri Oct 16 13:00:00 2026"

    def setUp(self):
        super().setUp()
        self.saved_peer = {k: getattr(S, k) for k in ("_socket_owners", "_procs")}
        self.owners, self.table = set(), {}
        S._socket_owners = lambda host, port: None if self.owners is None else set(self.owners)
        S._procs = lambda: dict(self.table)

    def tearDown(self):
        for k, v in self.saved_peer.items():
            setattr(S, k, v)
        super().tearDown()

    def peer(self, *chain):
        """The socket's owner and the processes above it, nearest first, each a comm path; launchd on top."""
        self.table.clear()
        pids = [990100 + i for i in range(len(chain))]
        for i, (pid, comm) in enumerate(zip(pids, chain)):
            self.table[pid] = (pids[i + 1] if i + 1 < len(pids) else 1, self.START, comm, "S")
        self.owners = {pids[0]}

    def writes(self):
        return [r[:2] for r in self.gh_calls() if r[:2] in (["issue", "close"], ["issue", "edit"])]

    def accept_card(self, a):
        return sent(a, {"line": "The lineup fix is ready to accept", "items": [{"ref": TAP["key"]}],
                        "action": {"kind": "tap_accept", "tap": TAP["key"]}},
                    images=[{"proof": {"key": TAP["key"], "i": 0}}])

    def press(self, body, headers=None):
        return call("/api/taps/accept", body, headers=headers)

    def test_accept_from_the_card_answers_it_in_its_thread(self):
        a = name("accepted")
        join(a)
        c = self.accept_card(a)
        self.peer("/Applications/TapSpace.app/Contents/MacOS/TapSpace")
        code, r = self.press({"key": TAP["key"], "card": c["id"]})
        self.assertEqual(code, 200, r)
        m = r["message"]
        self.assertEqual((r["ok"], m["agent"], m["role"], m["body"], m["answer"]),
                         (True, a, "you", f"Accepted: {TAP_TITLE}", {"card": c["id"], "action": "accept"}))
        self.assertEqual(self.writes(), [["issue", "close"]])
        self.assertEqual(thread(a)[-1], m)
        kinds = [e["kind"] for e in self.new_inbox()]
        self.assertIn("tapspace-tap-accept", kinds)
        chat = [e for e in self.new_inbox() if e["kind"] == "tapspace-chat"][-1]
        self.assertEqual((chat["agent"], chat["id"], chat["answer"]), (a, m["id"], m["answer"]))
        got = join(a)[1]["pending"][-1]
        self.assertEqual((got["id"], got["answer"]), (m["id"], {"card": c["id"], "action": "accept",
                                                               "line": "The lineup fix is ready to accept",
                                                               "tap": TAP["key"]}))
        code, r = self.press({"key": TAP["key"], "card": c["id"]}, headers={"Origin": BASE})   # pressed again
        self.assertEqual((code, r["message"]["id"]), (200, m["id"]))
        self.assertEqual(sum(1 for x in thread(a) if (x.get("answer") or {}).get("action") == "accept"), 1)

    def test_accept_names_the_tap_as_the_card_does(self):
        a = name("acceptname")
        join(a)
        c = sent(a, {"line": "The lineup fix is ready. Accept it?", "items": [{"ref": TAP["key"], "name": "Lineup cards"}],
                     "action": {"kind": "tap_accept", "tap": TAP["key"]}}, images=[{"proof": {"key": TAP["key"], "i": 0}}])
        self.peer("/Applications/TapSpace.app/Contents/MacOS/TapSpace")
        code, r = self.press({"key": TAP["key"], "card": c["id"]})
        self.assertEqual((code, r["message"]["body"]), (200, "Accepted: Lineup cards"))
        self.assertEqual([e["message"] for e in self.new_inbox() if e["kind"] == "tapspace-chat"], ["Accepted: Lineup cards"])

    def test_an_agent_caller_is_refused_and_the_card_is_not_answered(self):
        a = name("agentcall")
        join(a)
        c = self.accept_card(a)
        rows = thread(a)
        self.peer("/usr/bin/curl", "/bin/zsh", "claude", "/Applications/Ghostty.app/Contents/MacOS/ghostty")
        code, r = self.press({"key": TAP["key"], "card": c["id"]})
        self.assertEqual(code, 403)
        self.assertIn("inside claude", r["error"])
        self.peer("/Applications/TapSpace.app/Contents/MacOS/TapSpace")
        code, r = self.press({"key": TAP["key"], "card": c["id"]}, headers=ident(sid(), 990999))
        self.assertEqual(code, 403)
        self.assertIn("tap.py inside a Claude session", r["error"])
        self.assertEqual(thread(a), rows)
        self.assertFalse(self.gh_log.exists())

    def test_a_wrong_card_accepts_nothing(self):
        a = name("wrong")
        join(a)
        plain_card = sent(a, {"line": "No accept on this one"})
        c = self.accept_card(a)
        reply = call("/api/chat/reply", {"agent": a, "body": "plain"})[1]["message"]
        rows = thread(a)
        self.peer("/Applications/TapSpace.app/Contents/MacOS/TapSpace")
        for body, why in [({"key": TAP["key"], "card": plain_card["id"]}, "no Accept"),
                          ({"key": TAP["key"], "card": reply["id"]}, "no card by that id"),
                          ({"key": TAP["key"], "card": 999999}, "no card by that id"),
                          ({"key": TAP["key"], "card": str(c["id"])}, "message id"),
                          ({"key": "hperwin/fu-fixture#7", "card": c["id"]}, "another tap")]:
            with self.subTest(body=body):
                code, r = self.press(body)
                self.assertEqual(code, 400)
                self.assertIn(why, r["error"])
        self.assertEqual(thread(a), rows)
        self.assertFalse(self.gh_log.exists())                      # gh never even ran

    def test_accept_without_a_card_is_unchanged(self):
        a = name("bare")
        join(a)
        self.accept_card(a)
        rows = thread(a)
        self.peer("/Applications/TapSpace.app/Contents/MacOS/TapSpace")
        self.assertEqual(self.press({"key": TAP["key"]}), (200, {"ok": True}))
        self.assertEqual(self.writes(), [["issue", "close"]])
        self.assertEqual(thread(a), rows)

    def test_two_presses_at_once_write_one_row(self):
        a = name("twice")
        join(a)
        c = self.accept_card(a)
        reads = S._chat_rows
        self.addCleanup(setattr, S, "_chat_rows", reads)

        def slow(*args):                       # widen the gap between reading the thread and writing the row
            got = reads(*args)
            time.sleep(0.05)
            return got
        S._chat_rows = slow
        got, gate = [], threading.Barrier(4)

        def press():
            gate.wait()
            got.append(S._accepted(c)["id"])
        presses = [threading.Thread(target=press) for _ in range(4)]
        for t in presses:
            t.start()
        for t in presses:
            t.join(10)
        S._chat_rows = reads
        rows = [x for x in thread(a) if (x.get("answer") or {}).get("action") == "accept"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(got, [rows[0]["id"]] * 4)
        self.assertEqual([e["id"] for e in self.new_inbox() if e["kind"] == "tapspace-chat"], [rows[0]["id"]])


class ListenTests(Taps):
    """GET /api/chat/listen: the heard cursor (R6) and the answer fields it adds."""

    def test_a_card_never_hides_a_message_the_listener_was_not_handed(self):
        a = name("race")
        join(a)
        first = say(a, "first")
        self.assertEqual(ids(join(a)), [first])
        second = say(a, "Approve this?")                       # arrives while the agent is busy
        sent(a, {"line": "Fleet moved"})                       # its proactive card, posted before its next listen
        self.assertEqual(ids(join(a)), [second])
        self.assertEqual(ids(join(a)), [])
        self.assertEqual(row(a)["state"], "here")              # Thinking keeps the old rule

    def test_unanswered_messages_come_back_until_the_agent_answers(self):
        a = name("again")
        join(a)
        m = say(a, "still there?")
        self.assertEqual((ids(join(a)), ids(join(a))), ([m], [m]))
        self.assertEqual(row(a)["state"], "thinking")
        call("/api/chat/reply", {"agent": a, "body": "yes"})
        self.assertEqual(ids(join(a)), [])

    def test_an_agent_from_before_heard_keeps_todays_rule(self):
        a = name("upgrade")
        join(a)
        say(a, "one")
        call("/api/chat/reply", {"agent": a, "body": "answered one"})
        two = say(a, "two")
        with S._lock:
            ag = S._agents()
            ag[a].pop("heard", None)
            S._agents_write(ag)
        self.assertEqual(ids(join(a)), [two])
        self.assertEqual(S._agents()[a]["heard"], two)

    def test_heard_never_moves_back(self):
        a = name("cross")
        join(a)
        one = say(a, "one")
        self.assertEqual(ids(join(a)), [one])
        sent(a, {"line": "Fleet moved"})                       # its card, posted before its next listen
        S._agent_touch(a, heard=one - 1)                       # a poll that read the rows before `one` writes last
        self.assertEqual(S._agents()[a]["heard"], one)
        self.assertEqual(ids(join(a)), [])                     # so `one` is never handed over twice
        two = say(a, "two")
        self.assertEqual(ids(join(a)), [two])                  # and it still moves forward
        self.assertEqual(S._agents()[a]["heard"], two)

    def test_listen_hands_an_answer_over_with_its_card_line_label_and_file(self):
        a = name("handover")
        join(a)
        c = sent(a, {"line": "Fleet moved", "images": [{"label": "Grid"}],
                     "ask": {"q": "Ship it?", "checks": ["Yes"], "text": False}}, images=[B64])
        img = answer(a, {"card": c["id"], "image": 0, "verdict": "approve"})[1]["message"]
        ask = answer(a, {"card": c["id"], "checks": ["c1"]})[1]["message"]
        got = {r["id"]: r for r in join(a)[1]["pending"]}
        f = pathlib.Path(S.DATA / c["images"][0].lstrip("/"))
        self.assertEqual(got[img["id"]]["answer"], {"card": c["id"], "image": 0, "verdict": "approve",
                                                    "line": "Fleet moved", "label": "Grid", "file": str(f)})
        self.assertTrue(f.is_file() and str(f.resolve()).startswith(str(S.MEDIA.resolve()) + "/"))
        self.assertEqual(got[ask["id"]]["answer"], {"card": c["id"], "checks": ["c1"], "line": "Fleet moved"})
        stored = {r["id"]: r for r in thread(a)}
        self.assertEqual(stored[img["id"]]["answer"], {"card": c["id"], "image": 0, "verdict": "approve"})
        self.assertEqual(stored[ask["id"]]["answer"], {"card": c["id"], "checks": ["c1"]})


class TapCardWaitTests(Taps):
    """tap.py card waits up to 120 s for the server's answer, which reads the proof images from GitHub first; its
    other calls keep 30 s. tap.py runs in-process against this module's server."""

    def test_the_card_post_waits_up_to_120_s(self):
        spec = importlib.util.spec_from_file_location("tapspace_tap_card_wait_under_test", ROOT / "tap.py")
        T = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(T)
        T.BASE, T._who = BASE, lambda: {}                       # this server, no Claude session headers
        a = name("wait")
        join(a)
        real, waits = urllib.request.urlopen, []
        self.addCleanup(setattr, urllib.request, "urlopen", real)

        def spy(req, timeout=None, **k):
            waits.append((urllib.parse.urlsplit(req.full_url).path, timeout))
            return real(req, timeout=timeout, **k)
        urllib.request.urlopen = spy
        r = T._card_try(a, {"line": "Two proofs to look at"}, [], [], [(TAP["key"], None)])
        urllib.request.urlopen = real
        self.assertTrue(r.get("ok"), r)
        self.assertEqual(len(r["message"]["images"]), 2)
        self.assertEqual(waits, [("/api/taps", 30), ("/api/chat/reply", 120)])


class LookTests(unittest.TestCase):
    """Server-assigned looks: the lowest free one, the lead holds none, a stored look never moves."""

    def rows(self, n, **extra):
        now = time.time()
        return {f"m{i}": {"name": f"m{i}", "first": i, "seen": now, **extra} for i in range(1, n + 1)}

    def test_lowest_free_then_least_held(self):
        a = self.rows(19)
        S._looks(a)
        self.assertEqual([a[f"m{i}"]["look"] for i in range(1, 20)], list(range(1, 18)) + [1, 2])

    def test_the_lead_gets_none_and_holds_none(self):
        a = {"lead": {"name": "lead", "role": "lead", "look": 1, "first": 0, "seen": time.time()},
             "bare": {"name": "bare", "role": "lead", "first": 0, "seen": time.time()}, **self.rows(1)}
        S._looks(a)
        self.assertEqual(a["m1"]["look"], 1)
        self.assertNotIn("look", a["bare"])
        self.assertEqual((S._look(a["lead"]), S._look(a["m1"])), (0, 1))

    def test_stale_and_stopped_rows_let_their_look_go(self):
        now = time.time()
        a = {"old": {"name": "old", "look": 1, "first": 0, "seen": now - 2 * 86400},
             "gone": {"name": "gone", "look": 2, "first": 0, "seen": now, "stopped": now}, **self.rows(2)}
        S._looks(a)
        self.assertEqual((a["m1"]["look"], a["m2"]["look"]), (1, 2))
        self.assertEqual((a["old"]["look"], a["gone"]["look"]), (1, 2))

    def test_a_stored_look_never_moves(self):
        a = {"kept": {"name": "kept", "look": 9, "first": 5, "seen": time.time()}, **self.rows(1)}
        S._looks(a)
        self.assertEqual((a["kept"]["look"], a["m1"]["look"]), (9, 1))

    def test_served_on_every_row_and_the_lead_is_0(self):
        x, y = name("look"), name("look")
        join(x)
        join(y)
        for who in (x, y):
            got = row(who)["look"]
            self.assertIn(got, range(1, 18))
            self.assertEqual(got, S._agents()[who]["look"])
        lead = name("lead")
        join(lead)
        self.assertEqual(call("/api/agents/lead", {"name": lead})[0], 200)
        self.assertEqual(row(lead)["look"], 0)


class FleetTests(Taps):
    """GET /api/agents fleet: who is in it and in what order, computed once on the server."""

    def at(self, hhmm):
        return S._epoch(f"2026-10-16T{hhmm}:00Z")

    def master(self, n, state, done=None, total=None, last="19:00", role=None):
        return {"name": n, "state": state, "title": n.title(), "look": 3, "last": self.at(last), "role": role,
                "progress": {"goal": "G", "steps": [str(i) for i in range(total)], "current": done, "updated": 0}
                if total else None}

    def ask_card(self, agent, cid, **card):
        return {"id": cid, "agent": agent, "role": "master", "ts": 1.0,
                "card": {"v": 1, "line": "x", "face": "agent:" + agent, "items": [], "images": [], **card}}

    def you(self, agent, cid, rid, **a):
        return {"id": rid, "agent": agent, "role": "you", "ts": 2.0, "body": "b", "answer": {"card": cid, **a}}

    def test_membership_and_order(self):
        rows = [self.master("lead", "here", role="lead"), self.master("m1", "thinking", 2, 7, "19:45"),
                self.master("m2", "here", last="19:35"), self.master("m3", "away", 3, 3, "19:58"),
                self.master("m4", "away", 1, 4, "19:20"), self.master("m5", "here", last="19:40")]
        taps = [tap("a/app#1", "holding", "2026-10-16T19:50:00Z", "Holding one"),
                tap("a/app#2", "landed", "2026-10-16T19:30:00Z"), tap("a/app#3", "enroute", "2026-10-16T19:55:00Z"),
                tap("a/app#4", "proposed", "2026-10-16T19:25:00Z"), tap("a/app#5", "done", "2026-10-16T19:59:00Z"),
                dict(tap("a/app#6", "done", "2026-10-16T19:59:00Z"), outcome="aborted")]   # aborted: done too
        chat = [self.ask_card("m5", 10, ask={"q": "Q?", "checks": [{"id": "c1", "label": "A"}], "text": False})]
        f = S._fleet(rows, taps, chat)
        self.assertEqual([x["key"] for x in f], ["a/app#1", "agent:m5", "a/app#2", "a/app#3", "agent:m1",
                                                 "agent:m2", "a/app#4", "agent:m4"])
        self.assertEqual(f[4], {"key": "agent:m1", "kind": "master", "title": "M1", "look": 3, "state": "thinking",
                                "done": 2, "total": 7, "needs_you": False, "working": True})
        self.assertEqual(f[0], {"key": "a/app#1", "kind": "tap", "title": "Holding one", "initial": "A",
                                "phase": "holding", "word": "Needs you", "done": 1, "total": 3, "needs_you": True,
                                "working": False})
        self.assertEqual((f[5]["done"], f[5]["total"]), (0, 0))       # a master with no goal card

    def test_a_master_waits_on_you_until_each_part_is_answered(self):
        ask = {"q": "Q?", "checks": [{"id": "c1", "label": "A"}], "text": False}
        imgs = [{"label": "One"}, {"label": "Two"}]
        w = S._waits_on_you
        self.assertFalse(w([]))
        self.assertFalse(w([self.ask_card("m", 1)]))                                   # nothing to answer
        self.assertTrue(w([self.ask_card("m", 1, ask=ask)]))
        self.assertTrue(w([self.ask_card("m", 1, ask=ask), self.you("m", 1, 2, image=0, verdict="approve")]))
        self.assertFalse(w([self.ask_card("m", 1, ask=ask), self.you("m", 1, 2, checks=["c1"])]))
        self.assertTrue(w([self.ask_card("m", 1, images=imgs), self.you("m", 1, 2, image=0, verdict="approve")]))
        self.assertFalse(w([self.ask_card("m", 1, images=imgs), self.you("m", 1, 2, image=0, verdict="approve"),
                            self.you("m", 1, 3, image=1, verdict="change")]))
        older = [self.ask_card("m", 1, images=imgs), self.ask_card("m", 2)]               # only the newest card counts
        self.assertFalse(w(older))
        acc = self.ask_card("m", 1, images=imgs[:1], action={"kind": "tap_accept", "tap": TAP["key"]})
        self.assertTrue(w([acc]))
        self.assertFalse(w([acc, self.you("m", 1, 2, action="deny")]))
        self.assertFalse(w([acc, self.you("m", 1, 2, action="accept")]))
        self.assertTrue(w([acc, self.you("m", 1, 2, image=0, verdict="approve")]))       # the picture is the action's

    def test_only_the_newest_card_asks_and_an_accept_settles_with_its_tap(self):
        ask = {"q": "Q?", "checks": [{"id": "c1", "label": "A"}], "text": False}
        w = S._waits_on_you
        # a newer card leaves an older question behind, like its images (CONTRACT 18)
        moved_on = [self.ask_card("m", 1, ask=ask), self.ask_card("m", 2)]
        self.assertFalse(S._ask_open(moved_on))
        self.assertFalse(w(moved_on))
        self.assertTrue(w([self.ask_card("m", 1, ask=ask), self.you("m", 1, 2, checks=["c1"]), self.ask_card("m", 3, ask=ask)]))
        self.assertFalse(w([self.ask_card("m", 1, ask=ask), self.ask_card("m", 3, ask=ask), self.you("m", 3, 4, checks=[])]))
        # an Accept card is settled by its answer row, or once its tap is done, or has left with its outcome kept
        acc = self.ask_card("m", 5, images=[{"label": "One"}], action={"kind": "tap_accept", "tap": TAP["key"]})
        for phase, waits in (("landed", True), ("holding", True), ("done", False)):
            with self.subTest(phase=phase):
                self.assertEqual(w([acc], [tap(TAP["key"], phase, TAP["last"])]), waits)
        self.assertTrue(w([acc], [tap("hperwin/other-app#1", "done", TAP["last"])]))     # another tap says nothing
        self.assertTrue(w([acc], []))                                                     # not served: still waits
        self.assertFalse(w([acc], [], {TAP["key"]: "shipped"}))                         # left the board, its ending kept
        self.assertFalse(w([acc], [], {TAP["key"]: "aborted"}))
        self.assertTrue(w([acc], [tap(TAP["key"], "enroute", TAP["last"])], {TAP["key"]: "aborted"}))   # reopened
        # the fleet's needs_you reads the same rule
        rows = [self.master("lead", "here", role="lead"), self.master("m", "here", last="19:40")]
        f = lambda phase: next(x for x in S._fleet(rows, [tap(TAP["key"], phase, "2026-10-16T19:50:00Z")], [acc])
                               if x["key"] == "agent:m")
        self.assertTrue(f("landed")["needs_you"])
        self.assertFalse(f("done")["needs_you"])

    def test_the_leads_waiting_word_reads_the_same_rule(self):
        lead = name("waitlead")
        join(lead)
        self.assertEqual(call("/api/agents/lead", {"name": lead})[0], 200)
        sent(lead, {"line": "The lineup fix is ready to accept", "action": {"kind": "tap_accept", "tap": TAP["key"]}})
        self.addCleanup(setattr, S.activity, "of", S.activity.of)
        S.activity.of = lambda *a, waits, **k: {"waits": waits()}       # what the lead's activity is handed
        S.TAPS["items"] = [dict(TAP, phase="landed", phase_word="Landed")]
        self.assertEqual(row(lead)["activity"], {"waits": True})
        S.TAPS["items"] = [dict(TAP, phase="done", phase_word="Done")]       # Hayden accepted it on its tap card
        self.assertEqual(row(lead)["activity"], {"waits": False})

    def test_no_lead_no_fleet(self):
        self.assertEqual(S._fleet([self.master("m1", "thinking", 1, 3)], [tap("a/app#1", "holding", "2026-10-16T19:50:00Z")],
                                  []), [])

    def test_served_and_a_tap_change_wakes_agents(self):
        d = call("/api/agents")[1]
        self.assertIsInstance(d["fleet"], list)
        self.assertIn("focus", d)
        cur = call("/api/wait?since=0&timeout=0")[1]["cursor"]
        S._publish([dict(TAP, phase="done", phase_word="Done")])
        self.assertLessEqual({"taps", "agents"}, set(call(f"/api/wait?since={cur}&timeout=0")[1]["changed"]))


class FocusTests(Taps):
    """focus: the thread the reviewer's newest own message went to (CONTRACT section 6). A tap comment counts only
    when he sent it through TapSpace (the say route records it); an agent's own gh comment never does (section 18)."""

    def iso(self, ts):
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))

    def his(self, ts):
        """A tap comment the reviewer sent through TapSpace, as _residue reads it, recorded the way the say route does."""
        url = f"{TAP['url']}#issuecomment-{uuid.uuid4().int % 10 ** 9}"
        with S._lock:
            S.SAID.write_text(json.dumps({**S._said(), url: time.time()}))
        return {"who": "you", "ts": self.iso(ts), "via": "", "body": "go", "url": url}

    def test_the_newest_own_message_names_the_thread(self):
        t0 = S._epoch("2026-10-16T19:00:00Z")
        chat = [{"id": 1, "agent": "A", "role": "you", "ts": t0 + 10}, {"id": 2, "agent": "B", "role": "master", "ts": t0 + 50},
                {"id": 3, "agent": "B", "role": "you", "ts": t0 + 20}, {"id": 4, "agent": "C", "role": "system", "ts": t0 + 90}]
        msg = lambda who, ts, via="": {"who": who, "ts": self.iso(ts), "via": via, "body": "b",
                                       "url": f"{TAP['url']}#issuecomment-{int(ts)}"}
        said = {msg("you", t0 + 30)["url"], msg("you", t0 + 15)["url"]}           # what the say route recorded
        self.assertEqual(S._focus(chat, [], said), "agent:B")
        t = dict(TAP, messages=[msg("you", t0 + 30), msg("tap", t0 + 80), msg("you", t0 + 70, via="Muddi")])
        self.assertEqual(S._focus(chat, [t], said), TAP["key"])
        self.assertEqual(S._focus(chat, [dict(TAP, messages=[msg("you", t0 + 15)])], said), "agent:B")
        self.assertEqual(S._focus(chat[:1], [], said), "agent:A")
        self.assertIsNone(S._focus([], [dict(TAP, messages=[msg("tap", t0)])], said))
        self.assertIsNone(S._focus([chat[1], chat[3]], [], said))
        # an agent's own gh comment is signed in as him too, but no TapSpace surface sent it: never the focus
        self.assertEqual(S._focus(chat, [dict(TAP, messages=[msg("you", t0 + 95)])], said), "agent:B")

    def test_served_and_it_moves_with_each_message(self):
        a, b = name("focusa"), name("focusb")
        join(a)
        join(b)
        say(a, "hi a")
        self.assertEqual(call("/api/agents")[1]["focus"], "agent:" + a)
        c = sent(b, {"line": "L", "images": [{"label": "One"}]}, images=[B64])
        self.assertEqual(call("/api/agents")[1]["focus"], "agent:" + a)            # a card is not his message
        answer(b, {"card": c["id"], "image": 0, "verdict": "approve"})
        self.assertEqual(call("/api/agents")[1]["focus"], "agent:" + b)            # an answer is
        S.TAPS["items"] = [dict(TAP, messages=[self.his(time.time() + 60)])]
        self.assertEqual(call("/api/agents")[1]["focus"], TAP["key"])

    def test_a_tap_comment_that_moves_focus_wakes_agents(self):
        cur = call("/api/wait?since=0&timeout=0")[1]["cursor"]
        deadline = time.time() + 10
        while time.time() < deadline:                       # let the watch settle first
            r = call(f"/api/wait?since={cur}&timeout=1.5")[1]
            cur = r["cursor"]
            if "agents" not in r["changed"]:
                break
        # no write, no bump: only the watch's signature (focus) can notice this
        S.TAPS["items"] = [dict(TAP, messages=[self.his(time.time() + 3600)])]
        seen, deadline = set(), time.time() + 5
        while "agents" not in seen and time.time() < deadline:
            r = call(f"/api/wait?since={cur}&timeout=3")[1]
            seen.update(r["changed"])
            cur = r["cursor"]
        self.assertIn("agents", seen)

    def test_his_say_moves_focus_and_an_agents_own_comment_never_does(self):
        shim = pathlib.Path(TMP.name) / "gh-say"
        shim.mkdir(exist_ok=True)
        (shim / "gh").write_text(GH_SAY)
        (shim / "gh").chmod(0o755)
        S.GH = str(shim / "gh")
        key = "hperwin/fixture-say#9"
        self.addCleanup(S.HOT.pop, key, None)
        code, r = call("/api/taps/say", {"key": key, "body": "Ship the lineup first"})     # a TapSpace surface
        self.assertEqual(code, 200, r)
        his = r["url"]
        code, r = call("/api/taps/say", {"key": key, "body": "On it", "via": "Muddi"})     # tap.py tap-say
        self.assertEqual(code, 200, r)
        for_him = r["url"]
        self.assertIn(his, S._said())
        self.assertNotIn(for_him, S._said())
        self.assertEqual(S._via()[for_him], "Muddi")
        # the tap's thread as _residue reads it: his comment, Muddi's tap-say, then Muddi's own gh comment, signed in
        # as him too
        t0 = float(int(time.time()))                       # whole seconds, like GitHub's createdAt
        said = [("Ship the lineup first", his), ("On it", for_him),
                ("Accepted: all three goals", "https://github.com/hperwin/fixture-say/issues/9#issuecomment-999")]
        iss = {"author": {"login": "hperwin"}, "title": "A tap", "body": "- [ ] it works", "labels": [{"name": "flightdeck"}],
               "state": "OPEN", "url": "https://github.com/hperwin/fixture-say/issues/9", "createdAt": self.iso(t0 - 600),
               "closedAt": None, "stateReason": None,
               "comments": [{"author": {"login": "hperwin"}, "body": b, "createdAt": self.iso(t0 + k), "url": u}
                            for k, (b, u) in enumerate(said, 1)]}
        item = dict(TAP, key=key, messages=S._residue(key, iss)["messages"])
        self.assertEqual([(m["who"], m["via"]) for m in item["messages"]], [("you", ""), ("you", "Muddi"), ("you", "")])
        self.assertEqual(S._focus([{"id": 1, "agent": "Muddi", "role": "you", "ts": t0}], [item]), key)
        # Muddi's thread stays the focus while Muddi talks on the tap (its tap-say at +2 s, its own comment at +3 s)
        self.assertEqual(S._focus([{"id": 1, "agent": "Muddi", "role": "you", "ts": t0 + 1.5}], [item]), "agent:Muddi")
        self.assertIsNone(S._focus([], [dict(item, messages=item["messages"][1:])]))


if __name__ == "__main__":
    unittest.main()
