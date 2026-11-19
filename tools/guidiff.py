#!/usr/bin/env python3
"""Run two builds of a GUI program through the same steps from the same state, and compare everything
they leave: the windows, the whole registry of the Wine prefix, the files in the program's folder.

usage: guidiff.py EXE_A EXE_B WORK [--data PATH ...] [--reg KEY|NAME|TYPE|VALUE ...] [--wait S]
                  [--step S] [--poke STEP ...] [--watch FILE ...] [-- ARGS]

WORK must be a new folder.  Each build runs from WORK/run, under the original's file name, with --data
copied beside it, so that paths in what it writes are the same; the folder is kept as WORK/A/files and
WORK/B/files.  Before each run the prefix's registry is put back as it was (plus --reg values, what `wine
reg add` takes, as a known starting point) and --watch files in the prefix with it; afterwards too.  The
steps are those of tools/runshot.py.

This ends the prefix's wineserver between runs, so nothing else may be using the prefix: not rtsuite, not
a VB6 build.  It says so and stops when it finds a VB6 there.  Exit status 0 when all is the same.
"""
import argparse
import difflib
import filecmp
import os
import re
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import vbmake  # noqa: E402

REG_FILES = ("system.reg", "user.reg", "userdef.reg")
ENV = dict(os.environ, WINEPREFIX=vbmake.PREFIX, WINEDEBUG="-all")


def kill_server():
    subprocess.run(["wineserver", "-k"], env=ENV, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                   timeout=60)
    subprocess.run(["wineserver", "-w"], env=ENV, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                   timeout=60)


def spellings(f):
    """The files in the prefix that are --watch file f to a Windows program, whatever their case: a
    program that deletes hosts and writes HOSTS leaves both names about under Wine otherwise."""
    d, name = os.path.split(os.path.join(vbmake.PREFIX, f))
    try:
        return [os.path.join(d, n) for n in os.listdir(d) if n.lower() == name.lower()]
    except OSError:
        return []


def save(base, watch):
    os.makedirs(base)
    for f in REG_FILES:
        shutil.copy2(os.path.join(vbmake.PREFIX, f), base)
    for i, f in enumerate(watch):
        for p in spellings(f)[:1]:
            shutil.copy2(p, os.path.join(base, "watch%d" % i))


def restore(base, watch):
    kill_server()
    for f in REG_FILES:
        shutil.copy2(os.path.join(base, f), vbmake.PREFIX)
    for i, f in enumerate(watch):
        for p in spellings(f):
            os.remove(p)                # (only the file named by --watch, in whatever case)
        s = os.path.join(base, "watch%d" % i)
        if os.path.exists(s):
            shutil.copy2(s, os.path.join(vbmake.PREFIX, f))


def registry_lines(d):
    """The registry a run left, without what says only when: the timestamps on keys."""
    out = []
    for f in REG_FILES:
        text = open(os.path.join(d, f), encoding="latin-1", errors="replace").read()
        for ln in text.split("\n"):
            if ln.startswith("#time=") or ln.startswith(";; All keys relative") or ln.startswith("#arch"):
                continue
            out.append(f + ": " + re.sub(r"^(\[.*\]) \d+$", r"\1", ln))
    return out


def run_one(label, exe, a, work, base, script_args):
    restore(base, a.watch)
    for spec in a.reg:
        key, name, typ, value = spec.split("|", 3)
        subprocess.run(["wine", "reg", "add", key, "/v", name, "/t", typ, "/d", value, "/f"], env=ENV,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60)
    run = os.path.join(work, "run")
    os.makedirs(run)
    target = os.path.join(run, os.path.basename(a.exe_a))
    shutil.copy2(exe, target)
    for p in a.data:
        (shutil.copytree if os.path.isdir(p) else shutil.copy2)(p, os.path.join(run, os.path.basename(p)))
    d = os.path.join(work, label)
    os.makedirs(d)
    cmd = [sys.executable, os.path.join(HERE, "runshot.py"), target, os.path.join(d, "shots"),
           "--wait", str(a.wait), "--step", str(a.step)] + script_args
    r = subprocess.run(cmd, capture_output=True, text=True, errors="replace", timeout=3600)
    open(os.path.join(d, "run.log"), "w").write(r.stdout + r.stderr)
    kill_server()
    time.sleep(0.5)
    for f in REG_FILES:
        shutil.copy2(os.path.join(vbmake.PREFIX, f), d)
    for i, f in enumerate(a.watch):
        for p in spellings(f)[:1]:
            shutil.copy2(p, os.path.join(d, "watch%d" % i))
    os.rename(run, os.path.join(d, "files"))
    return r.stdout


def compare_shots(a, b, out):
    r = subprocess.run([sys.executable, os.path.join(HERE, "shotcmp.py"), a, b, out], capture_output=True,
                       text=True)
    lines = r.stdout.strip().split("\n")
    return r.returncode == 0, [ln for ln in lines[:-1] if not ln.endswith(" same")], lines[-1]


def compare_files(a, b, skip):
    bad = []
    names = set()
    for top, base in ((a, a), (b, b)):
        for root, _, files in os.walk(top):
            for f in files:
                names.add(os.path.relpath(os.path.join(root, f), base))
    for n in sorted(names - skip):
        pa, pb = os.path.join(a, n), os.path.join(b, n)
        if not os.path.exists(pa) or not os.path.exists(pb):
            bad.append("%s: only in %s" % (n, "B" if os.path.exists(pb) else "A"))
        elif not filecmp.cmp(pa, pb, shallow=False):
            bad.append("%s: differs (%d, %d bytes)" % (n, os.path.getsize(pa), os.path.getsize(pb)))
    return bad, len(names - skip)


def main(argv):
    extra = []
    if "--" in argv:
        i = argv.index("--")
        argv, extra = argv[:i], argv[i:]
    ap = argparse.ArgumentParser()
    ap.add_argument("exe_a")
    ap.add_argument("exe_b")
    ap.add_argument("work")
    ap.add_argument("--data", action="append", default=[])
    ap.add_argument("--reg", action="append", default=[])
    ap.add_argument("--wait", type=float, default=15)
    ap.add_argument("--step", type=float, default=3)
    ap.add_argument("--poke", action="append", default=[])
    ap.add_argument("--watch", action="append", default=[])
    a = ap.parse_args(argv)
    if subprocess.run(["pgrep", "-x", "VB6.EXE"], capture_output=True).stdout:
        print("guidiff: a VB6 is running; this would end the wineserver of the prefix it may be in")
        return 2
    os.makedirs(a.work)
    base = os.path.join(a.work, "base")
    kill_server()
    save(base, a.watch)
    script = []
    for p in a.poke:
        script += ["--poke", p]
    script += extra
    try:
        run_one("A", a.exe_a, a, a.work, base, script)
        run_one("B", a.exe_b, a, a.work, base, script)
    finally:
        restore(base, a.watch)
    A, B = os.path.join(a.work, "A"), os.path.join(a.work, "B")
    same = True
    ok, lines, last = compare_shots(os.path.join(A, "shots"), os.path.join(B, "shots"),
                                    os.path.join(a.work, "diff"))
    print("windows:  " + last)
    for ln in lines:
        print("   " + ln[:110])
    same = same and ok
    ra, rb = registry_lines(A), registry_lines(B)
    if ra == rb:
        print("registry: same (%d lines, of which %d new)" % (
            len(ra), len(set(ra) - set(registry_lines(base)))))
    else:
        same = False
        d = [ln for ln in difflib.unified_diff(ra, rb, "A", "B", lineterm="", n=0)
             if not ln.startswith(("---", "+++", "@@"))]
        print("registry: %d lines differ" % len(d))
        for ln in d[:12]:
            print("   " + ln[:150])
    skip = {os.path.basename(a.exe_a)}
    bad, n = compare_files(os.path.join(A, "files"), os.path.join(B, "files"), skip)
    print("files:    %s" % ("%d same" % n if not bad else "%d of %d differ" % (len(bad), n)))
    for ln in bad[:12]:
        print("   " + ln[:150])
    same = same and not bad
    for i, f in enumerate(a.watch):
        pa, pb = os.path.join(A, "watch%d" % i), os.path.join(B, "watch%d" % i)
        if os.path.exists(pa) != os.path.exists(pb) or \
                os.path.exists(pa) and not filecmp.cmp(pa, pb, shallow=False):
            print("watch:    %s differs" % f)
            same = False
    la = [x for x in open(os.path.join(A, "run.log")).read().split("\n") if x.startswith(("box:", "crash:"))]
    lb = [x for x in open(os.path.join(B, "run.log")).read().split("\n") if x.startswith(("box:", "crash:"))]
    print("boxes:    %s" % ("same (%d)" % len(la) if la == lb else "differ"))
    if la != lb:
        same = False
        for x in la[:6]:
            print("   A " + x[:130])
        for x in lb[:6]:
            print("   B " + x[:130])
    return 0 if same else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
