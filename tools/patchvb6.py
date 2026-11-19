#!/usr/bin/env python3
"""Let VB6.EXE build projects with OCX controls under Wine: give it an
import of vb6fix.dll (tools/vb6fix/), which corrects some of Wine's type
library and picture functions in memory when it loads.  See vb6fix.c for the
five bugs; without the fixes every project with an OCX control fails, first with
"System Error &H80004001 Not implemented", then with a stack overflow.

usage: patchvb6.py VB6.EXE [--check]

The original is kept as VB6.EXE.orig and every run patches that copy again,
so running it twice is harmless.  The patch is a new section ".vb6fix"
holding the import directory: VB6's own descriptors, then one for
vb6fix.dll!vb6fix.  No VB6 code changes.  vb6fix.dll is copied next to
VB6.EXE (its own folder comes first in the DLL search order); build it with
tools/vb6fix/build.sh.  Works on the RTM VB6.EXE (6.0.81.76) and on SP6
(6.0.97.82).
"""
import os
import shutil
import struct
import sys

import pefile

HERE = os.path.dirname(os.path.abspath(__file__))
DLL = os.path.join(HERE, "vb6fix", "vb6fix.dll")
SECTION = b".vb6fix\0"


def align(n, a):
    return -(-n // a) * a


def add_import(data, dll_name, func_name):
    """data: the PE file; returns it with a new section holding an import
    directory that adds dll_name!func_name to the existing imports."""
    pe = pefile.PE(data=bytes(data))
    if any(s.Name == SECTION for s in pe.sections):
        raise SystemExit("already has a %s section" % SECTION.rstrip(b"\0").decode())
    opt = pe.OPTIONAL_HEADER
    sa, fa = opt.SectionAlignment, opt.FileAlignment
    imp = opt.DATA_DIRECTORY[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_IMPORT"]]
    off = pe.get_offset_from_rva(imp.VirtualAddress)
    descs = []
    while True:
        d = bytes(data[off:off + 20])
        if d == b"\0" * 20:
            break
        descs.append(d)
        off += 20
    newva = align(max(s.VirtualAddress + max(s.Misc_VirtualSize, s.SizeOfRawData)
                      for s in pe.sections), sa)
    # new directory, then the lookup table, the IAT, the name and the hint/name
    n = len(descs) + 2
    int_off = 20 * n
    iat_off = int_off + 8
    hint_off = iat_off + 8
    hint = struct.pack("<H", 0) + func_name.encode() + b"\0"
    hint += b"\0" * (len(hint) & 1)
    name_off = hint_off + len(hint)
    body = b"".join(descs)
    body += struct.pack("<IIIII", newva + int_off, 0, 0, newva + name_off, newva + iat_off)
    body += b"\0" * 20
    body += struct.pack("<II", newva + hint_off, 0) * 2
    body += hint + dll_name.encode() + b"\0"
    raw = align(len(body), fa)
    rawptr = align(len(data), fa)
    out = bytearray(data) + b"\0" * (rawptr - len(data)) + body + b"\0" * (raw - len(body))
    # section header after the last one
    fh = pe.DOS_HEADER.e_lfanew + 4
    oh = fh + 20
    nsec = pe.FILE_HEADER.NumberOfSections
    sh = fh + 20 + pe.FILE_HEADER.SizeOfOptionalHeader + 40 * nsec
    if sh + 40 > opt.SizeOfHeaders:
        # SP6's VB6.EXE declares 200h bytes of headers, but its first section
        # starts at 1000h in the file and in memory: the headers can grow
        room = min(min(s.PointerToRawData for s in pe.sections),
                   min(s.VirtualAddress for s in pe.sections))
        if sh + 40 > room:
            raise SystemExit("no room for another section header")
        struct.pack_into("<I", out, oh + 60, align(sh + 40, fa) if align(sh + 40, fa) <= room
                         else room)                                     # SizeOfHeaders
        out[sh:sh + 40] = b"\0" * 40        # (padding outside every section)
    if any(out[sh:sh + 40]):
        raise SystemExit("no room for another section header")
    out[sh:sh + 40] = SECTION + struct.pack("<IIIIIIHHI", len(body), newva, raw, rawptr,
                                            0, 0, 0, 0, 0xC0000040)   # initialised data, RW
    struct.pack_into("<H", out, fh + 2, nsec + 1)
    struct.pack_into("<I", out, oh + 56, align(newva + len(body), sa))      # SizeOfImage
    struct.pack_into("<II", out, oh + 96 + 8 * 1, newva, 20 * (n - 1))   # import directory
    struct.pack_into("<II", out, oh + 96 + 8 * 11, 0, 0)                 # no bound imports
    return out


def main(argv):
    if not argv:
        print(__doc__.strip())
        return 2
    exe = argv[0]
    orig = exe + ".orig"
    src = orig if os.path.exists(orig) else exe
    pe = pefile.PE(exe, fast_load=True)
    patched = any(s.Name == SECTION for s in pe.sections)
    pe.close()
    if "--check" in argv:
        print("%s: %s" % (exe, "patched" if patched else "not patched"))
        return 0
    if not os.path.exists(DLL):
        raise SystemExit("build %s first (tools/vb6fix/build.sh)" % DLL)
    new = add_import(open(src, "rb").read(), "vb6fix.dll", "vb6fix")
    if src == exe:
        shutil.copy2(exe, orig)
    with open(exe, "wb") as f:
        f.write(new)
    shutil.copy2(DLL, os.path.join(os.path.dirname(exe), "vb6fix.dll"))
    print("%s: imports vb6fix.dll (original kept as %s)" % (exe, os.path.basename(orig)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
