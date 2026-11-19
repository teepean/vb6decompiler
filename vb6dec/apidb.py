"""Declare statements for Win32 API functions from WIN32API.TXT (shipped
with VB6 in Common\\Tools\\Winapi), keyed by the exported function name."""
import os
import re

from . import msfiles

PATH = msfiles.find("WIN32API.TXT")

DECL = re.compile(r'^(?:Public |Private )?Declare (Function|Sub) (\w+) Lib "([^"]+)"'
                  r'(?: Alias "([^"]+)")? \((.*?)\)(?: As (\w+))?\s*$', re.I)

_db = None
_types = None
_lower = {}         # lower-case Type name -> the first Type of that name
_consts = {}


class Api:
    def __init__(self, kind, name, lib, alias, params, ret, text):
        self.kind, self.name, self.lib, self.alias = kind, name, lib, alias
        self.params, self.ret, self.text = params, ret, text

    def stack_bytes(self):
        n = 0
        for byval, _, t in self.params:
            if byval and t in ("Double", "Currency", "Date"):
                n += 8
            elif byval and t == "Variant":
                n += 16
            else:
                n += 4
        return n

    def declare(self, scope="Private"):
        t = self.text
        t = re.sub(r"^(?:Public |Private )?", "", t)
        return "%s %s" % (scope, t)


def _parse_params(s):
    out = []
    s = s.strip()
    if not s:
        return out
    for p in s.split(","):
        p = p.strip()
        m = re.match(r"(?:(Optional) )?(?:(ByVal|ByRef) )?(\w+)(?:\(\))?(?: As (\w+))?", p)
        if not m:
            continue
        out.append((m.group(2) == "ByVal", m.group(3), m.group(4) or "Variant"))
    return out


def load():
    global _db, _types
    if _db is not None:
        return _db
    _db, _types = {}, {}
    _lower.clear()
    _consts.clear()
    if not os.path.exists(PATH):
        return _db
    lines = open(PATH, encoding="latin-1").read().replace("\r", "").split("\n")
    i = 0
    while i < len(lines):
        ln = lines[i].strip()
        m = DECL.match(ln)
        if m:
            kind, name, lib, alias, params, ret = m.groups()
            api = Api(kind, name, lib, alias or name, _parse_params(params), ret, ln)
            key = (alias or name).lower()
            prev = _db.get(key)
            # several VB names can alias one export; prefer the export's own name
            if prev is None or (name.lower() == key or name.lower() + "a" == key) and \
                    not (prev.name.lower() == key or prev.name.lower() + "a" == key):
                _db[key] = api
        elif re.match(r"^(?:Public |Private )?Const (\w+)\s*=\s*(-?\w+)\s*$", ln):
            cm = re.match(r"^(?:Public |Private )?Const (\w+)\s*=\s*(-?\w+)\s*$", ln)
            try:
                _consts[cm.group(1)] = int(cm.group(2), 0)
            except ValueError:
                pass
        elif re.match(r"^(?:Public |Private )?Type (\w+)", ln):
            tname = re.match(r"^(?:Public |Private )?Type (\w+)", ln).group(1)
            body = [ln]
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("End Type"):
                body.append(lines[i].rstrip())
                i += 1
            body.append("End Type")
            _types.setdefault(tname, body)
            _lower.setdefault(tname.lower(), tname)
        i += 1
    return _db


def lookup(fn):
    return load().get(fn.lower())


def udt(name):
    load()
    t = _types.get(name)
    if t is None and _types:
        k = _lower.get(name.lower())        # VB names are not case-sensitive
        if k is not None:
            return _types[k]
    return t


# VB6 alignment: a member starts on a multiple of its own size, capped at 4
SIZES = {"Byte": 1, "Integer": 2, "Boolean": 2, "Long": 4, "Single": 4, "Double": 8,
         "Currency": 8, "Date": 8, "String": 4, "Variant": 16, "Any": 4, "Object": 4}
_layouts = {}
_arrays = {}
_dynf = set()       # (UDT name, field): a dynamic array, whatever its element
_mdims = {}        # (UDT name, field) -> (lower bound, element size) of a 1-D array field


def num(t):
    """An integer literal or a constant from WIN32API.TXT."""
    t = t.strip()
    try:
        return int(t, 0)
    except ValueError:
        return _consts.get(t)


_busy = set()


def layout(name):
    """[(offset, field name, type name)] for a UDT, and its total size."""
    if name in _layouts:
        return _layouts[name]
    if name in _busy:
        return None                     # a Type containing itself: not a layout
    _busy.add(name)
    try:
        return _layout(name)
    finally:
        _busy.discard(name)


def _layout(name):
    body = udt(name)
    if body is None:
        return None
    fields, off, maxalign = [], 0, 1
    for ln in body[1:-1]:
        t = ln.strip()
        if not t or t.startswith("'"):
            continue
        m = re.match(r"(\w+)(\(([^)]*)\))?\s+As\s+([\w.]+)(\s*\*\s*(\d+))?", t)
        if not m:
            return None
        fname, arr, bounds, tname, _, fixlen = m.groups()
        if tname in SIZES:
            # String * n: n UTF-16 characters in VB's memory (VB converts the
            # UDT to ANSI only for the API call itself)
            size = 2 * int(fixlen) if fixlen and tname == "String" else SIZES[tname]
            # a String * n is not aligned at all: a target's record descriptor
            # puts `String * 1` at 16Dh right after a Byte (aligned to 2 here,
            # every later field moved: Form1(1).f180 printed as .f17C)
            align = 1 if (fixlen and tname == "String") else min(size, 4)
        else:
            sub = layout(tname)
            if sub is None and udt(tname) is None:
                sub = ([], 4, 4)            # not a Type: a class, an object reference
            if sub is None:
                return None
            size, align = sub[1], sub[2]
        if arr is not None and not bounds.strip():
            size, align = 4, 4              # a dynamic array: a SAFEARRAY pointer
            _dynf.add((name, fname))
        elif arr is not None:
            n = 1
            first = 0
            if bounds:
                parts = [b.strip() for b in bounds.split(",")]
                for j, b in enumerate(parts):
                    if " To " in b:
                        lo, hi = [num(x) for x in b.split(" To ")]
                        if lo is None or hi is None:
                            return None
                        n *= hi - lo + 1
                        if j == 0:
                            first = lo
                    elif b:
                        v = num(b)
                        if v is None:
                            return None
                        n *= v + 1
                if len(parts) == 1:
                    _arrays[(name, fname)] = (first, size)
                else:
                    dims = []
                    for b in parts:
                        if " To " in b:
                            lo, hi = [num(x) for x in b.split(" To ")]
                        else:
                            lo, hi = 0, num(b)
                        dims.append((lo, hi - lo + 1))
                    _mdims[(name, fname)] = (dims, size)
            size *= n
        off = (off + align - 1) // align * align
        fields.append((off, fname, tname))
        off += size
        maxalign = max(maxalign, align)
    total = (off + maxalign - 1) // maxalign * maxalign
    _layouts[name] = (fields, total, maxalign)
    return _layouts[name]


def field_at(name, off, whole=False):
    """Field path for a byte offset inside a UDT, e.g. "rcItem.Left".  With
    whole, a nested struct starting exactly at off is returned as itself
    ("rcItem"): the address of a sub-structure."""
    lay = layout(name)
    if lay is None:
        return None
    fields, total, _ = lay
    for i, (o, fname, tname) in enumerate(fields):
        end = fields[i + 1][0] if i + 1 < len(fields) else total
        if o <= off < end:
            if (name, fname) in _dynf:
                return fname                    # the array's own pointer, no field of an element
            arr = _arrays.get((name, fname))
            if arr is not None and arr[1]:
                lo, esize = arr                 # an element of a fixed array field
                k, rest = divmod(off - o, esize)
                fname = "%s(%d)" % (fname, lo + k)
                o = off - rest
            if tname in SIZES or (whole and off == o):
                return fname
            sub = field_at(tname, off - o, whole)
            return "%s.%s" % (fname, sub) if sub else fname
    return None


def array_from_bound(name, off, stride):
    """A fixed array whose element 0 would be at off, with a lower bound other than 0: subscripts go
    in as they are, x(i) being [off + i * stride] (a record's liu(2 To 5): the code's raw offset is
    8 below the field, inside the array before it).  (field, element size, type), or None."""
    lay = layout(name)
    if lay is None:
        return None
    for o, fname, tname in lay[0]:
        arr = _arrays.get((name, fname))
        if arr is not None and arr[0] and arr[1] == stride and o - arr[0] * stride == off:
            return fname, arr[1], tname
    return None


def multi_array_at(name, off):
    """(field, [(lb, n)...], element size, type, its offset) of the several-dimensional fixed array
    of a UDT holding off, or None."""
    lay = layout(name)
    if lay is None:
        return None
    for i, (o, fname, tname) in enumerate(lay[0]):
        md = _mdims.get((name, fname))
        if md is None:
            continue
        count = 1
        for _, n in md[0]:
            count *= n
        if o - md[1] * count <= off < o + md[1] * count:
            return fname, md[0], md[1], tname, o
    return None


def array_at(name, off):
    """(field path, first index, element size, element type, offset inside
    the element) for a byte offset inside a fixed array field of a UDT
    (nested UDTs followed), or None.  The first index already counts the
    elements before off: TRec2's psprites at E4h, 16 bytes each, gives
    ("fE4", 1, 16, ..., 8) for F4h + 8."""
    lay = layout(name)
    if lay is None:
        return None
    fields, total, _ = lay
    for i, (o, fname, tname) in enumerate(fields):
        end = fields[i + 1][0] if i + 1 < len(fields) else total
        if o <= off < end:
            arr = _arrays.get((name, fname))
            if arr is not None and arr[1]:
                lo, esize = arr
                k, rest = divmod(off - o, esize)
                return fname, lo + k, esize, tname, rest
            if tname in SIZES:
                return None
            sub = array_at(tname, off - o)
            return ("%s.%s" % (fname, sub[0]),) + sub[1:] if sub else None
    return None


def field_type(name, off):
    """VB type of the scalar field starting at off inside a UDT, or None."""
    lay = layout(name)
    if lay is None:
        return None
    fields, total, _ = lay
    for i, (o, fname, tname) in enumerate(fields):
        end = fields[i + 1][0] if i + 1 < len(fields) else total
        if o <= off < end:
            if (name, fname) in _dynf:
                return None                     # a dynamic array field: its pointer isn't a value
            arr = _arrays.get((name, fname))
            if arr is not None and arr[1]:
                off = o + (off - o) % arr[1]
            if tname in SIZES:
                return tname if off == o else None
            return field_type(tname, off - o)
    return None


def bound_consts(body):
    """[(name, value)] of the WIN32API.TXT constants a Type's array bounds
    use, e.g. lfFaceName(1 To LF_FACESIZE): VB needs them declared."""
    load()
    out = []
    for ln in body[1:-1]:
        m = re.match(r"\s*\w+\(([^)]*)\)", ln)
        words = re.findall(r"[A-Za-z_]\w*", m.group(1)) if m else []
        f = re.search(r"\bString\s*\*\s*([A-Za-z_]\w*)", ln)   # String * CCHDEVICENAME
        if f:
            words.append(f.group(1))
        for w in words:
            if w.lower() != "to" and w in _consts and (w, _consts[w]) not in out:
                out.append((w, _consts[w]))
    return out


def all_types():
    load()
    return list(_types)


def add_type(name, body):
    """Register a synthetic Type so field_at/layout work for it."""
    load()
    if _types.get(name) != body:
        for k in [k for k in _arrays if k[0] == name]:
            del _arrays[k]              # (its array fields are parsed again)
        for k in [k for k in _mdims if k[0] == name]:
            del _mdims[k]
    _types[name] = body
    _lower.setdefault(name.lower(), name)
    _layouts.pop(name, None)
    return name


def by_size(size):
    """UDT names of exactly this byte size (for recovering a local's type)."""
    load()
    out = []
    for n in _types:
        lay = layout(n)
        if lay is not None and lay[1] == size:
            out.append(n)
    return out
