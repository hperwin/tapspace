#!/usr/bin/env python3
"""Taps from Flightdeck's board: server.py composes the board's rows (a fake Board here) over each tap's GitHub
residue (a fake gh here) into /api/taps, keyed in GitHub's spelling, phase for phase, with done items that linger
and the outcomes that settle cards.

Runs server.py in-process on an ephemeral port with a throwaway data dir: never :4747, never the live data, never a
real push, never GitHub, never Flightdeck, never the Keychain. board.py is never imported (server.py imports it only
when run as the live server); _taps_loop and _watch_loop never start: each test drives _compose itself. Stdlib only.
    python3 -B -m unittest tests.test_taps_board -v
"""
import base64
import copy
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
import urllib.request
from unittest import mock

sys.dont_write_bytecode = True
ROOT = pathlib.Path(__file__).resolve().parent.parent
TMP = tempfile.TemporaryDirectory(prefix="tapspace-taps-board-")
PNG_BYTES = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==")
NO_KEY = "Flightdeck isn't signed in on this Mac, so the tap list can't update."
RAN_OUT = "The Flightdeck sign-in on this Mac ran out, so the tap list can't update."
ISO = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")


def _load_server():
    """server.py as a fresh module whose paths point at our data dir (they are fixed at import)."""
    env = {"TAPSPACE_DATA": str(pathlib.Path(TMP.name) / "data"),
           "TAPSPACE_APNS_DIR": str(pathlib.Path(TMP.name) / "no-apns"),    # no key: nothing ever pushes
           "TAPSPACE_PORT": "0"}
    saved = {k: os.environ.get(k) for k in env}
    os.environ.update(env)
    sys.path.insert(0, str(ROOT))                 # server.py imports qr and links from its folder
    try:
        spec = importlib.util.spec_from_file_location("tapspace_server_taps_board_under_test", ROOT / "server.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    assert mod.DATA == pathlib.Path(env["TAPSPACE_DATA"]) and mod.DATA != ROOT / "data"
    assert not hasattr(mod, "board"), "server.py imported board.py outside __main__"
    return mod


S = _load_server()
SRV = None
BASE = ""
SHIM = pathlib.Path(TMP.name) / "gh-shim"


def setUpModule():
    global SRV, BASE
    S.DATA.mkdir(parents=True)
    S._save({"tickets": []})
    SRV = S.ThreadingHTTPServer(("127.0.0.1", 0), S.H)
    S.PORT = SRV.server_address[1]
    assert S.PORT != 4747
    BASE = f"http://127.0.0.1:{S.PORT}"
    threading.Thread(target=SRV.serve_forever, daemon=True).start()


def tearDownModule():
    SRV.shutdown()
    SRV.server_close()
    TMP.cleanup()


def call(path, body=None):
    """(status, raw text) for a GET (body None) or a POST, from this Mac."""
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(BASE + path, data=data, headers={"content-type": "application/json"} if data else {})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        with e:
            return e.code, e.read().decode()


def get(path):
    code, text = call(path)
    assert code == 200, (code, text)
    return json.loads(text)


GH_SHIM = '''#!%s
"""Fake gh: issue view answers from issues.json (by owner/repo#n, any case), api user is hperwin, anything else
(search included) fails as unexpected. Records every call; never reaches GitHub."""
import json, os, sys
here = os.path.dirname(os.path.abspath(__file__))
a = sys.argv[1:]
with open(os.path.join(here, "gh-argv.jsonl"), "a") as f:
    f.write(json.dumps(a) + "\\n")
if a[:2] == ["issue", "view"]:
    iss = json.load(open(os.path.join(here, "issues.json"))).get((a[a.index("--repo") + 1] + "#" + a[2]).lower())
    if iss is None:
        sys.exit("GraphQL: Could not resolve to an issue")
    print(json.dumps(iss))
elif a[:2] == ["api", "user"]:
    print("hperwin")
else:
    sys.exit("fake gh: unexpected call " + " ".join(a))
''' % sys.executable

LABEL = {"proposed": "○ proposed", "en route": "✈ en route", "holding": "● needs you", "landed": "✔ ready for sign-off",
         "done": "✓ shipped"}
STATUS = {"proposed": "proposed", "en route": "enroute", "holding": "needs-you", "landed": "ready", "done": "shipped"}


def journey(key):
    """The board's name for a tap: its issue url, lower-cased."""
    return ("https://github.com/" + key.replace("#", "/issues/")).lower()


def row(key, phase="landed", **extra):
    """A board row in the live shape (evidence/fleet-before.json): journey, handle and the note/why lines the server
    must never serve, two written goals, one cancelled and two standard ones."""
    repo, n = key.split("#")
    r = {"id": "pmvc" + n, "journey": journey(key), "handle": key.lower(), "repo": repo, "number": int(n),
         "title": f"Tap {n} title", "headline": f"Tap {n} headline", "status": STATUS[phase], "key": STATUS[phase],
         "label": LABEL[phase], "phase": phase,
         "note": "the pilot is done \u2014 read_journey has more", "why": "every goal is proven \u2014 read_journey",
         "waiting": None, "event": "landed" if phase == "landed" else "departed", "ask": None,
         "goals": {"total": 2, "proven": 1, "standard": 2, "line": "2 goals + 2 standard"},
         "goalList": [{"text": "The lineup card shows", "checked": True, "standard": False, "proof": "it shows"},
                      {"text": "The box score links", "checked": False, "standard": False},
                      {"text": "An old goal", "checked": False, "standard": False, "cancelled": True},
                      {"text": "Loose ends filed", "label": "follow-ups", "kind": "loose-ends", "standard": True,
                       "what": "every follow-up is filed"},
                      {"text": "Evidence \u2014 in production", "label": "visual proof", "kind": "conviction",
                       "standard": True, "what": "plain proof"}],
         "pilot": {"flight": "ff0bff8e", "posture": "flying", "thinking": False, "lane": "mac", "flying": True},
         "issueState": "open", "read": True}
    r.update(extra)
    return r


GOALS = [{"text": "The lineup card shows", "done": True}, {"text": "The box score links", "done": False}]
FIELDS = ["key", "repo", "number", "initial", "title", "url", "status", "goals", "messages", "replying", "created",
          "closed", "last", "phase", "phase_word", "ask", "fly_requested", "proof"]


class FakeBoard:
    """Any object with snapshot() and read(url) is a board to server.py (the board.py contract)."""

    def __init__(self, *rows):
        self.rows = {r["journey"]: r for r in rows}
        self.loaded, self.state, self.error, self.answers, self.reads = True, "live", "", {}, []

    def snapshot(self):
        return {"loaded": self.loaded, "rows": dict(self.rows), "at": time.time(), "state": self.state,
                "error": self.error}

    def read(self, url):
        self.reads.append(url)
        return self.answers.get(url)

    def put(self, *rows):
        for r in rows:
            self.rows[r["journey"]] = r

    def drop(self, key):
        del self.rows[journey(key)]


class TapsBoardTests(unittest.TestCase):
    STATE = ("HOT", "_ISSUES", "_CANON", "_TRIED", "_DONE", "_LEAVING", "_SETTLE", "_ON_BOARD", "_PROOF_SRC", "_HELD")

    def setUp(self):
        self.saved = {k: getattr(S, k) for k in ("BOARD", "GH", "_push")}
        self.saved_taps = dict(S.TAPS)
        self.saved_state = {k: copy.copy(getattr(S, k)) for k in self.STATE}
        SHIM.mkdir(exist_ok=True)
        self.reset()
        (SHIM / "gh").write_text(GH_SHIM)
        (SHIM / "gh").chmod(0o755)
        S.GH = str(SHIM / "gh")
        self.pushes = []
        S._push = lambda *a, **k: self.pushes.append((a, k))

    def reset(self):
        """A server that just started: no taps, no issues read, no outcomes, an empty state file, no gh calls."""
        for k in self.STATE:
            getattr(S, k).clear()
        S.TAPS.clear()
        S.TAPS.update(me="hperwin", items=[], fetched=0.0, error="", outcomes={})
        S._save({"tickets": []})
        S._taps_kick.clear()
        self.issues = {}
        (SHIM / "issues.json").unlink(missing_ok=True)
        (SHIM / "gh-argv.jsonl").unlink(missing_ok=True)
        S.BOARD = None

    def tearDown(self):
        for k, v in self.saved.items():
            setattr(S, k, v)
        S.TAPS.clear()
        S.TAPS.update(self.saved_taps)
        for k, v in self.saved_state.items():
            getattr(S, k).clear()
            getattr(S, k).update(v)
        S._taps_kick.clear()

    # ---- fixtures ----
    def board(self, *rows):
        S.BOARD = FakeBoard(*rows)
        return S.BOARD

    def issue(self, key, comments=(), labels=("flightdeck",), body="- [x] The lineup card shows\n- [ ] The box score links",
              title=None, created="2026-10-20T10:00:00Z", url=None):
        """The gh issue view answer for key (GitHub's spelling in its url, or the url given: a moved issue);
        comments are (login, body) pairs, an hour apart from 11:00 on October 21."""
        repo, n = key.split("#")
        url = url or f"https://github.com/{repo}/issues/{n}"
        self.issues[key.lower()] = {
            "author": {"login": "hperwin"}, "title": title or f"Issue {n}", "body": body,
            "labels": [{"name": x} for x in labels], "state": "OPEN", "url": url, "createdAt": created,
            "closedAt": None, "stateReason": None,
            "comments": [{"author": {"login": who}, "body": b, "createdAt": f"2026-10-21T{11 + i:02d}:00:00Z",
                          "url": f"{url}#issuecomment-{9000 + i}", "reactionGroups": []}
                         for i, (who, b) in enumerate(comments)]}
        (SHIM / "issues.json").write_text(json.dumps(self.issues))

    def gh_calls(self):
        log = SHIM / "gh-argv.jsonl"
        return [json.loads(x) for x in log.read_text().splitlines()] if log.exists() else []

    def views(self, key=None):
        return [c for c in self.gh_calls() if c[:2] == ["issue", "view"] and
                (key is None or f"{c[c.index('--repo') + 1]}#{c[2]}".lower() == key.lower())]

    def settle(self):
        """Runs _compose the way _taps_loop does until a pass leaves the kick unset (no slow call due)."""
        for _ in range(60):
            S._taps_kick.clear()
            S._compose()
            if not S._taps_kick.is_set():
                return
        self.fail("the compose kept kicking itself")

    def once(self):
        S._taps_kick.clear()
        S._compose()

    def items(self):
        return {t["key"]: t for t in S.TAPS["items"]}

    def kept(self):
        return json.loads(S.STATE.read_text()).get("tap_outcomes") or {}

    def lead(self):
        return {"name": "Muddi", "role": "lead", "state": "here", "title": "Muddi", "look": 0, "last": 0, "progress": None}

    def fleet(self):
        return {f["key"]: f for f in S._fleet([self.lead()], S.TAPS["items"], [])}

    def accept_card(self, key):
        """An Accept card in a master's thread (the FleetTests ask_card shape)."""
        return {"id": 5, "agent": "m", "role": "master", "ts": 1.0,
                "card": {"v": 1, "line": "x", "face": "agent:m", "items": [], "images": [{"label": "One"}],
                         "action": {"kind": "tap_accept", "tap": key}}}

    def cursor(self):
        return get("/api/wait?since=0&timeout=0")["cursor"]

    def changed(self, since):
        return set(get(f"/api/wait?since={since}&timeout=0")["changed"])

    # ---- the list ----
    def test_rows_become_taps_phase_for_phase(self):
        ask = {"kind": "question", "question": "Ship the lineup card today?", "why": "the pilot wants a yes",
               "toolUseID": "toolu_01", "key": "k-secret", "options": [{"value": "y", "label": "Yes", "what": "ship"},
                                                                     {"value": "n", "label": "Not yet", "what": "wait"}]}
        self.issue("SnappStats/snappy#3288", [("tseller", "landed")])
        self.issue("SnappStats/snappy#3300", [("tseller", "flying")], created="2026-10-21T09:00:00Z")
        self.issue("hperwin/fixture-app#7")
        self.issue("hperwin/fixture-app#8")
        self.board(row("SnappStats/snappy#3288"), row("SnappStats/snappy#3300", "en route"),
                   row("hperwin/fixture-app#7", "holding", ask=ask), row("hperwin/fixture-app#8", "proposed"))
        self.settle()
        it = self.items()
        self.assertEqual(set(it), {"SnappStats/snappy#3288", "SnappStats/snappy#3300", "hperwin/fixture-app#7",
                                   "hperwin/fixture-app#8"})
        order = ["SnappStats/snappy#3288", "SnappStats/snappy#3300", "hperwin/fixture-app#7", "hperwin/fixture-app#8"]
        self.assertEqual([it[k]["phase"] for k in order], ["landed", "enroute", "holding", "proposed"])
        self.assertEqual([it[k]["phase_word"] for k in order], ["ready for sign-off", "en route", "needs you", "proposed"])
        self.assertEqual([it[k]["status"] for k in order], ["flying", "flying", "needs-you", "boarding"])
        self.assertTrue(all(it[k]["goals"] == GOALS for k in order))
        a7 = it["hperwin/fixture-app#7"]["ask"]
        self.assertEqual(set(a7), {"text", "url", "at"})
        self.assertEqual((a7["text"], a7["url"]), ("Ship the lineup card today? (Yes, Not yet)",
                                                   "https://github.com/hperwin/fixture-app/issues/7"))
        self.assertRegex(a7["at"], ISO)
        self.assertEqual(it["SnappStats/snappy#3288"]["title"], "Tap 3288 title")
        self.assertEqual((S.TAPS["items"][0]["key"], S.TAPS["items"][-1]["key"]),
                         ("hperwin/fixture-app#7", "hperwin/fixture-app#8"))
        for t in S.TAPS["items"]:                                       # today's fields in today's order; outcome only when done
            self.assertEqual(list(t), FIELDS)
        code, text = call("/api/taps")
        self.assertEqual(code, 200)
        for never in ("read_journey", "\u2014", "\\u2014", "toolUseID", "toolu_01", "k-secret", "the pilot wants a yes",
                      "pmvc", "ff0bff8e", '"phase": "closed"', '"phase": "en route"'):
            self.assertNotIn(never, text)
        served = json.loads(text)
        self.assertEqual(set(served), {"me", "items", "fetched", "error", "outcomes"})
        self.assertGreater(served["fetched"], 0)
        self.assertEqual((served["error"], served["outcomes"]), ("", {}))
        self.assertEqual(len(self.views()), 4)                                # one read per issue
        self.assertFalse(any("search" in c for c in self.gh_calls()))
        self.assertEqual(self.pushes, [])

    def test_the_key_is_githubs_spelling_from_the_issue_url(self):
        self.issue("SnappStats/snappy#3313", [("tseller", "hello")])
        self.issue("SnappStats/snappy#3314", [("tseller", "hello")])
        self.board(row("snappstats/snappy#3313"), row("snappstats/snappy#3314"))     # the board's lower case
        self.once()                                                # reads #3313: learns the repo's spelling
        self.assertEqual(S.TAPS["items"], [])
        self.assertEqual(S.TAPS["fetched"], 0)
        self.once()                                                # a known repo needs no wait
        it = self.items()
        self.assertEqual(set(it), {"SnappStats/snappy#3313", "SnappStats/snappy#3314"})
        self.assertEqual(len(it["SnappStats/snappy#3313"]["messages"]), 1)
        self.assertEqual((it["SnappStats/snappy#3314"]["messages"], it["SnappStats/snappy#3314"]["url"]),
                         ([], "https://github.com/SnappStats/snappy/issues/3314"))     # served before its own read
        self.settle()
        self.assertEqual(len(self.items()["SnappStats/snappy#3314"]["messages"]), 1)
        self.assertFalse(any(k.startswith("snappstats/") for k in self.items()))
        # an issue gh cannot read: never served under any spelling, and the list still loads once it was tried
        self.reset()
        self.issue("SnappStats/snappy#3313")
        self.board(row("hperwin/gone-app#5"), row("snappstats/snappy#3313"))
        self.settle()
        self.assertGreater(S.TAPS["fetched"], 0)
        self.assertEqual(set(self.items()), {"SnappStats/snappy#3313"})
        self.assertEqual(len(self.views("hperwin/gone-app#5")), 1)
        # an issue GitHub answers from another repo (moved or renamed): only the case of a name is ever learned
        self.reset()
        self.issue("hperwin/old-name#4", url="https://github.com/hperwin/new-name/issues/4")
        self.issue("hperwin/old-name#6", url="https://github.com/hperwin/old-name/issues/9")
        self.board(row("hperwin/old-name#4"))
        self.settle()
        self.assertEqual((self.items(), S._CANON), ({}, {}))
        self.assertGreater(S.TAPS["fetched"], 0)
        self.board(row("hperwin/old-name#6"))
        self.settle()
        self.assertEqual((self.items(), S._CANON), ({}, {}))

    def test_a_tile_rows_repo_keys_the_tap_when_github_cannot_say_its_case(self):
        tile, plain = "SnappStats/snappy#3313", "snappstats/snappy#3314"
        b = self.board(row(tile), row(plain), row("hperwin/fixture-app#7", repo="Other/Repo"))
        self.settle()                                  # gh cannot read any of them (signed out at boot)
        self.assertEqual(set(self.items()), {tile})    # a tile row's repo carries GitHub's case (A2.3)
        self.assertGreater(S.TAPS["fetched"], 0)
        self.assertEqual(len(self.views(tile)), 2)     # its read was tried first, then once more under its key
        self.issue(tile, [("tseller", "landed")])      # gh is back: the same key gets its thread, and the repo's
        S._TRIED[tile] = 0                             # case it teaches keys the plain row too
        self.settle()
        self.assertEqual(set(self.items()), {tile, "SnappStats/snappy#3314"})
        self.assertEqual(len(self.items()[tile]["messages"]), 1)
        self.reset()                                   # a read GitHub answers always wins over the tile
        self.issue("SnappStats/snappy#3313")
        self.board(row(tile, repo="Snappstats/Snappy"))
        self.settle()
        self.assertEqual(set(self.items()), {tile})

    def test_a_landed_row_reads_landed_whatever_its_comments_say(self):
        # snappy#3288 as it was live: every goal checked on GitHub, the pilot's last comment older than a body edit,
        # which the GitHub guess read as en route
        self.issue("SnappStats/snappy#3288", [("tseller", "Flying: proving goal 1"), ("tseller", "Landed")],
                   body="- [x] The lineup card shows\n- [x] The box score links\n\nEdited after landing.")
        self.board(row("SnappStats/snappy#3288"))                    # status ready, label ready for sign-off
        self.settle()
        t = self.items()["SnappStats/snappy#3288"]
        self.assertEqual((t["phase"], t["phase_word"], t["status"]), ("landed", "ready for sign-off", "flying"))
        self.assertEqual(self.fleet()["SnappStats/snappy#3288"]["word"], "ready for sign-off")
        self.assertTrue(self.fleet()["SnappStats/snappy#3288"]["needs_you"])

    def test_fog_keeps_what_was_known_and_a_new_fog_row_stays_out(self):
        ask = {"kind": "question", "question": "Which league first?", "options": []}
        self.issue("hperwin/fixture-app#7")
        self.issue("hperwin/fixture-app#9")
        b = self.board(row("hperwin/fixture-app#7", "holding", ask=ask))
        self.settle()
        before = self.items()["hperwin/fixture-app#7"]
        self.assertEqual(before["ask"]["text"], "Which league first?")
        b.put(dict(row("hperwin/fixture-app#7", "holding"), read=False, phase=None, label=None, ask=None, goalList=[]),
              row("hperwin/fixture-app#9", "en route", read=False))
        self.settle()
        after = self.items()["hperwin/fixture-app#7"]
        self.assertEqual((after["phase"], after["phase_word"], after["goals"], after["ask"]),
                         (before["phase"], before["phase_word"], before["goals"], before["ask"]))   # same at too
        self.assertNotIn("hperwin/fixture-app#9", self.items())

    def test_a_row_that_cannot_be_read_is_fog_and_the_others_keep_moving(self):
        a, c, n = "hperwin/fixture-app#7", "hperwin/fixture-app#8", "hperwin/fixture-app#9"
        for k in (a, c, n):
            self.issue(k)
        b = self.board(row(a, "holding", ask={"kind": "question", "question": "Which league first?", "options": []}),
                       row(c, "en route"))
        self.settle()
        before = self.items()[a]
        b.put(row(a, "landed", ask="Which league first?"),                     # shapes Flightdeck might change
              row(c, "landed"),
              row(n, "en route", goalList=["The lineup card shows"]))
        self.settle()
        it = self.items()
        self.assertEqual(it[a], before)                # fog: as last served (A2.2)
        self.assertEqual(it[c]["phase"], "landed")     # every other row keeps moving
        self.assertNotIn(n, it)                        # a new row that cannot be read stays out
        b.put(row(a, "landed"))
        self.settle()
        self.assertEqual(self.items()[a]["phase"], "landed")

    # ---- leaving the board ----
    def test_a_tap_that_leaves_lingers_done_with_its_outcome(self):
        key, url = "SnappStats/snappy#3313", "https://github.com/snappstats/snappy/issues/3313"
        for answer, word in (({"phase": "done", "status": "aborted", "event": "discarded",
                               "board": {"status": "aborted", "label": "⊘ aborted", "phase": "done"}}, "aborted"),
                             ({"phase": "done", "status": "shipped", "event": "accepted",
                               "board": {"label": "✓ shipped"}}, "shipped"),
                             ({"phase": "done", "status": "closed", "event": "closed",
                               "board": {"label": "⊙ closed"}}, "closed")):
            with self.subTest(word=word):
                self.reset()
                self.issue(key, [("tseller", "landed")])
                self.issue("hperwin/fixture-app#7")
                b = self.board(row(key), row("hperwin/fixture-app#7", "holding"))
                self.settle()
                was = self.items()[key]
                n_views = len(self.views(key))
                b.answers[url] = answer
                b.drop(key)
                self.once()                    # served as it was until the board says how it ended (A1.4)
                self.assertEqual(self.items()[key], was)
                self.assertEqual(b.reads, [url])
                self.settle()
                d = self.items()[key]
                self.assertEqual((d["phase"], d["phase_word"], d["outcome"], d["status"], d["ask"], d["replying"]),
                                 ("done", word, word, "landed", None, False))
                self.assertEqual(S.TAPS["items"][-1]["key"], key)
                self.assertNotIn(key, self.fleet())
                self.assertEqual(get("/api/taps")["outcomes"][key], word)
                self.assertEqual(self.kept()[key]["outcome"], word)
                S._DONE[key] = (time.time() - 1, S._DONE[key][1])
                self.once()
                self.assertNotIn(key, self.items())
                self.assertEqual(get("/api/taps")["outcomes"], {key: word})
                self.assertEqual(len(self.views(key)), n_views)                 # never read after it left
                self.assertEqual(b.reads, [url])

    def test_a_done_row_on_the_board_needs_no_read(self):
        key = "SnappStats/snappy#3290"
        self.issue(key, [("tseller", "done")])
        b = self.board(row(key, "done", label="✓ shipped", status="shipped", event="accepted"))
        self.settle()                                  # done at boot: served as done at once (A1.4)
        d = self.items()[key]
        self.assertEqual((d["phase"], d["phase_word"], d["outcome"], d["status"]), ("done", "shipped", "shipped", "landed"))
        self.assertEqual(self.kept()[key]["outcome"], "shipped")
        self.assertEqual(S.TAPS["outcomes"], {key: "shipped"})
        b.drop(key)
        self.settle()
        self.assertEqual(self.items()[key], d)
        self.assertEqual(b.reads, [])

    def leave_ago(self, key, since=0, asked=0):
        """Moves a leaving tap's clock back: since it left, and since the board was last asked how it ended."""
        s, it, at = S._LEAVING[key]
        S._LEAVING[key] = (s - since, it, at if at is None else at - asked)

    def test_a_leaving_tap_is_asked_again_until_the_board_says_how_it_ended(self):
        key, url = "SnappStats/snappy#3313", "https://github.com/snappstats/snappy/issues/3313"
        self.assertEqual(S.LEAVE_WAIT, 120)
        self.issue(key, [("tseller", "landed")])
        b = self.board(row(key))
        self.settle()
        was = self.items()[key]
        b.drop(key)
        self.settle()                                  # the read was refused, paced or failed (None)
        self.assertEqual((self.items()[key], b.reads), (was, [url]))     # served as it was (A2.1)
        self.settle()
        self.assertEqual(b.reads, [url])               # asked again on a later pass, not at once
        self.leave_ago(key, asked=S.TAP_HOT_EVERY)
        b.answers[url] = {"phase": "done", "status": "shipped", "event": "accepted", "board": {"label": "✓ shipped"}}
        self.settle()
        self.assertEqual(b.reads, [url, url])
        d = self.items()[key]
        self.assertEqual((d["phase"], d["phase_word"], d["outcome"]), ("done", "shipped", "shipped"))
        self.assertEqual(S.TAPS["outcomes"], {key: "shipped"})

    def test_a_leaving_tap_the_board_says_is_open_waits_unasked_until_it_is_back(self):
        key, url = "SnappStats/snappy#3313", "https://github.com/snappstats/snappy/issues/3313"
        self.issue(key, [("tseller", "landed")])
        b = self.board(row(key))
        self.settle()
        was, n_views = self.items()[key], len(self.views(key))
        b.answers[url] = {"phase": "landed", "status": "ready", "board": {"label": "✔ ready for sign-off"}}
        b.drop(key)
        self.settle()
        with mock.patch.object(S, "TAP_HOT_EVERY", 0):              # a retry would be due on every pass
            self.settle()
            self.settle()
        self.assertEqual((self.items()[key], b.reads), (was, [url]))     # still served; the board said, so not asked
        b.put(row(key))                                # (a) the board lists it again
        self.settle()
        self.assertEqual(self.items()[key], was)
        self.assertEqual((S._LEAVING, S.TAPS["outcomes"]), ({}, {}))
        self.assertEqual(len(self.views(key)), n_views)

    def test_a_tap_never_told_ended_leaves_after_leave_wait_and_comes_back_with_its_thread(self):
        key = "SnappStats/snappy#3313"
        self.issue(key, [("tseller", "landed")])
        b = self.board(row(key))
        self.settle()
        was, n_views = self.items()[key], len(self.views(key))
        b.drop(key)
        self.settle()
        self.leave_ago(key, since=S.LEAVE_WAIT)
        self.settle()
        self.assertNotIn(key, self.items())            # (c) it just leaves, with no ending invented
        self.assertEqual((S.TAPS["outcomes"], self.kept()), ({}, {}))
        b.put(row(key))                                # back: its thread and its place, not an unread newest tap
        self.once()
        self.assertEqual(self.items()[key], was)
        self.settle()
        self.assertEqual(len(self.views(key)), n_views)
        b.drop(key)                                    # its residue is kept DONE_LINGER past its last listing, no longer
        self.settle()
        self.leave_ago(key, since=S.LEAVE_WAIT)
        self.settle()
        self.assertIn(key, S._ISSUES)
        S._HELD[key] -= S.DONE_LINGER
        self.once()
        self.assertNotIn(key, S._ISSUES)

    def test_the_loop_wakes_every_few_seconds_while_a_tap_is_leaving(self):
        class Stop(Exception):
            pass
        waits = []

        def wait(timeout):
            waits.append(timeout)
            raise Stop
        with mock.patch.object(S, "_compose", lambda: None), mock.patch.object(S._taps_kick, "wait", wait):
            self.assertRaises(Stop, S._taps_loop)
            S._LEAVING["o/r#1"] = (time.time(), {}, 0)
            self.assertRaises(Stop, S._taps_loop)
        self.assertEqual(waits, [S.TAP_RETRY, S.TAP_HOT_EVERY])

    # ---- cards ----
    def test_an_accept_card_settles_with_its_tap_and_stays_settled(self):
        key, url = "SnappStats/snappy#3288", "https://github.com/snappstats/snappy/issues/3288"
        acc = self.accept_card(key)
        w = S._waits_on_you
        self.issue(key)
        b = self.board(row(key))
        self.settle()
        self.assertTrue(w([acc], S.TAPS["items"]))                     # landed: waits on you
        b.answers[url] = {"phase": "done", "status": "shipped", "event": "accepted", "board": {"label": "✓ shipped"}}
        b.drop(key)
        self.once()
        self.assertTrue(w([acc], S.TAPS["items"]))                     # still served landed while it is asked
        self.settle()
        self.assertFalse(w([acc], S.TAPS["items"]))                    # lingers done
        S._DONE[key] = (time.time() - 1, S._DONE[key][1])
        self.settle()
        self.assertNotIn(key, self.items())
        self.assertFalse(w([acc], S.TAPS["items"]))                    # left: its outcome settles it
        self.assertNotIn(key, S._SETTLE)
        self.assertFalse(w([acc], [], {key: "aborted"}))               # an aborted ending settles it too
        self.assertTrue(w([acc], [{"key": key, "phase": "enroute"}], {key: "aborted"}))   # reopened: the served tap decides
        # reopened for real: back on the board open, its kept ending is forgotten (A1.3)
        b.put(row(key, "en route"))
        self.settle()
        self.assertEqual(self.items()[key]["phase"], "enroute")
        self.assertEqual((S.TAPS["outcomes"], self.kept()), ({}, {}))
        self.assertNotIn(key, get("/api/taps")["outcomes"])
        self.assertTrue(w([acc], S.TAPS["items"]))

    def test_a_card_tap_nobody_saw_end_is_asked_once(self):
        key, url = "SnappStats/snappy#3295", "https://github.com/snappstats/snappy/issues/3295"
        acc = self.accept_card(key)
        for answer, settled in (({"phase": "done", "status": "shipped", "event": "accepted",
                                  "board": {"label": "✓ shipped"}}, True), (None, False)):
            with self.subTest(answer=answer):
                self.reset()
                b = self.board()
                b.answers[url] = answer
                self.settle()
                self.assertTrue(S._waits_on_you([acc], S.TAPS["items"]))
                self.assertIn(key, S._SETTLE)
                self.assertTrue(S._taps_kick.is_set())
                self.settle()
                self.assertEqual(S.TAPS["outcomes"], {key: "shipped"} if settled else {})
                self.assertEqual(S._waits_on_you([acc], S.TAPS["items"]), not settled)
                self.assertEqual(b.reads, [url])
                self.settle()                                          # a failed lookup waits DONE_LINGER
                self.assertEqual(b.reads, [url])

    # ---- the GitHub residue ----
    def test_a_colleague_pilots_question_is_needs_you(self):
        key = "hperwin/fixture-app#8"
        self.issue(key, [("tseller", "@hperwin which league goes first?")],
                   labels=("flightdeck", "flightdeck:needs-reply"))
        self.board(row(key, "en route", event="stopover", waiting="waiting on hperwin's reply", status="parked"))
        self.settle()
        t = self.items()[key]
        self.assertEqual((t["phase"], t["phase_word"], t["status"]), ("enroute", "en route", "needs-you"))
        self.assertEqual(t["ask"]["text"], "which league goes first?")
        self.assertEqual(t["ask"]["url"], "https://github.com/hperwin/fixture-app/issues/8#issuecomment-9000")
        self.assertTrue(self.fleet()[key]["needs_you"])
        self.assertEqual(self.fleet()[key]["phase"], "enroute")
        self.issue(key, [("tseller", "@hperwin which league goes first?")], labels=("flightdeck",))
        S._TRIED[key] = 0
        S._hot(key, 0.15)
        self.settle()
        t = self.items()[key]
        self.assertEqual((t["status"], t["ask"]), ("flying", None))
        self.assertFalse(self.fleet()[key]["needs_you"])

    def test_the_open_tap_read_longest_ago_is_read_again_past_five_minutes(self):
        a, c, d = "hperwin/fixture-app#7", "hperwin/fixture-app#8", "SnappStats/snappy#3290"
        for k in (a, c, d):
            self.issue(k)
        self.board(row(a), row(c, "en route"), row(d, "done", label="✓ shipped", status="shipped", event="accepted"))
        self.settle()
        self.settle()
        self.assertEqual([len(self.views(k)) for k in (a, c, d)], [1, 1, 1])     # fresh reads: no safety read
        now = time.time()
        S._TRIED.update({a: now - S.TAP_REREAD, c: now - S.TAP_REREAD - 1, d: now - 2 * S.TAP_REREAD})
        self.issue(c, [("tseller", "a comment _watch_loop never saw")])
        self.once()
        self.assertEqual([len(self.views(k)) for k in (a, c, d)], [1, 2, 1])     # the oldest open read first (A2.4)
        self.settle()
        self.assertEqual([len(self.views(k)) for k in (a, c, d)], [2, 2, 1])     # then the next; a done tap never
        self.assertEqual(len(self.items()[c]["messages"]), 1)
        self.assertEqual(S.TAP_REREAD, 300)

    def test_only_what_is_served_wakes_clients(self):
        key = "SnappStats/snappy#3288"
        self.issue(key)
        b = self.board(row(key))
        self.settle()
        cur = self.cursor()
        r = b.rows[journey(key)]
        r["pilot"]["thinking"] = True
        r["goalList"][0]["proof"] = "a new proof line"
        self.settle()
        self.assertNotIn("taps", self.changed(cur))
        self.assertNotIn("agents", self.changed(cur))
        n_views = len(self.views(key))
        b.put(row(key, "holding"))
        S._TRIED[key] = 0
        self.once()
        self.assertLessEqual({"taps", "agents"}, self.changed(cur))
        self.assertIn(key, S.HOT)
        self.settle()
        self.assertEqual(len(self.views(key)), n_views + 1)

    def test_a_pilot_comment_pushes_once_and_never_on_a_first_read_or_reconnect(self):
        key = "SnappStats/snappy#3300"
        self.issue(key, [("tseller", "Flying"), ("tseller", "Proving goal 1")], title="Lineup card")
        b = self.board(row(key, "en route"))
        self.settle()
        self.assertEqual(self.pushes, [])                              # a first read never pushes
        self.issue(key, [("tseller", "Flying"), ("tseller", "Proving goal 1"), ("tseller", "Goal 1 proven")],
                   title="Lineup card")
        S._TRIED[key] = 0
        S._hot(key, 0.15)
        self.settle()
        self.assertEqual(self.pushes, [(("Lineup card", "Goal 1 proven"), {"key": key})])
        b.rows = copy.deepcopy(b.rows)                                 # a reconnect: a fresh, equal snapshot
        self.settle()
        self.assertEqual(len(self.pushes), 1)
        self.issue(key, [("tseller", "Flying"), ("tseller", "Proving goal 1"), ("tseller", "Goal 1 proven"),
                         ("hperwin", "Thanks")], title="Lineup card")
        S._TRIED[key] = 0
        S._hot(key, 0.15)
        self.settle()
        self.assertEqual(len(self.pushes), 1)                          # his own comment never pushes

    # ---- the board's state ----
    def test_the_list_waits_for_the_board_and_says_why_in_plain_words(self):
        b = self.board()
        b.loaded, b.state, b.error = False, "signed-out", NO_KEY
        cur = self.cursor()
        self.once()
        self.assertEqual(S.TAPS["fetched"], 0)
        self.assertEqual(S.TAPS["error"], NO_KEY)
        self.assertIn("taps", self.changed(cur))
        served = get("/api/taps")
        self.assertEqual((served["error"], served["fetched"], served["items"], served["outcomes"]), (NO_KEY, 0, [], {}))
        self.assertIn("still loading", str(S._loading()))
        self.assertIn(NO_KEY, str(S._loading()))
        self.issue("hperwin/fixture-app#7")
        b.put(row("hperwin/fixture-app#7"))
        b.loaded, b.state, b.error = True, "expired", RAN_OUT
        self.settle()
        self.assertEqual(list(self.items()), ["hperwin/fixture-app#7"])
        self.assertEqual(S.TAPS["error"], RAN_OUT)                     # a publish keeps the sentence
        self.assertEqual(get("/api/taps")["error"], RAN_OUT)

    def test_no_child_the_server_starts_gets_the_board_key_or_url(self):
        board_env = {"TAPSPACE_BOARD_KEY": "a-fake-board-key", "TAPSPACE_BOARD_URL": "http://127.0.0.1:9"}
        self.assertLessEqual(set(board_env), set(S.SPAWN_SCRUB))      # muddi.sh's restart env (test_agents_api)
        envs = []

        def record(args, *a, **kw):
            envs.append(kw["env"])

        def launch(args, *a, **kw):                  # new-master.sh says it started, and has ended
            record(args, *a, **kw)
            kw["stdout"].write("MASTER-OK\n")
            kw["stdout"].flush()
            return mock.Mock(poll=lambda: 0, wait=lambda: 0)
        with mock.patch.dict(os.environ, board_env), mock.patch.object(S.subprocess, "Popen", launch), \
                mock.patch.object(S.subprocess, "run", record):
            S._spawn_master(str(pathlib.Path.home()), "Claude · scrub · 0001")     # every new master (new-master.sh)
            self.assertEqual(call("/pair")[0], 200)                                # tap.py pair
        self.assertEqual(len(envs), 2)
        for env in envs:
            self.assertFalse(set(board_env) & set(env))
            self.assertEqual(env["TAPSPACE_URL"], f"http://127.0.0.1:{S.PORT}")

    def test_refresh_heats_a_key_and_kicks_the_compose(self):
        code, _ = call("/api/taps/refresh", {})
        self.assertEqual(code, 200)
        self.assertTrue(S._taps_kick.is_set())
        self.assertEqual(S.HOT, {})
        S._taps_kick.clear()
        code, _ = call("/api/taps/refresh", {"key": "hperwin/fixture-app#7"})
        self.assertEqual(code, 200)
        self.assertIn("hperwin/fixture-app#7", S.HOT)
        self.assertTrue(S._taps_kick.is_set())

    def test_a_lingering_done_taps_proof_is_still_served(self):
        key, url = "SnappStats/snappy#3288", "https://github.com/snappstats/snappy/issues/3288"
        asset = "0f5a1b2c-3d4e-4f60-8a7b-9c0d1e2f3a4b"
        self.issue(key, body=f"- [x] shows\n\n![Shot](https://github.com/user-attachments/assets/{asset})")
        S.PROOF.mkdir(parents=True, exist_ok=True)
        (S.PROOF / f"{asset}.png").write_bytes(PNG_BYTES)
        b = self.board(row(key))
        self.settle()
        self.assertEqual(len(self.items()[key]["proof"]), 1)
        b.answers[url] = {"phase": "done", "status": "shipped", "event": "accepted", "board": {"label": "✓ shipped"}}
        b.drop(key)
        self.settle()
        self.assertEqual(self.items()[key]["phase"], "done")
        self.assertEqual(S._proof_image(key, 0)[0], PNG_BYTES)        # from data/proof: no network
        S._DONE[key] = (time.time() - 1, S._DONE[key][1])
        self.settle()
        with self.assertRaisesRegex(LookupError, "no such tap"):
            S._proof_image(key, 0)

    def test_outcomes_keep_the_newest_500_of_30_days(self):
        now = time.time()
        m = {f"o/r#{i}": {"outcome": "shipped", "at": now - 1000 + i} for i in range(501)}
        m["o/r#old"] = {"outcome": "aborted", "at": now - 31 * 86400}
        kept = S._kept(m)
        self.assertEqual(len(kept), 500)
        self.assertNotIn("o/r#0", kept)                                # the oldest recent one
        self.assertNotIn("o/r#old", kept)                              # older than 30 days
        self.assertIn("o/r#500", kept)


if __name__ == "__main__":
    unittest.main()
