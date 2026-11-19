#!/usr/bin/env python3
"""What a VB6 EXE's code says about the compile options it was made with.

usage: optprobe.py EXE...

For each EXE: native code or P-Code, the checks resources.compile_options
reads (OverflowCheck, FlPointCheck, BoundsCheck, FDIVCheck), whether there
is CodeView debug information, and the counts the optimisation shows in:
  direct   call [iat] to a runtime function (optimised code)
  thunk    call to a "jmp [iat]" thunk (unoptimised code calls through them)
  sub_esp  prologues "push ebp / mov ebp, esp / sub esp, imm8 / push offset
           handler" (optimised code)
  push_ecx prologues "push ebp / mov ebp, esp / push ecx..." (unoptimised)
"""
import os
import struct
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from vb6dec.pe import Image                 # noqa: E402
from vb6dec import resources                # noqa: E402


def probe(path):
    im = Image(path)
    out = {}
    text, tva = b"", None
    for va, size, name in im.sections:
        if name.startswith(".text"):
            text, tva = im.bytes(va, size), va
            break
    vb = {slot for slot, name in im.imports.items() if "!" not in name}
    direct = thunk = 0
    thunks = set()
    k = text.find(b"\xff")
    while k >= 0:
        if k + 6 <= len(text) and text[k + 1] in (0x15, 0x25):
            slot = struct.unpack_from("<I", text, k + 2)[0]
            if slot in vb:
                if text[k + 1] == 0x15:
                    direct += 1
                else:
                    thunks.add(tva + k)
        k = text.find(b"\xff", k + 1)
    k = text.find(b"\xe8")
    while k >= 0:
        if k + 5 <= len(text):
            t = (tva + k + 5 + struct.unpack_from("<i", text, k + 1)[0]) & 0xffffffff
            if t in thunks:
                thunk += 1
        k = text.find(b"\xe8", k + 1)
    sub_esp = push_ecx = 0
    k = text.find(b"\x55\x8b\xec")
    while k >= 0:
        nxt = text[k + 3:k + 7]
        if nxt[:2] == b"\x83\xec" and nxt[3:4] == b"\x68":
            sub_esp += 1
        elif nxt[:1] == b"\x51":
            push_ecx += 1
        k = text.find(b"\x55\x8b\xec", k + 1)
    out["direct"], out["thunk"] = direct, thunk
    out["sub_esp"], out["push_ecx"] = sub_esp, push_ecx
    # CodeView information: the debug directory's entries
    dbg = []
    for e in getattr(im.pe, "DIRECTORY_ENTRY_DEBUG", []):
        name = {2: "CodeView", 1: "COFF", 4: "Misc"}.get(e.struct.Type, str(e.struct.Type))
        info = getattr(e, "entry", None)
        pdb = getattr(info, "PdbFileName", b"") if info is not None else b""
        dbg.append(name + (" " + pdb.rstrip(b"\0").decode("latin-1") if pdb else ""))
    out["debug"] = ", ".join(dbg) or "none"
    # P-Code: the project information's native code pointer is 0
    from vb6dec.project import Project
    try:
        prj = Project(path)
        out["code"] = "P-Code" if prj.pcode else "native"
    except Exception as e:                  # noqa: BLE001
        out["code"] = "? (%s)" % e
    out.update(resources.compile_options(im))
    return out


def main(argv):
    for path in argv:
        r = probe(path)
        checks = " ".join("%s=%s" % (k, v) for k, v in r.items()
                          if k in ("OverflowCheck", "FlPointCheck", "BoundsCheck", "FDIVCheck"))
        calls = r["direct"] + r["thunk"]
        print("%-28s %-6s calls direct %5d thunk %5d (%3.0f%% thunk)  prologue sub_esp %4d push_ecx %4d"
              "  debug: %s  %s" % (
                  os.path.basename(path)[:28], r["code"], r["direct"], r["thunk"],
                  100.0 * r["thunk"] / calls if calls else 0, r["sub_esp"], r["push_ecx"],
                  r["debug"], checks))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
