#!/usr/bin/env python3
"""Run two builds of a program with the same arguments and compare the files they write.

usage: runcmp.py ORIG_EXE DEC_EXE [--timeout S] [--compare FILE ...] -- ARGS...
Each build runs in its own folder; a message box it stops at is reported (vbmake.LAST_BOX).  Prints
both files' lines side by side from the first difference, and the seconds each run took.
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import vbmake           # noqa: E402


def main(argv):
    args = []
    if "--" in argv:
        i = argv.index("--")
        argv, args = argv[:i], argv[i + 1:]
    timeout = 120
    if "--timeout" in argv:
        i = argv.index("--timeout")
        timeout = int(argv[i + 1])
        del argv[i:i + 2]
    names = ["out.txt"]
    if "--compare" in argv:
        i = argv.index("--compare")
        names = argv[i + 1:]
        del argv[i:]
    got = []
    for exe in argv[:2]:
        d = os.path.dirname(os.path.abspath(exe))
        for f in names:
            p = os.path.join(d, f)
            if os.path.exists(p):
                os.remove(p)
        t0 = time.time()
        done = vbmake.run(os.path.abspath(exe), timeout=timeout, args=args)
        t = time.time() - t0
        print("%s: %s in %.1f s%s" % (exe, "ended" if done else "DID NOT END", t,
                                      " box: %s" % vbmake.LAST_BOX[0][:160] if not done and vbmake.LAST_BOX else ""))
        got.append({f: open(os.path.join(d, f), "rb").read() if os.path.exists(os.path.join(d, f)) else None
                    for f in names})
    bad = 0
    for f in names:
        a, b = got[0][f], got[1][f]
        if a == b:
            print("%s: the same (%s bytes)" % (f, None if a is None else len(a)))
            continue
        bad += 1
        la = (a or b"").decode("latin-1").replace("\r", "").split("\n")
        lb = (b or b"").decode("latin-1").replace("\r", "").split("\n")
        i = next((i for i in range(min(len(la), len(lb))) if la[i] != lb[i]), min(len(la), len(lb)))
        print("%s DIFFERS from line %d (%d and %d lines)" % (f, i + 1, len(la), len(lb)))
        for k in range(i, min(i + 6, max(len(la), len(lb)))):
            print("  A %s" % (la[k][:150] if k < len(la) else "<end>"))
            print("  B %s" % (lb[k][:150] if k < len(lb) else "<end>"))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
