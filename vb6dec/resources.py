"""What a project carries besides its code: the .res file and the compile
options, both recovered from the EXE."""
import struct

RT_ICON, RT_GROUP_ICON, RT_VERSION = 3, 14, 16


def project_resources(im):
    """(type, name, language, data) for every resource the project's .res file
    supplied.  VB writes the version block (from the .vbp) and the icons
    itself: RT_VERSION, and RT_ICON entries numbered from 30001 with the
    RT_GROUP_ICON that lists them."""
    pe = im.pe
    root = getattr(pe, "DIRECTORY_ENTRY_RESOURCE", None)
    if root is None:
        return []
    out = []
    for t in root.entries:
        tkey = str(t.name) if t.name is not None else t.id
        for n in t.directory.entries:
            nkey = str(n.name) if n.name is not None else n.id
            for lang in n.directory.entries:
                d = lang.data.struct
                data = pe.get_data(d.OffsetToData, d.Size)
                if tkey == RT_VERSION:
                    continue
                if tkey == RT_ICON and isinstance(nkey, int) and nkey >= 30001:
                    continue
                if tkey == RT_GROUP_ICON and vb_icon_group(data):
                    continue
                out.append((tkey, nkey, lang.id, data))
    return out


def parse_version(data):
    """{"major", "minor", "revision", "strings"} of a VS_VERSIONINFO block, None if it isn't one."""
    def node(o):
        """(key, offset of the value, length of the value in its own units, offset of the children, end)"""
        length, vlen, typ = struct.unpack_from("<HHH", data, o)
        e = o + 6
        while data[e:e + 2] != b"\0\0":
            e += 2
        key = data[o + 6:e].decode("utf-16-le", "replace")
        v = (e + 2 + 3) & ~3
        size = vlen * 2 if typ == 1 else vlen
        return key, v, vlen, (v + size + 3) & ~3, o + length, typ

    def children(start, end):
        o = start
        while o + 6 <= end:
            n = node(o)
            if n[4] <= o:
                return
            yield n
            o = (n[4] + 3) & ~3
    try:
        key, v, vlen, first, end, _ = node(0)
        if key != "VS_VERSION_INFO" or vlen < 52 or struct.unpack_from("<I", data, v)[0] != 0xFEEF04BD:
            return None
        ms, ls = struct.unpack_from("<II", data, v + 8)
        out = {"major": ms >> 16, "minor": ms & 0xFFFF, "revision": ls & 0xFFFF, "strings": {}}
        for k, _, _, kids, kend, _ in children(first, end):
            if k != "StringFileInfo":
                continue
            for _, _, _, table, tend, _ in children(kids, kend):
                for name, sv, n, _, _, _ in children(table, tend):
                    out["strings"][name] = data[sv:sv + n * 2].decode("utf-16-le", "replace").split("\0")[0]
                return out      # (the first table: VB writes one)
        return out
    except (struct.error, IndexError):
        return None


def version_info(im):
    """What the project's Make tab said: VB writes the version block from it, and App.Major and the like read it back
    at run time.  None if the program has no block."""
    root = getattr(im.pe, "DIRECTORY_ENTRY_RESOURCE", None)
    for t in root.entries if root is not None else ():
        if t.id == RT_VERSION:
            for n in t.directory.entries:
                for lang in n.directory.entries:
                    d = lang.data.struct
                    return parse_version(im.pe.get_data(d.OffsetToData, d.Size))
    return None


def icon_images(im):
    """The images of the EXE's own icon, the ones VB numbers from 30001.  Project properties name a form for it."""
    root = getattr(im.pe, "DIRECTORY_ENTRY_RESOURCE", None)
    out = []
    for t in root.entries if root is not None else ():
        if t.id == RT_ICON:
            for n in t.directory.entries:
                if n.id is not None and n.id >= 30001:
                    for lang in n.directory.entries:
                        d = lang.data.struct
                        out.append(im.pe.get_data(d.OffsetToData, d.Size))
    return out


def vb_icon_group(data):
    """A GRPICONDIR whose entries all point at VB's own icons (30001...)."""
    if len(data) < 6:
        return False
    count = struct.unpack_from("<H", data, 4)[0]
    ids = [struct.unpack_from("<H", data, 6 + 14 * i + 12)[0] for i in range(count)
           if 6 + 14 * i + 14 <= len(data)]
    return bool(ids) and all(i >= 30001 for i in ids)


def res_file(resources):
    """A 32-bit .res file (what RC.EXE writes) holding the resources."""
    def ident(x):
        if isinstance(x, int):
            return struct.pack("<HH", 0xFFFF, x)
        return x.upper().encode("utf-16-le") + b"\0\0"

    def entry(typ, name, lang, data, flags=0x0030):
        hdr = ident(typ) + ident(name)
        hdr += b"\0" * (-(8 + len(hdr)) % 4)
        hdr += struct.pack("<IHHII", 0, flags, lang, 0, 0)
        out = struct.pack("<II", len(data), 8 + len(hdr)) + hdr + data
        return out + b"\0" * (-len(out) % 4)
    blob = struct.pack("<II", 0, 32) + struct.pack("<HHHH", 0xFFFF, 0, 0xFFFF, 0) + b"\0" * 16
    for typ, name, lang, data in resources:
        blob += entry(typ, name, lang, data)
    return blob


def compile_options(im):
    """.vbp switches that change what the program does, read from the code.
    A check is written as removed when its code never appears; for a
    program that has no such arithmetic both settings are the same.
      OverflowCheck: jo -> __vbaErrorOverflow (called directly or via a thunk)
      FlPointCheck:  fnstsw ax / test al, 0Dh / jne after FP operations
      BoundsCheck:   __vbaGenerateBoundsError, also through a register (fast code:
                     mov edi, [iat]; ...; call edi), or the runtime's checked __vbaDerefAry1
      FDIVCheck:     fdiv replaced by a test of the Pentium flag and calls of
                     _adj_fdiv_* (no effect on results on a sound FPU)
    UnroundedFP is left at its default: it only changes how integers are
    converted for FP arithmetic (fimul instead of a Double temporary), which
    gives the same values under the 53-bit precision Windows sets."""
    text = b""
    for va, size, name in im.sections:
        if name.startswith(".text"):
            text += im.bytes(va, size)
            tva = va
            break
    else:
        return {}
    iat = {}
    for slot, name in im.imports.items():
        iat.setdefault(name.split("!")[-1], []).append(slot)

    def used(*names):
        for nm in names:
            for slot in iat.get(nm, ()):
                p = struct.pack("<I", slot)
                j = text.find(b"\xff\x25" + p)
                # call [iat], mov reg, [iat]: anything but the thunk.  (The optimiser keeps a pointer it
                # calls often in a register; the plain build of tools/optmatrix.py read as checks removed)
                if text.count(p) > (1 if j >= 0 else 0):
                    return True
                if j >= 0 and calls_to(tva + j):
                    return True             # call thunk; thunk: jmp [iat]
        return False

    def calls_to(target):
        for op, size, at in ((b"\xe8", 5, 1), (b"\xe9", 5, 1), (b"\x0f", 6, 2)):
            k = text.find(op)
            while k >= 0:
                if k + size <= len(text) and (op != b"\x0f" or 0x80 <= text[k + 1] <= 0x8f) and \
                        (tva + k + size + struct.unpack_from("<i", text, k + at)[0]) & 0xffffffff == target:
                    return True
                k = text.find(op, k + 1)
        return False

    fdiv = [n for n in iat if n.startswith(("_adj_fdiv", "_adj_fpatan", "_adj_fprem", "_adj_fptan"))]
    return {
        "OverflowCheck": 0 if used("__vbaErrorOverflow") else -1,
        "FlPointCheck": 0 if b"\xdf\xe0\xa8\x0d" in text else -1,
        "BoundsCheck": 0 if used("__vbaGenerateBoundsError", "__vbaDerefAry1") else -1,
        "FDIVCheck": 0 if used(*fdiv) else -1,
    }
