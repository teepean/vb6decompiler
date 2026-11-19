#!/usr/bin/env python3
"""Time the processes a Wine command starts (VB6.EXE, C2.EXE, LINK.EXE, the
Wine services...): when each started, how long it lived, how much CPU it used.

usage: wineprof.py [--every MS] [--all] -- COMMAND...

Only processes started after the command whose WINEPREFIX is the command's
(VB6WINE, default re/wine-vb6) are listed, so another Wine program running
at the same time is left out.  /proc is polled (every 10 ms by default):
a process shorter than that can be missed, and its end time and last CPU
sample can be one interval late.  Prints one line per process (--all) or
per Windows program name, then the total wall time of the command.
"""
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PREFIX = os.environ.get("VB6WINE", os.path.normpath(os.path.join(HERE, "..", "..", "wine-vb6")))
TICK = os.sysconf("SC_CLK_TCK")


def pids():
    return {int(p) for p in os.listdir("/proc") if p.isdigit()}


def stat(pid):
    """(start in s since boot, user+sys CPU s), or None once it has ended."""
    try:
        s = open("/proc/%d/stat" % pid).read()
    except OSError:
        return None
    f = s[s.rindex(")") + 2:].split()
    if f[0] in "ZX":                    # exited, not reaped yet
        return None
    return int(f[19]) / TICK, (int(f[11]) + int(f[12])) / TICK


def ours(pid):
    try:
        env = open("/proc/%d/environ" % pid, "rb").read().split(b"\0")
    except OSError:
        return False
    return ("WINEPREFIX=" + PREFIX).encode() in env


def name(pid):
    try:
        cmd = open("/proc/%d/cmdline" % pid, "rb").read().split(b"\0")[0].decode("latin-1")
    except OSError:
        return "?"
    return cmd.replace("\\", "/").split("/")[-1] or "?"


def main(argv):
    every, full = 0.01, "--all" in argv
    argv = [a for a in argv if a != "--all"]
    if argv[:1] == ["--every"]:
        every, argv = float(argv[1]) / 1000, argv[2:]
    if argv[:1] == ["--"]:
        argv = argv[1:]
    if not argv:
        print(__doc__.strip())
        return 2
    before = pids()
    boot = time.time() - float(open("/proc/uptime").read().split()[0])
    t0 = time.time()
    env = dict(os.environ, WINEPREFIX=PREFIX)
    proc = subprocess.Popen(argv, env=env)
    seen, skip, done = {}, set(), None      # pid -> [name, start, end, cpu]
    while True:
        now = time.time()
        for p in pids() - before - skip - set(seen):
            st = stat(p)
            if st is None or not ours(p):
                skip.add(p)
            else:
                seen[p] = [name(p), st[0] + boot - t0, None, st[1]]
        for p, v in seen.items():
            if v[2] is None:
                st = stat(p)
                if st is None:
                    v[2] = now - t0
                else:
                    v[3] = st[1]
                    v[0] = name(p)      # Wine renames the process once the .exe is loaded
        if done is None and proc.poll() is not None:
            done = now - t0
        if done is not None and (all(v[2] is not None for v in seen.values())
                                 or now - t0 - done > 30):
            break
        time.sleep(every)
    wall = time.time() - t0
    rows = sorted(seen.values(), key=lambda v: v[1])
    end = lambda v: v[2] if v[2] is not None else wall
    if full:
        print("%8s %8s %8s  %s" % ("start", "wall", "cpu", "process"))
        for nm, st, en, cpu in rows:
            print("%8.2f %8.2f %8.2f  %s" % (st, end([nm, st, en]) - st, cpu, nm))
    else:
        tot = {}
        for nm, st, en, cpu in rows:
            t = tot.setdefault(nm, [0, 0.0, 0.0, st])
            t[0] += 1
            t[1] += end([nm, st, en]) - st
            t[2] += cpu
        print("%5s %8s %8s %8s  %s" % ("runs", "wall", "cpu", "first", "program"))
        for nm, (n, w, c, st) in sorted(tot.items(), key=lambda kv: kv[1][3]):
            print("%5d %8.2f %8.2f %8.2f  %s" % (n, w, c, st, nm))
    print("command: %.2f s wall (%.2f s until Wine shut down), exit %s" % (done, wall, proc.returncode))
    return proc.returncode


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
