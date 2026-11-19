#!/usr/bin/env python3
"""The loops of a decompiled tree that aren't For loops, with what is before them and the last lines of
their bodies: what stops a loop being recognised, at a glance.

usage: looptails.py DIR [--kind while|do] [--max N] [--grep TEXT]"""
import glob
import os
import re
import sys


def main(argv):
    d = argv[0]
    kind = argv[argv.index("--kind") + 1] if "--kind" in argv else None
    n = int(argv[argv.index("--max") + 1]) if "--max" in argv else 40
    pat = argv[argv.index("--grep") + 1] if "--grep" in argv else None
    shown = 0
    total = 0
    for f in sorted(glob.glob(os.path.join(d, "*.bas")) + glob.glob(os.path.join(d, "*.frm")) +
                    glob.glob(os.path.join(d, "*.cls"))):
        with open(f, encoding="cp1252", errors="replace") as h:
            lines = h.read().replace("\r", "").split("\n")
        for i, ln in enumerate(lines):
            m = re.match(r"( *)(Do While .*|Do)$", ln)
            if not m:
                continue
            if kind == "while" and not m.group(2).startswith("Do While") or \
                    kind == "do" and m.group(2) != "Do":
                continue
            end = next((j for j in range(i + 1, len(lines))
                        if re.match(m.group(1) + r"Loop\b", lines[j])), None)
            if end is None:
                continue
            total += 1
            if pat and not any(pat in x for x in lines[i - 3:end + 1]):
                continue
            if shown >= n:
                continue
            shown += 1
            print("--- %s:%d (%d lines)" % (os.path.basename(f), i + 1, end - i))
            for x in lines[max(0, i - 3):i + 2]:
                print("   ", x.strip()[:100])
            print("    ...")
            for x in lines[max(i + 2, end - 3):end + 1]:
                print("   ", x.strip()[:100])
    print("loops:", total)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
