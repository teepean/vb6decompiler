"""User-defined types that need deep copies: VB passes a record descriptor
to __vbaRecAssign / __vbaRecDestruct / __vbaRecUniToAnsi for every UDT that
holds Strings, Objects or Variants.

Descriptor (t03 TItem = {Id As Long, Price As Double, Title As String}):
    u16 ?          0x10
    u16 size       the record's byte size (VB packs to 4)
    u16 0
    u16 count      entries that need cleanup
    u32 ?
    count x (u16 offset, u16 kind)   kind 1 = String, 3 = Object
The same table shape lists a class instance's String/Object members
(tObject+0x08), so the kinds agree with what the cleanup does.

The descriptor gives the size and where the Strings and Objects are; the
other fields come from how the code uses them (udt.py).
"""

import re

KINDS = {1: "String", 2: "Variant", 3: "Object", 5: "Array", 11: "number"}  # by the kind's low byte; 0x0105 = a
                                                  # dynamic array (high byte: dimensions?)

# kind 11 is a plain numeric field, whose byte size is the u16 of the 0x2000
# block (tests/t16rec: Integer 2, Double 8, Byte 1); 4 and 8 are ambiguous
# (Long/Single, Double/Currency/Date), so the code's use of the field decides
NUM_KINDS = {1: "Byte", 2: "Integer", 4: "Long", 8: "Double"}


# a fixed array's elements in a record: by the VARTYPE after its SAFEARRAY, else by entry kind
FIX_ELEM = {2: "Integer", 3: "Long", 4: "Single", 5: "Double", 6: "Currency", 7: "Date", 8: "String",
            9: "Object", 11: "Boolean", 12: "Variant", 17: "Byte"}
FIX_KINDS = {1: "String", 2: "Variant", 3: "Object"}


def drop_alignment_pads(lines, gaps=None):
    """Without the pad fields VB's own alignment already makes: Put and Get on a
    Random file write a record field by field, with no alignment gaps, so a
    declared gap is bytes on disk.  a target's three record files give the check:
    TRec15, TRec14, TRec13 are 150, 55, 46 bytes with pads, and exactly the 134, 50,
    42 of their Open ... Len = without ("Bad record length": no password record,
    no family files).  A gap more than alignment explains stays.  Stops at a
    field whose size is not known yet.
    gaps: where the record's Get/Put descriptor says memory has no disk bytes
    (io_gaps), when known; a pad anywhere else is a field nothing reads."""
    from . import apidb
    sizes = dict(apidb.SIZES, Object=4)
    out, pos, maxal, pending = lines[:1], 0, 1, None
    known = True
    for ln in lines[1:-1]:
        m = re.match(r"\s*(\w+)(?:\(([^)]*)\))?\s+As\s+([\w.]+)(?:\s*\*\s*(\d+))?", ln)
        if not known or not m:
            known = False
            if pending:
                out.append(pending[0])
                pending = None
            out.append(ln)
            continue
        name, bounds, t, fix = m.groups()
        n = 1
        if bounds is not None:
            if not bounds.strip():
                t, bounds, n = "Long", None, 1          # a dynamic array: a pointer
            else:
                for b in bounds.split(","):
                    q = re.match(r"\s*(?:(-?\d+) To )?(-?\d+)\s*$", b)
                    if not q:
                        known = False
                        break
                    n *= int(q.group(2)) - int(q.group(1) or 0) + 1
        if fix and t == "String":
            sz, al = 2 * int(fix), 1
        elif t in sizes:
            sz, al = sizes[t], min(sizes[t], 4)
        else:
            lay = apidb.layout(t)
            if lay is None:
                known = False
            else:
                sz, al = lay[1], lay[2]
        if not known:
            if pending:
                out.append(pending[0])
                pending = None
            out.append(ln)
            continue
        if name.startswith("pad") and t == "Byte" and bounds is not None:
            pending = (ln, n)
            continue
        if pending:
            gap = pending[1]
            if -(-pos // al) * al != pos + gap or gaps is not None and pos not in gaps:
                out.append(pending[0])          # more than alignment: real bytes
                al_pos = pos + gap
            else:
                al_pos = pos + gap
            pending, pos = None, al_pos
        pos = -(-pos // al) * al + sz * n
        maxal = max(maxal, al)
        out.append(ln)
    if pending:
        if not known or -(-pos // maxal) * maxal != pos + pending[1]:
            out.append(pending[0])
    out.append(lines[-1])
    return out


def io_size(im, va):
    """The memory size in the descriptor __vbaPutOwner / GetOwner take for a UDT
    with no Strings (no Record: u32 size, u32 count, then its byte arrays), or None."""
    if not im.valid(va) or not im.valid(va + 8):
        return None
    if im.u16(va + 2) != 0:
        return None
    size, count = im.u32(va), im.u32(va + 4)
    if count == 1 and im.u16(va + 14) == 6 and im.u32(va + 20) * 2 == size:
        return None                         # just a String * n, no record
    return size if 4 < size < 0x10000 and 0 < count <= 256 else None


# memory size of a record -> its Get/Put descriptor's gaps (decompile.global_struct_hints)
IO_GAPS = {}


def io_gaps(im, va):
    """The memory offsets the Get/Put descriptor at va skips.  After a u16 0, entries of u32 bytes
    copied as they are, u16 kind, u32 count, u16 n; kind 5 with the size in its high byte is a gap
    of memory with no disk bytes, kind 6 a String * n.  TRec6: 0x56 copied, skip 2, 6 copied, skip
    2, 0x5C copied, String * 10, and the rest: 202 bytes on disk, Open ... Len = 202.  None for
    any other kind.  (t75seltab: a, un, b, String * 3, c, c2, skip 2, d.  No gap at 2, so the
    field nothing reads is there on disk: a As Integer alone made the file 2 bytes short.)"""
    size = io_size(im, va)
    if size is None:
        return None
    pos, mem, out = va + 8, 0, set()
    for _ in range(im.u32(va + 4)):
        if not im.valid(pos + 12):
            return None
        mem += im.u32(pos + 2)
        kind, count = im.u16(pos + 6), im.u32(pos + 8)
        pos += 12
        if mem > size:
            return None
        if kind & 0xff == 5 and kind >> 8:
            # (the last one may go past the size, which is where the last field ends: 3 Bytes, a gap of 1,
            # a Long and 2 Bytes, a gap of 2: size 10, 12 in memory.  t102oddrec)
            out.add(mem)
            mem += (kind >> 8) * count
        elif kind == 6:
            mem += 2 * im.u16(pos) * count
            if mem > size:
                return None
        else:
            return None
    return out


class Record:
    def __init__(self, va, size, fields):
        self.va, self.size = va, size
        self.fields = fields            # {offset: "String" / "Object" / "kind<N>"}
        self.name = None                # given by the writer: TRec1, TRec2, ...
        self.elem = {}                  # {offset: element type} of array fields
        self.subs = {}                  # {offset: descriptor VA} of embedded UDTs
        self.fsize = {}                 # {offset: byte size} of fields wider than 4

    def __repr__(self):
        return "<record %X size %d %s>" % (self.va, self.size, self.fields)


def read(im, va):
    """The record descriptor at va, or None when it does not look like one."""
    if not im.valid(va) or not im.valid(va + 12):
        return None
    size, zero, count = im.u16(va + 2), im.u16(va + 4), im.u16(va + 6)
    if zero != 0 or not 0 < size < 0x10000 or count > 256:
        return None
    fields, subs, fsize, fixarr = {}, {}, {}, {}
    pos = va + 12
    for i in range(count):
        if not im.valid(pos + 4):
            return None
        off, kind = im.u16(pos), im.u16(pos + 2)
        if off >= size:
            return None
        pos += 4
        if kind & 0x2000:
            pos += 4                # u16 ?, u16 offset in the ANSI copy of the record
        base = kind & 0x3f
        if base == 9 or (kind & 0x1f == 9 and kind & 0x60):     # an embedded UDT: its own descriptor
            if im.u32(pos):
                subs[off] = im.u32(pos)
                fields[off] = "Record"
            # (2009h with no descriptor: a record of numbers, known by the code's use of it)
            pos += 4 + 16
        elif base == 5 and kind & 0x80:
            # a fixed array: the element's own entry (u32 kind: 2069h a record, then its
            # descriptor and 16 bytes; 200Bh a number, then 4 bytes...), then the array's
            # SAFEARRAY as the compiler made it, with its bounds (DarwinBots rob: its 62 fields after
            # the first such array were lost, DNA() and the Strings among them)
            ekind = im.u16(pos)         # (its upper word and later dwords can hold rubbish)
            q = pos + 4
            d = 0
            if ekind & 0x1f == 9 and ekind & 0x60:
                d = im.u32(q)           # 2069h; 2009h: a record of numbers, no descriptor
                q += 4 + 16
            sa = safearray_at(im, q, size)
            if sa is None:
                # (not the shape above: what it could tell before, and the entries end here)
                for k in range(0, 16, 2):
                    d = im.u32(pos + k) if im.valid(pos + k + 4) else 0
                    if d and d != va and im.valid(d) and im.u16(d + 4) == 0 and \
                            0 < im.u16(d + 2) < size:
                        fields[off] = "RecordArray"
                        subs[off] = d
                        break
                break
            q, features, esize, dims = sa
            if features & 0x80:
                q += 4                  # the element's VARTYPE after it
                vt = im.u16(q - 4)
            else:
                vt = None
            fixarr[off] = (esize, dims, vt, ekind & 0x1f)
            if d and im.valid(d):
                fields[off] = "RecordArray"
                subs[off] = d
            else:
                fields[off] = "FixedArray"
            pos = q
        elif base == 5:
            # a dynamic array: its element's kind.  A record: that kind's dword, the descriptor, 16
            # bytes.  A number with the ANSI block: the word alone.  Otherwise 6 bytes in all.
            fields[off] = "Array"
            ek = im.u16(pos)
            if ek & 0x1f == 9 and ek & 0x60:
                subs[off] = im.u32(pos + 4)
                pos += 24
            elif kind & 0x2000 and ek & 0x2000:
                pos += 2                # (2009h too: records of numbers, DarwinBots dna())
            else:
                pos += 6
        elif base == 10:            # String * n
            n = im.u16(pos)
            fields[off] = "String * %d" % n
            fsize[off] = 2 * n
            pos += 2
        elif base == 11 and kind & 0x2000:
            # u16 size, u16 offset in the ANSI copy (skipped above)
            n = im.u16(pos - 4)
            fields[off] = NUM_KINDS.get(n, "Long")
            if n in NUM_KINDS:
                fsize[off] = n          # body(): the field's exact width
        else:
            fields[off] = KINDS.get(kind & 0xff, "kind%d" % kind)
            if fields[off] == "Variant":
                fsize[off] = 16
    r = Record(va, size, fields)
    r.subs, r.fsize, r.fixarr = subs, fsize, fixarr
    return r


def table_entries(im, va):
    """The entries of a module's variable table (the record descriptor format: u16 ?, size, 0, count,
    u32, then the entries): [(offset, kind, what follows)], and the address after them.  A module
    lists its Strings, Variants, objects, records and arrays; plain numbers are not there.

    kind & 2000h: 4 more bytes.  5 with 100h: a dynamic array.  5 without: a static SAFEARRAY image
    12 bytes on, or 28 (an array of records, with its descriptor).  9 (69h, 29h): a record, u32
    descriptor and 16 bytes.  (Between the entries' fields lie bytes of the compiler's stack.)"""
    count = im.u16(va + 6)
    pos = va + 12
    out = []
    for _ in range(count):
        if not im.valid(pos + 4):
            break
        off, kind = im.u16(pos), im.u16(pos + 2)
        pos += 4
        if kind & 0x2000:
            pos += 4
        base = kind & 0x1f
        info = None
        if base == 5 and kind & 0x100:
            ek = im.u16(pos)
            if ek & 0x1f == 9 and ek & 0x60:
                info = ("dynamic of records", im.u32(pos + 4))
                pos += 24
            elif kind & 0x2000 and ek & 0x2000:
                info = ("dynamic", ek)
                pos += 2
            else:
                info = ("dynamic", ek)
                pos += 6
        elif base == 5:
            # the image: 16 bytes after the entry's start, or 32 with a record's descriptor
            start = pos - (8 if kind & 0x2000 else 4)
            for at in (start + 16, start + 32):
                sa = safearray_at(im, at, 0x7fffffff)
                if sa is not None:
                    q, features, esize, dims = sa
                    vt = None
                    if features & 0x80:
                        vt = im.u16(q)
                        q += 2
                    info = ("static", esize, dims, hex(features),
                            im.u32(start + 12) if at == start + 32 else None, vt)
                    # (the next entry starts on a dword: cCipher's Static arrays, one after another,
                    # were read two bytes off)
                    pos = va + ((q - va + 3) & ~3)
                    break
            else:
                break
        elif base == 9:
            # (the descriptor a dword further in some: 4C01 2900 00000000 785B4000, VBDoom am_map's
            # cheat sequence, which only the two bytes the parser used to be off had kept it from)
            d = im.u32(pos)
            if not im.valid(d) and im.valid(im.u32(pos + 4)):
                d = im.u32(pos + 4)
            info = ("record", d)
            pos += 20
        elif base == 10:
            info = ("fixed string", im.u16(pos))
            pos += 2
        out.append((off, kind, info))
    return out, pos


def safearray_at(im, q, size):
    """(address after it, fFeatures, element size, [(lower bound, count)...] in declaration order),
    for the SAFEARRAY a descriptor holds at q, else None.  The bounds are stored last dimension
    first."""
    # (some element entries are longer than the kinds seen: look a little further)
    for k in range(0, 32, 2):
        p = q + k
        if not im.valid(p + 32):
            return None
        dims, features, esize, locks, data = im.u16(p), im.u16(p + 2), im.u32(p + 4), \
            im.u32(p + 8), im.u32(p + 12)
        if not 1 <= dims <= 8 or locks or data or not 0 < esize <= size or features & 0xF000:
            continue
        bounds = []
        ok = True
        total = esize
        for i in range(dims):
            n, lb = im.u32(p + 16 + 8 * i), im.u32(p + 20 + 8 * i)
            lb = lb - (1 << 32) if lb >= 1 << 31 else lb
            if not 0 < n <= size:
                ok = False
                break
            bounds.append((lb, n))
            total *= n
        if ok and total <= size:
            return p + 16 + 8 * dims, features, esize, bounds[::-1]
    return None


SCALARS = {1: "Byte", 2: "Integer", 4: "Long", 8: "Double"}
SIZE_NAMES = {"Byte", "Integer", "Boolean", "Long", "Single", "Double", "Currency", "Date", "String"}


def inline_arrays(name, size, fa, fixed, access, etypes=None, widths=None, subtypes=None):
    """The fixed arrays inside a UDT, {start: (stride, count, element
    type)}, from the elements the code reached (fa: raw offset -> (stride,
    access extent, count from a bounds check or 0); fixed: offsets of fields
    that stay, e.g. a record's Strings; access: the other fields' offsets).
    An array of scalars starts at every such offset (players(i).powers(k),
    .ammo(k)); the offsets of an array of UDTs (accesses narrower than its
    stride, or the element only copied whole) within one stride of the lowest
    are fields of one element (psprites(k).sx at +8), which gets a Type of
    its own, named after the UDT - unless etypes ({raw offset: Type}) names
    the Type its elements are passed as (udt.nest_types).  Without a bounds
    check an array reaches the next field known."""
    from . import apidb
    found = []
    for stride in sorted({v[0] for v in fa.values()}):
        offs = sorted(o for o, v in fa.items() if v[0] == stride)
        # an element reached with no check of its own, within two strides below a checked one: the
        # same array, its subscript's constant in the address (.ties(k - 1).ang: raw 0Ch of an array
        # at 1Ch).  Its offset says nothing of where elements begin.
        biased = []
        chk = [o for o in offs if fa[o][2]]
        if stride > 8 and chk:
            # (a field the checked ones have too, a whole number of elements on.  HUlib s.l(i) at
            # raw 0, by reference, and s.l(i).needsupdate checked at 10h: 0 is where elements begin)
            have = {o - chk[0] for o in chk if o < chk[0] + stride}
            biased = [o for o in offs if chk[0] - 2 * stride <= o < chk[0] and
                      (o - chk[0]) % stride in have]
            offs = [o for o in offs if o not in biased]
        i = 0
        while i < len(offs):
            base = offs[i]
            members, n, j = {}, 0, i
            while j < len(offs) and (j == i or fa[base][1] < stride and offs[j] < base + stride):
                members[offs[j] - base] = fa[offs[j]][1]
                n = max(n, fa[offs[j]][2])
                j += 1
            if biased and base == chk[0]:
                for o in biased:
                    rel = (o - base) % stride
                    members[rel] = max(members.get(rel, 0), fa[o][1])
            found.append((base, stride, n, members))
            i = j
    # the same stride, one starting inside the other's bounds-checked extent, or records a whole
    # number apart: one array seen at several raw offsets, as the address takes a subscript's
    # constant along (rob(i).Ties(k + 1).x: f70(0 To 0) and fB4(0 To 9), 11 ties in the source;
    # .Ties(k - 1), raw 2Ch, in the fields before the array).  Most of the elements' fields are
    # reached without such a constant: the start is where most of them are seen (or the lowest).
    found.sort()
    x = 0
    while x + 1 < len(found):
        run = [found[x]]
        ids = [x]
        y = x + 1
        while y < len(found):
            b2, s2, n2, m2 = found[y]
            b1, s1, n1, m1 = run[-1]
            if s2 != s1:
                y += 1                  # (an array of another stride among these: .ties(k).mem(j))
                continue
            # (a scalar window with no check counts as long as the others: sw(k - 1), no check, at
            # raw 8 ties sw(0 To 3) at 2 to sw2(0 To 3) at 0Ah, Booleans side by side)
            if s1 <= 8 and not n1:
                n1 = max(r[2] for r in run)
            if s1 == s2 and (n1 and b2 < b1 + s1 * n1 or
                             not n1 and s1 > 8 and (b2 - b1) % s1 == 0):
                run.append(found[y])
                ids.append(y)
                y += 1
            else:
                break
        if len(run) == 1:
            x += 1
            continue
        st = run[0][1]
        best = max(len(r[3]) for r in run)
        base = next(r for r in run if 2 * len(r[3]) > best or len(r[3]) == best)
        if st <= 8:
            # (not below the record: bits.bit(i - 1) is raw -2, DarwinBots BitShiftLeft, and the
            # Type lost its array)
            base = next((r for r in run if r[0] >= 0), run[0])
        mm = dict(base[3])
        count = base[2]
        for r in run:
            if r is base:
                continue
            k, rel = divmod(r[0] - base[0], st)
            for o, w in r[3].items():
                o2 = (o + rel) % st
                mm[o2] = max(mm.get(o2, 0), w)
            # (a check's count is the array's whichever window it was seen at: .bit(i + 1), 32 bits)
            if st > 8 and not count:
                count = max(count, k + r[2]) if r[2] else count
        if st <= 8:
            # (reaching as far as any window does, or sw2's elements are out of its range; the
            # record's size cuts it back where the windows are one array: .bit(i + 1))
            r0 = max((r[2] for r in run), default=0)
            top = max(r[0] + st * (r[2] or r0) for r in run)
            count = max(count, (top - base[0]) // st)
            if size:
                count = min(count, (size - base[0]) // st)
        for i in reversed(ids[1:]):
            del found[i]
        found[x] = (base[0], st, count, mm)
        x += 1
    # arrays that overlap, one stride a multiple of the other, are one array of
    # the smaller stride (VBDoom wbs.plyr(i).frags(j): 40-byte elements with
    # an array of Longs inside, indexed i * 40 + j * 4; render scales the index)
    # Unless the elements have fields of other widths: then those would be read as the small ones
    # (.ties(k).k As Single, in an array of Integers with .ties(k).mem(j)): the array goes inside
    # the element's Type.
    inner = {}                          # array's start -> {offset in the element: (stride, count)}
    merged = True
    while merged:
        merged = False
        found.sort()
        for x in range(len(found)):
            for y in range(x + 1, len(found)):
                b1, s1, n1, m1 = found[x]
                b2, s2, n2, m2 = found[y]
                e1 = b1 + s1 * max(n1, 1)
                e2 = b2 + s2 * max(n2, 1)
                if b2 >= e1 or s1 == s2 or max(s1, s2) % min(s1, s2):
                    continue
                if s1 > s2 and s1 > 8 and any(w and w != s2 for w in m1.values()):
                    inner.setdefault(b1, {})[(b2 - b1) % s1] = (s2, n2)
                    del found[y]
                    merged = True
                    break
                st = min(s1, s2)
                lo, hi = min(b1, b2), max(e1, e2)
                found[x] = (lo, st, (hi - lo) // st, {0: st})
                del found[y]
                merged = True
                break
            if merged:
                break
    bases = {b for b, _, _, _ in found}
    out = {}
    for base, stride, n, members in found:
        # fields after it: what the descriptor lists, other arrays, and the
        # accesses outside the bounds-checked extent
        end = base + n * stride if n else base + stride
        after = [o for o in set(fixed) | bases if o > base] + \
                [o for o in access if o >= end and o not in fixed] + [size]
        if stride <= 8:
            # (read wider than an element: a field, whatever check seemed to say the array goes on.
            # rob.TieLenOverwrite(k - 1), with Ties(k)'s check of k: 11 Integers over Poisoncount)
            after += [o for o, w in (access if widths is None else widths).items()
                      if o > base and w > stride]
        cap = (min(after) - base) // stride
        n = min(n, cap) if n else cap
        if n < 1:
            continue
        for o, w in access.items():
            # (fields reached with a constant subscript: at their own offsets)
            if base <= o < base + n * stride and w:
                rel = (o - base) % stride
                members[rel] = max(members.get(rel, 0), min(w, stride - rel))
        known = [etypes[base + o] for o in members if base + o in (etypes or {})]
        lay = apidb.layout(known[0]) if known else None
        if known and known[0].startswith("String * "):
            et = known[0]                       # (fixed strings: udt.fixed_string_elements)
        elif lay is not None and lay[1] == stride:
            et = known[0]                       # the Type its elements are passed as
        elif max(o + w for o, w in members.items()) <= stride and set(members) == {0} and \
                members[0] >= stride and base not in inner:
            et = SCALARS.get(stride, "Long")
        else:
            et = "%s_%X" % (name, base)         # an array of UDTs: its element Type
            lines, pos, pad = ["Type %s" % et], 0, 0
            # (an array inside: the window starts a step below the array when its lower bound
            # isn't 0, which shows when that is inside the field before: mem(1 To 3) at 10h, raw 0Eh)
            arrs = {}
            for rel, (s2, n2) in inner.get(base, {}).items():
                lb = 0
                while any(o < rel < o + max(w, (subtypes or {}).get(base + o, (0, 0))[1])
                          for o, w in members.items()):
                    rel += s2
                    lb += 1
                limit = min([o for o, w in members.items() if o >= rel and w > s2] + [stride])
                cnt = min(n2 or stride, (limit - rel) // s2)
                if cnt >= 1:
                    arrs[rel] = (s2, lb, cnt)
            order = sorted(set(members) | set(arrs))
            for i, o in enumerate(order):
                # (0: the element copied whole, or a field only passed by reference; it still
                # ends the field before it)
                if o < pos:
                    continue
                if o in arrs:
                    if o > pos:
                        pad += 1
                        lines.append("    pad%d(1 To %d) As Byte" % (pad, o - pos))
                    s2, lb, cnt = arrs[o]
                    lines.append("    f%X(%d To %d) As %s" % (o, lb, lb + cnt - 1, SCALARS.get(s2, "Long")))
                    pos = o + cnt * s2
                    continue
                sub = (subtypes or {}).get(base + o)
                if sub is not None and o + sub[1] <= stride:
                    # (a record there, known by what it is passed as or copied from)
                    if o > pos:
                        pad += 1
                        lines.append("    pad%d(1 To %d) As Byte" % (pad, o - pos))
                    lines.append("    f%X As %s" % (o, sub[0]))
                    pos = o + sub[1]
                    continue
                w = members[o]
                if not w:
                    if not o or not any(members[q] for q in order[:i]):
                        continue
                    w = 4 if o % 4 == 0 else 2 if o % 2 == 0 else 1
                if o > pos:
                    pad += 1
                    lines.append("    pad%d(1 To %d) As Byte" % (pad, o - pos))
                w = min(w, stride - o, (order[i + 1] - o) if i + 1 < len(order) else stride)
                w = w if w in SCALARS else 2 if w == 3 else 4
                lines.append("    f%X As %s" % (o, SCALARS.get(w, "Long")))
                pos = o + w
            if stride > pos:
                lines.append("    pad%d(1 To %d) As Byte" % (pad + 1, stride - pos))
            lines.append("End Type")
            apidb.add_type(et, lines)
        out[base] = (stride, n, et)
    return out


def body(rec, access=None):
    """Type definition for a record: Strings/Objects where the descriptor says,
    other fields from the accesses seen ({offset: byte size}), fixed arrays
    the code indexes (inline_arrays), padding between."""
    from . import apidb
    access = dict(access or {})
    # (what the code does with them: the 4s just below are only the fields being there)
    widths = dict(access)
    for off, kind in rec.fields.items():
        access[off] = 4
    farrays = {}
    for off, fa in getattr(rec, "farrays", {}).items():
        # (a subscript's - 1 merged into the offset, p(k - 1) at 10h: raw 0Ch, k.  A String
        # the descriptor lists there says the array is a step on: render.inline_element)
        while str(rec.fields.get(off)).startswith(("String", "Object")) and fa[0] and \
                off + fa[0] < rec.size:     # (not a fixed array of records: gh_doom f8(0 To 7))
            off += fa[0]
        old = farrays.get(off)
        farrays[off] = fa if old is None or old[0] != fa[0] else (
            fa[0], max(old[1], fa[1]), max(old[2], fa[2]))
    # (not the fields only a parameter's type made: Lisaa Oliot(2).mem1(4) cut mem1(7) at 3)
    arrays = inline_arrays(rec.name, rec.size, farrays,
                           set(rec.fields) - set(getattr(rec, "ufield_added", ())),
                           access, getattr(rec, "etypes", None), widths,
                           {o: (rec.fields[o], apidb.layout(rec.fields[o])[1])
                            for o in getattr(rec, "ufield_added", ())
                            if apidb.layout(str(rec.fields.get(o)))})
    for off in arrays:
        access.setdefault(off, 4)
    lines = ["Type %s" % rec.name]
    pos, pad = 0, 0
    fixarr = getattr(rec, "fixarr", None) or {}
    for off in sorted(access):
        if off < pos or off >= rec.size:
            continue
        if off > pos:
            pad += 1
            lines.append("    pad%d(1 To %d) As Byte" % (pad, off - pos))
        if off in fixarr:
            esize, dims, vt, ekind = fixarr[off]
            et = None
            if rec.fields.get(off) == "RecordArray":
                et = getattr(rec, "arr_of", {}).get(off, (None,))[0]
            elif ekind == 10:
                et = "String * %d" % (esize // 2)
            elif ekind == 9:
                # records of numbers: what the code shows of them (inline_arrays' nested Type)
                et = arrays[off][2] if off in arrays else None
            else:
                et = FIX_ELEM.get(vt) or FIX_KINDS.get(ekind) or NUM_KINDS.get(esize)
            count = 1
            for _, n in dims:
                count *= n
            bounds = ", ".join("%d To %d" % (lb, lb + n - 1) for lb, n in dims)
            if not et:
                # (nothing known of the elements: the bytes, in their place)
                lb0 = dims[0][0] if len(dims) == 1 else 1
                et, bounds = "Byte", "1 To %d" % (count * esize)
            lines.append("    f%X(%s) As %s" % (off, bounds, et))
            pos = off + count * esize
            continue
        if off in arrays and off in getattr(rec, "ufield_added", ()) and \
                rec.fsize.get(off) == arrays[off][0] and rec.fields.get(off) not in SIZE_NAMES:
            # (an element given to a parameter of a Type: an array of that Type, t84recret jalki)
            stride, n, et = arrays[off]
            lines.append("    f%X(0 To %d) As %s" % (off, n - 1, rec.fields[off]))
            pos = off + n * stride
            continue
        if off in arrays and (off not in rec.fields or off in getattr(rec, "ufield_added", ())):
            stride, n, et = arrays[off]
            lines.append("    f%X(0 To %d) As %s" % (off, n - 1, et))
            pos = off + n * stride
            continue
        if rec.fields.get(off) == "RecordArray" and off in getattr(rec, "arr_of", {}):
            # a fixed array of records: up to the next field the descriptor or
            # the code knows (or the end)
            et, esize = rec.arr_of[off]
            nxt = min([o for o in set(rec.fields) | set(access) if o > off] + [rec.size])
            n = max((nxt - off) // esize, 1)
            lines.append("    f%X(0 To %d) As %s" % (off, n - 1, et))
            pos = off + n * esize
            continue
        size = access[off]
        if off not in rec.fields:
            # a field the code shows does not run into one the descriptor
            # lists (a Byte read as a dword for Print swallowed the String * 1
            # and String * 2 after it: tests/t19fixrec); other accesses may be
            # a wider value's halves (t03c's Double stored as two dwords)
            nxt = min([o for o in rec.fields if o > off] + [rec.size])
            if nxt - off < size:
                size = {3: 2}.get(nxt - off, nxt - off)
            if size not in (1, 2, 4, 8):
                # the record's own copy reads as an access of its whole size at 0 (r(i) = r(j),
                # t78): no field that wide, and it swallowed the ones up to the next String
                size = 4
        t = rec.fields.get(off) or {1: "Byte", 2: "Integer", 8: (
            "Currency" if off in getattr(rec, "cy", ()) else "Double")}.get(size, "Long")
        if t.startswith("kind"):
            t = "Long"
        if t == "Array":
            lines.append("    f%X() As %s" % (off, rec.elem.get(off, "Variant")))
            pos = off + 4
            continue
        lines.append("    f%X As %s" % (off, t))
        if off in rec.fsize:
            pos = off + rec.fsize[off]
        else:
            pos = off + (4 if t in ("String", "Object") else max(size, 1))
    if rec.size > pos:
        lines.append("    pad%d(1 To %d) As Byte" % (pad + 1, rec.size - pos))
    lines.append("End Type")
    return drop_alignment_pads(lines, IO_GAPS.get(rec.size))


def model_record(m, va):
    """The program-wide Record for a record descriptor address (named TRec2,
    TRec3, ... in the order found; embedded UDTs and arrays of records
    first, as the outer Type uses them)."""
    if va is None:
        return None
    recs = m.__dict__.setdefault("records", {})
    if va not in recs:
        recs[va] = None                 # (a descriptor that refers to itself)
        r = read(m.im, va)
        for off, sva in (r.subs.items() if r is not None else ()):
            sub = model_record(m, sva)
            if sub is None:
                continue
            if r.fields.get(off) == "RecordArray":
                r.arr_of = getattr(r, "arr_of", {})
                r.arr_of[off] = (sub.name, sub.size)    # an array of them
                continue
            if r.fields.get(off) == "Array":
                r.elem = getattr(r, "elem", None) or {}
                r.elem[off] = sub.name                  # a dynamic array of them
                continue
            r.fields[off] = sub.name
            r.fsize[off] = sub.size
        if r is not None:
            r.name = "TRec%d" % (2 + sum(1 for x in recs.values() if x is not None))
            register(r)
        recs[va] = r
    return recs[va]


def register(rec, access=None):
    """(Re)declare the record's Type so field names resolve against it."""
    from . import apidb
    import os
    dbg = os.environ.get("VB6DEC_RECDBG", "")   # NAME:OFF (hex), as in udt.record_bodies
    if ":" in dbg and dbg.split(":")[0] == rec.name:
        import sys
        import traceback
        off = int(dbg.split(":")[1], 16)
        b = body(rec, access)
        print("RECREG %s+%X field %s fsize %s access %s -> %s  from %s" % (
            rec.name, off, rec.fields.get(off), rec.fsize.get(off), (access or {}).get(off),
            [ln.strip() for ln in b[1:4]], " < ".join(
                "%s:%d" % (os.path.basename(f.filename), f.lineno)
                for f in reversed(traceback.extract_stack()[-4:-1]))), file=sys.stderr)
    apidb.add_type(rec.name, body(rec, access))
