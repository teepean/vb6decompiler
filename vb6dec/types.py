"""Variable, parameter and result types from the evidence in the lifted code."""
import os
import sys
from .ir import Const, Str, Var, Deref, BinOp, UnOp, Call, Prop, Assign, ExprStmt, AddrOf
from . import ir
from .lift import CallStmt

# runtime functions whose first argument is a String
STRING_ARG = {"__vbaI4Str", "__vbaI2Str", "__vbaR8Str", "__vbaR4Str", "__vbaBoolStr",
              "__vbaUI1Str", "__vbaLenBstr", "__vbaLenBstrB", "rtcLeftCharBstr",
              "rtcRightCharBstr", "rtcMidCharBstr", "rtcUpperCaseBstr", "rtcLowerCaseBstr",
              "rtcTrimBstr", "rtcLeftTrimBstr", "rtcRightTrimBstr", "rtcAnsiValueBstr",
              "rtcR8ValFromBstr", "__vbaStrCmp", "__vbaStrComp"}
# functions whose String parameters (by position) say a variable is a String
STRING_ARGS_OF = {"Replace": (0, 1, 2), "InStrRev": (0, 1), "Split": (0, 1), "StrConv": (0,),
                  "StrReverse": (0,)}
STRING_FN = {"Left$", "Right$", "Mid$", "UCase$", "LCase$", "Trim$", "LTrim$", "RTrim$",
             "Len", "Asc", "Val", "InStr"}


def exprs_of(s):
    if isinstance(s, Assign):
        return [s.dst, s.src]
    if isinstance(s, (ExprStmt, CallStmt)):
        return [s.e]
    if isinstance(s, ir.Return) and getattr(s, "value", None) is not None:
        return [s.value]
    return []


# VB widening for + - *: the operand type that ranks higher wins
RANK = {"Byte": 0, "Boolean": 1, "Integer": 2, "Long": 3, "Single": 4, "Double": 5,
        "Currency": 6}
INTEGRAL = {"Byte", "Boolean", "Integer", "Long"}
NUMERIC = INTEGRAL | {"Single", "Double", "Currency"}
# what only the FPU computes with (Currency is done in integer registers
# and fild qword: gh_zip pvVfsSeek's ByVal cPosition; Date is mostly moved)
FLOATS = {"Single", "Double"}
PUT_WIDTHS = {1: "Byte", 2: "Integer", 4: "Long", 8: "Double"}     # Put / Get byte counts
# parameter types a ByRef argument must match exactly
SCALAR = {"Byte", "Boolean", "Integer", "Long", "Single", "Double", "Currency", "Date",
          "String"}


def arith(op, ta, tb):
    """Result type of a binary arithmetic or logical operator."""
    if ta is None or tb is None or ta not in RANK or tb not in RANK:
        return ta or tb
    if op in ("\\", "Mod", "And", "Or", "Xor", "Eqv", "Imp"):
        if op not in ("\\", "Mod") and ta == tb == "Boolean":
            return "Boolean"
        r = max(RANK[ta], RANK[tb])
        return "Long" if r >= RANK["Long"] else ("Byte" if r == 0 else "Integer")
    if {ta, tb} == {"Long", "Single"}:
        return "Double"
    t = ta if RANK[ta] >= RANK[tb] else tb
    return "Integer" if t == "Boolean" else t


def param_args(plist, args):
    """(argument, parameter index) for each pushed dword that starts a
    parameter of a decompiled procedure (a ByVal Double takes two dwords,
    a ByVal Variant four)."""
    if not plist:
        return []
    first = plist[0][0]
    starts = {(off - first) // 4: pi for pi, (off, _, _, _) in enumerate(plist)}
    return [(a, starts[i]) for i, a in enumerate(args) if i in starts]


class Types:
    def __init__(self, lf, body, model):
        self.lf, self.body, self.m = lf, body, model
        self.ev = {}            # var key -> set of type names
        self.exact = {}         # var key -> type of the ByRef parameter it is passed to
        self.elem = {}          # array key -> types of what its elements hold
        self.newcls = {}        # var key -> the class it was created with (New C)
        self._int_args = None
        self.collect()

    def fp_stores(self):
        """{variable key: widths fstp writes it with}."""
        c = self.__dict__.get("_fp_stores")
        if c is None:
            c = self._fp_stores = {}
            for _, ss, _ in self.body:
                for s in ss:
                    w = getattr(s, "fp_store", None)
                    if w and isinstance(s, Assign) and isinstance(s.dst, Var):
                        c.setdefault(s.dst.key(), set()).add(w)
        return c

    def holds_object(self, key):
        """Does a register's variable get an object in the final body?"""
        got = self.__dict__.get("_reg_objects")
        if got is None:
            got = self._reg_objects = set()
            for _, ss, _ in self.body:
                for s in ss:
                    if isinstance(s, Assign) and isinstance(s.dst, Var) and s.dst.space == "reg" \
                            and (getattr(s.src, "iface", None) is not None or
                                 getattr(s.src, "control", None) is not None or
                                 isinstance(s.src, Var) and (
                                     self.lf.objtype.get(s.src.key()) is not None or
                                     s.src.space == "global" and
                                     self.m.global_types.get(s.src.off) is not None)):
                        got.add(s.dst.key())
        return key in got

    def arg_end(self):
        """The arg key offset past the last parameter, from the procedure's
        ret N (less a method's result pointer)."""
        lf = self.lf
        end = 4 + lf.p.ret_pop
        if lf.obj is not None and lf.result_slot is not None:
            end -= 4
        return end

    def int_used(self):
        """Variables the procedure does integer arithmetic on (And, \\, a 32-bit
        add or imul, a compare of 4 bytes) or indexes an array with: no
        Single and no 8-byte value, whatever else reaches them (ChessBrainVB:
        GenerateMoves' Ply indexes Moves(Ply, n); a Double from its callers
        had made it and bCapturesOnly one parameter)."""
        if getattr(self, "_int_used", None) is None:
            out = set()
            for a, ss, br in self.body:
                es = [e for s in ss for e in exprs_of(s)] + ([br.cond] if br is not None else [])
                for e in es:
                    for x in e.walk():
                        if isinstance(x, BinOp) and (x.op in INT_OPS or
                                                     getattr(x, "width", None) in (1, 2, 4)):
                            for y in (x.a, x.b):
                                if isinstance(y, Var):
                                    out.add(y.key())
                        elif isinstance(x, ir.Index) and not getattr(x, "vbtype", None):
                            for y in self.subscript_vars(x):
                                out.add(y.key())
            self._int_used = out
        return self._int_used

    def subscripts(self):
        """Variables used as an array's subscript."""
        if getattr(self, "_subs", None) is None:
            out = set()
            for a, ss, br in self.body:
                es = [e for s in ss for e in exprs_of(s)] + ([br.cond] if br is not None else [])
                for e in es:
                    for x in e.walk():
                        if isinstance(x, ir.Index) and not getattr(x, "vbtype", None):
                            for y in self.subscript_vars(x):
                                out.add(y.key())
            self._subs = out
        return self._subs

    def int_args(self):
        """Parameter slots the procedure's own code does integer arithmetic
        on (And, \\, a 32-bit add or imul, a compare of 4 bytes) or indexes
        an array with: no 8-byte ByVal value."""
        if self._int_args is None:
            self._int_args = {k for k in self.int_used() if k[0] == "arg"}
        return self._int_args

    @staticmethod
    def subscript_vars(x):
        """The variables an array subscript adds up (i, i + 1, i * 151 + j)."""
        from .lift import Lifter
        out = []
        for idx in x.idx:
            for e, c in Lifter.linear(idx).values():
                if isinstance(e, Var) and c:
                    out.append(e)
        return out

    def is_variant(self, e):
        """A Variant variable, ByRef Variant parameter or Variant array element."""
        if isinstance(e, Deref) and e.off == 0:
            e = e.e
        if isinstance(e, ir.Index) and isinstance(e.arr, Var):
            info = self.lf.arrays.get(e.arr.key()) or {}
            return info.get("vt") == 12 or info.get("size") == 16 and not self.lf.udt.get(e.arr.key())
        return isinstance(e, Var) and e.key() in self.lf.variants

    def add(self, e, t):
        if t is None:
            return
        if isinstance(e, Deref) and isinstance(e.e, Var) and e.e.space == "arg" and e.off == 0:
            e = e.e
        if isinstance(e, Var):
            self.ev.setdefault(e.key(), set()).add(t)

    def typeof(self, e):
        if isinstance(e, Str):
            return "String"
        if isinstance(e, Const):
            if e.vbtype:
                return e.vbtype
            if isinstance(e.value, float):
                return "Double"
            return "Long" if isinstance(e.value, int) else None
        if isinstance(e, BinOp):
            if e.op == "&":
                return "String"
            if e.op in ("=", "<>", "<", ">", "<=", ">="):
                return "Boolean"
            if e.vbtype:
                return e.vbtype
            ta, tb = self.typeof(e.a), self.typeof(e.b)
            if e.op == "/":
                return "Double"
            t = arith(e.op, ta, tb)
            if getattr(e, "fp", False) and t in INTEGRAL:
                return "Double"     # the FPU's: the renderer writes CDbl(a) op b
            if e.op in ("+", "-", "*") and getattr(e, "width", None) != 2 and t in ("Byte", "Integer", "Boolean"):
                return "Long"       # (render.expr: what isn't a 16-bit instruction prints as Long)
            return t
        if isinstance(e, Call) and e.vbtype is None and isinstance(e.raw, int):
            return self.m.ret_types.get(e.raw)      # a decompiled Function
        if isinstance(e, (Call, Prop)):
            return e.vbtype
        if isinstance(e, Var):
            return self.var_type(e.key())
        if isinstance(e, Deref) and isinstance(e.e, Var) and e.e.space == "arg" and e.off == 0 \
                and e.e.key() not in self.lf.udt:
            return self.var_type(e.e.key())
        if isinstance(e, Deref):
            return self.field_type(e)
        if isinstance(e, ir.Index) and isinstance(e.arr, Var):
            ts = self.elem.get(e.arr.key())
            if ts:
                return pick(ts)
            info = self.lf.arrays.get(e.arr.key())
            if info is not None:
                return elem_type(info)          # an element: the array's type
        if isinstance(e, UnOp):
            t = self.typeof(e.a)
            if e.op == "-" and t == "Boolean":
                # setne cl: 1 or 0, printed -(x <> 0).  No Boolean, which would make that 1 True, -1:
                # ChessBrainVB Eval_KRKP SideToMove = x <> StrongSide compared 0/1 with it
                return "Integer"
            return t
        return None

    def field_type(self, e):
        """Type of a field of a variable whose UDT is known."""
        from . import apidb
        b, off = e.e, e.off
        if isinstance(b, BinOp) and b.op == "+" and isinstance(b.b, Const) and \
                isinstance(b.b.value, int):
            b, off = b.a, off + b.b.value
        if isinstance(b, ir.Index) and isinstance(b.arr, Var) and not getattr(b.arr, "inline", None):
            b = b.arr                   # (an element's field: rob(i).DnaLen)
        if not isinstance(b, Var):
            return None
        t = self.lf.udt.get(b.key())
        return apidb.field_type(t, off) if t else None

    def collect(self):
        # what callers pass for this procedure's parameters
        from . import apidb
        scalar = getattr(self.m, "scalar_params", ())
        from .lift import param_base
        first = param_base(self.lf)
        for i, (off, name, t, byref) in enumerate(self.m.plists.get(self.lf.p.va, ())):
            if byref and (self.lf.p.va, i) in getattr(self.m, "variant_params", ()) and \
                    ((self.lf.p.va, i) not in getattr(self.m, "not_variant_params", ()) or
                     (self.lf.p.va, i) in getattr(self.m, "wrapped_params", ())) and \
                    not {4, 8} & {z for z, _ in
                                  getattr(self.lf, "deref_load_sizes", {}).get(("arg", off), ())}:
                # (its first dword read as a value, which VB never does with a Variant: a caller's
                # slot reused for one isn't evidence, a target Proc_EEB860 a1 As String)
                self.lf.variants.add(("arg", off))
            ct = self.m.arg_types.get((self.lf.p.va, i))
            if ct and (self.lf.p.va, (off - first) // 4) in scalar:
                # a record's first field passed is the record's address
                # (decompile.scalar_params): the field's type, not the record's
                ct = {x for x in ct if apidb.layout(x) is None}
            ct = dominant(ct, getattr(self.m, "arg_type_counts", {}).get((self.lf.p.va, i)),
                          byref)
            if ct:
                self.ev.setdefault(("arg", off), set()).update(ct)
        # the tags of Variants VB built around a variable give its static type
        for key, ts in getattr(self.lf, "vt_types", {}).items():
            self.ev.setdefault(key, set()).update(ts)
        def learnt():
            # ev and elem only grow; exact entries can be replaced
            return (len(self.ev), sum(map(len, self.ev.values())), len(self.elem),
                    sum(map(len, self.elem.values())), dict(self.exact))
        for rnd in range(3):
            if rnd:
                # a round that learnt nothing: the next one would repeat it
                # (the rounds read only this state, besides what they leave alone)
                if learnt() == before:
                    break
            before = learnt()
            for a, ss, br in self.body:
                for s in ss:
                    for e in exprs_of(s):
                        self.walk(e)
                    if isinstance(s, Assign):
                        for x, y in ((s.dst, s.src), (s.src, s.dst)):
                            if isinstance(x, ir.Index) and isinstance(x.arr, Var):
                                t = self.typeof(y) if not isinstance(y, ir.Index) else None
                                if t is None and isinstance(y, Var) and y.space == "local" and \
                                        y.off == self.lf.result_slot:
                                    m = getattr(self.lf.p, "member", None)
                                    t = m.ret_type if m is not None else None
                                if t and not isinstance(y, Const):
                                    self.elem.setdefault(x.arr.key(), set()).add(t)
                        arrs = self.lf.arrays
                        if any(isinstance(side, Var) and side.key() in arrs and
                               arrs[side.key()].get("dyn") for side in (s.dst, s.src)):
                            continue                # ba = "" / a = b between arrays: no scalar type
                        if isinstance(s.src, Const) and s.src.vbtype is None and \
                                isinstance(s.src.value, int) and float_bits(s.src.value):
                            self.add(s.dst, WEAK_LONG)
                        elif s.kind == "set" and isinstance(s.src, Const):
                            pass        # Set g = Nothing is no number (Public g As cT came out As Long)
                        elif not self.reg_string_leak(s.src, s.dst):
                            self.add(s.dst, self.typeof(s.src))
                        if isinstance(s.dst, Var) and isinstance(s.src, BinOp) and s.src.op in ("+", "-") and \
                                isinstance(s.src.a, Var) and s.src.a.key() == s.dst.key() and \
                                isinstance(s.src.b, Const) and isinstance(s.src.b.value, int):
                            # i = i + 1 by machine instructions: a number, whatever a compare with a
                            # frame slot the compiler also uses for Strings suggests (a target
                            # Public i As Long came out As String: Type mismatch at For i = 1 To n)
                            self.__dict__.setdefault("counters", set()).add(s.dst.key())
                        if s.kind == "set" and not self.is_variant(s.dst):
                            # Set x = y: both hold object references (Set v(i) = a on a
                            # Variant leaves a's type open: gh_dict dict_AddKeyValue)
                            for side in (s.dst, s.src):
                                if self.is_variant(side):
                                    continue
                                if self.lf.objtype.get(getattr(side, "key", lambda: None)()) is None:
                                    self.add(side, "Object")
                        self.add(s.src, self.typeof(s.dst) if isinstance(s.dst, Prop) else None)
                        if isinstance(s.dst, Deref) and s.kind == "let" and \
                                isinstance(s.src, (Var, Deref)) and \
                                self.field_type(s.dst) in ("Single", "Double"):
                            # a plain move into a float: no conversion, so a float comes in
                            # (NewObstacle(.. Width As Single): .Width = Width, nothing else)
                            self.add(s.src, self.field_type(s.dst))
                        if isinstance(s.dst, Var) and isinstance(s.src, Deref) and s.src.off == 0 and \
                                isinstance(s.src.e, Var) and s.src.e.space == "arg" and \
                                self.lf.objtype.get(s.dst.key()) is not None and \
                                self.typeof(s.dst) not in ("VBGlobal", "Global"):
                            # a ByRef parameter's object fetched into a typed slot: the parameter
                            # holds that (Sub strings(frm As Form): a1 As Long, called with Me)
                            self.add(s.src, self.typeof(s.dst))
                        if isinstance(s.src, Var) and isinstance(s.dst, Var) and \
                                not self.reg_string_leak(s.dst, s.src):
                            self.add(s.src, self.typeof(s.dst))   # a copy: same type both ways
                if br is not None:
                    self.walk(br.cond)

    def walk(self, e):
        for x in e.walk():
            ob = getattr(x, "obj", None)
            if isinstance(ob, Deref) and ob.off == 0 and isinstance(ob.e, Var) and \
                    ob.e.space == "arg" and self.lf.objtype.get(ob.e.key()) is None and \
                    not getattr(x, "file", False):
                # a call on a ByRef parameter (strings frm).  Print #n's "object" is its file number
                # (a target's f As Integer, printed to and nothing else: Object, by way of the
                # caller's f = FreeFile an error 91 in a program that compiles)
                self.add(ob, "Object")
            if isinstance(x, Call) and getattr(x, "string_target", False) and x.args:
                self.add(x.args[0], "String")
            if isinstance(x, Call) and getattr(x, "input_codes", None):
                # Input #f, a, b: VB converts each as its descriptor says
                for c, a in zip(x.input_codes, x.args[1:]):
                    a = a.e if isinstance(a, AddrOf) else a
                    if isinstance(a, (Var, Deref)) and VT_NAMES.get(c):
                        self.add(a, VT_NAMES[c])
            if isinstance(x, Call) and (getattr(x, "stmt_fmt", None) or "").startswith("Open {") and x.args:
                # Open path For ...: a String (LoadOrganism(path As String, ...) was a1 As Long)
                self.add(x.args[0], "String")
            if isinstance(x, Call) and getattr(x, "print_codes", None):
                # Print #f, s: the same (Out(s As String), printing all it does, came out a1 As
                # Long; its callers' strings then failed at run time)
                items = [c & 0x3f for c in x.print_codes if c & 0x3f]
                for c, a in zip(items, x.args):
                    if isinstance(a, (Var, Deref)) and VT_NAMES.get(c):
                        self.add(a, VT_NAMES[c])
                    elif isinstance(a, Call) and isinstance(a.raw, int) and VT_NAMES.get(c):
                        # (Print #1, F(x): what F returns, whatever its own code looks like.
                        # CyByVal just hands its two dwords back in edx:eax)
                        self.m.__dict__.setdefault("printed_results", {}).setdefault(
                            a.raw, set()).add(VT_NAMES[c])
            if isinstance(x, Call) and getattr(x, "put_size", None) in PUT_WIDTHS and x.args:
                # Put / Get #f, , v of so many bytes: v is that wide (VBDoom
                # M_WriteBMP fh.bfReserved1, never assigned, was a Variant)
                v = x.args[-1]
                v = v.e if isinstance(v, AddrOf) else v
                if isinstance(v, (Var, Deref)):
                    self.add(v, PUT_WIDTHS[x.put_size])
                if isinstance(v, Var):
                    # (of that width whatever else it holds: Fe = 254: Put #n, , Fe a Byte, not the
                    # Long the literal suggests, or the file has other bytes)
                    self.__dict__.setdefault("put_widths", {})[v.key()] = x.put_size
            if isinstance(x, Var) and getattr(x, "vbtype", None) in ("Currency", "Double", "Date"):
                self.add(x, x.vbtype)   # both halves of an 8-byte value (runtime.dword_pair)
            if isinstance(x, Deref) and x.off == 0 and x.size == 8 and \
                    getattr(x, "vbtype", None) in ("Currency", "Double", "Date"):
                self.add(x, x.vbtype)   # (the same through a ByRef parameter: CyAdd(a As Currency))
            if isinstance(x, BinOp) and x.op == "&":
                # weak: VB turns a number into its digits for &, so a variable
                # also typed otherwise keeps that type (VBDoom "at gametic " &
                # gametic made gametic a String: Type mismatch in G_DoLoadLevel)
                self.add(x.a, WEAK_STRING)
                self.add(x.b, WEAK_STRING)
            elif isinstance(x, BinOp) and getattr(x, "width", None) == 4 and \
                    x.op in ("+", "-", "*"):
                # 32-bit machine arithmetic: VB did it on Longs (lQrSize * lQrSize
                # with lQrSize loaded from a Byte array is not Byte arithmetic)
                for y in (x.a, x.b):
                    if isinstance(y, Var) and y.space in ("reg", "tmp", "stk", "local"):
                        self.add(y, "Long")
            elif isinstance(x, BinOp) and x.op == "Is":
                for y in (x.a, x.b):            # object references
                    z = y.e if isinstance(y, Deref) and y.off == 0 else y
                    if getattr(y, "of_variant", False):
                        self.add(y, "Variant")  # v Is o on a Variant (__vbaUnkVar)
                        continue
                    if getattr(z, "key", None) and z.key() in self.lf.variants:
                        continue
                    if not isinstance(y, Const) and self.lf.objtype.get(
                            getattr(y, "key", lambda: None)()) is None:
                        self.add(y, "Object")
            elif isinstance(x, Call) and getattr(x, "fmt", "").startswith("TypeOf") and x.args:
                y = x.args[0]
                if self.lf.objtype.get(getattr(y, "key", lambda: None)()) is None:
                    self.add(y, "Object")
            elif isinstance(x, BinOp) and x.op in ("=", "<>", "<", ">", "<=", ">="):
                ta, tb = self.typeof(x.a), self.typeof(x.b)
                if (ta == "String" or tb == "String") and not \
                        (self.reg_string_leak(x.a, x.b) or self.reg_string_leak(x.b, x.a)):
                    self.add(x.a, "String")
                    self.add(x.b, "String")
            elif isinstance(x, Call):
                for i in getattr(x, "ansi_args", ()):
                    if i < len(x.args) and isinstance(x.args[i], Var):
                        self.add(x.args[i], "String")
                if isinstance(x.raw, str) and "!" in x.raw:
                    # a Me variable given to an API's ByRef UDT is that UDT, even if its first
                    # field is also read (UserControl1 GetClientRect h, mB0; m4C = mB0)
                    from . import apidb
                    api = apidb.lookup(x.raw.split("!", 1)[1])
                    for (byval, _, ptype), a in zip(api.params if api else (), x.args):
                        a = a.e if isinstance(a, ir.AddrOf) else a
                        if not byval and isinstance(a, Var) and a.space == "member" and \
                                (apidb.layout(ptype) or (0, 0))[1] > 4:
                            self.exact[a.key()] = ptype
                        elif not byval and isinstance(a, Var) and a.space == "local" and \
                                ptype in SCALAR and a.key() not in self.lf.variants and \
                                not self.ev.get(a.key()):
                            # a literal for a ByRef Long goes in a temporary: nothing else says
                            # what that is (cReg RegQueryInfoKey(h, "", 0&, 0, n, ...))
                            self.exact.setdefault(a.key(), ptype)
                    for i, a in enumerate(x.args):
                        # a local's address given by value: what the API fills is no Variant.  With
                        # no other evidence it is one by default, and OleTranslateColor a1, 0,
                        # VarPtr(v0) wrote over its tag: error 458 on the next read (UserControl1)
                        v = a.e if isinstance(a, ir.AddrOf) else \
                            a.args[0] if isinstance(a, Call) and a.name == "VarPtr" and a.args else None
                        if isinstance(v, Var) and v.space == "local" and \
                                v.key() not in self.lf.variants and \
                                not (api and i < len(api.params) and not api.params[i][0]):
                            self.add(v, WEAK_LONG)
                if not isinstance(x.raw, int) and x.name in STRING_ARGS_OF:
                    for i in STRING_ARGS_OF[x.name]:
                        if i < len(x.args) and not isinstance(x.args[i], (Const, Str)):
                            self.add(x.args[i], "String")
                if x.raw in STRING_ARG or (x.raw is None and x.name in STRING_FN):
                    idx = 1 if x.name == "InStr" and len(x.args) > 2 else 0
                    if x.args:
                        self.add(x.args[idx], "String")
                    if x.name == "InStr" and len(x.args) > 2:
                        self.add(x.args[2], "String")
                evm = getattr(x, "event_member", None)
                if evm is not None:
                    # RaiseEvent: a ByRef event parameter takes a variable of its type
                    for prm, a in zip(evm.params, x.args):
                        if isinstance(a, ir.AddrOf) and isinstance(a.e, Var):
                            a = a.e
                        elif isinstance(a, Deref) and a.off == 0 and isinstance(a.e, Var) and \
                                a.e.space == "arg":
                            a = a.e                 # a ByRef parameter passed on
                        elif type(a).__name__ == "SP":
                            a = Var("local", a.off + 4)     # a frame variable's address
                        if not prm.byref and isinstance(a, Var) and \
                                prm.type in ("Currency", "Double", "Date"):
                            self.add(a, prm.type)   # ByVal Total As Currency: 8 bytes
                        if prm.byref and isinstance(a, Var) and prm.type in SCALAR:
                            self.exact[a.key()] = prm.type
                        elif prm.byref and isinstance(a, Var) and prm.type == "Variant":
                            self.add(a, "Variant")
                f = getattr(x, "func", None)
                if f is not None and x.method:
                    # a method: a variable passed to a ByRef parameter must have
                    # exactly its type (a ByRef Variant takes any variable)
                    for prm, a in zip(f.params, x.args):
                        t = prm.type
                        if t.vt == 26 and t.sub is not None and isinstance(a, Var):
                            name = VT_NAMES.get(t.sub.vt)
                            if name in SCALAR and getattr(prm, "array", False):
                                self.exact[a.key()] = name + "()"   # an array parameter
                            elif name in SCALAR and \
                                    not self.exact.get(a.key(), "").endswith("()"):
                                self.exact[a.key()] = name
                proc = self.m.procs.get(x.raw) if isinstance(x.raw, int) else None
                if proc is not None:
                    plist = self.m.plists.get(proc.va) or ()
                    # v, Hi(v): an 8-byte value passed ByVal (gh_zip pvVfsSeek
                    # ByVal cPosition As Currency)
                    from .machine import Hi
                    for a, pi in param_args(plist, x.args):
                        i = next((k for k, y in enumerate(x.args) if y is a), None)
                        if i is None or i + 1 >= len(x.args):
                            continue
                        h = x.args[i + 1]
                        if isinstance(h, Hi) and (h.of is a or h.of.key() == a.key()):
                            t8 = self.typeof(a)
                            if t8 in ("Currency", "Double", "Date"):
                                self.m.arg_types.setdefault((proc.va, pi), set()).add(t8)
                    for a, pi in param_args(plist, x.args):
                        tag = None
                        if plist[pi][2] == "Variant" and not plist[pi][3]:
                            tag = a                 # a ByVal Variant: its tag dword first
                        if isinstance(a, ir.Missing) or (
                                isinstance(a, ir.AddrOf) and isinstance(a.e, ir.Missing)) or (
                                isinstance(tag, Const) and isinstance(tag.value, int) and
                                tag.value & 0xfff == 10):
                            # (&Missing: an omitted ByRef Variant - a target
                            # Proc_85B240(i, n, x): 48 calls "Argument not optional")
                            # a caller leaves it out: the parameter is Optional
                            self.m.__dict__.setdefault("optional_params", set()).add(
                                (proc.va, pi))
                        pushed = a
                        if getattr(a, "wrapped", False):
                            # VB built a Variant around the variable for it: a ByRef Variant
                            # parameter, even if other callers pass a variable of one type (or
                            # several: a target Proc_4C05D0 global_531054, a Long, and v18)
                            self.m.__dict__.setdefault("wrapped_params", set()).add((proc.va, pi))
                            self.m.__dict__.setdefault("variant_params", set()).add((proc.va, pi))
                        if isinstance(a, ir.AddrOf) and isinstance(a.e, (Var, Deref)):
                            a = a.e                 # the variable (field) passed by reference
                        off, pname, t, byref = plist[pi]
                        if byref and isinstance(a, Var) and a.space == "local" and \
                                a.key() in self.lf.variants and \
                                a.key() not in getattr(self.lf, "vt_types", {}):
                            # a Variant's own address passed: a parameter of another type would
                            # have had a converted copy (a target Proc_4C0940 v, &temp "0")
                            self.m.__dict__.setdefault("variant_params", set()).add((proc.va, pi))
                        elif byref and (isinstance(a, Const) or isinstance(a, Var) and
                                        self.var_type(a.key(), own=True) not in (None, "Variant")):
                            # ...unless another caller passes something else: a slot reused for
                            # a Variant isn't enough (a target Proc_EE7C10 a1 As Single, 1!)
                            self.m.__dict__.setdefault("not_variant_params", set()).add((proc.va, pi))
                        fw = a.e if isinstance(a, Deref) and a.off == 0 else a
                        if byref and t in SCALAR and isinstance(fw, Var) and fw.space == "arg":
                            # our own parameter passed on to one of a plain type: not a Variant
                            # (a target Proc_8344E0 a1, on to Proc_EE9A10(a1 As String))
                            for j, q in enumerate(self.m.plists.get(self.lf.p.va, ())):
                                if ("arg", q[0]) == fw.key():
                                    self.m.__dict__.setdefault("not_variant_params", set()).add(
                                        (self.lf.p.va, j))
                        if byref and isinstance(a, Var) and t in SCALAR and \
                                ((proc.va, ("arg", off)) in getattr(self.m, "proc_arrays", {}) or
                                 (proc.va, off) in getattr(self.m, "param_arrays", ())):
                            self.exact[a.key()] = t + "()"  # baBuffer() As Byte: an array
                        elif byref and t == "Object" and isinstance(a, Var) and a.space == "local" and \
                                self.m.param_known.get((proc.va, pi)):
                            # a declared ByRef As Object takes nothing but an Object variable, whatever
                            # went on with a Variant around it (a target DrawAll v90: Set, and given
                            # to target1 As Object: ByRef argument type mismatch as a Variant)
                            self.lf.variants.discard(a.key())
                            self.exact[a.key()] = "Object"
                        elif byref and t == "Variant" and isinstance(a, Var) and \
                                a.space == "local" and self.m.param_known.get((proc.va, pi)) and \
                                a.key() not in getattr(self.lf, "vt_types", {}) and \
                                not self.ev.get(a.key(), set()) & NUMERIC:
                            # (not with integer arithmetic on it: a target Walk's For counter)
                            # a variable of another type would go in a VT_BYREF temporary: this
                            # pointer is to a Variant (t79varref: Read1 k, "ei", v)
                            self.lf.variants.add(a.key())
                        elif byref and isinstance(a, Var) and t in SCALAR:
                            # VB rejects a ByRef variable of any other type
                            if not self.exact.get(a.key(), "").endswith("()"):
                                self.exact[a.key()] = t     # (an array stays an array)
                        elif self.m.param_known.get((proc.va, pi)) and not (
                                byref and t == "Variant" and isinstance(a, Var) and
                                a.key() in getattr(self.lf, "vt_types", {})):
                            if not (byref and t == "Variant" and isinstance(a, Var) and
                                    a.key() in self.lf.arrays):
                                self.add(a, t)
                        if isinstance(a, ir.AddrOf) and isinstance(a.e, BinOp) and a.e.op == "&":
                            a = a.e                 # a String's temporary by reference: Decrypt(p & "\\x")
                        ta = self.var_type(a.key(), own=True) if isinstance(a, Var) \
                            else self.typeof(a)
                        if isinstance(a, Var) and self.exact.get(a.key()) in WIDE8:
                            # (its type by another call's ByRef parameter is no guess: an 8-byte
                            # variable, and a literal in it says Long: c = 2.5@: CyAdd(c, c): CyByVal(c))
                            ta = self.exact[a.key()]
                        if ta in WIDE8 and pushed is a and not self.upper_follows(x.args, a) and \
                                not (ta == "Currency" and isinstance(a, Var)):
                            # (not a Currency variable, which only moves as integers and whose upper
                            # half may be known by now: F(c, 0) of c = 2.5@)
                            # no upper half after it: an 8-byte value doesn't go in one dword.  Cos(x)
                            # is a Double, fstp dword [esp] makes it the Single the parameter takes
                            # (VectorSet(Cos(x), Sin(x)): ByVal a1 As Double, eating a2, fast code)
                            ta = "Single" if ta == "Double" else None
                        # a literal says nothing: VB pushes a dword for any small type
                        if ta and ta != "Boolean" and not isinstance(a, Const):
                            if ta not in self.m.arg_types.get((proc.va, pi), ()) and \
                                    "%X" % proc.va in os.environ.get("VB6DEC_ARGDBG", "").split(","):
                                print("ARGDBG %s arg %d %s from %s: %s" % (proc.name, pi, ta, self.lf.p.name, a),
                                      file=sys.stderr)
                            self.m.arg_types.setdefault((proc.va, pi), set()).add(ta)
                            counts = self.m.__dict__.setdefault("arg_type_counts", {})
                            c = counts.setdefault((proc.va, pi), {})
                            c[ta] = c.get(ta, 0) + 1

    @staticmethod
    def upper_follows(args, a):
        """Is the argument dword after a the upper half of what a is the lower half of?"""
        from .machine import Hi
        i = next((k for k, y in enumerate(args) if y is a), None)
        if i is None or i + 1 >= len(args):
            return False
        h = args[i + 1]
        if isinstance(h, Hi):
            return h.of is a or h.of.key() == a.key()
        if isinstance(a, Var) and isinstance(h, Var):
            return a.space == h.space and isinstance(a.off, int) and isinstance(h.off, int) and \
                h.off == a.off + 4
        if isinstance(a, Deref) and isinstance(h, Deref):
            return h.off == a.off + 4 and h.e.key() == a.e.key()
        return False

    def reg_string_leak(self, src, other):
        """A register left unrenamed in a procedure too tangled to separate its
        uses holds a String in one place and a number in another: its String is
        no evidence for what it is copied to or compared with.  a target
        Proc_7E1470: t_eax = Left$(v84, 4) ... t_eax = 1: v44 = t_eax made the
        loop counter v44 String, and through the parameters of Proc_70DC00 every
        record number in the program (vbcheck 8 -> 1160)."""
        if not isinstance(src, Var) or src.space != "reg" or isinstance(other, Const) or \
                self.typeof(src) != "String":
            return False
        mixed = getattr(self, "_mixed_regs", None)
        if mixed is None:
            mixed = set()
            for _, ss, _ in self.body:
                for t in ss:
                    if isinstance(t, Assign) and isinstance(t.dst, Var) and t.dst.space == "reg" and \
                            (isinstance(t.src, Const) and isinstance(t.src.value, int) or
                             isinstance(t.src, BinOp) and t.src.op in ("+", "-", "*", "\\")):
                        mixed.add(t.dst.key())
            self._mixed_regs = mixed
        return src.key() in mixed

    def var_type(self, key, own=False):
        """The variable's type; own leaves out what ByRef parameters impose."""
        if key in getattr(self.lf, "variants", ()):
            return "Variant"
        if key in self.__dict__.get("counters", ()) and "String" in self.ev.get(key, ()):
            self.ev[key] = set(self.ev[key]) - {"String"} or {"Long"}
        if not own and key in self.exact:
            return self.exact[key]
        w = self.__dict__.get("put_widths", {}).get(key)
        if w in (1, 2, 8) and key not in self.lf.udt and key not in self.lf.arrays:
            fits = self.ev.get(key, set()) & {1: {"Byte"}, 2: {"Integer", "Boolean"},
                                              8: {"Double", "Currency", "Date"}}[w]
            return pick(fits) if fits else {1: "Byte", 2: "Integer", 8: "Double"}[w]
        if key[0] == "local":
            # a slot renamed block by block has none of its sizes: the fstp that sets it says
            # (Impulse As Single came out o1002 As Double, and its digits changed a simulation)
            w = self.fp_stores().get(key)
            if w in ({4}, {8}) and not self.ev.get(key, set()) & {"String", "Object", "Variant"}:
                return "Single" if w == {4} else "Double"
        if key[0] == "arg":
            # the tag VB put on a Variant around the parameter is its declared type, whatever callers
            # hand it (ByVal d As Date, Format$(d, ...): callers with a Variant, a Double and a Date)
            tags = getattr(self.lf, "vt_types", {}).get(key)
            if tags and len(tags) == 1:
                return next(iter(tags))
        it = self.lf.objtype.get(key)
        if it is not None and key[0] == "reg" and not self.holds_object(key):
            it = None                   # (only copies since folded away: t_eax = Form6)
        if it is not None and key[0] == "local" and key in self.subscripts():
            it = None                   # (a frame slot reused: clist(v40) = n, v40 As MDIForm1)
        if it is not None and getattr(it, "name", None) and not (
                # a register is reused: t10carr's For counter in edi held a
                # CheckBox element between its uses; numbers it takes part in
                # say what its variable is
                key[0] == "reg" and self.ev.get(key, set()) & (NUMERIC | {"String"})):
            n = it.name
            if n.endswith("()"):
                return "Object"         # a control array's own object (lift.ControlArray):
                                        # no VB type to declare it with ("Dim v34() As Text1")
            return n[1:] if n.startswith("_") else n      # _Form -> Form, _TextBox -> TextBox
        t = self.ev.get(key)
        sz = self.lf.sizes.get(key)
        if t and "Variant" in t and key[0] == "local":
            # only ever read as a word or a byte, with the tag VB put on the Variant around it: the
            # Variant in the evidence is that copy's (v = f, f As Boolean an API filled, t96sbfix)
            nl = getattr(self.lf, "load_sizes", {}).get(key)
            tags = getattr(self.lf, "vt_types", {}).get(key)
            if nl and tags and len(tags) == 1 and all(z in (1, 2) and not fp for z, fp in nl):
                return next(iter(tags))
        if key[0] == "arg" and key[1] in getattr(self.lf, "qword_args", ()):
            # both its dwords go into an 8-byte element (simplify.merge_element_halves): ByVal x As
            # Double copied in halves, DarwinBots PushQStack
            return "Currency" if t and "Currency" in t else "Double"
        if sz and key[0] == "arg" and key[1] in self.lf.byref and \
                getattr(self.lf, "deref_load_sizes", {}).get(key) and \
                not getattr(self.lf, "store_sizes", {}).get(key):
            # a ByRef parameter read only through its pointer: the reads are
            # its width, not the dword load of the pointer itself (a target
            # Proc_8D8880(a1 As Byte), "mov cl, byte ptr [eax]", stayed As Long
            # and typed the Byte fields of the records passed to it Long)
            sz = set(self.lf.deref_load_sizes[key])
        loads = getattr(self.lf, "deref_load_sizes" if key[0] == "arg" and
                        key[1] in self.lf.byref else "load_sizes", {})
        if t and len(t) > 1 and (4, False) in loads.get(key, ()):
            # its first dword is read as a value, which VB never does with a
            # Variant (that takes __vbaI4Var): the Variant only came along
            # copies and ByRef parameters (a target's For counter v80, read
            # into global_F04740, flooded 66 procedures through ReadAll);
            # nor is it a float when the FPU never loads or stores it and
            # integer evidence is there (one Double copy made it a Double)
            t2 = t - {"Variant"}
            fpu = key[1] in getattr(self.m, "global_fp", ()) if key[0] == "global" else \
                any(fp for _, fp in sz or ())   # (a module variable: anywhere in the program)
            if t2 & INTEGRAL and not fpu:
                t2 = t2 - FLOATS
            t = t2 or t
        if sz and (4, True) in sz and not (t and t & {"String", "Object", "Double", "Currency"}) \
                and key[0] in ("arg", "local") and \
                not (key[0] == "arg" and any(z in (1, 2) and not fp for z, fp in sz)):
            # (not a parameter also read as a word: its slot reused for a Single temporary, once
            # the Integer is in a register: fstp [esp+10h], Uses(ByVal n As Integer), fast code)
            return "Single"     # the FPU's dword forms: no Long read those (VSet(ByVal a, ...))
        if key[0] == "arg" and key[1] in self.lf.byref and t and "Double" in t and \
                (4, True) in getattr(self.lf, "deref_load_sizes", {}).get(key, ()) and \
                not any(s == 8 for s, _ in getattr(self.lf, "deref_load_sizes", {}).get(key, ())):
            # read through its pointer as a Single: a Double from a caller is its expression,
            # converted to a temporary (NewObstacle(x As Single, ...) given CDbl(n) + k)
            return "Single"
        if t and "Single" in t and t & {"Integer", "Byte", "Boolean"} and key[0] == "arg" and \
                key[1] in self.lf.byref and sz and any(s in (1, 2) and not fp for s, fp in sz) and \
                not any(fp for _, fp in sz):
            # read as a word through its pointer, and never with the FPU: no Single, whatever
            # some caller passes (eyestrength(n1 As Integer) out of optimised code; the Single
            # came from a caller's variable typed by this same parameter round the call graph)
            t = t - {"Single"}
        if t and any(x.endswith("()") for x in t):
            # holds a Function's array result: a dynamic array of that type
            return sorted(x for x in t if x.endswith("()"))[0]
        if t and key[0] == "arg" and key[1] in self.lf.byref and "Long" in t and \
                key[1] in getattr(self.lf, "varptr_args", ()) and \
                t & {"Integer", "Byte", "Boolean"} and not t & {"String", "Variant", "Object",
                                                             "Double", "Currency", "Date"}:
            # a ByRef parameter takes one exact type: a field read as a word is
            # an Integer, the dword view of it is only the default (pvToDosDateTime nDate)
            for name in ("Integer", "Boolean", "Byte"):
                if name in t:
                    return name
        if t:
            if key[0] == "arg" and t & WIDE8 and key[1] not in self.lf.byref and \
                    (key in self.int_args() or key[1] + 8 > self.arg_end() or
                     # read as a word or a byte: an Integer / Boolean / Byte
                     # (ChessBrainVB GenerateMoves' bCapturesOnly)
                     (sz and any(s in (1, 2) and not fp for s, fp in sz))):
                # its dword takes part in integer arithmetic: a Long, whatever
                # callers pass (VBDoom FixedMul: Doubles from callers made its
                # two Long parameters one ByVal Double, and so its callers'
                # globals Doubles in turn); or the procedure's ret N leaves it
                # no 8 bytes (VBDoom UDbl(ByVal a As Long) As Double: ret 4)
                t = (t - WIDE8) or {"Long"}
                if sz and any(s in (1, 2) and not fp for s, fp in sz):
                    t = (t - {"Single"}) or {"Integer"}     # (a word is no Single either)
            if key[0] != "arg" and sz and (4, True) in sz and \
                    not any(s == 8 for s, fp in sz) and not t & {"String", "Variant", "Object"} \
                    and key not in self.int_used():
                t = {"Single"}          # read with fld dword: a Single (simplify.fold_single_bits)
            if key[0] == "arg" and t & WIDE8 and key[1] not in self.lf.byref and sz and \
                    (4, True) in sz and not any(s == 8 for s, fp in sz):
                # read as a 4-byte float (fld dword): a Single, whatever callers
                # pass - an 8-byte type would take the next parameter's slot too
                # (ChessBrainVB CalcTime's MoveOverhead As Single and the Boolean after it)
                t = (t - WIDE8) | {"Single"}
            name = pick(t)
            if name in ("Long", "Integer", "Byte", "Boolean") and sz:
                widths = {s for s, fp in sz if not fp}
                # what the variable is stored with decides when known: without
                # overflow checks VB makes a Byte of a Long by reading its low
                # byte (VBDoom R_InitTranslationTables: For i = 0 To 255 as a
                # Byte never ended), and an Integer is pushed as a dword
                stores = getattr(self.lf, "store_sizes", {}).get(key)
                if stores:
                    widths = stores
                if 1 in widths and max(widths) == 1:
                    return "Byte"
                if 2 in widths and max(widths) <= 2:
                    return "Boolean" if "Boolean" in t else "Integer"
                if not stores and 1 in widths:
                    return "Byte"
                if not stores and 2 in widths:
                    return "Boolean" if "Boolean" in t else "Integer"
            return name
        if sz:
            # (see above: a parameter read as a word has its slot reused for the FPU's temporary)
            word = key[0] == "arg" and any(z in (1, 2) and not fp for z, fp in sz)
            if (8, True) in sz and not word:
                return "Double"
            if (4, True) in sz and not word:
                return "Single"
            widths = getattr(self.lf, "store_sizes", {}).get(key) or {s for s, _ in sz}
            if max(widths) == 2 or 2 in widths and not getattr(self.lf, "store_sizes", {}).get(key):
                return "Integer"
            if max(widths) == 1:
                return "Byte"
        if key[0] == "arg":
            for off, name, t, byref in self.m.plists.get(self.lf.p.va, ()):
                if off == key[1]:
                    return t            # as declared (Long when nothing says otherwise)
        if key[0] == "local" and key in self.result_sources():
            # copied into the Function's value and typed by nothing else: its
            # type (VBDoom RdLong: Dim v As Long, CopyMemory v, ..., 4; an
            # untyped v was a Variant whose tag the copy overwrote: error 458)
            t = self.m.ret_types.get(self.lf.p.va)
            if t in ("Long", "Integer", "Byte", "Boolean", "Single", "Double", "Currency",
                     "Date", "String"):
                return t
        return None

    def result_sources(self):
        """Keys of variables assigned whole to the Function's result slot."""
        if getattr(self, "_rsrc", None) is None:
            rs = self.lf.result_slot
            self._rsrc = {s.src.key() for a, ss, br in self.body for s in ss
                          if isinstance(s, Assign) and isinstance(s.dst, Var) and
                          s.dst.space == "local" and s.dst.off == rs and isinstance(s.src, Var)} \
                if rs is not None else set()
        return self._rsrc

    def result_type(self):
        ts = set()
        lf = self.lf
        if getattr(lf, "rec_ret", None) is not None and \
                lf.udt.get(("local", lf.result_slot)):
            return lf.udt[("local", lf.result_slot)]     # a record UDT
        if getattr(lf.p, "udt_ret", False) and isinstance(lf.result_slot, int) and \
                lf.udt.get(("local", lf.result_slot)):
            return lf.udt[("local", lf.result_slot)]   # the result buffer's UDT
        if getattr(lf, "small_rec", None) is not None and lf.udt.get(("local", lf.result_slot)):
            return lf.udt[("local", lf.result_slot)]   # a record in edx:eax
        if getattr(lf.p, "udt_ret", False):
            for a, ss, br in self.body:     # a UDT: the type of the variable returned
                for s in ss:
                    v = getattr(s, "value", None) if isinstance(s, ir.Return) else None
                    if isinstance(v, Var) and lf.udt.get(v.key()):
                        return lf.udt[v.key()]
        if getattr(lf, "hidden_ret", False):
            return "Variant"            # returned through the caller's result pointer
        for a, ss, br in self.body:     # F = arr: a Function returning an array
            for s in ss:
                if isinstance(s, Assign) and isinstance(s.dst, Var) and s.dst.space == "local" \
                        and s.dst.off == lf.result_slot and isinstance(s.src, Var):
                    info = lf.arrays.get(s.src.key())
                    if info is not None and info.get("dyn"):
                        if lf.udt.get(s.src.key()):
                            info = dict(info, udt=lf.udt[s.src.key()])
                        return elem_type(info) + "()"
        for a, ss, br in self.body:
            for s in ss:
                if isinstance(s, Assign) and isinstance(s.dst, Var) and s.dst.space == "local" \
                   and s.dst.off == lf.result_slot:
                    t = self.typeof(s.src) or ("Long" if isinstance(s.src, Const) else None)
                    if t:
                        ts.add(t)
                if isinstance(s, ir.Return) and getattr(s, "value", None) is not None:
                    v = s.value
                    if isinstance(v, Var) and v.space == "local" and v.off == lf.result_slot:
                        continue
                    t = self.typeof(v)
                    if t:
                        ts.add(t)
                    elif isinstance(v, Var):
                        t2 = self.var_type(v.key())
                        if t2:
                            ts.add(t2)
        if lf.result_slot is not None:
            t = self.var_type(("local", lf.result_slot))
            if t:
                ts.add(t)
        pr = self.m.__dict__.get("printed_results", {}).get(lf.p.va)
        if pr and len(pr) == 1 and next(iter(pr)) in ("Currency", "Double", "Single", "Date"):
            return next(iter(pr))
        rf = getattr(lf, "result_fp", None)
        if rf in ({4}, {8}):
            # (written through the result pointer by fstp: Anti_Prob = 1 - 1 / a, whose quotient
            # is a Double in the evidence, in a method As Single)
            return "Single" if rf == {4} else "Double"
        if getattr(lf.p, "float_ret", False):
            # (SetAimFunc As Single, fld dword ptr [ebp-14h] before the ret: a Double there keeps
            # digits the Single doesn't, and a simulation drifts off)
            size = getattr(lf.p, "float_ret_size", None)
            if size in (4, 8):
                return "Single" if size == 4 else "Double"
            return "Single" if "Single" in ts and "Double" not in ts else "Double"
        if ts - {"Boolean"}:
            ts.discard("Boolean")       # a comparison result mixed with numbers
        return pick(ts) if ts else None


# Date before the numeric types: a VT_DATE tag or a CDate assignment is exact
# where Long/Double are only the default the bit-width suggests
WIDE8 = {"Double", "Currency", "Date"}        # 8-byte ByVal parameters
INT_OPS = {"And", "Or", "Xor", "\\", "Mod", "Eqv", "Imp"}
ORDER = ["String", "Variant", "Object", "Date", "Double", "Single", "Currency", "Long",
         "Integer", "Byte", "Boolean"]

VT_NAMES = {2: "Integer", 3: "Long", 4: "Single", 5: "Double", 6: "Currency", 7: "Date",
            8: "String", 9: "Object", 11: "Boolean", 12: "Variant", 17: "Byte"}


def elem_type(info):
    """Element type of an array from its VARTYPE or element size."""
    if info.get("udt"):
        return info["udt"]
    if info.get("fixed"):
        return "String * %d" % info["fixed"]
    if info.get("class"):
        return info["class"]
    vt = info.get("vt")
    if vt in VT_NAMES:
        return VT_NAMES[vt]
    return {1: "Byte", 2: "Integer", 4: "Long", 8: "Double", 16: "Variant"}.get(info.get("size"), "Long")


def array_decl(name, info):
    """"name(bounds) As Type" for an array variable."""
    if info.get("dims") and not info.get("dyn"):
        parts = [("%d To %d" % (lb, lb + n - 1)) if lb else "%d" % (n - 1)
                 for lb, n in info["dims"]]
        return "%s(%s) As %s" % (name, ", ".join(parts), elem_type(info))
    lb, n = info.get("lb"), info.get("n")
    if info.get("dyn") or n is None:
        dims = ""
    elif isinstance(lb, int) and lb:
        dims = "%d To %d" % (lb, lb + n - 1)
    else:
        dims = "%d" % (n - 1)
    return "%s(%s) As %s" % (name, dims, elem_type(info))


WEAK_STRING = "String&"         # only ever an operand of &: a String unless typed otherwise
WEAK_LONG = "Long&"             # given a number that may be a Single's bits: a Long unless typed otherwise


def float_bits(v):
    """Could this dword be a Single somebody wrote?  Fast code stores x = 2.5 as mov [x], 40200000h,
    which looks like a Long as much as anything (DarwinBots y_LFOR = 10: As Long, whatever Input # and
    CStr said, and every later read a billion).  A value of six digits or fewer, and more than one bit
    set: &H40000000 is 2!, but more likely a flag."""
    import struct
    n = v & 0xffffffff
    if n & 0x7f800000 in (0, 0x7f800000) or bin(n).count("1") < 2:
        return False
    raw = struct.pack("<I", n)
    f = struct.unpack("<f", raw)[0]
    return 1e-6 <= abs(f) <= 1e9 and struct.pack("<f", float("%.6g" % f)) == raw


def dominant(ts, counts, byref=False):
    """What callers pass for one parameter, with the stragglers dropped: with
    many call sites a few mistyped arguments otherwise decide the parameter
    (a target: 2153 callers pass a Long record number, a handful look like
    String / Menu / TextBox, and ORDER puts String first - the parameter came
    out As String and every one of those 2153 calls was a type mismatch)."""
    # (a two-way conflict is left alone: lowering this to 2 for ByRef
    # parameters cost more than it fixed - a target 1383 -> 1478 problems)
    if not ts or not counts or len(ts) < 3:
        return ts
    top = max(counts.get(t, 0) for t in ts)
    rest = sum(counts.get(t, 0) for t in ts) - top
    if top < 8 or top < 3 * rest:
        return ts
    return {t for t in ts if counts.get(t, 0) == top}


def pick(ts):
    if WEAK_STRING in ts:
        rest = set(ts) - {WEAK_STRING}
        ts = rest if rest else {"String"}
    if WEAK_LONG in ts:
        rest = set(ts) - {WEAK_LONG}
        ts = rest if rest else {"Long"}
    for t in ORDER:
        if t in ts:
            return t
    return sorted(ts)[0] if ts else None
