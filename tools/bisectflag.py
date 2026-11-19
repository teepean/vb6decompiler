#!/usr/bin/env python3
"""Which change made a difference?  Decompile a program with one piece of the decompiler turned off
at a time, each in its own copy of the sources, and report whether a text comes back in the output.

usage: bisectflag.py EXE MAP WORK FILE TEXT TOGGLES.py
TOGGLES.py defines TOGGLES = {name: (source file, old text, new text), ...}.  MAP "" for none.  WORK
must be a new folder.  For regressions with no version control to bisect: name the suspects, and let
this say which of them it was.
"""
import os
import runpy
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main(argv):
    if len(argv) != 6:
        print(__doc__.strip())
        return 2
    exe, map_, work, fname, text, toggles = argv
    tog = runpy.run_path(toggles)["TOGGLES"]
    os.makedirs(work)
    for name, (path, old, new) in tog.items():
        d = os.path.join(work, name)
        os.makedirs(d)
        shutil.copy(os.path.join(ROOT, "decompile.py"), d)
        shutil.copytree(os.path.join(ROOT, "vb6dec"), os.path.join(d, "vb6dec"),
                        ignore=shutil.ignore_patterns("__pycache__"))
        os.symlink(os.path.join(ROOT, "tools"), os.path.join(d, "tools"))
        p = os.path.join(d, path)
        s = open(p, encoding="latin-1", newline="").read()
        if old not in s:
            print("%-22s old text not found" % name)
            continue
        open(p, "w", encoding="latin-1", newline="").write(s.replace(old, new))
        out = os.path.join(d, "out")
        cmd = [sys.executable, "decompile.py", exe] + ([map_] if map_ else []) + ["-o", out]
        r = subprocess.run(cmd, cwd=d, capture_output=True, text=True, errors="replace", timeout=1200,
                           env=dict(os.environ, VB6DEC_JOBS="4", VB6DEC_NOSNAP="1"))
        try:
            got = text.encode("latin-1") in open(os.path.join(out, fname), "rb").read()
        except OSError:
            print("%-22s no output: %s" % (name, r.stderr.strip()[-120:]))
            continue
        print("%-22s %s" % (name, "comes back" if got else "still gone"), flush=True)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
