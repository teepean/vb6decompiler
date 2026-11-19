"""Remove the compiler's scaffolding: evaluated-call statements whose value
is consumed later, zero initialisation of locals, temporary frees, and
single-use temporaries."""
import bisect
import heapq

from . import ir
from .ir import Const, Str, Var, Deref, BinOp, UnOp, Call, Prop, Assign, Free, ExprStmt
from .lift import CallStmt
from .machine import SP, add


def run(lf):
    body = flatten(lf)
    body = reused_arg_reads(body, lf)
    body = fold_single_bits(body, lf, tags_only=True)   # (before they are inlined as numbers)
    body = drop_temp_arrays(body, lf)
    # (the lift's last check of each subscript in the procedure; from here on that may be another
    # array's: DarwinBots .Ties(k) got the 15 of a later loop's check, too long to fit)
    lf.index_bounds = {}
    # a fixed array's descriptor by reference: before the temporary holding it
    # is renamed and inlined into a bare address (VBDoom thintriangle_guy)
    body = fixed_arrays_by_ref(body, lf)
    body = fold_static_derefs(body, lf)
    body = alias_addr_temps(body, lf)
    mark_error_regions(body, lf)
    body = fold_variant_temps(body, lf)
    body = resolve_lock_regs(body, lf)
    body = alias_safearrays(body, lf)
    body = drop_frees(body)
    pin_statement_variables(body, lf)
    body = drop_result_out(body, lf)
    body = merge_result_halves(body, lf)
    body = merge_hi_stores(body)
    body = drop_consumed_calls(body)
    body = fold_field_pointer_regs(body, lf)
    for rnd in range(2):
        nodes0 = len(body)
        body = unmerge_calls(body, lf)          # before folding: each path gets its call
        body = fold_variant_args(body, lf)
        body = fold_variant_slot_reads(body, lf)   # before the payload half looks dead
        if rnd == 0:
            body = fold_variant_reaching(body, lf)  # (and the pairs built before a join)
            body = drop_zero_init(body, lf)
            body = fold_double_slots(body, lf)
            body = merge_const_doubles(body, lf)    # (before the upper half looks dead)
            body = alias_byval_copies(body, lf)
            body = rename_object_temps(body, lf)
            body = rename_statement_temps(body, lf)
        body = rename_block_temps(body, lf)
        body = missing_slots(body)              # (a reused slot's Missing versions)
        body = dead_by_liveness(body, lf)
        body = drop_dead_stores(body, lf)
        body = fold_variant_slot_reads(body, lf)
        body = inline_all(body, lf)
        # (again: optimised code keeps a fixed array's pvData in a register,
        # which only inlining turns into the array - ChessBrainVB ResetMaterial
        # *(esi + *(edi + ...) * 4))
        body = scaled_index_slots(body, lf)
        body = fold_static_derefs(body, lf)
        body = map_statics(body, lf)
        body = array_bases_in_registers(body, lf)
        body = frame_offsets(body, lf)
        body = fold_dyn_elements(body, lf)
        body = refold_dyn_index(body, lf)
        body = alias_reg_params(body, lf)
        body = alias_arg_copies(body, lf)
        body = alias_invariant_addrs(body, lf)
        body = coalesce_copies(body, lf)
        body = unmerge_calls(body, lf)
        body = thread_empty(body, lf)
        body = merge_conditions(body, lf)       # And/Or before tails are duplicated
        body = duplicate_small_joins(body, lf)
        if len(body) == nodes0 and rnd:
            break
    body = hresult_stores(body)
    body = whole_small_results(body, lf)
    body = fold_elem_addresses(body, lf)
    body = missing_literals(body, lf)
    body = globals_by_ref(body, lf)             # &module variable pushed as an immediate
    body = apply_locks(body, lf)                # __vbaAryLock temporaries: the arrays they lock
    body = frame_offsets(body, lf)
    body = member_element_parts(body, lf)       # a member array's parts seen as arrays of their own
    body = element_address_slots(body, lf)      # an element's address kept in the frame
    body = fold_dyn_elements(body, lf)          # array parameters seen through a register copy
    body = fold_magic_divs(body)                # x \ 41 as a multiply by a reciprocal
    body = fold_single_bits(body, lf)           # Single literals stored as integers
    body = split_register_webs(body, lf)        # one register, several variables
    body = static_block_regs(body, lf)
    # (again on the webs: eax holding a form's predeclared instance across its
    # creation check, "If Form3 Is Nothing Then New", among other values)
    body = alias_arg_copies(body, lf)
    body = alias_array_param_copies(body, lf)
    body = alias_param_reg_copies(body, lf)
    body = array_bases_in_registers(body, lf)   # (a register known as a parameter only by now)
    body = fold_elem_pointers(body, lf)         # With a(i): fields through a pointer
    body = array_bases_in_registers(body, lf)   # (and one known as a(i).arr only by now)
    body = unbias_index_regs(body, lf)
    body = fold_fixed_array_copies(body, lf)    # (the array itself for the folds below)
    body = fold_dyn_elements(body, lf)
    body = fold_offset_slots(body, lf)
    body = refold_dyn_index(body, lf)           # (subscripts the register webs just made)
    body = fold_static_derefs(body, lf)
    body = fold_pointer_ivs(body, lf)           # a pointer walking a fixed array
    body = fold_offset_ivs(body, lf)            # a byte offset stepping through one
    body = unbias_late(body, lf)
    body = fold_fixed_array_copies(body, lf)
    body = fold_record_sums(body, lf)           # pvData + constant: element and field
    body = fold_fixed_sums(body, lf)            # pvData + i * k + constant
    body = fold_elem_pointers(body, lf)         # (registers those just showed to hold an element)
    body = fold_byte_ops(body, lf)
    body = fold_dyn_leftovers(body, lf)
    body = fold_me_copies(body)
    note_byref_params(body, lf)
    body = fold_strings(body, lf)
    body = merge_number_elements(body, lf)
    # (before the pairs: mov ecx, [x+14h]; mov eax, [x+18h] read as __cy(ecx, eax) later on would
    # leave this test comparing with Hi(ecx); gh_zip frCopyCompressed)
    body = fold_cy_branches(body, lf)
    body = fold_flag_and(body, lf)
    body = fold_double_eq(body, lf)
    body = fold_register_pairs(body, lf)
    body = fold_pairs(body)
    body = fold_cy_compares(body)
    body = fold_cy_branches(body, lf)
    body = fold_cy_pairs(body, lf)
    for rnd in range(2):
        for _ in range(4):                      # the halves' copies the compares read
            n0 = sum(len(ss) for _, ss, _ in body)
            body = drop_dead_stores(body, lf)
            if sum(len(ss) for _, ss, _ in body) == n0:
                break
        if not rnd:
            # (HRESULT tests whose failing arm only went now keep the slot they test alive, and
            # with it vD4 = datirob.infoupdate(...), a Sub: "Expected Function or variable";
            # DarwinBots Form1.main)
            body = thread_empty(body, lf)
            body = dead_by_liveness(body, lf)
    body = register_copies(body, lf)
    body = sink_paramarray_calls(body, lf)
    n0 = sum(len(ss) for _, ss, _ in body)
    body = fold_paramarrays(body)
    if sum(len(ss) for _, ss, _ in body) != n0:
        # (the ReDim and Erase of its array no longer in the way: Set x = CallByName(o, "Obj", VbGet)
        # stayed t = CallByName(...): Set x = t, a Let of an object: 438)
        body = inline_temps(body, lf)
    body = set_late_receivers(body, lf)
    body = fold_elem_addresses(body, lf)        # (again: the temporaries are inlined now)
    body = constant_inline_elements(body, lf)
    body = alias_usercontrol(body, lf)
    retype_object_slots(body, lf)
    return fix_bools(body)


WIDE_ELEMENTS = {5: "Double", 6: "Currency", 7: "Date"}


def merge_number_elements(body, lf):
    """a(i) = 1.5 of a Double, Currency or Date array is two integer moves: mov [eax], 0; mov [eax + 4],
    3FF80000h, which is two fields of a record until the array's type says otherwise; and that is known
    (__vbaAryConstruct2 and __vbaRedim are told it, a module's table has it, callers tell a parameter).
    Reads of the whole element become the element too."""
    from .runtime import dword_pair

    def wide(x):
        return isinstance(x, ir.Index) and isinstance(x.arr, Var) and \
            (lf.arrays.get(x.arr.key()) or {}).get("vt") in WIDE_ELEMENTS

    def half(x):
        """(the element, its offset) of a store's target."""
        if isinstance(x, Deref) and wide(x.e) and x.off in (0, 4) and x.size == 4:
            return x.e, x.off
        if wide(x):
            return x, 0
        return None

    def whole(x):
        # (any size: the upper half of a pair pushed for Print is gone by now, and nothing in VB reads
        # the lower half of a Double alone)
        if isinstance(x, Deref) and wide(x.e) and x.off == 0:
            return x.e
        return None
    out = []
    for a, ss, br in body:
        keep = []
        i = 0
        while i < len(ss):
            s0 = ss[i]
            if isinstance(s0, Assign) and i + 1 < len(ss) and isinstance(ss[i + 1], Assign):
                p, q = half(s0.dst), half(ss[i + 1].dst)
                if p is not None and q is not None and {p[1], q[1]} == {0, 4} and repr(p[0]) == repr(q[0]):
                    lo, hi = (s0.src, ss[i + 1].src) if p[1] == 0 else (ss[i + 1].src, s0.src)
                    v = dword_pair(lo, hi, WIDE_ELEMENTS[lf.arrays[p[0].arr.key()]["vt"]])
                    if not (isinstance(v, Call) and v.name.startswith("__")):
                        keep.append(keep_flags(Assign(p[0], v), s0))
                        i += 2
                        continue
            keep.append(substitute_expr(s0, whole))
            i += 1
        if br is not None:
            br = type(br)(br.cond.replace(whole))
        out.append((a, keep, br))
    return out


def alias_usercontrol(body, lf):
    """A temporary that only ever holds the UserControl object (Me+10h) stands for it: Set v50 =
    UserControl is "Invalid use of base class name", the first compile error of a target's
    UserControl1 on Windows."""
    if lf.obj is None or getattr(lf.obj, "kind", None) != "UserControl":
        return body
    uc = ("member", 0x10)
    defs = {}
    for _, ss, _ in body:
        for s in ss:
            if isinstance(s, Assign) and isinstance(s.dst, Var) and s.dst.space in ("local", "reg"):
                zero = isinstance(s.src, Const) and s.src.value == 0
                ok = isinstance(s.src, Var) and s.src.key() == uc or zero
                defs[s.dst.key()] = defs.get(s.dst.key(), True) and ok
                if not zero:
                    defs.setdefault(("any", s.dst.key()), True)
    mapping = {k: Var("member", 0x10) for k, ok in defs.items()
               if ok and k[0] != "any" and ("any", k) in defs and k not in lf.arrays and
               not (isinstance(k[1], int) and k[1] == lf.result_slot)}
    if not mapping:
        return body
    out = []
    for a, ss, br in body:
        keep = []
        for s in ss:
            if isinstance(s, Assign) and isinstance(s.dst, Var) and s.dst.key() in mapping:
                continue
            keep.append(substitute_many(s, mapping))
        if br is not None:
            br = type(br)(ir.subst(br.cond, mapping))
        out.append((a, keep, br))
    return out


UNKNOWN_RESULT = object()


def retype_object_slots(body, lf):
    """A frame slot of the compiler's holds any object for a while (unoptimised code pools them), and
    keeps the type it first had in objtype.  What is still set in it now says what it is: App went
    with the temporary it was for, and Set v38 = fso.GetFile(p), If v38.Size > 0 had Dim v38 As App,
    "Method or data member not found" (DarwinBots MDIForm_Load)."""
    objs = getattr(lf, "objtype", None)
    if not objs:
        return
    seen = {}
    for _, ss, _ in body:
        for s in ss:
            if isinstance(s, Assign) and isinstance(s.dst, Var) and s.dst.space == "local" and \
                    isinstance(s.dst.off, int) and s.dst.key() in objs and \
                    not lf.is_named_local(s.dst):
                it = getattr(s.src, "iface", None)
                if isinstance(s.src, Var):
                    continue                    # (objtype itself: the last one set, wherever)
                if it is None and isinstance(s.src, Call) and s.src.obj is not None and s.kind == "set":
                    it = UNKNOWN_RESULT         # a method's result, the type table says nothing
                seen.setdefault(s.dst.key(), []).append(it)
    for key, its in seen.items():
        known = [i for i in its if i is not None]
        cur = objs[key]
        if not known or any(i is cur or getattr(i, "name", 0) == getattr(cur, "name", 1) for i in known):
            continue
        if None not in its and all(getattr(i, "name", None) == getattr(known[0], "name", 0)
                                   for i in known) and known[0] is not UNKNOWN_RESULT:
            objs[key] = known[0]
        else:
            objs.pop(key)               # (several kinds: As Object)


def constant_inline_elements(body, lf):
    """An element of an array in a record with a constant subscript, known only now, is a field
    at its offset (t84recret .jalki(2): no array the code shows, and the field wasn't declared).
    The same for an address that turns out constant: Lisaa K.lajit(2).iho(3) printed the sum."""
    def konst(ix):
        return len(ix.idx) == 1 and isinstance(ix.idx[0], Const) and \
            isinstance(ix.idx[0].value, int) and ix.arr.off + ix.idx[0].value * ix.arr.inline >= 0

    def f(x):
        if isinstance(x, BinOp) and x.op == "+":
            lin = lf.linear(x)
            if set(lin) == {None} and lf.data_address(Const(lin[None][1])):
                va = lin[None][1] & 0xffffffff
                ms = lf.module_struct_ref(Const(va))
                return ms if ms is not None else ir.AddrOf(lf.global_var(va))
        if isinstance(x, Deref) and isinstance(x.e, ir.Index) and \
                getattr(x.e.arr, "inline", None) and konst(x.e):
            n = Deref(x.e.arr.e, x.size, x.e.arr.off + x.e.idx[0].value * x.e.arr.inline + x.off)
            if getattr(x, "fp4", False):
                n.fp4 = True
            n.from_inline = (x.e.arr.off, x.e.arr.inline)   # (udt.collect: still an array's)
            return n
        if isinstance(x, ir.Index) and getattr(x.arr, "inline", None) and konst(x):
            # (as big as the element: K.lajit(4) = K.lajit(0), the whole record)
            n = Deref(x.arr.e, x.arr.inline, x.arr.off + x.idx[0].value * x.arr.inline)
            n.from_inline = (x.arr.off, x.arr.inline)
            return n
        return None
    out = []
    for a, ss, br in body:
        keep = [substitute_expr(s, f) if not isinstance(s, ir.Return) else s for s in ss]
        out.append((a, keep, type(br)(br.cond.replace(f)) if br is not None else br))
    return out


def set_late_receivers(body, lf):
    """A temporary that got a call's Variant result and is only the receiver
    of late-bound calls holds an object (else those calls fail with 424): its
    assignment is a Set.  A Let would call the object's default member (gh_dict
    o = d.Item("obj"): error 450; tests_wip/lateorder k = O(1).Cnt + N(2) with
    k unused)."""
    uses, as_recv = {}, {}

    def count(e):
        for x in e.walk():
            if isinstance(x, Var):
                uses[x.key()] = uses.get(x.key(), 0) + 1
            if is_late(x) and isinstance(getattr(x, "obj", None), Var):
                as_recv[x.obj.key()] = as_recv.get(x.obj.key(), 0) + 1
            if is_late(x) and isinstance(x, ir.Index) and isinstance(x.arr, Var):
                as_recv[x.arr.key()] = as_recv.get(x.arr.key(), 0) + 1
    for _, ss, br in body:
        for s in ss:
            for e in stmt_exprs(s):
                if isinstance(s, Assign) and e is s.dst:
                    if not isinstance(e, Var):
                        count(e)
                    continue
                count(e)
        if br is not None:
            count(br.cond)
    out = []
    for a, ss, br in body:
        keep = []
        for s in ss:
            if isinstance(s, Assign) and s.kind != "set" and isinstance(s.dst, Var) and \
                    isinstance(s.src, (Call, Prop)) and not getattr(s.src, "pure", False) and \
                    getattr(s.src, "vbtype", None) in (None, "Variant") and \
                    not lf.is_named_local(s.dst) and uses.get(s.dst.key(), 0) and \
                    uses.get(s.dst.key()) == as_recv.get(s.dst.key()):
                n = Assign(s.dst, s.src, "set")
                n.va = getattr(s, "va", None)
                s = n
            keep.append(s)
        out.append((a, keep, br))
    return out


def globals_by_ref(body, lf):
    """A module variable passed ByRef to a decompiled procedure: VB pushes
    its address as an immediate (VBDoom: cht_MakeSeq cheat_amap,
    HUlib_resetIText w_title).  Once the callee's parameter list is known
    (the second lift) such a constant is &that variable, so it gets declared
    and typed like any variable passed by reference.  An element of a fixed
    array passed ByRef arrives as pvData + offset: &that element."""
    from .types import param_args
    plists = getattr(lf.m, "plists", None) or {}
    struct_args = {x.e.off for _, ss, br in body for st in ss
                   for e in getattr(st, "exprs", lambda: ())() for x in e.walk()
                   if isinstance(x, Deref) and isinstance(x.e, Var) and x.e.space == "arg" and
                   x.off > 0}

    from . import apidb

    def project_methods():
        """The names of the project's own classes' and forms' members."""
        names = getattr(lf.m, "_project_methods", None)
        if names is None:
            names = {f.name.lower() for pi in getattr(lf.m, "proj_ifaces", {}).values()
                     for f in pi.funcs}
            lf.m._project_methods = names
        return names

    def fix(x):
        if isinstance(x, Call) and isinstance(x.raw, str) and "!" in x.raw and \
                apidb.lookup(x.raw.split("!", 1)[1]) is None:
            # a Declare WIN32API.TXT does not know: an element of a known fixed
            # array passed by address is a ByRef As Any argument (VBDoom sendto
            # insocket, pkt(0), ...; WSAStartup &H202, wsa(0)); the writer
            # declares that parameter As Any
            swap = {}
            for a in x.args:
                if isinstance(a, BinOp) and any(
                        isinstance(t, Var) and not (lf.arrays.get(t.key()) or {"dyn": True})
                        .get("dyn", True) for t in a.walk()):
                    el = lf.element_address(a)
                    if el is not None:
                        swap[id(a)] = el
            if not swap:
                return None
            args = [swap.get(id(a)) or a.replace(fix) for a in x.args]
            return x._keep(Call(x.name, args, x.vbtype, x.raw, x.method, x.obj, x.pure))
        if isinstance(x, Call) and x.method and x.obj is not None and x.name.lower() in project_methods():
            # the same through a project class or form's own method (Quick.WriteOne
            # 4, 0, global_F04578 printed &HF04578, and wrote that number to the file)
            if any(lf.data_address(a) for a in x.args):
                args = [ir.AddrOf(lf.var_at(a.value & 0xffffffff)) if lf.data_address(a)
                        else a.replace(fix) for a in x.args]
                return x._keep(Call(x.name, args, x.vbtype, x.raw, x.method, x.obj, x.pure))
            return None
        if not (isinstance(x, Call) and isinstance(x.raw, int) and x.raw in plists):
            return None
        plist = plists[x.raw]
        swap = {}
        for a, pi in param_args(plist, x.args):
            if isinstance(a, BinOp):
                # an element's address: pvData + i * k of a fixed array passed
                # ByRef, or a struct parameter's p + i * k (s.l(s.cl)) - VB
                # computes no such value but for a ByRef argument, whatever the
                # first renders said of the parameter.  (element_address makes
                # any base it is given an array: only for ByRef ones, or a base
                # known to be a fixed array: w_inputbuffer(i) to HUlib_initIText.)
                slots = lf.m.__dict__.get("_array_slots")
                if slots is None:
                    slots = lf.m._array_slots = lf.m.global_array_slots()
                known = any(isinstance(t, Var) and (
                    not (lf.arrays.get(t.key()) or {"dyn": True}).get("dyn", True) or
                    # a module array its module's variable table lists (w_ammo(i))
                    t.space == "global" and t.off in slots) for t in a.walk())
                el = lf.element_address(a) if plist[pi][3] or known else None
                if el is None:
                    el = lf.param_element(a, struct_args)
                if el is not None:
                    swap[id(a)] = el
                continue
            if not plist[pi][3]:
                # a module-level UDT's address is that UDT whatever the first
                # renders said of the parameter (VBDoom HUlib_initSText w_message)
                ms = lf.module_struct_ref(a) if lf.data_address(a) else None
                if ms is not None and isinstance(ms.e, Var):
                    swap[id(a)] = ms
                elif isinstance(a, Const) and isinstance(a.value, int) and \
                        lf.im.section_of(a.value & 0xffffffff) == ".data":
                    # no program has that literal: a parameter only handed on looks
                    # ByVal in the first renders (a target Proc_855240 &HF04740 went
                    # on to t_muunto as person number 15746880, and to its demo message)
                    swap[id(a)] = ir.AddrOf(lf.global_var(a.value & 0xffffffff))
                continue
            if isinstance(a, Const) and isinstance(a.value, int) and \
                    lf.im.section_of(a.value & 0xffffffff) == ".data":
                # (a module-level UDT as a whole, or a field of one)
                swap[id(a)] = lf.module_struct_ref(a) or \
                    ir.AddrOf(lf.global_var(a.value & 0xffffffff))

        if not swap:
            return None
        # (an element's subscripts may hold further calls: s(i, f(s(j).f0)), a target
        # Proc_995B50 left the inner s(j) an address sum)
        args = [swap[id(a)].replace(fix) if id(a) in swap else a.replace(fix) for a in x.args]
        obj = x.obj.replace(fix) if x.obj is not None else None
        return x._keep(Call(x.name, args, x.vbtype, x.raw, x.method, obj, x.pure))

    out = []
    for a, ss, br in body:
        ss = [substitute_expr(s, fix) for s in ss]
        if br is not None:
            br = type(br)(br.cond.replace(fix))
        out.append((a, ss, br))
    return out


BYREF_SIZES = {2: 2, 11: 2, 17: 1, 5: 8, 6: 8, 7: 8}


def byref_target(lf, p, tag=None):
    """What a VT_BYREF Variant's pointer p points at: the variable (&v), an
    element (pvData + i * 2: VBDoom IIf(playeringame(i), 1, 0)), a module
    variable (its address as a constant), else *p.  The tag says how wide a field is:
    TextWidth(m3C(i, j).f6) of an Integer read as 4 bytes made the record's field a Long,
    and in memory a Long moves all that follows (a target A4 TRec18: fields 2 bytes off,
    two of them with one name)."""
    if isinstance(p, ir.AddrOf):
        return p.e
    if isinstance(p, SP):
        # (its address kept in the Variant's slot: IIf(a > b, b, a) printed as *SP, a read of no
        # variable, and a's and b's stores died; DarwinBots DNAceil, both builds)
        q = lf.frame_ptr(p)
        if isinstance(q, ir.AddrOf):
            return q.e
    if isinstance(p, BinOp):
        ea = lf.element_address(p)
        if ea is not None:
            e = ea.e
            size = BYREF_SIZES.get((tag or 0) & 0xfff)
            if size and isinstance(e, Deref) and e.size != size:
                n = Deref(e.e, size, e.off)
                n.vbtype = getattr(e, "vbtype", None)
                return n
            return e
    if lf.data_address(p):
        ms = lf.module_struct_ref(p)
        return ms.e if ms is not None else lf.global_var(p.value & 0xffffffff)
    return Deref(p, 4, 0)


def fold_static_derefs(body, lf):
    """*(address) for a constant address of a module variable: that variable
    (a field of a module-level UDT); *(pvData + i * k): that element.  The
    runtime helpers' pointer arguments come out this way when VB passes a
    variable by reference through a VT_BYREF Variant (VBDoom G_BeginRecording:
    IIf(netgame, 1, 0), IIf(playeringame(i), 1, 0))."""
    def fix(x):
        if isinstance(x, Call) and x.name == "LSet" and \
                any(isinstance(a, BinOp) and a.op == "+" for a in x.args[:2]):
            # (its address in a register until now: LSet v38 = v64 + 16, CompareShapes)
            args, hit = list(x.args), False
            for i in (0, 1):
                ea = lf.element_address(args[i]) if isinstance(args[i], BinOp) and \
                    args[i].op == "+" else None
                if ea is not None:
                    args[i] = ea
                    hit = True
            if hit:
                return x._keep(Call(x.name, [a.replace(fix) for a in args], x.vbtype, x.raw,
                                    x.method, x.obj, x.pure))
        if isinstance(x, Call) and x.name in ("UBound", "LBound") and x.args and \
                isinstance(x.args[0], Deref) and x.args[0].off == 0 and \
                isinstance(x.args[0].e, Var) and x.args[0].e.space == "arg" and \
                (lf.arrays.get(x.args[0].e.key()) or {}).get("dyn"):
            # an array parameter's descriptor read through its pointer (a register's copy made
            # this one out of UBound(a1), once a1 was seen to be an array: Function scanfromn)
            return x._keep(Call(x.name, [x.args[0].e] + list(x.args[1:]), x.vbtype, x.raw,
                                x.method, x.obj, x.pure))
        if isinstance(x, ir.Index) and isinstance(x.arr, Deref) and x.arr.off == 0xc and \
                x.arr.size == 4 and isinstance(x.arr.e, Var) and \
                (lf.arrays.get(x.arr.e.key()) or {}).get("dyn"):
            # its data pointer taken for the array: a register that held the descriptor on one
            # way in, pvData on the other (DarwinBots DrawRobPer rob.fC(t).f390, Invalid qualifier).
            # The subscript came as it was, i - lLbound
            idx = [i.replace(fix) for i in x.idx]
            if len(idx) == 1:
                lbk = Deref(x.arr.e, 4, 0x14).key()
                lin = lf.linear(idx[0])
                if lbk in lin and lin[lbk][1] == -1:
                    t = None
                    for k, (e, c) in lin.items():
                        if k is None or k == lbk or not c:
                            continue
                        part = e if c == 1 else BinOp("*", e, Const(c))
                        t = part if t is None else add(t, part)
                    c0 = lin.get(None, [None, 0])[1]
                    if c0:
                        t = add(t, Const(c0)) if t is not None else Const(c0)
                    idx = [t if t is not None else Const(0)]
            return x._keep(ir.Index(x.arr.e, idx))
        if isinstance(x, Deref) and isinstance(x.e, ir.Index) and len(x.e.idx) == 1 and \
                getattr(x.e, "stride", None) and isinstance(x.e.arr, (BinOp, ir.AddrOf)):
            # slot(i).f, the slot's value (a With pointer) now in its place: the array inside
            # that element, and the field (rob(t).Ties(k + 1).pnt, DarwinBots ReadTRefVars)
            el = x.e.arr.e if isinstance(x.e.arr, ir.AddrOf) and \
                isinstance(x.e.arr.e, ir.Index) else \
                lf.dyn_element(x.e.arr) if isinstance(x.e.arr, BinOp) else None
            if el is not None:
                st = x.e.stride
                i = x.e.idx[0].replace(fix)
                scale, lf.scale = getattr(lf, "scale", 1), 1
                try:
                    r = lf.element_inline(el, BinOp("*", i, Const(st)) if st != 1 else i, x.off,
                                          x.size)
                finally:
                    lf.scale = scale
                if r is not None:
                    for attr in ("inline", "inline_n"):
                        if getattr(x, attr, None):
                            setattr(r, attr, getattr(x, attr))
                    return r
        if isinstance(x, Deref) and isinstance(x.e, BinOp) and x.e.op == "+" and \
                isinstance(x.e.a, ir.AddrOf) and isinstance(x.e.a.e, ir.Index) and \
                isinstance(x.e.b, Const) and isinstance(x.e.b.value, int):
            # *(&a(i) + k): the element's field at k (VBDoom Hex$(mobjs(m).x))
            return Deref(x.e.a.e.replace(fix), x.size, x.off + x.e.b.value)
        if isinstance(x, Deref) and isinstance(x.e, ir.AddrOf) and isinstance(x.e.e, ir.Index):
            return Deref(x.e.e.replace(fix), x.size, x.off)     # *(&a(i)): its first field
        if isinstance(x, Deref) and x.off in (0xc, 0x10, 0x14, 0x18, 0x1c) and x.size == 4 and \
                isinstance(x.e, Deref) and x.e.off == 0 and x.e.size == 4 and \
                isinstance(x.e.e, ir.AddrOf) and isinstance(x.e.e.e, Var) and \
                x.e.e.e.space == "member":
            # *(*(&m34) + 0Ch): a member's SAFEARRAY read through lea esi,[Me+34h], before the
            # alias of esi is known (t78 Rivit: element sums for the first block's mR(2, 1).a = 7).
            # Only that: *(&v) in general is a Variant's slot (gh_json printed vC0.f8)
            return Deref(x.e.e.e, 4, x.off)
        if not isinstance(x, Deref) or isinstance(x.e, (Var, ir.Index, Deref, ir.AddrOf)):
            return None
        p = x.e
        if lf.data_address(p):
            return lf.global_var((p.value + x.off) & 0xffffffff, x.size)
        off = x.off
        if isinstance(p, Const) and isinstance(p.value, int) and off and \
                lf.data_address(Const(off)):
            # [100 * 4 + address], the subscript known: IntStack.val(.pos) just after .pos = 100,
            # DarwinBots PushIntStack (400.f86454C = a1, Syntax error)
            return lf.global_var((p.value + off) & 0xffffffff, x.size)
        if isinstance(p, BinOp) and off and lf.data_address(Const(off)):
            # [(i - 1) * 364 + address]: the address as the displacement, an element of a module
            # record's array (SimOpts.Specie(i - 1).Name, DarwinBots)
            p, off = BinOp("+", p, Const(off)), 0
        if isinstance(p, BinOp) and off == 0:
            ea = lf.element_address(p, whole=False)
            if ea is not None:
                el = ea.e
                if isinstance(el, Deref) and el.off == 0 and isinstance(el.e, ir.Index):
                    info = lf.arrays.get(getattr(el.e.arr, "key", lambda: None)()) or {}
                    if (info.get("size") or 0) <= max(x.size, 8):
                        return el.e         # a scalar element read whole
                    # the first field of a record element, not the record (a
                    # String * 50 read into a temporary declared As TRec3:
                    # a target o1016 = global_F04688(t_esi).f0, Type mismatch)
                    return Deref(el.e, x.size, 0)
                return el
        return None
    out = []
    for a, ss, br in body:
        ss = [substitute_expr(st, fix) for st in ss]
        if br is not None:
            br = type(br)(br.cond.replace(fix))
        out.append((a, ss, br))
    return out


def fixed_arrays_by_ref(body, lf):
    """A fixed module-level array passed to an array parameter: VB stores
    the address of its static SAFEARRAY descriptor in a temporary and passes
    the temporary's address (VBDoom AM_drawPlayers: v40 = &H489DF8 then
    AM_drawLineCharacter v40, ... is player_arrow, whose pvData slot is the
    descriptor + 12, 489E04h).  The argument becomes the array, and the
    temporary's store goes once nothing reads it.  The array is known when
    the code indexes it elsewhere, or because the callee's parameter is an
    array (player_arrow is otherwise only seen through element addresses,
    found in the second lift: this one)."""
    from .types import param_args
    fixed = {k[1] for k, info in lf.arrays.items()
             if k[0] == "global" and isinstance(k[1], int) and not info.get("dyn")}
    plists = getattr(lf.m, "plists", None) or {}
    pa = getattr(lf.m, "array_params", None) or set()

    def array_params(x):
        """Argument positions of call x that are the callee's array parameters."""
        plist = plists.get(x.raw)
        if not plist:
            return set()
        pos = {id(a): i for i, a in enumerate(x.args)}
        return {pos[id(a)] for a, pi in param_args(plist, x.args)
                if (x.raw, plist[pi][0]) in pa}
    swapped = set()
    out = []
    for a, ss, br in body:
        cur = {}                    # local key -> the .data constant it holds here

        def fix(x):
            erase = isinstance(x, Call) and x.name == "Erase" and x.raw is None
            if not (isinstance(x, Call) and (isinstance(x.raw, int) or erase)):
                return None
            hit, args = False, []
            ap = None
            for i, arg in enumerate(x.args):
                if erase and isinstance(arg, Var) and arg.key() in cur:
                    arg = ir.AddrOf(arg)    # (the erase handler took the temporary itself)
                if isinstance(arg, ir.AddrOf) and isinstance(arg.e, Var) and arg.e.key() in cur:
                    d = cur[arg.e.key()]
                    if erase:
                        pass                # (Erase of a fixed array: VBDoom Get #f, , players)
                    elif d + 12 not in fixed:
                        ap = array_params(x) if ap is None else ap
                        if i not in ap:
                            args.append(arg.replace(fix))
                            continue
                        lf.arrays.setdefault(("global", d + 12), {
                            "size": None, "dyn": False, "lb": 0, "n": None,
                            "var": lf.global_var(d + 12)})
                        fixed.add(d + 12)
                    args.append(lf.global_var(d + 12))
                    swapped.add(arg.e.key())
                    hit = True
                else:
                    args.append(arg.replace(fix))
            if not hit:
                return None
            obj = x.obj.replace(fix) if x.obj is not None else None
            return x._keep(Call(x.name, args, x.vbtype, x.raw, x.method, obj, x.pure))
        new = []
        for st in ss:
            new.append(substitute_expr(st, fix) if cur else st)
            if isinstance(st, Assign) and isinstance(st.dst, Var):
                k = st.dst.key()
                v = st.src.value & 0xffffffff if isinstance(st.src, Const) and \
                    isinstance(st.src.value, int) else None
                if v is not None and st.dst.space == "local" and \
                        (v + 12 in fixed or lf.im.section_of(v) == ".data"):
                    cur[k] = v
                else:
                    cur.pop(k, None)
        out.append((a, new, br))
    if not swapped:
        return out
    reads = set()
    for a, ss, br in out:
        for st in ss:
            reads |= set(var_reads(st))
        if br is not None:
            reads |= {x.key() for x in br.cond.walk() if isinstance(x, Var)}
    gone = swapped - reads
    return [(a, [st for st in ss if not (isinstance(st, Assign) and isinstance(st.dst, Var) and
                                         st.dst.key() in gone)], br) for a, ss, br in out]


def sink_paramarray_calls(body, lf):
    """The compiler shares a call taking a ParamArray between paths that
    each build the array (Choose(i, 10, 12, 14) / Choose(i, 9, 11, 13) ...
    into one "v = Choose(i, t)").  Copy such a call from the start of the
    join into every predecessor that flows only into it, so that each path
    has ReDim t, the items and the call together for fold_paramarrays."""
    def pa_call(s):
        e = s.src if isinstance(s, Assign) else getattr(s, "e", None)
        return isinstance(e, Call) and getattr(e, "paramarray_last", False) and \
            e.args and isinstance(e.args[-1], (Var, ir.AddrOf))
    pos = {a: i for i, (a, _, _) in enumerate(body)}
    for i, (a, ss, br) in enumerate(body):
        if not ss or not pa_call(ss[0]):
            continue
        n = lf.nodes.get(a)
        preds = [p for p in (n.preds if n is not None else []) if p in pos]
        if len(preds) < 2:
            continue
        ok = all(lf.nodes.get(p) is not None and list(lf.nodes[p].succs) == [a] and
                 body[pos[p]][2] is None for p in preds)
        if not ok:
            continue
        call = ss[0]
        for p in preds:
            pa, pss, pbr = body[pos[p]]
            body[pos[p]] = (pa, list(pss) + [call], pbr)
        body[i] = (a, ss[1:], br)
    return body


def fold_paramarrays(body):
    """VB passes a ParamArray as a temporary Variant array: ReDim t(n - 1),
    t(0) = a ... t(n - 1) = z, the call, Erase t.  Put the items back."""
    def call_of(s):
        e = s.e if isinstance(s, (ExprStmt, CallStmt)) else (s.src if isinstance(s, Assign) else None)
        f = getattr(e, "func", None) if isinstance(e, Call) else None
        if f is not None and f.params and getattr(f.params[-1], "paramarray", False) and \
                len(e.args) == len(f.params) and isinstance(e.args[-1], Var):
            return e
        if getattr(e, "paramarray_last", False) and e.args and isinstance(e.args[-1], ir.AddrOf) \
                and isinstance(e.args[-1].e, Var):
            e.args = e.args[:-1] + [e.args[-1].e]      # (&t: the array itself)
        if getattr(e, "paramarray_last", False) and e.args and isinstance(e.args[-1], Var):
            return e                        # Choose(index, a, b, ...)
        return None

    def redim_of(s, t):
        if isinstance(s, ExprStmt) and isinstance(s.e, Call) and s.e.name == "ReDim" and \
                s.e.args and isinstance(s.e.args[0], ir.Index) and s.e.args[0].arr == t:
            b = s.e.args[0].idx
            if len(b) == 1 and isinstance(b[0], Const):
                return b[0].value + 1           # ReDim t(ub): ub + 1 items
            if len(b) == 1 and isinstance(b[0], BinOp) and b[0].op == "To" and \
                    b[0].a == Const(0) and isinstance(b[0].b, Const):
                return b[0].b.value + 1         # ReDim t(0 To ub)
        return None

    out = []
    for a, ss, br in body:
        ss = list(ss)
        i = 0
        while i < len(ss):
            c = call_of(ss[i]) if ss[i] is not None else None
            if c is None:
                i += 1
                continue
            t = c.args[-1]
            j = i - 1
            while j >= 0 and ss[j] is not None and redim_of(ss[j], t) is None:
                j -= 1
            n = redim_of(ss[j], t) if j >= 0 and ss[j] is not None else None
            items, ok = {}, n is not None
            for k in range(j + 1, i):
                s = ss[k]
                if s is None:
                    continue
                if isinstance(s, Assign) and isinstance(s.dst, ir.Index) and s.dst.arr == t and \
                        len(s.dst.idx) == 1 and isinstance(s.dst.idx[0], Const):
                    items[s.dst.idx[0].value] = s.src
                elif any(contains(e, t) for e in stmt_exprs(s)):
                    ok = False
            if not ok or set(items) != set(range(n)):
                i += 1
                continue
            c.args = c.args[:-1] + [items[k] for k in range(n)]
            for k in range(j, i):
                s = ss[k]
                if s is not None and (k == j or isinstance(s, Assign) and
                                      isinstance(s.dst, ir.Index) and s.dst.arr == t):
                    ss[k] = None
            nxt = i + 1
            if nxt < len(ss) and isinstance(ss[nxt], ExprStmt) and isinstance(ss[nxt].e, Call) and \
                    ss[nxt].e.name == "Erase" and ss[nxt].e.args and ss[nxt].e.args[0] == t:
                ss[nxt] = None
            i += 1
        out.append((a, [s for s in ss if s is not None], br))
    return out


def fold_register_pairs(body, lf):
    """An 8-byte value kept in two registers, which optimised code does for a Double or a Currency
    it only moves around: mov ebx, [esp+18h]; mov edi, [esp+1Ch]; ...; Sgn(__r8(ebx, edi)); or
    t_edi = t_edi + x, t_ebx = Hi(t_edi) of a Currency sum in a loop.  Every definition of the two
    registers must be one such value, its halves together; then the lower register holds it whole.
    A local's two halves given constants together are one store too (c = 0, read as a Double)."""
    from .machine import Hi
    import struct

    def reg(x):
        return isinstance(x, Var) and x.space in ("reg", "tmp")

    def off_of(v):
        return v.off[0] if isinstance(v.off, tuple) else v.off

    def number(name, lo, hi):
        raw = struct.pack("<II", lo & 0xffffffff, hi & 0xffffffff)
        if name == "__cy":
            return Const(struct.unpack("<q", raw)[0] / 10000.0, "Currency")
        return Const(struct.unpack("<d", raw)[0], "Double")

    hislots = set()

    def whole(name, a, b, ka):
        """The 8-byte value of the halves a and b, or None."""
        if isinstance(b, Hi) and (b.of.key() == a.key() or b.of.key() == ka):
            return a
        if isinstance(b, Var) and b.space == "local" and not isinstance(a, Const) and \
                (b.key() not in stored or b.key() in hislots):
            return a        # (the upper half of a slot fstp qword wrote: the lower one has been
                            # inlined, t_ebx = Sgn(x) * 10, t_edi = v14; DarwinBots Qpow)
        if isinstance(a, Const) and isinstance(b, Const) and isinstance(a.value, int) and \
                isinstance(b.value, int):
            return number(name, a.value, b.value)
        if isinstance(a, Var) and isinstance(b, Var) and a.space == b.space and \
                a.space in ("local", "global", "arg", "member") and \
                isinstance(off_of(a), int) and isinstance(off_of(b), int) and \
                off_of(b) == off_of(a) + 4:
            return a
        if isinstance(a, Deref) and isinstance(b, Deref) and a.size == 4 == b.size and \
                b.off == a.off + 4 and b.e.key() == a.e.key():
            d = Deref(a.e, 8, a.off)
            d.vbtype = "Currency" if name == "__cy" else "Double"
            return d
        if reg(a) and reg(b):
            c = Call(name, [a, b])
            c.vbtype = "Currency" if name == "__cy" else "Double"
            return c
        return None
    for _round in range(4):
        pairs = {}
        stored = {t.dst.key() for _, ss, _b in body for t in ss
                  if isinstance(t, Assign) and isinstance(t.dst, Var)}
        for _, ss, br in body:
            for st in list(ss) + ([ExprStmt(br.cond)] if br is not None else []):
                for e in stmt_exprs(st):
                    for x in e.walk():
                        if isinstance(x, Call) and x.name in ("__r8", "__cy") and len(x.args) == 2 \
                                and reg(x.args[0]) and reg(x.args[1]):
                            pairs.setdefault((x.args[0].key(), x.args[1].key()), x.name)
        changed = False
        for (ka, kb), name in pairs.items():
            news = {}
            ok = True
            # the slot holding the upper half wherever the lower one is a slot's too
            hislots = set()
            for _, ss, _b in body:
                las = [t for t in ss if isinstance(t, Assign) and isinstance(t.dst, Var) and
                       t.dst.key() == ka]
                lbs = [t for t in ss if isinstance(t, Assign) and isinstance(t.dst, Var) and
                       t.dst.key() == kb]
                for ta, tb in zip(las, lbs):
                    if isinstance(ta.src, Var) and isinstance(tb.src, Var) and \
                            ta.src.space == tb.src.space == "local" and \
                            isinstance(off_of(ta.src), int) and isinstance(off_of(tb.src), int) and \
                            off_of(tb.src) == off_of(ta.src) + 4:
                        hislots.add(tb.src.key())
            for bi, (_, ss, _b) in enumerate(body):
                las = [i for i, t in enumerate(ss) if isinstance(t, Assign) and
                       isinstance(t.dst, Var) and t.dst.key() == ka]
                lbs = [i for i, t in enumerate(ss) if isinstance(t, Assign) and
                       isinstance(t.dst, Var) and t.dst.key() == kb]
                if len(las) != len(lbs):
                    ok = False
                    break
                for ia, ib in zip(las, lbs):
                    w = whole(name, ss[ia].src, ss[ib].src, ka)
                    if w is None or abs(ia - ib) > 4:
                        ok = False
                        break
                    news[(bi, ia)] = w
                    news[(bi, ib)] = None
                if not ok:
                    break
            if not ok or not news:
                continue
            ty = "Currency" if name == "__cy" else "Double"

            def fix(x, ka=ka, kb=kb, ty=ty):
                if isinstance(x, Call) and x.name in ("__r8", "__cy") and len(x.args) == 2 and \
                        reg(x.args[0]) and reg(x.args[1]) and x.args[0].key() == ka and \
                        x.args[1].key() == kb:
                    return Var(x.args[0].space, x.args[0].off, vbtype=ty)
                if isinstance(x, Var) and x.key() == kb:
                    return Hi(Var(*ka, vbtype=ty))
                return None
            out = []
            for bi, (a, ss, br) in enumerate(body):
                keep = []
                for si, t in enumerate(ss):
                    if (bi, si) in news:
                        w = news[(bi, si)]
                        if w is None:
                            continue
                        n = Assign(t.dst, w.replace(fix) if hasattr(w, "replace") else w, t.kind)
                        keep.append(keep_flags(n, t))
                    else:
                        keep.append(substitute_expr(t, fix))
                out.append((a, keep, type(br)(br.cond.replace(fix)) if br is not None else None))
            body = out
            changed = True
            break
        if not changed:
            break
    # __r8(x, slot) with nothing ever stored in that slot itself: its upper half, of a value fstp
    # qword put there, the lower half inlined (20 ^ Sin(x): the argument pushed in two dwords)
    stored = {t.dst.key() for _, ss, _b in body for t in ss
              if isinstance(t, Assign) and isinstance(t.dst, Var)}

    def half(x):
        if isinstance(x, Call) and x.name in ("__r8", "__cy") and len(x.args) == 2 and \
                isinstance(x.args[1], Var) and x.args[1].space == "local" and \
                x.args[1].key() not in stored and not isinstance(x.args[0], Const):
            return x.args[0].replace(half)
        return None
    body = [(a, [substitute_expr(t, half) for t in ss],
             type(br)(br.cond.replace(half)) if br is not None else None) for a, ss, br in body]
    body = merge_const_doubles(body, lf)
    return merge_hi_stores(merge_arg_halves(body, lf))


def merge_const_doubles(body, lf):
    """c = 0 in two dwords, of a local the FPU reads whole, or of a module variable it does anywhere
    in the program (x = 1#: mov [x], 0; mov [x+4], 3FF00000h, DarwinBots StartAnotherRound)."""
    import struct

    def off_of(v):
        return v.off[0] if isinstance(v.off, tuple) else v.off
    q = set(getattr(lf, "fp_qword", ()))
    gq = getattr(lf.m, "global_fp8", ())
    if not q and not gq:
        return body
    out = []
    for a, ss, br in body:
        ss = list(ss)
        for i, t in enumerate(ss):
            if t is None or not (isinstance(t, Assign) and isinstance(t.dst, Var) and
                                 t.dst.space in ("local", "global") and
                                 isinstance(t.src, Const)):
                continue
            o = off_of(t.dst)
            if not isinstance(o, int):
                continue
            for j in range(i + 1, min(i + 3, len(ss))):
                u = ss[j]
                if not (isinstance(u, Assign) and isinstance(u.dst, Var) and
                        u.dst.space == t.dst.space and isinstance(u.src, Const) and
                        isinstance(off_of(u.dst), int)):
                    continue
                lo, hi = (t, u) if off_of(u.dst) == o + 4 else (u, t)
                if off_of(hi.dst) != off_of(lo.dst) + 4 or \
                        (("local", off_of(lo.dst)) not in q if lo.dst.space == "local" else
                         off_of(lo.dst) not in gq) or \
                        not isinstance(lo.src.value, int) or not isinstance(hi.src.value, int):
                    continue
                raw = struct.pack("<II", lo.src.value & 0xffffffff, hi.src.value & 0xffffffff)
                ss[i] = keep_flags(Assign(lo.dst, Const(struct.unpack("<d", raw)[0], "Double")), lo)
                ss[j] = None
                break
        out.append((a, [x for x in ss if x is not None], br))
    return out


def merge_arg_halves(body, lf):
    """An 8-byte parameter copied in two moves: mov eax, [ebp+0Ch]; mov [x], eax; mov eax, [ebp+10h];
    mov [x+4], eax.  It is one store of the parameter (Static oldMx = Mx printed global_7F380004 = a2,
    global_7F380008 = a3, DarwinBots calculateZB); the upper half of an argument is no variable."""
    wide = {off for off, _, t, byref in (lf.m.plists.get(lf.p.va) or ())
            if not byref and t in ("Double", "Currency", "Date")}
    wide |= {k[1] for k, sz in lf.sizes.items() if isinstance(k, tuple) and len(k) == 2 and
             k[0] == "arg" and (8, True) in sz}
    if not wide:
        return body

    def arg(x):
        return x.off if isinstance(x, Var) and x.space == "arg" and isinstance(x.off, int) else None

    def next_to(lo, hi):
        if isinstance(lo, Var) and isinstance(hi, Var):
            return lo.space == hi.space and isinstance(lo.off, int) and isinstance(hi.off, int) and \
                hi.off == lo.off + 4
        if isinstance(lo, Deref) and isinstance(hi, Deref):
            return lo.size == hi.size == 4 and hi.off == lo.off + 4 and lo.e.key() == hi.e.key()
        return False
    out = []
    for a, ss, br in body:
        ss = list(ss)
        for i, t in enumerate(ss):
            if t is None or not isinstance(t, Assign) or arg(t.src) is None:
                continue
            for j in range(i + 1, min(i + 4, len(ss))):
                u = ss[j]
                if u is None or not isinstance(u, Assign) or arg(u.src) is None:
                    continue
                lo, hi = (t, u) if arg(u.src) == arg(t.src) + 4 else (u, t)
                if arg(hi.src) != arg(lo.src) + 4 or arg(lo.src) not in wide or \
                        not next_to(lo.dst, hi.dst):
                    continue
                d = Deref(lo.dst.e, 8, lo.dst.off) if isinstance(lo.dst, Deref) else lo.dst
                ss[i] = keep_flags(Assign(d, lo.src, lo.kind), lo)
                ss[j] = None
                break
        out.append((a, [x for x in ss if x is not None], br))
    return out


def fold_pairs(body):
    """__pair(lo, hi) (an 8-byte argument whose halves were not both known
    while lifting) as a literal once constant propagation made them so."""
    import struct

    def fold(e):
        e = e._rebuild(fold)
        if isinstance(e, Call) and e.name in ("__pair", "__cy", "__r8") and len(e.args) == 2 and \
                all(isinstance(x, Const) and isinstance(x.value, int) for x in e.args):
            raw = struct.pack("<II", e.args[0].value & 0xffffffff, e.args[1].value & 0xffffffff)
            if e.vbtype == "Currency" or e.name == "__cy":
                return Const(struct.unpack("<q", raw)[0] / 10000.0, "Currency")
            return Const(struct.unpack("<d", raw)[0], "Date" if e.vbtype == "Date" else None)
        return e

    out = []
    for a, ss, br in body:
        keep = [substitute_expr(s, lambda x: fold(x)) for s in ss]
        if br is not None:
            br = type(br)(fold(br.cond))
        out.append((a, keep, br))
    return out


def norm_deref(x):
    """*(p + k + off) as *(p + (k + off))."""
    if isinstance(x, Deref) and isinstance(x.e, BinOp) and x.e.op == "+" and \
            isinstance(x.e.b, Const) and isinstance(x.e.b.value, int):
        d = Deref(x.e.a, x.size, x.off + x.e.b.value)
        d.vbtype = getattr(x, "vbtype", None)
        return d
    return x


def fold_cy_pairs(body, lf=None):
    """__cy(lo, hi) whose halves were kept apart (lo = x, h = Hi(x) parked in
    another variable, gh_zip OpenArchive): x itself.  The upper-half dword
    of a ByVal Currency/Double/Date argument of a decompiled procedure is
    Hi(lower) (render.regroup prints the lower one only)."""
    from .machine import Hi
    from .types import param_args
    defs = {}
    for _, ss, _ in body:
        for s in ss:
            if isinstance(s, Assign) and isinstance(s.dst, Var):
                defs.setdefault(s.dst.key(), []).append(s.src)
    # one value however many times it is stored (the same Hi(x) on each path)
    single = {k: v[0] for k, v in defs.items()
              if len({getattr(x, "key", lambda: id(x))() for x in v}) == 1}

    def res(x, n=0):
        while isinstance(x, Var) and x.key() in single and n < 8:
            x, n = single[x.key()], n + 1
        return x

    blk = {"last": {}, "alias": {}, "regs": {}}     # within the block being rewritten

    def f(e):
        if isinstance(e, Call) and e.name in ("__cy", "__r8", "__pair") and len(e.args) == 2:
            lo, hi = [norm_deref(blk["regs"].get(x.key(), x) if isinstance(x, Var) and
                                 x.space == "reg" else x) for x in e.args]
            # x = x + d: ...: __cy(t + d, [x+4]): the upper half just stored
            # with x (simplify.merge_hi_stores made x one 8-byte store)
            if isinstance(hi, Var) and hi.space in ("local", "global", "member") and \
                    isinstance(hi.off, int):
                k = (hi.space, hi.off - 4)
                src = blk["last"].get(k)
                if src is not None:
                    lk = ir.subst(lo, blk["alias"]).key() if blk["alias"] else lo.key()
                    if lk == src or lo.key() == k:
                        return Var(hi.space, hi.off - 4, vbtype="Currency"
                                   if e.name == "__cy" else None)
            rl, rh = res(lo), res(hi)
            if isinstance(rl, Const) and isinstance(rh, Const) and isinstance(rl.value, int) and \
                    isinstance(rh.value, int):
                import struct
                raw = struct.pack("<II", rl.value & 0xffffffff, rh.value & 0xffffffff)
                if e.name == "__cy" or e.vbtype == "Currency":
                    return Const(struct.unpack("<q", raw)[0] / 10000.0, "Currency")
                return Const(struct.unpack("<d", raw)[0])   # (a zero register: xor ebx, ebx)
            if isinstance(lo, Deref) and isinstance(hi, Deref) and lo.e == hi.e and \
                    lo.size == hi.size == 4 and hi.off == lo.off + 4:
                d = Deref(lo.e, 8, lo.off)      # both halves of one field, now visible
                d.vbtype = "Currency" if e.name == "__cy" else e.vbtype
                return d
            if isinstance(lo, Var) and isinstance(hi, Var) and lo.space == hi.space and \
                    lo.space in ("local", "global", "arg", "member") and \
                    isinstance(lo.off, int) and hi.off == lo.off + 4:
                return Var(lo.space, lo.off, vbtype="Currency" if e.name == "__cy" else None)
            h = res(hi)
            if isinstance(h, Hi):
                if hasattr(lo, "key") and (h.of.key() == lo.key() or
                                           hasattr(res(lo), "key") and
                                           h.of.key() == res(lo).key()):
                    return lo
                # lo's variable holds the whole value its Hi came from
                if isinstance(lo, Var) and isinstance(res(lo), Call) and \
                        isinstance(h.of, Var) and h.of.key() in single and \
                        single[h.of.key()].key() == res(lo).key():
                    return lo
        if isinstance(e, Call) and isinstance(e.raw, int) and lf is not None:
            plist = lf.m.plists.get(e.raw)
            if plist:
                wide = []
                for a, pi in param_args(plist, e.args):
                    if not plist[pi][3] and plist[pi][2] in ("Currency", "Double", "Date"):
                        i = next((k for k, y in enumerate(e.args) if y is a), None)
                        if i is not None and i + 1 < len(e.args) and \
                                not isinstance(e.args[i + 1], Hi) and \
                                not isinstance(e.args[i], Const):
                            wide.append(i)
                if wide:
                    args = [x.replace(f) for x in e.args]
                    for i in wide:
                        args[i + 1] = Hi(args[i])
                    obj = e.obj.replace(f) if e.obj is not None else None
                    return e._keep(Call(e.name, args, e.vbtype, e.raw, e.method, obj, e.pure))
        return None
    out = []
    for a, ss, br in body:
        blk["last"], blk["alias"], blk["regs"] = {}, {}, {}
        # registers loaded in the single predecessor (chain) and not changed since
        if lf is not None:
            pos = {x: i for i, (x, _, _) in enumerate(body)}
            chain, cur, seen = [], a, set()
            while True:
                nd = lf.nodes.get(cur)
                if nd is None or len(nd.preds) != 1 or nd.preds[0] in seen or \
                        nd.preds[0] not in pos:
                    break
                cur = nd.preds[0]
                seen.add(cur)
                chain.append(cur)
            for pb in reversed(chain):
                for st in body[pos[pb]][1]:
                    if isinstance(st, Assign) and isinstance(st.dst, Var):
                        if st.dst.space == "reg" and isinstance(st.src, (Var, Deref)):
                            blk["regs"][st.dst.key()] = st.src
                        else:
                            blk["regs"].pop(st.dst.key(), None)
                    if isinstance(st, (ExprStmt, CallStmt)):
                        blk["regs"] = {k: v for k, v in blk["regs"].items()
                                       if not isinstance(v, Deref)}  # a call may change memory
        keep = []
        for s in ss:
            n = substitute_expr(s, f)
            keep.append(n)
            if isinstance(n, Assign) and isinstance(n.dst, Var) and n.dst.space == "reg" and \
                    isinstance(n.src, (Var, Deref)):
                blk["regs"][n.dst.key()] = n.src    # a half loaded into a register
            if isinstance(n, Assign) and isinstance(n.dst, Var) and isinstance(n.dst.off, int):
                if isinstance(n.src, Var):
                    blk["alias"][n.dst.key()] = n.src
                al = blk["alias"]
                blk["last"][n.dst.key()] = (ir.subst(n.src, al) if al else n.src).key()
        if br is not None:
            br = type(br)(br.cond.replace(f))
        out.append((a, keep, br))
    return out


def fold_cy_branches(body, lf):
    """The branch form of a 64-bit Currency compare (a loop test, gh_zip
    frCopyCompressed "Do While cDone < .CompressedSize"):
        B1: If h1 < h2 GoTo T    B2: If h1 > h2 GoTo F    B3: If l1 >= l2 GoTo F Else T
    is one test x1 < x2 -> T / F."""
    idx = {a: i for i, (a, _, _) in enumerate(body)}
    defs = {}
    for _, ss, _ in body:
        for s in ss:
            if isinstance(s, Assign) and isinstance(s.dst, Var):
                defs.setdefault(s.dst.key(), []).append(s.src)
    single = {k: v[0] for k, v in defs.items() if len(v) == 1}

    def res(x):
        return single.get(x.key(), x) if isinstance(x, Var) and x.space in ("reg", "tmp") else x

    def whole(lo, hi):
        lo, hi = norm_deref(res(lo)), norm_deref(res(hi))
        if isinstance(lo, Deref) and isinstance(hi, Deref) and lo.e == hi.e and \
                lo.size == hi.size == 4 and hi.off == lo.off + 4:
            d = Deref(lo.e, 8, lo.off)
            d.vbtype = "Currency"
            return d
        if isinstance(lo, Var) and isinstance(hi, Var) and lo.space == hi.space and \
                lo.space in ("local", "global", "arg", "member") and isinstance(lo.off, int) and \
                hi.off == lo.off + 4:
            return Var(lo.space, lo.off, vbtype="Currency")
        return None

    def cmp(br):
        c = br.cond if br is not None else None
        if isinstance(c, BinOp) and c.op in ("<", ">", "<=", ">="):
            return c
        return None
    changed = False
    for a, ss, br in list(body):
        n = lf.nodes.get(a)
        c1 = cmp(br)
        if n is None or c1 is None or n.kind != "cond" or c1.op not in ("<", ">"):
            continue
        t1, b2 = n.succs
        n2 = lf.nodes.get(b2)
        if n2 is None or b2 not in idx or len(n2.preds) != 1 or body[idx[b2]][1]:
            continue
        c2 = cmp(body[idx[b2]][2])
        if c2 is None or c2.op != {"<": ">", ">": "<"}[c1.op] or \
                c2.a.key() != c1.a.key() or c2.b.key() != c1.b.key():
            continue
        f2, b3 = n2.succs
        n3 = lf.nodes.get(b3)
        if n3 is None or b3 not in idx or len(n3.preds) != 1 or body[idx[b3]][1]:
            continue
        c3 = cmp(body[idx[b3]][2])
        if c3 is None:
            continue
        t3, f3 = n3.succs

        def same(x, y):         # (or a copy of it: cfg.duplicate_tails)
            return x == y or getattr(lf.nodes.get(x), "orig", None) == y or \
                getattr(lf.nodes.get(y), "orig", None) == x
        if not same(t3, f2) or not same(f3, t1):
            continue
        # c3 true -> F: x OP y needs Not c3 on the low halves
        want = {"<": {">=": "<", ">": "<="}, ">": {"<=": ">", "<": ">="}}[c1.op].get(c3.op)
        if want is None:
            continue
        local = {}
        for st in ss:                       # the halves B1 itself loaded into registers
            if isinstance(st, Assign) and isinstance(st.dst, Var):
                local[st.dst.key()] = st.src

        def rl(v):
            return local.get(v.key(), v) if isinstance(v, Var) else v
        x, y = whole(rl(c3.a), rl(c1.a)), whole(rl(c3.b), rl(c1.b))
        if x is None or y is None:
            continue
        body[idx[a]] = (a, ss, type(br)(BinOp(want, x, y)))
        n.succs = [t1, f2]
        for g in (t1, f2):
            gn = lf.nodes.get(g)
            if gn is not None:
                gn.preds = [a if p in (b2, b3) else p for p in gn.preds]
                gn.preds = list(dict.fromkeys(gn.preds))
        for b in (b2, b3):
            del lf.nodes[b]
        changed = True
    if changed:
        body = [(a, ss, br) for a, ss, br in body if a in lf.nodes]
    return body


def fold_fixed_sums(body, lf):
    """[pvData + i * k + c] of a fixed array, read once its data pointer in a register is known for what
    it is (fold_fixed_array_copies): what the lift makes of the same address when it sees the array
    (ChessBrainVB, no bounds checks: mov ebp, [Board]; ...; mov ecx, [ebp + esi*4 + 2Ch] printed
    (Board + esi * 4).f2C).  The same sum with no load is an element's address.  Where the terms don't
    divide by the element's size they are a byte offset the compiler steps by itself (t_esi = t_esi +
    7168 for PV(ply, j) with j gone), and VB can still say it: a((offset) \\ K), with each subscript
    worked out of the linear index for more dimensions."""
    def fixed_base(lin):
        for key, (base, c) in lin.items():
            if key is None or c != 1 or not isinstance(base, Var):
                continue
            info = lf.arrays.get(base.key())
            if info is not None and not info.get("dyn") and info.get("size"):
                return key, base, info
        return None

    def by_offset(base, info, rest, const, size):
        """Element and field for a byte offset no subscript can be read from."""
        K = info["size"]
        field = const % K
        off = None
        for e, k in rest:
            part = e if k == 1 else BinOp("*", e, Const(k))
            off = part if off is None else add(off, part)
        if const - field:
            off = add(off, Const(const - field))
        whole = BinOp("\\", off, Const(K))
        dims = info.get("dims") or [(info.get("lb") if isinstance(info.get("lb"), int) else 0, info.get("n"))]
        subs = []
        rest_i = whole
        for k, (lb, n) in enumerate(dims):
            if k == len(dims) - 1 or not n:
                sub, rest_i = rest_i, None
            else:
                sub = BinOp("Mod", rest_i, Const(n))
                rest_i = BinOp("\\", rest_i, Const(n))
            subs.append(add(sub, Const(lb)) if lb else sub)
            if rest_i is None:
                break
        if len(subs) != len(dims):
            return None
        el = ir.Index(base, subs)
        el.vbtype = None
        return Deref(el, size, field) if K > size or field else el

    def element(x):
        if isinstance(x, Deref) and isinstance(x.e, Var) and x.off:
            # [pvData + 400h]: a constant subscript (printed global_44A554.f400, Invalid qualifier)
            info = lf.arrays.get(x.e.key())
            K = (info or {}).get("size")
            if not info or info.get("dyn") is not False or not K or K > 8 or x.off < K:
                return None
            k0, f = divmod(x.off, K)
            dims = info.get("dims") or [(info.get("lb") if isinstance(info.get("lb"), int) else 0, info.get("n"))]
            el = ir.Index(x.e, element_subscripts(Const(k0), dims))
            el.vbtype = None
            return el if not f and x.size == K else Deref(el, x.size, f)
        addr = isinstance(x, BinOp) and x.op in ("+", "-")
        if not addr and not (isinstance(x, Deref) and isinstance(x.e, BinOp) and x.e.op in ("+", "-")):
            return None
        lin = lf.linear(x if addr else x.e)
        fb = fixed_base(lin)
        if fb is None:
            return None
        key, base, info = fb
        rest = [(e, k) for kk, (e, k) in lin.items() if kk not in (key, None) and k]
        if not rest:
            return None
        if any(isinstance(e, Var) and (lf.arrays.get(e.key()) or {}).get("dyn") is False for e, _ in rest):
            return None                     # (two arrays' data pointers: the difference of addresses)
        const = lin.get(None, [None, 0])[1] + (0 if addr else x.off)
        if addr:
            el = lf.element_address(x)
            if isinstance(el, ir.AddrOf):
                return el
            el = by_offset(base, info, rest, const, info["size"])
            return ir.AddrOf(el) if el is not None else None
        index = None
        for e, k in rest:
            part = e if k == 1 else BinOp("*", e, Const(k))
            index = part if index is None else add(index, part)
        save, lf.scale = lf.scale, 1
        try:
            el = lf.array_at(base, index, const, x.size)
        except Exception:
            el = None
        finally:
            lf.scale = save
        return el if el is not None else by_offset(base, info, rest, const, x.size)
    out = []
    for a, ss, br in body:
        keep = [substitute_expr(s, element) for s in ss]
        out.append((a, keep, type(br)(br.cond.replace(element)) if br is not None else None))
    return out


def fold_flag_and(body, lf):
    """A comparison of two parts made into a flag: f = 0 unless both hold, in fast code
        if A goto B else Z;  B: f = 1: if C goto J else Z;  Z: f = 0;  J:
    (or with the second test the other way round).  One test, for fold_cy_compares to read:
    Print #1, c = e of two Currencies printed the halves of each."""
    idx = {a: i for i, (a, _, _) in enumerate(body)}
    out = list(body)
    for i, (a, ss, br) in enumerate(body):
        n = lf.nodes.get(a)
        if br is None or n is None or len(n.succs) != 2:
            continue
        for b_at, z_at in ((n.succs[1], n.succs[0]), (n.succs[0], n.succs[1])):
            if b_at not in idx or z_at not in idx:
                continue
            _, bs, bbr = out[idx[b_at]]
            _, zs, zbr = out[idx[z_at]]
            nb, nz = lf.nodes.get(b_at), lf.nodes.get(z_at)
            if nb is None or nz is None or bbr is None or zbr is not None or len(nb.succs) != 2 or \
                    len(nb.preds) != 1 or len(nz.succs) != 1:
                continue
            if not (len(bs) == 1 and len(zs) == 1 and isinstance(bs[0], Assign) and isinstance(zs[0], Assign) and
                    isinstance(bs[0].dst, Var) and bs[0].dst == zs[0].dst and isinstance(bs[0].src, Const) and
                    isinstance(zs[0].src, Const)):
                continue
            j = nz.succs[0]
            if j not in nb.succs:
                continue
            z2 = nb.succs[0] if nb.succs[1] == j else nb.succs[1]
            if z2 != z_at:
                # (its own copy of f = 0: duplicate_small_joins)
                n2 = lf.nodes.get(z2)
                if n2 is None or z2 not in idx or list(n2.succs) != [j] or out[idx[z2]][2] is not None or \
                        len(out[idx[z2]][1]) != 1 or repr(out[idx[z2]][1][0]) != repr(zs[0]) or len(n2.preds) != 1:
                    continue
                out[idx[z2]] = (z2, [], None)
                n2.preds = []
                nj = lf.nodes.get(j)
                if nj is not None:
                    nj.preds = [p for p in nj.preds if p != z2]
            # (a block's condition says its first successor is taken; both read as "the flag stays set")
            from .structure import negate
            c1 = br.cond if b_at == n.succs[0] else negate(br.cond)
            c2 = bbr.cond if j == nb.succs[0] else negate(bbr.cond)
            new = BinOp("And", c1, c2)
            nz.preds = [p for p in nz.preds if p != b_at]
            nb.succs = [j]
            nb.kind = "jmp"
            nj = lf.nodes.get(j)
            if nj is not None and b_at not in nj.preds:
                nj.preds = list(nj.preds) + [b_at]
            out[idx[b_at]] = (b_at, bs, None)
            # A's branch keeps its targets: the condition now says both
            out[i] = (a, ss, type(br)(new if b_at == n.succs[0] else negate(new)))
            break
    return out


def fold_double_eq(body, lf):
    """A Double tested for equality with a constant, in fast code: an integer compare of each half,
    If lo = L And hi = H (merge_conditions has made one condition of the jumps), or lo <> L Or
    hi <> H.  One test of the 8 bytes (DarwinBots OptionsForm: If .Viscosity = 0.01 printed t_ecx <>
    &H47AE147B Or t_eax <> &H3F847AE1, and read the upper half of a parameter as the next one)."""
    import struct
    defs = {}
    for _, ss, _ in body:
        for s in ss:
            if isinstance(s, Assign) and isinstance(s.dst, Var):
                defs.setdefault(s.dst.key(), []).append(s.src)
    single = {k: v[0] for k, v in defs.items() if len(v) == 1}

    def res(x):
        return single.get(x.key(), x) if isinstance(x, Var) and x.space in ("reg", "tmp") else x

    def whole(lo, hi):
        lo, hi = norm_deref(res(lo)), norm_deref(res(hi))
        if isinstance(lo, Deref) and isinstance(hi, Deref) and lo.e == hi.e and \
                lo.size == hi.size == 4 and hi.off == lo.off + 4:
            d = Deref(lo.e, 8, lo.off)
            d.vbtype = "Double"
            return d
        if isinstance(lo, Var) and isinstance(hi, Var) and lo.space == hi.space and \
                lo.space in ("local", "global", "arg", "member") and isinstance(lo.off, int) and \
                isinstance(hi.off, int) and hi.off == lo.off + 4:
            return Var(lo.space, lo.off, vbtype="Double")
        return None

    def number(lo, hi):
        """The Double of these two dwords, if it is a value one would write; two Longs tested one
        after the other are small numbers, a denormal here."""
        raw = struct.pack("<II", lo & 0xffffffff, hi & 0xffffffff)
        v = struct.unpack("<d", raw)[0]
        return v if v == v and 1e-30 < abs(v) < 1e30 else None

    def leaves(e, op, out):
        if isinstance(e, BinOp) and e.op == op:
            leaves(e.a, op, out)
            leaves(e.b, op, out)
        else:
            out.append(e)
        return out

    def fold(e):
        if not (isinstance(e, BinOp) and e.op in ("And", "Or")):
            return None
        test = "=" if e.op == "And" else "<>"
        xs = [x.replace(fold) for x in leaves(e, e.op, [])]
        out, i, hit = [], 0, False
        while i < len(xs):
            p, q = xs[i], xs[i + 1] if i + 1 < len(xs) else None
            if q is not None and all(isinstance(t, BinOp) and t.op == test and
                                     isinstance(t.b, Const) and isinstance(t.b.value, int)
                                     for t in (p, q)):
                x = whole(p.a, q.a)
                v = number(p.b.value, q.b.value) if x is not None else None
                if v is not None:
                    out.append(BinOp(test, x, Const(v, "Double")))
                    i += 2
                    hit = True
                    continue
            out.append(p)
            i += 1
        if not hit:
            return None
        r = out[0]
        for t in out[1:]:
            r = BinOp(e.op, r, t)
        return r
    return [(a, ss, type(br)(br.cond.replace(fold)) if br is not None else None)
            for a, ss, br in body]


def fold_cy_compares(body):
    """A Currency compared with a constant is compiled as a 64-bit compare
    of its halves: (hi <= H) And ((hi < H) Or (lo <= L)) is x <= C, and
    (hi > H) Or ((hi >= H) And (lo > L)) is x > C (gh_zip
    pvOutputLocalHeader: cFileSize > LNG_SIZE_LIMIT)."""
    import struct
    # a register loaded once from a half: "mov eax, [x+4]" before the compare
    single = {}
    for _, ss, _ in body:
        for s in ss:
            if isinstance(s, Assign) and isinstance(s.dst, Var) and s.dst.space in ("reg", "tmp"):
                single.setdefault(s.dst.key(), []).append(s.src)
    from .machine import Hi
    single = {k: v[0] for k, v in single.items() if len(v) == 1 and
              isinstance(v[0], (Var, Deref, Hi, Call))}

    def res(x):
        return single.get(x.key(), x) if isinstance(x, Var) else x

    def whole(lo, hi):
        lo, hi = res(lo), res(hi)
        if isinstance(hi, Hi) and hi.of.key() == getattr(lo, "key", lambda: None)():
            return lo                       # (a value in two registers: edi = CCur(x), ebx = Hi(CCur(x)))
        if isinstance(hi, Deref) and hi.off == 4 and isinstance(hi.e, Var) and isinstance(lo, Var) and \
                hi.e.key() == lo.key():
            return Var(lo.space, lo.off, vbtype="Currency")     # (its upper half by the variable's offset)
        if isinstance(lo, Deref) and lo.off == 0 and isinstance(lo.e, Var) and isinstance(hi, Deref) and \
                hi.off == 4 and isinstance(hi.e, Var) and hi.e.key() == lo.e.key() and lo.size == 4 == hi.size:
            return Var(lo.e.space, lo.e.off, vbtype="Currency")
        if isinstance(lo, Deref) and isinstance(hi, Deref) and lo.e == hi.e and \
                lo.size == hi.size == 4 and hi.off == lo.off + 4:
            d = Deref(lo.e, 8, lo.off)
            d.vbtype = "Currency"
            return d
        if isinstance(lo, Var) and isinstance(hi, Var) and lo.space == hi.space and \
                lo.space in ("local", "global", "arg", "member") and \
                isinstance(lo.off, int) and hi.off == lo.off + 4:
            return Var(lo.space, lo.off, vbtype="Currency")
        return None

    STRICT = {"<=": "<", ">=": ">"}
    LOOSE = {v: k for k, v in STRICT.items()}

    def cmp(e):
        if isinstance(e, BinOp) and e.op in ("<", "<=", ">", ">="):
            return e.a, e.op, e.b
        return None

    def same(x, y):
        return res(x).key() == res(y).key()

    def pair(p, q, outer):
        """(hi op H) outer-joined with ((hi op' H) inner (lo op3 L))."""
        inner = "Or" if outer == "And" else "And"
        a = cmp(p)
        if a is None or not (isinstance(q, BinOp) and q.op == inner):
            return None
        b, c = cmp(q.a), cmp(q.b)
        if b is None or c is None or not same(a[0], b[0]) or not same(a[2], b[2]):
            return None
        if outer == "And" and STRICT.get(a[1]) != b[1]:
            return None
        if outer == "Or" and LOOSE.get(a[1]) != b[1]:
            return None
        op = c[1]
        if op.strip("=") != a[1].strip("="):
            return None                     # one direction throughout
        x = whole(c[0], a[0])
        if x is None:
            return None
        lo, hi = c[2], a[2]
        if isinstance(lo, Const) and isinstance(hi, Const) and isinstance(lo.value, int) and \
                isinstance(hi.value, int):
            raw = struct.pack("<II", lo.value & 0xffffffff, hi.value & 0xffffffff)
            return BinOp(op, x, Const(struct.unpack("<q", raw)[0] / 10000.0, "Currency"))
        y = whole(lo, hi)                   # two Currency variables (gh_zip Extract)
        if y is None:
            return None
        return BinOp(op, x, y)

    def equal(p, q, outer):
        """lo = L And hi = H, lo <> L Or hi <> H, with the upper half of L known for what it is: no two
        Longs tested in turn (t59cyarith c = e with e in registers)."""
        test = "=" if outer == "And" else "<>"
        if not (isinstance(p, BinOp) and isinstance(q, BinOp) and p.op == test == q.op):
            return None
        hi = res(q.a)
        if not isinstance(res(q.b), Hi) and not (isinstance(hi, Deref) and hi.off == 4 and isinstance(hi.e, Var)):
            return None
        x, y = whole(p.a, q.a), whole(p.b, q.b)
        return BinOp(test, x, y) if x is not None and y is not None else None

    def chain(e, op):
        if isinstance(e, BinOp) and e.op == op:
            return chain(e.a, op) + chain(e.b, op)
        return [e]

    def f(e):
        if isinstance(e, BinOp) and e.op in ("=", "<>") and isinstance(e.b, Const) and e.b.value == 0 and \
                isinstance(e.a, BinOp) and e.a.op == "Or":
            # or eax, [c + 4]; jne: If c <> 0 Then
            x = whole(e.a.a, e.a.b)
            return BinOp(e.op, x, Const(0)) if x is not None else None
        if not (isinstance(e, BinOp) and e.op in ("And", "Or")):
            return None
        items = [x.replace(f) for x in chain(e, e.op)]
        out, i, hit = [], 0, False
        while i < len(items):
            n = (pair(items[i], items[i + 1], e.op) or equal(items[i], items[i + 1], e.op)) \
                if i + 1 < len(items) else None
            if n is not None:
                out.append(n)
                i += 2
                hit = True
            else:
                out.append(items[i])
                i += 1
        if not hit:
            r = out[0]
            for x in out[1:]:
                r = BinOp(e.op, r, x, e.vbtype)
            return e._keep(r) if len(out) > 1 else r
        r = out[0]
        for x in out[1:]:
            r = BinOp(e.op, r, x, e.vbtype)
        return r
    out = []
    for a, ss, br in body:
        keep = [substitute_expr(s, f) for s in ss]
        if br is not None:
            br = type(br)(br.cond.replace(f))
        out.append((a, keep, br))
    return out


CMP_OPS = {"=", "<>", "<", ">", "<=", ">="}
SWAP_CMP = {"=": "=", "<>": "<>", "<": ">", ">": "<", "<=": ">=", ">=": "<="}


def fold_strings(body, lf):
    """__vbaStrCmp(a, b) compared with 0 is the comparison a OP b (any other
    use is StrComp(a, b)); a pointer to an empty string constant in a String
    position is ""."""
    def lit(x):
        if isinstance(x, Const) and isinstance(x.value, int):
            va = x.value & 0xffffffff
            im = lf.im
            try:
                if im.valid(va) and im.section_of(va) == ".text" and im.u32(va - 4) == 0 \
                        and im.u16(va) == 0:
                    return Str("")
            except Exception:
                pass
        return x

    def fold(e):
        if isinstance(e, BinOp) and e.op in CMP_OPS:      # before the operands change
            a, b, op = e.a, e.b, e.op
            if isinstance(a, Const) and a.value == 0 and isinstance(b, (Call, BinOp)):
                a, b, op = b, a, SWAP_CMP[op]
            if isinstance(a, Call) and a.name == "__strcmp" and len(a.args) == 2 and \
                    isinstance(b, Const) and b.value == 0:
                return BinOp(op, fold(lit(a.args[0])), fold(lit(a.args[1])))
            if isinstance(a, BinOp) and a.op == "Like" and op in ("=", "<>") and \
                    isinstance(b, Const) and b.value == 0:
                like = fold(a)
                return like if op == "<>" else UnOp("Not", like)
        e = e._rebuild(fold)
        if isinstance(e, Call) and e.name == "__strcmp" and len(e.args) == 2:
            return Call("StrComp", [lit(x) for x in e.args], "Long", e.raw, pure=True)
        if isinstance(e, Call) and e.name == "CreateObject" and len(e.args) == 2 and \
                lit(e.args[1]) == Str(""):
            return e._keep(Call(e.name, e.args[:1], e.vbtype, e.raw))   # no server name
        if isinstance(e, Call) and e.name == "StrComp":
            return e._keep(Call(e.name, [lit(x) for x in e.args[:2]] + e.args[2:], e.vbtype, e.raw))
        if isinstance(e, BinOp) and e.op in ("&", "Like"):
            return BinOp(e.op, lit(e.a), lit(e.b), e.vbtype)
        return e

    def ret(s):
        # a Function's result too (a target: f = __strcmp(a2, ""))
        if getattr(s, "value", None) is not None:
            n = ir.Return()
            n.__dict__.update(s.__dict__)
            n.value = fold(s.value)
            return n
        return s
    out = []
    for a, ss, br in body:
        keep = [substitute_expr(s, lambda x: fold(x)) if not isinstance(s, ir.Return)
                else ret(s) for s in ss]
        if br is not None:
            br = type(br)(fold(br.cond))
        out.append((a, keep, br))
    return out


def alias_safearrays(body, lf):
    """A local fixed array's SAFEARRAY descriptor stands for the array
    (UBound(&descriptor)); the compiler's copies of the descriptor's address
    (for __vbaAryDestruct at exit) are not source."""
    mapping = dict(getattr(lf, "sa_alias", {}))
    if not mapping:
        return body

    def note_view(x):
        """UBound(descriptor) keeps the template's bounds: (element size, dims)."""
        if isinstance(x, Call) and x.name in ("UBound", "LBound") and x.args:
            a = x.args[0].e if isinstance(x.args[0], ir.AddrOf) else x.args[0]
            v = mapping.get(a.key()) if isinstance(a, Var) else None
            info = lf.arrays.get(v.key()) if isinstance(v, Var) else None
            if info and info.get("sa") is not None and info.get("dims"):
                x.view = (info.get("size"), info["dims"])
    for _, ss, br in body:
        for s in ss:
            for e in stmt_exprs(s):
                for x in e.walk():
                    note_view(x)
        if br is not None:
            for x in br.cond.walk():
                note_view(x)
    # a temporary locking more than one array: by flow (apply_locks)
    for k, v in getattr(lf, "sa_locks", {}).items():
        if len(v) > 1 or not all(isinstance(x, Var) for x in v):
            mapping.pop(k, None)
    out = []
    for a, ss, br in body:
        keep = []
        cur = dict(mapping)
        for s in ss:
            if isinstance(s, Assign) and isinstance(s.src, ir.AddrOf) and \
                    isinstance(s.src.e, Var) and s.src.e.key() in mapping:
                continue
            if isinstance(s, Assign) and isinstance(s.src, SP) and \
                    ("local", s.src.off + 4) in mapping:
                # the descriptor's address in a (reused) temporary: a fixed
                # array passed to an array parameter, until the slot changes
                if isinstance(s.dst, Var):
                    cur[s.dst.key()] = mapping[("local", s.src.off + 4)]
                continue
            n = substitute_many(s, cur)
            if isinstance(s, Assign) and isinstance(s.dst, Var) and s.dst.key() in cur and \
                    s.dst.key() not in mapping:
                del cur[s.dst.key()]
            keep.append(n)
        if br is not None:
            br = type(br)(ir.subst(br.cond, cur))
        out.append((a, keep, br))
    return out


def alias_addr_temps(body, lf):
    """A frame temporary that only ever holds &x (lea ebx, [esi+38h]; mov
    [ebp-0D8h], ebx: a member array's slot, gh_dict dict_AddKeyValue) stands
    for x itself: *t is x, ReDim t(..) is ReDim x(..), t alone is &x."""
    defs = {}
    for _, ss, _ in body:
        for st in ss:
            if isinstance(st, Assign) and isinstance(st.dst, Var) and \
                    st.dst.space in ("local", "reg"):
                defs.setdefault(st.dst.key(), []).append(st.src)
    mapping = {}
    for k, srcs in defs.items():
        if isinstance(k[1], int) and (k[1] in lf.kept or k[1] == lf.result_slot):
            continue
        if k[0] == "local" and len(srcs) > 1:
            # (the zero at End With: With theTray, a member record; o1001 = m3C, "Type mismatch")
            srcs = [x for x in srcs if not (isinstance(x, Const) and x.value == 0)] or srcs
        x0 = srcs[0]
        if all(isinstance(x, ir.AddrOf) and isinstance(x.e, Var) and
               x.e.space in ("member", "global") and x.e.key() == x0.e.key() for x in srcs):
            mapping[k] = x0.e
        elif k[0] == "local" and isinstance(x0, SP) and lf.base_esp is not None and \
                x0.off >= lf.base_esp and all(isinstance(x, SP) and x.off == x0.off for x in srcs) and \
                not (isinstance(k[1], int) and any(
                    isinstance(t, Const) and isinstance(t.value, int) and t.value & 0x4000
                    for t in defs.get(("local", k[1] - 8), ()))):
            # (not the payload of a VT_BYREF Variant: see merged_byref_arg)
            # With v34 (a local): VB keeps &v34 in a frame slot.  a target Form1_Load
            # printed v7C8 = v34 and v7C8.SetFileName ...: error 438
            t = lf.slot_var(x0.off)
            if isinstance(t, Var) and t.key() != k:
                mapping[k] = t
    if not mapping:
        return body
    for k, x in mapping.items():
        info = lf.arrays.pop(k, None)
        if info is not None:
            cur = lf.arrays.setdefault(x.key(), dict(info, var=x))
            for kk, vv in info.items():
                if cur.get(kk) is None:
                    cur[kk] = vv
        for d in (getattr(lf, "rec_of", None),):
            if d is not None and k in d:
                d.setdefault(x.key(), d.pop(k))

    def f(e):
        if isinstance(e, Deref) and e.off == 0 and isinstance(e.e, Var) and e.e.key() in mapping:
            return mapping[e.e.key()]
        if isinstance(e, ir.Index) and isinstance(e.arr, Var) and e.arr.key() in mapping:
            return ir.Index(mapping[e.arr.key()], [i.replace(f) for i in e.idx], e.vbtype)
        if isinstance(e, BinOp) and e.op == "+" and isinstance(e.a, Var) and \
                e.a.key() in mapping and mapping[e.a.key()].space == "member" and \
                isinstance(e.b, Const) and isinstance(e.b.value, int):
            # (a field of a member record, by address: With theTray: .szTip = s)
            return ir.AddrOf(Var("member", mapping[e.a.key()].off + e.b.value))
        if isinstance(e, Var) and e.key() in mapping:
            return ir.AddrOf(mapping[e.key()])
        return None
    out = []
    for a, ss, br in body:
        keep = []
        for st in ss:
            if isinstance(st, Assign) and isinstance(st.dst, Var) and st.dst.key() in mapping:
                continue
            n = substitute_expr(st, f)
            if isinstance(n, Assign) and isinstance(n.dst, Var) and n is not st and \
                    getattr(n.src, "iface", None) is not None:
                lf.objtype.setdefault(n.dst.key(), n.src.iface)     # (Set m34 = New C)
            keep.append(n)
        if br is not None:
            br = type(br)(br.cond.replace(f))
        out.append((a, keep, br))
    return out


def fold_variant_temps(body, lf):
    """A Variant temporary filled in place (tag at k, value at k+8) in one
    block and read through its address in another (the compiler shares the
    final VarCopy between the Cases: gh_zip FileInfo): the temporary is
    that value."""
    VT = {2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 13, 17}   # 9, 13: object references; 10: Missing
    reads = {}
    for _, ss, br in body:
        for s in ss:
            for e in stmt_exprs(s):
                if isinstance(s, Assign) and e is s.dst:
                    continue
                for x in e.walk():
                    if isinstance(x, Var) and x.space == "local":
                        reads[x.key()] = reads.get(x.key(), 0) + 1
        if br is not None:
            for x in br.cond.walk():
                if isinstance(x, Var) and x.space == "local":
                    reads[x.key()] = reads.get(x.key(), 0) + 1
    out = []
    for a, ss, br in body:
        ss = list(ss)
        for i, s in enumerate(ss):
            if not (isinstance(s, Assign) and isinstance(s.dst, Var) and s.dst.space == "local"
                    and isinstance(s.dst.off, int) and isinstance(s.src, Const) and
                    isinstance(s.src.value, int) and (s.src.value & 0xbfff) in VT):
                continue
            k, t = s.dst.off, s.src.value

            def store(x, off):
                return isinstance(ss[x], Assign) and isinstance(ss[x].dst, Var) and \
                    ss[x].dst.space == "local" and ss[x].dst.off == off and \
                    not (off == k + 8 and getattr(ss[x], "whole_variant", False))
                    # (a Variant this pass has made already is nobody's value half)

            if reads.get(("local", k + 8)):
                continue
            if i + 1 < len(ss) and store(i + 1, k - 8) and isinstance(ss[i + 1].src, Const) and \
                    reads.get(("local", k - 8)):
                # the value half of the Variant whose tag comes next, read as a whole: a 3 looks like
                # a tag too.  (value 2, tag 2; value 3, tag 3: the 3 took the 2 for its value, and the
                # next tag it for its own: ScaleY(y, 2, 1) for vbPixels, Bar1's bottom edge)
                continue
            j = None
            def used(x):
                # the Variant was read between: that value belongs to the next one
                # (a target Proc_8FF190, p = 0: t = 2: a.Top = t: p = 8500: t = 2 ...
                # paired each tag with the next payload: Left = 8500, Height = 12000,
                # Width = 0, and the main window came out 0 pixels wide)
                return ss[x] is not None and any(
                    isinstance(y, Var) and y.space == "local" and y.off == k
                    for e in stmt_exprs(ss[x])
                    if not (isinstance(ss[x], Assign) and e is ss[x].dst) for y in e.walk())

            jf = next((j for j in range(i + 1, min(i + 6, len(ss))) if store(j, k + 8)), None)
            # VB fills the value first and writes the tag just before the call (Property Set with a
            # ByVal Variant, t04w): pair back, to the nearest
            jb = next((j for j in range(i - 1, max(-1, i - 6), -1) if store(j, k + 8)), None)
            back = jb is not None and not any(store(x, k) for x in range(jb + 1, i)) and \
                not any(store(x, k + 12) for x in range(jb, i))

            def calls_between(lo, hi):
                # (a Variant is built whole: no call of an object's between its halves)
                return any(ss[x] is not None and any(
                    isinstance(y, Call) and y.obj is not None for e in stmt_exprs(ss[x]) for y in e.walk())
                    for x in range(lo, hi))
            if jf is not None and not any(store(x, k) or used(x) for x in range(i + 1, jf)) and \
                    not any(store(x, k + 12) for x in range(i, min(i + 6, len(ss)))) and \
                    not calls_between(i + 1, jf) and not (
                        # (a value nearer before it, or one the next tag follows, belongs to that:
                        # chameleon's progress bar v - 1: value 1, tag; two calls; value 3, tag: the first
                        # tag took the 3, the second the 2 of the line after, ScaleY(y, 2, 1) for
                        # vbPixels, and the bar's bottom edge was out by a point)
                        back and (i - jb < jf - i or jf + 1 < len(ss) and store(jf + 1, k) and
                                  isinstance(ss[jf + 1].src, Const))):
                j = jf                          # tag first, value after: as before
            elif back:
                j = jb
            prev_val = None
            if j is None and not any(ss[x] is not None and store(x, k + 8) for x in range(i)):
                # the value stored in the block before, on the only way here (a block
                # ends at a line of its own: LCase(global_F06CCC) printed the tag)
                pa = next((t for t in reversed(out)
                           if a in node_succs(lf, t[0]) and len(node_succs(lf, t[0])) == 1), None)
                if pa is not None:
                    hit = [t for t in pa[1] if isinstance(t, Assign) and isinstance(t.dst, Var) and
                           t.dst.space == "local" and t.dst.off == k + 8]
                    prev_val = hit[-1].src if hit else None
            if j is None and prev_val is None:
                continue                        # (8-byte payloads: not handled here)
            val = ss[j].src if j is not None else prev_val
            if j is not None and not t & 0x4000 and isinstance(val, Const) and \
                    isinstance(val.value, int) and val.value & 0x4000 and \
                    (val.value & 0xbfff) in VT and \
                    any(x is not None and store(x, k + 16) for x in range(len(ss))):
                # the next slot starts a Variant of its own (tag 4002h, pointer at k + 16): a
                # plain 3 in k is no type word.  a target Proc_82D810 a1, 3, 3, v24 printed
                # Proc_82D810 a1, 3, 16386, v24
                continue
            if j is not None and not t & 0x4000 and t not in (9, 13) and \
                    isinstance(val, ir.Prop) and getattr(val, "control", None) is not None:
                # a control is no value of a number's type: 6 stored by itself, a literal for
                # a ByRef parameter (a target Form4 Text1_LostFocus Proc_82AD50 x, 6 printed
                # Proc_82AD50 x, Me.Label1; Argument not optional)
                continue
            if t & 0x4000:
                val = byref_target(lf, val, t)  # VT_BYREF: the variable it points at
            if (t & 0xfff) == 10:
                if not (isinstance(val, Const) and isinstance(val.value, int) and
                        val.value & 0xffffffff == 0x80020004):
                    continue                    # a real error value
                from .ir import Missing
                val = Missing()                 # an omitted Optional (tests/t20misc)
            elif (t & 0xfff) == 11 and isinstance(val, Const) and isinstance(val.value, int) \
                    and val.vbtype is None and val.value & 0xffff in (0, 0xffff):
                # VT_BOOL: True / False (a target Me.List1.Font.Bold = -1)
                val = Const(-1 if val.value & 0xffff else 0, "Boolean")
            elif (t & 0xfff) == 4 and isinstance(val, Const) and isinstance(val.value, int):
                # a Single's bits (a target Me.List1.Left = &H42700000 for 60: the
                # list views 16000 pixels off the form)
                val = lf.wide_value(4, val, None) or val
            n = Assign(Var("local", k), val)
            n.va = getattr(ss[j], "va", None) if j is not None else getattr(s, "va", None)
            n.va = n.va or getattr(s, "va", None)
            n.whole_variant = True          # the value, not a tag word
            lf.variants.add(("local", k))
            if j is None:
                ss[i] = n
            else:
                # (where the value is: moved up to the tag it went ahead of what it reads,
                # v1A8 = a(k): tag: value = v1A8 / 6: a target A4.DrawRow)
                ss[j], ss[i] = n, None
        out.append((a, [x for x in ss if x is not None], br))
    return missing_slots(out)


def missing_slots(body):
    """A frame Variant that only ever holds Missing (the VT_ERROR temporaries
    VB keeps for a call's omitted arguments: MsgBox text, , , v68, v78, v88)
    is the omitted argument itself wherever it is read; it printed `v88 =`
    (tests/t20misc made these Missing)."""
    from .ir import Missing
    srcs = {}
    for bi, (_, ss, _) in enumerate(body):
        for st in ss:
            if isinstance(st, Assign) and isinstance(st.dst, Var) and st.dst.space == "local":
                if bi == 0 and isinstance(st.src, Const) and st.src.value == 0 and st.src.vbtype is None:
                    continue        # the prologue's zero-fill (VT_EMPTY for __vbaFreeVar)
                srcs.setdefault(st.dst.key(), []).append(st.src)
    keys = {k for k, v in srcs.items() if v and all(isinstance(x, Missing) for x in v)}
    if not keys:
        return body
    zero = {k for k in keys}                # their zero-fill goes too

    def fix(x):
        if isinstance(x, ir.AddrOf) and isinstance(x.e, Var) and x.e.key() in keys:
            return ir.AddrOf(Missing())
        if isinstance(x, Var) and x.key() in keys:
            return Missing()
        return None
    out = []
    for a, ss, br in body:
        keep = [substitute_expr(st, fix) for st in ss
                if not (isinstance(st, Assign) and isinstance(st.dst, Var) and
                        st.dst.key() in zero)]
        out.append((a, keep, br if br is None else type(br)(br.cond.replace(fix))))
    return out


def reused_arg_reads(body, lf):
    """A parameter's slot the compiler goes on to use for a temporary (lift._store, reused_args): the
    lift names reads after the first store by address, but where no store comes first on any way to
    a read the slot still holds the parameter (ChessBrainVB MovePicker: ClearMove Move printed
    ClearMove v40008, and the slot, an element's offset elsewhere, became a TMOVE)."""
    keys = {("local", lf.ARG_REUSE - off): off for off in getattr(lf, "reused_args", ())}
    if not keys:
        return body
    idx = {a: i for i, (a, _, _) in enumerate(body)}
    preds = {i: [] for i in range(len(body))}
    for i, (a, _, _) in enumerate(body):
        for t in node_succs(lf, a):
            if t in idx:
                preds[idx[t]].append(i)
    gen = []
    for _, ss, _b in body:
        gen.append({s.dst.key() for s in ss if isinstance(s, Assign) and isinstance(s.dst, Var) and
                    s.dst.key() in keys})
    out_ = [set(g) for g in gen]
    changed = True
    while changed:
        changed = False
        for i in range(len(body)):
            cur = set(gen[i])
            for p_ in preds[i]:
                cur |= out_[p_]
            if cur != out_[i]:
                out_[i] = cur
                changed = True
    out = []
    for i, (a, ss, br) in enumerate(body):
        cur = set()
        for p_ in preds[i]:
            cur |= out_[p_]

        def f(x):
            if isinstance(x, Var) and x.key() in keys and x.key() not in cur:
                return Var("arg", keys[x.key()])
            return None
        keep = []
        for s in ss:
            keep.append(substitute_expr(s, f))
            if isinstance(s, Assign) and isinstance(s.dst, Var) and s.dst.key() in keys:
                cur.add(s.dst.key())
        out.append((a, keep, type(br)(br.cond.replace(f)) if br is not None else None))
    return out


def resolve_lock_regs(body, lf):
    """__arylock(tmp, *reg): the register's reaching definition (a
    parameter or local loaded into it), so the marker does not keep the
    register's stores alive."""
    idx = {a: i for i, (a, _, _) in enumerate(body)}
    preds = {i: [] for i in range(len(body))}
    for i, (a, _, _) in enumerate(body):
        for t in node_succs(lf, a):
            if t in idx:
                preds[idx[t]].append(i)

    def reaching(bi, si, key):
        """Sources of the defs of key reaching statement si of block bi."""
        srcs, seen = [], set()
        work = [(bi, si)]
        while work:
            b, upto = work.pop()
            ss = body[b][1]
            hit = None
            for k in range((len(ss) if upto is None else upto) - 1, -1, -1):
                t = ss[k]
                if isinstance(t, Assign) and isinstance(t.dst, Var) and t.dst.key() == key:
                    hit = t.src
                    break
            if hit is not None:
                srcs.append(hit)
                continue
            if not preds[b] or len(seen) > 200:
                return None
            for p_ in preds[b]:
                if p_ not in seen:
                    seen.add(p_)
                    work.append((p_, None))
        return srcs
    out = []
    for bi, (a, ss, br) in enumerate(body):
        keep = []
        for si, s in enumerate(ss):
            if isinstance(s, ExprStmt) and isinstance(s.e, Call) and s.e.name == "__arylock":
                arr = s.e.args[1]
                r = arr.e if isinstance(arr, Deref) and arr.off == 0 else None
                if isinstance(r, Var) and r.space == "reg":
                    srcs = reaching(bi, si, r.key())
                    if srcs and all(isinstance(x, Var) and x.space in ("arg", "local", "member",
                                                                       "global") and
                                    x.key() == srcs[0].key() for x in srcs):
                        n = ExprStmt(Call("__arylock", [s.e.args[0], Deref(srcs[0], 4, 0)]))
                        n.va = getattr(s, "va", None)
                        s = n
                elif isinstance(arr, Var) and arr.space == "reg":
                    # the array's descriptor itself, loaded before a join (With HashTable(i), fast
                    # code: mov eax, [HashTable] ... push eax).  The temporary is that array from
                    # here on, not whatever the register holds next: global_44A020(i).fE, of
                    # mov eax, [ZobristHash1] right after the lock (ChessBrainVB IsInHashTable)
                    srcs = reaching(bi, si, arr.key())
                    if srcs and all(isinstance(x, Var) and x.space in ("arg", "local", "member",
                                                                       "global") and
                                    x.key() == srcs[0].key() for x in srcs):
                        tgt = s.e.args[0]
                        n = ExprStmt(Call("__arylock", [tgt, srcs[0]]))
                        n.va = getattr(s, "va", None)
                        s = n
                        locks = lf.__dict__.setdefault("sa_locks", {}).get(tgt.key())
                        if locks:
                            locks[:] = [srcs[0] if isinstance(x, Var) and x.key() == arr.key() else x
                                        for x in locks]
                        if isinstance(tgt, Var):
                            lf.sa_alias[tgt.key()] = srcs[0]
                    elif isinstance(s.e.args[0], Var):
                        lf.sa_alias.pop(s.e.args[0].key(), None)    # (never a register)
            keep.append(s)
        out.append((a, keep, br))
    return out


def map_statics(body, lf):
    """The class's Static-locals block reached through a register copy of
    its pointer: [p + k] is that Static variable, p + k its address (lift.py
    static_var does the direct loads).  A module's block likewise, its pointer in a module variable:
    mov edx, [44A624h]; add edx, 1Ch; push edx is ClearMove LastPonderMove (ChessBrainVB
    GetPonderMove); while it sits in a register static_block_regs sees to it."""
    X = lf.m.static_slot.get(lf.obj.index) if lf.obj is not None else None
    mod = getattr(lf.m, "module_static_slot", None) or {}
    if X is None and not mod:
        return body
    base = lf.STATIC_BASE

    def is_p(x):
        return isinstance(x, Var) and (x.space == "member" and x.off == X or
                                       x.space == "global" and x.off in mod)

    def at(p, off):
        if p.space == "member":
            return Var("member", base + off)
        return Var("global", mod[p.off] + off, lf.m.global_name(mod[p.off] + off))

    def f(x):
        if isinstance(x, Var) and x.space == "member" and isinstance(x.off, int) and \
                (lf.arrays.get(("member", x.off + 12)) or {}).get("fixed_bounds"):
            return Var("member", x.off + 12)    # (a fixed array's descriptor: the array, given whole)
        if isinstance(x, Deref) and is_p(x.e):
            return at(x.e, x.off)
        if isinstance(x, Deref) and isinstance(x.e, BinOp) and x.e.op == "+" and is_p(x.e.a) and \
                isinstance(x.e.b, Const) and isinstance(x.e.b.value, int):
            return at(x.e.a, x.e.b.value + x.off)
        if isinstance(x, BinOp) and x.op == "+" and is_p(x.a) and isinstance(x.b, Const) and \
                isinstance(x.b.value, int):
            return ir.AddrOf(at(x.a, x.b.value))
        if isinstance(x, ir.Index) and isinstance(x.arr, ir.AddrOf) and \
                isinstance(x.arr.e, Var) and (x.arr.e.space == "member" or
                                              x.arr.e.space == "global" and x.arr.e.off >= 0x7F000000):
            return ir.Index(x.arr.e, [i.replace(f) for i in x.idx])
        if is_p(x):
            return ir.AddrOf(at(x, 0))
        return None
    out = []
    for a, ss, br in body:
        keep = []
        for s in ss:
            n = substitute_expr(s, f)
            if isinstance(s, Assign) and isinstance(s.dst, Var) and is_p(s.dst):
                n = s                       # (never: the pointer is the runtime's)
            if isinstance(s, Assign) and isinstance(s.dst, Var) and s.dst.space in ("reg", "tmp") and \
                    is_p(s.src) and s.src.space == "global":
                n = s                       # (a module's: copied to a register, static_block_regs)
            if isinstance(s, Assign) and not isinstance(s.dst, Var):
                d = s.dst.replace(f)
                if isinstance(d, ir.AddrOf):
                    d = d.e
                if d is not s.dst:
                    # (its flags too: the record's lost, TmpOpts.Specie(b) = c printed .f0(0) = c)
                    n = keep_flags(Assign(d, s.src.replace(f), s.kind), s)
            e = getattr(n, "e", None)
            info = getattr(getattr(s, "e", None), "redim_info", None)
            if isinstance(e, Call) and getattr(e, "redim", False) is not False and info and \
                    e.args and isinstance(e.args[0], ir.Index) and isinstance(e.args[0].arr, Var):
                v = e.args[0].arr
                ai = lf.arrays.setdefault(v.key(), {"size": 4, "dyn": True, "lb": None,
                                                    "n": None, "var": v})
                ai["dyn"] = True
                if isinstance(info[0], Const):
                    ai["size"] = info[0].value
                if isinstance(info[1], Const):
                    ai["vt"] = info[1].value
                e.redim = True
            keep.append(n)
        if br is not None:
            br = type(br)(br.cond.replace(f))
        out.append((a, keep, br))
    return out


def static_block_regs(body, lf):
    """A register holding the pointer to the Static block, in every definition of its web, kept over
    a join (fast code: mov ecx, [864AFCh] ... mov word ptr [ecx], ax).  The lift only sees through
    the register in the block it was loaded in.  [ecx + k] is that Static variable; left as it was,
    a store through it printed t_ecx = x, and oldpop1 never changed (DarwinBots Countpop)."""
    mod = getattr(lf.m, "module_static_slot", None) or {}
    cls = lf.m.static_slot.get(lf.obj.index) if lf.obj is not None else None
    if not mod and cls is None:
        return body

    def block(x):
        """The Static block whose pointer variable x is: a function from an offset to its variable."""
        if isinstance(x, Var) and x.space == "global" and x.off in mod:
            b = mod[x.off]
            return lambda off: Var("global", b + off, lf.m.global_name(b + off))
        if isinstance(x, Var) and x.space == "member" and x.off == cls:
            return lambda off: Var("member", lf.STATIC_BASE + off)
        return None
    defs = {}
    for _, ss, _b in body:
        for s in ss:
            if isinstance(s, Assign) and isinstance(s.dst, Var) and s.dst.space in ("reg", "tmp"):
                defs.setdefault(s.dst.key(), []).append(s.src)
    regs = {}
    for k, srcs in defs.items():
        if block(srcs[0]) is not None and len({x.key() for x in srcs}) == 1:
            regs[k] = block(srcs[0])
    if not regs:
        return body

    def f(x):
        if isinstance(x, BinOp) and x.op == "+" and isinstance(x.a, Var) and x.a.key() in regs and \
                isinstance(x.b, Const) and isinstance(x.b.value, int):
            return ir.AddrOf(regs[x.a.key()](x.b.value))    # (the address: ClearMove LastPonderMove)
        if isinstance(x, Var) and x.key() in regs:
            return ir.AddrOf(regs[x.key()](0))
        if not isinstance(x, Deref):
            return None
        e, off = x.e, x.off
        if isinstance(e, BinOp) and e.op == "+" and isinstance(e.b, Const) and \
                isinstance(e.b.value, int):
            e, off = e.a, off + e.b.value
        if isinstance(e, Var) and e.key() in regs:
            v = regs[e.key()](off)
            lf.sizes.setdefault(v.key(), set()).add((x.size, bool(getattr(x, "fp4", False))))
            return v
        return None
    out = []
    for a, ss, br in body:
        keep = []
        for s in ss:
            n = substitute_expr(s, f)
            if isinstance(s, Assign) and isinstance(s.dst, Deref):
                d = s.dst.replace(f)
                if isinstance(d, Var):
                    lf.__dict__.setdefault("store_sizes", {}).setdefault(d.key(), set()).add(s.dst.size)
                    n = keep_flags(Assign(d, s.src.replace(f), s.kind), s)
            keep.append(n)
        out.append((a, keep, type(br)(br.cond.replace(f)) if br is not None else None))
    return out


def apply_locks(body, lf):
    """__arylock(tmp, arr) markers (calls.py arylock): from each lock on,
    tmp stands for arr (element addresses through the locked copy), along
    the flow until another lock of tmp; the markers go."""
    from .ir import subst
    def mark(s):
        return isinstance(s, ExprStmt) and isinstance(s.e, Call) and s.e.name == "__arylock"
    if not any(mark(s) for _, ss, _ in body for s in ss):
        return body
    idx = {a: i for i, (a, _, _) in enumerate(body)}
    preds = {i: [] for i in range(len(body))}
    for i, (a, _, _) in enumerate(body):
        for t in node_succs(lf, a):
            if t in idx:
                preds[idx[t]].append(i)
    CONFLICT = object()

    def meet(states):
        out = {}
        for st in states:
            for k, v in st.items():
                if k in out and out[k] is not CONFLICT and v is not CONFLICT and \
                        out[k].key() != v.key():
                    out[k] = CONFLICT
                elif k not in out or v is CONFLICT:
                    out[k] = v
        return out

    def run_block(i, st):
        st = dict(st)
        for s in body[i][1]:
            if mark(s) and isinstance(s.e.args[0], Var):
                arr = s.e.args[1]
                if isinstance(arr, ir.Deref) and arr.off == 0 and isinstance(arr.e, Var) and \
                        arr.e.space == "arg":
                    arr = arr.e             # *param: the array parameter
                # (a lock of an array inside a locked array's element, rob(n).dna(k): in terms of
                # what the first temporary stands for; Parse a, v48(n).dna(k) had Dim v48)
                m = {k: v for k, v in st.items() if v is not CONFLICT}
                st[s.e.args[0].key()] = subst(arr, m) if m else arr
        return st
    outs = {i: {} for i in range(len(body))}
    for _ in range(len(body) + 2):
        changed = False
        for i in range(len(body)):
            st = run_block(i, meet(outs[p] for p in preds[i]))
            if st.keys() != outs[i].keys() or any(
                    (st[k] is CONFLICT) != (outs[i][k] is CONFLICT) or
                    (st[k] is not CONFLICT and st[k].key() != outs[i][k].key()) for k in st):
                outs[i] = st
                changed = True
        if not changed:
            break
    new = []
    for i, (a, ss, br) in enumerate(body):
        cur = meet(outs[p] for p in preds[i])
        keep = []
        for s in ss:
            if mark(s):
                m = {k: v for k, v in cur.items() if v is not CONFLICT}
                cur = run_block_one(cur, s, subst, m)
                continue
            m = {k: v for k, v in cur.items() if v is not CONFLICT}
            keep.append(substitute_many(s, m) if m else s)
        if br is not None:
            m = {k: v for k, v in cur.items() if v is not CONFLICT}
            br = type(br)(subst(br.cond, m)) if m else br
        new.append((a, keep, br))
    for k in getattr(lf, "sa_locks", {}):
        info = lf.arrays.pop(k, None)       # the temporary is no array of its own
        if info is not None:
            for _, ss, _ in new:
                for s in ss:
                    for e in stmt_exprs(s):
                        for x in e.walk():
                            if isinstance(x, ir.Index) and isinstance(x.arr, Var) and \
                                    x.arr.key() in lf.arrays:
                                tgt = lf.arrays[x.arr.key()]
                                for kk, vv in info.items():
                                    if tgt.get(kk) is None and kk != "var":
                                        tgt[kk] = vv
    return new


def run_block_one(cur, s, subst, m):
    cur = dict(cur)
    arr = s.e.args[1]
    if isinstance(arr, Deref) and arr.off == 0 and isinstance(arr.e, Var) and arr.e.space == "arg":
        arr = arr.e
    if isinstance(s.e.args[0], Var):
        cur[s.e.args[0].key()] = subst(arr, m) if m else arr
    return cur


def note_elem_udt(lf, s):
    """a(i) = r with r of UDT type T: a is an array of T."""
    if not (isinstance(s, Assign) and isinstance(s.dst, ir.Index) and isinstance(s.dst.arr, Var)):
        return
    src = s.src
    if isinstance(src, Deref) and src.off == 0 and isinstance(src.e, ir.AddrOf):
        src = src.e.e
    if isinstance(src, Var) and lf.udt.get(src.key()) and s.dst.arr.key() not in lf.udt:
        lf.udt[s.dst.arr.key()] = lf.udt[src.key()]


def poly(e):
    """e as ({monomial: coefficient}, {atom key: atom}): +, -, * and negation
    expanded, a monomial a sorted tuple of atom keys (() the constant)."""
    atoms = {}

    def go(e):
        if isinstance(e, Const) and isinstance(e.value, int):
            return {(): e.value}
        if isinstance(e, BinOp) and e.op in ("+", "-"):
            out = dict(go(e.a))
            for k, v in go(e.b).items():
                out[k] = out.get(k, 0) + (v if e.op == "+" else -v)
            return out
        if isinstance(e, BinOp) and e.op == "*":
            a, b, out = go(e.a), go(e.b), {}
            for ka, va in a.items():
                for kb, vb in b.items():
                    k = tuple(sorted(ka + kb, key=str))
                    out[k] = out.get(k, 0) + va * vb
            return out
        if isinstance(e, UnOp) and e.op == "-":
            return {k: -v for k, v in go(e.a).items()}
        if isinstance(e, Call) and e.name in ("CLng", "CInt") and len(e.args) == 1 and \
                e.raw is None or isinstance(e, Call) and e.name in ("CLng", "CInt") and \
                len(e.args) == 1 and isinstance(e.raw, str) and e.raw.startswith("__vbaI"):
            return go(e.args[0])            # (address arithmetic is integral anyway)
        if isinstance(e, Deref) and isinstance(e.e, BinOp) and e.e.op == "+" and \
                isinstance(e.e.b, Const) and isinstance(e.e.b.value, int):
            e = Deref(e.e.a, e.size, e.off + e.e.b.value)   # *(sa + 14h) is sa's lLbound
        k = e.key() if hasattr(e, "key") else ("node", id(e))
        atoms[k] = e
        return {(k,): 1}
    return {k: v for k, v in go(e).items() if v}, atoms


def from_poly(p, atoms):
    """The expression of a {monomial: coefficient} of single atoms."""
    e = None
    for k, c in p.items():
        if k == () or not c:
            continue
        t = atoms[k[0]]
        part = t if c == 1 else BinOp("*", t, Const(c))
        e = part if e is None else add(e, part)
    c = p.get((), 0)
    if c:
        e = add(e, Const(c)) if e is not None else Const(c)
    return e if e is not None else Const(0)


def split_inner_const(c, K, inner):
    """For an element with a fixed array inside: (whole elements, field offset, the inner
    index's own constant, inner stride) from the address's constant c, element size K
    and inner terms; stride None when they do not fit."""
    import math
    st = 0
    for v in inner.values():
        st = math.gcd(st, abs(v))
    if st <= 0 or st >= K:
        return 0, 0, 0, None            # (also records: rob(n).Ties(k).x, 68 bytes each)
    # the constant: whole elements, then a field offset (>= 0, the
    # inner array 0-based) or, below zero, the inner index's own
    # (j - 1) * 2 for a 1-based inner array
    # (only a step or two below: 1Eh in a 2Ch element is the field f1E, not a
    # next element's f0(i - 14), which lost f1E's array and declared f0 as Bytes,
    # a target Proc_952490; a subscript below 0 is a run time error too)
    q = c // K
    if K - (c - q * K) <= 2 * st:
        q += 1
    rem = c - q * K
    if rem >= 0:
        field, cin = rem, 0
    else:
        cin = -((-rem + st - 1) // st)
        field = rem - cin * st
    return q, field, cin, st


def dyn_subscripts(p, atoms, sa):
    """(subscripts, element size, offset in the element) when p, a byte
    offset from pvData, is (i - lb) * K + c on the SAFEARRAY sa, or for two
    dimensions ((x - lbX) + nX * (y - lbY)) * K + c: VB keeps the bounds last
    dimension first, x's lLbound at +1Ch and its count at +18h, y's at +14h."""
    lb1, n1, lb2 = (Deref(sa, 4, o).key() for o in (0x14, 0x18, 0x1C))
    n2, lb3 = (Deref(sa, 4, o).key() for o in (0x20, 0x24))
    if (lb3,) in p and len(p) <= 32 and all(len(k) <= 3 for k in p):
        # three dimensions (ReadIntArr3 a(i, j, k): ((k - lb0) * n1 + j - lb1) * n2 + i - lb2): the
        # first subscript has neither count, the second n2, the last both
        K = -p[(lb3,)]
        mid = tuple(sorted((n2, lb2), key=str))
        last = tuple(sorted((n1, n2, lb1), key=str))
        if K < 1 or p.get(mid) != -K or p.get(last) != -K:
            return None
        parts = {0: {}, 1: {}, 2: {}}
        for k, v in p.items():
            if k in ((lb3,), mid, last, ()):
                continue
            if v % K:
                return None
            rest = list(k)
            has1, has2 = n1 in rest, n2 in rest
            if has1:
                rest.remove(n1)
            if has2:
                rest.remove(n2)
            if len(rest) > 1 or has1 and not has2 or not rest and not has2:
                return None
            parts[2 if has1 else 1 if has2 else 0][tuple(rest)] = v // K
        q, r = divmod(p.get((), 0), K)
        if q:
            parts[0][()] = q
        if any(b in kk for d in parts.values() for kk in d for b in (lb1, lb2, lb3, n1, n2)):
            return None
        return [from_poly(parts[i], atoms) for i in range(3)], K, r
    if len(p) > 24 or any(len(k) > 2 for k in p):
        return None
    subs = []
    if (lb2,) in p:                         # two dimensions
        K = -p[(lb2,)]
        nlb = tuple(sorted((n1, lb1), key=str))
        if K < 1 or p.get(nlb) != -K:       # (K = 1: a Byte array)
            return None
        x, y, inner2 = {}, {}, {}
        for k, v in p.items():
            if k in ((lb2,), nlb, ()):
                continue
            if v % K:
                if len(k) != 1 or n1 in k:
                    return None
                inner2[k] = v               # an index into a fixed array inside it
                continue
            if n1 in k:
                rest = list(k)
                rest.remove(n1)
                if len(rest) > 1:
                    return None
                y[tuple(rest)] = y.get(tuple(rest), 0) + v // K
            elif len(k) == 1:
                x[k] = v // K
            else:
                return None
        if inner2:
            q, field, cin, st = split_inner_const(p.get((), 0), K, inner2)
            if st is None:
                return None
            if q:
                x[()] = q
            iidx = {k: v // st for k, v in inner2.items()}
            if cin:
                iidx[()] = cin
            if any(lb1 in k or lb2 in k or n1 in k for k in list(x) + list(y)):
                return None
            return [from_poly(x, atoms), from_poly(y, atoms)], K, field, (from_poly(iidx, atoms), st)
        q, r = divmod(p.get((), 0), K)
        if q:
            x[()] = q
        # (no y: a constant 0 subscript; compiled as 0 - lb, a target Form12
        # m3C(v2C - m3C.f1C + m3C.f18 * (0 - m3C.f14)) is m3C(v2C, 0))
        if any(lb1 in k or lb2 in k or n1 in k for k in list(x) + list(y)):
            return None
        return [from_poly(x, atoms), from_poly(y, atoms)], K, r
    if (lb1,) in p:                         # one dimension
        K = -p[(lb1,)]
        if K <= 1:
            return None
        x, inner = {}, {}
        for k, v in p.items():
            if k in ((lb1,), ()):
                continue
            if len(k) != 1 or n1 in k:
                return None
            if v % K:
                inner[k] = v                # an index into a fixed array inside it
            else:
                x[k] = v // K
        c = p.get((), 0)
        if not inner:
            q, r = divmod(c, K)
            if q:
                x[()] = q
            return [from_poly(x, atoms)], K, r
        q, field, cin, st = split_inner_const(c, K, inner)
        if st is None:
            return None
        if q:
            x[()] = q
        iidx = {k: v // st for k, v in inner.items()}
        if cin:
            iidx[()] = cin
        return [from_poly(x, atoms)], K, field, (from_poly(iidx, atoms), st)
    return None


def dyn_at_address(lf, ptr, off, size):
    """The element, or its field, at pvData + (subscripts as the code has them) + off: *Index(a, subs)
    with the field's offset, or None.  One or several dimensions."""
    p, atoms = poly(ptr)
    base = [atoms[k[0]] for k, c in p.items() if len(k) == 1 and c == 1 and
            isinstance(atoms[k[0]], Deref) and atoms[k[0]].off == 0x0c and
            atoms[k[0]].size == 4]
    if len(base) != 1:
        return None
    b = base[0].e
    if isinstance(b, Var):
        sa, arr = b, b
    elif isinstance(b, Deref) and b.off == 0 and isinstance(b.e, Var) and b.e.space == "arg":
        sa, arr = b, b.e                    # an array parameter: *param
    else:
        return None
    p = dict(p)
    del p[(base[0].key(),)]
    if off:
        p[()] = p.get((), 0) + off
    got = dyn_subscripts(p, atoms, sa)
    if got is None or len(got) > 3:
        return None
    subs, K, r = got[:3]
    info = lf.arrays.setdefault(arr.key(), {"size": K, "dyn": True, "lb": "dyn", "n": None,
                                            "var": arr})
    if not info.get("dyn") or (info.get("size") or 0) > K:
        return None
    info["size"] = K
    el = ir.Index(arr, subs)
    el.vbtype = None
    return Deref(el, size, r)


def fold_offset_slots(body, lf):
    """An offset (i - lb) in a slot of its own, where what i is made of changes elsewhere in the
    procedure (o = v30 - lb: v30 = v30 + 1 on the next way round).  Where the slot is read and nothing
    in it has changed since, it can read what it was set from, and the element shows: v(v30) = s, not
    (v.fC + o * 4) = s.  A forward flow over each such slot."""
    defs = {}
    for bi, (_, ss, _b) in enumerate(body):
        for si, s in enumerate(ss):
            if isinstance(s, Assign) and isinstance(s.dst, Var) and (
                    s.dst.space in ("reg", "tmp") or s.dst.space == "local" and s.dst.off not in lf.kept):
                defs.setdefault(s.dst.key(), []).append((bi, si, s))
    cands = {}
    for k, ds in defs.items():
        if len(ds) != 1 or k in lf.arrays or lf.udt.get(k):
            continue
        src = ds[0][2].src
        if any(isinstance(y, Call) for y in src.walk()):
            continue                        # (a Variant's CLng is a call: see below)
        # (of a dynamic array: a record's field at 14h is no lower bound, and its read may not move past
        # a store.  t = a.f14: a.f14 = b.f14: b.f14 = t lost the swap, ChessBrainVB SwapMove)
        if any(isinstance(y, Deref) and y.off == 0x14 and y.size == 4 and isinstance(y.e, Var) and
               (lf.arrays.get(y.e.key()) or {}).get("dyn") for y in src.walk()):
            cands[k] = ds[0]
    if not cands:
        return body
    idx = {a: i for i, (a, _, _) in enumerate(body)}
    preds = {i: [] for i in range(len(body))}
    for i, (a, _, _) in enumerate(body):
        for t in node_succs(lf, a):
            if t in idx:
                preds[idx[t]].append(i)
    out = body
    for k, (dbi, dsi, d) in cands.items():
        names = {y.key() for y in d.src.walk() if isinstance(y, Var)}
        impure_kills = any(isinstance(y, Var) and y.space in ("global", "member") for y in d.src.walk())

        def kills(s):
            if isinstance(s, Assign) and isinstance(s.dst, Var) and (s.dst.key() in names or s.dst.key() == k):
                return True
            for e in stmt_exprs(s):
                for y in e.walk():
                    if isinstance(y, Call) and (y.name in ("ReDim", "ReDim Preserve", "Erase") or
                                                impure_kills and not y.pure):
                        return True
                    if isinstance(y, ir.AddrOf) and isinstance(y.e, Var) and y.e.key() in names:
                        return True
            return False
        n = len(out)
        avail_out = [True] * n              # (optimistic, the entry block aside)
        changed = True
        while changed:
            changed = False
            for i in range(n):
                cur = bool(preds[i]) and all(avail_out[p] for p in preds[i])
                for j, s in enumerate(out[i][1]):
                    if i == dbi and j == dsi:
                        cur = True
                    elif kills(s):
                        cur = False
                if cur != avail_out[i]:
                    avail_out[i] = cur
                    changed = True
        new, left = [], 0

        def g(y):
            return d.src if isinstance(y, Var) and y.key() == k else None
        for i, (a, ss, br) in enumerate(out):
            cur = bool(preds[i]) and all(avail_out[p] for p in preds[i])
            keep = []
            for j, s in enumerate(ss):
                if i == dbi and j == dsi:
                    keep.append(s)
                    cur = True
                    continue
                reads = any(isinstance(y, Var) and y.key() == k for e in stmt_exprs(s)
                            if not (isinstance(s, Assign) and e is s.dst and isinstance(e, Var))
                            for y in e.walk())
                if reads and cur:
                    keep.append(substitute_expr(s, g))
                else:
                    left += reads
                    keep.append(s)
                if kills(s):
                    cur = False
            if br is not None and any(isinstance(y, Var) and y.key() == k for y in br.cond.walk()):
                if cur:
                    br = type(br)(br.cond.replace(g))
                else:
                    left += 1
            new.append((a, keep, br))
        if not left:
            new = [(a, [s for j, s in enumerate(ss) if not (i == dbi and j == dsi)], br)
                   for i, (a, ss, br) in enumerate(new)]
            # (statement numbers in the other candidates' defs moved)
            cands = {q: (b_, (s_ - 1 if b_ == dbi and s_ > dsi else s_), st) for q, (b_, s_, st) in cands.items()}
        out = new
    return out


def fold_dyn_leftovers(body, lf):
    """What no other fold could tie to a subscript, as VB can still say it: *(pvData + o * K + c) of a
    one-dimensional dynamic array is a(o + LBound(a)), with its field; the bound itself LBound(a).  o
    holds the offset the compiler computed, whatever it came from."""
    def array_of(sa):
        if isinstance(sa, Deref) and sa.off == 0 and isinstance(sa.e, Var) and sa.e.space == "arg":
            sa = sa.e
        if not isinstance(sa, Var):
            return None
        info = lf.arrays.get(sa.key())
        if not info or not info.get("dyn") or len(info.get("dims") or ()) > 1:
            return None
        return sa, info

    def f(x):
        if isinstance(x, Deref) and x.off == 0x14 and x.size == 4 and array_of(x.e) is not None:
            return Call("LBound", [array_of(x.e)[0]], "Long", pure=True)
        if not (isinstance(x, Deref) and isinstance(x.e, BinOp) and x.e.op in ("+", "-")):
            return None
        lin = lf.linear(x.e)
        base = [(kk, e) for kk, (e, c) in lin.items() if kk is not None and c == 1 and
                isinstance(e, Deref) and e.off == 0xc and e.size == 4 and array_of(e.e) is not None]
        if len(base) != 1:
            return None
        kk0, pv = base[0]
        arr, info = array_of(pv.e)
        rest = [(e, c) for kk, (e, c) in lin.items() if kk not in (None, kk0) and c]
        K = info.get("size") or 0
        if not rest or K < 1 or any(c % K for _, c in rest):
            return None
        const = lin.get(None, [None, 0])[1] + x.off
        k0, field = divmod(const, K)
        # ((i - a.f14) * K is the subscript i itself, no LBound(a) * -1 + i + LBound(a))
        own = [t for t in rest if t[1] == -K and isinstance(t[0], Deref) and t[0].off == 0x14 and
               t[0].size == 4 and array_of(t[0].e) is not None and
               array_of(t[0].e)[0].key() == arr.key()]
        idx = None if own else Call("LBound", [arr], "Long", pure=True)
        for e, c in rest:
            if own and e is own[0][0]:
                continue
            part = e.replace(f)
            part = part if c == K else BinOp("*", part, Const(c // K))
            idx = part if idx is None else BinOp("+", part, idx)
        if idx is None:
            idx = Const(0)
        if k0:
            idx = BinOp("+", idx, Const(k0))
        el = ir.Index(arr, [idx])
        el.vbtype = None
        return Deref(el, x.size, field) if lf.udt.get(arr.key()) or field or x.size != K else el
    def address(x):
        """The same address kept in a variable (a Variant's by-reference payload, printed v.fC + o * 4)."""
        if isinstance(x, BinOp) and x.op in ("+", "-"):
            d = f(Deref(x, 4, 0))
            el = d.e if isinstance(d, Deref) and not d.off else d
            if isinstance(el, ir.Index):
                return ir.AddrOf(el)
        return None
    out = []
    for a, ss, br in body:
        keep = []
        for s in ss:
            if isinstance(s, Assign) and isinstance(s.dst, Var) and address(s.src) is not None:
                keep.append(keep_flags(Assign(s.dst, address(s.src), s.kind), s))
            else:
                keep.append(substitute_expr(s, f))
        out.append((a, keep, type(br)(br.cond.replace(f)) if br is not None else None))
    return out


def refold_dyn_index(body, lf):
    """Dynamic array elements the lift saw only as address arithmetic:
    optimised code with bounds checks keeps the offset (i - lb) * K in a
    frame slot across the check's join and reads [pvData + slot + c], so
    the lift had no lLbound to see.  Inlined, the descriptor's own fields
    show the element: Index(a, [byte offset]) of an array the lift read as
    access-sized elements, or *(pvData + offset) itself, becomes a(i) or
    a(x, y) and the field c bytes into it (a target: 1061 lines read the
    descriptor by hand - Form7's global_F050A8((2 - global_F050A8.f14) *
    76 + 28).pad1(1) is global_F050A8(2).f1C)."""
    # an offset kept in a register over a loop, one value however often it is
    # recomputed: t = (1 - a.f14) * 76 ... a(t + 29) (Form7's Proc_9A77F0)
    srcs, offs, inlined = {}, {}, set()
    for _, ss, _ in body:
        for s in ss:
            if isinstance(s, Assign) and isinstance(s.dst, Var) and (
                    s.dst.space in ("reg", "tmp") or
                    s.dst.space == "local" and s.dst.off not in lf.kept):
                srcs.setdefault(s.dst.key(), set()).add(s.src.key())
                # (the offset; or the data pointer, kept as well: o1012 = rob.fC, DarwinBots)
                if any(isinstance(y, Deref) and y.off in (0x14, 0xc) and y.size == 4
                       for y in s.src.walk()):
                    offs[s.dst.key()] = s.src
                elif isinstance(s.src, Var) and s.src.key() in offs:
                    offs[s.dst.key()] = offs[s.src.key()]   # (a copy of one: t2 = t_edi)
    assigned = {s.dst.key() for _, ss, _ in body for s in ss
                if isinstance(s, Assign) and isinstance(s.dst, Var)}
    offs = {k: v for k, v in offs.items() if len(srcs.get(k, ())) == 1 and
            not any(isinstance(y, Var) and y.key() in assigned for y in v.walk())}

    def expand_offsets(e):
        hit = set()

        def g(y):
            if isinstance(y, Var) and y.key() in offs:
                hit.add(y.key())
                return offs[y.key()]
            return None
        return (e.replace(g), hit) if offs else (e, hit)

    def sa_and_arr(base):
        if isinstance(base, Var):
            return base, base
        if isinstance(base, Deref) and base.off == 0 and isinstance(base.e, Var) and \
                base.e.space == "arg":
            return base, base.e                 # an array parameter: *param
        if isinstance(base, Deref) and base.size == 4 and isinstance(base.e, ir.Index):
            return base, base                   # an array in a field of a record's element
        return None, None

    def element(arr, sa, p, atoms, size):
        got = dyn_subscripts(p, atoms, sa)
        if got is None:
            return None
        subs, K, r = got[:3]
        info = lf.arrays.setdefault(arr.key(), {"size": K, "dyn": True, "lb": "dyn",
                                                "n": None, "var": arr})
        if not info.get("dyn") or (info.get("size") or 0) > K:
            return None
        info["size"] = K
        el = ir.Index(arr, subs)
        el.vbtype = None
        if len(got) > 3:
            # a fixed array inside the element (Form2 m40(i).f0(j - 1))
            return lf.inline_field(el, r, got[3], size)
        return Deref(el, size, r)

    def at_address(ptr, off, size):
        """The element (field) at pvData + offset + off, or None."""
        ptr2, hit = expand_offsets(ptr)     # (i - lb) kept in a slot or register
        p, atoms = poly(ptr2)
        base = [atoms[k[0]] for k, c in p.items() if len(k) == 1 and c == 1 and
                isinstance(atoms[k[0]], Deref) and atoms[k[0]].off == 0x0c and
                atoms[k[0]].size == 4]
        if len(base) != 1:
            return None
        sa, arr = sa_and_arr(base[0].e)
        if sa is None:
            return None
        p = dict(p)
        del p[(base[0].key(),)]
        if off:
            p[()] = p.get((), 0) + off
        el = element(arr, sa, p, atoms, size)
        if el is not None:
            inlined.update(hit)
        return el

    def fix(x):
        if isinstance(x, ir.Index) and isinstance(x.arr, BinOp) and len(x.idx) == 1 and \
                any(isinstance(y, Deref) and y.off == 0xc for e in (x.arr, x.idx[0]) for y in e.walk()) and \
                any(isinstance(y, Deref) and y.off == 0x14 or isinstance(y, Call) and y.name == "LBound"
                    for e in (x.arr, x.idx[0]) for y in e.walk()):
            # [slot + pvData + 1] with (i - lb) * 3 in the slot: lifted as if the slot held an array's
            # data, and the slot's value inlined (a target UserControl1's RGB triples,
            # ((v18 - v2C.f14) * 3)(v2C.fC + 2) for v2C(v18).f2).  (Only with the bound in it: an
            # array in a record's element is an Index on a sum too, t90optwith .mem(i))
            d = at_address(BinOp("+", x.arr, x.idx[0]), 0, 1)
            if d is not None:
                return d
        if isinstance(x, Call) and isinstance(x.raw, int):
            # an element's address given to a decompiled procedure ByRef
            # (two dimensions: Proc_E8A480 a1.fC + ((v2C - a1.f1C) + a1.f18 *
            # (v24 - a1.f14)) * 4, a2, ...)
            args, hit = [], False
            for a in x.args:
                if isinstance(a, BinOp) and a.op in ("+", "-"):
                    d = at_address(a, 0, 4)
                    if d is not None:
                        args.append(ir.AddrOf(d.e if d.off == 0 else d))
                        hit = True
                        continue
                args.append(a.replace(fix))
            if hit:
                obj = x.obj.replace(fix) if x.obj is not None else None
                return x._keep(Call(x.name, args, x.vbtype, x.raw, x.method, obj, x.pure))
            return None
        if isinstance(x, Call) and (x.name in ("Put", "Get") and
                                    getattr(x, "stmt_fmt", None) or x.name == "LSet"):
            # an element by its address: Put #f, n, a(i) / LSet a(i) = v
            args, hit = list(x.args), False
            for i in ([len(args) - 1] if x.name != "LSet" else [0, 1]):
                a = args[i]
                if isinstance(a, BinOp) and a.op in ("+", "-"):
                    d = at_address(a, 0, 4)
                    if d is not None:
                        args[i] = ir.AddrOf(d.e if d.off == 0 else d)
                        hit = True
            if not hit:
                return None
            args = [y.replace(fix) for y in args]
            return x._keep(Call(x.name, args, x.vbtype, x.raw, x.method, x.obj, x.pure))
        if isinstance(x, Deref) and isinstance(x.e, ir.Index) and \
                x.off >= (getattr(x.e, "unit", None) or 1 << 30):
            # the element read, an address, and a read through it: an array first in the record,
            # its descriptor's fields (Buckets(x, y).arr(a), DarwinBots Delete_Bot, printed
            # (G(x, y).fC + (a - G(x, y).f14) * 2) of a descriptor it took for the record)
            d = fix(x.e)
            return x._keep(Deref(d, x.size, x.off)) if d is not None else None
        if isinstance(x, Deref) and isinstance(x.e, ir.Index):
            ix, size, off = x.e, x.size, x.off
        elif isinstance(x, ir.Index):
            ix, size, off = x, None, 0
        elif isinstance(x, Deref) and isinstance(x.e, BinOp) and x.e.op in ("+", "-"):
            d = at_address(x.e, x.off, x.size)          # *(pvData + offset): no Index made
            if d is not None and getattr(x, "fixstr", None):
                d.fixstr = x.fixstr     # (a String * n read: udt.array_element_types)
                if d.off == 0 and isinstance(d.e, ir.Index):
                    d.e.fixstr = x.fixstr
            if d is not None and x.off == 0 and d.off == 0 and isinstance(d.e, ir.Index) and \
                    x.size > 4 and isinstance(d.e.arr, Var) and \
                    (lf.arrays.get(d.e.arr.key()) or {}).get("size") == x.size:
                # all of it: a record's copy, mR(2, 2) = mR(2, 1) printed .f0 = .f0
                return d.e
            return d
        else:
            return None
        unit = getattr(ix, "unit", None)
        arr = ix.arr
        if not isinstance(arr, Var) or len(ix.idx) != 1:
            return None
        info = lf.arrays.get(arr.key())
        if not info or not info.get("dyn"):
            return None
        # without the lift's unit, the access size when that makes the
        # array's known element size (a word read of a 12-byte element:
        # m38((t - m38.f14) * 6 + 5).f0, a form's own array)
        units = [unit] if unit else ([size] if size else
                                     # a bare element stored: its element size
                                     [info["size"]] if 0 < (info.get("size") or 0) <= 8
                                     else [])
        idx0, hit = expand_offsets(ix.idx[0])
        for u in units:
            p, atoms = poly(idx0)
            p = {k: v * u for k, v in p.items()}
            if off:
                p[()] = p.get((), 0) + off
            for sa in (arr, Deref(arr, 4, 0)):
                got = dyn_subscripts(p, atoms, sa)
                if got is None or not unit and info.get("size") and got[1] != info["size"]:
                    continue
                el = element(arr, sa, p, atoms, size if size is not None else u)
                if el is not None:
                    inlined.update(hit)
                    return el
        return None
    out = []
    for a, ss, br in body:
        ss = [substitute_expr(st, fix) for st in ss]
        if br is not None:
            br = type(br)(br.cond.replace(fix))
        out.append((a, ss, br))
    if inlined:
        # the offset registers nothing reads any more
        left = {k for k in inlined if any(
            isinstance(y, Var) and y.key() == k for _, ss, br in out for s in ss
            for e in stmt_exprs(s) if not (isinstance(s, Assign) and e is s.dst and
                                           isinstance(e, Var))     # (*(p + k) = x reads p)
            for y in e.walk()) or any(
            isinstance(y, Var) and y.key() == k for _, _, br in out if br is not None
            for y in br.cond.walk())}
        out = [(a, [s for s in ss if not (isinstance(s, Assign) and isinstance(s.dst, Var) and
                                          s.dst.key() in inlined - left)], br)
               for a, ss, br in out]
    return out


def array_bases_in_registers(body, lf):
    """Optimised code keeps an array's SAFEARRAY pointer in a register across blocks and reads
    its bounds and data through it: t_ecx = a8 ... (a8.fC + (x - t_ecx.f14) * 4), a target
    Proc_7E1470, A4 MoveRow.  Where every definition reaching such a read holds the
    same array (following register copies), the read is of that array, and the element folds."""
    def array_form(e):
        if isinstance(e, Var) and e.space in ("member", "global", "local", "arg"):
            return e if (lf.arrays.get(e.key()) or {}).get("dyn") else None
        if isinstance(e, Deref) and e.off == 0 and isinstance(e.e, Var) and e.e.space == "arg":
            # (only called for a register read as a SAFEARRAY: the parameter is an array, also
            # when nothing else shows it - DarwinBots a1.f0.fC + (i - t1.f14) * 4)
            lf.arrays.setdefault(e.e.key(), {"size": None, "dyn": True, "lb": "dyn", "n": None,
                                             "var": e.e})
            return e if lf.arrays[e.e.key()].get("dyn") else None
        if isinstance(e, Deref) and e.size == 4 and isinstance(e.e, ir.Index):
            return e                        # an array in a record element's field
        return None

    def is_reg(e):
        return isinstance(e, Var) and e.space in ("reg", "tmp")
    # a parameter whose lower bound is read through its pointer is an array parameter, even when
    # every element goes through a register (Proc_7E1470 a7 came out As Long: a7.f14)
    for _, ss, br in body:
        for s in ss:
            for e in stmt_exprs(s):
                for y in e.walk():
                    if isinstance(y, Deref) and y.off == 0x14 and y.size == 4 and \
                            isinstance(y.e, Deref) and y.e.off == 0 and isinstance(y.e.e, Var) and \
                            y.e.e.space == "arg":
                        lf.arrays.setdefault(y.e.e.key(), {"size": None, "dyn": True, "lb": "dyn",
                                                           "n": None, "var": y.e.e})
    used = {y.e.key() for _, ss, br in body for s in ss for e in stmt_exprs(s) for y in e.walk()
            if isinstance(y, Deref) and is_reg(y.e) and y.off in (0xc, 0x10, 0x14, 0x18, 0x1c)}
    used |= {y.e.key() for _, ss, br in body if br is not None for y in br.cond.walk()
             if isinstance(y, Deref) and is_reg(y.e) and y.off in (0xc, 0x10, 0x14, 0x18, 0x1c)}

    def bound_of_reg(y):
        # (UBound(t_ecx): the array read into a register for it, DarwinBots CalcStats t13)
        return isinstance(y, Call) and y.name in ("UBound", "LBound") and y.args and is_reg(y.args[0])
    used |= {y.args[0].key() for _, ss, br in body for s in ss for e in stmt_exprs(s)
             for y in e.walk() if bound_of_reg(y)}
    used |= {y.args[0].key() for _, ss, br in body if br is not None for y in br.cond.walk()
             if bound_of_reg(y)}
    # (and an element through it: t_ecx(a1).f3AC, the array loaded in one If's arms: HandleWaste)
    used |= {y.arr.key() for _, ss, br in body for s in ss for e in stmt_exprs(s) for y in e.walk()
             if isinstance(y, ir.Index) and is_reg(y.arr)}
    used |= {y.arr.key() for _, ss, br in body if br is not None for y in br.cond.walk()
             if isinstance(y, ir.Index) and is_reg(y.arr)}
    if not used:
        return body
    idx = {a: i for i, (a, _, _) in enumerate(body)}
    preds = {i: [] for i in range(len(body))}
    for i, (a, _, _) in enumerate(body):
        for t in node_succs(lf, a):
            if t in idx:
                preds[idx[t]].append(i)

    def defs_at(bi, si, key):
        got, seen, work = [], set(), [(bi, si)]
        while work:
            b, upto = work.pop()
            ss = body[b][1]
            for k in range(upto - 1, -1, -1):
                t = ss[k]
                if isinstance(t, Assign) and isinstance(t.dst, Var) and t.dst.key() == key:
                    got.append((b, k))
                    break
            else:
                if not preds[b] or len(seen) > 400:
                    return None             # (from the entry, or too far)
                for p_ in preds[b]:
                    if p_ not in seen:
                        seen.add(p_)
                        work.append((p_, len(body[p_][1])))
        return got

    def holds(bi, si, key, depth=0):
        """The array every definition of key reaching there stores, or None."""
        defs = defs_at(bi, si, key)
        if not defs or depth > 4:
            return None
        found = None
        for b, k in defs:
            src = body[b][1][k].src
            v = array_form(src)
            if v is None and is_reg(src):
                v = holds(b, k, src.key(), depth + 1)
            if v is None or found is not None and v.key() != found.key():
                return None
            found = v
        return found
    def offset_at(bi, si, r):
        """The element offset (i - lb) * K in register r at that point, when every definition
        reaching it computes the same thing right before this block and what it reads does not
        change on the way (Proc_7E1470: a7(v2C) = x in two arms, one store; eax is used
        for much else in the procedure, so fold_dyn_elements' whole-procedure rule does not fit)."""
        defs = defs_at(bi, si, r.key())
        if not defs:
            return None
        srcs = {body[b][1][k].src.key() for b, k in defs}
        if len(srcs) != 1:
            return None
        val = body[defs[0][0]][1][defs[0][1]].src
        if not any(isinstance(y, Deref) and y.off == 0x14 and y.size == 4 for y in val.walk()):
            return None
        deps = {y.key() for y in val.walk() if isinstance(y, Var)}
        if r.key() in deps:
            return None

        def writes(t):
            return isinstance(t, Assign) and isinstance(t.dst, Var) and t.dst.key() in deps
        for b, k in defs:
            if b == bi:
                if any(writes(t) for t in body[bi][1][k + 1:si]):
                    return None
                continue
            if any(writes(t) for t in body[b][1][k + 1:]) or \
                    any(writes(t) for t in body[bi][1][:si]):
                return None
            # (not right before it: an If may come between, Shots(n).color = -x after If x < 0,
            # DarwinBots newshot.  What it reads doesn't change on any way there)
            seen, work = {bi}, [b]
            steps = 0
            while work:
                c = work.pop()
                for t_ in node_succs(lf, body[c][0]):
                    i_ = idx.get(t_)
                    if i_ is None or i_ in seen:
                        continue
                    seen.add(i_)
                    steps += 1
                    if steps > 40 or any(writes(t) for t in body[i_][1]):
                        return None
                    work.append(i_)
        return val

    def element_sum(x):
        """x = *(pvData + r): r as what it computes."""
        if not (isinstance(x, Deref) and isinstance(x.e, BinOp) and x.e.op == "+"):
            return None
        for base, r in ((x.e.a, x.e.b), (x.e.b, x.e.a)):
            if isinstance(base, Deref) and base.off == 0xc and (
                    is_reg(r) or isinstance(r, Var) and r.space == "local" and
                    r.off not in lf.kept):
                return base, r
        return None
    out = []
    for bi, (a, ss, br) in enumerate(body):
        keep = []
        for si, s in enumerate(ss):
            def fix(x, si=si):
                if isinstance(x, Deref) and is_reg(x.e) and x.off in (0xc, 0x10, 0x14, 0x18, 0x1c):
                    v = holds(bi, si, x.e.key())
                    if v is not None:
                        return Deref(v, x.size, x.off)
                if bound_of_reg(x):
                    v = holds(bi, si, x.args[0].key())
                    if v is not None:
                        return x._keep(Call(x.name, [v] + list(x.args[1:]), x.vbtype, x.raw,
                                            x.method, x.obj, x.pure))
                if isinstance(x, ir.Index) and is_reg(x.arr):
                    v = holds(bi, si, x.arr.key())
                    if isinstance(v, (Var, Deref)):
                        return x._keep(ir.Index(v, [i.replace(fix) for i in x.idx]))
                got = element_sum(x)
                if got is not None:
                    val = offset_at(bi, si, got[1])
                    if val is not None:
                        return Deref(BinOp("+", got[0].replace(fix), val), x.size, x.off)
                return None
            keep.append(substitute_expr(s, fix))
        if br is not None:
            def fixb(x):
                if isinstance(x, Deref) and is_reg(x.e) and x.off in (0xc, 0x10, 0x14, 0x18, 0x1c):
                    v = holds(bi, len(ss), x.e.key())
                    if v is not None:
                        return Deref(v, x.size, x.off)
                if bound_of_reg(x):
                    v = holds(bi, len(ss), x.args[0].key())
                    if v is not None:
                        return x._keep(Call(x.name, [v] + list(x.args[1:]), x.vbtype, x.raw,
                                            x.method, x.obj, x.pure))
                if isinstance(x, ir.Index) and is_reg(x.arr):
                    v = holds(bi, len(ss), x.arr.key())
                    if isinstance(v, (Var, Deref)):
                        return x._keep(ir.Index(v, [i.replace(fixb) for i in x.idx]))
                return None
            br = type(br)(br.cond.replace(fixb))
        out.append((a, keep, br))
    return out


def drop_temp_arrays(body, lf):
    """calls.note_array_args took each variable given to a method's array parameter for an
    array.  An array variable isn't assigned to; a compiler's temporary is (t04w: vFC, a pointer
    to a fixed array for the call, then vFC = v24.RetLong: "Can't assign to array")."""
    for k in [k for k, i in lf.arrays.items() if i.get("from_method")]:
        if any(isinstance(s, Assign) and isinstance(s.dst, Var) and s.dst.key() == k
               for _, ss, _ in body for s in ss):
            del lf.arrays[k]
    return body


def frame_offsets(body, lf):
    """Unoptimised code keeps an element's offset in frame slots it uses again for anything
    else, across the bounds check: v228 = i - a.f14, v35C = v228 * 4, [a.fC + v35C] (a target
    Proc_4C48A0 printed v88.f14, for which fold_dyn_elements' one-value rule does not fit).  In
    address order: a slot's latest such value, gone as soon as the slot or the index changes."""
    def local(e):
        return isinstance(e, Var) and e.space == "local"

    def lbound_of(e):
        for y in e.walk():
            if isinstance(y, Deref) and y.off == 0x14 and y.size == 4 and local(y.e) and \
                    (lf.arrays.get(y.e.key()) or {}).get("dyn"):
                return y.e
        return None
    vals, made, used = {}, {}, set()
    out = []
    for a, ss, br in body:
        keep = []
        for s in ss:
            def fix(x):
                if not (isinstance(x, BinOp) and x.op == "+" and isinstance(x.a, Deref) and
                        x.a.off == 0xc and local(x.a.e)):
                    return None
                y = x.b
                # (by now the slot may hold the subscript and the sum scale it, scaled_index_slots,
                # or a single use have the scaling inlined: [a.fC + v398 * 4])
                scale = None
                if isinstance(y, BinOp) and y.op == "*" and isinstance(y.b, Const):
                    y, scale = y.a, y.b
                if local(y) and y.key() in vals and lbound_of(vals[y.key()]) is not None and \
                        lbound_of(vals[y.key()]).key() == x.a.e.key():
                    used.add(y.key())
                    used.update(made.get(y.key(), ()))
                    v = vals[y.key()]
                    return BinOp("+", x.a, v if scale is None else BinOp("*", v, scale))
                return None
            s = substitute_expr(s, fix)
            keep.append(s)
            if not (isinstance(s, Assign) and isinstance(s.dst, Var)):
                continue
            k = s.dst.key()
            for o in [o for o, v in vals.items() if any(isinstance(y, Var) and y.key() == k
                                                        for y in v.walk())]:
                del vals[o]
            vals.pop(k, None)
            made.pop(k, None)
            if not local(s.dst):
                continue
            v = s.src
            if isinstance(v, BinOp) and v.op == "*" and local(v.a) and v.a.key() in vals and \
                    isinstance(v.b, Const):
                made[k] = {v.a.key()}
                vals[k] = BinOp("*", vals[v.a.key()], v.b)
            elif local(v) and v.key() in vals:
                made[k] = {v.key()}     # (v398 = v228, scaled_index_slots took its * 4 away)
                vals[k] = vals[v.key()]
            elif isinstance(v, BinOp) and v.op in ("-", "*") and lbound_of(v) is not None:
                vals[k] = v             # (o1069 = (i - a.f14) * 4, in one go)
        out.append((a, keep, br))
    if not used:
        return body

    def goes(s):
        if not (isinstance(s, Assign) and isinstance(s.dst, Var) and s.dst.key() in used):
            return False
        v = s.src
        return lbound_of(v) is not None or local(v) and v.key() in used or \
            isinstance(v, BinOp) and v.op == "*" and local(v.a) and v.a.key() in used

    def read_by(s):
        return {y.key() for e in stmt_exprs(s)
                if not (isinstance(s, Assign) and e is s.dst and isinstance(e, Var))
                for y in e.walk() if isinstance(y, Var)}
    # the slots' assignments went with their uses, unless a read of what one gave is left: the slot
    # is used for other things, and with the value gone v88(v228 + LBound(v88)) read another's
    # value (a target Proc_4C48A0: 54 subscripts through the cReg object)
    going = {id(s) for a, ss, br in out for s in ss if goes(s)}
    changed = True
    while changed:
        changed = False
        last = {}
        for a, ss, br in out:
            for s in ss:
                if id(s) not in going:
                    for k in read_by(s):
                        if last.get(k) in going:
                            going.discard(last[k])
                            changed = True
                if isinstance(s, Assign) and isinstance(s.dst, Var):
                    last[s.dst.key()] = id(s)
            if br is not None:
                for y in br.cond.walk():
                    if isinstance(y, Var) and last.get(y.key()) in going:
                        going.discard(last[y.key()])
                        changed = True
    return [(a, [s for s in ss if id(s) not in going], br) for a, ss, br in out]


def offset_still_good(body, lf, key, value):
    """An element's offset whose index is a variable, computed in several arms that the
    compiler merged at the store (a target Proc_D09260: v5C(v6C) = vB0 twice, one store): the
    same value at the use if each computation ends its block, right before the block that
    uses it, and nothing it reads changes on the way."""
    deps = {y.key() for y in value.walk() if isinstance(y, Var)}
    if key in deps:
        return False

    def writes(s):
        return isinstance(s, Assign) and isinstance(s.dst, Var) and s.dst.key() in deps

    def reads(s):
        return any(isinstance(y, Var) and y.key() == key for e in stmt_exprs(s)
                   if not (isinstance(s, Assign) and e is s.dst and isinstance(e, Var))
                   for y in e.walk())
    users = [a for a, ss, br in body if any(reads(s) for s in ss) or
             br is not None and any(isinstance(y, Var) and y.key() == key for y in br.cond.walk())]
    if len(users) != 1:
        return False
    use = users[0]
    for a, ss, br in body:
        if a == use:
            for s in ss:
                if reads(s):
                    break
                if writes(s):
                    return False
        for i, s in enumerate(ss):
            if isinstance(s, Assign) and isinstance(s.dst, Var) and s.dst.key() == key:
                n = lf.nodes.get(a)
                if n is None or list(n.succs) != [use] or any(writes(x) for x in ss[i + 1:]):
                    return False
    return True


def hresult_stores(body):
    """x = o.M() of a method with no return value: x is the HRESULT, kept in a frame slot for the check
    that follows (dropped with the check).  The compiler uses the slot for other things as well, so
    no dead store rule gets it (DarwinBots showEyeDesign_Click: v58 = frmEYE.Show, "Expected
    Function or variable")."""
    def sub(e):
        f = getattr(e, "func", None) if isinstance(e, Call) else None
        pars = getattr(f, "params", None)
        return f is not None and getattr(e, "method", False) and pars is not None and \
            not (pars and pars[-1].flags & 8) and getattr(f, "invkind", 1) == 1
    out = []
    for a, ss, br in body:
        keep = []
        for st in ss:
            if isinstance(st, Assign) and isinstance(st.dst, Var) and \
                    st.dst.space in ("local", "reg", "tmp") and sub(st.src):
                n = ExprStmt(st.src)
                n.va = getattr(st, "va", None)
                st = n
            keep.append(st)
        out.append((a, keep, br))
    return out


def unbias_index_regs(body, lf):
    """A register given i - lLbound(a) and nothing else, whatever i is each time: the subscript of a,
    bounds-checked, kept for the next element (t_esi = -a1.f14 ... t_esi = n + 1 - a1.f14, and
    a1.fC + t_esi * 2 in the loop's test: DarwinBots ListCells While lst(w) > 0).  It holds i now,
    and each read is i - lLbound again, for the folds of elements to see."""
    defs = {}
    for _, ss, _b in body:
        for st in ss:
            if isinstance(st, Assign) and isinstance(st.dst, Var) and st.dst.space == "reg":
                defs.setdefault(st.dst.key(), []).append(st)
    found = {}
    for k, sts in defs.items():
        lbs = set()
        for st in sts:
            lin = lf.linear(st.src) if isinstance(st.src, (BinOp, UnOp)) else {}
            one = [(e, c) for key, (e, c) in lin.items() if key is not None and
                   isinstance(e, Deref) and e.off == 0x14 and e.size == 4 and c == -1]
            if len(one) != 1:
                lbs = None
                break
            lbs.add(one[0][0].key())
            lb = one[0][0]
        if lbs and len(lbs) == 1:
            found[k] = lb
    if not found:
        return body

    def changes_array(st, lb):
        e = getattr(st, "e", None)
        return isinstance(e, Call) and e.name in ("ReDim", "ReDim Preserve", "Erase") and \
            any(x.key() == lb.e.key() for a in e.args for x in a.walk() if hasattr(x, "key"))
    found = {k: lb for k, lb in found.items()
             if not any(changes_array(st, lb) for _, ss, _b in body for st in ss)}
    if not found:
        return body

    def without(src, lb):
        """src without its - lb."""
        lin = lf.linear(src)
        t = None
        for key, (e, c) in lin.items():
            if key is None or not c or key == lb.key():
                continue
            part = e if c == 1 else BinOp("*", e, Const(c))
            t = part if t is None else add(t, part)
        c0 = lin.get(None, [None, 0])[1]
        if c0:
            t = add(t, Const(c0)) if t is not None else Const(c0)
        return t if t is not None else Const(0)

    def fix(x):
        if isinstance(x, Var) and x.key() in found:
            return BinOp("-", x, found[x.key()])
        return None
    out = []
    for a, ss, br in body:
        keep = []
        for st in ss:
            if isinstance(st, Assign) and isinstance(st.dst, Var) and st.dst.key() in found:
                n = Assign(st.dst, without(st.src, found[st.dst.key()]).replace(fix))
                keep.append(keep_flags(n, st))
            else:
                keep.append(substitute_expr(st, fix))
        out.append((a, keep, type(br)(br.cond.replace(fix)) if br is not None else None))
    return out


def scaled_index_slots(body, lf):
    """A frame slot given i * size + k and read only as a term of a sum, or as [slot + address in
    .data]: optimised code keeps a scaled subscript there for an element or field it reaches twice
    (DarwinBots LoadSettings_Click: TmpOpts.Specie(i).Native, .Name, which made the slot a record 8
    MB long; DNAaddstore rob(currbot).mem(b): v0 = b * 2 + 11064 made a Byte array of the whole
    record).  The slot now holds i, and each read is i * size + k again, for the folds of elements
    to see."""
    cands, total, forms = {}, {}, {}

    def of_image(off):
        return isinstance(off, int) and lf.im.section_of(off & 0xffffffff) == ".data"

    def slot(x):
        # (a tuple: one statement's name for a pooled slot, rename_statement_temps)
        return isinstance(x, Var) and (x.space == "local" and isinstance(x.off, (int, tuple)) or
                                       x.space in ("reg", "tmp"))

    def form(src):
        """(E, k, c) when src is E * k + c, k > 1."""
        if not isinstance(src, BinOp) or src.op not in ("*", "+", "-"):
            return None
        lin = lf.linear(src)
        c = lin.get(None, [None, 0])[1]
        terms = [(e, q) for key, (e, q) in lin.items() if key is not None and q]
        if len(terms) != 1 or terms[0][1] < 2 or not isinstance(c, int):
            return None
        return terms[0][0], terms[0][1], c
    others = set()
    for _, ss, br in body:
        for st in list(ss) + ([ExprStmt(br.cond)] if br is not None else []):
            if isinstance(st, Assign) and slot(st.dst):
                k = st.dst.key()
                f = form(st.src)
                if f is None or k in forms and forms[k][1:] != f[1:]:
                    others.add(k)
                else:
                    forms[k] = f
            for e in stmt_exprs(st):
                if isinstance(st, Assign) and e is st.dst and isinstance(e, Var):
                    continue
                for x in e.walk():
                    if isinstance(x, Deref) and slot(x.e) and of_image(x.off):
                        cands[x.e.key()] = cands.get(x.e.key(), 0) + 1
                    elif isinstance(x, BinOp) and x.op == "+":
                        for y in (x.a, x.b):
                            if slot(y):
                                cands[y.key()] = cands.get(y.key(), 0) + 1
                    elif isinstance(x, ir.Index) and len(x.idx) == 1 and slot(x.idx[0]):
                        # (a subscript in a module record, at a stride of its own: t_eax = i * 91,
                        # [t_eax * 4 + address], SimOpts.Specie(i).kill_mb in a join's two arms)
                        cands[x.idx[0].key()] = cands.get(x.idx[0].key(), 0) + 1
                    if slot(x):
                        total[x.key()] = total.get(x.key(), 0) + 1
    good = {k for k in cands if k in forms and k not in others and cands[k] == total.get(k) and
            (k[0] != "local" or k[1] not in lf.kept)}
    if not good:
        return body

    def value(v):
        """What the slot held: its new contents scaled back."""
        E, k, c = forms[v.key()]
        t = BinOp("*", v, Const(k))
        return BinOp("+", t, Const(c)) if c else t

    def fix(x):
        if isinstance(x, Deref) and slot(x.e) and x.e.key() in good and of_image(x.off):
            t = value(x.e)
            d = Deref(BinOp("+", t, Const(x.off)), x.size, 0)
            d.vbtype = getattr(x, "vbtype", None)
            return d
        if slot(x) and x.key() in good:
            return value(x)
        return None
    out = []
    for a, ss, br in body:
        keep = []
        for st in ss:
            if isinstance(st, Assign) and isinstance(st.dst, Var) and st.dst.key() in good:
                # (each definition its own: the same multiple of another thing.  All of them with
                # the last one's made lea edx, [edi*4] read another block's edi, unset: ChessBrainVB
                # CheckLegalNotInCheck never ended)
                keep.append(keep_flags(Assign(st.dst, form(st.src)[0]), st))
                continue
            keep.append(substitute_expr(st, fix))
        if br is not None:
            br = type(br)(br.cond.replace(fix))
        out.append((a, keep, br))
    return out


def member_element_parts(body, lf):
    """A fixed array among an object's members (decompile.member_extents: the bounds-checked spans of
    all its procedures), reached in the ways optimised code reaches it: [Me + (i + 1) * 8 + 78h] is
    m80(i) when lifted, [Me + 398h] a member of its own, and a Double copied in two dwords has its
    upper half in m7C(i).  All are elements of m78 (Form1 QStack.val: four arrays that did not share
    their values).  Two halves stored together become one store of the element."""
    if lf.obj is None:
        return body
    ext = lf.m.__dict__.get("member_extents", {}).get(lf.obj.index)
    if not ext:
        return body

    def locate(off):
        for base, stride, n in ext:
            if base <= off < base + n * stride:
                k, rest = divmod(off - base, stride)
                return base, stride, k, rest
        return None

    def element(base, idx, rest, vbtype=None):
        el = ir.Index(Var("member", base), [idx])
        el.vbtype = vbtype
        if rest:
            d = Deref(el, 4, rest)
            d.upper_half = True
            return d
        return el

    def fix(x):
        if isinstance(x, ir.AddrOf) and isinstance(x.e, Var):
            return x
        if isinstance(x, Deref) and isinstance(x.e, Var):
            return x
        if isinstance(x, ir.Index) and isinstance(x.arr, Var) and x.arr.space == "member" and \
                isinstance(x.arr.off, int) and len(x.idx) == 1:
            loc = locate(x.arr.off)
            idx = x.idx[0].replace(fix)
            if loc is None or not loc[2] and not loc[3]:
                return x if idx is x.idx[0] else x._keep(ir.Index(x.arr, [idx]))
            return element(loc[0], add(idx, Const(loc[2])) if loc[2] else idx, loc[3],
                           getattr(x, "vbtype", None))
        if isinstance(x, Var) and x.space == "member" and isinstance(x.off, int):
            loc = locate(x.off)
            if loc is None or loc[1] <= 4 and not (loc[2] or loc[3]):
                return None
            return element(loc[0], Const(loc[2]), loc[3])
        return None

    def parts(s):
        d = s.dst
        if isinstance(d, Var):
            d = d.replace(fix)
        else:
            d = d.replace(fix)
        return d, s.src.replace(fix)
    changed = False
    out = []
    for a, ss, br in body:
        keep = []
        for s in ss:
            if isinstance(s, Assign):
                d, src = parts(s)
                n = keep_flags(Assign(d, src, s.kind), s)
                changed = changed or d is not s.dst or src is not s.src
            else:
                n = substitute_expr(s, fix)
            keep.append(n)
        out.append((a, keep, type(br)(br.cond.replace(fix)) if br is not None else None))
    return merge_element_halves(out, lf)


def merge_element_halves(body, lf):
    """e = x, e.upper = y for an 8-byte element e (member_element_parts): x's two halves, y the upper
    one of x, make one store: m78(i) = m80(i), a1 = m78(i), m78(i) = a2."""
    def upper_of(hi, lo):
        """Is hi the upper half of the 8 bytes lo starts?"""
        if getattr(hi, "upper_half", False) and isinstance(hi, Deref) and hi.e.key() == lo.key():
            return True
        if isinstance(hi, Var) and isinstance(lo, Var) and hi.space == lo.space and \
                hi.space in ("arg", "local", "member", "global") and \
                isinstance(hi.off, int) and isinstance(lo.off, int) and hi.off == lo.off + 4:
            return True
        if isinstance(hi, Deref) and isinstance(lo, Deref) and hi.size == 4 == lo.size and \
                hi.off == lo.off + 4 and hi.e.key() == lo.e.key():
            return True
        return isinstance(lo, Const) and isinstance(hi, Const) and hi.value == 0 == lo.value
    out = []
    for a, ss, br in body:
        ss = list(ss)
        for i, s in enumerate(ss):
            if s is None or not isinstance(s, Assign):
                continue
            for j in range(i + 1, min(i + 3, len(ss))):
                t = ss[j]
                if t is None or not isinstance(t, Assign):
                    continue
                for lo, hi in ((s, t), (t, s)):
                    if getattr(hi.dst, "upper_half", False) and isinstance(hi.dst, Deref):
                        if hi.dst.e.key() != lo.dst.key() or not upper_of(hi.src, lo.src):
                            continue
                        n = Assign(lo.dst, lo.src)
                    elif getattr(hi.src, "upper_half", False) and isinstance(hi.dst, Deref) and \
                            isinstance(lo.dst, Deref) and hi.dst.off == lo.dst.off + 4 and \
                            hi.dst.e.key() == lo.dst.e.key() and upper_of(hi.src, lo.src):
                        nd = Deref(lo.dst.e, 8, lo.dst.off)
                        nd.vbtype = "Double"
                        n = Assign(nd, lo.src)
                    else:
                        continue
                    if isinstance(n.src, Const) and n.src.value == 0:
                        n.src = Const(0.0)
                    if isinstance(n.src, Var) and n.src.space == "arg" and \
                            isinstance(n.src.off, int):
                        lf.__dict__.setdefault("qword_args", set()).add(n.src.off)
                    ss[i], ss[j] = keep_flags(n, lo), None
                    break
                else:
                    continue
                break
        out.append((a, [x for x in ss if x is not None], br))
    return out


def element_address_slots(body, lf):
    """A frame slot given the address of a field of an array element and only read before anything
    in that address changes, in the same block: the compiler's temporary for an argument it passes
    by reference twice (t85recretno, in With olio(n): mov [ebp-16Ch], eax; ... mov eax, [ebp-16Ch]).
    The address itself goes to where it is read, for the element to be seen there."""
    def slot(e):
        return (isinstance(e, Var) and e.space == "local") or \
            (isinstance(e, Deref) and isinstance(e.e, Var) and e.e.space == "local")

    def addr(e):
        return isinstance(e, BinOp) and e.op == "+" and (
            lf.dyn_element(e) is not None or lf.dyn_field(e) is not None)
    cands = [(i, j, s) for i, (_, ss, _) in enumerate(body) for j, s in enumerate(ss)
             if isinstance(s, Assign) and slot(s.dst) and addr(s.src)]
    if not cands:
        return body
    reads = {}
    for i, (_, ss, br) in enumerate(body):
        for j, s in enumerate(ss):
            for e in stmt_exprs(s):
                for x in e.walk():
                    if isinstance(x, (Var, Deref)) and slot(x) and not (isinstance(s, Assign) and
                                                                        x is s.dst):
                        reads.setdefault(x.key(), []).append((i, j))
        if br is not None:
            for x in br.cond.walk():
                if isinstance(x, (Var, Deref)) and slot(x):
                    reads.setdefault(x.key(), []).append((i, len(ss)))
    out = list(body)
    for i, j, s in cands:
        k = s.dst.key()
        rs = reads.get(k, [])
        # (a Var's own reads count as reads of the struct holding it)
        if not rs or any(r[0] != i or r[1] <= j for r in rs):
            continue
        a, ss, br = out[i]
        last = max(r[1] for r in rs)
        if last >= len(ss):
            continue
        names = {x.key() for x in s.src.walk() if isinstance(x, Var)}
        ok = True
        for t in ss[j + 1:last]:
            d = getattr(t, "dst", None)
            if isinstance(t, Assign) and (isinstance(d, Var) and d.key() in names or d == s.dst):
                ok = False
        if not ok:
            continue

        def g(y, k=k, v=s.src):
            return v if isinstance(y, (Var, Deref)) and y.key() == k else None
        new = list(ss)
        for n in range(j + 1, last + 1):
            new[n] = substitute_expr(new[n], g)
        new[j] = None
        out[i] = (a, new, br)
    return [(a, [x for x in ss if x is not None], br) for a, ss, br in out]


def fold_dyn_elements(body, lf):
    """Stores through pvData + (index - lLbound) * size whose scratch
    temporaries are now inlined: the dynamic array element."""
    # offsets the compiler kept in a register: t = (i - lLbound(a)) ... [pvData(a) + t]
    offs, srcs = {}, {}
    for _, ss, _ in body:
        for s in ss:
            if isinstance(s, Assign) and isinstance(s.dst, Var) and (
                    s.dst.space in ("reg", "tmp") or
                    # (or a frame slot of the compiler's: o2002 = robfocus - rob.f14, DarwinBots)
                    s.dst.space == "local" and s.dst.off not in lf.kept):
                srcs.setdefault(s.dst.key(), set()).add(s.src.key())
                # (the offset; or the data pointer, kept as well: o1012 = rob.fC, DarwinBots)
                if any(isinstance(y, Deref) and y.off in (0x14, 0xc) and y.size == 4
                       for y in s.src.walk()):
                    offs[s.dst.key()] = s.src
                elif isinstance(s.src, Var) and s.src.key() in offs:
                    offs[s.dst.key()] = offs[s.src.key()]   # (a copy of one: t2 = t_edi)
    # one value however many times it is computed (once per branch), not
    # depending on itself or on anything assigned in the procedure
    assigned = {s.dst.key() for _, ss, _ in body for s in ss
                if isinstance(s, Assign) and isinstance(s.dst, Var)}
    offs = {k: v for k, v in offs.items() if len(srcs.get(k, ())) == 1 and
            (not any(isinstance(y, Var) and y.key() in assigned for y in v.walk()) or
             offset_still_good(body, lf, k, v))}
    used = set()

    def expand(e):
        hit = []

        def g(y):
            if isinstance(y, Var) and y.key() in offs:
                hit.append(y.key())
                return offs[y.key()]
            return None
        return e.replace(g), hit

    def fix(x):
        if isinstance(x, Call) and x.name in ("StrPtr", "VarPtr") and len(x.args) == 1:
            # the address of an element or a UDT field: VarPtr(a(i)), VarPtr(u.f)
            # (StrPtr/VarPtr are one export; StrPtr only takes a String)
            e = x.args[0]
            tgt = None
            info = lf.arrays.get(e.key()) if isinstance(e, Var) else None
            if info and not info.get("dyn") and e.space in ("global", "member"):
                # the data pointer of a fixed array, read from its slot: VarPtr(HashCluster(0)),
                # StrPtr(global_44A1B0) would not compile (ChessBrainVB)
                dims = info.get("dims") or [(info.get("lb") if isinstance(info.get("lb"), int) else 0, None)]
                tgt = ir.Index(e, [Const(lb or 0) for lb, _ in dims])
                tgt.vbtype = None
            elif isinstance(e, Var) and lf.udt.get(e.key()) and e.key() not in lf.arrays:
                tgt = e
            elif isinstance(e, BinOp) and e.op == "+" and isinstance(e.a, Var) and \
                    lf.udt.get(e.a.key()) and isinstance(e.b, Const) and \
                    isinstance(e.b.value, int) and e.a.key() not in lf.arrays:
                tgt = Deref(e.a, 4, e.b.value)
            elif not isinstance(e, (Var, Deref, Const)):
                tgt = lf.dyn_element(e)
            if tgt is not None:
                return x._keep(Call("VarPtr", [tgt], x.vbtype, x.raw, pure=x.pure))
        # (Get #f, , a(i), optimised, no lock; a method of another object: datirob.infoupdate n,
        # rob(n).nrg)
        io = isinstance(x, Call) and (x.name in ("Get", "Put") or x.obj is not None or
                                      bool(getattr(x, "method", None)))
        if isinstance(x, Call) and x.name in ("ReDim", "ReDim Preserve", "Erase"):
            # the array in a record's element, by the address of its descriptor: ReDim .dna(n),
            # optimised (the descriptor's address is a field's)
            def field_of(e):
                if not isinstance(e, BinOp):
                    return None
                fld = lf.dyn_field(e)
                if fld is not None:
                    return Deref(fld[0], 4, fld[1])
                # (several dimensions, or the array first in the record: ReDim Buckets(x, y).arr(0))
                return dyn_at_address(lf, e, 0, 4)
            args, hit = [], False
            for a in x.args:
                if isinstance(a, ir.Index) and isinstance(a.arr, BinOp):
                    f = field_of(a.arr)
                    if f is not None:
                        n = ir.Index(f, [i.replace(fix) for i in a.idx])
                        args.append(a._keep(n))
                        hit = True
                        continue
                f = field_of(a.e if isinstance(a, ir.AddrOf) else a)    # (&: kept in a slot first)
                if f is not None:
                    args.append(f)
                    hit = True
                    continue
                args.append(a)
            if hit:
                return x._keep(Call(x.name, args, x.vbtype, x.raw, x.method, x.obj, x.pure))
        if isinstance(x, Call) and (isinstance(x.raw, int) or io or
                                    isinstance(x.raw, str) and "!" in x.raw):
            # an element's address passed on: a(i) by reference (to a decompiled
            # procedure's UDT parameter, or ByVal <address> to an API's As Any)
            args, hit = [], False
            api = None
            if isinstance(x.raw, str) and "!" in x.raw:
                from . import apidb
                api = apidb.lookup(x.raw.split("!", 1)[1])
            for i, a in enumerate(x.args):
                byval = isinstance(a, UnOp) and a.op == "ByVal"
                e = a.a if byval else a
                el = lf.dyn_element(e) if isinstance(e, BinOp) else None
                if el is None and isinstance(e, BinOp) and (isinstance(x.raw, int) or io):
                    fld = lf.dyn_field(e)
                    if fld is not None:
                        # a field of an element passed by reference: a(i).fN
                        args.append(ir.AddrOf(Deref(fld[0], 4, fld[1])))
                        hit = True
                        continue
                if el is not None:
                    prm = api.params[i] if api is not None and i < len(api.params) else None
                    if isinstance(x.raw, int) or io:
                        args.append(ir.AddrOf(el))
                    elif byval or prm is not None and (not prm[0] or prm[2] in ("String",
                                                                                "Any")):
                        # As Any (or a ByVal String buffer the writer makes As Any):
                        # the element itself, by reference
                        el.byref_elem = True
                        args.append(el)
                    else:
                        args.append(Call("VarPtr", [el], "Long"))   # a ByVal Long pointer
                    hit = True
                else:
                    args.append(a.replace(fix))
            if hit:
                obj = x.obj.replace(fix) if x.obj is not None else None
                return x._keep(Call(x.name, args, x.vbtype, x.raw, x.method, obj, x.pure))
        if isinstance(x, BinOp) and x.op == "+":
            # a Variant element's address as a value: VarCopy/IsObject read the
            # Variant there (gh_dict: dict_pKeys(i) = dict_pKeys(i - 1))
            el = lf.dyn_element(x)
            info = lf.arrays.get(el.arr.key()) if el is not None and isinstance(el.arr, Var) else None
            if info is not None and (info.get("size") == 16 or info.get("vt") == 12) and \
                    not lf.udt.get(el.arr.key()):
                return el
        if isinstance(x, Deref) and not isinstance(x.e, Var) and offs and \
                lf.dyn_element(x.e) is None:
            e2, hit = expand(x.e)
            if hit and lf.dyn_element(e2) is not None:
                used.update(hit)
                x = Deref(e2, x.size, x.off)
        if isinstance(x, Deref) and not isinstance(x.e, Var):
            el = lf.dyn_element(x.e)
            if el is not None:
                info = lf.arrays.get(el.arr.key()) if isinstance(el.arr, (Var, Deref)) else None
                esize = (info or {}).get("size")
                is_udt = isinstance(el.arr, Var) and bool(lf.udt.get(el.arr.key()) or
                                                          (info or {}).get("udt"))
                if esize is None and isinstance(el.arr, Deref):
                    # (an array in a record's field: pvData + (i - lb) * 4, its element size there)
                    lin = lf.linear(x.e)
                    lbk = Deref(el.arr, 4, 0x14).key()
                    if lbk in lin and lin[lbk][1] < 0:
                        esize = -lin[lbk][1]
                        is_udt = x.off + x.size < esize or x.off > 0
                fx = getattr(x, "fixstr", None)     # (a String * n read: keep it)
                if x.off == 0 and (not esize or esize == x.size or not is_udt or fx):
                    if fx:
                        el.fixstr = fx
                    return el
                if esize and (is_udt or esize > x.size) and x.off + x.size <= esize:
                    # a field of a UDT element: a(i).fC (also one whose Type is not
                    # known yet - array_element_types makes it from such reads:
                    # ChessBrainVB ReadScoreArr pDest(i).EG at +4 of 8 bytes)
                    d = Deref(el, x.size, x.off)
                    d.vbtype = getattr(x, "vbtype", None)
                    if fx:
                        d.fixstr = fx
                    return d
        return None
    def array_in_record(e):
        """The array field whose descriptor's address e is: rob(a).spermDNA = Shots(b).dna, with
        both sides addresses in optimised code."""
        if not isinstance(e, BinOp) or e.op not in ("+", "-"):
            return None
        fld = lf.dyn_field(e)
        return Deref(fld[0], 4, fld[1]) if fld is not None else None
    out = []
    for a, ss, br in body:
        keep = []
        for s in ss:
            if isinstance(s, Assign) and isinstance(s.dst, BinOp):
                d = array_in_record(s.dst)
                if d is not None:
                    src = array_in_record(s.src) or s.src.replace(fix)
                    n = Assign(d, src, s.kind)
                    keep.append(keep_flags(n, s))
                    continue
            if isinstance(s, Assign) and isinstance(s.dst, Deref):
                d = fix(s.dst)
                if d is not None:
                    n = Assign(d, s.src.replace(fix), s.kind)
                    n.va = getattr(s, "va", None)
                    rec = getattr(s, "record", None)
                    if rec is not None:
                        n.record = rec      # an array of UDTs: the element's record type
                        if isinstance(d, ir.Index) and isinstance(d.arr, Var):
                            lf.note_record(d, rec)
                    note_elem_udt(lf, n)
                    keep.append(n)
                    continue
            keep.append(substitute_expr(s, fix))
        if br is not None:
            br = type(br)(br.cond.replace(fix))
        out.append((a, keep, br))
    if used:
        # drop the offset temporaries nothing reads any more
        left = {k for k in used if any(
            isinstance(y, Var) and y.key() == k for _, ss, br in out for s in ss
            for e in stmt_exprs(s) if not (isinstance(s, Assign) and e is s.dst and
                                           isinstance(e, Var))
            for y in e.walk())}
        out = [(a, [s for s in ss if not (isinstance(s, Assign) and isinstance(s.dst, Var) and
                                          s.dst.key() in used - left)], br)
               for a, ss, br in out]
    return out


def inline_all(body, lf):
    for _ in range(5000):
        n0 = total(body)
        body = inline_temps(body, lf)
        body = drop_dead_stores(body, lf)
        body = dead_by_liveness(body, lf)
        if total(body) == n0:
            break
    return body


def rename_object_temps(body, lf):
    """VB re-fetches a control into the same slot before every use.  Give
    each fetch its own name so the temporaries can be inlined.

    So too a form's predeclared instance and VB's Global object, moved into a pooled slot where there are no
    registers to keep them in (On Error Resume Next, no optimisation).  The slot goes on to hold other
    objects, so its one type in objtype says nothing: v48 = Form2 printed without Set, the Global's copy not
    at all, v48.App.  It all compiled, and the errors were resumed from (tests/t100ornobj)."""
    names = getattr(lf.m, "global_names", {})

    def fetch(s):
        if getattr(s.src, "control", None) is not None:
            return True
        return s.kind != "set" and isinstance(s.src, Var) and s.src.space == "global" and \
            s.src.off in names and s.dst.space == "local" and not lf.is_named_local(s.dst)

    slots = {s.dst.key() for _, ss, _ in body for s in ss
             if isinstance(s, Assign) and isinstance(s.dst, Var) and s.dst.space != "member" and fetch(s)}
    if not slots:                   # (a member isn't a temporary: Set btn = Command1, WithEvents)
        return body

    def reads(s):
        es = [s.src] + ([] if isinstance(s.dst, Var) else [s.dst]) if isinstance(s, Assign) \
            else list(getattr(s, "exprs", lambda: ())())
        return {x.key() for e in es if e is not None for x in e.walk() if isinstance(x, Var)}
    # slots read in some block before that block sets them: their value can
    # come from another block, so a block's last fetch must keep the name
    # (t10carr: edi held the For counter too, and renaming its 0 and +1 cut
    # them off from the loop that reads edi)
    exposed = set()
    for a, ss, br in body:
        done = set()
        for s in ss:
            exposed |= (reads(s) & slots) - done
            if isinstance(s, Assign) and isinstance(s.dst, Var) and s.dst.key() in slots:
                done.add(s.dst.key())
        if br is not None:
            exposed |= {x.key() for x in br.cond.walk() if isinstance(x, Var)} & slots - done
    seq = [0]
    out = []
    for a, ss, br in body:
        cur = {}
        keep = []
        last = {}
        for i, s in enumerate(ss):
            if isinstance(s, Assign) and isinstance(s.dst, Var) and s.dst.key() in slots:
                last[s.dst.key()] = i
        for i, s in enumerate(ss):
            if isinstance(s, Assign) and isinstance(s.dst, Var) and s.dst.key() in slots:
                k = s.dst.key()
                src = rename_reads(s.src, cur)
                if not fetch(s) or (k in exposed and last[k] == i):
                    cur.pop(k, None)        # another value, or one later blocks read
                    keep.append(keep_flags(Assign(s.dst, src, s.kind), s))
                    continue
                seq[0] += 1
                new = Var("local", (s.dst.off, seq[0]))
                cur[k] = new
                keep.append(keep_flags(Assign(new, src, s.kind), s))
                continue
            keep.append(rename_stmt(s, cur))
        out.append((a, keep, br if br is None else type(br)(rename_reads(br.cond, cur))))
    return out


def rename_reads(e, cur):
    if not cur:
        return e

    def f(x):
        if isinstance(x, Var) and x.key() in cur:
            return cur[x.key()]
        return None
    return e.replace(f)


def rename_stmt(s, cur):
    if isinstance(s, Assign):
        dst = s.dst if isinstance(s.dst, Var) else rename_reads(s.dst, cur)
        n = Assign(dst, rename_reads(s.src, cur), s.kind)
    elif isinstance(s, (ExprStmt, CallStmt)):
        n = type(s)(rename_reads(s.e, cur))
    elif isinstance(s, ir.Return):
        n = ir.Return()
        n.value = rename_reads(s.value, cur) if getattr(s, "value", None) is not None else None
    else:
        return s
    return keep_flags(n, s)


def fix_bools(body):
    """Collapse the setcc/test pattern the compiler leaves in conditions, and
    "-((a <> b) + 1)" (neg; sbb; inc; neg: a = b as a Boolean value, gh_zip
    harness CStr(z.FromUtf8Array(baOut) = s) prints True, not -1)."""
    from .lift import Lifter
    from .structure import negate

    def f(e):
        if isinstance(e, UnOp) and e.op == "-" and isinstance(e.a, BinOp) and e.a.op == "+" \
                and isinstance(e.a.b, Const) and e.a.b.value == 1 and \
                isinstance(e.a.a, BinOp) and e.a.a.op in CMP_OPS:
            n = negate(e.a.a.replace(f))
            n.vbtype = "Boolean"
            return n
        return None
    out = []
    for a, ss, br in body:
        ss = [substitute_expr(s, f) for s in ss]
        if br is not None:
            br = type(br)(Lifter.boolify(br.cond.replace(f)))
        out.append((a, ss, br))
    return out


def total(body):
    return sum(len(ss) for _, ss, _ in body)


def drop_dead_stores(body, lf):
    """Assignments to a temporary nothing ever reads."""
    mark_error_regions(body, lf)    # (raises_here: statements rewritten since lost the mark)
    uses = var_index(body)[1]       # (count_uses per store was quadratic)
    out = []
    for a, ss, br in body:
        keep = []
        for s in ss:
            if isinstance(s, Assign) and isinstance(s.dst, Var) and \
               s.dst.space in ("local", "reg", "tmp", "stk") and not lf.is_named_local(s.dst) \
               and not uses.get(s.dst.key()) and not has_effect(s.src) and not raises_here(s) and \
               not (s.dst.space == "local" and lf.result_slot is not None and
                    s.dst.off == lf.result_slot):       # the Function's value
                continue
            keep.append(s)
        out.append((a, keep, br))
    return out


COND = 1 << 30              # the place of a block's branch condition: after its statements


def copy_for_procedure(lf, d, v, blocks, use):
    """v = record.f: Proc v.  The copy stays: as Proc record.f the parameter would be the
    field itself, and change when the procedure reads over the record (a target piirra_puu:
    Proc_748E80 Form1(5).f0 reads the next person into Form1(5), then its a1 is that person's
    father, 0 at the end of a line: "Bad Number1 0")."""
    if not (isinstance(d.src, Deref) or isinstance(d.src, Var) and d.src.space in ("global", "member")):
        return False
    names = getattr(lf.m, "_proc_names", None)
    if names is None:
        names = lf.m._proc_names = {p.name.lower() for p in lf.m.procs.values()}
    bi, si = use
    if si == COND:
        exprs = [blocks[bi][2].cond]
    else:
        exprs = stmt_exprs(blocks[bi][1][si])
    k = v.key()
    return any(isinstance(x, Call) and x.name.lower() in names and
               any(isinstance(a, ir.AddrOf) and isinstance(a.e, Var) and a.e.key() == k for a in x.args)
               for e in exprs for x in e.walk())


def var_index(body):
    """count_defs and count_uses for every variable in one walk, where each
    one is defined and read - (block, statement), or (block, COND) for a
    branch condition: for a variable defined (read) once, the place - and
    the variables VarPtr takes the address of (inlining one would put its
    value where the address is wanted: VarPtr(uBuf)).
    Returns (defs, uses, def_at, use_at, address_taken)."""
    ndefs, nuses, def_at, use_at, addr = {}, {}, {}, {}, set()

    def reads(e, pos):
        for x in e.walk():
            if isinstance(x, Var):
                k = x.key()
                nuses[k] = nuses.get(k, 0) + 1
                use_at[k] = pos
            elif isinstance(x, Call) and x.name == "VarPtr" and x.args and \
                    isinstance(x.args[0], Var):
                addr.add(x.args[0].key())
            elif isinstance(x, Call) and getattr(x, "put_size", None) and x.args and \
                    isinstance(x.args[-1], ir.AddrOf) and isinstance(x.args[-1].e, Var):
                addr.add(x.args[-1].e.key())    # Put / Get #f, , v: the variable itself
            elif isinstance(x, Call) and getattr(x, "input_codes", None):
                for it in x.args[1:]:           # Input #f, v: written, not read (the
                    if isinstance(it, ir.AddrOf) and isinstance(it.e, Var):
                        addr.add(it.e.key())    # zero before it folded to "Input #1, 0")
    for bi, (a, ss, br) in enumerate(body):
        for si, s in enumerate(ss):
            if isinstance(s, Assign) and isinstance(s.dst, Var):
                k = s.dst.key()
                ndefs[k] = ndefs.get(k, 0) + 1
                def_at[k] = (bi, si)
            for e in stmt_exprs(s):
                if not (isinstance(s, Assign) and e is s.dst and isinstance(e, Var)):
                    reads(e, (bi, si))      # (the target itself is not a use)
        if br is not None:
            reads(br.cond, (bi, COND))
    return ndefs, nuses, def_at, use_at, addr


CONVERSIONS = {"CDate", "CVDate", "CInt", "CLng", "CByte", "CSng", "CDbl", "CCur", "CBool", "CDec"}


def may_raise(e):
    """Does evaluating e fail for some values, whatever becomes of the result?  d = CDate("nope") with d
    never read still raises 13, and On Error Resume Next code asks Err.Number about it (t37cdate).
    A statement of its own, as there is no other way to write a conversion in VB."""
    # (the conversion itself, not one inside a sum: an element's offset left behind when the element
    # was folded, (CLng(c - 1) - lb) * 80, is no statement of the program's)
    if isinstance(e, Call) and e.name in CONVERSIONS and e.args:
        a = e.args[0]
        return not isinstance(a, Const) or e.name in ("CDate", "CVDate") and abs(a.value) >= 2958466
    return False


def raises_here(s):
    """Under On Error Resume Next a runtime function's failure is what the code goes on to ask about,
    its value often of no interest: n = QBColor(16): Print Err.Number (t39filter)."""
    return may_raise(s.src) or may_skip(s) and isinstance(s.src, Call)


def has_effect(e):
    from .machine import Hi
    if isinstance(e, Hi):
        return False        # the upper half of a value computed (and stored) already
    return any(isinstance(x, Call) and not x.pure for x in e.walk())


def flatten(lf):
    """[(node_start, [stmts], branch)] in reverse post order."""
    return [(a, list(lf.out[a][0]), lf.out[a][1]) for a in lf.rpo() if a in lf.out]


def drop_frees(body):
    return [(a, [s for s in ss if not isinstance(s, Free)], br) for a, ss, br in body]


def contains(e, sub):
    k = sub.key()
    tk = type(sub)
    for x in e.walk():
        if type(x) is tk and x.key() == k:
            return True
    return False


def stmt_exprs(s):
    out = []
    if isinstance(s, Assign):
        out += [s.dst, s.src]
    elif isinstance(s, (CallStmt, ExprStmt)):
        out.append(s.e)
    elif isinstance(s, ir.Return) and getattr(s, "value", None) is not None:
        out.append(s.value)
    return out


def drop_consumed_calls(body):
    """A CallStmt is scaffolding when its value appears in a later statement."""
    out = []
    for a, ss, br in body:
        keep = []
        for i, s in enumerate(ss):
            if isinstance(s, CallStmt):
                later = ss[i + 1:]
                used = any(contains(e, s.e) for t in later for e in stmt_exprs(t))
                if not used and br is not None and contains(br.cond, s.e):
                    used = True
                if used:
                    continue
                if is_pure(s.e):
                    continue
                keep.append(ExprStmt(s.e))
                keep[-1].va = s.va
                continue
            keep.append(s)
        out.append((a, keep, br))
    return out


def is_pure(e):
    """Expressions with no effect worth printing on their own."""
    from .ir import AddrOf
    if isinstance(e, (Const, Str, Var, Deref, AddrOf)):
        return True
    if isinstance(e, Prop):
        return True
    if isinstance(e, (BinOp, UnOp)):
        return True
    if isinstance(e, Call):
        return e.pure and not e.method
    return False


def fold_double_slots(body, lf):
    """A Double constant written as two dwords and read by the FPU: a For loop's
    Step (For dt = a To b As Date: [s] = 0, [s+4] = 3FF00000h, fadd qword [s]).
    Only the low dword was seen, so the loop added 0 and never ended."""
    import struct
    keys = [k for k in getattr(lf, "fp_qword", ()) if isinstance(k[1], int)]
    if not keys:
        return body
    defs, reads, addr = {}, set(), set()
    for _, ss, br in body:
        for s in ss:
            if isinstance(s, Assign) and isinstance(s.dst, Var):
                defs.setdefault(s.dst.key(), []).append(s.src)
            for e in stmt_exprs(s):
                if isinstance(s, Assign) and e is s.dst and isinstance(e, Var):
                    continue
                for y in e.walk():
                    if isinstance(y, Var):
                        reads.add(y.key())
                    elif isinstance(y, ir.AddrOf) and isinstance(y.e, Var):
                        addr.add(y.e.key())
        if br is not None:
            reads.update(y.key() for y in br.cond.walk() if isinstance(y, Var))
    vals = {}
    for k in keys:
        hi = Var(k[0], k[1] + 4).key()
        lo_d, hi_d = defs.get(k, ()), defs.get(hi, ())
        if len(lo_d) != 1 or len(hi_d) != 1 or hi in reads or k in addr or hi in addr:
            continue
        a, b = lo_d[0], hi_d[0]
        if not (isinstance(a, Const) and isinstance(b, Const) and isinstance(a.value, int) and
                isinstance(b.value, int)):
            continue
        raw = struct.pack("<II", a.value & 0xffffffff, b.value & 0xffffffff)
        vals[k] = (hi, Const(struct.unpack("<d", raw)[0], "Double"))
    if not vals:
        return body
    drop = set(vals) | {h for h, _ in vals.values()}

    def fix(x):
        if isinstance(x, Var) and x.key() in vals:
            return vals[x.key()][1]
        return None
    out = []
    for a, ss, br in body:
        keep = [substitute_expr(s, fix) for s in ss
                if not (isinstance(s, Assign) and isinstance(s.dst, Var) and s.dst.key() in drop)]
        out.append((a, keep, type(br)(br.cond.replace(fix)) if br is not None else None))
    return out


def set_nothing(body):
    """Set a.Picture = Nothing goes through a temporary: Set t = (cast) 0, a.Picture = t.  The
    Set looks like the prologue's zeroing, and a.Picture = t, of an empty t, raises error 91
    (a target Proc_E85D70, the first line of a print procedure)."""
    out = []
    for a, ss, br in body:
        keep = []
        for s in ss:
            p = keep[-1] if keep else None
            if isinstance(s, Assign) and isinstance(s.dst, ir.Prop) and isinstance(s.src, Var) and \
                    isinstance(p, Assign) and p.kind == "set" and isinstance(p.dst, Var) and \
                    p.dst.key() == s.src.key() and isinstance(p.src, Const) and p.src.value == 0:
                n = Assign(s.dst, Const(0), "set")
                n.va = getattr(s, "va", None)
                keep[-1] = n
                continue
            keep.append(s)
        out.append((a, keep, br))
    return out


def drop_zero_init(body, lf):
    """VB zeroes every local at entry; drop those that are written again
    before they are read."""
    if not body:
        return body
    body = set_nothing(body)
    # (a dynamic array's slot: only the prologue gives it a number, whatever comes between, and
    # optimised code puts the first statements' stores in between: Dim s() As String, s = 0)
    body = [(a, [s for s in ss if not (
        isinstance(s, Assign) and isinstance(s.src, Const) and s.src.value == 0 and
        isinstance(s.dst, Var) and s.dst.space == "local" and
        (lf.arrays.get(s.dst.key()) or {}).get("dyn"))], br) for a, ss, br in body]
    first_a, first, br = body[0]
    later = [s for _, ss, _ in body for s in ss]
    keep = []
    prologue = True
    written = set()
    for i, s in enumerate(first):
        const = isinstance(s, Assign) and isinstance(s.src, Const)
        zero = const and s.src.value == 0
        slot = const and (isinstance(s.dst, Deref) and (
            isinstance(s.dst.e, Var) and s.dst.e.space == "local" or
            isinstance(s.dst.e, BinOp) and isinstance(s.dst.e.a, Var) and
            s.dst.e.a.space == "local" and isinstance(s.dst.e.b, Const)) or
            isinstance(s.dst, Var) and s.dst.space == "local")
        if prologue and slot and zero and s.dst.key() not in written:
            continue        # the prologue zeroing every local (and struct field)
        if prologue and slot:
            # the compiler interleaves the zeroing with the first constant
            # stores (".Mode = 7" between them, gh_qr MakeEci): keep going
            written.add(s.dst.key())
            keep.append(s)
            continue
        prologue = False
        if isinstance(s, Assign) and isinstance(s.src, Const) and s.src.value == 0 \
           and isinstance(s.dst, Var) and s.dst.space == "local":
            rest = first[i + 1:] + later[len(first):]
            nxt = next((t for t in rest if isinstance(t, Assign) and t.dst == s.dst), None)
            read_before = False
            for t in rest:
                if t is nxt:
                    # (and it may read what it overwrites: d = 1.5, two stores, the lower half 0,
                    # then d = d * 2 + i, which lost the 1.5.  t101ornall)
                    read_before = contains(t.src, s.dst)
                    break
                if any(contains(e, s.dst) for e in stmt_exprs(t)):
                    read_before = True
                    break
            if nxt is not None and not read_before:
                continue
        keep.append(s)
    return [(first_a, keep, br)] + body[1:]


def count_uses(body, v):
    n = 0
    for _, ss, br in body:
        for s in ss:
            for e in stmt_exprs(s):
                if isinstance(s, Assign) and e is s.dst and isinstance(e, Var):
                    continue        # the target itself is not a use
                n += sum(1 for x in e.walk() if isinstance(x, Var) and x.key() == v.key())
        if br is not None:
            n += sum(1 for x in br.cond.walk() if isinstance(x, Var) and x.key() == v.key())
    return n


def count_defs(body, v):
    return sum(1 for _, ss, _ in body for s in ss
               if isinstance(s, Assign) and isinstance(s.dst, Var) and s.dst.key() == v.key())


class Gone:
    """An inlined definition, left in its place while inline_temps works so
    that the places of the others stay put (it drops them on the way out;
    the checks it calls skip them like a removed statement)."""


def inline_temps(body, lf):
    """Inline the locals assigned once and read once, when nothing in
    between could change what they hold.

    The candidates are tried in the order of their definitions.  After each
    inline the next one tried is the first in that order that the inline may
    have let through: inlining changes two places only, the definition it
    removes and the statement (or condition) it rewrites, so a candidate
    that failed is tried again only when one of them lies between its
    definition and its use, where its checks looked.  That is the order of
    inlining one temporary per call and starting over, without walking the
    whole procedure for every candidate after every inline (cubic in the
    procedure's size: VBDoom did not decompile in seven minutes)."""
    blocks = [[a, list(ss), br] for a, ss, br in body]
    ndefs, nuses, def_at, use_at, addr = var_index(blocks)
    gone = Gone()
    moved = {}                  # an inlined definition's place -> where its value went
    state = {}                  # key -> "pending", "failed", "never" or "done"
    failed = {}                 # key -> (first, last): the places its checks read
    heap = []                   # (place of the definition, push number, key)
    pushed = [0]

    fixed = getattr(lf, "fixed_strings", {})

    def is_fixed(v):
        """A String * n slot (or a numbered version of one: rename_block_temps)."""
        k = v.key()
        return k in fixed or isinstance(k[1], tuple) and (k[0], k[1][0]) in fixed

    def rounded(s):
        """A value the FPU computed and fstp rounded to a Single, or a Double: the slot stays, or the
        expression would keep digits the store lost, and a simulation parts over the last bit (b =
        Cos(a / 200) * 32000: PushIntStack b, DarwinBots DNAcos).  Where the compiler had no reason to
        store, the source had a variable; where it had one (a call in the middle of an expression),
        the same store comes out of this temporary.  (Under Wine the FPU keeps 64 bits, so a Double
        store rounds too; on Windows 53, and only a Single's does.)"""
        return getattr(s, "fp_store", None) in (4, 8) and isinstance(s.src, (BinOp, Call, UnOp)) and \
            not (isinstance(s.src, Call) and s.src.name in ("CSng", "CDbl"))

    def candidate(s):
        # (a String * n pads what it is given, and Put / Get need the
        # variable itself: tests/t18varfor wrote "abcdefghij" with a length)
        return isinstance(s, Assign) and isinstance(s.dst, Var) and not rounded(s) and \
            s.dst.space in ("local", "reg", "tmp", "stk") and \
            ndefs.get(s.dst.key()) == 1 and not getattr(s, "fixstr", None) and \
            not is_fixed(s.dst) and \
            (s.kind != "set" or getattr(s.src, "control", None) is not None or
             (isinstance(s.src, Var) and getattr(s.src, "iface", None) is not None) or
             getattr(s, "cast_set", False) or fresh_object(s.src))

    def push(key):
        if state.get(key) is None:
            state[key] = "pending"
            pushed[0] += 1
            heapq.heappush(heap, (def_at[key], pushed[0], key))

    avoiding = {}

    def reaches_around(bi, tbi):
        """Can the entry reach block tbi without passing block bi?  Then the use
        may see the variable's zero, not the definition: found = True in a loop,
        If Not found after it (a target Text4_GotFocus: the one store folded
        into "If -1 = 0", so a missing font was never replaced, error 380)."""
        seen = avoiding.get(bi)
        if seen is None:
            seen = avoiding[bi] = set()
            nodes = getattr(lf, "nodes", {}) or {}
            work = [blocks[0][0]] if bi != 0 else []
            while work:
                a = work.pop()
                if a in seen or a == blocks[bi][0]:
                    continue
                seen.add(a)
                n = nodes.get(a)
                if n is not None:
                    work.extend(n.succs)
        return blocks[tbi][0] in seen

    repeats = {}
    sccs = []

    def repeats_without(bi, tbi):
        """Can block tbi run again with no run of block bi in between?  Then a value read once by
        bi would be read every time by tbi (For t = 1 To .DnaLen - 1, whose Delete shortens .DnaLen:
        DarwinBots MajorDeletion, three random numbers short and all the runs apart from there)."""
        r = repeats.get((bi, tbi))
        if r is None:
            nodes = getattr(lf, "nodes", {}) or {}
            if not sccs:
                sccs.append(cycle_groups({a: node_succs(lf, a) for a in nodes}))
            group = sccs[0].get(blocks[tbi][0])
            r = False
            if group is not None:
                target, stop = blocks[tbi][0], blocks[bi][0]
                seen, work = set(), node_succs(lf, target)
                while work:
                    a = work.pop()
                    if a == target:
                        r = True
                        break
                    if a == stop or a in seen or sccs[0].get(a) != group:
                        continue
                    seen.add(a)
                    work.extend(node_succs(lf, a))
            repeats[(bi, tbi)] = r
        return r

    def attempt(v, bi, si, d, use):
        """How to inline v (defined by d at bi, si, read at use), or None."""
        if use <= (bi, si):
            return None                 # read before its definition (a loop)
        tbi, tsi = use[0], (-1 if use[1] == COND else use[1])
        if tbi != bi and v.space == "local" and reaches_around(bi, tbi):
            return None
        if tsi >= 0 and isinstance(d.src, (Var, Deref, ir.Index)) and any(
                isinstance(x, ir.AddrOf) and isinstance(x.e, Var) and x.e.key() == v.key()
                for e in stmt_exprs(blocks[tbi][1][tsi]) for x in e.walk()):
            # F (k), or a copy VB made for any other reason: what F does to it stays out of k
            # (t83obscure par1 (k) printed par1 k: k one too many)
            return None
        if d.kind == "set" and fresh_object(d.src):
            # "Set t = New C: Set x = t": only straight into another Set
            u = blocks[tbi][1][tsi] if tsi >= 0 else None
            if not (isinstance(u, Assign) and u.kind == "set" and
                    isinstance(u.src, Var) and u.src.key() == v.key()):
                return None
        if not safe_between(blocks, bi, si, tbi, tsi, d.src, lf.arrays):
            return None
        use_stmt = None
        new_cond = None
        if has_call(d.src):
            # the calls in d.src ran before everything in the use statement
            late_src = is_late(d.src)
            if tsi >= 0:
                u = blocks[tbi][1][tsi]
                if late_src or any(has_late(e) for e in stmt_exprs(u)):
                    use_stmt = hoist_late(u, v.key(), late_src)     # VB's own order
                else:
                    use_stmt = hoist_stmt(u, v.key())
                if use_stmt is None:
                    return None
            else:
                cond = blocks[tbi][2].cond
                if late_src or has_late(cond):
                    new_cond = cond if key_first(cond, v.key(), late_src) else None
                else:
                    new_cond = hoist_use(cond, v.key())
                if new_cond is None:
                    return None
        if tbi != bi:
            # the use may run again after a loop back edge: only inline when
            # nothing the value depends on is ever reassigned
            srcvars = {x for x in d.src.walk() if isinstance(x, Var)}
            if any(ndefs.get(x.key(), 0) > (0 if x.space == "arg" else 1) for x in srcvars) or \
                    lf.impure(d.src):
                return None
            if reads_memory(d.src, lf.arrays) and repeats_without(bi, tbi):
                return None
        return tbi, tsi, use_stmt, new_cond

    for a, ss, br in blocks:
        for s in ss:
            if candidate(s):
                push(s.dst.key())
    while heap:
        pos, _, key = heapq.heappop(heap)
        if state.get(key) != "pending":
            continue
        bi, si = pos
        d = blocks[bi][1][si]
        if not candidate(d):
            state[key] = None           # an inline rewrote its definition
            continue
        v = d.dst
        if nuses.get(key) != 1 or lf.is_named_local(v) or key in addr:
            state[key] = "never"        # (VarPtr(uBuf): the variable, not its value)
            continue
        use = use_at[key]
        while use in moved:
            use = moved[use]            # read in a definition inlined since
        how = None if copy_for_procedure(lf, d, v, blocks, use) else attempt(v, bi, si, d, use)
        if how is None:
            state[key] = "failed"
            failed[key] = (min(pos, use), max(pos, use))
            continue
        tbi, tsi, use_stmt, new_cond = how
        blocks[bi][1][si] = gone
        src = d.src
        slot = (key[0], key[1][0]) if isinstance(key[1], tuple) else key    # (renamed copies)
        if isinstance(src, Const) and src.vbtype is None and isinstance(src.value, int) and \
                getattr(lf, "vt_types", {}).get(slot) == {"Boolean"}:
            src = Const(src.value, "Boolean")   # the variable was a Boolean: True, not -1
        if tsi >= 0:
            s = use_stmt if use_stmt is not None else blocks[tbi][1][tsi]
            blocks[tbi][1][tsi] = substitute(s, v, src)
        else:
            br = blocks[tbi][2]
            cond = new_cond if new_cond is not None else br.cond
            blocks[tbi][2] = type(br)(ir.subst(cond, {v.key(): src}))
        state[key] = "done"
        moved[pos] = use
        for k, (first, last) in list(failed.items()):
            if first <= pos <= last or first <= use <= last:
                del failed[k]
                state[k] = None
                push(k)
        if tsi >= 0 and candidate(blocks[tbi][1][tsi]):
            push(blocks[tbi][1][tsi].dst.key())    # Set x = New C: now one too
    return [(a, [s for s in ss if s is not gone], br) for a, ss, br in blocks]


def lower_bound(x):
    """A dynamic array's lower bound, read from its descriptor."""
    return isinstance(x, Deref) and x.off == 0x14 and isinstance(x.e, Var)


def element_offset(e, arrays):
    """Does e use an array's lower bound from its descriptor?  (Any variable in i - [v + 14h], as the
    array may not be known yet: gh_zip's module-level mB0 in CopyMemory a(0), ByVal mB0.fC - mB0.f14.)"""
    return any(lower_bound(x) and x.e.key() in arrays or
               isinstance(x, BinOp) and x.op == "-" and lower_bound(x.b) or
               isinstance(x, UnOp) and x.op == "-" and lower_bound(x.a) for x in e.walk())


def reads_memory(e, arrays=()):
    """Does e read what a store through a pointer, or another procedure, can change: a field, an
    element, a module's or the object's variable?  Not an address on its own, nor an array's
    descriptor (what the compiler works out an element's place from: rob(n)'s (n - lb) * 13668 is
    held over the calls made for the arguments before it: MDIForm1 robinf_Click)."""
    if isinstance(e, ir.AddrOf):
        x = e.e
        if isinstance(x, Var):
            return False
        if isinstance(x, Deref):
            return reads_memory(x.e, arrays)
        return any(reads_memory(c, arrays) for c in x.children())
    if isinstance(e, Deref) and e.off in (0xc, 0x10, 0x14) and isinstance(e.e, Var) and \
            e.e.key() in arrays:
        return False
    if isinstance(e, BinOp) and e.op == "-" and lower_bound(e.b):
        return reads_memory(e.a, arrays)
    if isinstance(e, UnOp) and e.op == "-" and lower_bound(e.a):
        return False
    if isinstance(e, (Deref, ir.Index, ir.Field)) or \
            isinstance(e, Var) and e.space in ("global", "member"):
        return True
    return any(reads_memory(c, arrays) for c in e.children())


def cycle_groups(graph):
    """{block address: group} of the blocks on a cycle of the control flow graph {address:
    successors} (the strongly connected components with an edge inside; Tarjan's, without
    recursion: procedures have thousands of blocks)."""
    index, low, on, stack, out = {}, {}, set(), [], {}
    counter = [0]
    for root in graph:
        if root in index:
            continue
        index[root] = low[root] = counter[0]
        counter[0] += 1
        stack.append(root)
        on.add(root)
        work = [(root, iter(graph[root]))]
        while work:
            a, it = work[-1]
            for b in it:
                if b not in graph:
                    continue
                if b not in index:
                    index[b] = low[b] = counter[0]
                    counter[0] += 1
                    stack.append(b)
                    on.add(b)
                    work.append((b, iter(graph[b])))
                    break
                if b in on:
                    low[a] = min(low[a], index[b])
            else:
                work.pop()
                if work:
                    p = work[-1][0]
                    low[p] = min(low[p], low[a])
                if low[a] == index[a]:
                    comp = []
                    while True:
                        x = stack.pop()
                        on.discard(x)
                        comp.append(x)
                        if x == a:
                            break
                    if len(comp) > 1 or a in graph[a]:
                        for x in comp:
                            out[x] = a
    return out


def register_copies(body, lf):
    """A register or temporary that holds a variable's value where it is read can read the variable.
    Fast code keeps a loop's counter and limit in registers as well as in their variables (mov eax, 1;
    mov [v18], eax; L: cmp eax, [v11C]; ...; mov [v18], eax; jmp L), and a test or a subscript reading
    the register is what stops the loop being a For: 175 of DarwinBots' loops printed Do While t_eax <=
    limit.  Which holds is flow-sensitive (available copies), so a slot used for something else later
    is no trouble: its next assignment ends the copy."""
    if len(body) < 2:
        return body
    idx = {a: i for i, (a, _, _) in enumerate(body)}
    succ = [[idx[t] for t in node_succs(lf, a) if t in idx] for a, _, _ in body]
    preds = [[] for _ in body]
    for i, ts in enumerate(succ):
        for t in ts:
            preds[t].append(i)
    taken = address_taken(body)
    variables = {}

    def holder(k):
        return k[0] in ("reg", "tmp") and k not in taken

    def source(k):
        return k[0] in ("local", "global", "member", "arg") and k not in lf.arrays

    def shared(k):
        """Can a procedure change it?  (A local passed by reference only while it runs: VB keeps no
        pointer, so For t with Delete a, t still has t in the register's place between calls.)"""
        return k[0] in ("global", "member") or k in taken

    def step(cur, s):
        """cur after statement s: (holder key, variable key), and ("k", key, value) for a constant
        (mov eax, 1; ...; mov [v18], eax: a constant, so no variable to refer to)."""
        if isinstance(s, Assign) and isinstance(s.dst, Var):
            d = s.dst.key()
            src = s.src
            if isinstance(src, BinOp) and src.op in ("+", "-") and isinstance(src.a, Var) and \
                    src.a.key() == d and isinstance(src.b, Const) and isinstance(src.b.value, int):
                # t1 = esi: esi = esi + 1 ... Loop While t1 + 1 <= 31: t1 + 1 is the new esi
                k = src.b.value if src.op == "+" else -src.b.value
                sums = {("+", p[1], k, d) for p in cur if len(p) == 3 and p[0] == "c" and p[2] == d}
                if sums:
                    variables[d] = s.dst
            else:
                sums = set()
            cur = {p for p in cur if d not in p} | sums
            if isinstance(src, Var) and src.key() != d:
                o = src.key()
                if holder(d) and source(o):
                    variables[o] = src
                    cur.add((d, o))
                elif source(d) and holder(o):
                    variables[d] = s.dst
                    cur.add((o, d))
                elif holder(d) and holder(o):
                    cur |= {(d, p[1]) for p in cur if len(p) == 2 and p[0] == o}
                if holder(d):
                    variables[o] = src
                    cur.add(("c", d, o))    # (only for the sums below)
            elif isinstance(src, Const) and isinstance(src.value, int) and (holder(d) or source(d)):
                variables[d] = s.dst
                for p in list(cur):
                    if len(p) == 3 and p[2] == src.value and p[1] != d:
                        if holder(d) and source(p[1]):
                            cur.add((d, p[1]))
                        elif source(d) and holder(p[1]):
                            cur.add((p[1], d))
                cur.add(("k", d, src.value))
        es = stmt_exprs(s)
        if any(lf.impure(e) for e in es):
            cur = {p for p in cur if not shared(p[1])}      # (a pair, or a constant's variable)
        elif taken:
            given = {x.e.key() for e in es for x in e.walk()
                     if isinstance(x, ir.AddrOf) and isinstance(x.e, Var)}
            if given:
                cur = {p for p in cur if p[1] not in given}
        return cur

    def on_entry(i):
        ins = [out[p] for p in preds[i] if out[p] is not None]
        return set() if i == 0 or not preds[i] or not ins else set.intersection(*ins)

    def copies(cur):
        m = {}
        for p in sorted((p for p in cur if len(p) == 2), key=str):
            m.setdefault(p[0], variables[p[1]])
        return m

    def summed(e, cur):
        """e with t + k read as the variable that is t + k by now."""
        sums = {(p[1], p[2]): p[3] for p in cur if len(p) == 4}
        if not sums:
            return e

        def fix(x):
            if isinstance(x, BinOp) and x.op in ("+", "-") and isinstance(x.a, Var) and \
                    isinstance(x.b, Const) and isinstance(x.b.value, int):
                k = sums.get((x.a.key(), x.b.value if x.op == "+" else -x.b.value))
                if k is not None:
                    return variables[k]
            return None
        return e.replace(fix)

    out = [None] * len(body)        # None: not reached yet, so anything holds
    work = list(range(len(body)))
    while work:
        i = work.pop()
        cur = on_entry(i)
        for x in body[i][1]:
            cur = step(cur, x)
        if out[i] != cur:
            out[i] = cur
            work.extend(succ[i])
    res = []
    changed = False
    for i, (a, ss, br) in enumerate(body):
        cur = on_entry(i)
        keep = []
        for x in ss:
            m = copies(cur)
            if m:
                reads = set(var_reads(x))
                if reads & set(m):
                    x = substitute_many(x, {k: v for k, v in m.items() if k in reads})
                    changed = True
            cur = step(cur, x)
            keep.append(x)
        if br is not None:
            m = copies(cur)
            if any(isinstance(y, Var) and y.key() in m for y in br.cond.walk()):
                br = type(br)(ir.subst(br.cond, m))
                changed = True
            c = summed(br.cond, cur)
            if c is not br.cond and repr(c) != repr(br.cond):
                br = type(br)(c)
                changed = True
        res.append((a, keep, br))
    if not changed:
        return body
    res = counter_in_register(res, lf, preds, succ)
    return drop_dead_stores(dead_by_liveness(res, lf), lf)


def counter_in_register(body, lf, preds, succ):
    """L: mov [v24], ecx; cmp ecx, [limit]; ...; lea ecx, [v24's value + 1]; jmp L: the counter lives in
    the register and the variable gets its value at the top of each round.  With the test reading the
    variable (register_copies), that is the variable: v24 = 1 ... v24 = v24 + 1, a For."""
    ndefs, nuses, _, use_at, _ = var_index(body)
    defs = {}
    for bi, (a, ss, br) in enumerate(body):
        for si, x in enumerate(ss):
            if isinstance(x, Assign) and isinstance(x.dst, Var) and x.dst.space == "reg":
                defs.setdefault(x.dst.key(), []).append((bi, si))
    edits = []
    used = set()
    for bi, (a, ss, br) in enumerate(body):
        for si, x in enumerate(ss):
            if not (isinstance(x, Assign) and isinstance(x.dst, Var) and isinstance(x.src, Var) and
                    x.src.space == "reg" and x.dst.space == "local" and
                    nuses.get(x.src.key()) == 1 and x.src.key() in defs and
                    x.src.key() not in used and x.dst.key() not in lf.arrays):
                continue
            r, v = x.src.key(), x.dst.key()
            where = defs[r]
            if not preds[bi] or {b for b, _ in where} != set(preds[bi]) or len(where) != len(preds[bi]) or \
                    bi in preds[bi] and False:
                continue
            ok = not any(v in var_reads(y) or isinstance(y, Assign) and isinstance(y.dst, Var) and
                         y.dst.key() in (v, r) for y in ss[:si])
            for b, j in where:
                later = body[b][1][j + 1:]
                if succ[b] != [bi] or any(
                        v in var_reads(y) or isinstance(y, Assign) and isinstance(y.dst, Var) and
                        y.dst.key() == v for y in later):
                    ok = False
                bb = body[b][2]
                if bb is not None and any(isinstance(y, Var) and y.key() == v for y in bb.cond.walk()):
                    ok = False
            if ok:
                used.add(r)
                edits.append((bi, si, x.dst, where))
    if not edits:
        return body
    drop = {(bi, si) for bi, si, _, _ in edits}
    to = {}
    for bi, si, var, where in edits:
        for b, j in where:
            to[(b, j)] = var
    out = []
    for bi, (a, ss, br) in enumerate(body):
        keep = []
        for si, x in enumerate(ss):
            if (bi, si) in drop:
                continue
            if (bi, si) in to:
                n = Assign(to[(bi, si)], x.src, x.kind)
                x = keep_flags(n, x)
            keep.append(x)
        out.append((a, keep, br))
    return out


def fresh_object(e):
    """New C or Nothing: an object value nothing else refers to yet."""
    return (isinstance(e, Call) and e.name == "New") or \
        (isinstance(e, Const) and e.value == 0)


COMMUTE = {"+", "*", "And", "Or", "Xor", "=", "<>"}


def has_call(e):
    return any(isinstance(x, (Call, Prop)) for x in e.walk())


def mentions(e, key):
    return any(isinstance(x, Var) and x.key() == key for x in e.walk())


def hoist_use(e, key):
    """e rearranged so that variable key is read before any call evaluated
    ahead of it, by swapping the operands of commutative machine operators
    (its value was computed first: inlining it must not move those calls in
    front of it).  None when that is not possible."""
    if not mentions(e, key) or isinstance(e, Var):
        return e
    if isinstance(e, BinOp):
        if mentions(e.a, key):
            a = hoist_use(e.a, key)
            return None if a is None else BinOp(e.op, a, e.b, e.vbtype)
        b = hoist_use(e.b, key)
        if b is None:
            return None
        if not has_call(e.a):
            return BinOp(e.op, e.a, b, e.vbtype)
        if e.op in COMMUTE and e.vbtype is None:
            return BinOp(e.op, b, e.a, e.vbtype)     # integer/float: a + b = b + a
        return None
    kids = list(e.children())
    for i, c in enumerate(kids):
        if mentions(c, key):
            if any(has_call(k) for k in kids[:i]):
                return None
            c2 = hoist_use(c, key)
            if c2 is None:
                return None
            return e if c2 is c else e.replace(lambda x: c2 if x is c else None)
    return e


def is_late(x):
    return bool(getattr(x, "late", False))


def vb_order(e, key, late_src=False):
    """The calls of expression e in the order VB evaluates them, with "KEY"
    where variable key is read.  In an operator expression VB first runs
    every early-bound call (and the receivers and arguments of late-bound
    ones) left to right, then the late-bound member calls, innermost and
    leftmost first; the arguments of an early-bound call are each evaluated
    completely, in order (tests_wip/lateorder: `"x" & O(1).Cnt & F(2)` runs
    O(1), F(2), .Cnt; `G O(1).Cnt, F(2)` runs O(1), .Cnt, F(2)).  late_src:
    the value inlined for key is itself a late-bound call."""
    early, late = [], []

    def walk(x):
        if isinstance(x, Var) and x.key() == key:
            (late if late_src else early).append("KEY")
        elif is_late(x):
            for c in x.children():
                walk(c)
            late.append(x)
        elif isinstance(x, (Call, Prop)):
            for c in x.children():
                early.extend(vb_order(c, key, late_src))    # each argument completely
            if not (isinstance(x, Call) and x.pure) and \
                    not (isinstance(x, Prop) and getattr(x, "control", None) is not None):
                early.append(x)     # (a pure TypeName / CStr sits where its value is
                                    # used, not where it ran: its order is no evidence)
        else:
            for c in x.children():
                walk(c)
    walk(e)
    return early + late


def key_first(e, key, late_src=False):
    """Nothing VB evaluates in e before the read of key is a call."""
    order = vb_order(e, key, late_src)
    return "KEY" not in order or order.index("KEY") == 0


def hoist_late(s, key, late_src):
    """For statements with late-bound calls: s itself when inlining key keeps
    VB's evaluation order (no operand swapping here), else None."""
    es = [s.src] if isinstance(s, Assign) else [s.e] if isinstance(s, (ExprStmt, CallStmt)) \
        else None
    if es is None:
        return s
    return s if all(key_first(e, key, late_src) for e in es) else None


def has_late(e):
    return any(is_late(x) for x in e.walk())


def hoist_stmt(s, key):
    """hoist_use applied to what a statement evaluates; None if impossible."""
    if isinstance(s, Assign):
        src = hoist_use(s.src, key)
        if src is None:
            return None
        if src is s.src:
            return s
        n = Assign(s.dst, src, s.kind)
    elif isinstance(s, (ExprStmt, CallStmt)):
        e = hoist_use(s.e, key)
        if e is None:
            return None
        if e is s.e:
            return s
        n = type(s)(e)
    else:
        return s
    return keep_flags(n, s)


def address_taken(body):
    """Keys of variables whose address is passed somewhere (&v)."""
    out = set()
    for _, ss, br in body:
        for s in ss:
            for e in stmt_exprs(s):
                for x in e.walk():
                    if isinstance(x, ir.AddrOf) and isinstance(x.e, Var):
                        out.add(x.e.key())
    return out


def mem_root(x):
    """Which memory an element or field expression is in: ("elem", array)
    for an element of an array or a field of one, ("field", variable) for a
    field of a variable (or of what a pointer variable points at), None
    through a computed address."""
    if isinstance(x, ir.Index):
        return ("elem", x.arr.key() if isinstance(x.arr, Var) else mem_root(x.arr))
    if isinstance(x, Deref):
        if isinstance(x.e, ir.Index):
            return mem_root(x.e)
        if isinstance(x.e, Var):
            return ("field", x.e.key())
    return None


def safe_between(body, bi, si, tbi, tsi, src, arrays=None):
    """No call or assignment between definition and use may change src.  A
    value read from an element or field stays where it is when a store
    between may overwrite it (VBDoom G_PlayerReborn: killcount =
    players(p).killcount, then players(p) = zeroPlayer, then
    players(p).killcount = killcount); mem_root tells them apart.  (A
    store through a computed pointer counts for those only.)"""
    srcvars = {x.key() for x in src.walk() if isinstance(x, Var)}
    if getattr(src, "control", None) is not None:
        return True                 # a form's control reference does not change
    if isinstance(src, Var) and getattr(src, "iface", None) is not None:
        return True                 # nor does the Err / App object
    has_call = any(isinstance(x, Call) or isinstance(x, Prop) for x in src.walk())
    # (what a procedure may change.  Not a parameter's value or field unless it is given that
    # parameter.  Not an element's offset, (i - lb) * size, ever a compiler's temporary: one held over
    # the calls for the arguments before it is the statement's own subscript, with i read then)
    params = {x.e.key() for x in src.walk() if isinstance(x, Deref) and isinstance(x.e, Var) and
              x.e.space == "arg"}
    in_memory = arrays is not None and not element_offset(src, arrays) and \
        reads_memory(src.replace(lambda x: Const(0) if isinstance(x, Deref) and isinstance(x.e, Var) and
                                 x.e.space == "arg" else None), arrays)
    if arrays is None or element_offset(src, arrays):
        params = set()
    roots = {mem_root(x) for x in src.walk() if isinstance(x, (Deref, ir.Index))}
    # what a call in src is given may come back changed: nothing between may read it
    # first (a target Form8: n = GetWindowsDirectory(s, 255): t = s: Left(t, n)
    # became t = s: Left(t, GetWindowsDirectory(s, 255)), reading s before the call)
    given = set()
    for x in src.walk():
        if isinstance(x, Call) and not x.pure:
            for y in x.args:
                y = y.e if isinstance(y, ir.AddrOf) else y
                if isinstance(y, Var):
                    given.add(y.key())
    for i in range(bi, tbi + 1):
        a, ss, br = body[i]
        lo = si + 1 if i == bi else 0
        hi = (tsi if tsi >= 0 else len(ss)) if i == tbi else len(ss)
        for j in range(lo, hi):
            s = ss[j]
            if isinstance(s, Assign) and isinstance(s.dst, Var) and s.dst.key() in srcvars:
                return False
            if given and any(isinstance(y, Var) and y.key() in given
                             for e in stmt_exprs(s)
                             if not (isinstance(s, Assign) and e is s.dst)
                             for y in e.walk()):
                return False
            if has_call and (isinstance(s, (ExprStmt, CallStmt)) or
                             (isinstance(s, Assign) and
                              any(isinstance(x, (Call, Prop)) for x in s.src.walk()))):
                return False
            if roots and isinstance(s, Assign) and not isinstance(s.dst, Var):
                d = mem_root(s.dst)
                if d in roots:
                    return False        # the same element / field may be overwritten
            if (in_memory or params) and any(isinstance(x, Call) and not x.pure or
                                             isinstance(x, Prop) and x.control is None
                                             for e in stmt_exprs(s) for x in e.walk()):
                if in_memory or any(isinstance(x, Var) and x.key() in params
                                    for e in stmt_exprs(s) for x in e.walk()):
                    return False        # a procedure may change what was read
    return True


def unaddr_literals(e):
    """&literal (a temporary VB filled with the literal, passed ByRef) is the
    literal itself."""
    def f(x):
        if isinstance(x, ir.AddrOf) and isinstance(x.e, (Const, Str)):
            return x.e
        return None
    return e.replace(f)


def substitute(s, v, src):
    if isinstance(src, (Const, Str)):
        n = substitute(s, v, Var("__lit__", 0))     # put it in place, then unwrap
        return substitute_expr(n, lambda x: src if isinstance(x, ir.AddrOf) and
                               isinstance(x.e, Var) and x.e.space == "__lit__" else
                               (src if isinstance(x, Var) and x.space == "__lit__" else None))
    mapping = {v.key(): src}
    if isinstance(s, Assign):
        n = Assign(s.dst if s.dst == v else ir.subst(s.dst, mapping),
                   ir.subst(s.src, mapping), s.kind)
    elif isinstance(s, (ExprStmt, CallStmt)):
        n = type(s)(ir.subst(s.e, mapping))
    elif isinstance(s, ir.Return):
        n = ir.Return()
        n.value = ir.subst(s.value, mapping) if getattr(s, "value", None) is not None else None
    else:
        return s
    return keep_flags(n, s)         # (as substitute_expr: a value, never a tag word)


# ---------------------------------------------------------------- liveness
def node_succs(lf, a):
    # every node in lf.nodes counts: the passes add split and copied blocks
    # there (a successor outside the body just has nothing live in it)
    n = lf.nodes.get(a)
    return list(n.succs) + getattr(n, "flow_extra", []) if n is not None else []


def var_reads(s):
    """Keys of variables a statement reads (an Assign target is not a read,
    but anything inside a non-Var target is)."""
    out = []
    for e in stmt_exprs(s):
        if isinstance(s, Assign) and e is s.dst and isinstance(e, Var):
            continue
        out += [x.key() for x in e.walk() if isinstance(x, Var)]
    return out


def statement_starts(lf):
    """Where each statement starts, in address order: VB writes its number to [ebp - 4] in a procedure that may
    resume.  Statements are laid out in source order, so a va belongs to the last of these before it."""
    from capstone import x86 as X
    out = []
    for va in sorted(lf.p.insns):
        ins = lf.p.insns[va]
        ops = ins.operands
        if ins.mnemonic == "mov" and len(ops) == 2 and ops[0].type == X.X86_OP_MEM and \
                ops[0].mem.base == X.X86_REG_EBP and ops[0].mem.disp == -4 and ops[1].type == X.X86_OP_IMM:
            out.append(va)
    return out


STATEMENT_TEMP = 5000           # (rename_statement_temps' names, above rename_block_temps')


def branch_address(lf, a, last):
    """Where a block's branch is: its last instruction.  A jump table's tests have no instructions of their own, and
    their key holds the table's block (cfg.build): without it Select Case read the slot under another name than
    the statement's, no Case matched, and a target's settings file had spaces for its flags."""
    n = lf.nodes.get(a)
    if n is not None and n.insns:
        return n.insns[-1].address
    if isinstance(a, int) and a >= 1 << 40:
        m = lf.nodes.get((a >> 8) & 0xffffffff)
        if m is not None and m.insns:
            return m.insns[-1].address
    return last


def pin_statement_variables(body, lf):
    """Where an error goes on to the next statement, no statement is certain: x = x + 1 that fails leaves x as it
    was, and the next statement reads that.  Moving a value over a statement's edge is then no longer the
    same program: n = 32767: n = n + 1: Print #1, n came out Print #1, 32767 + 1, and the whole line was
    skipped.  (Nothing shows: it compiles, and the error is resumed from.)

    The compiler's own temporaries live and die inside a statement; a slot read in a statement that did not
    write it first is the source's variable, a hidden one at least (a For's limit, a With's object).  It
    gets the treatment of a String or Variant one the epilogue names (lf.pinned, is_named_local): all its stores
    stay, and it is neither renamed nor inlined.  tests/t101ornall."""
    if not any(isinstance(s, ir.OnError) and s.mode == "Resume Next" or
               isinstance(s, ExprStmt) and isinstance(s.e, Call) and s.e.name == "Resume"
               for _, ss, _ in body for s in ss):
        return
    starts = statement_starts(lf)
    if len(starts) < 2:
        return
    def number(va):
        return bisect.bisect_right(starts, va)

    written, handed = {}, {}
    reads = []
    pinned = set()
    each_flag = set()
    sums, derefed = set(), set()
    why = lf.pin_why = {}           # (tools/pinwhy.py)

    def local(k):
        return k[0] == "local" and isinstance(k[1], int) and k not in lf.arrays

    def note(s_or_e, va, is_stmt):
        """A slot handed over by its address may be written there: a Function's result, by way of
        F(&temp), read in the same statement.  Not read there, it is a ByRef variable (Foo x: Bar x)."""
        exprs = stmt_exprs(s_or_e) if is_stmt else [s_or_e]
        for e in exprs:
            if is_stmt and isinstance(s_or_e, Assign) and e is s_or_e.dst and isinstance(e, Var):
                continue
            addr = {}
            for x in e.walk():
                if isinstance(x, ir.AddrOf) and isinstance(x.e, Var) and local(x.e.key()):
                    addr[x.e.key()] = addr.get(x.e.key(), 0) + 1
                    handed.setdefault(x.e.key(), []).append(va)
            for x in e.walk():
                if isinstance(x, Var) and local(x.key()):
                    if addr.get(x.key()):
                        addr[x.key()] -= 1
                    else:
                        reads.append((x.key(), va))
    # (without what no one reads: the registers materialised at a loop's back edge read an element, and
    # the lift's saved copy of a slot about to be overwritten, for flags that are not used in the end)
    live = dead_by_liveness(body, lf)
    # (and a copy into a register counts only if the same statement goes on to read the register: over a join in
    # an expression.  VB holds nothing there from one statement to the next, whatever liveness sees)
    reg_read = {}
    for a, ss, br in live:
        last = None
        for s in ss:
            last = getattr(s, "va", None) or last
            for k in var_reads(s):
                if k[0] in ("tmp", "reg") and last is not None:
                    reg_read.setdefault(k, []).append(last)
        at = branch_address(lf, a, last)
        if br is not None and at is not None:
            for x in br.cond.walk():
                if isinstance(x, Var) and x.space in ("tmp", "reg"):
                    reg_read.setdefault(x.key(), []).append(at)
    for a, ss, br in live:
        last = None
        for s in ss:
            va = getattr(s, "va", None)
            if va is None:
                va = last
            if va is None:
                continue
            if isinstance(s, Assign) and isinstance(s.dst, Var) and s.dst.space in ("tmp", "reg") and \
                    not any(r >= va and number(r) == number(va) for r in reg_read.get(s.dst.key(), ())):
                continue
            last = va
            note(s, va, True)
            if isinstance(s, Assign) and isinstance(s.dst, Var) and s.dst.space == "local" and \
                    isinstance(s.dst.off, int):
                written.setdefault(s.dst.key(), []).append(va)
                if isinstance(s.src, ir.AddrOf) or isinstance(s.src, BinOp) and s.src.op == "+":
                    sums.add(s.dst.key())
            for e in stmt_exprs(s):
                for x in e.walk():
                    if isinstance(x, Deref) and isinstance(x.e, Var) and local(x.e.key()):
                        derefed.add(x.e.key())
            if isinstance(s, Assign) and isinstance(s.src, Call) and \
                    s.src.name.startswith(("ForEach", "NextEach", "VarFor")):
                # (For Each's own, and a Variant For's: the flag goes from the head to the Next, in a slot,
                # by way of eax.  Two names for it and no loop came out, and nothing ran: a target's index
                # file was never written)
                if isinstance(s.dst, Var):
                    each_flag.add(s.dst.key())
            elif isinstance(s, Assign) and isinstance(s.src, Var) and s.src.key() in each_flag and \
                    isinstance(s.dst, Var) and local(s.dst.key()):
                pinned.add(s.dst.key())
                why.setdefault(s.dst.key(), ("For Each's flag", va, s))
        if br is not None:
            va = branch_address(lf, a, last)
            if va is not None:
                note(br.cond, va, False)
    read_at = {}
    for k, va in reads:
        read_at.setdefault(k, []).append(va)
    for k, va in reads:
        here = number(va)
        # (before it, strictly: x = x + 1 reads what some other statement wrote)
        if not any(w < va and number(w) == here for w in written.get(k, []) + handed.get(k, [])):
            pinned.add(k)
            why.setdefault(k, ("read in statement %d" % here, va, None))
    for k, vas in handed.items():
        for va in vas:
            here = number(va)
            if not any(w < va and number(w) == here for w in written.get(k, [])) and \
                    not any(r > va and number(r) == here for r in read_at.get(k, [])):
                pinned.add(k)
                why.setdefault(k, ("handed over in statement %d and not read there" % here, va, None))
    # a With's pointer to an element goes round its statements, and it is no value of the source: it folds as ever
    # (With m(n): v614 = m(n) as a Type's copy, and its fields written to that: gh_zip AddFile)
    pointers = sums & derefed
    pinned -= pointers
    lf.pinned = {k[1] for k in pinned}
    lf.statement_temps = (starts, {k for k in set(written) | set(handed) if k not in pinned and k not in pointers and
                                   k[1] not in lf.named and k[1] != lf.result_slot and k not in lf.arrays and
                                   not lf.udt.get(k)})


def rename_statement_temps(body, lf):
    """What pin_statement_variables left are temporaries, each slot in pool for the whole procedure: one name for
    each statement (an object here, a number there, and one type for all: vF0 = v24 - 1 As Object).  Then
    each statement's are single and can go back in where they came from, in order, which is what makes a
    failing statement fail whole: Print #1, c("no"); "x" must not print."""
    info = getattr(lf, "statement_temps", None)
    if not info or not info[1]:
        return body
    starts, temps = info
    names = {}

    def f_at(n):
        def f(x):
            if isinstance(x, Var) and x.key() in temps:
                return names.setdefault((x.key(), n), Var("local", (x.off, STATEMENT_TEMP + n)))
            return None
        return f
    out = []
    for a, ss, br in body:
        keep = []
        last = None
        for s in ss:
            va = getattr(s, "va", None)
            if va is None:
                va = last
            if va is None:
                keep.append(s)
                continue
            last = va
            f = f_at(bisect.bisect_right(starts, va))
            n = substitute_expr(s, f) if not isinstance(s, ir.Return) else s
            if isinstance(n, Assign) and isinstance(n.dst, Var) and n.dst.key() in temps:
                d = f(n.dst)
                m = Assign(d, n.src, n.kind)
                n = keep_flags(m, s)
            keep.append(n)
        at = branch_address(lf, a, last)
        if br is not None and at is not None:
            br = type(br)(br.cond.replace(f_at(bisect.bisect_right(starts, at))))
        out.append((a, keep, br))
    return fold_handovers(out)


def fold_handovers(body):
    """t = x: x = 0 between two of a statement's temporaries: the compiler hands a result over, and it is one value.
    Printed, t = x reads an object's default property: Set x = lv.ListItems.Add(...), t = x, Set v = t, so
    v.Tag went nowhere, unseen under On Error Resume Next, and what the list's Tag was for came back
    empty.  tests/t100ornobj."""
    def temp(v):
        return isinstance(v, Var) and v.space == "local" and isinstance(v.off, tuple) and \
            v.off[1] >= STATEMENT_TEMP
    same = {}
    out = []
    for a, ss, br in body:
        keep = []
        i = 0
        while i < len(ss):
            s = ss[i]
            n = ss[i + 1] if i + 1 < len(ss) else None
            if isinstance(s, Assign) and temp(s.dst) and temp(s.src) and isinstance(n, Assign) and \
                    isinstance(n.dst, Var) and n.dst.key() == s.src.key() and \
                    isinstance(n.src, Const) and n.src.value == 0:
                same[s.dst.key()] = s.src
                i += 2
                continue
            keep.append(s)
            i += 1
        out.append((a, keep, br))
    if not same:
        return body

    def f(x):
        return same.get(x.key()) if isinstance(x, Var) else None
    return [(a, [substitute_expr(s, f) if not isinstance(s, ir.Return) else s for s in ss],
             type(br)(br.cond.replace(f)) if br is not None else br) for a, ss, br in out]


def mark_error_regions(body, lf):
    """Tag the statements that run under On Error Resume Next.  An error
    part-way through one of them leaves its assignment undone (VB resumes at
    the next statement), so no later pass may treat such a store as certain:
    what was in the variable before it can still be read.  ConvertToJson's
    LBound/UBound(a, 2) of a 1-D array fail there on purpose, keeping the -1
    initialiser alive."""
    out_mode = {}

    def in_mode(a):
        n = lf.nodes.get(a)
        return any(out_mode.get(p) for p in (n.preds if n is not None else []))

    for _ in range(len(body) + 2):
        changed = False
        for a, ss, br in body:
            m = in_mode(a)
            for s in ss:
                if isinstance(s, ir.OnError):
                    m = s.mode == "Resume Next"
            if out_mode.get(a) != m:
                out_mode[a] = m
                changed = True
        if not changed:
            break
    known = getattr(lf, "err_vas", None)
    if known is None:
        known = lf.err_vas = set()
    for a, ss, br in body:
        m = in_mode(a)
        for s in ss:
            if isinstance(s, ir.OnError):
                m = s.mode == "Resume Next"
            v = getattr(s, "va", None)
            if not m and v is not None:
                m = v in known            # a copy of a region statement (the
            if m and v is not None:       # passes duplicate blocks)
                known.add(v)
            s.err_skipped = m


def may_skip(s):
    """s runs under On Error Resume Next: it may end before its store."""
    return getattr(s, "err_skipped", False)


def dead_by_liveness(body, lf):
    """Drop assignments to compiler-owned variables (registers, unnamed
    locals) whose value no path reads before it is overwritten."""
    mark_error_regions(body, lf)
    idx = {a: i for i, (a, _, _) in enumerate(body)}

    taken = address_taken(body)

    def owned(v):
        if not isinstance(v, Var) or v.key() in taken:
            return False                # passed by reference: the callee may read it
        if v.space == "local" and lf.result_slot is not None and v.off == lf.result_slot:
            return False                # the Function's value: read at every exit
        if v.space in ("reg", "tmp", "stk"):
            return True
        if v.space == "local":
            return not lf.is_named_local(v)
        # a ByVal parameter the compiler used as scratch: VB code never
        # assigns to one, so an unread store to it is the compiler's
        return v.space == "arg" and isinstance(v.off, int) and v.off not in lf.byref
    live_in = block_live_in(body, lf)
    always = handler_live(live_in, lf, body)
    out = []
    for a, ss, br in body:
        live = set(always)
        for t in node_succs(lf, a):
            live |= live_in.get(t, set())
        if br is not None:
            live |= {x.key() for x in br.cond.walk() if isinstance(x, Var)}
        keep = []
        for s in reversed(ss):
            if isinstance(s, Assign) and owned(s.dst) and s.dst.key() not in live and \
                    (has_effect(s.src) or not raises_here(s) or
                     # (a register still holding what a statement before it computed: nothing is
                     # computed again.  v(i) = CLng(a(j)), and eax = that conversion, unread, left
                     # t_eax = CLng((a.fC + o)) printed after it: Invalid qualifier)
                     s.dst.space == "reg" and (repeats_earlier(ss, s) or only_converts(s.src))):
                if not has_effect(s.src):
                    continue
                e = s.src
                while isinstance(e, Call) and e.name in CONVERSIONS and len(e.args) == 1 and \
                        isinstance(e.args[0], Call):
                    e = e.args[0]                    # (CDbl Random(-2, 2) is no statement)
                if isinstance(e, Call) and getattr(e, "loaded", False) and not e.args and \
                        isinstance(e.obj, Var):
                    # a late bound read stays one: a statement calls it as a method, and a Property
                    # Get takes that as error 450 (k = o.Reads + 1 with k unused, t96sbfix)
                    live |= set(var_reads(s))
                    keep.append(s)
                    continue
                if isinstance(e, Call) and e.name in CONVERSIONS:
                    # (CInt(Proc() - g): no statement, "CInt Proc() - g" a syntax error; Proc still runs)
                    live |= set(var_reads(s))
                    keep.append(s)
                    continue
                if isinstance(e, Call):              # value unused: just the call
                    n = ExprStmt(e)
                    n.va = getattr(s, "va", None)
                    live |= set(var_reads(n))
                    keep.append(n)
                    continue
            if isinstance(s, Assign) and isinstance(s.dst, Var) and not may_skip(s) and \
                    s.dst.key() not in always:
                live.discard(s.dst.key())
            live |= set(var_reads(s))
            keep.append(s)
        out.append((a, keep[::-1], br))
    return out


def handler_live(live_in, lf, body=None):
    """The frame variables an error handler reads before setting them: any statement may
    jump there, and Resume comes back to any statement, neither of them an edge of the
    graph.  a target: If Err = 55 And n < 10 Then n = n + 1: Resume lost the n = n + 1,
    so a locked file was retried for ever; v = -1 ... handler: If v = -1 lost the v = -1."""
    out = set()
    for h in set(getattr(lf, "handlers", ())) | set(getattr(lf.p, "handlers", {}).values()):
        for k in live_in.get(h, ()):
            if k[0] in ("local", "arg"):
                out.add(k)
    if body is not None and any(
            isinstance(s, ExprStmt) and isinstance(s.e, Call) and s.e.name == "Resume" and
            getattr(s.e, "stmt_fmt", None) for _, ss, _ in body for s in ss):
        out |= set_for_resume(body, lf)
    return out


def set_for_resume(body, lf):
    """Resume and Resume Next go back to statements no edge shows, so what a handler sets for them counts
    where it is read (h: z = 2: Resume, for 6 / z: t12resume hung without it).  What a handler assigns, and
    some block reads before setting it.  (Not everything read anywhere: the compiler's temporaries, one
    slot for a constant here and an element's offset there, have to stay renamable block by block:
    a target's a(i).f4 printed a.fC + v434 * 40 + 4.)"""
    idx = {a: i for i, (a, _, _) in enumerate(body)}
    seen, work = set(), [h for h in set(getattr(lf, "handlers", ())) |
                         set(getattr(lf.p, "handlers", {}).values()) if h in idx]
    while work:
        a = work.pop()
        if a in seen:
            continue
        seen.add(a)
        work.extend(t for t in node_succs(lf, a) if t in idx)
    assigned = {s.dst.key() for a in seen for s in body[idx[a]][1]
                if isinstance(s, Assign) and isinstance(s.dst, Var) and s.dst.space in ("local", "arg")}
    if not assigned:
        return set()
    exposed = set()
    for _, ss, br in body:
        done = set()
        for s in ss:
            exposed |= set(var_reads(s)) - done
            if isinstance(s, Assign) and isinstance(s.dst, Var):
                done.add(s.dst.key())
        if br is not None:
            exposed |= {x.key() for x in br.cond.walk() if isinstance(x, Var)} - done
    return assigned & exposed


def block_live_in(body, lf):
    """The variables live on entry to each block, to a fixed point.  A fact
    travels back one block per round at least, along a path that repeats no
    block, so len(body) + 1 rounds settle it whatever the order; more mean
    two blocks share a key and overwrite each other's set for ever.
    Each block is summarised once, live in = (live out - kill) | gen, so a
    round does not walk the statements again."""
    summary = []                        # per block (by position: keys can repeat)
    for a, ss, br in body:
        gen, kill = set(), set()
        for s in reversed(ss):
            if isinstance(s, Assign) and isinstance(s.dst, Var) and not may_skip(s):
                kill.add(s.dst.key())
                gen.discard(s.dst.key())
            gen |= set(var_reads(s))
        if br is not None:              # read before the statements' effects
            gen |= {x.key() for x in br.cond.walk() if isinstance(x, Var)} - kill
        summary.append((a, gen, kill))
    live_in = {a: set() for a, _, _ in body}
    for _ in range(len(body) + 2):
        changed = False
        always = handler_live(live_in, lf, body)
        for a, gen, kill in reversed(summary):
            live = set(always)
            for t in node_succs(lf, a):
                live |= live_in.get(t, set())
            live = (live - kill) | gen | always
            if live != live_in[a]:
                live_in[a] = live
                changed = True
        if not changed:
            return live_in
    from collections import Counter
    dup = [a for a, n in Counter(a for a, _, _ in body).items() if n > 1]
    raise RuntimeError("liveness does not settle in %s (blocks sharing a key: %s)" % (
        lf.p.name, ", ".join("%r" % a for a in dup) or "none"))


def live_out_sets(body, lf):
    mark_error_regions(body, lf)
    live_in = block_live_in(body, lf)
    always = handler_live(live_in, lf, body)
    out = {}
    for a, ss, br in body:
        live = set(always)
        for t in node_succs(lf, a):
            live |= live_in.get(t, set())
        out[a] = live
    return out


def rename_block_temps(body, lf):
    """Within a block, every redefinition of a compiler-owned slot except
    the last starts a fresh variable; the last one too when its value is
    not needed after the block (staging slots VB reuses everywhere)."""
    out = []
    seq = [1000]
    live_out = live_out_sets(body, lf)
    # (a handler may read any store it can follow: t77 v = 1: Print: v = -1: Error 5)
    in_handler = handler_live(block_live_in(body, lf), lf, body)
    mark_error_regions(body, lf)
    for a, ss, br in body:
        if br is not None:
            brvars = {x.key() for x in br.cond.walk() if isinstance(x, Var)}
        else:
            brvars = set()
        last_def = {}
        defs = {}
        for i, s in enumerate(ss):
            if isinstance(s, Assign) and isinstance(s.dst, Var) and \
               s.dst.space in ("local", "reg", "stk") and (
                   isinstance(s.dst.off, (int, str)) or
                   # (a statement's temporary: its block copied by duplicate_tails has two definitions)
                   isinstance(s.dst.off, tuple) and s.dst.off[1] >= STATEMENT_TEMP) and \
               not lf.is_named_local(s.dst):
                last_def[s.dst.key()] = i
                defs.setdefault(s.dst.key(), []).append(i)
        # what each statement still has live in front of it (the same walk
        # dead_by_liveness does, may-throw stores included)
        live = set(live_out[a]) | brvars
        before = {}
        for i in range(len(ss) - 1, -1, -1):
            before[i] = live
            s = ss[i]
            if isinstance(s, Assign) and isinstance(s.dst, Var) and not may_skip(s):
                live.discard(s.dst.key())
            live |= set(var_reads(s))
        cur = {}
        keep = []
        for i, s in enumerate(ss):
            s2 = rename_stmt(s, cur)
            if isinstance(s, Assign) and isinstance(s.dst, Var) and s.dst.key() in last_def:
                k = s.dst.key()
                ds = defs[k]
                nxt = ds[ds.index(i) + 1] if i != ds[-1] else None
                # a store starts a new value only when it certainly runs, or
                # when nothing reads the old one if it is skipped by an error
                ends = k not in in_handler and (
                    (nxt is not None and (not may_skip(ss[nxt]) or k not in before[nxt])) or
                    (nxt is None and k not in live_out[a]))
                if ends:
                    seq[0] += 1
                    new = Var(s.dst.space, (s.dst.off[0] if isinstance(s.dst.off, tuple) else s.dst.off, seq[0]))
                    if isinstance(s.src, Var) and s.src.space in ("member", "global") and k in lf.objtype:
                        # (Form3.EncryptIt copied for its method: o1032 As App, the slot's other
                        # temporary, printed without Set)
                        lf.objtype.setdefault(new.key(), lf.objtype[k])
                    # (whole_variant: the value of a Variant built in place, no
                    # tag word: fold_variant_slot_reads read Opt3b(4, n, 1)'s 1
                    # as VT_NULL and passed Null - tests/t20misc)
                    s2 = keep_flags(Assign(new, s2.src, s.kind), s)
                    cur[k] = new
                else:
                    cur.pop(k, None)
            keep.append(s2)
        out.append((a, keep, br if br is None else type(br)(rename_reads(br.cond, cur))))
    return out


def drop_result_out(body, lf):
    """A method returns by writing its result slot through the last
    argument; that store is the Function's return, not a statement."""
    if lf.obj is None or lf.result_slot is None:
        return body
    import struct
    from .machine import Hi
    last = lf.p.ret_pop

    def through_ptr(s):
        return isinstance(s, Assign) and isinstance(s.dst, Deref) and \
            isinstance(s.dst.e, Var) and s.dst.e.space == "arg" and s.dst.e.off == last
    m = getattr(lf.p, "member", None)
    rtype = m.ret_type if m is not None else None
    out = []
    for a, ss, br in body:
        keep = []
        halves = {}
        for s in ss:
            if through_ptr(s):
                halves[s.dst.off] = s.src
        for s in ss:
            if through_ptr(s):
                if getattr(s, "fp_store", None):
                    lf.__dict__.setdefault("result_fp", set()).add(s.fp_store)
                if lf.result_slot == "ret" and s.dst.off == 0:
                    # no frame slot holds the result: the value written through
                    # the pointer is the Function's value (an 8-byte one in two
                    # halves: Hi(x) is x's, two literals make one)
                    v, hi = s.src, halves.get(4)
                    if isinstance(v, Var) and isinstance(hi, Var) and v.space == hi.space == \
                            "local" and isinstance(v.off, int) and hi.off == v.off + 4:
                        v = Var(v.space, v.off, vbtype=rtype if rtype in (
                            "Double", "Currency", "Date") else "Double")  # the 8-byte local
                    if isinstance(v, Const) and isinstance(hi, Const) and \
                            isinstance(v.value, int) and isinstance(hi.value, int):
                        raw = struct.pack("<II", v.value & 0xffffffff, hi.value & 0xffffffff)
                        if rtype == "Currency":
                            v = Const(struct.unpack("<q", raw)[0] / 10000.0, "Currency")
                        elif rtype in ("Double", "Date", "Variant", None):
                            v = Const(struct.unpack("<d", raw)[0], "Date" if rtype == "Date" else None)
                    n = Assign(Var("local", "ret"), v)
                    n.va = getattr(s, "va", None)
                    keep.append(n)
                continue                # the copy out (and an upper half)
            keep.append(s)
        out.append((a, keep, br))
    return out


def fold_elem_addresses(body, lf):
    """&a(i) + offset once the offset's parts are known: a field of the element (the subscript of a
    fixed array inside it was a variable when lifted: With o(n): .jalki(2) = VSet(...) printed
    o(n) + 1 * 8 + 24, a Syntax error)."""
    def terms(e, k=1):
        if isinstance(e, BinOp) and e.op == "+":
            return terms(e.a, k) + terms(e.b, k)
        if isinstance(e, BinOp) and e.op == "-":
            return terms(e.a, k) + terms(e.b, -k)
        if isinstance(e, BinOp) and e.op == "*" and isinstance(e.b, Const) and \
                isinstance(e.b.value, int):
            return terms(e.a, k * e.b.value)
        if isinstance(e, BinOp) and e.op == "*" and isinstance(e.a, Const) and \
                isinstance(e.a.value, int):
            return terms(e.b, k * e.a.value)
        return [(e, k)]

    def fix(x):
        if isinstance(x, Deref) and isinstance(x.e, BinOp) and x.e.op in ("+", "-"):
            # *(a1 + (i - 1) * 4 + 120): an element of an array in a record parameter, written
            # through its address (a String: r.jonot(i) = "j" printed (a1 + ...) = "j")
            ts = terms(x.e)
            bases = [t for t, k in ts if isinstance(t, Var) and t.space == "arg" and k == 1 and
                     isinstance(t.off, int) and t.off >= 4]
            if len(bases) == 1:
                base, off, rest = bases[0], x.off, []
                for t, k in ts:
                    if t is base:
                        continue
                    if isinstance(t, Const) and isinstance(t.value, int):
                        off += t.value * k
                    else:
                        rest.append((t, k))
                if rest:
                    idx = None
                    for t, k in rest:
                        part = t if k == 1 else BinOp("*", t, Const(k))
                        idx = part if idx is None else BinOp("+", idx, part)
                    scale, lf.scale = getattr(lf, "scale", 1), 1
                    try:
                        el = lf.element_inline(base, idx, off, x.size)
                    finally:
                        lf.scale = scale
                    return el
                return Deref(base, x.size, off)
            # *(&a(i) + j * 2 + 1000): the field of an element, the read's own offset included
            # (else that offset ended up outside the array: With rob(n), optimised, made the
            # Type an array of Integers from its first byte)
            addr = [t for t, k in ts if isinstance(t, ir.AddrOf) and k == 1 and (
                isinstance(t.e, ir.Index) or isinstance(t.e, Deref) and isinstance(t.e.e, ir.Index))]
            if len(addr) == 1:
                base = addr[0]
                elem, off = (base.e, x.off) if isinstance(base.e, ir.Index) else (
                    base.e.e, base.e.off + x.off)
                rest = []
                for t, k in ts:
                    if t is base:
                        continue
                    if isinstance(t, Const) and isinstance(t.value, int):
                        off += t.value * k
                    else:
                        rest.append((t, k))
                if not rest:
                    d = Deref(elem, x.size, off)
                    d.vbtype = getattr(x, "vbtype", None)
                    return d
                idx = None
                for t, k in rest:
                    part = t if k == 1 else BinOp("*", t, Const(k))
                    idx = part if idx is None else BinOp("+", idx, part)
                scale, lf.scale = getattr(lf, "scale", 1), 1
                try:
                    el = lf.element_inline(elem, idx, off, x.size)
                finally:
                    lf.scale = scale
                if el is not None:
                    return el
            return None
        if not (isinstance(x, BinOp) and x.op == "+"):
            return None
        ts = terms(x)
        bases = [t for t, k in ts if isinstance(t, ir.AddrOf) and k == 1 and (
            isinstance(t.e, ir.Index) or isinstance(t.e, Deref) and isinstance(t.e.e, ir.Index))]
        if len(bases) != 1:
            return None
        base = bases[0]
        elem, off = (base.e, 0) if isinstance(base.e, ir.Index) else (base.e.e, base.e.off)
        rest = []
        for t, k in ts:
            if t is base:
                continue
            if isinstance(t, Const) and isinstance(t.value, int):
                off += t.value * k
            else:
                rest.append((t, k))
        if not rest:
            return ir.AddrOf(Deref(elem, 4, off))
        idx = None
        for t, k in rest:
            part = t if k == 1 else BinOp("*", t, Const(k))
            idx = part if idx is None else BinOp("+", idx, part)
        scale, lf.scale = getattr(lf, "scale", 1), 1
        try:
            el = lf.element_inline(elem, idx, off, 4)
        finally:
            lf.scale = scale
        return ir.AddrOf(el) if el is not None else None
    out = []
    from .lift import BoundsAt
    bounds = lf.index_bounds
    try:
        for a, ss, br in body:
            keep = []
            for s in ss:
                lf.index_bounds = BoundsAt(lf.bounds_log, getattr(s, "va", None) or a)
                keep.append(substitute_expr(s, fix))
            lf.index_bounds = BoundsAt(lf.bounds_log, a if not keep else
                                       getattr(keep[-1], "va", None) or a)
            out.append((a, keep, type(br)(br.cond.replace(fix)) if br is not None else None))
    finally:
        lf.index_bounds = bounds
    return out


def whole_small_results(body, lf):
    """x.f0 = F() of a Function whose record comes back in edx:eax, once merge_hi_stores has
    the two halves in one 8-byte store: the whole x = F() (t84recret v10.f0 = VSet(1, 1); left as
    a store to f0 it made x a record with one Double)."""
    def small(e):
        return isinstance(e, Call) and getattr(e, "small_rec", None)

    def whole(d, e):
        if isinstance(d, Deref) and d.off == 0 and isinstance(d.e, Var) and d.size == e.small_rec:
            return d.e
        return d
    out = []
    for a, ss, br in body:
        keep = []
        for s in ss:
            if isinstance(s, Assign) and small(s.src):
                nd = whole(s.dst, s.src)
                if nd is not s.dst:
                    s = s._keep(Assign(nd, s.src, s.kind)) if hasattr(s, "_keep") else Assign(nd, s.src, s.kind)
            keep.append(s)
        out.append((a, keep, br))
    return out


def merge_hi_stores(body):
    """x = v; [x+4] = Hi(v): the two halves of one 8-byte store (a Currency
    from __vbaCyAdd in edx:eax, gh_zip uLocal.Ext.USize = ... + lInput)."""
    from .machine import Hi

    def elem_pos(d):
        """(record, subscript, byte offset) of an element of an array inside a record."""
        x = d.e if isinstance(d, Deref) and isinstance(d.e, ir.Index) else d
        if isinstance(x, ir.Index) and len(x.idx) == 1 and isinstance(x.arr, Deref) and \
                getattr(x.arr, "inline", None):
            return (x.arr.e.key(), x.idx[0].key(), x.arr.off + (d.off if isinstance(d, Deref) else 0))
        return None

    def upper(d):
        """The 8-byte location whose upper half d is."""
        if isinstance(d, Deref) and d.size == 4 and d.off >= 4:
            return Deref(d.e, 8, d.off - 4), Deref(d.e, 4, d.off - 4)
        if isinstance(d, Var) and d.space in ("local", "global", "member") and \
                isinstance(d.off, int):
            return Var(d.space, d.off - 4), Var(d.space, d.off - 4)
        return None, None
    reads = {}
    for _, ss, br in body:
        for s in ss:
            for e in stmt_exprs(s):
                if isinstance(s, Assign) and e is s.dst:
                    continue
                for x in e.walk():
                    if isinstance(x, Var):
                        reads[x.key()] = reads.get(x.key(), 0) + 1
        if br is not None:
            for x in br.cond.walk():
                if isinstance(x, Var):
                    reads[x.key()] = reads.get(x.key(), 0) + 1
    # slots given a value of their own, not the upper half of something
    own = {t.dst.key() for _, ss, _ in body for t in ss if isinstance(t, Assign) and
           isinstance(t.dst, Var) and not isinstance(t.src, Hi)}
    out = []
    for a, ss, br in body:
        ss = list(ss)
        for i, s in enumerate(ss):
            if isinstance(s, Assign) and isinstance(s.src, Var) and s.src.space == "local" and \
                    isinstance(s.src.off, int) and reads.get(s.src.key()) == 1:
                # x.f = t: x.f+4 = t+4 with t+4 read nowhere else: an 8-byte
                # value copied in halves (a method's Date result buffer); not when t+4 was given
                # a value (two Singles side by side, copied to two side by side: DarwinBots regang)
                whole, lo = upper(s.dst)
                if whole is not None and s.src.key() not in own:
                    for j in range(i - 1, max(i - 4, -1), -1):
                        t = ss[j]
                        if isinstance(t, Assign) and t.dst == lo and isinstance(t.src, Var) and \
                                t.src.space == "local" and t.src.off == s.src.off - 4:
                            n = Assign(whole, Var("local", t.src.off))
                            n.va = getattr(t, "va", None)
                            ss[j], ss[i] = n, None
                            break
                continue
            if not (isinstance(s, Assign) and isinstance(s.src, Hi)):
                continue
            ep = elem_pos(s.dst)
            if ep is not None:
                # an element of a fixed array in a record: its halves are lifted as two arrays 4
                # bytes apart (r.rahat(i) = c printed .f58(i).f0 = c: .f4 = <Hi(c)>)
                done = False
                for j in range(i - 1, max(i - 6, -1), -1):
                    t = ss[j]
                    if isinstance(t, Assign) and t.src is not None and elem_pos(t.dst) == \
                            (ep[0], ep[1], ep[2] - 4) and (t.src is s.src.of or
                                                           t.src.key() == s.src.of.key()):
                        d = t.dst
                        nd = Deref(d.e, 8, d.off) if isinstance(d, Deref) else d
                        nd.vbtype = getattr(t.src, "vbtype", None)
                        n = Assign(nd, t.src)
                        n.va = getattr(t, "va", None)
                        ss[j], ss[i] = n, None
                        done = True
                        break
                if done:
                    continue
            whole, lo = upper(s.dst)
            if whole is None:
                continue
            # "t = x: x = x + d: [x+4] = Hi(t + d)": the lifter saved the old
            # value of x before overwriting it; compare with that undone
            alias = {}
            for t in ss[:i]:
                if isinstance(t, Assign) and isinstance(t.dst, Var) and t.dst.space == "tmp" and \
                        isinstance(t.src, Var):
                    alias[t.dst.key()] = t.src
            hk = ir.subst(s.src.of, alias).key() if alias else s.src.of.key()
            for j in range(i - 1, max(i - 6, -1), -1):
                t = ss[j]
                if isinstance(t, Assign) and t.dst == lo and t.src is not None and (
                        t.src is s.src.of or t.src.key() == s.src.of.key() or
                        alias and ir.subst(t.src, alias).key() == hk):
                    whole.vbtype = getattr(t.src, "vbtype", None)
                    n = Assign(whole, t.src)
                    n.va = getattr(t, "va", None)
                    ss[j], ss[i] = n, None
                    break
            else:
                # optimised code stores the upper half first (t85recretno VThru: mov [esp+0Ch], edx;
                # mov [esp+8], eax)
                for j in range(i + 1, min(i + 4, len(ss))):
                    t = ss[j]
                    if isinstance(t, Assign) and t.dst == lo and t.src is not None and (
                            t.src is s.src.of or t.src.key() == s.src.of.key()):
                        whole.vbtype = getattr(t.src, "vbtype", None)
                        n = Assign(whole, t.src)
                        n.va = getattr(t, "va", None)
                        ss[j], ss[i] = n, None
                        break
        out.append((a, [x for x in ss if x is not None], br))
    return out


def merge_result_halves(body, lf):
    """A method's 8-byte result (Double, Currency, Date) in its frame slot is
    stored as two dwords: one literal assignment to the Function name.  The
    other result types get their literals typed (Single bits, Boolean) and
    object results are Set."""
    import struct
    m = getattr(lf.p, "member", None)
    rt = m.ret_type if m is not None else None
    slot = lf.result_slot
    if slot is None or rt is None:
        return body
    out = []
    for a, ss, br in body:
        ss = list(ss)
        for i, s in enumerate(ss):
            if not (isinstance(s, Assign) and isinstance(s.dst, Var) and s.dst.space == "local"):
                continue
            if s.dst.off == slot and isinstance(s.src, Const) and isinstance(s.src.value, int):
                if rt == "Single":
                    ss[i] = Assign(s.dst, Const(struct.unpack("<f", struct.pack(
                        "<I", s.src.value & 0xffffffff))[0], "Single"))
                elif rt == "Boolean":
                    ss[i] = Assign(s.dst, Const(s.src.value, "Boolean"))
            if rt in ("Object", "Class") and s.dst.off == slot and s.kind != "set":
                ss[i] = Assign(s.dst, s.src, "set")
            if rt in ("Double", "Currency", "Date") and isinstance(slot, int) and \
                    s.dst.off == slot + 4 and isinstance(s.src, Const) and \
                    isinstance(s.src.value, int):
                lo = Const(0)                   # the zero VB initialised it with
                for j in range(i - 1, -1, -1):
                    t = ss[j]
                    if isinstance(t, Assign) and isinstance(t.dst, Var) and t.dst.key() == \
                            ("local", slot) and isinstance(t.src, Const) and \
                            isinstance(t.src.value, int):
                        lo = t.src
                        ss[j] = None
                        break
                raw = struct.pack("<II", lo.value & 0xffffffff, s.src.value & 0xffffffff)
                v = Const(struct.unpack("<q", raw)[0] / 10000.0, "Currency") if rt == "Currency" \
                    else Const(struct.unpack("<d", raw)[0], "Date" if rt == "Date" else None)
                n = Assign(Var("local", slot), v)
                n.va = getattr(s, "va", None)
                ss[i] = n
        out.append((a, [s for s in ss if s is not None], br))
    return out


def alias_byval_copies(body, lf):
    """VB copies every ByVal String argument into a local on entry
    (__vbaStrCopy); use the parameter itself instead of the copy."""
    if not body:
        return body
    a0, first, br0 = body[0]
    alias = {}
    for s in first:
        if isinstance(s, Assign) and isinstance(s.dst, Var) and s.dst.space == "local" and \
           isinstance(s.src, Var) and s.src.space == "arg" and count_defs(body, s.dst) == 1 and \
           s.dst.off in lf.kept:
            # VB's copies of ByVal Strings/Variants go to locals the exit cleanup
            # frees; any other "local = arg" is real code (r.Id = id in a UDT)
            alias[s.dst.key()] = s.src
            if s.dst.key() in lf.objtype:       # (ByVal a As IAnimal: what the copy was called through)
                lf.objtype.setdefault(s.src.key(), lf.objtype[s.dst.key()])
            if s.src.key() not in getattr(lf, "variants", ()) and \
                    s.dst.key() not in getattr(lf, "variants", ()):
                if s.kind != "set":                 # (Set: a ByVal Object's AddRef copy)
                    lf.byval_strings.add(s.src.off)     # a String copy (a Variant copy is VarDup)
            else:
                lf.variants.add(s.src.key())
    if not alias:
        return body

    def f(x):
        # the copy's frame address too (SP(k) in On Error Resume Next procedures,
        # gh_json ConvertToJson: VarType(&copy))
        if isinstance(x, SP) and ("local", x.off + 4) in alias:
            return ir.AddrOf(alias[("local", x.off + 4)])
        if isinstance(x, Var) and x.key() in alias:
            return alias[x.key()]
        return None
    out = []
    for a, ss, br in body:
        keep = []
        for s in ss:
            if isinstance(s, Assign) and isinstance(s.dst, Var) and s.dst.key() in alias:
                continue
            keep.append(substitute_expr(substitute_many(s, alias), f))
        if br is not None:
            br = type(br)(ir.subst(br.cond, alias).replace(f))
        out.append((a, keep, br))
    return out


def substitute_many(s, mapping):
    if isinstance(s, Assign):
        dst = s.dst if isinstance(s.dst, Var) else ir.subst(s.dst, mapping)
        n = Assign(dst, ir.subst(s.src, mapping), s.kind)
    elif isinstance(s, (ExprStmt, CallStmt)):
        n = type(s)(ir.subst(s.e, mapping))
        w = getattr(s.e, "whole_array", None)
        if w is not None:
            # (Get #n, , rob(r).mem(): it isn't among the arguments)
            n.e.whole_array = (ir.subst(w[0], mapping), w[1])
    elif isinstance(s, ir.Return):
        n = ir.Return()
        v = getattr(s, "value", None)
        n.value = ir.subst(v, mapping) if v is not None else None
    else:
        return s
    return keep_flags(n, s)     # (whole_variant lost here: v30 = 1 read as VT_NULL, CLng(Null))


def alias_reg_params(body, lf):
    """A ByVal parameter the compiler keeps in a register: the entry block
    copies it ("t_eax = a1") and the argument slot is never read again.
    Name the register after the parameter."""
    if not body:
        return body
    a0, first, br0 = body[0]
    mapping = {}
    for _, blk, _ in body[:1]:
      for s in blk:
        if isinstance(s, Assign) and isinstance(s.dst, Var) and s.dst.space == "reg" and \
           isinstance(s.src, Var) and s.src.space == "arg" and s.src.off not in lf.byref:
            arg = s.src
            reads = sum(1 for _, ss, br in body for t in ss for e in stmt_exprs(t)
                        for x in e.walk() if isinstance(x, Var) and x.key() == arg.key())
            reads += sum(1 for _, _, br in body if br is not None for x in br.cond.walk()
                         if isinstance(x, Var) and x.key() == arg.key())
            if reads == 1 and s.dst.key() not in mapping and count_defs(body, s.dst) == 1:
                mapping[s.dst.key()] = arg
    if not mapping:
        return body
    out = []
    for a, ss, br in body:
        keep = []
        for s in ss:
            if isinstance(s, Assign) and isinstance(s.dst, Var) and s.dst.key() in mapping and \
               isinstance(s.src, Var) and s.src == mapping[s.dst.key()]:
                continue
            if isinstance(s, Assign) and isinstance(s.dst, Var) and s.dst.key() in mapping:
                n = keep_flags(Assign(mapping[s.dst.key()], ir.subst(s.src, mapping), s.kind), s)
                keep.append(n)
                continue
            keep.append(substitute_many(s, mapping))
        if br is not None:
            br = type(br)(ir.subst(br.cond, mapping))
        out.append((a, keep, br))
    return out


def fold_me_copies(body):
    """"Set v = Me" right before the one statement that uses v: VB copies Me
    to pass it ByRef; print Me itself."""
    from .ir import Me
    defs = [(bi, si) for bi, (_, ss, _) in enumerate(body) for si, s in enumerate(ss)
            if isinstance(s, Assign) and isinstance(s.dst, Var) and isinstance(s.src, Me)]
    by_var = {}
    for bi, si in defs:
        by_var.setdefault(body[bi][1][si].dst.key(), []).append((bi, si))
    out = [(a, list(ss), br) for a, ss, br in body]
    for key, places in by_var.items():
        v = out[places[0][0]][1][places[0][1]].dst
        ok = True
        for bi, si in places:
            ss = out[bi][1]
            if si + 1 >= len(ss) or sum(1 for e in stmt_exprs(ss[si + 1]) for x in e.walk()
                                         if isinstance(x, Var) and x.key() == key) != 1:
                ok = False
        if not ok or count_uses(out, v) != len(places):
            continue
        for bi, si in places:
            ss = out[bi][1]
            ss[si + 1] = substitute_many(ss[si + 1], {key: Me()})
            ss[si] = None
    return [(a, [s for s in ss if s is not None], br) for a, ss, br in out]


def alias_arg_copies(body, lf):
    """A register or local whose every definition copies the same (never
    reassigned) parameter is that parameter."""
    defs = {}
    for _, ss, _ in body:
        for s in ss:
            if isinstance(s, Assign) and isinstance(s.dst, Var) and \
                    s.dst.space in ("reg", "local", "tmp"):
                defs.setdefault(s.dst.key(), []).append(s.src)
    mapping = {}
    names = getattr(lf.m, "global_names", {})
    for key, srcs in defs.items():
        first = srcs[0]
        # VB's Global object (App, Screen, Load, Unload): unoptimised code
        # keeps it in a frame temporary that looks like a named local
        vbglobal = isinstance(first, Var) and first.space == "global" and \
            names.get(first.off) == ""
        # a parameter, or a variable nothing assigns (Dim x As New C, whose
        # creation is implicit)
        if not (isinstance(first, Var) and (first.space == "arg" or vbglobal or
                                            first.key() in getattr(lf, "auto_new", {}))):
            continue
        if any(not (isinstance(x, Var) and x.key() == first.key()) for x in srcs):
            continue
        if count_defs(body, first):
            continue                    # the parameter itself is written
        if lf.is_named_local(Var(key[0], key[1])) and not vbglobal:
            continue
        mapping[key] = first
    if not mapping:
        return body
    out = []
    for a, ss, br in body:
        keep = []
        for s in ss:
            if isinstance(s, Assign) and isinstance(s.dst, Var) and s.dst.key() in mapping:
                continue
            keep.append(substitute_many(s, mapping))
        if br is not None:
            br = type(br)(ir.subst(br.cond, mapping))
        out.append((a, keep, br))
    return out


def alias_invariant_addrs(body, lf):
    """A register or temporary whose every definition is the address of the
    same variable ("edi = &m38" on both arms of an If, then passed ByRef
    after the join) is that address: substitute it at every use."""
    defs = {}
    for _, ss, _ in body:
        for s in ss:
            if isinstance(s, Assign) and isinstance(s.dst, Var) and \
                    s.dst.space in ("reg", "tmp", "stk"):
                defs.setdefault(s.dst.key(), []).append(s.src)
    mapping = {}
    for key, srcs in defs.items():
        first = srcs[0]
        if isinstance(first, Const) and isinstance(first.value, int) and key[0] == "reg":
            # a constant the compiler keeps in a register ("xor esi, esi")
            if any(not (isinstance(x, Const) and x.value == first.value) for x in srcs):
                continue
        elif not (isinstance(first, ir.AddrOf) and isinstance(first.e, Var) and
                  first.e.space in ("member", "global", "local", "arg")):
            continue
        elif any(not (isinstance(x, ir.AddrOf) and x.e == first.e) for x in srcs):
            continue
        if lf.is_named_local(Var(key[0], key[1])):
            continue
        mapping[key] = first
    if not mapping:
        return body

    def fold(x):
        # *(&v) is v
        if isinstance(x, Deref) and x.off == 0 and isinstance(x.e, Var) and \
                x.e.key() in mapping and isinstance(mapping[x.e.key()], ir.AddrOf):
            return mapping[x.e.key()].e
        return None

    def constants_last(x):
        # a substituted constant: x + 1, not 1 + x (For loop steps)
        if isinstance(x, BinOp) and x.op == "+" and isinstance(x.a, Const) and \
                not isinstance(x.b, Const):
            return BinOp("+", x.b.replace(constants_last), x.a, x.vbtype)
        return None

    out = []
    for a, ss, br in body:
        keep = []
        for s in ss:
            if isinstance(s, Assign) and isinstance(s.dst, Var) and s.dst.key() in mapping:
                continue
            s = substitute_many(substitute_expr(s, fold), mapping)
            keep.append(substitute_expr(s, constants_last))
        if br is not None:
            br = type(br)(ir.subst(br.cond.replace(fold), mapping).replace(constants_last))
        out.append((a, keep, br))
    return out


def coalesce_copies(body, lf):
    """A value the compiler keeps in a register is copied into it on every
    edge into a merge point ("t_ebx = t1" in each Case).  When the register's
    old value is dead where the temporary is computed, and every later write
    to the register is such a copy, compute the value into the register."""
    for _ in range(50):
        idx = {a: i for i, (a, _, _) in enumerate(body)}
        defs = {}
        for bi, (a, ss, br) in enumerate(body):
            for si, s in enumerate(ss):
                if isinstance(s, Assign) and isinstance(s.dst, Var):
                    defs.setdefault(s.dst.key(), []).append((bi, si))
        pairs = []
        for bi, (a, ss, br) in enumerate(body):
            for si, s in enumerate(ss):
                if isinstance(s, Assign) and isinstance(s.dst, Var) and s.dst.space == "reg" and \
                        isinstance(s.src, Var) and s.src.space == "tmp" and \
                        (s.dst.key(), s.src.key()) not in pairs:
                    pairs.append((s.dst.key(), s.src.key()))
        if not pairs:
            return body
        live_out = live_out_sets(body, lf)

        def reach(b):
            seen, stack = set(), list(node_succs(lf, body[b][0]))
            while stack:
                x = stack.pop()
                if x in seen or x not in idx:
                    continue
                seen.add(x)
                stack.extend(node_succs(lf, x))
            return {idx[x] for x in seen}

        done = False
        for rk, tk in pairs:
            tdefs = defs.get(tk, [])
            if len(tdefs) != 1:
                continue
            tb, ts = tdefs[0]
            later = reach(tb)
            ok = True
            for bi, si in defs.get(rk, []):
                if bi in later or (bi == tb and si > ts):
                    s = body[bi][1][si]
                    if not (isinstance(s.src, Var) and s.src.key() == tk):
                        ok = False          # another value lands in the register later
                        break
            if not ok:
                continue
            a, ss, br = body[tb]
            live = set(live_out[a])
            if br is not None:
                live |= {x.key() for x in br.cond.walk() if isinstance(x, Var)}
            for s in reversed(ss[ts + 1:]):
                if isinstance(s, Assign) and isinstance(s.dst, Var):
                    live.discard(s.dst.key())
                live |= set(var_reads(s))
            if rk in live:
                continue                    # the register's old value is still needed
            r = Var(rk[0], rk[1])
            mapping = {tk: r}
            out = []
            for bi, (a, ss, br) in enumerate(body):
                keep = []
                for si, s in enumerate(ss):
                    if (bi, si) == (tb, ts):
                        n = keep_flags(Assign(r, ir.subst(s.src, mapping), s.kind), s)
                        keep.append(n)
                        continue
                    if isinstance(s, Assign) and isinstance(s.dst, Var) and s.dst.key() == rk \
                            and isinstance(s.src, Var) and s.src.key() == tk:
                        continue            # the copy itself
                    keep.append(substitute_many(s, mapping))
                if br is not None:
                    br = type(br)(ir.subst(br.cond, mapping))
                out.append((a, keep, br))
            body = out
            done = True
            break
        if not done:
            return body
    return body


def missing_literals(body, lf):
    """DISP_E_PARAMNOTFOUND as a method's argument is an omitted one: printed as a number,
    Print1.Show &H80020004 overflowed (a target Tree, Run-time error 6)."""
    from .ir import Missing

    def is_it(a):
        return isinstance(a, Const) and isinstance(a.value, int) and \
            a.value & 0xffffffff == 0x80020004

    def fix(x):
        if isinstance(x, Call) and x.method and any(is_it(a) for a in x.args):
            args = [Missing() if is_it(a) else a.replace(fix) for a in x.args]
            obj = x.obj.replace(fix) if x.obj is not None else None
            return x._keep(Call(x.name, args, x.vbtype, x.raw, x.method, obj, x.pure))
        return None
    out = []
    for a, ss, br in body:
        out.append((a, [substitute_expr(s, fix) for s in ss], br))
    return out


def fold_field_pointer_regs(body, lf):
    """A callee-saved register the procedure sets once, to the address of a field of
    a record parameter ("lea esi, [eax + 0Dh]" for every use of a String * 21 field):
    that address wherever it's read.  Printed as a variable it took the record's type
    (a target Proc_90B720 t_esi = a1.fD, a Type mismatch)."""
    defs = {}
    for _, ss, _ in body:
        for s in ss:
            if isinstance(s, Assign) and isinstance(s.dst, Var) and s.dst.space == "reg":
                defs.setdefault(s.dst.key(), []).append(s.src)
    plan = {}
    for k, srcs in defs.items():
        x = srcs[0]
        if len(srcs) == 1 and k[1] in ("esi", "edi", "ebx") and isinstance(x, BinOp) and \
                x.op == "+" and isinstance(x.a, Var) and x.a.space == "arg" and \
                isinstance(x.b, Const) and isinstance(x.b.value, int):
            plan[k] = x
    if not plan:
        return body
    out = []
    for a, ss, br in body:
        keep = [substitute_many(s, plan) for s in ss
                if not (isinstance(s, Assign) and isinstance(s.dst, Var) and s.dst.key() in plan)]
        if br is not None:
            br.cond = ir.subst(br.cond, plan)
        out.append((a, keep, br))
    return out


def merged_byref_arg(lf, stmts, v):
    """A merged call's argument &k, where the block coming in built the Variant
    at k around a variable (tag VT_BYREF | vt at k, its address at k + 8): that
    is the variable passed ByRef, as Lifter.proc_arg does when the call is in
    the same block.  Read as a value, k is only the tag: 16386 instead of the
    variable (a target Proc_CED370: a spouse's number 16386 was out of range)."""
    if not (isinstance(v, ir.AddrOf) and isinstance(v.e, Var) and v.e.space == "local" and
            isinstance(v.e.off, int)):
        return v
    tag = ptr = None
    for s in stmts:
        if isinstance(s, Assign) and isinstance(s.dst, Var) and s.dst.space == "local":
            if s.dst.off == v.e.off:
                tag = s.src
            elif s.dst.off == v.e.off + 8:
                ptr = s.src
    if not (isinstance(tag, Const) and isinstance(tag.value, int) and tag.value & 0x4000 and
            not tag.value & 0x2000) or ptr is None:
        return v
    from .lift import through_variant
    ref = lf.frame_ptr(ptr)
    if isinstance(ref, ir.AddrOf) and isinstance(ref.e, Var):
        return through_variant(ref)
    if isinstance(ref, Var) and ref.space == "arg":
        return through_variant(ref)         # a ByRef parameter's own pointer
    if isinstance(ref, BinOp):
        ea = lf.element_address(ref)        # an element (see Lifter.by_ref)
        if isinstance(ea, ir.AddrOf):
            return through_variant(ea)
    return v


def unmerge_calls(body, lf, limit=14):
    """Undo the compiler's merging of identical calls: a small block entered
    unconditionally from every predecessor, whose statements read argument
    variables ("stk") that each predecessor sets, is copied back into the
    predecessors with their own values."""
    split_merges(body, lf)
    pos = {a: i for i, (a, _, _) in enumerate(body)}
    changed = True
    while changed:
        changed = False
        for a, ss, br in list(body):
            n = lf.nodes.get(a)
            if n is None or br is not None or len(n.succs) != 1 or len(ss) > limit:
                continue
            preds = [p for p in n.preds if p in pos]
            if len(preds) < 2 or len(preds) != len(n.preds):
                continue
            used = {x.key() for s in ss for e in stmt_exprs(s) for x in e.walk()
                    if isinstance(x, Var) and x.space == "stk"}
            # locals the block reads that every way in has just set (a merged
            # call's arguments built in the frame)
            reads = {x.key() for s in ss for e in stmt_exprs(s) for x in e.walk()
                     if isinstance(x, Var) and x.space == "local"}
            setters = None
            for p in preds:
                _, pss, _ = body[pos[p]] if p in pos else (None, [], None)
                d = {s.dst.key() for s in pss if isinstance(s, Assign) and isinstance(s.dst, Var)}
                setters = d if setters is None else setters & d
            frame_args = reads & (setters or set())
            # a register every way in has just set, read only here: the argument of a
            # call the compiler shares ("add eax, 12A0h; jmp shared": a target
            # Proc_90E740 printed t_eax = global_F04AC4 + 4768, a Type mismatch)
            defined, regs = set(), set()
            for s in ss:
                for e in stmt_exprs(s):
                    if isinstance(s, Assign) and e is s.dst:
                        continue
                    regs |= {x.key() for x in e.walk() if isinstance(x, Var) and x.space == "reg"
                             and x.key() not in defined}
                if isinstance(s, Assign) and isinstance(s.dst, Var):
                    defined.add(s.dst.key())
            # (eax, ecx, edx: any call in the block ends their value.  Each way in sets
            # the register once, and doesn't read it after)
            calls = any(isinstance(x, Call) for s in ss for e in stmt_exprs(s) for x in e.walk())
            for k in regs if calls else ():
                name = k[1][0] if isinstance(k[1], tuple) else k[1]
                if name not in ("eax", "ecx", "edx") or k not in (setters or ()):
                    continue
                good = True
                for p in preds:
                    pss = body[pos[p]][1] if p in pos else []
                    at = [i for i, s in enumerate(pss) if isinstance(s, Assign) and
                          isinstance(s.dst, Var) and s.dst.key() == k]
                    if len(at) != 1 or any(
                            isinstance(x, Var) and x.key() == k for s in pss[at[0] + 1:]
                            for e in stmt_exprs(s) for x in e.walk()):
                        good = False
                        break
                if good:
                    used.add(k)
            if not used and not frame_args:
                continue
            ok = True
            plans = {}
            for p in preds:
                pn = lf.nodes[p]
                _, pss, pbr = body[pos[p]]
                if pbr is not None or pn.succs != [a]:
                    ok = False
                    break
                vals = {}
                for si, s in enumerate(pss):
                    if isinstance(s, Assign) and isinstance(s.dst, Var) and s.dst.key() in used:
                        vals[s.dst.key()] = s.src
                        # (the copy goes at the end of the block: what the value reads must still
                        # mean that there.  add ebx, 44h; push ebx: the argument is ebx + 44h, then
                        # ebx = ebx + 44h, and read after that the Get was 88h on: .stored of the next
                        # record, Subscript out of range at the last one.  DarwinBots LoadShot)
                        rd = {x.key() for x in s.src.walk() if isinstance(x, Var)}
                        for t in pss[si + 1:]:
                            if isinstance(t, Assign) and isinstance(t.dst, Var) and t.dst.key() in rd:
                                if t.src.key() == vals[s.dst.key()].key():
                                    vals[s.dst.key()] = t.dst
                                    rd = {t.dst.key()}
                                else:
                                    ok = False
                if set(vals) != used:
                    ok = False
                if not ok:
                    break
                for k, v in list(vals.items()):
                    vals[k] = merged_byref_arg(lf, pss, v)
                plans[p] = vals
            if not ok:
                continue
            succ = n.succs[0]
            for p in preds:
                i = pos[p]
                pa, pss, pbr = body[i]
                pss = [s for s in pss if not (isinstance(s, Assign) and isinstance(s.dst, Var)
                                               and s.dst.key() in used)]
                pss += [substitute_many(s, plans[p]) for s in ss]
                body[i] = (pa, pss, pbr)
                pn = lf.nodes[p]
                pn.succs = [succ]
                pn.kind = "jmp"
            sn = lf.nodes.get(succ)
            if sn is not None:
                sn.preds = [x for x in sn.preds if x != a] + [p for p in preds if p not in sn.preds]
            del body[pos[a]]
            del lf.nodes[a]
            if a in lf.out:
                del lf.out[a]
            pos = {x: i for i, (x, _, _) in enumerate(body)}
            changed = True
            break
    return body


def reads_stk(s):
    """The statement reads a pushed-argument slot (writing one does not
    count: that is a predecessor setting up a merged call)."""
    for e in stmt_exprs(s):
        if isinstance(s, Assign) and e is s.dst and isinstance(e, Var):
            continue
        if any(isinstance(x, Var) and x.space == "stk" for x in e.walk()):
            return True
    return False


def free_key(lf, body, k):
    """k as the key of a block a pass splits off or copies, or - when a
    block has it already - a free key between k and the next one in use.
    unmerge_calls and duplicate_small_joins run in every round and can cut
    or copy the same block again; two blocks under one key never settle the
    liveness fixpoints (VBDoom P_GroupLines: split_merges made a + 0.5 a
    second time and live_out_sets looped forever)."""
    taken = set(lf.nodes) | {a for a, _, _ in body}
    while k in taken:
        k = (k + min((t for t in taken if t > k), default=k + 1)) / 2
    return k


def split_merges(body, lf):
    """A merge block that starts with statements reading per-path argument
    variables is split after the last such statement, so that part alone can
    be copied back into the predecessors."""
    from .cfg import Node
    for i, (a, ss, br) in enumerate(list(body)):
        n = lf.nodes.get(a)
        if n is None or len(n.preds) < 2:
            continue
        k = -1
        for j, s in enumerate(ss):
            if reads_stk(s):
                k = j
        if k < 0 or (k == len(ss) - 1 and br is None):
            continue
        rest_id = free_key(lf, body, a + 0.5)
        rest = Node(rest_id, [], list(n.succs), n.kind)
        rest.preds = [a]
        for t in n.succs:
            tn = lf.nodes.get(t)
            if tn is not None:
                tn.preds = [rest_id if p == a else p for p in tn.preds]
        if hasattr(n, "cleanup"):
            rest.cleanup = n.cleanup
        lf.nodes[rest_id] = rest
        n.succs, n.kind = [rest_id], "fall"
        idx = [x[0] for x in body].index(a)
        body[idx] = (a, ss[:k + 1], None)
        body.insert(idx + 1, (rest_id, ss[k + 1:], br))


def duplicate_small_joins(body, lf, limit=2):
    """Give each predecessor its own copy of a tiny shared block (the compiler
    merges identical statement tails); this removes GoTos without changing
    what runs on any path."""
    from .cfg import Node
    from . import structure
    pos = {a: i for i, (a, _, _) in enumerate(body)}
    stmts_of = {a: ss for a, ss, _ in body}

    def reaches(src, dst):
        seen, stack = set(), [src]
        while stack:
            x = stack.pop()
            if x == dst:
                return True
            if x in seen or x not in lf.nodes:
                continue
            seen.add(x)
            stack.extend(lf.nodes[x].succs)
        return False

    for a, ss, br in list(body):
        n = lf.nodes.get(a)
        if n is None or br is not None or len(n.succs) != 1 or len(ss) > limit or not ss:
            continue
        if a == lf.p.va or len(n.preds) < 2 or n.kind == "exit":
            continue
        if any(isinstance(s, ExprStmt) and getattr(s.e, "stmt_fmt", "").isdigit() for s in ss):
            continue                        # (a line number, once: Duplicate declaration)
        if reaches(n.succs[0], a):
            continue                        # inside a loop: leave it alone
        preds = list(n.preds)
        if any(structure.resume_at_end(stmts_of.get(p, [])) is not None for p in preds):
            continue                        # (Resume label: the label's own block)
        for k, p in enumerate(preds[1:], 1):
            pn = lf.nodes.get(p)
            if pn is None:
                continue
            cid = free_key(lf, body, a + 0.25 * k / len(preds))
            c = Node(cid, [], list(n.succs), n.kind)
            c.preds = [p]
            lf.nodes[cid] = c
            pn.succs = [cid if t == a else t for t in pn.succs]
            for t in n.succs:
                tn = lf.nodes.get(t)
                if tn is not None:
                    tn.preds.append(cid)
            idx = [x[0] for x in body].index(a)
            body.insert(idx + 1, (cid, list(ss), None))
        n.preds = preds[:1]
    return body


def single_slots(lf, tags_only=False):
    """Keys of variables read as 4-byte floats (fld dword) and never as 8:
    Singles - a Long is loaded with fild."""
    out = set()
    for k, sz in lf.sizes.items():
        if not tags_only and (4, True) in sz and not any(s == 8 for s, fp in sz):
            out.add(k)
    # (or the Variant built around it says: a = 3: b = 8: IIf(a > b, a, b), with the compare gone
    # at compile time, and no FPU touching them)
    out.update(k for k, ts in getattr(lf, "vt_types", {}).items() if ts == {"Single"})
    return out


def fold_single_bits(body, lf, tags_only=False):
    """An integer stored into a Single is its bit pattern: VB writes a
    Single literal with a plain mov (ChessBrainVB CalcTime SafetyMargin =
    1.5 is mov [esp+x], 3FC00000h)."""
    import struct
    singles = single_slots(lf, tags_only)
    if not singles:
        return body

    def slot(k):
        return (k[0], k[1][0]) if isinstance(k[1], tuple) else k    # (a renamed web)
    out = []
    for a, ss, br in body:
        new = []
        for s in ss:
            if isinstance(s, Assign) and isinstance(s.dst, Var) and \
                    slot(s.dst.key()) in singles and \
                    isinstance(s.src, Const) and isinstance(s.src.value, int) and \
                    s.src.vbtype is None and \
                    (s.src.value & 0x7f800000 or not s.src.value & 0x7fffff):
                # (a denormal is a small integer in a reused slot, no literal:
                # a target Proc_97E220 3 printed 4.2e-45)
                n = Assign(s.dst, Const(struct.unpack("<f", struct.pack(
                    "<I", s.src.value & 0xffffffff))[0], "Single"), s.kind)
                n.va = getattr(s, "va", None)
                s = n
            new.append(s)
        out.append((a, new, br))
    return out


def element_subscripts(rel, dims):
    """The subscripts of flat element number rel (0-based, the first
    dimension fastest) of an array with dims [(lower bound, count)...]: each
    term goes to the dimension whose stride its coefficient is a multiple of,
    the constant to the nearest whole strides (ChessBrainVB InitSqBetween
    addresses SqBetween(sq, sq1, sq2) as sq + sq1 * 120 + sq2 * 14400; a
    variable holding all of it stays in the first subscript)."""
    from .lift import Lifter
    from .machine import mul_const
    lbs = [lb if isinstance(lb, int) else 0 for lb, n in dims]
    if len(dims) < 2 or not all(isinstance(n, int) and n > 0 for lb, n in dims[:-1]):
        return [add(rel, Const(lbs[0])) if lbs[0] else rel] + [Const(lb) for lb in lbs[1:]]
    strides = [1]
    for lb, n in dims[:-1]:
        strides.append(strides[-1] * n)
    lin = Lifter.linear(rel)
    parts = [None] * len(dims)
    for kk, (x, k) in sorted(lin.items(), key=lambda kv: str(kv[0])):
        if kk is None or not k:
            continue
        d = max(i for i, st in enumerate(strides) if k % st == 0)
        term = mul_const(x, k // strides[d])
        parts[d] = term if parts[d] is None else add(parts[d], term)
    c = lin.get(None, [None, 0])[1]
    consts = [0] * len(dims)
    for d in range(len(dims) - 1, 0, -1):
        consts[d] = (c + strides[d] // 2) // strides[d]
        c -= consts[d] * strides[d]
    consts[0] = c
    out = []
    for d in range(len(dims)):
        k = consts[d] + lbs[d]
        e = parts[d]
        out.append(Const(k) if e is None else (add(e, Const(k)) if k else e))
    return out


def fold_pointer_ivs(body, lf, no_scaled=frozenset()):
    """A register optimised code keeps an address into a fixed array in
    (the array's pvData plus an element offset, loaded once, stepped through
    a loop by strength reduction - ChessBrainVB ResetMaterial over
    Pieces(1 To n), MovePicker's MovePickerDat(a1), BestMoveAtFirst's
    Moves(Ply, i)) holds that element's number instead: VB has no pointers.
    Every definition of p must be the array's pvData plus multiples of the
    element size K (and one constant field offset r for them all), or p plus
    a multiple of K; p may only be dereferenced, *(p + M) with M's terms
    multiples of K too.  Then p = (offset - r) / K, and *(p + M) is element
    p + M / K (all of it in the first index of a multi-dimensional array:
    the same element without bounds checks), or its field."""
    from .lift import Lifter
    from .machine import mul_const
    slots = lf.m.__dict__.get("_array_slots")
    if slots is None:
        slots = lf.m._array_slots = lf.m.global_array_slots()

    def array_of(e):
        """(array Var, element size, [lower bound per dimension]) for a fixed
        array's pvData."""
        if not isinstance(e, Var):
            return None
        info = lf.arrays.get(e.key())
        if e.space == "global" and isinstance(e.off, int) and e.off in slots:
            cb, dims, vt, ff, img, rd = slots[e.off]
            return e, cb, [lb for lb, n in dims]
        if info is not None and not info.get("dyn") and info.get("size"):
            dims = info.get("dims")
            if dims:
                return e, info["size"], [lb if isinstance(lb, int) else 0 for lb, n in dims]
            lb = info.get("lb") if isinstance(info.get("lb"), int) else 0
            return e, info["size"], [lb]
        return None

    def dims_of(arr, lbs):
        if arr.space == "global" and isinstance(arr.off, int) and arr.off in slots:
            return slots[arr.off][1]
        dims = (lf.arrays.get(arr.key()) or {}).get("dims")
        return dims if dims and len(dims) == len(lbs) else [(lb, None) for lb in lbs]

    # a register that is one thing times a constant, added to pointers: shl esi, 2 ...; mov ecx,
    # [edx + esi]; add edx, esi (ChessBrainVB IsAttackedByW walking Board by a direction).  With the
    # pointers an element number, it holds the thing itself.
    scaled = {}
    sdefs = {}
    for _, ss, _b in body:
        for s in ss:
            if isinstance(s, Assign) and isinstance(s.dst, Var) and s.dst.space in ("reg", "tmp"):
                sdefs.setdefault(s.dst.key(), []).append(s)
    for k, ds in sdefs.items():
        if k not in no_scaled and len(ds) == 1 and isinstance(ds[0].src, BinOp) and ds[0].src.op == "*" and \
                isinstance(ds[0].src.b, Const) and isinstance(ds[0].src.b.value, int) and ds[0].src.b.value > 1:
            scaled[k] = ds[0].src.b.value

    def split(e, key=None):
        """e as (the array term or None, {term key: (expr, coefficient)},
        constant), leaving out key (the pointer itself)."""
        lin = Lifter.linear(e)
        arr, terms, c = None, {}, lin.get(None, [None, 0])[1]
        for kk, (x, k) in lin.items():
            if kk is None or not k:
                continue
            if kk == key:
                continue
            if k == 1 and arr is None and array_of(x) is not None:
                arr = array_of(x)
                continue
            terms[kk] = (x, scaled[kk]) if k == 1 and kk in scaled else (x, k)
        return arr, terms, c

    def flat_address(e):
        """&a(i, j) of a fixed array as its pvData plus the element's byte offset."""
        if not (isinstance(e, ir.AddrOf) and isinstance(e.e, ir.Index) and isinstance(e.e.arr, Var)):
            return e
        got = array_of(e.e.arr)
        if got is None:
            return e
        arr, K, lbs = got
        dims = dims_of(arr, lbs)
        if len(dims) != len(e.e.idx):
            return e
        out, stride = arr, K
        for (lb, n), sub in zip(dims, e.e.idx):
            part = add(sub, Const(-lb)) if isinstance(lb, int) and lb else sub
            out = add(out, mul_const(part, stride))
            stride *= n if isinstance(n, int) else 0
        return out

    # the difference of two arrays' pvData, kept to reach one from a pointer into the other:
    # mov edi, [Theirs]; sub edi, [Ours]; ...; mov eax, [edi + ecx] (ChessBrainVB ColImbalance)
    gaps = {}
    for _, ss, br in body:
        for s in ss:
            if isinstance(s, Assign) and isinstance(s.dst, Var) and s.dst.space in ("reg", "tmp"):
                lin = Lifter.linear(s.src)
                arrs = [(x, k) for kk, (x, k) in lin.items() if kk is not None and k]
                if len(arrs) == 2 and sorted(k for _, k in arrs) == [-1, 1] and \
                        not lin.get(None, [None, 0])[1] and all(array_of(x) is not None for x, _ in arrs):
                    gaps.setdefault(s.dst.key(), []).append(s.src)
                else:
                    gaps.setdefault(s.dst.key(), []).append(None)
    gaps = {k: v[0] for k, v in gaps.items() if v[0] is not None and
            all(x is not None and x.key() == v[0].key() for x in v)}
    if gaps:
        def inline_gap(x):
            return gaps.get(x.key()) if isinstance(x, Var) else None
        body = [(a, [s if isinstance(s, Assign) and isinstance(s.dst, Var) and s.dst.key() in gaps
                     else substitute_expr(s, inline_gap) for s in ss],
                 type(br)(br.cond.replace(inline_gap)) if br is not None else None)
                for a, ss, br in body]
    defs = {}
    for _, ss, br in body:
        for s in ss:
            if isinstance(s, Assign) and isinstance(s.dst, Var) and s.kind == "let" and \
                    (s.dst.space in ("reg", "tmp", "stk") or
                     # (or kept in the frame, a pointer per loop: mov [esp + 10h], edi)
                     s.dst.space == "local" and s.dst.key() not in lf.arrays and
                     not lf.udt.get(s.dst.key()) and not lf.is_named_local(s.dst)):
                defs.setdefault(s.dst.key(), []).append(s)
    def classify(k, s):
        """A definition of k: ("arr", array, terms, constant) for a pvData
        plus terms, ("ptr", q, terms, constant) for candidate q (k itself: a
        step) plus terms (TryMoveSliderList's p = p + d * 4; InitSqBetween
        copies File's pvData, t2 = t_ecx, in the loop), or None."""
        src = flat_address(s.src)
        a, terms, c = split(src)
        if a is not None:
            return ("arr", a, terms, c)
        lin = Lifter.linear(s.src)
        bases = [kk for kk, v in lin.items()
                 if kk is not None and v[1] == 1 and (kk == k or kk in cands)]
        if len(bases) != 1:
            return None
        a, terms, c = split(s.src, bases[0])
        return None if a is not None else ("ptr", bases[0], terms, c)

    cands, forms = {}, {}               # key -> ((arr, K, lbs), r); id(def) -> form
    grew = True
    while grew:
        grew = False
        for k, ds in defs.items():
            if k in cands:
                continue
            arr, r, ok, fs = None, None, True, []
            for s in ds:
                f = classify(k, s)
                if f is None:
                    ok = False
                    break
                fs.append((s, f))
                if f[0] == "ptr" and f[1] == k:
                    continue                # a step (checked once K is known)
                a, rq = (f[1], 0) if f[0] == "arr" else cands[f[1]]
                K = a[1]
                if (arr is not None and arr[0].key() != a[0].key()) or \
                        any(kc % K for _, kc in f[2].values()):
                    ok = False
                    break
                rr = (rq + f[3]) % K
                if r is not None and rr != r:
                    ok = False
                    break
                arr, r = a, rr
            if not ok or arr is None:
                continue
            K = arr[1]
            if all(f[3] % K == 0 and not any(kc % K for _, kc in f[2].values())
                   for _, f in fs if f[0] == "ptr" and f[1] == k):
                cands[k] = (arr, r)
                for s, f in fs:
                    forms[id(s)] = f
                grew = True
    if not cands:
        return body

    def use_of(x):
        """(pointer key, terms, constant) for *(p + M) with p a candidate."""
        if not isinstance(x, Deref):
            return None
        lin = Lifter.linear(x.e)
        ks = [kk for kk, v in lin.items() if kk in cands and v[1]]
        if len(ks) != 1 or lin[ks[0]][1] != 1:
            return None
        a, terms, c = split(x.e, ks[0])
        if a is not None:
            # the pointer into one array, plus the other's pvData less its own: the other's element
            own = cands[ks[0]][0]
            neg = terms.pop(own[0].key(), None)
            if neg is None or neg[1] != -1 or a[1] != own[1] or a[0].key() == own[0].key():
                return None
        K = cands[ks[0]][0][1]
        if any(kc % K for _, kc in terms.values()):
            return None
        return ks[0], terms, c + x.off, a   # (*(edi + 44) is Deref(edi, 4, 44))

    # every use must be such a dereference (or a candidate's definition: a
    # step or a copy)
    in_use = set()                      # the scaled registers pointers are stepped by
    loose = set()                       # and those read as anything else

    def bad_uses():
        bad = set()

        def check(e):
            stack = [e]
            while stack:
                x = stack.pop()
                if isinstance(x, Var) and x.key() in cands:
                    bad.add(x.key())
                    continue
                if isinstance(x, Var) and x.key() in scaled:
                    loose.add(x.key())
                    continue
                u = use_of(x)
                if u is not None:
                    for tk, (te, _) in u[1].items():
                        if tk in scaled:
                            in_use.add(tk)
                        else:
                            stack.append(te)
                    continue
                stack.extend(x.children())
        for _, ss, br in body:
            for s in ss:
                for e in stmt_exprs(s):
                    if isinstance(s, Assign) and e is s.dst:
                        if not isinstance(e, Var):
                            check(e)
                        continue
                    if isinstance(s, Assign) and e is s.src and isinstance(s.dst, Var) and \
                            s.dst.key() in cands:
                        for tk, (te, _) in forms[id(s)][2].items():
                            if tk in scaled:
                                in_use.add(tk)
                            else:
                                check(te)
                        continue
                    check(e)
            if br is not None:
                check(br.cond)
        return bad
    while True:
        in_use.clear()
        loose.clear()
        drop = bad_uses()
        more = True
        while more:                         # and the copies of what goes
            more = False
            for k in cands:
                if k not in drop and any(forms[id(s)][0] == "ptr" and forms[id(s)][1] in drop
                                         for s in defs[k]):
                    drop.add(k)
                    more = True
        if not drop:
            break
        for k in drop:
            cands.pop(k, None)
        if not cands:
            return body
    if in_use & loose:
        # (one of them is read as a number too: not the pointers' step alone)
        return fold_pointer_ivs(body, lf, frozenset(no_scaled) | (in_use & loose))

    def scaled_sum(terms, K):
        out = None
        for _, (te, kc) in sorted(terms.items(), key=lambda kv: str(kv[0])):
            te = te.replace(fix)
            part = te if kc == K else BinOp("*", te, Const(kc // K))
            out = part if out is None else add(out, part)
        return out

    # one constant definition (or a copy of one) and no steps: the element
    # number itself
    consts = {}
    grew = True
    while grew:
        grew = False
        for k in cands:
            if k in consts:
                continue
            (arr, K, lbs), r = cands[k]
            vals = set()
            for s in defs[k]:               # (the same pvData loaded again: one value)
                f = forms[id(s)]
                if f[2]:
                    break
                if f[0] == "arr":
                    vals.add((f[3] - r) // K)
                elif f[1] != k and f[1] in consts:
                    vals.add(consts[f[1]] + (cands[f[1]][1] + f[3] - r) // K)
                else:
                    break
            else:
                if len(vals) == 1:
                    consts[k] = vals.pop()
                    grew = True

    def fix(x):
        u = use_of(x)
        if u is None:
            return None
        key, terms, c, other = u
        (arr, K, lbs), r = cands[key]
        if other is not None:
            arr, K, lbs = other
        q, f = divmod(r + c, K)
        idx = Const(consts[key]) if key in consts else Var(*key)
        rest = scaled_sum({kk: (te.replace(fix), kc) for kk, (te, kc) in terms.items()}, K)
        if rest is not None:
            idx = add(idx, rest)
        idx = add(idx, Const(q)) if q else idx
        el = ir.Index(arr, element_subscripts(idx, dims_of(arr, lbs)))
        if f == 0 and x.size == K:
            return el
        return Deref(el, x.size, f)

    out = []
    for a, ss, br in body:
        new = []
        for s in ss:
            if isinstance(s, Assign) and isinstance(s.dst, Var) and s.dst.key() in consts:
                continue                    # (folded into its uses)
            if isinstance(s, Assign) and isinstance(s.dst, Var) and s.dst.key() in in_use:
                n = Assign(s.dst, s.src.a.replace(fix))     # (the multiple's other factor)
                n.va = getattr(s, "va", None)
                new.append(n)
                continue
            if isinstance(s, Assign) and isinstance(s.dst, Var) and s.dst.key() in cands:
                (arr, K, lbs), r = cands[s.dst.key()]
                kind, q, terms, c = forms[id(s)]
                src = scaled_sum(terms, K)
                if kind == "arr":
                    base, d = None, (c - r) // K
                elif q == s.dst.key():
                    base, d = s.dst, c // K
                else:
                    base = Const(consts[q]) if q in consts else Var(*q)
                    d = (cands[q][1] + c - r) // K
                for part in (base, Const(d) if d else None):
                    if part is not None:
                        src = part if src is None else add(part, src) if part is base \
                            else add(src, part)
                src = Const(0) if src is None else src
                n = Assign(s.dst, src)
                n.va = getattr(s, "va", None)
                new.append(n)
                continue
            new.append(substitute_expr(s, fix))
        if br is not None:
            br = type(br)(br.cond.replace(fix))
        out.append((a, new, br))
    return out


def fold_offset_ivs(body, lf):
    """Byte offsets optimised code steps through fixed arrays (strength
    reduction: "edx = 0: Do: ... *(KnightOffsets + edx) ...: edx = edx + 4",
    ChessBrainVB TryMoveListKnight's For ActDir = 0 To 7) become element
    indexes: VB has no address arithmetic.  The variables (a family through
    copies and steps) must only be set to multiples of the element size K or
    to each other plus such a multiple, and only be used as the byte offset
    of a fixed array of K-byte elements, compared with each other or with
    multiples of K, or copied and stepped.  Then each holds offset / K."""
    from .lift import Lifter
    from .machine import mul_const
    slots = lf.m.__dict__.get("_array_slots")
    if slots is None:
        slots = lf.m._array_slots = lf.m.global_array_slots()

    def array_of(e):
        if not isinstance(e, Var):
            return None
        if e.space == "global" and isinstance(e.off, int) and e.off in slots:
            cb, dims, vt, ff, img, rd = slots[e.off]
            return e, cb, [lb for lb, n in dims]
        info = lf.arrays.get(e.key())
        if info is not None and not info.get("dyn") and info.get("size"):
            lb = info.get("lb") if isinstance(info.get("lb"), int) else 0
            return e, info["size"], [lb]
        return None

    def dims_of(arr, lbs):
        if arr.space == "global" and isinstance(arr.off, int) and arr.off in slots:
            return slots[arr.off][1]
        return [(lb, None) for lb in lbs]

    defs = {}
    for _, ss, br in body:
        for s in ss:
            if isinstance(s, Assign) and isinstance(s.dst, Var):
                defs.setdefault(s.dst.key(), []).append(s)
    cand = {k for k, ds in defs.items()
            if (k[0] in ("reg", "tmp", "stk") or
                k[0] == "local" and k not in lf.arrays and not lf.udt.get(k) and
                not lf.is_named_local(ds[0].dst)) and all(s.kind == "let" for s in ds)}
    # (a frame slot the compiler keeps the scaled counter in: TryMoveListKnight's
    # [esp+4], ActDir * 4, compared with 28)

    def def_terms(s):
        """A definition as a linear sum: (member terms [(key, coefficient)],
        other terms [(expression, coefficient)], constant).  The other terms'
        coefficients must be multiples of K too (InitSqBetween's
        t = (sq + (sq1 + sq2 * 120) * 120) * 2, stepped by a member
        d = dir * 28800: SqBetween(sq, sq1, sq2) through sq2's loop)."""
        lin = Lifter.linear(s.src)
        mt, ot = [], []
        for kk, v in sorted(lin.items(), key=lambda kv: str(kv[0])):
            if kk is None or not v[1]:
                continue
            if kk in cand and v[1] in (1, -1):
                mt.append((kk, v[1]))
            else:
                ot.append((v[0], v[1]))     # (t = i * 4: i is a count, t the offset)
        return mt, ot, lin.get(None, [None, 0])[1]

    CMP_OPS = ("=", "<>", "<", ">", "<=", ">=")

    def cmp_side(y):
        """(member key or None, constant) for a compared operand that is a
        member plus a constant, or a constant (FillKingCheckW's loop test
        t1 + 4 <= 28 on the old value), else None."""
        lin = Lifter.linear(y)
        terms = [(kk, v) for kk, v in lin.items() if kk is not None and v[1]]
        c = lin.get(None, [None, 0])[1]
        if not terms:
            return None, c
        if len(terms) == 1 and terms[0][1][1] == 1 and terms[0][0] in cand:
            return terms[0][0], c
        return None

    sizes = {}                          # member -> {element sizes it is an offset into}
    links = []                          # (member, member): same family

    def scan_uses():
        sizes.clear()
        del links[:]
        bad = set()

        def visit(e, top_ok=False):
            stack = [e]
            while stack:
                x = stack.pop()
                if isinstance(x, Var) and x.key() in cand:
                    bad.add(x.key())
                    continue
                if isinstance(x, Deref):
                    lin = Lifter.linear(x.e)
                    # (several members: FillKingCheckW's Board(old Target + step))
                    ms = [kk for kk, v in lin.items() if kk in cand and v[1] in (1, -1)]
                    arrs = [array_of(v[0]) for kk, v in lin.items()
                            if kk is not None and v[1] == 1 and array_of(v[0]) is not None]
                    if ms and len(arrs) == 1:
                        K = arrs[0][1]
                        others = [(kk, v) for kk, v in lin.items() if kk is not None and
                                  kk not in ms and v[0] is not arrs[0][0] and v[1]]
                        if all(v[1] % K == 0 for kk, v in others):
                            for m in ms:
                                sizes.setdefault(m, set()).add(K)
                            for m in ms[1:]:
                                links.append((ms[0], m))
                            for kk, v in others:
                                stack.append(v[0])
                            continue
                if isinstance(x, BinOp) and x.op in CMP_OPS:
                    sa, sb = cmp_side(x.a), cmp_side(x.b)
                    if sa is not None and sb is not None and (sa[0] or sb[0]):
                        if sa[0] and sb[0]:
                            links.append((sa[0], sb[0]))
                        continue
                stack.extend(x.children())
        for _, ss, br in body:
            for s in ss:
                for e in stmt_exprs(s):
                    if isinstance(s, Assign) and e is s.dst:
                        if not isinstance(e, Var):
                            visit(e)
                        continue
                    if isinstance(s, Assign) and e is s.src and isinstance(s.dst, Var) and \
                            s.dst.key() in cand:
                        mt, ot, c = def_terms(s)
                        for m, _ in mt:
                            links.append((s.dst.key(), m))
                        for x, _ in ot:
                            visit(x)    # (a member inside another term: no offset)
                        continue
                    visit(e)
            if br is not None:
                visit(br.cond)
        return bad

    for _ in range(8):
        changed = False
        bad = scan_uses()
        if bad:
            cand -= bad
            changed = True
        if not changed:
            break
    if not cand:
        return body
    # families and their element size
    parent = {k: k for k in cand}

    def find(k):
        while parent[k] != k:
            parent[k] = parent[parent[k]]
            k = parent[k]
        return k
    for a, b in links:
        if a in cand and b in cand:
            parent[find(a)] = find(b)
    fam_sizes = {}
    for k in cand:
        fam_sizes.setdefault(find(k), set()).update(sizes.get(k, set()))
    fam_K = {}
    for f, ks in fam_sizes.items():
        if len(ks) == 1:
            fam_K[f] = next(iter(ks))
    K_of = {k: fam_K[find(k)] for k in cand if find(k) in fam_K}
    # every constant they meet must be a multiple of K
    ok = dict.fromkeys(K_of, True)
    for k, K in K_of.items():
        for s in defs[k]:
            mt, ot, c = def_terms(s)
            if c % K or any(kc % K for _, kc in ot) or any(m not in K_of for m, _ in mt):
                ok[k] = False
    def cmp_consts(e):
        for x in e.walk():
            if isinstance(x, BinOp) and x.op in CMP_OPS:
                sa, sb = cmp_side(x.a), cmp_side(x.b)
                if sa is None or sb is None:
                    continue
                for m in (sa[0], sb[0]):
                    if m in K_of and (sb[1] - sa[1]) % K_of[m]:
                        ok[m] = False
    for _, ss, br in body:
        for s in ss:
            for e in stmt_exprs(s):
                cmp_consts(e)
        if br is not None:
            cmp_consts(br.cond)
    bad_f = {find(k) for k, v in ok.items() if not v}
    K_of = {k: K for k, K in K_of.items() if find(k) not in bad_f}
    if not K_of:
        return body

    def fix(x):
        if isinstance(x, Deref):
            lin = Lifter.linear(x.e)
            ms = [kk for kk, v in lin.items() if kk in K_of and v[1] in (1, -1)]
            arrs = [array_of(v[0]) for kk, v in lin.items()
                    if kk is not None and v[1] == 1 and array_of(v[0]) is not None]
            if ms and len(arrs) == 1 and all(arrs[0][1] == K_of[m] for m in ms):
                arr, K, lbs = arrs[0]
                idx = None
                for m in ms:
                    part = mul_const(lin[m][0], lin[m][1])
                    idx = part if idx is None else add(idx, part)
                c = lin.get(None, [None, 0])[1] + x.off
                for kk, (e, k) in lin.items():
                    if kk is None or kk in ms or e is arr or not k:
                        continue
                    part = e.replace(fix)
                    idx = add(idx, part if k == K else BinOp("*", part, Const(k // K)))
                q, f = divmod(c, K)
                idx = add(idx, Const(q)) if q else idx
                el = ir.Index(arr, element_subscripts(idx, dims_of(arr, lbs)))
                if f == 0 and x.size == K:
                    return el
                return Deref(el, x.size, f)
        if isinstance(x, BinOp) and x.op in CMP_OPS:
            sa, sb = cmp_side(x.a), cmp_side(x.b)
            if sa is not None and sb is not None and (sa[0] in K_of or sb[0] in K_of):
                K = K_of[sa[0] if sa[0] in K_of else sb[0]]
                d = (sb[1] - sa[1]) // K
                if sa[0] and sb[0]:
                    return x._keep(BinOp(x.op, Var(*sa[0]),
                                         add(Var(*sb[0]), Const(d)) if d else Var(*sb[0])))
                if sa[0]:
                    return x._keep(BinOp(x.op, Var(*sa[0]), Const(d)))
                return x._keep(BinOp(x.op, Const(-d), Var(*sb[0])))
        return None

    out = []
    for a, ss, br in body:
        new = []
        for s in ss:
            if isinstance(s, Assign) and isinstance(s.dst, Var) and s.dst.key() in K_of:
                K = K_of[s.dst.key()]
                mt, ot, c = def_terms(s)
                src = None
                for m, kc in mt:
                    part = mul_const(Var(*m), kc)
                    src = part if src is None else add(src, part)
                for x, kc in ot:
                    part = mul_const(x.replace(fix), kc // K)
                    src = part if src is None else add(src, part)
                if c // K or src is None:
                    src = Const(c // K) if src is None else add(src, Const(c // K))
                n = Assign(s.dst, src)
                n.va = getattr(s, "va", None)
                new.append(n)
                continue
            new.append(substitute_expr(s, fix))
        if br is not None:
            br = type(br)(br.cond.replace(fix))
        out.append((a, new, br))
    return out


def fold_variant_args(body, lf):
    """A Variant argument built in place: "v = VT" and "v+8 = value" stored
    in the block before the call; the call gets the value itself.  A frame
    slot is reused all over a procedure, so the tag counts only until that
    slot holds something else (gh_json Main: `d.Add "a", 1` took the key
    from a tag stored for a much earlier Variant in the same slot)."""
    from .ir import Missing, Call
    out = []
    for a, ss, br in body:
        tags, vals = {}, {}
        keep = list(ss)
        for i, s in enumerate(keep):
            if isinstance(s, Assign) and isinstance(s.dst, Var) and s.dst.space == "local" \
                    and isinstance(s.dst.off, int):
                if isinstance(s.src, Const) and isinstance(s.src.value, int) and \
                        0 < (s.src.value & 0x7fff) < 0x20:
                    tags[s.dst.off] = s.src.value & 0x7fff     # 0x8000: from a literal
                else:
                    tags.pop(s.dst.off, None)   # the slot holds a value, not a tag
                vals[s.dst.off] = s.src
                continue

            def fix(x):
                if not isinstance(x, Call) or not (x.method or (isinstance(x.raw, str) and
                                                                 x.raw.startswith("rtc"))):
                    return None
                args = []
                for v in x.args:
                    # (a slot 8 bytes above a tag is that Variant's value, not a tag:
                    # ByVals(.., 8, ..) passes the payload 8 of an Integer Variant)
                    # (nor when that slot is an argument of this call too: temporaries
                    # for two ByRef literals, Quick.WriteOne 4, 0, s, True read as
                    # a Single Variant of value True: {-1;0} in a target.ini for {4;0})
                    # (nor a variable that merely holds 8: l = 8 printed the Integer 8 bytes up)
                    if isinstance(v, Var) and v.space == "local" and v.off in tags and \
                            not lf.is_named_local(v) and (v.off - 8) not in tags and not any(
                                isinstance(y, Var) and y.space == "local" and
                                y.off == v.off + 8 for y in x.args):
                        t, val = tags[v.off], vals.get(v.off + 8)
                        # (VT_ERROR needs its payload: x = 10: y = Bump(x): Print y; x
                        # lost x, a Long holding 10, as an "omitted argument")
                        if t == 10 and isinstance(val, Const) and \
                                (val.value & 0xffffffff) == 0x80020004:
                            args.append(Missing())      # VT_ERROR: an omitted argument
                            continue
                        if val is not None and t in (2, 3, 4, 5, 8, 11, 17):
                            args.append(val)
                            continue
                    args.append(v)
                c = Call(x.name, args, x.vbtype, x.raw, x.method, x.obj, x.pure)
                return x._keep(c)           # keep print_codes, iface, ...
            keep[i] = substitute_expr(s, fix)
        out.append((a, keep, br))
    return out


VARIANT_TAGS = {0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 13, 17}


def variant_value(lf, t, payload, hi):
    """A Variant's payload as the value its type word t says, or None."""
    from .ir import Missing
    if t in (0, 1):                         # VT_EMPTY, VT_NULL: no payload stored
        return Var("global", ("vbconst", t), "Empty" if t == 0 else "Null")
    if t == 10:
        if payload is None or (isinstance(payload, Const) and
                               (payload.value & 0xffffffff) == 0x80020004):
            return Missing()                # VT_ERROR: an omitted argument
        return None
    if payload is None:
        return None
    if t == 8 and isinstance(payload, Const):
        from .listing import vb_string
        txt = vb_string(lf.im, payload.value & 0xffffffff)
        return Str(txt) if txt is not None else None
    if t == 11:
        # VT_BOOL: a constant, or the Boolean stored as the payload word
        # (v.FontBold = CBool(Me.CD1.FontBold) printed v.FontBold = 11)
        return Const(payload.value, "Boolean") if isinstance(payload, Const) else payload
    if t in (4, 5, 6, 7) and isinstance(payload, Const):
        return lf.wide_value(t, payload, hi)
    # (4..7: a computed float, stored by fstp: a target List1.Left = List1.Left +
    # List1.Width + 75 printed Me.List1.Left = 4, the type word)
    return payload if t in (2, 3, 4, 5, 6, 7, 8, 9, 13, 17) else None


def fold_variant_reaching(body, lf):
    """A Variant frame temporary built in place (type word at k, payload at
    k+8) before a join and read whole after it: fold_variant_slot_reads only
    pairs the halves within a block, so the type word was the value
    (a target Proc_829720: Printer.Font.Name = 8 for "", the Variant built
    before the test that creates VB's Global object).  Folded when every
    path in stored the same constant halves and nothing took the slot's
    address on the way."""
    byref_slots = {s.dst.key() for _, ss, _ in body for s in ss
                   if isinstance(s, Assign) and isinstance(s.dst, Var) and s.dst.space == "local" and
                   isinstance(s.dst.off, int) and isinstance(s.src, Const) and
                   isinstance(s.src.value, int) and s.src.vbtype is None and
                   s.src.value & 0x4000 and not s.src.value & 0xffffa000 and
                   (s.src.value & 0xbfff) in VARIANT_TAGS}
    keys = {k for k in getattr(lf, "variants", ()) if k[0] == "local" and isinstance(k[1], int)}
    if not keys and not byref_slots:
        return body
    # only the Variants handed to a call by value (calls.byval_variant marks
    # them): elsewhere a slot read may be the variable itself (ReDim v(2))
    reads = set()
    for _, ss, br in body:
        for s in ss:
            for e in stmt_exprs(s):
                if isinstance(s, Assign) and e is s.dst:
                    continue
                for x in e.walk():
                    if isinstance(x, Var) and x.key() in keys and getattr(x, "byval_whole", False):
                        reads.add(x.key())
    if not reads and not byref_slots:
        return body
    idx = {a: i for i, (a, _, _) in enumerate(body)}
    preds = {i: [] for i in range(len(body))}
    for i, (a, _, _) in enumerate(body):
        for t in node_succs(lf, a):
            if t in idx:
                preds[idx[t]].append(i)
    # variables a payload may be copied from: never passed by address, and
    # the same definitions reach the read as reached the store
    taken = set()
    for _, ss, br in body:
        for s in ss:
            for e in stmt_exprs(s):
                for x in e.walk():
                    if isinstance(x, ir.AddrOf) and isinstance(x.e, Var):
                        taken.add(x.e.key())

    def defs_at(bi, si, key):
        """Where the definitions of key reaching statement si of block bi
        are, or None when they cannot all be found."""
        got, seen, work = set(), set(), [(bi, si)]
        while work:
            b, upto = work.pop()
            ss = body[b][1]
            hit = None
            for k in range((len(ss) if upto is None else upto) - 1, -1, -1):
                t = ss[k]
                if isinstance(t, Assign) and isinstance(t.dst, Var) and t.dst.key() == key:
                    hit = (b, k)
                    break
            if hit is not None:
                got.add(hit)
                continue
            if not preds[b] or len(seen) > 200:
                if not preds[b]:
                    got.add(("entry", key))
                    continue
                return None
            for p_ in preds[b]:
                if p_ not in seen:
                    seen.add(p_)
                    work.append((p_, None))
        return got

    def steady(x, at, use):
        """x, stored at position at, still holds that value at use."""
        if isinstance(x, (Const, Str, SP)):
            return True                     # (SP: a frame address)
        if not (isinstance(x, Var) and x.space in ("local", "arg", "global") and
                x.key() not in taken and x.key() not in keys):
            return False
        d1, d2 = defs_at(at[0], at[1], x.key()), defs_at(use[0], use[1], x.key())
        return d1 is not None and d1 == d2

    def addressed(s, ks):
        for e in stmt_exprs(s):
            for x in e.walk():
                if isinstance(x, ir.AddrOf) and isinstance(x.e, Var) and x.e.key() in ks:
                    return True
        return False

    def reaching(bi, si, key, ks, any_src=False):
        """The values every def of key reaching statement si of block bi
        stores ([(src, whole_variant)]) when they still hold there, or None
        (any_src: whatever they store, for the variable that holds it)."""
        srcs, seen, work = [], set(), [(bi, si)]
        while work:
            b, upto = work.pop()
            ss = body[b][1]
            hit = None
            for k in range((len(ss) if upto is None else upto) - 1, -1, -1):
                t = ss[k]
                if isinstance(t, Assign) and isinstance(t.dst, Var) and t.dst.key() == key:
                    hit = t
                    break
                if addressed(t, ks):
                    return None             # a call may fill it (__vbaVarDup(&k, ...))
            if hit is not None:
                if b == 0 and isinstance(hit.src, Const) and hit.src.value == 0 and \
                        not getattr(hit, "whole_variant", False):
                    return None             # the prologue's zero-fill: not built for this
                if not any_src and not steady(hit.src, (b, k), (bi, si)):
                    return None
                srcs.append((hit.src, bool(getattr(hit, "whole_variant", False))))
                continue
            if not preds[b] or len(seen) > 200:
                return None
            for p_ in preds[b]:
                if p_ not in seen:
                    seen.add(p_)
                    work.append((p_, None))
        return srcs

    def same(xs):
        return xs and all(type(x) is type(xs[0]) and x.key() == xs[0].key() for x in xs)

    def value_at(bi, si, k):
        ks = {("local", k), ("local", k + 8), ("local", k + 12)}
        tags = reaching(bi, si, ("local", k), ks)
        if not tags:
            return None
        if all(w for _, w in tags):
            vals = [x for x, _ in tags]
            return vals[0] if same(vals) else None  # merged pairs: the value itself
        if any(w for _, w in tags) or not same([x for x, _ in tags]):
            return None
        t = tags[0][0]
        if not isinstance(t, Const) or not isinstance(t.value, int) or t.vbtype is not None or \
                (t.value & 0xbfff) not in VARIANT_TAGS:
            return None                     # (a typed constant is a whole value: = False)
        if t.value & 0x4000:
            # VT_BYREF: the payload points at the variable (a module variable
            # by its address)
            ps = reaching(bi, si, ("local", k + 8), ks)
            if not ps:
                ps = reaching(bi, si, ("local", k + 8), ks, any_src=True)
                if ps and not all(isinstance(x, Var) and x.space == "reg" for x, _ in ps):
                    ps = None               # (only a register's value is resolved below)
            if not ps or not same([x for x, _ in ps]):
                return None
            if lf.data_address(ps[0][0]):
                return byref_target(lf, ps[0][0])
            # a variable or ByRef parameter when the Variant's use is in a later
            # block (Printer.DrawWidth = a1: the auto-instancing test of Printer
            # lies between; it printed the tag, Printer.DrawWidth = 16386)
            ref = lf.frame_ptr(ps[0][0])
            for _ in range(4):
                # (a parameter's pointer kept in a register, or saved in the slot that held
                # it in the last Variant: mov esi, [ebp+8]; mov edi, [ebp-0E0h]; A4 target1)
                if not (isinstance(ref, Var) and (ref.space == "reg" or ref.space == "local" and
                                                  isinstance(ref.off, int))):
                    break
                rs = reaching(bi, si, ref.key(), set(), any_src=True)
                rs = [x for x, _ in rs or () if not (isinstance(x, Const) and x.value == 0)]
                if not same(rs):
                    break
                ref = lf.frame_ptr(rs[0])
            if isinstance(ref, ir.AddrOf) and isinstance(ref.e, Var):
                return ref.e
            if isinstance(ref, Var) and ref.space == "arg":
                size = {2: 2, 3: 4, 4: 4, 5: 8, 6: 8, 7: 8, 8: 4, 9: 4, 11: 2}.get(t.value & 0xfff)
                return Deref(ref, size, 0) if size else None
            return None
        payload = hi = None
        if t.value not in (0, 1):
            ps = reaching(bi, si, ("local", k + 8), ks)
            if not ps and t.value in (2, 3, 8, 9, 11, 13, 17):
                # a computed payload (Printer.PaintPicture vC8.Image, ...: an
                # object stored as VT_DISPATCH printed its type word, 9): the
                # slot holds it, when one definition reaches
                ds = defs_at(bi, si, ("local", k + 8))
                if ds and len(ds) == 1 and "entry" not in next(iter(ds)) and \
                        reaching(bi, si, ("local", k + 8), ks, any_src=True):
                    return Var("local", k + 8)
            if not ps or not same([x for x, _ in ps]):
                return None
            payload = ps[0][0]
            if t.value in (5, 6, 7):        # an 8-byte payload: its high half too
                hs = reaching(bi, si, ("local", k + 12), ks)
                if not hs or not same([x for x, _ in hs]):
                    return None
                hi = hs[0][0]
        return variant_value(lf, t.value & 0x7fff, payload, hi)

    out = []
    for bi, (a, ss, br) in enumerate(body):
        keep = []
        for si, s in enumerate(ss):
            def fix(x, si=si):
                # a Variant built around a variable (VT_BYREF | vt, its address at +8),
                # read here: the variable.  As a value the slot is only the type word,
                # Controls("font")(16386) for (v24), TextHeight(16392) for (a1)
                slot = x.e if isinstance(x, ir.AddrOf) else x
                if isinstance(slot, Var) and slot.key() in byref_slots:
                    v = value_at(bi, si, slot.off)
                    if isinstance(v, (Var, Deref)):
                        return ir.AddrOf(v) if isinstance(x, ir.AddrOf) else v
                if isinstance(x, ir.AddrOf):
                    return x                # the variable itself, not its value
                if isinstance(x, Call) and x.name in ("VarForInit", "VarForNext", "ForEachAry", "NextEachAry"):
                    return x
                if isinstance(x, Var) and x.key() in reads and getattr(x, "byval_whole", False):
                    v = value_at(bi, si, x.off)
                    if v is not None:
                        return v
                    g = getattr(x, "payload_guess", None)
                    # (a module variable's address with no type word to go by:
                    # VT_BYREF, Printer.Scale (0, 0)-(global_F06FBC, ...))
                    return byref_target(lf, g) if g is not None and lf.data_address(g) else g
                return None
            keep.append(substitute_expr(s, fix) if any(
                isinstance(x, Var) and (x.key() in reads and getattr(x, "byval_whole", False) or
                                        x.key() in byref_slots)
                for e in stmt_exprs(s) if not (isinstance(s, Assign) and e is s.dst)
                for x in e.walk()) else s)
        if br is not None and any(isinstance(x, Var) and x.key() in reads and
                                  getattr(x, "byval_whole", False) for x in br.cond.walk()):
            br = type(br)(br.cond.replace(lambda x: (
                x if isinstance(x, ir.AddrOf) else
                (value_at(bi, len(ss), x.off) or getattr(x, "payload_guess", None))
                if isinstance(x, Var) and x.key() in reads and getattr(x, "byval_whole", False)
                else None)))
        out.append((a, keep, br))
    return out


def fold_variant_slot_reads(body, lf):
    """A Variant frame slot read where its *value* is wanted.  fold_variant_temps
    merges a slot's (tag, payload) stores into one assignment only while nothing
    reads the halves separately; when a call takes the whole 16-byte copy the pair
    stays and a read of the tag half alone reaches the inliner, which then hands
    out the tag as the value (gh_json Main: `o("n")` indexed by 8, `c.Add Null`
    by 1).  Read the pair instead: the tag at k says what the payload at k+8 is."""
    from .ir import Missing
    VT = VARIANT_TAGS
    keys = getattr(lf, "variants", ())
    if not keys:
        return body

    def value_of(t, payload, hi):
        return variant_value(lf, t, payload, hi)

    def base(x):
        """Frame offset of a local, renamed copies included: rename_block_temps
        makes a slot (off, seq), and the pair's halves keep their own names."""
        if not isinstance(x, Var) or x.space != "local":
            return None
        if isinstance(x.off, int):
            return x.off
        if isinstance(x.off, tuple) and isinstance(x.off[0], int):
            return x.off[0]
        return None

    out = []
    for a, ss, br in body:
        last = {}                           # frame offset -> last value stored

        def fix(x):
            if isinstance(x, Call) and x.name in ("VarForInit", "VarForNext", "ForEachAry", "NextEachAry"):
                # the counter and the loop's state are variables, not values
                # (VarForInit(&counter, ...) became VarForInit(Empty, ...))
                k = 4 if x.name.endswith("Ary") else 3          # (VARTYPE, &state, &state, &element)
                c = Call(x.name, list(x.args[:k]) + [y.replace(fix) for y in x.args[k:]],
                         x.vbtype, x.raw, x.method, x.obj, x.pure)
                return x._keep(c)
            if isinstance(x, Call) and isinstance(x.raw, int):
                # a Variant by value, all its four dwords pushed: the tag stays the tag, and the value
                # is what the third dword holds.  Read as a plain variable its slot is the pooled one,
                # with what the store before it didn't leave there once that was gone (a target
                # Refresh1 Proc_438C00 ..., False, v40 printed vC8, a String: v40 = False was a
                # type mismatch under On Error Resume Next, and the list lost its bold)
                args, hit = list(x.args), False
                for i in range(len(args) - 3):
                    k = base(args[i])
                    if k is None or ("local", k) not in keys or \
                            any(base(args[i + j]) != k + 4 * j for j in (1, 2, 3)):
                        continue
                    tag = last.get(k)
                    if not isinstance(tag, Const) or not isinstance(tag.value, int) or \
                            (tag.value & 0xbfff) not in VT or tag.value & 0x4000 or tag.vbtype is not None:
                        continue
                    v = value_of(tag.value & 0x7fff, last.get(k + 8), last.get(k + 12))
                    if v is not None:
                        args[i], args[i + 2] = Const(tag.value), v
                        hit = True
                if hit:
                    c = Call(x.name, [y.replace(fix) for y in args], x.vbtype, x.raw, x.method, x.obj, x.pure)
                    return x._keep(c)
            off = base(x)
            if off is None or ("local", off) not in keys:
                return None
            tag = last.get(off)
            if not isinstance(tag, Const) or not isinstance(tag.value, int) or \
                    (tag.value & 0xbfff) not in VT or tag.value & 0x4000:
                return None                 # not a tag word read as a Variant
            if tag.vbtype is not None:      # the whole Variant was stored at once
                return None                 # (a VariantCopy of a literal): json_ParseValue
                                            # = False leaves Const(0,"Boolean") here,
                                            # and a bare 0 would read as VT_EMPTY
            return value_of(tag.value & 0x7fff, last.get(off + 8), last.get(off + 12))

        keep = []
        for s in ss:
            n = substitute_expr(s, fix)
            keep.append(n)
            if isinstance(n, Assign) and isinstance(n.dst, Var):
                off = base(n.dst)
                if off is not None:
                    # a pair fold_variant_temps merged holds the value itself: v = 9
                    # is no VT_DISPATCH tag (gh_dict TryAdd(d, "a", 9) read the
                    # stale payload slot and became TryAdd(d, "a", &0))
                    last[off] = None if getattr(s, "whole_variant", False) else n.src
        if br is not None:
            br = type(br)(br.cond.replace(fix))
        out.append((a, keep, br))
    return out


def only_converts(e):
    """A conversion of memory and nothing else.  In a register no one reads that is what the lift left of a value
    stored already, at the end of a block where the slots read here have other names by now: no statement of
    VB's converts and drops the result.  (A call in it does run: CInt(Proc() - g).)"""
    return isinstance(e, Call) and e.name in CONVERSIONS and \
        not any(isinstance(x, Call) and x.name not in CONVERSIONS for x in e.walk())


def repeats_earlier(ss, s):
    """Does a statement before s in ss hold the very expression s assigns?"""
    want = s.src
    for t in ss:
        if t is s:
            return False
        for e in stmt_exprs(t):
            for y in e.walk():
                if type(y) is type(want) and y.key() == want.key():
                    return True
    return False


def substitute_expr(s, f):
    """Statement with every sub-expression mapped through f (None = keep)."""
    if isinstance(s, Assign):
        n = Assign(s.dst if isinstance(s.dst, Var) else s.dst.replace(f), s.src.replace(f), s.kind)
    elif isinstance(s, (ExprStmt, CallStmt)):
        n = type(s)(s.e.replace(f))
    elif isinstance(s, ir.Return) and getattr(s, "value", None) is not None:
        n = ir.Return()
        n.value = s.value.replace(f)
    else:
        return s
    return keep_flags(n, s)


STMT_FLAGS = ("whole_variant", "fixstr", "record", "cast_set", "fp_store")


def keep_flags(n, s):
    """n, a rewrite of statement s, with s's address and the flags later
    passes read (a pair fold_variant_temps merged lost whole_variant in
    rename_object_temps, and fold_variant_slot_reads took Font.Size = 10's
    value for a VT_ERROR tag: a target printed "Font.Size = ")."""
    n.va = getattr(s, "va", None)
    for f in STMT_FLAGS:
        v = getattr(s, f, None)
        if v is not None and v is not False:
            setattr(n, f, v)
    return n


def safe_cond(e):
    """A condition that cannot fail or have effects when evaluated early."""
    for x in e.walk():
        if isinstance(x, (Prop, ir.Index)):
            return False
        if isinstance(x, Call) and (x.method or x.obj is not None or not x.pure or
                                    isinstance(x.raw, int)):
            return False
    return True


def merge_conditions(body, lf):
    """"If a > 70 And a < 80": the compiler tests each half and jumps to the
    same place when either fails.  Two such tests become one condition."""
    from .ir import BinOp as B
    from .structure import negate
    pos = {a: i for i, (a, _, _) in enumerate(body)}
    changed = True
    while changed:
        changed = False
        for a, ss, br in list(body):
            n = lf.nodes.get(a)
            if n is None or br is None or n.kind != "cond" or a not in pos:
                continue
            t, f = n.succs                   # br.cond is the condition for t
            for inner, outer_taken in ((t, True), (f, False)):
                m = lf.nodes.get(inner)
                if m is None or m.kind != "cond" or inner not in pos or len(m.preds) != 1:
                    continue
                _, mss, mbr = body[pos[inner]]
                if mss or mbr is None:
                    continue
                if not safe_cond(mbr.cond):
                    # VB's And/Or evaluate both sides: an inner test that calls,
                    # reads a property or indexes an array ran only when the
                    # outer one passed (If Not o Is Nothing Then If o.Pattern = s)
                    continue
                other = f if outer_taken else t     # where the outer test goes otherwise
                mt, mf = m.succs
                c1 = br.cond if outer_taken else negate(br.cond)   # reach inner when c1
                if mf == other:                       # inner fails -> same place
                    cond, succs = B("And", c1, mbr.cond), [mt, other]
                elif mt == other:                     # inner succeeds -> same place
                    cond, succs = B("And", c1, negate(mbr.cond)), [mf, other]
                else:
                    continue
                # rewrite the outer node
                n.succs = succs
                body[pos[a]] = (a, ss, type(br)(cond))
                for x in m.succs:
                    xn = lf.nodes.get(x)
                    if xn is not None:
                        xn.preds = [a if p == inner else p for p in xn.preds]
                        xn.preds = list(dict.fromkeys(xn.preds))
                del body[pos[inner]]
                del lf.nodes[inner]
                pos = {x: i for i, (x, _, _) in enumerate(body)}
                changed = True
                break
            if changed:
                break
    return body


def thread_empty(body, lf):
    """Blocks with nothing left in them just forward to their successor:
    let their predecessors jump there directly.  Not an error handler's first
    block: no edge reaches it, so the code after it would go with it (a target
    Copy1 Form_Load: the handler starts with the auto-instancing test of a form,
    Cancel in the file dialog should Unload Me and got an empty handler)."""
    entries = set(getattr(lf, "handlers", ())) | set(getattr(lf.p, "handlers", {}).values())
    changed = True
    while changed:
        changed = False
        for i, (a, ss, br) in enumerate(body):
            n = lf.nodes.get(a)
            if n is None or ss or br is not None or a == lf.p.va or len(n.succs) != 1 or \
                    n.kind == "exit" or a in entries:
                continue
            s = n.succs[0]
            if s == a or s not in lf.nodes:
                continue
            sn = lf.nodes[s]
            for p in n.preds:
                pn = lf.nodes.get(p)
                if pn is None:
                    continue
                pn.succs = [s if t == a else t for t in pn.succs]
                if p not in sn.preds:
                    sn.preds.append(p)
                if pn.kind == "cond" and len(set(pn.succs)) == 1:
                    # both edges now meet: the test decides nothing
                    pn.succs, pn.kind = [s], "jmp"
                    for j, (pa, pss, pbr) in enumerate(body):
                        if pa == p:
                            body[j] = (pa, pss, None)
            sn.preds = [p for p in sn.preds if p != a]
            if getattr(lf.p, "gosubs", None):
                for x in lf.nodes.values():     # (a Return may go here: cfg.build)
                    if a in getattr(x, "flow_extra", ()):
                        x.flow_extra = [s if t == a else t for t in x.flow_extra]
            del lf.nodes[a]
            del body[i]
            changed = True
            break
    for j, (pa, pss, pbr) in enumerate(body):          # and any left from earlier passes
        pn = lf.nodes.get(pa)
        if pbr is not None and pn is not None and len(set(pn.succs)) == 1:
            pn.succs, pn.kind = [pn.succs[0]], "jmp"
            body[j] = (pa, pss, None)
    return body


# ------------------------------------------------------------ register webs
def split_register_webs(body, lf):
    """A register the compiler reuses for unrelated values (esi as a loop
    counter, then as a pointer to a(i) for a With block) is one name for
    several variables.  Split each register into webs (the definitions
    that reach a common use, and those uses) and give every web after the
    first its own name.  Reads only ever see definitions of their own web,
    so the meaning is unchanged."""
    idx = {a: i for i, (a, _, _) in enumerate(body)}
    defs = {}                               # reg key -> [(bi, si)]
    mixed = mixed_frame_slots(body, lf)
    for bi, (a, ss, br) in enumerate(body):
        for si, s in enumerate(ss):
            if isinstance(s, Assign) and isinstance(s.dst, Var) and (
                    s.dst.space == "reg" and isinstance(s.dst.off, str) or
                    # a parameter's slot reused as scratch (frameless code)
                    s.dst.space == "arg" and isinstance(s.dst.off, int) or
                    s.dst.key() in mixed):
                defs.setdefault(s.dst.key(), []).append((bi, si))
    # (not a parameter whose address is passed on: the callee's writes through
    # it are definitions no web sees - VBDoom ParseTrack q = q + ReadVLQ(q))
    taken = address_taken(body)
    regs = [k for k, d in defs.items() if (len(d) > 1 or k[0] == "arg") and k not in taken]
    if not regs:
        return body
    succ = {bi: [idx[t] for t in node_succs(lf, a) if t in idx] for bi, (a, _, _) in enumerate(body)}
    pred = {bi: [] for bi in succ}
    for bi, ts in succ.items():
        for t in ts:
            pred[t].append(bi)
    renames = {}                            # (bi, si) of a def -> new Var; (bi, si, 'use') -> Var
    seq = [2000]
    dropped, as_set = set(), set()
    for rk in regs:
        dset = defs[rk]
        # reaching definitions of this register, per block entry
        gen = {}
        for bi, si in dset:
            gen[bi] = (bi, si)              # the last def in the block wins
        for bi, si in dset:
            if gen[bi][1] < si:
                gen[bi] = (bi, si)
        inn = {bi: set() for bi in succ}
        out = {bi: ({gen[bi]} if bi in gen else set()) for bi in succ}
        changed = True
        while changed:
            changed = False
            for bi in succ:
                new_in = set()
                for p in pred[bi]:
                    new_in |= out[p]
                if not pred[bi] and bi == 0:
                    new_in.add("entry")     # the value on entry
                new_out = {gen[bi]} if bi in gen else set(new_in)
                if new_in != inn[bi] or new_out != out[bi]:
                    inn[bi], out[bi] = new_in, new_out
                    changed = True
        # union the definitions that reach each use
        parent = {}

        def find(x):
            while parent.setdefault(x, x) != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(xs):
            xs = list(xs)
            for x in xs:
                find(x)                     # every element is a web of its own at first
            for x in xs[1:]:
                parent[find(x)] = find(xs[0])
        uses = []                           # (bi, si or -1, reaching defs)
        for bi, (a, ss, br) in enumerate(body):
            cur = set(inn[bi])
            for si, s in enumerate(ss):
                if rk in var_reads(s):
                    uses.append((bi, si, frozenset(cur)))
                    union(cur)
                if isinstance(s, Assign) and isinstance(s.dst, Var) and s.dst.key() == rk:
                    cur = {(bi, si)}
            if br is not None and any(isinstance(x, Var) and x.key() == rk
                                      for x in br.cond.walk()):
                uses.append((bi, -1, frozenset(cur)))
                union(cur)
        webs = {}
        for d in dset:
            webs.setdefault(find(d), []).append(d)
        entry_web = find("entry") if "entry" in parent else None
        roots = sorted(webs, key=lambda r: (r != entry_web, min(webs[r])))
        # the entry value keeps the name; when it is a web of its own (no
        # definition joins it: a parameter slot later reused), every
        # definition's web is renamed
        keep = 0 if entry_web is not None and entry_web not in webs else 1
        if rk in mixed:
            keep = 0                        # (each its own type: the slot's is the objects')
        for r in roots[keep:]:              # the first web keeps the register's name
            seq[0] += 1
            v = Var("reg", (rk[1], seq[0])) if rk[0] == "reg" else \
                Var("local", (rk[1], seq[0])) if rk in mixed else \
                Var("local", ("arg", seq[0]))   # the parameter keeps its entry value
            if rk in mixed and len(webs[r]) == 1:
                # a ByRef parameter's object fetched for a call: the parameter itself
                src0 = body[webs[r][0][0]][1][webs[r][0][1]].src
                if isinstance(src0, Deref) and src0.off == 0 and isinstance(src0.e, Var) and \
                        src0.e.space == "arg":
                    v = src0
                    dropped.add(webs[r][0])
            for d in webs[r]:
                renames[d] = v
                if isinstance(v, Var):
                    web_object_type(lf, rk, v, body[d[0]][1][d[1]].src, body, d)
            receiver = False
            for bi, si, rd in uses:
                if rd and find(next(iter(rd))) == r:
                    renames[(bi, si, "use", rk)] = v
                    if rk in mixed and any(
                            getattr(x, "obj", None) is not None and
                            getattr(x.obj, "key", lambda: None)() == rk
                            for s in (body[bi][1][si:si + 1] if si >= 0 else ())
                            for e in stmt_exprs(s) for x in e.walk()):
                        receiver = True
            if receiver and isinstance(v, Var):
                as_set.update(webs[r])      # a call's receiver: an object (slot's type is another's)
    if not renames:
        return body
    out_body = []
    for bi, (a, ss, br) in enumerate(body):
        keep = []
        for si, s in enumerate(ss):
            n = s
            for (k_bi, k_si, *rest) in [k for k in renames if len(k) == 4 and k[0] == bi and
                                       k[1] == si]:
                n = substitute_reads(n, rest[1], renames[(k_bi, k_si, "use", rest[1])])
            if (bi, si) in dropped:
                continue
            if (bi, si) in renames:
                n = keep_flags(Assign(renames[(bi, si)], n.src, "set" if (bi, si) in as_set else
                                      n.kind), s)
            keep.append(n)
        if br is not None:
            for k in [k for k in renames if len(k) == 4 and k[0] == bi and k[1] == -1]:
                br = type(br)(ir.subst(br.cond, {k[3]: renames[k]}))
        out_body.append((a, keep, br))
    return out_body


def mixed_frame_slots(body, lf):
    """Frame slots of the compiler's own that hold objects at one time and values at another:
    unoptimised code has one pool of plain dwords for its temporaries, and an index that goes
    through a bounds check crosses blocks (Set v50 = optionsform.CurrSpec, with v50 As optionsform,
    the form fetched into the slot earlier).  Reads see only their own definitions, so each use of
    the slot can have its own name and type.  Not what an error handler may read."""
    objs = getattr(lf, "objtype", None)
    if not objs:
        return set()
    cands = {}
    for _, ss, _ in body:
        for s in ss:
            if isinstance(s, Assign) and isinstance(s.dst, Var) and s.dst.space == "local" and \
                    isinstance(s.dst.off, int) and s.dst.key() in objs and \
                    not lf.is_named_local(s.dst):
                src = s.src
                value = isinstance(src, (BinOp, UnOp)) or \
                    isinstance(src, Const) and (src.value != 0 or s.kind != "set") or \
                    isinstance(src, (Call, Prop)) and getattr(src, "control", None) is None and \
                    getattr(src, "iface", None) is None and \
                    getattr(src, "vbtype", None) not in (None, "Object", "Variant") or \
                    isinstance(src, Var) and src.space == "local" and \
                    objs.get(src.key()) is None and src.key() not in lf.variants
                cands.setdefault(s.dst.key(), []).append(value)
    keys = {k for k, vs in cands.items() if any(vs)}
    if not keys:
        return keys
    keys -= address_taken(body)
    keys = {k for k in keys if k not in lf.arrays and k not in lf.udt and k not in lf.variants}
    if keys and getattr(lf, "handlers", None) or getattr(lf.p, "handlers", None):
        keys -= handler_live(block_live_in(body, lf), lf, body)
    return keys


def web_object_type(lf, rk, v, src, body=None, at=None):
    """A web of a register holding a control or object keeps the type of what it holds: its
    own name has no entry in objtype, so t_edi2001 = Me.Label1 printed without Set, the
    caption went into an untyped variable and t_edi2001.Top raised error 424 (a target
    Form20 Form_Resize: Page > Page Info stopped the program)."""
    ctl = getattr(src, "control", None)
    it = getattr(src, "iface", None) or getattr(ctl, "iface", None)
    if it is None and isinstance(src, Var) and body is not None and src.space == "local":
        # (a frame slot of the pool: what was set in it just before, not the first thing it ever
        # held: Set v70 = App: o2012 = v70 had Dim o2012 As Frame, a target Form3)
        ss = body[at[0]][1]
        for j in range(at[1] - 1, -1, -1):
            t = ss[j]
            if isinstance(t, Assign) and isinstance(t.dst, Var) and t.dst.key() == src.key():
                c2 = getattr(t.src, "control", None)
                it = getattr(t.src, "iface", None) or getattr(c2, "iface", None)
                break
    if it is None and isinstance(src, Var):
        it = lf.objtype.get(src.key())
    if it is None and ctl is not None:
        it = lf.objtype.get(rk)
    if it is not None:
        lf.objtype.setdefault(v.key(), it)


def substitute_reads(s, key, new):
    """s with reads of the variable key replaced by new (an Assign's target
    variable itself is not a read)."""
    m = {key: new}
    if isinstance(s, Assign):
        dst = s.dst if isinstance(s.dst, Var) else ir.subst(s.dst, m)
        n = Assign(dst, ir.subst(s.src, m), s.kind)
    elif isinstance(s, (ExprStmt, CallStmt)):
        n = type(s)(ir.subst(s.e, m))
    elif isinstance(s, ir.Return) and getattr(s, "value", None) is not None:
        n = ir.Return()
        n.value = ir.subst(s.value, m)
    else:
        return s
    for k, v in s.__dict__.items():
        if k not in n.__dict__:
            setattr(n, k, v)                # va, record, fixstr, ...
    return keep_flags(n, s)


def fold_elem_pointers(body, lf):
    """p = <address of a(i)> (VB's With a(i), or an element passed on),
    every use [p + k]: use a(i) / a(i).fK there instead, when neither the
    array nor anything in i can change between p's definition and a use."""
    idx = {a: i for i, (a, _, _) in enumerate(body)}
    defs = {}
    for bi, (a, ss, br) in enumerate(body):
        for si, s in enumerate(ss):
            if isinstance(s, Assign) and isinstance(s.dst, Var) and (
                    s.dst.space in ("reg", "tmp") or
                    # the frame slot of a With block (gh_zip With m_uFiles(lIdx))
                    s.dst.space == "local" and s.dst.off not in lf.kept):
                defs.setdefault(s.dst.key(), []).append((bi, si, s))
    succ = {bi: [idx[t] for t in node_succs(lf, a) if t in idx] for bi, (a, _, _) in enumerate(body)}
    from .types import param_args
    SCALARS = {"String", "Long", "Integer", "Byte", "Boolean", "Double", "Currency", "Date",
               "Single", "Variant", "Object"}
    # slots given one value (i - lb) * K, or (i - lb), which nothing in it changes
    written = {s.dst.key() for _, ss, _ in body for s in ss
               if isinstance(s, Assign) and isinstance(s.dst, Var)}
    kept_offs = {k: v[0][2].src for k, v in defs.items() if len(v) == 1 and k[0] == "local" and
                 any(isinstance(y, Deref) and y.off == 0x14 and y.size == 4
                     for y in v[0][2].src.walk()) and
                 not any(isinstance(y, Var) and y.key() in written for y in v[0][2].src.walk())}
    read_through = {y.e.key() for _, ss, _ in body for s in ss for e in stmt_exprs(s)
                    for y in e.walk() if isinstance(y, Deref) and isinstance(y.e, Var) and
                    y.e.space == "local"}
    # (or given on: a frame slot holding the element's address for MakeMove and Unmake, ChessBrainVB
    # OrderMoves, where v2C = a(i) printed a copy and MakeMove changed that)
    read_through |= {a.key() for _, ss, _ in body for s in ss for e in stmt_exprs(s)
                     for y in e.walk() if isinstance(y, Call) and isinstance(y.raw, int)
                     for a in y.args if isinstance(a, Var) and a.space == "local"}

    for key, places in defs.items():
        # a With block's slot is cleared again at End With
        zeros = {(bi, si) for bi, si, s in places if key[0] == "local" and
                 isinstance(s.src, Const) and s.src.value == 0}
        places = [x for x in places if (x[0], x[1]) not in zeros]
        if not places or len({x[2].src.key() for x in places}) != 1:
            continue
        # (one address given on several paths: fine when nothing in it ever changes, below)
        dbi, dsi, d = places[0]
        more = {(x[0], x[1]) for x in places[1:]}
        el = lf.dyn_element(d.src)
        if el is None and isinstance(d.src, BinOp) and kept_offs:
            # (the offset (i - lb) * K kept in a frame slot of its own, unoptimised-looking
            # procedures of an optimised build: With rob(robfocus), DarwinBots main)
            def expand_off(y):
                return kept_offs.get(y.key()) if isinstance(y, Var) else None
            el = lf.dyn_element(d.src.replace(expand_off))
        if el is None and isinstance(d.src, BinOp) and d.src.op in ("+", "-"):
            # (several dimensions: With Buckets(pos.x, pos.y), DarwinBots Add_Bot)
            at = dyn_at_address(lf, d.src, 0, 4)
            if at is not None and at.off == 0 and len(at.e.idx) > 1:
                el = at.e
            elif key[0] in ("reg", "tmp"):
                # (a field's: t_edi = pvData + (a1 - lb) * K + 24, for With rob(a1).pos, DarwinBots
                # bordercolls)
                fld = lf.dyn_field(d.src)
                if fld is not None:
                    el = Deref(fld[0], 4, fld[1])
        if el is None and isinstance(d.src, ir.AddrOf) and isinstance(d.src.e, (Deref, ir.Index)) \
                and (key[0] != "local" or not isinstance(key[1], int) or key in read_through):
            # o = VarPtr(a(i).fK) kept in a temporary (gh_zip Extract); or in a frame slot read
            # through: optimised code's pointer to .DNA in With rob(n), DarwinBots Amplification
            el = d.src.e
        if el is None and isinstance(d.src, BinOp) and key[0] in ("reg", "tmp"):
            # a fixed array's element by its address, pvData + i * size + k
            # (a target t_eax = Form1 + 2184: Form1(3), then t_eax.f4 and
            # `Proc_85A770 2, t_eax`)
            ea = lf.element_address(d.src)
            if isinstance(ea, ir.AddrOf) and isinstance(ea.e, (ir.Index, Deref)):
                el = ea.e
        root = el
        while isinstance(root, Deref):
            root = root.e
        if not isinstance(root, ir.Index) or not isinstance(root.arr, Var):
            continue
        info = lf.arrays.get(root.arr.key()) or {}
        esize = info.get("size")
        is_udt = bool(lf.udt.get(root.arr.key()) or info.get("udt"))

        def is_p(x):
            return isinstance(x, Var) and x.key() == key

        def p_plus(x):
            """p + k: the offset k, else None."""
            if isinstance(x, BinOp) and x.op == "+" and is_p(x.a) and \
                    isinstance(x.b, Const) and isinstance(x.b.value, int):
                return x.b.value
            return None

        def field(off, size=4, vbtype=None):
            if isinstance(el, Deref):               # a field already: its sub-field
                if off == 0 and size == el.size:
                    return el
                f = Deref(el.e, size, el.off + off)
                f.vbtype = vbtype
                return f
            if off == 0 and (not is_udt or size == esize):
                return el
            f = Deref(el, size, off)                # a(i).fK
            f.vbtype = vbtype
            return f

        # [slot + i * K + c] lifted as an array of the slot's own: an array inside the element
        # (With rob(t): .Ties(k).x, optimised, v38(k + 1).f2E; DarwinBots DrawRobTies)
        slot_stride = (lf.arrays.get(key) or {}).get("size")

        def inner(ix, off, size):
            if not slot_stride or len(ix.idx) != 1 or not isinstance(el, ir.Index):
                return None
            i = ix.idx[0].replace(repl)
            # (the stride it was lifted with: [slot + 34C0h] as slot(3376), when it was a guess of 4)
            st = getattr(ix, "stride", None) or slot_stride
            e = BinOp("*", i, Const(st)) if st != 1 else i
            scale, lf.scale = getattr(lf, "scale", 1), 1
            try:
                return lf.element_inline(el, e, off, size)
            finally:
                lf.scale = scale

        def repl(x):
            if isinstance(x, Call) and x.name in ("ReDim", "ReDim Preserve") and \
                    any(isinstance(a, ir.Index) and (is_p(a.arr) or p_plus(a.arr) is not None)
                        for a in x.args):
                # p is the address of the array's descriptor, an array in the element: ReDim
                # Preserve .arr(.size + 5) in With Buckets(pos.x, pos.y), DarwinBots Add_Bot
                # (read as an array of p's own its constant went into the address: .fA(.size))
                args = []
                for a in x.args:
                    if isinstance(a, ir.Index) and is_p(a.arr):
                        f = el if isinstance(el, Deref) else Deref(el, 4, 0)
                    elif isinstance(a, ir.Index) and p_plus(a.arr) is not None:
                        f = field(p_plus(a.arr), 4)
                    else:
                        args.append(a.replace(repl))
                        continue
                    args.append(a._keep(ir.Index(f, [i.replace(repl) for i in a.idx])))
                return x._keep(Call(x.name, args, x.vbtype, x.raw, x.method, x.obj, x.pure))
            if isinstance(x, Deref) and is_p(x.e):
                return field(x.off, x.size, getattr(x, "vbtype", None))
            if isinstance(x, Deref) and p_plus(x.e) is not None:
                return field(p_plus(x.e) + x.off, x.size, getattr(x, "vbtype", None))
            if isinstance(x, Deref) and isinstance(x.e, ir.Index) and is_p(x.e.arr):
                r = inner(x.e, x.off, x.size)
                if r is not None:
                    # (itself an array in the record: .mem(i + 455), slot(176).f6(i))
                    for attr in ("inline", "inline_n"):
                        if getattr(x, attr, None):
                            setattr(r, attr, getattr(x, attr))
                    return r
            if isinstance(x, ir.Index) and is_p(x.arr):
                r = inner(x, 0, min(slot_stride or 4, 4))
                if r is not None:
                    return r
            if isinstance(x, Call) and key[0] in ("local", "reg", "tmp"):
                # p (+ k) passed on: the element / a field, by reference
                plist = lf.m.plists.get(x.raw) if isinstance(x.raw, int) else None
                starts = {id(a): pi for a, pi in param_args(plist, x.args)} if plist else {}
                args = []
                for i, a in enumerate(x.args):
                    if is_p(a) and id(a) in starts:
                        pi = starts[id(a)]
                        args.append(ir.AddrOf(el) if plist[pi][2] not in SCALARS
                                    else ir.AddrOf(field(0)))
                    elif p_plus(a) is not None:
                        args.append(ir.AddrOf(field(p_plus(a))))
                    else:
                        args.append(a.replace(repl))
                obj = x.obj.replace(repl) if x.obj is not None else None
                return x._keep(Call(x.name, args, x.vbtype, x.raw, x.method, obj, x.pure))
            if p_plus(x) is not None:
                # a field's address, kept for later: in a slot, or optimised code's second pointer
                # (t_edi = t_esi + 40 of With rob(n), for .vel: DarwinBots UpdatePosition)
                return ir.AddrOf(field(p_plus(x)))
            return None
        new, uses, left = [], [], 0
        for bi, (a, ss, br) in enumerate(body):
            keep = []
            for si, s in enumerate(ss):
                if bi == dbi and si == dsi or (bi, si) in zeros or (bi, si) in more:
                    continue
                if any(is_p(x) for e in stmt_exprs(s) for x in e.walk()):
                    uses.append((bi, si))
                n = substitute_expr(s, repl)
                left += sum(1 for e in stmt_exprs(n) for x in e.walk() if is_p(x))
                keep.append(n)
            if br is not None:
                if any(is_p(x) for x in br.cond.walk()):
                    uses.append((bi, -1))
                br = type(br)(br.cond.replace(repl))
                left += sum(1 for x in br.cond.walk() if is_p(x))
            new.append((a, keep, br))
        if not uses:
            continue
        spread = False
        if left:
            # p + i * 68 + 112, or p itself by some other route: the address itself goes there, for
            # the folds of array elements to find (With rob(n): Get #f, , .mem(i).x in optimised
            # code, DarwinBots LoadRobotBody).  Only a register's, an address no store changes.
            if not isinstance(d.src, (BinOp, ir.AddrOf)):
                continue            # p is used as a plain value somewhere
            spread = True
        # nothing the element depends on is redefined on a path def -> use
        dep = {x.key() for x in el.walk() if isinstance(x, Var)}

        def redims_dep(c):
            """Does this ReDim or Erase change an array the element is in?  Not ReDim .gene(n), an
            array inside an element: With bots(n) goes on as it was (t90optwith Genes)."""
            for a in c.args:
                t = a.arr if isinstance(a, ir.Index) else a.e if isinstance(a, ir.AddrOf) else a
                if isinstance(t, Var) and t.key() in dep:
                    return True
                if isinstance(t, Deref) and isinstance(t.e, Var) and t.off == 0 and t.e.key() in dep:
                    return True
            return False
        killers = [(bi, si) for bi, (a, ss, br) in enumerate(body) for si, s in enumerate(ss)
                   if isinstance(s, Assign) and isinstance(s.dst, Var) and s.dst.key() in dep
                   or (isinstance(s, ExprStmt) and isinstance(s.e, Call) and
                       s.e.name in ("ReDim", "ReDim Preserve", "Erase") and redims_dep(s.e))]
        # (p is given again wherever that happens: With rob(i) in a loop, on each way round)
        def_at = {}
        for x in [(dbi, dsi)] + sorted(more):
            def_at.setdefault(x[0], []).append(x[1])

        def after(pos, target):
            """target reachable from just after pos without passing a definition of p."""
            (b0, s0), (b1, s1) = pos, target
            later = [q for q in def_at.get(b0, ()) if q > s0]
            if b0 == b1 and (s1 == -1 or s1 > s0):
                if not later or s1 != -1 and s1 < min(later):
                    return True
            if later:
                return False                # p is defined again in this block
            seen, stack = set(), list(succ[b0])
            while stack:
                b = stack.pop()
                if b in seen:
                    continue
                seen.add(b)
                here = def_at.get(b)
                if b == b1 and (not here or s1 != -1 and s1 < min(here)):
                    return True
                if here:
                    continue                # p is defined first
                stack.extend(succ[b])
            return False
        if any(after(dp, k) and any(after(k, u) for u in uses) for k in killers
               for dp in [(dbi, dsi)] + sorted(more)):
            continue
        if spread:
            # (on what repl made of the rest: p's own definitions are gone from it)
            mp = {key: ir.AddrOf(el)}
            new = [(a, [substitute_many(s, mp) for s in ss],
                    type(br)(ir.subst(br.cond, mp)) if br is not None else None)
                   for a, ss, br in new]
            new = fold_static_derefs(new, lf)       # (*(&a(i) + k): the field, for what looks at it)
        body = new
        if slot_stride:
            lf.arrays.pop(key, None)
        return fold_elem_pointers(body, lf)         # indices shifted: start over
    return body


def alias_param_reg_copies(body, lf=None):
    """A register every definition of which copies the same parameter, the
    parameter never assigned: the parameter (gh_zip pvOutputEndHeader keeps
    uFile's pointer in ebx on two paths)."""
    defs, assigned = {}, set()
    for _, ss, _ in body:
        for s in ss:
            if isinstance(s, Assign) and isinstance(s.dst, Var):
                assigned.add(s.dst.key())
                if s.dst.space in ("reg", "tmp"):
                    defs.setdefault(s.dst.key(), []).append(s.src)
    mapping = {}
    arrays = getattr(lf, "arrays", {}) if lf is not None else {}
    for k, srcs in defs.items():
        if srcs and all(isinstance(x, Var) and x.space == "arg" and x.key() == srcs[0].key()
                        for x in srcs) and srcs[0].key() not in assigned:
            mapping[k] = srcs[0]
        elif srcs and all(isinstance(x, Var) and x.key() == srcs[0].key() for x in srcs) and \
                srcs[0].key() in arrays and (arrays[srcs[0].key()].get("dyn") or
                                             arrays[srcs[0].key()].get("sa") is not None):
            # a register holding a dynamic array's descriptor: that array
            # (t_ecx = baExtra ... t_ecx(i), gh_zip pvLoadFileInfo), or a local fixed array's
            # data pointer, past a branch (t_ecx = v64 ... LSet v38 = t_ecx + 16, CompareShapes)
            mapping[k] = srcs[0]
    if not mapping:
        return body
    out = []
    for a, ss, br in body:
        keep = []
        for s in ss:
            if isinstance(s, Assign) and isinstance(s.dst, Var) and s.dst.key() in mapping:
                continue
            keep.append(substitute_many(s, mapping))
        if br is not None:
            br = type(br)(ir.subst(br.cond, mapping))
        out.append((a, keep, br))
    return out


def alias_array_param_copies(body, lf):
    """A register holding an array parameter (its SAFEARRAY** pointer) and
    never redefined is that parameter: t_edi = a2 ... F t_edi."""
    mapping, srcs = {}, {}
    for _, ss, _ in body:
        for s in ss:
            if isinstance(s, Assign) and isinstance(s.dst, Var) and s.dst.space == "reg":
                srcs.setdefault(s.dst.key(), []).append(s.src)
    for k, vs in srcs.items():
        a = vs[0]
        # every definition copies the same array parameter (re-loaded in a loop)
        if isinstance(a, Var) and a.space == "arg" and a.key() in lf.arrays and \
                a.off in lf.byref and all(isinstance(v, Var) and v.key() == a.key() for v in vs) \
                and not count_defs(body, a):    # (the slot reused as scratch: not the array)
            mapping[k] = a
    if not mapping:
        return body
    out = []
    for a, ss, br in body:
        keep = []
        for s in ss:
            if isinstance(s, Assign) and isinstance(s.dst, Var) and s.dst.key() in mapping:
                continue
            keep.append(substitute_many(s, mapping))
        if br is not None:
            br = type(br)(ir.subst(br.cond, mapping))
        out.append((a, keep, br))
    return out


def magic_div(e):
    """x \\ d compiled as a multiply by a reciprocal: q = Hi(m * x) \\ 2^s
    (sar), then q + (q >>> 31) to round towards zero.  Returns x \\ d or None."""
    from .machine import Hi
    if not (isinstance(e, BinOp) and e.op == "+"):
        return None
    q, fix = e.a, e.b
    if not (isinstance(fix, BinOp) and fix.op == "\\" and isinstance(fix.b, Const) and
            fix.b.value & 0xffffffff == 0x80000000 and fix.a == q):
        q, fix = e.b, e.a
        if not (isinstance(fix, BinOp) and fix.op == "\\" and isinstance(fix.b, Const) and
                fix.b.value & 0xffffffff == 0x80000000 and fix.a == q):
            return None
    s, h = 0, q
    if isinstance(q, BinOp) and q.op == "\\" and isinstance(q.b, Const) and \
            isinstance(q.b.value, int) and q.b.value > 0 and q.b.value & (q.b.value - 1) == 0:
        s, h = q.b.value.bit_length() - 1, q.a
    added = None
    if isinstance(h, BinOp) and h.op == "+" and isinstance(h.a, Hi):
        h, added = h.a, h.b                 # a multiplier >= 2^31: Hi(m * x) + x
    if not (isinstance(h, Hi) and isinstance(h.of, BinOp) and h.of.op == "*"):
        return None
    m, x = h.of.a, h.of.b
    if not isinstance(m, Const):
        m, x = x, m
    if not isinstance(m, Const) or not isinstance(m.value, int):
        return None
    mv = m.value
    if added is not None:
        if added != x or mv >= 0:
            return None
        mv += 1 << 32
    elif not -(1 << 31) < mv < 1 << 31 or not mv:
        return None
    sign = 1
    if mv < 0:
        # (a negative divisor's is the negated positive one's: x \\ -100, ChessBrainVB Eval's
        # Score = Score - a * b \\ 100)
        mv, sign = -mv, -1
    d = round((1 << (32 + s)) / mv)
    if d < 2 or -(-(1 << (32 + s)) // d) != mv:
        return None                         # not the reciprocal of a whole divisor
    return BinOp("\\", x, Const(sign * d))


def fold_byte_ops(body, lf):
    """x = x Or &H200 for a Long or an Integer, compiled on the byte it changes: or byte ptr [x + 1],
    2.  As VB can say it (ChessBrainVB Eval: WAttack(sq) = WAttack(sq) Or &H400 printed a(i).f1 =
    a(i).f1 Or 4, Invalid qualifier)."""
    def whole(d):
        """The number d is a byte of: (it, its size), or None."""
        if not isinstance(d, Deref) or d.size != 1 or d.off < 0:
            return None
        b = d.e
        if isinstance(b, ir.Index) and isinstance(b.arr, Var):
            info = lf.arrays.get(b.arr.key())
            n = (info or {}).get("size")
            if n in (2, 4) and d.off < n and not lf.udt.get(b.arr.key()):
                return b, n
        return None
    out = []
    for a, ss, br in body:
        keep = []
        for s in ss:
            w = whole(s.dst) if isinstance(s, Assign) else None
            if w is not None and isinstance(s.src, BinOp) and s.src.op in ("Or", "And", "Xor") and \
                    isinstance(s.src.b, Const) and isinstance(s.src.b.value, int) and \
                    s.src.a == s.dst:
                e, n = w
                bits = 8 * n
                m = (s.src.b.value & 0xff) << (8 * s.dst.off)
                if s.src.op == "And":
                    m |= ((1 << bits) - 1) ^ (0xff << (8 * s.dst.off))
                if m >= 1 << (bits - 1):
                    m -= 1 << bits
                keep.append(keep_flags(Assign(e, BinOp(s.src.op, e, Const(m)), s.kind), s))
                continue
            keep.append(s)
        out.append((a, keep, br))
    return out


def fold_magic_divs(body):
    def f(x):
        q = magic_div(x)
        if q is not None or not (isinstance(x, BinOp) and x.op == "+"):
            return q
        # the two parts among other terms: Depth \\ 3 + t + 4, the adds in the compiler's order
        terms = []

        def flat(e):
            if isinstance(e, BinOp) and e.op == "+":
                flat(e.a)
                flat(e.b)
            else:
                terms.append(e)
        flat(x)
        if len(terms) < 3:
            return None
        hit, again = False, True
        while again:                        # (more than one in the sum)
            again = False
            for i, fix in enumerate(terms):
                if not (isinstance(fix, BinOp) and fix.op == "\\" and isinstance(fix.b, Const) and
                        isinstance(fix.b.value, int) and fix.b.value & 0xffffffff == 0x80000000):
                    continue
                for j, t in enumerate(terms):
                    if j != i and t == fix.a:
                        d = magic_div(BinOp("+", t, fix))
                        if d is not None:
                            terms = [d] + [y for k, y in enumerate(terms) if k not in (i, j)]
                            hit = again = True
                            break
                if again:
                    break
        if not hit:
            return None
        out = terms[0].replace(f)
        for y in terms[1:]:
            out = BinOp("+", out, y.replace(f))
        return out
    def rest(x):
        # shr x, 31 that is no division's: 1 for a negative number
        if isinstance(x, BinOp) and x.op == "\\" and isinstance(x.b, Const) and \
                isinstance(x.b.value, int) and x.b.value & 0xffffffff == 0x80000000:
            return UnOp("-", BinOp("<", x.a.replace(rest), Const(0)))
        return None
    out = []
    for a, ss, br in body:
        keep = [substitute_expr(substitute_expr(s, f), rest) for s in ss]
        if br is not None:
            br = type(br)(br.cond.replace(f).replace(rest))
        out.append((a, keep, br))
    return out


def unbias_late(body, lf):
    """a(j - lLbound(a)) for a dynamic array: the subscript was the
    compiler's offset (kept in a temporary while the bounds were checked)
    and only inlining showed the lower bound in it; the index is j."""
    def f(x):
        if isinstance(x, ir.Index) and isinstance(x.arr, Var) and len(x.idx) == 1:
            lb = Deref(x.arr, 4, 0x14)
            r = lf.drop_term(x.idx[0], lb)
            if r is not None:
                e = ir.Index(x.arr, [r])
                e.vbtype = getattr(x, "vbtype", None)
                return e
        return None
    out = []
    for a, ss, br in body:
        keep = [substitute_expr(s, f) for s in ss]
        if br is not None:
            br = type(br)(br.cond.replace(f))
        out.append((a, keep, br))
    return out


def fold_record_sums(body, lf):
    """A fixed array of records reached as pvData + constant, or with the whole
    constant as a field offset: element and field.  What the earlier folds leave
    (a target `(global_F04AC4 + 728).f290`, `Proc_8B2B30(global_F04AC4 + 5872)`,
    `global_F04AC4.f88C`, and t_ecx = global_F04AC4 + 5856 assigned once)."""
    def record_array(v):
        info = lf.arrays.get(v.key()) if isinstance(v, Var) else None
        if info is None or info.get("dyn") or info.get("dims") or not info.get("fixed_bounds") \
                or not info.get("size") or info["size"] <= 8 or \
                not isinstance(info.get("lb") or 0, int):
            return None
        return info

    def field(v, info, c, size):
        k0, off = divmod(c, info["size"])
        if k0 < 0 or isinstance(info.get("n"), int) and k0 >= info["n"]:
            return None
        e = ir.Index(v, [Const(k0 + (info.get("lb") or 0))])
        e.vbtype = None
        return Deref(e, size, off)

    def sum_of(x):
        if isinstance(x, BinOp) and x.op == "+" and isinstance(x.b, Const) and \
                isinstance(x.b.value, int) and record_array(x.a) is not None:
            return x.a, x.b.value
        return None
    # registers holding such a sum, assigned once
    ndefs, sums = {}, {}
    for _, ss, _ in body:
        for s in ss:
            if isinstance(s, Assign) and isinstance(s.dst, Var):
                ndefs[s.dst.key()] = ndefs.get(s.dst.key(), 0) + 1
                if s.dst.space == "reg" and sum_of(s.src) is not None:
                    sums[s.dst.key()] = s.src
    sums = {k: v for k, v in sums.items() if ndefs[k] == 1 and not ndefs.get(v.a.key())}

    def f(x):
        if isinstance(x, Deref):
            e = sums.get(x.e.key()) if isinstance(x.e, Var) else None
            sm = sum_of(e if e is not None else x.e)
            if sm is not None:
                return field(sm[0], record_array(sm[0]), sm[1] + x.off, x.size)
            info = record_array(x.e)
            if info is not None and x.off >= info["size"]:
                return field(x.e, info, x.off, x.size)
            return None
        if isinstance(x, Call) and isinstance(x.raw, int):
            args = []
            changed = False
            for a in x.args:
                r = sums.get(a.key()) if isinstance(a, Var) else None
                sm = sum_of(r if r is not None else a)
                el = field(sm[0], record_array(sm[0]), sm[1], 4) if sm is not None else None
                if el is not None:
                    args.append(ir.AddrOf(el))
                    changed = True
                else:
                    args.append(a.replace(f))
            if changed or any(n is not o for n, o in zip(args, x.args)):
                return x._keep(Call(x.name, args, x.vbtype, x.raw, x.method,
                                    x.obj.replace(f) if x.obj is not None else None, x.pure))
        return None
    out = []
    for a, ss, br in body:
        keep = [substitute_expr(s, f) for s in ss]
        if br is not None:
            br = type(br)(br.cond.replace(f))
        out.append((a, keep, br))
    if not sums:
        return out
    read = set()                            # (a register still read keeps its value)
    for _, ss, br in out:
        for s in ss:
            for e in stmt_exprs(s):
                if not (isinstance(s, Assign) and e is s.dst):
                    read.update(x.key() for x in e.walk() if isinstance(x, Var))
        if br is not None:
            read.update(x.key() for x in br.cond.walk() if isinstance(x, Var))
    return [(a, [s for s in ss if not (isinstance(s, Assign) and isinstance(s.dst, Var) and
                                       s.dst.key() in sums and s.dst.key() not in read)], br)
            for a, ss, br in out]


def fold_fixed_array_copies(body, lf):
    """r = <a module-level fixed array's pvData> (the global slot), uses
    [r + k]: those are a(k \\ size + lb).  t_edi = LNG_POW2 ... t_edi.f2C is
    LNG_POW2(11).  A fixed array's pvData never changes, so every other use
    of r is the global's (ChessBrainVB FillKingCheckW keeps Board's in edx
    for Board(Target) at a variable offset: fold_offset_ivs then sees the
    array itself)."""
    slots = lf.m.__dict__.get("_array_slots")
    if slots is None:
        slots = lf.m._array_slots = lf.m.global_array_slots()
    srcs = {}
    for _, ss, _ in body:
        for s in ss:
            if isinstance(s, Assign) and isinstance(s.dst, Var) and s.dst.space == "reg":
                srcs.setdefault(s.dst.key(), []).append(s.src)
    mapping = {}
    for k, vs in srcs.items():
        g = vs[0]
        if isinstance(g, Var) and g.space == "global" and all(
                isinstance(v, Var) and v.key() == g.key() for v in vs):
            info = lf.arrays.get(g.key())
            fixed = info and not info.get("dyn") and info.get("size") or \
                isinstance(g.off, int) and g.off in slots
            if fixed and not count_defs(body, g):
                mapping[k] = (g, info)
    if not mapping:
        return body

    def f(x):
        if isinstance(x, Deref) and isinstance(x.e, Var) and x.e.key() in mapping:
            g, info = mapping[x.e.key()]
            if info and info.get("size") and not info.get("dims"):
                sz = info["size"]
                lb = info.get("lb") if isinstance(info.get("lb"), int) else 0
                k0, field = divmod(x.off, sz)
                if sz > x.size and info.get("fixed_bounds") and \
                        (not isinstance(info.get("n"), int) or k0 < info["n"]):
                    # a field of an element of a fixed array of records, as
                    # the lift's record_element makes it where it knows the
                    # register (a target Form1(1).f4: global_F04AC4.f2DC
                    # beside global_F04AC4(1).f0 in one condition)
                    e = ir.Index(g, [Const(k0 + lb)])
                    e.vbtype = None
                    return Deref(e, x.size, field)
                if x.off % sz == 0 and x.size <= sz:
                    e = ir.Index(g, [Const(x.off // sz + lb)])
                    e.vbtype = None
                    return e
        if isinstance(x, Var) and x.key() in mapping:
            return mapping[x.key()][0]
        return None
    out = []
    for a, ss, br in body:
        keep = []
        for s in ss:
            if isinstance(s, Assign) and isinstance(s.dst, Var) and s.dst.key() in mapping:
                continue
            n = substitute_expr(s, f)
            keep.append(n)
        if br is not None:
            br = type(br)(br.cond.replace(f))
        out.append((a, keep, br))
    return out


def note_byref_params(body, lf):
    """A parameter dereferenced anywhere in the final code is ByRef, also when
    the lifter only saw it through a register copy (lBitLen As Long in
    pvAppendBitsToBuffer: read once into a register, written back at the
    end; as ByVal the caller's counter never moved and its loop never ended)."""
    from .types import param_args
    plists = getattr(lf.m, "plists", None) or {}
    for _, ss, br in body:
        exprs = [e for s in ss for e in stmt_exprs(s)] + ([br.cond] if br is not None else [])
        for e in exprs:
            for x in e.walk():
                if isinstance(x, Deref) and x.off == 0 and isinstance(x.e, Var) and \
                        x.e.space == "arg" and isinstance(x.e.off, int):
                    if x.e.off not in lf.byref:
                        lf.byref.add(x.e.off)
                        lf.sizes.setdefault(x.e.key(), set()).add((x.size, False))
                if isinstance(x, Call) and isinstance(x.raw, int) and x.raw in plists:
                    # the parameter's value handed to a ByRef parameter: the pointer
                    # it was given, passed on (VB passes a ByVal parameter by its
                    # copy's address, &a1) - gh_json ConvertToIso(utc_LocalDate As
                    # Date) to ConvertToUtc: as ByVal its 4 bytes were no Date
                    # (not a Variant: those have conventions of their own - a ByVal
                    # Variant's first dword is no pointer: gh_json ConvertToJson)
                    plist = plists[x.raw]
                    for a, pi in param_args(plist, x.args):
                        if plist[pi][3] and plist[pi][2] != "Variant" and \
                                isinstance(a, Var) and a.space == "arg" and \
                                isinstance(a.off, int) and a.off >= 4:
                            lf.byref.add(a.off)
