#!/usr/bin/env python3
"""tap — the agent's hand into TapSpace (see README.md and AGENTS.md).

  tap.py post   --agent T2-cards --title "Combined stat card" --image shot.png
                [--note "..."] [--campaign my-feature]        -> prints TICKET_ID
  tap.py update TICKET --image shot2.png [--note "v2: ..."]   -> new version, back to waiting
  tap.py wait   TICKET [--timeout 900]   -> blocks until the reviewer decides on the CURRENT version
                                            exit 0 = approved, 3 = changes (message on stdout), 4 = timeout
  tap.py status TICKET                   -> prints ticket JSON now, never blocks
  tap.py up                              -> ensure the whole rig is running (idempotent);
                                            starts the server if the port is quiet, health-checks it
  tap.py doctor                          -> one-look health: server, tickets, live activity, sessions
  tap.py listen --as NAME [--timeout 3600]
                                         -> a master agent joins the space under NAME (its own Chat
                                            thread) and blocks until the reviewer writes to it; prints his
                                            unanswered messages. exit 0 = message, 4 = timeout
  Taps (the reviewer's GitHub agent issues; every agent can talk to all of them):
  tap.py taps                            -> list them: key, status, title, last message
  tap.py tap-read KEY                    -> the whole thread of one tap (KEY = owner/repo#n)
  tap.py tap-say --as NAME KEY "text"    -> message a tap as the reviewer (signed "Sent by NAME")
  tap.py tap-watch [--timeout 3600]      -> blocks until any tap posts something new or changes status
  tap.py reply --as NAME "text" [--image a.png ...]
                                         -> that agent answers the reviewer in its thread (images optional;
                                            images the reviewer attaches print as absolute file paths)
  Any number of agents can be in the space at once; NAME defaults to $TAPSPACE_AGENT.

Server default http://127.0.0.1:4747 (override: TAPSPACE_URL).
Screenshots must be REAL renders of the running surface — never generated images.
"""
import argparse
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = os.environ.get("TAPSPACE_URL", "http://127.0.0.1:4747")


def _call(path, payload=None):
    if payload is None:
        req = urllib.request.Request(BASE + path)
    else:
        req = urllib.request.Request(
            BASE + path, data=json.dumps(payload).encode(),
            headers={"content-type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode())
        except Exception:
            return {"error": f"HTTP {e.code}"}


def _b64(path):
    with open(path, "rb") as fh:
        return base64.b64encode(fh.read()).decode()


def _media(a):
    """Exactly one of --image/--video -> the media payload for post/update."""
    if bool(a.image) == bool(a.video):
        sys.exit("exactly one of --image or --video is required")
    if a.image:
        return {"image_b64": _b64(a.image)}
    ext = a.video.rsplit(".", 1)[-1].lower()
    if ext not in ("mp4", "mov"):
        sys.exit("--video must be .mp4 or .mov")
    mb = os.path.getsize(a.video) / 1e6
    if mb > 60:
        sys.exit(f"video is {mb:.0f}MB; keep recordings short (5-20s, under ~60MB)")
    return {"video_b64": _b64(a.video), "video_ext": ext}


def main():
    ap = argparse.ArgumentParser(prog="tap.py")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("post")
    p.add_argument("--agent", required=True)
    p.add_argument("--title", required=True)
    p.add_argument("--image")
    p.add_argument("--video", help="short screen recording (.mp4/.mov); scrubbable in the space")
    p.add_argument("--note", default="")
    p.add_argument("--campaign", default="")

    u = sub.add_parser("update")
    u.add_argument("ticket")
    u.add_argument("--image")
    u.add_argument("--video", help="short screen recording (.mp4/.mov); scrubbable in the space")
    u.add_argument("--note", default="")

    w = sub.add_parser("wait")
    w.add_argument("ticket")
    w.add_argument("--timeout", type=int, default=900)

    s = sub.add_parser("status")
    s.add_argument("ticket")

    sub.add_parser("up")
    sub.add_parser("doctor")

    me = os.environ.get("TAPSPACE_AGENT", "")
    li = sub.add_parser("listen")
    li.add_argument("--as", dest="name", default=me, required=not me)
    li.add_argument("--timeout", type=int, default=3600)

    sub.add_parser("taps")
    tr = sub.add_parser("tap-read")
    tr.add_argument("key")
    ts = sub.add_parser("tap-say")
    ts.add_argument("--as", dest="name", default=me, required=not me)
    ts.add_argument("key")
    ts.add_argument("text")
    tw = sub.add_parser("tap-watch")
    tw.add_argument("--timeout", type=int, default=3600)

    rp = sub.add_parser("reply")
    rp.add_argument("--as", dest="name", default=me, required=not me)
    rp.add_argument("text", nargs="?", default="")
    rp.add_argument("--image", action="append", default=[], help="attach an image (repeatable)")

    a = ap.parse_args()

    if a.cmd == "up":
        try:
            _call("/api/state")
            print(f"TapSpace already up at {BASE}")
            return 0
        except Exception:
            pass
        import subprocess
        root = os.path.dirname(os.path.abspath(__file__))
        with open(os.path.join(root, "server.log"), "ab") as log:
            subprocess.Popen([sys.executable, os.path.join(root, "server.py")],
                             stdout=log, stderr=log, start_new_session=True)
        for _ in range(25):
            time.sleep(0.3)
            try:
                _call("/api/state")
                print(f"TapSpace UP at {BASE}")
                return 0
            except Exception:
                continue
        print(f"FAILED to start; see {root}/server.log")
        return 1

    if a.cmd == "doctor":
        try:
            st = _call("/api/state")
            act = _call("/api/activity")
        except Exception as e:
            print(json.dumps({"server": "DOWN", "url": BASE, "error": str(e),
                              "fix": "tap.py up"}))
            return 1
        print(json.dumps({
            "server": "UP", "url": BASE,
            "live_cards": len(st["tickets"]),
            "waiting_on_you": sum(1 for t in st["tickets"] if t["status"] == "waiting"),
            "activity": act["rows"],
            "claude_sessions": act["sessions"],
        }, indent=1))
        return 0

    if a.cmd == "post":
        r = _call("/api/post", {
            "agent": a.agent, "title": a.title, "note": a.note,
            "campaign": a.campaign, **_media(a)})
        print(r["id"])
        return 0

    if a.cmd == "update":
        r = _call("/api/update", {"id": a.ticket, "note": a.note, **_media(a)})
        print(json.dumps(r))
        return 0

    if a.cmd in ("taps", "tap-read", "tap-watch"):
        def snap():
            return {t["key"]: t for t in _call("/api/taps")["items"]}
        if a.cmd == "taps":
            for t in snap().values():
                last = [m for m in t["messages"] if m["who"] == "tap"]
                tail = (last[-1]["body"].splitlines() or [""])[0][:90] if last else ""
                print(f"{t['key']}  [{t['status']}{', replying' if t.get('replying') else ''}]  {t['title']}")
                if tail:
                    print(f"    last: {tail}")
            return 0
        if a.cmd == "tap-read":
            _call("/api/taps/refresh", {"key": a.key})
            t = snap().get(a.key)
            if not t:
                print(json.dumps({"error": "no such tap of yours"})); return 1
            print(f"{t['key']}  [{t['status']}]  {t['title']}\n{t['url']}")
            for g in t["goals"]:
                print(f"  [{'x' if g['done'] else ' '}] {g['text']}")
            for m in t["messages"]:
                who = "tap" if m["who"] == "tap" else ("you" + (f" (via {m['via']})" if m.get("via") else ""))
                print(f"\n--- {who} · {m['ts']}{' · seen' if m.get('seen') else ''}\n{m['body']}")
            return 0
        base = {k: (len(t["messages"]), t["status"]) for k, t in snap().items()}
        deadline = time.time() + a.timeout
        while time.time() < deadline:
            time.sleep(5)
            try:
                now = snap()
            except Exception:
                continue
            news = [t for k, t in now.items()
                    if k not in base or (len(t["messages"]), t["status"]) != base[k]]
            news = [t for t in news if t["messages"] and t["messages"][-1]["who"] == "tap"
                    or (t["key"] in base and t["status"] != base[t["key"]][1])]
            if news:
                for t in news:
                    print(f"== {t['key']} [{t['status']}] {t['title']}")
                    if t["messages"]:
                        print(t["messages"][-1]["body"])
                return 0
            base = {k: (len(t["messages"]), t["status"]) for k, t in now.items()}
        print(json.dumps({"timeout": True}))
        return 4

    if a.cmd == "tap-say":
        r = _call("/api/taps/say", {"key": a.key, "body": a.text, "via": a.name})
        print(json.dumps(r))
        return 0 if r.get("ok") else 1

    if a.cmd == "listen":
        deadline = time.time() + a.timeout
        while time.time() < deadline:
            try:
                q = urllib.parse.urlencode({"agent": a.name, "where": os.getcwd().replace(os.path.expanduser("~"), "~")})
                pending = _call("/api/chat/listen?" + q)["pending"]
            except Exception:
                time.sleep(3)
                continue
            if pending:
                for m in pending:
                    print(f"[{m['id']}] {m['body']}")
                    for f in m.get("files", []):
                        print(f"    image: {f}")
                return 0
            time.sleep(2)
        print(json.dumps({"timeout": True}))
        return 4

    if a.cmd == "reply":
        imgs = [{"b64": _b64(f), "ext": f.rsplit(".", 1)[-1].lower()} for f in a.image]
        r = _call("/api/chat/reply", {"agent": a.name, "body": a.text, "images": imgs})
        print(json.dumps(r))
        return 0 if r.get("ok") else 1

    if a.cmd == "status":
        print(json.dumps(_call("/api/ticket/" + a.ticket), indent=1))
        return 0

    if a.cmd == "wait":
        deadline = time.time() + a.timeout
        while time.time() < deadline:
            try:
                t = _call("/api/ticket/" + a.ticket)
            except Exception:
                # server blip (restart, transient refusal) — hold the wait, never die
                time.sleep(3)
                continue
            if t.get("error"):
                print(json.dumps(t)); return 1
            current = [d for d in t.get("decisions", []) if d["version"] == t["version"]]
            if current:
                d = current[-1]
                print(json.dumps(d))
                return 0 if d["action"] == "approve" else 3
            time.sleep(3)
        print(json.dumps({"timeout": True, "ticket": a.ticket}))
        return 4


if __name__ == "__main__":
    sys.exit(main())
