#!/usr/bin/env python3
"""A .frx holding one form's Icon, for test projects: a 32x32 16-colour icon of two colours in a pattern of its own.

usage: frxicon.py OUT.frx SEED

The blob is what VB6 writes: total size, "lt\\0\\0", the .ico's size, the .ico.  In the .frm: Icon = "OUT.frx":0000
"""
import struct
import sys


def ico(seed):
    size = 40 + 16 * 4 + 32 * 16 + 32 * 4
    bih = struct.pack("<IiiHHIIiiII", 40, 32, 64, 1, 4, 0, 32 * 16, 0, 0, 0, 0)
    pal = b"".join(struct.pack("<BBBB", (i * seed) & 255, (i * 3 + seed) & 255, (i * 7) & 255, 0) for i in range(16))
    xor = bytes(((x + y * seed) & 15) << 4 | ((x + 1 + y * seed) & 15) for y in range(32) for x in range(16))
    head = struct.pack("<HHH", 0, 1, 1) + struct.pack("<BBBBHHII", 32, 32, 16, 0, 1, 4, size, 22)
    return head + bih + pal + xor + bytes(32 * 4)


def main():
    data = ico(int(sys.argv[2]))
    with open(sys.argv[1], "wb") as f:
        f.write(struct.pack("<I", len(data) + 8) + b"lt\0\0" + struct.pack("<I", len(data)) + data)


if __name__ == "__main__":
    main()
