#!/usr/bin/env python3
"""The control flow nodes of a procedure, between two addresses, after the first pass
(what decompile.py's second lift sees).

usage: cfgdump.py EXE VA [FROM TO]   (hex)"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import decompile
from vb6dec import cfg

exe, va = sys.argv[1], int(sys.argv[2], 16)
lo = int(sys.argv[3], 16) if len(sys.argv) > 3 else 0
hi = int(sys.argv[4], 16) if len(sys.argv) > 4 else 1 << 62
m = decompile.first_pass_snapshot(exe, None)[0]
nodes = cfg.build(m, m.procs[va])
for a in sorted(nodes, key=lambda x: x):
    if lo <= a <= hi:
        n = nodes[a]
        print("%x %-5s -> %s  [%s]" % (a, n.kind, " ".join("%x" % s for s in n.succs),
                                       " / ".join(i.mnemonic for i in n.insns[-4:])))
