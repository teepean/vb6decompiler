#!/usr/bin/env python3
"""Disassemble one procedure of a VB6 EXE, naming imported runtime calls.

usage: vbdis.py EXE (VA | NAME-from-.map) [MAXBYTES]
"""
import os
import re
import sys

import capstone
import pefile


def main(argv):
    exe, what = argv[0], argv[1]
    size = int(argv[2], 0) if len(argv) > 2 else 0x400
    pe = pefile.PE(exe)
    base = pe.OPTIONAL_HEADER.ImageBase
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from vb6dec import msfiles
    rt = pefile.PE(msfiles.find("MSVBVM60.DLL"))      # (MSVBVM60 or VB6DIR say where, see msfiles)
    ords = {e.ordinal: e.name.decode() for e in rt.DIRECTORY_ENTRY_EXPORT.symbols if e.name}
    imp = {i.address: i.name.decode() if i.name else ords.get(i.ordinal, "#%d" % i.ordinal)
           for e in pe.DIRECTORY_ENTRY_IMPORT for i in e.imports}
    try:
        va = int(what, 16)
    except ValueError:
        mp = os.path.splitext(exe)[0] + ".map"
        va = None
        for ln in open(mp, errors="replace"):
            f = ln.split()
            if len(f) >= 3 and what in f[1]:
                va = int(f[2], 16)
                print("; %s" % f[1])
                break
        if va is None:
            sys.exit("not in the map: " + what)
    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
    for i in md.disasm(pe.get_data(va - base, size), va):
        s = "%x  %s %s" % (i.address, i.mnemonic, i.op_str)
        m = re.search(r"0x([0-9a-f]+)\]", i.op_str)
        if m and int(m.group(1), 16) in imp:
            s += "    ; " + imp[int(m.group(1), 16)]
        elif i.mnemonic == "call" and i.op_str.startswith("0x"):
            # unoptimised code calls the runtime through thunks: jmp [iat]
            t = int(i.op_str, 16)
            try:
                tb = pe.get_data(t - base, 6)
                if tb[:2] == b"\xff\x25":
                    slot = int.from_bytes(tb[2:6], "little")
                    if slot in imp:
                        s += "    ; " + imp[slot]
            except Exception:
                pass
        print(s)
        if i.mnemonic == "ret":
            break


if __name__ == "__main__":
    main(sys.argv[1:])
