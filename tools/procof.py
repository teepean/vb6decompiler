#!/usr/bin/env python3
"""The procedure that uses a string literal (its error handler's name, say).

usage: procof.py EXE TEXT   prints the VA of the prologue before each reference
"""
import struct
import sys

import pefile


def main(argv):
    exe, text = argv
    pe = pefile.PE(exe, fast_load=True)
    base = pe.OPTIONAL_HEADER.ImageBase
    im = bytes(pe.get_memory_mapped_image())
    a = im.find(text.encode("utf-16le") + b"\0\0")
    if a < 0:
        print("not found")
        return 1
    pat, pos = struct.pack("<I", a + base), 0
    while True:
        j = im.find(pat, pos)
        if j < 0:
            return 0
        pos = j + 1
        k = im.rfind(b"\x55\x8b\xec\x83\xec", 0, j)
        print("%X" % (k + base))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
