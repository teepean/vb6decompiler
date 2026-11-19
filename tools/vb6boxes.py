#!/usr/bin/env python3
"""Screenshots of the windows a stuck VB6 (or a program under Wine) shows on the desktop.

usage: vb6boxes.py OUTDIR [--exe VB6.EXE]
A /make that never ends has a dialog up: the run-time error of a UserControl's code that VB6 runs
loading a form, a compile error box.  Prints each visible window's title and saves it as a PNG.
Nothing is clicked.  Needs xdotool and ImageMagick's import.
"""
import argparse
import os
import subprocess


def out(*cmd, env=None):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=20, env=env).stdout
    except (OSError, subprocess.TimeoutExpired):
        return ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("outdir")
    ap.add_argument("--exe", default="VB6.EXE")
    a = ap.parse_args()
    os.makedirs(a.outdir, exist_ok=True)
    pids = out("pgrep", "-x", a.exe).split()
    for pid in pids:
        env = dict(os.environ)
        try:
            for item in open("/proc/%s/environ" % pid, "rb").read().split(b"\0"):
                if item.startswith(b"DISPLAY="):
                    env["DISPLAY"] = item[8:].decode()
        except OSError:
            continue
        for w in out("xdotool", "search", "--onlyvisible", "--pid", pid, env=env).split():
            title = out("xdotool", "getwindowname", w, env=env).strip()
            png = os.path.join(a.outdir, "%s_%s.png" % (pid, w))
            out("import", "-window", w, png, env=env)
            print(pid, w, repr(title), png)


if __name__ == "__main__":
    main()
