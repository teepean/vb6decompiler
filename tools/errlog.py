#!/usr/bin/env python3
"""A test copy of a decompiled project that logs its run-time errors.

Numbers the plain statements of every procedure, and makes the program's
common error handler (a Sub taking the procedure's name) append procedure,
Err.Number, Erl and description to errors.log next to the exe, without any
message box.  A program with On Error + Resume Next then tells what fails and
where, all of it, in one run; the number is a line of the copy.

usage: errlog.py SRC_DIR OUT_DIR HANDLER_SUB [FILE_TO_NUMBER ...]

Numbers only the files named (VB6 under Wine died without a message on a
large program with every file numbered); without any, Erl stays 0.
OUT_DIR must not exist.  SRC_DIR is not changed.
"""
import os
import re
import shutil
import sys

SKIP = re.compile(r"(?i)^(Case\b|Else\b|ElseIf\b|End\b(?!\s*$)|Loop\b|Next\b|Wend\b|Dim\b|Static\b|Const\b|"
                  r"Private\b|Public\b|Friend\b|Sub\b|Function\b|Property\b|Declare\b|Type\b|'|L[0-9A-F]+:|\w+:\s*$)")


def number(text, start):
    """Line numbers in the code part of a file (after the Attribute lines)."""
    lines = text.split("\r\n")
    last = max((i for i, ln in enumerate(lines) if ln.startswith("Attribute ")), default=-1)
    inproc, n = False, start
    for i in range(last + 1, len(lines)):
        ln = lines[i]
        s = ln.strip()
        if re.match(r"(?i)^(Private |Public |Friend )?(Static )?(Sub|Function|Property \w+) ", s):
            inproc = True
            continue
        if re.match(r"(?i)^End (Sub|Function|Property)\s*$", s):
            inproc = False
            continue
        if not inproc or not s or SKIP.match(s) or lines[i - 1].rstrip().endswith(" _"):
            continue
        n += 1
        lines[i] = "%d %s" % (n, s)
    return "\r\n".join(lines), n


def main(argv):
    if len(argv) < 3:
        print(__doc__.strip())
        return 2
    src, out, handler = argv[:3]
    tonumber = {x.lower() for x in argv[3:]}
    shutil.copytree(src, out, ignore=shutil.ignore_patterns("*.exe", "*.log", "*.OBJ", "mk*.log"))
    n, index = 0, []
    pat = re.compile(r"(?i)^(Private |Public )?Sub %s\((\w+)[^)]*\)" % handler)
    for fn in sorted(os.listdir(out)):
        if not fn.lower().endswith((".frm", ".bas", ".cls", ".ctl")):
            continue
        p = os.path.join(out, fn)
        text = open(p, encoding="cp1252", newline="").read()
        lines = text.split("\r\n")
        for i, ln in enumerate(lines):
            m = pat.match(ln)
            if m:
                j = next(k for k in range(i, len(lines)) if lines[k].startswith("End Sub"))
                body = ["    Dim erln As Long, erls As String, erlf As Integer",
                        "    erln = Err.Number: erls = Err.Description: erlf = FreeFile",
                        '    Open App.Path & "\\errors.log" For Append As #erlf',
                        '    Print #erlf, %s; " "; erln; " "; Erl; " "; erls' % m.group(2),
                        "    Close #erlf"]
                lines[i + 1:j] = body
                text = "\r\n".join(lines)
                # (numbered below: the body's own lines take numbers too, harmless)
                break
        first = n
        if fn.lower() in tonumber:
            text, n = number(text, n)
        index.append("%s %d-%d" % (fn, first + 1, n))
        open(p, "w", encoding="cp1252", newline="").write(text)
    open(os.path.join(out, "errlog.index"), "w").write("\n".join(index) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
