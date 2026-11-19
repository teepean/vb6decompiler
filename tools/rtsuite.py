#!/usr/bin/env python3
"""Round-trip every test project in tests/ (source -> EXE -> decompiled
source -> EXE, with VB6 under Wine), with and without the linker map.

usage: rtsuite.py [-j N] [--nocache] [--tests DIR] [WORKDIR] [NAME...]

The round trips run N at a time (default 8), each in WORKDIR/NAME/map or
WORKDIR/NAME/nomap, and are reported in order.  --nocache builds and runs
the originals again instead of reusing earlier results (roundtrip.py).
--tests takes the projects from another directory (tests_wip/coverage).
"""
import concurrent.futures
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TESTS = os.path.join(os.path.dirname(HERE), "tests")


def roundtrip(args):
    r = subprocess.run([sys.executable, os.path.join(HERE, "roundtrip.py")] + args,
                       capture_output=True, text=True)
    return r.returncode == 0, r.stdout.strip().split("\n")


def main(argv):
    global TESTS
    if "--tests" in argv:
        i = argv.index("--tests")
        TESTS = os.path.abspath(argv[i + 1])
        argv = argv[:i] + argv[i + 2:]
    if any(a.startswith("-") and a not in ("-j", "--nocache") for a in argv):
        print(__doc__)              # (anything else would be taken for WORKDIR)
        return 2
    jobs = 8
    if "-j" in argv:
        i = argv.index("-j")
        jobs = int(argv[i + 1])
        argv = argv[:i] + argv[i + 2:]
    extra = ["--nocache"] if "--nocache" in argv else []
    argv = [a for a in argv if a != "--nocache"]
    work = argv[0] if argv else "/tmp/vb6dec-rtsuite"
    names = argv[1:] or sorted(os.listdir(TESTS))
    runs = []
    for n in names:
        d = os.path.join(TESTS, n)
        vbps = [f for f in os.listdir(d) if f.lower().endswith(".vbp")]
        if not vbps:
            continue
        for mode in ("map", "nomap"):
            flags = ["--nomap"] if mode == "nomap" else []
            runs.append((n, mode, flags + extra + [os.path.join(d, vbps[0]),
                                                   os.path.join(work, n, mode)]))
    bad = 0
    with concurrent.futures.ThreadPoolExecutor(jobs) as pool:
        done = [pool.submit(roundtrip, args) for _, _, args in runs]
        for (n, mode, _), f in zip(runs, done):
            ok, lines = f.result()
            bad += not ok
            print("%-8s %-8s %s" % (n, mode, "ok" if ok else "FAILED"), flush=True)
            if not ok:
                for ln in lines[-4:]:
                    print("    " + ln)
    print("rtsuite: %d failure(s)" % bad)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
