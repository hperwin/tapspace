"""Live activity: what the lead is doing right now, read from its own Claude Code session (CONTRACT section 7).

Nobody reports anything. The lead's NAME has a claim in data/sessions.json (tap.py's session stamp, written by the
server's _claim; a relay's new car adds its own session under the same NAME). The newest live claim names the
session, the session id names its transcript (~/.claude/projects/*/<sid>.jsonl, found the way links.py finds it,
never from the folder the lead listens in), and the transcript's tail is read by byte offset, so an unchanged file
costs one stat. Each complete line moves a small state (step): a turn opens with a turnOrigin prompt and ends with
system/turn_duration; a tool_use waits for its tool_result. What the lead row serves:

  switching  a relay in progress                          Switching car
  gone       no live claimed session                      Offline
  working    a turn is open                               the running tool's word, Thinking, or what woke it
  waiting    turn over, its listener polled within 30 s   Listening, or Waiting on you while its newest card waits
  idle       turn over, nobody listening                  Idle

Words come from one fixed table of plain phrases (A18 section 4): no paths, codes or numbers. detail is "" or a title
the space already shows (a master's, a tap's, a helper's own description), cleaned by links._clip.

server.py:  activity.of(name, info, state, claims, proc_start, waits, titles, taps)   the lead row's "activity"
"""
import json
import os
import pathlib
import re
import shlex
import threading
import time

import links

CLAUDE_DIR = links.CLAUDE_DIR      # TAPSPACE_CLAUDE_DIR, else ~/.claude
TAIL_FIRST = 1_000_000             # first sight of a transcript: its last 1 MB (a turn boundary sits far closer)
TAIL_MAX = 8_000_000               # never read more than this from it in one pass
LISTEN_FRESH = 30                  # the rail's Here window: a listen poll this recent means the lead hears the reviewer
ORIGIN_HOLD = 1.0                  # what started a turn shows for its first second
PROC_EVERY = 10                    # seconds between checks that a claim's pid is still the process that claimed it
MISS_EVERY = 5                     # seconds before a session with no transcript yet is looked for again
KINDS_MAX = 200                    # background tasks remembered per transcript (what a notification is about)

_NEEDLES = (b'"tool_use"', b'"tool_result"', b'"turnOrigin"', b'"turn_duration"')
_REQUEST = b'{"type":"api-request",'      # a model request was sent; -blob and -shape copies never match
_HEREDOC = re.compile(r"<<-?\s*['\"]?(\w+)['\"]?.*?^\s*\1\s*$", re.S | re.M)
_SEP = {"&&", "||", ";", "|", "&", "(", ")", ";;", "|&", "$"}
_LEADS = {"do", "then", "else", "elif", "if", "while", "until", "!", "{", "time", "nohup", "exec", "command", "builtin"}
_QUIET = {"cd", "echo", "sleep", "true", "false", "printf", "export", "date", "set", "local", "mkdir", "test", "[",
          "[[", "for", "fi", "done", "esac", "case", "}", "source", ".", "wait", "trap", "unset", "read", "shift",
          "return", "exit", "touch", "nap", ":"}
_READS = {"cat", "grep", "egrep", "sed", "ls", "head", "tail", "find", "awk", "stat", "wc", "diff", "jq", "rg", "less",
          "file", "du", "df", "tree", "readlink", "realpath", "sort", "uniq", "cut", "column", "shasum", "md5", "cmp"}
_KEY = re.compile(r"\b([\w.-]+/[\w.-]+)(?:#|/issues/|/pull/)(\d+)\b")
_TASK = re.compile(r"/tasks/(\w+)\.output\b")
_TAG = re.compile(r"<(task-id|tool-use-id|status|summary)>(.*?)</\1>", re.S)
_LOCAL = re.compile(r"(?:127\.0\.0\.1|localhost):\d+")

# The fixed words, by what the lead ran (A18 section 4). Every word the line can show is in WORDS.
TAP_WORDS = {"listen": "Listening", "reply": "Writing to you", "card": "Sending you an update",
             "goal": "Updating its goal card", "step": "Updating its goal card", "taps": "Checking taps",
             "tap-read": "Reading a tap", "tap-say": "Answering a tap", "tap-watch": "Watching taps",
             "post": "Posting a card", "update": "Posting a card", "new": "Starting a master",
             "adopt": "Taking on a master", "progress": "Reading a master's progress", "lead": "Getting set up",
             "title": "Getting set up", "pair": "Getting set up", "status": "Checking a card",
             "wait": "Waiting on a card", "relay": "Handing off", "stop": "Spinning down a master",
             "doctor": "Checking TapSpace", "up": "Checking TapSpace"}
MUDDI_WORDS = {"fleet": "Checking the masters", "ledger": "Logging progress", "prompt": "Logging your message",
               "run": "Opening a run", "peer": "Finding a master"}
FLIGHTDECK_WORDS = {"read_journey": "Checking a tap", "fly_journey": "Launching a tap", "file_journey": "Filing a tap",
                    "message_pilot": "Messaging a pilot", "answer_pilot": "Answering a pilot",
                    "read_pilot_messages": "Reading a pilot's messages", "list_journeys": "Checking taps",
                    "introduce_pilots": "Messaging a pilot", "authenticate": "Signing in"}
TOOL_WORDS = {"SendMessage": "Messaging a master", "Monitor": "Arming a watcher", "Agent": "Sending a helper",
              "Task": "Sending a helper", "Workflow": "Running a team", "CronCreate": "Setting a check-in",
              "CronDelete": "Setting a check-in", "CronList": "Setting a check-in", "Skill": "Reading a playbook",
              "ReadMcpResourceTool": "Reading a guide", "ListMcpResourcesTool": "Reading a guide",
              "Grep": "Reading files", "Glob": "Reading files", "WebFetch": "Looking something up",
              "WebSearch": "Looking something up", "TaskStop": "Stopping a helper", "ListAgents": "Finding a master",
              "PushNotification": "Notifying you", "TodoWrite": "Planning", "NotebookEdit": "Editing a file"}
TURN_WORDS = {"scheduled": "Checking in", "peer": "Reading a master's message", "human": "Reading your message"}
WORDS = frozenset(list(TAP_WORDS.values()) + list(MUDDI_WORDS.values()) + list(FLIGHTDECK_WORDS.values())
                  + list(TOOL_WORDS.values()) + list(TURN_WORDS.values()) + [
    "Using TapSpace", "Filing a tap", "Checking a tap", "Commenting on a tap", "Looking through taps", "Checking a PR",
    "Checking GitHub", "Reading code", "Working on code", "Reading files", "Using a terminal", "Checking what's running",
    "Looking at screenshots", "Stopping a process", "Running a command", "Writing an order", "Saving a note",
    "Updating its rules", "Writing a file", "Editing a file", "Reading what came in", "Searching ads",
    "Using a connected app", "Working", "Reading your message", "Seeing a tap change", "Seeing a master move",
    "Reading a helper's findings", "Reading a team's findings", "Thinking", "Listening", "Waiting on you", "Idle",
    "Offline", "Switching car"])


def _segments(cmd):
    """A shell command's simple commands, in order, each as its words: assignments, keywords and the commands that
    only move around (cd, echo, sleep...) dropped, heredoc bodies skipped."""
    cmd = _HEREDOC.sub(" HEREDOC ", cmd[:4000]).replace("\n", " ; ")
    try:
        lx = shlex.shlex(cmd, posix=True, punctuation_chars=True)
        lx.whitespace_split = True
        toks = list(lx)
    except ValueError:
        toks = cmd.split()
    seg = []
    for t in toks + [";"]:
        if t not in _SEP:
            seg.append(t)
            continue
        w = seg
        seg = []
        while w and (w[0] in _LEADS or re.match(r"^[A-Za-z_]\w*=", w[0])):
            w = w[1:]
        if w and w[0] == "env":                          # env [-i] [-u NAME] [VAR=x] cmd
            w = w[1:]
            while w and (w[0].startswith("-") or "=" in w[0]):
                w = w[2:] if w[0] == "-u" else w[1:]
        if w and w[0] == "timeout":                       # timeout 30 cmd
            w = w[2:]
        if w and w[0] not in _QUIET and w[0] != "HEREDOC":
            yield w


def _tap_sub(w):
    """For tap.py run as w: its subcommand, or "" when w is not tap.py."""
    if re.match(r"^python3?(\.\d+)?$", os.path.basename(w[0])):
        w = [x for x in w[1:] if not x.startswith("-")]
    if w and (w[0].endswith("tap.py") or re.match(r"^\$\{?TAP\}?$", w[0])):
        return w[1] if len(w) > 1 else "help"
    return ""


def _tap_key(x):
    """owner/repo#n (also from an issue URL) or repo#n as typed, else ""."""
    m = _KEY.search(x)
    if m:
        return f"{m[1]}#{m[2]}"
    return x if re.match(r"^[\w.-]+#\d+$", x) else ""


def _gh_key(w):
    """The issue gh issue view|comment names, as owner/repo#n, or ""."""
    rest = [x for x in w[3:] if x]
    repo = next((rest[i + 1] for i, x in enumerate(rest[:-1]) if x in ("-R", "--repo")), "")
    for x in rest:
        m = _KEY.search(x)
        if m:
            return f"{m[1]}#{m[2]}"
        if x.isdigit() and repo:
            return f"{repo}#{x}"
    return ""


def bash_word(cmd, kinds, ctx=None):
    """(word, detail) for a Bash command (pure but for ctx's lookups): its first meaningful simple command decides."""
    for w in _segments(str(cmd or "")):
        a = os.path.basename(w[0])
        b = w[1] if len(w) > 1 else ""
        sub = _tap_sub(w)
        if sub:
            word = TAP_WORDS.get(sub, "Using TapSpace")
            key = next((_tap_key(x) for x in w[2:] if _tap_key(x)), "") if sub in ("tap-read", "tap-say") else ""
            return word, (ctx.tap(key) if ctx and key else "")
        if a in ("bash", "sh", "zsh") and re.match(r"^-\w*c$", b) and len(w) > 2:
            return bash_word(w[2], kinds, ctx)              # bash -c "..." / zsh -lc "..."
        if a in ("bash", "sh", "zsh") and b and not b.startswith("-"):
            w = w[1:]
            a, b = os.path.basename(w[0]), (w[1] if len(w) > 1 else "")
        if a.endswith("muddi.sh"):
            word = MUDDI_WORDS.get(b, "Running a command")
            return word, (ctx.title(w[2]) if ctx and b == "peer" and len(w) > 2 else "")
        if a.endswith("fly.sh"):
            return "Filing a tap", ""
        if a == "gh":
            two = " ".join(w[1:3])
            if two in ("issue view", "issue comment"):
                key = _gh_key(w)
                word = "Checking a tap" if two == "issue view" else "Commenting on a tap"
                return word, (ctx.tap(key) if ctx and key else "")
            if two in ("search issues", "issue list"):
                return "Looking through taps", ""
            if b == "pr":
                return "Checking a PR", ""
            return "Checking GitHub", ""
        if a == "git":
            verbs = [x for x in w[1:] if not x.startswith("-")]
            busy = {"commit", "push", "merge", "rebase", "add", "checkout", "switch", "worktree", "reset", "cherry-pick",
                    "stash", "branch", "tag", "rm", "mv", "restore", "pull"}
            return ("Working on code" if verbs and verbs[0] in busy else "Reading code"), ""
        if a == "curl":
            return ("Checking TapSpace" if any(_LOCAL.search(x) for x in w) else "Looking something up"), ""
        if a in ("cat", "tail", "head") and any(_TASK.search(x) for x in w):
            tid = next(_TASK.search(x)[1] for x in w if _TASK.search(x))
            return _came_in(kinds.get(tid)), ""
        if a in _READS:
            return "Reading files", ""
        if a == "tmux":
            return "Using a terminal", ""
        if a in ("ps", "pgrep", "lsof"):
            return "Checking what's running", ""
        if a in ("xcrun", "sips"):
            return "Looking at screenshots", ""
        if a in ("kill", "pkill", "killall"):
            return "Stopping a process", ""
        return "Running a command", ""
    return "Running a command", ""


def _came_in(kind):
    """What reading a finished background task's output is, by what that task was."""
    return {"listen": "Reading your message", "tap-watch": "Seeing a tap change", "fleet": "Seeing a master move",
            "agent": "Reading a helper's findings", "team": "Reading a team's findings"}.get(kind, "Reading what came in")


def _launch_kind(name, inp):
    """What a tool call that returns at once and tells the session later is (a background Bash, a watcher, an async
    helper, a team), so the notification that wakes the lead and a later read of its output get the right word."""
    if (name == "Bash" and inp.get("run_in_background") is True) or name == "Monitor":
        cmd = str(inp.get("command") or "")
        if re.search(r"(?:tap\.py|\$\{?TAP\}?)\s+listen\b", cmd):
            return "listen"
        if "tap-watch" in cmd:
            return "tap-watch"
        if re.search(r"muddi\.sh\s+fleet\b", cmd):
            return "fleet"
        return "bash" if name == "Bash" else "monitor"
    if name in ("Agent", "Task"):
        return "agent"
    if name == "Workflow":
        return "team"
    return None


def _order_detail(path):
    """A run's name as words for an order written into ~/.../runs/<date>-<run>/orders/: "chat update cards"."""
    parts = pathlib.PurePath(path).parts
    if "orders" in parts and parts.index("orders") > 0:
        run = re.sub(r"^\d{4}-\d{2}-\d{2}-", "", parts[parts.index("orders") - 1])
        return re.sub(r"[-_]+", " ", run).strip()
    return ""


def word_for(name, inp, kinds, ctx=None):
    """(word, detail) for one tool call, or (None, "") for bookkeeping that never changes the word (ToolSearch)."""
    inp = inp if isinstance(inp, dict) else {}
    name = str(name or "")
    if name == "Bash":
        return bash_word(inp.get("command"), kinds, ctx)
    if name == "ToolSearch":
        return None, ""
    if name == "SendMessage":
        return TOOL_WORDS[name], (ctx.peer(str(inp.get("to") or "")) if ctx else "")
    if name in ("Agent", "Task"):
        return TOOL_WORDS[name], links._clip(inp.get("description") or "")
    if name in ("Write", "Edit"):
        p = str(inp.get("file_path") or "")
        if "/orders/" in p:
            return "Writing an order", links._clip(_order_detail(p))
        if "/memory/" in p:
            return "Saving a note", ""
        if os.path.basename(p) == "MUDDI.md":
            return "Updating its rules", ""
        return ("Writing a file" if name == "Write" else "Editing a file"), ""
    if name == "Read":
        m = _TASK.search(str(inp.get("file_path") or ""))
        return (_came_in(kinds.get(m[1])) if m else "Reading files"), ""
    if name in TOOL_WORDS:
        return TOOL_WORDS[name], ""
    if name.startswith("mcp__flightdeck__"):
        m = _KEY.search(json.dumps(inp))
        return FLIGHTDECK_WORDS.get(name.split("__")[-1], "Checking taps"), \
            (ctx.tap(f"{m[1]}#{m[2]}") if ctx and m else "")
    if name.startswith("mcp__meta-ads__"):
        return "Searching ads", ""
    if name.startswith("mcp__"):
        return "Using a connected app", ""
    return "Working", ""


def turn_word(o, kinds):
    """What woke the lead, from the prompt line that opened its turn (turnOrigin, and for a task notification the
    task it is about)."""
    org = o.get("turnOrigin")
    if org in TURN_WORDS:
        return TURN_WORDS[org]
    if org != "task_notification":
        return "Reading what came in"
    c = (o.get("message") or {}).get("content")
    c = c if isinstance(c, str) else json.dumps(c)
    tags = {k: v.strip() for k, v in _TAG.findall(c)}
    kind = kinds.get(tags.get("tool-use-id", "")) or kinds.get(tags.get("task-id", ""))
    said = tags.get("summary", "")
    if kind in ("listen", "tap-watch"):
        return _came_in(kind) if tags.get("status", "completed") == "completed" else "Checking in"   # timed out
    if kind in ("fleet", "agent", "team"):
        return _came_in(kind)
    if said.startswith("Agent "):
        return _came_in("agent")
    if said.startswith("Dynamic workflow "):
        return _came_in("team")
    if "Listen for" in said or "tap.py listen" in c:
        return _came_in("listen")
    return "Reading what came in"


def fresh():
    """A transcript's state before its first line."""
    return {"open": None, "turn_at": None, "origin": "", "pending": {}, "free_at": None, "ended": None,
            "kinds": {}, "launch": {}}


def _remember(d, k, v):
    d[k] = v
    for old in list(d)[:-KINDS_MAX]:
        d.pop(old, None)


def step(st, o, ctx=None):
    """Pure but for ctx's lookups: one parsed transcript line moves st, in file order (never by timestamp: a block's
    line is written when it is complete). A request or an assistant line means a turn is open (the prompt line can
    come right after its request); the prompt with turnOrigin starts one; system/turn_duration ends it."""
    t = o.get("type")
    ts = links._epoch(o.get("timestamp"))
    msg = o.get("message") if isinstance(o.get("message"), dict) else {}
    content = msg.get("content") if isinstance(msg.get("content"), list) else []
    if t == "api-request":
        st["open"] = True
    elif t == "system" and o.get("subtype") == "turn_duration":
        st["open"], st["ended"] = False, ts
    elif t == "user" and o.get("turnOrigin"):
        st.update(open=True, turn_at=ts, origin=turn_word(o, st["kinds"]), pending={}, free_at=None)
    elif t == "user":
        r = o.get("toolUseResult") if isinstance(o.get("toolUseResult"), dict) else {}
        for b in content:
            if not isinstance(b, dict) or b.get("type") != "tool_result":
                continue
            st["open"] = True
            uid = b.get("tool_use_id")
            kind = st["launch"].pop(uid, None)
            task = r.get("backgroundTaskId") or r.get("taskId") or r.get("agentId")
            if kind and task:
                _remember(st["kinds"], str(task), kind)
            if st["pending"].pop(uid, None) is not None and not st["pending"]:
                st["free_at"] = ts
    elif t == "assistant":
        st["open"] = True
        for b in content:
            if not isinstance(b, dict) or b.get("type") != "tool_use" or not b.get("id"):
                continue
            name, inp = b.get("name"), b.get("input") if isinstance(b.get("input"), dict) else {}
            kind = _launch_kind(name, inp)
            if kind:
                _remember(st["launch"], b["id"], kind)
                _remember(st["kinds"], b["id"], kind)        # a notification names the call that armed it
            if name == "Bash" and inp.get("run_in_background") is True:
                continue                                       # it returns at once: never the word
            word, detail = word_for(name, inp, st["kinds"], ctx)
            if word:
                st["pending"][b["id"]] = (word, detail, ts)
    return st


def snapshot(st, listening, waits, now):
    """(state, word, detail, since) for a live session from its transcript state (pure). listening: the lead polled
    listen within LISTEN_FRESH; waits() is true while its newest card waits on the reviewer. since is when the
    transcript says this started, or None."""
    if st["open"]:
        if st["pending"]:
            word, detail, ts = list(st["pending"].values())[-1]     # the newest call still running
            return "working", word, detail, ts
        if st["turn_at"] is not None and now - st["turn_at"] < ORIGIN_HOLD:
            return "working", st["origin"], "", st["turn_at"]
        start = st["turn_at"] + ORIGIN_HOLD if st["turn_at"] is not None else None
        return "working", "Thinking", "", max(x for x in (st["free_at"], start, 0) if x is not None) or None
    if listening:
        return "waiting", ("Waiting on you" if waits() else "Listening"), "", st["ended"]
    return "idle", "Idle", "", st["ended"]


def _alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True             # it runs, under another user
    return True


class Names:
    """The titles a detail may show: masters by NAME (titles(): {NAME: title}), your taps by key (taps: TAPS items),
    and a SendMessage recipient through the session registry and the claims. Every answer goes through links._clip."""

    def __init__(self, titles=None, taps=(), claims=None, claude=None):
        self._titles, self._taps, self._claims, self._claude = titles, taps or (), claims or {}, claude
        self._t = None

    def title(self, name):
        if self._t is None:
            try:
                self._t = dict(self._titles()) if self._titles else {}
            except Exception:
                self._t = {}
        t = self._t.get(name)
        return links._clip(t, self._t) if t and t != "Claude" else ""

    def tap(self, key):
        k = str(key or "").casefold()
        hits = [t for t in self._taps if str(t.get("key", "")).casefold() == k or
                ("/" not in k and str(t.get("key", "")).casefold().endswith("/" + k))]
        return links._clip(hits[0].get("title") or "") if len(hits) == 1 else ""

    def peer(self, to):
        """A SendMessage `to` (a registry name, or uds:/.../<pid>.sock) as that master's title."""
        if not to or self._claude is None:
            return ""
        m = links._SOCK.match(to)
        hits = []
        for f in (self._claude / "sessions").glob("*.json"):
            try:
                d = json.loads(f.read_text())
            except (OSError, ValueError):
                continue
            if isinstance(d, dict) and (d.get("pid") == int(m[1]) if m else d.get("name") == to):
                hits.append(d)
        if len(hits) != 1:
            return ""
        row = self._claims.get(str(hits[0].get("sessionId")))
        return self.title(row.get("name")) if isinstance(row, dict) else ""


class Reader:
    """Reads the lead's live session. One per process (of() below); every call holds its lock, since the watch
    thread and each GET /api/agents ask."""

    def __init__(self, claude_dir=None, clock=time.time, alive=_alive):
        self.claude = pathlib.Path(claude_dir or CLAUDE_DIR)
        self.clock = clock
        self.alive = alive
        self._lock = threading.Lock()
        self.files = {}          # sid -> transcript path (None: not found yet, looked for at miss[sid])
        self.miss = {}
        self.tails = {}          # sid -> {"off": bytes consumed, "st": step state}
        self.claims = {}         # claims path -> (mtime, {sid: row})
        self.procs = {}          # (pid, pid_start) -> (checked at, same process)
        self.last = {}           # NAME -> ((state, word, detail), since)
        self.counts = {"reads": 0, "errors": 0}

    def of(self, name, info, state, claims, proc_start, waits=None, titles=None, taps=()):
        """The lead row's activity: {state, word, detail, since}, or None when this server cannot tell (the NAME never
        claimed a session, its transcript is not on this Mac, or the read failed). info: the NAME's agents.json row;
        state: its served state (_agent_state); claims: data/sessions.json; proc_start(pid): when pid started."""
        with self._lock:
            try:
                got = self._of(name, info or {}, state, pathlib.Path(claims), proc_start, waits or (lambda: False),
                               titles, taps)
            except Exception:
                self.counts["errors"] += 1
                got = None
            if got is None:
                self.last.pop(name, None)
                return None
            now = self.clock()
            st, word, detail, base = got
            prev = self.last.get(name)
            if prev and prev[0] == (st, word, detail):
                since = prev[1]
            else:           # a change the transcript dates (a tool, a turn) keeps its time; anything else is now
                since = base if base and (not prev or base > prev[1]) and base <= now else now
            self.last[name] = ((st, word, detail), since)
            return {"state": st, "word": word, "detail": detail, "since": round(since, 1)}

    def _of(self, name, info, state, claims, proc_start, waits, titles, taps):
        if state == "switching":
            return "switching", "Switching car", "", info.get("switching")
        rows = {sid: r for sid, r in self._claims(claims).items()
                if isinstance(r, dict) and r.get("name") == name and links._SID.match(sid)}
        if not rows:
            return None
        live = [(sid, r) for sid, r in rows.items() if self._live(sid, r, proc_start)]
        if not live:
            return "gone", "Offline", "", max((r.get("seen") or 0 for r in rows.values()), default=0) or None
        sid, _ = max(live, key=lambda x: x[1].get("first") or 0)      # a relay's new car beats the old one
        f = self._transcript(sid)
        if f is None:
            return None
        names = Names(titles, taps, self._claims(claims), self.claude)
        st = self._tail(sid, f, names)
        if st is None:
            return None
        listening = self.clock() - (info.get("listen") or 0) < LISTEN_FRESH
        return snapshot(st, listening, waits, self.clock())

    def _claims(self, path):
        """data/sessions.json's sessions, re-read when it changes (a broken or half-written file keeps the last)."""
        try:
            mtime = path.stat().st_mtime
        except OSError:
            self.claims.pop(path, None)
            return {}
        had = self.claims.get(path)
        if had and had[0] == mtime:
            return had[1]
        try:
            d = json.loads(path.read_text())
            rows = d.get("sessions") if isinstance(d, dict) and isinstance(d.get("sessions"), dict) else {}
        except (OSError, ValueError):
            return had[1] if had else {}
        self.claims[path] = (mtime, rows)
        return rows

    def _live(self, sid, row, proc_start):
        """The claimed session still runs: its registry file is there and names it, its pid is alive, and (checked
        every PROC_EVERY s) that pid is still the process that claimed it, since pids are reused."""
        pid = row.get("pid")
        if type(pid) is not int or pid < 2:
            return False
        try:
            reg = json.loads((self.claude / "sessions" / f"{pid}.json").read_text())
        except (OSError, ValueError):
            return False
        if not isinstance(reg, dict) or str(reg.get("sessionId")) != sid or not self.alive(pid):
            return False
        start = " ".join(str(row.get("pid_start") or "").split())
        if not start:
            return True
        now, had = self.clock(), self.procs.get((pid, start))
        if not had or now - had[0] >= PROC_EVERY:
            had = (now, " ".join(str(proc_start(pid) or "").split()) == start)
            self.procs = {k: v for k, v in self.procs.items() if now - v[0] < 3600}
            self.procs[(pid, start)] = had
        return had[1]

    def _transcript(self, sid):
        f = self.files.get(sid)
        if f is not None and f.exists():
            return f
        now = self.clock()
        if f is None and sid in self.files and now - self.miss.get(sid, 0) < MISS_EVERY:
            return None
        hits = sorted(self.claude.glob(f"projects/*/{sid}.jsonl"))        # top level only, never subagents/
        self.files[sid], self.miss[sid] = (hits[0] if hits else None), now
        if not hits:
            self.tails.pop(sid, None)
        return self.files[sid]

    def _tail(self, sid, f, names):
        """Steps the complete lines appended to f since the last call (links.py's offset read): first sight reads only
        its last TAIL_FIRST bytes and drops the partial first line; a partial last line waits for its newline."""
        try:
            size = f.stat().st_size
        except OSError:
            return None
        t = self.tails.get(sid)
        if t is None or size < t["off"]:                    # first sight, or the file was rewritten
            t = self.tails[sid] = {"off": max(0, size - TAIL_FIRST), "st": fresh(), "skip": size > TAIL_FIRST}
            for k in [k for k in self.tails if k != sid]:   # only the lead's own session is kept
                self.tails.pop(k, None)
        if size - t["off"] > TAIL_MAX:
            t.update(off=size - TAIL_MAX, skip=True)
        if size == t["off"]:
            return t["st"]
        with f.open("rb") as fh:
            fh.seek(t["off"])
            chunk = fh.read(size - t["off"])
        off = t["off"]
        if t.pop("skip", False):
            nl = chunk.find(b"\n") + 1
            chunk, off = chunk[nl:], off + nl
        end = chunk.rfind(b"\n") + 1
        t["off"] = off + end
        self.counts["reads"] += 1
        for raw in chunk[:end].splitlines():
            if not (raw.startswith(_REQUEST) or (not raw.startswith(b'{"type":"api-request-')
                                                 and any(n in raw for n in _NEEDLES))):
                continue
            try:
                step(t["st"], json.loads(raw), names)
            except Exception:
                self.counts["errors"] += 1
        return t["st"]


_ONE = None
_ONE_LOCK = threading.Lock()


def of(name, info, state, claims, proc_start, waits=None, titles=None, taps=()):
    """The lead row's activity from this process's one Reader (made on first use). See Reader.of."""
    global _ONE
    with _ONE_LOCK:
        if _ONE is None:
            _ONE = Reader()
        r = _ONE
    return r.of(name, info, state, claims, proc_start, waits, titles, taps)
