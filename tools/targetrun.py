#!/usr/bin/env python3
"""Decompile and build a list of programs one by one: what fails, and how much of it (for programs
with no source, whose output stays private: the results only improve the decompiler).

usage: targetrun.py WORK EXE...
Each goes to a new WORK/NN_name.  Prints a line per program: procedures, vbcheck's count, the build's
outcome and its first error."""
import glob
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def main(argv):
    if len(argv) < 2:
        print(__doc__.strip())
        return 2
    work = argv[0]
    os.makedirs(work, exist_ok=True)
    for n, exe in enumerate(argv[1:]):
        name = re.sub(r"\W", "_", os.path.basename(exe))
        out = os.path.join(work, "%02d_%s" % (n, name))
        t0 = time.time()
        r = subprocess.run([sys.executable, os.path.join(ROOT, "decompile.py"), exe, "-o", out],
                           capture_output=True, text=True, errors="replace", timeout=1500,
                           env=dict(os.environ, VB6DEC_JOBS="4"))
        t1 = time.time()
        vbp = glob.glob(os.path.join(out, "*.vbp"))
        if r.returncode or not vbp:
            print("%-28s decompile FAILED: %s" % (name[:28], (r.stderr or r.stdout).strip()[-160:]))
            continue
        files = glob.glob(os.path.join(out, "*.bas")) + glob.glob(os.path.join(out, "*.frm")) + \
            glob.glob(os.path.join(out, "*.cls")) + glob.glob(os.path.join(out, "*.ctl"))
        procs = lines = 0
        for f in files:
            with open(f, encoding="cp1252", errors="replace") as h:
                for ln in h:
                    lines += 1
                    if re.match(r"(Public |Private |Friend )?(Sub|Function|Property) ", ln):
                        procs += 1
        c = subprocess.run([sys.executable, os.path.join(HERE, "vbcheck.py"), out], capture_output=True,
                           text=True, errors="replace", timeout=300)
        m = re.search(r"vbcheck: (\d+)", c.stdout)
        b = subprocess.run([sys.executable, os.path.join(HERE, "builderr.py"), vbp[0]], capture_output=True,
                           text=True, errors="replace", timeout=600)
        tail = [x for x in b.stdout.strip().split("\n") if x.strip()][-2:]
        ok = any("succeeded" in x for x in tail)
        print("%-28s %5d procs %6d lines  decompile %3d s  vbcheck %s  %s" % (
            name[:28], procs, lines, t1 - t0, m.group(1) if m else "?",
            "BUILDS" if ok else "build: " + " | ".join(x.strip()[:100] for x in tail)))
        sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
