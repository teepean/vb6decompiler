#!/usr/bin/env python3
"""Is an EXE a VB6 native-code program worth decompiling?  Reads the file, runs nothing.

usage: python3 -I vbprobe.py FILE_OR_DIR...

Prints for each VB5/VB6 PE file: runtime DLL, native or P-code, code size, objects and forms.
A file with no VB header is listed as such only when named directly."""
import os
import re
import struct
import sys


def rva_map(d):
    pe = struct.unpack_from("<I", d, 0x3C)[0]
    if d[pe:pe + 4] != b"PE\0\0":
        return None
    n = struct.unpack_from("<H", d, pe + 6)[0]
    opt = struct.unpack_from("<H", d, pe + 20)[0]
    base = struct.unpack_from("<I", d, pe + 52)[0]
    secs = []
    for i in range(n):
        s = pe + 24 + opt + i * 40
        vs, va, rs, rp = struct.unpack_from("<IIII", d, s + 8)
        secs.append((base + va, max(vs, rs), rp, d[s:s + 8].rstrip(b"\0").decode("latin1")))
    return secs


def probe(p):
    d = open(p, "rb").read()
    if d[:2] != b"MZ":
        return None
    i = d.find(b"VB5!")
    secs = rva_map(d) if len(d) > 0x40 else None
    if i < 0 or not secs:
        return None

    def off(va):
        for b, sz, rp, _ in secs:
            if b <= va < b + sz:
                return rp + va - b
        return None
    ds = lambda o: struct.unpack_from("<I", d, o)[0]
    wc = struct.unpack_from("<H", d, i + 0x44)[0]
    pi = off(ds(i + 0x30))
    if pi is None:
        return "no project info"
    native = ds(pi + 0x20) != 0
    code = ds(pi + 0x10) - ds(pi + 0xC)
    ot = off(ds(pi + 4))
    total = struct.unpack_from("<H", d, ot + 0x2A)[0] if ot else -1
    rt = "MSVBVM60" if b"MSVBVM60" in d.upper() else "MSVBVM50" if b"MSVBVM50" in d.upper() else "?"
    ocx = sorted({x.decode("latin1").lower() for x in re.findall(rb"(?i)[a-z0-9_]+\.ocx", d)})
    names = ",".join(s[3] for s in secs)
    return "%s %s code=%dK file=%dK objects=%d forms=%d sections=%s ocx=%s" % (
        rt, "NATIVE" if native else "pcode", code // 1024, len(d) // 1024, total, wc, names, ",".join(ocx))


def main(argv):
    for a in argv:
        if os.path.isdir(a):
            for dp, _, fn in os.walk(a):
                for f in fn:
                    if f.lower().endswith((".exe", ".dll", ".ocx", ".scr")):
                        p = os.path.join(dp, f)
                        r = probe(p)
                        if r:
                            print(p, r)
        else:
            print(a, probe(a) or "no VB header")


if __name__ == "__main__":
    main(sys.argv[1:])
