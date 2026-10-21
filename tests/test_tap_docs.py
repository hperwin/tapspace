"""The tap docs say only what the code does (DOSSIER A2.10): README.md's Taps bullet and AGENTS.md's
orchestrator duty 8.

Reads the two docs and server.py's source as text; imports nothing, starts nothing, calls nothing. Stdlib only.
    python3 -B -m unittest tests.test_tap_docs -v
"""
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _flat(text):
    return " ".join(text.split())


def _readme_taps():
    """README.md's Taps bullet: from "- **Taps.**" to the blank line that ends it, unwrapped."""
    lines = (ROOT / "README.md").read_text().splitlines()
    start = next(i for i, l in enumerate(lines) if l.startswith("- **Taps.**"))
    end = next(i for i in range(start, len(lines)) if not lines[i].strip())
    return _flat(" ".join(lines[start:end]))


def _agents_duty_8():
    """AGENTS.md's orchestrator duty 8 (the taps duty), one line."""
    return _flat(next(l for l in (ROOT / "AGENTS.md").read_text().splitlines() if l.startswith("8. **Taps:")))


def _passages():
    return {"README.md Taps": _readme_taps(), "AGENTS.md duty 8": _agents_duty_8()}


class TapDocs(unittest.TestCase):
    def test_no_timing_promise_before_the_live_timing_is_in(self):
        # How fast a GitHub-side change reaches the board is not measured yet (A2.10, R5 F1/F8).
        for name, text in _passages().items():
            with self.subTest(name):
                self.assertFalse("within seconds" in text.lower(), f"{name} promises 'within seconds'")


if __name__ == "__main__":
    unittest.main()
