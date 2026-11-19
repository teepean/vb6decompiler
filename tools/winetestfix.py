#!/usr/bin/env python3
"""A test build of a census copy with Wine's false errors worked round.

VB6 under Wine rejects `Me.lv.Font.Size = 11` and `Me.il.ListImages(1).Picture`
("Variable uses an Automation type not supported"); VB6 on Windows builds
them, so tools/errcensus.py empties procedures that are right.  This restores
those procedures from the decompiled source, reaching the control through a
helper returning Object (late bound, same behaviour at run time).  Only for
running a build under Wine: never decompiler output.

usage: winetestfix.py DECOMPILED_DIR CENSUS_DIR CENSUS_TXT OUT_DIR

OUT_DIR must not exist.  The other emptied procedures stay empty.
"""
import os
import re
import shutil
import sys

HELPER = ("\r\nPublic Function WineObj(o As Object) As Object\r\n"
          "    Set WineObj = o\r\nEnd Function\r\n")
REWRITE = re.compile(r"\b((?:Me\.)?\w+(?:\(\w+\))?)\.(Font\.|ListImages\.)")
EXCLUDE = {"App", "Printer", "Screen", "Me"}


def read(p):
    return open(p, encoding="cp1252", newline="").read()


def write(p, s):
    open(p, "w", encoding="cp1252", newline="").write(s)


def proc_span(lines, name):
    pat = re.compile(r"(Private |Public |Friend )?(Static )?(Sub|Function|Property \w+) %s\(" % name)
    for i, ln in enumerate(lines):
        if pat.match(ln):
            for j in range(i + 1, len(lines)):
                if re.match(r"End (Sub|Function|Property)", lines[j]):
                    return i, j
    return None


def fixline(ln):
    def sub(m):
        if m.group(1) in EXCLUDE:
            return m.group(0)
        return "WineObj(%s).%s" % (m.group(1), m.group(2))
    return REWRITE.sub(sub, ln)


def main(argv):
    if len(argv) != 4:
        print(__doc__.strip())
        return 2
    dec, census, txt, out = argv
    shutil.copytree(census, out, ignore=shutil.ignore_patterns("*.exe", "*.log", "*.OBJ"))
    todo = []
    for ln in read(txt).splitlines():
        if "Variable uses an Automation type" in ln and "|" in ln:
            f = ln.split()
            todo.append((f[0], f[1]))
    for fn, proc in todo:
        src = read(os.path.join(dec, fn)).split("\r\n")
        dst = read(os.path.join(out, fn)).split("\r\n")
        a, b = proc_span(src, proc), proc_span(dst, proc)
        if a is None or b is None:
            print("not found:", fn, proc)
            continue
        body = [fixline(x) for x in src[a[0]:a[1] + 1]]
        write(os.path.join(out, fn), "\r\n".join(dst[:b[0]] + body + dst[b[1] + 1:]))
        print("restored", fn, proc)
    mods = sorted(x for x in os.listdir(out) if x.lower().endswith(".bas"))
    p = os.path.join(out, mods[0])
    write(p, read(p).rstrip("\r\n") + "\r\n" + HELPER)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
