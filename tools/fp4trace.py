#!/usr/bin/env python3
"""Which simplify pass loses the "read or written as a Single" mark of a field (udt.fp_fields).

usage: fp4trace.py EXE MAP VA
Prints, after the lift and each simplify pass that changes the number, how many Deref / Index
expressions of the procedure carry fp4.  A drop that isn't a dead store dropped is a pass building
a new Deref without keeping the mark, and the field then comes out As Long."""
import gc
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from vb6dec.lift import Lifter
from vb6dec import simplify
import decompile


def marked(body):
    out = []
    for _, ss, br in body:
        for s in ss:
            for e in simplify.stmt_exprs(s):
                for x in e.walk():
                    if getattr(x, "fp4", False):
                        out.append(str(x))
    return out


def main(argv):
    if len(argv) < 3:
        print(__doc__.strip())
        return 2
    gc.disable()
    m = decompile.first_pass_snapshot(argv[0], argv[1])[0]
    lf = Lifter(m, m.procs[int(argv[2], 0)]).run()
    last = marked(simplify.flatten(lf))
    print("lift", len(last))
    for name in dir(simplify):
        f = getattr(simplify, name)
        if not callable(f) or name in ("run", "flatten", "substitute_expr", "stmt_exprs") or \
                getattr(f, "__module__", None) != simplify.__name__ or isinstance(f, type):
            continue

        def wrap(f=f, name=name):
            def g(body, *a, **k):
                nonlocal last
                r = f(body, *a, **k)
                if isinstance(r, list) and r and isinstance(r[0], tuple) and len(r[0]) == 3:
                    now = marked(r)
                    if len(now) != len(last):
                        print("%-28s %d -> %d" % (name, len(last), len(now)))
                        for t in sorted(set(last) - set(now))[:6]:
                            print("    -", t[:130])
                    last = now
                return r
            return g
        setattr(simplify, name, wrap())
    simplify.run(lf)
    return 0


if __name__ == "__main__":
    code = main(sys.argv[1:])
    sys.exit(decompile.fast_exit(code))
