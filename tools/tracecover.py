#!/usr/bin/env python3
"""Which procedures of a decompiled source did a traced run (tools/firsterr.py's trace.txt) never enter?
What a comparison of two runs says nothing about.

usage: tracecover.py DIR TRACE.txt [--list]    (one line per module: entered / all; --list names them)"""
import glob
import os
import re
import sys

HEAD = re.compile(r"(?:Public |Private |Friend )?(?:Static )?(?:Sub|Function|Property \w+) (\w+)\(")


def main(argv):
    if len(argv) < 2:
        print(__doc__.strip())
        return 2
    seen = set()
    with open(argv[1], "rb") as f:
        for ln in f:
            seen.add(ln.strip().decode("latin-1"))
    total = entered = 0
    for p in sorted(glob.glob(os.path.join(argv[0], "*.bas")) + glob.glob(os.path.join(argv[0], "*.frm")) +
                    glob.glob(os.path.join(argv[0], "*.cls"))):
        fn = os.path.basename(p)
        with open(p, encoding="cp1252", newline="") as f:
            names = [m.group(1) for ln in f.read().split("\r\n") if (m := HEAD.match(ln))]
        missed = [n for n in names if "%s.%s" % (fn, n) not in seen]
        total += len(names)
        entered += len(names) - len(missed)
        if names:
            print("%-28s %4d / %4d" % (fn, len(names) - len(missed), len(names)))
            if "--list" in argv and missed:
                print("    " + " ".join(missed))
    print("%-28s %4d / %4d" % ("all", entered, total))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
