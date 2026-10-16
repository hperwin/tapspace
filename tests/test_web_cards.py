#!/usr/bin/env python3
"""Web update cards (contract sections 1 to 4, 9 and 10): the page's scripts still parse together, answers fold into
the card they answer, the outbox gives way to the server's echo, the visuals' pure parts, the chat-media allow-list, and
card markup that never meets a global class name or a review or tap route it must not call.

Reads space/index.html only; node runs the page's pure blocks (skipped where node is missing). Stdlib only.
    python3 -m unittest discover -s tests -p 'test_web_cards.py' -v
"""
import json
import pathlib
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
PAGE = (ROOT / "space/index.html").read_text()
NODE = shutil.which("node")
SCRIPTS = re.findall(r'<script( type="module")?>(.*?)</script>', PAGE, re.S)
CLASSIC = [body for module, body in SCRIPTS if not module]
MODULES = [body for module, body in SCRIPTS if module]


def block(start, end):
    i = PAGE.index(start)
    return PAGE[i:PAGE.index(end, i)]


PURE = block("// ---- pure: faces", "// ---- end pure: faces ----") + \
    block("// ---- pure: update cards", "// ---- end pure: cards ----")
CARDS = block("/* ---- update cards (contract sections 3, 4, 9 and 10)", "/* ---- end update cards ---- */")


def js(expr):
    code = PURE + "\nprocess.stdout.write(JSON.stringify(" + expr + "))"
    out = subprocess.run([NODE, "-e", code], capture_output=True, text=True, timeout=30)
    if out.returncode:
        raise AssertionError(out.stderr)
    return json.loads(out.stdout)


def fold(rows, out=(), now=0):
    return js(f"foldThread({json.dumps(rows)}, {json.dumps(list(out))}, {now})")


def card(cid, images=0, ask=False, action=None, v=1):
    c = {"v": v, "line": f"Card {cid}", "face": "agent:Muddi", "items": [], "images": [{"label": f"Image {i + 1}"} for i in range(images)]}
    if ask:
        c["ask"] = {"q": "Which first?", "checks": [{"id": "c1", "label": "Lineup"}, {"id": "c2", "label": "Box score"}], "text": True}
    if action:
        c["action"] = {"kind": "tap_accept", "tap": action}
    return {"id": cid, "agent": "Muddi", "role": "master", "body": c["line"], "ts": 1.0,
            "images": [f"/chat-media/{i:012x}.png" for i in range(images)], "card": c}


def answer(rid, a, nonce=None, body="answer"):
    return {"id": rid, "agent": "Muddi", "role": "you", "body": body, "ts": 2.0, "nonce": nonce or f"c:{rid}", "answer": a}


def text(rid, role="you", body="hi", nonce=None):
    r = {"id": rid, "agent": "Muddi", "role": role, "body": body, "ts": 3.0}
    if nonce:
        r["nonce"] = nonce
    return r


@unittest.skipUnless(NODE, "node is not installed")
class ScriptTests(unittest.TestCase):
    def check(self, suffix, code):
        with tempfile.NamedTemporaryFile("w", suffix=suffix, delete=False) as f:
            f.write(code)
        out = subprocess.run([NODE, "--check", f.name], capture_output=True, text=True, timeout=30)
        pathlib.Path(f.name).unlink()
        self.assertEqual(out.returncode, 0, out.stderr)

    def test_classic_scripts_parse_together(self):
        # one global scope: a const or let declared twice across scripts 1 and 2 is an early SyntaxError
        self.assertEqual(len(CLASSIC), 2)
        self.check(".js", "\n".join(CLASSIC))

    def test_modules_parse(self):
        self.assertEqual(len(MODULES), 2)
        for body in MODULES:
            self.check(".mjs", body)

    def test_no_function_declared_twice(self):
        names = re.findall(r"^(?:async )?function (\w+)", "\n".join(CLASSIC), re.M)
        self.assertEqual(sorted(n for n in set(names) if names.count(n) > 1), [])


@unittest.skipUnless(NODE, "node is not installed")
class FoldTests(unittest.TestCase):
    def test_answers_fold_into_a_loaded_card(self):
        msgs = fold([card(540, images=2, ask=True),
                     answer(541, {"card": 540, "image": 0, "verdict": "approve"}),
                     answer(542, {"card": 540, "image": 1, "verdict": "change", "note": "bigger"}),
                     answer(543, {"card": 540, "checks": ["c2"], "text": "box score"}),
                     text(544)])
        self.assertEqual([m["id"] for m in msgs], ["m540", "m544"])
        cst = msgs[0]["cst"]
        self.assertEqual(cst["shots"], [{"verdict": "approve", "note": "", "pending": False},
                                        {"verdict": "change", "note": "bigger", "pending": False}])
        self.assertEqual(cst["ask"], {"checks": ["c2"], "text": "box score", "done": True, "pending": False})
        self.assertIsNone(cst["act"])                               # no action on this card

    def test_an_answer_whose_card_is_not_loaded_is_a_bubble(self):
        msgs = fold([answer(600, {"card": 999, "image": 0, "verdict": "approve"}, nonce="c999:a", body="Approved: Grid")])
        self.assertEqual(len(msgs), 1)
        self.assertEqual((msgs[0]["who"], msgs[0]["body"], msgs[0]["id"]), ("you", "Approved: Grid", "nc999:a"))
        self.assertNotIn("card", msgs[0])

    def test_later_answers_win(self):
        msgs = fold([card(1, images=1, ask=True),
                     answer(2, {"card": 1, "image": 0, "verdict": "approve"}),
                     answer(3, {"card": 1, "image": 0, "verdict": "change", "note": "no"}),
                     answer(4, {"card": 1, "checks": ["c1"]}),
                     answer(5, {"card": 1, "checks": [], "text": "neither"})])
        self.assertEqual(msgs[0]["cst"]["shots"][0]["verdict"], "change")
        self.assertEqual(msgs[0]["cst"]["ask"]["checks"], [])
        self.assertEqual(msgs[0]["cst"]["ask"]["text"], "neither")

    def test_malformed_answers_stay_bubbles(self):
        msgs = fold([card(1, images=2),
                     answer(2, {"card": 1, "image": 5, "verdict": "approve"}),
                     answer(3, {"card": 1, "image": 0, "verdict": "maybe"}),
                     answer(4, {"card": 1, "checks": ["c1"]}),                    # this card asks nothing
                     answer(5, {"card": 1, "action": "accept"})])                 # and carries no action
        self.assertEqual([m["id"] for m in msgs], ["m1", "nc:2", "nc:3", "nc:4", "nc:5"])
        self.assertEqual(msgs[0]["cst"]["shots"][0]["verdict"], "")

    def test_unknown_version_draws_its_body(self):
        msgs = fold([card(1, images=1, v=2)])
        self.assertNotIn("card", msgs[0])
        self.assertEqual(msgs[0]["body"], "Card 1")

    def test_outbox_shows_then_gives_way_to_its_echo(self):
        msgs = fold([text(1, role="master")], [{"nonce": "w:a", "body": "hi", "ts": 5}])
        self.assertEqual((msgs[-1]["id"], msgs[-1]["pending"]), ("nw:a", True))
        msgs = fold([text(1, role="master"), text(600, body="hi", nonce="w:a")], [{"nonce": "w:a", "body": "hi", "ts": 5}])
        self.assertEqual([m["id"] for m in msgs], ["m1", "nw:a"])     # the same key: the echo keeps the node
        self.assertNotIn("pending", msgs[-1])

    def test_a_pending_answer_folds_pending_and_holds_sent(self):
        out = [{"nonce": "c540:x", "answer": {"card": 540, "image": 0, "verdict": "approve"}, "hold": 1600}]
        self.assertEqual(fold([card(540, images=1)], out, now=1000)[0]["cst"]["shots"][0],
                         {"verdict": "approve", "note": "", "pending": True})
        echoed = [card(540, images=1), answer(541, {"card": 540, "image": 0, "verdict": "approve"}, nonce="c540:x")]
        self.assertTrue(fold(echoed, out, now=1500)[0]["cst"]["shots"][0]["pending"])    # Sent holds 0.6 s
        self.assertFalse(fold(echoed, out, now=1700)[0]["cst"]["shots"][0]["pending"])   # then the echo wins
        self.assertEqual(js("SENT_HOLD"), 600)

    def test_the_action_folds_and_an_accept_stays_accepted(self):
        rows = [card(7, images=1, action="hperwin/tapspace#7"),
                answer(8, {"card": 7, "action": "deny", "note": "the button is off"})]
        self.assertEqual(fold(rows)[0]["cst"]["act"], {"verdict": "deny", "note": "the button is off", "pending": False})
        rows += [answer(9, {"card": 7, "action": "accept"}), answer(10, {"card": 7, "action": "deny", "note": "late"})]
        self.assertEqual(fold(rows)[0]["cst"]["act"], {"verdict": "accept", "note": "", "pending": False})


@unittest.skipUnless(NODE, "node is not installed")
class PartTests(unittest.TestCase):
    def test_timeline_shows_only_the_current_step(self):
        self.assertEqual(js("timeLine({steps: ['Audit', 'Spec', 'Build', 'Ship'], at: 1})"),
                         {"steps": ["Audit", "Spec", "Build", "Ship"], "n": 4, "at": 1, "count": "2 of 4", "text": "Spec"})
        done = js("timeLine({steps: ['a', 'b'], at: 9})")
        self.assertEqual((done["at"], done["count"], done["text"]), (2, "Done", "2 of 2"))

    def test_step_count(self):
        self.assertEqual(js("[stepCount(1, 4), stepCount(4, 4), stepCount(0, 0)]"), ["2 of 4", "Done", ""])

    def test_spark_path_is_p7s_geometry(self):
        self.assertEqual(js("sparkPath([1, 2, 3], 104)"), "M2 34L52 18L102 2")
        self.assertEqual(js("sparkPath([5, 5], 104)"), "M2 18L102 18")
        self.assertEqual(js("[sparkPath([1]), sparkPath(null)]"), ["", ""])
        self.assertTrue(js("sparkPath([3, 1, 2])").startswith("M2 2L160 34L318 "))

    def test_a_decision_card_is_pictures_without_a_visual(self):
        hero = js("""[cardHero({images: [{}], items: []}), cardHero({images: [{}, {}], items: [{}]}),
                      cardHero({images: [{}], items: [{}, {}]}), cardHero({images: [{}], visual: {kind: 'word'}}),
                      cardHero({images: [], items: []})]""")
        self.assertEqual(hero, [True, True, False, False, False])

    def test_send_needs_a_tick_or_words(self):
        self.assertEqual(js("[askReady(0, ''), askReady(0, '  '), askReady(1, ''), askReady(0, 'yes')]"), [False, False, True, True])

    def test_only_a_tap_accept_is_an_action(self):
        self.assertEqual(js("""[!!cardAction({action: {kind: 'tap_accept', tap: 'o/r#1'}}), !!cardAction({action: {kind: 'fly', tap: 'o/r#1'}}),
                               !!cardAction({action: {kind: 'tap_accept'}}), !!cardAction({})]"""), [True, False, False, False])


@unittest.skipUnless(NODE, "node is not installed")
class UrlTests(unittest.TestCase):
    def test_chat_media_only(self):
        ok = ["/chat-media/ab12cd34ef56.png", "/chat-media/ab12cd34ef56.jpg", "/chat-media/ab12cd34ef56.heic"]
        bad = ["https://x/y.png", "/chat-media/../x.png", "javascript:alert(1)", "/shots/a/1.png", "/chat-media/a.svg", ""]
        self.assertEqual(js(f"{json.dumps(ok + bad)}.map(u => CHAT_IMG.test(u))"), [True] * 3 + [False] * 6)

    def test_nonce_fits_the_server(self):
        n = js("newNonce('c540')")
        self.assertRegex(n, r"^c540:[0-9a-z]+$")
        self.assertLess(len(n), 64)                                 # the server keeps 64 characters


class MarkupTests(unittest.TestCase):
    GLOBAL = {"card", "chat", "state", "meta", "title", "agent", "ver", "acts", "shot", "shotwrap", "note", "notes", "plan"}

    def test_card_markup_avoids_global_class_names(self):
        tokens = {t for cls in re.findall(r'class="([^"$]*)', CARDS) for t in cls.split()}
        self.assertTrue(tokens)
        self.assertEqual(tokens & self.GLOBAL, set())

    def test_cards_never_call_the_review_or_fly_or_say_routes(self):
        for bad in ("/api/decide", "decide(", "/api/taps/fly", "/api/taps/say"):
            self.assertFalse(bad in CARDS, bad)
        # contract A1: a decision card's Accept is the guarded human route, always with the card's id
        self.assertEqual(re.findall(r"postJ\('/api/taps/accept', \{[^}]*\}\)", CARDS), ["postJ('/api/taps/accept', {key: tap, card: m.rid})"])
        self.assertIn("postJ('/api/chat', {agent: name, nonce: o.nonce, answer})", CARDS)

    def test_the_recorders_hooks_are_there(self):
        # tools/rec-web.py --answer finds cards by row id, images by index, and the ask's boxes by role and label
        for hook in ("el.dataset.card = m.rid", 'data-image="${i}"', 'role="checkbox" aria-checked=', ">Approve</button>",
                     ">Not quite</button>", ">Send</button>"):
            self.assertIn(hook, CARDS)

    def test_reduced_motion_covers_cards(self):
        blocks = re.findall(r"@media \(prefers-reduced-motion: reduce\)\{(.*?)\n  \}", PAGE, re.S)
        cards = [b for b in blocks if ".ucard" in b]
        self.assertTrue(cards)
        self.assertIn(".msg.ucard.new", cards[0])
        self.assertIn("cardRM.matches", CARDS)                      # the 0.2 s fade instead of the beats

    def test_the_composer_sends_a_nonce(self):
        self.assertIn("post('/api/chat', {agent: name, body, images, nonce: o.nonce})", PAGE)


if __name__ == "__main__":
    unittest.main()
