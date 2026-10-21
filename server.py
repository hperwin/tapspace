#!/usr/bin/env python3
"""TapSpace server. A local design-approval space for agent fleets.

Agents post real screenshots (or short videos) of what they just built;
you approve or send change notes from the space page in your browser;
decisions land in per-ticket state AND data/INBOX.jsonl, which your
orchestrator can tail and relay to the owning agent. See README.md and
AGENTS.md for the full protocol.

Chat tab: talk to any number of agents, each in its own thread
(data/chat.jsonl; an agent joins with `tap.py listen --as NAME` and answers
with `tap.py reply --as NAME`), and see your taps: the Flightdeck journeys you
authored, exactly as Flightdeck's board lists them (board.py holds the board's
stream with the Flightdeck sign-in Claude Code keeps on this Mac). The board
gives each tap its phase (proposed, en route, holding, landed, done), its word,
goals, title and question. GitHub, through gh as you, adds only what the board
does not carry: the issue's thread (comments, their seen mark, who sent them),
its fly and needs-reply labels (a colleague pilot's question in a comment shows
as needs you), and the proof images in its body, through a token-guarded proxy.
Fly, Accept and replies stay GitHub writes. A tap that leaves the board shows as
done for ten minutes, and how it ended (shipped, aborted, closed) is kept.

A master can publish its run (tap.py goal / step: a north star, the steps, the current one).
Cards know the master that owns them from the Claude session tap.py runs in; a change note
shows under that master until it posts a new version or its thread is opened. Agent-to-agent
messages show as links (links.py). Spin down (POST /api/agents/stop) ends a master's Claude
session and takes it off the list. A master whose sessions all ended (a crash or a reboot) is served
Offline; Restart (POST /api/agents/restart) starts it again under the same NAME.

A master can send an update card (tap.py card): one row with its line, its faces, one visual,
what moved, images to approve and a question; Hayden's answers come back as rows listen hands over.

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
import socket
import subprocess
import pathlib
import re
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import quote, urlencode

import activity
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

# the labels a tap's issue still carries on GitHub: the journey family (_is_mine, for Fly, Accept and say), fly, needs-reply
# Flightdeck label families (renamed in place October 15, #2734): read either spelling, write the first.
TAP_JOURNEY = tuple(dict.fromkeys(("flightdeck", "nava") + tuple(
    x for x in [os.environ.get("TAPSPACE_TAP_LABEL", "").strip()] if x)))
TAP_FLY = ("flightdeck:fly", "auto-launch")
TAP_NEEDS_REPLY = ("flightdeck:needs-reply", "needs-reply")
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
    """agents.json, atomically, every master row with its look (_looks). The caller holds _lock."""
    _looks(a)
    tmp = AGENTS.with_suffix(".tmp")
    tmp.write_text(json.dumps(a, indent=1))
    tmp.replace(AGENTS)


LOOKS = 18                            # 3 tones x 6 marks; look 0 is the lead's
LOOK_HOLD = 86400                     # a master seen within this long keeps its look to itself (the rail's longest quiet)


def _look(info):
    """The look a master's face is drawn with: 0 for the lead (by role, so it follows tap.py lead), else its own."""
    return 0 if info.get("role") == "lead" else info.get("look", 0)


def _looks(a):
    """Gives every master row without a look one, in the order they first joined: the lowest of 1..17 that no
    other master seen in the last day holds, else the one held least (the lowest of those). The lead holds none
    and gets none stored. A look, once stored, never moves. The caller holds _lock."""
    now = time.time()
    held = [r["look"] for r in a.values() if type(r.get("look")) is int and r.get("role") != "lead"
            and not r.get("stopped") and now - r.get("seen", 0) < LOOK_HOLD]
    for r in sorted((r for r in a.values() if "look" not in r and r.get("role") != "lead"),
                    key=lambda r: r.get("first", 0)):
        r["look"] = min(range(1, LOOKS), key=lambda k: (held.count(k), k))
        held.append(r["look"])


def _agent_touch(name, **extra):
    with _lock:
        a = _agents()
        row = a.get(name) or {"name": name, "first": time.time()}
        if "heard" in extra:            # listen's cursor only moves forward: two polls for one NAME can cross
            extra["heard"] = max(extra["heard"], row.get("heard") or 0)
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
    """Saves a post's attached images (max 10) under data/chat-media and returns their served paths, in order.
    An item is {url} (already imported by /api/chat/import), {b64, ext} (20 MB each), or {proof: {key, i}}:
    proof image i of one of your taps, its clean original copied here so it outlives the tap. Every item is
    read (a proof fetched) before any file is written, so a refused post leaves nothing behind. Raises
    ValueError: 400, or its code (503 while the taps load, 502 when GitHub did not serve it)."""
    imgs = p.get("images") or []
    if not isinstance(imgs, list) or len(imgs) > 10:
        raise ValueError("up to 10 images")
    got = []                              # (served path, None) or (ext, bytes)
    for im in imgs:
        im = im if isinstance(im, dict) else {}
        served = str(im.get("url") or "")
        if served:
            # already imported by /api/chat/import
            f = (DATA / served.lstrip("/")).resolve()
            if not served.startswith("/chat-media/") or not f.is_file() or \
               not str(f).startswith(str(MEDIA.resolve()) + "/"):
                raise ValueError("unknown image")
            got.append((served, None))
            continue
        if "proof" in im:
            got.append(_proof_copy(im["proof"]))
            continue
        ext = str(im.get("ext", "")).lower().lstrip(".")
        if ext not in _CHAT_IMG_EXTS:
            raise ValueError(f"unsupported image type {ext!r}")
        raw = base64.b64decode(im.get("b64") or "", validate=False)
        if not raw or len(raw) > 20_000_000:
            raise ValueError("empty or too large image")
        got.append((ext, raw))
    out = []
    for ext, raw in got:
        if raw is None:
            out.append(ext)
            continue
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


def _chat_add(agent, role, body, images=None, nonce="", card=None, answer=None):
    with _lock:
        row = _chat_append(agent, role, body, images, nonce, card, answer)
    _bump("chat", "agents")
    return row


def _chat_append(agent, role, body, images=None, nonce="", card=None, answer=None):
    """_chat_add's write, for a caller that holds _lock (it checks the thread in the same hold); it bumps after."""
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
    if card:
        row["card"] = card
    if answer:
        row["answer"] = answer
    with CHAT.open("a") as fh:
        fh.write(json.dumps(row) + "\n")
    return row


def _chat_pending(rows, heard=None):
    """Reviewer messages for the agent (rows of ONE agent): every one after its last reply, as before, plus,
    once listen has marked what it handed over (heard), any that came in after that mark, so a reply or card
    the agent posts before its next listen never hides one. _agent_state calls it without heard: Thinking
    keeps the old rule."""
    last = max((r["id"] for r in rows if r["role"] == "master"), default=0)
    return [r for r in rows if r["role"] == "you" and (r["id"] > last or (heard is not None and r["id"] > heard))]


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


def _offline(name):
    """NAME claimed Claude sessions and none of them still runs (a crash, a reboot): activity.py's own "gone"
    test, asked of its one Reader, so the rail and the lead's activity never disagree. False for a NAME that
    never claimed a session. Never under _lock."""
    with activity._ONE_LOCK:
        if activity._ONE is None:
            activity._ONE = activity.Reader()
        r = activity._ONE
    with r._lock:
        rows = [(sid, c) for sid, c in r._claims(CLAIMS).items()
                if isinstance(c, dict) and c.get("name") == name and links._SID.match(sid)]
        return bool(rows) and not any(r._live(sid, c, _proc_start) for sid, c in rows)


def _row_state(name, info, rows):
    """The state a master row is served with: _agent_state, except that a NAME whose claimed sessions all
    ended is offline, whatever it last stored. Switching and starting still win."""
    st = _agent_state(info, rows)
    return "offline" if st not in ("switching", "starting") and _offline(name) else st


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


def _focus(allrows, taps, said=None):
    """The thread the reviewer is talking in: the one his newest own message went to, a chat row of his
    (agent:NAME) or a comment he sent on a tap through TapSpace himself (that tap's key); None when he never
    wrote. An agent's own gh comment is signed in as him too, so a tap comment counts only when the say route
    recorded it (said: tap-said.json's urls, read when None). Clients never badge the focus. allrows: every chat
    row; taps: TAPS items."""
    said = _said() if said is None else said
    best, key = None, None
    for r in allrows:
        if r.get("role") == "you" and (best is None or r.get("ts", 0) >= best):
            best, key = r.get("ts", 0), "agent:" + r["agent"]
    for t in taps:
        for m in t.get("messages") or []:
            if m.get("who") == "you" and m.get("url") in said and (best is None or _epoch(m["ts"]) > best):
                best, key = _epoch(m["ts"]), t["key"]
    return key


def _agent_rows(allrows=None):
    out = []
    allrows = _chat_rows() if allrows is None else allrows
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
        out.append({"name": name, "state": _row_state(name, info, rows),
                    "seen": info.get("seen", 0), "first": info.get("first", 0),
                    "where": info.get("where", ""), "last": last, "count": len(rows),
                    "last_master": said[-1] if said else 0,
                    "title": _title(info),
                    "progress": info.get("progress"),
                    "notes": [n for n in notes.get(name, []) if n["ts"] > (read or 0)][:NOTES_MAX],
                    "notes_read": read, "look": _look(info)})
    rank = {"thinking": 0, "switching": 0, "starting": 0, "here": 1, "away": 2, "offline": 3}
    out.sort(key=lambda r: -r["last"])
    out.sort(key=lambda r: rank[r["state"]])
    # the lead (Muddi, the master over masters): live only while its row is served. Every other row's
    # parent is the NAME stored on it, else the live lead's; the lead comes first, then its children.
    lead = next((r for r in out if agents[r["name"]].get("role") == "lead"), None)
    for r in out:
        stored = agents[r["name"]].get("parent")
        r["parent"] = stored if isinstance(stored, str) and stored else \
            (lead["name"] if lead and r is not lead else None)
    if lead:
        lead["role"] = "lead"
        # what the lead is doing right now, read from its own Claude session's transcript (activity.py)
        lead["activity"] = activity.of(
            lead["name"], agents[lead["name"]], lead["state"], CLAIMS, _proc_start,
            waits=lambda: _waits_on_you([m for m in allrows if m.get("agent") == lead["name"]], TAPS["items"]),
            titles=lambda: {n: _title(i) for n, i in agents.items()}, taps=TAPS["items"])
        out = [lead] + [r for r in out if r is not lead and r["parent"] == lead["name"]] + \
              [r for r in out if r is not lead and r["parent"] != lead["name"]]
    return out


def _newest_card(thread):
    """The newest card row in one agent's thread, or None: the only card that can still wait on the reviewer."""
    return next((r for r in reversed(thread) if r.get("role") == "master" and isinstance(r.get("card"), dict)), None)


def _ask_open(thread):
    """True when the newest card in this thread asks something and its ask has no answer yet. Like its images,
    an older card's question is left behind once a newer card comes."""
    row = _newest_card(thread)
    return bool(row and row["card"].get("ask")) and not any(
        r.get("role") == "you" and (r.get("answer") or {}).get("card") == row["id"] and "checks" in r["answer"]
        for r in thread)


def _waits_on_you(thread, taps=(), outcomes=None):
    """True when a master's thread waits on the reviewer: its newest card's question is unanswered (_ask_open),
    or it still has an image without a verdict, or an Accept / Not quite nobody pressed while its tap is open
    (taps: TAPS items; outcomes: how taps ended, TAPS outcomes when None). A served tap is open until it is done;
    a tap no longer served is settled once its key is in outcomes (R13). One neither served nor in outcomes still
    waits, and _compose asks the board once how it ended (_SETTLE)."""
    if _ask_open(thread):
        return True
    row = _newest_card(thread)
    if not row:
        return False
    said = [r["answer"] for r in thread if r.get("role") == "you" and (r.get("answer") or {}).get("card") == row["id"]]
    if row["card"].get("action"):
        key = row["card"]["action"].get("tap")
        ended = TAPS["outcomes"] if outcomes is None else outcomes
        tap = next((t for t in taps if t.get("key") == key), None)
        if tap is None and key not in ended and key not in _SETTLE:
            _SETTLE[key] = 0.0                               # never asked yet
            _taps_kick.set()
        return (tap["phase"] != "done" if tap else key not in ended) and \
            not any("action" in a for a in said)             # the images are the action's picture
    return any(not any(a.get("image") == i for a in said) for i in range(len(row["card"].get("images") or [])))


def _fleet(rows, taps, chat):
    """GET /api/agents fleet: everything running under the lead. Needs you first (taps holding or landed, or with a
    pilot's question on their issue, masters whose newest card waits on you), then working (masters thinking, starting or switching cars, taps flying),
    then the rest, newest first within each. A master is in it unless it is away with its run done (or no run);
    a tap while it is proposed, flying, holding or landed. [] when no lead is served. Clients render it as is
    and never work membership out themselves. rows: _agent_rows(chat); taps: TAPS items; chat: every row."""
    lead = next((r for r in rows if r.get("role") == "lead"), None)
    if not lead:
        return []
    threads = {}
    for m in chat:
        threads.setdefault(m.get("agent"), []).append(m)
    out = []
    for r in rows:
        pg = r.get("progress") or {}
        steps = pg.get("steps") or []
        done = min(pg.get("current") or 0, len(steps))
        if r is lead or (r["state"] in ("away", "offline") and done >= len(steps)):
            continue
        out.append({"key": "agent:" + r["name"], "kind": "master", "title": r["title"], "look": r["look"],
                    "state": r["state"], "done": done, "total": len(steps),
                    "needs_you": _waits_on_you(threads.get(r["name"], []), taps),
                    "working": r["state"] in ("thinking", "starting", "switching"),
                    "at": max(r["last"], pg.get("updated") or 0)})
    for t in taps:
        if t.get("phase") not in ("holding", "landed", "enroute", "proposed"):
            continue
        g = t.get("goals") or []
        out.append({"key": t["key"], "kind": "tap", "title": _note_words(_undash(t["title"]), 80),
                    "initial": _initial(t["repo"].split("/")[-1]), "phase": t["phase"], "word": t["phase_word"],
                    "done": sum(1 for x in g if x.get("done")), "total": len(g),
                    "needs_you": t["phase"] in ("holding", "landed") or t["status"] == "needs-you",
                    "working": t["phase"] == "enroute",
                    "at": _epoch(t["last"])})
    out.sort(key=lambda f: -f["at"])
    out.sort(key=lambda f: 0 if f["needs_you"] else 1 if f["working"] else 2)
    return [{k: v for k, v in f.items() if k != "at"} for f in out]


def _set_lead(name):
    """NAME becomes the one lead (the newest call wins). A NAME that has not joined yet gets a bare row,
    off the rail until it listens. The lead reports to no one."""
    with _lock:
        a = _agents()
        for n, row in a.items():
            if n != name:
                row.pop("role", None)
        row = a.get(name) or {"name": name, "first": time.time()}
        row["role"] = "lead"
        row.pop("parent", None)
        a[name] = row
        _agents_write(a)
    _bump("agents")


def _adopt(lead, child):
    """Stores parent=lead on child (an empty lead clears it, back to the live lead) -> (code, answer).
    A mark the reviewer's lead makes, not the child's activity, so seen is left alone."""
    with _lock:
        a = _agents()
        if child not in a:
            return 404, {"error": "no agent by that name has joined"}
        if lead and lead not in a:
            return 404, {"error": "no lead by that name has joined"}
        if lead and lead == child:
            return 400, {"error": "an agent cannot lead itself"}
        if lead and a[child].get("role") == "lead":
            return 400, {"error": "the lead reports to no one"}
        up, seen = lead, set()
        while up and up not in seen:
            if up == child:
                return 400, {"error": "that would make a loop"}
            seen.add(up)
            up = (a.get(up) or {}).get("parent")
        if lead:
            a[child]["parent"] = lead
        else:
            a[child].pop("parent", None)
        _agents_write(a)
    _bump("agents")
    return 200, {"ok": True, "child": child, "parent": lead or None}


def _agents_watch():
    """Agent state also changes with time (Here goes Away, a new car starts listening),
    so the cursor follows the rendered rows, not only the writes."""
    last = None
    while True:
        try:
            chat = _chat_rows()
            rows = _agent_rows(chat)
            # focus too: a tap comment of his moves it through the taps refresh, not a chat write
            sig = ([(r["name"], r["state"], r["where"], r["title"], (r["progress"] or {}).get("updated"),
                     r["notes_read"], [(n["ticket"], n["version"], n["ts"]) for n in r["notes"]],
                     r.get("role"), r["parent"], r["look"],
                     tuple((r.get("activity") or {}).get(k) for k in ("state", "word", "detail")))
                    for r in rows],
                   links.rows({r["name"] for r in rows}), _focus(chat, TAPS["items"]))
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


# ---- update cards: a master's update in its thread, and the reviewer's answers to it ----
# A card is a master row with a card object; an answer is a reviewer row with an answer object. Rows are never
# edited: a newer update is a new card. The server writes both bodies as one plain line, so old clients, the push,
# the Chat-list preview and tap.py listen read them like any message.
CARD_MAX = 8192                       # bytes of one stored card, filled in
_TAP_KEY = re.compile(r"^[\w.-]+/[\w.-]+#\d+$")


class _Refused(ValueError):
    """A refused send whose http code is not 400: 503 while the taps are still loading (send it again; the
    answer says retry: true), 502 when GitHub did not serve a proof image."""

    def __init__(self, msg, code):
        super().__init__(msg)
        self.code = code


def _refusal(e):
    """(http code, answer) for a refused send."""
    code = e.code if isinstance(e, _Refused) else 400
    return code, {"error": str(e), **({"retry": True} if code == 503 else {})}


def _loading():
    """The 503 a send gets while the taps have not loaded since this server started, with the board's plain sentence
    when it has one (R12)."""
    return _Refused("taps are still loading; send it again in a minute" +
                    (f". {TAPS['error']}" if TAPS["error"] else ""), 503)


def _one_line(s):
    """s as one line: each line's whitespace folded, the lines joined with " / " (card and answer bodies)."""
    return " / ".join(x for x in (" ".join(line.split()) for line in str(s or "").splitlines()) if x)


def _initial(s):
    """The letter a tap's or a project's face carries: the first letter or digit of s, upper case."""
    return next((c.upper() for c in str(s) if c.isalnum()), "?")


def _ref(label, agents, taps, what):
    """The face key for what a sender typed: agent:NAME (a NAME or one agent's title; agent: optional),
    owner/repo#n (one of your taps, or repo#n when exactly one of them matches) or project:NAME.
    ValueError when nothing matches; the 503 while the taps load (taps is None)."""
    s = " ".join(str(label or "").split())
    if not s:
        _bad(f"{what}: a master's NAME or title, a tap (owner/repo#n) or project:NAME")
    if s.startswith("project:"):
        return "project:" + (_plain(s[8:], 60) or _bad(f"{what}: project: and a plain name up to 60 characters"))
    hit = _match_agent(s[6:] if s.startswith("agent:") else s, agents)
    if hit:
        return "agent:" + hit
    if re.match(r"^(?:[\w.-]+/)?[\w.-]+#\d+$", s):
        if taps is None:
            raise _loading()
        k = s.casefold()
        keys = [t["key"] for t in taps
                if t["key"].casefold() == k or ("/" not in k and t["key"].casefold().endswith("/" + k))]
        if len(keys) == 1:
            return keys[0]
        _bad(f"{what}: {'more than one of your taps is' if keys else 'none of your taps is'} {s} (tap.py taps lists them)")
    _bad(f"{what}: no master, tap or project by that name: {s}")


def _card_visual(v):
    """The card's one visual (pure): progress {done, total, label?}, timeline {steps, at} (at counts from 0;
    len(steps) = all done), graph {points, label?} or word {text}. ValueError says what to fix."""
    kind = v.get("kind") if isinstance(v, dict) else None
    if kind == "progress":
        d, t = v.get("done"), v.get("total")
        if type(d) is not int or type(t) is not int or not 0 <= d <= t <= 999 or t < 1:
            _bad("progress: whole numbers done and total, 0 <= done <= total, total from 1 to 999")
        out = {"kind": kind, "done": d, "total": t}
    elif kind == "timeline":
        steps = [_plain(s, 24) for s in v["steps"]] if isinstance(v.get("steps"), list) else []
        if not 2 <= len(steps) <= 7 or None in steps:
            _bad("timeline: 2 to 7 plain steps, up to 24 characters each")
        at = v.get("at", 0)
        if type(at) is not int or not 0 <= at <= len(steps):
            _bad(f"timeline at: 0 to {len(steps)} (0 = on the first step, {len(steps)} = all done)")
        out = {"kind": kind, "steps": steps, "at": at}
    elif kind == "graph":
        pts = v.get("points")
        if not isinstance(pts, list) or not 2 <= len(pts) <= 24 or \
           any(type(x) not in (int, float) or not -1e12 < x < 1e12 for x in pts):     # also refuses NaN and inf
            _bad("graph: 2 to 24 numbers")
        out = {"kind": kind, "points": pts}
    elif kind == "word":
        out = {"kind": kind, "text": _plain(v.get("text"), 16) or _bad("word: one plain word or two, up to 16 characters")}
    else:
        _bad("visual kind: progress, timeline, graph or word")
    if kind in ("progress", "graph") and " ".join(str(v.get("label") or "").split()):
        out["label"] = _plain(v["label"], 40) or _bad(f"{kind} label: one plain line up to 40 characters")
    return out


def _card_ask(a):
    """The card's question (pure): q, checks [{id: c1..cN, label}], text (whether it shows a text box)."""
    a = a if isinstance(a, dict) else {}
    q = _plain(a.get("q"), 120) or _bad("ask: a plain question up to 120 characters")
    raw = a.get("checks") or []
    labels = [_plain(c, 40) for c in raw] if isinstance(raw, list) else [None]
    if len(labels) > 6 or None in labels or len({c.casefold() for c in labels}) != len(labels):
        _bad("ask checks: up to 6 different plain labels, up to 40 characters each")
    text = a.get("text", False)
    if type(text) is not bool:
        _bad("ask text: true or false (a text box under the question)")
    if not labels and not text:
        _bad("ask: boxes to tick (checks), a text box (text: true), or both")
    return {"q": q, "checks": [{"id": f"c{i + 1}", "label": c} for i, c in enumerate(labels)], "text": text}


def _card(raw, sender, n_images, agents, taps):
    """An update card as a master sends it -> the checked card: v, line, face, visual?, items [{ref, name?, note?}],
    images (one {label?} per attached image), ask?, action? (the one action a card may carry: tap_accept, the
    reviewer's Accept of one tap). face and refs come back as face keys (_ref). face is the card's one subject:
    given, else the one item, else None (several items or none), never the sender, whose thread it already is.
    agents: agents.json; taps: TAPS items, or None while they load (pure except that its 503 names
    TAPS["error"]). ValueError says what to fix."""
    if not isinstance(raw, dict):
        _bad("card: an object with a line")
    line = _plain(raw.get("line"), 120) or _bad(
        "line: one plain line up to 120 characters (no quotes, angle brackets or long dashes)")
    items = raw.get("items") or []
    if not isinstance(items, list) or len(items) > 8:
        _bad("items: up to 8")
    out_items = []
    for it in items:
        it = it if isinstance(it, dict) else {}
        ref = _ref(it.get("ref"), agents, taps, "item")
        if any(x["ref"] == ref for x in out_items):
            _bad(f"items: {ref} is in the card twice")
        x = {"ref": ref}
        if " ".join(str(it.get("name") or "").split()):         # a short name the clients show over its title
            x["name"] = _plain(it["name"], 40) or _bad("item name: one plain line up to 40 characters")
        if " ".join(str(it.get("note") or "").split()):
            x["note"] = _plain(it["note"], 80) or _bad("item note: one plain line up to 80 characters")
        out_items.append(x)
    face = _ref(raw["face"], agents, taps, "face") if " ".join(str(raw.get("face") or "").split()) else \
        out_items[0]["ref"] if len(out_items) == 1 else None
    card = {"v": 1, "line": line, "face": face}
    if raw.get("visual") is not None:
        card["visual"] = _card_visual(raw["visual"])
    card["items"] = out_items
    labels = raw.get("images") or []
    if not isinstance(labels, list) or len(labels) > n_images:
        _bad(f"card images: at most one label per attached image ({n_images} attached)")
    card["images"] = []
    for i in range(n_images):
        lab = (labels[i] if i < len(labels) and isinstance(labels[i], dict) else {}).get("label")
        card["images"].append({"label": _plain(lab, 40) or _bad(f"image {i + 1} label: one plain line up to 40 characters")}
                              if " ".join(str(lab or "").split()) else {})
    if raw.get("ask") is not None:
        card["ask"] = _card_ask(raw["ask"])
    if raw.get("action") is not None:
        act = raw["action"] if isinstance(raw["action"], dict) else {}
        if act.get("kind") != "tap_accept":
            _bad("action kind: tap_accept (the reviewer's Accept of one of your taps)")
        tap = _ref(act.get("tap"), agents, taps, "action tap")
        if tap.startswith(("agent:", "project:")):
            _bad("action tap: one of your taps (owner/repo#n), not a master or a project")
        card["action"] = {"kind": "tap_accept", "tap": tap}
    return card


def _face(key, rows, agents, taps):
    """What a client needs to draw one face key when its own store does not know it: kind (lead, master, tap,
    project) and title, a master's look and state, a tap's initial and phase, a project's initial."""
    if key.startswith("agent:"):
        info, r = agents.get(key[6:]) or {}, rows.get(key[6:])
        return {"kind": "lead" if info.get("role") == "lead" else "master", "look": _look(info),
                "title": _title(info), "state": r["state"] if r else "away"}
    if key.startswith("project:"):
        name = key[8:].rsplit("/", 1)[-1]
        return {"kind": "project", "title": name, "initial": _initial(name)}
    t = next((t for t in taps if t["key"] == key), {})
    return {"kind": "tap", "title": _note_words(_undash(t.get("title") or ""), 80) or key.split("/", 1)[-1],
            "initial": _initial(key.split("/", 1)[-1]), **({"phase": t["phase"]} if t else {})}


def _now(ref, rows, agents, taps):
    """One item as it stands now: kind and title, a tap's phase, word and goals [done, total], a master's
    state and progress {current, steps} (when it has a goal card)."""
    f = _face(ref, rows, agents, taps)
    out = {"kind": f["kind"], "title": f["title"]}
    if f["kind"] == "tap":
        t = next((t for t in taps if t["key"] == ref), None)
        if t:
            g = t.get("goals") or []
            out.update(phase=t["phase"], word=t["phase_word"], goals=[sum(1 for x in g if x.get("done")), len(g)])
    elif f["kind"] in ("lead", "master"):
        out["state"] = f["state"]
        pg = (rows.get(ref[6:]) or agents.get(ref[6:]) or {}).get("progress")
        if pg and pg.get("steps"):
            out["progress"] = {"current": pg.get("current", 0), "steps": pg["steps"]}
    return out


def _was(old, now):
    """Where an item's change beat starts, from that item's snapshot in an earlier card: a tap's phase, word and
    goals, a master's state and current step (the step only when its steps are the same list); None when there is
    nothing to move from."""
    if not old:
        return None
    if now["kind"] == "tap":
        return {"phase": old["phase"], **({"word": old["word"]} if old.get("word") else {}),
                **({"goals": old["goals"]} if old.get("goals") else {})} if old.get("phase") else None
    out = {"state": old["state"]} if old.get("state") else {}
    if now.get("progress") and (old.get("progress") or {}).get("steps") == now["progress"]["steps"]:
        out["current"] = old["progress"]["current"]
    return out or None


def _card_fill(card, sources, served, agents, taps, thread):
    """The card as stored (pure): faces (a snapshot of every face it names, so a master that left or a tap that
    aged out still draws), each item as it stands now plus was (that item in the latest earlier card of this
    thread that showed it), visual.from (the last card's progress when it had the same face, or none like this
    one), and each image's label (given, else its proof's alt text, else Image N) and source. sources: each
    posted image item's proof ref ({key, i}) or None; served: _agent_rows(); agents: agents.json; taps: TAPS
    items; thread: this agent's chat rows."""
    rows = {r["name"]: r for r in served}
    earlier = [r["card"] for r in thread if r.get("role") == "master" and isinstance(r.get("card"), dict)]
    keys = ([card["face"]] if card["face"] else []) + [it["ref"] for it in card["items"]] + \
        ([card["action"]["tap"]] if card.get("action") else [])
    out = {"v": 1, "line": card["line"], "face": card["face"],
           "faces": {k: _face(k, rows, agents, taps) for k in keys}}
    vis, last = card.get("visual"), earlier[-1] if earlier else {}
    if vis:
        out["visual"] = vis
        if vis["kind"] == "progress" and last.get("face") == card["face"] and \
           (last.get("visual") or {}).get("kind") == "progress":
            out["visual"] = {**vis, "from": min(last["visual"]["done"], vis["total"])}
    out["items"] = []
    for it in card["items"]:
        x = {**it, **_now(it["ref"], rows, agents, taps)}
        old = next((y for c in reversed(earlier) for y in c.get("items") or [] if y.get("ref") == it["ref"]), None)
        was = _was(old, x)
        if was:
            x["was"] = was
        out["items"].append(x)
    out["images"] = []
    for i, im in enumerate(card["images"]):
        src = sources[i] if i < len(sources) and isinstance(sources[i], dict) else {}
        key, n = str(src.get("key") or ""), src.get("i")
        tap = next((t for t in taps if t["key"] == key), None) if type(n) is int and n >= 0 else None
        proof = (tap.get("proof") or []) if tap else []
        alt = (proof[n].get("alt") or "") if tap and n < len(proof) else ""
        label = im.get("label") or _proof_label(alt) or f"Image {i + 1}"
        out["images"].append({"label": label, **({"from": {"tap": key, "i": n}} if tap else {})})
    if card.get("ask"):
        out["ask"] = card["ask"]
    if card.get("action"):
        out["action"] = card["action"]
    return out


def _cut_words(s, cap=40):
    """s cut at a word boundary to at most cap characters, ending in ... when it was cut (labels, and the tap titles
    in answer bodies)."""
    s = " ".join(str(s or "").split())
    return s if len(s) <= cap else s[:cap - 3].rsplit(" ", 1)[0].rstrip(" ,;:.") + "..."


def _proof_label(alt):
    """A proof image's caption as its card label: the caption without its double quotes, cut at a word boundary
    under 40 characters; None when there is no caption, it is just "Image", or it is not plain."""
    s = " ".join(re.sub(r'["\u201c\u201d]', "", str(alt or "")).split())
    return _plain(_cut_words(s), 40) if s and s.casefold() != "image" else None


def _proof_copy(pr):
    """(ext, bytes) for a {proof: {key, i}} image item: the clean original of one of your taps' proof images,
    through _proof_image (its data/proof cache, else GitHub). Raises ValueError (400), or the 503 / 502 _Refused."""
    pr = pr if isinstance(pr, dict) else {}
    key, i = str(pr.get("key") or ""), pr.get("i")
    if not _TAP_KEY.match(key) or type(i) is not int or i < 0:
        raise ValueError("proof image: {key: owner/repo#n, i: its number counted from 0}")
    if not TAPS["fetched"]:
        raise _loading()
    try:
        raw, ctype, _ = _proof_image(key, i)
    except LookupError as e:                       # "no such tap" | "no such image"
        raise ValueError(f"proof image {i + 1} of {key}: {e} (tap.py tap-read {key} numbers them)")
    except Exception as e:
        raise _Refused(f"proof image {i + 1} of {key}: GitHub did not serve it ({str(e)[:160]})", 502)
    return _PROOF_TYPES[ctype][1:], raw


def _send_card(name, p, who):
    """POST /api/chat/reply with a card (this Mac only) -> (http code, answer). The card is checked before
    anything is written; then the reply counts as listening, the server fills the card in (_card_fill), copies
    its images (a tap's proof included) into chat-media, stores one row and pushes its line. Two cards from one
    NAME in the same instant may read the same earlier card for was; tap.py sends one at a time."""
    posted = p["images"] if isinstance(p.get("images"), list) else []
    try:
        card = _card(p["card"], name, len(posted), _agents(), TAPS["items"] if TAPS["fetched"] else None)
    except ValueError as e:
        return _refusal(e)
    _agent_touch(name, listen=time.time())
    _claim(who, name, "reply")
    # outside _lock: _agent_rows takes it, and threading.Lock is not reentrant
    card = _card_fill(card, [im.get("proof") if isinstance(im, dict) else None for im in posted],
                      _agent_rows(), _agents(), TAPS["items"], _chat_rows(name))
    if len(json.dumps(card)) > CARD_MAX:
        return 400, {"error": f"card: over {CARD_MAX // 1024} KB once filled in; send fewer items or shorter words"}
    try:
        images = _chat_images(p)
    except ValueError as e:
        return _refusal(e)
    body = _one_line(p.get("body")) or card["line"]
    row = _chat_add(name, "master", body, images, card=card)
    _push(_title(_agents().get(name, {})), body, key="agent:" + name)
    return 200, {"ok": True, "message": row}


def _answer(raw, card_row):
    """An answer to a card as a client sends it (pure) -> the checked answer: {card, image, verdict, note?} for
    one image, {card, checks, text?} for the question (checks in the card's order, each once), or {card,
    action: deny, note?} for Not quite on the card's Accept (Accept itself goes through the tap's own route,
    _accepted). card_row is that card's row in this thread, or None. Hayden's note and text are kept as he wrote
    them (trimmed, up to 2000 characters). ValueError says what to fix."""
    if not isinstance(raw, dict) or type(raw.get("card")) is not int:
        _bad("answer: {card: the card's message id, then image and verdict, or checks and text}")
    if not card_row:
        _bad("answer card: no card by that id in this thread")
    c, out = card_row["card"], {"card": card_row["id"]}
    for k in ("note", "text"):
        if raw.get(k) is not None and not isinstance(raw[k], str):
            _bad(f"answer {k}: words")
        if len((raw.get(k) or "").strip()) > 2000:
            _bad(f"answer {k}: up to 2000 characters")
    note, text = (raw.get("note") or "").strip(), (raw.get("text") or "").strip()
    if "action" in raw:
        if any(k in raw for k in ("image", "verdict", "checks", "text")):
            _bad("answer: the card's Accept, one image or the question, one at a time")
        if not c.get("action"):
            _bad("answer action: that card has no Accept")
        if raw["action"] != "deny":
            _bad("answer action: deny (Not quite); Accept is the tap's own button (POST /api/taps/accept with the card)")
        out.update(action="deny", **({"note": note} if note else {}))
        return out
    if "image" in raw:
        if "checks" in raw or "text" in raw:
            _bad("answer: one image or the question, not both")
        n, i = len(c.get("images") or []), raw["image"]
        if type(i) is not int or not 0 <= i < n:
            _bad(f"answer image: 0 to {n - 1}" if n else "answer image: that card has no images")
        if raw.get("verdict") not in ("approve", "change"):
            _bad("answer verdict: approve or change")
        if note and raw["verdict"] != "change":
            _bad("answer note: only with change (Not quite)")
        out.update(image=i, verdict=raw["verdict"], **({"note": note} if note else {}))
        return out
    if "checks" not in raw and "text" not in raw:
        _bad("answer: image and verdict, or checks and text")
    ask = c.get("ask")
    if not ask:
        _bad("answer: that card asks nothing")
    ids, got = [x["id"] for x in ask.get("checks") or []], raw.get("checks") or []
    if not isinstance(got, list) or any(x not in ids for x in got):
        _bad("answer checks: ids from the card (" + (", ".join(ids) or "it has none") + ")")
    if text and not ask.get("text"):
        _bad("answer text: that card has no text box")
    if not got and not text and not ids:
        _bad("answer text: write something to send")
    out["checks"] = [x for x in ids if x in got]
    if text:
        out["text"] = text
    return out


def _action_title(card):
    """The tap a card's Accept is for, as the card named it: the short name of the card's item for that tap when
    it has one, else the tap's title in the card's faces."""
    tap = card["action"]["tap"]
    names = [str(it.get("name") or "").strip() for it in card.get("items") or [] if it.get("ref") == tap]
    return next(filter(None, names), "") or ((card.get("faces") or {}).get(tap) or {}).get("title") or "the tap"


def _answer_line(a, card):
    """An answer row's body, one line: "Approved: <label>", "Not quite: <label>: <note>" (no note: just the label),
    "Accepted: <tap>", "Not quite: <tap>: <note>" (the tap as _action_title names it), or "<q> <ticked labels>.
    <text>" ("(no boxes ticked)" when the card has boxes and none is ticked)."""
    if "action" in a:
        title = _cut_words(_action_title(card))            # a long tap title would bury the note
        if a["action"] == "accept":
            return "Accepted: " + title
        return "Not quite: " + title + (": " + _one_line(a["note"]) if a.get("note") else "")
    if "image" in a:
        label = card["images"][a["image"]]["label"]
        if a["verdict"] == "approve":
            return "Approved: " + label
        return "Not quite: " + label + (": " + _one_line(a["note"]) if a.get("note") else "")
    ask = card["ask"]
    names = {x["id"]: x["label"] for x in ask["checks"]}
    ticks = ", ".join(names[x] for x in a["checks"]) + "." if a["checks"] else "(no boxes ticked)" if names else ""
    return " ".join(x for x in (ask["q"], ticks, _one_line(a.get("text"))) if x)


def _send_answer(name, p):
    """POST /api/chat with an answer to a card -> (http code, answer). The server writes the row's one-line body
    (a body sent is ignored); a retry with the same nonce gets the same row; the INBOX line carries the answer.
    It is never a ticket and never a tap's accept: nothing here reaches gh or _tap_act."""
    if p.get("images"):
        return 400, {"error": "an answer carries no images"}
    rows, nonce = _chat_rows(name), str(p.get("nonce") or "")[:64]
    dup = next((r for r in rows if nonce and r.get("nonce") == nonce), None)
    if dup:
        # the phone retries a send whose answer it lost: same nonce, same row
        return 200, {"ok": True, "message": dup}
    raw = p["answer"]
    cid = raw.get("card") if isinstance(raw, dict) else None
    card_row = next((r for r in rows if type(cid) is int and r["id"] == cid and r.get("role") == "master"
                     and isinstance(r.get("card"), dict)), None)
    try:
        answer = _answer(raw, card_row)
    except ValueError as e:
        return 400, {"error": str(e)}
    body = _answer_line(answer, card_row["card"])
    row = _chat_add(name, "you", body, nonce=nonce, answer=answer)
    _inbox({"kind": "tapspace-chat", "agent": name, "id": row["id"], "message": body, "images": [], "answer": answer})
    return 200, {"ok": True, "message": row}


def _accept_card(cid, key):
    """The card an Accept came from (POST /api/taps/accept {key, card}): the master row whose card's action is
    the Accept of that tap. Checked before gh is called, so a wrong card accepts nothing. ValueError says why."""
    if type(cid) is not int:
        _bad("card: the card's message id")
    row = next((r for r in _chat_rows() if r["id"] == cid and r.get("role") == "master"
                and isinstance(r.get("card"), dict)), None)
    if not row:
        _bad("card: no card by that id")
    act = row["card"].get("action") or {}
    if act.get("kind") != "tap_accept":
        _bad("card: that card has no Accept")
    if str(act.get("tap") or "").casefold() != str(key or "").casefold():
        _bad("card: that card's Accept is for another tap")
    return row


def _accepted(row):
    """After an Accept from a card went through: the reviewer's answer row in that card's thread ({card, action:
    accept}, "Accepted: <tap title>"), so the master hears it on listen like any answer. One per card: pressing
    it again gets the same row (checked and written in one _lock hold, so two presses at once write one)."""
    a = {"card": row["id"], "action": "accept"}
    body = _answer_line(a, row["card"])
    with _lock:
        dup = next((r for r in _chat_rows(row["agent"]) if r.get("role") == "you" and r.get("answer") == a), None)
        out = dup or _chat_append(row["agent"], "you", body, answer=a)
    if dup:
        return dup
    _bump("chat", "agents")
    _inbox({"kind": "tapspace-chat", "agent": row["agent"], "id": out["id"], "message": body, "images": [], "answer": a})
    return out


def _heard_answer(a, row):
    """An answer as listen hands it over (never stored): plus its card's line, for an image that image's label
    and its file on this Mac, for the card's Accept or Not quite the tap's key."""
    out = {**a, "line": row["card"]["line"]}
    i = a.get("image")
    if type(i) is int and 0 <= i < min(len(row.get("images") or []), len(row["card"].get("images") or [])):
        out.update(label=row["card"]["images"][i]["label"], file=str(DATA / row["images"][i].lstrip("/")))
    if "action" in a and row["card"].get("action"):
        out["tap"] = row["card"]["action"]["tap"]
    return out


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


# ---- who is on the other end of a request (fly and accept are the reviewer's own buttons) ----
# Headers are the caller's to choose; the socket is not. The process on this Mac that holds the other
# end of the connection, and every process above it, says whether an agent sent it: a claude or codex
# process anywhere above it makes it one. The page in the browser, the Mac app and the phone's tunnel
# (ngrok, Tailscale) run under launchd, never under an agent.
AGENT_COMMS = ("claude", "codex")
LSOF = shutil.which("lsof") or "/usr/sbin/lsof"


def _socket_owners(host, port):
    """Pids of this user's processes that hold the client end of the loopback connection from
    host:port, one lsof call, or None when lsof fails. Another user's process (a tunnel daemon
    running as root) is never listed."""
    end = (f"[{host}]" if ":" in host else host) + f":{port}"
    try:
        out = subprocess.run([LSOF, "-nP", "-iTCP@" + end, "-Fpn"], capture_output=True, text=True,
                             timeout=5).stdout
    except Exception:
        return None
    pids, pid, me = set(), 0, False
    for line in out.splitlines():
        if line.startswith("p"):
            pid = int(line[1:]) if line[1:].isdigit() else 0
            me = me or pid == os.getpid()
        elif line.startswith("n" + end + "->"):
            pids.add(pid)
    return pids if me else None      # this server holds the other end, so a working lsof always lists it


def _agent_caller(conn, addr):
    """Why the client on this connection is (or may be) an agent, or "" when it is not."""
    host, port = addr[0], addr[1]
    if not (host.startswith("127.") or host == "::1"):
        return ""                    # not from this Mac: the bearer is all there is to check
    owners = _socket_owners(host, port)
    if owners is None:
        return "TapSpace could not tell which app sent it"
    if not owners:
        # no process of this user holds that end. Read after lsof, ESTABLISHED means it was open all
        # through the lookup, so another user's process holds it; anything else means it hung up
        try:
            if conn.getsockopt(socket.IPPROTO_TCP, socket.TCP_CONNECTION_INFO, 64)[0] == 4:
                return ""
        except (AttributeError, OSError):
            pass
        return "it hung up before TapSpace could check who sent it"
    procs = _procs()
    for pid in sorted(owners):
        chain = _chain(pid, procs)
        if not chain:
            return "TapSpace could not tell which app sent it"
        hit = next((x for x in chain if os.path.basename(procs[x][2]).casefold() in AGENT_COMMS), None)
        if hit:
            return f"it was sent from inside {os.path.basename(procs[hit][2])} (pid {hit})"
    return ""


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
    to end, is refused and nothing is signalled, except a row served Offline (_row_state: it claimed sessions,
    all of them ended, and it is not starting), which is cleared off the list. Each stop is logged to server.log (stdout)."""
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
    cleared = not (targets or refused) and _row_state(name, info, _chat_rows(name)) == "offline"
    print(time.strftime("%Y-%m-%d %H:%M:%S"), "stop", json.dumps(name, ensure_ascii=False),
          json.dumps({"stopped": stopped, "tmux": tmux, "failed": failed,
                      "refused": [{k: r[k] for k in ("pid", "why")} for r in refused],
                      **({"cleared": True} if cleared else {})}), flush=True)
    if cleared:
        # Offline: its sessions already ended, so there is nothing to signal; it leaves the list as a spin-down does
        _chat_add(name, "system", STOP_LINE)
        _stop_record(name, [])
        _bump("chat", "agents")
        return 200, {"ok": True, "ended": [], "cleared": True}
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


ORDER_MAX = 65536                    # bytes of a first order appended to a new master's prompt


def _spawn_master(folder, name, parent="", order="", restart=False):
    """Starts a master in folder under name (new-master.sh, detached) -> {agent, session, tmux, parent}.
    The server picks the session id and the tmux name, so the answer names them before the child exists.
    parent (a joined NAME) is stored on the row first, so the child is born under its lead; order is
    kept under data/orders/ and appended to its first prompt. restart: _restart_master already found
    the row of that NAME offline. new-master.sh always runs through a login shell (LOGIN)."""
    home = pathlib.Path.home().resolve()
    f = pathlib.Path(folder).expanduser().resolve()
    if not f.is_dir() or not (str(f) + "/").startswith(str(home) + "/"):
        raise ValueError("pick a folder in your home folder")
    name = name or f"Claude · {f.name} · {uuid.uuid4().hex[:4]}"
    if not _NAME.match(name):
        raise ValueError("bad name")
    if not restart and name in _agents() and _agent_state(_agents()[name], []) in ("here", "starting"):
        raise ValueError("an agent with that name is already running")
    if parent:
        if not _NAME.match(parent):
            raise ValueError("bad parent name")
        if parent not in _agents():
            raise ValueError("no lead by that name has joined")
        if parent == name:
            raise ValueError("an agent cannot lead itself")
    if len(order.encode()) > ORDER_MAX:
        raise ValueError("order: up to 64 KB")
    sid = str(uuid.uuid4())
    ts = "master-" + re.sub(r"[^A-Za-z0-9_-]", "-", f.name) + "-" + sid[:6]   # new-master.sh's own rule
    where = "~/" + str(f.relative_to(home)) if f != home else "~"
    _agent_touch(name, spawned=time.time(), where=where, **({"parent": parent} if parent else {}))
    extra = [parent] if parent else []
    if order:
        orders = DATA / "orders"
        orders.mkdir(mode=0o700, exist_ok=True)
        path = orders / f"{sid}.md"
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(order)
        extra = [parent, str(path)]
    log = DATA / "spawn.log"
    with log.open("a") as fh:
        subprocess.Popen([*LOGIN, str(ROOT / "new-master.sh"), str(f), name] + extra,
                         stdout=fh, stderr=subprocess.STDOUT, start_new_session=True,
                         env={**os.environ, "TAPSPACE_URL": f"http://127.0.0.1:{PORT}",
                              "TAPSPACE_MASTER_UUID": sid, "TAPSPACE_MASTER_TMUX": ts})
    return {"agent": name, "session": sid, "tmux": ts, "parent": parent or None}


# ---- restart a master after a crash or a reboot (POST /api/agents/restart) ----
# An Offline row (or a spun-down one) starts again under the same NAME, so its thread, look and lead stay.
# Muddi, the lead, comes back through its own muddi.sh; any other master through new-master.sh in its folder.
MUDDI_SH = pathlib.Path(os.environ.get("MUDDI_SH") or pathlib.Path.home() / "orchestration/muddi/muddi.sh")
SPAWN_SCRUB = ("CLAUDE_CODE_SESSION_ID", "CLAUDE_PID", "CLAUDE_CODE_MESSAGING_SOCKET",
               "CLAUDE_CODE_MESSAGING_TOKEN", "CODEX_COMPANION_SESSION_ID", "CODEX_COMPANION_TRANSCRIPT_PATH")
LOGIN = ("/bin/zsh", "-l", "-c", 'exec bash "$0" "$@"')     # + a script and its args: runs it with the login PATH


def _restart_master(name):
    """POST /api/agents/restart -> (http code, answer). Refused unless no claimed session of NAME still runs
    (_offline) and its row is not already starting or switching; that check and the row's edit share one
    _lock hold, so two restarts never both launch. A launch that fails puts the row back as it was.
    Both scripts run through a login shell (LOGIN), so a server launched at login with launchd's bare
    PATH still hands them Homebrew and ~/.local/bin. Each restart is logged to stdout."""
    info = _agents().get(name) if _NAME.match(name) else None
    if not info:
        return 404, {"ok": False, "error": "no agent by that name is in the list"}
    muddi = info.get("role") == "lead" and name == "Muddi"
    if muddi and not MUDDI_SH.is_file():
        return 500, {"ok": False, "error": f"muddi.sh not found at {MUDDI_SH}"}
    offline = _offline(name)                 # never under _lock
    keys = ("stopped", "stopped_sessions", "spawned")
    was = {}

    def change(row):
        if not offline:
            raise ValueError("it is still running")
        if _agent_state(row, []) in ("starting", "switching"):
            raise ValueError("it is already starting")
        was.update((k, row[k]) for k in keys if k in row)
        row.pop("stopped", None)
        row.pop("stopped_sessions", None)
        row["spawned"] = time.time()

    def undo(row):
        for k in keys:
            row.pop(k, None)
        row.update(was)
    try:
        _agent_edit(name, change)
    except ValueError as e:
        return 409, {"ok": False, "error": str(e)}
    try:
        if muddi:
            env = {k: v for k, v in os.environ.items() if k not in SPAWN_SCRUB}
            env["TAPSPACE_URL"] = f"http://127.0.0.1:{PORT}"
            with (DATA / "spawn.log").open("a") as fh:
                subprocess.Popen([*LOGIN, str(MUDDI_SH)],
                                 stdout=fh, stderr=subprocess.STDOUT, start_new_session=True, env=env)
            out = {"ok": True, "agent": name, "how": "muddi"}
        else:
            home = pathlib.Path.home().resolve()
            f = pathlib.Path(info.get("where") or "~").expanduser().resolve()
            if not f.is_dir() or not (str(f) + "/").startswith(str(home) + "/"):
                f = home
            parent = info.get("parent")
            parent = parent if isinstance(parent, str) and parent != name and parent in _agents() else ""
            got = _spawn_master(str(f), name, parent=parent, restart=True)
            out = {"ok": True, "agent": name, "how": "master", "session": got["session"], "tmux": got["tmux"]}
    except Exception as e:
        _agent_edit(name, undo, seen=False)
        return 500, {"ok": False, "error": f"could not start it: {e}"}
    print(time.strftime("%Y-%m-%d %H:%M:%S"), "restart", json.dumps(name, ensure_ascii=False),
          json.dumps({k: out[k] for k in out if k != "ok"}, ensure_ascii=False), flush=True)
    _bump("agents")
    return 200, out


# ---- taps: the reviewer's Flightdeck journeys as the board lists them (board.py), each with its issue's GitHub residue ----
TAPS = {"me": "", "items": [], "fetched": 0.0, "error": "", "outcomes": {}}
_taps_kick = threading.Event()
HOT = {}                    # tap key -> refresh it every few seconds until this time
TAP_HOT_EVERY = 6
TAP_RETRY = 60              # a failed read waits this long; so does an idle _taps_loop (a done item leaves within it)
BOARD = None                # the board feed (board.Board): __main__ starts one when board.enabled(); tests set a fake
_JOURNEY = re.compile(r"^https://github\.com/([^/]+/[^/]+)/issues/(\d+)$")
_PHASE_OF = {"proposed": "proposed", "en route": "enroute", "holding": "holding", "landed": "landed", "done": "done"}
_ISSUES = {}                # tap key -> its issue's residue (_residue; _unread until the first read)
_CANON = {}                 # lower-cased owner/repo -> GitHub's spelling, from an issue's own url (R5)
_TRIED = {}                 # owner/repo#n as read -> when its issue was last read or tried
_ON_BOARD = set()           # the tap keys the board listed at the last compose (a key gone from it left the board)
_DONE = {}                  # tap key -> (until, its done item): it left the board and is served until then (R11)
_LEAVING = {}               # tap key -> (since, its last item): served unchanged until the board says how it ended
_SETTLE = {}                # an Accept card's tap neither served nor in outcomes -> when the board was last asked (R13)
DONE_LINGER = 600
LEAVE_WAIT = 60             # a leaving tap whose ending the board has not told by then just leaves (A1.4)
OUTCOMES_KEEP, OUTCOMES_DAYS = 500, 30
VIA = DATA / "tap-via.json"  # comment url -> the agent that sent it for the reviewer
SAID = DATA / "tap-said.json"  # comment url -> when the reviewer sent it himself through TapSpace (say with no via)


def _via():
    try:
        return json.loads(VIA.read_text())
    except Exception:
        return {}


def _said():
    try:
        return json.loads(SAID.read_text())
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


def _has(names, family):
    """Any label name in this family (exact, any case): flightdeck:fly:csat is attribution, not fly."""
    fam = {f.casefold() for f in family}
    return any(str(n).casefold() in fam for n in names)


def _epoch(iso):
    """GitHub's UTC stamp (2026-10-15T19:04:05Z) as epoch seconds."""
    return calendar.timegm(time.strptime(iso[:19], "%Y-%m-%dT%H:%M:%S"))


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


PHASE_WORD = {"proposed": "Proposed", "enroute": "Flying", "holding": "Needs you", "landed": "Landed", "done": "Done"}


def _undash(s):
    """Long dashes out of what clients show (house rule): a spaced one reads as a comma."""
    return re.sub(r"[\u2014\u2013]", "-", re.sub(r"\s+[\u2014\u2013]\s+", ", ", s))


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
    """The question a colleague pilot put to me in a comment (read only while needs-reply is on): the newest comment not by me that mentions @me."""
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


def _residue(key, iss):
    """What a tap's issue adds to its board row (R9: the board carries none of it): the thread (who, words, when, the
    seen mark, its url, the agent that sent it), replying, when it was filed, closed and last spoken in, the fly and
    needs-reply labels, a colleague pilot's question (only while needs-reply is on: the board shows that wait as en
    route), the proof images in its body, and its title (R8's last resort)."""
    me, via = _me(), _via()
    labels = [l["name"] for l in iss["labels"]]
    msgs = [{"who": "you" if c["author"]["login"] == me else "tap", "body": c["body"], "ts": c["createdAt"],
             "seen": any(g["content"] == "EYES" for g in c.get("reactionGroups") or []), "url": c["url"],
             "via": via.get(c["url"], "")} for c in iss["comments"]]
    is_open = iss["state"] == "OPEN"
    needs = is_open and _has(labels, TAP_NEEDS_REPLY)
    return {"title": iss["title"], "url": iss["url"], "messages": msgs,
            # the tap has your latest message (👀) and has not answered yet
            "replying": is_open and bool(msgs) and msgs[-1]["who"] == "you" and msgs[-1]["seen"],
            "created": iss["createdAt"], "closed": iss.get("closedAt"),
            "last": msgs[-1]["ts"] if msgs else iss["createdAt"],
            "fly_requested": _has(labels, TAP_FLY), "needs_reply": needs,
            "ask": _ask(iss, me) if needs else None, "proof": _proof(key, iss["body"])}


def _utc():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _journey(key):
    """A tap's journey as the board names it: its issue url, lower-cased (R5)."""
    return ("https://github.com/" + key.replace("#", "/issues/")).lower()


def _unread(key):
    """A tap's residue until its issue is first read: no thread, labels or proof yet, its url from its key, last
    spoken in when TapSpace first saw it (so it sorts as new)."""
    return {"unread": True, "title": "", "url": "https://github.com/" + key.replace("#", "/issues/"), "messages": [],
            "replying": False, "created": "", "closed": None, "last": _utc(), "fly_requested": False,
            "needs_reply": False, "ask": None, "proof": []}


def _word(label, phase):
    """A tap's word (R7): the board's label without its leading glyph, in the board's own words and case;
    PHASE_WORD's when nothing is left."""
    return _undash(re.sub(r"^\W+", "", label or "").strip()) or PHASE_WORD[phase]


def _outcome_of(r):
    """How a done journey ended (R11), from its board row or its read answer: shipped, aborted or closed."""
    said = {r.get("status"), r.get("event")}
    return "shipped" if said & {"accepted", "shipped"} else "aborted" if said & {"aborted", "discarded"} else "closed"


def _item(key, row, gh, prev):
    """One served tap (R6 to R10): the board row's phase, word, goals, title and question over the tap's GitHub
    residue gh; prev is the tap as last served. A fog row (unread, or no phase or label the board names) keeps
    prev's phase, word, goals, question and outcome. None while there is nothing true to show: a first row in fog,
    or no title from the board or GitHub (never the key)."""
    phase = _PHASE_OF.get(row.get("phase"))
    fog = row.get("read") is False or not phase or not row.get("label")
    if fog and not prev:
        return None
    if fog:
        phase, word, goals, outcome = prev["phase"], prev["phase_word"], prev["goals"], prev.get("outcome")
    else:
        word = _word(row["label"], phase)
        goals = [{"text": g.get("text") or "", "done": bool(g.get("checked"))} for g in row.get("goalList") or []
                 if not g.get("standard") and not g.get("cancelled")]
        outcome = _outcome_of(row) if phase == "done" else None
    title = row.get("title") or row.get("headline") or gh["title"]
    if not title:
        return None
    done, a = phase == "done", row.get("ask") or {}
    if done:
        ask = None
    elif fog:
        ask = prev["ask"]                       # a fog row carries no ask: keep the question
    elif a.get("question"):
        opts = ", ".join(o["label"] for o in a.get("options") or [] if o.get("label"))
        text = _undash(a["question"] + (f" ({opts})" if opts else ""))
        old = prev and prev.get("ask") or {}
        ask = {"text": text, "url": gh["url"], "at": old["at"] if old.get("text") == text else _utc()}
    else:
        ask = gh["ask"]                         # a colleague pilot's question: set only while needs-reply is on
    repo, n = key.split("#")
    return {"key": key, "repo": repo, "number": int(n), "initial": _initial(repo.split("/")[-1]), "title": title,
            "url": gh["url"],
            "status": "landed" if done else "needs-you" if phase == "holding" or gh["needs_reply"] else
                      "boarding" if phase == "proposed" else "flying",
            "goals": goals, "messages": gh["messages"], "replying": gh["replying"] and not done,
            "created": gh["created"], "closed": gh["closed"], "last": gh["last"], "phase": phase,
            "phase_word": word, "ask": ask, "fly_requested": gh["fly_requested"], "proof": gh["proof"],
            **({"outcome": outcome} if done else {})}


_ORDER = {"needs-you": 0, "flying": 1, "boarding": 2, "landed": 3}


def _publish(items, error=""):
    """Serves the composed items, needs you first, then flying, boarding and done (newest first within each), and
    the board's plain sentence (R12). Any change wakes every client: the fleet on /api/agents shows taps too."""
    items.sort(key=lambda t: t["last"], reverse=True)
    items.sort(key=lambda t: _ORDER[t["status"]])
    old = (TAPS["items"], TAPS["error"])
    TAPS.update(items=items, fetched=time.time(), error=error)
    if (items, error) != old:
        _bump("taps", "agents")


def _kept(m):
    """tap_outcomes as kept (R11): the newest OUTCOMES_KEEP of the last OUTCOMES_DAYS."""
    cut = time.time() - OUTCOMES_DAYS * 86400
    return dict(sorted(((k, v) for k, v in m.items() if v.get("at", 0) > cut),
                       key=lambda kv: kv[1]["at"])[-OUTCOMES_KEEP:])


def _outcome(key, o):
    """Keeps how a tap ended in the state file (tickets.json tap_outcomes: {key: {outcome, at}}, _kept) and serves
    the map as /api/taps outcomes, so a card about the tap stays settled after its done item leaves (R11, R13).
    o None forgets it: the tap is on the board again and open, and outcomes never names a listed open tap (A1.3)."""
    if TAPS["outcomes"].get(key) == o:
        return
    with _lock:
        state = _load()
        m = {k: v for k, v in (state.get("tap_outcomes") or {}).items() if k != key}
        state["tap_outcomes"] = kept = _kept({**m, **({key: {"outcome": o, "at": time.time()}} if o else {})})
        _save(state)
    TAPS["outcomes"] = {k: v["outcome"] for k, v in kept.items()}
    _bump("taps", "agents")


def _read_issue(t):
    """One gh read of the issue t names (owner/repo#n, any case): learns the repo's GitHub spelling from the issue's
    own url (R5) and keeps its residue under GitHub's key. A pilot comment that came since the last read pushes; a
    first read never does, so neither a boot, a new tap nor a reconnect pushes old comments (R14). A failed read
    changes nothing: the tap keeps what it had, and _TRIED spaces the next try. So does an issue GitHub answers
    from another repo or number (moved or renamed): only the case of a name is learned, never a different tap."""
    repo, n = t.split("#")
    try:
        iss = _issue(repo, n)
        canon, num = _JOURNEY.match(iss["url"]).groups()
        if canon.lower() != repo.lower() or num != n:
            return
        new = _residue(f"{canon}#{n}", iss)
    except Exception:
        return
    _CANON[repo.lower()] = canon
    key = f"{canon}#{n}"
    old = _ISSUES.get(key)
    _ISSUES[key] = new
    said = [m for m in new["messages"][len(old["messages"]):] if m["who"] == "tap"] \
        if old and not old.get("unread") else []
    if said:
        _push(iss["title"], said[-1]["body"], key=key)


def _compose():
    """One pass of _taps_loop, the only writer of TAPS (R14). BOARD's rows over each tap's GitHub residue become the
    served items (_item). A row the board itself lists as done is a done item at once, at boot too (A1.4). A tap
    that left the board is served as it was until the board says how it ended, at most LEAVE_WAIT; done, it lingers
    DONE_LINGER as a done item (R11); else it just leaves, with no ending invented (A1.4). Then at most one slow
    call: a board read of how a leaving tap ended, else an issue read (a repo whose spelling is not known yet, a tap
    not read yet, a hot one), else a board read for an Accept card's tap nobody saw end (R13). After a slow call it
    kicks itself, so a board change waits behind at most one call (a gh read, normally 1 to 2 s; a board read, at
    most 8 s)."""
    snap = BOARD.snapshot()
    if not snap["loaded"]:
        if TAPS["error"] != snap["error"]:
            TAPS["error"] = snap["error"]
            _bump("taps")
        return
    now = time.time()
    for key in [k for k, until in list(HOT.items()) if until < now]:
        HOT.pop(key, None)
    live, unkeyed = {}, []
    for journey, row in snap["rows"].items():
        m = _JOURNEY.match(journey.lower())
        if m and m[1] in _CANON:
            live[f"{_CANON[m[1]]}#{m[2]}"] = row
        elif m:
            unkeyed.append(f"{m[1]}#{m[2]}")                    # never served under the board's lower case (R5)
    prev = {t["key"]: t for t in TAPS["items"]}
    for key in _ON_BOARD - live.keys():                         # left the board since the last pass
        if key in prev:
            if prev[key]["phase"] == "done":
                _DONE[key] = (now + DONE_LINGER, prev[key])
            else:
                _LEAVING[key] = (now, prev[key])
    _ON_BOARD.clear()
    _ON_BOARD.update(live)
    for key in [k for k, (until, _) in list(_DONE.items()) if until <= now or k in live]:
        del _DONE[key]
    for key in [k for k, (since, _) in list(_LEAVING.items()) if now - since >= LEAVE_WAIT or k in live]:
        del _LEAVING[key]
    for key in [k for k in list(_SETTLE) if k in live or k in _DONE or k in TAPS["outcomes"]]:
        _SETTLE.pop(key, None)
    for key in [k for k in _ISSUES if k not in live and k not in _LEAVING]:
        del _ISSUES[key]
    items = []
    for key, row in live.items():
        if key not in _ISSUES:
            _ISSUES[key] = _unread(key)
        was = prev.get(key)
        it = _item(key, row, _ISSUES[key], was)
        if not it:
            continue
        if was and (it["phase"], it["phase_word"], it["goals"]) != (was["phase"], was["phase_word"], was["goals"]):
            _hot(key, 0.15)                                     # its board row moved: read its issue again (R9)
        _outcome(key, it.get("outcome"))                        # kept when done; forgotten when open again (A1.3)
        items.append(it)
    if TAPS["fetched"] or all(t in _TRIED for t in unkeyed):    # the first list waits until each repo was tried once
        _publish(items + [it for _, it in _LEAVING.values()] + [it for _, it in _DONE.values()], snap["error"])
    due = lambda t, wait: now - _TRIED.get(t, 0) >= wait
    if _LEAVING:                                                # first: it is served as it was until this answers
        key = next(iter(_LEAVING))
        _, last = _LEAVING.pop(key)
        r = BOARD.read(_journey(key))
        if r and r.get("phase") == "done":                     # else it just leaves: no ending is invented (A1.4)
            o = _outcome_of(r)
            _outcome(key, o)
            _DONE[key] = (time.time() + DONE_LINGER, dict(
                last, phase="done", phase_word=_word((r.get("board") or {}).get("label"), "done"), status="landed",
                ask=None, replying=False, outcome=o))
    elif (t := next((t for t in unkeyed if due(t, TAP_RETRY)), None) or
            next((k for k in live if _ISSUES[k].get("unread") and due(k, TAP_RETRY)), None) or
            next((k for k in live if k in HOT and due(k, TAP_HOT_EVERY)), None)):
        _TRIED[t] = now
        _read_issue(t)
    elif (key := next((k for k, at in list(_SETTLE.items()) if now - at >= DONE_LINGER), None)):
        _SETTLE[key] = now
        r = BOARD.read(_journey(key))
        if r and r.get("phase") == "done":
            _outcome(key, _outcome_of(r))
    else:
        return
    _taps_kick.set()


TAP_FLY_LABEL = {"color": "0e8a16", "description": "Flightdeck: fly this journey without a tap (capped, never auto-accepted)"}


def _tap_act(key, act):
    """POST /api/taps/fly | accept -> (http code, answer), through gh as the reviewer. fly adds the
    fly label (creating it in that repo when missing); accept closes the issue as completed, the
    reviewer's sign-off. A repeat answers already and writes nothing."""
    m = re.match(r"^([\w.-]+/[\w.-]+)#(\d+)$", str(key or ""))
    if not m:
        return 400, {"error": "bad tap key"}
    repo, n = m.group(1), m.group(2)
    try:
        iss = _issue(repo, n)
        if not _is_mine(iss):
            return 403, {"error": "not one of your taps"}
        closed = iss["state"] != "OPEN"
        if act == "fly":
            if closed:
                return 409, {"error": "this tap is closed"}
            if _has([l["name"] for l in iss["labels"]], TAP_FLY):
                return 200, {"ok": True, "already": True}
        elif closed:
            if str(iss.get("stateReason") or "").upper() == "COMPLETED":
                return 200, {"ok": True, "already": True}
            return 409, {"error": "this tap was closed another way"}
        who = _gh("api", "user", "--jq", ".login").strip()
        if who.casefold() != _me().casefold():
            return 409, {"error": f"gh is signed in as {who}, not {_me()}"}
        if act == "fly":
            try:
                _gh("api", f"repos/{repo}/labels/{TAP_FLY[0]}", "--jq", ".name")
            except RuntimeError as e:
                if "404" not in str(e):
                    raise
                try:
                    _gh("label", "create", TAP_FLY[0], "--repo", repo, "--color", TAP_FLY_LABEL["color"],
                        "--description", TAP_FLY_LABEL["description"])
                except RuntimeError as e2:
                    if "already exists" not in str(e2).lower():
                        raise
            _gh("issue", "edit", n, "--repo", repo, "--add-label", TAP_FLY[0])
        else:
            _gh("issue", "close", n, "--repo", repo, "--reason", "completed")
    except Exception as e:
        return 502, {"error": str(e)}
    _inbox({"kind": f"tapspace-tap-{act}", "tap": key})
    _hot(key, 0.15)
    return 200, {"ok": True}


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
    """Composes the taps whenever the board changes (BOARD's on_change), a tap is talked to (_hot) or a refresh asks;
    every TAP_HOT_EVERY while a tap is hot, else every TAP_RETRY."""
    while True:
        try:
            _compose()
        except Exception as e:          # a fault in this file: board.py and the gh reads fail quietly on their own
            print(time.strftime("%Y-%m-%d %H:%M:%S"), "taps", repr(e)[:240], flush=True)
        _taps_kick.wait(TAP_HOT_EVERY if HOT else TAP_RETRY)
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
            chat = _chat_rows()
            rows = _agent_rows(chat)
            self._json({"agents": rows, "links": links.rows({r["name"] for r in rows}),
                        "fleet": _fleet(rows, TAPS["items"], chat), "focus": _focus(chat, TAPS["items"])})
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
            # an agent's poll: joins the space under its name and gets the messages it has not answered, plus
            # any it was never handed (heard: a card it posted in between never hides one)
            name = self._q("agent")
            if not _NAME.match(name):
                self._json({"error": "bad agent name"}, 400)
                return
            _unstop(name, self._who())
            rows = _chat_rows(name)
            pend = _chat_pending(rows, (_agents().get(name) or {}).get("heard"))
            # everything this poll hands over, and everything the agent already answered, is heard now
            _agent_touch(name, listen=time.time(), where=self._q("where")[:80],
                         heard=max((r["id"] for r in rows if r["role"] == "you"), default=0))
            _claim(self._who(), name, "listen")
            cards = {r["id"]: r for r in rows if r.get("role") == "master" and isinstance(r.get("card"), dict)}
            for r in pend:
                r["files"] = [str(DATA / u.lstrip("/")) for u in r.get("images", [])]
                c = cards.get((r.get("answer") or {}).get("card"))
                if c:
                    r["answer"] = _heard_answer(r["answer"], c)
            self._json({"pending": pend})
            return
        if path == "/api/taps":
            self._json({k: TAPS[k] for k in ("me", "items", "fetched", "error", "outcomes")})
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
                         "/api/agents/progress", "/api/agents/read", "/api/agents/stop", "/api/agents/restart",
                         "/api/taps/fly", "/api/taps/accept", "/api/agents/lead", "/api/agents/adopt"):
            # these speak for the reviewer or write agent state: same-origin JSON only, so no
            # other page can drive them through the browser
            origin = self.headers.get("Origin")
            if (self.headers.get("content-type") or "").split(";")[0] != "application/json" or \
               (origin and origin not in (f"http://127.0.0.1:{PORT}", f"http://localhost:{PORT}")):
                self._json({"error": "forbidden"}, 403)
                return
            if self.path == "/api/agents/new":
                # parent and order come only from this Mac (tap.py new): the phone starts plain masters
                parent, order = str(p.get("parent") or "").strip(), str(p.get("order") or "")
                if (parent or order) and not self._local():
                    self._json({"error": "parent and order only from this Mac"}, 403)
                    return
                try:
                    out = _spawn_master(str(p.get("folder") or ""), str(p.get("name") or "").strip(),
                                        parent=parent, order=order)
                except Exception as e:
                    self._json({"error": str(e)}, 400)
                    return
                self._json({"ok": True, **out})
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
            if self.path == "/api/agents/restart":
                # the reviewer starts an Offline master again (after a crash or a reboot) under the same NAME
                try:
                    code, out = _restart_master(str(p.get("name") or p.get("agent") or "").strip())
                except Exception as e:
                    code, out = 400, {"ok": False, "error": str(e)}
                self._json(out, code)
                return
            if self.path in ("/api/taps/fly", "/api/taps/accept"):
                # the reviewer flies a proposed tap or signs a landed one off; the phone may (bearer), like say.
                # Never an agent: not a tap.py call from inside a Claude session, and not anything whose
                # socket belongs to a process under a claude or codex process (_agent_caller).
                act, w = self.path.rsplit("/", 1)[1], self._who()
                why = "it came through tap.py inside a Claude session" if (w.get("session") or w.get("pid")) \
                    else _agent_caller(self.connection, self.client_address)
                if why:
                    self._json({"error": f"{act} is Hayden's own {'sign-off' if act == 'accept' else 'button'}: {why}"},
                               403)
                    return
                # an Accept pressed on a card (its action) answers that card too, once GitHub has it
                card = None
                if act == "accept" and p.get("card") is not None:
                    try:
                        card = _accept_card(p["card"], p.get("key"))
                    except ValueError as e:
                        self._json({"error": str(e)}, 400)
                        return
                code, out = _tap_act(p.get("key"), act)
                if code == 200 and card:
                    out["message"] = _accepted(card)
                self._json(out, code)
                return
            if self.path in ("/api/agents/lead", "/api/agents/adopt"):
                # Muddi takes the lead, or puts a master under a lead (local only)
                if not self._local():
                    self._json({"error": "only from this Mac"}, 403)
                    return
                if self.path == "/api/agents/lead":
                    name = str(p.get("name") or p.get("agent") or "").strip()
                    if not _NAME.match(name):
                        self._json({"error": "bad agent name"}, 400)
                        return
                    _set_lead(name)
                    self._json({"ok": True, "lead": name})
                    return
                lead, child = str(p.get("lead") or "").strip(), str(p.get("child") or "").strip()
                if not _NAME.match(child) or (lead and not _NAME.match(lead)):
                    self._json({"error": "bad agent name"}, 400)
                    return
                code, out = _adopt(lead, child)
                self._json(out, code)
                return
            body = (p.get("body") or (str(p.get("text") or "") if self.path == "/api/taps/say" else "")
                    or "").strip()
            chatting = self.path in ("/api/chat", "/api/chat/reply")
            # an agent's card (reply) or the reviewer's answer to one (chat): the server writes its body
            own = {"/api/chat/reply": "card", "/api/chat": "answer"}.get(self.path)
            if not body and not (chatting and (p.get("images") or (own and p.get(own) is not None))):
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
                if own == "card" and p.get("card") is not None:
                    if not self._local():          # like /api/agents/progress: a card is a master's, from this Mac
                        self._json({"error": "only from this Mac"}, 403)
                        return
                    code, out = _send_card(name, p, self._who())
                    self._json(out, code)
                    return
                if own == "answer" and p.get("answer") is not None:
                    code, out = _send_answer(name, p)
                    self._json(out, code)
                    return
                try:
                    images = _chat_images(p)
                except Exception as e:
                    code, out = _refusal(e)
                    self._json(out, code)
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
            with _lock:
                if via:
                    v = _via()
                    v[url] = via
                    VIA.write_text(json.dumps(v, indent=1))
                else:       # his own words from a TapSpace surface: the only tap comments that move focus (_focus)
                    s = _said()
                    s[url] = time.time()
                    SAID.write_text(json.dumps(s, indent=1))
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
                t.pop("archived", None)          # a new version is a new ask: back in the space
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
    TAPS["outcomes"] = {k: v["outcome"] for k, v in _kept(_load().get("tap_outcomes") or {}).items()}
    threading.Thread(target=_agents_watch, daemon=True).start()
    threading.Thread(target=_scan_sessions, daemon=True).start()
    import board                        # the live server's tap source (R4); tests never import it
    if board.enabled():
        BOARD = board.Board(base=os.environ.get("TAPSPACE_BOARD_URL") or None,
                            key=os.environ.get("TAPSPACE_BOARD_KEY") or None, on_change=_taps_kick.set)
        BOARD.start()
        threading.Thread(target=_taps_loop, daemon=True).start()
        threading.Thread(target=_watch_loop, daemon=True).start()
    links.start(DATA, _bump, titles=lambda: {n: _title(i) for n, i in _agents().items()})
    print(f"TapSpace serving on http://127.0.0.1:{PORT}")
    ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
