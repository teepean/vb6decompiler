#!/usr/bin/env python3
"""The first procedure call where two runs part: two trace.txt files of tools/firsterr.py (an original
build's and a decompiled build's), compared by procedure name, with what came before.

usage: tracecmp.py A.txt B.txt [--context N] [--ignore NAME,NAME...]

--ignore leaves out procedures only a timer starts, as the clock decides when they run (cyccaption,
infos, menuupdate in DarwinBots): in a program that has them, runs part there first."""
import sys


IGNORE = set()


def names(path):
    with open(path, "rb") as f:
        for ln in f:
            ln = ln.strip().decode("latin-1")
            if ln and not ln.endswith(("_Timer", "_Paint", "_Resize", "_Activate")):
                # (what the clock and the window manager start: not the program's own course)
                n = ln.rsplit(".", 1)[-1].lower()
                if n not in IGNORE:
                    yield n, ln


def main(argv):
    ctx = int(argv[argv.index("--context") + 1]) if "--context" in argv else 8
    if "--ignore" in argv:
        IGNORE.update(x.lower() for x in argv[argv.index("--ignore") + 1].split(","))
    a, b = names(argv[0]), names(argv[1])
    last = []
    n = 0
    for (x, xs), (y, ys) in zip(a, b):
        if x != y:
            print("after %d calls:" % n)
            for p in last[-ctx:]:
                print("   %s" % (p,))
            print(" A %s\n B %s" % (xs, ys))
            for k, ((x2, xs2), (y2, ys2)) in enumerate(zip(a, b)):
                if k >= ctx:
                    break
                print(" A %s\n B %s" % (xs2, ys2))
            return 1
        last.append(xs)
        if len(last) > 100:
            del last[:50]
        n += 1
    print("the same for %d calls" % n)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
