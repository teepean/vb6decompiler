#!/usr/bin/env python3
"""Why is a frame slot a variable in a procedure that may resume (simplify.pin_statement_variables)?

usage: pinwhy.py EXE [MAP] VA [OFF...]      (hex; OFF like -0x150 to show only those)

Lifts the procedure with no first pass before it, and prints each pinned slot with the first reason and the
statement's disassembly around the address."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from vb6dec.model import Model      # noqa: E402
from vb6dec.lift import Lifter      # noqa: E402
from vb6dec import simplify         # noqa: E402


def main(argv):
    exe = argv[0]
    i = 1
    mp = None
    if len(argv) > 1 and argv[1].endswith(".map"):
        mp = argv[1]
        i = 2
    va = int(argv[i], 16)
    only = {int(x, 0) for x in argv[i + 1:]}
    m = Model(exe, mp)
    lf = Lifter(m, m.procs[va]).run()
    body = simplify.drop_frees(simplify.flatten(lf))
    simplify.pin_statement_variables(body, lf)
    why = getattr(lf, "pin_why", {})
    temps = getattr(lf, "statement_temps", (None, set()))[1]
    print("%d pinned, %d temporaries" % (len(why), len(temps)))
    for k, (reason, at, s) in sorted(why.items(), key=lambda t: t[1][1]):
        if only and k[1] not in only:
            continue
        print("%-8s %08X  %s%s" % (hex(k[1]), at, reason, "  " + str(s)[:100] if s is not None else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
