#!/usr/bin/env python3
"""Linear disassembly of an address range of a PE, read straight from the file.

usage: dislin.py EXE STARTHEX ENDHEX      (run with .venv/bin/python -I; it wants pefile and capstone)

tools/vbdis.py stops at a procedure's first ret; this one goes on, which is what fast code needs: its
failed checks and the rest of a procedure often come after a ret."""
import capstone
import pefile
import sys


def main(argv):
    if len(argv) != 3:
        print(__doc__.strip())
        return 2
    pe = pefile.PE(argv[0], fast_load=True)
    base = pe.OPTIONAL_HEADER.ImageBase
    a, b = int(argv[1], 16), int(argv[2], 16)
    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
    for i in md.disasm(pe.get_data(a - base, b - a), a):
        if i.mnemonic not in ("int3", "nop"):
            print("%x  %s %s" % (i.address, i.mnemonic, i.op_str.replace("dword ptr ", "")))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
