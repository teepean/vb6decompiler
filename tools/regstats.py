#!/usr/bin/env python3
"""How a VB6 EXE's code uses esi, edi and ebx, and which runtime functions it calls, to tell its optimisation.

usage: regstats.py EXE...

Goes through the code from each procedure's prologue (push ebp / mov ebp, esp) to its ret.  Prints the instructions
seen, how many use esi, edi or ebx other than to save and restore them, and the runtime calls by name with --calls.
"""
import os
import struct
import sys

import capstone
import pefile

REGS = {"esi", "edi", "ebx"}


def stats(path):
    pe = pefile.PE(path)
    base = pe.OPTIONAL_HEADER.ImageBase
    sec = [s for s in pe.sections if s.Name.startswith(b".text")][0]
    text = sec.get_data()[:sec.Misc_VirtualSize]
    tva = base + sec.VirtualAddress
    slots = {}
    for d in pe.DIRECTORY_ENTRY_IMPORT:
        for i in d.imports:
            slots[i.address] = i.name.decode() if i.name else "#%d" % i.ordinal
    thunks = {}
    for k in range(len(text) - 6):
        if text[k] == 0xff and text[k + 1] == 0x25:
            s = struct.unpack_from("<I", text, k + 2)[0]
            if s in slots:
                thunks[tva + k] = slots[s]
    calls = {}
    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
    total = used = procs = 0
    k = text.find(b"\x55\x8b\xec")
    while k >= 0:
        procs += 1
        n = 0
        for i in md.disasm(text[k:k + 200000], tva + k):
            n += 1
            total += 1
            m = i.mnemonic
            s = i.op_str
            if m not in ("push", "pop") and any(r in s for r in REGS):
                used += 1
            if m == "call":
                if s.startswith("dword ptr [0x"):
                    a = int(s[len("dword ptr ["):-1], 16)
                    if a in slots:
                        calls[slots[a]] = calls.get(slots[a], 0) + 1
                elif s.startswith("0x"):
                    a = int(s, 16)
                    if a in thunks:
                        calls[thunks[a]] = calls.get(thunks[a], 0) + 1
            if m in ("ret", "retn") and n > 3:
                break
        k = text.find(b"\x55\x8b\xec", k + 3)
    return {"procs": procs, "total": total, "used": used, "calls": calls}


def main(argv):
    want_calls = "--calls" in argv
    for path in [a for a in argv if a != "--calls"]:
        r = stats(path)
        print("%-34s procs %5d  instructions %8d  use esi/edi/ebx %8d (%5.2f%%)" % (
            os.path.basename(os.path.dirname(path))[:14] + "/" + os.path.basename(path)[:18], r["procs"], r["total"], r["used"],
            100.0 * r["used"] / max(r["total"], 1)))
        if want_calls:
            print("   " + " ".join("%s:%d" % kv for kv in sorted(r["calls"].items())))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
