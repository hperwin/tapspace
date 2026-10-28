#!/usr/bin/env python3
"""Web pool (contract C4): the lead's face on top of a honeycomb of the fleet's faces, talk lines that fade with age,
and the reveal card's words. Node runs the page's pure pool block: the honeycomb's rows and slots, each face's drift,
line opacity by age, which talks draw, and the reveal text with its copy rules (titles only; no '#', paths, hex ids,
em dashes or the word for a flight's agent). The markup tests pin the column's wiring.

Reads space/index.html only; node runs the pure block (skipped where node is missing). Stdlib only.
    python3 -m unittest discover -s tests -p 'test_web_pool.py' -v
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


def block(start, end):
    i = PAGE.index(start)
    return PAGE[i:PAGE.index(end, i)]


POOL = block("// ---- pure: pool", "// ---- end pure: pool ----")
MODULE = block("/* ---- the pool (Chat tab, right column)", "</script>")


def js(expr):
    code = POOL + "\nprocess.stdout.write(JSON.stringify(" + expr + "))"
    out = subprocess.run([NODE, "-e", code], capture_output=True, text=True, timeout=30)
    if out.returncode:
        raise AssertionError(out.stderr)
    return json.loads(out.stdout)


def honeycomb(count, width):
    """The C4 rule written out again: n = floor((W - 2*16 + 14) / 54); odd rows hold n - 1; rows centered; pitch 48."""
    n = max(1, (width - 32 + 14) // 54)
    slots, r = [], 0
    while len(slots) < count:
        k = min(max(1, n - 1) if r % 2 else n, count - len(slots))
        x0 = (width - (k * 54 - 14)) / 2 + 20
        slots += [(x0 + j * 54, r * 48 + 20) for j in range(k)]
        r += 1
    return n, slots


@unittest.skipUnless(NODE, "node is not installed")
class HoneycombTests(unittest.TestCase):
    def test_rows_and_slots(self):
        for width in (340, 296, 400, 260, 100, 40):
            for count in (0, 1, 4, 5, 9, 10, 27):
                got = js(f"poolLayout({count}, {width})")
                n, slots = honeycomb(count, width)
                self.assertEqual(got["n"], n, (width, count))
                self.assertEqual([(s["x"], s["y"]) for s in got["slots"]], slots, (width, count))
                rows = (slots[-1][1] - 20) // 48 + 1 if slots else 0
                self.assertEqual(got["rows"], rows)
                self.assertEqual(got["height"], (rows - 1) * 48 + 40 if rows else 0)

    def test_the_column_widths_hold_five_then_four(self):
        # both column widths (340 and 296 px) hold 5 a row: 27 members are 5, 4, 5, 4, 5, 4 in six rows
        for width in (340, 296):
            got = js(f"poolLayout(27, {width})")
            per_row = [sum(1 for s in got["slots"] if s["row"] == r) for r in range(got["rows"])]
            self.assertEqual(per_row, [5, 4, 5, 4, 5, 4])
            self.assertEqual(got["height"], 5 * 48 + 40)

    def test_odd_rows_sit_half_a_cell_over_and_every_row_is_centered(self):
        got = js("poolLayout(9, 340)")["slots"]
        self.assertEqual(got[5]["x"] - got[0]["x"], 27)                 # half of the 54 px cell
        for r in (0, 1):
            row = [s["x"] for s in got if s["row"] == r]
            self.assertAlmostEqual((row[0] + row[-1]) / 2, 170)          # centered on the column
        last = js("poolLayout(7, 340)")["slots"][5:]                     # a part row is centered too
        self.assertAlmostEqual((last[0]["x"] + last[-1]["x"]) / 2, 170)

    def test_no_two_faces_touch(self):
        for width in (340, 296):
            s = js(f"poolLayout(27, {width})")["slots"]
            gaps = [((a["x"] - b["x"]) ** 2 + (a["y"] - b["y"]) ** 2) ** .5 for i, a in enumerate(s) for b in s[i + 1:]]
            self.assertGreaterEqual(min(gaps), 40 * 1.12)               # even a hovered face (1.12) clears its neighbours
            self.assertTrue(all(16 <= x["x"] - 20 and x["x"] + 20 <= width - 16 for x in s))

    def test_each_face_drifts_on_its_own_beat(self):
        got = js("[0, 1, 4, 5, 9].map(bobOf)")
        self.assertEqual(got, [{"dur": 3.6, "delay": 0}, {"dur": 3.95, "delay": -0.6}, {"dur": 5, "delay": -2.4},
                               {"dur": 3.6, "delay": -3}, {"dur": 5, "delay": -5.4}])


@unittest.skipUnless(NODE, "node is not installed")
class TalkTests(unittest.TestCase):
    def test_opacity_by_age(self):
        self.assertEqual(js("[0, 60, 300, 600, 601].map(talkAlpha)"), [0.9, 0.9, 0.5667, 0.15, 0])
        self.assertEqual(js("[-5, null, 'x'].map(talkAlpha)"), [0.9, 0.9, 0.9])   # a clock a little ahead reads fresh

    def test_one_line_per_member_with_the_lead(self):
        talks = [
            {"id": "m1", "from": "agent:Muddi", "to": "agent:B", "ts": 990, "text": "check the build", "kind": "message"},
            {"id": "m0", "from": "agent:B", "to": "agent:Muddi", "ts": 900, "text": "older", "kind": "message"},
            {"id": "t1", "from": "agent:Muddi", "to": "snappstats/snappy#3286", "ts": 980, "text": "Flying it", "kind": "tap"},
            {"id": "x1", "from": "agent:B", "to": "agent:C", "ts": 995, "text": "not the lead", "kind": "message"},
            {"id": "x2", "from": "agent:Muddi", "to": "agent:Gone", "ts": 995, "text": "not in the pool", "kind": "message"},
            {"id": "x3", "from": "agent:Muddi", "to": "agent:C", "ts": 300, "text": "too old", "kind": "message"},
            {"id": "c1", "from": "agent:C", "to": "agent:Muddi", "ts": 950, "text": "", "kind": "message"}]
        keys = ["agent:B", "agent:C", "SnappStats/snappy#3286"]
        got = js(f"[...poolTalks({json.dumps(talks)}, 'agent:Muddi', {json.dumps(keys)}, 1000)]")
        self.assertEqual(dict(got), {
            "agent:B": {"id": "m1", "ts": 990, "text": "check the build", "fromLead": True},
            "SnappStats/snappy#3286": {"id": "t1", "ts": 980, "text": "Flying it", "fromLead": True},   # keyed as served
            "agent:C": {"id": "c1", "ts": 950, "text": "", "fromLead": False}})
        self.assertEqual(js("poolTalks(null, 'agent:Muddi', ['agent:B'], 1000).size"), 0)

    def test_masters_first_then_taps_in_server_order(self):
        fleet = [{"key": "o/r#1", "kind": "tap"}, {"key": "agent:A", "kind": "master"}, {"key": "o/r#2", "kind": "tap"},
                 {"key": "agent:B", "kind": "master"}, None, {"kind": "master"}]
        self.assertEqual([f["key"] for f in js(f"poolOrder({json.dumps(fleet)})")], ["agent:A", "agent:B", "o/r#1", "o/r#2"])
        self.assertEqual(js("poolOrder(null)"), [])


BAD = re.compile(r"#|/\w|\u2014|\bpilot", re.I)


@unittest.skipUnless(NODE, "node is not installed")
class RevealTests(unittest.TestCase):
    STEPS = {"goal": "Ship the rail", "steps": ["Audit", "Build in ~/Developer/tapspace/server.py", "Review"], "current": 1}

    def lines(self, expr):
        got = js(expr)
        for line in got:
            self.assertNotRegex(line["text"], BAD)
        return [(x["k"], x["text"]) for x in got]

    def test_a_master_with_a_goal(self):
        a = {"title": "Darian + Julian store pages", "state": "here", "brief": "Build the store pages", "progress": self.STEPS}
        got = self.lines(f"masterLines({json.dumps(a)}, null, 'Here', {{ts: 880, text: 'check the build', fromLead: true}}, 'Muddi', 1000)")
        self.assertEqual(got, [("title", "Darian + Julian store pages"), ("word", "Here"), ("about", "Ship the rail"),
                               ("step", "Step 2 of 3 · Build in"), ("talk", "Muddi: check the build · 2m")])

    def test_a_master_with_a_brief_only(self):
        a = {"title": "Muddi restart fix", "state": "thinking", "progress": None,
             "brief": "Fix #42 in hperwin/tapspace \u2014 restart must say why (see ~/orchestration/x.md)"}
        got = self.lines(f"masterLines({json.dumps(a)}, null, 'Thinking', {{ts: 990, text: '', fromLead: false}}, 'Muddi', 1000)")
        self.assertEqual(got, [("title", "Muddi restart fix"), ("word", "Thinking"), ("about", "Fix in, restart must say why (see)"),
                               ("talk", "Message from Muddi restart fix · now")])

    def test_a_master_with_neither(self):
        f = {"key": "agent:X", "kind": "master", "title": "Shawn push + research", "state": "here"}
        self.assertEqual(self.lines(f"masterLines(undefined, {json.dumps(f)}, 'Here', null, 'Muddi', 1000)"),
                         [("title", "Shawn push + research"), ("word", "Here")])
        done = dict(self.STEPS, current=3)
        self.assertEqual(self.lines(f"masterLines({{title: 'T', progress: {json.dumps(done)}}}, null, '', null, 'Muddi', 0)")[1:],
                         [("about", "Ship the rail"), ("step", "All 3 steps done")])

    def test_a_tap_with_an_ask(self):
        t = {"key": "SnappStats/snappy#3286", "title": "[snappy] Lineup alerts explain empty slots", "phase": "holding",
             "phase_word": "needs you", "goals": [{"text": "A staging dry run", "done": True}, {"text": "Prod proof on #3286", "done": False}],
             "ask": {"text": "Merge PR #3301 now?", "url": "https://github.com/x", "at": 1},
             "messages": [{"who": "tap", "body": "Holding for your go (pilot)\n\nmore", "ts": "2026-10-22T15:12:08Z"}]}
        f = {"key": t["key"], "kind": "tap", "needs_you": True, "word": "needs you"}
        self.assertEqual(self.lines(f"tapLines({json.dumps(t)}, {json.dumps(f)})"),
                         [("title", "Lineup alerts explain empty slots"), ("word", "Needs you"), ("goals", "1 of 2 goals done"),
                          ("goal", "Prod proof on"), ("ask", "Merge PR now?"), ("last", "Holding for your go (tap)")])

    def test_a_tap_without_an_ask(self):
        t = {"title": "Ship the pool", "phase": "landed", "phase_word": "ready for sign-off", "ask": None,
             "goals": [{"text": "One", "done": True}, {"text": "Two", "done": True}], "messages": []}
        self.assertEqual(self.lines(f"tapLines({json.dumps(t)}, {{needs_you: true}})"),
                         [("title", "Ship the pool"), ("word", "Ready for sign-off"), ("goals", "2 of 2 goals done"), ("goal", "Two")])
        # the taps not loaded yet: the fleet item's own words and counts
        f = {"key": "o/r#9", "kind": "tap", "title": "Fix the feed", "word": "flying", "done": 1, "total": 3}
        self.assertEqual(self.lines(f"tapLines(null, {json.dumps(f)})"),
                         [("title", "Fix the feed"), ("word", "Flying"), ("goals", "1 of 3 goals done")])

    def test_copy_rules(self):
        said = ["See https://github.com/hperwin/tapspace/pull/14 \u2014 done", "Commit 1e18015bf4 on d7ddc5fa-55fe-4441-9d3a-1a630a5356c5",
                "Edit ~/Developer/tapspace/space/index.html and ./run.sh", "SnappStats/snappy#3286 landed", "The pilots are up", "@hperwin DEBRIEF: all four goals proven"]
        got = js(f"{json.dumps(said)}.map(s => plain(s, 80))")
        for line in got:
            self.assertNotRegex(line, BAD)
        self.assertEqual(got, ["See, done", "Commit on", "Edit and", "landed", "The taps are up", "DEBRIEF: all four goals proven"])
        self.assertEqual(js("plain('Ship and/or test A/B in hperwin/tapspace 24/7', 80)"), "Ship and/or test A/B in 24/7")
        self.assertEqual(js("plain('one two three four five six', 14)"), "one two three…")
        self.assertEqual(js("[plain(null), plain(''), plain('\\n\\n  ## Second line')]"), ["", "", "Second line"])


class MarkupTests(unittest.TestCase):
    def test_the_column_is_the_pool(self):
        col = block('<aside id="progress"', "</aside>")
        for x in ('id="pool"', 'class="links"', 'id="poollead"', 'id="poolfaces"', 'id="poolcard"'):
            self.assertIn(x, col)
        self.assertNotIn("Progress</div>", col)                          # the header and the cards are gone
        self.assertNotIn("class=\"plan", PAGE)
        self.assertIn("#progress{display:none}", block("@media (max-width:1180px)", "}\n"))   # hides below the breakpoint as today

    def test_sizes_and_motion(self):
        self.assertIn("const POOL = {face: 40, gap: 14, pad: 16, pitch: 48};", POOL)
        self.assertIn(".pm.on .pf{transform:scale(1.12)", CSS)
        self.assertIn("#poolfaces.revealing .pm:not(.on) .pf > *{opacity:.5}", CSS)
        self.assertIn("#poolfaces{position:relative;z-index:2;margin-top:28px}", CSS)
        self.assertIn(".links{position:absolute;left:0;top:0;width:100%;height:0;overflow:visible;pointer-events:none;z-index:1}", CSS)
        self.assertIn("--s:64px", MODULE)
        self.assertIn("--s:40px", MODULE)
        self.assertIn("@keyframes bob{0%,100%{transform:none}50%{transform:translateY(var(--bob,-3px))}}", CSS)
        self.assertIn(".plead .bob{--bob:-2px;animation-duration:4.2s}", CSS)
        rm = re.findall(r"@media \(prefers-reduced-motion: reduce\)\{(.*?)\n  \}", CSS, re.S)
        self.assertTrue(any("#pool .bob" in b and ".pcard" in b for b in rm))
        # arrival on the page's pop curve, 40 ms apart capped at 6; removal 0.18 s to 0.9; the draw on the stroke curve
        self.assertIn("delay: Math.min(i, 6) * 40, ease: 'cubicBezier(.3,1.4,.5,1)'", MODULE)
        self.assertIn("{opacity: 0, scale: 0.9, duration: 180, ease: 'in(2)'", MODULE)
        self.assertIn("draw: ['0 0', '0 1'], duration: 600, ease: 'cubicBezier(.4,0,.2,1)'", MODULE)
        self.assertIn("draw: ['0 0', '0 1', '1 1'], duration: 1100", MODULE)
        self.assertNotIn("loop: true", MODULE)                            # one chase per talk, never a loop

    def test_wiring(self):
        self.assertIn("if ((e.pointerType === 'touch' || touch) && revealed !== key){ reveal(key); return; }", MODULE)
        self.assertIn("if (Date.now() - downAt > 500) reveal(key);", MODULE)                 # a press never reveals by focus
        self.assertIn("openThread(key);", MODULE)
        self.assertIn("if (lead) openThread(agentKey(lead.name));", MODULE)
        self.assertIn("talks = Array.isArray(d.talks) ? d.talks : [];", block("async function pollAgents(", "async function pollChat("))
        self.assertIn("window.tsPanel?.render(agents, links, fleet, taps.items, talks);", block("async function pollTaps(", "// Visual tab badge"))

    def test_copy(self):
        for x in (MODULE, CSS):
            self.assertNotIn("\u2014", x)
        words = re.findall(r"'([^'\n]*)'", MODULE)
        self.assertFalse([w for w in words if re.search(r"\bpilot", w, re.I)])
        self.assertIn("'Nothing under ' + (lead.title || 'Muddi') + ' yet'", MODULE)
        self.assertIn("'No lead yet'", MODULE)


if __name__ == "__main__":
    unittest.main()
