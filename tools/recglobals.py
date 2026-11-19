#!/usr/bin/env python3
"""Module variables declared with a record Type in decompiled source, and how
the code uses them: as numbers (g = 0, For i = 1 To g, ReDim a(g), g + 1,
F(g)...) or through fields (g.f8).  A record used only as a number is a
mistyped scalar (decompile.scalar_params).

usage: recglobals.py DIR [-v]
   -v  list every record-typed module variable used as a number
"""
import glob
import os
import re
import sys

SCALAR_USES = [r"\b%s = -?\d", r"To %s\b", r"ReDim \w+\(%s\)", r"\b%s [<>]=? -?\d",
               r"\b%s [-+*] ", r"[-+*] %s\b(?!\.)", r"\(%s\)", r"\b%s = \w+\(\w*\)"]


def scan(d):
    files = []
    for ext in ("bas", "frm", "cls"):
        files += glob.glob(os.path.join(d, "*." + ext))
    text = "\n".join(open(f, encoding="latin-1").read() for f in files)
    types = set(re.findall(r"^(?:Public |Private )?Type (\w+)", text, re.M))
    recs = {}
    for m in re.finditer(r"^(?:Public|Private|Dim) (global_[0-9A-F]+) As (\w+)\s*$", text, re.M):
        if m.group(2) in types:
            recs[m.group(1)] = m.group(2)
    out = []
    for g, t in recs.items():
        n = sum(len(re.findall(p % re.escape(g) + r"(?![.(\w])", text)) for p in SCALAR_USES)
        f = len(re.findall(re.escape(g) + r"\.f[0-9A-F]+", text))
        out.append((g, t, n, f))
    return out


def main(argv):
    verbose = "-v" in argv
    argv = [a for a in argv if a != "-v"]
    for d in argv:
        rows = scan(d)
        scalar = sorted((r for r in rows if r[2] and not r[3]), key=lambda r: -r[2])
        mixed = sorted((r for r in rows if r[2] and r[3]), key=lambda r: -r[2])
        print("%s: %d record-typed module variables, %d used only as numbers, %d both" % (
            d, len(rows), len(scalar), len(mixed)))
        if verbose:
            for g, t, n, f in scalar + mixed:
                print("   %-14s As %-8s number uses %4d  field uses %4d" % (g, t, n, f))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
