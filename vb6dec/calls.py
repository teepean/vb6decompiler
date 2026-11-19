"""Call handling: runtime helpers, COM property/method calls, user
procedures and Declare'd API functions."""
import copy

from capstone import x86

from .ir import (Const, Str, Var, Me, AddrOf, Deref, BinOp, UnOp, Call, Prop,
                 Index, Unknown, Assign, Free, OnError, ExprStmt)
from .machine import SP, IatSlot, Hi
from . import ir
from . import runtime
from .typelib import Registry, INVOKE

X = x86


class Site:
    """One call turned into IR; `value` is what the callee returns."""

    def __init__(self, name, value=None, stmts=(), fpu=False):
        self.name, self.value, self.stmts, self.fpu = name, value, list(stmts), fpu


def target_name(lf, st, ins):
    """(kind, detail) for a call instruction.

    kind: 'runtime' | 'proc' | 'declare' | 'vtable' | 'indirect'
    """
    im = lf.im
    op = ins.operands[0]
    if op.type == X.X86_OP_IMM:
        t = op.imm
        if t in lf.m.declares:
            return "declare", lf.m.declares[t]
        if t in lf.m.procs:
            return "proc", lf.m.procs[t]
        n = import_thunk(lf, t)
        if n:
            return "runtime", n
        return "proc?", t
    if op.type == X.X86_OP_MEM:
        if op.mem.base == 0 and op.mem.index == 0:
            n = im.imports.get(op.mem.disp)
            if n:
                return "runtime", n
            return "indirect", Const(op.mem.disp)
        base = st.get(_reg(op.mem.base)) if op.mem.base else None
        return "vtable", (base, op.mem.disp)
    if op.type == X.X86_OP_REG:
        v = st.get(_reg(op.reg))
        if isinstance(v, IatSlot):
            return "runtime", v.name
        return "indirect", v
    return "indirect", Unknown("call")


def _reg(r):
    from .machine import REG32
    return REG32.get(r)


def import_thunk(lf, va):
    from .disasm import decode
    t = decode(lf.im, va)
    if t is not None and t.mnemonic == "jmp" and t.operands[0].type == X.X86_OP_MEM \
       and t.operands[0].mem.base == 0:
        return lf.im.imports.get(t.operands[0].mem.disp)
    return None


# ------------------------------------------------------------------ runtime
def empty_bstr(lf, a):
    """Str("") for a pointer to an empty string constant ([u32 0][u16 0] in
    .text), else a unchanged.  A procedure entry or an error-handler label is
    never one: code bytes can happen to match the pattern."""
    if isinstance(a, Const) and isinstance(a.value, int):
        va = a.value & 0xffffffff
        if va in getattr(lf.m, "procs", {}) or va in getattr(lf, "handlers", ()):
            return a
        im = lf.im
        try:
            if im.valid(va - 4) and im.valid(va + 1) and im.section_of(va) == ".text" and \
                    im.u32(va - 4) == 0 and im.u16(va) == 0:
                return Str("")
        except Exception:
            pass
    return a


def variant_receiver(lf, obj):
    """The receiver of a __vbaVarLate* call, a Variant passed by address: the
    Variant itself.  The address of a frame slot (a local, or a ByVal
    Variant parameter) is that variable; a pointer value (a ByRef Variant
    parameter) is *p."""
    ob = lf.plain(obj)
    fp = lf.frame_ptr(obj) if isinstance(obj, SP) else None
    if isinstance(fp, AddrOf):
        ob = fp.e
    elif isinstance(obj, SP):
        ob = lf.slot_var(obj.off, whole=True)
    elif isinstance(ob, AddrOf):
        ob = ob.e
    elif isinstance(ob, Var) and ob.space == "arg":
        lf.byref.add(ob.off)                # the parameter holds the Variant's address
    else:
        ob = Deref(ob, 4, 0)
    if isinstance(ob, Var):
        lf.variants.add(ob.key())
    return ob


def do_runtime(lf, st, name, stmts):
    spec = runtime.get(name)
    if spec is None:
        return generic(lf, st, name, stmts)
    args = []
    for r in spec.regs:
        args.append(st.get(r))
    if "Str" in name or "Bstr" in name:
        # a string function's literal "": an empty BSTR constant (length 0)
        args = [empty_bstr(lf, a) for a in args]
    if spec.fpu_in:
        for _ in range(spec.fpu_in):
            args.append(st.fpop())
    n = spec.nargs
    if spec.varargs or name.endswith("List") or spec.kind == "redim":
        cnt = st.args(1)[0]
        n = (cnt.value * (2 if spec.kind == "redim" else 1) + 1) if isinstance(cnt, Const) else 1
    k = spec.kind
    # Variant inputs: where an all-zero Variant is the literal Empty
    vin = k == "varres" or name.startswith(("__vbaVarTst", "__vbaVarCat", "__vbaVarAdd",
                                            "__vbaVarSub", "__vbaVarMul", "__vbaVarDiv",
                                            "__vbaVar", "rtc"))
    # only functions that take Variants get their arguments read as Variants:
    # a UDT whose first field is 7 is not a VT_DATE
    vdecode = "Var" in name or name.startswith("rtc") or k in ("varres", "varstore")
    if n:
        words = st.args(n)
        if name == "rtDecFromVar":
            words = words[::-1]         # (rtDecFromVar(&source, &result) returns an HRESULT)
        for i, a in enumerate(words):
            if (i == 0 and k in ("varres", "varset", "var2vec")) or not vdecode or i in spec.plain:
                # the result Variant: written, not read (Set O = c into a Function's
                # zeroed result came out as Set Empty = c)
                args.append(lf.frame_ptr(a))
            else:
                args.append(lf.frame_ptr(lf.variant_arg(a, empty_ok=vin)))
    if k in ("expr", "varres"):
        # any String argument can be the literal "": VB pushes the address of a
        # zero-length BSTR in .text, which reads as a bare number (gh_json
        # ParseJson: Replace(s, vbCr, ""))
        args = [empty_bstr(lf, a) for a in args]
    # an array parameter is a SAFEARRAY**: Ubound(*arg), AryCopy(&x, arg), ...
    if name in ("__vbaCyForInit", "__vbaCyForNext", "__vbaVarForInit", "__vbaVarForNext") and args and \
            isinstance(args[0], Const) and isinstance(args[0].value, int) and lf.data_address(args[0]):
        # For gC = ...: a module variable's address pushed as a number
        args[0] = AddrOf(lf.global_var(args[0].value & 0xffffffff, 8))
    if name in ("__vbaUbound", "__vbaLbound") and len(args) > 1 and isinstance(args[1], Const):
        # a module-level fixed array: its SAFEARRAY descriptor is in .data, the
        # global slot holding pvData at descriptor + 0x0C
        g = (args[1].value + 0x0c) & 0xffffffff
        if lf.im.section_of(args[1].value & 0xffffffff) == ".data" and \
                ("global", g) in lf.arrays:
            args[1] = lf.global_var(g, 4)
        elif lf.global_struct(args[1].value & 0xffffffff) is not None:
            # (a fixed array inside a module record, by its address: UBound(SimOpts.Specie))
            args[1] = lf.global_var(args[1].value & 0xffffffff, 4)
    arr_pos = {"__vbaUbound": (1,), "__vbaLbound": (1,), "__vbaAryCopy": (1,),
               "__vbaAryMove": (1,), "__vbaErase": (1,), "__vbaForEachAry": (4,)}.get(name, ())
    for i in arr_pos:
        if i < len(args):
            a = args[i]
            pv = a.e if isinstance(a, Deref) and a.off == 0 and name.endswith(("bound", "Ary")) else a
            pv = lf.plain(pv) if not isinstance(pv, Var) else pv
            if isinstance(pv, Var) and pv.space == "arg" and not lf.struct_array_param(pv):
                info = lf.arrays.setdefault(pv.key(), {"size": None, "dyn": True, "lb": None,
                                                       "n": None, "var": pv})
                lf.byref.add(pv.off)
                if name.endswith(("bound", "Ary")):
                    args[i] = pv            # UBound(*a): the parameter itself
                if name == "__vbaForEachAry" and isinstance(args[0], Const):
                    info.setdefault("vt", args[0].value)    # (the elements' VARTYPE)
    if name == "__vbaChkstk":
        size = st.get("eax")
        if isinstance(size, Const):
            st.esp -= size.value            # allocates the frame below the SEH record
        return Site(name, None)
    if k == "marshal":
        # __vbaStrToAnsi(&tmp, s): a ByVal String for an API; the API gets s
        st.drop(n)
        dst, src = args[0], args[1]
        lf.store_through(dst, src, stmts)
        if name == "__vbaStrToAnsi" and hasattr(src, "__dict__"):
            src = copy.copy(src)            # (a shared node)
            src.marshalled = True           # do_declare: a ByVal String
        return Site(name, src)
    if k == "fixconstruct":
        # __vbaFixstrConstruct(n, &slot): Dim s As String * n, on the heap; the slot
        # holds its address, and __vbaLsetFixstr takes that address with n = 0
        st.drop(n)
        p = lf.frame_ptr(args[1])
        if isinstance(args[0], Const) and isinstance(p, AddrOf):
            lf.note_fixed(p.e, args[0].value)
        return Site(name, None)
    if k == "unmarshal":
        # __vbaStrToUnicode(&tmp, ansi): what a String * n given to an API comes back
        # as; only the __vbaLsetFixstr writing it back reads the result (lsetfix)
        st.drop(n)
        got = Const(0)
        got.unmarshalled = True
        return Site(name, got)
    if k in ("free", "ignore"):
        if k == "free":
            stmts.append(Free([a for a in args], name))
            if name in ("__vbaFreeVar", "__vbaFreeVarList"):
                # (udt.resolve: its +0 and +8 are a type word and a payload, no fields)
                for a in args:
                    if isinstance(a, SP) and lf.base_esp is not None and a.off >= lf.base_esp:
                        lf.__dict__.setdefault("freed_variants", set()).add(("local", a.off + 4))
        if not spec.cdecl and not spec.regs:
            st.drop(n)
        elif not spec.cdecl:
            st.drop(n)
        return Site(name, None)
    if k == "store":
        # __vbaStrCopy(dst=ecx, src=edx) / __vbaStrMove
        dst, src = args[0], args[1]
        if name == "__vbaStrCopy":
            # what it copies is a String: a parameter it reads no other way (BasicCommandTok(s As
            # String), Select Case s, came out a1 As Long: its callers' words, Type mismatch)
            v = src.e if isinstance(src, Deref) and src.off == 0 else src
            if isinstance(v, Var) and v.space == "arg":
                lf.vt_types.setdefault(v.key(), set()).add("String")
        return Site(name, lf.store_through(dst, src, stmts, kind="let"))
    if k == "varstore":
        dst, src = args[0], lf.variant_arg(args[1], empty_ok=True)
        built = getattr(src, "vt", None) is not None     # a typed value wrapped by VB
        if not built and isinstance(src, SP) and src.off >= 4:
            # a copy from the parameter's own stack slot: 16 bytes there, a ByVal Variant whatever
            # else is done with it (gh_json IsUndefined: only VarType and TypeName of the copy)
            lf.__dict__.setdefault("byval_variants", set()).add(src.off)
        src = src.e if isinstance(src, AddrOf) else lf.plain(src)
        n0 = len(stmts)
        tgt = lf.store_through(dst, src, stmts, kind="let")
        if isinstance(src, Const):
            # the whole Variant: v = 1 is no VT_NULL tag for
            # simplify.fold_variant_slot_reads (RaiseEvent Multi("a", v, 2.5) lost v)
            for x in stmts[n0:]:
                if isinstance(x, Assign) and x.src is src:
                    x.whole_variant = True
        for v in (tgt,) if built else (tgt, src):
            if isinstance(v, Var):
                lf.variants.add(v.key())
        if isinstance(tgt, Deref) and tgt.off == 0 and isinstance(tgt.e, Var) and tgt.e.space == "arg":
            # a ByRef parameter given a Variant store: it is a Variant, whatever the values
            # (t79varref: r = Left$(s, 3) made it As String, and r = 42 came back as text)
            lf.variants.add(tgt.e.key())
        if isinstance(dst, SP):
            # the whole Variant, not its type word: v = 4: QBColor(v) read 4 as VT_R4
            # in variant_arg, and took the value from the slot 8 bytes on
            lf.known.pop(dst.off, None)
            lf.known.pop(dst.off + 8, None)
        # eax = the destination (mov edx, eax after __vbaVargVarRef: gh_dict Array(k, v))
        return Site(name, AddrOf(tgt) if isinstance(tgt, Var) else None)
    if k == "varset":
        st.drop(n)
        dst, src = args[0], args[1]
        src = src.e if isinstance(src, AddrOf) and "Var" in name[10:] else lf.plain(src)
        tgt = lf.store_through(dst, src, stmts, kind="set")
        if isinstance(tgt, Var):
            lf.variants.add(tgt.key())
            if tgt.space == "global" and isinstance(tgt.off, int):
                # Set into a Variant, in every procedure: a target's global_F04014
                # takes Printer or Screen, and came out As Printer (Set g = Screen:
                # Type mismatch at run time)
                lf.m.__dict__.setdefault("variant_globals", set()).add(tgt.off)
        return Site(name, None)
    if k == "castobj":
        # __vbaCastObj(obj, &IID) / __vbaCastObjVar(&var, &IID): the object as
        # the interface the IID names, implicit in VB.  That is the static type
        # of the expression cast (a property's declared type), not of the
        # variable it is Set into (tests/t14ocxext: Set o = pb1.DragIcon casts
        # to IPictureDisp, o As Object), so it only names the late calls by
        # DISPID made through it: VB calls by DISPID only where it knows the
        # type (a ListView's Font comes back as a Variant cast to IFontDisp:
        # a target Me.List1.Font.Bold = True printed "Set o1039 =
        # Me.List1.Font: o1039.__lateid = -1")
        st.drop(n)
        v = spec.f(args)
        if name.endswith("Var"):
            # what the Variant holds is an object, then: its late call's result goes in with Set.  As a
            # plain assignment VB reads the object's default member, and ListItems' wants an index:
            # v60 = Me.ListView1.ListItems stopped a target's Protect with error 450
            p = lf.frame_ptr(args[0])
            t = p.e if isinstance(p, AddrOf) else None
            for s in reversed(stmts) if isinstance(t, Var) else ():
                if isinstance(s, Assign) and isinstance(s.dst, Var) and s.dst.key() == t.key():
                    if isinstance(s.src, Call) and getattr(s.src, "late", False):
                        # (by its type, for render: as an Assign of kind set the temporary doesn't fold,
                        # and Me.List1.Font.Bold = True became Set o1038 = Me.List1.Font)
                        s.src.vbtype = "Object"
                    break
        if isinstance(args[1], Const) and lf.im.valid(args[1].value & 0xffffffff):
            g = lf.im.guid(args[1].value & 0xffffffff)
            it = lf.m.iface_by_iid.get(g) or Registry.find_guid(g)
            if it is not None:
                v = copy.copy(v)            # (a shared node: the Variant itself)
                v.cast = it
        return Site(name, v)
    if k == "objset":
        dst, src = args[0], args[1]
        st.drop(2)
        before = len(stmts)
        t0 = lf.frame_ptr(dst)
        t0 = t0.e if isinstance(t0, AddrOf) else None
        if isinstance(t0, Var) and t0.space == "local" and not lf.is_named_local(t0) and \
                getattr(src, "iface", None) is None and getattr(src, "cast", None) is not None and \
                not (isinstance(src, Var) and (lf.objtype.get(src.key()) is not None or
                                               src.space == "global" and
                                               lf.m.global_types.get(src.off) is not None)):
            # (a pooled temporary keeps no old type through a Set of something else: App.Major,
            # then Set t = late call: Set lastSim = t with Dim lastSim As App)
            lf.objtype.pop(t0.key(), None)
        tgt = lf.store_through(dst, src, stmts, kind="set")
        if isinstance(tgt, Var):
            lf.cast_types.pop(tgt.key(), None)  # (a slot reused: only its latest Set counts)
            # (a ByVal object parameter's own reference, kept in a frame slot: lateid_member)
            copies = lf.__dict__.setdefault("obj_copies", {})
            if isinstance(src, Var):
                copies[tgt.key()] = src
            else:
                copies.pop(tgt.key(), None)
        if getattr(src, "cast", None) is not None and isinstance(tgt, Var):
            lf.cast_types[tgt.key()] = src.cast
            for a in stmts[before:]:
                if isinstance(a, Assign) and a.kind == "set":
                    a.cast_set = True       # a temporary when read once (inline_temps)
            if src.cast in lf.m.iface_by_iid.values() and tgt.space in ("local", "global", "member") \
                    and lf.objtype.get(tgt.key()) is not src.cast:
                # Set a = d, a As IAnimal, d As cDog: VB casts to the variable's own type when it is one
                # of the program's (a library's property may cast to something else: see castobj).
                # As cDog or As Object, a.Speak or l.Note fails: only the interface has them.
                lf.objtype[tgt.key()] = src.cast
                # (and note_type's copy of another variable's type made it no mixture)
                lf.__dict__.setdefault("mixed_objects", set()).discard(tgt.key())
        if isinstance(src, Var) and src.space == "global" and hasattr(tgt, "key") and \
                lf.m.global_types.get(src.off) is not None:
            lf.objtype.setdefault(tgt.key(), lf.m.global_types[src.off])   # Set v = frmX
        it = getattr(lf.plain(src), "iface", None)
        if it is None and isinstance(src, Var):
            it = lf.objtype.get(src.key())
        if it is not None and isinstance(tgt, Var) and tgt.space == "global" and \
                isinstance(tgt.off, int):
            # Set g = New Collection (or another typed object): every procedure's
            # next lift calls through g (VBDoom texindex(UCase$(name)) was vtbl_1C)
            old = lf.m.global_types.setdefault(tgt.off, it)
            if getattr(old, "name", None) != getattr(it, "name", None):
                # (a target global_F04014 As Printer: Set g = Screen, Type mismatch)
                lf.m.__dict__.setdefault("mixed_globals", set()).add(tgt.off)
        if getattr(src, "control", None) is not None:
            return Site(name, src)          # keep the control reference itself
        return Site(name, tgt)
    if k == "varres":
        # f(&result, ...): the result Variant lands in a frame slot; eax = &result
        v = spec.f(args)
        if isinstance(v, Call):
            v.raw = v.raw or name
            v.pure = spec.pure
            v.vbtype = v.vbtype or "Variant"
        st.drop(n)
        tgt = lf.store_through(args[0], v, stmts)
        if isinstance(tgt, Var):
            lf.variants.add(tgt.key())
        for a in args[1:]:
            x = a.e if isinstance(a, AddrOf) and getattr(a, "vt", None) is None else None
            if isinstance(x, Var) and x.space in ("local", "arg"):
                lf.variants.add(x.key())
        return Site(name, AddrOf(tgt) if isinstance(tgt, Var) else args[0])
    if k == "ongocheck":
        # On k GoTo prints as tests of k, which take any value; the runtime's check goes before them
        # (On -1 GoTo A: error 5, tests_wip/coverage/t23ongosub)
        st.drop(n)
        stmts.append(ExprStmt(runtime.stmt("If {0} < 0 Or {0} > 255 Then Error 5", [args[0]])))
        return Site(name, args[0])
    if k == "gosub":
        st.drop(n)
        target = getattr(lf.p, "gosubs", {}).get(lf.cur_va)
        if target is not None:
            g = runtime.stmt("GoSub L%X" % target, [])
            g.name = "GoSub L%X" % target   # (as text: If k > 2 Then GoSub A Else GoSub B went for two
            stmts.append(ExprStmt(g))       # of the same, structure.hoist_common compares repr)
        return Site(name, Const(0))
    if k == "recuni":
        # the record behind the ANSI copy goes to the Declare: as a record, or VB makes no copy
        # (Shell_NotifyIcon nim, theTray as VarPtr(m3C) would give the API Unicode: the record's
        # descriptor is the type of m3C too)
        st.drop(n)
        desc = args[0].value & 0xffffffff if isinstance(args[0], Const) else None
        rec = lf.record_type(desc)
        r = lf.record_ref(args[2])
        if rec is not None and isinstance(r, Deref) and r.off == 0:
            r.size = rec.size
        lf.note_record(r, rec)
        a = AddrOf(r)
        a.rec_ansi = True
        return Site(name, a)
    if k in ("recassign", "recdestruct"):
        st.drop(n)
        desc = args[0].value & 0xffffffff if isinstance(args[0], Const) else None
        rec = lf.record_type(desc)
        refs = [lf.record_ref(a) for a in args[1:]]
        if rec is not None:
            for r in refs:
                # the whole record, not its first field (also inside a module's record, which
                # optimised code reaches by constant address: Specie = TmpOpts.Specie(50))
                if isinstance(r, Deref):
                    r.size = rec.size
        for r in refs:
            lf.note_record(r, rec)
        if k == "recassign" and len(refs) == 2:
            a = Assign(refs[0], refs[1])
            a.record = rec
            stmts.append(a)
        if k == "recdestruct" and len(args) > 1:
            # it returns the record's address: Get #f, h, Form1(a) frees the
            # element and hands eax on to __vbaGetOwner4 (a target ReadAll;
            # eax was left at a * 728 from the address arithmetic)
            return Site(name, args[1])
        return Site(name, None)
    if k == "aryconstruct":
        st.drop(n)
        sa, tpl, vt = args[0], args[1], args[2]
        if isinstance(sa, AddrOf) and isinstance(sa.e, Var) and sa.e.space == "local" and \
                isinstance(sa.e.off, int) and isinstance(tpl, Const):
            lf.fixed_array(sa.e.off, tpl.value & 0xffffffff, vt.value if isinstance(vt, Const) else None)
        return Site(name, None)
    if k == "raiseevent":
        fixed = st.args(3)
        idx, cnt = fixed[1], fixed[2]
        n = cnt.value if isinstance(cnt, Const) and 0 <= cnt.value < 64 else 0
        words = st.args(3 + 4 * n)[3:]
        # DISPPARAMS order: the Variant nearest the fixed arguments is the last one
        vals = [byval_variant(lf, words[4 * i:4 * i + 4]) for i in range(n)][::-1]
        om = lf.m.members.get(lf.obj.index) if lf.obj is not None else None
        ev = None
        if om is not None and isinstance(idx, Const) and 1 <= idx.value <= len(om.events):
            ev = om.events[idx.value - 1]
        e = Call("RaiseEvent", [lf.plain(v) for v in vals], pure=False)
        e.event = ev.name if ev is not None else "Event%s" % (idx.value if isinstance(idx, Const) else "?")
        e.event_member = ev                 # its parameters, for ByRef typing
        stmts.append(ExprStmt(e))
        return Site(name, None)
    if k == "latememst":
        if name.endswith("StAd"):
            # (obj, name, 0, Variant by value), cdecl, as __vbaLateIdStAd: read as six dwords the
            # 0 was the type word (SETUP1 Set ctl.Font = oFont: oFont never assigned)
            args = st.args(7)
            args = args[:2] + args[3:]
        else:
            args = st.args(6)
            st.drop(6)
        # (__vbaVarLateMemSt: the receiver is a Variant by address)
        obj = variant_receiver(lf, args[0]) if name.startswith("__vbaVarLate") else \
            lf.plain(args[0])
        mname = args[1]
        member = late_name(lf, mname)
        val = lf.plain(byval_variant(lf, args[2:6]))
        tgt = Prop(obj, member or "__late", [])
        tgt.late = True
        late_receiver(lf, obj)
        a = Assign(tgt, val, "set" if name.endswith("Ad") else "let")
        a.va = lf.cur_va
        stmts.append(a)
        return Site(name, None)
    if k == "lateidst":
        if name.endswith("Ad"):
            # (obj, dispid, 0, Variant by value), cdecl: "add esp, 1Ch" after it
            # (a target Set lst.SelectedItem = lst.ListItems(i) took the 0 for
            # the type word and the Variant's second dword for the value)
            args = st.args(7)
            args = args[:2] + args[3:]
        else:
            args = st.args(6)
            st.drop(6)
        obj, dispid = lf.plain(args[0]), args[1]
        member = lateid_member(lf, obj, dispid) or "__lateid"
        val = lf.plain(byval_variant(lf, args[2:6]))
        tgt = Prop(obj, member, [])
        tgt.late = True
        late_receiver(lf, obj)
        a = Assign(tgt, val, "set" if name.endswith("Ad") else "let")
        a.va = lf.cur_va
        stmts.append(a)
        return Site(name, None)
    if k == "latecallst":
        # (a target v44.SubItems(1) = x, a late bound ListItem; printed VarLateMemCallSt)
        fixed = st.args(3)
        obj, mname, cnt = fixed
        n = cnt.value if isinstance(cnt, Const) and 0 <= cnt.value < 64 else 0
        words = st.args(3 + 4 * (n + 1))[3:]
        # the value first, then the subscripts from the last: v30.SubItems(1) = "Target" pushes
        # "Target", 1 (as DISPPARAMS has them)
        val = lf.plain(byval_variant(lf, words[:4]))
        vals = [lf.plain(byval_variant(lf, words[4 * i + 4:4 * i + 8])) for i in range(n)][::-1]
        ob = variant_receiver(lf, obj) if name.startswith("__vbaVarLate") else lf.plain(obj)
        tgt = Prop(ob, late_name(lf, mname) or "__late", vals)
        tgt.late = True
        late_receiver(lf, ob)
        a = Assign(tgt, val, "let")
        a.va = lf.cur_va
        stmts.append(a)
        return Site(name, None)
    if k in ("latecall", "latecallld"):
        # __vbaLateMemCall(obj, &name, argc, Variant by value...) and
        # __vbaLateMemCallLd(&result, obj, &name, argc, ...), cdecl: obj.name args
        nf = 4 if k == "latecallld" else 3
        fixed = st.args(nf)
        obj, mname, cnt = fixed[nf - 3], fixed[nf - 2], fixed[nf - 1]
        n = cnt.value if isinstance(cnt, Const) and 0 <= cnt.value < 64 else 0
        words = st.args(nf + 4 * n)[nf:]
        # last argument first, as for __vbaRaiseEvent (t05m LateTest2)
        vals = [lf.plain(byval_variant(lf, words[4 * i:4 * i + 4])) for i in range(n)][::-1]
        if "LateId" in name:
            member = None if isinstance(mname, Const) and mname.value == 0 else \
                (lateid_member(lf, lf.plain(obj), mname) or "__lateid")
        else:
            member = late_name(lf, mname)
        ob = variant_receiver(lf, obj) if name.startswith("__vbaVarLate") else lf.plain(obj)
        if "LateId" in name and member is None:
            e = Index(ob, vals)                 # the default member: o(k)
            e.vbtype = "Variant"
            e.late = True
        else:
            e = Call(member or "__late", vals, obj=ob, method=True, pure=False,
                     vbtype="Variant")
            if member == "__lateid" and isinstance(mname, Const):
                e.dispid = mname.value & 0xffffffff     # (render: the object may be known then)
        e.late = True
        e.loaded = k == "latecallld"        # (a value wanted: a property's Get, no statement by itself)
        late_receiver(lf, ob)
        if k == "latecall":
            stmts.append(ExprStmt(e))
            return Site(name, None)
        tgt = lf.store_through(lf.frame_ptr(fixed[0]), e, stmts)
        if isinstance(tgt, Var):
            lf.variants.add(tgt.key())
        return Site(name, AddrOf(tgt) if isinstance(tgt, Var) else fixed[0])
    if k == "onerror":
        tgt = args[0]
        st.drop(n)
        if isinstance(tgt, Const):
            if tgt.value == 0xFFFFFFFF or tgt.value == -1:
                stmts.append(OnError("Resume Next"))
            elif tgt.value == 0:
                stmts.append(OnError("GoTo 0"))
            elif tgt.value in (0xFFFFFFFD, -3):
                stmts.append(OnError("GoTo -1"))
            else:
                lf.handlers.add(tgt.value & 0xffffffff)
                stmts.append(OnError("GoTo", tgt.value & 0xffffffff))
        return Site(name, None)
    if k == "exitproc":
        return Site(name, None)
    if k == "raise":
        return Site(name, None)
    if k == "new":
        # __vbaNew2(&{?, &CLSID, &IID}, &objvar): create objvar on first use
        st.drop(n)
        desc, dst = args[0], args[1]
        po = lf.m.obj_by_info.get(desc.value & 0xffffffff) if isinstance(desc, Const) else None
        if po is not None:          # a project class or form: its ObjectInfo is passed
            cls, iface = None, lf.m.proj_ifaces.get(po.index)
        else:
            cls, iface = lf.m.class_of_desc(desc.value if isinstance(desc, Const) else None)
        var = lf.global_var(dst.value) if isinstance(dst, Const) else lf.plain(dst)
        if isinstance(var, BinOp) and var.op == "+" and isinstance(var.a, Var) and isinstance(var.b, Const):
            var = Deref(var.a, 4, var.b.value)      # (a member's address as a sum: lea eax, [ebx + 34h])
        space = getattr(var, "space", None)
        if iface is not None:
            lf.objtype[var.key()] = iface
            if space == "global":
                lf.m.global_types[var.off] = iface
        if cls is not None and cls.name == "Global":
            lf.m.global_names[var.off] = ""        # the implicit VB global namespace
            return Site(name, None)
        po = po or getattr(iface, "obj", None)      # one of the project's own forms/classes
        if po is not None and po.kind == "Form" and space == "global":
            lf.m.global_names[var.off] = po.name    # the form's predeclared instance
            lf.auto_new[var.key()] = po.name        # (created on first use, like As New)
            return Site(name, None)
        if po is not None:
            # "Dim x As New C": VB creates x on first use; the test is implicit
            lf.auto_new[var.key()] = po.name
            return Site(name, None)
        v = Call("New", [], vbtype="Object")       # Set fso = New FileSystemObject
        v.new_class = cls.name if cls else "Object"
        if iface is not None:
            v.iface = iface
        # (a register here holds the variable's address, from another block: lea esi,
        # [ecx + 34h] for a member As New; simplify.alias_addr_temps makes it the member)
        stmts.append(Assign(Deref(var, 4, 0) if isinstance(var, Var) and space == "reg"
                            else var, v, "set"))
        return Site(name, None)
    if k == "newobj":
        # __vbaNew(&ObjectInfo): New of one of the project's classes
        st.drop(n)
        o = lf.m.obj_by_info.get(args[0].value & 0xffffffff) if isinstance(args[0], Const) else None
        v = Call("New", [], vbtype="Object")
        if o is not None:
            v.new_class = o.name
            v.iface = lf.m.proj_ifaces.get(o.index)
        elif isinstance(args[0], Const):
            # {0, &CLSID, &IID}: a library class (New Collection)
            cls, itf = lf.m.class_of_desc(args[0].value & 0xffffffff)
            if cls is not None:
                v.new_class = cls.name
                v.iface = itf
        return Site(name, v)
    if k == "erase":
        st.drop(n)
        a = args[1]
        arr = lf.var_at(a.value & 0xffffffff) if lf.data_address(a) else lf.array_arg(a)
        stmts.append(ExprStmt(Call("Erase", [arr])))
        return Site(name, None)
    if k == "varindex":
        fixed = st.args(4 if name.endswith("RefLock") else 3)
        if name.endswith("RefLock"):
            # (result, object, lock state, count): the lock state slot is the
            # one __vbaAryUnlock takes afterwards (gh_json Walk)
            fixed = [fixed[0], fixed[1], fixed[3]]
        nfix = 4 if name.endswith("RefLock") else 3
        cnt = fixed[2]
        n = cnt.value if isinstance(cnt, Const) and 0 < cnt.value < 16 else 1
        words = st.args(nfix + 4 * n)[nfix:]
        # (index order for n > 1 is a guess: verify with a 2-D Variant array)
        subs = [lf.plain(byval_variant(lf, words[4 * i:4 * i + 4])) for i in range(n)][::-1]
        arr = lf.plain(lf.frame_ptr(fixed[1]))
        if isinstance(arr, AddrOf):
            arr = arr.e
        elif isinstance(arr, Var) and arr.space == "arg":
            lf.byref.add(arr.off)           # the pointer itself: a ByRef Variant (gh_zip At)
        lf.variants.add(arr.key()) if hasattr(arr, "key") else None
        e = Index(arr, subs)
        e.vbtype = "Variant"
        tgt = lf.store_through(lf.frame_ptr(fixed[0]), e, stmts)
        if isinstance(tgt, Var):
            lf.variants.add(tgt.key())
        return Site(name, AddrOf(tgt) if isinstance(tgt, Var) else fixed[0])
    if k == "varindexstore":
        fixed = st.args(2)
        cnt = fixed[1]
        n = cnt.value if isinstance(cnt, Const) and 0 < cnt.value < 16 else 1
        words = st.args(2 + 4 + 4 * n)[2:]
        val = lf.plain(byval_variant(lf, words[0:4]))
        subs = [lf.plain(byval_variant(lf, words[4 + 4 * i:8 + 4 * i])) for i in range(n)][::-1]
        arr = lf.plain(lf.frame_ptr(fixed[0]))
        if isinstance(arr, AddrOf):
            arr = arr.e
        elif isinstance(arr, Var) and arr.space == "arg":
            lf.byref.add(arr.off)
        if hasattr(arr, "key"):
            lf.variants.add(arr.key())
        e = Index(arr, subs)
        e.vbtype = "Variant"
        a = Assign(e, val)
        a.va = lf.cur_va
        stmts.append(a)
        return Site(name, None)
    if k == "derefary":
        st.drop(n)
        a = lf.plain(args[0])
        a = a.e if isinstance(a, AddrOf) else a
        if isinstance(a, Deref) and a.off == 0 and isinstance(a.e, Var) and \
                a.e.space == "arg" and not lf.struct_array_param(a.e):
            # *param: an array parameter's SAFEARRAY (VBDoom AM_drawLineCharacter
            # lineguy()), unless the first lift showed a UDT whose first field
            # is the array (cht_MakeSeq cht.sequence: struct_array_params)
            a = a.e
            lf.byref.add(a.off)
        if isinstance(a, Var):
            lf.arrays.setdefault(a.key(), {"size": None, "dyn": True, "lb": None, "n": None,
                                           "var": a})
        return Site(name, AddrOf(Index(a, [lf.plain(args[1])])))
    if k == "derefarym":
        cnt = st.args(1)[0]
        dims = cnt.value if isinstance(cnt, Const) and 0 < cnt.value < 61 else 1
        raw = st.args(2 + dims)
        a = lf.plain(raw[1])
        a = a.e if isinstance(a, AddrOf) else a
        if isinstance(a, Deref) and a.off == 0 and isinstance(a.e, Var) and a.e.space == "arg" and \
                not lf.struct_array_param(a.e):
            a = a.e
            lf.byref.add(a.off)
        if isinstance(a, Var):
            lf.arrays.setdefault(a.key(), {"size": None, "dyn": True, "lb": None, "n": None,
                                           "var": a})
        return Site(name, AddrOf(Index(a, [lf.plain(x) for x in raw[:1:-1]])))
    if k == "refvarary":
        st.drop(n)
        v = lf.plain(args[0])
        v = v.e if isinstance(v, AddrOf) else v
        return Site(name, AddrOf(v) if isinstance(v, Var) else args[0])
    if k == "arylock":
        st.drop(n)
        tgt = lf.plain(args[0])
        tgt = tgt.e if isinstance(tgt, AddrOf) else tgt
        arr = lf.plain(args[1])
        if isinstance(arr, Deref) and arr.off == 0 and isinstance(arr.e, Var) and \
                arr.e.space == "arg":
            arr = arr.e                     # *param: an array parameter's SAFEARRAY
            lf.arrays.setdefault(arr.key(), {"size": None, "dyn": True, "lb": None,
                                             "n": None, "var": arr})
            lf.byref.add(arr.off)
        if isinstance(tgt, Var):
            if isinstance(arr, Var):
                lf.sa_alias[tgt.key()] = arr    # element addresses through tmp are arr's
            # one temporary may lock several arrays in turn (gh_zip
            # pvOutputLocalHeader: baFName, then baExtra through a register):
            # a marker resolved after inlining (simplify.apply_locks)
            lf.__dict__.setdefault("sa_locks", {}).setdefault(tgt.key(), []).append(arr)
            mk = ExprStmt(Call("__arylock", [tgt, arr]))
            mk.va = lf.cur_va
            stmts.append(mk)
        return Site(name, None)
    if k == "varptr":
        st.drop(n)
        a = args[0] if isinstance(args[0], AddrOf) else lf.plain(args[0])
        if isinstance(a, BinOp) or lf.data_address(a):
            # pvData + i * k: an element (VBDoom VarPtr(whdr(i)), VarPtr(mixbuf(b * ...)));
            # a module variable by its address
            ea = lf.element_address(a) if isinstance(a, BinOp) else \
                (lf.module_struct_ref(a) or AddrOf(lf.global_var(a.value & 0xffffffff)))
            if ea is not None:
                a = ea
        if isinstance(a, AddrOf):
            v = Call("VarPtr", [a.e])       # VarPtr(x): pushes &x
        elif isinstance(a, Var) and a.space == "arg" and a.off in lf.byref:
            v = Call("VarPtr", [a])         # a ByRef parameter: its own address
        elif getattr(a, "iface", None) is not None or (hasattr(a, "key") and
                                                        lf.objtype.get(a.key())):
            v = Call("ObjPtr", [a])
        else:
            v = Call("StrPtr", [a])         # the BSTR value itself
            if isinstance(a, Var) and a.space == "arg":
                # or VarPtr(p) of a ByRef parameter: its pointer, pushed as is
                # (gh_zip pvToDosDateTime VarPtr(nDate)); the type decides
                lf.__dict__.setdefault("varptr_args", set()).add(a.off)
        v.vbtype, v.pure = "Long", True
        return Site(name, v)
    if k == "vararg":
        # returns its edx argument (the Variant itself, through any VT_BYREF)
        src = st.get("edx")
        if isinstance(src, Var) and src.space == "arg":
            lf.variants.add(src.key())      # a Variant parameter, read in place
            lf.byref.add(src.off)
        return Site(name, src)
    if k == "var2vec":
        # __vbaVar2Vec(&sa, &v): sa = the array in v
        st.drop(n)
        v = lf.plain(args[1])
        if name == "__vbaStr2Vec":
            from .listing import vb_string
            if isinstance(v, Const):
                t = vb_string(lf.im, v.value & 0xffffffff) if v.value else ""
                v = Str(t if t is not None else "")
        else:
            v = v.e if isinstance(v, AddrOf) else (Deref(v) if not (isinstance(v, Var) and
                                                                    v.space == "arg") else v)
        dst0 = lf.plain(args[0])
        if isinstance(dst0, Var) and dst0.space == "arg":
            # ba = "" for an array parameter: the caller's array, through the pointer
            lf.arrays.setdefault(dst0.key(), {"size": 1, "vt": 17, "dyn": True, "lb": None,
                                              "n": None, "var": dst0})
            lf.byref.add(dst0.off)
            a = Assign(Deref(dst0, 4, 0), v)
            a.va = lf.cur_va
            stmts.append(a)
            return Site(name, None)
        tgt = lf.store_through(args[0], v, stmts)
        if isinstance(tgt, Var):
            # a dynamic array of Bytes, what these two make (an array of another type goes through
            # __vbaAryVar).  As Long, b = StrConv(s, vbFromUnicode) was an error 13: a target's mHex
            lf.arrays.setdefault(tgt.key(), {"size": 1, "vt": 17, "dyn": True, "lb": None, "n": None,
                                             "var": tgt})
        return Site(name, None)
    if k == "checktype":
        st.drop(n)
        obj = lf.plain(args[0])
        if name.endswith("Var"):
            obj = obj.e if isinstance(obj, AddrOf) else Deref(obj)
        cname = None
        if isinstance(args[1], Const) and lf.im.valid(args[1].value & 0xffffffff):
            g = lf.im.guid(args[1].value & 0xffffffff)
            it = lf.m.iface_by_iid.get(g) or Registry.find_guid(g)
            cname = getattr(it, "name", None)
            if cname and cname.startswith("_") and not getattr(it, "obj", None):
                cname = cname[1:]           # _TextBox -> TextBox
        v = Call("TypeOf", [obj], vbtype="Boolean")
        v.fmt = "TypeOf {0} Is %s" % (cname or "Object")
        v.pure = True
        return Site(name, v)
    if k == "lsetfix":
        # __vbaLsetFixstr(n, &dst, src): dst (a String * n) = src
        st.drop(n)
        cnt = args[0].value if isinstance(args[0], Const) else None
        before = len(stmts)
        if getattr(args[2], "unmarshalled", False):
            # VB's own write-back after an API call: the call in the source says it
            # all (a target Form8: LSet v28 = v58, v58 never set, wiped what
            # GetWindowsDirectory had just returned)
            return Site(name, None)
        if cnt == 0:
            # n = 0: LSet on a variable-length String, which keeps its length and
            # pads (t54lsetstr: s = "xy" lost the padding)
            src = empty_bstr(lf, lf.plain(args[2]))
            tgt = lf.store_through(args[1], src, [])    # (only for the target)
            if isinstance(args[1], SP):
                lf.known.pop(args[1].off, None)         # (its value is not src)
            stmts.append(ExprStmt(Call("LSet", [tgt, src], raw=name)))
            return Site(name, None)
        # (the literal "" is the address of an empty BSTR constant)
        tgt = lf.store_through(args[1], empty_bstr(lf, lf.plain(args[2])), stmts)
        for a in stmts[before:]:
            if isinstance(a, Assign):
                a.fixstr = cnt
                lf.note_fixed(a.dst, cnt)
        lf.note_fixed(tgt, cnt)
        return Site(name, None)
    if k == "rsetfix":
        # __vbaRsetFixstr(n, &dst, src): RSet dst = src
        st.drop(n)
        cnt = args[0].value if isinstance(args[0], Const) else None
        src = empty_bstr(lf, lf.plain(args[2]))
        tgt = lf.store_through(args[1], src, [])        # (only for the target)
        if isinstance(args[1], SP):
            lf.known.pop(args[1].off, None)             # (its value is not src)
        if cnt:
            lf.note_fixed(tgt, cnt)
        stmts.append(ExprStmt(runtime.stmt("RSet {0} = {1}", [tgt, src])))
        return Site(name, None)
    if k == "strfix":
        # __vbaStrFixstr(n, &src): the String * n as a String
        st.drop(n)
        v = lf.plain(args[1])
        v = v.e if isinstance(v, AddrOf) else Deref(v)
        if isinstance(args[0], Const):
            v.fixstr = args[0].value
            lf.note_fixed(v, args[0].value)
        return Site(name, v)
    if k == "aryvar":
        # __vbaAryVar(vt, &variant): the array a Variant holds (parts = Split(...))
        st.drop(n)
        vt = args[0].value & 0xfff if isinstance(args[0], Const) else None
        v = lf.plain(args[1])
        v = v.e if isinstance(v, AddrOf) else v
        lf.last_aryvar = vt
        return Site(name, v)
    if k == "arycopy":
        st.drop(n)
        if n == 3:
            args = args[1:]                 # __vbaAryRecCopy: the record descriptor first
        dst, src = lf.array_arg(args[0]), lf.array_arg(args[1])
        # (an array in a record's element, by its descriptor's address: dna = rob(n).dna, optimised)
        for i, a in enumerate((dst, src)):
            if isinstance(a, BinOp) and a.op in ("+", "-"):
                fld = lf.dyn_field(a)
                if fld is None:
                    el = lf.dyn_element_linear(a)
                    fld = (el, 0) if el is not None else None
                if fld is not None:
                    if i == 0:
                        dst = Deref(fld[0], 4, fld[1])
                    else:
                        src = Deref(fld[0], 4, fld[1])
        vt = getattr(lf, "last_aryvar", None)
        lf.last_aryvar = None
        if vt is not None and isinstance(dst, Var):
            info = lf.arrays.setdefault(dst.key(), {"size": VT_SIZE.get(vt, 4), "dyn": True,
                                                    "lb": None, "n": None, "var": dst})
            info["vt"] = vt
        # both sides are (dynamic) arrays of the same type
        for a, b in ((src, dst), (dst, src)):
            info = lf.arrays.get(a.key()) if isinstance(a, Var) else None
            if info is not None and isinstance(b, Var) and b.key() not in lf.arrays:
                lf.arrays[b.key()] = dict(info, var=b, dyn=True, dims=None)
        if isinstance(dst, Var) and dst.space == "arg":
            lf.byref.add(dst.off)
            dst = Deref(dst, 4, 0)          # an array parameter: the caller's array
        stmts.append(Assign(dst, src))
        return Site(name, None)
    if k == "redimvar":
        head = st.args(3)
        nd = head[2].value if isinstance(head[2], Const) and 0 < head[2].value <= 8 else 1
        all_args = st.args(3 + 2 * nd)
        var = lf.array_arg(all_args[1])
        if isinstance(var, Var):
            lf.variants.add(var.key())
        bounds = []
        for d in range(nd):
            ub, lb = all_args[3 + 2 * d], all_args[4 + 2 * d]
            bounds.append(BinOp("To", lb, ub))
        e = Call("ReDim Preserve" if "Preserve" in name else "ReDim",
                 [Index(var, bounds)], pure=False)
        e.redim = True
        vt = all_args[0]
        tn = {2: "Integer", 3: "Long", 4: "Single", 5: "Double", 6: "Currency", 7: "Date",
              8: "String", 11: "Boolean", 12: "Variant", 17: "Byte"}.get(
            vt.value & 0xfff if isinstance(vt, Const) else None)
        if tn:
            e.redim_as = tn                 # ReDim v(0 To n) As Byte on a Variant
        stmts.append(ExprStmt(e))
        return Site(name, None)
    if k == "redim":
        # (flags, cbElements, &array, vt, nDims, ub1, lb1, ub2, lb2 ...)
        # (a cdecl spec with no count: args is empty, so read nDims off the stack.
        # Every ReDim came out one-dimensional: ReDim g(100) of g(100, 1), used as
        # g(i, j): Subscript out of range, a target Proc_85B670)
        nd = st.args(5)[4]
        nd = nd.value if isinstance(nd, Const) and 0 < nd.value <= 8 else 1
        all_args = st.args(5 + 2 * nd)
        var = lf.array_arg(all_args[2])
        if lf.struct_array_param(var):
            lf.byref.add(var.off)
            var = Deref(var, 4, 0)          # ReDim p.f0(..): the UDT's array field
        if isinstance(var, Var) and var.space == "arg":
            lf.byref.add(var.off)           # ReDim of an array parameter
        bounds = []
        for d in range(nd):
            ub, lb = all_args[5 + 2 * d], all_args[6 + 2 * d]
            lb0 = isinstance(lb, Const) and lb.value == 0
            bounds.append(ub if lb0 else BinOp("To", lb, ub))
        bounds.reverse()                    # (pushed last dimension first: t74redim2)
        e = Call("ReDim Preserve" if "Preserve" in name else "ReDim",
                 [Index(var, bounds)], pure=False)
        e.redim = True
        e.redim_info = (all_args[1], all_args[3])   # cbElements, vt (simplify.map_statics)
        if isinstance(var, Var):
            info = lf.arrays.setdefault(var.key(), {"size": 4, "dyn": True, "lb": None,
                                                    "n": None, "var": var})
            info["dyn"] = True
            cb = all_args[1]
            if isinstance(cb, Const):
                info["size"] = cb.value
            vt = all_args[3]
            if isinstance(vt, Const):
                info["vt"] = vt.value
                if vt.value == 0x24 or vt.value == 0 and isinstance(cb, Const) and \
                        cb.value not in (1, 2, 4, 8):
                    # VT_RECORD, or no VARTYPE at all (unoptimised code): UDT
                    # elements (VBDoom glows(), 16 bytes each: no Variants)
                    info["record"] = True
                if vt.value & 0xffffffff > 0xffff and lf.im.valid(vt.value & 0xffffffff):
                    # an array of records: the "vt" is the record descriptor
                    rec = lf.record_type(vt.value & 0xffffffff)
                    if rec is not None and isinstance(var, Var):
                        lf.rec_of[var.key()] = rec.va
                        lf.udt[var.key()] = rec.name
                        info["udt"] = rec.name
                        info["size"] = info.get("size") or rec.size
        stmts.append(ExprStmt(e))
        return Site(name, None)
    if k == "copybytes":
        # __vbaCopyBytes(len, &dst, &src): LSet dst = src (a byte copy of a UDT)
        st.drop(n)
        cnt, dst, src = args[0], args[1], args[2]
        # elements of fixed arrays of UDTs by address (LSet net(i, j) = loc(j))
        dst = lf.element_address(dst) or dst
        src = lf.element_address(src) or src

        def first(p):
            # pvData itself: a fixed array's first element (a target LSet
            # Form1(a) = empty1(1) printed the array, a Type mismatch)
            info = lf.arrays.get(p.key()) if isinstance(p, Var) else None
            if info is None or info.get("dyn") or not info.get("fixed_bounds"):
                return p
            dims = info.get("dims") or [(info.get("lb") or 0, info.get("n"))]
            e = Index(p, [Const(lb) for lb, _ in dims])
            e.vbtype = None
            return AddrOf(e)
        dst, src = first(dst), first(src)

        def part(p):
            # &a(i) + k: a part of an element, a nested UDT copied whole
            # (VBDoom itemrespawnque(iquehead) = mobjs(mobj).spawnpoint)
            if isinstance(p, BinOp) and p.op == "+" and isinstance(p.a, AddrOf) and \
                    isinstance(p.a.e, Index) and isinstance(p.b, Const) and \
                    isinstance(p.b.value, int) and isinstance(cnt, Const):
                return AddrOf(Deref(p.a.e, cnt.value, p.b.value))
            return p
        dst, src = part(dst), part(src)
        stmts.append(ExprStmt(Call("LSet", [dst, src, cnt], raw=name)))
        return Site(name, None)
    if k in ("print", "printfile"):
        return do_print(lf, st, name, stmts)
    if k == "inputfile":
        # __vbaInputFile(&desc, file, &item...), cdecl: Input #f, a, b; desc is
        # like Print's, {u16 count, a code per item} (a target Proc_6A0A80)
        desc = st.args(1)[0]
        if isinstance(desc, Str) and getattr(desc, "va", None):
            desc = Const(desc.va)
        if isinstance(desc, Const) and lf.im.valid(desc.value & 0xffffffff):
            n = lf.im.u16(desc.value & 0xffffffff)
            if 0 < n < 64:
                raw = st.args(2 + n)
                items = [lf.item_ref(a) for a in raw[2:]]
                e = Call("Input", [lf.plain(raw[1])] + items, pure=False)
                e.stmt_fmt = "Input #{0}, " + ", ".join("{%d}" % (i + 1) for i in range(n))
                # (80h + the item's VARTYPE)
                e.input_codes = [lf.im.u8(desc.value + 2 + i) & 0x3f for i in range(n)]
                stmts.append(ExprStmt(e))
        return Site(name, None)
    if k == "stmtcall":
        raw0 = args[0] if args else None
        if isinstance(raw0, Str) and getattr(raw0, "va", None):
            raw0 = Const(raw0.va)           # a descriptor that reads like a string
        # a module variable by its address (Put #f, , numsectors; Get #f, , players)
        args = [AddrOf(lf.var_at(a.value & 0xffffffff)) if lf.data_address(a) else a
                for a in args]
        if name.startswith(("__vbaPut", "__vbaGet")) and len(args) > 1:
            # an element of a fixed array of records by its address, pvData +
            # i * size (Put #f, n, Book(i): a target printed the sum)
            args[1] = lf.element_address(args[1]) or args[1]
        if "FxStr" in name and isinstance(args[0], Const) and isinstance(args[0].value, int):
            v = args[1].e if isinstance(args[1], AddrOf) else args[1]
            lf.note_fixed(v, args[0].value)     # Dim v As String * n
        if "Owner" in name and len(args) > 1 and isinstance(args[1], AddrOf) and \
                isinstance(args[1].e, Var):
            # the descriptor VB builds around a fixed array in a record, to Get or Put it whole:
            # the element its pvData is set to (Get #1, , Specie(k).Mutables.mutarray printed
            # Get #1, , k, what the slot keeps of it: lift._store)
            pv = lf.sa_alias.get(args[1].e.key())
            held = lf.known.get(pv.off - 4) if isinstance(pv, Var) and isinstance(pv.off, int) else None
            if isinstance(held, AddrOf) and isinstance(held.e, (Index, Deref)):
                whole = (held.e, pv)
            else:
                whole = None
        else:
            whole = None
        v = spec.f(args)
        if whole is not None:
            v.whole_array = whole           # (render; the arguments stay, for the passes)
        if "Owner" in name:
            v.owner_io = True       # Put / Get of a record (udt.collect: a struct)
            v.owner_desc = raw0     # (decompile.global_struct_hints)
        st.drop(n)
        stmts.append(ExprStmt(v))
        return Site(name, None)
    if k == "identity":
        # returns its (pointer) argument: keep the raw frame address, so loads
        # through it read the Variant built there
        raw = st.args(1)[0]
        st.drop(n)
        return Site(name, raw)
    if k == "cy64":
        # an 8-byte result in edx:eax: eax is the value, edx its high half
        v = spec.f(args)
        v.vbtype = spec.ret
        v.pure = True
        site = Site(name, v)
        site.edx = Hi(v)
        if not spec.cdecl:
            st.drop(n)
        return site
    # plain expression
    if name == "__vbaR4ForNextCheck":
        # (the limit is only ever moved: For x = 6 To 30 Step 6 kept 30's bits in a Long,
        # &H41F00000, and ran a billion times; a target Proc_789740)
        for x in args[:3]:
            lf.note_size(x, 4, True)
    v = spec.f(args) if spec.f else Call(name, args)
    if isinstance(v, Call) and v.raw is None:
        v.raw = name
    if isinstance(v, Call) and spec.pure and not v.method:
        v.pure = True
    if spec.ret and getattr(v, "vbtype", None) is None:
        v.vbtype = spec.ret
    if not spec.cdecl:
        st.drop(n)
    return Site(name, v, fpu=spec.fpu_out)


# VT -> dwords pushed (default 1).  A Variant goes by reference, one dword,
# variable or temporary alike (Print #1, "a"; v; o.Count; d; w: &v, &tmp, d
# as two dwords, &w); taking four made t10carr's next item read the stack
# below it
# A control's extender members, late-bound by DISPID (tools probe: 80010000h
# + the designer opcode for properties, 80011000h on for methods)
EXTENDER_DISPIDS = {0x80010000: "Name", 0x80010003: "Left", 0x80010004: "Top",
                    0x80010005: "Width", 0x80010006: "Height", 0x80010007: "Visible",
                    0x80010008: "Parent", 0x80010009: "DragMode", 0x8001000A: "DragIcon",
                    0x8001000B: "Tag", 0x8001000D: "Enabled", 0x8001000E: "TabStop", 0x8001000F: "TabIndex",
                    0x8001001C: "Object", 0x80010032: "HelpContextID", 0x8001003B: "Align",
                    0x80010046: "WhatsThisHelpID", 0x80010047: "Container",
                    0x80010048: "CausesValidation", 0x8001004A: "ToolTipText",
                    0x80011000: "ZOrder", 0x80011001: "Move"}


def pushed_dispid(lf):
    """The DISPID of a late call whose arguments began before the control's getter: its
    error check starts a new block, so the push arrives as a stack variable
    (a target Form9 Me.List.ZOrder printed Me.List.__lateid)."""
    addrs = sorted(lf.p.insns)
    import bisect
    i = bisect.bisect_left(addrs, lf.cur_va)
    for a in reversed(addrs[max(0, i - 24):i]):
        ins = lf.p.insns[a]
        if ins.mnemonic == "push" and ins.operands[0].type == X.X86_OP_IMM and \
                ins.operands[0].imm & 0xffffffff in EXTENDER_DISPIDS:
            return Const(ins.operands[0].imm & 0xffffffff)
    return None


def lateid_member(lf, ob, dispid):
    """The member a late-bound call by DISPID names: the extender's, or the
    one with that memid in the receiver's interface (a control's: a target's
    ProgressBar.Value is DISPID 5, Refresh -550)."""
    if isinstance(dispid, Var) and dispid.space == "stk":
        dispid = pushed_dispid(lf)
    if not isinstance(dispid, Const):
        return None
    d = dispid.value & 0xffffffff
    if d in EXTENDER_DISPIDS:
        return EXTENDER_DISPIDS[d]
    # the cast of the latest Set first: a slot reused for a Font keeps the type of
    # the OptionButton it held before (a target Form9 Form_Load)
    iface = lf.cast_types.get(ob.key()) if hasattr(ob, "key") else None
    if iface is None:
        iface = lf.iface_of(ob)
    if iface is None:
        iface = control_arg_iface(lf, ob)
    if iface is None:
        x = ob.e if isinstance(ob, Deref) and ob.off == 0 else ob
        if isinstance(x, Var):
            x = lf.__dict__.get("obj_copies", {}).get(x.key(), x)
            slots = getattr(lf.m, "object_slots", {})
            k = ("member", lf.p.owner.index, x.off) if x.space == "member" and lf.p.owner else \
                (x.space, lf.p.va, x.off)
            held = slots.get(k)
            if held is not None:
                return (PICTURE_DISPIDS if held == "Picture" else FONT_DISPIDS).get(d)
    if getattr(iface, "name", "") in ("Picture", "IPictureDisp", "StdPicture"):
        return PICTURE_DISPIDS.get(d)           # (Set p = v.Image: p.Width)
    if iface is None or getattr(iface, "name", "") in ("Font", "IFontDisp", "StdFont"):
        # a control's Font (StdFont's dispinterface, standard DISPIDs: a target
        # Me.Fonts.Font.__lateid = 9 for .Size)
        if isinstance(ob, Prop) and ob.name == "Font" or iface is not None:
            return FONT_DISPIDS.get(d)
        if isinstance(ob, Prop) and ob.name in ("Picture", "Image", "Icon", "DragIcon",
                                                "MouseIcon"):
            return PICTURE_DISPIDS.get(d)       # StdPicture (a target v.Image.Width)
        return None
    names = [f.name for f in iface.all_funcs()
             if f.memid is not None and (f.memid & 0xffffffff) == d and f.name]
    for v in getattr(iface, "vars", ()) or ():
        if getattr(v, "memid", None) is not None and (v.memid & 0xffffffff) == d and \
                getattr(v, "name", None):
            names.append(v.name)
    # (the visible name before a hidden alias)
    names.sort(key=lambda n: n.startswith("_"))
    if names and names[0].startswith("_") and names[0] != "_Default":
        # the alias alone (Slider's _Value is DISPID 0, its Value another): the same name without
        # the underscore, else in brackets, the only way VB takes a hidden name
        plain = names[0][1:]
        if any(f.name and f.name.lower() == plain.lower() for f in iface.all_funcs()):
            return plain
        return "[%s]" % names[0]
    if names and names[0] == "_Default":
        # an As Object parameter has no type for render to find the real name by,
        # and a1 = LoadPicture would assign the picture's Handle
        from .render import DEFAULT_PROPS
        return DEFAULT_PROPS.get(iface.name, "_Default")
    return names[0] if names else None


# StdPicture / IPictureDisp (OLE's fixed picture DISPIDs)
PICTURE_DISPIDS = {0: "Handle", 2: "hPal", 3: "Type", 4: "Width", 5: "Height"}
# StdFont / IFontDisp (OLE's fixed font DISPIDs)
FONT_DISPIDS = {0: "Name", 2: "Size", 3: "Bold", 4: "Italic", 5: "Underline",
                6: "Strikethrough", 7: "Weight", 8: "Charset"}


def late_receiver(lf, ob):
    """A variable late-bound calls go through is declared As Object (or is a
    Variant): a local, or a ByRef parameter, whose calls go through [a1]
    (a target Proc_8FE430(a1 As Long) took Me and read a1.WindowState)."""
    if isinstance(ob, Var):
        lf.late_bound.add(ob.key())
    elif isinstance(ob, Deref) and ob.off == 0 and isinstance(ob.e, Var) and \
            ob.e.space == "arg" and isinstance(ob.e.off, int):
        lf.late_bound.add(ob.e.key())
        lf.byref.add(ob.e.off)


PRINT_WORDS = {5: 2, 6: 2, 7: 2, 14: 4, 20: 2}


def predeclared(lf, desc, g):
    """What a __vbaNew2 of a form's predeclared variable or the Global object says of G."""
    po = lf.m.obj_by_info.get(desc)
    if po is not None:
        cls, iface = None, lf.m.proj_ifaces.get(po.index)
    else:
        cls, iface = lf.m.class_of_desc(desc)
    var = lf.global_var(g)
    if iface is not None:
        lf.objtype[var.key()] = iface
        if var.space == "global":
            lf.m.global_types[var.off] = iface
    if cls is not None and cls.name == "Global":
        lf.m.global_names[var.off] = ""
        return
    po = po or getattr(iface, "obj", None)
    if po is not None and var.space == "global":
        lf.m.global_names[var.off] = po.name
        lf.auto_new[var.key()] = po.name


def do_print(lf, st, name, stmts):
    """__vbaPrintObj(&desc, obj, items...): obj.Print item; item ..."""
    im = lf.im
    desc = st.args(1)[0]
    if isinstance(desc, Str) and getattr(desc, "va", None):
        desc = Const(desc.va)
    if not isinstance(desc, Const) or not im.valid(desc.value & 0xffffffff):
        return Site(name, None)
    d = desc.value & 0xffffffff
    count = im.u16(d)
    codes = [im.u8(d + 2 + i) for i in range(min(count, 32))]
    i = 1
    obj = st.args(2)[1]
    i = 2
    items = []
    for c in codes:
        if not c & 0x3f:
            continue                    # no item: a bare Print (just the newline)
        n = PRINT_WORDS.get(c & 0x3f, 1)
        vals = st.args(i + n)[i:i + n]
        v = vals[0] if vals else Unknown("?")
        if n == 2 and len(vals) == 2 and (c & 0x3f) in (5, 6, 7):
            v = byval_double(vals, c & 0x3f)    # Double / Currency / Date: two dwords
            lo, hi = vals
            if isinstance(lo, Deref) and isinstance(hi, Deref) and lo.size == hi.size == 4 and \
                    hi.off == lo.off + 4 and lo.e.key() == hi.e.key():
                # (both halves of a field: all that shows it is 8 bytes if it is only ever printed,
                # gP.d = 2.5 in two stores: f10 As Long, f14 As Long, and 0 printed.  t101ornall)
                v = Deref(lo.e, 8, lo.off)
        if (c & 0x3f) == 12 or (c & 0x40):            # by reference (Variant, ...)
            v = lf.variant_arg(v)
            v = v.e if isinstance(v, AddrOf) else lf.plain(v)
            if isinstance(v, BinOp) and v.op == "+":
                v = Deref(v)            # the item's address, computed: what is there is printed
        if (c & 0x3f) == 11 and isinstance(v, Const) and isinstance(v.value, int):
            v = Const(v.value, "Boolean")   # (Write #1, False writes #FALSE#, not 0)
        if (c & 0x3f) == 8:
            v = empty_bstr(lf, v)           # (Print #1, "": the empty BSTR's address printed 4413576)
        items.append((v, c))
        i += n
    if name in ("__vbaPrintFile", "__vbaWriteFile"):
        e = Call("Print", [v for v, c in items], obj=lf.plain(obj), method=True)
        e.file = True                       # Print #n, items
        e.write = name == "__vbaWriteFile"  # Write #n, items
    else:
        e = Call("Print", [v for v, c in items], obj=lf.recv_expr(obj), method=True)
    e.print_codes = codes
    stmts.append(ExprStmt(e))
    return Site(name, None)


def generic(lf, st, name, stmts):
    """Unknown runtime function: use the derived stack-pop count."""
    info = lf.m.rtinfo.get(name)
    n = 0
    if info and info.get("ret"):
        n = max(info["ret"]) // 4
    args = st.args(n)
    st.drop(n)
    if name in GENERIC_TYPES:
        # IsEmpty(VARIANT*) & co.: the variable behind a frame address, so the
        # passes see it read (gh_dict Exists: else v28 = dict_GetKeyValue(Key)
        # looked dead and only "dict_GetKeyValue Key" was left)
        args = [lf.plain(a) if isinstance(a, SP) else a for a in args]
    if name == "rtcBstrFromError" and args and isinstance(args[0], SP):
        # Error$(Err) passes a Variant built on the frame (VT_DISPATCH, the Err
        # object), Error$ a missing one.  Printed as the frame slot it read as an
        # empty Variant: the message lost its text (", 53, Rec1 = ...")
        v = lf.variant_arg(args[0])
        if isinstance(v, AddrOf):
            args = [] if isinstance(v.e, ir.Missing) else [v.e]
    return Site(name, Call(vbname(name), args, vbtype=GENERIC_TYPES.get(name)))


# result types of runtime functions handled generically
GENERIC_TYPES = {n: "Boolean" for n in ("rtcIsEmpty", "rtcIsNull", "rtcIsNumeric", "rtcIsDate",
                                         "rtcIsArray", "rtcIsObject", "rtcIsError",
                                         "rtcIsMissing")}


# runtime statements whose VB keyword is not the helper's name without "rtc"
# (a target: ChangeDir / ChangeDrive were "Sub or Function not defined")
RTC_KEYWORDS = {"rtcChangeDir": "ChDir", "rtcChangeDrive": "ChDrive", "rtcMakeDir": "MkDir",
                "rtcRemoveDir": "RmDir", "rtcSetFileAttr": "SetAttr", "rtcFileReset": "Reset",
                # plain-value functions (the Variant forms are runtime specs)
                "rtcBstrFromError": "Error$", "rtcGetDateBstr": "Date$", "rtcGetTimeBstr": "Time$",
                "rtcStrFromVar": "Str$", "rtcFileLocation": "Loc", "rtcFileSeek": "Seek",
                "rtcGetFileAttr": "GetAttr", "rtcFileAttributes": "FileAttr",
                # the byte (B) forms: Left$ etc. are rtcLeftCharBstr
                "rtcLeftBstr": "LeftB$", "rtcRightBstr": "RightB$", "rtcMidBstr": "MidB$",
                "rtcBstrFromByte": "ChrB$", "rtcByteValueBstr": "AscB"}


def vbname(name):
    if name in RTC_KEYWORDS:
        return RTC_KEYWORDS[name]
    if name.startswith("rtc"):
        return name[3:]
    if name.startswith("__vba"):
        return name[5:]
    return name


# ------------------------------------------------------------------ COM
def unwrap(base):
    """[obj] is obj's vtable pointer: recover the object."""
    if isinstance(base, Deref) and base.off == 0:
        return base.e
    return base


def frame_address_slot(proc, slot):
    """X when every store to the frame slot is lea r, [ebp+X]; mov [ebp+slot], r."""
    cache = proc.__dict__.setdefault("_addr_slots", {})
    if slot not in cache:
        found, addrs = set(), sorted(proc.insns)
        for i, a in enumerate(addrs):
            ins = proc.insns[a]
            ops = ins.operands
            if ins.mnemonic == "mov" and len(ops) == 2 and ops[0].type == X.X86_OP_MEM and \
                    ops[0].mem.base == X.X86_REG_EBP and ops[0].mem.disp == slot:
                p = proc.insns[addrs[i - 1]] if i else None
                if p is not None and p.mnemonic == "lea" and ops[1].type == X.X86_OP_REG and \
                        p.operands[0].reg == ops[1].reg and p.operands[1].mem.base == X.X86_REG_EBP:
                    found.add(p.operands[1].mem.disp)
                else:
                    found.add(None)
        cache[slot] = next(iter(found)) if len(found) == 1 else None
    return cache[slot]


def do_vtable(lf, st, base, off, stmts):
    """Call through an object vtable: resolve to a property or method."""
    if isinstance(base, Var) and base.space == "local" and isinstance(base.off, int):
        # the vtable pointer parked in a frame temporary ("mov [ebp-N], ebx"
        # with ebx = [Me], gh_zip pvOutputEndHeader): what was stored there
        v = lf.known.get(base.off - 4)
        if isinstance(v, Deref) and v.off == 0:
            base = v
    base = unwrap(base)
    iface = lf.iface_of(base)
    if iface is None and off not in (0, 4, 8):
        # the vtable pointer lost at a join, the object it belongs to not: the
        # this pointer on top of the stack (a target Text1_LostFocus: three
        # Cases jump to one "call [edx+0A4h]" with ebx = Me.Text1 pushed)
        this0 = lf.plain(st.args(1)[0])
        if isinstance(this0, Me):
            # (Me's own procedure: Cases jumping to one "call [eax+8F8h]", a target's
            # UserControl1 printed vtbl_8F8 mF0, ...)
            base, iface = this0, lf.iface_of(this0)
        elif getattr(this0, "control", None) is not None:
            iface = getattr(this0, "iface", None)
        elif isinstance(this0, Var) and lf.iface_of(this0) is not None:
            # (a variable, its vtable loaded before the auto-instancing test's join: Dim c
            # As New cK ... c.table1 2 printed vtbl_1C v64)
            base, iface = this0, lf.iface_of(this0)
        elif isinstance(this0, Var) and this0.space == "local" and isinstance(this0.off, int):
            # (unoptimised: t = *p with p = &c on both ways out of the test)
            v = lf.known.get(this0.off - 4)
            if isinstance(v, Deref) and v.off == 0 and isinstance(v.e, Var) and \
                    v.e.space == "local" and isinstance(v.e.off, int):
                x = frame_address_slot(lf.p, v.e.off)
                it = lf.objtype.get(("local", x)) if x is not None else None
                if it is not None:
                    iface = it
    f = None
    if iface is not None:
        f = pick(iface, off)
    if off in (0, 4, 8):
        # IUnknown: QueryInterface / AddRef / Release (reference counting only)
        st.drop(1 if off else 3)
        return Site("IUnknown", None)
    if f is None:
        ctl = lf.control_for(base, off)
        if ctl is not None:
            st.drop(1)
            return Site("ctl", ctl)
        proc = lf.own_method(base, off)
        if isinstance(proc, tuple):
            return own_accessor(lf, st, proc, stmts)
        if proc is not None:
            return do_proc(lf, st, proc, stmts, com=True)
        e = getattr(iface, "entries", {}).get(off) if iface is not None else None
        if e is not None and e[0] == "proc" and lf.m.procs.get(e[1]) is not None:
            # another project object's slot its members don't name
            return do_proc(lf, st, lf.m.procs[e[1]], stmts, com=True)
        if e is not None and e[0] == "withevents" and e[2] in ("let", "set"):
            # its WithEvents variable, if Public (Set Form1.t.OwnerForm = Form1: vtbl_24)
            args = st.args(2)
            st.drop(2)
            tgt = Prop(lf.plain(args[0]), e[1].name, [])
            a = Assign(tgt, lf.plain(args[1]), "set" if e[2] == "set" else "let")
            a.va = lf.cur_va
            stmts.append(a)
            return Site(e[1].name, None)
        n = guess_argc(lf, st)
        args = st.args(n)
        st.drop(n)
        return Site("vtbl", Call("vtbl_%X" % off, args[1:]))
    if getattr(f, "control", None) is not None:
        # another form's control getter: the control comes back in eax
        this = st.args(1)[0]
        st.drop(1)
        v = Prop(lf.recv_expr(this), f.name)
        v.control = f.control
        v.iface = getattr(f, "ret_iface_obj", None)
        return Site("ctl", v)
    widths = [param_width(p) for p in f.params]
    nargs = 1 + sum(widths)
    raw = st.args(nargs)
    st.drop(nargs)
    this = raw[0]
    rest, i = [], 1
    for p, w in zip(f.params, widths):
        chunk = raw[i:i + w]
        i += w
        if w == 4:                          # a Variant passed by value
            rest.append(byval_variant(lf, chunk))
        elif w == 2:                        # Double / Currency / Date by value
            rest.append(byval_double(chunk, p.type.vt))
        else:
            a = chunk[0]
            if p.type.vt == 26 and p.type.sub is not None and p.type.sub.vt == 12 and \
                    not p.flags & 8:
                if isinstance(a, Var) and a.space == "arg" and isinstance(a.off, int):
                    # our parameter's own pointer passed on: VB hands a VARIANT* on
                    # unchanged only when the parameter is a ByRef Variant (any
                    # other type gets a VT_BYREF wrapper; gh_dict TryAdd d.Add k, v)
                    lf.byref.add(a.off)
                    lf.__dict__.setdefault("variant_params", set()).add(a.off)
                a = lf.variant_arg(a)       # a Variant by reference (not the result slot)
            if p.type.vt == 8:
                a = empty_bstr(lf, a)       # a BSTR by value: "" (Me.Item1.Text = &H4EC3DC)
            rest.append(typed_literal(a, p.type.vt))
    recv = lf.recv_expr(this)
    if f.invkind == 2:                       # property get: last arg is the out slot
        out = rest[-1] if rest else None
        v = Prop(recv, f.name, [lf.in_arg(p, a) for p, a in zip(f.params, rest[:-1])],
                 vbtype=ret_type(f))
        v.memid = f.memid                   # (render.hidden_default)
        v.iface = ret_iface(f, iface)
        if getattr(f, "control", None) is not None:
            v.control = f.control           # another form's control
        if out is not None:
            lf.bind_temp(out, v)
            return Site(f.name, Const(0))   # eax is the HRESULT; the value is in *out
        return Site(f.name, v)
    if f.invkind in (4, 8):                  # property let / set
        val = rest[-1] if rest else Unknown("?")
        if f.params and f.params[-1].type.vt == 11 and isinstance(val, Const):
            val = Const(val.value, "Boolean")
        tgt = Prop(recv, f.name, [lf.in_arg(p, a) for p, a in zip(f.params, rest[:-1])])
        tgt.memid = f.memid
        stmts.append(Assign(tgt, lf.in_arg(f.params[-1] if f.params else None, val),
                            "set" if f.invkind == 8 else "let"))
        # (eax is the HRESULT, not what it held before: vD4 = Form1.SecTimer in a slot the
        # compiler keeps it in, the value of a vtable left there)
        return Site(f.name, Const(0))
    ret = None
    pars = list(f.params)
    note_array_args(lf, pars, rest)
    if pars and pars[-1].flags & 8:          # retval
        out = rest[-1] if rest else None
        v = Call(f.name, drop_defaults(pars, [lf.in_arg(p, a) for p, a in zip(pars[:-1], rest[:-1])]),
                 vbtype=ret_type(f), obj=recv, method=True)
        v.iface = ret_iface(f, iface)
        v.func = f
        if out is not None:
            lf.bind_temp(out, v)
            if getattr(f, "ret_array", None) is not None:
                tgt = lf.plain(out)             # the result slot holds a SAFEARRAY
                if isinstance(tgt, Var):
                    lf.arrays[tgt.key()] = {"size": VT_SIZE.get(f.ret_array, 4), "dyn": True,
                                            "lb": None, "n": None, "vt": f.ret_array, "var": tgt}
            return Site(f.name, Const(0))   # eax is the HRESULT; the value is in *out
        return Site(f.name, v)
    v = Call(f.name, drop_defaults(pars, [lf.in_arg(p, a) for p, a in zip(pars, rest)]),
             obj=recv, method=True)
    v.func = f                              # parameter types, for ByRef typing
    return Site(f.name, v)


def own_accessor(lf, st, entry, stmts):
    """Me's accessor of one of its variables (VB reads and sets WithEvents
    variables through them): the variable itself."""
    kind, info, acc = entry
    if kind == "withevents":
        var = Var("member", info.slot, info.name)
        src = lf.m.events_of_iid.get(info.guid)
        lf.withevents[var.key()] = src.name if src is not None else None
        if src is not None:
            lf.objtype[var.key()] = lf.m.proj_ifaces.get(src.index)
    else:
        var = Var("member", info.offset, info.name)
    args = st.args(2)
    st.drop(2)
    if acc == "get":
        lf.bind_temp(args[1], var)
        return Site(info.name, Const(0))
    stmts.append(Assign(var, lf.plain(args[1]), "set" if acc == "set" else "let"))
    return Site(info.name, None)


VT_SIZE = {2: 2, 3: 4, 4: 4, 5: 8, 6: 8, 7: 8, 8: 4, 9: 4, 11: 2, 12: 16, 17: 1}


def drop_defaults(params, args):
    """Trailing arguments equal to their Optional parameter's default: the
    compiler filled those in for an omitted argument."""
    args = list(args)
    n = len([p for p in params if not p.flags & 8])
    while args and len(args) <= n:
        p = params[len(args) - 1]
        d = getattr(p, "default", None)
        a = args[-1]
        if not p.flags & 16:
            break
        if isinstance(a, ir.Missing):
            args.pop()
            continue
        vt = p.type.sub.vt if p.type.vt == 26 and p.type.sub is not None else p.type.vt
        if d is None and vt != 12:
            d = (vt, "" if vt == 8 else 0)  # no default given: the type's zero value
        if d is None:
            break
        dv = d[1]
        if isinstance(a, Str) and a.text == dv or isinstance(a, Const) and \
                not isinstance(dv, str) and a.value == dv:
            args.pop()
            continue
        if vt == 8 and dv == "" and isinstance(a, Const) and a.value == 0:
            args.pop()                      # a null BSTR: the omitted String
            continue
        break
    return args


def param_width(p):
    """Dwords a COM parameter takes on the stack."""
    vt = p.type.vt
    if vt == 12:
        return 4
    if vt in (5, 6, 7):
        return 2
    return 1


def byval_variant(lf, chunk):
    """A Variant pushed by value: tag, reserved, value (low, high)."""
    import struct as _s
    tag = chunk[0]
    if isinstance(tag, ir.Missing):
        # fold_variant_slot_reads already saw the sentinel in the tag half;
        # the payload half below is the sentinel's own value
        return ir.Missing()
    def slot_of(x):
        """(space, frame offset) a dword read comes from: v, or v's k-th dword."""
        if isinstance(x, Var) and isinstance(x.off, int) and x.space in ("local", "arg"):
            return x.space, x.off
        if isinstance(x, Deref) and isinstance(x.e, Var) and isinstance(x.e.off, int) and \
                x.e.space in ("local", "arg"):
            return x.e.space, x.e.off + x.off
        return None
    sl = [slot_of(x) for x in chunk] if len(chunk) == 4 and not isinstance(tag, Const) else []
    if sl and all(sl) and len({x[0] for x in sl}) == 1 and \
            [x[1] - sl[0][1] for x in sl] == [0, 4, 8, 12]:
        # the 16 bytes of a Variant variable copied onto the stack: that variable
        v = Var(sl[0][0], sl[0][1])
        v.byval_whole = True                # (simplify.fold_variant_reaching)
        tag = lf.known.get(sl[0][1] - 4) if sl[0][0] == "local" else None
        if isinstance(tag, Const) and isinstance(tag.value, int) and tag.value & 0x4000:
            # VT_BYREF: the Variant VB builds around a variable for an array
            # index or ByRef Variant parameter - the variable itself (pvInit).
            # lf.known is keyed esp-relative: 4 below the ebp-relative slot.
            ref = lf.frame_ptr(lf.known.get(sl[0][1] + 4))
            if isinstance(ref, AddrOf) and isinstance(ref.e, Var):
                return ref.e
            if isinstance(ref, BinOp) or lf.data_address(ref):
                from .simplify import byref_target
                return byref_target(lf, ref, tag.value)    # an element, a module variable
        lf.variants.add(v.key())
        return v
    if len(chunk) == 4 and not isinstance(tag, Const) and all(isinstance(x, Deref) for x in chunk) and \
            all(x.e == chunk[0].e for x in chunk) and \
            [x.off - chunk[0].off for x in chunk] == [0, 4, 8, 12]:
        d = Deref(chunk[0].e, 16, chunk[0].off)      # a Variant field
        d.vbtype = "Variant"
        return d
    if not isinstance(tag, Const):
        if not isinstance(tag, (Var, Deref, SP)) and getattr(tag, "vbtype", None) == "Variant":
            # the whole 16 bytes are one Variant expression written into the slot
            # (`Walk` over o("list"): the index load's result is the Variant; its
            # payload half below is a stale value from an earlier use of the slot)
            return tag
        k = slot_of(tag)
        if k is not None and k[0] == "local" and len(chunk) == 4 and \
                slot_of(chunk[1]) == ("local", k[1] + 4) and isinstance(chunk[2], Const):
            # the type word stored before a join (unknown here) but the payload a
            # constant the lift still knows, a module variable's address: the
            # Variant at k, which simplify.fold_variant_reaching decodes from
            # what every path stored (VT_BYREF | VT_BSTR there: Printer.FontName
            # = global_F041C4 printed = &HF041C4); the payload is its fallback
            v = Var("local", k[1])
            v.payload_guess, v.byval_whole = chunk[2], True
            lf.variants.add(v.key())
            return v
        return chunk[2] if len(chunk) > 2 else Unknown("variant")
    t = tag.value & 0xffff
    if t & 0x8000:
        t &= 0x7fff                         # a Variant VB built from a literal
    val = chunk[2] if len(chunk) > 2 else None
    if t == 10:
        return ir.Missing()
    if t & 0x4000 and val is not None:      # VT_BYREF: a pointer to the variable
        p = lf.frame_ptr(val)
        if isinstance(p, AddrOf):
            return p.e
        ea = lf.element_address(val) if isinstance(val, BinOp) else None
        if ea is not None:
            return ea.e                     # an element (VBDoom IIf(playeringame(i), 1, 0))
        if isinstance(val, Var) and val.space == "arg" and t & 0xfff in lf.VT_TYPE:
            # a ByRef parameter given on through a Variant: the tag is its type (v.DecryptIt a1, ...
            # with a1 As Long by default made a mismatch of every path: "index file corrupted")
            lf.vt_types.setdefault(val.key(), set()).add(lf.VT_TYPE[t & 0xfff])
        return Deref(val)
    if t in (9, 13) and isinstance(val, Var) and val.space == "local" and isinstance(val.off, int):
        # (built in a frame slot, then its 16 bytes copied: the payload a constant the lift still
        # knows; lf.known is keyed 4 below the slot)
        k = lf.known.get(val.off - 4)
        if isinstance(k, Const):
            val = k
    if t in (9, 13) and isinstance(val, Const) and val.value == 0:
        # Variant holding no object: PropBag.ReadProperty("PICN", Nothing).  Printed 0, the default
        # came back a number and Set m78 = it was 424 (a target UserControl1: VB6 runs a
        # UserControl's code just loading the project, the build stopped at the message box)
        return Var("global", ("vbconst", 9), "Nothing")
    if t in (2, 3, 9, 11, 13, 17) and val is not None:  # (9, 13: an object reference)
        from .lift import long_literal
        return Const(val.value, "Boolean") if t == 11 and isinstance(val, Const) else \
            long_literal(val, t)
    if t == 8 and isinstance(val, Const):
        from .listing import vb_string
        txt = vb_string(lf.im, val.value & 0xffffffff)
        return Str(txt) if txt is not None else val
    if t in (4, 5, 6, 7) and isinstance(val, Const):
        # Single bits; Double / Currency / Date as two dwords
        return lf.wide_value(t, val, chunk[3] if len(chunk) > 3 else None)
    return val if val is not None else Unknown("variant")


def byval_double(chunk, vt=5):
    """An 8-byte value passed as two dwords (VT_R8, VT_CY, VT_DATE)."""
    import struct as _s
    lo, hi = chunk[0], chunk[1]
    if isinstance(lo, Const) and isinstance(hi, Const):
        raw = _s.pack("<II", lo.value & 0xffffffff, hi.value & 0xffffffff)
        if vt == 6:
            return Const(_s.unpack("<q", raw)[0] / 10000.0, "Currency")
        return Const(_s.unpack("<d", raw)[0], "Date" if vt == 7 else None)
    if isinstance(hi, Hi):
        return hi.of
    if isinstance(lo, Const) or isinstance(hi, Const):
        # one half a register the compiler keeps zero: decide after
        # constant propagation (simplify.fold_pairs)
        c = Call("__pair", [lo, hi], vbtype={6: "Currency", 7: "Date"}.get(vt, "Double"))
        c.pure = True
        return c
    return lo


def typed_literal(a, vt):
    """A dword literal passed for a parameter of type vt, written as VB would."""
    import struct as _s
    if isinstance(a, Call) and a.name == "CByte" and len(a.args) == 1 and \
            isinstance(a.args[0], Const) and vt == 17:
        return a.args[0]                    # CByte(9) for a Byte parameter: 9
    if not isinstance(a, Const) or not isinstance(a.value, int):
        return a
    if vt == 4:
        if not a.value & 0x7f800000 and a.value & 0x7fffff:
            return a                        # (a denormal is no literal: a small integer)
        return Const(_s.unpack("<f", _s.pack("<I", a.value & 0xffffffff))[0], "Single")
    if vt == 11:
        return Const(a.value, "Boolean")
    return a


def pick(iface, off):
    hits = [f for f in iface.all_funcs() if f.vtoff == off]
    if not hits:
        return None
    named = [f for f in hits if not f.name.startswith("_")]
    return (named or hits)[0]


def ret_iface(f, iface):
    """Interface of an object-valued property/method result."""
    if getattr(f, "ret_iface_obj", None) is not None:
        return f.ret_iface_obj
    if not f.params:
        return None
    t = f.params[-1].type
    while t is not None and t.vt == 26:
        t = t.sub
    if t is None or t.vt != 29:
        return None
    lib = getattr(iface, "lib", None)
    r = lib.resolve_href(t.href) if lib is not None else None
    if r is not None and getattr(r, "kind", None) == 5 and r.impls and \
            getattr(r, "lib", None) is not None:      # coclass: its default interface
        r = r.lib.resolve_href(r.impls[0][0])
    return r if getattr(r, "kind", -1) in (3, 4) else None


def ret_type(f):
    if f.params and f.params[-1].flags & 8:
        t = f.params[-1].type
        return t.sub.name(None).rstrip("*") if t.vt == 26 and t.sub.vt != 29 else None
    return None


def guess_argc(lf, st):
    """Arguments of an unresolved call: dwords pushed below the frame."""
    n = 0
    while n < 12 and (st.esp + 4 * n) in st.stack and \
            (lf.base_esp is None or st.esp + 4 * n < lf.base_esp):
        n += 1
    return n


# ------------------------------------------------------------------ user code
def note_control_args(lf, proc, args):
    """What a module procedure's As Object parameters are given, for the next
    lift's lateid_member: a late call compiled by DISPID, in a procedure whose
    parameter was typed in the source, names no member (a target Proc_9258A0
    is passed ListViews, and reads a2.ListItems as DISPID 7)."""
    seen = lf.m.__dict__.setdefault("control_args", {})
    for i, a in enumerate(args):
        x = lf.plain(a)
        x = x.e if isinstance(x, AddrOf) else x
        if not isinstance(x, (Var, Prop)):
            ptr = lf.slot_ptr(a)
            x = lf.slot_var(ptr.off) if isinstance(ptr, SP) else x
        it = getattr(x, "iface", None)
        if it is None and hasattr(x, "key"):
            it = lf.objtype.get(x.key())
        if it is None and getattr(x, "control", None) is not None:
            it = getattr(x.control, "iface", None)
        if it is not None:
            seen.setdefault((proc.va, 4 * (i + 1)), set()).add(it.name)
            lf.m.__dict__.setdefault("control_ifaces", {})[it.name] = it


def control_arg_iface(lf, ob):
    """The one control type all callers give the parameter ob is read through."""
    x = ob.e if isinstance(ob, Deref) and ob.off == 0 else ob
    if not (isinstance(x, Var) and x.space == "arg" and isinstance(x.off, int)):
        return None
    names = lf.m.__dict__.get("control_args", {}).get((lf.p.va, x.off))
    if not names or len(names) != 1:
        return None
    return lf.m.__dict__["control_ifaces"][next(iter(names))]


def first_element(lf, a):
    """What a fixed array's slot holds, its data, pushed for a procedure: the first element by reference
    (HashTableSave ..., StaticEvalArr(0), a ByRef Long; ChessBrainVB).  The whole array goes by its
    descriptor's address, which only by_ref makes the same Var."""
    if not isinstance(a, Var) or a.space != "global":
        return a
    info = lf.arrays.get(a.key())
    if info is None or info.get("dyn") is not False:
        return a
    dims = info.get("dims") or [(info.get("lb") if isinstance(info.get("lb"), int) else 0, None)]
    el = Index(a, [Const(lb or 0) for lb, _ in dims])
    el.vbtype = None
    return AddrOf(el)


def do_proc(lf, st, proc, stmts, com=False):
    """Call of a VB procedure.  Methods of forms/classes are COM calls:
    Me first, HRESULT in eax, a Function's value through a last out-pointer."""
    nargs = proc.ret_pop // 4
    args = [first_element(lf, a) for a in st.args(nargs)]
    st.drop(nargs)
    name = proc.truth[1] if getattr(proc, "truth", None) else proc.name
    member = getattr(proc, "member", None)
    if member is not None and member.name:
        name = member.name
    is_obj = proc.owner is not None and proc.owner.kind != "Module"
    if not is_obj:
        note_control_args(lf, proc, args)
    recv = None
    if is_obj and args:
        r = lf.plain(args[0])
        if not isinstance(r, Me):
            recv = r                        # called on another object (a Friend method)
            if not (isinstance(r, Var) and r.space == "reg"):
                proc.public = True          # (Me kept in a register over a join is no other object)
        args = args[1:]
    if member is not None and args:
        # module functions push their ByVal Variants the same 16 bytes
        # (ConvertToJson(o("list"), vbTab), gh_json)
        args = group_member_args(lf, member, args)
    if is_obj and member is not None and member.kind in ("let", "set") and args:
        # the object's own Property Let/Set: an assignment to the property
        tgt = Prop(Me(), name, [lf.plain(a) for a in args[:-1]])
        stmts.append(Assign(tgt, lf.plain(args[-1]), member.kind))
        return Site(name, None)
    if is_obj and member is not None and member.kind == "get" and args:
        v = Prop(Me(), name, [lf.plain(a) for a in args[:-1]])
        lf.bind_temp(args[-1], v)
        return Site(name, Const(0))
    from .lift import variant_return
    if not is_obj and (variant_return(proc) is not None or getattr(proc, "udt_ret", False)) \
            and args:
        from .lift import record_return
        udt = getattr(proc, "udt_ret", False) or record_return(lf.m, proc) is not None
        out, rest = args[0], args[1:]
        v = Call(name, [lf.by_ref(a) for a in rest], raw=proc.va,
                 vbtype=None if udt else "Variant")
        if udt:
            # the result buffer is a whole UDT local, not its first field
            ptr = lf.slot_ptr(out)
            tgt = lf.slot_var(ptr.off) if isinstance(ptr, SP) else \
                (ptr.e if isinstance(ptr, AddrOf) else None)
            if isinstance(tgt, Deref) and tgt.off == 0 and isinstance(tgt.e, Var):
                tgt = tgt.e
            if isinstance(tgt, Var):
                a = Assign(tgt, v)
                a.va = lf.cur_va
                stmts.append(a)
                return Site(name, out)
        lf.bind_temp(out, v)
        return Site(name, out)
    if is_obj and getattr(proc, "kind", None) != "event" and args and \
            lf.m.proc_returns.get(proc.va) and lf.is_out_slot(args[-1]):
        out = args[-1]
        v = Call(name, member_defaults(member, [lf.by_ref(a) for a in args[:-1]]),
                 raw=proc.va, obj=recv, method=recv is not None)
        from .lift import method_udt_return
        if method_udt_return(proc) is not None:
            # a UDT result: the buffer is a whole UDT local (gh_zip pvVfsOpen)
            ptr = lf.slot_ptr(out)
            tgt = lf.slot_var(ptr.off) if isinstance(ptr, SP) else \
                (ptr.e if isinstance(ptr, AddrOf) else None)
            if isinstance(tgt, Deref) and tgt.off == 0 and isinstance(tgt.e, Var):
                tgt = tgt.e
            if isinstance(tgt, Var):
                a = Assign(tgt, v)
                a.va = lf.cur_va
                stmts.append(a)
                return Site(name, Const(0))
        lf.bind_temp(out, v)
        if getattr(proc, "result8", False):
            ptr = lf.slot_ptr(out)
            if isinstance(ptr, SP):
                lf.hi8.add(ptr.off)         # an 8-byte result: [out+4] is its upper half
        return Site(name, Const(0))         # eax is the HRESULT; the value is in *out
    v = Call(name, member_defaults(member, [lf.by_ref(a) for a in args]) if is_obj else
             [lf.by_ref(a) for a in args], raw=proc.va, obj=recv, method=recv is not None)
    if member is not None and member.ret_type in ("Object", "Class"):
        v.vbtype = "Object"        # an object result: storing it needs Set
    if is_obj:
        stmts.append(ExprStmt(v))
        return Site(name, None)
    if getattr(proc, "float_ret", False):
        v.vbtype = "Double"
        return Site(name, v, fpu=True)
    if not is_obj:
        from .lift import small_record_return, pair_callers
        proc.model_procs = lf.m.procs       # (decides by what fills the callee's result)
        proc.pair_use = pair_callers(lf.m)
        small = small_record_return(proc)
        if small is not None:
            v.small_rec = small[1]          # (its upper half is in edx, when 8 bytes)
            site = Site(name, v)
            if small[1] == 8:
                site.edx = Hi(v)
            return site
        if proc.pair_use.get(proc.va):
            v.vbtype = "Currency"           # edx read after it, and not a record's
            site = Site(name, v)
            site.edx = Hi(v)
            return site
    if lf.m.__dict__.get("returns_known") and not lf.m.proc_returns.get(proc.va):
        # a Sub (decompile.first_pass knows by now): eax after it is no value
        # of the program's, also where a join makes it look live (gh_json
        # t_eax2006 = json_BufferAppend(...) once lift.no_result made the
        # Sub a Sub) - nor the value it held before the call
        stmts.append(ExprStmt(v))
        st.regs.pop("eax", None)
        return Site(name, None)
    return Site(name, v)


def note_array_args(lf, pars, args):
    """A variable passed to a method's array parameter is an array of that type: often the only
    place it is declared (cReg.EnumSections(sSec() As String, n): Dim v88 of a record
    with the descriptor's fields, Dim v1C() As Long)."""
    for prm, a in zip(pars, args):
        if not getattr(prm, "array", False):
            continue
        t = prm.type.sub if prm.type.vt == 26 else prm.type
        v = lf.frame_ptr(a)
        # (a variable's own address; simplify.drop_temp_arrays then keeps out the compiler's
        # slot holding a pointer to a fixed array, also used for numbers: t04w vFC)
        v = v.e if isinstance(v, AddrOf) else None
        if isinstance(v, Var) and t is not None:
            if v.key() in lf.arrays:
                lf.arrays[v.key()].setdefault("vt", t.vt)
            else:
                lf.arrays[v.key()] = {"size": VT_SIZE.get(t.vt, 4), "dyn": True, "lb": None,
                                      "n": None, "var": v, "vt": t.vt, "from_method": True}


def group_member_args(lf, member, args):
    """A project member's arguments as pushed: a ByVal Variant is 16 bytes,
    a ByVal Double / Currency / Date 8; merge those dwords into one value
    (the result pointer, if any, stays last)."""
    out, i = [], 0
    for prm in member.params:
        if i >= len(args):
            return args
        if not prm.byref and not prm.array and prm.type == "Variant":
            chunk = args[i:i + 4]
            out.append(byval_variant(lf, [lf.plain(x) for x in chunk]))
            i += 4
        elif not prm.byref and not prm.array and prm.type in ("Double", "Currency", "Date"):
            from .runtime import dword_pair
            out.append(dword_pair(lf.plain(args[i]), lf.plain(args[i + 1]) if i + 1 < len(args)
                                  else None, prm.type))
            i += 2
        else:
            out.append(args[i])
            i += 1
    return out + args[i:]


def member_defaults(member, args):
    """Arguments of a call to a project member that equal their Optional
    parameter's default: the compiler filled those in for omitted ones.
    Trailing ones go, earlier ones become empty ("F(, 2)")."""
    if member is None or len(member.params) != len(args):
        return args
    args = list(args)
    for i, p in enumerate(member.params):
        d = p.default
        a = args[i]
        if not p.optional or d is None or p.paramarray:
            continue
        if isinstance(a, Str) and a.text == d[1] or \
                isinstance(a, Const) and not isinstance(d[1], str) and a.value == d[1]:
            args[i] = ir.Missing()
    while args and isinstance(args[-1], ir.Missing):
        args.pop()
    return args


def late_name(lf, mname):
    """A late call's member name.  It can read as a String literal, a length in front of it
    by chance (a target Form15 v.Files printed v.__late)."""
    if isinstance(mname, ir.Str):
        return mname.text.split("\0")[0]
    return wide_cstr(lf.im, mname.value & 0xffffffff) if isinstance(mname, Const) else None


def wide_cstr(im, va):
    """A NUL-terminated UTF-16 name (late-bound member names)."""
    if not im.valid(va):
        return None
    out = []
    for i in range(64):
        if not im.valid(va + 2 * i + 1):
            return None
        c = im.u16(va + 2 * i)
        if c == 0:
            break
        out.append(chr(c))
    t = "".join(out)
    return t if t and (t[0].isalpha() or t[0] == "_") and \
        all(c.isalnum() or c == "_" for c in t) else None


def ansi_literal(im, va):
    """A NUL-terminated ANSI string constant in the code section."""
    if not im.valid(va) or im.section_of(va) != ".text":
        return None
    raw = im.bytes(va, min(256, im.base + len(im.mem) - va))
    end = raw.find(b"\0")
    if end < 0:
        return None
    t = raw[:end].decode("latin-1")
    if all(c.isprintable() or c in "\r\n\t" for c in t):
        return t
    return None


def do_declare(lf, st, decl, stmts):
    lib, fname = decl
    n = lf.declare_argc.get(fname)
    if n is None:
        from . import apidb, apiarity
        api = apidb.lookup(fname)
        if api is not None:
            n = api.stack_bytes() // 4
        else:
            n = apiarity.arity(lib, fname)
        if n is None:
            n = guess_argc(lf, st)
        lf.declare_argc[fname] = n
    args = st.args(n)
    st.drop(n)
    from . import apidb
    api = apidb.lookup(fname)
    raw_args = list(args)
    if api is not None:
        fixed = []
        for (byval, pname, ptype), a in zip(api.params, args):
            if ptype in ("String", "Any") and isinstance(a, Const):
                t = ansi_literal(lf.im, a.value & 0xffffffff)
                if t is not None:
                    a = Str(t)          # VB pre-converts literals passed to APIs
            fixed.append(a)
        args = fixed + args[len(fixed):]
        for (byval, pname, ptype), a, ra in zip(api.params, args, raw_args):
            if not byval and apidb.layout(ptype) is not None and (
                    isinstance(ra, (SP, AddrOf)) or isinstance(ra, Var) and ra.space == "arg"):
                # (a variable's address, or a ByRef parameter handed on - gh_zip
                # pvFromFileTime; a local's value pushed is a pointer it holds:
                # VBDoom waveOutWrite hwo, VarPtr(whdr(b)) through a temporary,
                # which then became a WAVEHDR with its neighbours)
                t = lf.plain(a)
                if isinstance(t, Var):
                    lf.udt.setdefault(t.key(), ptype)
    vbname = api.name if api is not None else fname
    out = []
    ansi = set()
    if api is None:
        # an API no list knows gets its Strings as ANSI copies: __vbaStrToAnsi's result, or a
        # literal VB converted beforehand.  ByVal As Long would make a number of them (RegQueryValueExA
        # h, "Shown": error 13, which On Error turned into the default value)
        for i, a in enumerate(args):
            if getattr(a, "marshalled", False):
                ansi.add(i)
            elif isinstance(a, Const) and isinstance(a.value, int) and \
                    ansi_literal(lf.im, a.value & 0xffffffff) is not None:
                args[i] = Str(ansi_literal(lf.im, a.value & 0xffffffff))
                ansi.add(i)
    for i, a in enumerate(args):
        x = lf.plain(a)
        if api is not None and i < len(raw_args) and getattr(raw_args[i], "rec_ansi", False):
            # a record VB makes an ANSI copy of: it goes whole, whatever WIN32API.TXT says of the
            # parameter (lstrlen(u) of a record); the writer's declare_fixes makes it As Any
            x = AddrOf(x.e if isinstance(x, AddrOf) else x)
            x.rec_arg = True
            out.append(x)
            continue
        if api is not None and i < len(api.params):
            byval, pname, ptype = api.params[i]
            addr = isinstance(raw_args[i], (SP, AddrOf)) or \
                (isinstance(raw_args[i], Const) and lf.im.section_of(raw_args[i].value & 0xffffffff)
                 == ".data")
            if isinstance(raw_args[i], Const) and addr and isinstance(raw_args[i].value, int) and \
                    (not byval or ptype == "Long"):
                # (a ByVal Long given one: the program declared it ByRef, as VBDoom
                # did lplpVoid; the writer declares it so - Call.byref_args)
                if byval:
                    lf.__dict__.setdefault("api_byref", set()).add((fname, i))
                # a module variable by its address: the variable, ByRef (VBDoom
                # CreateDIBSection memDC, bmi, 0, dibBits, 0, 0 passed the
                # original's addresses as numbers)
                x = AddrOf(lf.var_at(raw_args[i].value & 0xffffffff))
            el = lf.dyn_element(x) if not addr and not isinstance(x, (Str, Const)) else None
            if el is None and not addr and isinstance(x, Var) and isinstance(a, Var) and \
                    (lf.arrays.get(x.key()) or {}).get("dyn") is False:
                # (lstrcpynA b(0), "hi", 3 with b(7) As Byte, in a Declare of the program's own: the
                # slot's value is the array's data.  As the array itself for the list's ByVal As String,
                # the API filled a copy of it)
                info = lf.arrays[x.key()]
                dims = info.get("dims") or [(info.get("lb") if isinstance(info.get("lb"), int) else 0, None)]
                el = Index(x, [Const(lb or 0) for lb, _ in dims])
                el.vbtype = None
            if el is None and not byval and not addr and isinstance(x, BinOp) and any(
                    isinstance(t, Var) and not (lf.arrays.get(t.key()) or {"dyn": True})
                    .get("dyn", True) for t in x.walk()):
                ea = lf.element_address(x)  # pvData + k of a fixed array: its element
                el = ea.e if ea is not None else None   # (CopyMemory currentPalette(0), ...)
            if el is not None:
                x = el                      # baRetVal(0): an element passed ByRef
                x.byref_elem = True         # (the writer declares the parameter As Any)
            elif not byval and ptype == "Any" and not addr and isinstance(x, BinOp) and \
                    x.op == "+" and isinstance(x.a, Var) and x.a.space == "arg" and \
                    isinstance(x.b, Const) and isinstance(x.b.value, int):
                x = AddrOf(Deref(x.a, 4, x.b.value))   # &uHdr.f30: the field itself
            elif not byval and ptype == "Any" and not addr and not isinstance(x, Str):
                x = UnOp("ByVal", x)        # a value for "As Any": ByVal at the call
            if byval and ptype == "Any" and isinstance(x, Const) and isinstance(x.value, int):
                x = Const(x.value, "Long&")  # an Integer literal is a type mismatch here
            if byval and ptype == "String" and isinstance(x, Const) and isinstance(x.value, int):
                # a number for a WIN32API.TXT "ByVal ... As String" buffer: the writer
                # declares it As Any / As Long; ByVal 0& is right for both (a null
                # pointer; without ByVal "As Any" would pass a temporary's address)
                x = UnOp("ByVal", Const(x.value, "Long&"))
        elif api is None and i < len(raw_args) and isinstance(raw_args[i], (SP, AddrOf)):
            # an API without a WIN32API.TXT entry is declared "ByVal As Long":
            # a frame variable's address goes as VarPtr(v) (gh_zip
            # SystemTimeToVariantTime's vtime, SetFilePointerEx's result)
            t = x.e if isinstance(x, AddrOf) else x
            if getattr(raw_args[i], "rec_ansi", False):
                x = AddrOf(t)               # (declared As Any: the writer's declare_fixes)
            elif isinstance(t, (Var, Deref)) and not isinstance(t, SP):
                x = Call("VarPtr", [t], "Long")
            elif isinstance(t, Index):
                # an element's address (__vbaDerefAry1): the element by reference,
                # which the writer declares As Any (VBDoom FillMemory
                # vp_top(check * VPSTRIDE), VPSTRIDE, &HFF)
                x = AddrOf(t)
        out.append(x)
    if api is None:
        # v, Hi(v): an 8-byte value passed ByVal (SetFilePointerEx's
        # liDistanceToMove As Currency): one argument, declared that wide
        from .machine import Hi
        wide = lf.m.__dict__.setdefault("declare_wide", {}).setdefault(fname, {})
        merged, i = [], 0
        while i < len(out):
            a = out[i]
            h = out[i + 1] if i + 1 < len(out) else None
            if isinstance(h, Hi) and (h.of is a or h.of.key() == a.key()):
                t = getattr(a, "vbtype", None)
                wide[i] = t if t in ("Currency", "Double", "Date") else wide.get(i, "Double")
                merged.append(a)
                i += 2
                continue
            if i in wide and h is not None:
                merged.append(a)            # another call site saw the pair
                i += 2
                continue
            merged.append(a)
            i += 1
        out = merged
    v = Call(vbname, out, raw="%s!%s" % (lib, fname), vbtype="Long")
    if ansi:
        v.ansi_args = ansi                  # (writer.unknown_api_strings)
    if api is not None:
        # a frame slot's address for a ByRef parameter: its value may be inlined later, and it stays by
        # reference (writer.declare_fixes: GetUserName s, 199 went ByVal, 199 a pointer for the API)
        by_addr = {i for i, (byval, _, _) in enumerate(api.params)
                   if not byval and i < len(raw_args) and isinstance(raw_args[i], SP)}
        if by_addr:
            v.addr_args = by_addr
    return Site(fname, v)
