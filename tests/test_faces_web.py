#!/usr/bin/env python3
"""Web faces (contract R7): the 18-look table every client shares, the eye, mouth and mark paths on Glint's 88-unit
grid, the tones, the repo letter, and every master face drawn through its look.

Reads space/index.html only; node runs the page's pure faces block (skipped where node is missing). Stdlib only.
    python3 -m unittest discover -s tests -p 'test_faces_web.py' -v
"""
import json
import pathlib
import re
import shutil
import subprocess
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
PAGE = (ROOT / "space/index.html").read_text()
NODE = shutil.which("node")

# The shared table (P2 2.2; the same pairs as Glint FaceLook.table and the Mac app): [tone, mark]
LOOKS = [(0, 0), (0, 1), (1, 2), (2, 3), (0, 2), (1, 3), (2, 4), (0, 3), (1, 4), (2, 5), (0, 4), (1, 5), (2, 1), (0, 5),
         (1, 1), (2, 2), (1, 0), (2, 0)]
MARKS = ["plain", "glasses", "antenna", "bowtie", "cheeks", "brows"]
# The paths, copied from the campaign's faces-data.json (this test never reads campaign files)
EYES = {"happy": "M25 37a4 4 0 1 0 8 0a4 4 0 1 0 -8 0M55 37a4 4 0 1 0 8 0a4 4 0 1 0 -8 0",
        "focused": "M25 37a4 4 0 1 0 8 0a4 4 0 1 0 -8 0M55 37a4 4 0 1 0 8 0a4 4 0 1 0 -8 0",
        "wow": "M23 37a5 5 0 1 0 10 0a5 5 0 1 0 -10 0M55 37a5 5 0 1 0 10 0a5 5 0 1 0 -10 0",
        "sleepy": "M24.5 35.5H31.5a1.5 1.5 0 0 1 0 3H24.5a1.5 1.5 0 0 1 0 -3ZM56.5 35.5H63.5a1.5 1.5 0 0 1 0 3H56.5a1.5 1.5 0 0 1 0 -3Z"}
MOUTH = {"happy": "M32 53Q44 67 56 53", "focused": "M35.6 56H52.4", "wow": "M39 57a5 5 0 1 0 10 0a5 5 0 1 0 -10 0",
         "sleepy": "M40.4 53.5H47.6"}
MARK_PATHS = [
    "", "",
    "M20 37a9 9 0 1 0 18 0a9 9 0 1 0 -18 0M50 37a9 9 0 1 0 18 0a9 9 0 1 0 -18 0M37.5 34Q44 30.5 50.5 34", "",
    "M44 25V17", "M39.5 13a4.5 4.5 0 1 0 9 0a4.5 4.5 0 1 0 -9 0",
    "M36.5 70L44 73.5L36.5 77ZM51.5 70L44 73.5L51.5 77Z", "M36.5 70L44 73.5L36.5 77ZM51.5 70L44 73.5L51.5 77Z",
    "", "M15 51a4 2.6 0 1 0 8 0a4 2.6 0 1 0 -8 0M65 51a4 2.6 0 1 0 8 0a4 2.6 0 1 0 -8 0",
    "M24 25H34M54 25H64", ""]


def block(start, end):
    i = PAGE.index(start)
    return PAGE[i:PAGE.index(end, i)]


FACES = block("// ---- pure: faces", "// ---- end pure: faces ----")


def js(expr):
    code = FACES + "\nprocess.stdout.write(JSON.stringify(" + expr + "))"
    out = subprocess.run([NODE, "-e", code], capture_output=True, text=True, timeout=30)
    if out.returncode:
        raise AssertionError(out.stderr)
    return json.loads(out.stdout)


class WebFaceTests(unittest.TestCase):
    def test_table_is_every_tone_and_mark_once(self):
        self.assertEqual(set(LOOKS), {(t, m) for t in range(3) for m in range(6)})
        self.assertEqual(len(LOOKS), 18)
        self.assertEqual(LOOKS[0], (0, 0))                      # the lead keeps today's face

    def test_page_table_matches(self):
        got = json.loads(re.search(r"const FACE_LOOKS = (\[\[.*?\]\]);", PAGE).group(1))
        self.assertEqual([tuple(x) for x in got], LOOKS)

    def test_page_paths_match_the_spec(self):
        marks = re.search(r"const FACE_MARK = \[(.*?)\];", PAGE, re.S).group(1)
        self.assertEqual(re.findall(r"'([^']*)'", marks), MARK_PATHS)
        self.assertIn("// [stroked, filled]: " + ", ".join(MARKS), FACES)
        for name, d in {**{"eye " + k: v for k, v in EYES.items()}, **{"mouth " + k: v for k, v in MOUTH.items()}}.items():
            self.assertIn(f"'{d}'", FACES, name)

    def test_tones_come_before_asleep(self):
        # same specificity, so asleep must come later to win over every tone
        self.assertLess(PAGE.index(".orb.t1{"), PAGE.index(".orb.away{"))
        self.assertLess(PAGE.index(".orb.t2{"), PAGE.index(".orb.away{"))

    def test_every_master_face_goes_through_the_look(self):
        self.assertNotIn('class="orb ${a.state}"', PAGE)
        self.assertNotIn("'orb ' + (a.state", PAGE)
        self.assertIn("${masterFace(a)}", PAGE)                  # the rail row and the thread head
        self.assertIn("c.orb.className !== cls", PAGE)           # the Progress card keeps its node and sets the look


@unittest.skipUnless(NODE, "node is not installed")
class WebFaceDrawTests(unittest.TestCase):
    def test_tones_and_defaults(self):
        self.assertEqual(js("[orbCls({}), orbCls({look: 2, state: 'here'}), orbCls({look: 3, state: 'thinking'})]"),
                         ["orb t0 away", "orb t1 here", "orb t2 thinking"])
        # a missing, wrong or out-of-range look draws as look 0
        self.assertEqual(js("[faceLook(undefined), faceLook(-1), faceLook(18), faceLook('4'), faceLook(4)]"),
                         [[0, 0], [0, 0], [0, 0], [0, 0], [0, 2]])

    def test_expressions_follow_state(self):
        got = js("['here','thinking','starting','switching','away','','gone'].map(s => faceArt(0, s))")
        want = ["happy", "focused", "wow", "wow", "sleepy", "sleepy", "sleepy"]
        for svg, x in zip(got, want):
            self.assertIn(f'<path d="{EYES[x]}"/>', svg)
            self.assertIn(f'<path class="ln" d="{MOUTH[x]}"/>', svg)

    def test_marks_are_stroked_and_filled(self):
        glasses, antenna, cheeks, plain = js("[faceArt(1, 'here'), faceArt(2, 'here'), faceArt(6, 'here'), faceArt(0, 'here')]")
        self.assertIn(f'<path class="ln" d="{MARK_PATHS[2]}"/>', glasses)
        self.assertIn(f'<path class="ln" d="{MARK_PATHS[4]}"/>', antenna)
        self.assertIn(f'<path d="{MARK_PATHS[5]}"/>', antenna)
        self.assertIn(f'<path d="{MARK_PATHS[9]}"/>', cheeks)
        self.assertNotIn(f'class="ln" d="{MARK_PATHS[9]}"', cheeks)    # cheeks are only filled
        self.assertEqual(plain.count("<path"), 2)                        # eyes and mouth

    def test_master_face_markup(self):
        self.assertEqual(js("masterFace({look: 5, state: 'away'}, ' style=\"--s:26px\"').slice(0, 53)"),
                         '<span class="orb t1 away" style="--s:26px"><svg class')

    def test_initial_is_the_servers_rule(self):
        names = ["SnappStats/snapp-ios#1583", "SnappStats/snapp-ios", "hperwin/tapspace", "tapspace", ".github",
                 "owner/_9lives", "über", "owner/", "", None]
        self.assertEqual(js(f"{json.dumps(names)}.map(initialOf)"), ["S", "S", "T", "T", "G", "9", "Ü", "?", "?", "?"])


if __name__ == "__main__":
    unittest.main()
