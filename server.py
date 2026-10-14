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
authored with the tap label (TAPSPACE_TAP_LABEL, default nava), read and
messaged through gh as you. Taps are scoped by issue AUTHOR, never comment
author, so nobody else's taps ever show up here.

Run:  python3 server.py     (or: python3 tap.py up)
URL:  http://127.0.0.1:4747
"""
import base64
import json
import os
import shutil
import subprocess
import pathlib
import re
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# media a version can carry; videos stream with Range support so the space can scrub
_CTYPES = {".png": "image/png", ".mp4": "video/mp4", ".mov": "video/quicktime"}
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
PORT = int(os.environ.get("TAPSPACE_PORT", "4747"))

# taps = the reviewer's own agent issues on GitHub (authored by them, tap label)
TAP_LABEL = os.environ.get("TAPSPACE_TAP_LABEL", "nava")
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


def _agent_touch(name, **extra):
    with _lock:
        a = _agents()
        row = a.get(name) or {"name": name, "first": time.time()}
        row.update(extra)
        row["seen"] = time.time()
        a[name] = row
        tmp = AGENTS.with_suffix(".tmp")
        tmp.write_text(json.dumps(a, indent=1))
        tmp.replace(AGENTS)


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


def _chat_add(agent, role, body):
    with _lock:
        rows = _chat_rows()
        row = {"id": (rows[-1]["id"] + 1) if rows else 1, "agent": agent,
               "role": role, "body": body, "ts": time.time()}
        with CHAT.open("a") as fh:
            fh.write(json.dumps(row) + "\n")
    return row


def _chat_pending(rows):
    """Reviewer messages after the agent's last reply (rows of ONE agent)."""
    last = max((r["id"] for r in rows if r["role"] == "master"), default=0)
    return [r for r in rows if r["role"] == "you" and r["id"] > last]


def _agent_state(info, rows):
    if _chat_pending(rows):
        return "thinking"
    return "here" if time.time() - info.get("listen", 0) < 30 else "away"


def _agent_rows():
    out = []
    allrows = _chat_rows()
    for name, info in _agents().items():
        rows = [r for r in allrows if r.get("agent") == name]
        last = rows[-1]["ts"] if rows else info.get("seen", 0)
        if time.time() - max(last, info.get("seen", 0)) > 3 * 86400:
            continue            # quiet for 3 days: off the rail, history kept
        said = [r["ts"] for r in rows if r["role"] == "master"]
        out.append({"name": name, "state": _agent_state(info, rows),
                    "seen": info.get("seen", 0), "first": info.get("first", 0),
                    "where": info.get("where", ""), "last": last, "count": len(rows),
                    "last_master": said[-1] if said else 0})
    rank = {"thinking": 0, "here": 1, "away": 2}
    out.sort(key=lambda r: -r["last"])
    out.sort(key=lambda r: rank[r["state"]])
    return out


# ---- taps (the reviewer's own pilot issues, read through gh) ----
TAPS = {"me": "", "items": [], "fetched": 0.0, "error": ""}
_taps_kick = threading.Event()


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


def _issue(repo, number):
    return json.loads(_gh("issue", "view", str(number), "--repo", repo, "--json",
                          "author,title,body,labels,state,comments,url,createdAt,closedAt"))


def _is_mine(iss):
    return iss["author"]["login"] == _me() and \
        any(l["name"] == TAP_LABEL for l in iss["labels"])


def _tap(repo, number, iss):
    me = _me()
    labels = [l["name"] for l in iss["labels"]]
    msgs = []
    for c in iss["comments"]:
        mine = c["author"]["login"] == me
        seen = any(g["content"] == "EYES" for g in c.get("reactionGroups") or [])
        msgs.append({"who": "you" if mine else "tap", "body": c["body"],
                     "ts": c["createdAt"], "seen": seen, "url": c["url"]})
    tap_msgs = [m for m in msgs if m["who"] == "tap"]
    if iss["state"] != "OPEN":
        status = "landed"
    elif "needs-reply" in labels:
        status = "needs-you"
    elif tap_msgs:
        status = "flying"
    else:
        status = "boarding"
    return {"key": f"{repo}#{number}", "repo": repo, "number": number,
            "title": iss["title"], "url": iss["url"], "status": status,
            "goals": _goals(iss["body"]), "messages": msgs,
            "created": iss["createdAt"], "closed": iss.get("closedAt"),
            "last": msgs[-1]["ts"] if msgs else iss["createdAt"]}


def _refresh_taps():
    me = _me()
    found = json.loads(_gh("search", "issues", "--label", TAP_LABEL, "--author", me,
                           "--limit", "60", "--json", "repository,number,state,closedAt"))
    cutoff = time.time() - 14 * 86400
    items = []
    for f in found:
        if f["state"] != "open" and f.get("closedAt"):
            closed = time.mktime(time.strptime(f["closedAt"][:19], "%Y-%m-%dT%H:%M:%S"))
            if closed < cutoff:
                continue
        repo = f["repository"]["nameWithOwner"]
        iss = _issue(repo, f["number"])
        if _is_mine(iss):
            items.append(_tap(repo, f["number"], iss))
    order = {"needs-you": 0, "flying": 1, "boarding": 2, "landed": 3}
    items.sort(key=lambda t: t["last"], reverse=True)       # newest first...
    items.sort(key=lambda t: order[t["status"]])           # ...within each status
    TAPS.update({"items": items, "fetched": time.time(), "error": ""})


def _taps_loop():
    while True:
        try:
            _refresh_taps()
        except Exception as e:
            TAPS["error"] = str(e)
        _taps_kick.wait(TAP_REFRESH)
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

    def do_GET(self):
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
                        "now": time.time()})
            return
        if path == "/api/agents":
            self._json({"agents": _agent_rows()})
            return
        if path == "/api/chat":
            self._json({"messages": _chat_rows(self._q("agent"))})
            return
        if path == "/api/chat/listen":
            # an agent's poll: joins the space under its name, gets its unanswered messages
            name = self._q("agent")
            if not _NAME.match(name):
                self._json({"error": "bad agent name"}, 400)
                return
            _agent_touch(name, listen=time.time(), where=self._q("where")[:80])
            self._json({"pending": _chat_pending(_chat_rows(name))})
            return
        if path == "/api/taps":
            self._json({k: TAPS[k] for k in ("me", "items", "fetched", "error")})
            return
        if path == "/api/state":
            with _lock:
                state = _load()
            live = [t for t in state["tickets"] if not t.get("archived")]
            self._json({"tickets": live, "now": time.time(),
                        "rig": _rig((SPACE / "index.html").read_bytes())})
            return
        if path.startswith("/api/ticket/"):
            tid = path.rsplit("/", 1)[1]
            with _lock:
                t = _find(_load(), tid)
            if t and not t.get("archived"):
                if t["status"] == "waiting":
                    _touch(t.get("campaign"), t["agent"], "waiting on your tap",
                           t["title"], "waiting")
                else:
                    _touch(t.get("campaign"), t["agent"], "checking in", t["title"])
            self._json(t or {"error": "not found"}, 200 if t else 404)
            return
        if path.startswith("/shots/"):
            f = (DATA / path.lstrip("/")).resolve()
            if not str(f).startswith(str(SHOTS)) or not f.exists():
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
        try:
            n = int(self.headers.get("content-length", 0))
            p = json.loads(self.rfile.read(n).decode())
        except Exception:
            self._json({"error": "bad json"}, 400)
            return

        if self.path in ("/api/chat", "/api/chat/reply", "/api/taps/say"):
            # these speak for the reviewer: same-origin JSON only, so no other
            # page can drive them through the browser
            origin = self.headers.get("Origin")
            if (self.headers.get("content-type") or "").split(";")[0] != "application/json" or \
               (origin and origin not in (f"http://127.0.0.1:{PORT}", f"http://localhost:{PORT}")):
                self._json({"error": "forbidden"}, 403)
                return
            body = (p.get("body") or "").strip()
            if not body:
                self._json({"error": "empty"}, 400)
                return
            if self.path in ("/api/chat", "/api/chat/reply"):
                name = (p.get("agent") or "").strip()
                if not _NAME.match(name):
                    self._json({"error": "bad agent name"}, 400)
                    return
                if self.path == "/api/chat":
                    if name not in _agents():
                        self._json({"error": "no agent by that name has joined"}, 404)
                        return
                    row = _chat_add(name, "you", body)
                    _inbox({"kind": "tapspace-chat", "agent": name, "id": row["id"], "message": body})
                else:
                    _agent_touch(name, listen=time.time())
                    row = _chat_add(name, "master", body)
                self._json({"ok": True, "message": row})
                return
            key = p.get("key", "")
            m = re.match(r"^([\w.-]+/[\w.-]+)#(\d+)$", key)
            if not m:
                self._json({"error": "bad tap key"}, 400)
                return
            try:
                if not _is_mine(_issue(m.group(1), m.group(2))):
                    self._json({"error": "not one of your taps"}, 403)
                    return
                url = _gh("issue", "comment", m.group(2), "--repo", m.group(1),
                          "--body", body).strip()
            except Exception as e:
                self._json({"error": str(e)}, 502)
                return
            _inbox({"kind": "tapspace-tap-say", "tap": key, "message": body, "url": url})
            _taps_kick.set()
            self._json({"ok": True, "url": url})
            return

        if self.path == "/api/taps/refresh":
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
                })
                _save(state)
            _touch(p.get("campaign"), p["agent"], "posted", p["title"])
            self._json({"ok": True, "id": tid, "version": 1})
            return

        if self.path == "/api/update":
            tid = p.get("id", "")
            media = _media_from(p)
            if media is None:
                self._json({"error": "missing image_b64 or video_b64 (video_ext mp4|mov)"}, 400)
                return
            kind, ext, b64key = media
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
                _save(state)
            _touch(t.get("campaign"), t["agent"], "iterating",
                   p.get("note", "") or t["title"])
            self._json({"ok": True, "id": tid, "version": v})
            return

        if self.path == "/api/decide":
            tid = p.get("id", "")
            action = p.get("action", "")
            if action not in ("approve", "change"):
                self._json({"error": "action must be approve|change"}, 400)
                return
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
                _purge_ticket_videos(t, up_to=t["version"])
                _save(state)
            _touch(t.get("campaign"), t["agent"],
                   "approved" if action == "approve" else "got your notes",
                   t["title"], "decision")
            _inbox({
                "kind": "tapspace-decision",
                "ticket": tid,
                "agent": t["agent"],
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
    threading.Thread(target=_scan_sessions, daemon=True).start()
    threading.Thread(target=_taps_loop, daemon=True).start()
    print(f"TapSpace serving on http://127.0.0.1:{PORT}")
    ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
