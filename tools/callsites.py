#!/usr/bin/env python3
"""Print the instructions leading up to every direct call of TARGET inside a procedure.

usage: callsites.py EXE VA SIZE TARGET [--before N] [--grep TEXT]   (hex numbers)
--grep keeps only the sites whose lead-up contains TEXT, e.g. "ebp - 0x88"."""
import struct
import sys

import capstone

a = sys.argv[1:]
before, want = 14, None
if "--before" in a:
    i = a.index("--before")
    before = int(a[i + 1])
    del a[i:i + 2]
if "--grep" in a:
    i = a.index("--grep")
    want = a[i + 1]
    del a[i:i + 2]
exe, va, size, target = a[0], int(a[1], 16), int(a[2], 16), "0x%x" % int(a[3], 16)
d = open(exe, "rb").read()
pe = struct.unpack_from("<I", d, 0x3c)[0]
base = struct.unpack_from("<I", d, pe + 0x34)[0]
n = struct.unpack_from("<H", d, pe + 6)[0]
so = pe + 0x18 + struct.unpack_from("<H", d, pe + 0x14)[0]
secs = [struct.unpack_from("<IIII", d, so + 40 * i + 8) for i in range(n)]


def offset(v):
    r = v - base
    for _, ra, rs, rp in secs:
        if ra <= r < ra + rs:
            return r - ra + rp
    raise SystemExit("%x is outside the sections" % v)


ins = list(capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32).disasm(
    d[offset(va):offset(va) + size], va))
for i, x in enumerate(ins):
    if x.mnemonic == "call" and x.op_str == target:
        lead = ins[max(0, i - before):i + 1]
        if want is None or any(want in y.op_str for y in lead):
            for y in lead:
                print("%x  %s %s" % (y.address, y.mnemonic, y.op_str))
            print()
