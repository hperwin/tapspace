#!/usr/bin/env python3
"""Web rail and badges (contract sections 5, 6, 10 and 12): the lead pinned first with its fleet strip only when the
server serves a lead and a fleet, how many beads a strip holds, a master bead's ring, the O2 focus rule (A19 section 3's
table) through the page's own isUnread and the tab's news count, and a master item whose only change is its state.

Reads space/index.html only; node runs the page's pure blocks and its unread lines with stubbed page globals (skipped
where node is missing). Stdlib only.
    python3 -m unittest discover -s tests -p 'test_web_rail.py' -v
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
CSS = "\n".join(re.findall(r"<style>(.*?)</style>", PAGE, re.S))


def block(start, end):
    i = PAGE.index(start)
    return PAGE[i:PAGE.index(end, i)]


PURE = block("// ---- pure: faces", "// ---- end pure: faces ----") + \
    block("// ---- pure: update cards", "// ---- end pure: cards ----") + \
    block("// ---- pure: rail", "// ---- end pure: rail ----")
UNREAD = block("/* unread = the other side said", "/* ---- per agent:")


def js(expr, code=""):
    src = PURE + "\n" + code + "\nprocess.stdout.write(JSON.stringify(" + expr + "))"
    out = subprocess.run([NODE, "-e", src], capture_output=True, text=True, timeout=30)
    if out.returncode:
        raise AssertionError(out.stderr)
    return json.loads(out.stdout)


# The page's own unread lines (lastTheirs, readAt, markRead, onScreenKey, isUnread) over stubbed globals: masters A, B, C
# and tap T, each with a reply at t=100 s; `read` marks per key (ms); chat mode on, the window focused unless told.
def unread(focus, read, current="", focused=True, holding=()):
    stubs = f"""
const agentKey = n => 'agent:' + n, isAgent = k => k.startsWith('agent:');
const agents = ['A', 'B', 'C'].map(n => ({{name: n, last_master: 100}}));
const taps = {{items: [{{key: 'o/r#1', messages: [{{who: 'tap', ts: new Date(100000).toISOString()}}]}}]}};
const marks = {json.dumps(read)}, store = k => String(marks[k.replace('tapspace.read.', '')] || 0);
const document = {{body: {{classList: {{contains: c => c === 'chatmode'}}}}, hasFocus: () => {json.dumps(focused)}}};
let current = {json.dumps(current)}, focusKey = {json.dumps(focus)};
{UNREAD}
const keys = ['agent:A', 'agent:B', 'agent:C', 'o/r#1'];
const held = {json.dumps(list(holding))};
const threads = keys.map(k => ({{key: k, unread: lastTheirs(k) > readAt(k), holding: held.includes(k)}}));
"""
    return js("{dots: keys.filter(isUnread), count: newsCount(threads, focusKey, onScreenKey())}", stubs)


@unittest.skipUnless(NODE, "node is not installed")
class PureRailTests(unittest.TestCase):
    def test_pinned_only_with_a_lead_and_a_fleet(self):
        self.assertEqual(js("railPinned([{name: 'X'}, {name: 'M', role: 'lead'}], [])"), {"name": "M", "role": "lead"})
        self.assertIsNone(js("railPinned([{name: 'M', role: 'lead'}], undefined)"))      # an older server: no fleet
        self.assertIsNone(js("railPinned([{name: 'M'}], [{key: 'agent:M'}])"))           # no lead served
        self.assertIsNone(js("railPinned(null, [])"))

    def test_beads_fit_the_lane(self):
        self.assertEqual(js("[beadsFit(5, 268), beadsFit(20, 268), beadsFit(20, 232), beadsFit(20, 0), beadsFit(0, 240)]"),
                         [5, 8, 7, 1, 0])
        # what fits is never wider than the lane: the beads, a gap and the "+N"
        for lane in (120, 204, 240, 268):
            n = js(f"beadsFit(30, {lane})")
            self.assertLessEqual(n * 28 + 32, lane, lane)

    def test_ring_percent(self):
        self.assertEqual(js("[ringPct({done: 3, total: 4}), ringPct({done: 1, total: 0}), ringPct({done: 9, total: 4}), ringPct(null)]"),
                         [75, 0, 100, 0])


@unittest.skipUnless(NODE, "node is not installed")
class FocusTests(unittest.TestCase):
    """A19 section 3 rows 1 to 10 on the web (7 and 9 are the server's: shared reads are out of scope, a tap-say comment
    never moves focus). Nothing read means every thread has an unread reply."""
    NONE_READ = {}

    def test_1_the_conversation_he_is_having_never_badges(self):
        got = unread("agent:A", {"agent:B": 200000, "agent:C": 200000, "o/r#1": 200000})
        self.assertEqual(got, {"dots": [], "count": 0})

    def test_2_and_3_other_threads_badge(self):
        self.assertEqual(unread("agent:A", {"agent:C": 200000, "o/r#1": 200000}), {"dots": ["agent:B"], "count": 1})
        self.assertEqual(unread("agent:A", {"o/r#1": 200000}), {"dots": ["agent:B", "agent:C"], "count": 2})

    def test_4_writing_to_another_moves_the_quiet(self):
        self.assertEqual(unread("agent:B", {"o/r#1": 200000}), {"dots": ["agent:A", "agent:C"], "count": 2})

    def test_5_and_6_a_holding_tap(self):
        read = {"agent:B": 200000, "agent:C": 200000, "o/r#1": 200000}
        self.assertEqual(unread("agent:A", read, holding=["o/r#1"])["count"], 1)
        self.assertEqual(unread("o/r#1", dict(read, **{"agent:A": 200000}), holding=["o/r#1"])["count"], 0)

    def test_8_the_thread_on_screen_and_a_window_behind(self):
        read = {"agent:B": 200000, "agent:C": 200000, "o/r#1": 200000}
        self.assertEqual(unread("agent:A", read, current="agent:A", focused=False), {"dots": [], "count": 0})
        # no focus served (he never wrote): the open thread is quiet only while the window has focus, as today
        self.assertEqual(unread("", read, current="agent:A")["dots"], [])
        self.assertEqual(unread("", read, current="agent:A", focused=False)["dots"], ["agent:A"])

    def test_10_nothing_unread(self):
        read = {k: 200000 for k in ("agent:A", "agent:B", "agent:C", "o/r#1")}
        self.assertEqual(unread("", read), {"dots": [], "count": 0})

    def test_unread_and_holding_counts_once(self):
        self.assertEqual(js("newsCount([{key: 'o/r#1', unread: true, holding: true}, {key: 'o/r#1', unread: true}], '', '')"), 1)


@unittest.skipUnless(NODE, "node is not installed")
class ActivityTests(unittest.TestCase):
    """Contract section 7 on the web: the lead's thread head line from GET /api/agents activity."""
    HEAD = block("function esc(t)", "function md(src)") + "\nconst when = () => '6h';\n" + \
        block("/* O3 (contract section 7)", "function renderHead(")

    def sub(self, a):
        return js(f"headSub({json.dumps(a)})", self.HEAD)

    def test_the_face_follows_what_it_does(self):
        self.assertEqual(js("ACT_STATE"), {"working": "thinking", "switching": "switching", "waiting": "here", "idle": "away", "gone": "away"})
        self.assertEqual(js("['working', 'gone', 'planning', 'constructor', '__proto__', null].map(actKind)"),
                         ["working", "gone", "", "", "", ""])

    def test_the_head_line(self):
        lead = {"role": "lead", "first": 1}
        self.assertEqual(self.sub(dict(lead, activity=None)), "Started 6h")
        self.assertEqual(self.sub(dict(lead, activity={"state": "working", "word": "Checking a tap", "detail": "", "since": 1})),
                         '<span class="act working"><i></i><span class="aw">Checking a tap</span></span>')
        self.assertEqual(self.sub(dict(lead, activity={"state": "waiting", "word": "Listening", "detail": "Chat update cards"})),
                         '<span class="act waiting"><i></i><span class="aw">Listening · Chat update cards</span></span>')
        # contract 12: a state this page does not know still shows its word; nothing from the wire lands in a class
        self.assertEqual(self.sub(dict(lead, activity={"state": "x\" onmouseover=\"y", "word": "<b>Planning</b>"})),
                         '<span class="act"><i></i><span class="aw">&lt;b&gt;Planning&lt;/b&gt;</span></span>')
        self.assertEqual(self.sub({"role": "master", "first": 1, "activity": {"state": "working", "word": "Thinking"}}), "Started 6h")
        self.assertEqual(self.sub(dict(lead, activity={"state": "idle", "word": "  "})), "Started 6h")

    def test_head_wiring(self):
        head = block("function renderHead(", "/* ---- a tap's action card")
        self.assertIn("(act ? '' : `<span class=\"chip", head)                       # the activity line replaces the chip
        self.assertIn("w.classList.add('swap')", head)                             # the word swaps in
        self.assertIn("head.dataset.word = ''", head)                              # a thread switch never swaps the word
        self.assertIn("#panehead .aw.swap{animation:swapin", CSS)
        self.assertIn("#panehead .act.working i{animation:breathe", CSS)
        blocks = re.findall(r"@media \(prefers-reduced-motion: reduce\)\{(.*?)\n  \}", CSS, re.S)
        self.assertTrue(any("#panehead .act.working i" in b and "#panehead .aw.swap" in b for b in blocks))


@unittest.skipUnless(NODE, "node is not installed")
class MovedTests(unittest.TestCase):
    def test_a_master_moves_when_only_its_state_did(self):
        # contract 12: a state-only change gets its beat (the face pops to its new expression); nothing else moves
        cases = [{"state": "thinking", "was": {"state": "here"}},
                 {"state": "here", "progress": {"current": 2}, "was": {"state": "here", "current": 2}},
                 {"state": "away", "progress": {"current": 2}, "was": {"state": "here"}},
                 {"was": {"state": "here"}},
                 {"phase": "landed", "was": {"phase": "landed"}}]
        self.assertEqual(js(f"{json.dumps(cases)}.map(cardMoved)"), [True, False, True, False, False])


class MarkupTests(unittest.TestCase):
    def test_rail_scripts_still_parse(self):
        if not NODE:
            self.skipTest("node is not installed")
        classic = [body for module, body in SCRIPTS if not module]
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
            f.write("\n".join(classic))
        out = subprocess.run([NODE, "--check", f.name], capture_output=True, text=True, timeout=30)
        pathlib.Path(f.name).unlink()
        self.assertEqual(out.returncode, 0, out.stderr)

    def test_the_lead_comes_first(self):
        rail = block('<aside id="rail">', "</aside>")
        order = [rail.index(x) for x in ('id="leadpin"', 'id="agentshead"', 'id="agentlist"', 'id="tapshead"', 'id="taplist"')]
        self.assertEqual(order, sorted(order))
        self.assertNotIn("sticky", block("#leadpin:empty", "@keyframes spin"))      # pinned by position (contract 10)

    def test_o2_lines(self):
        self.assertIn("O2 (contract section 6)", block("function markRead", "function onScreenKey"))
        self.assertIn("!quietKey(key, focusKey, onScreenKey())", UNREAD)
        self.assertIn("newsCount(threads, focusKey, onScreenKey())", block("function renderRail(", "function threadData("))
        self.assertIn('classList.contains("chatmode") && document.hasFocus()) markRead(current)',
                      block("function renderThread(", "function msgEl("))
        self.assertIn("focusKey = typeof d.focus === 'string'", block("async function pollAgents(", "async function pollChat("))

    def test_a_bead_opens_its_thread_and_leaves_the_group_as_it_is(self):
        fleet = block("function syncFleet(", "function beadHtml(")
        self.assertIn("el.addEventListener('click', () => openThread(f.key));", fleet)
        self.assertEqual(fleet.count("setFold("), 2)                  # only the "+N" and the disclosure fold or open
        self.assertIn("store('tapspace.rail.open'", block("function setFold(", "function fitStrip("))

    def test_reduced_motion_stills_the_rail(self):
        blocks = re.findall(r"@media \(prefers-reduced-motion: reduce\)\{(.*?)\n  \}", CSS, re.S)
        self.assertTrue(any(".bead" in b and "#rail .thread.enter" in b for b in blocks))


if __name__ == "__main__":
    unittest.main()
