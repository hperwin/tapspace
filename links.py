"""Agent-to-agent links: which master agents are messaging each other, found without them reporting it.

Claude Code writes every cross-session SendMessage into both ends' session transcripts
(~/.claude/projects/<cwd-slug>/<sessionId>.jsonl). The sender's transcript holds the SendMessage
call (to, summary) and its result (msg_id); the receiver's holds the delivered message with the
sender's verified claude pid and the same msg_id. A thread tails the transcripts of known sessions
(data/sessions.json from tap.py's session stamp, plus a one-time boot scan of each session's own
`tap.py listen --as NAME`), joins the two halves on msg_id, maps both sessions to TapSpace NAMEs
and keeps one small record per message in data/links.jsonl. Message bodies are never kept: only the
sender's own summary (or the first words of its first line), with paths, files, links, hex ids,
keys and #numbers removed and agent NAMEs shown as their titles.

The same pass finds a master talking to a tap: a successful Flightdeck message_pilot, answer_pilot or
fly_journey call in a known session's transcript (the journey key is in the call). Those talks are kept
in memory for LIVE only, are found again by the boot scan, and never enter the pair rows.

server.py:  links.start(DATA, _bump, titles)   once, in __main__ (daemon thread, one pass every 2 s)
            links.rows(names)          the top-level "links" array of GET /api/agents
            links.talks(names)         per-message talks within LIVE, for GET /api/agents "talks"
            links.stats()              counters for /api/activity and tap.py doctor
"""
import json
import os
import pathlib
import re
import threading
import time
from datetime import datetime

CLAUDE_DIR = pathlib.Path(os.environ.get("TAPSPACE_CLAUDE_DIR") or pathlib.Path.home() / ".claude")
WINDOW = 86400              # links are served, kept and persisted for a day
LIVE = 600                  # a pair is active this long after its newest message
EVERY = 2                   # seconds between passes (a pass costs about 10 ms)
TAIL_FIRST = 4_000_000      # first sight of a transcript: read only its last 4 MB
TAIL_MAX = 16_000_000       # never read more than this from one file in one pass
PENDING_MAX = 200           # SendMessage calls per transcript still waiting for their result line
SUMMARY_MAX = 80
FIRST_WORDS = 8

_SOCK = re.compile(r"^uds:\S*/(\d+)\.sock$")
_LISTEN = re.compile(r"""(?:^|[\s;&|(])\S*python3?\s+\S*tap\.py\s+listen\s+--as[\s=]+(?:"([^"\\]+)"|'([^'\\]+)'|([^\s"';&|\\]{2,}))""")
_CODE = re.compile(                                              # one word of a summary that no agent UI shows
    r"^(?!\d+/\d+$).*[/\\]"                                      # paths, scheme:// links (a count like 14/20 stays)
    r"|^(?=.{4})[\w@+-][\w.@+-]*\.[a-z][a-z0-9]{0,9}(?::\d+)*$"  # files, with an optional :line ("e.g" stays)
    r"|(?<![0-9a-z])(?=[0-9a-f]*\d)[0-9a-f]{8,}(?![0-9a-z])"     # hex ids of 8 or more, uuids ("decade" stays)
    r"|^(?=[0-9a-f]*\d)(?=\d*[a-f])[0-9a-f]{6,7}$"               # short commit shas
    r"|#\d"                                                      # issue and PR numbers
    r"|^sk-|^(?=.*\d)(?=.*[a-z])[\w+=-]{20,}$",                  # keys: sk-..., long base64-ish tokens
    re.I)
_WORD = re.compile(r"""^(["'(\[{<]*)(.*?)([)\]}>"',;:.!?\u2026]*)$""")
_BEFORE = {"at", "in", "to", "on", "onto", "from", "and", "or", "of", "for", "with", "into", "via", "under",
           "sha", "commit"}                                      # goes with the word after it
_LABEL = {"session", "ticket", "id", "key", "file", "path", "pid", "branch", "worktree", "token"}  # ... at a clause end
_CLAUDE = r"\bClaude(?:\s*\u00b7\s*[\w-]+)+"                     # a NAME no agent title is known for
_PREAMBLE = re.compile(r"^\s*(?:[#>*+-]+\s*)*(?:from\b[^:\n]{0,80}:\s*)?", re.I)
_SID = re.compile(r"^[0-9a-fA-F-]{8,64}$")
_NEEDLES = (b'"SendMessage"', b'"msg_id"', b'"verifiedPeerPid"', b"tap.py listen", b'"mcp__flightdeck__')
PILOT = {"mcp__flightdeck__message_pilot", "mcp__flightdeck__answer_pilot", "mcp__flightdeck__fly_journey"}
_JOURNEY = re.compile(r"^(?:https?://github\.com/)?([\w.-]+)/([\w.-]+)(?:#|/issues/)(\d+)/?$")


def _epoch(iso):
    try:
        return datetime.fromisoformat(str(iso).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return None


def _scrub(s):
    """s without the words in _CODE. A word that goes takes along the one before it when that is
    "at", "in", "and"... (or "session", "key"... when it ended a clause); its , ; : . ! ? stay."""
    out = []
    words = s.split()
    for i, w in enumerate(words):
        lead, core, trail = _WORD.match(w).groups()
        if not core or not _CODE.search(core):
            out.append(w)
            continue
        trail = re.sub(r"[^,;:.!?]|\.{2,}", "", trail)
        end = bool(trail) or i == len(words) - 1
        if out and (out[-1].lower() in _BEFORE or end and out[-1].lower() in _LABEL):
            out.pop()
        if trail and out and out[-1][-1] not in ",;:.!?":
            out[-1] += trail
    return " ".join(out)


def _plain(text):
    s = _scrub(str(text or "").replace("\u2014", ", ").replace("`", "").replace("**", ""))
    s = re.sub(r"\s+([,;:.!?])", r"\1", s)
    return re.sub(r"[,;:](?=[,;:]|$)", "", s).strip(" -:;,.")


def _named(s, titles):
    """Every agent NAME in s ("Claude \u00b7 home \u00b7 e3cc") as that agent's title; one with no known title as Claude."""
    titles = titles or {}
    known = [re.escape(n) for n in sorted(titles, key=len, reverse=True) if n]
    pat = (f"(?:{'|'.join(known)})(?![\\w-])|" if known else "") + _CLAUDE
    return re.sub(pat, lambda m: titles.get(m.group(0)) or "Claude", s)


def _cut(s, cap=SUMMARY_MAX):
    return s if len(s) <= cap else s[:cap - 3].rsplit(" ", 1)[0].rstrip(" ,;:.") + "..."


def _clip(summary, titles=None, cap=SUMMARY_MAX):
    """The sender's own SendMessage summary as every agent UI may show it: no paths, files, links,
    hex ids, keys, #numbers or em dashes, agents by title (titles: {NAME: title}); at most
    cap (SUMMARY_MAX) characters."""
    return _cut(_named(_plain(summary), titles), cap)


def line(text, titles=None, cap=SUMMARY_MAX):
    """The first line of text that has words left once cleaned like a summary (a leading "From ...:" or
    markdown mark dropped), at most cap characters: a talk's text to a tap, a spawned master's brief."""
    if not isinstance(text, str):
        return ""
    for raw in text.splitlines():
        s = _clip(_PREAMBLE.sub("", raw, count=1), titles, cap)
        if s:
            return s
    return ""


def journey(ref):
    """A Flightdeck journey ref as a tap key, owner/repo#n (an issue URL too); "" for anything else.
    The case is the caller's: the server matches it to the key it serves."""
    m = _JOURNEY.match(str(ref or "").strip())
    return f"{m.group(1)}/{m.group(2)}#{m.group(3)}" if m else ""


def _delivered(content):
    """A Flightdeck tool result that did what it was asked: its JSON (a string, or the text of its first
    block) says ok and not undelivered. A result that is not JSON counts only when it is not an error."""
    if isinstance(content, list):
        content = next((b.get("text") for b in content if isinstance(b, dict) and b.get("type") == "text"), "")
    try:
        r = json.loads(content) if isinstance(content, str) else None
    except ValueError:
        return True
    return not isinstance(r, dict) or (r.get("ok") is not False and not r.get("undelivered"))


def _first_words(body, titles=None):
    """For a send without a summary: the first words of the message's first non-empty line
    (a leading "From ...:" preamble dropped), cleaned like a summary. Nothing else of the body."""
    if not isinstance(body, str):
        return ""
    for line in body.splitlines():
        words = _named(_plain(_PREAMBLE.sub("", line, count=1)), titles).split()
        if words:
            more = len(words) > FIRST_WORDS
            return _cut(" ".join(words[:FIRST_WORDS]).rstrip(" ,;:.") + ("..." if more else ""))
    return ""


def _events(o, pend, titles=dict):
    """Pure: one parsed transcript line -> events. `pend` carries a SendMessage call id to its
    (to, text, ts) until the result line in the same transcript brings the msg_id, and a Flightdeck
    call id (PILOT) to {"tap", "text", "ts"} until its result. titles() gives {NAME: title} for the
    text, read only when a SendMessage or a Flightdeck call is in the line.
      ("name", NAME)                       this session listens under NAME
      ("sent", msg_id, to_ref, ts, text)   this session sent msg_id; to_ref ("pid", n) | ("name", s)
      ("got", msg_id, pid, ts)             this session received msg_id from claude process pid
      ("tap", call_id, key, ts, text)      this session talked to the tap key (owner/repo#n)
    In-session teammates (a result with `routing`), failed sends and Flightdeck calls, in-process
    peers (no verifiedPeerPid), queue-operation and api-request-blob copies produce nothing."""
    out = []
    t = o.get("type")
    msg = o.get("message") if isinstance(o.get("message"), dict) else {}
    content = msg.get("content") if isinstance(msg.get("content"), list) else []
    if t == "assistant":
        for b in content:
            if not isinstance(b, dict) or b.get("type") != "tool_use":
                continue
            i = b.get("input") if isinstance(b.get("input"), dict) else {}
            if b.get("name") == "SendMessage":
                ts = _epoch(o.get("timestamp"))
                if ts is not None and b.get("id"):
                    names = titles()
                    pend[b["id"]] = (str(i.get("to") or ""),
                                     _clip(i.get("summary"), names) or _first_words(i.get("message"), names), ts)
            elif b.get("name") in PILOT:
                ts, key = _epoch(o.get("timestamp")), journey(i.get("journey"))
                if ts is not None and key and b.get("id"):
                    text = "Flying it" if b["name"].endswith("fly_journey") else \
                        line(str(i.get("text") or i.get("choice") or ""), titles())
                    pend[b["id"]] = {"tap": key, "text": text, "ts": ts}
            elif b.get("name") == "Bash":
                m = None
                for m in _LISTEN.finditer(str(i.get("command") or "")):
                    pass                                    # the last listen in the command wins
                if m:
                    out.append(("name", m.group(1) or m.group(2) or m.group(3)))
    elif t == "user":
        for b in content:                                   # a Flightdeck call's result
            p = pend.get(b.get("tool_use_id")) if isinstance(b, dict) else None
            if isinstance(p, dict):
                del pend[b["tool_use_id"]]
                if not b.get("is_error") and _delivered(b.get("content")):
                    out.append(("tap", str(b["tool_use_id"]), p["tap"], p["ts"], p["text"]))
        r = o.get("toolUseResult")
        if isinstance(r, dict) and r.get("msg_id"):
            for b in content:
                p = pend.pop(b.get("tool_use_id"), None) if isinstance(b, dict) else None
                if not p or not r.get("success") or "routing" in r:
                    continue
                to, text, ts = p
                s = _SOCK.match(to)
                out.append(("sent", str(r["msg_id"]), ("pid", int(s.group(1))) if s else ("name", to), ts, text))
    att = o.get("attachment") if t == "attachment" and isinstance(o.get("attachment"), dict) else {}
    org = o.get("origin") if t == "user" else att.get("origin")
    if isinstance(org, dict) and org.get("kind") == "peer" and org.get("verifiedPeerPid") and org.get("msg_id"):
        ts = _epoch(o.get("timestamp"))
        if ts is not None:
            out.append(("got", str(org["msg_id"]), int(org["verifiedPeerPid"]), ts))
    return out


class Links:
    """The scanner. Every method but rows() and stats() runs on its one thread."""

    def __init__(self, data_dir, bump=None, claude_dir=None, clock=time.time, titles=None):
        data = pathlib.Path(data_dir)
        self.file = data / "links.jsonl"
        self.claims = data / "sessions.json"
        self.claude = pathlib.Path(claude_dir or CLAUDE_DIR)
        self.bump = bump or (lambda *kinds: None)
        self.clock = clock
        self.titles = titles or dict    # () -> {NAME: title}: a NAME in a summary shows as its title
        self._lock = threading.Lock()   # guards self.links (read by HTTP threads); never server._lock
        self.links = {}                 # msg_id -> {"msg", "ts", "from": NAME, "to": NAME, "summary"}
        self.taps = {}                  # call id -> {"id", "ts", "from": NAME, "to": tap key, "text"}, within LIVE
        self.tap_pending = {}           # call id -> {"sid", "ts", "to", "text"} until sid has a NAME
        self.pending = {}               # msg_id -> {"from": sid|None, "to": sid|None, "ts", "text"}
        self.heard = {}                 # sid -> NAME from the session's own tap.py listen --as
        self.claimed = {}               # sid -> NAME from data/sessions.json (tap.py's stamp)
        self.off = {}                   # transcript path -> bytes consumed
        self.pend = {}                  # sid -> {SendMessage call id: (to, text, ts)}
        self.booted = False
        self.counts = {"transcripts": 0, "passes": 0, "errors": 0, "ms": 0.0, "last": 0.0}
        self._claims_mtime = None
        self._reg = None                # ~/.claude/sessions/*.json, read at most once per pass
        self._out = []                  # rows to append to links.jsonl after this pass
        self._fresh = False             # a link turned up or changed inside LIVE: wake /api/wait

    def start(self, every=EVERY):
        def loop():
            while True:
                try:
                    self.tick()
                except Exception:
                    self.counts["errors"] += 1      # a vanished file or an odd line never stops it
                time.sleep(every)
        t = threading.Thread(target=loop, name="links", daemon=True)
        t.start()
        return t

    def tick(self):
        """One pass. The first pass (boot) reads every top-level transcript touched in the last
        day; later passes read only the sessions known by then (claimed or heard)."""
        t0 = time.monotonic()
        now = self.clock()
        if not self.booted:
            self._load(now)
        self._read_claims()
        self._reg, self._out, self._fresh = None, [], False
        known = set(self.claimed) | set(self.heard)
        tailed = 0
        for f in sorted(self.claude.glob("projects/*/*.jsonl")):     # top level only, never subagents/
            if f.name.startswith("agent-") or (self.booted and f.stem not in known):
                continue
            tailed += self._read(f, now)
        self.booted = True
        self._resolve(now)
        if self._out:
            self._append(self._out)
        self.counts.update(transcripts=tailed, passes=self.counts["passes"] + 1,
                           ms=round((time.monotonic() - t0) * 1000, 1), last=now)
        if self._fresh:
            self.bump("agents")

    def _read(self, f, now):
        """Consume the complete lines appended to transcript f since the last pass."""
        try:
            st = f.stat()
        except OSError:
            return 0
        off = self.off.get(f)
        skip = False
        if off is None or st.st_size < off:             # first sight, or the file was rewritten
            if off is None and now - st.st_mtime > WINDOW:
                return 0
            off = max(0, st.st_size - TAIL_FIRST)
            skip = off > 0
        if st.st_size - off > TAIL_MAX:
            off, skip = st.st_size - TAIL_MAX, True
        if st.st_size == off:
            self.off[f] = off
            return 1
        try:
            with f.open("rb") as fh:
                fh.seek(off)
                chunk = fh.read(st.st_size - off)
        except OSError:
            return 0
        if skip:                                        # started mid-line: drop that partial line
            nl = chunk.find(b"\n") + 1
            chunk, off = chunk[nl:], off + nl
        end = chunk.rfind(b"\n") + 1                    # a partial last line waits for next pass
        self.off[f] = off + end
        sid = f.stem
        pend = self.pend.setdefault(sid, {})
        calls = [k.encode() for k, v in pend.items() if isinstance(v, dict)]   # Flightdeck calls awaiting results
        for raw in chunk[:end].splitlines():
            if not any(n in raw for n in _NEEDLES) and not any(k in raw for k in calls):
                continue                                # about 2 percent of lines get parsed
            try:
                evs = _events(json.loads(raw), pend, self._titles)
            except Exception:
                self.counts["errors"] += 1
                continue
            calls = [k.encode() for k, v in pend.items() if isinstance(v, dict)]
            for ev in evs:
                self._take(sid, ev)
        for k in list(pend)[:-PENDING_MAX]:             # results that never came: keep the newest
            pend.pop(k, None)
        return 1

    def _titles(self):
        try:
            return dict(self.titles())
        except Exception:
            self.counts["errors"] += 1
            return {}

    def _take(self, sid, ev):
        if ev[0] == "name":
            self.heard[sid] = ev[1]
        elif ev[0] == "tap":
            _, cid, key, ts, text = ev
            if cid not in self.taps:
                self.tap_pending[cid] = {"sid": sid, "ts": ts, "to": key, "text": text}
        elif ev[0] == "sent":
            _, mid, (how, ref), ts, text = ev
            other = self._session(ts, pid=ref) if how == "pid" else self._session(ts, name=ref)
            self._merge(mid, sid, other, ts, text)
        else:
            _, mid, pid, ts = ev
            self._merge(mid, self._session(ts, pid=pid), sid, ts, "")

    def _session(self, at, pid=None, name=None):
        """The session id of the Claude Code session with this pid, or addressed by this
        SendMessage name, that was already running at `at` (a reused pid or name never claims an
        older message). None when unknown or ambiguous. Reads ~/.claude/sessions/*.json only,
        never the *.key files beside them. A session's file is gone once it exits, which is why
        both halves of a message are read and joined."""
        if self._reg is None:
            self._reg = []
            for f in (self.claude / "sessions").glob("*.json"):
                try:
                    d = json.loads(f.read_text())
                except (OSError, ValueError):
                    continue
                if isinstance(d, dict) and d.get("sessionId"):
                    self._reg.append(d)
        try:
            hits = [d for d in self._reg
                    if (d.get("pid") == pid if pid is not None else d.get("name") == name)
                    and float(d.get("startedAt") or 0) / 1000 <= at + 5]
        except (TypeError, ValueError):
            return None
        return str(hits[0]["sessionId"]) if len(hits) == 1 else None

    def _merge(self, mid, frm, to, ts, text):
        """Each half knows its own session for sure (the file it is in) and the other one through
        the registry; msg_id joins them. A sender half that lands after its message was already
        resolved from the receiver half still brings the summary."""
        row = self.links.get(mid)
        if row is not None:
            if text and not row["summary"]:
                with self._lock:
                    row["summary"] = text
                self._out.append(dict(row))
                self._fresh = self._fresh or self.clock() - row["ts"] < LIVE
            return
        m = self.pending.setdefault(mid, {"from": None, "to": None, "ts": ts, "text": ""})
        m["from"] = m["from"] or frm
        m["to"] = m["to"] or to
        m["text"] = m["text"] or text

    def _resolve(self, now):
        names = {**self.heard, **self.claimed}          # tap.py's stamp wins over the transcript
        for mid, m in list(self.pending.items()):
            if now - m["ts"] > WINDOW:
                del self.pending[mid]
                continue
            a, b = names.get(m["from"]), names.get(m["to"])
            if not (a and b):
                continue                                # an end may still be claimed later
            del self.pending[mid]
            if a == b:
                continue                                # one agent at both ends: a relay handoff
            row = {"msg": mid, "ts": m["ts"], "from": a, "to": b, "summary": m["text"]}
            with self._lock:
                self.links[mid] = row
            self._out.append(dict(row))
            self._fresh = self._fresh or now - m["ts"] < LIVE
        for cid, m in list(self.tap_pending.items()):
            a = names.get(m["sid"])
            if a or now - m["ts"] > LIVE:
                del self.tap_pending[cid]
            if a and now - m["ts"] <= LIVE:
                with self._lock:
                    self.taps[cid] = {"id": cid, "ts": m["ts"], "from": a, "to": m["to"], "text": m["text"]}
                self._fresh = True
        with self._lock:
            for k in [k for k, r in self.links.items() if now - r["ts"] > WINDOW]:
                del self.links[k]
            for k in [k for k, r in self.taps.items() if now - r["ts"] > LIVE]:
                del self.taps[k]

    def _read_claims(self):
        """data/sessions.json, re-read when it changes. Takes {sid: NAME}, {sid: {"name": NAME, ...}}
        or either under a "sessions" key; other keys (such as "campaigns") are skipped."""
        try:
            mtime = self.claims.stat().st_mtime
        except OSError:
            self.claimed, self._claims_mtime = {}, None
            return
        if mtime == self._claims_mtime:
            return
        try:
            d = json.loads(self.claims.read_text())
        except (OSError, ValueError):
            return                                      # mid-write or broken: keep the last good map
        self._claims_mtime = mtime
        if isinstance(d, dict) and isinstance(d.get("sessions"), dict):
            d = d["sessions"]
        out = {}
        for sid, v in (d.items() if isinstance(d, dict) else ()):
            name = v.get("name") if isinstance(v, dict) else v
            if _SID.match(str(sid)) and isinstance(name, str) and name:
                out[str(sid)] = name
        self.claimed = out

    def _load(self, now):
        """links.jsonl back into memory at boot (the last line per message wins), keeping only the
        window; rewritten without the rest, so the file never holds more than a day."""
        try:
            text = self.file.read_text()
        except OSError:
            return
        kept, lines = {}, 0
        for line in text.splitlines():
            lines += 1
            try:
                r = json.loads(line)
                row = {"msg": str(r["msg"]), "ts": float(r["ts"]), "from": str(r["from"]),
                       "to": str(r["to"]), "summary": str(r.get("summary") or "")}
            except (ValueError, KeyError, TypeError, AttributeError):
                continue
            if now - row["ts"] <= WINDOW and row["from"] and row["to"] and row["from"] != row["to"]:
                kept[row["msg"]] = row
        with self._lock:
            self.links = kept
        if lines != len(kept):
            tmp = self.file.with_suffix(".tmp")
            try:
                tmp.write_text("".join(json.dumps(r) + "\n" for r in kept.values()))
                tmp.replace(self.file)
            except OSError:
                self.counts["errors"] += 1

    def _append(self, rows):
        try:
            with self.file.open("a") as fh:
                fh.write("".join(json.dumps(r) + "\n" for r in rows))
        except OSError:
            self.counts["errors"] += 1

    def rows(self, names):
        """One row per pair of different agents in `names` that messaged each other in the last
        day, newest first: {"a", "b", "last", "count", "summary", "active", "from"}, a < b,
        `from` = sender of the newest message, `summary` = that message's, `active` = within LIVE."""
        now = self.clock()
        pairs = {}
        with self._lock:
            for r in sorted(self.links.values(), key=lambda r: r["ts"]):
                f, t = r["from"], r["to"]
                if now - r["ts"] > WINDOW or f == t or f not in names or t not in names:
                    continue
                a, b = sorted((f, t))
                p = pairs.setdefault((a, b), {"a": a, "b": b, "last": 0.0, "count": 0,
                                              "summary": "", "active": False, "from": f})
                p.update({"last": r["ts"], "summary": r["summary"], "from": f})
                p["count"] += 1
        for p in pairs.values():
            p["active"] = now - p["last"] < LIVE
        return sorted(pairs.values(), key=lambda p: -p["last"])

    def talks(self, names):
        """Every talk within LIVE whose sender is in `names`, newest first: a message between two agents
        in names (kind "message", id the msg_id) or a Flightdeck call to a tap (kind "tap", id the call
        id; `to` the journey key as the transcript wrote it). {"id", "from", "to", "ts", "text", "kind"},
        ends as NAMEs and tap keys; the server turns them into face keys."""
        now = self.clock()
        with self._lock:
            out = [{"id": r["msg"], "from": r["from"], "to": r["to"], "ts": r["ts"], "text": r["summary"],
                    "kind": "message"} for r in self.links.values()
                   if now - r["ts"] <= LIVE and r["from"] != r["to"] and r["from"] in names and r["to"] in names]
            out += [{**r, "kind": "tap"} for r in self.taps.values() if now - r["ts"] <= LIVE and r["from"] in names]
        return sorted(out, key=lambda r: -r["ts"])

    def stats(self):
        with self._lock:
            messages = len(self.links)
        return {"messages": messages, "pending": len(self.pending),
                "sessions": len(set(self.claimed) | set(self.heard)),
                "claimed": len(self.claimed), "heard": len(self.heard), **self.counts}


_ONE = None


def start(data_dir, bump=None, titles=None):
    """Starts the one scanner of this process (idempotent) and returns it."""
    global _ONE
    if _ONE is None:
        _ONE = Links(data_dir, bump, titles=titles)
        _ONE.start()
    return _ONE


def rows(names):
    return _ONE.rows(names) if _ONE else []


def talks(names):
    return _ONE.talks(names) if _ONE else []


def stats():
    return _ONE.stats() if _ONE else {}
