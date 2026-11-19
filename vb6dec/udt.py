"""Recover user-defined types for struct variables.

Evidence per variable: the offsets its fields are read or written at, and
offsets where a struct of known type starts (a Win32 API declares the
parameter, e.g. FillRect's lpRect As RECT at di + 0x1C).  A variable whose
constraints match exactly one type in WIN32API.TXT gets that name;
otherwise a synthetic Type is written with the observed fields.
"""
from . import apidb
from .ir import Var, Deref, BinOp, Const, AddrOf, Assign, ExprStmt
from .lift import CallStmt
from . import ir


def base_off(e):
    """(variable, offset) for a struct field expression (an element of an
    array of UDTs counts for the array)."""
    if isinstance(e, Deref) and isinstance(e.e, ir.Index) and isinstance(e.e.arr, Var):
        return (e.e.arr, e.off, e.size)
    if isinstance(e, Deref):
        b, off = e.e, e.off
        if isinstance(b, BinOp) and b.op == "+" and isinstance(b.b, Const):
            return (b.a, off + b.b.value, e.size) if isinstance(b.a, Var) else None
        if isinstance(b, Var):
            return (b, off, e.size)
    return None


ELEM_TYPES = {2: "Integer", 3: "Long", 4: "Single", 5: "Double", 6: "Currency", 7: "Date",
              8: "String", 11: "Boolean", 12: "Variant", 17: "Byte"}


def array_field(x):
    """(variable, offset, element type or None) when x shows a UDT field to be
    a dynamic array: an element of an array whose SAFEARRAY* is that field
    (v.f0(i)), or a ReDim of it (the element type from its VARTYPE)."""
    if isinstance(x, ir.Call) and getattr(x, "redim_info", None) and x.args and \
            isinstance(x.args[0], ir.Index) and isinstance(x.args[0].arr, Deref):
        bo = base_off(x.args[0].arr)
        vt = x.redim_info[1]
        t = ELEM_TYPES.get(vt.value & 0xfff) if isinstance(vt, Const) and \
            isinstance(vt.value, int) and vt.value < 0x10000 else None
        return (bo[0], bo[1], t) if bo is not None else None
    if isinstance(x, Deref) and isinstance(x.e, ir.Index) and isinstance(x.e.arr, Deref) \
            and x.off == 0 and not getattr(x.e.arr, "inline", None):
        bo = base_off(x.e.arr)
        t = {1: "Byte", 2: "Integer", 4: "Long", 8: "Double"}.get(x.size)
        return (bo[0], bo[1], t) if bo is not None else None
    return None


def addr_off(e):
    """(variable, offset) for the address of a field: var + N, &var."""
    if isinstance(e, BinOp) and e.op == "+" and isinstance(e.b, Const) and isinstance(e.a, Var):
        return e.a, e.b.value
    if isinstance(e, AddrOf) and isinstance(e.e, Var):
        return e.e, 0
    if isinstance(e, Var):
        return e, 0
    return None


def check_counts(lf, exprs, va):
    """{id(array field): the element count a bounds check right before this statement allows} for the
    fixed arrays inside records in a statement, when folds after the lift made them (the lift's
    own map of checks then holds the procedure's last one of each subscript: lift.BoundsAt).  Not
    when one subscript goes into two arrays there: which check was whose?  (.mem(t + 455) =
    .occurr(t), DarwinBots ReadTRefVars, gave occurr the 1001 of mem.)"""
    log = getattr(lf, "bounds_log", None)
    if not log or va is None:
        return {}
    from .lift import BoundsAt
    uses = {}
    for e in exprs:
        for x in e.walk():
            if isinstance(x, ir.Index) and getattr(x.arr, "inline", None) and len(x.idx) == 1:
                lin = lf.linear(x.idx[0])
                atoms = [k for k, (a, c) in lin.items() if k is not None and c]
                if len(atoms) == 1 and lin[atoms[0]][1] == 1:
                    uses.setdefault(base_key(atoms[0]), []).append(x.arr)
    if not uses:
        return {}
    view = BoundsAt(log, va)
    out = {}
    for k, arrs in uses.items():
        if len({(a.inline, getattr(a, "off", None)) for a in arrs}) != 1:
            continue
        n = view.get(k)
        if n:
            for a in arrs:
                out[id(a)] = n
    return out


def base_key(k):
    """A variable's key as it was lifted: the register webs and block temporaries number theirs
    (('local', (-236, 1008)) for ('local', -236))."""
    if isinstance(k, tuple) and len(k) == 2 and isinstance(k[1], tuple) and k[0] in ("local", "reg"):
        return k[0], k[1][0]
    return k


class Evidence:
    def __init__(self):
        self.access = {}        # var key -> {offset: size}
        self.redim_elems = {}   # var key -> {offset of an array field: its ReDim's element type}
        self.dyn_elem = {}      # var key -> {offset of a dynamic array field: {field offset: width}}
        self.redim_size = {}    # var key -> {offset of an array field: its ReDim's element size}
        self.sub = {}           # var key -> {offset: UDT name}
        self.size = {}          # var key -> byte size, when known
        self.names = {}         # var key -> the Var itself
        self.passed = {}        # (callee VA, arg index) -> {var keys passed there}
        self.passed_field = set()   # (callee VA, arg index) given &v + n, n > 0 (a field)
        self.whole = set()          # var keys Put / Get as a record (__vbaPutOwner4...)
        self.lset_size = {}         # var key -> size copied whole by an LSet
        self.strings = {}           # var key -> {offsets of fields given a String}
        self.passed_elem = {}       # (callee VA, arg index) -> stride of an array element
                                    # inside a UDT passed there (its exact size)
        self.fixed = {}         # var key -> {offset: n} for String * n fields
        self.fps = {}           # var key -> offsets the FPU reads as dwords: Singles
        self.cy = {}            # var key -> {offsets of 8-byte fields used as Currency}
        self.arrays = {}        # var key -> {offset: element type} for dynamic array fields
        self.farrays = {}       # var key -> {raw offset: (stride, access size, count or 0)}
                                # for fixed arrays inside the UDT (lift.inline_field)


def collect(lf, body):
    ev = Evidence()
    forced = {}
    for key, t in lf.udt.items():
        if key in lf.arrays:
            continue                # an array of UDTs: lf.udt names the element type
        lay = apidb.layout(t)
        if lay:
            ev.sub.setdefault(key, {})[0] = t
    for _, ss, br in body:
        items = list(ss) + ([ExprStmt(br.cond)] if br is not None else [])
        last_va = _ if isinstance(_, int) else None     # (the block's address)
        for s in items:
            exprs = []
            if isinstance(s, Assign):
                exprs = [s.dst, s.src]
            elif isinstance(s, (ExprStmt, CallStmt)):
                exprs = [s.e]
            elif isinstance(s, ir.Return) and getattr(s, "value", None) is not None:
                exprs = [s.value]
            if isinstance(s, Assign) and (isinstance(s.src, ir.Str) or
                                          getattr(s.src, "vbtype", None) == "String" or
                                          isinstance(s.src, BinOp) and s.src.op == "&"):
                bo = base_off(s.dst)        # a String stored in a field (t.txt = "")
                if bo is not None and isinstance(bo[0], Var):
                    ev.strings.setdefault(bo[0].key(), set()).add(bo[1])
            last_va = getattr(s, "va", None) or last_va
            near = check_counts(lf, exprs, last_va)
            for e in exprs:
                addressed = set()
                for x in e.walk():
                    if isinstance(x, AddrOf):
                        addressed.add(id(x.e))
                    if getattr(x, "fp4", False) and isinstance(x, Deref):
                        bo = base_off(x)
                        if bo is not None and isinstance(bo[0], Var):
                            ev.fps.setdefault(bo[0].key(), set()).add(bo[1])
                    if getattr(x, "from_inline", None) and isinstance(x, Deref):
                        # (an element with a constant subscript, already a field: the array stays
                        # known, t86recarr r.fB0(2).f4)
                        bo = base_off(x)
                        if bo is not None:
                            raw, st = x.from_inline
                            fa = ev.farrays.setdefault(bo[0].key(), {})
                            k0, rel = divmod(bo[1] - raw, st)
                            low = raw if k0 >= 0 else raw + k0 * st
                            w = 0 if id(x) in addressed or x.size >= st > 8 else x.size
                            old = fa.get(low, (st, 0, 0))
                            if old[0] == st:
                                fa[low] = (st, max(old[1], 0 if rel else w), old[2])
                            if rel:
                                old = fa.get(low + rel, (st, 0, 0))
                                if old[0] == st:
                                    fa[low + rel] = (st, max(old[1], w), old[2])
                            ev.names[bo[0].key()] = bo[0]
                    fd = None
                    if isinstance(x, Deref) and isinstance(x.e, ir.Index) and \
                            getattr(x.e.arr, "inline", None):
                        # an element of a fixed array field; only its address: no width (DeleteTie
                        # t, .Ties(k).pnt, a lea, made pnt a Long and Port and pnt one field)
                        fd, ext = x.e.arr, 0 if id(x) in addressed else x.size + x.off
                    elif isinstance(x, ir.Index) and getattr(x.arr, "inline", None):
                        fd, ext = x.arr, 0          # the element as a whole (LSet, ByRef)
                    if fd is not None:
                        bo = base_off(fd)
                        if bo is not None:
                            v, off, _ = bo
                            fa = ev.farrays.setdefault(v.key(), {})
                            old = fa.get(off, (0, 0, 0))
                            count = max(old[2], getattr(fd, "inline_n", 0) or 0,
                                        near.get(id(fd), 0))
                            if isinstance(x, Deref) and x.e.arr is fd and x.off:
                                # a field further into the element (With a(i): .x): at its own
                                # raw offset, the element's first field unseen
                                fa[off] = (fd.inline, old[1], count)
                                o2 = off + x.off
                                old2 = fa.get(o2, (0, 0, 0))
                                fa[o2] = (fd.inline, max(old2[1], ext and x.size),
                                          max(old2[2], count))
                            else:
                                fa[off] = (fd.inline, max(old[1], ext), count)
                            ev.names[v.key()] = v
                    if getattr(x, "inline", None):
                        continue                # (not a field of its own size)
                    if isinstance(x, Deref) and isinstance(x.e, ir.Index) and \
                            isinstance(x.e.arr, Deref) and not getattr(x.e.arr, "inline", None):
                        # a field of an element of a dynamic array in a record
                        de = base_off(x.e.arr)
                        if de is not None:
                            f = ev.dyn_elem.setdefault(de[0].key(), {}).setdefault(de[1], {})
                            if id(x) in addressed and x.size == 4:
                                f.setdefault(x.off, 0)      # (its address: no width)
                            else:
                                f[x.off] = max(f.get(x.off, 0), x.size)
                    if isinstance(x, ir.Call) and getattr(x, "put_size", None) in (1, 2, 4, 8) and \
                            x.args:
                        # (Get #f, , .dna(i).value: the width is the Get's)
                        y = x.args[-1].e if isinstance(x.args[-1], AddrOf) else x.args[-1]
                        ya, yo = (y.e, y.off) if isinstance(y, Deref) else (y, 0)
                        if isinstance(ya, ir.Index) and isinstance(ya.arr, Deref) and \
                                not getattr(ya.arr, "inline", None):
                            de = base_off(ya.arr)
                            if de is not None:
                                ev.dyn_elem.setdefault(de[0].key(), {}).setdefault(de[1], {})[yo] = \
                                    x.put_size
                    if isinstance(x, ir.Call) and getattr(x, "redim_info", None) and x.args and \
                            isinstance(x.args[0], ir.Index) and isinstance(x.args[0].arr, Deref) and \
                            isinstance(x.redim_info[0], Const):
                        de = base_off(x.args[0].arr)
                        if de is not None:
                            ev.redim_size.setdefault(de[0].key(), {})[de[1]] = x.redim_info[0].value
                    af = array_field(x)
                    if af is not None:
                        v, off, t = af
                        arrs = ev.arrays.setdefault(v.key(), {})
                        if t is not None or off not in arrs:
                            arrs[off] = t
                        ev.names[v.key()] = v
                        if t is not None and getattr(x, "redim_info", None):
                            ev.redim_elems.setdefault(v.key(), {})[off] = t
                    bo = base_off(x)
                    if bo is not None and isinstance(x, Deref) and isinstance(x.e, Var) and \
                            (lf.arrays.get(x.e.key()) or {}).get("dyn") and x.off <= 0x14:
                        # (a dynamic array's own descriptor, not a record's field: lLbound in
                        # UBound(rob(n).mem) made NewMove As Long, and in the save file that Get
                        # read 4 bytes for 2.  DarwinBots LoadRobotBody)
                        bo = None
                    if bo is not None:
                        v, off, size = bo
                        n = getattr(x, "fixstr", None)
                        if n is None and isinstance(s, Assign) and x is s.dst:
                            n = getattr(s, "fixstr", None)
                        if n is None and v.space == "local" and isinstance(v.off, int):
                            n = lf.m.fixed_slots.get((lf.p.va, v.off + off))
                        if n:
                            # String * n: n UTF-16 characters inside the UDT
                            ev.fixed.setdefault(v.key(), {})[off] = n
                            size = 2 * n
                        elif id(x) in addressed and size == 4:
                            # its address only (a lea, for a ByRef argument): the 4 are no width.
                            # (a2.value given by reference, at 2: a 6-byte block.  What the callee
                            # takes it as comes in through forced)
                            size = 2 if off % 2 == 0 else 1
                        acc = ev.access.setdefault(v.key(), {})
                        acc[off] = max(acc.get(off, 0), size)
                        if size in (1, 2) and isinstance(s, Assign) and x is s.dst and \
                                not getattr(x, "from_inline", None):
                            # stored as a word: no Long, whatever reads it.  Fast code loads an
                            # Integer in .data with a dword move and looks at ax (mov eax, [856280h];
                            # cmp ax, 4Ch: SimOpts.SpeciesNum As Long, and Put wrote 4 bytes for 2)
                            forced.setdefault(v.key(), {})[off] = size
                        if size == 8 and "Currency" in (
                                getattr(x, "vbtype", None),
                                getattr(s.src, "vbtype", None) if isinstance(s, Assign) and
                                x is s.dst else None):
                            ev.cy.setdefault(v.key(), set()).add(off)
                        ev.names[v.key()] = v
                    if isinstance(x, ir.Call) and getattr(x, "owner_io", False) and x.args:
                        ao = addr_off(x.args[-1])   # Put / Get #f, , rec of a record
                        if ao is not None and ao[1] == 0:
                            ev.whole.add(ao[0].key())
                    if isinstance(x, ir.Call) and getattr(x, "put_size", None) in (1, 2, 4, 8) and \
                            x.args:
                        # Put / Get #f, , r.f takes its width (r.mt As Single only ever went through
                        # Put and Get, by its address: an Integer by the rule above, and the record
                        # file came out two bytes short; DarwinBots LoadRobotBody)
                        y = x.args[-1].e if isinstance(x.args[-1], AddrOf) else x.args[-1]
                        bo = base_off(y) if isinstance(y, Deref) else None
                        if bo is not None and isinstance(bo[0], Var):
                            forced.setdefault(bo[0].key(), {})[bo[1]] = x.put_size
                    if isinstance(x, ir.Call) and (getattr(x, "put_size", None) or 0) > 8 and \
                            x.args:
                        ao = addr_off(x.args[-1])   # Put / Get #f, , v: Len(v) bytes
                        if ao is not None and ao[1] == 0:
                            ev.size[ao[0].key()] = x.put_size
                            ev.names[ao[0].key()] = ao[0]
                    if isinstance(x, ir.Call) and isinstance(x.raw, str) and \
                            x.raw.split("!")[-1] in ("RtlMoveMemory", "CopyMemory",
                                                     "__vbaCopyBytes") and \
                            len(x.args) == 3 and isinstance(x.args[2], Const):
                        for a in x.args[:2]:        # Len(udt) gives the exact size
                            ao = addr_off(a)
                            if ao is not None and ao[1] == 0:
                                ev.size[ao[0].key()] = x.args[2].value
                                ev.names[ao[0].key()] = ao[0]
                                if x.name == "LSet":    # a whole UDT (CopyMemory may copy part)
                                    ev.lset_size[ao[0].key()] = x.args[2].value
                    if isinstance(x, ir.Call) and getattr(x, "view", None) and x.args:
                        # UBound of a record's fixed array: its bounds and element size
                        y = x.args[0].e if isinstance(x.args[0], AddrOf) else x.args[0]
                        bo = base_off(y) if isinstance(y, Deref) else None
                        esz, dims = x.view
                        if bo is not None and len(dims) == 1 and dims[0][0] == 0 and esz:
                            fa = ev.farrays.setdefault(bo[0].key(), {})
                            old = fa.get(bo[1], (esz, 0, 0))
                            if old[0] == esz:
                                fa[bo[1]] = (esz, old[1], max(old[2], dims[0][1]))
                    if isinstance(x, ir.Call) and getattr(x, "input_codes", None):
                        # Input #f, x: the item's VARTYPE says how wide
                        for c, a in zip(x.input_codes, x.args[1:]):
                            w = {2: 2, 3: 4, 4: 4, 5: 8, 6: 8, 7: 8, 8: 4, 11: 2, 17: 1}.get(c)
                            aa = a.e if isinstance(a, AddrOf) else a
                            if w and isinstance(aa, ir.Index) and getattr(aa.arr, "inline", None):
                                bo = base_off(aa.arr)
                                if bo is not None:
                                    fa = ev.farrays.setdefault(bo[0].key(), {})
                                    old = fa.get(bo[1], (aa.arr.inline, 0, 0))
                                    fa[bo[1]] = (old[0], max(old[1], w), old[2])
                            elif w and isinstance(aa, Deref):
                                bo = base_off(aa)
                                if bo is not None:
                                    forced.setdefault(bo[0].key(), {})[bo[1]] = w
                                    if c == 8 and isinstance(bo[0], Var):
                                        ev.strings.setdefault(bo[0].key(), set()).add(bo[1])
                    if isinstance(x, ir.Call) and getattr(x, "print_codes", None):
                        # Print #f, r.s / Write #f, r.s of a String
                        for c, a in zip([c & 0x3f for c in x.print_codes if c & 0x3f], x.args):
                            bo = base_off(a) if c == 8 and isinstance(a, Deref) else None
                            if bo is not None and isinstance(bo[0], Var):
                                ev.strings.setdefault(bo[0].key(), set()).add(bo[1])
                    if isinstance(x, ir.Call) and isinstance(x.raw, int):
                        # a field passed to a ByRef Integer/Byte parameter is that
                        # wide (the pointer pass itself reads as a dword)
                        from .types import param_args
                        plist = lf.m.plists.get(x.raw)
                        for a, pi in param_args(plist, x.args):
                            # (a Long / Single one too: a field only ever passed
                            # by reference had no width and came out as padding,
                            # a target m68.pad4(3) given to a1 As Long)
                            w = {"Integer": 2, "Boolean": 2, "Byte": 1, "Long": 4,
                                 "Single": 4}.get(plist[pi][2])
                            aa = a.e if isinstance(a, ir.AddrOf) else a
                            if w and plist[pi][3] and isinstance(aa, ir.Deref):
                                bo = base_off(aa)
                                if bo is not None:
                                    forced.setdefault(bo[0].key(), {})[bo[1]] = w
                            if w and plist[pi][3] and isinstance(aa, ir.Index) and \
                                    getattr(aa.arr, "inline", None):
                                # (Specie(i).qty given to a ByRef Integer: an array of records'
                                # field, nothing else tells how wide)
                                bo = base_off(aa.arr)
                                if bo is not None:
                                    fa = ev.farrays.setdefault(bo[0].key(), {})
                                    old = fa.get(bo[1], (aa.arr.inline, 0, 0))
                                    fa[bo[1]] = (old[0], max(old[1], w), old[2])
                        proc = lf.m.procs.get(x.raw)
                        # (a parameter its procedure copies as a whole looks ByVal
                        # until it has a Type: VBDoom WI_initVariables)
                        sps = getattr(lf.m, "struct_param_sizes", None) or {}
                        byval_at = {(off - plist[0][0]) // 4 for off, _, _, byref in plist
                                    if not byref} if plist else set()
                        byval_at = {i for i in byval_at if (x.raw, i) not in sps}
                        for i, a in enumerate(x.args):
                            if isinstance(a, ir.AddrOf) and isinstance(a.e, ir.Index) and \
                                    getattr(a.e.arr, "inline", None):
                                k2 = (x.raw, i)     # s.l(i): exactly one element's size
                                ev.passed_elem[k2] = max(ev.passed_elem.get(k2, 0),
                                                         a.e.arr.inline)
                            if isinstance(a, ir.AddrOf) and isinstance(a.e, ir.Index) and \
                                    isinstance(a.e.arr, Var) and (
                                        lf.udt.get(a.e.arr.key()) or
                                        (lf.arrays.get(a.e.arr.key()) or {}).get("size", 0) > 8 or
                                        # VT_RECORD in its template (VBDoom cheat_powerup(i),
                                        # 8-byte cheatseq_t elements, not Doubles)
                                        (lf.arrays.get(a.e.arr.key()) or {}).get("record")):
                                # an element of an array of UDTs: the element's type
                                # (an array not typed yet learns it from the callee)
                                ev.passed.setdefault((x.raw, i), set()).add(a.e.arr.key())
                                continue
                            ao = addr_off(a)
                            if ao is not None and ao[1] != 0 or \
                                    isinstance(a, AddrOf) and isinstance(a.e, Deref) and \
                                    isinstance(a.e.e, Var) and a.e.off != 0 or \
                                    isinstance(a, Deref) and isinstance(a.e, Var) and a.off != 0:
                                # (v.f8 of a known struct, passed ByRef: the field itself)
                                ev.passed_field.add((x.raw, i))
                            if ao is None or ao[1] != 0:
                                continue
                            if isinstance(a, Var) and (a.space == "global" or
                                                       i in byval_at):
                                # a value, not an address: a module variable's,
                                # or one a ByVal parameter gets (ChessBrainVB
                                # GetMax(ByVal x1, ByVal x2) got every Long of
                                # the program and made them one struct)
                                continue
                            ev.passed.setdefault((x.raw, i), set()).add(ao[0].key())
                    if isinstance(x, ir.Call) and isinstance(x.raw, str) and "!" in x.raw:
                        api = apidb.lookup(x.raw.split("!", 1)[1])
                        if api is None:
                            continue
                        for (byval, pname, ptype), a in zip(api.params, x.args):
                            if byval or apidb.layout(ptype) is None:
                                continue
                            if isinstance(a, Var) and a.space != "arg":
                                # a local's value pushed: a pointer it holds (VBDoom
                                # waveOutWrite hwo, VarPtr(whdr(b)) through a
                                # temporary), not a struct; a parameter's value is
                                # the address it was given
                                continue
                            ao = addr_off(a)
                            if ao is None:
                                continue
                            v, off = ao
                            ev.sub.setdefault(v.key(), {})[off] = ptype
                            ev.names[v.key()] = v
    if getattr(lf, "small_rec", None) is not None:
        ev.size.setdefault(("local", lf.result_slot), lf.small_rec[1])
    ur = getattr(lf.p, "udt_ret", 0)
    if isinstance(ur, int) and not isinstance(ur, bool) and ur > 8 and \
            isinstance(lf.result_slot, int):
        # (a record of numbers, copied out: BitAND As DoubleWord, 64 bytes)
        ev.size.setdefault(("local", lf.result_slot), ur)
    hints = getattr(lf.m, "global_struct_hint", None) or {}
    for key in list(ev.access) + list(ev.farrays):
        if key[0] == "global" and key[1] in hints:
            ev.size.setdefault(key, hints[key[1]])  # (decompile.global_struct_hints)
    ev.forced = forced
    for key, offs in forced.items():
        acc = ev.access.get(key)
        if acc is not None:
            for off, w in offs.items():
                if acc.get(off, 0) <= 4:
                    acc[off] = w
    return ev


def candidates(ev, key):
    acc = ev.access.get(key, {})
    sub = ev.sub.get(key, {})
    want = ev.size.get(key)
    if not sub and not acc:
        return []
    if not sub:
        # no API takes it: the program's own Type, whatever API one of that
        # size its fields would fit (VBDoom wminfo, 200 bytes, came out As
        # JOB_INFO_2; its inner arrays then did not compile)
        return []
    if not sub and want is None and len(acc) < 3:
        # only one or two fields read and nothing else known: any API Type
        # with Longs there would "match" (pvVfsRead's private UDT came out
        # As EXCEPTION_POINTERS); a synthetic type is the honest answer
        return []
    out = []
    for name in apidb.by_size(None) if False else apidb.all_types():
        lay = apidb.layout(name)
        if lay is None:
            continue
        fields, total, _ = lay
        if want is not None and total != want:
            continue
        starts = {o: t for o, f, t in fields}
        ok = True
        for off, t in sub.items():
            if off == 0 and t == name:
                continue
            if starts.get(off) is None or starts[off].lower() != t.lower():
                ok = False
                break
        if not ok:
            continue
        for off in acc:
            if off < 0 or off >= total or apidb.field_at(name, off) is None:
                ok = False
                break
            if not sub and off not in starts and \
                    apidb.layout(starts.get(max([o for o in starts if o <= off], default=0)) or "")\
                    is not None:
                # without API evidence every read must start a field of the Type
                # itself: inside a nested UDT (EXCEPTION_POINTERS embeds the huge
                # CONTEXT) any offset would "fit"
                ok = False
                break
        if ok:
            out.append((total, name))
    out.sort()
    return [n for _, n in out]


def resolve(lf, body, publish=True):
    """Assign a UDT name to every struct variable; return the synthetic
    Type definitions that had to be generated."""
    ev = collect(lf, body)
    m = lf.m
    pu, ps = m.param_udt, m.param_size
    scalar = getattr(m, "scalar_params", ())
    from .lift import param_base
    first = param_base(lf)
    # what other procedures already know about this one's parameters
    for i, (off, name, t, byref) in enumerate(getattr(lf, "plist", ()) or ()):
        if (lf.p.va, (off - first) // 4) in scalar:
            continue                    # (decompile.scalar_params)
        if (lf.p.va, i) in pu:
            cur, known = lf.udt.get(("arg", off)), narrow_first_record(m, pu[(lf.p.va, i)],
                                                                     ev.access.get(("arg", off)))
            if cur is None or cur.startswith("T_") and not known.startswith("T_"):
                lf.udt[("arg", off)] = known    # a real Type over a guess (cht_GetParam:
                                                # its callers pass cheatseq_t records)
        if (lf.p.va, i) in ps:
            ev.size.setdefault(("arg", off), ps[(lf.p.va, i)])
    gen = {}
    keys = set(ev.access) | {k for k in ev.farrays if k[0] in ("local", "global", "arg")}
    # and the group members this procedure only passes on (HUlib_drawIText it:
    # nested into HUlib_drawTextLine's, so no longer typed by the callee)
    keys |= {k for (gva, k) in getattr(m, "synth_group", {}) if gva == lf.p.va}
    for key in sorted(keys, key=str):
        acc = ev.access.get(key, {})
        if key in lf.udt or key in lf.arrays:
            continue                    # (an array's SAFEARRAY fields are no struct)
        sub = ev.sub.get(key, {})
        groups = getattr(m, "synth_group", {})
        gkey = (None, key) if key[0] == "global" else (lf.p.va, key)
        if key[0] == "local" and (key in lf.variants or key in getattr(lf, "freed_variants", ())) and \
                max(acc, default=0) <= 12 and \
                not sub and key not in ev.size and gkey not in groups:
            continue                    # a Variant temporary: type word at +0, payload at +8
                                        # (a target Proc_7D1C00 vB0.f8, If vB0 > a5)
        if key[0] == "reg" and gkey not in groups:
            continue                    # a register is used for anything in turn: a target
                                        # Proc_7E1470 t_ecx As T_..., t_ecx.f0 = v208
        if not sub and len(acc) < 2 and key not in ev.size and gkey not in groups and \
                not (key[0] == "arg" and ev.farrays.get(key) and
                     key[1] in lf.__dict__.get("with_args", ())):
            continue                    # a plain pointer, not a struct (unless passed along
                                        # with struct variables: one Type for all of them;
                                        # or With on it, an array read through it: With p: .a(i))
        cands = candidates(ev, key)
        if len(cands) == 1:
            lf.udt[key] = cands[0]
            continue
        g_acc, g_cy, g_size = acc, ev.cy.get(key) or (), ev.size.get(key)
        if gkey in groups:
            g_acc, g_size, _, g_cy, g_arrays = m.synth_group_acc[groups[gkey]]
            g_cy = g_cy or ()
            if g_arrays or getattr(m, "synth_group_farrays", {}).get(groups[gkey]):
                g_acc = None
        if not sub and g_acc is not None and set(g_acc) <= {0, 4} and 0 in g_cy and g_size is None and \
                not ev.farrays.get(key):
            # both halves of one Currency, read and written: mov eax, [esi]; mov edx, [esi + 4] of Sub
            # Inc(c As Currency).  A record of that one field would be the same bits, but no Format$
            # or Variant can take it, nor an element of a Currency array go to its parameter
            # (t42currency, t59cyarith).  (And Type T: c As Currency passed about works as well as a
            # Currency.)
            continue
        if not publish:
            continue                    # wait: another round may disambiguate
        # one Type for every variable passed along a chain of struct
        # parameters (gh_zip: a private UDT handed from helper to helper)
        gid = groups.get(gkey)
        if gid is not None:
            acc, size, fixed, cy, arrays = m.synth_group_acc[gid]
            farrays = getattr(m, "synth_group_farrays", {}).get(gid, {})
            strs = m.__dict__.get("synth_group_strings", {}).get(gid, set())
        else:
            size, fixed, cy = ev.size.get(key), ev.fixed.get(key), ev.cy.get(key)
            arrays = ev.arrays.get(key)
            farrays = ev.farrays.get(key, {})
            strs = ev.strings.get(key, set())
        # (Singles apart: TVec and a record of two Longs look alike, and one Type would take
        # the numbers of one to the other; t84recret Pari after VSet)
        sig = (tuple(sorted(acc.items())), size, tuple(sorted((arrays or {}).items())),
               tuple(sorted(farrays.items())), tuple(sorted(ev.fps.get(key, ()))))
        names = m.__dict__.setdefault("synth_names", {})
        name = names.get(gid) if gid is not None else names.get(sig)
        if name is None:
            name = "T_%X_%d" % (max(list(acc) + list(farrays)) + 4 if acc or farrays else 0,
                                len(names) + 1)
            names[gid if gid is not None else sig] = name
            m.__dict__.setdefault("synth_farrays", {})[name] = farrays
            m.__dict__.setdefault("synth_strings", {})[name] = strs
            define(m, name, acc, size, fixed, cy, arrays)
        elif gid is not None:
            # a later prepass saw more of the group: the same name, the new layout
            m.__dict__.setdefault("synth_farrays", {})[name] = farrays
            m.__dict__.setdefault("synth_strings", {})[name] = strs
            define(m, name, acc, size, fixed, cy, arrays)
        gen[name] = name
        lf.udt[key] = name
    for key in ev.size:
        if key not in lf.udt and key not in ev.access:
            cands = candidates_size(ev, key)
            if len(cands) == 1:
                lf.udt[key] = cands[0]
    lset_types(lf, body)
    # this procedure's own struct parameters, for its callers (a synthetic
    # type too: a ByRef UDT argument must be of the parameter's very type)
    for i, (off, name, t, byref) in enumerate(getattr(lf, "plist", ()) or ()):
        u = lf.udt.get(("arg", off))
        if u is not None:
            pu.setdefault((lf.p.va, i), u)
    # tell callees (and callers) what is known (not where one UDT starts
    # with the other: group_structs.nested_params)
    nested = getattr(m, "nested_params", {})
    for (callee, idx), keys in ev.passed.items():
        if (callee, idx) in scalar:
            continue                    # a record's first field passed (scalar_params)
        for k in sorted(keys, key=str):
            if (lf.p.va, k) in nested or (None, k) in nested:
                continue                    # (a module variable's: ChessBrainVB Killer0 As TKiller)
            u = lf.udt.get(k)
            if u is not None and not u.startswith("T_"):     # never spread synthetic types
                pu.setdefault((callee, idx), u)
            sz = ev.size.get(k)
            if sz is not None:
                ps.setdefault((callee, idx), sz)
            plist = m.plists.get(callee) or ()
            if idx < len(plist) and (callee, idx) in pu:
                t = pu[(callee, idx)]
                if lf.udt.get(k, t).startswith("T_"):
                    lf.udt[k] = t           # the callee's type wins over a guess
    return [apidb.udt(n) for n in gen.values()]


def lset_types(lf, body):
    """LSet a = b over the whole UDT: both are of the same type.  Also run
    after share_global_udts, which may only then give an array its element
    type (VBDoom D_PostEvent: events(eventhead) = ev; the array is typed
    where others pass its elements, so ev learns it last)."""
    # a Function's value of a record type: that type, over a guess made from the fields read, and
    # what it is copied to too (randomV = VectorSet(...): v34 As T_8_24 by itself, T_8_3 returned)
    strong = set()
    for _, ss, br in body:
        for st in ss:
            if isinstance(st, Assign) and isinstance(st.dst, Var) and st.dst.space in ("local", "tmp") \
                    and isinstance(st.src, ir.Call) and isinstance(st.src.raw, int) and \
                    st.dst.key() not in lf.arrays:
                rt = lf.m.ret_types.get(st.src.raw)
                lay = apidb.layout(rt) if rt else None
                cur = lf.udt.get(st.dst.key())
                if lay and (cur is None or cur.startswith("T_") and apidb.layout(cur) and
                            apidb.layout(cur)[1] == lay[1]):
                    lf.udt[st.dst.key()] = rt
                    strong.add(st.dst.key())
    for _, ss, br in body:
        for st in ss:
            e = getattr(st, "e", None)
            if isinstance(e, ir.Call) and e.name == "LSet" and e.raw == "__vbaCopyBytes" and \
                    len(e.args) == 3:
                def end(x):
                    """(variable, its type) for one side; an element of an
                    array of UDTs is typed by the array (VBDoom D_PostEvent:
                    events(eventhead) = ev) and types it; a nested UDT field of
                    an element by its field (itemrespawnque(i) =
                    mobjs(m).spawnpoint)."""
                    if isinstance(x, AddrOf) and isinstance(x.e, ir.Index) and \
                            isinstance(x.e.arr, Var):
                        return x.e.arr, lf.udt.get(x.e.arr.key())
                    if isinstance(x, AddrOf) and isinstance(x.e, ir.Index) and \
                            isinstance(x.e.arr, Deref) and not getattr(x.e.arr, "inline", None):
                        # (an element of a dynamic array field: bp = rob(n).dna(i), LSet v38 = ...)
                        bo = base_off(x.e.arr)
                        ow = bo[0].arr if bo is not None and isinstance(bo[0], ir.Index) else \
                            (bo[0] if bo is not None else None)
                        et = lf.udt.get(ow.key()) if isinstance(ow, Var) else None
                        lay = apidb.layout(et) if et else None
                        ft = next((t for o, f, t in (lay[0] if lay else ()) if o == bo[1]), None)
                        return None, ft if ft and apidb.layout(ft) is not None else None
                    if isinstance(x, AddrOf) and isinstance(x.e, Deref) and \
                            isinstance(x.e.e, ir.Index) and isinstance(x.e.e.arr, Var):
                        et = lf.udt.get(x.e.e.arr.key())
                        lay = apidb.layout(et) if et else None
                        ft = next((t for o, f, t in (lay[0] if lay else ()) if o == x.e.off), None)
                        return None, ft if ft and apidb.layout(ft) is not None else None
                    ao = addr_off(x)
                    if ao is None or ao[1] != 0:
                        return None
                    return ao[0], lf.udt.get(ao[0].key())
                ends = [end(x) for x in e.args[:2]]
                if any(x is None for x in ends):
                    continue
                elem = [isinstance(x, AddrOf) and isinstance(x.e, ir.Index) for x in e.args[:2]]
                for (x, _), (y, t), el in ((ends[0], ends[1], elem[0]),
                                           (ends[1], ends[0], elem[1])):
                    lay = apidb.layout(t) if t else None
                    # (a real record over a synthetic Type too: LSet F = v80 made
                    # the result slot's T_30_3 a TRec13, a target Proc_9AE030)
                    cur = lf.udt.get(x.key()) if x is not None else None
                    if not (x is not None and t and (cur is None or cur.startswith("T_") and (
                            not t.startswith("T_") or y is not None and y.key() in strong)) and lay and
                            isinstance(e.args[2], Const) and lay[1] == e.args[2].value):
                        continue
                    if x.space in ("local", "tmp", "global"):
                        lf.udt[x.key()] = t     # (a module-level UDT: reboundstore)
                    elif x.space == "arg" and isinstance(x.off, int) and el and \
                            x.key() in lf.arrays:
                        # an array parameter's element copied whole: an array of
                        # that UDT (a target LSet v94 = a1(i) with a1() As Long)
                        lf.udt[x.key()] = t
                    elif x.space == "arg" and isinstance(x.off, int) and \
                            x.key() not in lf.arrays:
                        lf.udt[x.key()] = t     # a UDT parameter copied as a whole: ByRef
                        lf.byref.add(x.off)


def candidates_size(ev, key):
    """API Types of exactly the copied size (none for a variable with arrays
    of its own inside: see candidates).  A size alone is a coincidence
    (a 0x250-byte find-data UDT came out As EXCEPTION_POINTERS): without API
    evidence the fields read must also fit, at least three of them."""
    if ev.farrays.get(key) and not ev.sub.get(key):
        return []
    want = ev.size.get(key)
    sub = ev.sub.get(key, {})
    acc = ev.access.get(key, {})
    if not sub and len(acc) < 3:
        return []
    out = []
    for name in apidb.all_types():
        lay = apidb.layout(name)
        if lay is None or lay[1] != want:
            continue
        starts = {o: t for o, f, t in lay[0]}
        if not all(starts.get(o, "").lower() == t.lower() for o, t in sub.items() if o):
            continue
        if any(apidb.field_at(name, o) is None for o in acc if o >= 0):
            continue
        out.append(name)
    return out


def exact_array_bounds(model, bodies):
    """Module arrays the lifts found after the bounds were decided
    (decompile.global_array_bounds runs on the first lift's arrays): their
    module's variable table still knows them (Model.global_array_slots;
    VBDoom cheat_powerup(0 To 6), 8-byte records, seen only through element
    addresses in the second lift)."""
    slots = model.__dict__.get("_array_slots")
    if slots is None:
        slots = model._array_slots = model.global_array_slots()
    for va, (lf, body) in bodies.items():
        if lf is None:
            continue
        for key, info in lf.arrays.items():
            if key[0] != "global" or info.get("dyn") or info.get("fixed_bounds") or \
                    key[1] not in slots:
                continue
            cb, dims, vt, ff, img, rdesc = slots[key[1]]
            info["size"] = cb
            info["lb"], info["n"] = dims[0]
            info["dims"] = dims if len(dims) > 1 else None
            info["fixed_bounds"] = True
            if vt == 0x24:
                info["record"] = True
                from . import records
                rec = records.model_record(model, rdesc) if rdesc else None
                if rec is not None and not lf.udt.get(key):
                    info["udt"] = rec.name
                    lf.udt[key] = rec.name
                    lf.rec_of[key] = rec.va
            elif vt in (2, 3, 4, 5, 6, 7, 8, 9, 11, 12, 13, 17) and not info.get("vt"):
                info["vt"] = vt              # the elements' VARTYPE (player_names: Strings)


def prepass(model, bodies, rounds=4):
    """Resolve struct types across the whole program, then once more with
    synthetic types for whatever is still ambiguous."""
    exact_array_bounds(model, bodies)
    for r in range(rounds):
        last = r == rounds - 1
        if last:
            group_structs(model, bodies)
        for va, (lf, body) in bodies.items():
            if lf is None:
                continue
            types = resolve(lf, body, publish=last)
            if last:
                lf.synth_types = types
    # (first: a module record's fields are read in procedures that never
    # name its type - a target global_F06878.f0 is a Byte, Put #f only
    # in another one)
    share_global_udts(bodies)
    caller_records(model, bodies)
    record_bodies(model, bodies)
    array_element_types(model, bodies)
    share_global_udts(bodies)
    for va, (lf, body) in bodies.items():
        if lf is not None:
            lset_types(lf, body)
    small_return_types(model, bodies)
    adopt_passed_types(model, bodies)
    if alias_synthetic_records(model, bodies):
        adopt_passed_types(model, bodies)
    unify_array_params(model, bodies)
    # (again: a parameter's record just adopted types what is copied from it,
    # a target LSet v94 = a1 with v94 As T_30_4, a1 As TRec13)
    for va, (lf, body) in bodies.items():
        if lf is not None:
            lset_types(lf, body)
    nest_types(model, bodies)
    synth_scalar_fields(model, bodies)
    fp_fields(model, bodies)


def small_return_types(model, bodies):
    """A record Function in edx:eax (lift.small_record_return) and what takes its value: one Type
    (VB wants the very type for x = F()).  Its value in a field makes the field that Type."""
    rets = {}
    for va, (lf, body) in bodies.items():
        # (and one returned through a hidden pointer: Function hsltorgb only ever assigns its value
        # whole, hsltorgb = huetorgb(h), and came out As Variant)
        if lf is not None and (getattr(lf, "small_rec", None) is not None or
                               getattr(lf.p, "udt_ret", 0)):
            t = lf.udt.get(("local", lf.result_slot))
            if t:
                rets[va] = t
    fields = model.__dict__.setdefault("small_ret_fields", {})
    for rnd in range(2):
        for va, (lf, body) in bodies.items():
            if lf is None:
                continue
            for _, ss, _ in body:
                for st in ss:
                    e = getattr(st, "e", None)
                    if isinstance(e, ir.Call) and e.name == "LSet" and len(e.args) >= 2:
                        # LSet x = F(): the copy of a value that went through a temporary
                        src = e.args[1].e if isinstance(e.args[1], ir.AddrOf) else e.args[1]
                        dst = e.args[0].e if isinstance(e.args[0], ir.AddrOf) else e.args[0]
                        if isinstance(src, ir.Call) and isinstance(dst, Var) and \
                                rets.get(src.raw) and dst.key() not in lf.arrays and \
                                (getattr(src, "small_rec", None) or
                                 dst.key() == ("local", lf.result_slot)):
                            lf.udt.setdefault(dst.key(), rets[src.raw])
                            if dst.key() == ("local", lf.result_slot):
                                rets.setdefault(va, rets[src.raw])
                        # a field copied from a variable of a known Type is of that Type
                        # (o(i).jalki(2) = v: f20 As Long, "LSet allowed only on strings...")
                        t = rets.get(src.raw) if isinstance(src, ir.Call) else (
                            lf.udt.get(src.key()) if isinstance(src, Var) else None)
                        if t and isinstance(dst, Deref) and rnd == 1:
                            fields.setdefault(id(lf), []).append((lf, dst, t))
                        continue
                    if not (isinstance(st, ir.Assign) and isinstance(st.src, ir.Call) and
                            getattr(st.src, "small_rec", None)):
                        continue
                    t = rets.get(st.src.raw)
                    d = st.dst
                    if isinstance(d, Deref) and d.off == 0 and isinstance(d.e, Var) and \
                            lf.udt.get(d.e.key()):
                        d = d.e             # (the first field of a record: the record)
                    if isinstance(d, Var):
                        if t and d.key() not in lf.arrays:
                            lf.udt.setdefault(d.key(), t)
                        elif lf.udt.get(d.key()) and st.src.raw in bodies:
                            clf = bodies[st.src.raw][0]
                            if clf is not None and getattr(clf, "small_rec", None) is not None:
                                clf.udt.setdefault(("local", clf.result_slot), lf.udt[d.key()])
                                rets[st.src.raw] = lf.udt[d.key()]
                    elif isinstance(d, Deref) and t:
                        fields.setdefault(id(lf), []).append((lf, d, t))


def fp_fields(model, bodies):
    """A field the code reads or writes with the FPU's dword forms is a Single: 4 bytes alone say
    Long, and a Long there holds the bits (DarwinBots rob(i).nrg: fld dword, f3C4 As Long, so every
    value read as a number was wrong).  For the Types made here, not the API's."""
    import re

    def field(t, off):
        """The field holding byte off of Type t: (its name, its type), or None."""
        lay = apidb.layout(t) if t else None
        best = None
        for o, name, ft in (lay[0] if lay else ()):
            if o <= off and (best is None or o >= best[0]):
                best = (o, name, ft)
        return best[1:] if best else None

    def type_at(lf, e):
        if isinstance(e, Var):
            return lf.udt.get(e.key())
        if isinstance(e, ir.Index):
            if isinstance(e.arr, Var):
                return lf.udt.get(e.arr.key()) or (lf.arrays.get(e.arr.key()) or {}).get("udt")
            return type_at(lf, e.arr)
        if isinstance(e, Deref):
            b, off = e.e, e.off
            if isinstance(b, BinOp) and b.op == "+" and isinstance(b.b, Const) and \
                    isinstance(b.b.value, int):
                b, off = b.a, off + b.b.value
            f = field(type_at(lf, b), off)
            return f[1].split("(")[0] if f and apidb.layout(f[1].split("(")[0]) else None
        return None
    # what a Print statement's descriptor says of its items: the type VB compiled for (a Boolean
    # prints True, an Integer -1, and the bytes are the same: t86recarr r.tot(1), r.paivat(1))
    PRINTED = {2: "Integer", 3: "Long", 4: "Single", 5: "Double", 6: "Currency", 7: "Date",
               11: "Boolean", 17: "Byte"}
    SAME = {2: ("Integer", "Boolean"), 4: ("Long", "Single"), 8: ("Double", "Currency", "Date"),
            1: ("Byte",)}

    def locate(lf, x):
        """(Type, field name) of a field or element of an array in a record, or None."""
        stride = None
        if isinstance(x, Deref) and isinstance(x.e, ir.Index) and isinstance(x.e.arr, Deref) and \
                getattr(x.e.arr, "inline", None):
            b, off, stride = x.e.arr.e, x.e.arr.off + x.off, x.e.arr.inline
        elif isinstance(x, Deref):
            b, off = x.e, x.off
            if isinstance(b, BinOp) and b.op == "+" and isinstance(b.b, Const) and \
                    isinstance(b.b.value, int):
                b, off = b.a, off + b.b.value
        elif isinstance(x, ir.Index) and isinstance(x.arr, Deref):
            b, off, stride = x.arr.e, x.arr.off, getattr(x.arr, "inline", None)
        else:
            return None
        t = type_at(lf, b)
        if not t or not t.startswith(("T_", "TRec")):
            return None
        lb = apidb.array_from_bound(t, off, stride) if stride else None
        if lb:
            return t, lb[0]
        while True:
            lay = apidb.layout(t)
            best = None
            for o, name, ft in (lay[0] if lay else ()):
                if o <= off:
                    best = (o, name, ft)
            if best is None:
                return None
            inner = apidb.layout(best[2]) if best[2] not in apidb.SIZES else None
            if not inner or not inner[0]:
                return t, best[1]
            # (a record inside: the field of that one, in whichever element)
            off = (off - best[0]) % (inner[1] or 1)
            t = best[2]
    found = {}

    def single_var(lf, v):
        """A variable the FPU reads or writes as a dword."""
        if not isinstance(v, Var):
            return False
        k = v.key()
        sz = lf.sizes.get(k) or (lf.sizes.get((k[0], k[1][0])) if isinstance(k[1], tuple) else None)
        return bool(sz) and (4, True) in sz and not any(z == 8 for z, _ in sz)
    for va, (lf, body) in bodies.items():
        if lf is None:
            continue
        for _, ss, br in body:
            for st in list(ss) + ([ExprStmt(br.cond)] if br is not None else []):
                for e in (st.exprs() if hasattr(st, "exprs") else ()):
                    for x in e.walk():
                        if getattr(x, "fp4", False):
                            loc = locate(lf, x)
                            if loc:
                                found.setdefault(loc[0], set()).add(loc[1])
                if isinstance(st, Assign) and st.kind == "let":
                    # moved with integer instructions from or to a Single: the same bits, the same
                    # type (l = .OldGD ... l * 2: a Long field, and VB would convert the number)
                    for f, v in ((st.dst, st.src), (st.src, st.dst)):
                        if isinstance(f, (Deref, ir.Index)) and single_var(lf, v):
                            loc = locate(lf, f)
                            if loc:
                                found.setdefault(loc[0], set()).add(loc[1])
    for t, names in found.items():
        body = apidb.udt(t)
        if not body:
            continue
        new = [re.sub(r"^(\s*(%s)\b(?:\([^)]*\))? As )Long\s*$" % "|".join(map(re.escape, names)),
                      r"\1Single", ln) for ln in body]
        if new != body:
            apidb.add_type(t, new)
    # a field that holds objects: Is Nothing, Set, a method called on it (Form1 Charts(i).graf: a
    # Long in the Type, all of its uses a Type mismatch)
    held = {}

    def note_object(lf, x, name=None):
        loc = locate(lf, x) if isinstance(x, (Deref, ir.Index)) else None
        if loc is None:
            return
        cur = held.setdefault(loc[0], {})
        if name or loc[1] not in cur:
            cur[loc[1]] = name or cur.get(loc[1]) or "Object"

    def class_name(lf, x):
        it = lf.objtype.get(x.key()) if hasattr(x, "key") else None
        n = getattr(it, "name", None)
        if not n or n.endswith("()"):
            return None
        return n[1:] if n.startswith("_") else n
    for va, (lf, body) in bodies.items():
        if lf is None:
            continue
        for _, ss, br in body:
            for st in list(ss) + ([ExprStmt(br.cond)] if br is not None else []):
                if isinstance(st, Assign) and st.kind == "set":
                    for side in (st.dst, st.src):
                        note_object(lf, side, class_name(lf, side))
                for e in (st.exprs() if hasattr(st, "exprs") else ()):
                    for x in e.walk():
                        if isinstance(x, BinOp) and x.op == "Is":
                            for y in (x.a, x.b):
                                note_object(lf, y, class_name(lf, y))
                        ob = getattr(x, "obj", None)
                        if ob is not None and isinstance(ob, Deref):
                            note_object(lf, ob, class_name(lf, ob))
    for t, names in held.items():
        body = apidb.udt(t)
        if not body:
            continue
        new = []
        for ln in body:
            m = re.match(r"^(\s*(\w+) As )(Long|Object)\s*$", ln)
            if m and m.group(2) in names:
                ln = m.group(1) + names[m.group(2)]
            new.append(ln)
        if new != body:
            apidb.add_type(t, new)
    printed = {}
    for va, (lf, body) in bodies.items():
        if lf is None:
            continue
        for _, ss, _ in body:
            for st in ss:
                e = getattr(st, "e", None)
                codes = getattr(e, "print_codes", None)
                args = e.args if codes else None
                if not codes and getattr(e, "input_codes", None):
                    codes, args = e.input_codes, e.args[1:]     # (Input #f, x: the same codes)
                if not codes:
                    continue
                items = [c for c in codes if c & 0x3f]
                for c, a in zip(items, args):
                    vt = PRINTED.get(c & 0x3f)
                    loc = locate(lf, a.e if isinstance(a, ir.AddrOf) else a) if vt else None
                    if loc:
                        printed.setdefault(loc[0], {})[loc[1]] = vt
    for t, names in printed.items():
        body = apidb.udt(t)
        if not body:
            continue
        new = []
        for ln in body:
            m = re.match(r"^(\s*(\w+)\b(?:\([^)]*\))? As )(\w+)\s*$", ln)
            vt = names.get(m.group(2)) if m else None
            if vt and any(vt in g and m.group(3) in g for g in SAME.values()):
                ln = m.group(1) + vt
            new.append(ln)
        if new != body:
            apidb.add_type(t, new)
    # an 8-byte field given a Currency or a Date (its bytes alone say Double): the same
    wide = {}
    for va, (lf, body) in bodies.items():
        if lf is None:
            continue
        for _, ss, _ in body:
            for st in ss:
                if not (isinstance(st, ir.Assign) and isinstance(st.dst, (Deref, ir.Index))):
                    continue
                vt = getattr(st.src, "vbtype", None)
                if vt not in ("Currency", "Date"):
                    continue
                d = st.dst
                if isinstance(d, Deref) and not isinstance(d.e, ir.Index):
                    b, off = d.e, d.off
                elif isinstance(d, Deref) and getattr(d.e.arr, "inline", None):
                    b, off = d.e.arr.e, d.e.arr.off + d.off
                elif isinstance(d, ir.Index) and getattr(d.arr, "inline", None):
                    b, off = d.arr.e, d.arr.off
                elif isinstance(d, Deref):
                    b, off = d.e, d.off
                else:
                    continue
                t = type_at(lf, b)
                f = field(t, off) if t and t.startswith(("T_", "TRec")) else None
                if f:
                    wide.setdefault(t, {})[f[0]] = vt
    for t, names in wide.items():
        body = apidb.udt(t)
        if body:
            new = list(body)
            for fname, vt in names.items():
                new = [re.sub(r"^(\s*%s\b(?:\([^)]*\))? As )Double\s*$" % re.escape(fname),
                              r"\g<1>" + vt, ln) for ln in new]
            if new != body:
                apidb.add_type(t, new)
    for items in model.__dict__.get("small_ret_fields", {}).values():
        for lf, d, rt in items:
            b, off = d.e, d.off
            if isinstance(b, BinOp) and b.op == "+" and isinstance(b.b, Const) and \
                    isinstance(b.b.value, int):
                b, off = b.a, off + b.b.value
            t = type_at(lf, b)
            f = field(t, off) if t and t.startswith(("T_", "TRec")) else None
            if f:
                retype_field(t, f[0], rt)


def retype_field(t, fname, rt):
    """Field fname of Type t is of Type rt.  The fields a bigger Type covers go: rob(i).f3318 As
    T_8_5 stayed followed by the f331C it holds, and everything after it was 4 bytes off in
    DarwinBots' robot and 8 in its shot."""
    import re
    body, lay = apidb.udt(t), apidb.layout(t)
    if not body or not lay:
        return False
    fields = [x for x in lay[0]]
    at = next((i for i, x in enumerate(fields) if x[1] == fname), None)
    if at is None or fields[at][2] == rt:
        return False
    o = fields[at][0]
    rl = apidb.layout(rt)
    size = rl[1] if rl else apidb.SIZES.get(rt)
    if not size:
        return False
    end = o + size
    drop, last = set(), end
    for i in range(at + 1, len(fields)):
        if fields[i][0] >= end:
            break
        drop.add(fields[i][1])
    new, left = [], None
    for ln in body:
        m = re.match(r"^(\s*)(\w+)\b(\([^)]*\))?( As )(.+?)\s*$", ln)
        name = m.group(2) if m else None
        if name == fname:
            if m.group(3) and m.group(3) != "()":
                return False            # (an array: its elements' Type is another matter)
            new.append("%s%s%s%s" % (m.group(1), m.group(2), m.group(4), rt))
        elif name in drop:
            continue
        else:
            new.append(ln)
    apidb.add_type(t, new)
    nl = apidb.layout(t)
    if nl is None or nl[1] != lay[1] or any(x[0] != y[0] for x in nl[0] for y in lay[0] if x[1] == y[1]):
        apidb.add_type(t, body)         # (it didn't fit: a field further on moved)
        return False
    return True


def synth_scalar_fields(model, bodies):
    """A field of a synthetic Type passed by reference to a Single parameter
    (or Currency / Date) is of that type, which its 4 or 8 bytes alone do not
    tell (a target: Proc_9271E0's ByRef Single coordinates were given Long
    fields - 68 ByRef argument mismatches).  Kept where every parameter such a
    field is passed to agrees; the Type's line is rewritten."""
    from .types import param_args
    cands = {}
    for va, (lf, body) in bodies.items():
        if lf is None:
            continue
        for _, ss, br in body:
            for st in ss:
                for e in (st.exprs() if hasattr(st, "exprs") else ()):
                    for x in e.walk():
                        if not (isinstance(x, ir.Call) and isinstance(x.raw, int)):
                            continue
                        plist = model.plists.get(x.raw)
                        for a, pi in param_args(plist, x.args):
                            if not plist[pi][3] or not isinstance(a, AddrOf) or \
                                    not isinstance(a.e, Deref):
                                continue
                            bo = base_off(a.e)
                            if bo is None:
                                continue
                            owner = bo[0].arr if isinstance(bo[0], ir.Index) else bo[0]
                            u = lf.udt.get(getattr(owner, "key", lambda: None)())
                            if u and u.startswith("T_"):
                                cands.setdefault((u, bo[1]), set()).add(plist[pi][2])
    WIDE = {"Single": "Long", "Currency": "Double", "Date": "Double"}
    for (u, off), ts in sorted(cands.items()):
        t = next(iter(ts)) if len(ts) == 1 else None
        body = apidb.udt(u) if t in WIDE else None
        if body is None:
            continue
        line = "    f%X As %s" % (off, WIDE[t])
        if line in body:
            apidb.add_type(u, [("    f%X As %s" % (off, t)) if ln == line else ln
                               for ln in body])


def alias_synthetic_records(model, bodies):
    """A synthetic Type of a parameter that a caller gives a real record of the
    same size, with fields only where the record has them, is that record, in
    every procedure using it (a target T_30_4 = TRec13, 48 bytes: besides the
    ByRef mismatch, its f10 As String wrote the wrong bytes to List2.idx, and
    Put failed with Bad record length).  Returns {synthetic: record}."""
    alias = {}

    def fields(t):
        lay = apidb.layout(t)
        return None if lay is None else ({o for o, n, _ in lay[0] if not n.startswith("pad")}, lay[1])
    for va, (lf, body) in bodies.items():
        if lf is None:
            continue
        for (callee, idx), keys in collect(lf, body).passed.items():
            clf = bodies.get(callee, (None, None))[0]
            plist = model.plists.get(callee) or ()
            if clf is None or idx >= len(plist):
                continue
            cur = clf.udt.get(("arg", plist[idx][0]))
            if not cur or not cur.startswith("T_"):
                continue
            for k in keys:
                u = lf.udt.get(k)
                if not u or u.startswith("T_"):
                    continue
                a, b = fields(cur), fields(u)
                if a and b and a[1] == b[1] and a[0] <= b[0] and alias.setdefault(cur, u) != u:
                    alias[cur] = None           # two records: no telling
    alias = {k: v for k, v in alias.items() if v}
    if alias:
        for lf, _ in bodies.values():
            if lf is None:
                continue
            for k, t in list(lf.udt.items()):
                if t in alias:
                    lf.udt[k] = alias[t]
            for info in lf.arrays.values():
                if info.get("udt") in alias:
                    info["udt"] = alias[info["udt"]]
    return alias


def adopt_passed_types(model, bodies, rounds=10):
    """A parameter typed only by a guess (a synthetic T_ Type) that callers
    give a variable of a real Type (a record, an API Type) to is of that Type:
    the variable's type may be known only after the struct rounds
    (share_global_udts: VBDoom cht_GetParam(cht) gets cheat_mus, a
    cheatseq_t record).  Not where the caller's struct only starts with the
    callee's (group_structs.nested_params)."""
    nested = getattr(model, "nested_params", {})
    # a parameter the callee only ever reads at offset 0 is not a record,
    # whatever a caller passes: VB gives the address of a record's first
    # field and of the record itself alike (a target Proc_8D8880 reads one
    # Byte there, and 245 calls passing other records' fields mismatched)
    sps = getattr(model, "struct_param_sizes", None) or {}
    scalar = getattr(model, "scalar_params", ())
    import os
    adbg = int(os.environ["VB6DEC_ADBG"], 16) if os.environ.get("VB6DEC_ADBG") else None
    evs = {va: collect(lf, body) for va, (lf, body) in bodies.items() if lf is not None}
    # the buffer a UDT Function's result goes to is of the Function's record
    # (a target LSet v21C = ReadInfo(n): v21C was a T_30_3 of the
    # same 48 bytes, and 18 calls passing it on to TRec13 parameters mismatched)
    results = []
    for va, (lf, body) in bodies.items():
        if lf is None:
            continue
        for _, ss, _ in body:
            for st in ss:
                dst = src = None
                if isinstance(st, Assign):
                    dst, src = st.dst, st.src
                elif isinstance(st, ExprStmt) and isinstance(st.e, ir.Call) and \
                        st.e.name == "LSet" and len(st.e.args) == 3:
                    # LSet v = F(...): the call inlined into the block copy
                    dst, src = st.e.args[0], st.e.args[1]
                    dst = dst.e if isinstance(dst, AddrOf) else dst
                    src = src.e if isinstance(src, AddrOf) else src
                if isinstance(dst, ir.Index) and isinstance(dst.arr, Var):
                    dst = dst.arr                # an element: its array's element type
                if isinstance(dst, Var) and dst.space in ("local", "global", "tmp", "member") \
                        and isinstance(src, ir.Call) and isinstance(src.raw, int) and \
                        getattr(model.procs.get(src.raw), "udt_ret", False):
                    results.append((lf, dst.key(), src.raw))
    for _ in range(rounds):
        changed = False
        for lf, k, callee in results:
            clf = bodies.get(callee, (None, None))[0]
            if clf is None or not isinstance(clf.result_slot, int):
                continue
            rk = ("local", clf.result_slot)
            rt = clf.udt.get(rk)
            u = lf.udt.get(k)
            if rt and not rt.startswith("T_") and u != rt and (not u or u.startswith("T_")) \
                    and k not in lf.arrays and apidb.layout(rt) is not None:
                lf.udt[k] = rt
                changed = True
            elif u and not u.startswith("T_") and u != rt and (not rt or rt.startswith("T_")) \
                    and apidb.layout(u) is not None and (
                        not rt or apidb.layout(rt) is None or
                        apidb.layout(rt)[1] == apidb.layout(u)[1]):
                # the other way: a caller's record gives the Function its type
                # (a target empty1-style Functions returning a zeroed record As
                # T_44_16, assigned to the program's own records)
                clf.udt[rk] = u
                changed = True
        for va, ev in evs.items():
            lf = bodies[va][0]
            for (callee, idx), keys in ev.passed.items():
                if adbg == callee:
                    import sys
                    print("ADBG %s -> %X a%d scalar %s keys %s udts %s arrays %s" % (
                        lf.p.name, callee, idx + 1, (callee, idx) in scalar, sorted(keys, key=str),
                        [lf.udt.get(k) for k in sorted(keys, key=str)],
                        [{kk: vv for kk, vv in (lf.arrays.get(k) or {}).items() if kk != "var"}
                         for k in sorted(keys, key=str)]), file=sys.stderr)
                clf = bodies.get(callee, (None, None))[0]
                plist = model.plists.get(callee) or ()
                if clf is None or idx >= len(plist):
                    continue
                ck = ("arg", plist[idx][0])
                if (callee, idx) in scalar and \
                        (callee, ck) not in getattr(model, "proc_arrays", {}):
                    continue            # no record either way (decompile.scalar_params;
                                        # an array parameter is no scalar: it was
                                        # classified before the second lift knew it)
                cur = clf.udt.get(ck)
                struct_like = (callee, idx) in sps or cur is not None or \
                    (callee, ck) in getattr(model, "proc_arrays", {})
                rec = getattr(model, "rec_params", {}).get((callee, idx))
                if rec is not None and rec.name and (not cur or cur.startswith("T_")):
                    clf.udt[ck] = cur = rec.name    # the callee's own record
                both_arrays = (callee, ck) in getattr(model, "proc_arrays", {})
                for k in sorted(keys, key=str):
                    u = lf.udt.get(k)
                    if not struct_like and rec is None:
                        continue                # the callee uses it as a scalar
                    # (an array passed to an array parameter: the UDT names the
                    # element type on both sides - a target v8B4() As Long given
                    # to a1() As TRec13)
                    if cur and not cur.startswith("T_") and (not u or u.startswith("T_")) and \
                            (k not in lf.arrays or both_arrays and
                             (lf.arrays[k].get("size") or 0) in (0, 4, apidb.layout(cur)[1]
                                                                 if apidb.layout(cur) else 0) and
                             not lf.arrays[k].get("vt")) and ((va, k) not in nested) and \
                            k[0] in ("local", "global", "arg") and \
                            apidb.layout(cur) is not None and (
                                not u or apidb.layout(u) is None or
                                apidb.layout(u)[1] <= apidb.layout(cur)[1]):
                        # the other way: the callee's parameter is a real record,
                        # so what callers pass by reference is one too
                        lf.udt[k] = cur
                        changed = True
                        continue
                    if not u or u.startswith("T_") or ((va, k) in nested) or \
                            (k[0] == "global" and (None, k) in nested):
                        continue
                    lu, lc = apidb.layout(u), apidb.layout(cur) if cur else None
                    if lu is None or cur == u or cur and not cur.startswith("T_") or \
                            lc is not None and lc[1] > lu[1]:
                        continue
                    clf.udt[ck] = u
                    changed = True
        if not changed:
            break


def unify_array_params(model, bodies):
    """An array passed to an array parameter has the parameter's very element type, whatever each
    procedure saw of it: DnaLen reads a1(i).f2, so a1() As T_4_52; Delete only moves elements with
    LSet and hands the array on, and its a1() As Long could not be given to DnaLen; nor rob(i).dna
    to it.  Arrays that are passed to one another (fields of records too) are one group; the group
    takes a record if one of them has one, a real one before a synthetic, the one with the most
    fields of those.  Only where the sizes agree."""
    from .types import param_args
    pa = getattr(model, "proc_arrays", {})
    if not pa:
        return
    recs = {r.name: r for r in model.__dict__.get("records", {}).values() if r is not None}
    parent = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def node(va, k):
        return (None, k) if k[0] == "global" else (va, k)
    for va, (lf, body) in bodies.items():
        if lf is None:
            continue
        for _, ss, br in body:
            for st in list(ss) + ([ExprStmt(br.cond)] if br is not None else []):
                for e in (st.exprs() if hasattr(st, "exprs") else ()):
                    for x in e.walk():
                        if not (isinstance(x, ir.Call) and isinstance(x.raw, int)):
                            continue
                        plist = model.plists.get(x.raw)
                        for a, pi in param_args(plist, x.args):
                            ck = ("arg", plist[pi][0])
                            clf = bodies.get(x.raw, (None, None))[0]
                            if isinstance(a, AddrOf) and isinstance(a.e, Var) and \
                                    a.e.key() in lf.arrays and clf is not None and \
                                    ck not in clf.arrays and ck not in clf.udt and \
                                    not any(isinstance(y, Var) and y.key() == ck
                                            for _, ss2, br2 in bodies[x.raw][1] for st2 in ss2
                                            for e2 in (st2.exprs() if hasattr(st2, "exprs") else ())
                                            for y in e2.walk()):
                                # an array for a parameter its procedure doesn't use: still an
                                # array (RemapTies clist, OList, n: ByVal a2 As Long, Type mismatch)
                                info = lf.arrays[a.e.key()]
                                clf.arrays[ck] = {"size": info.get("size"), "dyn": True, "lb": None,
                                                  "n": None, "vt": info.get("vt"),
                                                  "var": Var("arg", plist[pi][0])}
                                clf.byref.add(plist[pi][0])
                            if (x.raw, ck) not in pa and not (clf is not None and
                                                              ck in clf.arrays):
                                continue
                            aa = a.e if isinstance(a, AddrOf) else a
                            n = None
                            if isinstance(aa, Var) and aa.key() in lf.arrays:
                                n = node(va, aa.key())
                            elif isinstance(aa, Deref):
                                bo = base_off(aa)
                                if bo is not None:
                                    ow = bo[0].arr if isinstance(bo[0], ir.Index) else bo[0]
                                    u = lf.udt.get(ow.key()) if isinstance(ow, Var) else None
                                    if u in recs and recs[u].fields.get(bo[1]) == "Array":
                                        n = ("rec", u, bo[1])
                            if n is not None:
                                parent[find(n)] = find((x.raw, ck))
        # a = b between dynamic arrays, or their elements copied whole: one element type too
        # (shot.dna = rob(n).dna: "Can't assign to array", with f3C() As Variant)
        def array_node(e):
            if isinstance(e, Var) and e.key() in lf.arrays:
                return node(va, e.key())
            if isinstance(e, Deref):
                bo = base_off(e)
                if bo is not None:
                    ow = bo[0].arr if isinstance(bo[0], ir.Index) else bo[0]
                    u = lf.udt.get(ow.key()) if isinstance(ow, Var) else None
                    if u in recs and recs[u].fields.get(bo[1]) == "Array":
                        return ("rec", u, bo[1])
            return None
        for _, ss, br in body:
            for st in ss:
                pair = None
                if isinstance(st, Assign):
                    pair = (st.dst, st.src)
                    pair = tuple(x.e if isinstance(x, AddrOf) else x for x in pair)
                e = getattr(st, "e", None)
                if isinstance(e, ir.Call) and e.name == "LSet" and len(e.args) == 3:
                    pair = tuple(x.e if isinstance(x, AddrOf) else x for x in e.args[:2])
                    pair = tuple(x.arr if isinstance(x, ir.Index) else x for x in pair)
                if pair is None:
                    continue
                na, nb = array_node(pair[0]), array_node(pair[1])
                if na is not None and nb is not None:
                    parent[find(na)] = find(nb)
    groups = {}
    for x in list(parent):
        groups.setdefault(find(x), []).append(x)

    def info_of(n):
        if n[0] == "rec":
            return None, None
        if n[0] is None:
            return None, n[1]
        lf = bodies.get(n[0], (None, None))[0]
        return lf, n[1]

    def cur_type(n):
        if n[0] == "rec":
            return recs[n[1]].elem.get(n[2]) if getattr(recs[n[1]], "elem", None) else None
        if n[0] is None:
            for lf, _ in bodies.values():
                if lf is not None and lf.udt.get(n[1]):
                    return lf.udt[n[1]]
            return None
        lf, k = info_of(n)
        return lf.udt.get(k) if lf is not None else None

    def fcount(t):
        lay = apidb.layout(t)
        return sum(1 for o, nm, ft in lay[0] if not nm.startswith("pad")) if lay else 0
    touched = set()
    import os
    uadbg = os.environ.get("VB6DEC_UADBG")
    for members in groups.values():
        ts = {t for t in map(cur_type, members) if t and apidb.layout(t) and apidb.layout(t)[0]}
        if uadbg and any(isinstance(n[0], int) and "%X" % n[0] == uadbg for n in members):
            import sys
            print("UA", [("%X" % n[0] if isinstance(n[0], int) else n[0], n[1:], cur_type(n))
                         for n in members], {t: apidb.layout(t)[1] for t in ts}, file=sys.stderr)
        if not ts:
            # no record: one scalar element type, as far as one of them says (DrawGrid(ga() As
            # Boolean) hands ga to Private DrawStat, a1() As Long, and rob(n).ga goes to it)
            from .types import VT_NAMES
            names_vt = {v: k for k, v in VT_NAMES.items()}

            def scalar_of(n):
                if n[0] == "rec":
                    return names_vt.get((getattr(recs[n[1]], "elem", None) or {}).get(n[2]))
                for lf in ([lf for lf, _ in bodies.values() if lf is not None and n[1] in lf.arrays]
                           if n[0] is None else [bodies.get(n[0], (None, None))[0]]):
                    if lf is not None and (lf.arrays.get(n[1]) or {}).get("vt"):
                        return lf.arrays[n[1]]["vt"]
                return None
            vts = {v for v in map(scalar_of, members) if v and v != 12}
            if len(vts) == 1:
                vt = next(iter(vts))
                for n in members:
                    if n[0] == "rec":
                        r = recs[n[1]]
                        if (r.elem.get(n[2]) or "Variant") == "Variant":
                            r.elem[n[2]] = VT_NAMES[vt]
                            touched.add(n[1])
                        continue
                    for lf in ([lf for lf, _ in bodies.values() if lf is not None and n[1] in lf.arrays]
                               if n[0] is None else [bodies.get(n[0], (None, None))[0]]):
                        info = lf.arrays.get(n[1]) if lf is not None else None
                        if info is not None and not info.get("vt") and not lf.udt.get(n[1]):
                            info["vt"] = vt
                            info["size"] = {2: 2, 3: 4, 4: 4, 5: 8, 6: 8, 7: 8, 8: 4, 9: 4, 11: 2,
                                            17: 1}.get(vt, info.get("size"))
                            info.pop("size_weak", None)
            continue
        real = [t for t in ts if not t.startswith("T_")]
        # (of synthetic ones, first the one that saw the arrays inside at the most offsets: a procedure
        # only reaching a part of them makes a Type with more fields and less right, f0 As Long and
        # f4(0 To 99) for 202 Longs, and the others' .f0(i) had no array: a target global_F044D8)
        seen = model.__dict__.get("synth_farrays", {})
        pick = sorted(real or ts, key=lambda t: (-len(seen.get(t) or ()), -fcount(t), t))
        if len({apidb.layout(t)[1] for t in ts}) != 1:
            continue                    # (different sizes: they are not one array, or a guess is off)
        best = pick[0]
        size = apidb.layout(best)[1]
        for n in members:
            if n[0] == "rec":
                r = recs[n[1]]
                if r.elem.get(n[2]) != best:
                    r.elem[n[2]] = best
                    touched.add(n[1])
                continue
            if n[0] is None:
                holders = [lf for lf, _ in bodies.values() if lf is not None and n[1] in lf.arrays]
            else:
                holders = [bodies[n[0]][0]] if bodies.get(n[0], (None, None))[0] is not None else []
            for lf in holders:
                info = lf.arrays.get(n[1]) or {}
                if info.get("vt") and info.get("vt") != 36:
                    continue            # (its template says a scalar type)
                if info.get("size") not in (None, 0, size) and not info.get("size_weak"):
                    continue
                if info.get("size_weak"):
                    info["size"] = size
                if lf.udt.get(n[1]) != best:
                    lf.udt[n[1]] = best
    from . import records
    for name in touched:
        records.register(recs[name], getattr(recs[name], "access", None))


def share_global_udts(bodies):
    """A module variable or array has one type: what any procedure learnt of
    it (a record from __vbaRecAssign, a parameter it was passed to, its
    accesses) holds in all of them, a real type over a synthetic one (t09
    modB reads net(3, 1).a, field 0, where only modA saw the record)."""
    best = {}
    for va, (lf, body) in bodies.items():
        if lf is None:
            continue
        for key, t in lf.udt.items():
            if key[0] == "global" and t:
                cur = best.get(key)
                if cur is None or cur.startswith("T_") and not t.startswith("T_"):
                    best[key] = t
    for va, (lf, body) in bodies.items():
        if lf is None:
            continue
        for key, t in best.items():
            cur = lf.udt.get(key)
            if cur is None or cur.startswith("T_") and not t.startswith("T_"):
                lf.udt[key] = t


def scalar_global_arrays(model):
    """Keys of the module-level fixed arrays whose static descriptor names a
    scalar element type (VARTYPE Long, Integer, String...): never an array of
    UDTs, whatever groups they seem to fall in (ChessBrainVB PVLength As Long
    came out As T_20_1, WAttack As Integer As T_2_34)."""
    slots = model.__dict__.get("_array_slots")
    if slots is None:
        slots = model._array_slots = model.global_array_slots()
    return {("global", va) for va, (cb, dims, vt, ff, img, rd) in slots.items()
            if vt not in (0x24, 0) and not rd}


def record_like(fields, k):
    """Fields of several sizes and places fit for a record of k bytes, but for the last one's width."""
    return len(fields) >= 3 and k % 4 != 0


def array_element_types(model, bodies):
    """Arrays read field by field whose elements have no type yet (no record
    descriptor, no parameter they were passed to): one synthetic Type each,
    from the element accesses of every procedure that uses the array, as
    wide as its element (VBDoom players(i).mo, mobjs(i).x).  resolve leaves
    arrays alone: their own variable also reads the SAFEARRAY header."""
    acc, size, holders, record = {}, {}, {}, set()
    farr = {}                               # arrays inside the elements (inline_field)
    fixed = {}                              # String * n fields of the elements
    scalar = scalar_global_arrays(model)

    def aid(va, lf, key):
        if key[0] == "global":
            return key
        if key[0] == "member" and lf.obj is not None:
            return (lf.obj.index, key)
        return (va, key)
    for va, (lf, body) in bodies.items():
        if lf is None:
            continue
        for key, info in lf.arrays.items():
            if key in scalar:
                continue                    # (its descriptor says Long, Integer, ...)
            a = aid(va, lf, key)
            holders.setdefault(a, []).append((lf, key))
            if info.get("size"):
                size[a] = max(size.get(a, 0), info["size"])
            if info.get("record"):
                record.add(a)               # VT_RECORD in its descriptor template
        for _, ss, br in body:
            exprs = [e for st in ss for e in getattr(st, "exprs", lambda: ())()]
            if br is not None:
                exprs.append(br.cond)
            for e in exprs:
                addressed = set()
                for x in e.walk():
                    if isinstance(x, AddrOf):
                        addressed.add(id(x.e))
                    fd = None
                    if isinstance(x, Deref) and isinstance(x.e, ir.Index) and \
                            getattr(x.e.arr, "inline", None):
                        # an array inside the element (only its address: no width)
                        fd, ext = x.e.arr, 0 if id(x) in addressed else x.size + x.off
                    elif isinstance(x, ir.Index) and getattr(x.arr, "inline", None):
                        fd, ext = x.arr, 0
                    if fd is not None and isinstance(fd.e, ir.Index) and \
                            isinstance(fd.e.arr, Var) and fd.e.arr.key() in lf.arrays:
                        a = aid(va, lf, fd.e.arr.key())
                        fa = farr.setdefault(a, {})
                        old = fa.get(fd.off, (fd.inline, 0, 0))
                        fa[fd.off] = (fd.inline, max(old[1], ext),
                                      max(old[2], getattr(fd, "inline_n", 0) or 0))
                        acc.setdefault(a, {})
                        continue
                    if getattr(x, "inline", None):
                        continue                    # (seen above, as an array)
                    n = getattr(x, "fixstr", None)
                    if n and isinstance(x, ir.Index) and isinstance(x.arr, Var) and \
                            x.arr.key() in lf.arrays:
                        # the element read as a String * n: its first field is one
                        # (a target o1006 = Places(i) of a 30-character name)
                        a = aid(va, lf, x.arr.key())
                        fixed.setdefault(a, {})[0] = n
                        acc.setdefault(a, {}).setdefault(0, 2 * n)
                        continue
                    if isinstance(x, Deref) and isinstance(x.e, ir.Index) and \
                            isinstance(x.e.arr, Var) and x.e.arr.key() in lf.arrays:
                        a = aid(va, lf, x.e.arr.key())
                        m = acc.setdefault(a, {})
                        # (its address taken says no width: PB_keys(i).Invert, 2 bytes at 8 of a
                        # 10-byte element, "did not fit" and the array stayed As Long)
                        w = x.size if id(x) not in addressed or x.size != 4 else \
                            (2 if x.off % 2 == 0 else 1)
                        m[x.off] = max(m.get(x.off, 0), w)
                        if n:
                            fixed.setdefault(a, {})[x.off] = n
    names = model.__dict__.setdefault("synth_names", {})
    groups = getattr(model, "synth_group", {})
    # arrays whose elements are only passed whole to UDT parameters (no field
    # read of their own): the group's Type (VBDoom cheat_powerup(i) to
    # cht_MakeSeq / cht_CheckCheat)
    for a, hs in holders.items():
        if a in acc or any(lf.udt.get(key) for lf, key in hs):
            continue
        for lf, key in hs:
            gid = groups.get((None, key) if key[0] == "global" else (lf.p.va, key))
            if gid is not None and names.get(gid):
                for lf2, key2 in hs:
                    lf2.udt[key2] = names[gid]
                break
    dyn = {a for a, hs in holders.items()
           if any((lf.arrays.get(key) or {}).get("dyn") for lf, key in hs)}

    def typed(a, hs):
        """Typed already - not counting a synthetic Type a procedure that did
        not know the dynamic array gave its SAFEARRAY descriptor (fields at
        0Ch / 14h: a target Form2 m40.pad2(5) for its lLbound)."""
        for lf, key in hs:
            u = lf.udt.get(key)
            if u and not (a in dyn and u.startswith("T_") and u != names.get(("elem", a))):
                return True
        return False
    # (what one round saw of the arrays inside an element is added to: the first round may have only
    # part of them.  a target's pairs at raw 0 and 4: a field and f4(0 To 99) of records, then
    # .f0(v18) on no array at all.  At one offset a stride with a bounds check beats one without)
    seen_farr = model.__dict__.setdefault("elem_farrays", {})
    regrown = set()
    for a, fa in farr.items():
        old = seen_farr.setdefault(a, {})
        for off, (st, ext, n) in fa.items():
            cur = old.get(off)
            if cur is None:
                old[off] = (st, ext, n)
                regrown.add(a)
            elif cur[0] == st:
                if ext > cur[1] or n > cur[2]:
                    old[off] = (st, max(cur[1], ext), max(cur[2], n))
                    regrown.add(a)
            elif n and not cur[2]:
                old[off] = (st, ext, n)
                regrown.add(a)
        fa.update(old)
    for a, fields in acc.items():
        hs = holders.get(a, [])
        if not hs:
            continue
        own = names.get(("elem", a))
        if typed(a, hs) and not (a in regrown and own and all(lf.udt.get(key) == own for lf, key in hs)):
            continue                    # typed already
        # elements passed to a struct parameter: the group's Type, so the
        # argument is of the parameter's very type (VBDoom events(i) to the
        # *_Responder(ev As event_t) procedures)
        gname = None
        for lf, key in hs:
            gid = groups.get((None, key) if key[0] == "global" else (lf.p.va, key))
            if gid is not None and names.get(gid):
                gname = names[gid]
                break
        if gname is not None:
            for lf, key in hs:
                lf.udt[key] = gname
            continue
        K = size.get(a) or 0
        if not fields and not farr.get(a):
            continue
        if K <= 8 and max(fields, default=0) == 0 and a not in record and not farr.get(a):
            continue                    # scalar elements
        # (a field at 4 or 0Ch is no part of a Variant: DarwinBots ScriptList(i).Action, 16 bytes
        # of four Strings)
        if K == 16 and a not in record and not any(o in (4, 12) for o in fields) or \
                any(info.get("vt") == 12 for lf, key in hs
                    for info in [lf.arrays.get(key) or {}]):
            continue                    # Variants: tag at 0, value at 8 (gh_json arr(2))
        if K and any(o >= K for o in fields):
            continue                    # the accesses do not fit the element size
        if K and record_like(fields, K):
            # (a dword read at the last Boolean of a 10-byte element: PB_keys stayed As Long)
            fields = {o: min(w, K - o) for o, w in fields.items()}
        if K and max([o + w for o, w in fields.items()], default=0) > K:
            continue
        name = names.get(("elem", a))
        if name is None:
            name = "T_%X_%d" % (K or max(list(fields) + list(farr.get(a, {}))) + 4,
                                len(names) + 1)
            names[("elem", a)] = name
        model.__dict__.setdefault("synth_farrays", {})[name] = farr.get(a, {})
        # (fields given Strings, or the Long a field looks like takes "" with a Type mismatch at
        # run time: ScriptList(i).Action = "", DarwinBots LoadScripts)
        strs = set()
        for lf, key in hs:
            b2 = bodies.get(lf.p.va, (None, None))[1]
            if b2 is not None:
                strs |= collect(lf, b2).strings.get(key, set())
        model.__dict__.setdefault("synth_strings", {})[name] = strs
        define(model, name, fields, K or None, fixed.get(a) or None)
        for lf, key in hs:
            lf.udt[key] = name


def group_structs(model, bodies):
    """Struct variables linked by being passed to a procedure's struct
    parameter: they are all of one type.  Union their field accesses."""
    parent = {}

    def find(x):
        while parent.get(x, x) != x:
            x = parent[x]
        return x

    def node(va, key):
        # a module variable is one variable however many procedures use it
        # (VBDoom cheat_amap: passed to cht_MakeSeq in one, cht_CheckCheat in another)
        return (None, key) if key[0] == "global" else (va, key)
    evs = {}
    for va, (lf, body) in bodies.items():
        if lf is not None:
            evs[va] = collect(lf, body)

    # parameters some caller gives a computed number: no record, whatever else it is given
    # (LoadOrganism path, tele.x + w / 2, y; LoadOrganism s, randomV.x, randomV.y made X As T_8_12)
    from .types import param_args as _param_args
    valued = set()

    def is_value(a):
        a = a.e if isinstance(a, AddrOf) else a
        if isinstance(a, ir.Call):
            if a.name in ("CSng", "CDbl", "CLng", "CInt", "CCur", "CByte", "CBool"):
                return True             # (a number by its conversion: CSng(x / 2))
            return isinstance(a.raw, int) and not apidb.layout(model.ret_types.get(a.raw) or "")
        return isinstance(a, BinOp) and any(
            isinstance(y, BinOp) and y.op == "/" or
            isinstance(y, Const) and isinstance(y.value, float) for y in a.walk())
    for va, (lf, body) in bodies.items():
        if lf is None:
            continue
        for _, ss, br in body:
            for st in ss:
                for e in (st.exprs() if hasattr(st, "exprs") else ()):
                    for x in e.walk():
                        if isinstance(x, ir.Call) and isinstance(x.raw, int):
                            for a, pi in _param_args(model.plists.get(x.raw), x.args):
                                if is_value(a):
                                    valued.add((x.raw, pi))
    for _ in range(4):                  # (a parameter of such a procedure handed on)
        n0 = len(valued)
        for va, ev in evs.items():
            plist = model.plists.get(va) or ()
            for i, prm in enumerate(plist):
                if (va, i) in valued:
                    k = ("arg", prm[0])
                    for link, keys in ev.passed.items():
                        if k in keys:
                            valued.add(link)
        if len(valued) == n0:
            break

    def scalar_param(callee, key, idx, seen=frozenset()):
        """The callee uses only the first dword of this ByRef parameter and
        hands it on to no procedure but as such, and some caller passes a field further
        into a struct for it, or a computed number: a scalar (AM_rotate x As Long gets &l.a.x and
        &l.b.x), whose callers' structs pass their first field.  Without such
        a caller it stays a struct (F_CastResponder reads only ev.typ)."""
        cev = evs.get(callee)
        if cev is None or not ((callee, idx) in valued or
                               any((callee, idx) in e.passed_field for e in evs.values())):
            return False
        acc = cev.access.get(key)
        if not (bool(acc) and set(acc) == {0} and acc[0] <= 4 and key not in cev.size and
                key not in cev.arrays and key not in cev.farrays and
                (bodies[callee][0].udt.get(key) or "T_").startswith("T_")):
            return False                # (a synthetic type there may be this grouping's own guess)
        for (c2, i2), ks in cev.passed.items():
            if key in ks and (c2, i2) not in seen:
                pl2 = model.plists.get(c2) or ()
                if i2 >= len(pl2) or not scalar_param(c2, ("arg", pl2[i2][0]), i2,
                                                      seen | {(callee, idx)}):
                    return False
        return True
    def extent(ev, key):
        """How far into the struct one procedure's accesses to key reach."""
        acc = ev.access.get(key, {})
        # (an inner array's raw offset is the field reached in its first
        # element: its last element, n - 1 strides on, ends that far again)
        ends = [o + max(w, 1) for o, w in acc.items()] + \
            [o + st * (max(n, 1) - 1) + max(ext, 1)
             for o, (st, ext, n) in ev.farrays.get(key, {}).items()]
        return max(ends, default=0)
    # Passing &x to a UDT parameter means the parameter's Type is x's, or a
    # Type x starts with (VBDoom STlib_initPercent passes p.n, an
    # st_number_t, to STlib_initNum; HUlib_resetIText it.l to
    # HUlib_clearTextLine).  Bounds on each variable's size decide which:
    # at least as far as its own accesses, its LSets and its callees' reach,
    # at most its exact size where known (a module UDT's, an array element's)
    # and its callers'.  A caller provably bigger than its callee
    # nests the callee's Type at its start; otherwise one Type for both.
    links = []                          # (caller node, callee node)
    for va, ev in evs.items():
        for (callee, idx), keys in ev.passed.items():
            plist = model.plists.get(callee) or ()
            if idx >= len(plist):
                continue
            ck = (callee, ("arg", plist[idx][0]))
            if scalar_param(callee, ck[1], idx):
                continue
            # (a set of keys: in the order of the string hashes, a target's
            # Proc_E8D010 a2() came out As TRec13 or T_30_4, and v3C() As TRec13
            # or Long, from run to run; this order gives TRec13)
            for k in sorted(keys, key=str, reverse=True):
                links.append((node(va, k), ck))
    # x = F() of a record Function in edx:eax: VB wants the very Type, so x's fields say what F's are
    # (t85recretno VSet only stores its parameters; VAdd reads what comes of it with fld dword)
    for va, (lf, body) in bodies.items():
        if lf is None:
            continue
        for _, ss, _br in body:
            for st in ss:
                if not (isinstance(st, ir.Assign) and isinstance(st.src, ir.Call) and
                        getattr(st.src, "small_rec", None) and isinstance(st.src.raw, int)):
                    continue
                clf = bodies[st.src.raw][0] if st.src.raw in bodies else None
                if clf is None or getattr(clf, "small_rec", None) is None:
                    continue
                d = st.dst
                if isinstance(d, Deref) and d.off == 0 and isinstance(d.e, Var):
                    d = d.e
                if isinstance(d, Var) and d.key() not in lf.arrays:
                    links.append((node(va, d.key()), (st.src.raw, ("local", clf.result_slot))))
    # a record's field passed to struct parameters: of one Type too (default1 K.lajit(i).mu, ArvotS
    # K.lajit(i).mu: T_3A_5 and T_20_4, so the field had none and neither call compiled)
    from .types import param_args

    def structish(callee, key):
        ev = evs.get(callee)
        return ev is not None and (len(ev.access.get(key, {})) >= 2 or ev.farrays.get(key))
    for va, (lf, body) in bodies.items():
        if lf is None:
            continue
        for _, ss, br in body:
            for st in ss:
                for e in (st.exprs() if hasattr(st, "exprs") else ()):
                    for x in e.walk():
                        if not (isinstance(x, ir.Call) and isinstance(x.raw, int)):
                            continue
                        plist = model.plists.get(x.raw)
                        for a, pi in param_args(plist, x.args):
                            aa = a.e if isinstance(a, AddrOf) else a
                            bo = base_off(aa) if isinstance(aa, Deref) else None
                            if bo is None:
                                continue
                            ow = bo[0].arr if isinstance(bo[0], ir.Index) else bo[0]
                            u = lf.udt.get(ow.key()) if isinstance(ow, Var) else None
                            ck = (x.raw, ("arg", plist[pi][0]))
                            if u and u.startswith("TRec") and structish(x.raw, ck[1]) and bo[1]:
                                links.append((("field", (u, bo[1])), ck))
    lb, ub = {}, {}
    for va, ev in evs.items():
        for key in set(ev.access) | set(ev.farrays):
            n = node(va, key)
            lb[n] = max(lb.get(n, 0), extent(ev, key))
        for key, size in ev.lset_size.items():
            # a lower bound only: an LSet at a variable's address may copy its
            # first field (AM_clipMline fl.a = tmp copies 8 of fl's 16 bytes)
            n = node(va, key)
            lb[n] = max(lb.get(n, 0), size)
        for (callee, idx), st in ev.passed_elem.items():
            plist = model.plists.get(callee) or ()
            if idx < len(plist):
                n = (callee, ("arg", plist[idx][0]))
                ub[n] = min(ub.get(n, st), st)
    for g, size in (getattr(model, "global_struct_hint", None) or {}).items():
        if g in getattr(model, "global_struct_reach", ()):
            continue                    # (how far a callee reads it says nothing of its size)
        n = (None, ("global", g))
        ub[n] = min(ub.get(n, size), size)
        if g in getattr(model, "global_struct_exact", ()):
            # (the whole is copied, or described: Killer(i) = EmptyKiller, LSet of 84 bytes.  ClearMove
            # .Killer1 doesn't make the variable a TMOVE; ChessBrainVB global_44A480.f1C)
            lb[n] = max(lb.get(n, 0), size)
    # a variable that gives a procedure's parameter its address at two offsets holds records of that
    # parameter's Type side by side: With Killer(ss + 2): ClearMove .Killer1: ClearMove .Killer2:
    # ClearMove .Killer3 is not one record of 84 bytes given to a parameter of 84 (ChessBrainVB).
    offsets = {}
    for va, (lf, body) in bodies.items():
        if lf is None:
            continue
        for _, ss, br in body:
            for st in ss:
                for e in (st.exprs() if hasattr(st, "exprs") else ()):
                    for x in e.walk():
                        if not (isinstance(x, ir.Call) and isinstance(x.raw, int)):
                            continue
                        for i, a in enumerate(x.args):
                            v = off = None
                            if isinstance(a, AddrOf) and isinstance(a.e, ir.Index) and isinstance(a.e.arr, Var):
                                v, off = a.e.arr, 0
                            elif isinstance(a, AddrOf) and isinstance(a.e, Deref) and \
                                    isinstance(a.e.e, ir.Index) and isinstance(a.e.e.arr, Var):
                                v, off = a.e.e.arr, a.e.off
                            elif isinstance(a, AddrOf) and isinstance(a.e, Deref) and isinstance(a.e.e, Var):
                                v, off = a.e.e, a.e.off
                            elif isinstance(a, AddrOf) and isinstance(a.e, Var):
                                v, off = a.e, 0
                            if v is not None and v.space in ("global", "local"):
                                offsets.setdefault((node(va, v.key()), (x.raw, i)), set()).add(off)
    composite = {}
    for (n, target), offs in offsets.items():
        if len(offs) >= 2:
            so = sorted(offs)
            gap = min(b - a for a, b in zip(so, so[1:]))
            if gap > 4 and so[0] % gap == 0 and all(o % gap == 0 for o in so):
                composite.setdefault(n, {})[target] = (gap, so)
    model.composite_records = composite
    for n, ts in composite.items():
        for (callee, idx), (gap, so) in ts.items():
            plist = model.plists.get(callee) or ()
            if idx < len(plist):
                k = (callee, ("arg", plist[idx][0]))
                ub[k] = min(ub.get(k, gap), gap)
    # an upper bound below what a variable's own accesses reach is wrong
    # (an element stride misread elsewhere): it does not count, and none
    # passes on below a callee's own reach
    own = dict(lb)
    ub = {n: u for n, u in ub.items() if u >= own.get(n, 0)}
    for _ in range(len(links) + 1):
        changed = False
        for c, k in links:
            if lb.get(k, 0) > lb.get(c, 0):
                lb[c] = lb[k]
                changed = True
            if c in ub and ub[c] < ub.get(k, ub[c] + 1) and ub[c] >= own.get(k, 0):
                ub[k] = ub[c]
                changed = True
        if not changed:
            break
    # one Type per group: a group's bounds are its members'; links from
    # variables of known size first (module UDTs: w_chat's 28 bytes then
    # tell HUlib_drawIText's parameter from HUlib_drawTextLine's 20)
    INF = 1 << 30
    glb, gub = {}, {}
    import os as _os
    import sys as _sys
    _dbg = _sys.stderr if _os.environ.get("VB6DEC_NESTDBG") else None   # (why a Type nests)

    def bounds(r):
        return glb.get(r, lb.get(r, 0)), gub.get(r, ub.get(r, INF))
    nested = {}                         # caller node -> (callee node, callee's size bound)
    absorb = {}                         # callee node -> [(caller node, bound)]
    order = sorted(links, key=lambda l: (0 if l[0][1][0] == "global" else
                                         1 if l[0] in ub else 2))
    for c, k in order:
        G, H = find(c), find(k)
        if G == H:
            continue
        (lg, ug), (lh, uh) = bounds(G), bounds(H)
        if lg > uh:
            if _dbg:
                print("nested %r -> %r: caller group at least %d, callee at most %d" % (
                    c, k, lg, uh), file=_dbg)
            nested[c] = (k, uh)
            absorb.setdefault(k, []).append((c, uh))
            parent.setdefault(k, k)         # (a group, for what callers add)
            glb[G] = max(lg, lh)
            continue
        parent[G] = H
        glb[H], gub[H] = max(lg, lh), min(ug, uh)
    model.nested_params = nested

    # a module-level UDT (decompile.global_struct_hints) is one variable for
    # every procedure that touches it: a group of its own, even unlinked, so
    # its Type has every field and array any of them uses
    for g in getattr(model, "global_struct_hint", None) or {}:
        parent.setdefault((None, ("global", g)), (None, ("global", g)))

    def evidence(x):
        """The Evidence objects that saw node x (every procedure, for a global)."""
        if x[0] is not None:
            return [evs[x[0]]] if x[0] in evs else []
        return [ev for ev in evs.values()
                if x[1] in ev.access or x[1] in ev.arrays or x[1] in ev.farrays]
    groups = {}
    for x in list(parent):
        groups.setdefault(find(x), []).append(x)
    for root, xs in list(groups.items()):
        if root not in xs:
            xs.append(root)
    model.synth_group, model.synth_group_acc = {}, {}
    model.synth_group_farrays = {}
    for xs in groups.values():
        gid = ("group",) + tuple(sorted(map(str, xs)))     # stable across prepasses
        acc, size, fixed, cy, arrays = {}, None, None, set(), {}
        farrays, strs = {}, set()
        # what callers do inside the part of their own struct they pass here
        inner = [(cn, lim) for x in xs for cn, lim in absorb.get(x, [])]
        for cn, lim in inner:
            for ev in evidence(cn):
                for off, sz in ev.access.get(cn[1], {}).items():
                    if off + sz <= lim:
                        acc[off] = max(acc.get(off, 0), sz)
        for x in xs:
            key = x[1]
            for ev in evidence(x):
                for off, (st, ext, n) in ev.farrays.get(key, {}).items():
                    old = farrays.get(off, (st, 0, 0))
                    farrays[off] = (st, max(old[1], ext), max(old[2], n))
                for off, sz in ev.access.get(key, {}).items():
                    acc[off] = max(acc.get(off, 0), sz)
                size = size or ev.size.get(key)
                fixed = fixed or ev.fixed.get(key)
                cy |= ev.cy.get(key, set())
                strs |= ev.strings.get(key, set())
                for off, t in ev.arrays.get(key, {}).items():
                    if t is not None or off not in arrays:
                        arrays[off] = t
        for x in xs:
            for ev in evidence(x):
                for off, w in getattr(ev, "forced", {}).get(x[1], {}).items():
                    if acc.get(off, 0) <= 4:
                        acc[off] = w        # passed ByRef As Integer somewhere
        if not acc:
            continue
        # a struct only when some member is one (fields at several offsets, or
        # a known size); Strings/Variants/Objects passed ByRef are pointers too
        def strong(x):
            return any(len(ev.access.get(x[1], {})) >= 2 or x[1] in ev.size
                       for ev in evidence(x))

        def scalar(x):
            if x[0] is None or x[0] == "field":
                return False                # (a global: judged by its accesses)
            lf = bodies[x[0]][0]
            return lf is None or x[1] in lf.variants or lf.objtype.get(x[1]) is not None or \
                (x[1][0] == "arg" and x[1][1] in lf.byval_strings)
        if not any(strong(x) for x in xs) or any(scalar(x) for x in xs):
            continue
        for x in xs:
            model.synth_group[x] = gid
        model.synth_group_acc[gid] = (acc, size, fixed, cy, arrays)
        model.synth_group_farrays[gid] = farrays
        model.__dict__.setdefault("synth_group_strings", {})[gid] = strs
        name = model.__dict__.get("synth_names", {}).get(gid)
        if name is not None:
            # named in an earlier prepass (its variables keep that name): the
            # layout as now known
            define(model, name, acc, size, fixed, cy, arrays)


def narrow_first_record(model, name, acc):
    """A caller gives a record starting with a record, and the code only reaches that one: it is
    the parameter's Type (VBDoom HUlib_clearTextLine(hu_textline_t) given a hu_itext_t's first
    field, the same address).  The caller's side prints the field (render)."""
    if not acc:
        return name
    recs = {r.name: r for r in model.__dict__.get("records", {}).values() if r is not None}
    r = recs.get(name)
    top = max(acc)
    while r is not None and r.fields.get(0) in recs and top < r.fsize.get(0, 0):
        name = r.fields[0]
        r = recs[name]
    return name


def caller_records(model, bodies):
    """A parameter with a made-up Type when every caller passes a record of the program's own:
    ByRef wants the same Type, so it is that one (a target Proc_E8D0F0 a2 touches no field, and
    came out T_1A0_61 for its caller's v5FC As TRec4: ByRef argument type mismatch)."""
    seen, vts = {}, {}
    for va, (lf, body) in bodies.items():
        if lf is None:
            continue
        ev = collect(lf, body)
        for (callee, idx), keys in ev.passed.items():
            plist = model.plists.get(callee) or ()
            if idx < len(plist):
                ck = (callee, ("arg", plist[idx][0]))
                seen.setdefault(ck, set()).update(
                    None if k in lf.arrays else lf.udt.get(k) for k in keys)
                vts.setdefault(ck, set()).update(
                    (lf.arrays.get(k) or {}).get("vt") if k in lf.arrays else None for k in keys)
    # an array parameter's elements, when the callers' arrays say and nothing here does: Proc_7E1470
    # stores Strings in a7(), v38(10) As String, through plain code the lift did not see as such
    for (callee, key), names in vts.items():
        lf = bodies.get(callee, (None, None))[0]
        info = lf.arrays.get(key) if lf is not None else None
        if info is not None and info.get("dyn") and len(names) == 1 and None not in names and \
                info.get("vt") is None and not info.get("udt"):
            info["vt"] = next(iter(names))
    for (callee, key), names in seen.items():
        lf = bodies.get(callee, (None, None))[0]
        if lf is None or key in lf.arrays or len(names) != 1:
            continue
        name = next(iter(names))
        if name and not name.startswith("T_") and (lf.udt.get(key) or "").startswith("T_"):
            # a record that starts with a record: what the parameter reaches may be only that one
            # (VBDoom HUlib_initTextLine(hu_textline_t) given itext.l: hu_itext_t's first field)
            recs = {r.name: r for r in model.__dict__.get("records", {}).values() if r is not None}
            lay = apidb.layout(lf.udt[key])
            own = lay[1] if lay else 0
            r = recs.get(name)
            while r is not None and r.fields.get(0) in recs and own <= r.fsize.get(0, 0):
                name = r.fields[0]
                r = recs[name]
            lf.udt[key] = name


def record_bodies(model, bodies):
    """UDTs with Strings (records.py): their field layout from every access
    any procedure makes to a variable or array element of the type."""
    from . import records
    recs = [r for r in model.__dict__.get("records", {}).values() if r is not None]
    from .types import elem_type
    accs = {}
    import os
    dbg = os.environ.get("VB6DEC_RECDBG", "")   # NAME:OFF (hex): who accesses it how wide
    dbg = (dbg.split(":")[0], int(dbg.split(":")[1], 16)) if ":" in dbg else None
    # What the loops over all procedures would find for a record, by record
    # name, in procedure order: one walk of the program, not one per record
    # (lf.udt, the bodies and the parameter lists do not change in here).
    # (a target: 28 s of a decompile for 90 records)
    names = {r.name for r in recs}
    i_acc, i_elem, i_obj, i_arg = {}, {}, {}, {}
    i_lset = []
    from .types import param_args

    def udt_of(lf, e):
        return lf.udt.get(getattr(e, "key", lambda: None)())
    # records among an object's member variables (a descriptor names them): the code reaches their
    # fields as plain member slots, With theTray: .hWnd = x is m40 = x (Shell_NotifyIcon's record
    # came out with only padding before its String * 64)
    mrec = {}
    byname = {r.name: r for r in recs}
    for va, (lf, body) in bodies.items() if recs else ():
        if lf is not None and lf.obj is not None:
            for key, u in lf.udt.items():
                if key[0] == "member" and isinstance(key[1], int) and u in byname and \
                        key not in lf.arrays:
                    mrec.setdefault(lf.obj.index, {})[key[1]] = byname[u]
    for va, (lf, body) in bodies.items() if recs else ():
        if lf is None:
            continue
        ev = collect(lf, body)      # (once per procedure: it does not read the records)
        for key, offs in ev.access.items():
            u = lf.udt.get(key)
            if u in names:
                i_acc.setdefault(u, []).append((lf, key, offs, ev))
        for b, r in (mrec.get(lf.obj.index, {}) if lf.obj is not None else {}).items():
            offs = {}
            for _, ss, br in body:
                for st in list(ss) + ([ExprStmt(br.cond)] if br is not None else []):
                    for e in (st.exprs() if hasattr(st, "exprs") else ()):
                        inner = set()
                        for x in e.walk():
                            if isinstance(x, Deref) and isinstance(x.e, AddrOf) and \
                                    isinstance(x.e.e, Var) and x.e.e.space == "member" and \
                                    isinstance(x.e.e.off, int):
                                # (through the slot With keeps its address in)
                                inner.add(id(x.e.e))
                                o = x.e.e.off + x.off
                                if b <= o < b + r.size:
                                    offs[o - b] = max(offs.get(o - b, 0), x.size or 4)
                            elif isinstance(x, Var) and x.space == "member" and id(x) not in inner \
                                    and isinstance(x.off, int) and b <= x.off < b + r.size:
                                w = max((z for z, _ in lf.sizes.get(x.key(), ())), default=4)
                                offs[x.off - b] = max(offs.get(x.off - b, 0), w)
            if offs:
                lf.udt.setdefault(("member", b), r.name)
                i_acc.setdefault(r.name, []).append((lf, ("member", b), offs, ev))
        for key, fa in ev.farrays.items():  # fixed arrays inside the record
            u = lf.udt.get(key)
            if u in names:
                i_elem.setdefault(u, []).append(("farray", fa))
        for key, d in ev.redim_elems.items():
            u = lf.udt.get(key)
            if u in names:
                for off, t in d.items():    # (ReDim rob(n).ga(k): rob(n).ga() As Boolean)
                    i_elem.setdefault(u, []).append(("redim", off, t))
        # r.Data = ba: an array field holds arrays of ba's element type
        for _, ss, _ in body:
            for st in ss:
                if not (isinstance(st, ir.Assign) and isinstance(st.src, Var)):
                    continue
                info = lf.arrays.get(st.src.key())
                bo = base_off(st.dst)
                if info is None or not info.get("dyn") or bo is None:
                    continue
                v, off, _ = bo
                u = udt_of(lf, v.arr if isinstance(v, ir.Index) else v)
                if u in names:
                    i_elem.setdefault(u, []).append(("elem", off, info))
        # a field a class's own method is called on holds that class
        # (SourceArchive As cZipArchive: Friend calls need the early-bound type)
        sets = {}
        for _, ss, _ in body:
            for st in ss:
                if isinstance(st, ir.Assign) and isinstance(st.dst, Var) and \
                        isinstance(st.src, ir.Deref):
                    sets.setdefault(st.dst.key(), []).append(st.src)
        for _, ss, br in body:
            for st in list(ss) + ([ExprStmt(br.cond)] if br is not None else []):
                for e in (st.exprs() if hasattr(st, "exprs") else ()):
                    for x in e.walk():
                        if not (isinstance(x, ir.Call) and isinstance(x.raw, int)):
                            continue
                        o = x.obj
                        if isinstance(o, Var) and len(sets.get(o.key(), ())) == 1:
                            o = sets[o.key()][0]    # Set t = r.f: t.Method ...
                        if not isinstance(o, ir.Deref):
                            continue
                        pr = model.procs.get(x.raw)
                        own = getattr(pr, "owner", None)
                        bo = base_off(o)
                        if own is None or own.kind == "Module" or bo is None:
                            continue
                        u = udt_of(lf, bo[0].arr if isinstance(bo[0], ir.Index) else bo[0])
                        if u in names:
                            i_obj.setdefault(u, []).append((bo[1], own.name))
        # a record copied whole into a field: the field is of that Type (rob(i).Mutables =
        # Specie(k).Mutables; "LSet allowed only on strings and user-defined types" for a field
        # the code otherwise only reads a Single of)
        for _, ss, _ in body:
            for st in ss:
                e = getattr(st, "e", None)
                if not (isinstance(e, ir.Call) and e.name == "LSet" and len(e.args) == 3 and
                        isinstance(e.args[2], Const) and isinstance(e.args[2].value, int)):
                    continue
                n = e.args[2].value
                ends = [(x.e if isinstance(x, AddrOf) else x) for x in e.args[:2]]
                ends = [lset_end(lf, x, model, bodies) for x in ends]
                i_lset.append((n, ends[0], ends[1]))
        # a field passed to a procedure's UDT parameter is of that UDT.  Made
        # again each time from the parameter lists as they are now, and only
        # where every such parameter agrees (an early round's ReadAll-style
        # Integer parameter left TRec11's h As Integer for good: 70 calls
        # passing Form1(a).h to Long parameters mismatched)
        for _, ss, _ in body:
            for st in ss:
                for e in (st.exprs() if hasattr(st, "exprs") else ()):
                    for x in e.walk():
                        if not (isinstance(x, ir.Call) and isinstance(x.raw, int)):
                            continue
                        plist = model.plists.get(x.raw)
                        for a, pi in param_args(plist, x.args):
                            t = plist[pi][2]
                            if (x.raw, ("arg", plist[pi][0])) in getattr(
                                    model, "proc_arrays", {}) and t:
                                # an array field passed to an array parameter:
                                # its element type (baExtra() As Byte)
                                aa = a.e if isinstance(a, ir.AddrOf) else a
                                bo2 = base_off(aa) if isinstance(aa, ir.Deref) else None
                                if bo2 is not None:
                                    ow = bo2[0].arr if isinstance(bo2[0], ir.Index) else bo2[0]
                                    clf = bodies.get(x.raw, (None, None))[0]
                                    cinfo = clf.arrays.get(("arg", plist[pi][0])) \
                                        if clf is not None else None
                                    # (its record, if it has one: DnaLen(dna() As block), given
                                    # rob(i).dna: f330C() As Variant was no array of those)
                                    et = (clf.udt.get(("arg", plist[pi][0])) or elem_type(cinfo)) \
                                        if cinfo else None
                                    u = udt_of(lf, ow)
                                    if u in names and et:
                                        i_arg.setdefault(u, []).append(("elem", bo2[1], et))
                                continue
                            lay = apidb.layout(t) if t else None
                            if lay is None and t in ("Date", "Currency", "Double", "Integer",
                                                     "Byte", "Single", "Long", "Boolean"):
                                # ByRef: exactly that type (a Long too, so that one
                                # odd Integer parameter does not decide alone)
                                lay = ([], apidb.SIZES[t])
                            if lay is None or not plist[pi][3]:
                                continue
                            if isinstance(a, ir.AddrOf) and isinstance(a.e, ir.Deref):
                                a = a.e                 # &x.f: the field
                            if isinstance(a, ir.AddrOf) and isinstance(a.e, ir.Index) and \
                                    not lay[0]:
                                v, off = a.e, 0         # &a(i) for a scalar: its first field
                            else:
                                ao = addr_off(a) if not isinstance(a, ir.Deref) else None
                                bo = base_off(a) if isinstance(a, ir.Deref) else None
                                v, off = (ao if ao else (bo[0], bo[1]) if bo else (None, None))
                            owner = v.arr if isinstance(v, ir.Index) else v
                            if owner is None or off is None:
                                continue
                            u = udt_of(lf, owner)
                            if u in names:
                                i_arg.setdefault(u, []).append(
                                    ("use", off, t, lay[1], lf, x.raw, pi))
    fixed_elems = fixed_string_elements(bodies)
    for rec in recs:
        if rec.name in fixed_elems:
            rec.etypes = {**(getattr(rec, "etypes", None) or {}), **fixed_elems[rec.name]}
        acc = {}
        rec.io_width = {}
        for lf, key, offs, ev in i_acc.get(rec.name, ()):
            rec.io_width.update(ev.forced.get(key, {}))   # (Put / Get / Input said how wide)
            for off, size in offs.items():
                if size == 4 and off % 4:
                    # no Long there: VB aligns one on 4 bytes.  An address taken for a ByRef
                    # Variant reads as 4 bytes whatever it points at (TextWidth(m3C(i, j).f6) of
                    # an Integer: a target A4's TRec18 got a Long at 6, every later field 2 bytes off)
                    size = 2 if off % 2 == 0 else 1
                acc[off] = max(acc.get(off, 0), size)
                if dbg and dbg == (rec.name, off):
                    import sys
                    print("RECDBG %s+%X %d in %s %s forced %s" % (
                        rec.name, off, size, lf.p.name, key,
                        ev.forced.get(key, {}).get(off)), file=sys.stderr)
            rec.cy = getattr(rec, "cy", set()) | ev.cy.get(key, set())
        for off in list(acc):
            if acc[off] == 4 and acc.get(off + 2):
                acc[off] = 2    # (another field in its upper half: the same kind of 4)
        for off, w in rec.io_width.items():
            if off in acc and w < acc[off] <= 4:
                acc[off] = w    # (one procedure's word store or Put against another's dword load)
        # a dynamic array of records of numbers, no parameter naming them: its elements' Type from
        # the fields the code reaches (.dna(i).value, Get alone: f18() As Variant, and no such field)
        delem, dsize = {}, {}
        for lf, key, offs, ev in i_acc.get(rec.name, ()):
            for off, f in ev.dyn_elem.get(key, {}).items():
                g = delem.setdefault(off, {})
                for o, w in f.items():
                    g[o] = max(g.get(o, 0), w)
            dsize.update(ev.redim_size.get(key, {}))
        for off, f in delem.items():
            if rec.fields.get(off) == "Array" and off not in rec.subs and \
                    any(o > 0 for o in f) and (rec.elem.get(off) or "Variant") == "Variant":
                # (a guess by its name, T_: a parameter's Type goes over it, and merges with its like)
                name = "T_%X_o%X" % (dsize.get(off) or max(o + (w or 2) for o, w in f.items()), off)
                define(model, name, {o: w or 2 for o, w in f.items()}, dsize.get(off))
                rec.elem[off] = name
        for it in i_elem.get(rec.name, ()):
            if it[0] == "farray":
                cur = rec.__dict__.setdefault("farrays", {})
                for off, (stride, ext, n) in it[1].items():
                    # one variable in both subscripts, an array in an array of records whose size
                    # the descriptor gives: .sp(i).mean(i) is i * 24 + i * 4, a stride of 28
                    hit = None
                    for b, (esize, dims, _, _) in getattr(rec, "fixarr", {}).items():
                        cnt = 1
                        for _, c in dims:
                            cnt *= c
                        if b <= off < b + esize * cnt and stride - esize in (1, 2, 4, 8) and \
                                b in rec.subs:
                            hit = (b, esize)
                    if hit is not None:
                        sub = records.model_record(model, rec.subs[hit[0]])
                        if sub is not None:
                            fs = sub.__dict__.setdefault("farrays", {})
                            o2 = (off - hit[0]) % hit[1]
                            old = fs.get(o2, (stride - hit[1], 0, 0))
                            fs[o2] = (stride - hit[1], max(old[1], ext), old[2])
                            continue
                    old = cur.get(off, (stride, 0, 0))
                    cur[off] = (stride, max(old[1], ext), max(old[2], n))
            elif it[0] == "redim":
                if rec.fields.get(it[1]) == "Array":
                    rec.elem[it[1]] = it[2]
            else:
                _, off, info = it
                if rec.fields.get(off) == "Array" and (info.get("vt") or info.get("size")):
                    rec.elem.setdefault(off, elem_type(info))
        accs[rec.va] = acc
        for off, name in i_obj.get(rec.name, ()):
            if rec.fields.get(off) == "Object":
                rec.fields[off] = name
        for off in getattr(rec, "ufield_added", ()):
            rec.fields.pop(off, None)
            rec.fsize.pop(off, None)
        rec.ufield_added = set()
        ucands = {}
        for it in i_arg.get(rec.name, ()):
            if it[0] == "elem":
                if rec.fields.get(it[1]) == "Array":
                    # (a record over a number the bytes suggested: rob(i).dna() As Long, passed to
                    # DnaLen(a1() As T_4_40) and read .f2)
                    if apidb.layout(it[2]) and apidb.layout(it[2])[0]:
                        rec.elem[it[1]] = it[2]
                    else:
                        rec.elem.setdefault(it[1], it[2])
                continue
            _, off, t, size, lf, raw, pi = it
            if off in rec.fields or t == rec.name or off == 0 and size >= rec.size:
                continue        # (the record itself, passed whole)
            ucands.setdefault(off, set()).add((t, size))
            if dbg and dbg == (rec.name, off):
                import sys
                print("RECDBG %s+%X passed in %s to %s a%d As %s" % (
                    rec.name, off, lf.p.name, model.procs[raw].name,
                    pi + 1, t), file=sys.stderr)
        for off, c in list(ucands.items()):
            # one layout under several names: merged later (writer.merge_types), and a guess of a
            # first round (a1 As Single, the record's first field) doesn't count against it
            ud = sorted(x for x in c if x[0].startswith(("T_", "TRec")))
            if len(c) > 1 and ud and len({x[1] for x in ud}) == 1 and \
                    len({tuple((o, ft) for o, n, ft in (apidb.layout(x[0]) or ([], 0))[0])
                         for x in ud}) == 1:
                ucands[off] = {ud[0]}
        rec.ufields = {off: next(iter(c)) for off, c in ucands.items() if len(c) == 1}
    element_accesses(recs, accs)
    lset_fields(recs, i_lset)
    # an embedded UDT's fields read through the outer record (v.Data.ftLastWriteTime)
    byva = {r.va: r for r in recs}
    for rec in recs:
        for soff, sva in getattr(rec, "subs", {}).items():
            sub = byva.get(sva)
            if sub is None:
                continue
            sacc = accs.setdefault(sva, {})
            for off, size in accs.get(rec.va, {}).items():
                if soff <= off < soff + sub.size:
                    sacc[off - soff] = max(sacc.get(off - soff, 0), size)
            for off in getattr(rec, "cy", set()):
                if soff <= off < soff + sub.size:
                    sub.cy = getattr(sub, "cy", set()) | {off - soff}
            for off, tu in list(getattr(rec, "ufields", {}).items()):
                if soff <= off < soff + sub.size:
                    sub.ufields = getattr(sub, "ufields", {})
                    sub.ufields[off - soff] = tu
                    del rec.ufields[off]
    for rec in recs:
        acc = accs.get(rec.va, {})
        for off, (t, size) in getattr(rec, "ufields", {}).items():
            nxt = min([o for o in rec.fields if o > off] + [rec.size])
            # (not over a field the descriptor lists: an Integer parameter given
            # the Byte before a String * 21 hid the string, TRec15.fD; nor
            # wider than every access the code makes to it: a target's Byte
            # settings in TRec21 passed to a Proc_8D8880 still As Long then)
            if off not in rec.fields and off + size <= nxt and \
                    not (t in apidb.SIZES and acc.get(off) and acc[off] < size):
                rec.fields[off] = t         # an API / private UDT inside the record
                rec.fsize[off] = size
                rec.ufield_added.add(off)
                # its first field's width, if the code only shows it from out here (Get #n, ,
                # rob(i).Mutables.Mutations, a Boolean: f0 As Long took the file 2 bytes off)
                sa = model.__dict__.get("synth_args", {}).get(t)
                w0 = getattr(rec, "io_width", {}).get(off)
                if sa is not None and w0 and sa[0].get(0, size) > w0 and w0 < size:
                    # (and the Types of its very layout, which are this one to writer.merge_types)
                    shape = [(o, ft) for o, _, ft in apidb.layout(t)[0]]
                    for x, xa in list(model.__dict__.get("synth_args", {}).items()):
                        lay = apidb.layout(x)
                        if lay and xa[0].get(0, size) > w0 and \
                                [(o, ft) for o, _, ft in lay[0]] == shape:
                            a0 = dict(xa[0])
                            a0[0] = w0
                            define(model, x, a0, *xa[1:])
    for rec in recs:
        rec.access = accs.get(rec.va, {})     # (nest_types registers it again)
        records.register(rec, rec.access)


def lset_end(lf, x, model, bodies):
    """What one end of a whole record copy is: ("type", Type) for a variable or a Function's result
    of a known Type, ("field", record name, offset) for a part of a record; else None.  Offsets
    inside a fixed array of records count for its elements' Type."""
    if isinstance(x, Var):
        t = lf.udt.get(x.key())
        return ("type", t) if t else None
    if isinstance(x, ir.Call) and isinstance(x.raw, int):
        t = model.ret_types.get(x.raw) if hasattr(model, "ret_types") else None
        clf = bodies.get(x.raw, (None, None))[0]
        if clf is not None and isinstance(clf.result_slot, int):
            t = clf.udt.get(("local", clf.result_slot)) or t
        return ("type", t) if t and apidb.layout(t) else None
    off = 0
    while isinstance(x, Deref) and isinstance(x.e, ir.Index) and getattr(x.e.arr, "inline", None):
        off += x.off
        x = x.e
    if isinstance(x, ir.Index) and getattr(x.arr, "inline", None):
        off += x.arr.off
        x = x.arr.e
    if isinstance(x, Deref):
        bo = base_off(x)
        if bo is None:
            return None
        off += bo[1]
        x = bo[0]
    if isinstance(x, ir.Index):
        x = x.arr
    t = lf.udt.get(x.key()) if isinstance(x, Var) else None
    return ("field", t, off) if t else None


def lset_fields(recs, pairs):
    """Fields a whole record is copied to or from take its Type; see record_bodies."""
    byname = {r.name: r for r in recs}

    def locate(end):
        """(record, offset inside it), through arrays of records."""
        _, name, off = end
        rec = byname.get(name)
        while rec is not None:
            for b, (ename, esize) in getattr(rec, "arr_of", {}).items():
                dims = (getattr(rec, "fixarr", None) or {}).get(b, (0, ()))[1]
                count = 1
                for _, c in dims:
                    count *= c
                if esize and b <= off < b + count * esize and ename in byname:
                    rec, off = byname[ename], (off - b) % esize
                    break
            else:
                return rec, off
        return None

    def type_of(end, n):
        if end is None:
            return None
        if end[0] == "type":
            lay = apidb.layout(end[1])
            return end[1] if lay and lay[1] == n else None
        at = locate(end)
        if at is None:
            return None
        rec, off = at
        t = rec.fields.get(off)
        if not t and off in getattr(rec, "ufields", {}):
            t = rec.ufields[off][0]
        lay = apidb.layout(t) if t else None
        if lay and lay[1] == n:
            return t
        return None
    for _ in range(4):
        changed = False
        for n, a, b in pairs:
            if n <= 4:
                continue                # (a Long's worth: no telling)
            ta, tb = type_of(a, n), type_of(b, n)
            for end, t, other in ((a, tb, ta), (b, ta, tb)):
                if t is None or other is not None or end is None or end[0] != "field":
                    continue
                at = locate(end)
                if at is None:
                    continue
                rec, off = at
                if off in rec.fields or (off == 0 and n >= rec.size):
                    continue
                rec.__dict__.setdefault("ufields", {})[off] = (t, n)
                changed = True
        if not changed:
            break


def element_accesses(recs, accs):
    """What the code does at a raw offset inside a fixed array of records in a record is about the
    element's Type: SimOpts.Specie(i).qty is [global + i * 16Ch + 5Ch], so a field at 44h of
    datispecie; Specie(i).Skin(j) has stride 2, an array in the element.  (TRec2's numbers were
    padding: f18(i).pad3(21), the largest cause of errors in DarwinBots.)  An element with no
    descriptor of its own (records only of numbers) has no Type to tell here."""
    byname = {r.name: r for r in recs}
    guess = {}
    for rec in recs:
        fa = rec.__dict__.get("farrays") or {}
        for b, (ename, esize) in getattr(rec, "arr_of", {}).items():
            sub = byname.get(ename)
            if sub is None or not esize:
                continue
            dims = (getattr(rec, "fixarr", None) or {}).get(b, (0, ()))[1]
            count = 1
            for _, n in dims:
                count *= n
            total = count * esize
            sacc = accs.setdefault(sub.va, {})
            for off, (stride, ext, n) in list(fa.items()):
                if not b <= off < b + total:
                    continue
                rel = (off - b) % esize
                if stride == esize:
                    if ext:
                        sacc[rel] = max(sacc.get(rel, 0), ext)
                    elif rel:
                        guess.setdefault(sub.va, set()).add(rel)
                    del fa[off]
                elif stride < esize and esize % stride == 0:
                    cur = sub.__dict__.setdefault("farrays", {})
                    old = cur.get(rel, (stride, 0, 0))
                    if old[0] == stride:
                        cur[rel] = (stride, max(old[1], ext), max(old[2], n))
                    del fa[off]
    for va, rels in guess.items():
        # only its address seen: as wide as its place allows
        sacc = accs.setdefault(va, {})
        for rel in rels:
            sacc.setdefault(rel, 4 if rel % 4 == 0 else 2 if rel % 2 == 0 else 1)


def fixed_string_elements(bodies):
    """{record Type: {raw offset: "String * n"}} for arrays inside a record whose
    elements are read or stored as fixed strings (a target TRec23.fF(0 To 14),
    __vbaLsetFixstr(10, &rec.fF(i), s) at a stride of 20 bytes, was a Type of
    20 Bytes: "Type mismatch" on the assignment)."""
    out = {}

    def note(lf, e, n):
        ix = e.e if isinstance(e, Deref) and e.off == 0 else e
        f = ix.arr if isinstance(ix, ir.Index) else None
        if not n or not isinstance(f, Deref) or getattr(f, "inline", None) != 2 * n:
            return
        b = f.e.arr if isinstance(f.e, ir.Index) else f.e
        t = lf.udt.get(b.key()) if isinstance(b, Var) else None
        if t:
            out.setdefault(t, {})[f.off] = "String * %d" % n

    for lf, body in bodies.values():
        if lf is None:
            continue
        for _, ss, br in body:
            for st in ss:
                if isinstance(st, ir.Assign):
                    note(lf, st.dst, getattr(st, "fixstr", None))
                for e in getattr(st, "exprs", lambda: ())():
                    for x in e.walk():
                        note(lf, x, getattr(x, "fixstr", None))
    return out


def define(model, name, acc, size=None, fixed=None, cy=None, arrays=None):
    """Register synthetic Type name, remembering what it was made of (nest_types
    may rebuild it with nested Types)."""
    model.__dict__.setdefault("synth_args", {})[name] = (dict(acc), size, fixed, cy, arrays)
    subs = model.__dict__.get("synth_subs", {}).get(name)
    farrays = model.__dict__.get("synth_farrays", {}).get(name)
    etypes = model.__dict__.get("synth_etypes", {}).get(name)
    strs = model.__dict__.get("synth_strings", {}).get(name)
    apidb.add_type(name, synth_body(name, acc, size, fixed, cy, arrays, subs, farrays,
                                    etypes, strs))


def nest_types(model, bodies):
    """A whole UDT copied into a part of another (LSet / __vbaCopyBytes) is a
    field of that UDT there: fl.b = tmp copies an fpoint_t into the fline_t
    at +8 (VBDoom AM_clipMline: "LSet a2.f8 = v18" was not VB).  Synthetic
    Types are rebuilt with the nested field."""
    args = getattr(model, "synth_args", {})
    subs = model.__dict__.setdefault("synth_subs", {})
    for va, (lf, body) in bodies.items():
        if lf is None:
            continue
        for _, ss, br in body:
            for st in ss:
                e = getattr(st, "e", None)
                if not (isinstance(e, ir.Call) and e.name == "LSet" and len(e.args) == 3 and
                        isinstance(e.args[2], Const) and isinstance(e.args[2].value, int)):
                    continue
                n = e.args[2].value

                def end(a):
                    """(variable, offset) of an LSet operand; an element or a
                    part of one counts for its array (lf.udt names the element
                    type)."""
                    if isinstance(a, AddrOf) and isinstance(a.e, ir.Index) and \
                            isinstance(a.e.arr, Var):
                        return a.e.arr, 0
                    if isinstance(a, AddrOf) and isinstance(a.e, Deref) and \
                            isinstance(a.e.e, ir.Index) and isinstance(a.e.e.arr, Var):
                        return a.e.e.arr, a.e.off
                    return addr_off(a)
                d, sr = end(e.args[0]), end(e.args[1])
                if d is None or sr is None:
                    continue
                for (x, k), (y, k2) in ((d, sr), (sr, d)):
                    if k2:
                        continue            # the copied UDT must be whole
                    tx, ty = lf.udt.get(x.key()), lf.udt.get(y.key())
                    if not tx or not ty or tx == ty or tx not in args:
                        continue
                    lx, ly = apidb.layout(tx), apidb.layout(ty)
                    if lx is None or ly is None or ly[1] != n or k + n > lx[1] or \
                            (k == 0 and lx[1] == n):
                        continue
                    subs.setdefault(tx, {})[k] = ty
    # a struct passed whole to a smaller struct parameter starts with it
    # (group_structs: STlib_initPercent's p.n is STlib_initNum's st_number_t)
    def type_of(va, key):
        """A variable's Type; a module variable's as any procedure knows it."""
        if va is not None:
            lf = bodies.get(va, (None, None))[0]
            return lf.udt.get(key) if lf is not None else None
        for lf, _ in bodies.values():
            if lf is not None and lf.udt.get(key):
                return lf.udt[key]
        return None
    for (va, key), ((callee, ckey), q) in getattr(model, "nested_params", {}).items():
        inner = bodies.get(callee, (None, None))[0]
        tx = type_of(va, key)
        ty = inner.udt.get(ckey) if inner is not None else None
        if tx and ty and tx != ty and tx in args:
            lx, ly = apidb.layout(tx), apidb.layout(ty)
            if lx is not None and ly is not None and ly[1] < lx[1]:
                subs.setdefault(tx, {})[0] = ty
    # (the same at each offset it gives the parameter: Killer1, Killer2, Killer3)
    for (va, key), ts in getattr(model, "composite_records", {}).items():
        tx = type_of(va, key)
        if not tx or tx not in args:
            continue
        for (callee, idx), (gap, so) in ts.items():
            inner = bodies.get(callee, (None, None))[0]
            plist = model.plists.get(callee) or ()
            if inner is None or idx >= len(plist):
                continue
            ty = inner.udt.get(("arg", plist[idx][0]))
            lx, ly = apidb.layout(tx), apidb.layout(ty) if ty else None
            if ty and ty != tx and lx is not None and ly is not None:
                for o in so:
                    if o + ly[1] <= lx[1]:
                        subs.setdefault(tx, {})[o] = ty
    # a field's address given to a parameter of a record Type: a field of that Type (SetMove .BestMove,
    # m of TMovePicker, a TMOVE at +8; ChessBrainVB)
    for va, (lf, body) in bodies.items():
        if lf is None:
            continue
        for _, ss, br in body:
            for st in ss:
                for e in getattr(st, "exprs", lambda: ())():
                    for x in e.walk():
                        if not (isinstance(x, ir.Call) and isinstance(x.raw, int)):
                            continue
                        plist = model.plists.get(x.raw) or ()
                        clf = bodies.get(x.raw, (None, None))[0]
                        if clf is None:
                            continue
                        for i, a in enumerate(x.args):
                            if i >= len(plist) or not isinstance(a, AddrOf) or not isinstance(a.e, Deref) or \
                                    not a.e.off:
                                continue
                            b = a.e.e.arr if isinstance(a.e.e, ir.Index) else a.e.e
                            tx = lf.udt.get(b.key()) if isinstance(b, Var) else None
                            ty = clf.udt.get(("arg", plist[i][0]))
                            if not tx or not ty or tx == ty or tx not in args:
                                continue
                            lx, ly = apidb.layout(tx), apidb.layout(ty)
                            if lx is not None and ly is not None and ly[1] > 4 and a.e.off + ly[1] <= lx[1]:
                                subs.setdefault(tx, {})[a.e.off] = ty
    # an array inside a UDT whose elements go to a UDT parameter: elements
    # of that Type (HUlib_initSText passes s.l(i) to HUlib_initTextLine)
    etypes = model.__dict__.setdefault("synth_etypes", {})
    recs = {r.name: r for r in model.__dict__.get("records", {}).values() if r is not None}
    touched = set()
    for va, (lf, body) in bodies.items():
        if lf is None:
            continue
        for _, ss, br in body:
            for st in ss:
                for e in getattr(st, "exprs", lambda: ())():
                    for x in e.walk():
                        if not (isinstance(x, ir.Call) and isinstance(x.raw, int)):
                            continue
                        plist = model.plists.get(x.raw) or ()
                        clf = bodies.get(x.raw, (None, None))[0]
                        for i, a in enumerate(x.args):
                            if not (isinstance(a, AddrOf) and isinstance(a.e, ir.Index) and
                                    getattr(a.e.arr, "inline", None)) or i >= len(plist) \
                                    or clf is None:
                                continue
                            f = a.e.arr
                            b = f.e.arr if isinstance(f.e, ir.Index) else f.e
                            tx = lf.udt.get(b.key()) if isinstance(b, Var) else None
                            ty = clf.udt.get(("arg", plist[i][0]))
                            if tx and ty and tx != ty:
                                etypes.setdefault(tx, {})[f.off] = ty
                                touched.add(tx)
    from . import records
    for tx in touched:
        if tx in recs:
            recs[tx].etypes = etypes[tx]
            records.register(recs[tx], getattr(recs[tx], "access", None))
        elif tx in args and tx not in subs:
            subs[tx] = {}
    farr = model.__dict__.get("synth_farrays", {})
    sstr = model.__dict__.get("synth_strings", {})
    for name, nested in subs.items():
        if name in args:
            acc, size, fixed, cy, arrays = args[name]
            # (a nesting an early round's wider guess made: nothing is left of the outer Type
            # but the inner one, T_6_26 = {f0 As T_4_52}.  They are one Type)
            whole = (apidb.layout(nested.get(0)) or ((), 0, 0))[1] if nested.get(0) else 0
            if whole and len(nested) == 1 and not farr.get(name) and not fixed and (
                    size and size <= whole or not size and
                    max([o + max(w, 1) for o, w in acc.items()], default=0) <= whole):
                nested = {}
                subs[name] = nested
            apidb.add_type(name, synth_body(name, acc, size, fixed, cy, arrays, nested,
                                            farr.get(name), etypes.get(name), sstr.get(name)))


def synth_body(name, acc, size=None, fixed=None, cy=None, arrays=None, subs=None,
               farrays=None, etypes=None, strs=None):
    """Type definition covering the observed fields, padded between them;
    fixed: {offset: n} for String * n fields (2n bytes); arrays: {offset:
    element type} for dynamic array fields (a SAFEARRAY*, 4 bytes);
    farrays: elements of fixed arrays inside it (records.inline_arrays)."""
    fixed = fixed or {}
    arrays = arrays or {}
    subs = subs or {}
    inline = {}
    if farrays:
        from .records import inline_arrays
        total = size or max([o + max(w, 1) for o, w in acc.items()] +
                            [f + st * max(n, 1) for f, (st, _, n) in farrays.items()])
        inline = inline_arrays(name, total, farrays, set(fixed) | set(arrays) | set(subs),
                               acc, etypes)
        acc = dict(acc)
        for off in inline:
            acc.setdefault(off, 4)
    if arrays or subs:
        acc = dict(acc)
        for off in arrays:
            acc[off] = 4
        for off, t in subs.items():         # a nested UDT replaces the fields inside it
            n = (apidb.layout(t) or ((), 4, None))[1]
            for o in [o for o in acc if off <= o < off + n]:
                del acc[o]
            acc[off] = n
    body = ["Type %s" % name]
    offs = sorted(acc)
    pad = 0
    pos = 0
    # a size from Put's descriptor is where the last field ends, and it lists the holes (records.io_gaps): 3 Bytes,
    # a Long and 2 Bytes are 10 there, 12 in memory and Len 9.  So a size not a multiple of 4 is no sign of no
    # Long then (a target's flags and a colour: f4 As Byte, f5, f6 As Integer, and RGB() overflowed)
    from .records import IO_GAPS
    gaps = IO_GAPS.get(size)
    for off in offs:
        if off > pos == 0 and off in (2, 4) or off > 4 and pos == 0:
            # nothing names the first field when the code uses it through the element:
            # m40(m3C(h)), m3C(i) = n.  As padding those were m3C(i).pad1(1), one byte of a
            # record number (a target Form2 Add1)
            w = 2 if off == 2 or size and size % 4 else 4
            body.append("    f0 As %s" % ("Integer" if w == 2 else "Long"))
            pos = w
        if off > pos and gaps and pos in gaps:
            pos = off                   # (a hole of VB's alignment, no bytes on disk: no field of ours)
        if off > pos:
            pad += 1
            body.append("    pad%d(1 To %d) As Byte" % (pad, off - pos))
            pos = off
        if off < pos:
            continue                    # inside the previous field (a fixed string)
        fsize = acc[off]
        if off in inline:
            stride, n, et = inline[off]
            body.append("    f%X(0 To %d) As %s" % (off, n - 1, et))
            pos = off + n * stride
            continue
        if off in fixed:
            body.append("    f%X As String * %d" % (off, fixed[off]))
            pos = off + 2 * fixed[off]
            continue
        if off in arrays:
            body.append("    f%X() As %s" % (off, arrays[off] or "Long"))
            pos = off + 4
            continue
        if off in (strs or ()) and acc[off] <= 4:
            body.append("    f%X As String" % off)     # (a String stored there)
            pos = off + 4
            continue
        if off in subs:
            body.append("    f%X As %s" % (off, subs[off]))
            pos = off + fsize
            continue
        if size and off + fsize > size > off:
            fsize = size - off          # (a dword read at 2 of a 4-byte block: its two words)
        later = [o for o in offs if o > off]
        if fsize == 8 and later and later[0] == off + 4 and acc[later[0]] <= 4 and \
                (len(later) < 2 or later[1] >= off + 8):
            later = later[1:]           # the upper half of the 8-byte value, read alone
        if later and later[0] < off + fsize:
            fsize = later[0] - off      # the next field starts inside: not that wide
        # VB aligns a Long/Double field to 4 and an Integer to 2: a dword
        # access at 0xA is two Integers (gh_zip FTime/FDate copied at once)
        o, end = off, off + max(fsize, 1)
        while o < end:
            left = end - o
            # (no 4-aligned field in a Type of 6 bytes: it would be 8.  An address read as a dword
            # made block2's first field a Long, and T_6_39 no Type of the others of its array)
            if size and size % 4 and left >= 4 and gaps is None:
                left = 2 if size % 2 == 0 else 1
            if left >= 8 and o % 4 == 0 and fsize == 8:
                t, n = ("Currency" if o in (cy or ()) else "Double"), 8
            elif left >= 4 and o % 4 == 0:
                t, n = "Long", 4
            elif left >= 2 and o % 2 == 0:
                t, n = "Integer", 2
            else:
                t, n = "Byte", 1
            body.append("    f%X As %s" % (o, t))
            o += n
        pos = end
    if size is not None and size > pos:
        body.append("    pad%d(1 To %d) As Byte" % (pad + 1, size - pos))
    if len(body) == 1:
        # (nothing known but an array with no room: VB takes no Type without members)
        body.append("    f0 As Long")
    body.append("End Type")
    from .records import drop_alignment_pads
    return drop_alignment_pads(body)
