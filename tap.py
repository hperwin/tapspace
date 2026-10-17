#!/usr/bin/env python3
"""tap: the agent's hand into TapSpace (see README.md and AGENTS.md).

  tap.py post   --agent T2-cards --title "Combined stat card" --image shot.png
                [--note "..."] [--campaign my-feature] [--owner NAME]
                                                              -> prints TICKET_ID
  tap.py update TICKET --image shot2.png [--note "v2: ..."]   -> new version, back to waiting
  tap.py wait   TICKET [--timeout 900]   -> blocks until the reviewer decides on the CURRENT version
                                            exit 0 = approved, 3 = changes (message on stdout), 4 = timeout
  tap.py status TICKET                   -> prints ticket JSON now, never blocks
  tap.py up                              -> ensure the whole rig is running (idempotent);
                                            starts the server if the port is quiet, health-checks it
  tap.py doctor                          -> one-look health: server, cards and their owners, goal
                                            cards, notes, links, claimed sessions, live activity
  tap.py listen --as NAME [--timeout 3600]
                                         -> a master agent joins the space under NAME (its own Chat
                                            thread) and blocks until the reviewer writes to it; prints his
                                            unanswered messages; an answer to a card adds one
                                            "    answer: {json}" line. exit 0 = message, 4 = timeout
  tap.py title --as NAME "Stat card redesign"
                                         -> names the agent after its work (2 to 4 plain words; the
                                            only name the reviewer sees)
  tap.py goal --as NAME "the outcome, one plain line" --steps "Audit|Spec|Build|Review|PR open"
              [--current N]              -> a master publishes its run's goal card (the Chat tab and
                                            Glint show it); the goal or --steps alone changes just that
  tap.py step --as NAME next|done|N|"step name" [--note "14 of 20 builders done"]
                                         -> moves the goal card (N counts from 1, as the card shows it);
                                            --note alone changes only the note
  tap.py progress --as NAME              -> prints the goal card as stored (a relay car reads it here)
  tap.py stop --as NAME                  -> spins that master down, as the Chat rail's Spin down does: its
                                            Claude Code session ends and it leaves the list (--as is required)
                                            exit 0 = stopped, 1 = refused (nothing was ended)
  tap.py lead --as NAME                  -> marks NAME the lead, the master over all masters: every client
                                            shows it first and the other masters under it (one lead at a time;
                                            the newest replaces the old one; --as is required)
  tap.py new --as LEAD --dir DIR [--order-file FILE]
                                         -> starts a new master in DIR (headless tmux, as the space's New
                                            session does), born under LEAD, FILE's text added to its first
                                            prompt; prints its NAME and tmux session
  tap.py adopt --as LEAD CHILD           -> puts CHILD, a master already running, under LEAD
  Taps (the reviewer's GitHub agent issues; every agent can talk to all of them):
  tap.py taps                            -> list them: key, phase (Proposed, Flying, Needs you, Landed, Done,
                                            Closed), title, last message
  tap.py tap-read KEY                    -> the whole thread of one tap (KEY = owner/repo#n), with its numbered proof images
  tap.py tap-say --as NAME KEY "text"    -> message a tap as the reviewer (signed "Sent by NAME")
  tap.py tap-watch [--timeout 3600]      -> blocks until any tap posts something new or changes state
  tap.py relay --as NAME                 -> this session is handing off to its next car: the thread
                                            shows "Switching car" until the new car listens --as NAME
  tap.py reply --as NAME "text" [--image a.png ...]
                                         -> that agent answers the reviewer in its thread (images optional;
                                            images the reviewer attaches print as absolute file paths)
  tap.py card --as NAME "one plain line" [--face REF]
              [--progress 3/5[:label] | --timeline "a|b|c" [--at N] | --graph "3,5,8[:label]" | --word W]
              [--item REF[|name][=note] ...] [--image FILE[=label] ...] [--proof KEY[:1,3] ...]
              [--ask "question" [--check "label" ...] [--text]] [--accept KEY]
                                         -> one update card in NAME's thread: a face, one visual, how far each item
                                            got, images for the reviewer to approve (your --image files, then each
                                            --proof), a question; prints the stored row. REF = a master's NAME or
                                            title, a tap (owner/repo#n or repo#n) or project:NAME; name = a short name
                                            the card shows in place of its title (up to 40 characters); --proof KEY
                                            alone sends all its proof images; --accept KEY gives the card Accept and Not
                                            quite for that tap (and its proof images when the card has no --image
                                            or --proof). His answers come back through listen.
                                            exit 0 = stored, 1 = refused (the reason is printed)
  Any number of agents can be in the space at once; NAME defaults to $TAPSPACE_AGENT.
  tap.py pair [--off]                    -> opens the phone's route to this Mac (Tailscale Serve when
                                            logged in, else an ngrok tunnel) and prints the Glint
                                            pairing link and QR code; --off closes the ngrok tunnel

Cards land under their master by themselves: inside Claude Code every call names the session it
runs in (only when that Claude process is an ancestor of this one, so an id inherited through tmux
or a launcher is never sent). A lane in its own process (claude -p, Codex, a separate tmux session)
or outside Claude Code posts with --owner NAME.

Server default http://127.0.0.1:4747 (override: TAPSPACE_URL).
Screenshots must be REAL renders of the running surface, never generated images.
"""
import argparse
import base64
import functools
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = os.environ.get("TAPSPACE_URL", "http://127.0.0.1:4747")
DATA = pathlib.Path(os.environ.get("TAPSPACE_DATA") or pathlib.Path(__file__).resolve().parent / "data")
TAILSCALE = shutil.which("tailscale") or "/Applications/Tailscale.app/Contents/MacOS/Tailscale"
NGROK = shutil.which("ngrok") or "/opt/homebrew/bin/ngrok"
_SID = re.compile(r"^[0-9A-Fa-f-]{8,64}$")
# one Claude Code session's identity; a process that outlives the session must not carry it
SESSION_ENV = ("CLAUDE_CODE_SESSION_ID", "CLAUDE_PID", "CLAUDE_CODE_MESSAGING_SOCKET",
               "CLAUDE_CODE_MESSAGING_TOKEN", "CODEX_COMPANION_SESSION_ID",
               "CODEX_COMPANION_TRANSCRIPT_PATH")


def _ancestors():
    """The pids above this process, nearest first (one ps call; empty when ps is unavailable)."""
    try:
        out = subprocess.run(["ps", "-A", "-o", "pid=", "-o", "ppid="], capture_output=True,
                             text=True, timeout=5).stdout
    except Exception:
        return []
    up = {}
    for line in out.splitlines():
        f = line.split()
        if len(f) == 2 and f[0].isdigit() and f[1].isdigit():
            up[int(f[0])] = int(f[1])
    chain, p = [], os.getppid()
    while p > 1 and p not in chain:
        chain.append(p)
        p = up.get(p, 0)
    return chain


@functools.lru_cache(maxsize=None)
def _who():
    """Headers naming the Claude Code session this tap.py runs in, so the server can file cards
    under their master. Sent only when CLAUDE_PID is one of our ancestors: tmux and launchers
    hand stale ids of other sessions to processes that are not theirs."""
    pid = os.environ.get("CLAUDE_PID", "")
    if not pid.isdigit() or int(pid) not in _ancestors():
        return {}
    sid = os.environ.get("CLAUDE_CODE_SESSION_ID") or os.environ.get("CODEX_COMPANION_SESSION_ID") or ""
    return {"X-Claude-Pid": pid, **({"X-Claude-Session": sid} if _SID.match(sid) else {})}


def _daemon_env(env):
    """env without any Claude Code session's identity (for the server, which outlives its starter)."""
    return {k: v for k, v in env.items() if k not in SESSION_ENV}


def _call(path, payload=None, timeout=30):
    headers = dict(_who())
    if payload is not None:
        headers["content-type"] = "application/json"
    req = urllib.request.Request(BASE + path, headers=headers,
                                 data=None if payload is None else json.dumps(payload).encode())
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode())
        except Exception:
            return {"error": f"HTTP {e.code}"}


def _ngrok_pid(cur):
    """The pid of the ngrok tunnel recorded in data/remote.json, if it is still that process."""
    pid = cur.get("pid") if cur.get("kind") == "ngrok" else None
    if not pid:
        return None
    ps = subprocess.run(["ps", "-p", str(pid), "-o", "comm="], capture_output=True, text=True)
    return pid if "ngrok" in ps.stdout else None


def _pair(off):
    """The phone's route to this Mac, then the glint://pair link as text and as a QR code.
    Tailscale Serve (private HTTPS) when this Mac is logged in to Tailscale, else ngrok
    (public URL; the server's token guards every request that comes through it)."""
    import qr
    remote = DATA / "remote.json"
    try:
        cur = json.loads(remote.read_text())
    except Exception:
        cur = {}
    pid = _ngrok_pid(cur)
    if off:
        if pid:
            os.kill(pid, 15)
        remote.unlink(missing_ok=True)
        print("Route closed." if pid else "No ngrok tunnel was open.")
        return 0
    try:
        if not _call("/api/health").get("ok"):
            raise RuntimeError
    except Exception:
        print(f"TapSpace at {BASE} is not running this version; start it with: python3 tap.py up")
        return 1
    port = urllib.parse.urlsplit(BASE).port or 4747
    url = ""
    try:
        st = json.loads(subprocess.run([TAILSCALE, "status", "--json"], capture_output=True,
                                       text=True, timeout=15).stdout)
        if st.get("BackendState") == "Running":
            subprocess.run([TAILSCALE, "serve", "--bg", str(port)], capture_output=True,
                           timeout=30, check=True)
            url = "https://" + st["Self"]["DNSName"].rstrip(".")
    except Exception:
        pass
    if url:
        if pid:
            os.kill(pid, 15)            # the private route replaces the public one
        cur = {"kind": "tailscale", "url": url, "port": port}
    elif pid and cur.get("port") == port:
        url = cur["url"]
    else:
        log = DATA / "ngrok.log"
        log.write_text("")
        cmd = [NGROK, "http", str(port), "--log", str(log), "--log-format", "json"]
        if os.environ.get("TAPSPACE_NGROK_DOMAIN"):     # a reserved domain keeps the link stable
            cmd += ["--url", "https://" + os.environ["TAPSPACE_NGROK_DOMAIN"]]
        pid = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               start_new_session=True).pid
        for _ in range(75):
            time.sleep(0.2)
            for line in log.read_text().splitlines():
                try:
                    j = json.loads(line)
                except Exception:
                    continue
                if j.get("msg") == "started tunnel" and j.get("url", "").startswith("https://"):
                    url = j["url"]
                elif j.get("lvl") in ("eror", "crit"):
                    print("ngrok failed: " + str(j.get("err") or j.get("msg")))
                    return 1
            if url:
                break
        if not url:
            print(f"ngrok did not open a tunnel within 15 s; see {log}")
            return 1
        cur = {"kind": "ngrok", "url": url, "pid": pid, "port": port}
    remote.write_text(json.dumps(cur, indent=1))
    link = "glint://pair?" + urllib.parse.urlencode(
        {"url": url, "token": (DATA / "token").read_text().strip()})
    print(qr.terminal(link))
    print(f"\nScan this in Glint (You, then Pair with your Mac), or paste the link:\n{link}\n")
    if cur["kind"] == "tailscale":
        print(f"Route: Tailscale Serve, private to your tailnet ({url}).")
    else:
        print(f"Route: ngrok, a public URL ({url}); the token guards every request.\n"
              "For a private route, open Tailscale on this Mac and on your phone, log in, and run "
              "tap.py pair again: it switches to Tailscale Serve and closes ngrok.\n"
              "Close the tunnel any time with: tap.py pair --off")
    return 0


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


def _to_step(to):
    """A step as the card shows it (1 = first), a step's name, next or done -> the wire fields."""
    to = to.strip()
    return {"current": int(to) - 1} if to.isdigit() else {"step": to}


def _at(to, steps):
    """--at as the wire's at (0 = on the first step, len(steps) = all done): a step number counted from 1, a
    step's name, or done; the first step when unset."""
    if to is None:
        return 0
    w = to.strip()
    if w.lower() == "done":
        return len(steps)
    if w.isdigit() and 1 <= int(w) <= len(steps):
        return int(w) - 1
    hit = next((i for i, s in enumerate(steps) if s.casefold() == w.casefold()), None)
    if hit is None:
        sys.exit(f"--at: a step number from 1 to {len(steps)}, a step's name, or done")
    return hit


def _card_parse(a):
    """tap.py card's flags -> (card, images, labels, proofs), before anything is sent; a flag that does not
    parse exits here. images and labels hold the --image files; proofs [(KEY, [i, ...] or None for all)] and
    --accept's KEY are looked up on each try (_card_try)."""
    card = {"line": a.line}
    if a.face:
        card["face"] = a.face
    if a.at is not None and a.timeline is None:
        sys.exit("--at goes with --timeline")
    if a.progress is not None:
        m = re.fullmatch(r"\s*(\d+)\s*/\s*(\d+)\s*(?::(.*))?", a.progress)
        if not m:
            sys.exit('--progress: DONE/TOTAL, like 3/5 or "3/5:3 of 5 landed"')
        card["visual"] = {"kind": "progress", "done": int(m[1]), "total": int(m[2]),
                          **({"label": m[3].strip()} if (m[3] or "").strip() else {})}
    elif a.timeline is not None:
        steps = [s.strip() for s in a.timeline.split("|") if s.strip()]
        card["visual"] = {"kind": "timeline", "steps": steps, "at": _at(a.at, steps)}
    elif a.graph is not None:
        nums, _, label = a.graph.partition(":")
        try:
            pts = [float(x) for x in nums.split(",")]
            if not all(abs(x) < float("inf") for x in pts):     # nan and inf: not points, and not JSON
                raise ValueError
        except ValueError:
            sys.exit('--graph: numbers with commas, like 3,5,8 or "3,5,8:visits"')
        card["visual"] = {"kind": "graph", "points": [int(x) if x.is_integer() else x for x in pts],
                          **({"label": label.strip()} if label.strip() else {})}
    elif a.word is not None:
        card["visual"] = {"kind": "word", "text": a.word}
    if a.item:
        card["items"] = []
        for s in a.item:
            left, _, note = s.partition("=")        # REF[|name][=note]: the note after the first =, the name after |
            ref, _, short = left.partition("|")
            card["items"].append({"ref": ref.strip(), **({"name": short.strip()} if short.strip() else {}),
                                  **({"note": note.strip()} if note.strip() else {})})
    if (a.check or a.text) and a.ask is None:
        sys.exit("--check and --text go with --ask")
    if a.ask is not None:
        card["ask"] = {"q": a.ask, "checks": a.check, "text": a.text or not a.check}
    if a.accept is not None and not a.accept.strip():
        sys.exit("--accept: the tap to accept, owner/repo#n or repo#n")
    images, labels = [], []
    for s in a.image:
        f, _, label = s.partition("=")
        try:
            images.append({"b64": _b64(f), "ext": f.rsplit(".", 1)[-1].lower()})
        except OSError as e:
            sys.exit(f"cannot read --image {f}: {e.strerror}")
        labels.append({"label": label.strip()} if label.strip() else {})
    proofs = []
    for s in a.proof:
        key, _, nums = s.partition(":")
        try:
            idx = [int(x) for x in nums.split(",")] if nums.strip() else None
        except ValueError:
            idx = [0]                   # not a number: refused below, as a 0 is
        if not key.strip() or (idx is not None and min(idx) < 1):
            sys.exit("--proof: KEY for all its proof images, or KEY:1,3 (they count from 1, as tap-read shows them)")
        proofs.append((key.strip(), None if idx is None else [n - 1 for n in idx]))
    return card, images, labels, proofs


def _your_tap(flag, key, items):
    """(the tap, None) for KEY (owner/repo#n, or repo#n when only one tap matches), else (None, the refusal)."""
    k = key.casefold()
    hits = [t for t in items
            if t["key"].casefold() == k or ("/" not in k and t["key"].casefold().endswith("/" + k))]
    if len(hits) == 1:
        return hits[0], None
    return None, {"error": f"{flag}: {'more than one of your taps is' if hits else 'none of your taps is'} "
                           f"{key} (tap.py taps lists them)"}


def _card_try(name, card, images, labels, proofs, accept=None):
    """One try at sending the card: the --accept tap and each --proof tap are looked up first and the proof
    images numbered; an --accept card with no --image or --proof shows that tap's own proof (a decision card is
    mostly the picture). The server's answer, or {error, retry: true} while the taps load."""
    card, images, labels = dict(card), list(images), list(labels)
    if proofs or accept:
        got = _call("/api/taps")
        if not got.get("fetched"):
            return {"error": "taps are still loading; send it again in a minute", "retry": True}
        items = got.get("items") or []
        if accept:
            t, why = _your_tap("--accept", accept.strip(), items)
            if why:
                return why
            card["action"] = {"kind": "tap_accept", "tap": t["key"]}
            if not images and not proofs:       # a decision card is mostly the picture: the tap's own proof
                for i in range(len(t.get("proof") or [])):
                    images.append({"proof": {"key": t["key"], "i": i}})
                    labels.append({})
        for key, idx in proofs:
            t, why = _your_tap("--proof", key, items)
            if why:
                return why
            n = len(t.get("proof") or [])
            if not n or (idx and max(idx) >= n):
                return {"error": f"--proof: {t['key']} has {n or 'no'} proof images (tap.py tap-read {t['key']})"}
            for i in range(n) if idx is None else idx:
                images.append({"proof": {"key": t["key"], "i": i}})
                labels.append({})
    if any(labels):
        card["images"] = labels
    # the server reads up to 10 proof images from GitHub before it answers: a sender that gave up would send again
    return _call("/api/chat/reply", {"agent": name, "images": images, "card": card}, timeout=120)


def _progress(name, body):
    """One change to NAME's goal card; prints the server's answer. Exit 0 on ok, 1 otherwise."""
    r = _call("/api/agents/progress", {"name": name, **body})
    print(json.dumps(r))
    return 0 if r.get("ok") else 1


def _claimed():
    """How many Claude Code sessions the server has tied to how many agent NAMEs
    (data/sessions.json), or None when this tap.py cannot read the server's data folder."""
    try:
        d = json.loads((DATA / "sessions.json").read_text())
        d = d["sessions"] if isinstance(d.get("sessions"), dict) else d
        names = [v["name"] for v in d.values() if isinstance(v, dict) and isinstance(v.get("name"), str)]
    except Exception:
        return None
    return {"sessions": len(names), "agents": len(set(names))}


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
    p.add_argument("--owner", default="",
                   help="the master's chat NAME; only for callers outside Claude Code, where it is automatic")

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
    pr = sub.add_parser("pair")
    pr.add_argument("--off", action="store_true", help="close the ngrok tunnel")
    sub.add_parser("doctor")

    me = os.environ.get("TAPSPACE_AGENT", "")
    li = sub.add_parser("listen")
    li.add_argument("--as", dest="name", default=me, required=not me)
    li.add_argument("--timeout", type=int, default=3600)

    ti = sub.add_parser("title")
    ti.add_argument("--as", dest="name", default=me, required=not me)
    ti.add_argument("title")

    rl = sub.add_parser("relay")
    rl.add_argument("--as", dest="name", default=me, required=not me)

    go = sub.add_parser("goal")
    go.add_argument("--as", dest="name", default=me, required=not me)
    go.add_argument("goal", nargs="?", help="the run's north star: the outcome the reviewer will see, one plain line")
    go.add_argument("--steps", help='the run\'s steps in order, e.g. "Audit|Spec|Build|Review|PR open"')
    go.add_argument("--current", help="where the run is now: a step number (1 = first), a step's name, or done")

    st = sub.add_parser("step")
    st.add_argument("--as", dest="name", default=me, required=not me)
    st.add_argument("to", nargs="?", help="a step number (1 = first), a step's name, next, or done")
    st.add_argument("--note", help='where inside the step, one short plain line ("" clears it)')

    pg = sub.add_parser("progress")
    pg.add_argument("--as", dest="name", default=me, required=not me)

    sp = sub.add_parser("stop")
    sp.add_argument("--as", dest="name", required=True, help="the master to spin down (never defaults to yourself)")

    ld = sub.add_parser("lead")
    ld.add_argument("--as", dest="name", required=True,
                    help="the master to mark as the lead over all masters (never defaults to yourself)")

    ad = sub.add_parser("adopt")
    ad.add_argument("--as", dest="name", default=me, required=not me, help="the lead's chat NAME")
    ad.add_argument("child", help="the chat NAME of a running master to put under the lead")

    nw = sub.add_parser("new")
    nw.add_argument("--as", dest="name", default=me, required=not me,
                    help="the lead's chat NAME; the new master is born under it")
    nw.add_argument("--dir", required=True, help="the folder the new master starts in (inside your home folder)")
    nw.add_argument("--order-file", help="a text file whose words are added to the new master's first prompt")

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

    cd = sub.add_parser("card")
    cd.add_argument("--as", dest="name", default=me, required=not me)
    cd.add_argument("line", help="what the reviewer reads first: one plain line (also the push and what older apps show)")
    cd.add_argument("--face", help="whose face leads the card: a master's NAME or title, a tap (owner/repo#n or "
                                   "repo#n) or project:NAME; default the one --item, else none")
    vis = cd.add_mutually_exclusive_group()
    vis.add_argument("--progress", help='how far: DONE/TOTAL, e.g. "3/5" or "3/5:3 of 5 landed"')
    vis.add_argument("--timeline", help='steps in order, e.g. "Audit|Spec|Build" (where it is: --at)')
    vis.add_argument("--graph", help='a small line graph: numbers, e.g. "3,5,8" or "3,5,8:visits"')
    vis.add_argument("--word", help="one big word, e.g. Landed")
    cd.add_argument("--at", help="where the timeline is: a step number (1 = first), a step's name, or done")
    cd.add_argument("--item", action="append", default=[],
                    help="a master or tap the card moves: REF[|name][=note], name a short name the card shows "
                         "for it in place of its title (repeatable)")
    cd.add_argument("--image", action="append", default=[], help="an image to approve: FILE or FILE=label (repeatable)")
    cd.add_argument("--proof", action="append", default=[],
                    help="a tap's proof images to approve: KEY for all, KEY:1,3 for some (repeatable)")
    cd.add_argument("--ask", help="a question for the reviewer, one plain line")
    cd.add_argument("--check", action="append", default=[], help="a box he can tick under --ask (repeatable)")
    cd.add_argument("--text", action="store_true", help="a text box under --ask (on by itself when there is no --check)")
    cd.add_argument("--accept", help="a landed tap (owner/repo#n or repo#n) the reviewer can accept from the card: "
                                     "its buttons become Accept and Not quite; with no --image or --proof the card "
                                     "shows that tap's proof images")

    a = ap.parse_args()

    if a.cmd == "pair":
        return _pair(a.off)

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
                             stdout=log, stderr=log, start_new_session=True,
                             env=_daemon_env(os.environ))
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
            ag = _call("/api/agents")
        except Exception as e:
            print(json.dumps({"server": "DOWN", "url": BASE, "error": str(e),
                              "fix": "tap.py up"}))
            return 1
        rows, links = ag.get("agents") or [], ag.get("links") or []
        print(json.dumps({
            "server": "UP", "url": BASE,
            "live_cards": len(st["tickets"]),
            "waiting_on_you": sum(1 for t in st["tickets"] if t["status"] == "waiting"),
            "cards_with_owner": sum(1 for t in st["tickets"] if t.get("owner")),
            "agents": len(rows),
            "goal_cards": sum(1 for r in rows if r.get("progress")),
            "notes": sum(len(r.get("notes") or []) for r in rows),
            "links": len(links),
            "links_active": sum(1 for x in links if x.get("active")),
            "claimed_sessions": _claimed(),
            # the link scanner's own counters (null on a server without it); zero heard and claimed
            # with masters live means Claude Code's transcript format changed
            "link_scanner": act.get("links"),
            "activity": act["rows"],
            "claude_sessions": act["sessions"],
        }, indent=1))
        return 0

    if a.cmd == "post":
        r = _call("/api/post", {
            "agent": a.agent, "title": a.title, "note": a.note, "campaign": a.campaign,
            **({"owner": a.owner} if a.owner else {}), **_media(a)})
        print(r["id"])
        return 0

    if a.cmd == "update":
        r = _call("/api/update", {"id": a.ticket, "note": a.note, **_media(a)})
        print(json.dumps(r))
        return 0

    if a.cmd in ("taps", "tap-read", "tap-watch"):
        def snap():
            return {t["key"]: t for t in _call("/api/taps")["items"]}
        def word(t):                    # the one plain word every client shows; status on an older server
            return t.get("phase_word") or t["status"]
        def sig(t):
            return (len(t["messages"]), t["status"], t.get("phase"))
        if a.cmd == "taps":
            for t in snap().values():
                last = [m for m in t["messages"] if m["who"] == "tap"]
                tail = (last[-1]["body"].splitlines() or [""])[0][:90] if last else ""
                fly = ", waiting to fly" if t.get("phase") == "proposed" and t.get("fly_requested") else ""
                print(f"{t['key']}  [{word(t)}{fly}{', replying' if t.get('replying') else ''}]  {t['title']}")
                if tail:
                    print(f"    last: {tail}")
            return 0
        if a.cmd == "tap-read":
            _call("/api/taps/refresh", {"key": a.key})
            t = snap().get(a.key)
            if not t:
                print(json.dumps({"error": "no such tap of yours"})); return 1
            print(f"{t['key']}  [{word(t)}]  {t['title']}\n{t['url']}")
            for g in t["goals"]:
                print(f"  [{'x' if g['done'] else ' '}] {g['text']}")
            for n, pf in enumerate(t.get("proof") or [], 1):
                print(f"  proof {n}: {pf.get('alt') or 'image'}")
            for m in t["messages"]:
                who = "tap" if m["who"] == "tap" else ("you" + (f" (via {m['via']})" if m.get("via") else ""))
                print(f"\n--- {who} · {m['ts']}{' · seen' if m.get('seen') else ''}\n{m['body']}")
            return 0
        base = {k: sig(t) for k, t in snap().items()}
        deadline = time.time() + a.timeout
        while time.time() < deadline:
            time.sleep(5)
            try:
                now = snap()
            except Exception:
                continue
            news = [t for k, t in now.items()
                    if k not in base or sig(t) != base[k]]
            news = [t for t in news if t["messages"] and t["messages"][-1]["who"] == "tap"
                    or (t["key"] in base and sig(t)[1:] != base[t["key"]][1:])]
            if news:
                for t in news:
                    print(f"== {t['key']} [{word(t)}] {t['title']}")
                    if t["messages"]:
                        print(t["messages"][-1]["body"])
                return 0
            base = {k: sig(t) for k, t in now.items()}
        print(json.dumps({"timeout": True}))
        return 4

    if a.cmd == "title":
        r = _call("/api/agents/title", {"agent": a.name, "title": a.title})
        print(json.dumps(r))
        return 0 if r.get("ok") else 1

    if a.cmd == "relay":
        r = _call("/api/agents/relay", {"agent": a.name})
        print(json.dumps(r))
        return 0 if r.get("ok") else 1

    if a.cmd == "goal":
        body = {}
        if a.goal is not None:
            body["goal"] = a.goal
        if a.steps is not None:
            body["steps"] = [s.strip() for s in a.steps.split("|") if s.strip()]
        if a.current is not None:
            body.update(_to_step(a.current))
        if not body:
            sys.exit('give the goal and/or --steps: tap.py goal --as NAME "goal" --steps "a|b|c"')
        return _progress(a.name, body)

    if a.cmd == "step":
        body = _to_step(a.to) if a.to else {}
        if a.note is not None:
            body["note"] = a.note
        if not body:
            sys.exit("give a step (a number, a step's name, next or done) and/or --note")
        return _progress(a.name, body)

    if a.cmd == "progress":
        row = next((r for r in _call("/api/agents").get("agents") or [] if r.get("name") == a.name), None)
        if row is None:
            print(json.dumps({"error": "no agent by that name is in the space"}))
            return 1
        print(json.dumps(row.get("progress"), indent=1))
        return 0

    if a.cmd == "stop":
        r = _call("/api/agents/stop", {"name": a.name})
        print(json.dumps(r))
        return 0 if r.get("ok") else 1

    if a.cmd == "lead":
        r = _call("/api/agents/lead", {"name": a.name})
        print(json.dumps(r))
        return 0 if r.get("ok") else 1

    if a.cmd == "adopt":
        r = _call("/api/agents/adopt", {"lead": a.name, "child": a.child})
        print(json.dumps(r))
        return 0 if r.get("ok") else 1

    if a.cmd == "new":
        body = {"folder": os.path.abspath(os.path.expanduser(a.dir)), "parent": a.name}
        if a.order_file is not None:
            try:
                order = pathlib.Path(a.order_file).read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as e:
                sys.exit(f"cannot read --order-file: {e}")
            if not order.strip():
                sys.exit(f"--order-file {a.order_file} is empty")
            body["order"] = order
        r = _call("/api/agents/new", body)
        print(json.dumps(r))
        return 0 if r.get("ok") else 1

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
                    if m.get("answer"):
                        print("    answer: " + json.dumps(m["answer"]))
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

    if a.cmd == "card":
        card, images, labels, proofs = _card_parse(a)
        deadline = time.time() + 60          # a server that just started reads the taps first
        while True:
            r = _card_try(a.name, card, images, labels, proofs, a.accept)
            if not r.get("retry") or time.time() >= deadline:
                break
            time.sleep(2)
        print(json.dumps(r))
        if r.get("ok") and "card" not in (r.get("message") or {}):
            print("this TapSpace has no update cards yet: it stored a plain reply", file=sys.stderr)
            return 1
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
