#!/usr/bin/env python3
"""Procedures whose line count changed between two decompiled projects (a pass that loses code shows).

usage: proclines.py DIR_A DIR_B [--min N]
Prints file, procedure, lines in A, lines in B for those that differ by at least N (default 1), biggest
loss first, and the totals.
"""
import glob
import os
import re
import sys

HEAD = re.compile(r"(?:(?:Public|Private|Friend) )?(?:Static )?(?:Sub|Function|Property \w+) (\w+)\(")
END = re.compile(r"End (?:Sub|Function|Property)\s*$")


def procs(d):
    out = {}
    for f in sorted(glob.glob(os.path.join(d, "*"))):
        if not f.lower().endswith((".bas", ".frm", ".cls", ".ctl")):
            continue
        cur, n = None, 0
        for ln in open(f, encoding="cp1252", errors="replace", newline=""):
            if cur is None:
                m = HEAD.match(ln)
                if m:
                    cur, n = m.group(1), 0
            else:
                n += 1
                if END.match(ln):
                    out[(os.path.basename(f), cur)] = n
                    cur = None
    return out


def main(argv):
    a, b = procs(argv[0]), procs(argv[1])
    lim = int(argv[argv.index("--min") + 1]) if "--min" in argv else 1
    rows = []
    for k in sorted(set(a) | set(b)):
        x, y = a.get(k, 0), b.get(k, 0)
        if abs(x - y) >= lim:
            rows.append((y - x, k, x, y))
    for d, k, x, y in sorted(rows, key=lambda r: r[0]):
        print("%-22s %-34s %6d %6d %+d" % (k[0], k[1], x, y, d))
    print("total %d -> %d lines, %d procedures differ" % (sum(a.values()), sum(b.values()), len(rows)))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
