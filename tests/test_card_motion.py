#!/usr/bin/env python3
"""Web card motion (contract section 4, R9; P7 2.1): the page keeps one motion vocabulary (its own keyframes, curves and
eases, no new ones), the shared beat table with the page's staggers, the moved rule, the bridge the cards nod and draw
through, no loop inside a card but the Sent dot, Reduce Motion, and the gates that keep history at rest.

Reads space/index.html only; node runs the page's pure cards block (skipped where node is missing). Stdlib only.
    python3 -B -m unittest discover -s tests -p 'test_card_motion.py' -v
"""
import json
import pathlib
import re
import shutil
import subprocess
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
PAGE = (ROOT / "space/index.html").read_text()
CSS = "\n".join(re.findall(r"<style>(.*?)</style>", PAGE, re.S))
NODE = shutil.which("node")
KEYFRAMES = {"breathe", "dot", "draw", "hlin", "msgin", "msgup", "noteout", "orbpulse", "orbswap", "railin", "ripple",
             "spin", "swapin", "thumbin", "thumbout"}
CURVES = {"(.22,.9,.24,1)", "(.3,1.6,.5,1)", "(.3,1.4,.5,1)", "(.4,0,.2,1)", "(.3,.8,.3,1)"}


def pure():
    i = PAGE.index("// ---- pure: update cards")
    return PAGE[i:PAGE.index("// ---- end pure: cards ----", i)]


def js(expr):
    code = pure() + "\nprocess.stdout.write(JSON.stringify(" + expr + "))"
    out = subprocess.run([NODE, "-e", code], capture_output=True, text=True, timeout=30)
    if out.returncode:
        raise AssertionError(out.stderr)
    return json.loads(out.stdout)


def rules():
    """(selector, declarations) of every innermost CSS rule (a media block's rules come out one by one)."""
    return [(sel.strip(), body) for sel, body in re.findall(r"([^{}]+)\{([^{}]*)\}", re.sub(r"/\*.*?\*/", "", CSS, flags=re.S))]


class VocabularyTests(unittest.TestCase):
    def test_keyframes_are_todays_fifteen(self):
        self.assertEqual(set(re.findall(r"@keyframes (\w+)", PAGE)), KEYFRAMES)

    def test_every_curve_is_one_the_page_had(self):
        got = {re.sub(r"\s+", "", c) for c in re.findall(r"cubic-bezier(\([^)]*\))", PAGE)}
        self.assertEqual(got - CURVES, set())

    def test_eases_and_no_springs(self):
        self.assertEqual(set(re.findall(r"ease: '([^']*)'", PAGE)) - {"out(3)", "in(2)", "inOutSine", "linear"}, set())
        self.assertEqual(set(re.findall(r"easing: '([^']*)'", PAGE)) - {"cubic-bezier(.22,.9,.24,1)", "ease-out"}, set())
        self.assertNotIn("createSpring", PAGE)

    def test_the_bridge_names_nod_draw_and_still(self):
        line = re.search(r"window\.tsPanel = \{([^}]*)\}", PAGE).group(1)
        self.assertEqual({x.strip() for x in line.split(",")} >= {"render", "nod", "draw", "still"}, True)
        self.assertIn("function draw(el){", PAGE)

    def test_reduced_motion_covers_cards(self):
        blocks = re.findall(r"@media \(prefers-reduced-motion: reduce\)\{(.*?)\n  \}", CSS, re.S)
        self.assertTrue(any(".ucard" in b for b in blocks))

    def test_nothing_loops_inside_a_card_but_the_sent_dot(self):
        looping = [sel for sel, body in rules() if ".ucard" in sel and "infinite" in body]
        self.assertEqual([s for s in looping if ".usent" not in s], [])
        self.assertTrue(looping)
        # and the faces' and chips' own loops are stopped inside a card
        stopped = " ".join(sel for sel, body in rules() if ".ucard" in sel and "animation:none" in body.replace(" ", ""))
        for x in (".ucard .orb", ".ucard .orb::after", ".ucard .tapav .arc", ".ucard .chip i"):
            self.assertIn(x, stopped)

    def test_the_beats_and_their_gates_exist(self):
        for x in ("function cardTimes(", "function playCard(", "function cardMoved(", "const CARD_END = 2400;",
                  # P7 2.1.1: a render counts as the open itself until the thread's rows are loaded
                  "const switched = rendered.key !== current || !rendered.loaded;",
                  # contract 10: the newest two play per open, after the open settles
                  "due.slice(-2)", "const CARD_SETTLE = 700;"):
            self.assertIn(x, PAGE)


@unittest.skipUnless(NODE, "node is not installed")
class BeatTableTests(unittest.TestCase):
    R = {"items": [{"ref": "a", "phase": "landed", "was": {"phase": "enroute"}}, {"ref": "b"},
                   {"ref": "c", "progress": {"current": 2}, "was": {"current": 1}}]}

    def times(self, card, images, pills):
        return js(f"""(() => {{ const t = cardTimes({json.dumps(card)}); return {{line: t.line, visual: t.visual, ask: t.ask,
            pills: [...Array({pills}).keys()].map(t.pill), items: (card => card.items.map((_, i) => t.item(i)))({json.dumps(card)}),
            images: [...Array({images}).keys()].map(t.image), change: [...t.change]}}; }})()""")

    def test_reference_card_r(self):
        # P7 2.0: 3 items (0 and 2 moved), 2 images, 4 pills
        t = self.times(self.R, 2, 4)
        self.assertEqual((t["line"], t["visual"], t["ask"]), (100, 250, 800))
        self.assertEqual(t["pills"], [250, 290, 330, 370])
        self.assertEqual(t["items"], [450, 520, 590])
        self.assertEqual(t["change"], [[0, 890], [2, 930]])
        self.assertEqual(t["images"], [650, 720])

    def test_a_big_card_caps_its_staggers(self):
        big = {"items": [{"ref": str(i), "phase": "done", "was": {"phase": "landed"}} for i in range(8)]}
        t = self.times(big, 10, 0)
        self.assertEqual(t["items"][7], 800)
        self.assertEqual(t["change"][0], [0, 1100])
        self.assertEqual(t["images"][9], 1000)

    def test_an_item_moves_only_when_its_snapshot_did(self):
        cases = [{"phase": "landed", "was": {"phase": "enroute"}}, {"phase": "landed", "goals": [3, 3], "was": {"phase": "landed", "goals": [3, 3]}},
                 {"goals": [3, 3], "was": {"goals": [1, 3]}}, {"progress": {"current": 2}, "was": {"current": 1}},
                 {"progress": {"current": 2}, "was": {"current": 2}}, {}, {"ref": "project:tapspace"}]
        self.assertEqual(js(f"{json.dumps(cases)}.map(cardMoved)"), [True, False, True, True, False, False, False])


@unittest.skipUnless(NODE, "node is not installed")
class FirstViewTests(unittest.TestCase):
    """Contract 10 and 18: the page's own cardsOnScreen and showCards over a stubbed thread. A live arrival plays on a
    visible page, focused or not; a hidden page or another tab plays nothing and sees nothing until it shows; a card seen
    at rest never plays later; an open plays only the newest two of its unread cards, after the settle."""
    SHOW = PAGE[PAGE.index("const CARD_SETTLE = "):PAGE.index("function beginCard(")]

    def run_steps(self, focused, steps):
        # steps: [action, arg]: ['open'|'show', null] calls showCards(true|false); ['card', [rid, ts, live]] adds a card
        # to the thread (live: it arrived by a poll); ['live', rid] marks one live; ['page', {vis, chat}] changes the
        # page; ['settle', null] runs the held timers
        code = f"""(() => {{
const page = {{vis: 'visible', chat: true}}, out = [];
const document = {{body: {{classList: {{contains: c => c === 'chatmode' && page.chat}}}},
  get visibilityState(){{ return page.vis; }}, hasFocus: () => {json.dumps(focused)}}};
const tms = ts => ts * 1000, cardMsgs = new Map(), cardSeen = new Set(), cardLive = new Set(), msgsEl = {{children: []}};
const current = 'agent:M', openMark = {{key: 'agent:M', at: 5000}}, timers = [];
const setTimeout = (fn, ms) => timers.push([fn, ms]);
const beginCard = (el, m) => out.push(['play', m.rid, el.wait]);
{self.SHOW}
for (const [act, arg] of {json.dumps(steps)}){{
  if (act === 'card'){{ const [rid, ts, live] = arg, el = {{dataset: {{id: 'm' + rid}}, isConnected: true, wait: false}};
    el.classList = {{contains: c => c === 'ucard', add: c => {{ if (c === 'wait') el.wait = true; }}}};
    cardMsgs.set('m' + rid, {{rid, ts}}); if (live) cardLive.add(rid); msgsEl.children.push(el); }}
  else if (act === 'live') cardLive.add(arg);
  else if (act === 'page') Object.assign(page, arg);
  else if (act === 'settle') timers.splice(0).forEach(([fn, ms]) => {{ out.push(['settle', ms]); fn(); }});
  else showCards(act === 'open');
}}
out.push(['seen', [...cardSeen].sort((a, b) => a - b)]);
process.stdout.write(JSON.stringify(out)); }})()"""
        out = subprocess.run([NODE, "-e", code], capture_output=True, text=True, timeout=30)
        self.assertEqual(out.returncode, 0, out.stderr)
        return json.loads(out.stdout)

    def test_a_live_arrival_plays_on_a_visible_page_focused_or_not(self):
        steps = [["card", [1, 1, False]], ["open", None], ["card", [2, 9, True]], ["show", None]]
        for focused in (True, False):
            self.assertEqual(self.run_steps(focused, steps), [["play", 2, False], ["seen", [1, 2]]], focused)

    def test_a_hidden_page_or_another_tab_waits_and_then_plays(self):
        steps = [["card", [1, 1, False]], ["open", None],
                 ["page", {"vis": "hidden"}], ["card", [2, 9, True]], ["show", None],
                 ["page", {"vis": "visible", "chat": False}], ["card", [3, 10, True]], ["show", None],
                 ["page", {"chat": True}], ["show", None]]
        self.assertEqual(self.run_steps(False, steps), [["play", 2, False], ["play", 3, False], ["seen", [1, 2, 3]]])

    def test_a_card_seen_at_rest_never_plays_later(self):
        # history drawn at rest (older than the read mark, not live) is seen at once; marking it live later changes nothing
        steps = [["card", [1, 1, False]], ["open", None], ["live", 1], ["show", None], ["show", None]]
        self.assertEqual(self.run_steps(False, steps), [["seen", [1]]])

    def test_an_open_plays_the_newest_two_unread_after_the_settle(self):
        steps = [["card", [1, 1, False]], ["card", [2, 6, False]], ["card", [3, 7, False]], ["card", [4, 8, False]],
                 ["open", None], ["settle", None], ["show", None]]
        self.assertEqual(self.run_steps(False, steps),
                         [["settle", 700], ["play", 3, True], ["settle", 700], ["play", 4, True], ["seen", [1, 2, 3, 4]]])

    def test_focus_is_only_the_read_marks(self):
        gate = re.search(r"const cardsOnScreen = \(\) => (.*);", PAGE).group(1)
        self.assertIn("document.visibilityState === 'visible'", gate)
        self.assertNotIn("hasFocus", gate)


if __name__ == "__main__":
    unittest.main()
