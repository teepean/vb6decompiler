"""Reader for MSFT-format type libraries (VB6.OLB, MSVBVM60.DLL, OCXs).

Only what the decompiler needs: type infos with their functions (name,
member id, vtable offset, invoke kind, parameters) and coclass/interface
relations.  Layout follows the MSFT format as documented by Wine.
"""
import struct

TKIND = ["enum", "record", "module", "interface", "dispatch", "coclass", "alias", "union"]
INVOKE = {1: "func", 2: "get", 4: "let", 8: "set"}
VT = {0: "Empty", 1: "Null", 2: "Integer", 3: "Long", 4: "Single", 5: "Double",
      6: "Currency", 7: "Date", 8: "String", 9: "Object", 10: "Error", 11: "Boolean",
      12: "Variant", 13: "IUnknown", 14: "Decimal", 16: "I1", 17: "Byte", 18: "UI2",
      19: "UI4", 20: "I8", 21: "UI8", 22: "INT", 23: "UINT", 24: "Void", 25: "HRESULT",
      26: "PTR", 27: "SAFEARRAY", 28: "CARRAY", 29: "USERDEFINED", 30: "LPSTR",
      31: "LPWSTR"}
PARAMFLAG_FIN, PARAMFLAG_FOUT, PARAMFLAG_FRETVAL, PARAMFLAG_FOPT = 1, 2, 8, 16


class TypeDesc:
    def __init__(self, vt, sub=None, href=None):
        self.vt, self.sub, self.href = vt, sub, href

    lib = None

    def name(self, lib=None):
        lib = lib or self.lib
        if self.vt == 26:
            return self.sub.name(lib) + "*"
        if self.vt == 27:
            return self.sub.name(lib) + "()"
        if self.vt == 29:
            ti = lib.resolve_href(self.href) if lib is not None else None
            return ti.name if ti else "<ref %#x>" % self.href
        return VT.get(self.vt, "vt%d" % self.vt)


class Param:
    def __init__(self, name, td, flags):
        self.name, self.type, self.flags = name, td, flags


# VB writes its own types for the OLE control aliases in an event handler
# (tests/t17ocxev: MSComctlLib MouseDown's OLE_XPOS_PIXELS is `x As Single`,
# not the Long the library aliases it to)
OLE_ALIASES = {"OLE_XPOS_PIXELS": "Single", "OLE_YPOS_PIXELS": "Single",
               "OLE_XSIZE_PIXELS": "Single", "OLE_YSIZE_PIXELS": "Single",
               "OLE_XPOS_CONTAINER": "Single", "OLE_YPOS_CONTAINER": "Single",
               "OLE_XSIZE_CONTAINER": "Single", "OLE_YSIZE_CONTAINER": "Single",
               "OLE_XPOS_HIMETRIC": "Long", "OLE_YPOS_HIMETRIC": "Long",
               "OLE_XSIZE_HIMETRIC": "Long", "OLE_YSIZE_HIMETRIC": "Long",
               "OLE_CANCELBOOL": "Boolean", "OLE_ENABLEDEFAULTBOOL": "Boolean",
               "OLE_COLOR": "Long", "OLE_HANDLE": "Long", "OLE_OPTEXCLUSIVE": "Boolean"}


def type_name(td):
    """The name VB gives a parameter's type, with the OLE aliases mapped."""
    cur = td
    for _ in range(8):
        if cur is None or cur.vt != 29:
            break
        ti = cur.lib.resolve_href(cur.href) if cur.lib is not None else None
        name = getattr(ti, "name", None)
        if name in OLE_ALIASES:
            return OLE_ALIASES[name]
        if getattr(ti, "kind", None) != 6 or getattr(ti, "alias", None) is None:
            break
        cur = ti.alias
    return (cur.name() if cur is not None else td.name()).rstrip("*")


def unalias(td):
    """An alias resolved to the type it stands for: VB writes the base type
    in an event handler (MSComctlLib MouseDown's x As OLE_XPOS_PIXELS is
    `x As Single`)."""
    for _ in range(8):
        if td is None or td.vt != 29:
            break
        ti = td.lib.resolve_href(td.href) if td.lib is not None else None
        if getattr(ti, "kind", None) != 6 or getattr(ti, "alias", None) is None:
            break
        td = ti.alias
    return td


def by_reference(td, dispatch=False):
    """(ByRef, the type itself) for a parameter's descriptor.  An object is
    already one pointer and a scalar none, so ByRef is one level more than
    that.  An OCX's events are a dispinterface, where VB's handler takes
    every scalar ByRef whatever the library says (tests/t17ocxev: VB6
    refuses `ByVal Shift As Integer` for MSComctlLib's ListView KeyDown, and
    refuses `Item As ListItem` without ByVal for ItemClick)."""
    depth, cur = 0, td
    while cur is not None and cur.vt == 26 and cur.sub is not None:
        depth += 1
        cur = cur.sub
    base = unalias(cur)             # (cur itself stays: type_name wants the alias)
    natural = 0
    if base is not None and base.vt == 29:
        ti = base.lib.resolve_href(base.href) if base.lib is not None else None
        if getattr(ti, "kind", None) in (3, 4, 5):      # interface, dispatch, coclass
            natural = 1
    if dispatch:
        return not (natural and depth <= natural), cur
    return depth > natural, cur


class Func:
    def __init__(self):
        self.name = None
        self.memid = None
        self.vtoff = None
        self.invkind = 1
        self.funckind = 0
        self.ret = None
        self.params = []
        self.flags = 0


class TypeInfo:
    def __init__(self, lib, index):
        self.lib, self.index = lib, index
        self.funcs, self.vars, self.impls = [], [], []

    def __repr__(self):
        return "<%s %s %s>" % (TKIND[self.kind], self.name, self.guid)

    def all_funcs(self):
        """Functions including inherited ones (by vtable)."""
        out = []
        for base in self.bases():
            out.extend(base.all_funcs())
        out.extend(self.funcs)
        return out

    def bases(self):
        if self.kind in (3, 4) and self.impls:
            r = self.lib.resolve_href(self.impls[0][0])
            if r is not None and r is not self:
                return [r]
        return []

    def by_vtoff(self, off, invkind=None):
        for f in self.all_funcs():
            if f.vtoff == off and (invkind is None or f.invkind == invkind):
                return f
        return None


class TypeLib:
    def __init__(self, data, name=None):
        self.d = data
        self.name = name
        self.external = {}      # href -> (libguid, typename)
        self._parse()

    @classmethod
    def from_file(cls, path, res=None):
        """res: which TYPELIB resource, when a file has several (MSVBVM60.DLL: 1 VBA, 3 VBRUN)."""
        data = open(path, "rb").read()
        if data[:4] != b"MSFT":
            data = cls._from_pe(path, res)
        return cls(data, path)

    _files = {}

    @classmethod
    def cached(cls, path):
        """from_file once per file (the OCX folder is searched more than
        once), or None when it holds no type library."""
        if path not in cls._files:
            try:
                cls._files[path] = cls.from_file(path)
            except Exception:
                cls._files[path] = None
        return cls._files[path]

    @staticmethod
    def _from_pe(path, res=None):
        import pefile
        # only the resources: a full parse (relocations, imports) of the 47
        # OCX folder files took 1 s of every decompile
        pe = pefile.PE(path, fast_load=True)
        pe.parse_data_directories(directories=[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_RESOURCE"]])
        for t in pe.DIRECTORY_ENTRY_RESOURCE.entries:
            if t.name is not None and str(t.name) == "TYPELIB":
                ents = t.directory.entries
                if res is not None:
                    ents = [x for x in ents if x.id == res]
                e = ents[0].directory.entries[0].data.struct
                return pe.get_data(e.OffsetToData, e.Size)
        raise ValueError("no TYPELIB resource in %s" % path)

    def i32(self, o): return struct.unpack_from("<i", self.d, o)[0]
    def u32(self, o): return struct.unpack_from("<I", self.d, o)[0]
    def i16(self, o): return struct.unpack_from("<h", self.d, o)[0]
    def u16(self, o): return struct.unpack_from("<H", self.d, o)[0]

    def _parse(self):
        d = self.d
        assert d[:4] == b"MSFT", "not an MSFT type library"
        varflags = self.u32(0x14)
        self.ntypes = self.i32(0x20)
        o = 0x54
        if varflags & 0x100:
            o += 4
        self.ti_offs = [self.i32(o + 4 * i) for i in range(self.ntypes)]
        self._posguid = self.i32(0x08)
        o += 4 * self.ntypes
        seg = []
        for i in range(15):
            seg.append((self.i32(o), self.i32(o + 4)))
            o += 16
        (self.s_ti, self.s_impinfo, self.s_impfiles, self.s_ref, self.s_guidhash,
         self.s_guid, self.s_namehash, self.s_name, self.s_string, self.s_typdesc,
         self.s_arrdesc, self.s_custdata, self.s_cdguid, _, _) = seg
        self._typedescs = {}
        self.libid = self.guid_at(self._posguid)
        v = self.i32(0x18)
        self.lib_major, self.lib_minor = v & 0xffff, (v >> 16) & 0xffff
        try:
            self.lib_name = self.name_at(self.i32(0x38))
        except Exception:
            self.lib_name = None
        self.lib_doc = None
        try:
            ds = getattr(self, "string_at", None)
            if ds is not None and self.i32(0x24) >= 0:
                self.lib_doc = ds(self.i32(0x24))
        except Exception:
            pass
        self.types = [self._typeinfo(i) for i in range(self.ntypes)]
        self.by_name = {t.name.lower(): t for t in self.types}
        self.by_guid = {t.guid: t for t in self.types if t.guid}

    # ---- tables ---------------------------------------------------------
    def name_at(self, off):
        if off < 0:
            return None
        o = self.s_name[0] + off
        n = self.u32(o + 8) & 0xff
        return self.d[o + 12:o + 12 + n].decode("latin-1")

    def string_at(self, off):
        if off < 0:
            return None
        o = self.s_string[0] + off
        n = self.u16(o)
        return self.d[o + 2:o + 2 + n].decode("latin-1")

    def guid_at(self, off):
        if off < 0:
            return None
        o = self.s_guid[0] + off
        d1, d2, d3 = struct.unpack_from("<IHH", self.d, o)
        d4 = self.d[o + 8:o + 16]
        return "{%08X-%04X-%04X-%s-%s}" % (d1, d2, d3, d4[:2].hex().upper(), d4[2:].hex().upper())

    def typedesc(self, dt):
        if dt < 0:
            td = TypeDesc(dt & 0xfff)
            td.lib = self
            return td
        if dt in self._typedescs:
            return self._typedescs[dt]
        o = self.s_typdesc[0] + dt
        v0, v1, v2, v3 = struct.unpack_from("<hhhh", self.d, o)
        vt = v0 & 0xfff
        if vt in (26, 27):
            sub = TypeDesc(v2 & 0x7fff) if v3 < 0 else self.typedesc(self.u32(o + 4) & 0xffff)
            td = TypeDesc(vt, sub=sub)
        elif vt == 29:
            td = TypeDesc(vt, href=self.u32(o + 4))
        elif vt == 28:
            td = TypeDesc(vt, sub=TypeDesc(12))
        else:
            td = TypeDesc(vt)
        td.lib = self
        if td.sub is not None and td.sub.lib is None:
            td.sub.lib = self
        self._typedescs[dt] = td
        return td

    def resolve_href(self, href):
        if href is None or href < 0:
            return None
        if href & 3 == 0:
            idx = href // 0x64
            return self.types[idx] if idx < len(self.types) else None
        # imported: offset into ImpInfo table
        o = self.s_impinfo[0] + (href & ~3)
        if o + 12 > len(self.d):
            return None
        flags, oimpfile, oguid = struct.unpack_from("<iii", self.d, o)
        ext = ExternalType()
        ext.libfile = self._impfile(oimpfile)
        if flags & 0x10000:                 # MSFT_IMPINFO_OFFSET_IS_GUID
            ext.guid = self.guid_at(oguid)
            ext.name = ext.guid
            r = Registry.lookup(ext)
            return r or ext
        # otherwise oguid is the type's index in the imported library
        ext.index = oguid
        ext.name = "#%d" % oguid
        lib = Registry.lib_by_id(ext.libfile[0])
        if lib is not None:
            idx = oguid // 0x64 if oguid % 0x64 == 0 and oguid // 0x64 < lib.ntypes else oguid
            if 0 <= idx < lib.ntypes:
                return lib.types[idx]
        return ext

    def _impfile(self, off):
        o = self.s_impfiles[0] + off
        guid = self.guid_at(self.i32(o))
        n = self.u16(o + 12) >> 2
        return guid, self.d[o + 14:o + 14 + n].decode("latin-1")

    # ---- type infos -----------------------------------------------------
    def _typeinfo(self, idx):
        o = self.s_ti[0] + idx * 0x64
        t = TypeInfo(self, idx)
        tk = self.i32(o)
        t.kind = tk & 0xf
        memoff = self.i32(o + 4)
        cel = self.u32(o + 0x18)
        nfunc, nvar = cel & 0xffff, cel >> 16
        t.guid = self.guid_at(self.i32(o + 0x2c))
        t.flags = self.u32(o + 0x30)
        t.name = self.name_at(self.i32(o + 0x34))
        t.doc = self.string_at(self.i32(o + 0x3c))
        cimpl = self.u32(o + 0x4c)
        t.cimpl, t.cbvft = cimpl & 0xffff, cimpl >> 16
        t.size = self.u32(o + 0x50)
        dt1 = self.i32(o + 0x54)
        if t.kind == 5:                                  # coclass: RefTab chain
            r = dt1
            while r >= 0 and r != -1:
                ro = self.s_ref[0] + r
                reftype, rflags, _, onext = struct.unpack_from("<iiii", self.d, ro)
                t.impls.append((reftype, rflags))
                r = onext
        elif t.kind in (3, 4) and dt1 >= 0:
            t.impls.append((dt1, 0))
        elif t.kind == 6:
            t.alias = self.typedesc(dt1)
        if memoff >= 0 and (nfunc or nvar):
            self._members(t, memoff, nfunc, nvar)
        return t

    def _members(self, t, memoff, nfunc, nvar):
        infolen = self.i32(memoff)
        base = memoff + 4
        n = nfunc + nvar
        tail = base + infolen
        memids = [self.i32(tail + 4 * i) for i in range(n)]
        names = [self.i32(tail + 4 * (n + i)) for i in range(n)]
        offs = [self.i32(tail + 8 * n + 4 * i) for i in range(n)]
        for i in range(nfunc):
            ro = base + offs[i]
            info = self.u32(ro)
            reclen = info & 0xffff
            f = Func()
            f.ret = self.typedesc(self.i32(ro + 4))
            f.flags = self.u32(ro + 8) & 0xffff
            f.vtoff = self.i16(ro + 0x0c) & ~1
            fk = self.u32(ro + 0x10)
            f.funckind = fk & 7
            f.invkind = (fk >> 3) & 0xf
            f.callconv = (fk >> 8) & 0xf
            nargs = self.u16(ro + 0x14)
            f.nopt = self.u16(ro + 0x16)
            f.memid = memids[i]
            f.name = self.name_at(names[i])
            po = ro + reclen - 12 * nargs
            for a in range(nargs):
                dt, on, pf = struct.unpack_from("<iii", self.d, po + 12 * a)
                f.params.append(Param(self.name_at(on), self.typedesc(dt), pf))
            t.funcs.append(f)
        for i in range(nvar):
            j = nfunc + i
            ro = base + offs[j]
            t.vars.append((self.name_at(names[j]), memids[j], self.typedesc(self.i32(ro + 4)),
                           self.i32(ro + 0x0c)))   # var offset / const value offset


class ExternalType:
    guid = None
    name = None
    libfile = None
    kind = -1
    funcs = ()

    def __repr__(self):
        return "<external %s from %s>" % (self.guid, self.libfile)

    def all_funcs(self):
        return []


class Registry:
    """Libraries loaded so far, for resolving cross-library references."""
    libs = []

    @classmethod
    def add(cls, lib):
        cls.libs.append(lib)
        return lib

    @classmethod
    def lookup(cls, ext):
        for lib in cls.libs:
            t = lib.by_guid.get(ext.guid)
            if t is not None:
                return t
        return None

    @classmethod
    def lib_by_id(cls, libid):
        for lib in cls.libs:
            if lib.libid == libid:
                return lib
        return None

    extra_dir = None            # true: the libraries msfiles knows of are searched on a miss
    _scanned = False

    @classmethod
    def find_guid(cls, guid):
        for lib in cls.libs:
            t = lib.by_guid.get(guid)
            if t is not None:
                return t
        if not cls.extra_dir:
            return None
        from . import msfiles
        # (on Windows the registry knows; but a folder given by hand goes first)
        path = msfiles.library_of(guid) if cls._scanned or not msfiles.ocx_folder_given() else ""
        lib = TypeLib.cached(path) if path else None
        t = lib.by_guid.get(guid) if lib is not None else None
        if t is not None:
            if lib.libid not in {x.libid for x in cls.libs}:
                cls.add(lib)
            return t
        if not cls._scanned:
            # an interface of a referenced library that is no control's (the
            # Scripting FileSystemObject, early bound): every library there, once
            cls._scanned = True
            have = {lib.libid for lib in cls.libs}
            for f in msfiles.scan_files():
                lib = TypeLib.cached(f)
                if lib is not None and lib.libid not in have:
                    cls.add(lib)
                    have.add(lib.libid)
            return cls.find_guid(guid)
        return None
