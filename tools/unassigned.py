#!/usr/bin/env python3
"""Locals of a decompiled source that are read but never given a value: each is a store the
decompiler lost, or a temporary it could not follow; they compile, and quietly read 0.

usage: unassigned.py DIR [--all]
    reads      read in an expression, never assigned and never handed to a procedure
    passed     only handed to a procedure (it may be an output: lower confidence), shown
               with --all
A name in Dim is what is looked at; loop variables, ReDim, Get, Input, Set and the
statements that fill their argument count as assignments."""
import glob
import os
import re
import sys

HEAD = re.compile(r"(?:Public |Private |Friend )?(?:Static )?(?:Sub|Function|Property (?:Get|Let|Set)) (\w+)")
TAIL = re.compile(r"End (?:Sub|Function|Property)\b")
DIM = re.compile(r"\s*(?:Dim|Static) (\w+)(\(\))?(?: As (.+?))?\s*$")
NAME = r"(?:^|[^\w.])%s\b"


def scan(lines):
    """[(procedure, variable, type, kind, first line with it)]"""
    out, i, n = [], 0, len(lines)
    while i < n:
        m = HEAD.match(lines[i])
        if not m:
            i += 1
            continue
        e = i + 1
        while e < n and not TAIL.match(lines[e]):
            e += 1
        body = lines[i + 1:e]
        dims = {}
        code = []
        for x in body:
            d = DIM.match(x)
            if d:
                dims[d.group(1)] = (d.group(3) or "Variant", bool(d.group(2)))
            else:
                code.append(re.sub(r'"[^"]*"', '""', x))
        for v, (ty, _arr) in dims.items():
            word = re.compile(NAME % v)
            uses = [x for x in code if word.search(x)]
            if not uses:
                continue
            v_ = re.escape(v)
            set_ = re.compile(
                r"^\s*(?:\d+ )?(?:Set |LSet |RSet |Let )?%s(?:\([^=]*\))?(?:\.\w+(?:\([^=]*\))?)* = |"
                r"^\s*(?:\d+ )?For %s = |\bFor Each %s\b|\bReDim(?: Preserve)? %s\b|"
                r"^\s*(?:\d+ )?(?:Get|Input|Line Input)\b.*\b%s\b|\bMid\$?\(%s\b|\bOpen\b.*\b%s\b"
                % ((v_,) * 7))
            if any(set_.search(x) for x in uses):
                continue
            # (SendKeys "x", v: the Wait the program left out, reading Empty, which is False)
            if all(re.match(r"\s*(?:\d+ )?SendKeys ", x) for x in uses):
                continue
            # (a procedure's argument, in a statement or a function call: maybe an output)
            call = re.compile(r"\b(?!If\b|Then\b|Else\b|And\b|Or\b|Not\b)[A-Za-z_]\w*(?: |\().*\b%s\b" % v_)
            kind = "passed" if any(call.search(x) and "Proc_" in x or
                                   re.match(r"\s*(?:\d+ )?[A-Za-z_]\w* .*\b%s\b" % v_, x) and
                                   not re.match(r"\s*(?:\d+ )?(?:If|Put|Print|MsgBox|ElseIf|Do|Loop|Select)\b", x)
                                   for x in uses) else "reads"
            out.append((m.group(1), v, ty, kind, uses[0].strip()))
        i = e + 1
    return out


def main(argv):
    show_all = "--all" in argv
    d = [a for a in argv if not a.startswith("-")][0]
    tot = {"reads": 0, "passed": 0}
    for f in sorted(glob.glob(os.path.join(d, "*"))):
        if not f.lower().endswith((".bas", ".frm", ".cls", ".ctl")):
            continue
        lines = open(f, "rb").read().decode("cp1252").split("\r\n")
        for p, v, ty, kind, ln in scan(lines):
            tot[kind] += 1
            if kind == "reads" or show_all:
                print("%-7s %s %s %s As %s | %s" % (kind, os.path.basename(f), p, v, ty, ln[:90]))
    print("%d read, %d only passed" % (tot["reads"], tot["passed"]))


if __name__ == "__main__":
    main(sys.argv[1:])
