#!/usr/bin/env python3
"""Insert a diagnostic Print # line after an anchor line in a VB source file
(CRLF kept): for hand-made copies of an original and a decompiled program,
to compare their state where two runs diverge (see buildrun.py).

usage: dumpdiag.py FILE ANCHOR LINE

ANCHOR must occur exactly once in FILE (a whole or partial line); LINE is
inserted after the line containing it, with the anchor line's indentation
plus four spaces.
"""
import sys


def main(argv):
    if len(argv) != 3:
        print(__doc__.strip())
        return 2
    path, anchor, line = argv
    t = open(path, encoding="latin-1", newline="").read()
    lines = t.split("\r\n")
    hits = [i for i, ln in enumerate(lines) if anchor in ln]
    if len(hits) != 1:
        print("== %d lines contain the anchor" % len(hits))
        return 1
    i = hits[0]
    ind = lines[i][:len(lines[i]) - len(lines[i].lstrip())] + "    "
    lines.insert(i + 1, ind + line)
    open(path, "w", encoding="latin-1", newline="").write("\r\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
