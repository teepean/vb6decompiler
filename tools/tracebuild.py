#!/usr/bin/env python3
"""A traced build of a decompiled program, for running by hand or through runshot.py: every procedure
entered goes to trace.txt beside the program, and every statement of the procedures named.

usage: tracebuild.py DECOMPILED_DIR WORK [--lines PROC ...] [--skip REGEX]
WORK must be a new folder.  --skip leaves procedures out of the trace (the often-called ones).  The line
numbers in the trace are those of DECOMPILED_DIR's files.  Prints the path of the program, or the error.
"""
import argparse
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import firsterr  # noqa: E402
import vbmake  # noqa: E402


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("work")
    ap.add_argument("--lines", action="append", default=[])
    ap.add_argument("--skip")
    a = ap.parse_args(argv)
    shutil.copytree(a.src, a.work, ignore=shutil.ignore_patterns("*.exe", "*.log", "*.OBJ", "make_box.png"))
    for proc in a.lines:
        firsterr.trace_lines(a.work, proc)
    env = dict(os.environ)
    if a.skip:
        env["VB6TRACE_SKIP"] = a.skip
    subprocess.run([sys.executable, os.path.join(HERE, "instr.py"), a.work], env=env, check=True,
                   stdout=subprocess.DEVNULL)
    vbp = next(f for f in os.listdir(a.work) if f.lower().endswith(".vbp"))
    ok, log = vbmake.make(os.path.join(a.work, vbp))
    if not ok:
        print(log.strip().split("\n")[-1])
        return 1
    print(next(os.path.join(a.work, f) for f in os.listdir(a.work) if f.lower().endswith(".exe")))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
