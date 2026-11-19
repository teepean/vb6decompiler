"""Print the structured IR as Visual Basic source."""
import copy
import os
import sys
from . import ir, structure
from .ir import (Const, Str, Var, Me, AddrOf, Deref, BinOp, UnOp, Call, Prop,
                 Index, Field, Unknown, Assign, ExprStmt, Free, OnError)
from .lift import CallStmt
from .machine import SP, IatSlot, Hi, FSW
from .structure import Seq, Block, If, Loop, Goto, Label, Exit, For, ForEach, Handlers, \
    ExitLoop, Select

DEREF_LOG = bool(os.environ.get("VB6DEC_DEREFLOG"))     # (a value read through a pointer no fold explained)
KEY = {"=", "<>", "<", ">", "<=", ">=", "And", "Or", "Xor", "Not", "Is", "Like", "Mod"}


class Names:
    """Local, argument and global names for one procedure."""

    def __init__(self, lf, types=None):
        self.lf = lf
        self.types = types
        # which argument dwords the code touches (Var(arg, off)) and which have
        # their address taken: a ByVal Variant is 16 bytes used as such
        self.arg_words, self.arg_addr = None, set()
        self.arg_values = set()     # argument dwords used as values, not only passed on
        body = getattr(types, "body", None) if types is not None else None
        if body is not None:
            from .machine import SP as _SP
            self.arg_words = set()
            for _, ss, br in body:
                exprs = [e for st in ss for e in getattr(st, "exprs", lambda: ())()]
                if br is not None:
                    exprs.append(br.cond)
                for e in exprs:
                    passed = {id(a) for x in e.walk() if isinstance(x, Call) for a in x.args}
                    for x in e.walk():
                        if isinstance(x, Var) and x.space == "arg" and isinstance(x.off, int):
                            self.arg_words.add(x.off)
                            if id(x) not in passed:
                                self.arg_values.add(x.off)
                        if isinstance(x, AddrOf) and isinstance(x.e, Var) and \
                                x.e.space == "arg" and isinstance(x.e.off, int):
                            self.arg_addr.add(x.e.off)
                        if isinstance(x, _SP) and x.off >= 0:
                            self.arg_addr.add(x.off + 4)
        self.locals = {}
        self.args = {}
        self.regs = {}
        self.tmps = {}
        self.stks = {}
        self.n = 0
        self.loops = []          # kinds of the loops being rendered (Exit Do / For)
        self.proc_name = lf.p.truth[1] if getattr(lf.p, "truth", None) else lf.p.name
        if getattr(lf.p, "member", None) is not None and lf.p.member.name:
            self.proc_name = lf.p.member.name       # "Value", not the map's Value__Get
        self.member_names = {}
        om = lf.m.members.get(lf.obj.index) if lf.obj is not None else None
        if om is not None:
            self.member_names = {v.offset: v.name for v in om.vars if v.name}
        self.params = self.param_names()

    def param_names(self):
        """Event handlers get their parameter names from the type library;
        other procedures get a1..an in stack order."""
        out = {}
        self.plist = []          # (off, name, type, byref)
        self.known = []          # per parameter: the type has evidence (not the Long default)
        lf = self.lf
        ev = getattr(lf.p, "event", None)
        off = 8 if lf.obj is not None else 4
        if ev is not None:
            why = getattr(lf.p, "why", None)
            if why and why[0] == "event" and getattr(why[1], "array_events", False):
                # a control array's event: Index comes first (model.control_class)
                out[off] = "Index"
                self.plist.append((off, "Index", "Integer", True))
                off += 4
            from .typelib import by_reference, type_name
            for p in ev.params:
                t = p.type
                byref, inner = by_reference(t, getattr(lf.p, "event_dispatch", False))
                tn = type_name(inner if inner is not None else t)
                tn = {"_PropertyBag": "PropertyBag"}.get(tn, tn)
                out[off] = p.name or "a%d" % (off // 4)
                self.plist.append((off, out[off], tn, byref))
                off += 4
            return out
        m = getattr(lf.p, "member", None)
        if m is not None:
            # a Public member: names, types and passing from its descriptor
            for prm in m.params:
                t = member_type(lf.m, prm.type, prm.cls)
                byref = prm.byref or prm.array
                if prm.array:
                    lf.m.__dict__.setdefault("param_arrays", set()).add((lf.p.va, off))
                out[off] = prm.name
                self.known.append(True)
                self.plist.append((off, prm.name, t, byref))
                off += 4 * (1 if byref else {"Double": 2, "Currency": 2, "Date": 2,
                                             "Variant": 4}.get(t, 1))
            return out
        end = 4 + lf.p.ret_pop
        if lf.obj is not None and lf.result_slot is not None:
            end -= 4                                # out-pointer for the result
        n = 0
        if getattr(lf, "hidden_ret", False):
            off += 4                            # the result pointer the caller passes
        while off < end:
            n += 1
            key = ("arg", off)
            if off in getattr(lf, "varptr_args", ()) and off not in lf.byref and \
                    (self.types.var_type(key) if self.types else None) not in (
                        "String", "Object", "Variant"):
                lf.byref.add(off)               # VarPtr(param): the pointer it was passed
            byref = off in lf.byref
            t = lf.udt.get(key) or (self.types.var_type(key) if self.types else None)
            if off in lf.byval_strings:
                t = "String"
            if off in getattr(lf, "variant_params", ()):
                t = "Variant"                   # passed on to a ByRef Variant as is
            if key in lf.late_bound and t != "Variant" and key not in lf.udt:
                t = "Object"                    # late-bound calls go through it
            if t and key not in lf.udt and key not in lf.arrays:
                from . import apidb
                if apidb.layout(t) is not None:
                    lf.udt[key] = t             # declared As that UDT: name its fields
            if lf.udt.get(key) and not byref and key not in lf.arrays:
                byref = True                    # a UDT parameter is always ByRef (fields
                lf.byref.add(off)               # only read further in: HUlib_addLineToSText)
            width = 4
            if not byref and t in ("Double", "Currency", "Date"):
                width = 8
            if not byref and t == "Variant":
                if off + 16 > end or (
                        off not in getattr(lf, "byval_variants", ()) and
                        self.arg_words is not None and off + 8 not in self.arg_words and
                        off not in self.arg_addr and off in self.arg_words and
                        off not in self.arg_values):
                    # only ever passed on as a pointer, its other 12 bytes never
                    # touched: a ByRef Variant (gh_dict dict_ReplaceKeyValue); not
                    # when its first dword is computed with (t01m VariantTest
                    # w = v + 1, the ByVal Variant read as a whole)
                    byref = True
                    lf.byref.add(off)
                else:
                    width = 16
            name = "a%d" % n
            out[off] = name
            if key in lf.udt:
                byref = True                    # a UDT is always passed ByRef
            self.known.append(t is not None)
            self.plist.append((off, name, t or "Long", byref))
            off += width
        return out

    def arg(self, off):
        if off in self.params:
            return self.params[off]
        n = off // 4 - (1 if self.lf.obj is not None else 0)
        return self.args.setdefault(off, "a%d" % n)

    def arg_is_result(self, off):
        lf = self.lf
        return lf.obj is not None and lf.result_slot is not None and off == lf.p.ret_pop

    def local(self, off):
        if off == self.lf.result_slot and (getattr(self, "is_function", True) or
                                           not isinstance(off, int)):
            return self.proc_name       # (a Sub's slot is no result: mSettings = "0")
        if off not in self.locals:
            if isinstance(off, tuple):
                self.locals[off] = "o%d" % off[1]
                if isinstance(off[0], str):
                    # (("stk", -216, 3): a pushed argument's slot, with a sign no name can have)
                    self.locals[off] = ("t_%s%s" % (off[0], "_".join(str(x) for x in off[1:]))).replace("-", "m")
                elif sum(1 for n in self.locals.values() if n == self.locals[off]) > 1:
                    # two slots numbered alike by different rounds of renaming
                    # (a target: Dim o1001 twice, "declared twice")
                    self.locals[off] = "o%d_%X" % (off[1], -off[0])
            else:
                # (above the frame's zero: the upper half of the slot a push ecx reserved)
                self.locals[off] = "v%X" % (-off) if off <= 0 else "vp%X" % off
        return self.locals[off]

    def inline_element(self, ix, off=None):
        """base.f3C(k) for ix, an element of a fixed array inside a UDT
        (lift.inline_field), and with off the field at off inside that
        element (base.fE4(k).f8), from the array the UDT's layout puts
        there; None when the layout has no such array."""
        from . import apidb
        from .machine import add
        f = ix.arr
        b = f.e
        if isinstance(b, Index) and isinstance(b.arr, Var):
            t = self.lf.udt.get(b.arr.key())
        elif isinstance(b, Var):
            t = self.lf.udt.get(b.key())
        else:
            t = None
        if t:
            md = apidb.multi_array_at(t, f.off)
            if md is not None and f.inline == md[2]:
                # several dimensions: the linear index the compiler made, back to subscripts
                idx0 = ix.idx[0]
                extra = (f.off - md[4]) // md[2]
                if extra:
                    idx0 = BinOp("+", idx0, Const(extra))
                subs = self.lf.split_index(idx0, md[1])
                if subs is not None:
                    text = "%s.%s(%s)" % (expr(b, self, 100), md[0],
                                          ", ".join(expr(x, self) for x in subs))
                    if md[3] in apidb.SIZES or off is None:
                        return text if not off else None
                    sub = apidb.field_at(md[3], off or 0, whole=False)
                    return "%s.%s" % (text, sub) if sub else None
            lb = apidb.array_from_bound(t, f.off, f.inline)
            if lb is not None:
                text = "%s.%s(%s)" % (expr(b, self, 100), lb[0], expr(ix.idx[0], self))
                if lb[2] in apidb.SIZES or off is None:
                    return text if not off else None
                sub = apidb.field_at(lb[2], off or 0, whole=False)
                return "%s.%s" % (text, sub) if sub else None
        at = apidb.array_at(t, f.off) if t else None
        below = 0
        if t and f.inline > 8 and (at is None or at[2] != f.inline):
            # records: the array of exactly this stride a step or two on, whatever the offset lands
            # in now (.ties(k - 1).ang: raw 0Ch + k * 20, inside the Booleans before the array)
            for n in range(1, 4):
                a2 = apidb.array_at(t, f.off + n * f.inline)
                if a2 is not None and a2[2] == f.inline and a2[1] == 0:
                    at, below = a2, n
                    break
        if at is None and t:
            # the field offset took the - 1 of a subscript along: [p + (k - 1) * 4 + 4Ch] is
            # raw 48h, k; the array starts at 4Ch (a target Proc_9B2B70 printed f48(k), its
            # first compile error once everything else built)
            for n in range(1, 4):
                at = apidb.array_at(t, f.off + n * f.inline)
                if at is not None and (at[2] == f.inline or at[2] > f.inline and
                                       at[2] % f.inline == 0) and at[1] == 0 and at[4] == 0:
                    below = n
                    break
                at = None
        if (at is None or f.inline % at[2]) and t and isinstance(ix.idx[0], Const) and \
                isinstance(ix.idx[0].value, int):
            # no array there, for all the code shows: a constant subscript's bytes are a field
            # (t84recret jalki(2).x, only ever given constant subscripts)
            whole = apidb.field_at(t, f.off + ix.idx[0].value * f.inline + (off or 0))
            if whole:
                return "%s.%s" % (expr(b, self, 100), whole)
        if at is not None and (at[2] > f.inline and at[2] % f.inline == 0 or
                               # (one variable in both subscripts: .sp(i).mean(i), stride 24 + 4)
                               f.inline - at[2] in (1, 2, 4, 8) and at[2] > 8):
            idx = ix.idx[0]
            if below:
                idx = add(idx, Const(-below))
            return self.nested_element(b, at, idx, f.inline, off)
        if at is None or f.inline % at[2]:
            return None
        path, first, esize, etype, rest = at
        idx = ix.idx[0]
        if below:
            idx = add(idx, Const(-below))
        if f.inline != esize:
            # an array of bigger elements laid over this one (records.inline_arrays
            # merged them): k elements of it per element of the code's
            idx = BinOp("*", idx, Const(f.inline // esize))
        idx = add(idx, Const(first)) if first else idx
        text = "%s.%s(%s)" % (expr(b, self, 100), path, expr(idx, self))
        rest += off or 0
        if etype in apidb.SIZES or off is None and rest == 0:
            return text if rest == 0 else None
        # (off None: the field as a whole, Specie(i).Name passed ByRef, or a Type inside)
        sub = apidb.field_at(etype, rest, whole=off is None)
        return "%s.%s" % (text, sub) if sub else None

    def nested_element(self, b, at, idx, stride, off):
        """base.outer(i).inner(j) for an index in a smaller stride than the array the offset lands
        in: the compiler adds both subscripts up, i * 182 + j at a stride of 2 for
        SimOpts.Specie(i).Skin(j).  Terms go to the outermost array they are whole elements of; at
        = apidb.array_at's (field path, first index, element size, element type, offset inside
        it).  None when the Type has no such array."""
        from . import apidb
        from .machine import add
        lin = self.lf.linear(idx)
        terms = [(a, k * stride) for key, (a, k) in lin.items() if key is not None and k]
        path, first, esize, etype, rest = at
        rest += lin.get(None, [None, 0])[1] * stride
        text = expr(b, self, 100)
        while True:
            n, rest = divmod(rest, esize)
            first += n
            sub = None
            left = []
            for a, k in terms:
                # (one variable in both subscripts: K.lajit(i).mu.keski(i) is i * 100, 96 + 4)
                q, r = divmod(abs(k), esize)
                if q:
                    q = q if k > 0 else -q
                    t = a if q == 1 else BinOp("*", a, Const(q))
                    sub = t if sub is None else add(sub, t)
                if r:
                    left.append((a, r if k > 0 else -r))
            terms = left
            if sub is None:
                sub = Const(first)
            elif first:
                sub = add(sub, Const(first))
            text = "%s.%s(%s)" % (text, path, expr(sub, self))
            if not terms:
                break
            at2 = apidb.array_at(etype, rest) if etype not in apidb.SIZES else None
            if at2 is None and etype not in apidb.SIZES:
                # (a lower bound other than 0: the raw offset is below the array)
                lb = apidb.array_from_bound(etype, rest, min(abs(k) for _, k in terms))
                at2 = (lb[0], 0, lb[1], lb[2], 0) if lb else None
            if at2 is None:
                return None
            path, first, esize, etype, rest = at2
        rest += off or 0
        if etype in apidb.SIZES:
            return text if rest == 0 else None
        if off is None and rest == 0:
            return text
        f2 = apidb.field_at(etype, rest, whole=off is None)
        return "%s.%s" % (text, f2) if f2 else None

    def udt_of(self, e):
        """The UDT of an expression: a variable, an element, a field; for an array its element's."""
        from . import apidb
        if isinstance(e, Var):
            return self.lf.udt.get(e.key())
        if isinstance(e, Index):
            return self.udt_of(e.arr)
        if isinstance(e, Deref):
            t = self.udt_of(e.e)
            lay = apidb.layout(t) if t else None
            if not lay:
                return None
            best = None
            for o, fname, tname in lay[0]:
                if o <= e.off:
                    best = tname
            return best if best and apidb.layout(best) else None
        return None

    def field(self, base, off, whole=False):
        """Field name when base is a variable of a known UDT type."""
        from . import apidb
        if isinstance(base, BinOp) and base.op == "+" and isinstance(base.b, Const) and \
                isinstance(base.a, Var):
            base, off = base.a, off + base.b.value
        if isinstance(base, Index) and isinstance(base.arr, Var):
            t = self.lf.udt.get(base.arr.key())        # an element of an array of UDTs
            return apidb.field_at(t, off, whole) if t else None
        if isinstance(base, Index) and isinstance(base.arr, Deref) and \
                not getattr(base.arr, "inline", None):
            # an element of a dynamic array field: r.dynrec(1).name1 (a1.f10C(1) = "d" was a Type
            # mismatch; DarwinBots rob(i).dna(k).tipo)
            t = self.udt_of(base.arr)
            return apidb.field_at(t, off, whole) if t else None
        if not isinstance(base, Var):
            return None
        t = self.lf.udt.get(base.key())
        if t is None:
            return None
        return apidb.field_at(t, off, whole)

    def stk(self, off):
        return self.stks.setdefault(off, "a_%d" % (len(self.stks) + 1))

    def tmp(self, n):
        return self.tmps.setdefault(n, "t%d" % (len(self.tmps) + 1))

    def reg(self, r):
        return self.regs.setdefault(r, "t_%s" % r)


def paren(s, inner, outer):
    return "(%s)" % s if inner < outer else s


def single(e, nm):
    """A Single passed by value (graphics coordinates): its bits as a number."""
    import struct
    if isinstance(e, Const) and isinstance(e.value, int):
        f = struct.unpack("<f", struct.pack("<I", e.value & 0xffffffff))[0]
        return "%d" % f if f == int(f) and abs(f) < 1e9 else repr(f)
    return expr(e, nm)


def graphics(e, nm):
    """VB's graphics statements: their methods take flat argument lists with
    a flags word first (verified with t02 LineTests): 0x01 Step (first
    point), 0x02 color given, 0x04 first point given, 0x08 Step (second
    point), 0x10 B, 0x20 BF.  Coordinates are Singles."""
    if not isinstance(e, Call) or e.obj is None:
        return None
    late = getattr(e, "late", False)
    name = e.name.capitalize() if late and e.name.lower() in ("line", "pset", "circle") \
        else e.name                     # late-bound: the name as typed (a target obj.line)
    if name == "Pset":
        name = "PSet"
    if name not in ("Line", "PSet", "Circle", "Print", "Scale"):
        return None
    o = expr(e.obj, nm, 100)
    a = [expr(x, nm) for x in e.args]
    flags = e.args[0].value if e.args and isinstance(e.args[0], Const) else None
    f = flags or 0
    if name == "Scale" and len(a) == 5:
        # (flags, x1, y1, x2, y2), 4 when the points are given (tests/t15gfx)
        return "%s.Scale (%s, %s)-(%s, %s)" % (o, a[1], a[2], a[3], a[4]) if f & 4 else \
            "%s.Scale" % o
    if late:
        # late-bound: the arguments are Variants with their own types (an
        # Integer 10 is 10, not the Single with those bits)
        def single(x, nm):          # noqa: F811
            return expr(x, nm)
    else:
        single = globals()["single"]
    if name == "Line" and len(a) >= 6:
        p1 = ""
        if f & 4:
            p1 = ("Step" if f & 1 else "") + "(%s, %s)" % (single(e.args[1], nm), single(e.args[2], nm))
        p2 = ("Step" if f & 8 else "") + "(%s, %s)" % (single(e.args[3], nm), single(e.args[4], nm))
        t = "%s.Line %s-%s" % (o, p1, p2)
        box = ", BF" if f & 0x20 else (", B" if f & 0x10 else "")
        if f & 2:
            t += ", %s" % a[5]
        elif box:
            t += ","
        note = "" if flags is not None and not f & ~0x3f else "   ' vb6dec: Line flags=%s" % flags
        return t + box + note
    if name == "PSet" and len(a) >= 4:
        t = "%s.PSet %s(%s, %s)" % (o, "Step" if f & 1 else "", single(e.args[1], nm),
                                   single(e.args[2], nm))
        if f & 2:
            t += ", %s" % a[3]
        return t + ("" if flags is not None and not f & ~3 else "   ' vb6dec: PSet flags=%s" % flags)
    if name == "Circle" and len(a) >= 8:
        t = "%s.Circle %s(%s, %s), %s" % (o, "Step" if f & 1 else "", single(e.args[1], nm),
                                         single(e.args[2], nm), single(e.args[3], nm))
        extra = [a[4] if f & 2 else ""]
        # start, end and aspect: 40h, 80h, 100h (tools probe: Circle (50, 60),
        # 7, vbGreen, 0.5, 1.5, 2 has flags 1C2h)
        for bit, x in ((0x40, e.args[5]), (0x80, e.args[6]), (0x100, e.args[7])):
            extra.append(single(x, nm) if f & bit else "")
        while extra and not extra[-1]:
            extra.pop()
        if extra:
            t += ", " + ", ".join(extra)
        known = 1 | 2 | 0x40 | 0x80 | 0x100
        return t + ("" if flags is not None and not f & ~known else
                    "   ' vb6dec: Circle flags=%s" % flags)
    if name == "Print" and getattr(e, "write", False):
        return "Write #%s, %s" % (o, ", ".join(a)) if a else "Write #%s," % o
    if name == "Print" and hasattr(e, "print_codes"):
        # per item: low 6 bits its VARTYPE (0: no item), 0x40 "; " follows,
        # 0x80 the line ends, neither: ", " follows
        out, vals, items = [], iter(a), iter(e.args)
        codes = e.print_codes
        for k, c in enumerate(codes):
            last = k == len(codes) - 1
            if c & 0x3f:
                v, x = next(vals, ""), next(items, None)
                if c & 0x3f == 4 and isinstance(x, Const) and isinstance(x.value, int):
                    # (a Single's bits: x = 2.5: Print #1, x; fast code pushes the literal)
                    import struct
                    v = expr(Const(struct.unpack("<f", struct.pack("<I", x.value & 0xffffffff))[0],
                                   "Single"), nm)
                elif c & 0x3f in PRINT_AS and x is not None:
                    # (what is printed depends on the type: c = a * b: Print #1, c with VB's own
                    # folding left 2.42!, a Single's digits for a Double's 2.42000007629395; CDbl(d) of
                    # a Date printed the date.  The descriptor says what the item is)
                    t = getattr(x, "vbtype", None) if isinstance(x, Const) else nm.types.typeof(x)
                    fn, fam = PRINT_AS[c & 0x3f]
                    if t in fam:
                        v = "%s(%s)" % (fn, v)
                out.append(v)
            if c & 0x40:
                out.append(";" if last else "; ")
            elif not c & 0x80:
                out.append("," if last else ", ")
        if getattr(e, "file", False):
            return "Print #%s, %s" % (o, "".join(out)) if out else "Print #%s," % o
        return ("%s.Print %s" % (o, "".join(out))).rstrip()
    return None


def regroup(e, nm):
    """Arguments of a call to a decompiled procedure, merged by the
    callee's parameter widths (a ByVal Double is two pushed dwords)."""
    model = getattr(nm, "model", None)
    if model is None or not isinstance(e.raw, int) or e.raw not in model.plists:
        return e.args
    import struct
    out, i = [], 0
    for off, name, t, byref in model.plists[e.raw]:
        if i >= len(e.args):
            break
        if not byref and t == "Variant" and i + 3 < len(e.args):
            from .calls import byval_variant
            ch = e.args[i:i + 4]
            rest = [x for x in ch[1:]]
            if all(isinstance(x, Var) and x.space == "local" and isinstance(x.off, int)
                   for x in rest) and [x.off for x in rest] == [rest[0].off, rest[0].off + 4,
                                                               rest[0].off + 8] and \
                    not isinstance(ch[0], Const):
                # dwords 2..4 of one Variant; the first became its value (an alias
                # or an inlined temporary): that value
                out.append(ch[0])
            else:
                out.append(byval_variant(nm.lf, ch))
            i += 4
            continue
        if not byref and t in ("Double", "Currency", "Date") and i + 1 < len(e.args):
            lo, hi = e.args[i], e.args[i + 1]
            if isinstance(lo, Const) and isinstance(hi, Const) and \
                    isinstance(lo.value, int) and isinstance(hi.value, int):
                raw = struct.pack("<II", lo.value & 0xffffffff, hi.value & 0xffffffff)
                if t == "Currency":
                    out.append(Const(struct.unpack("<q", raw)[0] / 10000.0, "Currency"))
                else:
                    out.append(Const(struct.unpack("<d", raw)[0]))
            else:
                out.append(lo)
            i += 2
            continue
        if t == "String" and isinstance(e.args[i], Const) and e.args[i].value == 0:
            out.append(Var("global", "vbNullString", "vbNullString"))  # a null String
            i += 1
            continue
        if t == "String" and isinstance(e.args[i], Const) and isinstance(e.args[i].value, int):
            # a String literal the lifter could not tell from a number: "" (an
            # empty BSTR has length 0 before it, so vb_string skips it)
            im = nm.lf.im
            va = e.args[i].value & 0xffffffff
            if im.valid(va - 4) and im.valid(va + 1) and im.u32(va - 4) == 0 and \
                    im.u16(va) == 0 and im.section_of(va) == ".text":
                out.append(Str(""))
                i += 1
                continue
        if not byref and t == "Single" and isinstance(e.args[i], Const) and \
                isinstance(e.args[i].value, int):
            f = struct.unpack("<f", struct.pack("<I", e.args[i].value & 0xffffffff))[0]
            out.append(Const(f))
            i += 1
            continue
        if byref and t and (isinstance(e.args[i], Deref) or
                            isinstance(e.args[i], Var) and e.args[i].space == "member"):
            from . import apidb
            if apidb.layout(t) is not None:
                out.append(AddrOf(e.args[i]))   # a nested UDT field as a whole
                i += 1
                continue
        if byref and t and isinstance(e.args[i], AddrOf) and isinstance(e.args[i].e, Var) and \
                e.args[i].e.space == "member":
            from . import apidb
            if apidb.layout(t) is not None:
                out.append(e.args[i])           # (api_whole_members: stays whole)
                i += 1
                continue
        if byref and t and not t.endswith("()") and isinstance(
                e.args[i].e if isinstance(e.args[i], AddrOf) else e.args[i], Var):
            from . import apidb
            v = e.args[i].e if isinstance(e.args[i], AddrOf) else e.args[i]
            tv = nm.lf.udt.get(v.key())
            lt, lv = apidb.layout(t), apidb.layout(tv) if tv else None
            ft = lv[0][0][2] if lv is not None and lv[0] and lv[0][0][0] == 0 else None
            same = ft == t or ft is not None and apidb.udt(ft) is not None and \
                apidb.udt(t) is not None and apidb.udt(ft)[1:] == apidb.udt(t)[1:]
            if lt is not None and lv is not None and tv != t and same:
                # the argument's UDT starts with one of the parameter's: that
                # field (udt.nest_types; STlib_initNum p.n, HUlib_initTextLine it.l)
                out.append(AddrOf(Deref(v, lt[1], 0)))
                i += 1
                continue
        if byref and t in ("Long", "Integer", "Byte", "Boolean", "Single", "Double",
                           "Currency", "Date", "String") and isinstance(e.args[i], AddrOf) and \
                isinstance(e.args[i].e, Var) and nm.lf.udt.get(e.args[i].e.key()):
            from . import apidb
            v = e.args[i].e
            if apidb.layout(nm.lf.udt[v.key()]) is not None and \
                    (v.key() not in nm.lf.arrays):
                # &struct for a ByRef scalar: its first field (VBDoom AM_rotate
                # l.a.x, l.a.y: &l and &l + 4)
                out.append(Deref(v, apidb.SIZES.get(t, 4), 0))
                i += 1
                continue
        if byref and t in ("Long", "Integer", "Byte", "Boolean", "Single", "Double",
                           "Currency", "Date", "String") and isinstance(e.args[i], AddrOf) and \
                isinstance(e.args[i].e, Index) and isinstance(e.args[i].e.arr, Var) and \
                nm.lf.udt.get(e.args[i].e.arr.key()):
            from . import apidb
            lay = apidb.layout(nm.lf.udt[e.args[i].e.arr.key()])
            if lay is not None and lay[0] and lay[0][0][0] == 0:
                # &a(i) of an array of records for a ByRef scalar: the element's
                # first field (a target ReadAll Form1(a).h, 5)
                out.append(Deref(e.args[i].e, apidb.SIZES.get(t, 4), 0))
                i += 1
                continue
        x = e.args[i].e if isinstance(e.args[i], AddrOf) else e.args[i]
        if t == "Variant" and isinstance(x, Var) and nm.lf.udt.get(x.key()) and \
                x.key() not in nm.lf.arrays:
            from . import apidb
            lay = apidb.layout(nm.lf.udt[x.key()])
            if lay is not None and lay[0] and lay[0][0][0] == 0 and lay[0][0][2] in apidb.SIZES:
                # (a record variable: Proc_926D00 vDC, vDC.f0 just assigned)
                out.append(Deref(x, apidb.SIZES[lay[0][0][2]], 0))
                i += 1
                continue
        if (t == "Variant" or t in SCALAR_VALUES) and isinstance(x, Index) and \
                isinstance(x.arr, (Var, Deref)) and field0(x, nm) is not x:
            # no record goes into a Variant: the element's first field (a target
            # Proc_927940 global_F041EC(a), ...; DarwinBots TipoDetok(.dna(t).tipo))
            out.append(field0(x, nm))
            i += 1
            continue
        out.append(e.args[i])
        i += 1
    return out + e.args[i:]


def args_text(args, nm):
    args = list(args)
    while args and isinstance(args[-1], ir.Missing):
        args.pop()
    texts = ["" if isinstance(a, ir.Missing) else expr(a, nm) for a in args]
    while texts and not texts[-1]:
        texts.pop()             # an omitted last argument, whatever carried it ("P(o, )")
    return ", ".join(texts)


def array_record(v, nm):
    """The record type of array variable v's elements, or None (an array
    parameter's comes with its array info: Proc_756920's a1() As T_82_66)."""
    return nm.lf.udt.get(v.key()) or (nm.lf.arrays.get(v.key()) or {}).get("udt")


def array_text(x, nm):
    """The array itself, for an expression that names its first element: a(i).f(0) as a(i).f."""
    t = expr(x, nm, 0)
    if isinstance(x, Deref):
        # (whole: an array of records, whose first field would come on the end, x.f70(0).f0)
        f = nm.field(x.e, x.off, whole=True)
        if f is not None:
            b = x.e.a if isinstance(x.e, BinOp) and x.e.op == "+" else x.e
            t = "%s.%s" % (expr(b, nm, 100), f)
    if not t.endswith(")") or "(" not in t:
        return None
    depth = 0
    for i in range(len(t) - 1, -1, -1):
        if t[i] == ")":
            depth += 1
        elif t[i] == "(":
            depth -= 1
            if depth == 0:
                return t[:i] if i and (t[i - 1].isalnum() or t[i - 1] == "_") else None
    return None


def field0(x, nm):
    """An element of an array of UDTs read as a value is its first field
    (the lift's [&a(i)] as a dword: VBDoom Hex$(mobjs(m).x)); anything else
    as it is."""
    if isinstance(x, Index) and (isinstance(x.arr, Var) or (
            isinstance(x.arr, Deref) and not getattr(x.arr, "inline", None))):
        from . import apidb
        t = array_record(x.arr, nm) if isinstance(x.arr, Var) else nm.udt_of(x.arr)
        lay = apidb.layout(t) if t else None
        if lay is not None and lay[0] and lay[0][0][0] == 0 and lay[0][0][2] in apidb.SIZES:
            return Deref(x, apidb.SIZES[lay[0][0][2]], 0)
    return x


def printed_type(y, text, nm):
    """The Type of what text, y as printed, ends in: a.f18(i).f30 walked down the fields from a's
    Type, or None for anything else (y's own offsets may be inside the inline array: render.
    inline_element); a field of a scalar type is None too."""
    from . import apidb
    root = y
    while isinstance(root, (Deref, Index)):
        root = root.e if isinstance(root, Deref) else root.arr
    if not isinstance(root, Var):
        return None
    t = nm.lf.udt.get(root.key())
    parts, depth, cur = [], 0, ""
    for ch in text:
        if ch == "." and not depth:
            parts.append(cur)
            cur = ""
            continue
        depth += (ch == "(") - (ch == ")")
        cur += ch
    parts.append(cur)
    for seg in parts[1:]:
        lay = apidb.layout(t) if t else None
        if not lay:
            return None
        name = seg.split("(")[0]
        t = next((ft for _, f, ft in lay[0] if f == name), None)
    return t if t and apidb.layout(t) else None


def input_item(x, nm):
    """What Input # reads into: a record's address is its first field (Input #1, SimOpts.SimName
    printed Input #1, SimOpts, a Type mismatch)."""
    from . import apidb
    y = x.e if isinstance(x, AddrOf) else x
    if isinstance(y, Var) and y.key() in nm.lf.arrays or getattr(y, "inline", None):
        return x
    t = nm.udt_of(y)
    lay = apidb.layout(t) if t else None
    if lay is None or not lay[0] or lay[0][0][0] != 0:
        return x
    return Deref(y, apidb.SIZES.get(lay[0][0][2], 4), 0)


def string_field0(x, nm):
    """The source of a string LSet: a record element read as a string is its
    first String field (a target Proc_756920: LSet v24 = a1(i).f0)."""
    text = expr(x, nm, 0)
    if isinstance(x, Index) and isinstance(x.arr, Var):
        from . import apidb
        t = array_record(x.arr, nm)
        lay = apidb.layout(t) if t else None
        if lay is not None and lay[0] and lay[0][0][0] == 0 and \
                lay[0][0][2].lower().startswith("string"):
            return "%s.%s" % (text, lay[0][0][1])
    return text


def late_dispid_name(e):
    """The member behind a DISPID, once the object is known: Printer in a frame slot at the
    lift, Printer.EndDoc is 20000h (a target Proc_E85D70 printed Printer.__lateid)."""
    d = getattr(e, "dispid", None)
    ob = e.obj
    it = getattr(ob, "iface", None) or getattr(getattr(ob, "control", None), "iface", None)
    if __import__("os").environ.get("VB6DEC_LATEDBG"):
        print("LATE", d, type(ob).__name__, repr(ob)[:60], it, file=__import__("sys").stderr)
    if d is None or it is None:
        return None
    names = [f.name for f in it.all_funcs()
             if f.memid is not None and f.memid & 0xffffffff == d and f.name]
    return next((n for n in names if not n.startswith("_")), None)


def api_whole_members(e):
    """A Me variable given to an API's ByRef UDT: the variable, not the field at its start
    (CopyRect m90.Left, mA0.Left is a ByRef type mismatch)."""
    from . import apidb
    api = apidb.lookup(e.raw.split("!", 1)[1])
    for i, (byval, _, ptype) in enumerate(api.params if api else ()):
        if i < len(e.args) and not byval and isinstance(e.args[i], Var) and \
                e.args[i].space == "member" and (apidb.layout(ptype) or (0, 0))[1] > 4:
            e.args[i] = AddrOf(e.args[i])


def expr(e, nm, prec=0):
    if isinstance(e, Call) and isinstance(e.raw, str) and "!" in e.raw:
        api_whole_members(e)
    if isinstance(e, BinOp) and (isinstance(e.a, Index) or isinstance(e.b, Index)) and \
            e.op not in ("To",):
        a, b = field0(e.a, nm), field0(e.b, nm)
        if a is not e.a or b is not e.b:
            e = e._keep(BinOp(e.op, a, b, e.vbtype))
    if isinstance(e, Call) and e.name in ("CSng", "CLng", "CInt", "CDbl", "CByte", "CBool", "CStr") \
            and len(e.args) == 1 and isinstance(e.args[0], Var) and nm.lf.udt.get(e.args[0].key()) \
            and e.args[0].key() not in nm.lf.arrays:
        # a conversion of a record variable: of its first field (CSng(vEC), vEC.f0
        # just assigned)
        from . import apidb
        lay = apidb.layout(nm.lf.udt[e.args[0].key()])
        if lay is not None and lay[0] and lay[0][0][0] == 0 and lay[0][0][2] in apidb.SIZES:
            e = e._keep(Call(e.name, [Deref(e.args[0], apidb.SIZES[lay[0][0][2]], 0)], e.vbtype,
                             e.raw, e.method, e.obj, e.pure))
    if isinstance(e, Call) and (not isinstance(e.raw, int) or e.obj is not None or e.method) \
            and e.name not in ("LSet", "Erase") and any(isinstance(x, Index) for x in e.args):
        # (a COM method takes no UDT either: Collection.Remove lumpinfo(i).name)
        args = [field0(x, nm) for x in e.args]
        if any(x is not y for x, y in zip(args, e.args)):
            e = e._keep(Call(e.name, args, e.vbtype, e.raw, e.method, e.obj, e.pure))
    if isinstance(e, BinOp) and e.op == "Is" and isinstance(e.b, Const) and e.b.value == 0:
        t = "%s Is Nothing" % expr(e.a, nm, 8)
        return "(%s)" % t if prec > 7 else t
    arg0 = e.args[0] if isinstance(e, Call) and e.args else None
    arg0 = arg0.e if isinstance(arg0, AddrOf) else arg0
    if isinstance(e, Call) and e.name in ("LBound", "UBound") and getattr(e, "view", None) and \
            not (isinstance(arg0, Var) and arg0.key() in nm.lf.arrays):
        # a fixed array in a record: VB made a SAFEARRAY for it on the frame, from a template with
        # the bounds, which are constants anyway.  The array itself can't always be told from its
        # first element's first field at the same address (UBound(K.lajit), 0x40701C)
        d = 1
        if len(e.args) > 1 and isinstance(e.args[1], Const):
            d = e.args[1].value
        dims = e.view[1]
        if 1 <= d <= len(dims):
            lb, n = dims[d - 1]
            return const(lb if e.name == "LBound" else lb + n - 1, None)
    if isinstance(e, Call) and e.name in ("LBound", "UBound") and isinstance(arg0, Const) and \
            nm.lf.data_address(arg0):
        arg0 = nm.lf.global_var(arg0.value & 0xffffffff)    # (UBound(SimOpts.Specie), by its data)
        if isinstance(arg0, Var):
            args = ", ".join(expr(x, nm, 0) for x in e.args[1:])
            return "%s(%s%s)" % (e.name, expr(arg0, nm, 0), ", " + args if args else "")
    if isinstance(e, Call) and e.name in ("LBound", "UBound") and isinstance(arg0, Deref) and \
            arg0.off == 0 and isinstance(arg0.e, AddrOf) and isinstance(arg0.e.e, Call):
        # UBound(Filter(a, "x")), a function's result in a Variant (array_text takes the parentheses
        # for a subscript: UBound(Filter))
        args = ", ".join(expr(x, nm, 0) for x in e.args[1:])
        return "%s(%s%s)" % (e.name, expr(arg0.e.e, nm, 0), ", " + args if args else "")
    if isinstance(e, Call) and e.name in ("LBound", "UBound") and e.args and (
            isinstance(arg0, Index) and getattr(arg0.arr, "inline", None) or
            isinstance(arg0, Deref)):
        # of a fixed array inside a record: VB built a SAFEARRAY for it on the frame, whose data is
        # the array's first element; the array itself (LBound(a1.f4(0)): "Expected array";
        # UBound(rob(n).mem) arrives as the address of the field, global.f2348(0))
        t = array_text(arg0, nm)
        if t is not None:
            args = ", ".join(expr(x, nm, 0) for x in e.args[1:])
            return "%s(%s%s)" % (e.name, t, ", " + args if args else "")
    if isinstance(e, Call) and getattr(e, "fmt", None):
        t = e.fmt.format(*[expr(a, nm, 13) for a in e.args])
        return "(%s)" % t if prec > 7 else t        # TypeOf x Is C binds like a comparison
    if isinstance(e, Const):
        if e.vbtype == "Boolean":
            return "True" if e.value else "False"
        model = getattr(nm, "model", None)
        if model is not None and isinstance(e.value, int) and (e.value & 0xffffffff) in model.procs \
                and model.prj.code_start <= (e.value & 0xffffffff) < model.prj.code_end:
            p = model.procs[e.value & 0xffffffff]
            return "AddressOf " + (p.truth[1] if getattr(p, "truth", None) else p.name)
        return const(e.value, e.vbtype)
    if isinstance(e, ir.Missing):
        return ""
    if isinstance(e, Str):
        t = vb_literal(e.text)
        return "(%s)" % t if " & " in t and prec > 8 else t
    if isinstance(e, Me):
        return "Me"
    if isinstance(e, Var):
        if getattr(e, "scalar_tag", None) and e.key() in nm.lf.udt:
            f = nm.field(e, 0)      # (lift.typed_ref)
            if f is not None:
                e2 = copy.copy(e)
                e2.scalar_tag = None
                return "%s.%s" % (expr(e2, nm, 100), f)
        if e.space == "arg":
            return nm.arg(e.off)
        if e.space == "local":
            return nm.local(e.off)
        if e.space == "reg":
            if isinstance(e.off, tuple):
                return nm.local(e.off)
            return nm.reg(e.off)
        if e.space == "tmp":
            return nm.tmp(e.off)
        if e.space == "stk":
            if isinstance(e.off, tuple):
                return nm.local(("stk",) + e.off)
            return nm.stk(e.off)
        if e.space == "global":
            if e.name is not None:
                return e.name
            return "global_%X" % e.off if isinstance(e.off, int) else str(e.off)
        if e.space == "member":
            if e.off == 0x10 and getattr(nm.lf.obj, "kind", None) == "UserControl":
                return "UserControl"
            if e.name:
                return e.name
            n = nm.member_names.get(e.off)
            if n:
                return n
            obj = getattr(nm.lf, "obj", None)
            ms = getattr(nm.model, "member_structs", {}).get(obj.index, {}) \
                if obj is not None and getattr(nm, "model", None) is not None else {}
            for b, (t, size) in ms.items():
                if b <= e.off < b + size and not (b == e.off and getattr(nm, "whole", False)):
                    from . import apidb
                    f = apidb.field_at(t, e.off - b)
                    if f:
                        return "m%X.%s" % (b, f)   # a field of the member UDT
            return "m%X" % e.off
        if e.space == "frame":
            return "frame%X" % (-e.off)
        return str(e.off)
    if isinstance(e, Deref) and isinstance(e.e, AddrOf) and isinstance(e.e.e, Var) and \
            isinstance(e.e.e.off, int):
        v = e.e.e                   # *(&x + k): x's field at k
        if v.space == "member":
            return expr(Var("member", v.off + e.off), nm, prec)
        if e.off == 0 and not nm.lf.udt.get(v.key()):
            return expr(v, nm, prec)
        return expr(Deref(v, e.size, e.off), nm, prec)
    if isinstance(e, Deref) and isinstance(e.e, Var) and e.e.space == "member" and \
            isinstance(e.e.off, int) and nm.lf.obj is not None and \
            any(b <= e.e.off + e.off < b + n for b, (_, n) in getattr(
                nm.model, "member_structs", {}).get(nm.lf.obj.index, {}).items()):
        # (the slot prints as its field already: o1001 = m34.f0.f0)
        return expr(Var("member", e.e.off + e.off), nm, prec)
    if isinstance(e, Deref) and isinstance(e.e, Index) and e.off == 0 and e.size > 8 and \
            isinstance(e.e.arr, Var):
        # (the record as a whole: K.lajit(4) = K.lajit(0), not its first field)
        from . import apidb
        lay = apidb.layout(nm.lf.udt.get(e.e.arr.key()) or "")
        if lay is not None and lay[1] == e.size:
            return expr(e.e, nm, prec)
    if isinstance(e, Deref) and isinstance(e.e, Index) and getattr(e.e.arr, "inline", None):
        # (of the element's size: the whole record, Specie(b) = v180)
        s = nm.inline_element(e.e, None if e.off == 0 and e.size == e.e.arr.inline and e.size > 8
                              else e.off)
        if s is not None:
            return s
    if isinstance(e, Index) and getattr(e.arr, "inline", None):
        s = nm.inline_element(e)            # the element itself (passed ByRef)
        if s is not None:
            return s
    if isinstance(e, Deref):
        f = nm.field(e.e, e.off, whole=e.size > 8)
        if f is not None:
            b = e.e
            if isinstance(b, BinOp) and b.op == "+":
                b = b.a
            return "%s.%s" % (expr(b, nm, 100), f)
        # a ByRef argument is a pointer; print the parameter itself
        if e.off == 0 and isinstance(e.e, Var) and e.e.space == "arg":
            return nm.arg(e.e.off)
        if e.off == 0:
            info = nm.lf.arrays.get(e.e.key()) if isinstance(e.e, Var) else None
            if info is not None and not info.get("dyn"):
                # (a fixed array's slot holds its data: *slot is the first element, Line Input #f, a(0))
                dims = info.get("dims") or [(info.get("lb") or 0, None)]
                return "%s(%s)" % (expr(e.e, nm, 100), ", ".join(str(d[0] or 0) for d in dims))
            if DEREF_LOG and isinstance(e.e, Var) and e.e.space in ("reg", "tmp"):
                print("deref of a register: %s in %s" % (e, nm.lf.p.name), file=sys.stderr)
            return expr(e.e, nm, 100)
        return "%s.f%X" % (expr(e.e, nm, 100), e.off)
    if isinstance(e, Call) and e.name in ("VarPtr", "LenB", "Len") and len(e.args) == 1 and \
            isinstance(e.args[0], Var) and e.args[0].space == "member":
        nm.whole = True             # VarPtr(m3C): the member UDT itself
        try:
            return "%s(%s)" % (e.name, expr(e.args[0], nm, 0))
        finally:
            nm.whole = False
    if isinstance(e, AddrOf):
        d = e.e
        if isinstance(d, Var) and d.space == "member":
            nm.whole = True         # &m3C passed ByRef: the whole member UDT
            try:
                return expr(d, nm, prec)
            finally:
                nm.whole = False
        if isinstance(d, Deref):
            # &v.f: the field as a whole (a nested UDT passed ByRef, not its
            # first member: v.Data.ftLastWriteTime)
            f = nm.field(d.e, d.off, whole=True)
            if f is not None:
                b = d.e.a if isinstance(d.e, BinOp) and d.e.op == "+" else d.e
                return "%s.%s" % (expr(b, nm, 100), f)
        return expr(e.e, nm, prec)
    if isinstance(e, BinOp) and e.op == "+" and isinstance(e.b, Const):
        f = nm.field(e, 0, whole=True)          # &var.field passed ByRef
        if f is not None:
            return "%s.%s" % (expr(e.a, nm, 100), f)
    if isinstance(e, BinOp):
        if e.op == "To":
            return "%s To %s" % (expr(e.a, nm, 0), expr(e.b, nm, 0))
        # (only 16-bit instructions are Integer arithmetic: whatever a fold rebuilt, lea and the
        # like are the machine's 32 bits.  (Piece * 119 + Target) * 1786 of two Integer fields, with
        # the sum on a 32-bit register, wrapped at 16 bits in VB; ChessBrainVB QSearch)
        if getattr(e, "width", None) != 2 and e.op in ("*", "+", "-") and \
                not getattr(e, "fp", False) and getattr(nm, "types", None) is not None and \
                narrow(e.a, nm) and narrow(e.b, nm):
            # the machine's 32-bit sum or product of two Integers or Bytes: VB's
            # would be an Integer one, which wraps or overflows (VBDoom M_WriteBMP
            # CLng(currentPalette(i * 3 + 1)) * 256&)
            if isinstance(e.b, Const):
                s = "%s %s %s&" % (expr(e.a, nm, e.prec), e.op, const(e.b.value))
            elif isinstance(e.a, Const):
                s = "%s& %s %s" % (const(e.a.value), e.op, expr(e.b, nm, e.prec + 1))
            else:
                s = "CLng(%s) %s %s" % (expr(e.a, nm, 0), e.op, expr(e.b, nm, e.prec + 1))
            return paren(s, e.prec, prec)
        if getattr(e, "fp", False) and e.op in ("*", "+", "-") and \
                getattr(nm, "types", None) is not None:
            from .types import INTEGRAL
            if (nm.types.typeof(e.a) in INTEGRAL or getattr(e.a, "from_fild", False)) and \
                    (nm.types.typeof(e.b) in INTEGRAL or getattr(e.b, "from_fild", False)) and \
                    not isinstance(e.a, Const):
                # the FPU's sum or product of two integers: VB's would be a Long
                # one, which overflows (VBDoom FixedMul CDbl(a And &HFFFF&) * CDbl(b))
                s = "CDbl(%s) %s %s" % (expr(e.a, nm, 0), e.op, expr(e.b, nm, e.prec + 1))
                return paren(s, e.prec, prec)
        s = "%s %s %s" % (expr(e.a, nm, e.prec), e.op, expr(e.b, nm, e.prec + 1))
        return paren(s, e.prec, prec)
    if isinstance(e, UnOp):
        if e.op == "ByVal":
            a = e.a
            if isinstance(a, BinOp) and a.op == "+" and isinstance(a.a, Var) and \
                    a.a.space == "arg" and isinstance(a.b, Const) and \
                    nm.field(a, 0, whole=True) is not None:
                # ByVal (&uHdr + 30h) for As Any: the field itself, by reference
                return "%s.%s" % (expr(a.a, nm, 100), nm.field(a, 0, whole=True))
            info = nm.lf.arrays.get(a.key()) if isinstance(a, Var) else None
            if info is not None and not info.get("dyn"):
                # ByVal <a fixed array's data pointer> for As Any: its first element
                lb = info.get("lb") if isinstance(info.get("lb"), int) else 0
                return "%s(%d)" % (expr(a, nm, 100), lb)
            return "ByVal %s" % expr(e.a, nm, 0)
        if e.op == "-":
            return paren("-%s" % expr(e.a, nm, 13), 13, prec)
        return paren("%s %s" % (e.op, expr(e.a, nm, 6)), 6, prec)
    if isinstance(e, Prop):
        base = expr(e.obj, nm, 100)
        if not e.name or hidden_default(e):  # a default member: chk(1) for chk.Item(1)
            s = base
        elif e.name == "_Default":
            # the hidden duplicate of a control's default property (memid 0):
            # its real name ("_" cannot start a VB name)
            dflt = DEFAULT_PROPS.get(getattr(getattr(e.obj, "iface", None), "name", "") or "")
            s = "%s.%s" % (base, dflt) if dflt else base
        else:
            s = "%s.%s" % (base, e.name) if base else e.name
        if e.args:
            s += "(%s)" % ", ".join(expr(a, nm) for a in e.args)
        return s
    if isinstance(e, Call) and e.name == "IIf" and len(e.args) == 3 and \
            not isinstance(e.raw, int) and const_truth(e.args[0]) is not None:
        # a constant condition (the Single For's step: IIf(6! >= 0, ...))
        return expr(e.args[1] if const_truth(e.args[0]) else e.args[2], nm, prec)
    if isinstance(e, Call) and e.name == "New" and getattr(e, "new_class", None):
        return "New " + e.new_class
    if isinstance(e, Call) and e.name == "StrPtr" and len(e.args) == 1 and \
            isinstance(e.args[0], AddrOf):
        return "VarPtr(%s)" % expr(e.args[0], nm)   # (an element's address seen only by now)
    if isinstance(e, Call) and e.name == "StrPtr" and len(e.args) == 1 and \
            getattr(nm, "types", None) is not None and \
            nm.types.typeof(e.args[0]) not in (None, "String"):
        # StrPtr and VarPtr are one export: StrPtr only takes a String (a UDT
        # parameter's address: VarPtr(uHdr), gh_zip pvOutputLocalHeader)
        return "VarPtr(%s)" % expr(e.args[0], nm)
    if isinstance(e, Call):
        a = args_text(regroup(e, nm), nm)
        if e.obj is not None:
            o = "" if is_vb_global(e.obj, nm) or vb_global_member(e, nm) else \
                expr(e.obj, nm, 100)
            if o and hidden_default(e):
                return "%s(%s)" % (o, a) if a else o
            name = "%s.%s" % (o, e.name) if o else e.name   # (VB's Global object: none)
            return "%s(%s)" % (name, a) if a else name
        return "%s(%s)" % (e.name, a) if a else "%s()" % e.name
    if isinstance(e, Index):
        # (a record element as a subscript is its first field: m40(m3C(h)) for m40(m3C(h).f0))
        return "%s(%s)" % (expr(e.arr, nm, 100), ", ".join(expr(field0(i, nm), nm) for i in e.idx))
    if isinstance(e, SP):
        return nm.local(e.off + 4)
    if isinstance(e, IatSlot):
        return "AddressOf %s" % e.name
    if isinstance(e, Unknown):
        return "'<%s>" % e.text
    return "'<%r>" % (e,)


def const_truth(c):
    """True / False for a comparison of two numeric constants, else None."""
    import operator
    ops = {"<": operator.lt, "<=": operator.le, ">": operator.gt, ">=": operator.ge,
           "=": operator.eq, "<>": operator.ne}
    if isinstance(c, BinOp) and c.op in ops and isinstance(c.a, Const) and \
            isinstance(c.b, Const) and isinstance(c.a.value, (int, float)) and \
            isinstance(c.b.value, (int, float)):
        return ops[c.op](c.a.value, c.b.value)
    return None


def hidden_default(e):
    """A library's hidden default member (memid 0) that VB source does not
    name: "_" cannot start a VB name, and ControlDefault is MSComctlLib's
    Item (a target printed `Me.TabStrip1.Tabs.ControlDefault(1).
    _ObjectDefault = x`, a Syntax error, for `Me.TabStrip1.Tabs(1) = x`).
    _Default has the control's real name (DEFAULT_PROPS)."""
    name = e.name or ""
    if not name or name == "_Default" or not (name.startswith("_") or name == "ControlDefault"):
        return False
    memid = getattr(e, "memid", None)
    if memid is None:
        memid = getattr(getattr(e, "func", None), "memid", None)
    return memid is not None and memid & 0xffffffff == 0


# the default properties of VB's controls, for the hidden _Default members
DEFAULT_PROPS = {"_Image": "Picture", "_PictureBox": "Picture", "_TextBox": "Text",
                 "_Label": "Caption", "_CommandButton": "Value", "_CheckBox": "Value",
                 "_OptionButton": "Value", "_ListBox": "Text", "_ComboBox": "Text",
                 "_Frame": "Caption", "_Timer": "Enabled", "_HScrollBar": "Value",
                 "_VScrollBar": "Value", "_DriveListBox": "Drive", "_DirListBox": "Path",
                 "_FileListBox": "FileName", "_Shape": "Shape", "_Line": "Visible",
                 "_Data": "Caption"}

CTRL_NAMES = {"\r\n": "vbCrLf", "\r": "vbCr", "\n": "vbLf", "\t": "vbTab", "\0": "vbNullChar"}


def ansi(ch):
    """Can a source file (cp1252, writer.ANSI) hold ch?"""
    try:
        ch.encode("cp1252")
        return True
    except UnicodeEncodeError:
        return False


def vb_literal(text):
    """A VB string expression for text: quoted runs, control characters as
    vbCrLf / vbTab / Chr$(n), characters the ANSI code page lacks as
    ChrW$(n), joined with &."""
    import re
    parts = []
    for piece in re.split(r"(\r\n|[\x00-\x1f])", text):
        if piece == "":
            continue
        if piece in CTRL_NAMES:
            parts.append(CTRL_NAMES[piece])
        elif len(piece) == 1 and ord(piece) < 32:
            parts.append("Chr$(%d)" % ord(piece))
        else:
            text_run = ""
            for ch in piece:
                if ord(ch) > 0x7F and not ansi(ch):
                    if text_run:
                        parts.append('"%s"' % text_run.replace('"', '""'))
                        text_run = ""
                    parts.append("ChrW$(%d)" % ord(ch))
                else:
                    text_run += ch
            if text_run:
                parts.append('"%s"' % text_run.replace('"', '""'))
    return " & ".join(parts) if parts else '""'


def narrow(x, nm):
    """An operand VB does Integer arithmetic with: a Byte, Integer or Boolean,
    or a literal const() writes in Integer range."""
    if isinstance(x, Const):
        return isinstance(x.value, int) and x.vbtype is None and -0x8000 <= x.value <= 32767
    # (a field read as a word is one: CLng(rob(n).DnaLen) * CLng(k), movsx both and imul, printed
    # with no CLng overflowed once a robot's DNA was longer than 30)
    if isinstance(x, Deref) and x.size in (1, 2) and not getattr(x, "fp4", False):
        return True
    return nm.types.typeof(x) in ("Byte", "Integer", "Boolean")


def const(v, vbtype=None):
    if isinstance(v, float) and vbtype == "Currency":
        return ("%d" % v if v == int(v) else repr(v)) + "@"
    if isinstance(v, float) and vbtype == "Single":
        if v == int(v) and abs(v) < 1e7:
            return "%d!" % v
        import struct
        for n in (6, 7, 8, 9):              # (as few digits as give those 32 bits)
            t = "%.*G" % (n, v)
            if struct.pack("<f", float(t)) == struct.pack("<f", v):
                return t + "!"
        return repr(v) + "!"
    if isinstance(v, float) and vbtype == "Date":
        import datetime
        day = datetime.datetime(1899, 12, 30) + datetime.timedelta(days=v)
        if day.hour or day.minute or day.second:
            return "#%d/%d/%d %d:%02d:%02d#" % (day.month, day.day, day.year, day.hour,
                                                 day.minute, day.second)
        return "#%d/%d/%d#" % (day.month, day.day, day.year)
    if isinstance(v, float):
        s = repr(v)
        return s
    if isinstance(v, int) and vbtype == "Long&":
        return const(v) + "&"           # a Long literal (VB reads small ones as Integer)
    if isinstance(v, int):
        if v == 0:
            return "0"
        # (an Integer literal stays one: &HFFFF8300 is a Long, Random(-32000, 32000) of Variants
        # takes the other arithmetic)
        if -0x8000 <= v <= 1000:
            return str(v)
        return "&H%X" % (v & 0xffffffff) if v < 0 or v > 0xffff else str(v)
    return str(v)


def two_values(e):
    """The compiler's branch-free choice between two constants, as
    (condition, value if true, value if false), or None:
      -(C) + K                 1 + K / K
      ((-(C) - 1) And K) + B   B / K + B      (C a comparison, -1 when true)"""
    def boolean(c):
        return isinstance(c, BinOp) and c.op in ("=", "<>", "<", ">", "<=", ">=", "And",
                                                 "Or", "Like")
    if isinstance(e, BinOp) and e.op in ("+", "-") and isinstance(e.b, Const) and \
            isinstance(e.b.value, int):
        k = e.b.value if e.op == "+" else -e.b.value
        a = e.a
        if isinstance(a, UnOp) and a.op == "-" and boolean(a.a):
            return a.a, Const(1 + k), Const(k)
        if isinstance(a, BinOp) and a.op == "And" and isinstance(a.b, Const) and \
                isinstance(a.b.value, int):
            m = a.a
            if isinstance(m, BinOp) and m.op == "-" and isinstance(m.b, Const) and \
                    m.b.value == 1 and isinstance(m.a, UnOp) and m.a.op == "-" and boolean(m.a.a):
                return m.a.a, Const(k), Const(a.b.value + k)
    return None


def nested_part(x, n, nm):
    """(text, type) of the nested UDT field of n bytes that address x points
    at, when x is inside a UDT variable (&v + k, or &v itself where v is
    larger); None otherwise."""
    from .udt import addr_off
    from . import apidb
    ao = addr_off(x)
    if ao is None or n is None:
        return None
    v, k = ao
    t = nm.lf.udt.get(v.key())
    lay = apidb.layout(t) if t else None
    if lay is None or (k == 0 and lay[1] == n):
        return None
    for o, fname, tname in lay[0]:
        sub = apidb.layout(tname)
        if o == k and sub is not None and sub[1] == n:
            return "%s.%s" % (expr(v, nm, 100), fname), tname
    return None


def object_value(e, nm=None):
    """An expression whose value is an object reference (a Function or
    Property returning one): VB needs Set even for a Variant target.  A
    project Function's return type is known from its own signature
    (ParseJson As Object, gh_json Main: the roundtrip temp)."""
    if not isinstance(e, (Call, Prop)):
        return False
    if getattr(e, "vbtype", None) == "Object":
        return True
    if getattr(e, "iface", None) is not None and getattr(e, "control", None) is None:
        return True                 # returns an object of a known class (Global.App)
    raw = getattr(e, "raw", None)
    return isinstance(raw, int) and nm is not None and \
        nm.model.ret_types.get(raw) in ("Object", "Unknown")


def object_typed(t):
    """A type name of objects: Object or a class, no value or record."""
    from . import apidb
    return bool(t) and t not in SCALAR_VALUES and t not in ("String", "Variant") and \
        t.isidentifier() and apidb.layout(t) is None


def is_vb_global(e, nm):
    """VB's Global object (Unload, Load, App...), or a variable it was copied
    to: a frame slot the compiler reuses for it printed `v1E8 =` and
    `v1E8.Unload Form6` (a target: 27 empty assignments)."""
    if not isinstance(e, Var):
        return False
    if e.space == "global" and isinstance(e.off, int):
        return getattr(nm, "model", None) is not None and \
            nm.model.global_names.get(e.off) == ""
    it = nm.lf.objtype.get(e.key())
    return getattr(it, "name", None) in ("VBGlobal", "_Global", "Global")


def form_test(c, nm):
    """`frm = 0` / `frm <> 0` on a form's predeclared instance: VB's test before
    a use that creates the form first (the __vbaNew2 is gone, so both arms
    are alike, maybe in different shapes: a target "If Form6 = 0 Then
    Unload Form6: Exit Sub Else Unload Form6: Exit Sub")."""
    if not (isinstance(c, BinOp) and c.op in ("=", "<>") and isinstance(c.b, Const) and
            c.b.value == 0 and isinstance(c.a, Var) and c.a.space == "global"):
        return False
    model = getattr(nm, "model", None)
    name = model.global_names.get(c.a.off) if model is not None else None
    if name == "":
        return True                 # VB's Global object, created the same way before
                                    # Unload Me (a target Form17 "ElseIf  = 0 Then")
    return bool(name) and any(o.kind == "Form" and o.name == name
                              for o in model.prj.objects)


def vb_global_member(e, nm):
    """A method of VB's Global object (the lift resolved it through that
    interface), whatever variable holds the object now."""
    f = getattr(e, "func", None)
    model = getattr(nm, "model", None)
    if f is None or model is None:
        return False
    ids = model.__dict__.get("_vbglobal_funcs")
    if ids is None:
        it = next((model.global_types[va] for va, n in model.global_names.items()
                   if n == "" and va in model.global_types), None)
        ids = model._vbglobal_funcs = {id(g) for g in it.all_funcs()} if it is not None \
            else set()
    return id(f) in ids


# a Print item's VARTYPE -> the conversion that gives it that type, from the types it can't be taken for
_FLOATS = {"Single", "Double", "Currency", "Date", "Variant"}
PRINT_AS = {4: ("CSng", _FLOATS - {"Single"}), 5: ("CDbl", _FLOATS - {"Double"}),
            6: ("CCur", _FLOATS - {"Currency"}), 7: ("CDate", _FLOATS - {"Date"}),
            11: ("CBool", {"Integer", "Long", "Byte"} | _FLOATS)}


SCALAR_VALUES = {"Byte", "Boolean", "Integer", "Long", "Single", "Double", "Currency", "Date",
                 "String"}


def read_alone(e, nm):
    """What read this value has gone, and it can't stand as a statement: "Expected Sub" for v28(i),
    "Invalid use of property" for Me.List3.ListItems.  On an object variable a late bound read
    is a statement, and runs: k = o.Cnt + N("n2") with k unused still calls Cnt (t08late)."""
    if isinstance(e, Index):
        return True
    if not (isinstance(e, Call) and getattr(e, "loaded", False) and not e.args):
        return False
    ob = e.obj
    types = getattr(nm, "types", None)
    if not isinstance(ob, Var):
        return True
    if types is None or ob.key() in getattr(nm.lf, "mixed_objects", ()) or \
            ob.key() in getattr(nm.lf, "late_bound", ()):
        return False                # (declared As Object)
    return types.var_type(ob.key()) not in (None, "Object", "Variant")


def stmt(s, nm):
    if isinstance(s, (ExprStmt, CallStmt)) and read_alone(s.e, nm):
        return ""
    if DEREF_LOG and isinstance(s, Assign) and isinstance(s.src, AddrOf) and isinstance(s.dst, Var) and \
            s.dst.space in ("reg", "tmp", "local"):
        print("address kept: %s = %s in %s" % (s.dst, s.src, nm.lf.p.name), file=sys.stderr)
    from .machine import FSW, Hi
    if isinstance(s, Assign) and isinstance(s.src, FSW):
        return ""                   # the FPU status word left in eax by fnstsw: no value
                                    # of the program's (a target Proc_750D90 = '<<FSW>>)
    if isinstance(s, Assign) and isinstance(s.src, Hi) and isinstance(s.dst, Var) and s.dst.space == "reg":
        return ""                   # (the upper half of a value in two registers, still read where
                                    # the pair is an argument, printed once: regroup)
    if isinstance(s, Assign) and s.kind == "let":
        tv = two_values(s.src)
        if tv is not None:
            d = expr(s.dst, nm, 0)
            return "If %s Then %s = %s Else %s = %s" % (expr(tv[0], nm, 0), d,
                                                      expr(tv[1], nm, 0), d, expr(tv[2], nm, 0))
    if isinstance(s, Assign) and isinstance(s.src, Var) and s.src.space == "global" and \
            is_vb_global(s.src, nm):
        return ""                   # a copy of VB's Global object (its methods print bare)
    if isinstance(s, Assign):
        dst = s.dst
        if isinstance(dst, Index) and (isinstance(dst.arr, Var) or (
                isinstance(dst.arr, Deref) and not getattr(dst.arr, "inline", None))) and \
                not isinstance(s.src, Index):
            # a store to a whole element of an array of UDTs is to its first
            # field (__vbaStrCopy into &S_sfx(i): S_sfx(i).name = nm, VBDoom)
            from . import apidb
            t = nm.lf.udt.get(dst.arr.key()) if isinstance(dst.arr, Var) else nm.udt_of(dst.arr)
            lay = apidb.layout(t) if t else None
            st = getattr(nm, "types", None)
            src_t = nm.lf.udt.get(s.src.key()) if isinstance(s.src, Var) else \
                (st.typeof(s.src) if st is not None else None)
            if lay is not None and lay[0] and src_t != t and not (
                    isinstance(s.src, (Var, Deref)) and src_t is None and st is None):
                fo, fname, ftype = lay[0][0]
                if fo == 0 and ftype in apidb.SIZES:
                    dst = Deref(dst, apidb.SIZES[ftype], 0)
        elif isinstance(dst, Index) and getattr(dst.arr, "inline", None) and \
                not isinstance(s.src, Index) and getattr(s, "record", None) is None:
            # the same for an element of an array inside a record: rob(n).vars(i) = s, its name
            dst = Deref(dst, 4, 0)
        elif isinstance(dst, Var) and s.kind == "let" and nm.lf.udt.get(dst.key()) and \
                dst.space != "member" and \
                getattr(nm, "types", None) is not None and getattr(s, "record", None) is None and \
                nm.types.typeof(s.src) in SCALAR_VALUES:
            # a number or String stored in a record variable: its first field
            # (rec.h = CLng(...) in Text2, ReadAll-style; a whole record is a
            # Type mismatch)
            from . import apidb
            lay = apidb.layout(nm.lf.udt[dst.key()])
            if lay is not None and lay[0] and lay[0][0][0] == 0 and lay[0][0][2] in apidb.SIZES:
                dst = Deref(dst, apidb.SIZES[lay[0][0][2]], 0)
        if isinstance(dst, Index) and isinstance(dst.arr, Var) and s.kind != "set" and \
                array_record(dst.arr, nm) and getattr(s, "record", None) is None:
            # a number stored in a record element: its first field (a3(i).tipo = a1(j).tipo, the
            # element seen as one word when optimised code folded it)
            rec_ = array_record(dst.arr, nm)
            sr = s.src
            whole_ = isinstance(sr, Index) and isinstance(sr.arr, Var) and \
                array_record(sr.arr, nm) == rec_ or \
                isinstance(sr, Var) and nm.lf.udt.get(sr.key()) == rec_ or \
                isinstance(sr, Call) and getattr(sr, "small_rec", None)
            if not whole_:
                dst = field0(dst, nm)
        # (a member record copied whole; alone a member slot inside one prints as that field)
        nm.whole = getattr(s, "record", None) is not None and isinstance(dst, Var) and \
            dst.space == "member"
        d = expr(dst, nm, 0)
        nm.whole = False
        src = s.src
        if isinstance(src, Index) and isinstance(src.arr, Var) and s.kind != "set" and \
                array_record(src.arr, nm) and (
                    isinstance(dst, Var) and
                    nm.lf.udt.get(dst.key()) != array_record(src.arr, nm) or
                    isinstance(dst, (Deref, Index)) and getattr(nm, "types", None) is not None and
                    nm.types.typeof(dst) != array_record(src.arr, nm)) and not (
                    # (a whole element to one of the same array: a(i) = a(j), a String * 65 in it or not)
                    isinstance(dst, Index) and isinstance(dst.arr, Var) and
                    array_record(dst.arr, nm) == array_record(src.arr, nm)):
            # a record element assigned to a scalar: its first field (o1006 =
            # Places(i) of a String * 30 name; a whole record is a Type mismatch)
            src = field0(src, nm)
        if isinstance(src, Index) and isinstance(src.arr, Deref) and s.kind != "set" and \
                not getattr(src.arr, "inline", None) and getattr(s, "record", None) is None and \
                (isinstance(dst, Deref) and dst.size <= 8 or
                 isinstance(dst, Var) and not nm.lf.udt.get(dst.key())):
            # (the same of an array in a record's field: v(i).f0 = rob(n).dna(i).tipo)
            src = field0(src, nm)
        if isinstance(src, Index) and getattr(src.arr, "inline", None) and s.kind != "set" and \
                not isinstance(dst, Index) and getattr(s, "record", None) is None:
            src = Deref(src, 4, 0)
        v = expr(src, nm, 0)
        if s.kind == "set" and isinstance(s.src, Const) and s.src.value == 0:
            v = "Nothing"
        # an object value assigned to a Variant local needs Set too: the
        # compiler stores the ParseJson result into a Variant temp with a
        # plain copy (gh_json Main: roundtrip)
        setkw = s.kind == "set" or object_value(s.src, nm) or (
            # a compiler temporary of an object type copied with a plain mov
            # (v64 = v38 for two controls; v104 = lv.ListItems, late-bound)
            isinstance(s.dst, Var) and s.dst.space in ("local", "reg") and
            getattr(nm, "types", None) is not None and nm.lf.objtype.get(s.dst.key()) is not None and
            nm.types.var_type(s.dst.key()) not in SCALAR_VALUES and
            (s.dst.space != "reg" or nm.types.holds_object(s.dst.key())) and
            (isinstance(s.src, (Var, Call, Prop)) or
             # (a ByRef object parameter's object: v44 = a1, then v44.Tayta under On Error
             # Resume Next, where the missing Set only showed as nothing happening: t80arrmethod)
             isinstance(s.src, Deref) and s.src.off == 0 and isinstance(s.src.e, Var) and
             s.src.e.space == "arg") and not is_vb_global(s.dst, nm)) or (
            # two object variables: a copy (a merged call's a_6 = o1040, o1064 = m7C before a join),
            # and without Set the default property of an object that isn't there yet
            isinstance(s.dst, Var) and isinstance(s.src, Var) and
            getattr(nm, "types", None) is not None and not is_vb_global(s.dst, nm) and
            object_typed(nm.types.var_type(s.dst.key())) and
            object_typed(nm.types.var_type(s.src.key()))) or (
            # a variable that holds nothing but objects takes nothing without Set: a plain copy into it is
            # the default property of no object, error 91, unseen under On Error Resume Next (a target
            # vA8 = v70, a Variant of a pooled slot, then vA8.Count).  A compile error is the better way
            # for a wrong type to show
            isinstance(s.dst, Var) and isinstance(s.src, (Var, Deref, Call, Prop)) and
            getattr(nm, "types", None) is not None and not is_vb_global(s.dst, nm) and
            nm.types.var_type(s.dst.key()) == "Object")
        return "%s%s = %s" % ("Set " if setkw else "", d, v)
    if isinstance(s, (ExprStmt, CallStmt)):
        e = s.e
        if isinstance(e, Call) and isinstance(e.raw, str) and "!" in e.raw:
            api_whole_members(e)
        if isinstance(e, Call) and e.name == "__lateid":
            e.name = late_dispid_name(e) or e.name
        if isinstance(e, Prop) or isinstance(e, Call) and e.obj is not None and not e.args and \
                e.name in ("CurrentX", "CurrentY"):
            return ""               # a property read whose value went unused: VB has no
                                    # such statement (Printer.CurrentY: Invalid use of property)
        if isinstance(e, Call) and e.obj is None and e.name.startswith("ExitEach") and \
                not isinstance(e.raw, int):
            # __vbaExitEachVar / __vbaExitEachColl: the enumerator's release
            # on an Exit For out of a For Each, which the Exit For implies
            return None
        if isinstance(e, Call) and getattr(e, "stmt_fmt", None):
            # statements with their own syntax: Open ... For ... As #n, Close #n
            if e.stmt_fmt.startswith(("Put #", "Get #")) and (
                    getattr(e, "whole_array", None) is not None or
                    isinstance(e.args[-1], AddrOf) and isinstance(e.args[-1].e, AddrOf) and
                    isinstance(e.args[-1].e.e, (Deref, Index))):
                # a fixed array in a record, whole: VB builds a descriptor around its address (Get #n,
                # , rob(r).mem() printed rob(r).f2B38(0), 2 bytes for 2002)
                from . import apidb
                if getattr(e, "whole_array", None) is not None:
                    # (the element's subscript as the frame slot holds it: what the last argument
                    # has become, the slot being one with it)
                    x, slot = e.whole_array
                    y = e.args[-1].e if isinstance(e.args[-1], AddrOf) else e.args[-1]
                    if isinstance(x, Index):
                        x = Index(x.arr, [y if isinstance(i, Var) and i.key() == slot.key() else i
                                          for i in x.idx])
                    elif isinstance(x.e, Index):
                        x = Deref(Index(x.e.arr, [y if isinstance(i, Var) and i.key() == slot.key()
                                                  else i for i in x.e.idx]), x.size, x.off)
                else:
                    x = e.args[-1].e.e
                whole = expr(x, nm, 0)
                t = printed_type(x, whole, nm)
                f = apidb.field_at(t, 0) if t and "(" in (apidb.field_at(t, 0) or "") else None
                if f:
                    whole += "." + f            # (the first field of an element: Specie(k).Skin)
                import re
                m = re.match(r"^(.*)\([^()]*\)$", whole) or re.match(r"^(.*)$", whole)
                return e.stmt_fmt.format(*([expr(y, nm) for y in e.args[:-1]] + [m.group(1) + "()"]))
            if e.stmt_fmt.startswith(("Put #", "Get #")) and not getattr(e, "owner_io", False):
                # a String or a number at a record's address: its first field, not the record
                # (Put #1, , SimOpts.SimName; VB: "Can't Get or Put an object reference variable")
                from . import apidb
                y = e.args[-1].e if isinstance(e.args[-1], AddrOf) else e.args[-1]
                t = nm.lf.udt.get(y.key()) if isinstance(y, Var) else \
                    (array_record(y.arr, nm) if isinstance(y, Index) and isinstance(y.arr, Var) else None)
                lay = apidb.layout(t) if t and (not isinstance(y, Var) or y.key() not in nm.lf.arrays) \
                    else None
                n = getattr(e, "put_size", None)
                if lay is not None and (n is None or n < lay[1]):
                    return e.stmt_fmt.format(*([expr(x, nm) for x in e.args[:-1]] +
                                               [expr(Deref(y, n or 4, 0), nm)]))
                if isinstance(y, (Deref, Index)) and not lay:
                    # a field or an array element that is a record: its first field, as deep as
                    # the bytes say (Get #n, , rob(r).pos.x printed rob(r).pos, 8 bytes for 4)
                    text = expr(e.args[-1], nm)
                    t = printed_type(y, text, nm)
                    while t and apidb.layout(t) and (n is None or n < apidb.layout(t)[1]):
                        f = apidb.field_at(t, 0, whole=False)
                        if not f:
                            break
                        text += "." + f
                        lay0 = apidb.layout(t)[0]
                        ft = lay0[0][2] if lay0 and lay0[0][0] == 0 else None
                        t = ft if ft and apidb.layout(ft) else None
                    return e.stmt_fmt.format(*([expr(x, nm) for x in e.args[:-1]] + [text]))
            if e.stmt_fmt.startswith("Input #"):
                return e.stmt_fmt.format(*[expr(x if i == 0 else input_item(x, nm), nm)
                                           for i, x in enumerate(e.args)])
            return e.stmt_fmt.format(*[expr(x, nm) for x in e.args])
        if isinstance(e, Call) and e.name == "Erase" and e.obj is None and e.args:
            v = e.args[0].e if isinstance(e.args[0], AddrOf) else e.args[0]
            if isinstance(v, Const) and nm.lf.data_address(v):
                v = nm.lf.global_var(v.value & 0xffffffff)      # (Erase K.kulut: its data's address)
                if isinstance(v, Var):
                    return "Erase " + expr(v, nm, 0)
            if not isinstance(v, Var) and array_text(v, nm) is not None:
                return "Erase " + array_text(v, nm)     # (a fixed array in a record: its address)
            if isinstance(v, Var) and v.space == "member" and isinstance(v.off, int) and \
                    v.key() not in nm.lf.arrays and ("member", v.off + 12) in nm.lf.arrays:
                # a form's fixed array by its inline SAFEARRAY: pvData is 0Ch on, and
                # that is the variable (a target Form19 Erase m7C printed Erase m70)
                return "Erase " + expr(Var("member", v.off + 12), nm, 0)
        if isinstance(e, Call) and e.obj is None and not isinstance(e.raw, int) and \
                e.name in ("Seek", "Loc", "LOF", "EOF", "FileAttr", "GetAttr") and \
                len(e.args) == 1:
            # a file query whose value went unused: no statement (Seek 5 is
            # the Seek statement without its position, a Syntax error)
            return ""
        if isinstance(e, Call) and e.obj is not None and any(isinstance(x, Index) for x in e.args):
            # (a coordinate: a1.Line (pts(i), pts(i).f2)-...)
            args = [field0(x, nm) for x in e.args]
            if any(x is not y for x, y in zip(args, e.args)):
                e = e._keep(Call(e.name, args, e.vbtype, e.raw, e.method, e.obj, e.pure))
        g = graphics(e, nm)
        if g is not None:
            return g
        if isinstance(e, Call) and e.obj is not None:
            a = args_text([field0(x, nm) for x in regroup(e, nm)], nm)
            o = "" if is_vb_global(e.obj, nm) or vb_global_member(e, nm) else \
                expr(e.obj, nm, 100)
            name = "%s.%s" % (o, e.name) if o else e.name   # Load frmDoom: VB.Global's method
            return "%s %s" % (name, a) if a else name
        if isinstance(e, Call) and getattr(e, "redim", False):
            t = getattr(e, "redim_as", None)
            return "%s %s%s" % (e.name, expr(e.args[0], nm, 0), " As " + t if t else "")
        if isinstance(e, Call) and e.name == "RaiseEvent" and hasattr(e, "event"):
            a = args_text(e.args, nm)
            return "RaiseEvent %s%s" % (e.event, "(%s)" % a if a else "")
        if isinstance(e, Call) and e.raw == "__vbaCopyBytes" and len(e.args) == 3:
            a, b = e.args[0], e.args[1]
            ta = nm.lf.udt.get(a.e.key()) if isinstance(a, AddrOf) and isinstance(a.e, Var) else None
            tb = nm.lf.udt.get(b.e.key()) if isinstance(b, AddrOf) and isinstance(b.e, Var) else None
            bc = b.e if isinstance(b, AddrOf) else b
            if isinstance(bc, Call) and isinstance(bc.raw, int):
                tb = nm.model.ret_types.get(bc.raw)     # a Function returning the UDT
            n = e.args[2].value if isinstance(e.args[2], Const) else None

            def whole_element(x):
                # (an element of an array inside a record copied whole: rob(t).Ties(k) =
                # rob(t).Ties(k + 1) printed .f70(k).f0, an Integer)
                y = x.e if isinstance(x, AddrOf) else x
                if isinstance(y, Deref) and y.off == 0 and isinstance(y.e, Index) and \
                        getattr(y.e.arr, "inline", None) == n:
                    return AddrOf(y.e)
                return x
            a, b = whole_element(a), whole_element(b)
            sa, sb = nested_part(a, n, nm), nested_part(b, n, nm)
            if sa is not None or sb is not None:
                # a nested UDT field copied whole: fl.b = tmp (udt.nest_types)
                xa, ta = sa if sa is not None else (expr(a, nm, 0), ta)
                xb, tb = sb if sb is not None else (expr(b, nm, 0), tb)
                return ("%s = %s" if ta and ta == tb else "LSet %s = %s") % (xa, xb)
            if ta and ta == tb:
                return "%s = %s" % (expr(a, nm, 0), expr(b, nm, 0))     # same UDT: plain copy
            return "LSet %s = %s" % (expr(a, nm, 0), expr(b, nm, 0))
        if isinstance(e, Call) and e.name == "LSet" and len(e.args) == 2:
            return "LSet %s = %s" % (expr(e.args[0], nm, 0), string_field0(e.args[1], nm))
        if isinstance(e, Call):
            a = args_text(regroup(e, nm), nm)
            return "%s %s" % (e.name, a) if a else "%s" % e.name
        return expr(e, nm, 0)
    if isinstance(s, OnError):
        if s.mode == "GoTo" and s.target:
            va = getattr(nm.lf.p, "handlers", {}).get(s.target, s.target)
            return "On Error GoTo L%X" % int(va)
        return "On Error %s" % s.mode
    if isinstance(s, Free):
        return None
    if isinstance(s, ir.Return):
        v = getattr(s, "value", None)
        if not nm.is_function or v is None:
            return None
        if isinstance(v, Var) and v.space == "local" and v.off == nm.lf.result_slot:
            return None
        m = getattr(nm.lf.p, "member", None)
        obj = (m is not None and m.ret_type in ("Object", "Class")) or isinstance(v, Me) or \
            getattr(v, "iface", None) is not None
        tv = None if obj else two_values(v)
        if tv is not None:
            return "If %s Then %s = %s Else %s = %s" % (expr(tv[0], nm, 0), nm.proc_name,
                                                      expr(tv[1], nm, 0), nm.proc_name,
                                                      expr(tv[2], nm, 0))
        return "%s%s = %s" % ("Set " if obj else "", nm.proc_name, expr(v, nm, 0))
    return "' %r" % (s,)


def render_region(r, nm, labels, out, ind, lf, tail=True, elseif=False):
    """tail: nothing runs after this region (an Exit here is redundant)."""
    pad = "    " * ind
    if isinstance(r, Seq):
        items = [x for x in r.items if not (isinstance(x, Block) and not visible(x, nm, labels))
                 or (isinstance(x, Block) and x.va in labels)]
        for i, x in enumerate(items):
            later = items[i + 1:]
            last = i == len(items) - 1 or all(isinstance(y, Exit) for y in later)
            if any(isinstance(y, Handlers) for y in later):
                last = False                # error handlers follow: keep the Exit
            render_region(x, nm, labels, out, ind, lf, tail and last)
        return
    if isinstance(r, Block):
        if r.va in labels:
            out.append("L%X:" % int(r.va))
        for s in r.stmts:
            t = stmt(s, nm)
            if t:
                out.append(pad + t)
        return
    if isinstance(r, If):
        if empty(r.then, nm, labels, tail) and empty(r.els, nm, labels, tail):
            if elseif:
                out.append(pad + "End If")  # (the outer If left its End If to this one)
            return
        if r.then is not None and r.els is not None and form_test(r.cond, nm):
            # a predeclared form's test (form_test) whose arms are alike once the
            # New is gone, as an ElseIf too ("ElseIf Walk = 0 Then Unload
            # Walk Else Unload Walk"), or whose Else only jumps to the
            # start of the Then ("If Form1 = 0 Then L: ... Else GoTo L")
            a_lines, b_lines = [], []
            save = dict(nm.__dict__)
            render_region(r.then, nm, labels, a_lines, ind + 1, lf, tail)
            nm.__dict__.update(save)
            render_region(r.els, nm, labels, b_lines, ind + 1, lf, tail)
            nm.__dict__.update(save)
            first = next((ln.strip() for ln in a_lines if ln.strip()), "")
            jumps_in = len([ln for ln in b_lines if ln.strip()]) == 1 and \
                first.startswith("L") and first.endswith(":") and \
                b_lines[0].strip() == "GoTo " + first[:-1]
            # (alike but for their numbered temporaries: Set o1132 = frm: P v, o1132)
            import re
            alike = [re.sub(r"\bo\d+(_[0-9A-F]+)?\b", "o", x) for x in a_lines] == \
                [re.sub(r"\bo\d+(_[0-9A-F]+)?\b", "o", x) for x in b_lines]
            if a_lines == b_lines or jumps_in or alike:
                if elseif:
                    out.append(pad + "Else")
                    render_region(r.then, nm, labels, out, ind + 1, lf, tail)
                    out.append(pad + "End If")
                else:
                    render_region(r.then, nm, labels, out, ind, lf, tail)
                return
        if r.then is not None and r.els is not None and not elseif and \
                shape(r.then, nm) == shape(r.els, nm):
            # "If c Then A Else A" with a c that cannot fail or do anything:
            # A (unoptimised code tests a predeclared form before each use,
            # and the shared tail was copied into both arms: VBDoom
            # "If frmDoom = 0 Then frmDoom.SetFocus ... Else frmDoom.SetFocus").
            # Only arms of the same shape are rendered to compare: rendering
            # both arms of every If, then again for the output, rendered an
            # If nested d deep 2^d times (ChessBrainVB Eval never finished).
            from .simplify import safe_cond
            a_lines, b_lines = [], []
            save = dict(nm.__dict__)
            render_region(r.then, nm, labels, a_lines, ind, lf, tail)
            nm.__dict__.update(save)
            render_region(r.els, nm, labels, b_lines, ind, lf, tail)
            nm.__dict__.update(save)
            if a_lines == b_lines and safe_cond(r.cond) and \
                    not any(ln.lstrip().startswith("L") and ln.rstrip().endswith(":")
                            for ln in a_lines):
                render_region(r.then, nm, labels, out, ind, lf, tail)
                return
        if empty(r.then, nm, labels, tail):         # If c Then (nothing) Else B
            r = If(structure.negate(r.cond), r.els, None)
        out.append(pad + ("ElseIf %s Then" if elseif else "If %s Then") % expr(r.cond, nm, 0))
        if r.then is not None:
            render_region(r.then, nm, labels, out, ind + 1, lf, tail)
        inner = sole_if(r.els, nm, labels, tail)
        if inner is not None:
            render_region(inner, nm, labels, out, ind, lf, tail, elseif=True)
            return
        if r.els is not None and not empty(r.els, nm, labels, tail):
            out.append(pad + "Else")
            render_region(r.els, nm, labels, out, ind + 1, lf, tail)
        out.append(pad + "End If")
        return
    if isinstance(r, Loop):
        if getattr(r, "head", None) in labels and r.kind == "while":
            out.append("L%X:" % int(r.head))
        if r.kind == "while":
            out.append(pad + "Do While %s" % expr(r.cond, nm, 0))
        else:
            out.append(pad + "Do")
        nm.loops.append("Do")
        render_region(r.body, nm, labels, out, ind + 1, lf, False)
        nm.loops.pop()
        if r.kind == "loopwhile":
            out.append(pad + "Loop While %s" % expr(r.cond, nm, 0))
        else:
            out.append(pad + "Loop")
        return
    if isinstance(r, For):
        if isinstance(r.step, int):
            step = "" if r.step == 1 else " Step %d" % r.step
        elif isinstance(r.step, float):
            # a Single / Double counter's constant step (fadd [1.5!]: t18varfor)
            ty = getattr(nm, "types", None)
            vt = ty.typeof(r.var) if ty is not None else None
            step = "" if r.step == 1 else \
                " Step %s" % expr(Const(r.step, vt if vt in ("Single", "Double") else None), nm, 0)
        else:                           # a Variant For's step (structure.as_varfor)
            step = "" if isinstance(r.step, Const) and r.step.value == 1 else \
                " Step %s" % expr(r.step, nm, 0)
        v = expr(r.var, nm, 0)
        out.append(pad + "For %s = %s To %s%s" % (v, expr(r.start, nm, 0), expr(r.end, nm, 0), step))
        nm.loops.append("For")
        render_region(r.body, nm, labels, out, ind + 1, lf, False)
        nm.loops.pop()
        out.append(pad + "Next " + v)
        return
    if isinstance(r, ForEach):
        v = expr(r.var, nm, 0)
        out.append(pad + "For Each %s In %s" % (v, expr(r.coll, nm, 0)))
        nm.loops.append("For")
        render_region(r.body, nm, labels, out, ind + 1, lf, False)
        nm.loops.pop()
        out.append(pad + "Next " + v)
        return
    if isinstance(r, Select):
        out.append(pad + "Select Case %s" % expr(field0(r.subject, nm), nm, 0))
        for cl, body in r.cases:
            parts = []
            for c in cl:
                if c[0] == "eq":
                    # (like the subject: a target Proc_961530 Case global_F071F4(v24))
                    parts.append(expr(field0(c[1], nm), nm, 8))
                elif c[0] == "range":
                    parts.append("%s To %s" % (expr(c[1], nm, 8), expr(c[2], nm, 8)))
                else:
                    parts.append("Is %s %s" % (c[1], expr(c[2], nm, 8)))
            out.append(pad + "Case " + ", ".join(parts))
            if body is not None:
                render_region(body, nm, labels, out, ind + 1, lf, tail)
        if r.els is not None and not empty(r.els, nm, labels, tail):
            out.append(pad + "Case Else")
            render_region(r.els, nm, labels, out, ind + 1, lf, tail)
        out.append(pad + "End Select")
        return
    if isinstance(r, Goto):
        out.append(pad + "GoTo L%X" % int(r.target))
        return
    if isinstance(r, structure.ResumeTo):
        out.append(pad + "Resume L%X" % int(r.target))
        return
    if isinstance(r, Exit):
        if not tail:
            m = getattr(lf.p, "member", None)
            kind = "Property" if m is not None and m.kind in PROPERTY else \
                ("Function" if nm.is_function else "Sub")
            out.append(pad + "Exit " + kind)
        return
    if isinstance(r, ExitLoop):
        out.append(pad + "Exit " + (nm.loops[-1] if nm.loops else "Do"))
        return
    if isinstance(r, Handlers):
        return
    out.append(pad + "' %r" % (r,))


def empty(r, nm, labels, tail=True):
    """Nothing to print.  An Exit counts as nothing only where the procedure
    ends anyway (tail); inside a loop it leaves early (gh_qr IsAlphanumeric)."""
    if r is None:
        return True
    if isinstance(r, Seq):
        return all(empty(x, nm, labels, tail) for x in r.items)
    if isinstance(r, Block):
        return not visible(r, nm, labels)
    if isinstance(r, Exit):
        return tail
    if isinstance(r, If):
        return empty(r.then, nm, labels, tail) and empty(r.els, nm, labels, tail)
    return False


def visible(b, nm, labels):
    if b.va in labels:
        return True
    seen = nm.__dict__.setdefault("_visible", {})     # (asked at every nesting level)
    v = seen.get(id(b))
    if v is None or v[0] is not b:
        v = seen[id(b)] = (b, any(stmt(s, nm) for s in b.stmts))
    return v[1]


def shape(r, nm):
    """A region's structure and statements as a value: two arms compare
    without rendering them.  Each region's is made once per procedure."""
    if r is None:
        return None
    memo = nm.__dict__.setdefault("_shapes", {})
    v = memo.get(id(r))
    if v is not None and v[0] is r:
        return v[1]
    if isinstance(r, Seq):
        s = ("seq",) + tuple(shape(x, nm) for x in r.items)
    elif isinstance(r, Block):
        s = ("block",) + tuple(repr(x) for x in r.stmts)
    elif isinstance(r, If):
        s = ("if", repr(r.cond), shape(r.then, nm), shape(r.els, nm))
    elif isinstance(r, Loop):
        s = ("loop", r.kind, repr(r.cond), shape(r.body, nm))
    elif isinstance(r, For):
        s = ("for", repr(r.var), repr(r.start), repr(r.end), repr(r.step), shape(r.body, nm))
    elif isinstance(r, ForEach):
        s = ("foreach", repr(r.var), repr(r.coll), shape(r.body, nm))
    elif isinstance(r, Select):
        s = ("select", repr(r.subject), tuple((repr(c), shape(b, nm)) for c, b in r.cases),
             shape(r.els, nm))
    elif isinstance(r, Goto):
        s = ("goto", r.target)
    else:
        s = (type(r).__name__,)
    memo[id(r)] = (r, s)
    return s


def sole_if(r, nm, labels, tail=True):
    """An Else branch that is just another If becomes ElseIf."""
    if not isinstance(r, Seq):
        return None
    if not tail and any(isinstance(x, Exit) for x in r.items):
        return None                 # the Exit after it matters
    items = [x for x in r.items if not isinstance(x, Exit) and
             not (isinstance(x, Block) and not visible(x, nm, labels))]
    if len(items) == 1 and isinstance(items[0], If):
        return items[0]
    return None


def merge_halves(body, lf):
    """A Double (Currency, Date) field of a UDT stored as two dwords becomes
    one store of the literal.  A half never written in the procedure's first
    block is the zero VB initialised the variable with."""
    import struct
    from . import apidb

    def wide_field(d):
        b = d.e.arr if isinstance(d.e, Index) else d.e
        t = lf.udt.get(b.key()) if hasattr(b, "key") else None
        if t is None:
            return None
        ft = apidb.field_type(t, d.off)
        return ft if ft in ("Double", "Currency", "Date") else None

    out = []
    for bi, (a, ss, br) in enumerate(body):
        ss = list(ss)
        for i, s in enumerate(ss):
            if not (isinstance(s, Assign) and isinstance(s.dst, Deref) and s.dst.size == 4 and
                    isinstance(s.src, Const) and isinstance(s.src.value, int)):
                continue
            lo_d = Deref(s.dst.e, 4, s.dst.off - 4)
            ft = wide_field(lo_d)
            if ft is None or apidb.field_at(lf.udt.get(getattr(s.dst.e, "arr", s.dst.e).key()),
                                            s.dst.off - 4) != apidb.field_at(
                    lf.udt.get(getattr(s.dst.e, "arr", s.dst.e).key()), s.dst.off):
                continue                # not the high half of an 8-byte field
            lo = None
            for j in range(i - 1, -1, -1):
                t = ss[j]
                if isinstance(t, Assign) and t.dst == lo_d and isinstance(t.src, Const):
                    lo = t.src
                    ss[j] = None
                    break
            if lo is None and bi == 0:
                lo = Const(0)           # the initial value
            if lo is None:
                continue
            raw = struct.pack("<II", lo.value & 0xffffffff, s.src.value & 0xffffffff)
            v = struct.unpack("<q", raw)[0] / 10000.0 if ft == "Currency" else \
                struct.unpack("<d", raw)[0]
            n = Assign(Deref(s.dst.e, 8, s.dst.off - 4), Const(v, ft if ft != "Double" else None))
            n.va = getattr(s, "va", None)
            ss[i] = n
        out.append((a, [s for s in ss if s is not None], br))
    return out


def proc_source(model, lf, body):
    body = merge_halves(body, lf)
    st = structure.Structurer(lf, body)
    region, labels = st.run()
    region = structure.recover(region)
    from .simplify import var_index
    region = structure.fold_for_limits(region, lf, var_index(body)[1])
    region = structure.selects(region)
    region = structure.skip_gotos(region, labels)
    from .types import Types
    # a parameter IsMissing tests is an Optional Variant, whatever else it
    # takes part in (a target Proc_724340's ByVal a3 As Long: 48 calls
    # leaving it out were "Argument not optional")
    for x in (x for _, ss, br in body
              for e in [e for s in ss for e in getattr(s, "exprs", lambda: ())()] +
              ([br.cond] if br is not None else []) for x in e.walk()):
        if isinstance(x, Call) and x.name == "IsMissing" and x.args:
            a = x.args[0]
            a = a.e if isinstance(a, (Deref, AddrOf)) and getattr(a, "off", 0) == 0 else a
            if isinstance(a, Var) and a.space == "arg" and isinstance(a.off, int):
                lf.__dict__.setdefault("variant_params", set()).add(a.off)
    ty = Types(lf, body, model)
    nm = Names(lf, ty)
    nm.model = model
    nm.is_function = is_function(model, lf)
    model.plists[lf.p.va] = nm.plist
    for i, k in enumerate(nm.known):
        model.param_known[(lf.p.va, i)] = k
    lf.plist = nm.plist
    # IsMissing(param): the parameter is Optional (a target Lock1: 21
    # callers leave it out through a ByRef Variant temporary)
    tested = set()
    for _, ss, br in body:
        es = [e for x in ss for e in getattr(x, "exprs", lambda: ())()]
        if br is not None:
            es.append(br.cond)
        for e in es:
            for x in e.walk():
                if isinstance(x, Call) and x.name == "IsMissing" and x.args:
                    a = x.args[0]
                    a = a.e if isinstance(a, Deref) and a.off == 0 else a
                    if isinstance(a, Var) and a.space == "arg":
                        tested.add(a.off)
    for i, (off, _, t, _) in enumerate(nm.plist):
        if off in tested and t == "Variant":
            model.__dict__.setdefault("optional_params", set()).add((lf.p.va, i))
    lines = []
    render_region(region, nm, labels, lines, 1, lf)
    # a GoTo / On Error GoTo whose target is the shared epilogue (an error
    # handler that just leaves: "On Error GoTo QH ... QH: End Function")
    import re
    defined = {m.group(1) for m in (re.match(r"^L([0-9A-F]+):", x) for x in lines) if m}
    wanted = {m for x in lines for m in re.findall(r"GoTo L([0-9A-F]+)\b", x)}
    for t in sorted(wanted - defined):
        va = int(t, 16)
        n = lf.nodes.get(va)
        if n is not None and n.kind == "exit" and not any(
                stmt(x, nm) for x in st.stmts.get(va, [])):
            lines.append("L%s:" % t)
        elif n is None and va in getattr(lf.p, "handlers", {}).values():
            # a handler inside the last block (falls through to the exit
            # right after __vbaExitProc, gh_zip At): nothing visible after it
            own = max((a for a in lf.nodes if a < va), default=None)
            if own is not None and lf.nodes[own].kind == "exit" and not any(
                    stmt(x, nm) for x in st.stmts.get(own, [])
                    if (getattr(x, "va", None) or 0) >= va):
                lines.append("L%s:" % t)
    head = signature(model, lf, nm, ty)
    decls = []
    from .types import array_decl
    for off, n in sorted(nm.locals.items(), key=lambda kv: str(kv[0])):
        if n == nm.proc_name:
            continue
        info = lf.arrays.get(("local", off))
        if info is not None:
            if lf.udt.get(("local", off)):
                info = dict(info, udt=lf.udt[("local", off)])     # an array of UDTs
            decls.append("    Dim " + array_decl(n, info))
            continue
        if isinstance(off, tuple) and isinstance(off[0], str):
            # a numbered version of a register or pushed-argument slot
            # (a parameter's slot reused for a temporary is a local of its own: simplify.
            # split_register_webs.  As a register no evidence found it: Dim t_arg2001, a Variant
            # holding what fstp dword meant as a Single, and its digits changed a result)
            key = ("stk", off[1:]) if off[0] == "stk" else \
                ("local", off) if off[0] == "arg" else ("reg", off)
        else:
            key = ("local", off)
        t = lf.udt.get(key) or ty.var_type(key)
        if key in getattr(lf, "mixed_objects", ()) and t not in SCALAR_VALUES:
            # controls of two types in one slot (only here: as evidence, Object
            # spread through call sites to Proc_8B3AF0(a1 As Object), 16 errors)
            t = "Object"
        fxs = getattr(lf, "fixed_strings", {})
        fx = fxs.get(key) or (fxs.get(("local", off[0])) if isinstance(off, tuple) and
                              isinstance(off[0], int) else None)    # (a numbered version)
        if fx and t in (None, "String"):
            t = "String * %d" % fx              # (Get / Put of a String * n)
        if key in lf.late_bound and t != "Variant":
            t = "Object"                # (a Variant stays one: IsEmpty(v) after a failed Set v = ...)
        if key in getattr(lf, "auto_new", {}):
            t = "New " + lf.auto_new[key]       # created on first use
        decls.append(dim_line(n, t))
    for r, n in sorted(nm.regs.items()):
        t = ty.var_type(("reg", r))
        if ("reg", r) in getattr(lf, "mixed_objects", ()) and ty.holds_object(("reg", r)) and \
                t not in SCALAR_VALUES:
            t = "Object"                # controls of two types (Form11 t_edi.Text = x)
        decls.append(dim_line(n, t))
    for k, n in sorted(nm.stks.items(), key=lambda kv: kv[1]):
        t = ty.var_type(("stk", k))
        decls.append(dim_line(n, t))
    for k, n in sorted(nm.tmps.items(), key=lambda kv: kv[1]):
        t = ty.var_type(("tmp", k)) or lf.tmp_types.get(("tmp", k))
        decls.append(dim_line(n, t))
    while lines and lines[-1].strip() in ("Exit Sub", "Exit Function", "Exit Property"):
        lines.pop()                 # leaving at the end is just the end
    m = getattr(lf.p, "member", None)
    if m is not None and m.kind in PROPERTY:
        tail = "End Property"
    else:
        tail = "End " + ("Function" if nm.is_function else "Sub")
    return "\n".join([head] + decls + lines + [tail])


def dim_line(n, t):
    """Dim n As t; an array type ("Byte()", a Function's result) as Dim n() As Byte."""
    if t and t.endswith("()"):
        return "    Dim %s() As %s" % (n, t[:-2])
    return "    Dim %s%s" % (n, " As " + t if t else "")


def is_function(model, lf):
    if getattr(lf.p, "kind", None) == "event":
        return False
    m = getattr(lf.p, "member", None)
    if m is not None:
        return m.returns
    if lf.result_slot is not None or getattr(lf.p, "float_ret", False) or \
            getattr(lf.p, "udt_ret", False):
        return True
    return bool(model.proc_returns.get(lf.p.va))


PROPERTY = {"get": "Property Get", "let": "Property Let", "set": "Property Set"}


def member_type(model, t, cls=None):
    """VB type name of a member descriptor type (a project class by name)."""
    if t == "Class":
        o = model.obj_by_info.get(cls) if cls else None
        return o.name if o is not None else "Object"
    return t or "Variant"


def default_text(d):
    import datetime
    vt, v = d
    if vt == 8:
        return '"%s"' % v.replace('"', '""')
    if vt == 11:
        return "True" if v else "False"
    if vt == 7:
        day = datetime.datetime(1899, 12, 30) + datetime.timedelta(days=v)
        return "#%d/%d/%d#" % (day.month, day.day, day.year)
    if isinstance(v, float):
        return repr(v) if v != int(v) else "%d" % v
    return str(v)


def member_signature(model, lf, nm, m):
    kind = PROPERTY.get(m.kind) or ("Function" if m.returns else "Sub")
    args = []
    for prm in m.params:
        t = member_type(model, prm.type, prm.cls)
        if prm.paramarray:
            args.append("ParamArray %s() As %s" % (prm.name, t))
            continue
        a = "%s%s%s%s As %s" % ("Optional " if prm.optional else "",
                                "ByVal " if not prm.byref and not prm.array else "",
                                prm.name, "()" if prm.array else "", t)
        if prm.optional and prm.default is not None:
            a += " = " + default_text(prm.default)
        args.append(a)
    rt = ""
    if m.returns:
        rt = " As " + member_type(model, m.ret_type, m.ret_cls) + ("()" if m.ret_array else "")
    # WithEvents handlers have descriptors too, for late binding: still Private
    # (unless another object calls one: Form1.t_MouseDown 1, Public Sub in DarwinBots' source)
    vis = "Private" if m.event_slot is not None and not getattr(lf.p, "public", False) else "Public"
    if lf.obj is not None and m.name and "_" in m.name and \
            m.name.split("_")[0] in model.implements_of(lf.obj):
        vis = "Private"         # (IAnimal_Speak: reached through the interface, as VB wants it written)
    return "%s %s %s(%s)%s" % (vis, kind, nm.proc_name, ", ".join(args), rt)


def signature(model, lf, nm, ty):
    p = lf.p
    name = nm.proc_name
    ev = getattr(p, "event", None)
    m = getattr(p, "member", None)
    if m is not None and ev is None:
        if m.returns:
            model.ret_types[p.va] = member_type(model, m.ret_type, m.ret_cls)
        return member_signature(model, lf, nm, m)
    args = []
    from .types import array_decl
    opt = [i for i in range(len(nm.plist))
           if (p.va, i) in getattr(model, "optional_params", ())]
    first_opt = min(opt) if opt else None
    for i, (off, pname, t, byref) in enumerate(nm.plist):
        if first_opt is not None and i >= first_opt and ev is None:
            # Optional from the first one a caller leaves out (VB wants the rest too)
            if t == "Variant":
                args.append("Optional %s%s As Variant" % ("" if byref else "ByVal ", pname))
            elif t in ("String",):
                args.append('Optional %s%s As String = ""' % ("" if byref else "ByVal ", pname))
            elif t in ("Long", "Integer", "Byte", "Double", "Single", "Currency", "Boolean"):
                args.append("Optional %s%s As %s = 0" % ("" if byref else "ByVal ", pname, t))
            else:
                args.append("Optional %s%s As %s" % ("" if byref else "ByVal ", pname, t))
            continue
        info = lf.arrays.get(("arg", off))
        if info is not None and info.get("dyn") and off in lf.byref:
            if lf.udt.get(("arg", off)) and not info.get("udt"):
                info = dict(info, udt=lf.udt[("arg", off)])     # an array of UDTs
            args.append(array_decl(pname, info))     # an array is always ByRef
        elif ev is not None:
            args.append("%s%s As %s" % ("" if byref else "ByVal ", pname, t))
        else:
            args.append("%s%s As %s" % ("" if byref else "ByVal ", pname, t))
    kind = "Function" if nm.is_function else "Sub"
    vis = "Private" if ev is not None and not getattr(p, "public", False) or \
        p.va in getattr(model, "private_procs", ()) else "Public"
    if lf.obj is not None and ev is None and not getattr(p, "public", False):
        vis = "Private"         # forms keep names only for their Public procedures
    if getattr(p, "friend", False):
        vis = "Friend"          # checks Me's vtable: callable from other modules
    rt = ""
    if nm.is_function:
        r = ty.result_type() or "Long"
        model.ret_types[p.va] = r
        rt = " As " + r
    return "%s %s %s(%s)%s" % (vis, kind, name, ", ".join(args), rt)


def vbtype(param):
    # an object is a pointer to its interface, and an out parameter one more
    # (a target: As Printer*); VB names have no pointers
    t = param.type
    if t.vt == 26 and t.sub is not None:
        return t.sub.name(None).rstrip("*")
    return t.name(None).rstrip("*")
