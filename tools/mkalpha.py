#!/usr/bin/env python3
"""Make the Windows package, from a list of what goes in, and nothing else: no docs, tests, or other tools.  It
stops if tools/privscan.py finds a private name in what would ship.

usage: mkalpha.py OUT --embed PYTHON_EMBED.zip --wheel WHEEL ... [--private DIR ...] [--public DIR ...] [--word WORD ...]

OUT must be a new folder.  Writes OUT/vb6dec-<version> (the folder, to try) and OUT/vb6dec-<version>.zip, the version
taken from run_vb6dec.py (VERSION "alpha 2" makes vb6dec-alpha2).  The package holds alpha/README.txt and
alpha/vb6dec.bat, LICENSE as LICENSE.txt, the embedded Python in python/, and the decompiler with the wheels' packages
in app/.

--embed  the Windows embeddable package of Python, 64-bit (python-3.x.y-embed-amd64.zip from python.org).
--wheel  each package the decompiler needs, as a win_amd64 or pure-Python wheel from PyPI: capstone and pefile
         (`pip download capstone pefile --only-binary=:all: --platform win_amd64 --python-version 3.x -d DIR`).
--private, --public, --word  what privscan.py takes, to check that nothing from programs whose output must stay
         private ships (a word anywhere in the shipped text or in ocxbag.exe stops it).  All optional; privscan also
         reads tools/privallow.txt, a local list of words checked and found harmless, if there is one.
"""
import argparse
import glob
import os
import shutil
import subprocess
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def package_name():
    """vb6dec-alpha2 from run_vb6dec.py's VERSION = "alpha 2"."""
    import re
    text = open(os.path.join(ROOT, "run_vb6dec.py"), encoding="utf-8").read()
    return "vb6dec-" + re.search(r'^VERSION = "([^"]+)"', text, re.M).group(1).replace(" ", "")


NAME = package_name()
# what goes in app/: the decompiler, and of tools only what it runs
SHIP = ["run_vb6dec.py", "decompile.py", "vb6dec/*.py", "vb6dec/*.json", "data/runtime_auto.json", "tools/vbcheck.py",
        "tools/ocxbag/ocxbag.exe"]


def copy_app(app):
    for pattern in SHIP:
        files = glob.glob(os.path.join(ROOT, pattern))
        if not files:
            sys.exit("mkalpha: nothing matches %s" % pattern)
        for f in files:
            rel = os.path.relpath(f, ROOT)
            os.makedirs(os.path.dirname(os.path.join(app, rel)) or app, exist_ok=True)
            shutil.copy2(f, os.path.join(app, rel))


def main(argv):
    ap = argparse.ArgumentParser(usage=__doc__)
    ap.add_argument("out")
    ap.add_argument("--embed", required=True)
    ap.add_argument("--wheel", action="append", default=[])
    ap.add_argument("--private", action="append", default=[])
    ap.add_argument("--public", action="append", default=[])
    ap.add_argument("--word", action="append", default=[])
    a = ap.parse_args(argv)
    os.makedirs(a.out)
    top = os.path.join(a.out, NAME)
    app = os.path.join(top, "app")
    os.makedirs(app)
    copy_app(app)
    with zipfile.ZipFile(a.embed) as z:
        z.extractall(os.path.join(top, "python"))
    for w in a.wheel:
        with zipfile.ZipFile(w) as z:
            z.extractall(os.path.join(app, "lib"))
    for f in ("README.txt", "vb6dec.bat"):
        shutil.copy2(os.path.join(ROOT, "alpha", f), top)
    with open(os.path.join(ROOT, "LICENSE"), encoding="utf-8") as f, \
            open(os.path.join(top, "LICENSE.txt"), "w", encoding="utf-8", newline="\r\n") as g:
        g.write(f.read())           # (CRLF, like README.txt, for any Notepad)
    # what is ours, not what comes with Python and the libraries
    cmd = [sys.executable, os.path.join(HERE, "privscan.py"), "--allow", os.path.join(HERE, "privallow.txt"), "--lines"]
    for k in ("private", "public", "word"):
        for v in getattr(a, k):
            cmd += ["--" + k, v]
    theirs = os.path.join(app, "lib")
    ours = [os.path.join(top, "README.txt"), os.path.join(top, "vb6dec.bat")] + \
        [os.path.join(app, x) for x in os.listdir(app) if os.path.join(app, x) != theirs]
    r = subprocess.run(cmd + ours, capture_output=True, text=True, errors="replace")
    print(r.stdout.strip().split("\n")[-1])
    if r.returncode:
        print(r.stdout[-3000:])
        print("mkalpha: private names in what would ship; no zip made")
        return 1
    with open(os.path.join(ROOT, "tools", "ocxbag", "ocxbag.exe"), "rb") as f:
        exe = f.read().lower()
    for w in a.word:
        if w.lower().encode() in exe:
            print("mkalpha: %r in ocxbag.exe; no zip made" % w)
            return 1
    z = shutil.make_archive(top, "zip", a.out, NAME)
    print("%s, %.1f MB" % (z, os.path.getsize(z) / 1048576.0))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
