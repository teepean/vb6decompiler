#!/usr/bin/env python3
"""Compare the screenshots of two tools/runshot.py runs, window by window.

usage: shotcmp.py DIR_A DIR_B [DIFF_DIR]
A picture in only one folder is a window only that run showed.  Prints the pixels that differ for each;
with DIFF_DIR saves the differences as pictures.  Exit status 0 when all are the same.
"""
import os
import subprocess
import sys


def main(argv):
    if len(argv) < 2:
        print(__doc__.strip())
        return 2
    a, b = argv[:2]
    out = argv[2] if len(argv) > 2 else None
    if out:
        os.makedirs(out, exist_ok=True)
    fa = {f for f in os.listdir(a) if f.endswith(".png")}
    fb = {f for f in os.listdir(b) if f.endswith(".png")}
    bad = 0
    for f in sorted(fa | fb):
        if f not in fa or f not in fb:
            print("%-60s only in %s" % (f, a if f in fa else b))
            bad += 1
            continue
        r = subprocess.run(["compare", "-metric", "AE", os.path.join(a, f), os.path.join(b, f),
                            os.path.join(out, f) if out else "null:"],
                           capture_output=True, text=True)
        n = r.stderr.split(" ")[0]
        same = n.strip() == "0"
        print("%-60s %s" % (f, "same" if same else n + " pixels"))
        bad += not same
    print("%d of %d differ" % (bad, len(fa | fb)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
