#!/usr/bin/env python3
"""Statistical profile of a Python program: where its time goes, without
cProfile's cost per call (which inflates small functions and generators
several times over: ir.Expr.walk looked like 18% of a VBDoom decompile and
making it faster changed nothing).

usage: pysample.py [--every MS] [--top N] [--callers F1,F2...] [--for S] -- SCRIPT [ARGS...]

SCRIPT runs in this process.  Every MS milliseconds of CPU time (default 2)
a SIGPROF handler records the Python stack; the functions with the most
samples are printed, inclusive (anywhere on the stack) and self (the
innermost frame), as a share of all samples.  A long call into C (capstone,
re) is seen once when it returns, so it counts at most one sample per call.
--callers also lists who called the functions whose names contain one of
the given strings.  --for S stops the program after S seconds (wall time)
and prints what was sampled so far: where a run that never ends spins.
(py-spy cannot attach here: ptrace is restricted.  perf with -X perf does
not unwind through this Python's eval loop.)
"""
import collections
import os
import runpy
import signal
import sys


def name(code):
    return "%s:%d %s" % (os.path.relpath(code.co_filename), code.co_firstlineno, code.co_qualname)


def main(argv):
    every, top, callers, limit = 2.0, 40, None, None
    while argv and argv[0] != "--":
        if argv[0] == "--every":
            every, argv = float(argv[1]), argv[2:]
        elif argv[0] == "--for":
            limit, argv = int(argv[1]), argv[2:]
        elif argv[0] == "--top":
            top, argv = int(argv[1]), argv[2:]
        elif argv[0] == "--callers":
            callers, argv = argv[1].split(","), argv[2:]
        else:
            break
    if argv[:1] == ["--"]:
        argv = argv[1:]
    if not argv:
        print(__doc__.strip())
        return 2
    incl, own, by_caller = collections.Counter(), collections.Counter(), collections.Counter()
    total = [0]

    def sample(sig, frame):
        total[0] += 1
        if frame is None:
            return
        own[frame.f_code] += 1
        seen = set()
        f = frame
        while f is not None:
            c = f.f_code
            if c not in seen:
                seen.add(c)
                incl[c] += 1
                if callers and f.f_back is not None and any(x in c.co_qualname for x in callers):
                    by_caller[(c, f.f_back.f_code, f.f_back.f_lineno)] += 1
            f = f.f_back

    sys.argv = argv
    os.environ["VB6DEC_NOFASTEXIT"] = "1"   # (decompile.fast_exit: we report after it ends)
    sys.path.insert(0, os.path.dirname(os.path.abspath(argv[0])))
    signal.signal(signal.SIGPROF, sample)
    signal.setitimer(signal.ITIMER_PROF, every / 1000, every / 1000)
    if limit:
        def stop(sig, frame):
            raise KeyboardInterrupt
        signal.signal(signal.SIGALRM, stop)
        signal.alarm(limit)
    try:
        runpy.run_path(argv[0], run_name="__main__")
    except SystemExit:
        pass
    except KeyboardInterrupt:
        print("\n(stopped after %s s)" % limit, file=sys.stderr)
    finally:
        signal.setitimer(signal.ITIMER_PROF, 0, 0)
    n = max(total[0], 1)
    out = sys.stderr
    print("\n%d samples, every %g ms of CPU (about %.1f s)" % (total[0], every,
                                                          total[0] * every / 1000), file=out)
    print("\n  incl%   self%  function", file=out)
    for c, k in incl.most_common(top):
        print("%6.1f  %6.1f  %s" % (100.0 * k / n, 100.0 * own[c] / n, name(c)), file=out)
    print("\n  self%  function", file=out)
    for c, k in own.most_common(top):
        print("%6.1f  %s" % (100.0 * k / n, name(c)), file=out)
    if callers:
        print("\n  incl%  function <- caller:line", file=out)
        for (c, cc, ln), k in by_caller.most_common(top):
            print("%6.1f  %s <- %s:%d" % (100.0 * k / n, c.co_qualname, name(cc), ln), file=out)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
