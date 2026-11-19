#!/usr/bin/env python3
"""Where a decompiled program fails at run time: copy its folder, trace every
procedure entry (instr.py), build, run with the given arguments, and print
the message box the run stopped at and the last procedures entered.

usage: firsterr.py DECOMPILED_DIR WORKDIR [--timeout S] [--last N] [--lines PROC]
                   [-- ARGS...]

--lines PROC also traces every statement of the procedure PROC (its line
number in its file), to find which statement fails inside it.

Data files the run needs must be in DECOMPILED_DIR (roundtrip.py copies a
test's rt.txt "data:" files there).  The traced build writes trace.txt in
WORKDIR; a procedure that raises an error is the last one entered before the
program's error handler (or the box).
"""
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import vbmake           # noqa: E402


def trace_lines(work, proc):
    """Put Tr "file:line" before every statement of procedure proc (in the
    copy): not before what continues a block (Else, Case, End If, Loop...),
    labels, Dims or continuation lines."""
    import re
    head = re.compile(r"^(Public |Private |Friend )?(Static )?(Sub|Function|Property \w+) %s\b"
                      % re.escape(proc))
    skip = re.compile(r"^(Else|ElseIf|Case|End |Loop|Next|Wend|Dim |Static |#|L[0-9A-F]+:|\w+:$)")
    for fn in os.listdir(work):
        if not fn.lower().endswith((".bas", ".cls", ".frm", ".ctl")):
            continue
        p = os.path.join(work, fn)
        lines = open(p, encoding="latin-1", newline="").read().split("\r\n")
        out, inside, cont = [], False, False
        for n, ln in enumerate(lines, 1):
            t = ln.strip()
            if inside and re.match(r"^End (Sub|Function|Property)", t):
                inside = False
            if inside and t and not cont and not skip.match(t):
                out.append('    Tr "%s:%d"' % (fn, n))
            out.append(ln)
            cont = ln.endswith(" _")
            if head.match(t):
                inside = True
        open(p, "w", encoding="latin-1", newline="").write("\r\n".join(out))


def main(argv):
    timeout, last = 120, 12
    args = []
    if "--" in argv:
        i = argv.index("--")
        argv, args = argv[:i], argv[i + 1:]
    if "--timeout" in argv:
        i = argv.index("--timeout")
        timeout = int(argv[i + 1])
        argv = argv[:i] + argv[i + 2:]
    if "--last" in argv:
        i = argv.index("--last")
        last = int(argv[i + 1])
        argv = argv[:i] + argv[i + 2:]
    lines_of = None
    if "--lines" in argv:
        i = argv.index("--lines")
        lines_of = argv[i + 1]
        argv = argv[:i] + argv[i + 2:]
    if len(argv) != 2:
        print(__doc__.strip())
        return 2
    src, work = argv
    if os.path.isdir(work):
        shutil.rmtree(work)
    shutil.copytree(src, work, ignore=shutil.ignore_patterns("*.exe", "*.log", "trace.txt"))
    if lines_of:
        trace_lines(work, lines_of)
    subprocess.run([sys.executable, os.path.join(HERE, "instr.py"), work], check=True)
    vbp = os.path.join(work, next(f for f in os.listdir(work) if f.lower().endswith(".vbp")))
    ok, log = vbmake.make(vbp)
    if not ok:
        print("== the traced build failed:\n" + log.strip())
        return 1
    exe = next(f for f in os.listdir(work) if f.lower().endswith(".exe"))
    done = vbmake.run(os.path.join(work, exe), timeout=timeout, args=args)
    if vbmake.LAST_BOX:
        print("== message box: " + vbmake.LAST_BOX[0])
    elif not done:
        print("== did not end in %d s" % timeout)
    else:
        print("== ended")
    tr = os.path.join(work, "trace.txt")
    lines = open(tr, encoding="latin-1").read().split("\n") if os.path.exists(tr) else []
    lines = [ln.strip() for ln in lines if ln.strip()]
    print("== %d procedure entries; the last %d:" % (len(lines), min(last, len(lines))))
    for ln in lines[-last:]:
        print("   " + ln)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
