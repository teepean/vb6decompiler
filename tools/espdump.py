#!/usr/bin/env python3
"""The stack height the lift believes in, block by block: for a procedure with '<argN> in its output
(the height went off somewhere and arguments are read from the wrong place).

usage: espdump.py EXE MAP VA [--all] [--block VA]

Prints every block whose height on entry or exit is above the frame (esp > base_esp) and, with --all,
every block.  The height at a block's exit is what the next block starts from.  --block prints that
block's calls: the height above the frame before and after each, to find the one that pops too much."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import decompile                    # noqa: E402
from vb6dec.lift import Lifter      # noqa: E402


def main(argv):
    exe, mp, va = argv[0], argv[1], int(argv[2], 16)
    every = "--all" in argv
    m, out = decompile.first_pass_snapshot(exe, mp)
    orig = Lifter.exec_node
    seen = []
    blk = int(argv[argv.index("--block") + 1], 16) if "--block" in argv else None
    if blk is not None:
        ocall = Lifter.do_call
        inside = []

        def dc(self, st, ins, stmts):
            e0 = st.esp
            r = ocall(self, st, ins, stmts)
            if inside:
                print("%x %-40s %d -> %d" % (ins.address, ins.op_str[:40], e0 - self.base_esp,
                                             st.esp - self.base_esp))
            return r
        Lifter.do_call = dc

    def wrap(self, node, st):
        e0 = st.esp
        if blk is not None and node.start == blk:
            inside.append(1)
        r = orig(self, node, st)
        if blk is not None and node.start == blk:
            inside.clear()
        seen.append((node.start if hasattr(node, "start") else getattr(node, "va", None), e0, r[2].esp,
                     self.base_esp))
        return r
    Lifter.exec_node = wrap
    Lifter(m, m.procs[va]).run()
    bad = 0
    for a, e0, e1, base in seen:
        if every or (base is not None and (e0 > base or e1 > base)):
            print("%s entry %d exit %d base %s" % (hex(a) if a else a, e0, e1, base))
            bad += 1
    print("%d of %d blocks" % (bad, len(seen)))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
