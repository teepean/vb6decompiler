"""Members of forms, classes and user controls, from the descriptor VB keeps
for every object module (ObjectInfo+0x0C; -1 for standard modules).

Layout (verified on t02/t03, cf. Semi VB Decompiler's LinkNativePublicParams):

  hdr+0x04  the object's ObjectInfo (what __vbaNew receives for a class)
  hdr+0x10  u16 member variable count, u16 event count
  hdr+0x18  -> one method descriptor pointer per procedure, parallel to the
               object's method name table (0 for Private procedures)
  hdr+0x20  -> one variable descriptor pointer per member variable
               (0 for Private variables)
  hdr+0x24  -> one descriptor pointer per Public Event

  variable descriptor: +0x00 name, +0x0C DISPID, +0x10 vtable offset << 16 |
      accessor slots, +0x14 offset in the instance, +0x18 type code
  method / event descriptor:
      +0x00 byte 0 = slots << 2 | kind (0 Sub/Function, 1 Get, 2 Let, 3 Set)
            byte 1 bit 0 = returns a value (the last slot is the result)
            high word = vtable offset (0xFFFF for an event)
      +0x04 0xFFFF; for a WithEvents handler the vtable offset of the event
            in the source's event interface
      +0x08 -> Optional default values: {u32 size, u32 -> (u16 VARTYPE, value)}
      +0x0C DISPID
      +0x10 -> parameter names (char*)
      +0x20 type stream: one leading byte (0x1E method, 0x00 event), then per
            slot a type code | 0x20 ByRef (the result slot too) | 0x40 array
            (then a u16, 2-aligned) | 0x80 Optional; aligned to 4, one ObjectInfo
            pointer follows for every slot typed as a project class (0x13)
  Optional defaults: {u32 size, u32 ->} entries for the Optional parameters
      in order: u16 VARTYPE + value (String: u16 length + ANSI, padded to
      even; Byte padded; VT_EMPTY = no default); trailing ones without a
      default are left out.
  Type codes verified with t04 (clsT04: every type in every position).
"""
import struct

TYPES = {0x03: "Boolean", 0x05: "Byte", 0x06: "Integer", 0x08: "Long", 0x0A: "Single",
         0x0B: "Double", 0x0C: "Date", 0x0D: "Currency", 0x0F: "Variant", 0x10: "String",
         0x1B: "Object", 0x13: "Class"}
KINDS = {0: "method", 1: "get", 2: "let", 3: "set"}
CLASS = 0x13
EXTERN = 0x1D       # another library's object (As Control, As Form): a reference follows like CLASS's,
                    # {?, &IID, ...}


def extern_name(im, ref):
    """The VB name of the interface a 1Dh type refers to; Object if it can't be found."""
    from .typelib import Registry
    if not im.valid(ref) or not im.valid(im.u32(ref + 4)):
        return "Object"
    try:
        it = Registry.find_guid(im.guid(im.u32(ref + 4)))
    except Exception:
        it = None
    n = getattr(it, "name", None)
    return (n[1:] if n.startswith("_") else n) if n else "Object"


class Param:
    def __init__(self, name, code, default=None):
        self.name, self.code, self.default = name, code, default
        self.optional = bool(code & 0x80)
        self.byref = bool(code & 0x20)
        self.array = bool(code & 0x40)
        self.type = TYPES.get(code & 0x1F)
        self.cls = None             # ObjectInfo VA of a project class type
        self.paramarray = False


class Member:
    """A Public method, property accessor or event of an object."""

    def __init__(self, va):
        self.va = va                # descriptor address
        self.name = None
        self.kind = "method"        # method / get / let / set / event
        self.returns = False
        self.vtoff = None           # offset in the object's vtable
        self.event_slot = None      # WithEvents handler: the event's vtable offset
        self.dispid = None
        self.params = []
        self.ret_code = None
        self.lead = None
        self.proc = None            # code address of the procedure

        self.ret_cls = None

    @property
    def ret_type(self):
        if getattr(self, "ret_extern", None):
            return self.ret_extern
        return TYPES.get(self.ret_code & 0x1F) if self.ret_code is not None else None

    @property
    def ret_array(self):
        return self.ret_code is not None and bool(self.ret_code & 0x40)

    def __repr__(self):
        return "<%s %s vt=%s %s -> %s>" % (self.kind, self.name, self.vtoff and hex(self.vtoff),
                                          [(p.name, p.type, hex(p.code)) for p in self.params],
                                          self.ret_type)


class Var:
    def __init__(self, name, dispid, vtoff, slots, offset, code, cls=None):
        self.name, self.dispid, self.vtoff = name, dispid, vtoff
        self.offset, self.code, self.cls = offset, code, cls
        self.type = TYPES.get(code & 0x1F)
        if code & 0x1F == EXTERN:
            self.type = "Object"        # (the name, in Members: it needs the image)
        # accessors: Get, Let (+ Set for Object, Variant and class types)
        # (1Dh: Public WithEvents v As Form, DarwinBots TrayIcon.OwnerForm)
        self.slots = 3 if (code & 0x1F) in (0x0F, 0x1B, 0x1D, CLASS) else 2

    def __repr__(self):
        return "<var %s @%#x vt=%#x %s>" % (self.name, self.offset, self.vtoff, self.type)


def read_method(im, p):
    m = Member(p)
    f00 = im.u32(p)
    slots = (f00 & 0xff) >> 2
    m.kind = KINDS[f00 & 3]
    m.returns = bool(f00 & 0x100)
    hi = f00 >> 16
    m.vtoff = None if hi == 0xffff else hi & 0xfffc
    slot = im.u32(p + 4)
    m.event_slot = slot if slot != 0xffff else None
    m.dispid = im.u32(p + 0x0c)
    names = im.u32(p + 0x10)
    m.lead = im.u8(p + 0x20)
    q, codes = p + 0x21, []
    clsrefs = []
    for _ in range(slots):
        c = im.u8(q)
        codes.append(c)
        q += 1
        if c & 0x40:                        # an array: then a u16 at an even address
            q = ((q + 1) & ~1) + 2
        if c & 0x1F in (CLASS, EXTERN):
            # its ObjectInfo follows inline, 4-aligned, before the next code
            # ("13 00 00 | ObjectInfo | 90" for (sh As C, Optional key As String))
            q = (q + 3) & ~3
            clsrefs.append(im.u32(q))
            q += 4
    nparams = slots - (1 if m.returns else 0)
    opt = [k for k in range(nparams) if codes[k] & 0x80]
    defaults = read_defaults(im, im.u32(p + 8), opt)
    refs = iter(clsrefs)
    for k in range(nparams):
        n = im.u32(names + 4 * k) if im.valid(names) else 0
        prm = Param(im.cstr(n) if im.valid(n) else "a%d" % (k + 1), codes[k], defaults.get(k))
        if codes[k] & 0x1F == CLASS:
            prm.cls = next(refs, None)
        elif codes[k] & 0x1F == EXTERN:
            prm.type = extern_name(im, next(refs, None))    # (ctl As Control)
        m.params.append(prm)
    if (f00 >> 8) & 0xfc == 0xfc and m.params:
        # byte 1 = 0xFC: ParamArray last (t04 Params).  (Other bits there count something else:
        # Weigh(a, b, Optional c As Variant) has 05h, and came out ParamArray c() As Variant)
        m.params[-1].paramarray = True
    if m.returns:
        m.ret_code = codes[slots - 1]
        if m.ret_code & 0x1F == CLASS:
            m.ret_cls = next(refs, None)
        elif m.ret_code & 0x1F == EXTERN:
            m.ret_extern = extern_name(im, next(refs, None))
    return m


VT_SIZES = {2: "<h", 3: "<i", 4: "<f", 5: "<d", 6: "<q", 7: "<d", 11: "<h", 17: "<B"}


def read_defaults(im, p, optional):
    """Default values of the Optional parameters (indexes in optional):
    {parameter index: (VARTYPE, value)}."""
    out = {}
    if not p or not im.valid(p):
        return out
    size, q = im.u32(p), im.u32(p + 4)
    if not im.valid(q) or size > 0x1000:
        return out
    end = q + size
    for k in optional:
        if q + 2 > end:
            break                           # trailing parameters without a default
        vt = im.u16(q)
        q += 2
        if vt == 0:
            continue                        # VT_EMPTY: no default
        if vt == 8:
            n = im.u16(q)
            v = im.bytes(q + 2, n).decode("latin-1")
            q += 2 + n + (n & 1)            # padded to an even length
        elif vt in VT_SIZES:
            fmt = VT_SIZES[vt]
            n = struct.calcsize(fmt)
            v = struct.unpack(fmt, im.bytes(q, n))[0]
            q += n + (n & 1)
            if vt == 6:
                v = v / 10000.0             # Currency is scaled by 10000
        else:
            break                           # not seen yet: stop rather than misread
        out[k] = (vt, v)
    return out


class ObjectMembers:
    def __init__(self, prj, o):
        im = prj.im
        self.obj = o
        self.vars = []
        self.methods = []           # parallel to o.method_names (None for Private)
        self.events = []
        h = o.private_object
        self.valid = im.valid(h)
        if not self.valid:
            return
        nvars, nevents = im.u16(h + 0x10), im.u16(h + 0x12)
        arr = im.u32(h + 0x20)
        if im.valid(arr):
            for i in range(nvars):
                p = im.u32(arr + 4 * i)
                if not im.valid(p):
                    continue
                name = im.cstr(im.u32(p)) if im.valid(im.u32(p)) else None
                vs, code = im.u32(p + 0x10), im.u32(p + 0x18)
                cls = im.u32(p + 0x1c) if code & 0x1F == CLASS else None
                self.vars.append(Var(name, im.u32(p + 0x0c), vs >> 16, vs & 0xffff,
                                     im.u32(p + 0x14), code, cls))
                if code & 0x1F == EXTERN:
                    self.vars[-1].type = extern_name(im, im.u32(p + 0x1c))
        arr = im.u32(h + 0x18)
        if im.valid(arr):
            for i in range(o.nmethods):
                p = im.u32(arr + 4 * i)
                m = read_method(im, p) if im.valid(p) and im.u32(p + 4) is not None else None
                if m is not None:
                    m.name = o.method_names[i] if i < len(o.method_names) else None
                self.methods.append(m)
        arr = im.u32(h + 0x24)
        if im.valid(arr):
            names = event_names(prj, o, nevents)
            for i in range(nevents):
                p = im.u32(arr + 4 * i)
                if im.valid(p):
                    e = read_method(im, p)
                    e.kind = "event"
                    e.name = names[i] if i < len(names) else "Event%d" % (i + 1)
                    self.events.append(e)

    def by_vtoff(self):
        out = {}
        for v in self.vars:
            for k in range(v.slots):
                out[v.vtoff + 4 * k] = (v, k)
        for m in self.methods:
            if m is not None and m.vtoff is not None:
                out[m.vtoff] = (m, 0)
        return out


def event_names(prj, o, n):
    """Event names are not referenced by any pointer: the compiler writes
    them into the object's name pool after the method names, just before
    the instance cleanup table (tObject+0x08)."""
    im = prj.im
    end = o.public_bytes
    if not n or not im.valid(end):
        return []
    starts = [im.u32(im.u32(o.va + 0x20) + 4 * i) for i in range(o.nmethods)] \
        if im.valid(im.u32(o.va + 0x20)) else []
    starts = [s for s in starts if im.valid(s) and s < end]
    pos = max(starts) if starts else end - 0x100
    pos += len(im.cstr(pos)) + 1 if starts else 0
    out = []
    while pos < end and len(out) < n:
        while pos < end and im.u8(pos) == 0:
            pos += 1
        if pos >= end:
            break
        s = im.cstr(pos)
        if not s or not all(c.isalnum() or c == "_" for c in s):
            break
        out.append(s)
        pos += len(s) + 1
    return out
