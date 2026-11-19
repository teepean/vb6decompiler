#!/usr/bin/env python3
"""Build a project once and print its first compile error with the source line it means.

usage: builderr.py PROJECT.vbp

VB counts lines its own way (roundtrip.source_line): the number it reports is not the file's."""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from roundtrip import compile_errors, source_line    # noqa: E402


def main(argv):
    vbp = os.path.abspath(argv[0])
    r = subprocess.run([sys.executable, os.path.join(HERE, "vbmake.py"), vbp],
                       capture_output=True, text=True, errors="replace")
    log = r.stdout + r.stderr
    errs = compile_errors(log)
    if not errs:
        print(log.strip().splitlines()[-1] if log.strip() else "(no output)")
        return 0
    for fname, line, msg in errs:
        n, text = source_line(os.path.join(os.path.dirname(vbp), fname), line)
        print("%s:%d: %s\n  | %s" % (fname, n, msg, text.strip()))
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
