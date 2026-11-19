#!/usr/bin/env python3
"""Every instruction of a procedure that names a frame slot, whatever vbdis.py stops at:
the bytes are read straight from the file, up to the size given.

usage: slotuse.py EXE VA SIZE OFF...   OFF in hex, as in v30 (ebp - 0x30)"""
import re
import struct
import sys

import capstone


def rva_map(d):
    pe = struct.unpack_from("<I", d, 0x3c)[0]
    nsec = struct.unpack_from("<H", d, pe + 6)[0]
    opt = struct.unpack_from("<H", d, pe + 20)[0]
    base = struct.unpack_from("<I", d, pe + 52)[0]
    secs = []
    for i in range(nsec):
        s = pe + 24 + opt + i * 40
        vs, va, rs, rp = struct.unpack_from("<IIII", d, s + 8)
        secs.append((base + va, rs, rp))
    return secs


def main(argv):
    exe, va, size = argv[0], int(argv[1], 16), int(argv[2], 16)
    offs = [int(a, 16) for a in argv[3:]]
    d = open(exe, "rb").read()
    fo = next(va - b + p for b, n, p in rva_map(d) if b <= va < b + n)
    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
    pat = re.compile(r"\[ebp - (0x[0-9a-f]+)\]")
    prev = []
    for i in md.disasm(d[fo:fo + size], va):
        m = pat.search(i.op_str)
        if m and int(m.group(1), 16) in offs:
            print("%x  %-5s %s" % (i.address, i.mnemonic, i.op_str))
        prev.append(i)


if __name__ == "__main__":
    main(sys.argv[1:])
