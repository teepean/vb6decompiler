"""Minimal PE image access by virtual address."""
import struct
import pefile


class Image:
    def __init__(self, path):
        self.path = path
        self.pe = pefile.PE(path)
        self.base = self.pe.OPTIONAL_HEADER.ImageBase
        self.mem = self.pe.get_memory_mapped_image()
        self.imports = {}           # IAT slot VA -> "DLL!name" / "DLL!#ord"
        for dll in getattr(self.pe, "DIRECTORY_ENTRY_IMPORT", []):
            dn = dll.dll.decode().upper()
            for imp in dll.imports:
                name = imp.name.decode() if imp.name else "#%d" % imp.ordinal
                self.imports[imp.address] = name if dn == "MSVBVM60.DLL" else "%s!%s" % (dn, name)
        self.sections = [(self.base + s.VirtualAddress, s.Misc_VirtualSize,
                          s.Name.rstrip(b"\0").decode()) for s in self.pe.sections]

    # A snapshot (snapshot.py) keeps no pefile object: its mmap cannot be pickled,
    # and only the resources and optprobe read it once made
    def __getstate__(self):
        return {k: v for k, v in self.__dict__.items() if k != "pe"}

    def __getattr__(self, name):
        if name == "pe":
            self.pe = pefile.PE(self.path)
            return self.pe
        raise AttributeError(name)

    def valid(self, va):
        return va is not None and self.base <= va < self.base + len(self.mem)

    def bytes(self, va, n):
        o = va - self.base
        if o < 0 or o + n > len(self.mem):
            raise ValueError("VA %#x out of image" % va)
        return self.mem[o:o + n]

    def u8(self, va):  return self.bytes(va, 1)[0]
    def u16(self, va): return struct.unpack_from("<H", self.mem, va - self.base)[0]
    def i16(self, va): return struct.unpack_from("<h", self.mem, va - self.base)[0]
    def u32(self, va): return struct.unpack_from("<I", self.mem, va - self.base)[0]
    def i32(self, va): return struct.unpack_from("<i", self.mem, va - self.base)[0]

    def cstr(self, va, maxlen=1024):
        if not self.valid(va):
            return None
        o = va - self.base
        e = self.mem.find(b"\0", o, o + maxlen)
        return self.mem[o:e if e >= 0 else o + maxlen].decode("latin-1")

    def wstr(self, va, maxlen=4096):
        o = va - self.base
        out = []
        while len(out) < maxlen:
            c = struct.unpack_from("<H", self.mem, o)[0]
            if c == 0:
                break
            out.append(chr(c))
            o += 2
        return "".join(out)

    def bstr(self, va):
        """VB string constant: pointer to UTF-16 data preceded by a byte length."""
        n = self.u32(va - 4)
        return self.bytes(va, n).decode("utf-16-le", "replace")

    def guid(self, va):
        d1, d2, d3 = struct.unpack_from("<IHH", self.mem, va - self.base)
        d4 = self.bytes(va + 8, 8)
        return "{%08X-%04X-%04X-%s-%s}" % (d1, d2, d3, d4[:2].hex().upper(), d4[2:].hex().upper())

    def section_of(self, va):
        for s, n, name in self.sections:
            if s <= va < s + n:
                return name
        return None
