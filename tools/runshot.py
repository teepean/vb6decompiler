#!/usr/bin/env python3
"""Run a program under our Wine prefix for a while, then screenshot its windows and end it.

usage: runshot.py EXE OUTDIR [--wait S] [--step S] [--poke STEP ...] [-- ARGS]
Prints each visible window's title and size, and saves it as a PNG in OUTDIR, named by step and title
(tools/shotcmp.py compares two such folders).  Nothing is clicked unless asked: --poke (repeatable) does
one step through tools/wintree/winpoke.exe after the wait, --step S later the windows are taken again.
Steps: at:TITLE,X,Y (a click) set:TITLE,X,Y=TEXT (a text box) key:TITLE,X,Y (Space)
click:CAPTION pw:TEXT menu:CAPTION file:PATH, or sleep:S.
--desktop WxH runs it all on a Wine virtual desktop, one window on the user's screen, inside which the
program is active without taking the real focus: an inactive form loses a control's focus in the middle
of a click, and no Click comes (ThunderRT6UserControl).  Its picture is then the whole desktop.
Posted messages only:
the real mouse and keyboard stay the user's.
The program runs with its own folder as the current directory.  Message boxes are reported through
Wine's trace (vbmake.run does the same), and the run ends with the program killed by its path only.
"""
import argparse
import os
import re
import subprocess
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import vbmake  # noqa: E402


def windows(pid, env):
    out = []
    try:
        ids = subprocess.run(["xdotool", "search", "--onlyvisible", "--pid", pid], env=env,
                             capture_output=True, text=True, timeout=20).stdout.split()
        for w in ids:
            t = subprocess.run(["xdotool", "getwindowname", w], env=env, capture_output=True,
                               text=True, timeout=20).stdout.strip()
            g = subprocess.run(["xdotool", "getwindowgeometry", "--shell", w], env=env,
                               capture_output=True, text=True, timeout=20).stdout
            m = re.search(r"WIDTH=(\d+)\s+HEIGHT=(\d+)", g.replace("\n", " "))
            out.append((w, t, int(m.group(1)) if m else 0, int(m.group(2)) if m else 0))
    except (OSError, subprocess.TimeoutExpired):
        pass
    return out


def main():
    argv = sys.argv[1:]
    args = []
    if "--" in argv:
        i = argv.index("--")
        argv, args = argv[:i], argv[i + 1:]
    ap = argparse.ArgumentParser()
    ap.add_argument("exe")
    ap.add_argument("outdir")
    ap.add_argument("--wait", type=float, default=15)
    ap.add_argument("--step", type=float, default=3)
    ap.add_argument("--poke", action="append", default=[])
    ap.add_argument("--desktop")
    a = ap.parse_args(argv)
    os.makedirs(a.outdir, exist_ok=True)
    env = dict(os.environ, WINEPREFIX=vbmake.PREFIX, WINEDEBUG="-all,trace+msgbox")
    d, name = os.path.split(os.path.abspath(a.exe))
    desk = ["explorer", "/desktop=runshot%d,%s" % (os.getpid(), a.desktop)] if a.desktop else []
    target = "Z:" + os.path.abspath(a.exe).replace("/", "\\") if a.desktop else name
    proc = subprocess.Popen(["wine"] + desk + [target] + args, cwd=d, env=env, stdout=subprocess.DEVNULL,
                            stderr=subprocess.PIPE)
    boxes = []

    def watch():
        for ln in proc.stderr:
            m = re.search(rb'MSGBOX_OnInit L"(.*)"', ln)
            if m:
                boxes.append(m.group(1).decode("latin-1", "replace"))
            elif b"Unhandled" in ln or b"page fault" in ln:
                print("crash:", ln.decode("latin-1", "replace").strip())
    threading.Thread(target=watch, daemon=True).start()
    t0 = time.time()
    while proc.poll() is None and time.time() - t0 < a.wait:
        time.sleep(0.25)
    ended = proc.poll() is not None
    print("ended by itself after %.1f s" % (time.time() - t0) if ended else "still running")
    want = ("Z:" + os.path.abspath(a.exe).replace("/", "\\")).lower()

    def shots(label):
        if a.desktop:
            shot_env = dict(os.environ, DISPLAY=os.environ.get("DISPLAY", ":0"))
            ids = subprocess.run(["xdotool", "search", "--name", "runshot%d" % os.getpid()], env=shot_env,
                                 capture_output=True, text=True, timeout=20).stdout.split()
            for w in ids[:1]:
                png = os.path.join(a.outdir, "%s_desktop.png" % label)
                subprocess.run(["import", "-window", w, png], env=shot_env, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, timeout=20)
                print("%s desktop %s" % (label, os.path.basename(png)))
            return
        for pid in os.listdir("/proc"):
            if not pid.isdigit():
                continue
            try:
                cmd = open("/proc/%s/cmdline" % pid, "rb").read().split(b"\0")[0].decode("latin-1").lower()
                if cmd != want:
                    continue
                items = open("/proc/%s/environ" % pid, "rb").read().split(b"\0")
            except OSError:
                continue
            display = next((i[8:].decode() for i in items if i.startswith(b"DISPLAY=")), None)
            shot_env = dict(os.environ, DISPLAY=display or os.environ.get("DISPLAY", ":0"))
            seen = {}
            for w, t, wd, h in windows(pid, shot_env):
                if wd < 8 or h < 8:
                    continue
                safe = re.sub(r"\W+", "_", t) or "untitled"
                seen[safe] = seen.get(safe, 0) + 1
                png = os.path.join(a.outdir, "%s_%s_%d_%dx%d.png" % (label, safe, seen[safe], wd, h))
                subprocess.run(["import", "-window", w, png], env=shot_env, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, timeout=20)
                print("%s window %r %dx%d %s" % (label, t, wd, h, os.path.basename(png)))
    shots("s00")
    for i, p in enumerate(a.poke, 1):
        if proc.poll() is not None:
            print("ended before step %d" % i)
            break
        if p.startswith("sleep:"):
            time.sleep(float(p[6:]))
        else:
            verb, _, arg = p.partition(":")
            poke_env = dict(os.environ, WINEPREFIX=vbmake.PREFIX, WINEDEBUG="-all")
            poke = os.path.join(os.path.dirname(os.path.abspath(__file__)), "wintree", "winpoke.exe")
            if a.desktop:
                poke = "Z:" + poke.replace("/", "\\")
            r = subprocess.run(["wine"] + desk + [poke, verb, arg],
                               env=poke_env, timeout=60, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL)
            if r.returncode:
                print("step %d %s: no such window" % (i, p))
            time.sleep(a.step)
        print("== step %d: %s" % (i, p))
        shots("s%02d" % i)
    for b in boxes:
        print("box:", b)
    vbmake.kill_windows_process(os.path.abspath(a.exe))
    try:
        proc.wait(5)
    except subprocess.TimeoutExpired:
        proc.kill()


if __name__ == "__main__":
    main()
