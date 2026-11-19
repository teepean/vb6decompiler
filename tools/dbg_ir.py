#!/usr/bin/env python3
"""Print the IR of one procedure after lift and after each early simplify
pass, filtered to statements touching given local slots.

usage: dbg_ir.py EXE [MAP] VA [OFFS...] [--full | --after=PASS,...]
(OFFS hex like -0x60; --full runs every simplify pass, --after runs the named ones)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from vb6dec.model import Model
from vb6dec.lift import Lifter
from vb6dec import simplify
from vb6dec.ir import Var


def main(argv):
    exe = argv[0]
    i = 1
    mp = None
    if len(argv) > 1 and argv[1].endswith(".map"):
        mp = argv[1]
        i = 2
    va = int(argv[i], 0)
    offs, passes = [], []
    for x in argv[i + 1:]:
        if x.startswith("--after="):
            passes = x.split("=", 1)[1].split(",")
        elif x.startswith("--full"):
            passes = [""]
        else:
            offs.append(int(x, 0))
    m = Model(exe, mp)
    p = next(p for p in m.procs.values() if p.va == va)
    lf = Lifter(m, p).run()
    body = simplify.flatten(lf)

    def touches(s):
        if not offs:
            return True
        for e in simplify.stmt_exprs(s):
            for x in e.walk():
                if isinstance(x, Var) and x.space == "local" and x.off in offs:
                    return True
        return False

    def show(tag, body):
        print("==== %s" % tag)
        for a, ss, br in body:
            print("-- block %s" % a)
            for s in ss:
                if touches(s):
                    print("  %08x  %s" % (getattr(s, "va", 0) or 0, s))

    show("lift", body)
    if passes == [""]:                    # the whole pipeline, on a fresh lift
        body = simplify.run(Lifter(m, p).run())
        show("run", body)
        return 0
    for name in passes or ("fold_variant_temps", "fold_variant_args"):
        fn = getattr(simplify, name)
        try:
            try:
                body = fn(body, lf)
            except TypeError:
                body = fn(body)             # drop_frees, merge_hi_stores, ...
        except Exception as e:
            print("!! %s: %s" % (name, e))
            return 1
        show(name, body)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
