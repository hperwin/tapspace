"""tests/test_links.py: the agent-to-agent link scanner (links.py) on throwaway transcripts.

Every fixture line copies a shape verified on real Claude Code 2.1.296 to 2.1.301 transcripts
(audit B18 sections 1.2 and 1.3). Nothing here reads ~/.claude or talks to a server.
Run from the repo root:  python3 -B -m unittest discover -s tests -p 'test_links.py' -v
"""
import json
import os
import pathlib
import sys
import tempfile
import time
import unittest

sys.dont_write_bytecode = True
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import links  # noqa: E402

T0 = 1792099100.0                       # October 15, 2026, 2:18:20 PM PT
A = "aaaaaaaa-1111-4111-8111-111111111111"
B = "bbbbbbbb-2222-4222-8222-222222222222"
C = "cccccccc-3333-4333-8333-333333333333"
A2 = "dddddddd-4444-4444-8444-444444444444"
NA, NB, NC = "Claude · alpha · 1a2b", "Claude · beta · 3c4d", "Claude · gamma · 5e6f"
PA, PB, PC = 101, 202, 303              # their claude pids; SendMessage names alpha-1a, beta-3c, gamma-5e


def iso(ts):
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(ts)) + ".%03dZ" % round(ts % 1 * 1000)


def dump(o):
    return json.dumps(o, separators=(",", ":"), ensure_ascii=False)   # Claude Code writes compact JSON


def listen(ts, name, call="toolu_listen"):
    return {"type": "assistant", "timestamp": iso(ts), "message": {"role": "assistant", "content": [
        {"type": "tool_use", "id": call, "name": "Bash",
         "input": {"command": f'python3 ~/orchestration/tapspace/tap.py listen --as "{name}" --timeout 3600'}}]}}


def bash(ts, command):
    return {"type": "assistant", "timestamp": iso(ts), "message": {"role": "assistant", "content": [
        {"type": "tool_use", "id": "toolu_bash", "name": "Bash", "input": {"command": command}}]}}


def send(ts, call, to, message="Body of the message", summary=None):
    i = {"to": to, "message": message, "type": "message", "recipient": to, "recipient_kind": "name"}
    if summary is not None:
        i["summary"] = summary
    return {"type": "assistant", "timestamp": iso(ts), "message": {"role": "assistant", "content": [
        {"type": "tool_use", "id": call, "name": "SendMessage", "input": i}]}}


def result(ts, call, msg_id, routing=False, success=True):
    r = {"success": success, "message": "sent", "display": "sent", "msg_id": msg_id}
    if routing:
        r["routing"] = {"sender": "team-lead", "target": "@L1-ios", "summary": "go", "content": "go"}
    return {"type": "user", "timestamp": iso(ts + 0.2), "message": {"role": "user", "content": [
        {"tool_use_id": call, "type": "tool_result", "content": [{"type": "text", "text": json.dumps(r)}]}]},
        "toolUseResult": r}


def origin(pid, msg_id, body):
    return {"kind": "peer", "from": f"uds:/tmp/cc-socks/{pid}.sock", "verifiedPeerPid": pid,
            "msg_id": msg_id, "name": "peer-00", "fromMode": "bypass", "body": body}


def got_idle(ts, msg_id, pid, body="Body of the message"):
    return {"type": "user", "isMeta": True, "turnOrigin": "peer", "timestamp": iso(ts + 0.3),
            "message": {"role": "user", "content": f"Another Claude session sent a message:\n{body}"},
            "origin": origin(pid, msg_id, body)}


def got_busy(ts, msg_id, pid, body="Body of the message"):
    return {"type": "attachment", "timestamp": iso(ts + 0.3),
            "attachment": {"type": "queued_command", "prompt": body, "commandMode": "prompt",
                           "origin": origin(pid, msg_id, body)}}


def pilot(ts, call, tool, **inp):
    return {"type": "assistant", "timestamp": iso(ts), "message": {"role": "assistant", "content": [
        {"type": "tool_use", "id": call, "name": "mcp__flightdeck__" + tool, "input": inp}]}}


def pilot_result(ts, call, body, error=False, blocks=False):
    """A Flightdeck call's result as Claude Code writes it: the tool's JSON text in the result block (or as a text
    block), the same string as toolUseResult, is_error on a failure."""
    b = {"tool_use_id": call, "type": "tool_result", "content": [{"type": "text", "text": body}] if blocks else body}
    if error:
        b["is_error"] = True
    return {"type": "user", "timestamp": iso(ts + 0.4), "message": {"role": "user", "content": [b]},
            "toolUseResult": body}


OK_SENT = json.dumps({"ok": True, "undelivered": False, "held": False, "reason": None,
                      "flight": "42fc7f72-806a-43db-a94c-83513a517fcd",
                      "journey": "https://github.com/snappstats/snapp-ios/issues/1614"}, separators=(",", ":"))


class Rig(unittest.TestCase):
    """A throwaway ~/.claude (projects/ + sessions/ registry) and TapSpace data dir per test."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = pathlib.Path(tmp.name)
        self.claude, self.data = root / "claude", root / "data"
        (self.claude / "projects" / "-Users-x").mkdir(parents=True)
        (self.claude / "sessions").mkdir()
        self.data.mkdir()
        self.now = T0 + 60
        self.bumps = []
        self.scan = self.scanner()

    def scanner(self):
        return links.Links(self.data, bump=lambda *k: self.bumps.append(k),
                           claude_dir=self.claude, clock=lambda: self.now)

    def write(self, sid, *lines, folder="-Users-x"):
        d = self.claude / "projects" / folder
        d.mkdir(parents=True, exist_ok=True)
        with (d / f"{sid}.jsonl").open("a") as fh:
            fh.write("".join(dump(o) + "\n" for o in lines))

    def live(self, pid, sid, peer, started=T0 - 3600):
        (self.claude / "sessions" / f"{pid}.json").write_text(dump(
            {"pid": pid, "sessionId": sid, "name": peer, "startedAt": int(started * 1000), "status": "busy"}))
        (self.claude / "sessions" / f"{pid}.0123abcd.key").write_text("not json, never read")

    def two_masters(self):
        self.live(PA, A, "alpha-1a")
        self.live(PB, B, "beta-3c")
        self.write(A, listen(T0 - 600, NA))
        self.write(B, listen(T0 - 600, NB))

    def file_rows(self):
        f = self.data / "links.jsonl"
        return [json.loads(x) for x in f.read_text().splitlines()] if f.exists() else []


class Halves(Rig):
    def test_both_halves_make_one_link(self):
        self.two_masters()
        self.write(A, send(T0, "toolu_1", "beta-3c", summary="Rail is live, panel next"),
                   result(T0, "toolu_1", "m1"))
        self.write(B, got_busy(T0, "m1", PA))
        self.scan.tick()
        self.assertEqual(self.scan.rows({NA, NB}), [{"a": NA, "b": NB, "last": T0, "count": 1,
                                                    "summary": "Rail is live, panel next",
                                                    "active": True, "from": NA}])
        self.assertEqual(len(self.file_rows()), 1)

    def test_receiver_half_alone_resolves_by_verified_pid(self):
        self.two_masters()
        self.write(B, got_idle(T0, "m1", PA))
        self.scan.tick()
        [row] = self.scan.rows({NA, NB})
        self.assertEqual((row["from"], row["count"], row["summary"]), (NA, 1, ""))

    def test_sender_half_alone_resolves_by_name_or_socket(self):
        self.two_masters()
        self.write(A, send(T0, "toolu_1", "beta-3c", summary="By name"), result(T0, "toolu_1", "m1"),
                   send(T0 + 5, "toolu_2", f"uds:/tmp/cc-socks/{PB}.sock", summary="By socket"),
                   result(T0 + 5, "toolu_2", "m2"))
        self.scan.tick()
        [row] = self.scan.rows({NA, NB})
        self.assertEqual((row["count"], row["summary"], row["from"]), (2, "By socket", NA))

    def test_late_sender_half_brings_the_summary(self):
        self.two_masters()
        self.write(B, got_busy(T0, "m1", PA))
        self.scan.tick()
        self.assertEqual(self.scan.rows({NA, NB})[0]["summary"], "")
        self.write(A, send(T0, "toolu_1", "beta-3c", summary="Late but here"), result(T0, "toolu_1", "m1"))
        self.scan.tick()
        self.assertEqual(self.scan.rows({NA, NB})[0]["summary"], "Late but here")
        self.assertEqual([r["summary"] for r in self.file_rows()], ["", "Late but here"])
        again = self.scanner()
        again.tick()
        self.assertEqual(again.rows({NA, NB})[0]["summary"], "Late but here")
        self.assertEqual(len(self.file_rows()), 1)          # compacted: last line per message

    def test_copies_and_noise_are_not_counted(self):
        self.two_masters()
        line = send(T0, "toolu_1", "beta-3c", summary="Once")
        self.write(A, line, {"type": "api-request-blob", "message": line["message"]}, result(T0, "toolu_1", "m1"))
        self.write(B, {"type": "queue-operation", "operation": "enqueue", "timestamp": iso(T0),
                       "content": f'<cross-session-message from="uds:/tmp/cc-socks/{PA}.sock">x'},
                   got_busy(T0, "m1", PA), got_idle(T0, "m1", PA))
        self.scan.tick()
        self.assertEqual(self.scan.rows({NA, NB})[0]["count"], 1)


class NotLinks(Rig):
    def test_teammates_failures_and_in_process_peers_make_nothing(self):
        self.two_masters()
        self.write(A, send(T0, "toolu_1", "L1-ios", summary="Lane order"),
                   result(T0, "toolu_1", "m1", routing=True),
                   send(T0, "toolu_2", "beta-3c", summary="Failed"),
                   result(T0, "toolu_2", "m2", success=False),
                   {"type": "user", "timestamp": iso(T0), "message": {"role": "user", "content": "x"},
                    "origin": {"kind": "peer", "from": "L1o-picks", "senderTaskId": "aL1o-picks-1",
                               "name": "L1o-picks", "msg_id": "m3", "body": "done"}})
        self.scan.tick()
        self.assertEqual(self.scan.rows({NA, NB}), [])
        self.assertEqual(self.scan.stats()["pending"], 0)

    def test_relay_cars_of_one_agent_make_no_link(self):
        self.two_masters()
        self.live(PC, A2, "alpha-77")
        self.write(A2, listen(T0 - 30, NA), send(T0, "toolu_1", "alpha-1a", summary="Handoff"),
                   result(T0, "toolu_1", "m1"))
        self.scan.tick()
        self.assertEqual(self.scan.rows({NA, NB}), [])
        self.assertEqual(self.file_rows(), [])

    def test_reused_pid_never_claims_an_older_message(self):
        self.two_masters()
        self.live(PA, A, "alpha-1a", started=T0 + 3600)
        self.write(B, got_busy(T0, "m1", PA))
        self.scan.tick()
        self.assertEqual(self.scan.rows({NA, NB}), [])
        self.assertEqual(self.scan.stats()["pending"], 1)

    def test_ambiguous_peer_name_waits_for_the_receiver_half(self):
        self.two_masters()
        self.live(PC, C, "beta-3c")                          # two live sessions answer to beta-3c
        self.write(A, send(T0, "toolu_1", "beta-3c", summary="Which one"), result(T0, "toolu_1", "m1"))
        self.scan.tick()
        self.assertEqual(self.scan.rows({NA, NB}), [])
        self.write(B, got_busy(T0, "m1", PA))
        self.scan.tick()
        self.assertEqual(self.scan.rows({NA, NB})[0]["summary"], "Which one")

    def test_subagent_transcripts_are_never_read(self):
        self.two_masters()
        nested = self.claude / "projects" / "-Users-x" / A / "subagents"
        nested.mkdir(parents=True)
        (nested / "agent-x1.jsonl").write_text(dump(send(T0, "toolu_1", "beta-3c", summary="Lane")) + "\n" +
                                               dump(result(T0, "toolu_1", "m1")) + "\n")
        self.scan.tick()
        self.assertEqual(self.scan.rows({NA, NB}), [])
        self.assertEqual(self.scan.stats()["pending"], 0)

    def test_boot_skips_transcripts_quiet_for_a_day(self):
        self.two_masters()
        self.write(A, send(T0, "toolu_1", "beta-3c", summary="x"), result(T0, "toolu_1", "m1"))
        old = T0 - 2 * 86400
        for sid in (A, B):
            os.utime(self.claude / "projects" / "-Users-x" / f"{sid}.jsonl", (old, old))
        self.scan.tick()
        self.assertEqual(self.scan.heard, {})
        self.assertEqual(self.scan.rows({NA, NB}), [])


class Names(Rig):
    def test_stamped_name_wins_over_the_listen_command(self):
        self.two_masters()
        (self.data / "sessions.json").write_text(dump(
            {A: {"name": NC, "pid": PA, "how": "listen"}, "campaigns": {"c1": {NC: 1}}}))
        self.write(B, got_busy(T0, "m1", PA))
        self.scan.tick()
        self.assertEqual(self.scan.rows({NB, NC})[0]["from"], NC)

    def test_claims_file_shapes(self):
        for body in ({A: NC}, {A: {"name": NC, "pid": PA}}, {"sessions": {A: {"name": NC}}, "campaigns": {}}):
            (self.data / "sessions.json").write_text(dump(body))
            scan = self.scanner()
            scan._read_claims()
            self.assertEqual(scan.claimed, {A: NC})

    def test_only_a_real_listen_command_names_a_session(self):
        self.two_masters()
        self.write(A, bash(T0, 'python3 tap.py title --as "Claude · other" "Work"'),
                   bash(T0, "pgrep -f 'tap.py listen --as Claude'"),
                   bash(T0, 'echo "python3 tap.py listen --as \\"Claude · other\\""'),
                   {"type": "user", "timestamp": iso(T0), "message": {"role": "user", "content":
                    'Run python3 tap.py listen --as "Claude · other" now'}})
        self.scan.tick()
        self.assertEqual(self.scan.heard[A], NA)

    def test_unknown_sessions_are_read_only_at_boot(self):
        self.two_masters()
        self.scan.tick()                                     # boot
        self.live(PC, C, "gamma-5e")
        self.write(C, listen(T0, NC), got_busy(T0 + 1, "m1", PA))
        self.scan.tick()
        self.assertNotIn(C, self.scan.heard)
        self.assertEqual(self.scan.rows({NA, NC}), [])
        (self.data / "sessions.json").write_text(dump({C: {"name": NC}}))
        self.scan.tick()
        self.assertEqual(self.scan.rows({NA, NC})[0]["from"], NA)


class Serving(Rig):
    def test_newest_message_sets_from_summary_and_last(self):
        self.two_masters()
        self.write(A, send(T0, "toolu_1", "beta-3c", summary="First"), result(T0, "toolu_1", "m1"))
        self.write(B, send(T0 + 10, "toolu_2", "alpha-1a", summary="Answer"), result(T0 + 10, "toolu_2", "m2"))
        self.scan.tick()
        self.assertEqual(self.scan.rows({NA, NB}), [{"a": NA, "b": NB, "last": T0 + 10, "count": 2,
                                                    "summary": "Answer", "active": True, "from": NB}])

    def test_rows_keep_only_given_names_newest_first(self):
        self.two_masters()
        self.live(PC, C, "gamma-5e")
        self.write(C, listen(T0 - 600, NC), send(T0 + 20, "toolu_3", "alpha-1a", summary="C"),
                   result(T0 + 20, "toolu_3", "m3"))
        self.write(A, send(T0, "toolu_1", "beta-3c", summary="B"), result(T0, "toolu_1", "m1"))
        self.scan.tick()
        self.assertEqual([(r["a"], r["b"]) for r in self.scan.rows({NA, NB, NC})], [(NA, NC), (NA, NB)])
        self.assertEqual([(r["a"], r["b"]) for r in self.scan.rows({NA, NB})], [(NA, NB)])

    def test_active_for_ten_minutes_served_for_a_day(self):
        self.two_masters()
        self.write(A, send(T0, "toolu_1", "beta-3c", summary="x"), result(T0, "toolu_1", "m1"))
        self.scan.tick()
        self.now = T0 + 599
        self.assertTrue(self.scan.rows({NA, NB})[0]["active"])
        self.now = T0 + 601
        self.assertFalse(self.scan.rows({NA, NB})[0]["active"])
        self.now = T0 + 86401
        self.assertEqual(self.scan.rows({NA, NB}), [])
        self.scan.tick()
        self.assertEqual(self.scan.stats()["messages"], 0)

    def test_bump_only_for_a_fresh_link(self):
        self.two_masters()
        self.write(A, send(T0, "toolu_1", "beta-3c", summary="x"), result(T0, "toolu_1", "m1"))
        self.now = T0 + 3600
        self.scan.tick()
        self.assertEqual(self.bumps, [])
        self.write(A, send(self.now - 5, "toolu_2", "beta-3c", summary="y"), result(self.now - 5, "toolu_2", "m2"))
        self.scan.tick()
        self.assertEqual(self.bumps, [("agents",)])

    def test_links_file_survives_a_restart_and_keeps_one_day(self):
        (self.data / "links.jsonl").write_text(
            dump({"msg": "old", "ts": T0 - 90000, "from": NA, "to": NB, "summary": "old"}) + "\n" +
            "not json\n" +
            dump({"msg": "m9", "ts": T0, "from": NB, "to": NA, "summary": "kept"}) + "\n")
        self.scan.tick()
        self.assertEqual(self.scan.rows({NA, NB})[0]["summary"], "kept")
        self.assertEqual([r["msg"] for r in self.file_rows()], ["m9"])

    def test_partial_last_line_waits_for_its_newline(self):
        self.two_masters()
        self.scan.tick()
        f = self.claude / "projects" / "-Users-x" / f"{A}.jsonl"
        tail = dump(send(T0, "toolu_1", "beta-3c", summary="Whole")) + "\n" + dump(result(T0, "toolu_1", "m1"))
        with f.open("a") as fh:
            fh.write(tail[:-15])
        self.scan.tick()
        self.assertEqual(self.scan.rows({NA, NB}), [])
        with f.open("a") as fh:
            fh.write(tail[-15:] + "\n")
        self.scan.tick()
        self.assertEqual(self.scan.rows({NA, NB})[0]["summary"], "Whole")

    def test_module_is_empty_until_started(self):
        saved, links._ONE = links._ONE, None
        self.addCleanup(setattr, links, "_ONE", saved)
        self.assertEqual(links.rows({NA}), [])
        self.assertEqual(links.stats(), {})

    def test_start_is_idempotent_and_the_module_serves_it(self):
        saved, links._ONE = links._ONE, None
        self.addCleanup(setattr, links, "_ONE", saved)
        self.addCleanup(setattr, links, "CLAUDE_DIR", links.CLAUDE_DIR)
        links.CLAUDE_DIR = self.claude                       # never the real ~/.claude in a test
        one = links.start(self.data)
        self.assertIs(links.start(self.data), one)
        self.assertEqual(links.rows({NA, NB}), [])
        self.assertIn("passes", links.stats())


class Talks(Rig):
    def test_each_message_is_a_talk_for_ten_minutes(self):
        self.two_masters()
        self.write(A, send(T0, "toolu_1", "beta-3c", summary="First"), result(T0, "toolu_1", "m1"))
        self.write(B, send(T0 + 10, "toolu_2", "alpha-1a", summary="Answer"), result(T0 + 10, "toolu_2", "m2"))
        self.scan.tick()
        self.assertEqual(self.scan.talks({NA, NB}), [
            {"id": "m2", "from": NB, "to": NA, "ts": T0 + 10, "text": "Answer", "kind": "message"},
            {"id": "m1", "from": NA, "to": NB, "ts": T0, "text": "First", "kind": "message"}])
        self.assertEqual(self.scan.talks({NA}), [])                  # both ends must be served
        self.now = T0 + 605
        self.assertEqual([t["id"] for t in self.scan.talks({NA, NB})], ["m2"])
        self.now = T0 + 611
        self.assertEqual(self.scan.talks({NA, NB}), [])
        self.assertEqual(self.scan.rows({NA, NB})[0]["count"], 2)     # the pair row still serves the day

    def test_each_flightdeck_call_to_a_tap_is_a_talk(self):
        self.two_masters()
        self.write(A, pilot(T0, "toolu_m", "message_pilot", journey="SnappStats/snapp-ios#1622",
                            text="From Muddi: the staging sign-in steps in ~/docs/pilot.md were corrected (#1620)\nmore"),
                   pilot_result(T0, "toolu_m", OK_SENT),
                   pilot(T0 + 1, "toolu_a", "answer_pilot", journey="snappstats/snapp-ios#1614",
                         toolUseID="toolu_x", choice="Pilot removes fly label"),
                   pilot_result(T0 + 1, "toolu_a", OK_SENT, blocks=True),
                   pilot(T0 + 2, "toolu_f", "fly_journey", journey="https://github.com/SnappStats/snappy/issues/3323",
                         quiet=True, ultracode=True),
                   pilot_result(T0 + 2, "toolu_f", json.dumps({"ok": True, "queued": True, "how": "flightdeck:fly"})))
        self.scan.tick()
        self.assertEqual(self.scan.talks({NA, NB}), [
            {"id": "toolu_f", "from": NA, "to": "SnappStats/snappy#3323", "ts": T0 + 2, "text": "Flying it", "kind": "tap"},
            {"id": "toolu_a", "from": NA, "to": "snappstats/snapp-ios#1614", "ts": T0 + 1,
             "text": "Pilot removes fly label", "kind": "tap"},
            {"id": "toolu_m", "from": NA, "to": "SnappStats/snapp-ios#1622", "ts": T0,
             "text": "the staging sign-in steps were corrected", "kind": "tap"}])
        self.assertEqual(self.scan.rows({NA, NB}), [])                # never a pair row
        self.assertEqual(self.file_rows(), [])                        # and never in links.jsonl
        self.assertEqual(self.bumps, [("agents",)])

    def test_failed_or_undelivered_calls_and_unnamed_sessions_make_no_talk(self):
        self.two_masters()
        self.write(A, pilot(T0, "toolu_1", "fly_journey", journey="SnappStats/snappy#3285"),
                   pilot_result(T0, "toolu_1", "GitHub refused the option label", error=True),
                   pilot(T0 + 1, "toolu_2", "message_pilot", journey="SnappStats/snapp-ios#1622", text="x"),
                   pilot_result(T0 + 1, "toolu_2", json.dumps({"ok": True, "undelivered": True})),
                   pilot(T0 + 2, "toolu_3", "message_pilot", journey="not a key", text="x"),
                   pilot_result(T0 + 2, "toolu_3", OK_SENT),
                   pilot(T0 + 3, "toolu_4", "message_pilot", journey="SnappStats/snapp-ios#1622", text="no result yet"))
        self.scan.tick()
        self.assertEqual(self.scan.talks({NA, NB}), [])
        self.write(A, pilot_result(T0 + 3, "toolu_4", OK_SENT))      # its result lands on a later pass
        self.scan.tick()
        self.assertEqual([t["id"] for t in self.scan.talks({NA, NB})], ["toolu_4"])
        self.assertEqual(self.scan.talks({NB}), [])                   # its sender is not served

    def test_a_talk_waits_for_its_session_to_have_a_name(self):
        self.write(A, pilot(T0, "toolu_1", "fly_journey", journey="o/r#1"), pilot_result(T0, "toolu_1", "{}"))
        self.scan.tick()
        self.assertEqual(self.scan.talks({NA}), [])
        (self.data / "sessions.json").write_text(dump({"sessions": {A: {"name": NA}}}))   # tap.py's stamp, later
        self.scan.tick()
        self.assertEqual([(t["from"], t["to"]) for t in self.scan.talks({NA})], [(NA, "o/r#1")])

    def test_a_scan_every_two_seconds(self):
        self.assertEqual(links.EVERY, 2)

    def test_line_and_journey(self):
        self.assertEqual(links.line("\n## From Muddi: Ship the lineup page first\nmore"), "Ship the lineup page first")
        self.assertEqual(links.line(" ".join(["w"] * 80), cap=20), "w w w w w w w w...")
        self.assertEqual(links.journey("https://github.com/snappstats/snappy/issues/3286"), "snappstats/snappy#3286")
        self.assertEqual(links.journey("SnappStats/snappy#3286"), "SnappStats/snappy#3286")
        self.assertEqual(links.journey("snappy#3286"), "")


class Text(Rig):
    def test_summary_is_scrubbed_and_clipped(self):
        c = links._clip
        self.assertEqual(c("Merged PR #1551, sha f11a070 at ~/Developer/tapspace/server.py ready"), "Merged PR, ready")
        self.assertEqual(c("PR #1551 green, ready to merge"), "PR green, ready to merge")
        self.assertEqual(c("Pushed tip, idle, Chat rail is yours"), "Pushed tip, idle, Chat rail is yours")
        self.assertEqual(c("Rail done \u2014 panel next"), "Rail done, panel next")
        self.assertEqual(c("A decade of facade work"), "A decade of facade work")
        self.assertEqual(c("Session e8f091d4-ad6f-481a-b8dc-0f9fb206ebda is up"), "Session is up")
        self.assertEqual(c("See https://github.com/hperwin/tapspace/pull/2 then `tap.py up`"), "See then up")
        long = c("word " * 40)
        self.assertLessEqual(len(long), 80)
        self.assertTrue(long.endswith("..."))
        self.assertEqual(c(None), "")

    def test_first_words_when_a_send_has_no_summary(self):
        f = links._first_words
        self.assertEqual(f("**From the Glint master:** Rail is live at ~/Developer/tapspace, PR #12 open and the panel is next\nmore"),
                         "Rail is live, PR open and the panel...")
        self.assertEqual(f("\n\n# Status\nall green"), "Status")
        self.assertEqual(f("Short note"), "Short note")
        self.assertEqual(f({"type": "shutdown_request"}), "")
        self.assertEqual(f("Hey there, please ask Claude · beta · 3c4d to look at the rail now", {NB: "Beta work"}),
                         "Hey there, please ask Beta work to look...")

    def test_codes_never_show(self):
        """Every case R4 F4 found passing through, then the neighbours of each rule."""
        c = links._clip
        self.assertEqual(c("Fixed server.py:1415 and Glint/Interface/Chat/ThreadPill.swift"), "Fixed")
        self.assertEqual(c("Read /opt/homebrew/bin/tmux logs"), "Read logs")
        self.assertEqual(c("Ticket 3F7DE0B7AF approved, session 5903E6DD-1AA4-4531-AD5D-E85C9AE9230F"),
                         "Ticket approved")
        self.assertEqual(c("Ask Claude · home · e3cc to rebase", {"Claude · home · e3cc": "Home screen"}),
                         "Ask Home screen to rebase")
        self.assertEqual(c("Use key sk-ant-api03-AbCd... to call it"), "Use key to call it")
        self.assertEqual(c("Deploy to gs://snappstats-stg-videos/..."), "Deploy")
        self.assertEqual(c("Worktree ../tapspace-wt-integrate is clean"), "Worktree is clean")
        self.assertEqual(c("Open C:\\Users\\x\\notes.txt now"), "Open now")
        self.assertEqual(c("Fix in links.py, then push"), "Fix, then push")
        self.assertEqual(c("Rebased onto 026A6BD, tests green"), "Rebased, tests green")
        self.assertEqual(c("Ticket 5284886400 approved"), "Ticket approved")
        self.assertEqual(c("Push with ghp_A1b2C3d4E5f6G7h8I9j0K1l2 now"), "Push now")
        self.assertEqual(c("Ping Claude · zz · 9999 about it"), "Ping Claude about it")
        self.assertEqual(c("Ping Claude · main about it", {"Claude · main": "Glint app"}), "Ping Glint app about it")
        self.assertEqual(c("Claude · mainline is up", {"Claude · main": "Glint app"}), "Claude is up")
        for kept in ("Done 14/20 builders, e.g. the rail", "Is the expert feed live yet?",
                     "Tell action-slip car flag is beta-only", "Progress cards are in; can the phone read them now?",
                     "Claude is on it", "Build 3 of 5 at 2:15 PM"):
            self.assertEqual(c(kept), kept)

    def test_names_in_a_send_show_as_titles(self):
        self.scan = links.Links(self.data, claude_dir=self.claude, clock=lambda: self.now,
                                titles=lambda: {NA: "Alpha work", NB: "Beta work"})
        self.two_masters()
        self.write(A, send(T0, "toolu_1", "beta-3c", summary="Claude · alpha · 1a2b is done, over to you"),
                   result(T0, "toolu_1", "m1"))
        self.write(B, got_busy(T0, "m1", PA))
        self.scan.tick()
        self.assertEqual(self.scan.rows({NA, NB})[0]["summary"], "Alpha work is done, over to you")
        self.assertEqual([r["summary"] for r in self.file_rows()], ["Alpha work is done, over to you"])

    def test_bodies_are_never_kept(self):
        self.two_masters()
        body = "Alpha bravo charlie delta echo foxtrot golf hotel india zebrasecretword"
        self.write(A, send(T0, "toolu_1", "beta-3c", message=body), result(T0, "toolu_1", "m1"))
        self.write(B, got_busy(T0, "m1", PA, body=body))
        self.scan.tick()
        self.assertEqual(self.scan.rows({NA, NB})[0]["summary"], "Alpha bravo charlie delta echo foxtrot golf hotel...")
        self.assertNotIn("zebrasecretword", (self.data / "links.jsonl").read_text())
        self.assertNotIn("zebrasecretword", repr(vars(self.scan)))


if __name__ == "__main__":
    unittest.main()
