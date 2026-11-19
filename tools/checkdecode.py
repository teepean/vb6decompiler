#!/usr/bin/env python3
"""disasm.decode (plain Insn objects, encodings in a trie) against capstone,
for every instruction Model(EXE) decodes: all that callers can read of it.

usage: checkdecode.py EXE
"""
import gc
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from vb6dec import disasm               # noqa: E402
from vb6dec.model import Model          # noqa: E402


def main(argv):
    gc.disable()
    m = Model(argv[0])
    im = m.im
    bad = n = 0
    enc = {}
    for va, ins in im._decoded.items():
        o = va - im.base
        c = next(disasm._md.disasm(bytes(im.mem[o:o + 16]), va), None)
        n += 1
        if (c is None) != (ins is None):
            print("%X: capstone %r, decode %r" % (va, c, ins))
            bad += 1
            continue
        if c is None:
            continue
        a = (c.address, c.size, c.mnemonic, c.op_str, c.id, list(c.prefix),
             [(x.type, x.size, x.access, x.reg, x.imm, x.mem.segment, x.mem.base, x.mem.index,
               x.mem.scale, x.mem.disp) for x in c.operands])
        b = (ins.address, ins.size, ins.mnemonic, ins.op_str, ins.id, list(ins.prefix),
             [(x.type, x.size, x.access, x.reg, x.imm, x.mem.segment, x.mem.base, x.mem.index,
               x.mem.scale, x.mem.disp) for x in ins.operands])
        if a != b:
            print("%X:\n  capstone %r\n  decode   %r" % (va, a, b))
            bad += 1
        if tuple(c.regs_access()[0]) != tuple(ins.regs_access()[0]) or \
                tuple(c.regs_access()[1]) != tuple(ins.regs_access()[1]):
            print("%X regs_access differ" % va)
            bad += 1
        del c
    print("%d instructions, %d differ" % (n, bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
