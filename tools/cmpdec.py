#!/usr/bin/env python3
"""Decompile the same EXEs with two copies of the decompiler and compare:
time taken and the files written.  For checking that a change meant to be
behaviour-neutral (a speed-up) leaves the output alone.

usage: cmpdec.py OLDROOT NEWROOT OUTDIR EXE... [--nomap] [--timeout SECONDS]
                 [--seeds OLD,NEW]

OLDROOT / NEWROOT hold decompile.py and its vb6dec package (e.g. a copy of
the tree saved before the change, and this tree).  Each EXE is decompiled by
both into OUTDIR/<exe name>/old and .../new, one after the other.
Both runs get the same PYTHONHASHSEED (0), so a difference is the change's
and not the order of a set of strings; `--seeds 1,2` with the same tree
twice checks that the output does not depend on that order.
"""
import difflib
import os
import shutil
import subprocess
import sys
import time


def run(root, exe, out, nomap, timeout, seed):
    if os.path.isdir(out):
        shutil.rmtree(out)
    cmd = [sys.executable, os.path.join(root, "decompile.py"), exe]
    if nomap:
        cmd.append("--nomap")
    env = dict(os.environ, PYTHONHASHSEED=seed)
    t0 = time.time()
    try:
        r = subprocess.run(cmd + ["-o", out], capture_output=True, text=True, timeout=timeout,
                           env=env)
        ok = r.returncode == 0
    except subprocess.TimeoutExpired:
        return None, time.time() - t0
    return ok, time.time() - t0


def files(d):
    if not os.path.isdir(d):
        return {}
    return {f: open(os.path.join(d, f), "rb").read() for f in sorted(os.listdir(d))
            if f.lower().endswith((".bas", ".cls", ".frm", ".frx", ".ctl", ".ctx", ".vbp", ".res"))}


def main(argv):
    nomap = "--nomap" in argv
    argv = [a for a in argv if a != "--nomap"]
    timeout = None
    if "--timeout" in argv:
        i = argv.index("--timeout")
        timeout = float(argv[i + 1])
        argv = argv[:i] + argv[i + 2:]
    seeds = {"old": "0", "new": "0"}
    if "--seeds" in argv:
        i = argv.index("--seeds")
        seeds["old"], seeds["new"] = argv[i + 1].split(",")
        argv = argv[:i] + argv[i + 2:]
    if len(argv) < 4:
        print(__doc__.strip())
        return 2
    old, new, outdir = argv[0], argv[1], argv[2]
    same_all = True
    for exe in argv[3:]:
        name = os.path.splitext(os.path.basename(exe))[0]
        res = {}
        for tag, root in (("old", old), ("new", new)):
            res[tag] = run(root, os.path.abspath(exe), os.path.join(outdir, name, tag), nomap,
                           timeout, seeds[tag])
        fo, fn = files(os.path.join(outdir, name, "old")), files(os.path.join(outdir, name, "new"))
        diff = sorted(f for f in set(fo) | set(fn) if fo.get(f) != fn.get(f))

        def show(r):
            ok, t = r
            return "%7.1fs%s" % (t, "" if ok else " TIMEOUT" if ok is None else " FAILED")
        print("%-14s old %s  new %s  %s" % (name, show(res["old"]), show(res["new"]),
                                            "same output" if not diff else
                                            "%d file(s) differ" % len(diff)), flush=True)
        if res["old"][0] is None or res["new"][0] is None:
            continue                    # (no complete output to compare)
        for f in diff:
            same_all = False
            a = fo.get(f, b"").decode("latin-1").replace("\r", "").split("\n")
            b = fn.get(f, b"").decode("latin-1").replace("\r", "").split("\n")
            lines = list(difflib.unified_diff(a, b, "old/" + f, "new/" + f, n=1, lineterm=""))
            for ln in lines[:40]:
                print("    " + ln)
            if len(lines) > 40:
                print("    ... %d more diff lines" % (len(lines) - 40))
    return 0 if same_all else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
