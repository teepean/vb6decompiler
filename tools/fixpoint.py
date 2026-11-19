#!/usr/bin/env python3
"""Decompile, build, decompile again: is the second source the first?  What differs once generated names are
taken out shows what a round through the compiler changed: a statement lost shows here even in code no run reaches.

usage: fixpoint.py FIRST_DIR SECOND_DIR [--show N] [--name TEXT] [--procs]

Compares the two folders file by file, in any spelling of the file names, procedure by procedure.  In a procedure
the frame's names (v2C, o12, t_eax, a_3, labels) are numbered in order of first use and Dim lines left out; in a
file Proc_7D1C00 and global_F04740 are numbered too, as both are addresses, and a record type's name is just TYPE.
Prints the files that differ, the most different first, then a count of what kind the differing lines are; --show N
prints N lines of each file's diff, --procs one line for each differing procedure.
"""
import argparse
import difflib
import os
import re
import sys
from collections import Counter

LOCAL = re.compile(r"\b(v[0-9A-F]+|o\d+|t_e[a-ds][xip]\d*|t_\d+|tmp\d+|a_\d+|T_\w+|L[0-9A-F]{4,})\b")
FILEWIDE = re.compile(r"\b(Proc_[0-9A-F]+|global_[0-9A-F]+)\b")
TYPES = re.compile(r"\b(t_\d+_\d+|trec\d+|TRec\d+|t_\w+_\d+)\b")
PROC = re.compile(r"^(?:(?:Public|Private|Friend|Static)\s+)*(Sub|Function|Property\s+(?:Get|Let|Set))\s+(\w+)", re.I)
ENDPROC = re.compile(r"^End\s+(Sub|Function|Property)\b", re.I)
SOURCE = (".bas", ".cls", ".frm", ".ctl", ".dob", ".pag")
CALLISH = re.compile(r"\b(\w+)\s*\(")


def read(path):
    with open(path, encoding="cp1252", newline="") as f:
        lines = f.read().split("\r\n")
    # the code, without the designer block: a build gives that the same anyway and frmcmp.py looks at it
    i = next((k for k, x in enumerate(lines) if x.startswith("Attribute VB_Name")), 0)
    return lines[i:]


def normalise(lines):
    """[(procedure name or None, [lines])] with the generated names numbered."""
    names = {}

    def filewide(m):
        w = m.group(1)
        kind = re.match(r"[A-Za-z]+", w).group(0)
        return names.setdefault(w, "%s#%d" % (kind, len(names)))

    out = []
    cur, body, local = None, [], {}

    def localname(m):
        return local.setdefault(m.group(1), "L#%d" % len(local))

    for ln in lines:
        if cur is not None and ln.strip().startswith("Dim "):
            continue            # (in frame order, and what first use numbers is what matters)
        ln = TYPES.sub("TYPE", FILEWIDE.sub(filewide, ln))
        m = PROC.match(ln)
        if m and cur is None:
            if body:
                out.append((None, body))
            cur, body, local = m.group(2), [], {}
        body.append(LOCAL.sub(localname, ln))
        if cur is not None and ENDPROC.match(ln):
            out.append((cur, body))
            cur, body = None, []
    if body:
        out.append((cur, body))
    return out


def kinds(line):
    t = line.strip()
    if t.startswith(("If ", "ElseIf ", "Else", "End If")):
        return "If"
    if t.startswith(("For ", "Next", "Do", "Loop", "While", "Wend", "Exit ")):
        return "loop"
    if t.startswith(("Dim ", "Static ", "ReDim ", "Erase ")):
        return "declaration"
    if t.startswith(("Select ", "Case ", "End Select")):
        return "Select"
    if t.startswith("GoTo ") or re.match(r"^\w+:$", t):
        return "GoTo or label"
    if t.startswith("Set "):
        return "Set"
    if " = " in t:
        return "assignment"
    return "call or other"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("first")
    ap.add_argument("second")
    ap.add_argument("--show", type=int, default=0)
    ap.add_argument("--name")
    ap.add_argument("--procs", action="store_true")
    o = ap.parse_args()
    second = {f.lower(): f for f in os.listdir(o.second)}
    results = []
    kind = Counter()
    onlyfirst = []
    same_procs = diff_procs = 0
    for f in sorted(os.listdir(o.first), key=str.lower):
        if not f.lower().endswith(SOURCE) or (o.name and o.name.lower() not in f.lower()):
            continue
        if f.lower() not in second:
            onlyfirst.append(f)
            continue
        a = normalise(read(os.path.join(o.first, f)))
        b = normalise(read(os.path.join(o.second, second[f.lower()])))
        bypos = {}
        for i, (n, body) in enumerate(b):
            bypos.setdefault(n, []).append(body)
        total, per_proc, shown = 0, [], []
        for n, body in a:
            other = bypos[n].pop(0) if bypos.get(n) else []
            if n is None:
                continue        # (the module's declarations: the types change with what they are built from)
            if body == other:
                same_procs += n is not None
                continue
            diff_procs += n is not None
            d = [x for x in difflib.unified_diff(body, other, lineterm="", n=0)
                 if not x.startswith(("---", "+++", "@@"))]
            for x in d:
                kind[("lost " if x[0] == "-" else "new ") + kinds(x[1:])] += 1
            total += len(d)
            per_proc.append((n, len(d), sum(x[0] == "-" for x in d), sum(x[0] == "+" for x in d)))
            shown += d
        for name, rest in bypos.items():
            for body in rest if name is not None else ():
                total += len(body)
                shown += ["+" + x for x in body]
        if total:
            results.append((total, f, per_proc, shown))
    for total, f, per_proc, shown in sorted(results, key=lambda r: -r[0]):
        print("%6d  %s  (%d procedures)" % (total, f, len(per_proc)))
        if o.procs:
            for n, c, lost, new in sorted(per_proc, key=lambda p: -p[1]):
                print("        %-40s %5d lines: %d lost, %d new" % (n, c, lost, new))
        for x in shown[:o.show]:
            print("    " + x[:170])
    print("%d files differ of those compared; procedures: %d the same, %d differ" % (len(results), same_procs, diff_procs))
    if onlyfirst:
        print("only in the first:", " ".join(onlyfirst))
    for k, n in kind.most_common():
        print("%7d  %s" % (n, k))
    return 0


if __name__ == "__main__":
    sys.exit(main())
