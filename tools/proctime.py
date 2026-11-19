#!/usr/bin/env python3
"""Where the decompiler spends its time: lift and simplify every procedure of
an EXE once, then list the slowest procedures and the simplify passes by
time.

usage: proctime.py EXE [MAP] [--top N] [--slow SECONDS] [--stacks SECONDS] [--proc VA]

A pass is a function of simplify.py whose first parameter is the body; its
"self" time leaves out the passes it calls (inline_all calls inline_temps,
drop_dead_stores and dead_by_liveness).  Without the rest of decompile.py's
passes (no struct hints, plists ...) the procedures differ a little from a
real run, but where the time goes does not.

Procedures slower than --slow (default 2 s) are printed as they finish.
--stacks N prints the Python stack every N seconds (faulthandler): where a
run that never ends is spending its time, without a profiler attaching.
--proc VA does only that procedure, printing each pass simplify.run calls as
it ends: its time and the blocks and statements it leaves (a pass that blows
the body up shows there).
"""
import faulthandler
import inspect
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from vb6dec.model import Model          # noqa: E402
from vb6dec.lift import Lifter          # noqa: E402
from vb6dec import simplify             # noqa: E402


def main(argv):
    opts = {"--top": 15, "--slow": 2.0, "--stacks": 0, "--proc": ""}
    for o in opts:
        if o in argv:
            i = argv.index(o)
            opts[o] = type(opts[o])(argv[i + 1])
            argv = argv[:i] + argv[i + 2:]
    top = opts["--top"]
    if opts["--stacks"]:
        faulthandler.dump_traceback_later(opts["--stacks"], repeat=True)
    if not argv:
        print(__doc__.strip())
        return 2
    exe = argv[0]
    mp = argv[1] if len(argv) > 1 else None
    incl, excl, calls, stack = {}, {}, {}, []

    def wrap(name, f):
        def g(*a, **k):
            outer = not stack
            t0 = time.perf_counter()
            stack.append(0.0)
            r = None
            try:
                r = f(*a, **k)
                return r
            finally:
                dt = time.perf_counter() - t0
                child = stack.pop()
                incl[name] = incl.get(name, 0) + dt
                excl[name] = excl.get(name, 0) + dt - child
                calls[name] = calls.get(name, 0) + 1
                if stack:
                    stack[-1] += dt
                if opts["--proc"] and outer and isinstance(r, list):
                    print("    %-26s %8.2f s  %5d blocks %7d statements" % (
                        name, dt, len(r), sum(len(x[1]) for x in r)), flush=True)
        return g
    for name, f in list(vars(simplify).items()):
        if inspect.isfunction(f) and f.__module__ == simplify.__name__ and name != "run":
            params = list(inspect.signature(f).parameters)
            if params and params[0] == "body":
                setattr(simplify, name, wrap(name, f))
    t0 = time.perf_counter()
    m = Model(exe, mp)
    print("model: %.1f s, %d procedures" % (time.perf_counter() - t0, len(m.procs)), flush=True)
    rows, tl, ts = [], 0.0, 0.0
    for p in m.procs.values():
        if opts["--proc"] and int(opts["--proc"], 16) != p.va:
            continue
        if opts["--stacks"]:
            print("start %s %#x" % (p.name, p.va), flush=True)
        a = time.perf_counter()
        try:
            lf = Lifter(m, p).run()
        except Exception as e:
            print("  lift failed: %s: %s" % (p.name, e))
            continue
        b = time.perf_counter()
        try:
            body = simplify.run(lf)
            n = sum(len(ss) for _, ss, _ in body)
        except Exception as e:
            print("  simplify failed: %s: %s" % (p.name, e))
            n = -1
        c = time.perf_counter()
        tl += b - a
        ts += c - b
        raw = sum(len(lf.out[x][0]) for x in lf.out)
        rows.append((c - a, b - a, c - b, raw, n, p.name))
        if c - a >= opts["--slow"]:
            print("  %-30s %7.2f s (lift %.2f, simplify %.2f)  %d -> %d statements" % (
                p.name[:30], c - a, b - a, c - b, raw, n), flush=True)
    print("lift %.1f s, simplify %.1f s" % (tl, ts))
    print("\nslowest procedures:  total    lift  simplify  statements (lifted -> left)")
    for tot, lt, st, raw, n, name in sorted(rows, reverse=True)[:top]:
        print("  %-30s %7.2f %7.2f %8.2f   %5d -> %d" % (name[:30], tot, lt, st, raw, n))
    print("\npasses by self time:          self    incl   calls")
    for name in sorted(excl, key=excl.get, reverse=True)[:top]:
        print("  %-26s %7.2f %7.2f %7d" % (name, excl[name], incl[name], calls[name]))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
