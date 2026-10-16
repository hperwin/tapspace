#!/usr/bin/env python3
"""TapSpace server. A local design-approval space for agent fleets.

Agents post real screenshots (or short videos) of what they just built;
you approve or send change notes from the space page in your browser;
decisions land in per-ticket state AND data/INBOX.jsonl, which your
orchestrator can tail and relay to the owning agent. See README.md and
AGENTS.md for the full protocol.

Chat tab: talk to any number of agents, each in its own thread
(data/chat.jsonl; an agent joins with `tap.py listen --as NAME` and answers
with `tap.py reply --as NAME`), and see your taps: GitHub issues you
authored with a Flightdeck journey label (flightdeck, or the legacy nava), read and
messaged through gh as you. Taps are scoped by issue AUTHOR, never comment
author, so nobody else's taps ever show up here. Each tap carries a phase read
from GitHub alone (proposed, en route, holding, landed, done, closed), the
question it holds on, and its proof images through a token-guarded proxy.

A master can publish its run (tap.py goal / step: a north star, the steps, the current one).
Cards know the master that owns them from the Claude session tap.py runs in; a change note
shows under that master until it posts a new version or its thread is opened. Agent-to-agent
messages show as links (links.py). Spin down (POST /api/agents/stop) ends a master's Claude
session and takes it off the list.

Run:  python3 server.py     (or: python3 tap.py up)
URL:  http://127.0.0.1:4747
"""
import base64
import calendar
import hashlib
import hmac
import html
import json
import os
import secrets
import shutil
import signal
import subprocess
import pathlib
import re
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import quote, urlencode

import links
import qr

# media a version can carry; videos stream with Range support so the space can scrub
_CTYPES = {".png": "image/png", ".mp4": "video/mp4", ".mov": "video/quicktime",
           ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif",
           ".webp": "image/webp", ".heic": "image/heic"}
_CHAT_IMG_EXTS = ("png", "jpg", "jpeg", "gif", "webp", "heic")
_VIDEO_EXTS = ("mp4", "mov")


def _rig(raw):
    """Version stamp of the page code a browser is running. Served pages bake it
    in place of __RIG__; /api/state reports the current one; the page reloads
    itself on drift, so rig upgrades never require a human reload."""
    import hashlib
    return hashlib.sha1(raw).hexdigest()[:12]


def _purge_ticket_videos(t, up_to=None):
    """House rule: decided card videos do not live on the review machine.
    Deletes the ticket's video files for versions <= up_to (all when None) and marks
    each entry video_deleted so the page shows a placeholder, never a broken player.
    Stills are never touched. Returns the number of files deleted."""
    n = 0
    for e in t.get("versions", []):
        if e.get("media") != "video" or e.get("video_deleted"):
            continue
        if up_to is not None and e["v"] > up_to:
            continue
        f = SHOTS / t["id"] / f"{e['v']}.{e.get('ext', 'mp4')}"
        try:
            if f.exists():
                f.unlink()
                n += 1
        except Exception:
            pass
        e["video_deleted"] = True
    return n


def _media_from(p):
    """(media, ext, b64key) for a post/update payload; None when neither is present."""
    if p.get("video_b64"):
        ext = (p.get("video_ext") or "mp4").lower()
        return ("video", ext, "video_b64") if ext in _VIDEO_EXTS else None
    if p.get("image_b64"):
        return ("image", "png", "image_b64")
    return None

ROOT = pathlib.Path(__file__).resolve().parent
SPACE = ROOT / "space"
DATA = pathlib.Path(os.environ.get("TAPSPACE_DATA") or ROOT / "data")
SHOTS = DATA / "shots"
STATE = DATA / "tickets.json"
INBOX = DATA / "INBOX.jsonl"
CHAT = DATA / "chat.jsonl"
MEDIA = DATA / "chat-media"          # images attached in the Chat tab
PORT = int(os.environ.get("TAPSPACE_PORT", "4747"))
VERSION = _rig(pathlib.Path(__file__).read_bytes())

# ---- the phone (Glint): token, route, push ----
# Local callers (tap.py, the page, the Mac app) need nothing. Anything else, which
# includes every request through a tunnel, sends `Authorization: Bearer <data/token>`.
TOKEN_FILE = DATA / "token"
TOKEN = ""                           # read at startup
REMOTE = DATA / "remote.json"        # the phone's route to this Mac, written by tap.py pair
DEVICES = DATA / "devices.json"      # phone push tokens (POST /api/devices)
APNS = pathlib.Path(os.environ.get("TAPSPACE_APNS_DIR") or pathlib.Path.home() / ".config/glint/apns")
OPENSSL = shutil.which("openssl") or "/usr/bin/openssl"


def _token():
    if not TOKEN_FILE.exists():
        fd = os.open(TOKEN_FILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.write(fd, secrets.token_urlsafe(32).encode())
        os.close(fd)
    return TOKEN_FILE.read_text().strip()


def _pair_link():
    """glint://pair?url=...&token=... for the route tap.py pair opened, or None."""
    try:
        return "glint://pair?" + urlencode({"url": json.loads(REMOTE.read_text())["url"],
                                            "token": TOKEN})
    except Exception:
        return None


def _apns():
    """The APNs key config, or None until ~/.config/glint/apns holds AuthKey_*.p8 + key.json."""
    try:
        conf = {"team_id": "289D9TAJCH", "topic": "com.tappinai.glint",
                **json.loads((APNS / "key.json").read_text())}
        conf["p8"] = str(next(APNS.glob("AuthKey_*.p8")))
        return conf if conf.get("key_id") else None
    except Exception:
        return None


def _b64u(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


_JWT = {"kid": "", "ts": 0.0, "v": ""}


def _jwt(c):
    """ES256 provider token, signed by openssl and reused for 50 minutes (APNs allows 60)."""
    if _JWT["kid"] == c["key_id"] and time.time() - _JWT["ts"] < 3000:
        return _JWT["v"]
    head = _b64u(json.dumps({"alg": "ES256", "kid": c["key_id"]}).encode()) + "." + \
        _b64u(json.dumps({"iss": c["team_id"], "iat": int(time.time())}).encode())
    der = subprocess.run([OPENSSL, "dgst", "-sha256", "-sign", c["p8"]], input=head.encode(),
                         capture_output=True, check=True).stdout
    # DER SEQUENCE { INTEGER r, INTEGER s } -> the fixed 64-byte r||s a JWT carries
    r = der[4:4 + der[3]]
    s = der[6 + der[3]:6 + der[3] + der[5 + der[3]]]
    _JWT.update(kid=c["key_id"], ts=time.time(),
                v=head + "." + _b64u(r[-32:].rjust(32, b"\0") + s[-32:].rjust(32, b"\0")))
    return _JWT["v"]


def _push(title, body, kind="replies", **ref):
    """Alerts every registered phone that wants this kind ("replies" or "needs_you");
    ref is {key: thread} or {id: card}, for the tap-through."""
    c = _apns()
    try:
        devs = {t: d for t, d in json.loads(DEVICES.read_text()).items() if d.get(kind, True)}
    except Exception:
        devs = {}
    if not c or not devs:
        return
    payload = json.dumps({"aps": {"alert": {"title": title, "body": (body or "Sent you an image")[:140]},
                                  "sound": "default", "thread-id": next(iter(ref.values()))}, **ref})
    threading.Thread(target=_push_send, args=(c, devs, payload), daemon=True).start()


def _push_send(c, devs, payload):
    dead = []
    with (DATA / "push.log").open("a") as log:
        for tok, d in devs.items():
            host = "api.sandbox.push.apple.com" if d.get("env") == "sandbox" else "api.push.apple.com"
            try:
                r = subprocess.run(["curl", "-s", "--http2", "-w", "\n%{http_code}",
                                    "-H", f"authorization: bearer {_jwt(c)}",
                                    "-H", f"apns-topic: {c['topic']}", "-H", "apns-push-type: alert",
                                    "-d", payload, f"https://{host}/3/device/{tok}"],
                                   capture_output=True, text=True, timeout=20)
                out, _, code = r.stdout.rpartition("\n")
            except Exception as e:
                out, code = str(e), "error"
            log.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {tok[:8]} {code} {out[:200]}\n")
            if code == "410" or "BadDeviceToken" in out:
                dead.append(tok)
    if dead:
        with _lock:
            left = {k: v for k, v in json.loads(DEVICES.read_text()).items() if k not in dead}
            DEVICES.write_text(json.dumps(left, indent=1))


# ---- change cursor: GET /api/wait returns the moment anything a client shows changes ----
# It starts at the boot time in ms, so a cursor from before a restart is never ahead.
_cv = threading.Condition()
KINDS = ("agents", "chat", "taps", "cards")
CURSOR = dict.fromkeys(("n",) + KINDS, int(time.time() * 1000))


def _bump(*kinds):
    with _cv:
        CURSOR["n"] += 1
        CURSOR.update(dict.fromkeys(kinds, CURSOR["n"]))
        _cv.notify_all()


def _wait(since, timeout):
    with _cv:
        fresh = 0 < since <= CURSOR["n"]          # else: first call, or a cursor from another boot
        if fresh:
            _cv.wait_for(lambda: CURSOR["n"] > since, timeout)
        return {"cursor": CURSOR["n"], "changed": [k for k in KINDS if not fresh or CURSOR[k] > since]}

# taps = the reviewer's own agent issues on GitHub (authored by them, a journey label)
# Flightdeck label families (renamed in place October 15, #2734): read either spelling, write the first.
TAP_JOURNEY = tuple(dict.fromkeys(("flightdeck", "nava") + tuple(
    x for x in [os.environ.get("TAPSPACE_TAP_LABEL", "").strip()] if x)))
TAP_FLY = ("flightdeck:fly", "auto-launch")
TAP_NEEDS_REPLY = ("flightdeck:needs-reply", "needs-reply")
TAP_KEEP_CLOSED = 7 * 86400          # a closed tap stays visible this long, then drops
TAP_REFRESH = 45            # seconds between background refreshes
GH = shutil.which("gh") or "/opt/homebrew/bin/gh"

_lock = threading.Lock()

# ---- automatic presence (no agent reports anything) ----
# ACTIVITY is derived from the request stream itself: posts, iterations,
# decision polls. SESSIONS is derived from Claude Code session transcript
# mtimes on disk (~/.claude/projects) — a session only appends while it is
# actually working. Harmless zeros if you don't run Claude Code.
ACTIVITY = {}   # (campaign, agent) -> {verb, detail, ts, kind}
SESSIONS = {"working": 0, "recent": 0, "scanned": 0}


def _touch(campaign, agent, verb, detail="", kind="build"):
    with _lock:
        ACTIVITY[(campaign or "-", agent)] = {
            "verb": verb, "detail": detail[:60], "ts": time.time(), "kind": kind}


def _activity_rows():
    now = time.time()
    rows = []
    with _lock:
        stale = [k for k, v in ACTIVITY.items() if now - v["ts"] > 300]
        for k in stale:
            del ACTIVITY[k]
        for (campaign, agent), v in ACTIVITY.items():
            rows.append({
                "campaign": campaign, "agent": agent, "verb": v["verb"],
                "detail": v["detail"], "kind": v["kind"],
                "age": round(now - v["ts"], 1),
                "state": "active" if now - v["ts"] < 120 else "fading",
            })
    rows.sort(key=lambda r: r["age"])
    return rows


def _scan_sessions():
    base = pathlib.Path.home() / ".claude" / "projects"
    while True:
        working = recent = 0
        try:
            now = time.time()
            for proj in base.iterdir():
                if not proj.is_dir():
                    continue
                for f in proj.iterdir():
                    if f.suffix != ".jsonl" or f.name.startswith("agent-"):
                        continue
                    age = now - f.stat().st_mtime
                    if age < 1800:
                        recent += 1
                        if age < 180:
                            working += 1
        except Exception:
            pass
        SESSIONS.update({"working": working, "recent": recent, "scanned": time.time()})
        time.sleep(5)


# ---- chat with master agents (any number of them) ----
# Every agent that joins gets its own thread. An agent joins by listening or
# replying under a name (tap.py listen/reply --as NAME); the space lists every
# agent seen recently, and the reviewer can talk to each one.
AGENTS = DATA / "agents.json"
_NAME = re.compile(r"^[^\x00-\x1f<>\"]{1,48}$")


def _agents():
    try:
        return json.loads(AGENTS.read_text())
    except Exception:
        return {}


def _agents_write(a):
    """agents.json, atomically. The caller holds _lock."""
    tmp = AGENTS.with_suffix(".tmp")
    tmp.write_text(json.dumps(a, indent=1))
    tmp.replace(AGENTS)


def _agent_touch(name, **extra):
    with _lock:
        a = _agents()
        row = a.get(name) or {"name": name, "first": time.time()}
        row.update(extra)
        row["seen"] = time.time()
        a[name] = row
        _agents_write(a)


def _agent_edit(name, change, seen=True):
    """One joined agent's row, read, changed and written under ONE _lock hold, so two writers never
    interleave. change(row) edits it in place and may raise ValueError, which writes nothing.
    Returns the row, or None when no agent by that name has joined. seen=False for marks the
    reviewer makes: seen is the agent's own activity and decides how long it stays on the rail."""
    with _lock:
        a = _agents()
        row = a.get(name)
        if row is None:
            return None
        change(row)
        if seen:
            row["seen"] = time.time()
        _agents_write(a)
        return row


def _chat_rows(agent=None):
    rows = []
    if CHAT.exists():
        for line in CHAT.read_text().splitlines():
            try:
                r = json.loads(line)
            except Exception:
                continue
            if agent is None or r.get("agent") == agent:
                rows.append(r)
    return rows


def _chat_images(p):
    """Saves a post's attached images ([{b64, ext}], max 10, 20 MB each) under
    data/chat-media and returns their served paths. Raises ValueError on bad input."""
    out = []
    imgs = p.get("images") or []
    if not isinstance(imgs, list) or len(imgs) > 10:
        raise ValueError("up to 10 images")
    for im in imgs:
        served = str((im or {}).get("url") or "")
        if served:
            # already imported by /api/chat/import
            f = (DATA / served.lstrip("/")).resolve()
            if not served.startswith("/chat-media/") or not f.is_file() or \
               not str(f).startswith(str(MEDIA.resolve()) + "/"):
                raise ValueError("unknown image")
            out.append(served)
            continue
        ext = str((im or {}).get("ext", "")).lower().lstrip(".")
        if ext not in _CHAT_IMG_EXTS:
            raise ValueError(f"unsupported image type {ext!r}")
        raw = base64.b64decode(im.get("b64") or "", validate=False)
        if not raw or len(raw) > 20_000_000:
            raise ValueError("empty or too large image")
        MEDIA.mkdir(parents=True, exist_ok=True)
        name = f"{uuid.uuid4().hex[:12]}.{ext}"
        (MEDIA / name).write_bytes(raw)
        out.append("/chat-media/" + name)
    return out


def _chat_import(path):
    """Copies a local image file (a Finder copy or a screenshot drag arrives in the
    browser as a path, not as image data) into data/chat-media; returns its url."""
    from urllib.parse import unquote, urlparse
    path = path.strip()
    if path.startswith("file://"):
        path = unquote(urlparse(path).path)
    f = pathlib.Path(path).expanduser().resolve()
    ext = f.suffix.lower().lstrip(".")
    home = str(pathlib.Path.home().resolve()) + "/"
    if ext not in _CHAT_IMG_EXTS or not str(f).startswith(home) or not f.is_file():
        raise ValueError("not an image file in your home folder")
    if f.stat().st_size > 20_000_000:
        raise ValueError("image too large")
    MEDIA.mkdir(parents=True, exist_ok=True)
    name = f"{uuid.uuid4().hex[:12]}.{'jpg' if ext == 'jpeg' else ext}"
    shutil.copyfile(f, MEDIA / name)
    return "/chat-media/" + name


CHAT_SEQ = DATA / "chat-seq"           # last id ever used, so ids stay unique after a clear


def _chat_add(agent, role, body, images=None, nonce=""):
    with _lock:
        rows = _chat_rows()
        try:
            seq = int(CHAT_SEQ.read_text())
        except Exception:
            seq = 0
        nid = max(seq, rows[-1]["id"] if rows else 0) + 1
        CHAT_SEQ.write_text(str(nid))
        row = {"id": nid, "agent": agent,
               "role": role, "body": body, "ts": time.time()}
        if images:
            row["images"] = images
        if nonce:
            row["nonce"] = nonce
        with CHAT.open("a") as fh:
            fh.write(json.dumps(row) + "\n")
    _bump("chat", "agents")
    return row


def _chat_pending(rows):
    """Reviewer messages after the agent's last reply (rows of ONE agent)."""
    last = max((r["id"] for r in rows if r["role"] == "master"), default=0)
    return [r for r in rows if r["role"] == "you" and r["id"] > last]


def _agent_state(info, rows):
    # a session handing off to its next car (session relay): same thread, same name;
    # the new car clears this the moment it starts listening
    if info.get("switching", 0) > info.get("listen", 0):
        return "switching"
    if info.get("spawned", 0) > info.get("listen", 0):
        if time.time() - info["spawned"] < 180:
            return "starting"   # a New session that has not joined the chat yet
        return "away"           # it never joined; do not say Starting forever
    if _chat_pending(rows):
        return "thinking"
    return "here" if time.time() - info.get("listen", 0) < 30 else "away"


def _title(info):
    """What the person sees: the plain name the agent gave itself for the work
    (tap.py title), else the project it was started in, else just Claude."""
    t = (info.get("title") or "").strip()
    if t:
        return t
    if info.get("spawned") and info.get("where"):
        base = info["where"].rstrip("/").rsplit("/", 1)[-1]
        return base[:1].upper() + base[1:] if base not in ("~", "") else "Claude"
    return "Claude"


def _agent_rows():
    out = []
    allrows = _chat_rows()
    agents = _agents()
    with _lock:
        state = _load()
    notes = _notes_by_owner(state, _owner_index(agents))
    for name, info in agents.items():
        if info.get("stopped"):
            continue            # spun down; a new session under that NAME brings it back
        rows = [r for r in allrows if r.get("agent") == name]
        last = rows[-1]["ts"] if rows else info.get("seen", 0)
        quiet = time.time() - max(last, info.get("seen", 0))
        if quiet > (86400 if rows else 1800):
            continue            # off the rail (an agent you never talked to after 30 min,
                                # any other after a quiet day); history is kept
        said = [r["ts"] for r in rows if r["role"] == "master"]
        read = info.get("notes_read")
        out.append({"name": name, "state": _agent_state(info, rows),
                    "seen": info.get("seen", 0), "first": info.get("first", 0),
                    "where": info.get("where", ""), "last": last, "count": len(rows),
                    "last_master": said[-1] if said else 0,
                    "title": _title(info),
                    "progress": info.get("progress"),
                    "notes": [n for n in notes.get(name, []) if n["ts"] > (read or 0)][:NOTES_MAX],
                    "notes_read": read})
    rank = {"thinking": 0, "switching": 0, "starting": 0, "here": 1, "away": 2}
    out.sort(key=lambda r: -r["last"])
    out.sort(key=lambda r: rank[r["state"]])
    return out


def _agents_watch():
    """Agent state also changes with time (Here goes Away, a new car starts listening),
    so the cursor follows the rendered rows, not only the writes."""
    last = None
    while True:
        try:
            rows = _agent_rows()
            sig = ([(r["name"], r["state"], r["where"], r["title"], (r["progress"] or {}).get("updated"),
                     r["notes_read"], [(n["ticket"], n["version"], n["ts"]) for n in r["notes"]])
                    for r in rows],
                   links.rows({r["name"] for r in rows}))
            if sig != last:
                last = sig
                _bump("agents")
        except Exception:
            pass
        time.sleep(1)


def _chat_clear():
    """Clear all: every thread's history and every agent that is not live moves to
    data/archive/ (nothing is deleted). Live agents stay, with empty threads: listening, starting,
    switching cars, or busy between listens (a Claude session registered to it still runs), so a
    master answering keeps its goal card and its read mark. A spun-down row keeps its stopped mark
    (it is never served), so a leftover listener of the ended session cannot bring it back."""
    running = _running_names()         # runs ps: before _lock
    with _lock:
        arch = DATA / "archive"
        arch.mkdir(exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        rows = _chat_rows()
        if CHAT.exists():
            shutil.copyfile(CHAT, arch / f"chat-{stamp}.jsonl")
            if rows:
                CHAT_SEQ.write_text(str(max(int(CHAT_SEQ.read_text() or 0) if CHAT_SEQ.exists() else 0,
                                            rows[-1]["id"])))
            CHAT.write_text("")
        a = _agents()
        if AGENTS.exists():
            shutil.copyfile(AGENTS, arch / f"agents-{stamp}.json")
        now = time.time()
        keep = {n: i for n, i in a.items()
                if now - i.get("listen", 0) < 30 or i.get("spawned", 0) > i.get("listen", 0)
                or i.get("switching", 0) > i.get("listen", 0) or n in running or i.get("stopped")}
        tmp = AGENTS.with_suffix(".tmp")
        tmp.write_text(json.dumps(keep, indent=1))
        tmp.replace(AGENTS)
    _bump("chat", "agents")
    return len(rows), len(a) - len(keep)


# ---- which master owns a card (agents report nothing) ----
# tap.py sends the Claude session it runs in (X-Claude-Session, X-Claude-Pid), only when that
# Claude process is its own ancestor, so a stale inherited id is never sent. listen, reply, title
# and relay record session -> NAME here; a card posted from that session, or by a lane inside it,
# belongs to that NAME, and a lane speaking under another NAME never moves it (_claim_moves). A relay
# car adds its own session under the same NAME, so cards from either car keep one owner. Cards
# without a provable owner fall back at read time.
CLAIMS = DATA / "sessions.json"     # {"sessions": {sid: {name, pid, pid_start, first, seen, how}},
                                    #  "campaigns": {campaign: [NAME, ...]}}
CLAIM_KEEP = 14 * 86400             # a session nobody spoke for in this long is forgotten
CLAIM_MOVE = 30                     # a claimed session goes to another NAME only after its own NAME has
                                    # not listened this long (the rail's Here window)


def _claims():
    try:
        c = json.loads(CLAIMS.read_text())
    except Exception:
        c = {}
    return {"sessions": c.get("sessions") or {}, "campaigns": c.get("campaigns") or {}}


def _claims_write(c):
    """sessions.json, atomically. The caller holds _lock."""
    tmp = CLAIMS.with_suffix(".tmp")
    tmp.write_text(json.dumps(c, indent=1))
    tmp.replace(CLAIMS)


def _proc_start(pid):
    """When process pid started, or "" when there is none: a recorded pid counts only while the
    same process still holds it (pids are reused)."""
    try:
        return subprocess.run(["ps", "-o", "lstart=", "-p", str(int(pid))], capture_output=True,
                              text=True, timeout=5).stdout.strip()
    except Exception:
        return ""


def _running_names():
    """NAMEs with a registered Claude session whose process still runs: the same process that
    claimed it, since pids are reused. One ps call; never under _lock."""
    rows = [s for s in _claims()["sessions"].values() if s.get("pid") and s.get("pid_start") and s.get("name")]
    if not rows:
        return set()
    try:
        out = subprocess.run(["ps", "-o", "pid=", "-o", "lstart=", "-p", ",".join(sorted({str(int(s["pid"])) for s in rows}))],
                             capture_output=True, text=True, timeout=5).stdout
    except Exception:
        return set()
    starts = {int(f[0]): " ".join(f[1].split()) for f in (line.split(None, 1) for line in out.splitlines())
              if len(f) == 2 and f[0].isdigit()}
    return {s["name"] for s in rows if starts.get(int(s["pid"])) == " ".join(str(s["pid_start"]).split())}


def _claim_moves(cur, name, how, now):
    """Whether a session claimed by cur may now speak as name. An agent inside a master shares the
    master's session, so its listen, reply or title under another NAME must not take that session:
    a claim moves only through the new NAME's listen once cur has not listened for CLAIM_MOVE
    seconds, or through a relay."""
    if not cur or cur == name or how == "relay":
        return True
    return how == "listen" and now - (_agents().get(cur) or {}).get("listen", 0) > CLAIM_MOVE


def _claim(who, name, how):
    """This call's Claude session speaks as NAME: the first claim takes the session, a later one
    only as _claim_moves allows. Listen polls every 2 s, so nothing is written unless the claim is
    new, moved, or 10 minutes old."""
    sid, pid = who.get("session"), who.get("pid") or 0
    if not sid:
        return
    old = _claims()["sessions"].get(sid) or {}
    now = time.time()
    if old.get("name") == name and old.get("pid") == pid and now - old.get("seen", 0) < 600:
        return
    if not _claim_moves(old.get("name"), name, how, now):
        return
    start = old.get("pid_start", "") if old.get("pid") == pid else (_proc_start(pid) if pid else "")
    with _lock:
        c = _claims()
        row = c["sessions"].get(sid) or {"first": now}
        if not _claim_moves(row.get("name"), name, how, now):
            return
        row.update(name=name, pid=pid, pid_start=start, seen=now, how=how)
        c["sessions"][sid] = row
        c["sessions"] = {k: v for k, v in c["sessions"].items() if now - v.get("seen", 0) < CLAIM_KEEP}
        _claims_write(c)


def _match_agent(label, agents):
    """The one agent whose NAME is label, or whose own title is label (any case); None when no
    agent or several match."""
    label = " ".join(str(label or "").split())
    if not label:
        return None
    if label in agents:
        return label
    hits = [n for n, i in agents.items() if (i.get("title") or "").strip().casefold() == label.casefold()]
    return hits[0] if len(hits) == 1 else None


def _owner_index(agents=None):
    """What owner resolution reads, once per request: the joined agents and the claims."""
    return {"agents": _agents() if agents is None else agents, **_claims()}


def _owner_at_write(p, who, ix):
    """(NAME, how) for a card a tap.py call posts or iterates, from what that call proves: an
    explicit owner (a NAME or one agent's title), else its Claude session, else its Claude process
    (the same master after its session id changed). (None, None) otherwise. Runs ps: never under _lock."""
    hit = _match_agent(p.get("owner"), ix["agents"])
    if hit:
        return hit, "explicit"
    s = ix["sessions"].get(who.get("session") or "")
    if s:
        return s["name"], "session"
    pid = who.get("pid")
    mine = sorted((s for s in ix["sessions"].values() if pid and s.get("pid") == pid and s.get("pid_start")),
                  key=lambda s: -s.get("seen", 0))
    if mine and mine[0]["pid_start"] == _proc_start(pid):
        return mine[0]["name"], "pid"
    return None, None


def _owner_of(t, ix):
    """(NAME, how) a card is served with: the owner stored when it was posted, else the session it
    was posted from (claimed later), else the one master its campaign is known for, else the one
    agent whose title or NAME is its label. Reads only; (None, None) when nothing says."""
    if t.get("owner"):
        return t["owner"], t.get("owner_how")
    s = ix["sessions"].get(t.get("session") or "")
    if s:
        return s["name"], "session"
    names = ix["campaigns"].get(t.get("campaign") or "") or []
    if len(names) == 1:
        return names[0], "campaign"
    hit = _match_agent(t.get("agent"), ix["agents"])
    return (hit, "title") if hit else (None, None)


def _learn_campaign(campaign, owner):
    """A card its master provably owns teaches the campaign, so lanes posting from outside that
    master's process still land with it. Two masters in one campaign teach nothing. The caller
    holds _lock."""
    if not campaign or not owner:
        return
    c = _claims()
    names = c["campaigns"].setdefault(campaign, [])
    if owner not in names:
        names.append(owner)
        _claims_write(c)


def _served(t, ix):
    """A ticket as clients get it: with its owner, without the internal session."""
    out = {k: v for k, v in t.items() if k != "session"}
    out["owner"], out["owner_how"] = _owner_of(t, ix)
    return out


# ---- a master's run: its north star, its steps, where it is now (tap.py goal / step) ----
_PLAIN = re.compile(r'^[^\x00-\x1f<>"\u2013\u2014]+$')    # one plain line: no markup, quotes or long dashes


def _bad(msg):
    raise ValueError(msg)


def _plain(s, cap):
    """s with its whitespace folded when that is one plain line of 1 to cap characters, else None."""
    s = " ".join(str(s if s is not None else "").split())
    return s if s and len(s) <= cap and _PLAIN.match(s) else None


def _step_to(v, steps, cur):
    """A step move -> the new 0-based current: next, done, a step's text (any case), or a step
    number counted from 1 as the cards show it."""
    w, n = str(v).strip(), len(steps)
    if w.lower() == "next":
        return min(cur + 1, n)
    if w.lower() == "done":
        return n
    for i, s in enumerate(steps):
        if s.casefold() == w.casefold():
            return i
    if w.isdigit() and 1 <= int(w) <= n:
        return int(w) - 1
    _bad(f"step: next, done, a step's name, or a number from 1 to {n}")


def _progress_change(old, p):
    """The progress after one POST /api/agents/progress body (pure). current is 0-based and equals
    len(steps) when the run is done. New steps start over at the first one; moving to another step
    clears the note unless the same call sets one. ValueError says what to fix."""
    if p.get("clear"):
        return None
    new, was = dict(old or {}), (old or {}).get("current")
    if "goal" in p:
        new["goal"] = _plain(p["goal"], 120) or _bad(
            "goal: one plain line up to 120 characters (no quotes, angle brackets or long dashes)")
    if "steps" in p:
        raw = p["steps"].split("|") if isinstance(p["steps"], str) else p["steps"]
        steps = [_plain(s, 40) for s in (raw if isinstance(raw, list) else [])]
        if not 1 <= len(steps) <= 12 or None in steps or len({s.casefold() for s in steps}) != len(steps):
            _bad("steps: 1 to 12 different plain labels, up to 40 characters each")
        new.update(steps=steps, current=0, note=None)
    if not new.get("goal") or not new.get("steps"):
        _bad('set a goal and steps first: tap.py goal --as NAME "goal" --steps "a|b|c"')
    n = len(new["steps"])
    if "current" in p:
        c = p["current"]
        new["current"] = c if type(c) is int and 0 <= c <= n else _bad(f"current: a number from 0 to {n}")
    if "step" in p:
        new["current"] = _step_to(p["step"], new["steps"], new["current"])
    if new["current"] != was and "note" not in p:
        new["note"] = None
    if "note" in p:
        note = " ".join(str(p["note"] or "").split())
        new["note"] = (_plain(note, 80) or _bad("note: one plain line up to 80 characters")) if note else None
    new["updated"] = time.time()
    return {k: new.get(k) for k in ("goal", "steps", "current", "note", "updated")}


# ---- change notes per master: derived from the cards, never stored twice ----
NOTES_MAX = 8


def _note_words(s, cap=200):
    s = " ".join(str(s or "").split())
    return s if len(s) <= cap else s[:cap].rsplit(" ", 1)[0] + "..."


def _notes_by_owner(state, ix):
    """NAME -> the change notes still open on cards that agent owns, newest first: the card's latest
    decision is a change on its current version (a new version or an approve closes it; archived
    cards are gone). thumb is that version's image path, "" for a video (purged once decided)."""
    out = {}
    for t in state["tickets"]:
        d = (t.get("decisions") or [{}])[-1]
        if t.get("archived") or d.get("action") != "change" or d.get("version") != t.get("version"):
            continue
        owner = _owner_of(t, ix)[0]
        if not owner:
            continue
        e = next((v for v in t.get("versions", []) if v.get("v") == t["version"]), {})
        out.setdefault(owner, []).append({
            "ticket": t["id"], "version": t["version"], "title": t.get("title", ""),
            "note": _note_words(d.get("message")), "ts": d.get("ts", 0),
            "thumb": "" if e.get("media") == "video" else f"/shots/{t['id']}/{t['version']}.{e.get('ext') or 'png'}"})
    for notes in out.values():
        notes.sort(key=lambda n: -n["ts"])
    return out


# ---- spin down a master from the Chat rail (POST /api/agents/stop) ----
# Ends every Claude Code session registered to a NAME in sessions.json: the tmux session it runs in
# (only its pane when that session holds other panes), else SIGTERM and, 5 s later, SIGKILL. It only
# ever signals a process named claude that is still the process the session claimed, never this
# server, a process above it or one below it. The row stays in agents.json marked stopped, so a
# stale heartbeat never brings it back; a listen from a Claude session the stop did not end does.
TMUX = shutil.which("tmux") or "/opt/homebrew/bin/tmux"
STOP_GRACE = 5.0                     # seconds from SIGTERM (or the tmux kill) to SIGKILL
STOP_LINE = "Spun down from TapSpace. Its session has ended."


def _norm(s):
    return " ".join(str(s or "").split())


def _procs():
    """{pid: (ppid, start, comm, stat)} for every process, one ps call; {} when ps fails."""
    try:
        out = subprocess.run(["ps", "-A", "-o", "pid=", "-o", "ppid=", "-o", "stat=", "-o", "lstart=", "-o", "comm="],
                             capture_output=True, text=True, timeout=10).stdout
    except Exception:
        return {}
    procs = {}
    for line in out.splitlines():
        f = line.split(None, 8)          # lstart is five words; comm (a path) may hold spaces
        if len(f) == 9 and f[0].isdigit() and f[1].isdigit():
            procs[int(f[0])] = (int(f[1]), " ".join(f[3:8]), f[8].strip(), f[2])
    return procs


def _tmux_panes():
    """[(session_id, session_name, pane_id, pane_pid)] for every tmux pane; [] without tmux."""
    try:
        out = subprocess.run([TMUX, "list-panes", "-a", "-F",
                              "#{session_id}\t#{session_name}\t#{pane_id}\t#{pane_pid}"],
                             capture_output=True, text=True, timeout=10).stdout
    except Exception:
        return []
    return [(f[0], f[1], f[2], int(f[3])) for f in (line.split("\t") for line in out.splitlines())
            if len(f) == 4 and f[3].isdigit()]


def _kill_pid(pid, sig):
    """Signals one process; False when it is already gone."""
    try:
        os.kill(pid, sig)
        return True
    except ProcessLookupError:
        return False


def _kill_tmux(*args):
    """tmux kill-session -t $id or kill-pane -t %id; True when tmux did it."""
    try:
        return subprocess.run([TMUX, *args], capture_output=True, timeout=10).returncode == 0
    except Exception:
        return False


def _chain(pid, procs):
    """pid and every process above it, nearest first."""
    out = []
    while pid > 1 and pid in procs and pid not in out:
        out.append(pid)
        pid = procs[pid][0]
    return out


def _stop_plan(name):
    """What spinning NAME down would end: (targets, refused). A target is the claude process behind
    one or more of its sessions, with its tmux pane when it runs in one. A session whose process is
    gone (or whose pid another process holds now) is skipped; a live one that is not a claude
    process, or is this server or above or below it, is refused and never signalled."""
    procs, panes, me = _procs(), _tmux_panes(), os.getpid()
    own = set(_chain(me, procs)) | {p for p in procs if me in _chain(p, procs)} | {me}
    found = {}
    for sid, s in _claims()["sessions"].items():
        if s.get("name") == name and s.get("pid"):
            key = (s["pid"], _norm(s.get("pid_start")))
            found.setdefault(key, {"pid": s["pid"], "start": key[1], "sessions": []})["sessions"].append(sid)
    targets, refused = [], []
    for t in found.values():
        p = procs.get(t["pid"])
        if not p or p[3].startswith("Z") or not t["start"] or _norm(p[1]) != t["start"]:
            continue
        hit = next((x for x in panes if x[3] in _chain(t["pid"], procs)), None)
        why = ("not a claude process" if os.path.basename(p[2]) != "claude" else
               "this server's own process tree" if t["pid"] in own or (hit and hit[3] in own) else "")
        if why:
            refused.append({"pid": t["pid"], "why": why, "sessions": t["sessions"]})
            continue
        if hit:
            t["tmux"] = {"id": hit[0], "session": hit[1], "pane": hit[2],
                         "alone": sum(1 for x in panes if x[0] == hit[0]) == 1}
        targets.append(t)
    return targets, refused


def _gone(t, wait):
    """True once target t's process has exited, polling for up to wait seconds."""
    end = time.time() + wait
    while True:
        p = _procs().get(t["pid"])
        if not p or p[3].startswith("Z") or _norm(p[1]) != t["start"]:
            return True
        if time.time() >= end:
            return False
        time.sleep(0.2)


def _stop_record(name, sessions):
    """NAME leaves the list the way Clear all drops an agent that is not live: its thread and its row
    are copied to data/archive/ and the thread leaves chat.jsonl (nothing is deleted). The row stays,
    marked stopped, with the sessions the stop ended. The caller does not hold _lock."""
    with _lock:
        arch = DATA / "archive"
        arch.mkdir(exist_ok=True)
        tag = time.strftime("%Y%m%d-%H%M%S") + "-" + (re.sub(r"[^A-Za-z0-9]+", "-", name).strip("-")[:40] or "agent")
        keep, mine, top = [], [], 0
        for line in (CHAT.read_text().splitlines() if CHAT.exists() else []):
            try:
                r = json.loads(line)
            except Exception:
                keep.append(line)
                continue
            top = max(top, r.get("id") or 0)
            (mine if r.get("agent") == name else keep).append(line)
        (arch / f"chat-{tag}.jsonl").write_text("".join(x + "\n" for x in mine))
        if mine:
            try:
                seq = int(CHAT_SEQ.read_text())
            except Exception:
                seq = 0
            CHAT_SEQ.write_text(str(max(seq, top)))      # ids stay unique after the rows leave
            tmp = CHAT.with_suffix(".tmp")
            tmp.write_text("".join(x + "\n" for x in keep))
            tmp.replace(CHAT)
        a = _agents()
        row = a.get(name) or {"name": name, "first": time.time()}
        (arch / f"agents-{tag}.json").write_text(json.dumps({name: row}, indent=1))
        row.update(stopped=time.time(), stopped_sessions=sessions)
        a[name] = row
        _agents_write(a)


def _stop_master(name):
    """POST /api/agents/stop -> (http code, answer). An unknown NAME, or one with no live Claude session
    to end, is refused and nothing is signalled. Each stop is logged to server.log (stdout)."""
    info = _agents().get(name) if _NAME.match(name) else None
    if not info or info.get("stopped"):
        return 404, {"ok": False, "error": "no agent by that name is in the list"}
    targets, refused = _stop_plan(name)
    stopped, tmux, failed = [], [], []
    for t in targets:
        pane = t.get("tmux")
        if pane and _kill_tmux(*(("kill-session", "-t", pane["id"]) if pane["alone"] else ("kill-pane", "-t", pane["pane"]))):
            tmux.append(pane["session"])
        else:
            _kill_pid(t["pid"], signal.SIGTERM)
        if not _gone(t, STOP_GRACE):
            _kill_pid(t["pid"], signal.SIGKILL)      # _gone just saw that same claude process still running
            if not _gone(t, 2.0):
                failed.append(t["pid"])
                continue
        stopped.append(t["pid"])
    print(time.strftime("%Y-%m-%d %H:%M:%S"), "stop", json.dumps(name, ensure_ascii=False),
          json.dumps({"stopped": stopped, "tmux": tmux, "failed": failed,
                      "refused": [{k: r[k] for k in ("pid", "why")} for r in refused]}), flush=True)
    if not stopped:
        return 409, {"ok": False, "error": "could not end its session" if failed else
                     "no live Claude session to end for this agent",
                     "refused": [r["pid"] for r in refused], "failed": failed}
    # every session of NAME counts as ended except a refused one, which is still running
    alive = {s for r in refused for s in r["sessions"]}
    ended = sorted(s for s, c in _claims()["sessions"].items() if c.get("name") == name and s not in alive)
    _chat_add(name, "system", STOP_LINE)
    _stop_record(name, ended)
    _bump("chat", "agents")
    out = {"ok": True, "stopped": stopped, "tmux": tmux}
    if refused or failed:
        out.update(refused=[r["pid"] for r in refused], failed=failed)
    return 200, out


def _unstop(name, who):
    """A listen from a Claude session the spin-down did not end is a new session under that NAME: the
    agent is back on the list. A stale heartbeat (a leftover listener of an ended session, or a call
    that names no session) changes nothing."""
    sid = who.get("session")
    row = _agents().get(name) or {}
    if not sid or not row.get("stopped") or sid in (row.get("stopped_sessions") or []):
        return
    with _lock:
        a = _agents()
        row = a.get(name)
        if not row or sid in (row.get("stopped_sessions") or []):
            return
        row.pop("stopped", None)
        row.pop("stopped_sessions", None)
        _agents_write(a)
    _bump("agents")


def _projects():
    """Folders a New session can start in: git repos under ~/Developer, newest first."""
    home = pathlib.Path.home()
    seen = []
    for d in [home / "Developer", home / "Developer" / "SnappStats"]:
        if d.is_dir():
            seen += [c for c in d.iterdir() if c.is_dir() and (c / ".git").is_dir()]   # repos, not worktrees
    seen.sort(key=lambda c: -c.stat().st_mtime)
    return [{"path": str(c), "name": c.name, "short": "~/" + str(c.relative_to(home))}
            for c in seen[:40]]


def _spawn_master(folder, name):
    home = pathlib.Path.home().resolve()
    f = pathlib.Path(folder).expanduser().resolve()
    if not f.is_dir() or not (str(f) + "/").startswith(str(home) + "/"):
        raise ValueError("pick a folder in your home folder")
    name = name or f"Claude · {f.name} · {uuid.uuid4().hex[:4]}"
    if not _NAME.match(name):
        raise ValueError("bad name")
    if name in _agents() and _agent_state(_agents()[name], []) in ("here", "starting"):
        raise ValueError("an agent with that name is already running")
    where = "~/" + str(f.relative_to(home)) if f != home else "~"
    _agent_touch(name, spawned=time.time(), where=where)
    log = DATA / "spawn.log"
    with log.open("a") as fh:
        subprocess.Popen(["bash", str(ROOT / "new-master.sh"), str(f), name],
                         stdout=fh, stderr=subprocess.STDOUT, start_new_session=True,
                         env={**os.environ, "TAPSPACE_URL": f"http://127.0.0.1:{PORT}"})
    return name


# ---- taps (the reviewer's own pilot issues, read through gh) ----
TAPS = {"me": "", "items": [], "fetched": 0.0, "error": ""}
_taps_kick = threading.Event()
HOT = {}                    # tap key -> refresh it every few seconds until this time
TAP_HOT_EVERY = 6
VIA = DATA / "tap-via.json"  # comment url -> the agent that sent it for the reviewer


def _via():
    try:
        return json.loads(VIA.read_text())
    except Exception:
        return {}


def _hot(key, minutes):
    HOT[key] = max(HOT.get(key, 0), time.time() + minutes * 60)
    _taps_kick.set()


def _gh(*args, timeout=40):
    r = subprocess.run([GH, *args], capture_output=True, text=True, timeout=timeout)
    if r.returncode:
        raise RuntimeError((r.stderr or r.stdout).strip()[:240])
    return r.stdout


def _me():
    if not TAPS["me"]:
        TAPS["me"] = os.environ.get("TAPSPACE_GH_USER") or \
            _gh("api", "user", "--jq", ".login").strip()
    return TAPS["me"]


_GOAL = re.compile(r"^\s*-\s*\[([ xX])\]\s*(.+?)\s*$")


def _goals(body):
    out = []
    for line in (body or "").splitlines():
        m = _GOAL.match(line)
        if m:
            text = re.sub(r"\*\*(.+?)\*\*", r"\1", m.group(2)).strip()
            out.append({"text": text, "done": m.group(1) != " "})
    return out


def _has(names, family):
    """Any label name in this family (exact, any case): flightdeck:fly:csat is attribution, not fly."""
    fam = {f.casefold() for f in family}
    return any(str(n).casefold() in fam for n in names)


def _epoch(iso):
    """GitHub's UTC stamp (2026-10-15T19:04:05Z) as epoch seconds."""
    return calendar.timegm(time.strptime(iso[:19], "%Y-%m-%dT%H:%M:%S"))


def _too_old(iso):
    return bool(iso) and time.time() - _epoch(iso) > TAP_KEEP_CLOSED


_FENCE = re.compile(r"^\s*(?:```|~~~)")


def _unfenced(body):
    """The body's lines outside ``` / ~~~ fences (the fence lines themselves left out)."""
    out, fenced = [], False
    for line in (body or "").split("\n"):
        if _FENCE.match(line):
            fenced = not fenced
            continue
        if not fenced:
            out.append(line)
    return out


def _live_goals(body):
    """[(text, checked)] the way Flightdeck counts goals (goalsFrom): every - [ ] / - [x] line outside
    a fence; a goal whose whole text is struck through is cancelled and left out."""
    out = []
    for line in _unfenced(body):
        m = _GOAL.match(line)
        if not m:
            continue
        text = re.sub(r"\s*_\([^)]*\)_\s*$", "", m.group(2).replace("**", "")).strip()
        if re.fullmatch(r"~~[\s\S]+~~", text):
            continue
        out.append((" ".join(text.split()), m.group(1) != " "))
    return out


def _gql(q, repo, n):
    """One read-only GraphQL call about issue n of repo; the issue node."""
    owner, name = repo.split("/", 1)
    return json.loads(_gh("api", "graphql", "-f", "query=" + q, "-f", "o=" + owner, "-f", "r=" + name,
                          "-F", f"n={int(n)}"))["data"]["repository"]["issue"]


_CLOSER = {}                 # (repo, n, closedAt) -> "none" | "pr" | "commit", once GitHub says
_Q_CLOSER = ("query($o:String!,$r:String!,$n:Int!){repository(owner:$o,name:$r){issue(number:$n){"
             "timelineItems(itemTypes:[CLOSED_EVENT],last:1){nodes{... on ClosedEvent{closer{__typename}}}}}}}")


def _closer_of(repo, n, closed_at):
    """What closed the issue: "none" (a person or the board), "pr" or "commit" (a merge that said
    closes #n), or None while GitHub cannot say (its timeline lags a fresh close)."""
    k = (repo, int(n), closed_at)
    if k in _CLOSER:
        return _CLOSER[k]
    try:
        nodes = _gql(_Q_CLOSER, repo, n)["timelineItems"]["nodes"]
    except Exception:
        return None
    if not nodes:
        return None
    kind = ((nodes[-1] or {}).get("closer") or {}).get("__typename")
    _CLOSER[k] = {"PullRequest": "pr", "Commit": "commit"}.get(kind, "none")
    return _CLOSER[k]


_TICK = {}                   # (repo, n, sha1 of the body) -> epoch of the last goal change, or None
_Q_EDITS = ("query($o:String!,$r:String!,$n:Int!){repository(owner:$o,name:$r){issue(number:$n){"
            "createdAt userContentEdits(last:20){totalCount nodes{editedAt diff}}}}}")


def _tick_at(repo, n, body):
    """When the body's goals last changed (epoch), from GitHub's saved revisions of it; None when that
    cannot be told. lastEditedAt is no stand-in: the board rewrites the body after landing."""
    k = (repo, int(n), hashlib.sha1((body or "").encode()).hexdigest())
    if k in _TICK:
        return _TICK[k]
    try:
        iss = _gql(_Q_EDITS, repo, n)
        edits = iss["userContentEdits"]
        revs = sorted((e for e in edits["nodes"] or [] if e and e.get("editedAt")), key=lambda e: e["editedAt"])
        cur = _live_goals(body)
        same = lambda e: e.get("diff") is not None and _live_goals(e["diff"]) == cur
        if not revs:
            out = _epoch(iss["createdAt"])              # never edited: the goals are as filed
        elif not same(revs[-1]):
            out = None                                  # the newest saved revision is not this body
        else:
            j = len(revs) - 1
            while j > 0 and same(revs[j - 1]):
                j -= 1
            out = _epoch(revs[j]["editedAt"]) if j > 0 or edits["totalCount"] <= len(revs) else None
    except Exception:
        return None
    _TICK[k] = out
    return out


PHASE_WORD = {"proposed": "Proposed", "enroute": "Flying", "holding": "Needs you", "landed": "Landed",
              "done": "Done", "closed": "Closed"}


def _phase(repo, n, iss, msgs, prev):
    """Hayden's five states plus closed, from GitHub alone (CONTRACT section 2), first match wins."""
    if iss["state"] != "OPEN":
        if str(iss.get("stateReason") or "").upper() != "COMPLETED":
            return "closed"
        by = _closer_of(repo, n, iss.get("closedAt"))
        if by is None:                                  # unknown yet: never claim done unproven
            return prev if prev in ("done", "closed") else "closed"
        return "done" if by == "none" else "closed"
    if _has([l["name"] for l in iss["labels"]], TAP_NEEDS_REPLY):
        return "holding"
    said = [_epoch(m["ts"]) for m in msgs if m["who"] == "tap"]
    goals = _live_goals(iss["body"])
    if goals and all(done for _, done in goals):
        tick = _tick_at(repo, n, iss["body"])
        if tick is None or (said and max(said) > tick):
            return "landed"
    return "enroute" if said else "proposed"


def _undash(s):
    """Long dashes out of what clients show (house rule): a spaced one reads as a comma."""
    return re.sub(r"[—–]", "-", re.sub(r"\s+[—–]\s+", ", ", s))


def _readable(md, me, cap=600):
    """A comment's markdown as plain readable text, up to cap characters, without its leading @me."""
    s = re.sub(r"<!--[\s\S]*?-->", "", md or "")
    s = "\n".join(line for line in s.split("\n") if not _FENCE.match(line))
    s = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", s)
    s = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", s)
    s = re.sub(r"<(https?://[^>\s]+)>", r"\1", s)
    s = re.sub(r"<br\s*/?>", "\n", s, flags=re.I)
    s = re.sub(r"</?[A-Za-z][^>]*>", "", s)
    s = re.sub(r"(?m)^[ \t]*#{1,6}[ \t]+", "", s)
    s = re.sub(r"(?m)^[ \t]*(?:>[ \t]?)+", "", s)
    s = re.sub(r"(?m)^([ \t]*)[-*+][ \t]+\[[ xX]\][ \t]+", r"\1- ", s)
    for pat in (r"\*\*(.+?)\*\*", r"(?<!\w)__(.+?)__(?!\w)", r"~~(.+?)~~",
                r"(?<![\w*])\*(?![\s*])(.+?)(?<![\s*])\*(?![\w*])",
                r"(?<![\w_])_(?![\s_])(.+?)(?<![\s_])_(?![\w_])"):
        s = re.sub(pat, r"\1", s)
    s = _undash(html.unescape(s.replace("`", "")))
    s = re.sub(rf"^\s*@{re.escape(me)}(?![\w-])[\s,:;.!\-]*", "", s, flags=re.I)
    s = "\n".join(line for line in (" ".join(x.split()) for x in s.split("\n")) if line)
    if len(s) > cap:
        s = s[:cap - 3].rsplit(" ", 1)[0].rstrip() + "..."
    return s


def _ask(iss, me):
    """The question a holding tap waits on: the newest comment not by me that mentions @me."""
    hit = re.compile(rf"(?<![\w/-])@{re.escape(me)}(?![\w-])", re.I)
    for c in reversed(iss["comments"]):
        if c["author"]["login"].casefold() != me.casefold() and hit.search(c["body"] or ""):
            return {"text": _readable(c["body"], me), "url": c["url"], "at": c["createdAt"]}
    return None


# ---- proof: the images a pilot put in the issue body, through this server ----
# GitHub serves them only to a signed-in viewer (user-attachments) or by a short-lived signed url from the
# issue's body_html, so no client can load them directly. Clients get /api/taps/proof?key=&i=&v= urls; the
# server fetches each image once and keeps its bytes under data/proof/, named by the attachment id.
PROOF = DATA / "proof"
_PROOF_SRC = {}              # tap key -> ([(src, index among all body images)], how many images): never served
_PROOF_HTML = {}             # tap key -> (fetched at, [(src, data-canonical-src)]) of its body_html
_PROOF_LOCKS = {}            # tap key -> Lock, so a row of thumbnails makes one body_html call
_PROOF_TYPES = {"image/png": ".png", "image/jpeg": ".jpg", "image/gif": ".gif", "image/webp": ".webp"}
_PROOF_HOSTS = {"private-user-images.githubusercontent.com", "user-images.githubusercontent.com",
                "camo.githubusercontent.com", "raw.githubusercontent.com"}
_PROOF_MAX = 25_000_000
_ASSET = re.compile(r"user-attachments/(?:assets|files)/([0-9A-Fa-f-]{36})")
_IMG = re.compile(r"!\[([^\]]*)\]\(\s*<?([^)\s>]+)>?(?:\s+(?:\"[^\"]*\"|'[^']*'))?\s*\)|<img\b[^>]*>", re.I)
_ATTR = re.compile(r"([\w-]+)\s*=\s*(?:\"([^\"]*)\"|'([^']*)'|([^\s>]+))")


def _attrs(tag):
    return {a.group(1).lower(): html.unescape(next(g for g in a.groups()[1:] if g is not None))
            for a in _ATTR.finditer(tag)}


def _github_image(url):
    """Only GitHub's own image hosts, or an attachment on github.com itself: never an open proxy."""
    from urllib.parse import urlsplit
    p = urlsplit(url)
    return p.scheme == "https" and (p.hostname in _PROOF_HOSTS or
                                    (p.hostname == "github.com" and p.path.startswith("/user-attachments/")))


def _proof(key, body):
    """[{url, alt}] for the GitHub images in the body (markdown or <img>, outside comments and fences),
    in order. v is a short hash of the image address, so a client cache never shows a replaced one."""
    found = []
    for m in _IMG.finditer("\n".join(_unfenced(re.sub(r"<!--[\s\S]*?-->", "", body or "")))):
        if m.group(0).startswith("!"):
            found.append((m.group(1), m.group(2)))
        else:
            a = _attrs(m.group(0))
            found.append((a.get("alt", ""), a.get("src", "")))
    keep = [(n, alt, src) for n, (alt, src) in enumerate(found) if _github_image(src)]
    _PROOF_SRC[key] = ([(src, n) for n, _, src in keep], len(found))
    return [{"url": f"/api/taps/proof?key={quote(key, safe='')}&i={i}&v={hashlib.sha1(src.encode()).hexdigest()[:8]}",
             "alt": _undash(" ".join(alt.split()))} for i, (_, alt, src) in enumerate(keep)]


def _proof_hit(cid):
    for f in PROOF.glob(cid + ".*"):
        if f.suffix in _PROOF_TYPES.values() and f.is_file():
            return f.read_bytes(), _CTYPES[f.suffix]
    return None


def _proof_signed(key, src, n, total, fresh=False):
    """The address GitHub serves image n of the body from right now: the matching <img> of body_html,
    read once per key and reused inside the signed urls' five minutes."""
    repo, num = key.split("#")
    got = _PROOF_HTML.get(key)
    if fresh or not got or time.time() - got[0] > 240:
        raw = _gh("api", f"repos/{repo}/issues/{num}", "-H", "Accept: application/vnd.github.full+json",
                  "--jq", ".body_html")
        got = _PROOF_HTML[key] = (time.time(), [(a.get("src", ""), a.get("data-canonical-src", ""))
                                                for a in map(_attrs, re.findall(r"<img\b[^>]*>", raw, re.I))])
    tags, asset = got[1], _ASSET.search(src)
    pick = next((s for s, _ in tags if asset and asset.group(1).lower() in s.lower()), None) or \
        next((s for s, c in tags if c == src), None) or next((s for s, _ in tags if s == src), None) or \
        (tags[n][0] if len(tags) == total else None)
    if not pick:
        raise RuntimeError("image not found in the issue")
    return pick


def _proof_fetch(url):
    """(bytes, content type) of one GitHub image. The gh token goes only to github.com itself, as a
    header never forwarded on a redirect; a signed url needs none."""
    import urllib.request
    from urllib.parse import urlsplit
    if not _github_image(url):
        raise RuntimeError("not a GitHub image")
    req = urllib.request.Request(url, headers={"User-Agent": "TapSpace"})
    if urlsplit(url).hostname == "github.com":
        if not _TOKEN["v"]:
            _TOKEN["v"] = _gh("auth", "token").strip()
        req.add_unredirected_header("Authorization", "Bearer " + _TOKEN["v"])
    with urllib.request.urlopen(req, timeout=20) as r:
        ctype, raw = r.headers.get_content_type(), r.read(_PROOF_MAX + 1)
    if len(raw) > _PROOF_MAX:
        raise RuntimeError("image over 25 MB")
    if ctype not in _PROOF_TYPES:
        raise RuntimeError("not an image")
    return raw, ctype


def _proof_image(key, i):
    """(bytes, content type, src) of proof image i of one of the taps served now: from data/proof/,
    else fetched once and kept. LookupError when there is no such image, else any failure raises."""
    if not any(t["key"] == key for t in TAPS["items"]):
        raise LookupError("no such tap")
    srcs, total = _PROOF_SRC.get(key) or ([], 0)
    if not 0 <= i < len(srcs):
        raise LookupError("no such image")
    src, n = srcs[i]
    asset = _ASSET.search(src)
    cid = asset.group(1).lower() if asset else hashlib.sha1(src.encode()).hexdigest()[:24]
    with _PROOF_LOCKS.setdefault(key, threading.Lock()):
        hit = _proof_hit(cid)
        if hit:
            return hit + (src,)
        try:
            raw, ctype = _proof_fetch(_proof_signed(key, src, n, total))
        except Exception:
            raw, ctype = _proof_fetch(_proof_signed(key, src, n, total, fresh=True))   # a signed url ran out
        PROOF.mkdir(parents=True, exist_ok=True)
        tmp = PROOF / f".tmp-{cid}-{uuid.uuid4().hex[:8]}"
        tmp.write_bytes(raw)
        tmp.replace(PROOF / (cid + _PROOF_TYPES[ctype]))
        return raw, ctype, src


def _issue(repo, number):
    return json.loads(_gh("issue", "view", str(number), "--repo", repo, "--json",
                          "author,title,body,labels,state,comments,url,createdAt,closedAt,stateReason"))


def _is_mine(iss):
    return iss["author"]["login"] == _me() and \
        _has([l["name"] for l in iss["labels"]], TAP_JOURNEY)


def _tap(repo, number, iss):
    me = _me()
    via = _via()
    labels = [l["name"] for l in iss["labels"]]
    msgs = []
    for c in iss["comments"]:
        mine = c["author"]["login"] == me
        seen = any(g["content"] == "EYES" for g in c.get("reactionGroups") or [])
        msgs.append({"who": "you" if mine else "tap", "body": c["body"],
                     "ts": c["createdAt"], "seen": seen, "url": c["url"],
                     "via": via.get(c["url"], "")})
    tap_msgs = [m for m in msgs if m["who"] == "tap"]
    # legacy status, kept for old clients (landed here means closed)
    if iss["state"] != "OPEN":
        status = "landed"
    elif _has(labels, TAP_NEEDS_REPLY):
        status = "needs-you"
    elif tap_msgs:
        status = "flying"
    else:
        status = "boarding"
    key = f"{repo}#{number}"
    prev = next((t.get("phase") for t in TAPS["items"] if t["key"] == key), None)
    phase = _phase(repo, number, iss, msgs, prev)
    return {"key": key, "repo": repo, "number": number,
            "title": iss["title"], "url": iss["url"], "status": status,
            "goals": _goals(iss["body"]), "messages": msgs,
            # the tap has your latest message (👀) and has not answered yet
            "replying": iss["state"] == "OPEN" and bool(msgs) and msgs[-1]["who"] == "you"
                        and msgs[-1]["seen"],
            "created": iss["createdAt"], "closed": iss.get("closedAt"),
            "last": msgs[-1]["ts"] if msgs else iss["createdAt"],
            "phase": phase, "phase_word": PHASE_WORD[phase],
            "ask": _ask(iss, me) if phase == "holding" else None,
            "fly_requested": _has(labels, TAP_FLY),
            "proof": _proof(key, iss["body"])}


_ORDER = {"needs-you": 0, "flying": 1, "boarding": 2, "landed": 3}


def _publish(items):
    items.sort(key=lambda t: t["last"], reverse=True)       # newest first...
    items.sort(key=lambda t: _ORDER[t["status"]])          # ...within each status
    old, known = TAPS["items"], TAPS["fetched"]
    TAPS.update({"items": items, "fetched": time.time(), "error": ""})
    if items == old:
        return
    _bump("taps")
    before = {t["key"]: len(t["messages"]) for t in old}
    for t in items if known else []:
        said = [m for m in t["messages"][before.get(t["key"], len(t["messages"])):] if m["who"] == "tap"]
        if said:
            _push(t["title"], said[-1]["body"], key=t["key"])


def _refresh_taps():
    me = _me()
    # label:a,b is GitHub's OR; repeating --label would AND them. Newest first, so the cap never
    # crowds out a live tap behind old closed ones.
    found = json.loads(_gh("search", "issues", "label:" + ",".join(TAP_JOURNEY), "--author", me,
                           "--limit", "60", "--sort", "updated",
                           "--json", "repository,number,state,closedAt"))
    items = []
    for f in found:
        if f["state"] != "open" and _too_old(f.get("closedAt")):
            continue
        repo = f["repository"]["nameWithOwner"]
        iss = _issue(repo, f["number"])
        if _is_mine(iss):
            items.append(_tap(repo, f["number"], iss))
    _publish(items)


def _refresh_hot():
    """Re-reads only the taps someone is talking to right now (one gh call each)."""
    now = time.time()
    for k in [k for k, until in HOT.items() if until < now]:
        HOT.pop(k, None)
    if not HOT:
        return
    items = list(TAPS["items"])
    for key in list(HOT):
        repo, num = key.split("#")
        iss = _issue(repo, num)
        if not _is_mine(iss) or (iss["state"] != "OPEN" and _too_old(iss.get("closedAt"))):
            continue
        fresh = _tap(repo, int(num), iss)
        items = [fresh if t["key"] == key else t for t in items]
        if not any(t["key"] == key for t in items):
            items.append(fresh)
    _publish(items)


# ---- near real-time: GitHub conditional requests ----
# GitHub has no push to localhost (webhooks need a public URL and repo admin),
# but a conditional GET with If-None-Match that comes back 304 costs no rate
# limit. So every open tap's comments + labels are checked every few seconds
# that way, and only a real change triggers the full gh read.
_ETAGS = {}
_TOKEN = {"v": ""}


def _changed(path):
    import urllib.request, urllib.error
    if not _TOKEN["v"]:
        _TOKEN["v"] = _gh("auth", "token").strip()
    req = urllib.request.Request("https://api.github.com" + path, headers={
        "Authorization": "Bearer " + _TOKEN["v"], "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        **({"If-None-Match": _ETAGS[path]} if path in _ETAGS else {})})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            first = path not in _ETAGS
            _ETAGS[path] = r.headers.get("ETag", "")
            return not first
    except urllib.error.HTTPError as e:
        if e.code == 304:
            return False
        if e.code == 401:
            _TOKEN["v"] = ""
        raise


def _watch_loop():
    while True:
        for t in list(TAPS["items"]):
            if t["status"] == "landed":
                continue
            repo, num = t["key"].split("#")
            try:
                # comments carry their reaction rollup, so a 👀 also changes the ETag
                if _changed(f"/repos/{repo}/issues/{num}/comments?per_page=100") | \
                   _changed(f"/repos/{repo}/issues/{num}"):
                    _hot(t["key"], 0.15)
            except Exception:
                pass
        time.sleep(3)


def _taps_loop():
    last_full = 0.0
    while True:
        try:
            if time.time() - last_full >= TAP_REFRESH:
                _refresh_taps()
                last_full = time.time()
            else:
                _refresh_hot()
        except Exception as e:
            TAPS["error"] = str(e)
        _taps_kick.wait(TAP_HOT_EVERY if HOT else TAP_REFRESH - (time.time() - last_full))
        _taps_kick.clear()


def _load():
    if STATE.exists():
        try:
            return json.loads(STATE.read_text())
        except Exception:
            pass
    return {"tickets": []}


def _save(state):
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=1))
    tmp.replace(STATE)
    _bump("cards")


def _find(state, tid):
    for t in state["tickets"]:
        if t["id"] == tid:
            return t
    return None


def _inbox(event):
    event["received_epoch"] = time.time()
    with INBOX.open("a") as fh:
        fh.write(json.dumps(event) + "\n")


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("content-type", "application/json")
        self.send_header("cache-control", "no-store")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _bytes(self, body, ctype):
        self.send_response(200)
        self.send_header("content-type", ctype)
        self.send_header("cache-control", "no-store")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _q(self, key):
        from urllib.parse import parse_qs, urlsplit
        return (parse_qs(urlsplit(self.path).query).get(key) or [""])[0].strip()

    def _local(self):
        """On this Mac: a loopback client with no proxy header. Tunnels (Tailscale Serve,
        ngrok) also connect from loopback, and they always add one of these headers."""
        return (self.client_address[0].startswith("127.") or self.client_address[0] == "::1") and \
            not any(k.lower() in ("forwarded", "x-real-ip", "tailscale-user-login") or
                    k.lower().startswith(("x-forwarded-", "ngrok-")) for k in self.headers.keys())

    def _allowed(self):
        if self._local() or (TOKEN and hmac.compare_digest(
                (self.headers.get("Authorization") or "").encode(), f"Bearer {TOKEN}".encode())):
            return True
        self._json({"error": "unauthorized"}, 401)
        return False

    def _who(self):
        """The Claude session behind a tap.py call: X-Claude-Session and X-Claude-Pid, which tap.py
        sends only when that Claude process is its own ancestor. Trusted from this Mac only."""
        if not self._local():
            return {}
        sid = (self.headers.get("X-Claude-Session") or "").strip()
        pid = (self.headers.get("X-Claude-Pid") or "").strip()
        return {"session": sid if re.fullmatch(r"[0-9A-Fa-f-]{8,64}", sid) else "",
                "pid": int(pid) if re.fullmatch(r"[0-9]{1,7}", pid) else 0}

    def do_GET(self):
        if not self._allowed():
            return
        path = self.path.split("?")[0]
        if path in ("/", "/index.html"):
            raw = (SPACE / "index.html").read_bytes()
            self._bytes(raw.replace(b"__RIG__", _rig(raw).encode()),
                        "text/html; charset=utf-8")
            return
        if path == "/favicon.ico":
            self._bytes(base64.b64decode(
                "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk"
                "YPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="), "image/png")
            return
        if path == "/anime.esm.js":
            self._bytes((SPACE / "anime.esm.js").read_bytes(),
                        "text/javascript; charset=utf-8")
            return
        if path == "/api/activity":
            self._json({"rows": _activity_rows(), "sessions": dict(SESSIONS),
                        "now": time.time(), "links": links.stats()})
            return
        if path == "/api/agents":
            rows = _agent_rows()
            self._json({"agents": rows, "links": links.rows({r["name"] for r in rows})})
            return
        if path == "/api/projects":
            self._json({"projects": _projects()})
            return
        if path == "/api/chat":
            after = int(self._q("after")) if self._q("after").isdigit() else 0
            self._json({"messages": [r for r in _chat_rows(self._q("agent")) if r["id"] > after]})
            return
        if path == "/api/wait":
            try:
                since = int(self._q("since") or 0)
                timeout = min(30.0, max(0.0, float(self._q("timeout") or 25)))
            except ValueError:
                self._json({"error": "since and timeout are numbers"}, 400)
                return
            self._json(_wait(since, timeout))
            return
        if path == "/api/health":
            self._json({"ok": True, "version": VERSION, "push": bool(_apns()), "time": time.time()})
            return
        if path == "/pair":
            if not self._local():
                self._json({"error": "pairing is only shown on this Mac"}, 403)
                return
            # open (or reuse) the phone route first, so this page is all Hayden needs
            subprocess.run(["python3", str(ROOT / "tap.py"), "pair"], capture_output=True, timeout=45,
                           env={**os.environ, "TAPSPACE_URL": f"http://127.0.0.1:{PORT}"})
            link = _pair_link()
            body = (f"<p>In Glint, tap Scan code and point it here.</p>{qr.svg(link)}"
                    f"<p><code>{html.escape(link)}</code></p>" if link else
                    "<p>Couldn't open a route to your phone. Check that ngrok or Tailscale is set up, "
                    "then reload this page.</p>")
            self._bytes(("<!doctype html><meta name=viewport content='width=device-width'>"
                         "<title>Pair Glint</title><style>body{font:16px -apple-system,system-ui;"
                         "max-width:420px;margin:40px auto;padding:0 16px}svg{width:100%;height:auto}"
                         "code{word-break:break-all;font-size:12px}</style><h1>Pair Glint</h1>" +
                         body).encode(), "text/html; charset=utf-8")
            return
        if path == "/api/chat/listen":
            # an agent's poll: joins the space under its name, gets its unanswered messages
            name = self._q("agent")
            if not _NAME.match(name):
                self._json({"error": "bad agent name"}, 400)
                return
            _unstop(name, self._who())
            _agent_touch(name, listen=time.time(), where=self._q("where")[:80])
            _claim(self._who(), name, "listen")
            pend = _chat_pending(_chat_rows(name))
            for r in pend:
                r["files"] = [str(DATA / u.lstrip("/")) for u in r.get("images", [])]
            self._json({"pending": pend})
            return
        if path == "/api/taps":
            self._json({k: TAPS[k] for k in ("me", "items", "fetched", "error")})
            return
        if path == "/api/taps/proof":
            # one proof image of one of your taps, as its proof[].url names it (local or bearer, like /api/taps)
            key, i = self._q("key"), self._q("i")
            if not re.match(r"^[\w.-]+/[\w.-]+#\d+$", key) or not re.fullmatch(r"[0-9]{1,4}", i):
                self._json({"error": "bad proof address"}, 400)
                return
            try:
                raw, ctype, src = _proof_image(key, int(i))
            except LookupError as e:
                self._json({"error": str(e)}, 404)
                return
            except Exception as e:
                self._json({"error": str(e)[:240]}, 502)
                return
            self.send_response(200)
            self.send_header("content-type", ctype)
            self.send_header("content-length", str(len(raw)))
            self.send_header("x-content-type-options", "nosniff")
            self.send_header("cache-control", "private, max-age=31536000, immutable"
                             if self._q("v") == hashlib.sha1(src.encode()).hexdigest()[:8] else "no-store")
            self.end_headers()
            self.wfile.write(raw)
            return
        if path == "/api/state":
            ix = _owner_index()
            with _lock:
                state = _load()
            live = [_served(t, ix) for t in state["tickets"] if not t.get("archived")]
            self._json({"tickets": live, "now": time.time(),
                        "rig": _rig((SPACE / "index.html").read_bytes())})
            return
        if path.startswith("/api/ticket/"):
            tid = path.rsplit("/", 1)[1]
            ix = _owner_index()
            with _lock:
                t = _find(_load(), tid)
            if t and not t.get("archived"):
                if t["status"] == "waiting":
                    _touch(t.get("campaign"), t["agent"], "waiting on your tap",
                           t["title"], "waiting")
                else:
                    _touch(t.get("campaign"), t["agent"], "checking in", t["title"])
            self._json(_served(t, ix) if t else {"error": "not found"}, 200 if t else 404)
            return
        if path.startswith("/shots/") or path.startswith("/chat-media/"):
            f = (DATA / path.lstrip("/")).resolve()
            root = SHOTS if path.startswith("/shots/") else MEDIA
            if not str(f).startswith(str(root.resolve()) + "/") or not f.is_file():
                self.send_error(404)
                return
            ctype = _CTYPES.get(f.suffix.lower(), "application/octet-stream")
            size = f.stat().st_size
            m = re.match(r"bytes=(\d*)-(\d*)$", self.headers.get("Range") or "")
            if m and (m.group(1) or m.group(2)):
                # single-range partial content — what <video> needs to scrub
                if m.group(1):
                    start = int(m.group(1))
                    end = min(int(m.group(2)), size - 1) if m.group(2) else size - 1
                else:
                    start, end = max(0, size - int(m.group(2))), size - 1
                if start >= size or start > end:
                    self.send_response(416)
                    self.send_header("content-range", f"bytes */{size}")
                    self.send_header("content-length", "0")
                    self.end_headers()
                    return
                with f.open("rb") as fh:
                    fh.seek(start)
                    body = fh.read(end - start + 1)
                self.send_response(206)
                self.send_header("content-type", ctype)
                self.send_header("accept-ranges", "bytes")
                self.send_header("content-range", f"bytes {start}-{end}/{size}")
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            self.send_response(200)
            self.send_header("content-type", ctype)
            self.send_header("accept-ranges", "bytes")
            self.send_header("cache-control", "no-store")
            self.send_header("content-length", str(size))
            self.end_headers()
            self.wfile.write(f.read_bytes())
            return
        self.send_error(404)

    def do_POST(self):
        if not self._allowed():
            return
        try:
            n = int(self.headers.get("content-length", 0))
            p = json.loads(self.rfile.read(n).decode())
        except Exception:
            self._json({"error": "bad json"}, 400)
            return

        if self.path in ("/api/chat", "/api/chat/reply", "/api/taps/say", "/api/chat/import",
                         "/api/agents/new", "/api/chat/clear", "/api/devices",
                         "/api/agents/progress", "/api/agents/read", "/api/agents/stop"):
            # these speak for the reviewer or write agent state: same-origin JSON only, so no
            # other page can drive them through the browser
            origin = self.headers.get("Origin")
            if (self.headers.get("content-type") or "").split(";")[0] != "application/json" or \
               (origin and origin not in (f"http://127.0.0.1:{PORT}", f"http://localhost:{PORT}")):
                self._json({"error": "forbidden"}, 403)
                return
            if self.path == "/api/agents/new":
                try:
                    name = _spawn_master(str(p.get("folder") or ""), str(p.get("name") or "").strip())
                except Exception as e:
                    self._json({"error": str(e)}, 400)
                    return
                self._json({"ok": True, "agent": name})
                return
            if self.path == "/api/chat/clear":
                msgs, agents = _chat_clear()
                self._json({"ok": True, "archived_messages": msgs, "archived_agents": agents})
                return
            if self.path == "/api/devices":
                tok = str(p.get("token") or "").strip()
                if not re.match(r"^[0-9a-fA-F]{64,200}$", tok):
                    self._json({"error": "bad device token"}, 400)
                    return
                with _lock:
                    try:
                        devs = json.loads(DEVICES.read_text())
                    except Exception:
                        devs = {}
                    devs[tok] = {"env": "sandbox" if p.get("env") == "sandbox" else "production",
                                 "replies": p.get("replies") is not False,
                                 "needs_you": p.get("needs_you") is not False,
                                 "ts": time.time()}
                    DEVICES.write_text(json.dumps(devs, indent=1))
                self._json({"ok": True, "push": bool(_apns())})
                return
            if self.path == "/api/chat/import":
                if not self._local():       # it reads files on this Mac
                    self._json({"error": "only from this Mac"}, 403)
                    return
                try:
                    self._json({"ok": True, "url": _chat_import(str(p.get("path") or ""))})
                except Exception as e:
                    self._json({"error": str(e)}, 400)
                return
            if self.path == "/api/agents/progress":
                # a master publishes its run: the north star, the steps, where it is now (local only)
                if not self._local():
                    self._json({"error": "only from this Mac"}, 403)
                    return
                name = str(p.get("name") or p.get("agent") or "").strip()
                out = {}

                def change(row):
                    out["progress"] = _progress_change(row.get("progress"), p)
                    if out["progress"] is None:
                        row.pop("progress", None)
                    else:
                        row["progress"] = out["progress"]
                try:
                    row = _agent_edit(name, change)
                except ValueError as e:
                    self._json({"error": str(e)}, 400)
                    return
                if row is None:
                    self._json({"error": "no agent by that name has joined"}, 404)
                    return
                _bump("agents")
                self._json({"ok": True, "progress": out["progress"]})
                return
            if self.path == "/api/agents/read":
                # the reviewer opened this agent's thread: its change notes read as seen on every client
                name = str(p.get("name") or p.get("agent") or "").strip()
                now = time.time()
                if _agent_edit(name, lambda row: row.update(notes_read=now), seen=False) is None:
                    self._json({"error": "no agent by that name has joined"}, 404)
                    return
                _bump("agents")
                self._json({"ok": True, "notes_read": now})
                return
            if self.path == "/api/agents/stop":
                # the reviewer spins a master down: its Claude session ends and it leaves the list
                code, out = _stop_master(str(p.get("name") or p.get("agent") or "").strip())
                self._json(out, code)
                return
            body = (p.get("body") or "").strip()
            chatting = self.path in ("/api/chat", "/api/chat/reply")
            if not body and not (chatting and p.get("images")):
                self._json({"error": "empty"}, 400)
                return
            if chatting:
                name = (p.get("agent") or "").strip()
                if not _NAME.match(name):
                    self._json({"error": "bad agent name"}, 400)
                    return
                if self.path == "/api/chat" and name not in _agents():
                    self._json({"error": "no agent by that name has joined"}, 404)
                    return
                if self.path == "/api/chat" and (_agents().get(name) or {}).get("stopped"):
                    self._json({"error": "that agent was spun down"}, 404)
                    return
                try:
                    images = _chat_images(p)
                except Exception as e:
                    self._json({"error": str(e)}, 400)
                    return
                nonce = str(p.get("nonce") or "")[:64]
                if self.path == "/api/chat" and nonce:
                    # the phone retries a send whose answer it lost: same nonce, same row
                    dup = next((r for r in _chat_rows(name) if r.get("nonce") == nonce), None)
                    if dup:
                        self._json({"ok": True, "message": dup})
                        return
                if self.path == "/api/chat":
                    row = _chat_add(name, "you", body, images, nonce=nonce)
                    _inbox({"kind": "tapspace-chat", "agent": name, "id": row["id"],
                            "message": body, "images": images})
                else:
                    _agent_touch(name, listen=time.time())
                    _claim(self._who(), name, "reply")
                    row = _chat_add(name, "master", body, images)
                    _push(_title(_agents().get(name, {})), body, key="agent:" + name)
                self._json({"ok": True, "message": row})
                return
            key = p.get("key", "")
            m = re.match(r"^([\w.-]+/[\w.-]+)#(\d+)$", key)
            if not m:
                self._json({"error": "bad tap key"}, 400)
                return
            via = (p.get("via") or "").strip()
            if via and not _NAME.match(via):
                self._json({"error": "bad via name"}, 400)
                return
            try:
                if not _is_mine(_issue(m.group(1), m.group(2))):
                    self._json({"error": "not one of your taps"}, 403)
                    return
                text = body + (f"\n\n_Sent by {via} for @{_me()}._" if via else "")
                url = _gh("issue", "comment", m.group(2), "--repo", m.group(1),
                          "--body", text).strip()
            except Exception as e:
                self._json({"error": str(e)}, 502)
                return
            if via:
                with _lock:
                    v = _via()
                    v[url] = via
                    VIA.write_text(json.dumps(v, indent=1))
            _inbox({"kind": "tapspace-tap-say", "tap": key, "message": body, "url": url,
                    "via": via})
            _hot(key, 0.15)
            self._json({"ok": True, "url": url})
            return

        if self.path == "/api/agents/title":
            # an agent names itself after the work (2 to 4 plain words); local only
            name = (p.get("agent") or "").strip()
            title = " ".join(str(p.get("title") or "").split())[:40]
            if name not in _agents() or not title or not _NAME.match(title):
                self._json({"error": "needs a joined agent and a short title"}, 400)
                return
            if not self._local():
                self._json({"error": "only from this Mac"}, 403)
                return
            _agent_touch(name, title=title)
            _claim(self._who(), name, "title")
            self._json({"ok": True, "title": title})
            return

        if self.path == "/api/agents/relay":
            name = (p.get("agent") or "").strip()
            if name not in _agents():
                self._json({"error": "no agent by that name has joined"}, 404)
                return
            _agent_touch(name, switching=time.time())
            _claim(self._who(), name, "relay")
            self._json({"ok": True})
            return

        if self.path == "/api/taps/refresh":
            key = p.get("key") or ""
            if re.match(r"^[\w.-]+/[\w.-]+#\d+$", key):
                _hot(key, 0.15)
            _taps_kick.set()
            self._json({"ok": True})
            return

        if self.path == "/api/post":
            missing = [k for k in ("agent", "title") if not p.get(k)]
            media = _media_from(p)
            if media is None:
                missing.append("image_b64 or video_b64 (video_ext mp4|mov)")
            if missing:
                self._json({"error": f"missing {missing}"}, 400)
                return
            kind, ext, b64key = media
            tid = uuid.uuid4().hex[:10]
            shot_dir = SHOTS / tid
            shot_dir.mkdir(parents=True, exist_ok=True)
            (shot_dir / f"1.{ext}").write_bytes(base64.b64decode(p[b64key]))
            who = self._who()
            owner, how = _owner_at_write(p, who, _owner_index())     # before _lock: it may run ps
            with _lock:
                state = _load()
                state["tickets"].append({
                    "id": tid,
                    "agent": p["agent"],
                    "title": p["title"],
                    "campaign": p.get("campaign", ""),
                    "created": time.time(),
                    "version": 1,
                    "versions": [{"v": 1, "note": p.get("note", ""), "ts": time.time(),
                                  "media": kind, "ext": ext}],
                    "status": "waiting",
                    "decisions": [],
                    "owner": owner,
                    "owner_how": how,
                    "session": who.get("session") or None,
                })
                _save(state)
                _learn_campaign(p.get("campaign", ""), owner)
            _touch(p.get("campaign"), p["agent"], "posted", p["title"])
            _push("New visual", p["title"], kind="needs_you", id=tid)
            self._json({"ok": True, "id": tid, "version": 1})
            return

        if self.path == "/api/update":
            tid = p.get("id", "")
            media = _media_from(p)
            if media is None:
                self._json({"error": "missing image_b64 or video_b64 (video_ext mp4|mov)"}, 400)
                return
            kind, ext, b64key = media
            who = self._who()
            owner, how = _owner_at_write(p, who, _owner_index())
            with _lock:
                state = _load()
                t = _find(state, tid)
                if not t:
                    self._json({"error": "not found"}, 404)
                    return
                t["version"] += 1
                v = t["version"]
                (SHOTS / tid).mkdir(parents=True, exist_ok=True)
                (SHOTS / tid / f"{v}.{ext}").write_bytes(base64.b64decode(p[b64key]))
                t["versions"].append({"v": v, "note": p.get("note", ""), "ts": time.time(),
                                      "media": kind, "ext": ext})
                t["status"] = "waiting"
                if owner and (how == "explicit" or not t.get("owner")):
                    t["owner"], t["owner_how"] = owner, how      # the lane iterating proves it; explicit corrects
                if who.get("session") and not t.get("session"):
                    t["session"] = who["session"]
                _save(state)
                _learn_campaign(t.get("campaign", ""), owner)
            _touch(t.get("campaign"), t["agent"], "iterating",
                   p.get("note", "") or t["title"])
            _push("New visual", t["title"], kind="needs_you", id=tid)
            self._json({"ok": True, "id": tid, "version": v})
            return

        if self.path == "/api/decide":
            tid = p.get("id", "")
            action = p.get("action", "")
            if action not in ("approve", "change"):
                self._json({"error": "action must be approve|change"}, 400)
                return
            ix = _owner_index()
            with _lock:
                state = _load()
                t = _find(state, tid)
                if not t:
                    self._json({"error": "not found"}, 404)
                    return
                decision = {
                    "action": action,
                    "message": p.get("message", ""),
                    "version": t["version"],
                    "ts": time.time(),
                }
                t["decisions"].append(decision)
                t["status"] = "approved" if action == "approve" else "changes"
                if not t.get("owner"):
                    # the note goes to the master the card resolves to now; pinned so the rail,
                    # the INBOX relay and every client agree on it from here on
                    t["owner"], t["owner_how"] = _owner_of(t, ix)
                _purge_ticket_videos(t, up_to=t["version"])
                _save(state)
            _touch(t.get("campaign"), t["agent"],
                   "approved" if action == "approve" else "got your notes",
                   t["title"], "decision")
            _inbox({
                "kind": "tapspace-decision",
                "ticket": tid,
                "agent": t["agent"],
                "owner": t.get("owner"),
                "title": t["title"],
                "campaign": t.get("campaign", ""),
                "version": decision["version"],
                "action": action,
                "message": p.get("message", ""),
            })
            self._json({"ok": True})
            return

        if self.path == "/api/archive":
            with _lock:
                state = _load()
                hit = 0
                for t in state["tickets"]:
                    if (p.get("id") and t["id"] == p["id"]) or \
                       (p.get("campaign") and t.get("campaign") == p["campaign"]) or \
                       p.get("all"):
                        t["archived"] = True
                        _purge_ticket_videos(t)
                        hit += 1
                        # a retired ticket takes its presence row with it;
                        # a genuinely active agent re-earns one on its next request
                        ACTIVITY.pop((t.get("campaign") or "-", t["agent"]), None)
                _save(state)
            self._json({"ok": True, "archived": hit})
            return

        self.send_error(404)


if __name__ == "__main__":
    DATA.mkdir(exist_ok=True)
    SHOTS.mkdir(exist_ok=True)
    INBOX.touch(exist_ok=True)
    if not STATE.exists():
        _save({"tickets": []})
    TOKEN = _token()
    threading.Thread(target=_agents_watch, daemon=True).start()
    threading.Thread(target=_scan_sessions, daemon=True).start()
    threading.Thread(target=_taps_loop, daemon=True).start()
    threading.Thread(target=_watch_loop, daemon=True).start()
    links.start(DATA, _bump, titles=lambda: {n: _title(i) for n, i in _agents().items()})
    print(f"TapSpace serving on http://127.0.0.1:{PORT}")
    ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
