#!/usr/bin/env python3
"""What each of Microsoft's files is worth to the output: decompile a program with each taken away in
turn, then with all of them away but those given, and count the lines of output that change.

usage: needfiles.py EXE WORK [VAR=PATH ...]

WORK must be a new folder (private when EXE is).  VAR=PATH is a file to use in the last run, as a friend's
machine might have it: VB6OLB=/some/where/Vb6.olb.  Writes WORK/full, WORK/no_VAR, WORK/given.
"""
import difflib
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
VARS = ("VB6OLB", "MSVBVM60", "STDOLE2", "VBWIN32API", "VBSYSDIR", "VBOCXDIR", "VB6WINE")
NOWHERE = "/nonexistent/needfiles"
TEXT = (".bas", ".cls", ".frm", ".ctl", ".vbp", ".pag", ".dsr")


def run(exe, out, env):
    r = subprocess.run([sys.executable, os.path.join(ROOT, "decompile.py"), exe, "-o", out], cwd=ROOT,
                       env=dict(os.environ, **env), capture_output=True, text=True, errors="replace",
                       timeout=1500)
    return r.returncode, (r.stderr or r.stdout).strip().split("\n")[-1][:100] if r.returncode else ""


def lines(top):
    out = {}
    for name in os.listdir(top) if os.path.isdir(top) else ():
        if name.lower().endswith(TEXT):
            out[name] = open(os.path.join(top, name), encoding="cp1252", errors="replace", newline="").read().split("\r\n")
    return out


def changed(a, b):
    n = 0
    files = 0
    for name in sorted(set(a) | set(b)):
        x, y = a.get(name, []), b.get(name, [])
        if x != y:
            files += 1
            n += sum(1 for d in difflib.ndiff(x, y) if d[:1] in "+-") if len(x) + len(y) < 4000 else \
                sum(1 for d in difflib.unified_diff(x, y, lineterm="", n=0) if d[:1] in "+-" and d[:3] not in ("---", "+++"))
    return n, files


def main(argv):
    if len(argv) < 2:
        print(__doc__.strip())
        return 2
    exe, work = argv[0], argv[1]
    given = dict(a.split("=", 1) for a in argv[2:])
    os.makedirs(work)
    env = {"VB6DEC_SNAPSHOTS": os.path.join(work, "snapshots")}
    os.makedirs(env["VB6DEC_SNAPSHOTS"])
    code, err = run(exe, os.path.join(work, "full"), env)
    base = lines(os.path.join(work, "full"))
    print("%-16s exit %d  %d lines in %d files %s" % ("everything", code, sum(map(len, base.values())), len(base), err), flush=True)
    for v in VARS:
        out = os.path.join(work, "no_" + v)
        code, err = run(exe, out, dict(env, **{v: NOWHERE}))
        n, f = changed(base, lines(out))
        print("%-16s exit %d  %d lines change in %d files %s" % ("no " + v, code, n, f, err), flush=True)
    out = os.path.join(work, "given")
    only = {v: given.get(v, NOWHERE) for v in VARS}
    code, err = run(exe, out, dict(env, **only))
    n, f = changed(base, lines(out))
    print("%-16s exit %d  %d lines change in %d files %s" % ("only given", code, n, f, err), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
