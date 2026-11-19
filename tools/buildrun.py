#!/usr/bin/env python3
"""Build a VB6 project folder in place under Wine and run the program: for
diagnostic copies edited by hand (an extra Print # in both the original and
the decompiled source, to find where two runs diverge).

usage: buildrun.py WORKDIR [--timeout S] [-- ARGS...]

The folder's .vbp is built (vbmake.make), then its .exe is run in the
folder with ARGS.  Prints the build log on failure, the message box the run
stopped at, or whether it ended.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import vbmake           # noqa: E402


def main(argv):
    timeout, args = 120, []
    if "--" in argv:
        i = argv.index("--")
        argv, args = argv[:i], argv[i + 1:]
    if "--timeout" in argv:
        i = argv.index("--timeout")
        timeout = int(argv[i + 1])
        argv = argv[:i] + argv[i + 2:]
    if len(argv) != 1:
        print(__doc__.strip())
        return 2
    work = argv[0]
    vbp = next((f for f in os.listdir(work) if f.lower().endswith(".vbp")), None)
    if vbp is None:
        print("== no .vbp in " + work)
        return 2
    for f in os.listdir(work):
        if f.lower().endswith(".exe"):
            os.remove(os.path.join(work, f))
    ok, log = vbmake.make(os.path.join(work, vbp))
    if not ok:
        print("== the build failed:\n" + log.strip())
        return 1
    exe = next(f for f in os.listdir(work) if f.lower().endswith(".exe"))
    done = vbmake.run(os.path.join(work, exe), timeout=timeout, args=args)
    if vbmake.LAST_BOX:
        print("== message box: " + vbmake.LAST_BOX[0])
    elif not done:
        print("== did not end in %d s" % timeout)
    else:
        print("== ended")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
