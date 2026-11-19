#!/usr/bin/env python3
"""emptyhandlers.py DIR: On Error GoTo targets whose label has no statements after it.

A handler with nothing in it is a sign the decompiler lost the handler's body
(the original's own empty handler would at least hold Resume or the implicit
__vbaExitProc, so look at the disassembly before believing it).
Prints FILE:LINE PROC LABEL and a count per file type.
"""
import glob
import os
import re
import sys

END = re.compile(r"^\s*End (Sub|Function|Property)\b", re.I)
SUB = re.compile(r"^\s*(?:(?:Public|Private|Friend|Static)\s+)*(?:Sub|Function|Property\s+\w+)\s+(\w+)", re.I)
ONERR = re.compile(r"^\s*On Error GoTo (\w+)", re.I)
LABEL = re.compile(r"^\s*(\w+):\s*$")


def scan(path):
    with open(path, encoding="cp1252", errors="replace") as f:
        lines = f.read().splitlines()
    proc, targets = None, set()
    for i, s in enumerate(lines):
        m = SUB.match(s)
        if m and not s.lstrip().lower().startswith("declare"):
            proc, targets = m.group(1), set()
            continue
        m = ONERR.match(s)
        if m:
            targets.add(m.group(1))
            continue
        m = LABEL.match(s)
        if m and m.group(1) in targets and i + 1 < len(lines) and END.match(lines[i + 1]):
            yield i + 1, proc, m.group(1)


def main():
    total = 0
    for p in sorted(glob.glob(os.path.join(sys.argv[1], "*"))):
        if p.lower().endswith((".frm", ".bas", ".cls", ".ctl")):
            for line, proc, label in scan(p):
                print("%s:%d %s %s" % (os.path.basename(p), line, proc, label))
                total += 1
    print("%d empty handler(s)" % total)


if __name__ == "__main__":
    main()
