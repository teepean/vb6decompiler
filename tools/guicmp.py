#!/usr/bin/env python3
"""Compare what two builds of a GUI program show after starting: run each
under Wine (the VB6 prefix) in its own folder, wait, dump its windows with
tools/wintree (class, position, size, state, text of every window), end it,
and diff the dumps.  For programs that do not end by themselves (an
original and its decompiled build).

usage: guicmp.py EXE_A EXE_B [--wait S] [--poke pw:TEXT|click:CAPTION ...] [--args ...]

--poke (repeatable) does one step through tools/wintree/winpoke.exe in each
program after --wait, and the windows are compared after every step as well.
Steps: pw:TEXT click:CAPTION menu:CAPTION file:PATH sleep:SECONDS; {DIR} in PATH is the exe's folder.

Each EXE runs in its own folder (copy the program's data files next to
both).  Exit status 0 when the dumps are equal.
"""
import difflib
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import vbmake           # noqa: E402

WINTREE = os.path.join(HERE, "wintree", "wintree.exe")
WINPOKE = os.path.join(HERE, "wintree", "winpoke.exe")


def dump(exe, wait, args, pokes=()):
    """The windows after starting and after each poke: [lines, ...]."""
    env = dict(os.environ, WINEPREFIX=vbmake.PREFIX, WINEDEBUG="-all")
    d, name = os.path.split(os.path.abspath(exe))
    proc = subprocess.Popen(["wine", name] + list(args), cwd=d, env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(wait)
    out = os.path.join(d, "wintree.txt")

    def snap():
        if os.path.exists(out):
            os.remove(out)
        subprocess.run(["wine", WINTREE, "Z:" + out.replace("/", "\\")], env=env,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60)
        text = open(out, encoding="cp1252", errors="replace").read() if os.path.exists(out) else ""
        # windows of other programs in the session are not ours to compare
        return [ln for ln in text.replace("\r", "").split("\n") if ln]
    snaps = [snap()]
    for p in pokes:
        if p.startswith("sleep:"):      # (a long operation: an import)
            time.sleep(float(p[6:]))
            snaps.append(snap())
            continue
        verb, _, arg = p.partition(":")
        arg = arg.replace("{DIR}", "Z:" + d.replace("/", "\\"))
        r = subprocess.run(["wine", WINPOKE, verb, arg], env=env, timeout=60,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if r.returncode:
            print("%s: step %s found no window" % (name, verb))
        time.sleep(wait)
        snaps.append(snap())
    vbmake.kill_windows_process(os.path.join(d, name))
    try:
        proc.wait(10)
    except subprocess.TimeoutExpired:
        proc.kill()
    return snaps


def main(argv):
    wait = 10.0
    if "--wait" in argv:
        i = argv.index("--wait")
        wait = float(argv[i + 1])
        argv = argv[:i] + argv[i + 2:]
    pokes = []
    while "--poke" in argv:
        i = argv.index("--poke")
        pokes.append(argv[i + 1])
        argv = argv[:i] + argv[i + 2:]
    args = []
    if "--args" in argv:
        i = argv.index("--args")
        args = argv[i + 1:]
        argv = argv[:i]
    if len(argv) != 2:
        print(__doc__.strip())
        return 2
    aa = dump(argv[0], wait, args, pokes)
    bb = dump(argv[1], wait, args, pokes)
    same = True
    for k, (a, b) in enumerate(zip(aa, bb)):
        step = "start" if k == 0 else pokes[k - 1].partition(":")[0]    # (not a password)
        diff = list(difflib.unified_diff(a, b, "A", "B", lineterm="", n=1))
        print("== %s: %d windows / %d windows" % (step, len(a), len(b)))
        for side, x in (("A", a), ("B", b)):
            print("   %s top: %s" % (side, " | ".join(ln[2:70] for ln in x if ln.startswith("0 "))))
        for ln in diff:
            print(ln)
        same = same and not diff
    return 0 if same else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
