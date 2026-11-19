#!/usr/bin/env python3
"""dbgregion.py EXE VA [FROM_VA]: the blocks after simplify.run, with their successors.

Answers "is the code gone before structuring or in it": for each block from
FROM_VA on, the statements, the branch and the node kind.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from vb6dec.lift import Lifter
from vb6dec import simplify
import decompile


def main(argv):
    exe, va = argv[0], int(argv[1], 0)
    start = int(argv[2], 0) if len(argv) > 2 else 0
    import gc
    gc.disable()
    m = decompile.first_pass_snapshot(exe, "")[0]
    lf = Lifter(m, m.procs[va]).run()
    print("handlers", {k: hex(v) for k, v in getattr(m.procs[va], "handlers", {}).items()})
    body = simplify.run(lf)
    for a, ss, br in sorted(body, key=lambda t: t[0]):
        if a >= start:
            n = lf.nodes.get(a)
            print("%x %s succs=%s preds=%s" % (a, n.kind if n else "?", [hex(x) for x in n.succs] if n else [],
                                               [hex(x) for x in n.preds] if n else []))
            for s in ss:
                print("    ", s)
            if br is not None:
                print("     br", br.cond)


if __name__ == "__main__":
    main(sys.argv[1:])
