#!/usr/bin/env python3
"""Dump the record descriptors of an EXE (records.py): header and raw bytes after each entry, to
see formats records.read doesn't decode yet.

usage: recdump.py EXE [MINSIZE] [BYTES]"""
import os
import struct
import sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from vb6dec.project import Project          # noqa: E402
from vb6dec import records                  # noqa: E402


def main(argv):
    prj = Project(argv[0])
    im = prj.im
    minsize = int(argv[1]) if len(argv) > 1 else 0
    nbytes = int(argv[2]) if len(argv) > 2 else 96
    text = bytes(im.mem)
    # every call pushing a descriptor is hard to list here: scan .text and .data for the shape
    base = im.base
    seen = 0
    for off in range(0, len(text) - 12, 4):
        magic, size, zero, count = struct.unpack_from("<HHHH", text, off)
        if zero or not count or count > 256 or size < max(minsize, 8) or size > 0xFFFF:
            continue
        va = base + off
        r = records.read(im, va)
        if r is None or not r.fields:
            continue
        seen += 1
        print("%08X magic %X size %X count %d fields %s" % (
            va, magic, size, count, {hex(k): v for k, v in list(r.fields.items())[:12]}))
        print("   ", text[off + 12:off + 12 + nbytes].hex(" "))
    print(seen, "descriptors")


if __name__ == "__main__":
    main(sys.argv[1:])
