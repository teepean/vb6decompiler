#!/usr/bin/env python3
"""Follow statements through simplify.run, with the whole first pass done
first (proc_returns, struct hints, parameter lists: what dbg_ir.py lacks).

usage: passtrace.py EXE MAP VA TEXT [--changes]
Prints every statement (and branch condition, "if ...") whose text contains
TEXT after the lift and after each simplify pass; with --changes only when
the matching text changed.  TEXT may list alternatives: "1065|1066".
--flags adds the statement flags passes read ([whole_variant] ...).
--raw traces the lift with no first pass before it, what the first pass decides from.
--va LO:HI (hex) follows the statements of those instructions and the branches of the blocks they
are in, whatever their text (give TEXT as "").
The first pass comes from a snapshot when one is valid (vb6dec/snapshot.py: the
first run makes it); --fresh runs it again.  VB6DEC_SNAPSHOT_ANY=1 keeps the old
snapshot while you edit the pass you are tracing.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from vb6dec.model import Model
from vb6dec.lift import Lifter
from vb6dec import simplify
import decompile


def main(argv):
    if len(argv) < 4:
        print(__doc__.strip())
        return 2
    exe, mp, va, texts = argv[0], argv[1], int(argv[2], 0), argv[3].split("|")
    only_changes = "--changes" in argv
    span = None
    if "--va" in argv:
        lo, hi = argv[argv.index("--va") + 1].split(":")
        span = int(lo, 16), int(hi, 16)
    flags = "--flags" in argv

    def show(s):
        t = str(s)
        if flags:
            t += "".join(" [%s=%s]" % (f, getattr(s, f)) for f in ("whole_variant", "fixstr", "kind", "record", "fp_store")
                          if getattr(s, f, None) not in (None, False, "let"))
        return t
    import gc
    gc.disable()                # (decompile.py does too: 5 min -> under 3)
    if "--raw" in argv:
        m = Model(exe, mp)              # (as the first pass itself sees the procedure)
    elif "--fresh" in argv:
        m = Model(exe, mp)
        decompile.first_pass(m)
    else:
        m = decompile.first_pass_snapshot(exe, mp)[0]
    lf = Lifter(m, m.procs[va]).run()
    last = [None]

    def report(tag, body):
        if span:
            hits = []
            for a, ss, br in body:
                inside = [s for s in ss if span[0] <= (getattr(s, "va", None) or 0) <= span[1]]
                hits += [show(s) for s in inside]
                if br is not None and (inside or span[0] <= a <= span[1]):
                    hits.append("if " + str(br.cond))
        else:
            hits = [show(s) for _, ss, _ in body for s in ss if any(t in str(s) for t in texts)] + \
                ["if " + str(br.cond) for _, _, br in body
                 if br is not None and any(t in str(br.cond) for t in texts)]
        if only_changes and hits == last[0]:
            return
        last[0] = hits
        print("==== %s" % tag)
        for h in hits:
            print("   ", h)

    report("lift", simplify.flatten(lf))
    # wrap the module-level passes simplify.run calls by name
    for name in dir(simplify):
        f = getattr(simplify, name)
        if not callable(f) or name in ("run", "flatten", "substitute_expr", "stmt_exprs") or \
                getattr(f, "__module__", None) != simplify.__name__ or isinstance(f, type):
            continue

        def wrap(f=f, name=name):
            def g(body, *a, **k):
                r = f(body, *a, **k)
                if isinstance(r, list) and r and isinstance(r[0], tuple) and len(r[0]) == 3:
                    report(name, r)
                return r
            return g
        setattr(simplify, name, wrap())
    simplify.run(lf)
    return 0


if __name__ == "__main__":
    code = main(sys.argv[1:])
    import decompile
    sys.exit(decompile.fast_exit(code))
