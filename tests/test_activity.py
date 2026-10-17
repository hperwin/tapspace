#!/usr/bin/env python3
"""Live activity (activity.py): what the lead is doing right now, from its own session's transcript; plus the card
words lane T7 owns in server.py (a proof caption as a label, a tap title cut in answer bodies).

Every fixture line copies a shape seen on real Claude Code 2.1.296 to 2.1.302 transcripts (audit A18 section 2.2),
written into a throwaway ~/.claude (projects/ + the sessions/ registry) and a throwaway data dir. Nothing here reads
the real ~/.claude, talks to :4747 or runs a real Claude session. Stdlib only.
    python3 -m unittest tests.test_activity -v        (or: python3 -m unittest discover -s tests -v)
"""
import importlib.util
import json
import os
import pathlib
import re
import sys
import tempfile
import threading
import time
import types
import unittest
import urllib.parse
import urllib.request
from unittest import mock

sys.dont_write_bytecode = True
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import activity  # noqa: E402
import links  # noqa: E402

T0 = 1792190000.0                       # October 16, 2026, 3:33:20 PM PT
LEAD = "Muddi"
SA = "aaaaaaaa-1111-4111-8111-111111111111"
SB = "bbbbbbbb-2222-4222-8222-222222222222"
PA, PB = 4446, 5550
STARTED_A, STARTED_B = "Fri Oct 16 13:03:15 2026", "Fri Oct 16 15:20:01 2026"
MASTER = "Claude · tapspace · 75c7"
TITLES = {LEAD: "Muddi", MASTER: "Chat update cards", "Claude · home · e3cc": "Claude"}
TAPS = [{"key": "SnappStats/snapp-ios#1583", "title": "MLB lineup card shows #1583 lineups"},
        {"key": "SnappStats/snappy#3157", "title": "Landing page for trysnapp.ai"}]


def iso(ts):
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(ts)) + ".%03dZ" % round(ts % 1 * 1000)


def dump(o):
    return json.dumps(o, separators=(",", ":"), ensure_ascii=False)   # Claude Code writes compact JSON


def prompt(ts, origin="human", content="How is it going?"):
    """The user line that opens a turn."""
    return {"parentUuid": "p0", "type": "user", "timestamp": iso(ts), "promptSource": "system", "turnOrigin": origin,
            "turnPosition": {"promptIndex": 1, "turnIndex": 52}, "message": {"role": "user", "content": content}}


def notice(ts, task, call=None, summary='Background command "Listen" completed (exit code 0)', status="completed"):
    """A task notification turn: a background command, a helper or a team finished (call: the tool use that armed it)."""
    body = f"<task-notification>\n<task-id>{task}</task-id>\n" + \
        (f"<tool-use-id>{call}</tool-use-id>\n<output-file>/private/tmp/x/tasks/{task}.output</output-file>\n"
         f"<status>{status}</status>\n" if call else "") + f"<summary>{summary}</summary>\n</task-notification>"
    return prompt(ts, "task_notification", body)


def tool(ts, call, name, **inp):
    return {"parentUuid": "p1", "type": "assistant", "timestamp": iso(ts), "requestId": "req_1", "apiBlockIndex": 1,
            "message": {"id": "msg_1", "role": "assistant", "model": "claude-opus-5-5", "stop_reason": "tool_use",
                        "content": [{"type": "tool_use", "id": call, "name": name, "input": inp,
                                     "caller": {"type": "direct"}}]}}


def bash(ts, call, command, bg=False):
    return tool(ts, call, "Bash", command=command, description="Run it", **({"run_in_background": True} if bg else {}))


def result(ts, call, **r):
    return {"parentUuid": "p2", "type": "user", "timestamp": iso(ts), "sourceToolAssistantUUID": "a1",
            "message": {"role": "user", "content": [{"tool_use_id": call, "type": "tool_result", "content": "ok",
                                                     "is_error": False}]},
            "toolUseResult": r or {"stdout": "ok", "stderr": "", "interrupted": False, "isImage": False,
                                   "noOutputExpected": False}}


def request(ts):
    return {"type": "api-request", "id": "r1", "timestamp": iso(ts), "querySource": "repl_main_thread"}


def ended(ts):
    return [{"parentUuid": "p3", "type": "system", "subtype": "stop_hook_summary", "hookCount": 2,
             "preventedContinuation": False, "timestamp": iso(ts - 0.01)},
            {"parentUuid": "p3", "type": "system", "subtype": "turn_duration", "durationMs": 9000, "messageCount": 40,
             "timestamp": iso(ts)}]


def queued(ts, text="hi"):
    return {"type": "queue-operation", "operation": "enqueue", "timestamp": iso(ts), "content": text}


class Rig(unittest.TestCase):
    """A throwaway ~/.claude and data dir, a Reader on an injected clock, pids alive by fiat and a fake ps."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = pathlib.Path(tmp.name)
        self.claude, self.data = root / "claude", root / "data"
        (self.claude / "projects" / "-Users-x").mkdir(parents=True)
        (self.claude / "sessions").mkdir()
        self.data.mkdir()
        self.claims = self.data / "sessions.json"
        self.now = T0
        self.alive = {PA, PB}
        self.starts = {PA: STARTED_A, PB: STARTED_B}
        self.asked = []                                  # pids the fake ps was asked about
        self.info = {"name": LEAD, "role": "lead", "listen": T0 - 5}
        self.state, self.waits = "here", False
        self.mtime = time.time()
        self.r = activity.Reader(claude_dir=self.claude, clock=lambda: self.now, alive=lambda pid: pid in self.alive)

    def ps(self, pid):
        self.asked.append(pid)
        return self.starts.get(pid, "")

    def claim(self, sid, pid, first, name=LEAD, started=None, seen=None):
        c = json.loads(self.claims.read_text()) if self.claims.exists() else {"sessions": {}, "campaigns": {}}
        c["sessions"][sid] = {"name": name, "pid": pid, "pid_start": started or self.starts.get(pid, ""),
                              "first": first, "seen": seen or first, "how": "listen"}
        self.claims.write_text(json.dumps(c, indent=1))
        self.mtime += 1
        os.utime(self.claims, (self.mtime, self.mtime))          # every write is a change the reader sees

    def register(self, pid, sid, name="haydenerwin-76"):
        (self.claude / "sessions" / f"{pid}.json").write_text(dump(
            {"pid": pid, "sessionId": sid, "cwd": "/Users/x", "startedAt": int((T0 - 7200) * 1000),
             "kind": "interactive", "name": name, "status": "busy"}))
        (self.claude / "sessions" / f"{pid}.0123abcd.key").write_text("not json, never read")

    def write(self, sid, *lines, folder="-Users-x"):
        flat = [x for o in lines for x in (o if isinstance(o, list) else [o])]
        (self.claude / "projects" / folder).mkdir(parents=True, exist_ok=True)
        with (self.claude / "projects" / folder / f"{sid}.jsonl").open("a") as fh:
            fh.write("".join(dump(o) + "\n" for o in flat))

    def raw(self, sid, data, folder="-Users-x"):
        with (self.claude / "projects" / folder / f"{sid}.jsonl").open("ab") as fh:
            fh.write(data)

    def live(self, sid=SA, pid=PA, first=T0 - 7200):
        """The lead's session: claimed, registered, its last turn over."""
        self.claim(sid, pid, first)
        self.register(pid, sid)
        self.write(sid, prompt(first + 10, "human"), request(first + 10.1), ended(first + 20))

    def act(self, **kw):
        return self.r.of(LEAD, self.info, self.state, self.claims, self.ps, waits=lambda: self.waits,
                         titles=lambda: TITLES, taps=TAPS, **kw)

    def word(self):
        a = self.act()
        return (a["state"], a["word"]) if a else None


class Turns(Rig):
    def test_a_running_tool_is_the_word_from_its_own_line(self):
        self.live()
        self.write(SA, prompt(T0 - 10, "scheduled"), request(T0 - 9.9), bash(T0 - 8, "c1", "$MUDDI/muddi.sh fleet"))
        self.assertEqual(self.act(), {"state": "working", "word": "Checking the masters", "detail": "",
                                      "since": round(T0 - 8, 1)})

    def test_when_the_tool_returns_it_is_thinking(self):
        self.live()
        self.write(SA, prompt(T0 - 10, "scheduled"), bash(T0 - 8, "c1", "ls ~/x"), result(T0 - 6, "c1"),
                   request(T0 - 5.9))
        self.assertEqual(self.act(), {"state": "working", "word": "Thinking", "detail": "", "since": round(T0 - 6, 1)})

    def test_what_woke_it_holds_until_the_turns_first_call(self):
        self.live()
        self.write(SA, request(T0 - 0.6), prompt(T0 - 0.5, "human"))            # the request can come first
        held = {"state": "working", "word": "Reading your message", "detail": "", "since": round(T0 - 0.5, 1)}
        self.assertEqual(self.act(), held)
        self.now = T0 + 20                                                       # long past a second, no call yet
        self.write(SA, request(T0 + 19))
        self.assertEqual(self.act(), held)
        self.write(SA, bash(T0 + 21, "c1", "ls ~/x"))
        self.now = T0 + 22
        self.assertEqual(self.word(), ("working", "Reading files"))
        self.write(SA, result(T0 + 23, "c1"), request(T0 + 23.1))
        self.now = T0 + 24
        self.assertEqual(self.act(), {"state": "working", "word": "Thinking", "detail": "", "since": round(T0 + 23, 1)})
        # a call the line never shows (a background command, a tool search) ends it too
        for k, call in enumerate((lambda t: bash(t, "b1", "python3 $TAP listen --as Muddi", bg=True),
                                  lambda t: tool(t, "ts", "ToolSearch", query="select:Monitor", max_results=1))):
            t = T0 + 30 + 10 * k
            self.write(SA, ended(t - 1), prompt(t, "scheduled"))
            self.now = t + 0.5
            self.assertEqual(self.word(), ("working", "Checking in"))
            self.write(SA, call(t + 1))
            self.now = t + 2
            self.assertEqual(self.act(), {"state": "working", "word": "Thinking", "detail": "", "since": round(t + 1, 1)})
        # and so does the turn's end
        self.write(SA, ended(T0 + 49), prompt(T0 + 50, "scheduled"), ended(T0 + 51))
        self.now, self.info["listen"] = T0 + 52, T0 + 50
        self.assertEqual(self.word(), ("waiting", "Listening"))

    def test_what_woke_it(self):
        self.live()
        self.write(SA, prompt(T0 - 300, "human"),
                   bash(T0 - 299, "lis", 'python3 $TAP listen --as Muddi --timeout 3600', bg=True),
                   result(T0 - 299, "lis", backgroundTaskId="bkm1", stdout="", stderr="", interrupted=False),
                   bash(T0 - 298, "tw", "python3 ~/orchestration/tapspace/tap.py tap-watch --timeout 3600", bg=True),
                   result(T0 - 298, "tw", backgroundTaskId="btw1", stdout=""),
                   tool(T0 - 297, "mon", "Monitor", command="$MUDDI/muddi.sh fleet --watch", description="Fleet",
                        timeout_ms=3600000),
                   result(T0 - 297, "mon", persistent=True, taskId="mfl1", timeoutMs=3600000),
                   tool(T0 - 296, "ag", "Agent", description="Read the order", prompt="...", subagent_type="Explore",
                        run_in_background=True),
                   result(T0 - 296, "ag", agentId="a5c0ffee1234", isAsync=True, outputFile="/x/tasks/a5c0ffee1234.output"),
                   tool(T0 - 295, "wf", "Workflow", script="x.js"),
                   result(T0 - 295, "wf", runId="r1", taskId="wteam1", status="running"), ended(T0 - 290))
        cases = [
            (prompt(T0, "scheduled"), "Checking in"),
            (prompt(T0, "peer", "Another Claude session sent a message"), "Reading a master's message"),
            (prompt(T0, "human"), "Reading your message"),
            (prompt(T0, "system", "Resumed"), "Reading what came in"),
            (notice(T0, "bkm1", "lis"), "Reading your message"),
            (notice(T0, "bkm1", "lis", 'Background command "Listen" failed with exit code 4', "failed"), "Checking in"),
            (notice(T0, "btw1", "tw"), "Seeing a tap change"),
            (notice(T0, "mfl1", summary='Monitor event: "Fleet"'), "Seeing a master move"),
            (notice(T0, "a5c0ffee1234", "ag", 'Agent "Read the order" finished'), "Reading a helper's findings"),
            (notice(T0, "wteam1", "wf", 'Dynamic workflow "Audit" completed'), "Reading a team's findings"),
            (notice(T0, "zzz9", "zz", 'Agent "From before the tail" finished'), "Reading a helper's findings"),
            (notice(T0, "zzz9", summary='Monitor event: "Something"'), "Reading what came in"),
        ]
        for line, want in cases:
            with self.subTest(want=want, origin=line["turnOrigin"]):
                self.write(SA, line)
                self.now = T0 + 0.5
                self.assertEqual(self.word(), ("working", want))
                self.write(SA, ended(T0 + 0.6))

    def test_a_background_listen_is_never_the_word(self):
        self.live()
        self.write(SA, prompt(T0 - 10, "human"),
                   bash(T0 - 9, "lis", "python3 ~/orchestration/tapspace/tap.py listen --as Muddi --timeout 3600", bg=True))
        self.assertEqual(self.word(), ("working", "Thinking"))
        self.write(SA, result(T0 - 9, "lis", backgroundTaskId="bkm1"), ended(T0 - 8))
        self.assertEqual(self.word(), ("waiting", "Listening"))

    def test_turn_over_listening_waiting_on_you_idle(self):
        self.live()
        self.write(SA, prompt(T0 - 20), bash(T0 - 19, "r1", 'python3 $TAP card --as Muddi "Ready?" --ask "Ship it?"'),
                   result(T0 - 18, "r1"), ended(T0 - 17))
        self.assertEqual(self.act(), {"state": "waiting", "word": "Listening", "detail": "", "since": round(T0 - 17, 1)})
        self.waits = True
        self.assertEqual(self.word(), ("waiting", "Waiting on you"))
        self.info["listen"] = T0 - activity.LISTEN_FRESH - 1
        self.assertEqual(self.word(), ("idle", "Idle"))

    def test_bookkeeping_keeps_the_word(self):
        self.live()
        self.write(SA, prompt(T0 - 10), bash(T0 - 8, "c1", "$MUDDI/muddi.sh ledger desk 'noted'"),
                   tool(T0 - 7, "ts", "ToolSearch", query="select:Monitor", max_results=1))
        self.assertEqual(self.word(), ("working", "Logging progress"))
        self.write(SA, result(T0 - 6, "c1"), result(T0 - 6, "ts"))
        self.assertEqual(self.word(), ("working", "Thinking"))

    def test_parallel_calls_the_newest_still_running_leads(self):
        self.live()
        self.write(SA, prompt(T0 - 10), bash(T0 - 8, "c1", "ls ~/x"),
                   bash(T0 - 8, "c2", "gh issue view 3157 -R SnappStats/snappy --json state"))
        self.assertEqual(self.act()["word"], "Checking a tap")
        self.write(SA, result(T0 - 7, "c2"))
        self.assertEqual(self.act()["word"], "Reading files")
        self.write(SA, result(T0 - 6, "c1"))
        self.assertEqual(self.act()["word"], "Thinking")

    def test_reading_a_finished_task_by_what_it_was(self):
        self.live()
        self.write(SA, prompt(T0 - 60), bash(T0 - 59, "lis", "python3 $TAP listen --as Muddi", bg=True),
                   result(T0 - 59, "lis", backgroundTaskId="bkm1"), ended(T0 - 58),
                   notice(T0 - 5, "bkm1", "lis"), bash(T0 - 4, "c1", "cat /private/tmp/claude-501/x/tasks/bkm1.output"))
        self.assertEqual(self.act()["word"], "Reading your message")
        self.write(SA, result(T0 - 3, "c1"), bash(T0 - 2, "c2", "cat /private/tmp/claude-501/x/tasks/b0ld.output"))
        self.assertEqual(self.act()["word"], "Reading what came in")
        self.write(SA, result(T0 - 1, "c2"), tool(T0 - 0.5, "c3", "Read", file_path="/private/tmp/x/tasks/bkm1.output"))
        self.assertEqual(self.act()["word"], "Reading your message")

    def test_a_turn_end_written_mid_turn_reopens_with_the_next_call(self):
        self.live()
        self.write(SA, prompt(T0 - 20), tool(T0 - 19, "s1", "TaskStop", task_id="b1"), ended(T0 - 18))
        self.assertEqual(self.word(), ("waiting", "Listening"))
        self.write(SA, result(T0 - 18, "s1"), bash(T0 - 10, "c1", "tmux capture-pane -p -t m"))
        self.assertEqual(self.word(), ("working", "Using a terminal"))

    def test_a_fresh_session_with_no_turn_yet_is_not_working(self):
        self.claim(SA, PA, T0 - 60)
        self.register(PA, SA)
        self.write(SA, queued(T0 - 50))
        self.assertEqual(self.word(), ("waiting", "Listening"))


class Sessions(Rig):
    def test_no_claim_means_it_cannot_tell(self):
        self.assertIsNone(self.act())
        self.register(PA, SA)
        self.write(SA, prompt(T0 - 5))
        self.assertIsNone(self.act())                    # a session nobody claimed for the lead is not the lead's
        self.claim(SA, PA, T0 - 60, name="Someone else")
        self.assertIsNone(self.act())

    def test_gone_when_the_registry_file_is_gone_or_names_another_session(self):
        self.live()
        self.assertEqual(self.word(), ("waiting", "Listening"))
        (self.claude / "sessions" / f"{PA}.json").unlink()
        self.assertEqual(self.act(), {"state": "gone", "word": "Offline", "detail": "", "since": T0})   # seen now
        self.register(PA, SB)                            # the pid now runs another session
        self.assertEqual(self.word(), ("gone", "Offline"))
        self.r = activity.Reader(claude_dir=self.claude, clock=lambda: self.now, alive=lambda pid: pid in self.alive)
        self.assertEqual(self.act()["since"], round(T0 - 7200, 1))   # first sight: when it last spoke to TapSpace

    def test_gone_when_the_pid_is_dead_or_reused(self):
        self.live()
        self.alive.discard(PA)
        self.assertEqual(self.word(), ("gone", "Offline"))
        self.alive.add(PA)
        self.starts[PA] = "Fri Oct 16 16:40:00 2026"     # pids are reused: another process holds it now
        self.now += activity.PROC_EVERY
        self.assertEqual(self.word(), ("gone", "Offline"))

    def test_ps_runs_at_most_once_per_interval(self):
        self.live()
        for _ in range(5):
            self.act()
        self.assertEqual(self.asked, [PA])
        self.now += activity.PROC_EVERY
        self.act()
        self.assertEqual(self.asked, [PA, PA])

    def test_a_relay_car_wins_while_both_run(self):
        self.live(SA, PA, T0 - 7200)
        self.write(SA, prompt(T0 - 30), bash(T0 - 29, "c1", "python3 $TAP relay --as Muddi"))
        self.assertEqual(self.word(), ("working", "Handing off"))
        self.live(SB, PB, T0 - 20)
        self.write(SB, prompt(T0 - 5, "human"), bash(T0 - 4, "c9", "python3 $TAP taps"))
        self.assertEqual(self.word(), ("working", "Checking taps"))
        self.alive.discard(PB)                           # the new car died: back to the one still running
        self.assertEqual(self.word(), ("working", "Handing off"))

    def test_switching_while_a_relay_runs(self):
        self.state, self.info["switching"] = "switching", T0 - 3
        self.assertEqual(self.act(), {"state": "switching", "word": "Switching car", "detail": "",
                                      "since": round(T0 - 3, 1)})

    def test_no_transcript_yet(self):
        self.claim(SA, PA, T0 - 60)
        self.register(PA, SA)
        self.assertIsNone(self.act())
        self.write(SA, prompt(T0 - 5), bash(T0 - 4, "c1", "git log -3"), folder="-Users-x-Developer-y")
        self.assertIsNone(self.act())                    # looked for again only after MISS_EVERY
        self.now += activity.MISS_EVERY
        self.assertEqual(self.word(), ("working", "Reading code"))

    def test_only_top_level_transcripts(self):
        self.claim(SA, PA, T0 - 60)
        self.register(PA, SA)
        sub = self.claude / "projects" / "-Users-x" / SA / "subagents" / "workflows" / "wf_1"
        sub.mkdir(parents=True)
        (sub / f"{SA}.jsonl").write_text(dump(prompt(T0 - 5)) + "\n")
        self.assertIsNone(self.act())


class Tail(Rig):
    def test_a_partial_last_line_waits_for_its_newline(self):
        self.live()
        self.write(SA, prompt(T0 - 10))
        line = dump(bash(T0 - 5, "c1", "$MUDDI/muddi.sh fleet")).encode()
        self.raw(SA, line[:40])
        self.now = T0 - 4
        self.assertEqual(self.word(), ("working", "Reading your message"))     # no whole call yet: what woke it
        self.raw(SA, line[40:] + b"\n")
        self.assertEqual(self.word(), ("working", "Checking the masters"))

    def test_first_sight_reads_only_the_tail(self):
        self.claim(SA, PA, T0 - 600)
        self.register(PA, SA)
        self.write(SA, prompt(T0 - 590), bash(T0 - 589, "old", "$MUDDI/muddi.sh fleet"),
                   *[queued(T0 - 500, "x" * 900) for _ in range(20)], request(T0 - 10))
        size = (self.claude / "projects" / "-Users-x" / f"{SA}.jsonl").stat().st_size
        with mock.patch.object(activity, "TAIL_FIRST", 4096):
            self.assertEqual(self.word(), ("working", "Thinking"))       # the old call is before the window
        self.assertGreaterEqual(self.r.tails[SA]["off"], size - 4096)

    def test_a_big_request_copy_is_never_parsed(self):
        self.live()
        fake = dump(bash(T0 - 3, "zz", "$MUDDI/muddi.sh fleet")) + dump(prompt(T0 - 3))
        blob = {"type": "api-request-blob", "timestamp": iso(T0 - 3), "body": fake * (700_000 // len(fake))}
        sizes = []

        def spy(s, *a, **k):
            sizes.append(len(s))
            return json.loads(s, *a, **k)
        self.write(SA, blob, {"type": "api-request-shape", "timestamp": iso(T0 - 3), "shape": fake * 100})
        self.assertLess(self.claude.joinpath("projects", "-Users-x", f"{SA}.jsonl").stat().st_size, activity.TAIL_FIRST)
        with mock.patch.object(activity, "json", types.SimpleNamespace(loads=spy, dumps=json.dumps)):
            self.assertEqual(self.word(), ("waiting", "Listening"))
        self.assertTrue(sizes and max(sizes) < 10_000, sizes)       # the copies were never parsed

    def test_an_unchanged_file_is_not_read_again(self):
        self.live()
        self.act()
        n = self.r.counts["reads"]
        for _ in range(3):
            self.act()
        self.assertEqual(self.r.counts["reads"], n)
        self.write(SA, prompt(T0 - 1))
        self.act()
        self.assertEqual(self.r.counts["reads"], n + 1)

    def test_a_broken_line_never_stops_it(self):
        self.live()
        self.raw(SA, b'{"type":"user","turnOrigin":"human", not json\n')
        self.write(SA, prompt(T0 - 0.2, "scheduled"))
        self.assertEqual(self.word(), ("working", "Checking in"))
        self.assertEqual(self.r.counts["errors"], 1)

    def test_a_read_failure_is_none_never_an_exception(self):
        self.live()
        with mock.patch.object(activity.Reader, "_tail", side_effect=RuntimeError("odd")):
            self.assertIsNone(self.act())
        self.assertEqual(self.word(), ("waiting", "Listening"))


class Since(Rig):
    def test_a_word_keeps_its_start_and_a_change_the_transcript_dates_keeps_its_time(self):
        self.live()
        self.write(SA, prompt(T0 - 30), bash(T0 - 20, "c1", "$MUDDI/muddi.sh fleet"))
        self.assertEqual(self.act()["since"], round(T0 - 20, 1))
        self.now = T0 + 30
        self.assertEqual(self.act()["since"], round(T0 - 20, 1))
        self.write(SA, result(T0 + 31, "c1"), ended(T0 + 32))
        self.now, self.info["listen"] = T0 + 33, T0 + 30
        self.assertEqual(self.act(), {"state": "waiting", "word": "Listening", "detail": "", "since": round(T0 + 32, 1)})

    def test_a_change_only_the_clock_sees_starts_now(self):
        self.live()
        self.assertEqual(self.act()["since"], round(T0 - 7180, 1))      # its last turn ended then
        self.now = T0 + 50
        self.info["listen"] = T0 + 10                                     # its listener stopped at 10 s
        self.assertEqual(self.act(), {"state": "idle", "word": "Idle", "detail": "", "since": round(T0 + 50, 1)})


def all_words_are_plain(test, word, detail):
    for s in (word, detail):
        test.assertFalse([w for w in s.split() if links._CODE.search(w)], s)
        test.assertNotIn("/", s)
        test.assertIsNone(re.search(r"#\d", s), s)
        test.assertNotIn("—", s)


class Words(Rig):
    BASH = [
        ("$MUDDI/muddi.sh ledger 2026-10-16-desk \"noted\"", "Logging progress"),
        ("cd $MUDDI; ./muddi.sh prompt 2026-10-16-desk 474", "Logging your message"),
        ("echo \"MUDDI=$MUDDI\"; ls -la ~/orchestration/muddi/", "Reading files"),
        ("bash ~/orchestration/muddi/muddi.sh peer Muddi", "Finding a master"),
        ("R1=$($MUDDI/muddi.sh run sim-tap); echo $R1", "Opening a run"),
        ("python3 $TAP reply --as Muddi \"On it.\" && python3 $TAP lead --as Muddi", "Writing to you"),
        ("python3 ~/orchestration/tapspace/tap.py goal --as Muddi \"Ready\" --steps \"Listening\"", "Updating its goal card"),
        ("python3 $TAP step --as Muddi next", "Updating its goal card"),
        ("python3 $TAP card --as Muddi \"Ready to accept\" --accept snapp-ios#1583", "Sending you an update"),
        ("python3 $TAP taps", "Checking taps"),
        ("python3 $TAP tap-read SnappStats/snapp-ios#1583", "Reading a tap"),
        ("python3 $TAP tap-say --as Muddi SnappStats/snappy#3157 \"Go\"", "Answering a tap"),
        ("python3 $TAP post --agent x --title y --image a.png", "Posting a card"),
        ("python3 $TAP new --as Muddi --dir ~/Developer/app", "Starting a master"),
        ("python3 $TAP adopt --as Muddi CHILD", "Taking on a master"),
        ("python3 $TAP progress --as Muddi", "Reading a master's progress"),
        ("python3 $TAP title --as Muddi Muddi", "Getting set up"),
        ("python3 $TAP relay --as Muddi", "Handing off"),
        ("python3 $TAP wait abc123 --timeout 900", "Waiting on a card"),
        ("python3 $TAP listen --as Muddi --timeout 5", "Listening"),
        ("python3 -I /x/tap.py frobnicate", "Using TapSpace"),
        ("TAPSPACE_URL=http://127.0.0.1:4811 python3 /x/tap.py taps", "Checking taps"),
        ("~/orchestration/muddi/fly.sh 2026-10-16-desk", "Filing a tap"),
        ("gh issue view 3157 -R SnappStats/snappy --json state", "Checking a tap"),
        ("gh issue comment 3157 -R SnappStats/snappy --body x", "Commenting on a tap"),
        ("gh search issues --author @me", "Looking through taps"),
        ("gh issue list -R SnappStats/snappy", "Looking through taps"),
        ("gh pr view 6 -R hperwin/tapspace", "Checking a PR"),
        ("gh api repos/SnappStats/snappy/issues/3157/events", "Checking GitHub"),
        ("git -C ~/Developer/tapspace log --oneline -5", "Reading code"),
        ("git commit -m x", "Working on code"),
        ("curl -s http://127.0.0.1:4747/api/taps | python3 -m json.tool", "Checking TapSpace"),
        ("curl -s https://example.com", "Looking something up"),
        ("tmux capture-pane -p -t muddi", "Using a terminal"),
        ("ps aux | grep tap.py", "Checking what's running"),
        ("xcrun simctl io booted screenshot a.png", "Looking at screenshots"),
        ("sleep 2; tail -3 /x/y.log", "Reading files"),
        ("d=/x; for t in a b; do tail -3 $d/$t.output; done", "Reading files"),
        ("env -u CLAUDE_PID python3 - <<'EOF'\nprint('x')\nEOF", "Running a command"),
        ("zsh -lc 'python3 $TAP taps'", "Checking taps"),
        ("kill -INT $(cat rec.pid)", "Stopping a process"),
        ("cswap list", "Running a command"),
        ("# look first\nls ~/x  # the folder", "Reading files"),
        ("python3 $TAP tap-read SnappStats/snapp-ios#1583 # its proof", "Reading a tap"),
        ("echo hi", "Running a command"),
        ("", "Running a command"),
    ]
    TOOLS = [
        ("SendMessage", {"to": "nobody-0", "message": "x"}, "Messaging a master"),
        ("Monitor", {"command": "tail -f x"}, "Arming a watcher"),
        ("Agent", {"description": "", "prompt": "x"}, "Sending a helper"),
        ("Workflow", {"script": "x"}, "Running a team"),
        ("Write", {"file_path": "/Users/x/orchestration/muddi/memory/note.md"}, "Saving a note"),
        ("Write", {"file_path": "/Users/x/a.txt"}, "Writing a file"),
        ("Edit", {"file_path": "/Users/x/orchestration/muddi/MUDDI.md"}, "Updating its rules"),
        ("Edit", {"file_path": "/Users/x/a.py"}, "Editing a file"),
        ("Read", {"file_path": "/Users/x/README.md"}, "Reading files"),
        ("Grep", {"pattern": "x"}, "Reading files"),
        ("CronCreate", {"cron": "*/10 * * * *"}, "Setting a check-in"),
        ("Skill", {"skill": "x"}, "Reading a playbook"),
        ("ReadMcpResourceTool", {}, "Reading a guide"),
        ("TaskStop", {"task_id": "b1"}, "Stopping a helper"),
        ("ListAgents", {}, "Finding a master"),
        ("PushNotification", {}, "Notifying you"),
        ("WebSearch", {"query": "x"}, "Looking something up"),
        ("mcp__flightdeck__fly_journey", {}, "Launching a tap"),
        ("mcp__flightdeck__read_pilot_messages", {}, "Reading a pilot's messages"),
        ("mcp__flightdeck__something_new", {}, "Checking taps"),
        ("mcp__meta-ads__ads_library_search", {}, "Searching ads"),
        ("mcp__posthog__exec", {}, "Using a connected app"),
        ("SomethingNew", {}, "Working"),
    ]

    def test_bash_words(self):
        for cmd, want in self.BASH:
            with self.subTest(cmd=cmd):
                word, detail = activity.bash_word(cmd, {})
                self.assertEqual(word, want)
                self.assertIn(word, activity.WORDS)
                all_words_are_plain(self, word, detail)

    def test_tool_words(self):
        for name, inp, want in self.TOOLS:
            with self.subTest(tool=name):
                word, detail = activity.word_for(name, inp, {})
                self.assertEqual(word, want)
                self.assertIn(word, activity.WORDS)
        self.assertEqual(activity.word_for("ToolSearch", {"query": "x"}, {}), (None, ""))

    def test_every_word_in_the_table_is_plain(self):
        for w in activity.WORDS:
            all_words_are_plain(self, w, "")
            self.assertTrue(w[0].isupper() and len(w) <= 30, w)

    def test_details_are_titles_the_space_shows(self):
        self.register(PB, SB, name="tapspace-f2")
        self.claim(SB, PB, T0 - 60, name=MASTER)
        names = activity.Names(lambda: TITLES, TAPS, json.loads(self.claims.read_text())["sessions"], self.claude)
        cases = [
            (activity.word_for("SendMessage", {"to": "tapspace-f2", "message": "x"}, {}, names),
             ("Messaging a master", "Chat update cards")),
            (activity.word_for("SendMessage", {"to": f"uds:/tmp/cc-socks/{PB}.sock", "message": "x"}, {}, names),
             ("Messaging a master", "Chat update cards")),
            (activity.word_for("SendMessage", {"to": "nobody-0", "message": "x"}, {}, names), ("Messaging a master", "")),
            (activity.bash_word("python3 $TAP tap-read SnappStats/snapp-ios#1583", {}, names),
             ("Reading a tap", "MLB lineup card shows lineups")),
            (activity.bash_word("python3 $TAP tap-read snapp-ios#1583", {}, names),
             ("Reading a tap", "MLB lineup card shows lineups")),
            (activity.bash_word("gh issue view 3157 -R SnappStats/snappy --json state", {}, names),
             ("Checking a tap", "Landing page")),
            (activity.bash_word("gh issue view https://github.com/SnappStats/snappy/issues/3157", {}, names),
             ("Checking a tap", "Landing page")),
            (activity.bash_word("gh issue view 3157", {}, names), ("Checking a tap", "")),
            (activity.bash_word(f'bash $MUDDI/muddi.sh peer "{MASTER}"', {}, names),
             ("Finding a master", "Chat update cards")),
            (activity.bash_word("$MUDDI/muddi.sh peer 'Claude · home · e3cc'", {}, names), ("Finding a master", "")),
            (activity.word_for("Agent", {"description": "Map the Glint thread code in ~/Developer/glint #4",
                                         "prompt": "x"}, {}, names), ("Sending a helper", "Map the Glint thread code")),
            (activity.word_for("Write", {"file_path": "/Users/x/orchestration/muddi/runs/2026-10-16-chat-update-cards/"
                                                      "orders/chat-update-cards.md"}, {}, names),
             ("Writing an order", "chat update cards")),
            (activity.word_for("mcp__flightdeck__read_journey", {"issue": "SnappStats/snappy#3157"}, {}, names),
             ("Checking a tap", "Landing page")),
        ]
        for got, want in cases:
            with self.subTest(want=want):
                self.assertEqual(got, want)
                all_words_are_plain(self, *got)

    def test_the_detail_rides_the_served_activity(self):
        self.register(PB, SB, name="tapspace-f2")
        self.claim(SB, PB, T0 - 60, name=MASTER)
        self.live()
        self.write(SA, prompt(T0 - 3), tool(T0 - 2, "m1", "SendMessage", to="tapspace-f2", message="Ship it",
                                             summary="Ship it"))
        self.assertEqual(self.act(), {"state": "working", "word": "Messaging a master", "detail": "Chat update cards",
                                      "since": round(T0 - 2, 1)})


# ---- the served row and the card words, on server.py in-process (an ephemeral port, a throwaway data dir) ----

TMP = tempfile.TemporaryDirectory(prefix="tapspace-activity-")
CLAUDE = pathlib.Path(TMP.name) / "claude"


def _load_server():
    """server.py as a fresh module whose paths point at our data dir (they are fixed at import)."""
    env = {"TAPSPACE_DATA": str(pathlib.Path(TMP.name) / "data"),
           "TAPSPACE_APNS_DIR": str(pathlib.Path(TMP.name) / "no-apns"),    # no key: a post never pushes
           "TAPSPACE_PORT": "0", "TAPSPACE_CLAUDE_DIR": str(CLAUDE)}
    saved = {k: os.environ.get(k) for k in env}
    os.environ.update(env)
    try:
        spec = importlib.util.spec_from_file_location("tapspace_server_activity_under_test", ROOT / "server.py")
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
SRV, BASE, SAVED = None, "", None


def setUpModule():
    global SRV, BASE, SAVED
    S.DATA.mkdir(parents=True)
    S.SHOTS.mkdir()
    S.INBOX.touch()
    S._save({"tickets": []})
    (CLAUDE / "projects" / "-Users-x").mkdir(parents=True)
    (CLAUDE / "sessions").mkdir()
    SAVED, activity._ONE = activity._ONE, activity.Reader(claude_dir=CLAUDE)   # this module's server reads CLAUDE
    SRV = S.ThreadingHTTPServer(("127.0.0.1", 0), S.H)
    S.PORT = SRV.server_address[1]
    assert S.PORT != 4747
    BASE = f"http://127.0.0.1:{S.PORT}"
    threading.Thread(target=SRV.serve_forever, daemon=True).start()
    threading.Thread(target=S._agents_watch, daemon=True).start()


def tearDownModule():
    activity._ONE = SAVED
    SRV.shutdown()
    SRV.server_close()
    TMP.cleanup()


def call(path, body=None, headers=None):
    req = urllib.request.Request(BASE + path, data=None if body is None else json.dumps(body).encode(),
                                 headers={"content-type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


class Served(unittest.TestCase):
    """GET /api/agents: the lead row carries activity, nothing else does, and a new word wakes /api/wait."""

    @classmethod
    def setUpClass(cls):
        cls.lead, cls.other, cls.sid = "T7 lead", "T7 other", "cccccccc-3333-4333-8333-333333333333"
        cls.transcript = CLAUDE / "projects" / "-Users-x" / f"{cls.sid}.jsonl"
        (CLAUDE / "sessions" / f"{os.getpid()}.json").write_text(dump(
            {"pid": os.getpid(), "sessionId": cls.sid, "startedAt": int(time.time() * 1000), "kind": "interactive"}))
        cls.add(prompt(time.time() - 30), ended(time.time() - 20))
        who = {"X-Claude-Session": cls.sid, "X-Claude-Pid": str(os.getpid())}
        assert call("/api/chat/listen?" + urllib.parse.urlencode({"agent": cls.lead, "where": "~/x"}), headers=who)[0] == 200
        assert call("/api/chat/listen?" + urllib.parse.urlencode({"agent": cls.other, "where": "~/y"}))[0] == 200
        assert call("/api/agents/lead", {"name": cls.lead})[0] == 200

    @classmethod
    def add(cls, *lines):
        flat = [x for o in lines for x in (o if isinstance(o, list) else [o])]
        with cls.transcript.open("a") as fh:
            fh.write("".join(dump(o) + "\n" for o in flat))

    def rows(self):
        return {r["name"]: r for r in call("/api/agents")[1]["agents"]}

    def test_the_lead_row_has_it_and_no_other_row_does(self):
        rows = self.rows()
        self.assertEqual(rows[self.lead]["role"], "lead")
        act = rows[self.lead]["activity"]
        self.assertEqual((act["state"], act["word"], act["detail"]), ("waiting", "Listening", ""))
        self.assertEqual(set(act), {"state", "word", "detail", "since"})
        self.assertNotIn("activity", rows[self.other])
        self.assertLessEqual({"name", "state", "seen", "first", "where", "last", "count", "last_master", "title",
                              "progress", "notes", "notes_read", "look", "parent", "role"}, set(rows[self.lead]))

    def test_a_new_word_wakes_agents_through_the_watch_alone(self):
        cur = call("/api/wait?since=0&timeout=0")[1]["cursor"]
        deadline = time.time() + 10
        while time.time() < deadline:                    # let the watch settle first
            r = call(f"/api/wait?since={cur}&timeout=1.5")[1]
            cur = r["cursor"]
            if "agents" not in r["changed"]:
                break
        now = time.time()
        self.add(prompt(now - 5, "scheduled"), bash(now - 4, "c1", "$MUDDI/muddi.sh fleet"))   # no write to TapSpace
        seen, deadline = set(), time.time() + 6
        while "agents" not in seen and time.time() < deadline:
            r = call(f"/api/wait?since={cur}&timeout=3")[1]
            seen.update(r["changed"])
            cur = r["cursor"]
        self.assertIn("agents", seen)
        self.assertEqual(self.rows()[self.lead]["activity"]["word"], "Checking the masters")
        self.add(result(time.time(), "c1"), ended(time.time()))

    def test_no_lead_no_activity(self):
        with S._lock:
            a = S._agents()
            a[self.lead].pop("role", None)
            S._agents_write(a)
        try:
            self.assertFalse(any("activity" in r for r in call("/api/agents")[1]["agents"]))
        finally:
            self.assertEqual(call("/api/agents/lead", {"name": self.lead})[0], 200)


class CardWords(unittest.TestCase):
    """A proof caption becomes its image's label without its quotes, cut at a word under 40 characters; a tap title
    in an Accept or Not quite body is cut to 40 the same way."""
    TAP = "SnappStats/snapp-ios#1583"

    def label(self, alt):
        taps = [{"key": self.TAP, "title": "MLB lineup card", "repo": "SnappStats/snapp-ios", "phase": "landed",
                 "phase_word": "Landed", "goals": [], "proof": [{"url": "u0", "alt": alt}]}]
        card = {"line": "Approve the new lineup card?", "face": self.TAP, "items": [], "images": [{}]}
        return S._card_fill(card, [{"key": self.TAP, "i": 0}], [], {}, taps, [])["images"][0]["label"]

    def test_proof_captions_as_labels(self):
        cases = [
            ('MLB game page: the lineup card reads "Lineup: Orioles"', "MLB game page: the lineup card reads..."),
            ('The "Lineup" card', "The Lineup card"),
            ("“Box score” on the game page", "Box score on the game page"),
            ("Lineup card on the MLB game page, iPhone", "Lineup card on the MLB game page, iPhone"),   # exactly 40
            ("Lineup card on the MLB game page, iPhone 17", "Lineup card on the MLB game page..."),
            ("A" * 50, "A" * 37 + "..."),
            ("Image", "Image 1"),
            ("", "Image 1"),
            ("<b>bold</b> caption", "Image 1"),
        ]
        for alt, want in cases:
            with self.subTest(alt=alt):
                got = self.label(alt)
                self.assertEqual(got, want)
                self.assertLessEqual(len(got), 40)
                self.assertNotIn('"', got)

    def test_a_label_given_wins(self):
        taps = [{"key": self.TAP, "title": "t", "repo": "SnappStats/snapp-ios", "phase": "landed",
                 "phase_word": "Landed", "goals": [], "proof": [{"url": "u0", "alt": "Box score"}]}]
        card = {"line": "L", "face": self.TAP, "items": [], "images": [{"label": "New lineup card"}]}
        self.assertEqual(S._card_fill(card, [{"key": self.TAP, "i": 0}], [], {}, taps, [])["images"][0]["label"],
                         "New lineup card")

    def test_accept_and_not_quite_bodies_cut_the_tap_title(self):
        long = "MLB lineup card shows the starting lineups for both teams before first pitch"
        card = {"action": {"kind": "tap_accept", "tap": self.TAP}, "faces": {self.TAP: {"kind": "tap", "title": long}}}
        accepted = S._answer_line({"card": 1, "action": "accept"}, card)
        self.assertEqual(accepted, "Accepted: MLB lineup card shows the starting...")
        self.assertLessEqual(len(accepted), len("Accepted: ") + 40)
        self.assertEqual(S._answer_line({"card": 1, "action": "deny", "note": "the score\nis small"}, card),
                         "Not quite: MLB lineup card shows the starting...: the score / is small")
        card["faces"][self.TAP]["title"] = "MLB lineup card"
        self.assertEqual(S._answer_line({"card": 1, "action": "accept"}, card), "Accepted: MLB lineup card")


if __name__ == "__main__":
    unittest.main()
