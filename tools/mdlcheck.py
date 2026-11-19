#!/usr/bin/env python3
"""Do the VB header's model object flags (vb6dec/detect.py MODEL_OBJECTS) say what a project's forms hold?  Compares
them with the Begin VB.x lines of the source a suite run left.

usage: mdlcheck.py SUITE_WORK ...     (rtsuite.py's folder: NAME/nomap/orig has the source and the EXE)
"""
import glob
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from vb6dec import detect                   # noqa: E402

NAMES = {t for b, t in detect.MODEL_OBJECTS}


def main(argv):
    same = bad = 0
    extra_all, lost_all = {}, {}
    for work in argv:
        for orig in sorted(glob.glob(os.path.join(work, "*", "nomap", "orig"))):
            exes = glob.glob(os.path.join(orig, "*.exe"))
            if not exes:
                continue
            try:
                mdl = detect.detect(exes[0]).get("mdl")
            except Exception:
                continue
            if not mdl:
                continue
            want = set()
            for f in glob.glob(os.path.join(orig, "*.frm")) + glob.glob(os.path.join(orig, "*.ctl")):
                text = open(f, encoding="latin-1").read()
                want |= set(re.findall(r"^\s*Begin VB\.(\w+)", text, re.M)) & NAMES
            said = {t for b, t in detect.MODEL_OBJECTS if mdl[0] & b}
            lost, extra = want - said, said - want
            for t in extra:
                extra_all[t] = extra_all.get(t, 0) + 1
            for t in lost:
                lost_all[t] = lost_all.get(t, 0) + 1
            if lost or extra:
                bad += 1
                print("%-14s %08X lost %s extra %s" % (os.path.basename(os.path.dirname(os.path.dirname(orig))), mdl[0],
                                                       sorted(lost), sorted(extra)))
            else:
                same += 1
    print("mdlcheck: %d same, %d differ; never in the source but flagged %s; in the source but not flagged %s" % (
        same, bad, extra_all, lost_all))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
