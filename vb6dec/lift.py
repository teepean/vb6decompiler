"""Lift a procedure's cleaned CFG into IR statements per basic block."""
import re

import copy
from capstone import x86

from . import ir, cfg as cfgmod, insns, calls
from .ir import (Const, Str, Var, Me, AddrOf, Deref, BinOp, UnOp, Call, Prop,
                 Index, Unknown, Assign, ExprStmt, Free)
from .machine import State, SP, Hi, IatSlot, FSW, REG32, HIGH8, add, sub, s32, mul_const
from .listing import vb_string
from .typelib import Registry

X = x86
JCC = {"je": "=", "jz": "=", "jne": "<>", "jnz": "<>",
       "jl": "<", "jle": "<=", "jg": ">", "jge": ">=",
       "jb": "<", "jbe": "<=", "ja": ">", "jae": ">=",
       "js": "<", "jns": ">="}
NEG = {"=": "<>", "<>": "=", "<": ">=", ">=": "<", ">": "<=", "<=": ">"}


class Branch:
    """Condition under which a node's first successor is taken."""

    def __init__(self, cond):
        self.cond = cond

    def __repr__(self):
        return "if %r" % (self.cond,)


def through_variant(ref):
    """What Lifter.by_ref made of a Variant VB built around a variable (VT_BYREF | vt): only a ByRef
    Variant parameter takes one, whatever the variable's own type (Types.collect)."""
    import copy
    ref = copy.copy(ref)
    ref.wrapped = True
    return ref


class Lifter:
    def __init__(self, model, proc):
        self.m = model
        self.im = model.im
        self.p = proc
        self.late_bound = set()     # variables late-bound calls go through (As Object)
        self.fixed_strings = {}     # var key -> n for String * n variables
        self.nodes = cfgmod.build(model, proc)
        self.obj = proc.owner if proc.owner and proc.owner.kind != "Module" else None
        self.temps = {}             # SP offset -> Expr bound to that slot
        self.objtype = {}           # Var/SP key -> interface TypeInfo
        self.cast_types = {}        # key -> the interface an object Set into it was cast to
        self.fp_qword = set()       # frame Vars read as 8-byte floats (simplify.fold_double_slots)
        self.handlers = set()
        self.declare_argc = model.declare_argc
        self.out = {}
        from .listing import vtable_iface_map
        self.check_iface = {va: it for va, (it, off, g) in vtable_iface_map(model, proc).items()
                            if it is not None and getattr(it, "kind", -1) in (3, 4)}
        self.base_esp = prologue_esp(proc)
        # without "mov ebp, esp" the compiler uses ebp as one more register
        if not uses_frame(proc):
            self.GPR = Lifter.GPR + ("ebp",)
        if getattr(proc, "kind", None) == "event":
            self.result_slot = None
        elif self.obj is not None:
            mem = getattr(proc, "member", None)
            self.result_slot = method_result(proc) if mem is None or mem.returns else None
            if self.result_slot is None and getattr(proc, "ptr_result", False) and \
                    (mem is None or mem.returns):
                self.result_slot = "ret"    # written straight through the pointer
        else:
            self.result_slot = variant_return(proc) or result_slot(proc)
            proc.model_procs = model.procs
            proc.pair_use = pair_callers(model)
            self.small_rec = small_record_return(proc)
            if self.small_rec is not None:
                self.result_slot = self.small_rec[0]
        self.hidden_ret = self.obj is None and (variant_return(proc) is not None or
                                                getattr(proc, "udt_ret", False))
        self.variants = set()           # var keys known to hold Variants
        self.auto_new = {}              # var key -> class: "Dim x As New C"
        self.withevents = {}            # member var key -> class whose events it handles
        self.with_slots = {}            # frame slot -> &a(slot): an element's With block (_store)
        self.vt_types = {}              # var key -> {VB type} from the Variants VB built around it
        self.arrays = {}                # var key -> element size, bounds, dynamic
        self.sa_alias = {}              # SAFEARRAY descriptor slot -> the array (pvData slot)
        owner = self.obj.index if self.obj is not None else None
        udt_seed0 = {}
        for (o, key), info in getattr(model, "shared_arrays", {}).items():
            if o is None or o == owner:     # members of this object, and globals
                self.arrays[key] = dict(info, var=Var(key[0], key[1]))
                if info.get("udt"):
                    udt_seed0[key] = info["udt"]    # an array of records
        # the module-level fixed arrays their modules' variable tables list,
        # also where no procedure indexes them (tests/t19fixrec: recs(3) As TF,
        # only copied whole and given by field, was declared As Long)
        slots = model.__dict__.get("_array_slots")
        if slots is None:
            slots = model._array_slots = model.global_array_slots()
        for gva, (cb, dims, vt, ff, img, rd) in slots.items():
            key = ("global", gva)
            if key in self.arrays or not dims or not cb:
                continue
            info = {"size": cb, "dyn": False, "lb": dims[0][0], "n": dims[0][1],
                    "dims": dims if len(dims) > 1 else None, "fixed_bounds": True,
                    "var": Var("global", gva)}
            if vt == 0x24:
                from . import records
                info["record"] = True
                rec = records.model_record(model, rd) if rd else None
                if rec is not None and rec.name:
                    info["udt"] = rec.name
                    udt_seed0[key] = rec.name
            elif vt in (2, 3, 4, 5, 6, 7, 8, 9, 11, 12, 13, 17):
                info["vt"] = vt
            self.arrays[key] = info
        if self.obj is not None:
            for moff, (cb, dims, vt, rd) in model.object_array_slots(self.obj).items():
                key = ("member", moff)
                if key in self.arrays or not dims or not cb:
                    continue
                info = {"size": cb, "dyn": False, "lb": dims[0][0], "n": dims[0][1],
                        "dims": dims if len(dims) > 1 else None, "fixed_bounds": True,
                        "var": Var("member", moff)}
                if vt == 0x24 or rd:
                    from . import records
                    info["record"] = True
                    rec = records.model_record(model, rd) if rd else None
                    if rec is not None and rec.name:
                        info["udt"] = rec.name
                        udt_seed0[key] = rec.name
                elif vt in (2, 3, 4, 5, 6, 7, 8, 9, 11, 12, 13, 17):
                    info["vt"] = vt
                self.arrays[key] = info
        udt_seed, byref_seed = dict(udt_seed0), set()
        for (va, key), info in getattr(model, "proc_arrays", {}).items():
            if va == proc.va:               # array parameters / arrays passed to them
                cur = self.arrays.setdefault(key, {"size": None, "dyn": True, "lb": None,
                                                   "n": None, "var": Var(key[0], key[1])})
                for kk, vv in info.items():
                    if cur.get(kk) is None:
                        cur[kk] = vv
                if info.get("udt"):
                    udt_seed[key] = info["udt"]             # an array of UDTs
                if key[0] == "arg":
                    byref_seed.add(key[1])                  # an array parameter is ByRef
        self.rec_of = {}                # var key -> record descriptor VA (UDT with Strings)
        self.scale = 1                  # scale of the index in the operand being lifted
        self.spilled = set()
        self.ntmp = 0
        self.udt = dict(udt_seed)   # var key -> UDT name (from API parameters)
        self.structs = dict(model.struct_hint.get(proc.va, {}))   # ebp offset -> (UDT, size)
        if getattr(self, "small_rec", None) is not None:
            self.structs.setdefault(self.small_rec[0], (None, self.small_rec[1]))
        self.index_bounds = {}      # index expression key -> count its bounds check allows
        self.bounds_log = {}        # ... -> [(address of the check, count)], for BoundsAt
        for base, (name, size) in self.structs.items():
            if name:
                self.udt[("local", base)] = name
        # a Function returning a record UDT: its result slot holds that record
        self.rec_ret = record_return(model, proc) if self.obj is None else None
        self.mudt = method_udt_return(proc) if self.obj is not None else None
        if self.mudt is not None and self.result_slot == self.mudt[0]:
            self.rec_ret = record_desc_of(model, proc, self.mudt[0])
            if self.rec_ret is None:            # a UDT without Strings: size only
                self.structs.setdefault(self.mudt[0], (None, self.mudt[1]))
        if self.rec_ret is not None and isinstance(self.result_slot, int):
            rec = self.record_type(self.rec_ret)
            if rec is not None:
                self.rec_of[("local", self.result_slot)] = rec.va
                self.udt[("local", self.result_slot)] = rec.name
        self.tmp_types = {}
        self.byval_strings = set()  # arg offsets VB copied into a local String
        self.sizes = {}             # var key -> {(bytes, fpu)} accesses seen
        self.byref = set(byref_seed)    # arg offsets used as pointers
        self.seh_book = seh_bookkeeping(proc)
        self.known = {}             # slot offset -> last value stored
        self.out_known = {}         # node -> slots holding a module variable's address at its end
        self.named = cleanup_slots(model, proc, self.nodes)   # user String/Variant/Object locals
        self.pinned = set()         # and what crosses a statement where errors resume (simplify)
        for va, (d, _) in model.module_records().items():
            if d:                       # (a module variable's record, known or not to the code)
                from . import records
                rec = records.model_record(model, d)
                if rec is not None:
                    self.rec_of[("global", va)] = rec.va
                    self.udt[("global", va)] = rec.name
        self.retslot = None
        self.cur_st = None

    # ================================================================= driver
    GPR = ("eax", "ebx", "ecx", "edx", "esi", "edi")
    

    def run(self):
        n = len(getattr(self.p, "stepped_slots", ()))
        out = self.run_once()
        if len(getattr(self.p, "stepped_slots", ())) != n:
            return Lifter(self.m, self.p).run()     # (blocks before it took the slot for a With)
        return out

    def run_once(self):
        for g, desc in sorted(getattr(self.p, "new_guards", ())):
            # (cfg.new_guard: the __vbaNew2 that would tell what the variable is has gone)
            from .calls import predeclared
            predeclared(self, desc, g)
        order = self.rpo()
        pos = {a: i for i, a in enumerate(order)}
        # a register crossing into a merge point or a loop header becomes a
        # variable: the two paths reach it holding different expressions
        self.merge = {a for a, n in self.nodes.items()
                      if len(n.preds) > 1 or any(pos.get(p, 0) >= pos.get(a, 0) for p in n.preds)}
        self.merge.discard(self.p.va)
        fixed = self.fixed_regs()
        states = {self.p.va: State()}
        for h in getattr(self.p, "handlers", {}).values():
            # an error handler starts with the frame restored by the runtime
            hs = State()
            hs.regs["ebp"] = SP(-4)
            hs.esp = self.base_esp if self.base_esp is not None else -0x40
            states[h] = hs
        for a in order:
            st = states.get(a, State()).copy()
            if a in self.merge:
                # ebp and cached import pointers are invariant; the rest of
                # the registers hold different values on each path in - but
                # for a constant in a callee-saved register the procedure
                # sets only once (mov ebx, 2 for the VT_I2 tag of every Variant
                # temporary): every path that reads it set it (tests/t18varfor:
                # the second For's Variant bounds were not decoded)
                keep = {r: v for r, v in st.regs.items()
                        if r == "ebp" or isinstance(v, (IatSlot, Me)) or
                        isinstance(v, Const) and fixed.get((a, r)) == v.value}
                # a control or a frame variable's vtable pointer that every way
                # in holds in the register (a shared tail: the compiler's
                # cross-jumped "call [edx+0A4h]"; stable_ref)
                refs = [getattr(self, "out_refs", {}).get(p) for p in self.nodes[a].preds]
                if refs and all(x is not None for x in refs):
                    for r, v in refs[0].items():
                        if r not in keep and all(r in x and x[r] == v for x in refs[1:]):
                            keep[r] = v
                elif refs and any(x is not None for x in refs):
                    keep.update(self.frame_regs_over_loop(a, refs, keep))
                st.regs = keep
                st.spilled = {}             # (not every path in assigned the variables)
                # values left on the FPU stack cross the join in variables too
                # (spill: ChessBrainVB CalcNodes, fild Nodes then on one path
                # fimul NoOfThreads, then fcom)
                st.fpu = [Var("reg", "fp%d" % i, vbtype="Double") for i in range(len(st.fpu))]
                # arguments pushed before the paths joined (the compiler merges
                # identical calls): each path assigned its own values
                srefs = [getattr(self, "out_stack_refs", {}).get(p) for p in self.nodes[a].preds]
                if not srefs or any(x is None for x in srefs):
                    srefs = None
                for off in list(st.stack):
                    if self.base_esp is None or off < self.base_esp:
                        v = st.stack[off]
                        if isinstance(v, SP) and isinstance(self.frame_ptr(v), SP):
                            continue
                        if srefs and off in srefs[0] and \
                                all(off in x and x[off] == srefs[0][off] for x in srefs[1:]):
                            st.stack[off] = srefs[0][off]   # the same control pushed
                            continue                        # on every way in
                        if not isinstance(v, (Me, IatSlot)):
                            st.stack[off] = Var("stk", off)
            self.spill_to = any(s in self.merge for s in self.nodes[a].succs)
            self.spill_succs = [s for s in self.nodes[a].succs if s in self.merge]
            stmts, br, st2 = self.exec_node(self.nodes[a], st)
            self.out[a] = (stmts, br)
            self.out_known[a] = {k: v for k, v in self.known.items()
                                 if self.data_address(v) or self.stable_ref(v)}
            for s in self.nodes[a].succs:
                if s not in states:
                    states[s] = st2.copy()
        return self

    def frame_regs_over_loop(self, a, refs, keep):
        """A callee-saved register holding a frame address on every way into loop header a that is
        lifted so far, and that nothing in the loop writes: it holds it all the way round (lea edi,
        [ebp-124h] before For i = 0 To 10 of v124.f4(i) = x, DarwinBots renormalize_mutations)."""
        known = [x for x in refs if x is not None]
        out = {}
        for r, v in known[0].items():
            if r in keep or r not in ("ebx", "esi", "edi") or not isinstance(v, SP) or \
                    not all(r in x and x[r] == v for x in known[1:]):
                continue
            body, work = {a}, [p for p, x in zip(self.nodes[a].preds, refs) if x is None]
            while work:
                n = work.pop()
                if n not in body and n in self.nodes:
                    body.add(n)
                    work.extend(self.nodes[n].preds)
            names = {r, r[1:], r[1:] + "l"}     # (edi, di, dil)
            if not any(self.node_writes(n) & names for n in body):
                out[r] = v
        return out

    def node_writes(self, a):
        """Names of the registers node a's instructions write."""
        cache = self.__dict__.setdefault("_node_writes", {})
        got = cache.get(a)
        if got is None:
            got = set()
            for ins in self.nodes[a].insns:
                # (the registers lift cares about appear in the operands or are implicit)
                for x in ins.regs_access()[1]:
                    got.add(ins.reg_name(x))
            cache[a] = got
        return got

    def fixed_regs(self):
        """{(merge node, register): constant} for the callee-saved registers
        (ebx, esi, edi) when every write that reaches the node's entry put
        the same constant there (mov r, imm / xor r, r; calls preserve them):
        then every path in holds it (Run's merges keep it).  A write reaches
        until the next write of the register on the path: a loop that reuses
        edi and sets it back to 2 before its back edge holds 2 at its head
        (tests/t21font's Font.Size = 10 in a For loop: the Variant's type
        word stored from edi; the procedure's entry value is never read)."""
        got = getattr(self.p, "_fixed_regs", None)
        if got is not None:
            return got
        fam = {"bl": "ebx", "bh": "ebx", "bx": "ebx", "ebx": "ebx",
               "si": "esi", "esi": "esi", "di": "edi", "edi": "edi",
               # (fast code keeps a tag in a scratch register over a loop's head too: mov edx, 8008h
               # on both ways into While Keys(k) <> key, Variants built with it.  A call ends those)
               "al": "eax", "ah": "eax", "ax": "eax", "eax": "eax",
               "cl": "ecx", "ch": "ecx", "cx": "ecx", "ecx": "ecx",
               "dl": "edx", "dh": "edx", "dx": "edx", "edx": "edx"}
        last = {}                           # (node, register) -> its last write there
        try:
            for a, node in self.nodes.items():
                for ins in node.insns:
                    if ins.mnemonic == "call":
                        for f in ("eax", "ecx", "edx"):
                            last[(a, f)] = None
                    for r in ins.regs_access()[1]:
                        f = fam.get(ins.reg_name(r))
                        if f is None:
                            continue
                        ops = ins.operands
                        val = None
                        if ins.mnemonic == "mov" and len(ops) == 2 and \
                                ops[0].type == X.X86_OP_REG and ops[1].type == X.X86_OP_IMM and \
                                ins.reg_name(ops[0].reg) == f:
                            val = ops[1].imm & 0xffffffff
                            val = val - (1 << 32) if val >= 0x80000000 else val
                        elif ins.mnemonic == "xor" and len(ops) == 2 and \
                                ops[0].type == X.X86_OP_REG and ops[1].type == X.X86_OP_REG and \
                                ops[0].reg == ops[1].reg and ins.reg_name(ops[0].reg) == f:
                            val = 0
                        last[(a, f)] = val
        except Exception:
            last = {}
        got = {}
        for r in {r for _, r in last}:
            into = {}                       # node -> the values reaching its entry
            work = [a for (a, f) in last if f == r]
            while work:
                a = work.pop()
                out = {last[(a, r)]} if (a, r) in last else into.get(a, set())
                for b in self.nodes[a].succs:
                    if b not in self.nodes:
                        continue
                    have = into.setdefault(b, set())
                    if not out <= have:
                        have |= out
                        work.append(b)
            for b, vs in into.items():
                if len(vs) == 1 and None not in vs:
                    got[(b, r)] = next(iter(vs))
        self.p._fixed_regs = got
        return got

    def spill(self, st, stmts):
        """Materialise registers and pushed arguments that are about to cross
        a merge point.  The values are all in terms of the block's entry
        state, so the stores are a parallel copy (see parallel_copy)."""
        # (what the registers held, for a merge whose ways in all agree)
        # (also a form's predeclared variable: "mov eax, [Form6]; test; jne L;
        # __vbaNew2; mov eax, [Form6]; L:" printed t_eax = Form6, As Integer)
        names = self.m.global_names
        self.__dict__.setdefault("out_refs", {})[self.cur_node] = {
            r: v for r, v in st.regs.items() if self.stable_ref(v) or
            isinstance(v, Var) and v.space == "global" and names.get(v.off) or
            # (a frame address: "lea ecx, [ebp-430h]" on every way into a shared LSet,
            # a target Proc_8C7250 LSet v50 = t_ecx)
            # (or a stack slot: "mov edx, esp" for a Variant pushed by value, the stores
            # after the join; a target Menu1_Click t_edx.pad1(1) = v24)
            isinstance(v, SP) and r != "esp"}
        self.__dict__.setdefault("out_stack_refs", {})[self.cur_node] = {
            off: v for off, v in st.stack.items() if self.stable_ref(v)}
        pairs = []
        # the branch condition (flags) may read a register variable that is
        # about to get another value: it means the old one (gh_zip
        # pvLoadFileInfo: "If baExtra(lPos + 4) = 1" read edx after edx = pvData)
        if getattr(self, "spill_cond", False) and st.flags is not None:
            stale = set()
            for r in self.GPR:
                v = st.regs.get(r)
                if v is None or isinstance(v, (Me, IatSlot, SP)) or \
                        (isinstance(v, Var) and v.space == "reg" and v.off == r):
                    continue
                stale.add(("reg", r))
            hit = {x.key() for f in st.flags if hasattr(f, "walk") for x in f.walk()
                   if isinstance(x, Var) and x.key() in stale}
            if hit:
                saved = {}
                for k in sorted(hit, key=str):
                    self.ntmp += 1
                    t = Var("tmp", self.ntmp)
                    a = Assign(t, Var(k[0], k[1]))
                    a.va = self.cur_va
                    stmts.append(a)
                    saved[k] = t
                st.flags = tuple(ir.subst(f, saved) if hasattr(f, "walk") else f
                                 for f in st.flags)
        for off in sorted(st.stack):
            if self.base_esp is not None and off >= self.base_esp:
                continue
            v = st.stack[off]
            if v is None or isinstance(v, (Me, IatSlot)) or \
                    (isinstance(v, Var) and v.space == "stk" and v.off == off):
                continue
            if isinstance(v, SP):
                v = self.frame_ptr(v)           # &local: may differ per path
                if isinstance(v, SP):
                    continue
            pairs.append((Var("stk", off), v))
            st.stack[off] = Var("stk", off)
        for i, v in enumerate(st.fpu):
            if v is None or (isinstance(v, Var) and v.space == "reg" and v.off == "fp%d" % i):
                continue
            fv = Var("reg", "fp%d" % i, vbtype="Double")
            pairs.append((fv, v))
            st.fpu[i] = fv
        done = {}
        copies = []
        flags0 = st.flags
        for r in self.GPR:
            v = st.regs.get(r)
            if v is None or (isinstance(v, Var) and v.space == "reg" and v.off == r):
                continue
            if isinstance(v, SP):
                # frame addresses: kept (see FINDINGS, FileInfo), but a scratch register
                # going into a join may be the source of a shared LSet, another way
                # in holding something else (a target Proc_8C7250, Proc_D09260)
                fp = self.frame_ptr(v)
                if r in ("eax", "ecx", "edx") and isinstance(fp, AddrOf) and \
                        isinstance(fp.e, Var) and getattr(self, "spill_succs", ()) and \
                        any(self.reads_reg_first(s, r) for s in self.spill_succs):
                    pairs.append((Var("reg", r), fp))
                continue
            if isinstance(v, (Me, IatSlot)):
                continue                    # invariant; kept across merges
            if isinstance(v, Const) and all(self.fixed_regs().get((s, r)) == v.value
                                            for s in getattr(self, "spill_succs", ())):
                continue                    # the same constant on every path in (fixed_regs)
            if isinstance(v, Const) and st.spilled.get(r) is v:
                continue                    # its variable got it on this path already
            if id(v) in done:               # same value as an earlier register: copy it
                copies.append((Var("reg", r), done[id(v)]))
            else:
                done[id(v)] = Var("reg", r)
                pairs.append((Var("reg", r), v))
            self.spilled.add(r)
            if isinstance(v, Const) and r in ("ebx", "esi", "edi"):
                # a constant stays one for the successors that are no merge
                # (the merge reads the variable assigned here): a Variant's
                # type word stored from edi = 2 there was no VT_I2 any more
                # (tests/t21font: List1.Font.Size = 9 came out as = 2).  A
                # later spill on this path need not assign it again (two
                # copies made the arms of a target's "If Print1 = 0" differ)
                st.spilled[r] = v
                continue
            st.spilled.pop(r, None)
            st.regs[r] = Var("reg", r)
            # (not a number that happens to be the same: test ah, 1 with ebp = 1 kept over the
            # procedure, FPU compares printed as (ah And t_ebp) <> 0: DarwinBots touch)
            # (the flags as they were: mov edi, eax; EOF(1); test ax, ax.  eax's turn makes the test
            # read eax, the new one, and edi's holds the old eax by that very name: the test moved to
            # edi, and If Not EOF(1) became a test of the loop's counter.  DarwinBots LoadSimulation)
            if st.flags is not None and not isinstance(v, Const):
                st.flags = tuple(
                    Var("reg", r) if x is x0 and hasattr(x0, "key") and type(x0) is type(v) and
                    x0.key() == v.key() else x for x, x0 in zip(st.flags, flags0))
        for dst, v, copied in [(d, v, False) for d, v in self.parallel_copy(pairs)] + \
                [(d, v, True) for d, v in copies]:
            a = Assign(dst, v)
            if dst.space != "tmp" and not copied:
                self.note_type(dst, v)
            a.va = self.cur_va
            stmts.append(a)

    def parallel_copy(self, pairs):
        """Order stores whose values all read the old contents: a store goes
        once no pending value still reads its target; a cycle saves one old
        value in a temporary first ("eax = ecx \\ 3: ecx = ecx + 1", not the
        other way round)."""
        out = []
        pending = list(pairs)
        # "edx = (ecx + 1) Mod 3: ecx = ecx + 1" reads the old ecx only to
        # recompute the new one: store ecx first and use it
        for i in range(len(pending)):
            dst, v = pending[i]
            if isinstance(v, Var) or not any(isinstance(x, Var) and x.key() == dst.key()
                                             for x in v.walk()):
                continue
            k = dst.key()

            mark = Var("new", k)

            def swap(x, v=v):
                return mark if x == v else None
            rest = [(d, w) for d, w in pending if d is not dst]
            marked = [(d, w.replace(swap)) for d, w in rest]
            if any(isinstance(x, Var) and x.key() == k for _, w in marked for x in w.walk()):
                continue
            if all(w2 == w for (_, w2), (_, w) in zip(marked, rest)):
                continue                    # nobody reads it: the usual order is fine
            out.append((dst, v))
            pending = [(d, ir.subst(w, {mark.key(): dst})) for d, w in marked]
            break
        while pending:
            reads = [{x.key() for x in v.walk() if isinstance(x, Var)} for _, v in pending]
            for i, (dst, v) in enumerate(pending):
                k = dst.key()
                if not any(k in rd for j, rd in enumerate(reads) if j != i):
                    out.append((dst, v))
                    del pending[i]
                    break
            else:
                dst = pending[0][0]
                self.ntmp += 1
                t = Var("tmp", self.ntmp)
                out.append((t, dst))
                pending = [(d, ir.subst(v, {dst.key(): t})) for d, v in pending]
        return out

    def rpo(self):
        seen, post = set(), []

        def go(a):
            seen.add(a)
            for s in self.nodes[a].succs:
                if s in self.nodes and s not in seen:
                    go(s)
            post.append(a)
        go(self.p.va)
        order = post[::-1]
        for h in getattr(self.p, "handlers", {}).values():   # error handlers last
            if h in self.nodes and h not in seen:
                post2 = []
                seen_before = set(seen)

                def go2(a):
                    seen.add(a)
                    for s in self.nodes[a].succs:
                        if s in self.nodes and s not in seen:
                            go2(s)
                    post2.append(a)
                go2(h)
                order += post2[::-1]
        return order

    def data_address(self, v):
        """v is a constant address of a module variable (in .data)."""
        return isinstance(v, Const) and isinstance(v.value, int) and \
            self.im.section_of(v.value & 0xffffffff) == ".data"

    @staticmethod
    def stable_ref(v):
        """A form's control (Me.Text1), or the vtable pointer of a frame
        variable: what the compiler parks in a frame slot and reloads in a
        tail several paths share (a target Text1_LostFocus: four Cases
        jump into one "mov ebx, [ebp-158h]; mov edx, [ebp-658h]; call [edx+
        0A4h]", which printed vtbl_A4 Mid$(...) for Me.Text1.Text = ...)."""
        if isinstance(v, Prop):
            return v.control is not None and not v.args
        return isinstance(v, Deref) and v.off == 0 and isinstance(v.e, Var) and \
            v.e.space == "local" and isinstance(v.e.off, int)

    def entry_known(self, node):
        """Frame slots every predecessor left holding the same address of a
        module variable (unoptimised code: "mov [ebp-0B8h], offset Global" on
        both paths of App's auto-instantiation, then "mov eax, [ebp-0B8h]" at
        the join).  Only once all predecessors are lifted (not loop headers)."""
        outs = [self.out_known.get(p) for p in node.preds]
        if not outs or any(o is None for o in outs):
            return {}
        first = outs[0]
        return {k: v for k, v in first.items()
                if all(k in o and o[k] == v for o in outs[1:])}

    def exec_node(self, node, st):
        stmts = []
        self.cur_node = node.start
        self.last_call = None
        self.cur_stmts = stmts
        self.cur_st = st                # the machine state, for helpers that store
        self.known = self.entry_known(node)
        self.temps = {}
        self.hi8 = set()                # slots holding an 8-byte method result
        self.cur_bound = self.p.bounds_at.get(node.start)
        last = node.insns[-1] if node.insns else None
        done = 0
        for ins in node.insns:
            for s in stmts[done:]:          # (its own place, not the block's: udt.check_counts)
                if s is not None and getattr(s, "va", None) is None:
                    s.va = self.cur_va
            done = len(stmts)
            self.cur_va = ins.address
            m = ins.mnemonic
            if m == "call":
                self.do_call(st, ins, stmts)
                continue
            tail = self.p.tail if ins.address == self.p.va and \
                getattr(self.p, "tail", None) else getattr(self.p, "tail_jumps", {}).get(ins.address)
            if m == "jmp" and tail:
                # tail call: the caller's return address is on top, our own
                # arguments above it are the callee's
                tp = self.m.procs.get(tail)
                n = tp.ret_pop // 4 if tp is not None else 0
                st.esp += 4
                for i in range(n):
                    st.stack.setdefault(st.esp + 4 * i, Var("arg", 4 + 4 * i))
                self.do_call(st, ins, stmts)
                break
            if m == "jmp" and ins is last and node.kind == "exit" and \
                    self.tail_runtime(st, ins):
                # a tail call into the runtime: its result is ours (ChessBrainVB
                # CalcNodes = TotalNodes ends "jmp [__vbaFpI4]")
                self.do_call(st, ins, stmts)
                break
            if m == "ret" or m == "jmp" or m.startswith(("j", "loop")):
                if ins is last:
                    break
                continue                    # branch left over from a removed check
            insns.run(self, st, ins, stmts)
        if getattr(self, "spill_to", False):
            self.spill_cond = node.kind == "cond"
            self.spill(st, stmts)
            self.spill_cond = False
        if node.kind == "exit" and getattr(self.p, "float_ret", False) and not st.fpu and \
                getattr(self, "last_call", None) and self.last_call[1] is not None and \
                node.insns and self.last_call[0] >= node.insns[-1].address:
            # a Single / Double Function ending with a call: that callee's
            # result is its own (TimeElapsed = TimerDiff(TimeStart, Timer()))
            self.m.__dict__.setdefault("float_callees", set()).add(self.last_call[1])
        if node.kind == "exit":
            self.forward_small_result(st, stmts)
            r = self.return_value(st)
            if r is not None:
                stmts.append(ir.Return())
                stmts[-1].value = r
        br = None
        if node.kind == "cond" and getattr(node, "switch", None) is not None:
            reg, vals = node.switch
            x = st.get(reg)
            off = 0                         # index = mode - 1: test mode = v + 1
            if isinstance(x, BinOp) and x.op in ("+", "-") and isinstance(x.b, Const) and \
                    isinstance(x.b.value, int):
                off = x.b.value if x.op == "-" else -x.b.value
                x = x.a
            c = None
            for v in vals:
                t = BinOp("=", x, Const(v + off))
                c = t if c is None else BinOp("Or", c, t)
            br = Branch(c)
        elif node.kind == "cond":
            last = node.insns[-1]
            c = self.cond_expr(st, last.mnemonic)
            c = self.switch_guard(node, last.mnemonic, c)
            br = Branch(c)
        stmts = [s for s in stmts if s is not None]
        for s in stmts:
            if getattr(s, "va", None) is None:
                # (the last instruction's: the block's own made an element's store the first thing in it,
                # and statement numbers go by this: simplify.pin_statement_variables)
                s.va = node.insns[-1].address if node.insns else node.start
        return stmts, br, st

    # ============================================================== operands
    FPREGS = {getattr(X, "X86_REG_ST%d" % i): i for i in range(8)}

    def rd(self, st, op, fp=False):
        if op.type == X.X86_OP_REG and op.reg in self.FPREGS:
            return st.st(self.FPREGS[op.reg])
        if op.type == X.X86_OP_REG:
            r = REG32.get(op.reg)
            if r is None:
                return Unknown("reg%d" % op.reg)
            v = st.get(r)
            if op.reg in HIGH8:
                if isinstance(v, FSW):
                    return FSWHigh(v)       # ah after fnstsw: C0 (0x01), C2 (0x04), C3 (0x40)
                if v is None:
                    return Unknown("%s_hi8" % r)
                # bits 8..15 (test ah, 40h: the VT_BYREF bit of a Variant's tag)
                if isinstance(v, Const) and isinstance(v.value, int):
                    return Const((v.value >> 8) & 0xff)
                return BinOp("\\", BinOp("And", v, Const(0xff00)), Const(0x100))
            return v
        if op.type == X.X86_OP_IMM:
            if not fp and op.size == 2:
                # (a word: cmp ax, 0FFFFh is = -1.  As 65535 an Integer never equals it, and
                # DarwinBots PopQStack's stack underflow went unseen)
                v = op.imm & 0xffff
                return Const(v - 0x10000 if v >= 0x8000 else v)
            if not fp:
                v = op.imm & 0xffffffff
                if self.im.valid(v):
                    t = vb_string(self.im, v)
                    if t is not None:
                        lit = Str(t)
                        lit.va = v          # (a Print / Input descriptor can look like one)
                        return lit
                    if v in self.m.empty_strings():
                        return Str("")      # the program's "" (Model.empty_strings)
                return Const(s32(op.imm))
            return Const(op.imm)
        if op.type == X.X86_OP_MEM:
            v = self.load(st, self.ea(st, op.mem), op.size, fp)
            if fp and op.size == 8 and isinstance(v, Var) and v.space == "local":
                self.fp_qword.add(v.key())
            return v
        if op.type == X.X86_OP_FP:
            return Const(op.fp)
        return Unknown("op")

    def wr(self, st, op, v, stmts, fp=False):
        if op.type == X.X86_OP_REG and op.reg in self.FPREGS:
            st.setst(self.FPREGS[op.reg], v)
            return
        if op.type == X.X86_OP_REG:
            r = REG32.get(op.reg)
            if r is None:
                return
            if op.reg in HIGH8:
                return
            st.set(r, v)
            return
        if op.type == X.X86_OP_MEM:
            self.store(st, self.ea(st, op.mem), v, op.size, stmts, fp)

    def ea(self, st, mem):
        if mem.segment == X.X86_REG_FS:
            return ("fs", mem.disp)
        b = st.get(REG32[mem.base]) if mem.base else None
        i = st.get(REG32[mem.index]) if mem.index else None
        if b is None and i is None:
            return ("abs", mem.disp & 0xffffffff)
        if isinstance(b, SP) and i is None:
            return ("stack", b.off + mem.disp)
        if isinstance(i, Const) and isinstance(i.value, int) and b is not None:
            # "mov ecx, 9B0h; mov dx, [eax + ecx]": a fixed offset in a register, a
            # field of a constant element (a target Proc_8C7250 printed
            # global_F04AC4(1240).f0, an index of 2480 / 2, for (3).f218)
            return ("mem", b, mem.disp + i.value * mem.scale, None, 1) \
                if not isinstance(b, SP) else ("stack", b.off + mem.disp + i.value * mem.scale)
        self.scale = mem.scale
        return ("mem", b, mem.disp, i, mem.scale)

    def addr_expr(self, st, mem):
        a = self.ea(st, mem)
        if a[0] == "stack":
            return SP(a[1])
        if a[0] == "abs":
            ms = self.module_struct_ref(Const(a[1]))
            return ms if ms is not None else AddrOf(self.global_var(a[1]))
        if a[0] == "mem":
            _, b, disp, i, sc = a
            if isinstance(b, Me) and i is None and disp:
                return AddrOf(Var("member", disp))
            if isinstance(b, Me) and i is not None:
                el = self.member_element(i, disp, 1)
                if el is not None:
                    return AddrOf(el)
            b = self.with_param(b)
            if isinstance(b, SP):
                # (the slot's value may be known here: Input #1, .mutarray(m) in With Load_mrates
                # printed Load_mrates + m * 4 + 4)
                if i is None:
                    return SP(b.off + disp)
                el = self.frame_record_elem(b, i, disp, 1)
                if el is not None:
                    return AddrOf(el.e if isinstance(el.e, Index) and el.off == 0 else el)
            if isinstance(b, Var) and b.space == "arg" and i is not None and \
                    b.off in self.__dict__.get("with_args", ()):
                # (only an argument With showed to be a record: lea on another is arithmetic,
                # gh_qr global_409060(a2 + a1 * 41))
                el = self.element_inline(b, i, disp, 1)
                if el is not None:
                    return AddrOf(el.e if isinstance(el.e, Index) and el.off == 0 and
                                  getattr(el.e.arr, "inline", None) else el)
            if i is not None and isinstance(b, AddrOf) and isinstance(b.e, Index):
                # &a(n).f(k), lea eax, [eax + ecx * 2 + 2B38h] (Str$(rob(n).mem(k)) printed the sum)
                el = self.element_inline(b.e, i, disp, 1)
                if el is not None:
                    return AddrOf(el.e if isinstance(el.e, Index) and el.off == 0 and
                                  getattr(el.e.arr, "inline", None) else el)
            e = b if b is not None else Const(0)
            if i is not None:
                # (add / mul_const fold the compiler's multiply by a constant:
                # lea eax, [ecx + ecx * 2] is ecx * 3)
                e = add(e, mul_const(i, sc) if sc != 1 else i)
            return add(e, Const(disp))
        return Unknown("lea")

    # ================================================================ memory
    def note_size(self, v, size, fp):
        if isinstance(v, (Var, Deref)):
            key = v.key() if isinstance(v, Var) else ("deref", v.e.key() if hasattr(v.e, "key") else None)
            self.sizes.setdefault(key, set()).add((size, fp))
            if fp and key[0] == "global":
                # program-wide: a module Double may be only moved in most
                # procedures (types.var_type)
                self.m.__dict__.setdefault("global_fp", set()).add(key[1])
                if size == 8:
                    self.m.__dict__.setdefault("global_fp8", set()).add(key[1])

    def load(self, st, a, size=4, fp=False):
        v = self._load(st, a, size, fp)
        self.note_size(v, size, fp)
        if fp and size == 4 and isinstance(v, (Deref, Index)):
            v.fp4 = True            # read as a Single (udt.fp_fields)
        if isinstance(v, Deref) and isinstance(v.e, Var) and v.e.space == "arg" and v.off != 0 and \
                isinstance(v.e.off, int):
            self.byref.add(v.e.off)     # a field through it: no ByVal (ReDim a2(a1.f5C))
        if isinstance(v, Deref) and isinstance(v.e, Var) and v.e.space == "arg" and v.off == 0:
            self.byref.add(v.e.off)
            self.sizes.setdefault(v.e.key(), set()).add((size, fp))
        # loads alone: a variable whose first dword is read as a value is no
        # Variant, whatever it is copied to or from (types.var_type); for a
        # ByRef parameter the value is read through the pointer the slot holds
        if isinstance(v, Var):
            self.__dict__.setdefault("load_sizes", {}).setdefault(v.key(), set()).add((size, fp))
        elif isinstance(v, Deref) and isinstance(v.e, Var) and v.e.space == "arg" and v.off == 0:
            self.__dict__.setdefault("deref_load_sizes", {}).setdefault(
                v.e.key(), set()).add((size, fp))
        return v

    def _load(self, st, a, size=4, fp=False):
        k = a[0]
        if k == "stack":
            off = a[1]
            if off in st.stack:
                return st.stack[off]
            if off in self.with_slots:
                # (_store: With a(i)): the latest block before here by address, as VB lays them out
                held = [x for x in self.with_slots[off] if x[0] <= self.cur_va]
                if held:
                    elem = max(held, key=lambda x: x[0])[1]
                    if elem is not None:
                        return elem
            if off in self.temps and not (self.impure(self.temps[off]) and self.base_esp is not None
                                          and off >= self.base_esp):
                return self.temps[off]
            if off - 4 in getattr(self, "hi8", ()):
                return Hi(self.slot_var(off - 4))   # the upper half of that result
            if off in self.known and off < 4 and \
                    (self.data_address(self.known[off]) or
                     isinstance(self.known[off], Prop) and self.stable_ref(self.known[off])) and \
                    not self.is_named_local(self.slot_var(off, size)):
                return self.known[off]      # a temporary holding &module variable (or a control)
            if off < 4 and size == 4 and not fp:
                # With (p): the slot only ever holds the parameter or the frame record's address
                sv = self.slot_var(off, size)
                w = self.with_param(sv)
                if w is not sv:
                    return w
            if off in self.known and off >= 4:
                v = self.known[off]
                tgt = self.slot_var(off, size)
                # not when the stored value reads the slot: the variable now
                # holds the new value, so the expression would be stale
                if not any(isinstance(x, Var) and isinstance(tgt, Var) and x.key() == tgt.key()
                           for x in v.walk()):
                    return v            # argument slot reused as scratch
            return self.slot_var(off, size)
        if k == "abs":
            va = a[1]
            if va in self.im.imports:
                return IatSlot(va, self.im.imports[va])
            if fp and self.im.section_of(va) == ".text":
                return self.fp_const(va, size)     # literal in the read-only constants
            return self.global_var(va, size)
        if k == "fs":
            return Unknown("fs%x" % a[1])
        _, b, disp, i, sc = a
        b = self.with_param(b)
        if isinstance(b, SP) and i is None:
            return self._load(st, ("stack", b.off + disp), size, fp)
        arr = self.module_struct_elem(b, i, disp, size)
        if arr is None and i is not None and isinstance(b, SP):
            arr = self.frame_record_elem(b, i, disp, size)
        if arr is None and i is not None and isinstance(b, Var) and b.space == "arg" and \
                isinstance(b.off, int) and b.off >= 4:
            # [p + i * k + c] of a parameter: an array inside the UDT it points
            # at (an array parameter holds a SAFEARRAY*, never pvData; VBDoom
            # HUlib_addLineToSText s.l(i).needsupdate)
            arr = self.element_inline(b, i, disp, size)
        if arr is None and i is not None and self.scale == 1 and isinstance(i, Var) and \
                i.space == "arg" and isinstance(i.off, int) and i.off >= 4 and \
                not (isinstance(b, Var) and b.space == "arg"):
            # the same with base and index the other way round (a Byte array, scale 1:
            # r.tavut(i) = x printed (v14 - 1)(a1.pad1(1)) = x)
            arr = self.element_inline(i, b, disp, size)
        if arr is None and i is not None and isinstance(b, BinOp) and b.op == "+" and \
                isinstance(b.a, Var) and b.a.space == "arg" and isinstance(b.a.off, int) and \
                b.a.off >= 4 and isinstance(b.b, Const) and isinstance(b.b.value, int):
            # ... or the field's offset already added to the parameter
            arr = self.element_inline(b.a, i, disp + b.b.value, size)
        if arr is None and i is None and isinstance(b, BinOp) and b.op == "+" and \
                isinstance(b.a, Var) and b.a.space == "arg" and isinstance(b.a.off, int) and \
                b.a.off >= 4 and not isinstance(b.b, Const):
            # ... or the index added to it (mov ecx, [ebp+8]; add ecx, i; mov [ecx+4Ch], al)
            scale, self.scale = self.scale, 1
            try:
                arr = self.element_inline(b.a, b.b, disp, size)
            finally:
                self.scale = scale
        if arr is None and i is not None and isinstance(b, Me):
            arr = self.member_element(i, disp, size)
        if arr is None and i is not None and b is None:
            arr = self.data_element(i, disp, size)
        if arr is None:
            arr = self.added_element(b, i, disp, size)
        if arr is None:
            arr = self.array_at(b, i, disp, size)
        if arr is None and i is not None and self.scale == 1 and isinstance(i, Var) and \
                i.key() in self.arrays and not self.arrays[i.key()].get("dyn"):
            arr = self.array_at(i, b, disp, size)   # [offset + pvData]: base and index swapped
        if arr is not None:
            return self.part_of_element(arr, size, fp, i)
        if isinstance(b, Me) and i is None:
            # offset 0 of any object is its vtable pointer; keep the object
            return Var("member", disp) if disp else Deref(b, size, 0)
        sv = self.static_var(b, disp) if i is None else None
        if sv is not None:
            return sv
        if i is None and isinstance(b, AddrOf) and isinstance(b.e, Index):
            self.note_elem_size(b.e, size, disp)
            return Deref(b.e, size, disp)       # an element (field) via __vbaDerefAry1
        if i is not None and isinstance(b, AddrOf) and isinstance(b.e, Index):
            el = self.element_inline(b.e, i, disp, size)
            if el is not None:
                return el
        if i is None and isinstance(b, Const) and self.data_address(Const(b.value + disp)):
            return self.global_var((b.value + disp) & 0xffffffff, size)   # *(&module var)
        if i is not None:
            return Deref(add(b if b else Const(0),
                             BinOp("*", i, Const(sc)) if sc != 1 else i), size, disp)
        return Deref(b if b is not None else Const(0), size, disp)

    def part_of_element(self, e, size, fp, index=None):
        """The low byte or word of a longer element, read alone: cmp byte ptr [edx + esi*2], 0 is
        (WAttack(sq) And 255) = 0, not WAttack(sq) = 0 (ChessBrainVB Eval: a square attacked only by
        a queen, bit 100h, counted as defended, and AddThreat never came)."""
        el = e.e if isinstance(e, Deref) and e.off == 0 else e     # (record_element's form of it)
        if fp or size >= 4 or not isinstance(el, Index) or not isinstance(el.arr, Var):
            return e
        info = self.arrays.get(el.arr.key())
        K = (info or {}).get("size")
        if not K or not size < K <= 4 or self.udt.get(el.arr.key()) or info.get("record"):
            return e
        if not info.get("fixed_bounds") or info.get("vt") not in (2, 3, 11):
            # (only an array known to hold numbers: in a dynamic one [p + i*4] read as a word is more
            # likely the field of a record of two Integers, tok(i).tipo, DarwinBots DetokenizeDNA)
            return e
        if getattr(self, "whole_element", False):
            return e                        # (or byte ptr [x], 2: that byte of all of x; fold_byte_ops)
        return BinOp("And", el, Const((1 << 8 * size) - 1))

    def low_value(self, e, size):
        """What an instruction on al or ax sees of a value kept in the register: a longer number's
        low part.  (test al, al after mov ax, [WAttack + sq*2].)"""
        wide = False
        if isinstance(e, Index) and isinstance(e.arr, Var):
            info = self.arrays.get(e.arr.key()) or {}
            wide = size < (info.get("size") or 0) <= 4 and not self.udt.get(e.arr.key()) and \
                not info.get("record") and info.get("vt") in (2, 3, 11) and bool(info.get("fixed_bounds"))
        elif isinstance(e, Deref):
            # (not an element's field: what size it is isn't known yet)
            wide = size < e.size <= 4 and not isinstance(e.e, Index)
        elif isinstance(e, Var) and e.space in ("global", "local", "member"):
            sizes = self.sizes.get(e.key())
            wide = bool(sizes) and all(size < w <= 4 and not f for w, f in sizes)
        return BinOp("And", e, Const((1 << 8 * size) - 1)) if wide else e

    def store(self, st, a, v, size, stmts, fp=False):
        n0 = len(stmts)
        self._store_value(st, a, v, size, stmts, fp)
        if fp and size == 4 and len(stmts) > n0 and isinstance(stmts[-1], Assign) and \
                isinstance(stmts[-1].dst, (Deref, Index)):
            stmts[-1].dst.fp4 = True        # written as a Single (udt.fp_fields)

    def line_marks(self):
        """{statement number: the line number to print there}.  mov [ebp - 4], k gives the statement's
        number, for Resume and for Erl: the descriptor's +18h has {count, u16 line per number}, read as
        line[k - 1] (the numbers run label, statement, label, statement: t83obscure).  Printed only if
        the program asks for Erl: 40 If n = 2 Then Error 5 ... Print Erl."""
        marks = self.__dict__.get("_line_marks")
        if marks is not None:
            return marks
        marks = self._line_marks = {}
        d = getattr(self.p, "procdesc", None)
        if not d or not self.im.valid(d) or "rtcGetErl" not in self.im.imports.values():
            return marks
        tab = self.im.u32(d + 0x18)
        if not self.im.valid(tab):
            return marks
        count = self.im.u32(tab)
        if not 0 < count < 0x10000:
            return marks
        ks = set()
        for ins in self.p.insns.values():
            ops = ins.operands
            if ins.mnemonic == "mov" and len(ops) == 2 and ops[0].type == X.X86_OP_MEM and \
                    ops[0].mem.base == X.X86_REG_EBP and ops[0].mem.disp == -4 and \
                    ops[1].type == X.X86_OP_IMM:
                ks.add(ops[1].imm)
        last = 0
        for k in sorted(ks):
            if 1 <= k <= count:
                line = self.im.u16(tab + 4 + 2 * (k - 1))
                if line and line != last:
                    marks[k] = line
                if line:
                    last = line
        return marks

    def tag_follows(self, off):
        """The next instruction stores a Variant tag below this slot: the slot is the payload of
        a VT_BYREF Variant being built around an element (t77pushecx Nayta gs(k)), no With."""
        addrs = self.__dict__.get("_addrs")
        if addrs is None:
            addrs = self._addrs = sorted(self.p.insns)
        import bisect
        i = bisect.bisect_right(addrs, self.cur_va)
        if i >= len(addrs):
            return False
        n = self.p.insns[addrs[i]]
        ops = n.operands
        return n.mnemonic == "mov" and len(ops) == 2 and ops[0].type == X.X86_OP_MEM and \
            ops[0].mem.base == X.X86_REG_EBP and ops[1].type == X.X86_OP_IMM and \
            bool(ops[1].imm & 0x4000) and self.ebp_slot(ops[0].mem.disp) == off - 8

    def ebp_slot(self, disp):
        """A frame slot's stack offset, from its [ebp + disp]."""
        return disp - 4

    def _store_value(self, st, a, v, size, stmts, fp=False):
        if size == 2 and not fp and isinstance(v, Const) and isinstance(v.value, int) and \
                not __import__("os").environ.get("T_WORD") and \
                not 0 <= v.value < 0x8000:
            # a word store of 0FFFFh is the Integer / Boolean -1 (True), not 65535
            # (an Integer holds -32768..32767: 65535 overflows, gh_zip bIsInit = True)
            w = v.value & 0xffff
            v = Const(w - 0x10000 if w >= 0x8000 else w, getattr(v, "vbtype", None))
        self._store(st, a, v, size, stmts, fp)
        if stmts and isinstance(stmts[-1], Assign) and stmts[-1].src is v:
            d = stmts[-1].dst
            self.note_size(d, size, fp)
            if isinstance(d, Var):
                # (stores alone: a Long read by its low byte for a Byte is no Byte)
                self.__dict__.setdefault("store_sizes", {}).setdefault(d.key(), set()).add(size)
            if isinstance(d, Deref) and isinstance(d.e, Var) and d.e.space == "arg" and d.off == 0:
                self.byref.add(d.e.off)
                self.sizes.setdefault(d.e.key(), set()).add((size, fp))

    def _store(self, st, a, v, size, stmts, fp=False):
        k = a[0]
        if k == "stack":
            off = a[1]
            if self.base_esp is not None and off < self.base_esp:
                st.stack[off] = v           # pushed argument area
                if size == 8:
                    st.stack[off + 4] = Hi(v)   # fstp qword [esp]: a Double argument
            else:
                st.stack.pop(off, None)
                self.temps.pop(off, None)
                self.hi8.discard(off)
                self.hi8.discard(off - 4)
                if off >= 4 and off in self.byref and not (off == 4 and self.obj is not None) \
                        and not (isinstance(v, Var) and v.key() == ("arg", off)):
                    # a store to a ByRef parameter's own slot, not through it: VB
                    # never repoints a ByRef parameter, so the compiler reuses the
                    # slot for a temporary of its own once it holds the pointer
                    # in a register (a target Proc_7C6BF0: `a1 = 4: F vC, a1` for
                    # the constant ByRef argument 4 typed a1, and its callers'
                    # Long counters, As Integer)
                    self.__dict__.setdefault("reused_args", set()).add(off)
                tgt = self.slot_var(off, size)
                if isinstance(tgt, Me) and not any(isinstance(x, Me) for x in v.walk()):
                    # optimised code keeps Me in a register and uses its slot as one more temporary
                    # (fstp [esp + 3Ch]; fld [esp + 3Ch]: DarwinBots DrawRobPer, Me.Circle (Me, Me)...)
                    self.__dict__.setdefault("reused_args", set()).add(off)
                    tgt = self.slot_var(off, size)
                if isinstance(tgt, Me):
                    return                  # the implicit Me argument
                if isinstance(tgt, Var) and tgt.space == "frame":
                    # SEH bookkeeping, and the statement's number
                    n = self.line_marks().get(v.value) if isinstance(v, Const) else None
                    if n:
                        stmts.append(ExprStmt(ir_line_number(n)))
                    return
                self.preserve(st, tgt, self.known.get(off), stmts, forget=False, value=v)
                if isinstance(v, BinOp) and v.op == "+" and isinstance(tgt, Var) and \
                        tgt.space == "local" and not self.is_named_local(tgt) and \
                        not self.tag_follows(off) and (any(
                            c == 1 and isinstance(e, Var) and
                            (self.arrays.get(e.key()) or {}).get("fixed_bounds")
                            for k, (e, c) in self.linear(v).items() if k is not None) or
                            self.global_struct(self.linear(v).get(None, [None, 0])[1] & 0xffffffff)):
                    # (With a(i) on a fixed array: pvData + i * size.  TeleportForm With
                    # Teleporters(i) printed o1036.f0 = global_897840 + i * 92&.  Or in a module
                    # record: With SimOpts.Specie(i).Mutables, o1001(2 + 1))
                    ea = self.element_address(v)
                    if isinstance(ea, AddrOf) and isinstance(ea.e, Index):
                        v = ea
                stepped = off in getattr(self.p, "stepped_slots", ())
                if isinstance(v, AddrOf) and isinstance(v.e, Index) and len(v.e.idx) > 1 and \
                        isinstance(v.e.arr, Var) and isinstance(tgt, Var) and \
                        tgt.space == "local" and not self.is_named_local(tgt) and \
                        not self.tag_follows(off) and not stepped:
                    # With a(i, j): the same, a variable for each subscript (DarwinBots With
                    # Buckets(x, y))
                    held = []
                    for n, x in enumerate(v.e.idx):
                        slot = Var("local", (off + 4 - 1, 7000 + 100 * len(self.with_slots.get(off, ())) + n))
                        self.note_type(slot, x)
                        stmts.append(Assign(slot, x))
                        held.append(slot)
                    elem = AddrOf(Index(v.e.arr, held))
                    self.with_slots.setdefault(off, []).append((self.cur_va, elem))
                    for r, e in list(st.regs.items()):
                        if e is v:
                            st.regs[r] = elem
                    self.known[off] = elem
                    return
                if isinstance(v, AddrOf) and isinstance(v.e, Index) and len(v.e.idx) == 1 and \
                        (isinstance(v.e.arr, Var) or getattr(v.e.arr, "inline", None)) and \
                        isinstance(tgt, Var) and \
                        tgt.space == "local" and not self.is_named_local(tgt) and \
                        not self.tag_follows(off) and not stepped:
                    # With a(i): the element's address in a frame slot for the whole block, read
                    # again in every block (DarwinBots With rob(t): [slot + i*2 + 2B38h] printed
                    # v30(i + 5532)).  The slot takes the index as it was then, and the block goes
                    # on with a(slot): it holds even if i changes inside.
                    elem = AddrOf(Index(v.e.arr, [tgt]))
                    self.with_slots.setdefault(off, []).append((self.cur_va, elem))
                    for r, e in list(st.regs.items()):
                        if e is v:
                            st.regs[r] = elem
                    self.known[off] = elem
                    self.note_type(tgt, v.e.idx[0])
                    stmts.append(Assign(tgt, v.e.idx[0]))
                    return
                # other slots known to hold this slot's (old) value keep that value:
                # "mov [v+8], eax(=[t]); mov [t], 0" (a string moved into a Variant)
                old = self.known.get(off)
                if isinstance(tgt, Var) and old is not None:
                    for k2, v2 in list(self.known.items()):
                        if k2 != off and hasattr(v2, "walk") and any(
                                isinstance(x, Var) and x.key() == tgt.key() for x in v2.walk()):
                            self.known[k2] = ir.subst(v2, {tgt.key(): old})
                elif isinstance(tgt, Var):
                    self.forget_known(tgt)
                if off in self.with_slots and not (isinstance(v, Const) and v.value == 0):
                    # (the slot on to other work: End With's zero isn't that; the blocks may
                    # be lifted out of order, so _load goes by address)
                    last = [x[1] for x in self.with_slots[off] if x[1] is not None]
                    if last and any(isinstance(x, Index) and x.key() == last[-1].e.key()
                                    for x in v.walk()):
                        # its own element plus something, stored back: no With but a pointer the
                        # compiler steps through a fixed array (mov edx, [esp + 14h]; add edx, 8;
                        # mov [esp + 14h], edx: ChessBrainVB ColImbalance read one element always)
                        self.p.__dict__.setdefault("stepped_slots", set()).add(off)
                    self.with_slots[off].append((self.cur_va, None))
                self.known[off] = v
                self.note_type(tgt, v)
                stmts.append(Assign(tgt, v))
                self.now_in(st, v, tgt)
            return
        if k == "abs":
            g = self.global_var(a[1], size)
            self.preserve(st, g, None, stmts, value=v)
            stmts.append(Assign(g, v))
            self.now_in(st, v, g)
            return
        if k == "fs":
            return
        _, b, disp, i, sc = a
        b = self.with_param(b)
        if isinstance(b, SP) and i is None:
            self._store(st, ("stack", b.off + disp), v, size, stmts, fp)
            return
        arr = self.module_struct_elem(b, i, disp, size)
        if arr is None and i is not None and isinstance(b, SP):
            arr = self.frame_record_elem(b, i, disp, size)
        if arr is None and i is not None and isinstance(b, Var) and b.space == "arg" and \
                isinstance(b.off, int) and b.off >= 4:
            # [p + i * k + c] of a parameter: an array inside the UDT it points
            # at (an array parameter holds a SAFEARRAY*, never pvData; VBDoom
            # HUlib_addLineToSText s.l(i).needsupdate)
            arr = self.element_inline(b, i, disp, size)
        if arr is None and i is not None and self.scale == 1 and isinstance(i, Var) and \
                i.space == "arg" and isinstance(i.off, int) and i.off >= 4 and \
                not (isinstance(b, Var) and b.space == "arg"):
            # the same with base and index the other way round (a Byte array, scale 1:
            # r.tavut(i) = x printed (v14 - 1)(a1.pad1(1)) = x)
            arr = self.element_inline(i, b, disp, size)
        if arr is None and i is not None and isinstance(b, BinOp) and b.op == "+" and \
                isinstance(b.a, Var) and b.a.space == "arg" and isinstance(b.a.off, int) and \
                b.a.off >= 4 and isinstance(b.b, Const) and isinstance(b.b.value, int):
            # ... or the field's offset already added to the parameter
            arr = self.element_inline(b.a, i, disp + b.b.value, size)
        if arr is None and i is None and isinstance(b, BinOp) and b.op == "+" and \
                isinstance(b.a, Var) and b.a.space == "arg" and isinstance(b.a.off, int) and \
                b.a.off >= 4 and not isinstance(b.b, Const):
            # ... or the index added to it (mov ecx, [ebp+8]; add ecx, i; mov [ecx+4Ch], al)
            scale, self.scale = self.scale, 1
            try:
                arr = self.element_inline(b.a, b.b, disp, size)
            finally:
                self.scale = scale
        if arr is None and i is not None and isinstance(b, Me):
            arr = self.member_element(i, disp, size)
        if arr is None and i is not None and b is None:
            arr = self.data_element(i, disp, size)
        if arr is None:
            arr = self.added_element(b, i, disp, size)
        if arr is None:
            arr = self.array_at(b, i, disp, size)
        if arr is None and i is not None and self.scale == 1 and isinstance(i, Var) and \
                i.key() in self.arrays and not self.arrays[i.key()].get("dyn"):
            arr = self.array_at(i, b, disp, size)   # [offset + pvData]: base and index swapped
        if arr is not None:
            self.preserve_element(st, arr, stmts, v)
            self.note_type(arr, v)
            stmts.append(Assign(arr, v))
            if self.memory_read_of(v, arr) and not isinstance(v, Var):
                self.now_in(st, v, arr)     # (eax = a(i) + 1, stored: it reads the new value)
            return
        if isinstance(b, Me) and i is None and disp:
            mv = Var("member", disp)
            self.preserve(st, mv, None, stmts)
            stmts.append(Assign(mv, v))
            self.now_in(st, v, mv)
            return
        sv = self.static_var(b, disp) if i is None else None
        if sv is not None:
            self.preserve(st, sv, None, stmts)
            stmts.append(Assign(sv, v))
            self.now_in(st, v, sv)
            return
        el = self.element_inline(b.e, i, disp, size) if i is not None and \
            isinstance(b, AddrOf) and isinstance(b.e, Index) else None
        if el is not None:
            self.preserve(st, el, None, stmts)
            stmts.append(Assign(el, v))
            self.now_in(st, v, el)
            return
        if i is None and isinstance(b, AddrOf) and isinstance(b.e, Index):
            self.note_elem_size(b.e, size, disp)
            tgt = Deref(b.e, size, disp)        # an element (field) via __vbaDerefAry1
            self.preserve(st, tgt, None, stmts)
            stmts.append(Assign(tgt, v))
            self.now_in(st, v, tgt)
            return
        if i is None and isinstance(b, Const) and self.data_address(Const(b.value + disp)):
            tgt = self.global_var((b.value + disp) & 0xffffffff, size)   # *(&module var)
            self.preserve(st, tgt, None, stmts)
            stmts.append(Assign(tgt, v))
            self.now_in(st, v, tgt)
            return
        if i is not None:
            tgt = Deref(add(b if b else Const(0),
                            BinOp("*", i, Const(sc)) if sc != 1 else i), size, disp)
        else:
            tgt = Deref(b if b is not None else Const(0), size, disp)
        self.preserve_element(st, tgt, stmts, v)
        if i is None and b is not None and not isinstance(b, Const):
            # *param = v (a ByRef parameter): what registers hold that read *param
            # mean its old value; v itself is now in *param (gh_json json_Index)
            self.preserve(st, tgt, None, stmts, value=v)
            stmts.append(Assign(tgt, v))
            self.now_in(st, v, tgt)
            return
        stmts.append(Assign(tgt, v))

    def now_in(self, st, v, var):
        """v was just stored in var: registers and flags holding v refer to
        var from now on (the source reads the variable, not the expression)."""
        if isinstance(v, (Const, Str, Me, IatSlot, SP)) or not hasattr(v, "key"):
            return
        if isinstance(v, Var) and (self.arrays.get(v.key()) or {}).get("dyn") is False:
            # a fixed array's data pointer: the register is still that array's (By-reference
            # Variants of sInput(0..3): mov [ebp - 98h], eax; lea edx, [eax + 8] printed o1007.f8)
            return
        if isinstance(v, Var) and v.space == "arg" and v.off in self.byref and \
                isinstance(var, Var) and var.space == "local":
            # a ByRef parameter's pointer put in a Variant (VT_BYREF, for a Variant parameter):
            # the register still is the parameter.  __vbaStrMove(ecx = it) assigned that
            # Variant's payload (a target Proc_71FC70 a1 = Mid(a1, k): v3C = ..., a1 unchanged)
            return
        k = (type(v), v.key())
        for r, e in list(st.regs.items()):
            if e is v or (e is not None and (type(e), e.key()) == k):
                st.regs[r] = var
        if st.flags is not None:
            st.flags = tuple(var if (x is v or (hasattr(x, "key") and (type(x), x.key()) == k))
                             else x for x in st.flags)

    def forget_known(self, var):
        """var is being overwritten: a frame slot known to hold a value that
        reads var holds var's old value, which only the slot's own variable
        still has (VBDoom R_CheckPlane pl = lastvisplane: lastvisplane =
        lastvisplane + 1: visplanes(pl) read the new lastvisplane)."""
        if not self.known or not hasattr(var, "key"):
            return
        k = var.key()
        for off, e in list(self.known.items()):
            if hasattr(e, "walk") and any(isinstance(x, (Var, Deref)) and x.key() == k
                                          for x in e.walk()):
                del self.known[off]

    def preserve(self, st, var, old, stmts, forget=True, value=None):
        """var is about to be overwritten: values in registers or pushed
        arguments that still refer to it must keep its current value (and
        frame slots known to hold one no longer do, unless forget is False:
        the caller substitutes the old value there itself).  value: what is
        being stored, which flags testing it go on to read in var (now_in)."""
        if forget:
            self.forget_known(var)
        k = var.key()

        def refers(e):
            return any(isinstance(x, Var) and x.key() == k for x in e.walk())
        same = (type(value), value.key()) if hasattr(value, "key") else None

        def stale(x):
            return isinstance(x, ir.Expr) and refers(x) and (type(x), x.key()) != same
        # (a register holding the value being stored reads the variable from now on: now_in.  So
        # add eax, 1; mov [v18], eax leaves eax = v18, not a copy of the old v18 and one added to it)
        holders = [("r", r) for r, e in st.regs.items() if e is not None and stale(e)]
        holders += [("s", o) for o, e in st.stack.items() if e is not None and refers(e)]
        holders += [("f", i) for i, e in enumerate(st.fpu) if e is not None and refers(e)]
        # (a compare waiting for its branch: fcomp [esp+1Ch]; mov [esp+1Ch], 1; fnstsw ax; test ah, 41h
        # makes a flag in the slot it has just compared: DarwinBots simplecoll tested 1, not Abs(x))
        for name in ("flags", "fsw"):
            f = getattr(st, name)
            if f is not None and any(stale(x) for x in f[1:]):
                holders.append((name, None))
        if not holders:
            return
        if old is not None and self.impure(old):
            old = None              # a call's result: read it once, from a copy
        if old is not None and refers(old):
            old = None              # (the slot's "known" value is itself: copy it)
        if old is None:
            self.ntmp += 1
            old = Var("tmp", self.ntmp)
            a = Assign(old, var)
            a.va = self.cur_va
            stmts.append(a)
        for kind, where in holders:
            if kind == "r":
                st.regs[where] = ir.subst(st.regs[where], {k: old})
            elif kind == "f":
                st.fpu[where] = ir.subst(st.fpu[where], {k: old})
            elif kind in ("flags", "fsw"):
                f = getattr(st, kind)
                setattr(st, kind, tuple(ir.subst(x, {k: old}) if stale(x) else x for x in f))
            else:
                st.stack[where] = ir.subst(st.stack[where], {k: old})

    @staticmethod
    def memory_root(e):
        """The variable an element's or a field's memory belongs to, or None."""
        while True:
            if isinstance(e, Index):
                e = e.arr
            elif isinstance(e, (Deref, AddrOf)):
                e = e.e
            elif isinstance(e, BinOp) and e.op in ("+", "-"):
                e = e.a
            else:
                return e.key() if isinstance(e, Var) else None

    def memory_read_of(self, e, tgt):
        """Does e read memory an element or field tgt may overlap?"""
        root = self.memory_root(tgt)
        if root is None:
            return False

        def apart(x):
            """x and tgt: the same variable, but provably other bytes."""
            if isinstance(x, Deref) and isinstance(tgt, Deref) and x.e.key() == tgt.e.key():
                return x.off + x.size <= tgt.off or tgt.off + tgt.size <= x.off
            a, b = (x.e if isinstance(x, Deref) else x), (tgt.e if isinstance(tgt, Deref) else tgt)
            if isinstance(a, Index) and isinstance(b, Index) and len(a.idx) == len(b.idx) and \
                    all(isinstance(i, Const) for i in a.idx + b.idx):
                return [i.value for i in a.idx] != [i.value for i in b.idx]
            return False
        stack = [e]
        while stack:
            x = stack.pop()
            if isinstance(x, AddrOf):
                # an address reads no element, only its subscripts
                y = x.e.e if isinstance(x.e, Deref) else x.e
                if isinstance(y, Index):
                    stack.extend(y.idx)
                continue
            if self.descriptor_read(x):
                continue
            if isinstance(x, (Index, Deref)) and self.memory_root(x) == root and not apart(x):
                return True
            stack.extend(x.children())
        return False

    def descriptor_read(self, x):
        """A dynamic array's own bound or data pointer: a store to an element changes no such thing,
        and an address worked out from them stays what it is (t_esi = pvData + (i - lb) * 72, With
        shots(i): kept in a temporary the With was no longer seen, DarwinBots NewShotCollision)."""
        if not isinstance(x, Deref) or x.size != 4:
            return False
        b = x.e
        if isinstance(b, Deref) and b.off == 0 and isinstance(b.e, Var) and b.e.space == "arg":
            b = b.e
        if not isinstance(b, Var):
            return False
        k = b.key()
        # (or a temporary __vbaAryLock has filled: With shots(i) without bounds checks)
        return bool((self.arrays.get(k) or {}).get("dyn")) or k in self.sa_alias or \
            k in getattr(self, "sa_locks", ())

    def keep_value(self, st, stale, stmts):
        """Registers (and what waits in pushed arguments, the FPU, a compare) for which stale(e) is
        true go on holding what e is now: a temporary, as the machine's register does."""
        made = {}

        def saved(e):
            if isinstance(e, Hi):
                return Hi(saved(e.of))      # (a temporary can't hold half a value)
            k = (type(e), e.key())
            if k not in made:
                self.ntmp += 1
                t = Var("tmp", self.ntmp)
                a = Assign(t, e)
                a.va = self.cur_va
                self.note_type(t, e)
                stmts.append(a)
                made[k] = t
            return made[k]
        for r, e in list(st.regs.items()):
            if isinstance(e, ir.Expr) and stale(e):
                st.regs[r] = saved(e)
        for o, e in list(st.stack.items()):
            if isinstance(e, ir.Expr) and stale(e):
                st.stack[o] = saved(e)
        for i, e in enumerate(st.fpu):
            if isinstance(e, ir.Expr) and stale(e):
                st.fpu[i] = saved(e)
        for name in ("flags", "fsw"):
            f = getattr(st, name)
            if f is not None and any(isinstance(x, ir.Expr) and stale(x) for x in f[1:]):
                setattr(st, name, tuple(saved(x) if isinstance(x, ir.Expr) and stale(x) and j else x
                                        for j, x in enumerate(f)))

    def preserve_element(self, st, tgt, stmts, value=None):
        """An element or field is about to be overwritten: what registers hold of it is its old value.
        The lift keeps a register's value as an expression: mov ebx, [ecx + eax*4]; mov [ecx + eax*4],
        0Dh read the new one at ebx's first use (ChessBrainVB MakeMove: PieceFrom = Board(From):
        Board(From) = NO_PIECE, and no move ever reached its target square)."""
        same = (type(value), value.key()) if hasattr(value, "key") else None
        # (Hi of it: the upper half, stored next.  x = x * 10000@ printed t1 = Hi(...), gh_zip)
        self.keep_value(st, lambda e: (type(e), e.key()) != same and
                        not (isinstance(e, Hi) and same is not None and (type(e.of), e.of.key()) == same) and
                        self.memory_read_of(e, tgt), stmts)

    def save_over_call(self, st, stmts):
        """A procedure may change what a register that survives the call read from memory: the
        register keeps the value it read (fast code: mov ebx, [Board + x*4]; call MakeMove; ... ebx)."""
        def changeable(e):
            stack = [e]
            while stack:
                x = stack.pop()
                if isinstance(x, AddrOf):
                    y = x.e.e if isinstance(x.e, Deref) else x.e
                    if isinstance(y, Index):
                        stack.extend(y.idx)
                    continue
                if isinstance(x, Deref) and x.off == 0 and (isinstance(x.e, Me) or
                                                            getattr(x.e, "iface", None) is not None or
                                                            hasattr(x.e, "key") and self.objtype.get(x.e.key())):
                    continue                # (an object's vtable: mov edi, [esi]; call [edi + 6F8h])
                if self.descriptor_read(x):
                    continue
                if isinstance(x, (Index, Deref)):
                    return True
                if isinstance(x, Var) and x.space in ("global", "member") and \
                        x.key() not in self.arrays and not self.objtype.get(x.key()):
                    return True
                stack.extend(x.children())
            return False
        made = {}
        for r in ("ebx", "esi", "edi", "ebp"):
            e = st.regs.get(r)
            if not isinstance(e, ir.Expr) or isinstance(e, (Const, Str, Me, IatSlot, SP)) or \
                    self.impure(e) or not changeable(e):
                continue
            k = (type(e), e.key())
            if k not in made:
                self.ntmp += 1
                t = Var("tmp", self.ntmp)
                a = Assign(t, e)
                a.va = self.cur_va
                self.note_type(t, e)
                stmts.append(a)
                made[k] = t
            st.regs[r] = made[k]

    def note_fixed(self, e, n):
        """A String * n variable or field; frame slots are kept by their
        offset, so the next lift (struct locals as fields) still knows."""
        if not n:
            return
        ix = e.e if isinstance(e, Deref) and e.off == 0 else e
        if isinstance(ix, ir.Index) and isinstance(ix.arr, Var):
            # a target Public global_F046C8(20) As String * 30, declared As Long:
            # the default password "Name1" stored into it was a Type mismatch
            key = ix.arr.key()
            if key in self.arrays:
                self.arrays[key]["fixed"] = n
            if key[0] == "global":
                self.m.__dict__.setdefault("fixed_elems", {})[key] = n
            return
        if isinstance(e, Var):
            self.fixed_strings[e.key()] = n
            if e.space == "local" and isinstance(e.off, int):
                self.m.fixed_slots[(self.p.va, e.off)] = n
        elif isinstance(e, Deref) and isinstance(e.e, Var) and e.e.space == "local" and \
                isinstance(e.e.off, int):
            self.m.fixed_slots[(self.p.va, e.e.off + e.off)] = n

    def note_type(self, tgt, v):
        t = getattr(v, "iface", None)
        if t is None and isinstance(v, Var) and v.space != "reg":
            # a copy of a variable whose object type is known (a form's
            # predeclared instance, an earlier Set).  Not of a register's
            # variable: one spill there typed the register for the whole
            # procedure, and every later copy of it (a target Proc_76E5E0:
            # eax once the Print1 form, then 0 into a ByRef temporary that
            # was declared As Print1)
            t = self.objtype.get(v.key())
            if t is None and v.space == "global":
                t = self.m.global_types.get(v.off)
        if t is not None and hasattr(tgt, "key"):
            old = self.objtype.get(tgt.key())
            if old is not None and old is not t and getattr(old, "name", None) != getattr(t, "name", None):
                # a frame slot reused for controls of two types: only As Object
                # holds both (a target Walk v64 As TextBox, then v64.Y1)
                self.__dict__.setdefault("mixed_objects", set()).add(tgt.key())
            self.objtype[tgt.key()] = t
            if isinstance(tgt, Var) and tgt.space == "global" and isinstance(tgt.off, int) and \
                    isinstance(getattr(v, "new_class", None) or getattr(v, "name", None), str) \
                    and isinstance(v, Call) and v.name == "New":
                # Set g = New Collection: what every procedure's next lift
                # calls through g (VBDoom texindex(UCase$(name)) was vtbl_1C)
                self.m.global_types.setdefault(tgt.off, t)

    def array_var(self, e):
        """The array variable behind a data pointer, or None.

        A fixed array's slot holds pvData; a dynamic array's slot holds the
        SAFEARRAY, whose pvData is at +0x0C.
        """
        if isinstance(e, Deref) and e.off == 0x0c and isinstance(e.e, Var):
            return e.e, True                        # SAFEARRAY.pvData
        if isinstance(e, Var) and e.space in ("global", "local", "arg", "member"):
            return e, False                         # a fixed array's slot holds pvData
        return None

    def checked_two_dims(self, idx):
        """The bounds of a fixed array from x + y * n when each term passed its own check,
        x below n and y below m: 0 To n - 1, 0 To m - 1.  With one dimension y * n ran past
        it (a target Form19 m7C(v2C + v24 * 199) of m7C(198): a subscript error for each
        pixel of the child's area, which is how a click finds the person)."""
        if not (isinstance(idx, BinOp) and idx.op == "+"):
            return None
        for x, y in ((idx.a, idx.b), (idx.b, idx.a)):
            if isinstance(y, BinOp) and y.op == "*" and isinstance(y.b, Const) and \
                    hasattr(x, "key") and hasattr(y.a, "key"):
                n, m = self.index_bounds.get(x.key()), self.index_bounds.get(y.a.key())
                if n and m and n == y.b.value:
                    return [(0, n), (0, m)]
        # (the multiplication in steps, optimised: x + y + y * 1000 for x + y * 1001)
        lin = self.linear(idx)
        terms = [(k, e, c) for k, (e, c) in lin.items() if k is not None and c]
        if len(terms) == 2 and not lin.get(None, [None, 0])[1]:
            for (kx, _, cx), (ky, _, cy) in (terms, terms[::-1]):
                n, m = self.index_bounds.get(kx), self.index_bounds.get(ky)
                if cx == 1 and n and m and n == cy:
                    return [(0, n), (0, m)]
        return None

    def note_index_bound(self, a, n):
        """A bounds check let a (an index, minus the lower bound) through below
        n: remember it for the variable(s) the index is made of."""
        self.index_bounds[a.key()] = n
        self.bounds_log.setdefault(a.key(), []).append((self.cur_va, n))
        lin = self.linear(a)
        atoms = [k for k, (e, c) in lin.items() if k is not None and c]
        if len(atoms) == 1 and lin[atoms[0]][1] == 1:
            self.index_bounds[atoms[0]] = n
            self.bounds_log.setdefault(atoms[0], []).append((self.cur_va, n))

    def element_count(self, rest, K):
        """Elements an array must have for the index terms rest ((atom, byte
        coefficient) pairs) given the bounds checks on them: the largest
        stride times its bound (net(i, j) of a 4 x 12 array read as one
        index i + j * 4 needs 48), or None."""
        n = None
        for e, k in rest:
            b = self.index_bounds.get(e.key()) if hasattr(e, "key") else None
            if b and K and k % K == 0 and k > 0:
                n = max(n or 0, k // K * b)
        return n

    def added_element(self, b, i, disp, size):
        """An element (field) of a fixed array from its address, split linearly
        into the data pointer, index terms and a constant.  The index terms'
        common factor is the element size when it is wider than the access
        and no scalar's (players(i).mo: pvData + i * 108h); a size from the
        array's descriptor template wins (a(i * 3) of Longs looks the same).
        For scalar elements only the unoptimised form is claimed, the index
        added to pvData first ("mov ecx, [pvData]; add ecx, i; mov [ecx], al",
        VBDoom cheat_xlate_table(i)); [pvData + i] stays array_at's.  A
        parameter holds a SAFEARRAY*, not pvData."""
        import math
        added = i is None
        if i is not None:
            if b is None:
                return None
            b = add(b, BinOp("*", i, Const(self.scale)) if self.scale != 1 else i)
        if not isinstance(b, BinOp) or b.op not in ("+", "-"):
            return None
        lin = self.linear(b)
        for key, (base, c) in list(lin.items()):
            if key is None or c != 1:
                continue
            av = self.array_var(base)
            if av is None or av[0].space == "arg" or av[1]:
                continue                    # (dynamic arrays: array_at)
            var = av[0]
            rest = [(e, k) for kk, (e, k) in lin.items() if kk not in (key, None) and k]
            if not rest:
                continue                    # pvData + constant: no index
            const = lin.get(None, [None, 0])[1] + disp
            index = None
            for e, k in rest:
                part = e if k == 1 else BinOp("*", e, Const(k))
                index = part if index is None else add(index, part)
            info = self.arrays.get(var.key())
            K = 0
            for e, k in rest:
                K = math.gcd(K, abs(k))
            known = (info or {}).get("size") or 0
            inner = K in (2, 4, 8) and size < K and const % K != 0 and not (info or {}).get(
                "fixed_bounds") and known in (0, K)
            if (K > size and K not in (2, 4, 8) or inner) and known < K and \
                    not (info or {}).get("fixed_bounds"):
                # an array of K-byte UDTs; its bounds come from the descriptor
                # templates once the code shows it (global_array_bounds)
                info = self.arrays.setdefault(var.key(), {"size": None, "dyn": False,
                                                          "lb": 0, "n": None, "var": var})
                info["size"] = known = K
            if inner and info is not None:
                info["record"] = True       # a field inside an 8-byte element (markpoints(i).y)
            if info is not None and not info.get("dims") and not info.get("fixed_bounds"):
                n = self.element_count(rest, known or K or size)
                if n is not None and (info.get("n") or 0) < n:
                    info["n"] = n           # the bounds checks on the index terms
                elif info.get("n") is None and getattr(self, "cur_bound", None) is not None:
                    info["n"] = self.cur_bound  # the bounds check before the access
            if known > size and (known not in (2, 4, 8) or (info or {}).get("record")):
                rec = dict(info)
                if not rec.get("dims"):
                    rec["dims"] = [(rec.get("lb") or 0, rec.get("n"))]
                save, self.scale = self.scale, 1
                try:
                    el = self.record_element(var, rec, index, const, size)
                finally:
                    self.scale = save
                if el is not None:
                    return el
                continue
            if added:
                save, self.scale = self.scale, 1
                try:
                    el = self.array_at(base, index, const, size)
                finally:
                    self.scale = save
                if el is not None:
                    return el
        return None

    def element_address(self, ptr, whole=True):
        """&element (or &field of one) for an address pvData + index * K + c of
        a fixed array: what a ByRef argument (or LSet operand) is when it is
        such a sum (VBDoom SetML player_arrow(0): pvData + 0 * 16).  K is the
        array's known element size, else the stride the address multiplies
        by; the slot is then registered as an array of K-byte elements."""
        ms = self.module_struct_ref(ptr)
        if ms is not None:
            return ms                       # (a module-level UDT or a part of one)
        if not isinstance(ptr, BinOp) or ptr.op not in ("+", "-"):
            return None
        lin = self.linear(ptr)
        c = lin.get(None, [None, 0])[1]
        if c and self.data_address(Const(c)):
            # (i * k + address in .data: Input #f, K.kulut(3 - i))
            part = None
            for key, (a, k) in lin.items():
                if key is not None and k:
                    t = a if k == 1 else BinOp("*", a, Const(k))
                    part = t if part is None else add(part, t)
            if part is not None:
                save, self.scale = self.scale, 1
                try:
                    el = self.data_element(part, c, 1)
                finally:
                    self.scale = save
                if el is not None:
                    return AddrOf(el)
        strides = [y.value for x in ptr.walk() if isinstance(x, BinOp) and x.op == "*"
                   for y in (x.a, x.b) if isinstance(y, Const) and isinstance(y.value, int)
                   and y.value > 1]
        lin = self.linear(ptr)
        for key, (base, c) in lin.items():
            if key is None or c != 1:
                continue
            av = self.array_var(base)
            if av is None or av[1] or av[0].space == "arg":
                continue
            var = av[0]
            info = self.arrays.get(var.key())
            K = (info or {}).get("size") or 0
            multi = len((info or {}).get("dims") or ()) > 1
            if not (info or {}).get("fixed_bounds") and not multi and \
                    max(strides, default=0) > K:
                # (a scalar guess from another access.  Not with two dimensions:
                # the stride is the first one's count, a target Proc_8D76B0
                # v58(0 To 367, 0 To 16) As String became a T_170 array)
                K = max(strides)
            if not K:
                continue
            const = lin.get(None, [None, 0])[1]
            index = None
            for kk, (e, k) in lin.items():
                if kk in (key, None) or not k:
                    continue
                part = e if k == 1 else BinOp("*", e, Const(k))
                index = part if index is None else add(index, part)
            rec = {"size": K, "dims": (info or {}).get("dims") or
                   [((info or {}).get("lb") or 0, (info or {}).get("n"))]}
            save, self.scale = self.scale, 1
            try:
                el = self.record_element(var, rec, index, const, 4)
            finally:
                self.scale = save
            if el is None:
                continue
            if whole and isinstance(el, Deref) and el.off == 0:
                el = el.e                   # the element itself (not its first field)
            if info is None:
                info = self.arrays[var.key()] = {"size": K, "dyn": False, "lb": 0, "n": None,
                                                 "var": var}
            else:
                info["size"] = K
            if not info.get("dims") and not info.get("fixed_bounds"):
                rest = [(e, k) for kk, (e, k) in lin.items() if kk not in (key, None) and k]
                n = self.element_count(rest, K)
                if n is not None and (info.get("n") or 0) < n:
                    info["n"] = n
                elif info.get("n") is None and getattr(self, "cur_bound", None) is not None:
                    info["n"] = self.cur_bound
            return AddrOf(el)
        return None

    def array_at(self, base, index, disp, size):
        """Element access through an array data pointer -> Index(...)."""
        if base is None:
            return None
        if index is None and disp == 0:
            # [pvData]: the first element of a known fixed array (LNG_POW2(0) = 1);
            # anything else at offset 0 is not known to be an array
            av = self.array_var(base)
            known = self.arrays.get(av[0].key()) if av is not None else None
            if av is None or known is None or known.get("dyn") or av[1]:
                return None
        av = self.array_var(base)
        if av is None:
            return None
        var, dyn = av
        known = self.arrays.get(var.key())
        if known is not None and known.get("dims") and not dyn and \
                (known.get("size") or 0) > size:
            return self.record_element(var, known, index, disp, size)
        if known is not None and not dyn and (known.get("size") or 0) > size and \
                (known.get("fixed_bounds") or index is None and known.get("record") and
                 var.space == "member" and disp not in (0xc, 0x10, 0x14, 0x18, 0x1c)) and \
                isinstance(known.get("lb") or 0, int):
            # one dimension of records: element and field too (a target
            # Form1(5).f4 came out global_F04AC4(911), the offset / 4)
            el = self.record_element(var, dict(known, dims=[(known.get("lb") or 0,
                                                             known.get("n"))]),
                                     index, disp, size)
            if el is not None:
                return el
        if not dyn:
            if known is not None and known.get("dyn"):
                return None             # a SAFEARRAY field, not an element
            if index is None and known is None:
                return None             # a plain global, not a known array
        idx = index
        esize = size
        if index is not None and self.scale in (2, 4, 8) and self.scale > size and \
                not (known is not None and len(known.get("dims") or ()) > 1 and
                     (known.get("size") or 0) == size):
            # [p + i*4] read as a byte: a Long element whose low byte the
            # compiler tests ("Data(i) And LNG_POW2(k)" <> 0); the stride is the size
            # (not with two dimensions of that size: [p + i*8 + 4] of a(1, 119) is a(1, i): ChessBrainVB)
            esize = self.scale
        if idx is None:
            idx = Const(0)
        elif self.scale != 1:
            idx = BinOp("*", idx, Const(self.scale))
        if disp:
            idx = add(idx, Const(disp))
        if dyn:
            # (i - lb) scaled in steps to the element size K of an array of
            # UDTs, plus a field offset: m_uFiles(i).FileName
            lin = self.linear(idx)
            lbk = Deref(var, 4, 0x14).key()
            K = -lin[lbk][1] if lbk in lin else 0
            if K > esize and all(c % K == 0 for key, (e, c) in lin.items()
                                 if key not in (lbk, None)):
                const = lin.get(None, [None, 0])[1]
                k0, field = divmod(const, K)
                sub_ = None
                for key, (e, c) in lin.items():
                    if key in (lbk, None):
                        continue
                    part = e if c == K else BinOp("*", e, Const(c // K))
                    sub_ = part if sub_ is None else add(sub_, part)
                if k0:
                    sub_ = add(sub_, Const(k0)) if sub_ is not None else Const(k0)
                info = self.arrays.setdefault(var.key(), {"size": K, "dyn": True, "lb": "dyn",
                                                          "n": None, "var": var})
                info["dyn"], info["size"] = True, K
                el = Index(var, [sub_ if sub_ is not None else Const(0)])
                el.vbtype = None
                el.stride = K
                f = Deref(el, size, field)          # a field of the element
                return f
        idx = self.unscale(idx, esize)
        if idx is None:
            return None
        info = self.arrays.setdefault(var.key(), {"size": esize, "dyn": dyn, "lb": None,
                                                  "n": None, "var": var})
        info["dyn"] = info["dyn"] or dyn
        if info["n"] is None and getattr(self, "cur_bound", None) is not None and not dyn:
            info["n"] = self.cur_bound
        if not dyn and not info.get("dims"):
            two = self.checked_two_dims(idx)
            if two is not None:
                info["dims"] = two
        dims = info.get("dims")
        if dims and len(dims) > 1:
            subs = self.split_index(idx, dims)
            if subs is not None:
                e = Index(var, subs)
                e.vbtype = None
                return e
        idx = self.unbias(idx, var, info)
        e = Index(var, [idx])
        e.vbtype = None
        e.stride = esize            # (what it was lifted with; a later element size may differ)
        if dyn:
            e.unit = esize          # bytes per subscript step (simplify.refold_dyn_index)
        return e

    def fixed_array(self, sa_off, tpl, vt):
        """A local array VB built from a SAFEARRAY template (cDims, fFeatures,
        cbElements, cLocks, pvData, then bounds last dimension first).  Code
        indexes it through pvData (the slot at sa + 0x0C): that slot is the
        array; the descriptor's own slot stands for it in UBound and Erase."""
        im = self.im
        if not im.valid(tpl):
            return
        nd, size = im.u16(tpl), im.u32(tpl + 4)
        if not 1 <= nd <= 60 or not 0 < size < 0x10000:
            return
        bounds = [(im.u32(tpl + 20 + 8 * k), im.u32(tpl + 16 + 8 * k)) for k in range(nd)]
        dims = [(lb if lb < 0x80000000 else lb - (1 << 32), n) for lb, n in reversed(bounds)]
        key = ("local", sa_off + 0x0c)
        var = Var("local", sa_off + 0x0c)
        cls = None
        if isinstance(vt, int) and vt > 0xffff and im.valid(vt):
            # (not a VARTYPE: the IID of an array's class, Dim ka(1) As cK)
            from .typelib import Registry
            g = im.guid(vt)
            it = self.m.iface_by_iid.get(g) or Registry.find_guid(g)
            n = getattr(it, "name", None)
            cls = (n[1:] if n.startswith("_") else n) if n else "Object"
            vt = 9
        self.arrays[key] = {"size": size, "dyn": False, "lb": dims[0][0], "n": dims[0][1],
                            "dims": dims, "vt": vt, "var": var, "sa": sa_off}
        if cls:
            self.arrays[key]["class"] = cls
        self.sa_alias[("local", sa_off)] = var

    @staticmethod
    def linear(e, coeff=1, out=None):
        """e as {atom key: [atom, coefficient]} plus a constant (key None):
        sums, differences, negation and multiplication by constants expanded
        ((i + i * 4) * 4 is 20 * i)."""
        if out is None:
            out = {}
        if isinstance(e, Const) and isinstance(e.value, int):
            out.setdefault(None, [None, 0])[1] += coeff * e.value
        elif isinstance(e, BinOp) and e.op in ("+", "-"):
            Lifter.linear(e.a, coeff, out)
            Lifter.linear(e.b, coeff if e.op == "+" else -coeff, out)
        elif isinstance(e, UnOp) and e.op == "-":
            Lifter.linear(e.a, -coeff, out)
        elif isinstance(e, BinOp) and e.op == "*" and isinstance(e.b, Const) and \
                isinstance(e.b.value, int):
            Lifter.linear(e.a, coeff * e.b.value, out)
        elif isinstance(e, BinOp) and e.op == "*" and isinstance(e.a, Const) and \
                isinstance(e.a.value, int):
            Lifter.linear(e.b, coeff * e.a.value, out)
        else:
            out.setdefault(e.key(), [e, 0])[1] += coeff
        return out

    def dyn_element_linear(self, ptr):
        """dyn_element for addresses the compiler scaled in steps
        (lea x, [x + x*4]; shl x, 2: element size 20): pvData + k * (i - lb)
        with k the element size, as a linear combination."""
        lin = self.linear(ptr)
        bases = [(e, c) for k, (e, c) in lin.items() if k is not None and c == 1 and
                 isinstance(e, Deref) and e.off == 0x0c and e.size == 4]
        for base, _ in bases:
            sa = base.e
            if isinstance(sa, Var):
                arr = sa
            elif isinstance(sa, Deref) and sa.off == 0 and isinstance(sa.e, Var) and \
                    sa.e.space == "arg":
                arr = sa.e
            elif isinstance(sa, Deref) and sa.size == 4 and (isinstance(sa.e, Index) or (
                    isinstance(sa.e, Var) and sa.e.key() in self.udt)):
                arr = sa                    # an array field of a UDT, as dyn_element_simple
            else:
                continue
            lbk = Deref(sa, 4, 0x14).key()
            if lbk not in lin or lin[lbk][1] >= 0:
                continue
            k = -lin[lbk][1]                # the element size
            idx = None
            for key, (e, c) in lin.items():
                if key in (lbk, base.key()) or key is None:
                    continue
                if c % k:
                    return None
                part = e if c == k else BinOp("*", e, Const(c // k))
                idx = part if idx is None else add(idx, part)
            const = lin.get(None, [None, 0])[1]
            if const % k:
                return None
            if const:
                idx = add(idx, Const(const // k)) if idx is not None else Const(const // k)
            info = self.arrays.setdefault(arr.key(), {"size": k, "dyn": True, "lb": "dyn",
                                                      "n": None, "var": arr})
            info["dyn"] = True
            if info.get("size") is None:
                info["size"] = k
            e = Index(arr, [idx if idx is not None else Const(0)])
            e.vbtype = None
            return e
        return None

    def dyn_field(self, ptr):
        """A field address inside a dynamic array's element: pvData + k * (i -
        lb) + r with 0 < r < k (a target passes a(2).f8 ByRef as
        G.fC + (2 - G.f14) * 216 + 8).  (element, r) or None."""
        lin = self.linear(ptr)
        const = lin.get(None, [None, 0])[1]
        if not const:
            return None
        bases = [e for k, (e, c) in lin.items() if k is not None and c == 1 and
                 isinstance(e, Deref) and e.off == 0x0c and e.size == 4]
        for base in bases:
            sa = base.e
            lbk = Deref(sa, 4, 0x14).key()
            if lbk not in lin or lin[lbk][1] >= 0:
                continue
            k = -lin[lbk][1]
            q, r = divmod(const, k)
            if not r:
                return None
            el = self.dyn_element_linear(BinOp("-", ptr, Const(r)))
            if el is not None:
                return el, r
        return None

    def dyn_element(self, ptr):
        """The element a dynamic array element address points at: VB computes
        pvData + (index - lLbound) * size by hand for __vbaVarCopy/__vbaStrCopy
        targets.  Index(array, [index]) or None."""
        r = self.dyn_element_simple(ptr)
        return r if r is not None else self.dyn_element_linear(ptr)

    def dyn_element_simple(self, ptr):
        terms = []

        def walk(e, sign):
            if isinstance(e, BinOp) and e.op == "+":
                walk(e.a, sign)
                walk(e.b, sign)
            elif isinstance(e, BinOp) and e.op == "-":
                walk(e.a, sign)
                walk(e.b, -sign)
            else:
                terms.append((sign, e))
        walk(ptr, 1)

        def sa_of(e):
            # the SAFEARRAY: a local's slot, or *param for an array parameter
            if isinstance(e, Var):
                return e
            if isinstance(e, Deref) and e.off == 0 and isinstance(e.e, Var) and e.e.space == "arg":
                return e.e
            if isinstance(e, Deref) and e.size == 4 and (isinstance(e.e, ir.Index) or (
                    isinstance(e.e, Var) and e.e.key() in self.udt)):
                return e                    # an array field of a UDT: a(i).Data(j)
            return None
        base = [t for s, t in terms if s > 0 and isinstance(t, Deref) and t.off == 0x0c
                and t.size == 4 and sa_of(t.e) is not None]
        if len(base) != 1:
            return None
        arr = sa_of(base[0].e)
        lb = Deref(base[0].e, 4, 0x14)
        size, rest, saw_lb = None, [], False
        for sgn, t in terms:
            if t is base[0]:
                continue
            # (index - lLbound) * size, as -lLbound*size, (k - lLbound)*size, ...
            if isinstance(t, BinOp) and t.op == "*" and isinstance(t.b, Const):
                inner, k = t.a, t.b.value
                if isinstance(inner, UnOp) and inner.op == "-":
                    inner, sgn = inner.a, -sgn  # (-lLbound) * 16
                if inner == lb:
                    size, saw_lb = k, True
                    if sgn > 0:
                        return None
                    continue
                if isinstance(inner, BinOp) and inner.op == "-" and inner.b == lb:
                    size, saw_lb = k, True
                    rest.append((sgn, inner.a, k))
                    continue
                rest.append((sgn, inner, k))
                continue
            if isinstance(t, Const) and isinstance(t.value, int):
                rest.append((sgn, None, t.value))
                continue
            # a Byte array: no scaling at all (pvData + (i - lLbound))
            if isinstance(t, UnOp) and t.op == "-":
                sgn, t = -sgn, t.a
            if t == lb and sgn < 0:
                saw_lb, size = True, size or 1
                bare = True
                continue
            if isinstance(t, BinOp) and t.op == "-" and t.b == lb:
                saw_lb, size = True, size or 1
                rest.append((sgn, t.a, 1))
                continue
            if isinstance(t, (Var, Deref, BinOp, Call)):
                rest.append((sgn, t, 1))
                continue
            return None
        if not saw_lb or not size:
            return None
        if size != 1 and any(k == 1 and e is not None for _, e, k in rest):
            return None                     # an unscaled term in a wider array
        idx = None
        for sgn, e, k in rest:
            if e is None:
                if k % size:
                    return None
                part = Const(sgn * k // size)
            else:
                if k % size:
                    return None
                part = e if k == size else BinOp("*", e, Const(k // size))
                if sgn < 0:
                    part = UnOp("-", part)
            idx = part if idx is None else add(idx, part)
        info = self.arrays.setdefault(arr.key(), {"size": size, "dyn": True, "lb": "dyn",
                                                  "n": None, "var": arr})
        info["dyn"] = True
        if info.get("size") is None:
            info["size"] = size             # known from UBound(a) before: now the width
        e = Index(arr, [idx if idx is not None else Const(0)])
        e.vbtype = None
        return e

    def element_ptr(self, ptr):
        """An element address of a fixed array (pvData + offset): the element,
        or for an array of UDTs that element's field."""
        base, off, rest = ptr, 0, None
        if isinstance(ptr, BinOp) and ptr.op == "+" and isinstance(ptr.b, Const) and \
                isinstance(ptr.b.value, int):
            base, off = ptr.a, ptr.b.value
        elif isinstance(ptr, BinOp) and ptr.op == "+":
            base, rest = (ptr.a, ptr.b) if isinstance(ptr.a, Var) else (ptr.b, ptr.a)
        if not isinstance(base, Var):
            return None
        info = self.arrays.get(base.key())
        if info is None or info.get("dyn"):
            return None
        esize = info.get("size") or 0
        if info.get("dims") and esize > 4 and rest is None:
            return self.record_element(base, info, None, off, 4)
        if esize > 8 and rest is None and info.get("fixed_bounds") and \
                isinstance(info.get("lb") or 0, int):
            # one dimension of records, as array_at takes them (a target
            # LSet List4(0).name1 = "" through pvData itself printed
            # global_F04688.f0 = &H4EC3DC)
            return self.record_element(base, dict(info, dims=[(info.get("lb") or 0,
                                                              info.get("n"))]), None, off, 4)
        if esize not in (1, 2, 4, 8):
            return None
        idx = self.unscale(rest if rest is not None else Const(off), esize)
        if idx is None:
            return None
        if len(info.get("dims") or ()) > 1:
            # (a target Proc_8D76B0 v58(v44 + (v24 - 1) * 368) = ..., whose read
            # came out v58(v44, v24): "Wrong number of dimensions")
            subs = self.split_index(idx, info["dims"])
            if subs is not None:
                e = Index(base, subs)
                e.vbtype = None
                return e
        e = Index(base, [self.unbias(idx, base, info)])
        e.vbtype = None
        return e

    def record_element(self, var, info, index, disp, size):
        """A field of an element of a fixed array of UDTs: the byte offset
        splits into the element (relative to the lower bounds) and the field.
        Index terms that are no multiple of the element size index a fixed
        array inside it (players(i).powers(k): pvData + i * 108h + k * 4
        + 3Ch); see inline_field."""
        esize = info["size"]
        k0, field = divmod(disp, esize)
        elem = Const(k0)
        inner = None
        if index is not None:
            part = BinOp("*", index, Const(self.scale)) if self.scale != 1 else index
            whole = self.unscale(part, esize)
            if whole is None:
                split = self.split_inner(part, esize, size)
                if split is None:
                    return None
                whole, inner = split
            if inner is not None and field + inner[1] >= esize:
                # both subscripts start at 1: (a - 1) * 104 + (h - 1) * 8 + 4 is
                # g(a).x(h), not g(a - 1).f0(h * 2 + 25), past the array's end for any
                # h >= 1 (a target Search2: Subscript out of range)
                k0 += 1
                field -= esize
                idx, stride = inner
                while field < 0:
                    idx = add(idx, Const(-1))
                    field += stride
                inner = (idx, stride)
            elem = add(whole, Const(k0)) if whole is not None else Const(k0)
        dims = info["dims"]
        if len(dims) > 1:
            subs = self.split_index(elem, dims)
            if subs is None:
                return None
        else:
            subs = [add(elem, Const(dims[0][0])) if dims[0][0] else elem]
        e = Index(var, subs)
        e.vbtype = None
        e.stride = esize
        if inner is not None:
            return self.inline_field(e, field, inner, size)
        return Deref(e, size, field)

    def note_elem_size(self, elem, size, disp):
        """An element __vbaDerefAry1 returned, read or written whole at its
        start: the array's element size, when nothing else said (VBDoom
        InitTables b = LoadResData(...) is a Byte array, read byte by byte;
        Dim b() As Long made the assignment a Type mismatch)."""
        if disp or not isinstance(elem.arr, Var) or elem.arr.space != "local":
            return              # (a module array's ReDim elsewhere gives its size; its
                                # first field read whole is no element size)
        info = self.arrays.get(elem.arr.key())
        if info is not None and info.get("dyn") and info.get("size") is None and \
                not info.get("vt") and size in (1, 2, 4, 8):
            info["size"] = size
            info["size_weak"] = True    # (a record's first field reads the same: udt.unify_array_params)

    def element_inline(self, elem, i, disp, size):
        """[&elem + i * scale + disp] for an element __vbaDerefAry1 returned:
        an element of a fixed array inside it (VBDoom lines(l).sidenum(k):
        [&lines(l) + k * 4 + 1Ch]; __vbaDerefAry1 indexes one dimension only,
        so further index terms are the inner array's), or with a constant
        index the field it lands on.  None when the terms do not fit."""
        import math
        e = BinOp("*", i, Const(self.scale)) if self.scale != 1 else i
        lin = self.linear(e)
        c = lin.get(None, [None, 0])[1]
        terms = [(a, k) for key, (a, k) in lin.items() if key is not None and k]
        if not terms:
            return Deref(elem, size, disp + c)
        if any(isinstance(a, (AddrOf, SP)) for a, _ in terms):
            return None
        stride = 0
        for a, k in terms:
            stride = math.gcd(stride, abs(k))
        if stride < max(size, 1):
            return None
        idx = None
        for a, k in terms:
            t = a if k == stride else BinOp("*", a, Const(k // stride))
            idx = t if idx is None else add(idx, t)
        if isinstance(elem, Index) and isinstance(elem.arr, Deref) and \
                getattr(elem.arr, "inline", None) and elem.arr.inline % stride == 0:
            # an array in an element of an array in a record, through With: one index, as the
            # compiler adds them up without With (With Specie(i).Mutables: .Mean(j))
            k = elem.arr.inline // stride
            t = elem.idx[0] if k == 1 else BinOp("*", elem.idx[0], Const(k))
            return self.inline_field(elem.arr.e, elem.arr.off + disp + c, (add(t, idx), stride), size)
        return self.inline_field(elem, disp + c, (idx, stride), size)

    def with_param(self, b):
        """With (p), p a ByRef parameter: the frame slot VB keeps it in is the parameter, read as a
        base: [slot + i * 4 + 58h] printed a1(v + 22) (DarwinBots SetDefaultLengths).  Only for a
        slot nothing else is stored to but the zero at End With."""
        if not (isinstance(b, Var) and b.space == "local" and isinstance(b.off, int) and b.off < 0):
            return b
        cache = self.__dict__.setdefault("_with_params", {})
        if b.off not in cache:
            found = None
            bad = False
            prev = None
            slot = b.off - 4                # (the frame's zero is ebp's value: slot - 4 from esp0)
            for a in sorted(self.p.insns):
                ins = self.p.insns[a]
                ops = ins.operands
                if len(ops) == 2 and ops[0].type == X.X86_OP_MEM and \
                        ops[0].mem.base == X.X86_REG_EBP and ops[0].mem.index == 0 and \
                        ops[0].mem.disp == b.off and ins.mnemonic not in ("cmp", "test"):
                    if ins.mnemonic == "and" and ops[1].type == X.X86_OP_IMM and ops[1].imm == 0:
                        pass
                    elif ins.mnemonic == "mov" and ops[1].type == X.X86_OP_REG and prev is not None and \
                            prev.mnemonic in ("mov", "lea") and prev.operands[0].type == X.X86_OP_REG and \
                            prev.operands[0].reg == ops[1].reg and \
                            prev.operands[1].type == X.X86_OP_MEM and \
                            prev.operands[1].mem.base == X.X86_REG_EBP and \
                            prev.operands[1].mem.index == 0 and \
                            (prev.mnemonic == "mov" and prev.operands[1].mem.disp >= 8 or
                             prev.mnemonic == "lea" and prev.operands[1].mem.disp < 0) and \
                            found in (None, (prev.mnemonic, prev.operands[1].mem.disp)):
                        # (or With on a record in the frame: With Load_mrates, the Function's value)
                        found = (prev.mnemonic, prev.operands[1].mem.disp)
                    else:
                        bad = True
                if ins.mnemonic == "lea" and ops[1].mem.base == X.X86_REG_EBP and \
                        ops[1].mem.disp == b.off:
                    bad = True
                prev = ins
            if found is not None and found[0] == "mov" and any(
                    len(q.operands) == 2 and q.operands[0].type == X.X86_OP_MEM and
                    q.operands[0].mem.base == X.X86_REG_EBP and q.operands[0].mem.index == 0 and
                    q.operands[0].mem.disp == found[1] and q.mnemonic not in ("cmp", "test")
                    for q in self.p.insns.values()):
                bad = True              # (a ByVal parameter changed: the slot may be a copy of it)
            if found is not None and found[0] == "mov" and not self.slot_is_base(b.off):
                bad = True              # (a ByVal parameter's copy, for a bounds check: an index)
            if bad or found is None:
                cache[b.off] = None
            elif found[0] == "mov":
                cache[b.off] = Var("arg", found[1] - 4)
                self.__dict__.setdefault("with_args", set()).add(found[1] - 4)
            else:
                cache[b.off] = SP(found[1] - 4)
        return cache[b.off] or b

    def slot_is_base(self, slot):
        """What the code loads from this frame slot it only ever uses as a memory operand's base."""
        addrs = sorted(self.p.insns)
        used = False
        for k, a in enumerate(addrs):
            ins = self.p.insns[a]
            ops = ins.operands
            if not (ins.mnemonic == "mov" and len(ops) == 2 and ops[0].type == X.X86_OP_REG and
                    ops[1].type == X.X86_OP_MEM and ops[1].mem.base == X.X86_REG_EBP and
                    ops[1].mem.index == 0 and ops[1].mem.disp == slot):
                if any(o.type == X.X86_OP_MEM and o.mem.base == X.X86_REG_EBP and
                       o.mem.index == 0 and o.mem.disp == slot for o in ops) and not (
                           ins.mnemonic in ("mov", "and") and ops[0].type == X.X86_OP_MEM):
                    return False        # (cmp, add, push...: a number)
                continue
            r = ops[0].reg
            for q in addrs[k + 1:k + 8]:
                n = self.p.insns[q]
                nops = n.operands
                mems = [o for o in nops if o.type == X.X86_OP_MEM]
                if any(o.mem.index == r for o in mems):
                    return False
                if any(o.mem.base == r for o in mems):
                    used = True
                    break
                if any(o.type == X.X86_OP_REG and o.reg == r for o in nops):
                    return False        # (moved, compared, added to: not only a base)
                if n.mnemonic in ("call", "ret", "jmp"):
                    break
        return used

    def member_element(self, i, disp, size):
        """[Me + i * k + disp]: an element of a fixed array in a record of the object (Private
        QStack As Stack, QStack.val(i): a plain fixed array has a SAFEARRAY).  It comes out as an
        array of the object, as nothing depends on where the object keeps it."""
        import math
        e = BinOp("*", i, Const(self.scale)) if self.scale != 1 else i
        lin = self.linear(e)
        c = lin.get(None, [None, 0])[1]
        terms = [(a, k) for key, (a, k) in lin.items() if key is not None and k]
        if not terms or disp + c <= 0 or any(isinstance(a, (AddrOf, SP)) for a, _ in terms):
            return None
        stride = 0
        for a, k in terms:
            stride = math.gcd(stride, abs(k))
        if stride < max(size, 1):
            return None
        idx = None
        for a, k in terms:
            t = a if k == stride else BinOp("*", a, Const(k // stride))
            idx = t if idx is None else add(idx, t)
        v = Var("member", disp + c)
        info = self.arrays.setdefault(v.key(), {"size": stride, "dyn": False, "lb": 0, "n": None,
                                                "var": v})
        info["inline_member"] = True    # (the data right there, as in a record: no descriptor)
        atoms = [k for k, (a, q) in self.linear(idx).items() if k is not None and q]
        if len(atoms) == 1 and self.linear(idx)[atoms[0]][1] == 1 and \
                self.index_bounds.get(atoms[0]):
            info["n"] = max(info.get("n") or 0, self.index_bounds[atoms[0]])
        el = Index(v, [idx])
        el.vbtype = None
        return el

    def data_element(self, i, disp, size):
        """[i * k + address in .data]: an element of a fixed array in a module record (Private
        IntStack As Stack, IntStack.val(i)), the data right there and no SAFEARRAY.  Without a
        record known at that address it comes out as a module array of its own."""
        import math
        if not self.data_address(Const(disp)):
            return None
        e = BinOp("*", i, Const(self.scale)) if self.scale != 1 else i
        lin = self.linear(e)
        c = lin.get(None, [None, 0])[1]
        terms = [(a, k) for key, (a, k) in lin.items() if key is not None and k]
        if not terms or any(isinstance(a, (AddrOf, SP)) for a, _ in terms):
            return None
        va0 = (disp + c) & 0xffffffff
        if any(h <= va0 < h + (rec.size if d and rec else room)
               for h, d, room, rec in self.module_record_spans()):
            return None                 # (its record's: the next lift knows it)
        stride = 0
        for a, k in terms:
            stride = math.gcd(stride, abs(k))
        if stride < max(size, 1):
            return None
        # (below a record by a whole element or two: the address took a subscript's - 1 along,
        # SimOpts.Specie(i - 1).Name, an array of Longs in the strings before it otherwise)
        if stride > 8 and any(h <= va0 + n * stride < h + (rec.size if d and rec else room)
                              for n in (1, 2, 3)
                              for h, d, room, rec in self.module_record_spans()):
            return None
        idx = None
        for a, k in terms:
            t = a if k == stride else BinOp("*", a, Const(k // stride))
            idx = t if idx is None else add(idx, t)
        va = (disp + c) & 0xffffffff
        v = Var("global", va, self.m.global_name(va))
        info = self.arrays.setdefault(v.key(), {"size": stride, "dyn": False, "lb": 0, "n": None,
                                                "var": v, "inline_data": True})
        atoms = [k for k, (a, q) in self.linear(idx).items() if k is not None and q]
        if len(atoms) == 1 and self.linear(idx)[atoms[0]][1] == 1 and \
                self.index_bounds.get(atoms[0]):
            info["n"] = max(info.get("n") or 0, self.index_bounds[atoms[0]])
        el = Index(v, [idx])
        el.vbtype = None
        return el

    def frame_record_elem(self, b, i, disp, size):
        """[ebp + i * 4 - N]: an element of a fixed array inside a local record, which is the
        only frame variable indexed directly: a local array has a SAFEARRAY.  Without knowing the
        record a target Proc_9B2B70 printed (v0 + v24 * 4).f-25C = 0.  The record is one of
        self.structs (Put / Get move it whole); otherwise the slot starts one of its own."""
        at = disp + b.off + 4               # (ebp is SP(-4))
        start, field = at, 0
        for off, (_, n) in self.structs.items():
            if off <= at < off + n:
                start, field = off, at - off
                break
        else:
            # (just below one: the address took a subscript's - 1 along, BitAND.bit(i) in optimised
            # code with i already 1 further on: [esp + esi * 2 + 0Eh], the record at 10h)
            for off, (_, n) in self.structs.items():
                if self.scale > 1 and at < off <= at + self.scale and off < 0:
                    start, field = off, at - off
                    break
        return self.element_inline(Var("local", start), i, field, size)

    def split_inner(self, part, esize, size):
        """(whole elements, (inner index, stride)) for a byte offset whose
        terms are not all multiples of the element size esize: the others,
        all multiples of one stride below esize (at least the access size),
        index an array inside the element.  None when they do not fit."""
        import math
        lin = self.linear(part)
        if lin.get(None, [None, 0])[1]:
            return None                     # (a constant belongs to the field offset)
        outer, rest = None, []
        for key, (e, c) in lin.items():
            if key is None or not c:
                continue
            if c % esize == 0:
                t = e if c == esize else BinOp("*", e, Const(c // esize))
                outer = t if outer is None else add(outer, t)
            else:
                # (one variable in both subscripts: i * (size + 4))
                q = abs(c) // esize
                if q:
                    q = q if c > 0 else -q
                    t = e if q == 1 else BinOp("*", e, Const(q))
                    outer = t if outer is None else add(outer, t)
                    c -= q * esize
                rest.append((e, c))
        stride = 0
        for e, c in rest:
            stride = math.gcd(stride, abs(c))
        if not rest or stride < max(size, 1) or stride >= esize:
            return None
        idx = None
        for e, c in rest:
            t = e if c == stride else BinOp("*", e, Const(c // stride))
            idx = t if idx is None else add(idx, t)
        return outer, (idx, stride)

    def inline_field(self, base, field, inner, size):
        """Element inner = (index, stride) of a fixed array inside a UDT:
        Deref(Index(F, [index]), size, 0), where F = Deref(base, stride,
        field) is marked inline (its elements follow each other in the UDT,
        unlike a dynamic array field's SAFEARRAY pointer).  field is the raw
        offset: for an array of UDTs it may point into an element (the
        writer finds the array that holds it); inline_n is the element count
        when a bounds check showed it."""
        idx, stride = inner
        f = Deref(base, stride, field)
        f.inline = stride
        lin = self.linear(idx)
        atoms = [k for k, (e, c) in lin.items() if k is not None and c]
        if len(atoms) == 1 and lin[atoms[0]][1] == 1:
            n = self.index_bounds.get(atoms[0])
            if n:
                f.inline_n = n
        el = Index(f, [idx])
        el.vbtype = None
        return Deref(el, size, 0)

    def record_type(self, va):
        """The program-wide Record for a record descriptor address."""
        from . import records
        return records.model_record(self.m, va)

    def record_ref(self, p):
        """The UDT variable or array element a record pointer addresses."""
        p = self.frame_ptr(self.slot_ptr(p) if isinstance(p, AddrOf) else p)
        if isinstance(p, AddrOf):
            return p.e
        if self.data_address(p):
            # a module-level record by its address (VBDoom G_PlayerReborn:
            # players(player) = zeroPlayer pushes 48AB68h)
            ms = self.module_struct_ref(p)
            return ms.e if ms is not None else self.global_var(p.value & 0xffffffff)
        base, off = p, 0
        if isinstance(p, BinOp) and p.op == "+" and isinstance(p.b, Const):
            base, off = p.a, p.b.value
        if isinstance(base, Var):
            info = self.arrays.get(base.key())
            if info is not None and info.get("size"):
                k, rem = divmod(off, info["size"])
                if rem == 0:
                    e = Index(base, [Const(k + (info.get("lb") or 0))])
                    e.vbtype = None
                    return e
        ea = self.element_address(p)        # pvData + i * K: an element (LSet net(i, j) = loc(j))
        if ea is not None:
            return ea.e
        return Deref(p)                     # through a pointer (a ByRef UDT parameter)

    def note_record(self, ref, rec):
        """ref holds a value of record type rec (an array element: the array)."""
        if rec is None:
            return
        v = ref.arr if isinstance(ref, Index) else ref
        if isinstance(v, Deref) and v.off == 0 and isinstance(v.e, Var):
            v = v.e
        n = fixed_string_record(rec)
        if n and not isinstance(ref, Index):
            # the descriptor VB makes to Get / Put / free a String * n: the
            # variable is that string, no UDT (a target Get #f, a1,
            # global_F06120 declared it As TRec18 = {f0 As String * 200})
            self.note_fixed(v, n)
            return
        if isinstance(v, Var):
            self.rec_of[v.key()] = rec.va
            self.udt[v.key()] = rec.name

    def split_index(self, idx, dims):
        """A multi-dimensional subscript list from the linear element index
        the compiler computed: column-major, i.e. dimension k has stride
        n1*...*n(k-1), and every subscript is relative to its lower bound."""
        strides, acc = [], 1
        for lb, n in dims:
            strides.append(acc)
            acc *= n
        terms, const = {}, 0

        def walk(e, c):
            nonlocal const
            if isinstance(e, Const) and isinstance(e.value, int):
                const += c * e.value
            elif isinstance(e, BinOp) and e.op == "+":
                walk(e.a, c)
                walk(e.b, c)
            elif isinstance(e, BinOp) and e.op == "-":
                walk(e.a, c)
                walk(e.b, -c)
            elif isinstance(e, BinOp) and e.op == "*" and isinstance(e.b, Const) and \
                    isinstance(e.b.value, int):
                walk(e.a, c * e.b.value)
            elif isinstance(e, BinOp) and e.op == "*" and isinstance(e.a, Const) and \
                    isinstance(e.a.value, int):
                walk(e.b, c * e.a.value)
            else:
                k = e.key()
                old = terms.get(k)
                terms[k] = (e, (old[1] if old else 0) + c)
        walk(idx, 1)
        subs = [[] for _ in dims]
        for e, c in terms.values():
            if c == 0:
                continue
            for k in reversed(range(len(dims))):
                if c % strides[k] == 0:
                    m = c // strides[k]
                    subs[k].append(e if m == 1 else BinOp("*", e, Const(m)))
                    break
            else:
                return None
        # the constant part is sum((s_k - lb_k) * stride_k).  A dimension with a
        # variable subscript contributes -lb_k (its own constant is usually 0);
        # what is left splits in mixed radix: digit k is the subscript's
        # offset from 0 (variable dimensions) or from lb_k (constant ones)
        relative = const == 0       # (each term is its subscript less the lower bound already: a pooled slot)
        const += sum(lb * st for (lb, n), st, sk in zip(dims, strides, subs) if sk)
        out = []
        for k in reversed(range(len(dims))):
            if relative and subs[k]:
                # (m(1, 2) with both in slots, x + y * 2: x + 1, y + 1.  To the nearest stride it was
                # x - 1, y + 2, the same element if both are valid and a subscript error on its own.  t101ornall)
                q = dims[k][0]
                const -= q * strides[k]
            elif any(subs[j] for j in range(k)):
                # (a variable below takes what's left over: to the nearest whole stride, not down.
                # dnamatrix(tipo - 2, value - 1) of a (8, 13) is tipo + value * 9 - 11: as (tipo + 7,
                # value - 2) one element, and tipo + 7 is out of range.  DarwinBots DNAtoInt)
                q = (const + strides[k] // 2) // strides[k]
                const -= q * strides[k]
            else:
                q, const = divmod(const, strides[k])
            e = None
            for t in subs[k]:
                e = t if e is None else add(e, t)
            if e is None:
                if q < 0:
                    return None         # (below a constant subscript's bound)
                e = Const(q + dims[k][0])
            elif q:
                e = add(e, Const(q))
            out.append(e)
        return out[::-1]

    def unscale(self, idx, size):
        """Byte offset -> element index."""
        if size <= 1:
            return idx
        if isinstance(idx, Const):
            return Const(idx.value // size) if idx.value % size == 0 else None
        if isinstance(idx, BinOp) and idx.op == "*" and isinstance(idx.b, Const):
            if idx.b.value == size:
                return idx.a
            if idx.b.value % size == 0:
                return BinOp("*", idx.a, Const(idx.b.value // size))
            return None
        if isinstance(idx, BinOp) and idx.op in ("+", "-"):
            a = self.unscale(idx.a, size)
            b = self.unscale(idx.b, size)
            if a is not None and b is not None:
                return BinOp(idx.op, a, b)
            return None
        if isinstance(idx, UnOp) and idx.op == "-":
            a = self.unscale(idx.a, size)
            return UnOp("-", a) if a is not None else None
        return None if size != 1 else idx

    @staticmethod
    def drop_term(idx, term):
        """idx (a sum) without one subtracted occurrence of term, folded; None
        when term is not subtracted exactly once."""
        terms = []

        def walk(e, sign):
            if isinstance(e, BinOp) and e.op in ("+", "-"):
                walk(e.a, sign)
                walk(e.b, sign if e.op == "+" else -sign)
            elif isinstance(e, UnOp) and e.op == "-":
                walk(e.a, -sign)
            else:
                terms.append((sign, e))
        walk(idx, 1)
        hits = [i for i, (sg, t) in enumerate(terms) if t == term and sg < 0]
        if len(hits) != 1 or any(t == term and sg > 0 for sg, t in terms):
            return None
        del terms[hits[0]]
        const, out = 0, None
        for sg, t in terms:
            if isinstance(t, Const) and isinstance(t.value, int):
                const += sg * t.value
                continue
            part = t if sg > 0 else UnOp("-", t)
            out = part if out is None else (BinOp("-", out, t) if sg < 0 else add(out, t))
        if out is None:
            return Const(const)
        return add(out, Const(const)) if const else out

    def unbias(self, idx, var, info):
        """The compiler indexes from zero: add the lower bound back."""
        lb = Deref(var, 4, 0x14)                    # SAFEARRAY lLbound
        r = self.drop_term(idx, lb)
        if r is not None:
            info["lb"] = "dyn"
            return r
        if isinstance(idx, UnOp) and idx.op == "-" and idx.a == lb:
            info["lb"] = "dyn"
            return Const(0)
        if isinstance(idx, BinOp) and idx.op == "+" and isinstance(idx.b, UnOp) and \
                idx.b.op == "-" and idx.b.a == lb:
            info["lb"] = "dyn"
            return idx.a
        if isinstance(idx, BinOp) and idx.op == "-" and idx.a == lb:
            info["lb"] = "dyn"
            return UnOp("-", idx.b)
        if isinstance(idx, BinOp) and idx.op == "-" and isinstance(idx.b, Const) and \
                info["lb"] in (None, idx.b.value):
            # i - 1 for a(i): a 1-based array, unless the bounds say otherwise
            info["lb"] = idx.b.value
            return idx.a
        if isinstance(idx, BinOp) and idx.op == "+" and isinstance(idx.b, Const) and \
                isinstance(idx.b.value, int) and idx.b.value < 0 and \
                info["lb"] in (None, -idx.b.value):
            info["lb"] = -idx.b.value
            return idx.a
        if isinstance(info["lb"], int) and info["lb"] and not isinstance(idx, Const):
            return add(idx, Const(info["lb"]))      # known lower bound: offset + lb
        if isinstance(idx, Const) and isinstance(info["lb"], int):
            return Const(idx.value + info["lb"])    # a constant subscript
        if info["lb"] is None:
            info["lb"] = 0
        return idx

    def store_through(self, ptr, v, stmts, kind="let"):
        """Write v through a pointer (ecx of __vbaStrCopy etc.); returns the
        variable now holding the value."""
        ptr = self.slot_ptr(ptr)
        el = self.element_ptr(ptr)
        if el is None:
            el = self.dyn_element(ptr)      # an element of a dynamic array
        if el is None:
            ea = self.element_address(ptr, whole=False)
            el = ea.e if ea is not None else None   # pvData + i * K + field
        if el is not None:                  # a field of an element of an array of UDTs
            stmts.append(Assign(el, v, kind))
            return el
        if isinstance(ptr, SP):
            off = ptr.off
            if self.base_esp is not None and off >= self.base_esp:
                tgt = self.slot_var(off)
                if isinstance(tgt, Var):
                    # like a plain store: earlier readers keep the old value
                    if self.cur_st is not None:
                        self.preserve(self.cur_st, tgt, self.known.get(off), stmts)
                self.known[off] = v
                self.note_type(tgt, v)
                stmts.append(Assign(tgt, v, kind))
                return tgt
        if isinstance(ptr, AddrOf):
            stmts.append(Assign(ptr.e, v, kind))
            return ptr.e
        if isinstance(ptr, Const) and self.im.section_of(ptr.value & 0xffffffff) == ".data":
            tgt = self.global_var(ptr.value & 0xffffffff)
            self.note_type(tgt, v)
            stmts.append(Assign(tgt, v, kind))
            return tgt
        tgt = Deref(ptr)
        stmts.append(Assign(tgt, v, kind))
        return tgt

    STATIC_BASE = 0x10000           # member offsets standing for a class's Static locals

    def static_var(self, b, disp):
        """[block + disp] where block is the class's Static-locals block (its
        pointer lives in a member slot, gh_zip cZipArchive +0xBC): that Static
        variable, as a member of its own.  A module's block has its pointer in a module variable
        (decompile.static_slots), and the variable is a module variable of its own."""
        if isinstance(b, Var) and b.space == "global" and \
                b.off in getattr(self.m, "module_static_slot", ()):
            va = self.m.module_static_slot[b.off] + disp
            return Var("global", va, self.m.global_name(va))
        if self.obj is None or not isinstance(b, Var) or b.space != "member":
            return None
        if self.m.static_slot.get(self.obj.index) != b.off:
            return None
        return Var("member", self.STATIC_BASE + disp)

    def struct_array_param(self, v):
        """v is a parameter pointing at a UDT whose first field is a dynamic
        array (decompile.struct_array_params): ReDim, UBound and the elements
        through *v are that field's, not v's own."""
        return isinstance(v, Var) and v.space == "arg" and \
            (self.p.va, v.off) in getattr(self.m, "udt_array_params", ())

    def array_arg(self, a):
        """An array passed by address: name the variable."""
        if isinstance(a, BinOp) and a.op == "+" and isinstance(a.b, Const) and \
                isinstance(a.b.value, int):
            sv = self.static_var(a.a, a.b.value)
            if sv is not None:
                return sv                   # ReDim of a Static array of the class
        if isinstance(a, Const) and self.im.section_of(a.value & 0xffffffff) == ".data":
            # (a fixed array by its static descriptor: x = Obstacles, ObsRepop)
            return self.var_at(a.value & 0xffffffff)
        a = self.frame_ptr(a)
        if isinstance(a, AddrOf) and isinstance(a.e, Var) and a.e.space == "local" and \
                isinstance(a.e.off, int):
            # (a frame slot holding the module array's address: mov [ebp-1Ch], 889038h; lea eax,
            # [ebp-1Ch]; x = Obstacles.Obstacles printed x = &H889038)
            k = self.known.get(a.e.off - 4)
            if isinstance(k, Const) and isinstance(k.value, int) and \
                    self.im.section_of(k.value & 0xffffffff) == ".data":
                return self.var_at(k.value & 0xffffffff)
        if isinstance(a, AddrOf) and isinstance(a.e, Index):
            # an element's address for an array: the record's first field is that array (ReDim
            # Buckets(x, y).arr(0) printed ReDim global_897DE8(vC, v8)(0))
            return Deref(a.e, 4, 0)
        if isinstance(a, AddrOf):
            return a.e
        if isinstance(a, Deref) and a.off == 0:
            return a.e
        return a

    def item_ref(self, a):
        """The variable behind an address a statement is given (Input #f, x): a frame variable, a
        module variable or a field of one, an element by pvData + i * k."""
        a = self.frame_ptr(a)
        if self.data_address(a):
            return AddrOf(self.var_at(a.value & 0xffffffff))
        if isinstance(a, BinOp) and a.op in ("+", "-"):
            ms = self.module_struct_ref(a)
            if ms is not None:
                return ms
            ea = self.element_address(a)
            if ea is not None:
                return ea
        return a

    def frame_ptr(self, a):
        """A pointer into the fixed frame -> &variable."""
        if isinstance(a, SP) and self.base_esp is not None and a.off >= self.base_esp:
            v = self.slot_var(a.off, whole=True)
            if isinstance(v, (Var, Deref)):
                return AddrOf(v)
        return a

    VT_OK = {2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 17}

    def variant_arg(self, a, empty_ok=False):
        """A VARIANT built in place on the frame: (vt, value) stores at
        off and off+8.  Returns AddrOf(value) so handlers can deref it.
        empty_ok: the callee reads a Variant here, so a zero (or 1) tag is the
        literal Empty (Null) - elsewhere a zeroed slot is just a zeroed slot."""
        if not isinstance(a, SP):
            return a
        vt = self.known.get(a.off)
        if not isinstance(vt, Const) or not isinstance(vt.value, int):
            return a                        # (a Double payload is no tag)
        t = vt.value & 0xffff
        if t & 0x8000:
            t &= 0x7fff                     # VB marks Variants it builds from literals
        if t & 0x4000:                      # VT_BYREF
            v = self.known.get(a.off + 8)
            if v is None:
                return a
            p = self.frame_ptr(v)
            if isinstance(p, AddrOf) and isinstance(p.e, Var) and t & 0xfff in (5, 7):
                # d = 1234.567 is stored as two dwords (simplify.fold_double_slots),
                # and FormatCurrency(d) reads it through this Variant
                self.fp_qword.add(p.e.key())
            return self.typed_ref(p.e if isinstance(p, AddrOf) else Deref(v), t)
        if t in (0, 1) and not empty_ok:
            return a
        if t in (0, 1):                     # VT_EMPTY, VT_NULL
            return self.typed_ref(Var("global", ("vbconst", t), "Empty" if t == 0 else "Null"), t)
        if t & 0x2000:
            # VT_ARRAY: the payload is the SAFEARRAY pointer, the array variable's
            # value (Items = dict_pItems: vt 200Ch, [+8] = Me.dict_pItems)
            v = self.known.get(a.off + 8)
            if isinstance(v, Var) and v.space in ("member", "global", "local", "arg"):
                return self.typed_ref(v, t)
            p = self.frame_ptr(v) if v is not None else None
            if isinstance(p, AddrOf) and isinstance(p.e, Var):
                # a local fixed array by its descriptor in the frame (s = bytes():
                # a target InitIdx printed CStr(8209), the tag 2011h)
                return self.typed_ref(p.e, t)
            return a
        if t not in self.VT_OK:
            return a
        v = self.known.get(a.off + 8)
        if t == 10 and isinstance(v, Const) and (v.value & 0xffffffff) == 0x80020004:
            return AddrOf(ir.Missing())
        v = self.wide_value(t, v, self.known.get(a.off + 12))
        if v is None:
            if t in (2, 3, 4, 8, 9, 11, 17):
                # the tag is known here, the value was stored on the way in:
                # a Variant's payload is 8 bytes above its tag
                w = self.slot_var(a.off + 8)
                if isinstance(w, (Var, Deref)):
                    return self.typed_ref(w, t)
            return a
        if t in (9, 13) and isinstance(v, Const) and v.value == 0:
            return self.typed_ref(Var("global", ("vbconst", 9), "Nothing"), t)  # (calls.byval_variant)
        if t == 11 and isinstance(v, Const):
            v = Const(v.value, "Boolean")
        if t & 0xfff == 8:
            from .calls import empty_bstr
            v = empty_bstr(self, v)         # (Trim(s) = "": printed = &H435888)
        return self.typed_ref(long_literal(v, t), t)

    @staticmethod
    def wide_value(t, lo, hi):
        """A Variant's payload as a literal: Single bits, and the two dwords of
        a Double / Currency / Date."""
        import struct
        if not isinstance(lo, Const) or not isinstance(lo.value, int):
            return lo
        if t == 2:
            # (mov [ebp-3Ch], 0FFFF8300h: -32000, an Integer, not the Long &HFFFF8300 that printed)
            v = lo.value & 0xffff
            return Const(v - 0x10000 if v >= 0x8000 else v, lo.vbtype)
        if t == 4:
            if not lo.value & 0x7f800000 and lo.value & 0x7fffff:
                return lo       # a denormal is no literal: a small integer in a slot that
                                # looks like a Single's (a target Proc_97E220 3 printed 4.2e-45)
            return Const(struct.unpack("<f", struct.pack("<I", lo.value & 0xffffffff))[0], "Single")
        if t in (5, 6, 7) and isinstance(hi, Const) and isinstance(hi.value, int):
            raw = struct.pack("<II", lo.value & 0xffffffff, hi.value & 0xffffffff)
            if t == 6:
                return Const(struct.unpack("<q", raw)[0] / 10000.0, "Currency")
            return Const(struct.unpack("<d", raw)[0], "Date" if t == 7 else "Double")
        return lo

    VT_TYPE = {2: "Integer", 3: "Long", 4: "Single", 5: "Double", 6: "Currency", 7: "Date",
               8: "String", 11: "Boolean", 17: "Byte"}

    def typed_ref(self, x, t):
        """&x for a Variant VB built around x with tag t.  The tag is the
        static type of x (VT_BSTR: a String), so a Variant runtime function
        receiving it does not make x itself a Variant."""
        if isinstance(x, Var) and t & 0xfff in self.VT_TYPE and not t & 0x2000:
            # (render: a scalar tag on what turns out a record's address is its first field, Format$(u.bal)
            # with a Currency in Type TAcc: a record can't go in a Variant, "Only user-defined types
            # defined in public object modules": t42currency)
            x = copy.copy(x)
            x.scalar_tag = t & 0xfff
        r = AddrOf(x)
        r.vt = t
        if t & 0x2000:
            # (VT_ARRAY: an array's descriptor or its pointer, of no such type: a slot holding &s() for
            # Filter(s, "p") and a number later.  A parameter is an array of that type: For Each e In a
            # with a() As Long)
            a = x.e if isinstance(x, Deref) and x.off == 0 else x
            if isinstance(a, Var) and a.space == "arg" and t & 0xfff in self.VT_TYPE:
                info = self.arrays.setdefault(a.key(), {"size": {2: 2, 3: 4, 4: 4, 5: 8, 6: 8, 7: 8, 8: 4,
                                                                  11: 2, 17: 1}.get(t & 0xfff, 4),
                                                        "dyn": True, "lb": None, "n": None, "var": a})
                info.setdefault("vt", t & 0xfff)
                self.byref.add(a.off)
            return r
        if isinstance(x, Deref) and x.off == 0 and isinstance(x.e, Var) and x.e.space == "arg" \
                and t & 0xfff in self.VT_TYPE:
            # VT_BYREF to *param: a ByRef parameter of that type (Year(dDate))
            self.vt_types.setdefault(x.e.key(), set()).add(self.VT_TYPE[t & 0xfff])
            self.byref.add(x.e.off)
        if isinstance(x, Var):
            if t & 0xfff == 12:
                self.variants.add(x.key())          # VT_BYREF|VT_VARIANT
            elif t & 0xfff in self.VT_TYPE:
                self.vt_types.setdefault(x.key(), set()).add(self.VT_TYPE[t & 0xfff])
        return r

    def slot_ptr(self, ptr):
        """&local (what frame_ptr makes of a stack address) back to the stack
        address, so stores through it update what the block knows."""
        if isinstance(ptr, AddrOf) and isinstance(ptr.e, Var) and ptr.e.space == "local" \
                and isinstance(ptr.e.off, int) and self.base_esp is not None \
                and ptr.e.off - 4 >= self.base_esp:
            return SP(ptr.e.off - 4)
        return ptr

    def bind_temp(self, ptr, v):
        """Result written through an out-pointer: the slot is assigned (so it
        survives into other blocks) and remembered for readers in this one."""
        ptr = self.slot_ptr(ptr)
        if isinstance(ptr, SP):
            self.temps[ptr.off] = v
            if self.base_esp is not None and ptr.off >= self.base_esp:
                tgt = self.slot_var(ptr.off)
                if isinstance(tgt, Var):
                    if self.cur_st is not None:
                        self.preserve(self.cur_st, tgt, self.known.get(ptr.off), self.cur_stmts)
                self.known[ptr.off] = v
                if isinstance(tgt, (Var, Deref)):
                    a = Assign(tgt, v)
                    a.va = self.cur_va
                    if isinstance(tgt, Var) and getattr(v, "vbtype", None) == "Object" and \
                            getattr(v, "iface", None) is None:
                        # a temporary reused: its old type must not pass on
                        # (Screen.FontCount, then Set v258 = Me.Controls through the
                        # same slot made v258 As Screen: Type mismatch at run time)
                        self.objtype.pop(tgt.key(), None)
                    self.note_type(tgt, v)
                    self.cur_stmts.append(a)
        elif isinstance(ptr, AddrOf):
            self.temps[("var", ptr.e.key())] = v
            a = Assign(ptr.e, v)
            a.va = self.cur_va
            self.cur_stmts.append(a)

    def is_temp_slot(self, off):
        return False

    def impure(self, e):
        for x in e.walk():
            if isinstance(x, Call) and not x.pure:
                return True
            if isinstance(x, Prop) and x.control is None:
                return True
        return False

    def is_named_local(self, v):
        if not isinstance(v, Var) or v.space in ("reg", "tmp", "stk") or not isinstance(v.off, int):
            return False                    # (a struct's field: slot_var gives a Deref)
        return v.off in self.named or v.off in self.pinned or v.off == self.result_slot

    @property
    def kept(self):
        """The slots no pass may treat as the compiler's own.  (lf.named alone says which the epilogue frees:
        a record's fields are not among them, decompile.struct_hints.)"""
        return self.named | self.pinned if self.pinned else self.named

    ARG_REUSE = -0x40000    # local offsets standing for a reused parameter slot (vA0008)

    def slot_var(self, off, size=4, whole=False):
        if off >= 4:
            if off in getattr(self, "reused_args", ()):
                return Var("local", self.ARG_REUSE - off)   # (see _store)
            if off == 4 and self.obj is not None:
                return Me()
            return Var("arg", off)
        rel = off + 4                      # ebp == esp0 - 4
        if self.seh_book and -self.seh_book <= rel <= 0:
            return Var("frame", rel)
        for base, (name, ssize) in self.structs.items():
            if base <= rel < base + ssize:
                rs = self.result_slot
                if isinstance(rs, int) and base != rs and rel != base and \
                        rel in (rs, rs + 4) and not getattr(self.p, "udt_ret", False):
                    return Var("local", rel)  # the Function's own scalar result value
                                              # (its upper half for a Double); not the
                                              # field at 0 of a struct that starts there
                v = Var("local", base)
                if whole and rel == base:
                    return v                # the struct itself (its address is taken)
                return Deref(v, size, rel - base)      # a field of a struct local
        return Var("local", rel)

    def global_struct(self, va):
        """(start, size) of the module-level UDT variable whose bytes hold va
        (decompile.global_struct_hints), or None."""
        hs = getattr(self.m, "global_struct_hint", None)
        if not hs:
            return None
        starts = getattr(self.m, "_gstruct_starts", None)
        if starts is None or len(starts) != len(hs):
            starts = self.m._gstruct_starts = sorted(hs)
        import bisect
        i = bisect.bisect_right(starts, va) - 1
        if i >= 0 and starts[i] <= va < starts[i] + hs[starts[i]]:
            return starts[i], hs[starts[i]]
        return None

    def module_record_spans(self):
        """[(address, descriptor, room, record)] of the module variables that are records."""
        got = self.m.__dict__.get("_module_rec_spans")
        if got is None:
            from . import records
            got = [(h, d, room, records.model_record(self.m, d) if d else None)
                   for h, (d, room) in self.m.module_records().items()]
            self.m._module_rec_spans = got
        return got

    def global_var(self, va, size=4):
        gs = self.global_struct(va)
        if gs is not None:
            b = gs[0]                       # a field of a module-level UDT
            return Deref(Var("global", b, self.m.global_name(b)), size, va - b)
        name = self.m.global_name(va)
        return Var("global", va, name)

    def param_element(self, ptr, struct_args):
        """&p.f(i) for an address p + i * k + c, p a parameter pointing at a
        UDT (struct_args: argument offsets the body reads fields through): an
        element of an array inside that UDT (VBDoom HUlib_initSText passes
        s.l(i) as a1 + i * 20).  None for anything else."""
        import math
        if not isinstance(ptr, BinOp) or ptr.op not in ("+", "-"):
            return None
        lin = self.linear(ptr)
        bases = [(key, a) for key, (a, k) in lin.items() if key is not None and k == 1 and
                 isinstance(a, Var) and a.space == "arg" and a.off in struct_args]
        if len(bases) != 1:
            return None
        key, base = bases[0]
        c = lin.get(None, [None, 0])[1]
        terms = [(a, k) for kk, (a, k) in lin.items() if kk not in (key, None) and k]
        if not terms or c < 0 or any(isinstance(a, (AddrOf, SP)) for a, _ in terms):
            return None
        stride = 0
        for a, k in terms:
            stride = math.gcd(stride, abs(k))
        if stride < 2:
            return None
        idx = None
        for a, k in terms:
            t = a if k == stride else BinOp("*", a, Const(k // stride))
            idx = t if idx is None else add(idx, t)
        return AddrOf(self.inline_field(base, c, (idx, stride), 4).e)

    def var_at(self, va):
        """The module variable a runtime helper is given the address of: a
        fixed array by its static descriptor (its pvData slot 0Ch further:
        VBDoom Get #f, , players passes 4890B4h), a module-level UDT or a
        field of one, else the variable at va."""
        slots = self.m.__dict__.get("_array_slots")
        if slots is None:
            slots = self.m._array_slots = self.m.global_array_slots()
        if va + 12 in slots:
            return self.global_var(va + 12)
        ms = self.module_struct_ref(Const(va))
        return ms.e if ms is not None else self.global_var(va)

    def module_struct_ref(self, ptr):
        """For an address inside a module-level UDT variable: &the variable
        (its start), &a field of it, or &an element of a fixed array in it
        (doomcom.data.cmds(i): 489550h + i * 18h, the array 14h into data at
        48953Ch); None for anything else."""
        if not getattr(self.m, "global_struct_hint", None):
            return None
        if isinstance(ptr, Const) and isinstance(ptr.value, int):
            gs = self.global_struct(ptr.value & 0xffffffff)
            if gs is None:
                return None
            b = gs[0]
            v = Var("global", b, self.m.global_name(b))
            off = (ptr.value & 0xffffffff) - b
            return AddrOf(v) if off == 0 else AddrOf(Deref(v, 4, off))
        if isinstance(ptr, BinOp) and ptr.op in ("+", "-"):
            # (an address: no width.  Input #1, Specie(i).Skin(j) in steps of 2)
            el = self.module_struct_elem(ptr, None, 0, 1)
            if el is not None:
                if isinstance(el, Deref) and (el.off or not getattr(el.e.arr, "inline", None)):
                    return AddrOf(el)       # (a field of an element of an array of records)
                return AddrOf(el.e)         # the element as a whole
        return None

    def module_struct_elem(self, b, i, disp, size):
        """Deref(Index(F, [index]), size, 0) for a memory operand (b + i *
        scale + disp) inside a module-level UDT with index terms: an element
        of a fixed array in it (inline_field); None otherwise."""
        import math
        if not getattr(self.m, "global_struct_hint", None):
            return None
        e = b if b is not None else Const(0)
        if i is not None:
            e = BinOp("+", e, BinOp("*", i, Const(self.scale)) if self.scale != 1 else i)
        lin = self.linear(e)
        c = lin.get(None, [None, 0])[1] + disp
        terms = [(a, k) for key, (a, k) in lin.items() if key is not None and k]
        if not terms or any(isinstance(a, (AddrOf, SP)) for a, _ in terms):
            return None
        stride = 0
        for a, k in terms:
            stride = math.gcd(stride, abs(k))
        gs = self.global_struct(c & 0xffffffff)
        if gs is None and stride > 8:
            # (below the first such variable: nothing there at all)
            for m in (1, 2, 3):
                if self.global_struct((c + m * stride) & 0xffffffff) is not None:
                    gs = (None, 0)
                    break
        if gs is None:
            return None
        base, n = gs
        below = 0
        if stride > 8:
            # the address took a subscript's - 1 along, and landed in the variable before: the
            # array is in the next one, when this one has none of that stride here
            # (TmpOpts.Specie(TmpOpts.SpeciesNum - 1), in the tail of SimOpts next to it)
            name = self.udt.get(("global", base)) if base is not None else None
            at = apidb_array_at(name, (c & 0xffffffff) - base) if name else None
            if base is None or at is None or at[2] != stride:
                for m in (1, 2, 3):
                    g2 = self.global_struct((c + m * stride) & 0xffffffff)
                    if g2 is None:
                        continue
                    n2 = self.udt.get(("global", g2[0]))
                    a2 = apidb_array_at(n2, (c + m * stride & 0xffffffff) - g2[0]) if n2 else None
                    # (or further on in this one: SimOpts.Specie(SimOpts.SpeciesNum - 1).Native)
                    if g2[0] != base or a2 is not None and a2[2] == stride:
                        c += m * stride
                        base, n = g2
                        below = m
                        break
        if base is None or stride < max(size, 1) or stride >= n:
            return None
        idx = Const(-below) if below else None
        for a, k in terms:
            t = a if k == stride else BinOp("*", a, Const(k // stride))
            idx = t if idx is None else add(idx, t)
        v = Var("global", base, self.m.global_name(base))
        return self.inline_field(v, (c & 0xffffffff) - base, (idx, stride), size)

    def fp_const(self, va, size):
        import struct
        try:
            b = self.im.bytes(va, size)
            if size == 8:
                return Const(struct.unpack("<d", b)[0])
            if size == 4:
                # (a Single's: x * 200! is a Single, x * 200.0 a Double, and Round(.aim * 200) on the
                # one or the other parts on a .5 sooner or later; DarwinBots SetAimFunc)
                return Const(struct.unpack("<f", b)[0], "Single")
        except Exception:
            pass
        return self.global_var(va, size)

    # ================================================================== calls
    def impure(self, e):
        """Calls with possible side effects (user procedures, APIs, methods,
        property gets) -- everything except pure runtime helpers and
        control references."""
        for x in e.walk():
            if isinstance(x, Call) and not x.pure:
                return True
            if isinstance(x, Prop) and x.control is None:
                return True
        return False

    def protect(self, st, stmts):
        """Before a call with side effects: registers that hold the result of
        an earlier call must keep that value, not re-evaluate it later."""
        done = {}
        for r in self.GPR:
            v = st.regs.get(r)
            if v is None or isinstance(v, (Me, IatSlot, SP)):
                continue
            if isinstance(v, Var) and v.space == "reg" and v.off == r:
                continue
            if id(v) in done:                   # same value already saved in another register
                st.regs[r] = done[id(v)]
                continue
            if self.impure(v):
                a = Assign(Var("reg", r), v)
                a.va = self.cur_va
                self.note_type(a.dst, v)
                stmts.append(a)
                done[id(v)] = Var("reg", r)
                st.regs[r] = Var("reg", r)
                st.spilled.pop(r, None)
                self.spilled.add(r)

    def tail_runtime(self, st, ins):
        """jmp to a runtime helper that takes nothing from the stack (st(0) or
        registers only): a tail call whose result the procedure returns."""
        kind, detail = calls.target_name(self, st, ins)
        if kind != "runtime":
            return False
        from . import runtime
        spec = runtime.get(detail)
        return spec is not None and not spec.nargs and not spec.cdecl and not spec.varargs

    def do_call(self, st, ins, stmts):
        kind, detail = calls.target_name(self, st, ins)
        given = None
        if kind in ("proc", "vtable", "declare"):
            self.save_over_call(st, stmts)
            given = dict(st.stack)
        # (insns.fpu: an FPU read right after a call to a procedure)
        self.last_call = (ins.address + ins.size, detail.va if kind == "proc" else None)

        if kind == "runtime":
            site = calls.do_runtime(self, st, detail, stmts)
        elif kind == "declare":
            site = calls.do_declare(self, st, detail, stmts)
        elif kind == "proc":
            site = calls.do_proc(self, st, detail, stmts)
        elif kind == "vtable":
            base, off = detail
            site = calls.do_vtable(self, st, base, off, stmts)
        else:
            site = calls.Site("indirect", Unknown("call %r" % (detail,)))
        if given:
            # what the callee got the address of it may have filled: a Variant zeroed at entry is
            # no Empty after it (a target Form_Load: Proc_438C00 ..., v24: If Empty = True)
            for k, a in given.items():
                if k not in st.stack and isinstance(a, SP):
                    t = self.known.get(a.off)
                    if isinstance(t, Const) and t.value in (0, 1):
                        del self.known[a.off]
        if site.value is not None:
            v = site.value
            if self.impure(v) and not isinstance(v, Var):
                # bind the result now: later code must see this call's value,
                # not a second evaluation of it
                self.ntmp += 1
                t = Var("tmp", self.ntmp)
                a = Assign(t, v)
                a.va = ins.address
                self.note_type(t, v)
                if getattr(v, "vbtype", None):
                    self.tmp_types[t.key()] = v.vbtype
                    t.vbtype = v.vbtype if v.vbtype in ("Currency", "Double", "Date") else None
                stmts.append(a)
                edx = getattr(site, "edx", None)
                if isinstance(edx, Hi) and edx.of is v:
                    site.edx = Hi(t)            # the upper half of the bound value
                v = t
            elif any(isinstance(x, (ir.Call, ir.Prop)) for x in v.walk()):
                cs = CallStmt(v)
                cs.va = ins.address
                stmts.append(cs)
            # else: an address or variable handed back in eax (&result of a
            # Variant Function): nothing was evaluated, so nothing to state
            if site.fpu:
                st.fpush(v)
            else:
                st.set("eax", v)
                if getattr(site, "edx", None) is not None:
                    st.set("edx", site.edx)     # the high half of an 8-byte result

    def forward_small_result(self, st, stmts):
        """The halves of a small record Function that optimised code keeps in registers to the end
        (VSet.x = x: mov eax, [esp + 0Ch]) go to its result here."""
        rec = getattr(self, "small_rec", None)
        if rec is None:
            return
        for reg in sorted(getattr(self.p, "_pair_forward", ())):
            off = 0 if reg == "eax" else 4
            v = st.get(reg)
            if isinstance(v, Var) and v.space == "reg" and reg not in self.spilled and \
                    reg not in st.regs:
                continue                    # never set here
            a = Assign(Deref(Var("local", rec[0]), 4, off), v)
            a.va = self.cur_va
            stmts.append(a)

    def return_value(self, st):
        """Value the procedure hands back.

        A Function with a frame result slot returns that slot (the
        procedure-name variable); otherwise the value left in st(0)/eax.
        """
        if getattr(self.p, "udt_ret", False):
            return None                     # the block copy into *arg was the Return
        if self.result_slot is not None:
            return Var("local", self.result_slot)
        if getattr(self.p, "kind", None) == "event":
            return None                     # a Sub; eax is just the HRESULT
        if getattr(self.m, "returns_known", False) and not self.m.proc_returns.get(self.p.va) \
                and not getattr(self.p, "float_ret", False):
            return None                     # callers never use a value: a Sub
        if st.fpu:
            return st.st(0)
        v = st.regs.get("eax")
        if v is None and "eax" in self.spilled:
            return Var("reg", "eax")
        return v

    def pure_runtime(self, name):
        from . import runtime
        spec = runtime.get(name)
        return spec is not None and (spec.pure or spec.kind in ("free", "ignore", "store",
                                                               "varstore", "objset"))

    # ============================================================ expressions
    @staticmethod
    def boolify(e):
        """(cmp) <> 0 and -(cmp) come from setcc; print the comparison.
        (x - c) = 0 comes from "sub reg, c; je": print x = c."""
        if isinstance(e, BinOp) and e.op in ("=", "<>") and isinstance(e.b, Const) and \
                e.b.value == 0 and isinstance(e.a, BinOp) and e.a.op in ("-", "+") and \
                isinstance(e.a.b, Const) and isinstance(e.a.b.value, int):
            c = e.a.b.value if e.a.op == "-" else -e.a.b.value
            return Lifter.boolify(BinOp(e.op, e.a.a, Const(c)))
        if isinstance(e, BinOp) and e.op in ("=", "<>") and isinstance(e.b, Const) \
                and e.b.value == -1 and Lifter.is_boolean(e.a):
            # "(a <> b) = -1": the (cmp) + 1 = 0 the first rule rewrote.  A VB
            # Boolean is -1 when true, so the condition is the comparison.
            from .structure import negate
            return e.a if e.op == "=" else negate(e.a)
        if isinstance(e, BinOp) and e.op in ("<>", "=") and isinstance(e.b, Const) \
                and e.b.value == 0:
            inner = e.a
            while isinstance(inner, UnOp) and inner.op == "-":
                inner = inner.a
            b = Lifter.vbool(inner)
            if b is None and isinstance(inner, (Call, ir.Prop)) and inner.vbtype == "Boolean":
                if e.op == "<>":
                    return inner            # IsEmpty(v) <> 0 is IsEmpty(v)
                return UnOp("Not", inner)
            if b is None and isinstance(inner, BinOp) and \
                    (inner.op in Lifter.CMP or
                     inner.op in ("And", "Or") and Lifter.is_boolean(inner.a) and
                     Lifter.is_boolean(inner.b)):
                # (x And y) <> 0 is x And y only for Booleans: with bit masks
                # (Data(i) And LNG_POW2(k)) it is a test of the result (gh_qr)
                b = inner
            if b is not None:
                from .structure import negate
                return b if e.op == "<>" else negate(b)
            if inner is not e.a:
                return BinOp(e.op, inner, Const(0))     # -(x) <> 0 is x <> 0
        return e

    CMP = ("=", "<>", "<", ">", "<=", ">=", "Like", "Is")

    @staticmethod
    def is_boolean(e):
        """e is a VB Boolean (-1/0): a comparison, Not/And/Or of Booleans, a
        Boolean constant or call."""
        if isinstance(e, BinOp) and e.op in Lifter.CMP:
            return True
        if isinstance(e, BinOp) and e.op in ("And", "Or", "Xor"):
            return Lifter.is_boolean(e.a) and Lifter.is_boolean(e.b)
        if isinstance(e, UnOp) and e.op == "Not":
            return Lifter.is_boolean(e.a)
        return getattr(e, "vbtype", None) == "Boolean"

    @staticmethod
    def vbool(e):
        """The VB Boolean (-1/0) equal to e <> 0 for an expression valued 1/0
        (setcc results -(cmp), and Or/And of them), or None."""
        if isinstance(e, UnOp) and e.op == "-" and isinstance(e.a, BinOp) and \
                e.a.op in Lifter.CMP:
            return e.a
        if isinstance(e, BinOp) and e.op in ("Or", "And"):
            a, b = Lifter.vbool(e.a), Lifter.vbool(e.b)
            if a is not None and b is not None:
                return BinOp(e.op, a, b)
        return None

    def switch_guard(self, node, mn, c):
        """"cmp index, n; ja default" before a jump table compares unsigned: a
        negative index (Asc(x) - 132 for x below 132) takes the default too.
        VB compares signed, so it says index < 0 Or index > n."""
        if mn not in ("ja", "jbe") or not isinstance(c, BinOp) or c.op not in (">", "<="):
            return c
        if getattr(node, "insns", None) and any(i.mnemonic == "sahf" for i in node.insns[-2:]):
            return c                    # (an FPU compare, jbe after sahf: DarwinBots calculateZB)
        def starts_table(a):
            # (cfg's keys of the tests of a table start at 1 << 40; copied tails at 2 << 40: those
            # are no table, and If Mx > oldMx printed If Mx < 0 Or Mx > oldMx)
            n = self.nodes.get(a) if isinstance(a, int) else None
            return n is not None and (getattr(n, "switch", None) is not None or
                                      any(isinstance(x, int) and 1 << 40 <= x < 2 << 40
                                          for x in n.succs))
        if not any(starts_table(a) for a in node.succs):
            return c
        x, zero = c.a, Const(0)
        if c.op == ">":
            return BinOp("Or", BinOp("<", x, zero), c)
        return BinOp("And", BinOp(">=", x, zero), c)

    def reads_reg_first(self, a, r):
        """Node a reads scratch register r before writing it."""
        n = self.nodes.get(a)
        names = {"eax": ("eax", "ax", "al", "ah"), "ecx": ("ecx", "cx", "cl", "ch"),
                 "edx": ("edx", "dx", "dl", "dh")}[r]
        for ins in (n.insns if n is not None else ()):
            rd, wr = ins.regs_access()
            if any(ins.reg_name(x) in names for x in rd):
                # (xor r, r reads it only on paper)
                if ins.mnemonic == "xor" and ins.operands[0].type == X.X86_OP_REG and \
                        ins.operands[0].reg == ins.operands[1].reg:
                    return False
                return True
            if any(ins.reg_name(x) in names for x in wr):
                return False
        return False

    def cond_expr(self, st, mn):
        f = st.flags
        op = JCC.get(mn)
        if f is None or op is None:
            return Unknown(mn)
        if f[0] == "test" and isinstance(f[1], FSWHigh) and isinstance(f[2], Const):
            # FPU compare of a with b: C0 = a < b, C3 = a = b
            a, b = f[1].fsw.a, f[1].fsw.b
            mask = f[2].value & 0x45
            jne = mn in ("jne", "jnz")
            if mn not in ("je", "jz", "jne", "jnz"):
                return Unknown(mn)
            table = {0x40: ("=", "<>"), 0x41: ("<=", ">"), 0x01: ("<", ">="),
                     0x05: ("<", ">="), 0x44: ("=", "<>"), 0x45: ("<=", ">")}
            ops = table.get(mask)
            if ops is None:
                return Unknown("fsw%x" % mask)
            return BinOp(ops[0] if jne else ops[1], a, b)
        if f[0] == "cmp":
            c = BinOp(op, f[1], f[2])
            if len(f) > 3 and f[3] in (1, 2, 4):
                c.width = f[3]          # integers compared (types.int_used: no Double)
            return self.boolify(c)
        if f[0] == "test":
            a, b = f[1], f[2]
            if a == b:
                return self.boolify(BinOp(op, a, Const(0)))
            if isinstance(a, BinOp) and a.op == "And" and isinstance(a.b, Const) and isinstance(b, Const) and \
                    isinstance(a.b.value, int) and isinstance(b.value, int):
                # (test byte ptr [p + i*4], 8: part_of_element's mask and this one)
                return BinOp(op, BinOp("And", a.a, Const(a.b.value & b.value)), Const(0))
            return BinOp(op, BinOp("And", a, b), Const(0))
        if f[0] == "fcom":
            return BinOp(op, f[1], f[2])

        if f[0] == "arith":
            r = f[1]
            # "sub eax, c; je" is the compare eax = c
            if isinstance(r, BinOp) and r.op in ("-", "+") and isinstance(r.b, Const) and \
                    isinstance(r.b.value, int) and op in ("=", "<>"):
                c = r.b.value if r.op == "-" else -r.b.value
                return BinOp(op, r.a, Const(c))
            return BinOp(op, r, Const(0))
        return Unknown(mn)

    def flag_expr(self, st):
        f = st.flags
        if f and f[0] == "cmp":
            return BinOp("=", f[1], f[2])
        return None

    # ============================================================== helpers
    def iface_of(self, base):
        it = self.check_iface.get(self.cur_va)
        if it is not None and not isinstance(base, Me):
            if hasattr(base, "key"):
                self.objtype.setdefault(base.key(), it)
            if isinstance(base, Deref) and base.off == 0 and isinstance(base.e, Var) and \
                    base.e.space == "arg":
                self.objtype.setdefault(base.e.key(), it)      # ByRef object parameter
            return it
        if isinstance(base, Me) and self.obj is not None:
            return self.m.me_interface(self.obj)
        if isinstance(base, Var) and base.space == "member" and base.off == 0x10 and \
                self.obj is not None and self.obj.kind == "UserControl":
            # [Me+10h] is the control's own UserControl object (t25usercontrol: UserControl.ScaleWidth)
            return self.m.me_interface(self.obj)
        if isinstance(base, Var) and base.space == "global" and base.key() not in self.objtype:
            t = self.m.global_types.get(base.off)
            if t is not None:
                return t
        t = getattr(base, "iface", None)
        if t is not None:
            return t
        k = base.key() if hasattr(base, "key") else None
        return self.objtype.get(k)

    def recv_expr(self, this):
        if isinstance(this, Me):
            return Me()
        return this

    OWN_METHODS = 0x6f8     # Me vtable slot of a form's first own procedure

    def own_method(self, base, off):
        """Me's own vtable entry at off: the procedure, or a tuple naming a
        variable accessor (WithEvents and Public variables)."""
        if not isinstance(base, Me) or self.obj is None:
            return None
        pi = self.m.proj_ifaces.get(self.obj.index)
        if pi is not None:
            e = pi.entries.get(off)
            if e is None:
                return None
            return self.m.procs.get(e[1]) if e[0] == "proc" else e
        lst = self.m.vtable_order.get(self.obj.index)
        if not lst or off < self.OWN_METHODS:
            return None
        i = (off - self.OWN_METHODS) // 4
        if i >= len(lst):
            return None
        return self.m.procs.get(lst[i])

    def control_for(self, base, off):
        """Me vtable above _Form.cbvft: a control property getter."""
        if not isinstance(base, Me) or self.obj is None:
            return None
        iface = self.m.me_interface(self.obj)
        if iface is None or off < iface.cbvft:
            return None
        idx = (off - iface.cbvft) // 4
        c = self.m.control_by_index(self.obj, idx)
        if c is None:
            return None
        _, default, _ = self.m.control_class(c)
        e = Prop(Me(), c.name)
        e.control = c
        e.iface = default
        if c.name.lower() in self.m.control_arrays(self.obj):
            e.iface = ControlArray(default, c.name)
        return e

    def in_arg(self, param, a):
        if isinstance(a, AddrOf) and isinstance(a.e, ir.Missing):
            return a.e
        if isinstance(a, AddrOf) and param is not None and \
                (param.type.vt == 12 or (param.type.vt == 26 and param.type.sub is not None and
                                         param.type.sub.vt == 12)):
            return a.e                      # a Variant built in place: its value
        return self.plain(self.element_ref(self.plain(a)))

    def plain(self, a):
        """Strip pointer wrappers from an argument for printing."""
        if isinstance(a, SP) and a.off in self.temps:
            v = self.temps[a.off]
            in_frame = self.base_esp is not None and a.off >= self.base_esp
            if not (in_frame and self.impure(v)):
                return v
            # the call was assigned to the slot: read the slot, not a second call
        if isinstance(a, SP):
            return self.slot_var(a.off, whole=True)
        if isinstance(a, AddrOf):
            return a.e
        return a

    def deref_arg(self, a):
        return self.plain(a)

    def by_ref(self, a):
        """A procedure argument: a frame variable's address stays &variable
        (passed by reference: inlining its value would be wrong); a call
        result's temporary is the call itself."""
        if isinstance(a, SP) and self.base_esp is not None and a.off >= self.base_esp and \
                a.off not in self.temps:
            tag = self.known.get(a.off)
            if isinstance(tag, Const) and isinstance(tag.value, int) and \
                    tag.value & 0x6000 == 0x6000:
                # an array for a ByRef Variant: the payload points at a slot holding the array's
                # SAFEARRAY (FeedGraph: CalcStats nomi, dati printed CalcStats 24584, 24580)
                p = self.known.get(a.off + 8)
                sa = self.known.get(p.off) if isinstance(p, SP) else None
                if isinstance(sa, SP):
                    v = self.slot_var(sa.off, whole=True)
                    if isinstance(v, Var):
                        return AddrOf(self.sa_alias.get(v.key(), v))
                elif isinstance(sa, Deref) and sa.off == 0 or isinstance(sa, Var):
                    return AddrOf(sa)
                elif self.data_address(sa):
                    # (a module's fixed array by its descriptor there: Position(k, Names))
                    return AddrOf(self.var_at(sa.value & 0xffffffff))
            if isinstance(tag, Const) and isinstance(tag.value, int) and \
                    tag.value & 0x4000 and not tag.value & 0x2000:
                # a Variant VB built around a variable for a ByRef Variant
                # parameter (VT_BYREF | vt -> &var): pass the variable itself
                ref = self.frame_ptr(self.known.get(a.off + 8)) if \
                    self.known.get(a.off + 8) is not None else None
                if self.data_address(ref):
                    # (a module variable: GiveMutationDetails(robfocus) to a ByRef Variant made
                    # robfocus a Variant, and all the Integer parameters it goes to mismatch)
                    ref = AddrOf(self.var_at(ref.value & 0xffffffff))
                if isinstance(ref, AddrOf) and isinstance(ref.e, Deref):
                    return through_variant(ref)
                if isinstance(ref, AddrOf) and isinstance(ref.e, Var):
                    # (the tag is the variable's type: it is no Variant for going through this
                    # one; t81byref, a target Proc_4C0940 ..., v with v a String)
                    if ref.e.space == "local" and isinstance(ref.e.off, int):
                        self.named.add(ref.e.off)   # the callee may change it: no compiler's temp
                    if tag.value & 0xfff in self.VT_TYPE:
                        self.vt_types.setdefault(ref.e.key(), set()).add(self.VT_TYPE[tag.value & 0xfff])
                    elif tag.value & 0xfff == 12:
                        self.variants.add(ref.e.key())
                    return through_variant(ref)
                if isinstance(ref, Var) and ref.space == "arg":
                    if tag.value & 0x2000:
                        self.typed_ref(ref, tag.value)      # (For Each e In a: an array parameter)
                    return through_variant(ref)     # a ByRef parameter's own pointer
                if isinstance(ref, BinOp):
                    # an element: a target Proc_EEA370 global_F045C4(i) printed 16392
                    ea = self.element_address(ref)
                    if isinstance(ea, AddrOf):
                        return through_variant(ea)
            v = self.slot_var(a.off, whole=True)
            if isinstance(v, Var):
                return AddrOf(v)
        return self.element_ref(self.plain(a))

    def element_ref(self, p):
        """An address added up: the element or field it is, else p."""
        if isinstance(p, BinOp) and p.op == "+":
            ms = self.module_struct_ref(p)  # (ArvotS K.lajit(i).mu: i * 96 + its address)
            if ms is not None:
                return ms
        if isinstance(p, BinOp) and p.op == "+" and any(
                c == 1 and isinstance(e, Var) and e.key() in self.arrays and
                not self.arrays[e.key()].get("dyn") and self.arrays[e.key()].get("dims")
                for k, (e, c) in self.linear(p).items() if k is not None):
            # an element of a local fixed array, by its address (SetValues "x", dati(3, k): the
            # sum v5C + (3 + k * 501) * 4 went to a ByRef Single)
            ea = self.element_address(p)
            if isinstance(ea, AddrOf):
                return ea
        return p

    def is_out_slot(self, a):
        # lea edx, [ebp-28h] as well as an esp-relative address (gh_dict Exists:
        # dict_GetKeyValue(Key, &v28) is v28 = dict_GetKeyValue(Key))
        return isinstance(a, SP) or isinstance(a, AddrOf) and isinstance(a.e, Var) and \
            a.e.space == "local" and isinstance(a.e.off, int) and not self.is_named_local(a.e)

    def class_name(self, a):
        return None


def long_literal(v, t):
    """A VT_I4 Variant built from a literal in Integer range: VB reads a bare 7
    as an Integer (VT_I2), so it must be written 7& (gh_dict d.Add CLng(7), ...:
    TypeName gave Integer)."""
    if t == 3 and isinstance(v, Const) and isinstance(v.value, int) and v.vbtype is None and \
            -0x8000 <= v.value < 0x8000:
        return Const(v.value, "Long&")
    return v


class FSWHigh(ir.Expr):
    """ah = high byte of the FPU status word after a compare."""

    def __init__(self, fsw):
        self.fsw = fsw

    def key(self):
        return ("fswh", self.fsw.key())

    def children(self):
        return [self.fsw]

    def _rebuild(self, g):
        return self._keep(FSWHigh(g(self.fsw)))


class CallStmt(ir.Stmt):
    def __init__(self, e):
        self.e = e
        self.used = False

    def exprs(self):
        return (self.e,)

    def __repr__(self):
        return repr(self.e)


SAVED = {X.X86_REG_EBP, X.X86_REG_EBX, X.X86_REG_ESI, X.X86_REG_EDI, X.X86_REG_EAX}


def param_base(lf):
    """The arg offset of a procedure's first parameter as its parameter list
    counts them: after Me for a method, after the hidden result pointer of a
    Function returning a Variant or a UDT (render.Names; struct_params and
    the record passes index parameters the same way)."""
    base = 8 if lf.obj is not None else 4
    if getattr(lf, "hidden_ret", False):
        base += 4
    return base


def variant_return(proc):
    """A Function returning a Variant (or another 16-byte value): the caller
    passes a result pointer as the hidden first argument, and the epilogue
    copies the result slot through it: "mov eax, [ebp+8]" ... "mov [ecx], edx"
    with edx loaded from [ebp-N].  Returns N or None."""
    if getattr(proc, "_vret", 0) != 0:
        return proc._vret
    proc._vret = None
    if proc.ret_pop < 4:
        return None
    addrs = sorted(proc.insns)
    if not proc.seh:
        # no cleanup to do: only a record of numbers, copied out (t84recret TIso)
        if not uses_frame(proc):
            return rep_movs_frameless(proc, addrs)
        return rep_movs_return(proc, addrs)
    for i, a in enumerate(addrs):
        ins = proc.insns[a]
        op = ins.op_str.replace(" ", "")
        if ins.mnemonic != "mov" or op not in ("eax,dwordptr[ebp+8]", "edx,dwordptr[ebp+8]",
                                               "ecx,dwordptr[ebp+8]"):
            continue
        ptr = ins.operands[0].reg
        if ptr != X.X86_REG_EAX and not ptr_copy_return(proc, addrs, i, ptr):
            continue
        for j in range(i + 1, min(i + 8, len(addrs))):
            x = proc.insns[addrs[j]]
            if x.mnemonic == "mov" and x.operands[0].type == X.X86_OP_REG and \
                    x.operands[1].type == X.X86_OP_MEM and x.operands[1].mem.base == X.X86_REG_EBP \
                    and x.operands[1].mem.disp < 0:
                nxt = [proc.insns[addrs[k]] for k in range(j + 1, min(j + 24, len(addrs)))]
                if any(y.mnemonic == "mov" and y.operands[0].type == X.X86_OP_MEM and
                       y.operands[0].mem.base in (X.X86_REG_EAX, X.X86_REG_ECX, X.X86_REG_EDX) and
                       y.operands[0].mem.disp == 0 for y in nxt) and \
                        any(y.mnemonic == "ret" for y in nxt):
                    proc._vret = x.operands[1].mem.disp
                    # a plain dword copy of the result slot through the pointer,
                    # covering [ptr+4]: the whole fixed UDT delivered field by
                    # field (a Variant copy writes only the type word at +0 and
                    # the payload at +8, gh_json utc_DateToSystemTime)
                    tagged, disps = {ptr}, set()
                    for y in nxt:
                        if y.mnemonic != "mov" or len(y.operands) != 2:
                            continue
                        o0, o1 = y.operands
                        if o0.type == X.X86_OP_MEM and o0.mem.base in tagged and \
                                o0.mem.index in (0, X.X86_REG_INVALID) and o0.size == 4:
                            disps.add(o0.mem.disp)
                        elif o0.type == X.X86_OP_REG and o1.type == X.X86_OP_REG and \
                                o1.reg in tagged:
                            tagged.add(o0.reg)
                    # (not 16 bytes, it is no Variant whatever takes its address: __vbaCopyBytes
                    # for a whole assignment, DarwinBots hsltorgb = huetorgb(h))
                    if 4 in disps and (max(disps) + 4 != 16 or
                                       not slot_is_deep(proc, x.operands[1].mem.disp)):
                        proc.udt_ret = max(disps) + 4
                    return proc._vret
    return rep_movs_return(proc, addrs)


def ptr_copy_return(proc, addrs, i, ptr):
    """The result pointer loaded into edx or ecx instead of eax (a target
    now1, a Variant Function: "mov edx, [ebp+8]; mov eax, [ebp-30h]; mov
    [edx], eax" ... "mov [edx+0Ch], ecx; mov eax, [ebp+8]" ... "ret 4").
    A ByRef parameter is loaded the same way, so ask for more than the eax
    shape does: stores at +0 and +8 through ptr, then eax reloaded from
    [ebp+8] before the ret."""
    disps, reload, live = set(), False, True
    for k in range(i + 1, min(i + 24, len(addrs))):
        y = proc.insns[addrs[k]]
        if y.mnemonic == "ret":
            return reload and {0, 8} <= disps
        ops = y.operands
        if y.mnemonic != "mov" or len(ops) != 2:
            continue
        if live and ops[0].type == X.X86_OP_MEM and ops[0].mem.base == ptr and \
                ops[0].mem.index in (0, X.X86_REG_INVALID) and ops[1].type == X.X86_OP_REG:
            disps.add(ops[0].mem.disp)
        elif ops[0].type == X.X86_OP_REG and ops[0].reg == ptr:
            live = False                    # ptr reloaded: later stores are not the copy
        if y.op_str.replace(" ", "") == "eax,dwordptr[ebp+8]":
            reload = True
    return False


def rep_movs_frameless(proc, addrs):
    """rep_movs_return without a frame pointer (fast code): mov eax, [esp+54h]; mov ecx, 10h;
    lea esi, [esp+10h]; mov edi, eax; rep movsd; pop...; add esp, 40h; ret 0Ch.  esp is known going
    back from the ret.  DarwinBots BitAND As DoubleWord."""
    for i, a in enumerate(addrs):
        if proc.insns[a].mnemonic != "rep movsd":
            continue
        tail, esp = [], 0
        for k in range(i + 1, min(i + 16, len(addrs))):
            x = proc.insns[addrs[k]]
            tail.append(x)
            if x.mnemonic == "ret":
                break
        else:
            continue
        for x in tail[:-1]:
            if x.mnemonic == "pop":
                esp -= 4
            elif x.mnemonic == "add" and x.operands[0].type == X.X86_OP_REG and \
                    x.operands[0].reg == X.X86_REG_ESP and x.operands[1].type == X.X86_OP_IMM:
                esp -= x.operands[1].imm
            elif x.mnemonic not in ("mov", "xor", "lea"):
                break
        else:
            n = slot = None
            dst = False
            ptr = set()
            for k in range(max(0, i - 8), i):
                x = proc.insns[addrs[k]]
                o = x.operands
                if x.mnemonic == "mov" and o[0].type == X.X86_OP_REG and o[0].reg == X.X86_REG_ECX \
                        and o[1].type == X.X86_OP_IMM:
                    n = o[1].imm
                elif x.mnemonic == "lea" and o[0].reg == X.X86_REG_ESI and \
                        o[1].mem.base == X.X86_REG_ESP:
                    slot = esp + o[1].mem.disp + 4     # (slot_var's offsets: ebp == esp0 - 4)
                elif x.mnemonic == "mov" and o[0].type == X.X86_OP_REG and \
                        o[1].type == X.X86_OP_MEM and o[1].mem.base == X.X86_REG_ESP and \
                        esp + o[1].mem.disp == 4:
                    ptr.add(o[0].reg)               # the first parameter: the caller's buffer
                    dst = dst or o[0].reg == X.X86_REG_EDI
                elif x.mnemonic == "mov" and o[0].type == X.X86_OP_REG and \
                        o[1].type == X.X86_OP_REG and o[0].reg == X.X86_REG_EDI and o[1].reg in ptr:
                    dst = True
            if n and slot is not None and dst and slot < 0 and X.X86_REG_EAX in ptr:
                proc._vret = slot
                proc.udt_ret = 4 * n
                return slot
    return None


def rep_movs_return(proc, addrs):
    """The other epilogue of a Function returning a fixed UDT: "mov ecx, n;
    lea esi, [ebp-N]; mov edi, [ebp+8]; rep movsd; mov eax, [ebp+8]" ...
    "ret": n dwords of the result slot into the caller's buffer.  It sits
    after the exit's cleanup call (push cont; jmp cleanup), which the lift
    does not follow (a target ReadInfo As TRec13: its 48
    bytes, so callers' buffers came out As T_30_3 and the Function As
    Long).  Returns N (negative) or None."""
    for i, a in enumerate(addrs):
        ins = proc.insns[a]
        run = 0
        if ins.mnemonic in ("movsd", "movsw", "movsb"):
            # a short one as single moves (t84recret TIso, 12 bytes: three movsd, and the Function
            # came out an empty Sub); bytes past the dwords round up, as the slot does
            if i and proc.insns[addrs[i - 1]].mnemonic in ("movsd", "movsw", "movsb"):
                continue
            k = i
            while k < len(addrs) and proc.insns[addrs[k]].mnemonic in ("movsd", "movsw", "movsb"):
                run += {"movsd": 4, "movsw": 2, "movsb": 1}[proc.insns[addrs[k]].mnemonic]
                k += 1
        elif ins.mnemonic != "rep movsd":
            continue
        n = slot = dst8 = None
        ptr_regs = set()                    # registers holding [ebp+8]
        for k in range(max(0, i - 6), i):
            x = proc.insns[addrs[k]]
            ops = x.operands
            if x.mnemonic == "mov" and len(ops) == 2 and ops[0].type == X.X86_OP_REG and \
                    ops[0].reg == X.X86_REG_ECX and ops[1].type == X.X86_OP_IMM:
                n = ops[1].imm
            elif x.mnemonic == "pop" and ops[0].type == X.X86_OP_REG and \
                    ops[0].reg == X.X86_REG_ECX and k and \
                    proc.insns[addrs[k - 1]].mnemonic == "push" and \
                    proc.insns[addrs[k - 1]].operands[0].type == X.X86_OP_IMM:
                n = proc.insns[addrs[k - 1]].operands[0].imm    # push 10h; pop ecx (DarwinBots BitAND)
            elif x.mnemonic == "lea" and ops[0].reg == X.X86_REG_ESI and \
                    ops[1].mem.base == X.X86_REG_EBP and ops[1].mem.disp < 0:
                slot = ops[1].mem.disp
            elif x.mnemonic == "mov" and len(ops) == 2 and ops[0].type == X.X86_OP_REG and \
                    ops[1].type == X.X86_OP_MEM and ops[1].mem.base == X.X86_REG_EBP and \
                    ops[1].mem.disp == 8:
                ptr_regs.add(ops[0].reg)    # mov edi, [ebp+8] / mov eax, [ebp+8]
                if ops[0].reg == X.X86_REG_EDI:
                    dst8 = True
            elif x.mnemonic == "mov" and len(ops) == 2 and ops[0].type == X.X86_OP_REG and \
                    ops[1].type == X.X86_OP_REG and ops[0].reg == X.X86_REG_EDI and \
                    ops[1].reg in ptr_regs:
                dst8 = True                 # mov eax, [ebp+8]; mov edi, eax
        if run:
            n = (run + 3) // 4
        if not (n and slot and dst8):
            continue
        tail = [proc.insns[addrs[k]] for k in range(i + 1, min(i + 12, len(addrs)))
                if not proc.insns[addrs[k]].mnemonic.startswith("movs")]
        if (X.X86_REG_EAX in ptr_regs or
                any(y.mnemonic == "mov" and y.op_str.replace(" ", "") == "eax,dwordptr[ebp+8]"
                    for y in tail[:3])) and any(y.mnemonic == "ret" for y in tail):
            proc._vret = slot
            # (16 bytes with the slot's address taken besides the copy's own lea esi: a Variant
            # given by __vbaVarMove, no record.  t87nested Find1 = a, "As T_4_1"; only seen since
            # push 4; pop ecx counts)
            taken = any(q.mnemonic == "lea" and len(q.operands) == 2 and
                        q.operands[0].type == X.X86_OP_REG and q.operands[0].reg != X.X86_REG_ESI and
                        q.operands[1].type == X.X86_OP_MEM and
                        q.operands[1].mem.base == X.X86_REG_EBP and q.operands[1].mem.disp == slot
                        for q in proc.insns.values())
            if not (n == 4 and taken):
                proc.udt_ret = 4 * n
            return slot
    return None


def slot_is_deep(proc, n):
    """True when the result slot is a Variant/String/Object, not a fixed
    UDT buffer: the runtime helpers (__vbaVarMove, __vbaFreeVar ecx-arg,
    __vbaStrMove ...) take its address, and only those spell it "lea reg,
    [ebp-n]".  A fixed UDT is copied into the caller's buffer with plain
    dword "mov reg, [ebp-N]" loads and the slot's address is never taken
    (gh_json utc_DateToSystemTime), while a Function returning a Variant
    copies the whole 16-byte slot exactly like a 16-byte UDT
    (json_ParseValue, and json_ParseErrorMessage's String result)."""
    for ins in proc.insns.values():
        if ins.mnemonic == "lea" and len(ins.operands) == 2 and \
                ins.operands[0].type == X.X86_OP_REG and \
                ins.operands[1].type == X.X86_OP_MEM and \
                ins.operands[1].mem.base == X.X86_REG_EBP and \
                ins.operands[1].mem.disp == n:
            return True
    return False


def record_return(model, proc):
    """A Function returning a UDT with Strings/arrays (a record): it returns
    like a Variant Function (variant_return), but the result slot is
    released with __vbaRecDestruct(&slot, desc) instead of __vbaFreeVar.
    Returns the record descriptor address or None (gh_qr QRCodegenMakeBytes)."""
    if getattr(proc, "_rret", 0) != 0:
        return proc._rret
    proc._rret = None
    n = variant_return(proc)
    if n is None:
        return None
    addrs = sorted(proc.insns)
    for i, a in enumerate(addrs):
        ins = proc.insns[a]
        if ins.mnemonic != "call" or "RecDestruct" not in (cfgmod.call_name(model, proc, ins)
                                                            or ""):
            continue
        desc, slot = None, False
        for j in range(max(0, i - 5), i):
            x = proc.insns[addrs[j]]
            if x.mnemonic == "push" and x.operands[0].type == X.X86_OP_IMM:
                desc = x.operands[0].imm
            if x.mnemonic == "lea" and x.operands[1].type == X.X86_OP_MEM and \
                    x.operands[1].mem.base == X.X86_REG_EBP and x.operands[1].mem.disp == n:
                slot = True
        if slot and desc:
            proc._rret = desc
            return desc
    return None


def result_slot(proc):
    """Frame slot (ebp-relative) the epilogue loads into eax as the result:
    "mov eax,[ebp-N]" a few instructions before the final ret, with only
    register restores in between."""
    addrs = sorted(proc.insns)
    for i, a in enumerate(addrs):
        ins = proc.insns[a]
        if ins.mnemonic != "ret" or not ins.operands and proc.ret_pop:
            continue
        for j in range(i - 1, max(i - 10, -1), -1):
            x = proc.insns[addrs[j]]
            if x.mnemonic == "call" or x.mnemonic.startswith("j"):
                break
            ops = x.operands
            if ops and ops[0].type == X.X86_OP_MEM and ops[0].mem.segment == 0 and \
                    x.mnemonic not in ("cmp", "test", "push", "fld", "fild"):
                # a store: still the procedure's statements (End With's and [ebp-8], 0, then fstp
                # [ecx + eax * 4 + 0ACh]: its index temporary taken for a Function's value,
                # DarwinBots SetDefaultLengths)
                break
            # a Double/Date Function: "fld qword ptr [ebp-N]" among the restores
            if x.mnemonic == "fld" and ops and ops[0].type == X.X86_OP_MEM and \
                    ops[0].mem.base == X.X86_REG_EBP and ops[0].mem.disp < 0:
                return ops[0].mem.disp
            # eax, or ax / al for a Boolean / Integer / Byte Function
            # ("mov ax, word ptr [ebp-0x44]", gh_qr QRCodegenEncodeSegments)
            if x.mnemonic in ("mov", "movzx", "movsx") and ops[0].type == X.X86_OP_REG and \
               ops[0].reg in (X.X86_REG_EAX, X.X86_REG_AX, X.X86_REG_AL):
                if ops[1].type == X.X86_OP_MEM and ops[1].mem.base == X.X86_REG_EBP \
                        and ops[1].mem.disp < 0:
                    return ops[1].mem.disp
                break
    return None


def apidb_array_at(name, off):
    from . import apidb
    return apidb.array_at(name, off)


class BoundsAt:
    """Lifter.index_bounds as it stood at address va, for folds after the lift: by then it holds the
    last check of each subscript in the procedure, which may be another array's (For v30 = 0 To 10
    of .Ties(v30), 11 elements, and a check for 15 of a later loop: too long to fit, the record lost
    its array.  Optimised code checks a subscript once for several accesses.)"""

    def __init__(self, log, va):
        self.log, self.va = log, va

    def get(self, key, default=None):
        # (close by, or it is the check of another array: i checked for .mem(i) before .Ties(i) was
        # lifted through a register, and the Ties were 1001 long)
        best = None
        for v, n in self.log.get(key, ()):
            if self.va - 0x40 <= v <= self.va and (best is None or v >= best[0]):
                best = (v, n)
        return best[1] if best is not None else default


RESULT_SLOT = -0x7000                   # an 8-byte result held only in registers has no frame slot


def small_record_return(proc, seen=()):
    """(frame slot, size) of a module Function returning a record of up to 8 bytes: VB leaves it in
    eax, or edx:eax, loaded from the result's frame slot at the exit (t84recret; DarwinBots
    VectorAdd lost its y).  A Currency comes back the same way; what fills the slot tells them
    apart.  None for anything else."""
    cached = proc.__dict__.get("_small_rec", False)
    if cached is not False:
        return cached
    if proc in seen:
        return None
    r = _small_record_return(proc, seen)
    proc._small_rec = r
    return r


EDX_READS = ("edx", "dx", "dl", "dh")


def pair_callers(model):
    """Procedure VA -> what its direct callers do with edx right after the call, for those that read
    it at all.  edx does not survive a call, so reading it means the callee left half a value there:
    an 8-byte value in edx:eax, a Currency or a small record (optimised code forwards both halves
    straight from registers and parameters, t85recretno VSet, so the callee alone may not show it).
      "value"  pushed with eax: passed by value, which only a Currency can be
      "copy"   stored and copied with __vbaCopyBytes, which VB does only to records
      "read"   neither"""
    cached = model.__dict__.get("_pair_callers")
    if cached is not None:
        return cached
    out = model._pair_callers = {}
    rank = {"read": 0, "copy": 1, "value": 2}
    for p in model.procs.values():
        addrs = sorted(p.insns)
        for i, a in enumerate(addrs):
            ins = p.insns[a]
            if ins.mnemonic != "call" or ins.operands[0].type != X.X86_OP_IMM or \
                    ins.operands[0].imm not in model.procs:
                continue
            how = None
            for j in range(i + 1, min(i + 16, len(addrs))):
                x = p.insns[addrs[j]]
                m, s = x.mnemonic, x.op_str
                if m.startswith("j") or m == "ret":
                    break
                if m == "call":
                    y = p.insns[addrs[j - 1]]
                    if how == "read" and y.mnemonic == "push" and y.op_str == "8":
                        how = "copy"        # __vbaCopyBytes(8, &dst, &src), its last push the 8
                    break
                if how is not None:
                    continue
                if m in ("cdq", "mul", "div", "idiv") or m == "imul" and "," not in s:
                    break                   # (edx written: imul ecx for x \ 3, and GetMin got a record)
                if not any(r in re.split(r"\W+", s) for r in EDX_READS):
                    continue
                first, _, rest = s.partition(",")
                if m in ("mov", "lea", "movzx", "movsx") and first.strip() in EDX_READS and \
                        not any(r in re.split(r"\W+", rest) for r in EDX_READS):
                    break                   # written: whatever it held is gone
                if m == "xor" and first.strip() == rest.strip() or m == "pop":
                    break
                how = "value" if m == "push" else "read"
            if how is not None and rank[how] >= rank[out.get(ins.operands[0].imm, "read")]:
                out[ins.operands[0].imm] = how
    return out


def exit_source(proc, addrs, i, reg):
    """What register reg holds at the ret at addrs[i], going back through the code in address order
    with esp followed from the ret: ("frame", esp0 offset), ("imm", v), ("call", ins), ("mem", op),
    or None when a jump comes first.  Copies between registers are followed."""
    esp = 0
    for j in range(i - 1, max(i - 80, -1), -1):
        x = proc.insns[addrs[j]]
        m, ops = x.mnemonic, x.operands
        if m == "call":
            return ("call", x) if reg in ("eax", "edx") else None
        if m == "ret" or m == "jmp":
            return None
        if m.startswith("j"):
            continue                        # (a check going off to the error call; or a join that
                                            # the lifter sees through and this does not)
        if m in ("add", "sub") and ops[0].type == X.X86_OP_REG and ops[0].reg == X.X86_REG_ESP and \
                ops[1].type == X.X86_OP_IMM:
            esp += -ops[1].imm if m == "add" else ops[1].imm
            continue
        if m == "pop":
            esp -= 4
            if ops[0].type == X.X86_OP_REG and REG32.get(ops[0].reg) == reg:
                return None
            continue
        if m == "push":
            esp += 4
            continue
        if not ops or ops[0].type != X.X86_OP_REG or REG32.get(ops[0].reg) != reg or ops[0].size != 4:
            continue
        if m == "xor" and ops[1].type == X.X86_OP_REG and ops[1].reg == ops[0].reg:
            return ("imm", 0)
        if m != "mov":
            return ("other", x)
        o = ops[1]
        if o.type == X.X86_OP_IMM:
            return ("imm", o.imm)
        if o.type == X.X86_OP_REG:
            reg = REG32.get(o.reg)
            continue
        if o.mem.base == X.X86_REG_ESP and o.mem.index == 0:
            return ("frame", esp + o.mem.disp)
        return ("mem", o)
    return None


def _frameless_pair(proc):
    """(frame slot, size) of a record Function of optimised code without a frame pointer, whose
    halves may come from the frame, from registers or straight from its parameters
    (lift.frameless_forward says which).  None if the eax:edx there are no record's."""
    use = getattr(proc, "pair_use", {}).get(proc.va)
    addrs = sorted(proc.insns)
    rets = [i for i, a in enumerate(addrs) if proc.insns[a].mnemonic == "ret"]
    slots, forward, cy = set(), set(), False
    size = 8 if use else None
    for i in rets:
        lo, hi = exit_source(proc, addrs, i, "eax"), exit_source(proc, addrs, i, "edx")
        for s in (lo, hi):
            if s is not None and s[0] == "call":
                t = s[1].operands[0]
                callee = getattr(proc, "model_procs", {}).get(t.imm) if t.type == X.X86_OP_IMM else None
                if callee is None:
                    cy = True               # from the runtime: __vbaCyMul...
        lo_slot = lo is not None and lo[0] == "frame" and lo[1] < 0
        hi_slot = hi is not None and hi[0] == "frame" and hi[1] < 0
        if lo_slot and hi_slot and hi[1] == lo[1] + 4:
            slots.add(lo[1] + 4)            # (slot_var's offsets: ebp == esp0 - 4)
            size = 8
        elif lo_slot and hi_slot:
            return None
        elif hi_slot:
            slots.add(hi[1])
            forward.add("eax")
            size = 8
        elif lo_slot:
            slots.add(lo[1] + 4)
            if use:
                forward.add("edx")
            elif size is None:
                size = 4
        else:
            forward.update(("eax", "edx"))
    if use == "value" or cy:
        return None                         # a Currency
    if size is None or not rets:
        return None
    if size == 4 and not _narrow_stores(proc, addrs, next(iter(slots)) if slots else None):
        return None                         # a dword: a Long, or a record nothing shows
    if use is None and size == 8 and forward:
        return None
    if len(slots) > 1:
        return None
    proc._pair_forward = forward
    return (slots.pop() if slots else RESULT_SLOT), size


def _narrow_stores(proc, addrs, slot):
    """Does the frame slot (slot_var's offset) get a word or byte store?  (short1: two Integers.)"""
    if slot is None:
        return False
    esp = 0
    for a in addrs:
        x = proc.insns[a]
        m, ops = x.mnemonic, x.operands
        if m == "push":
            esp -= 4
        elif m == "pop":
            esp += 4
        elif m in ("add", "sub") and ops[0].type == X.X86_OP_REG and ops[0].reg == X.X86_REG_ESP \
                and ops[1].type == X.X86_OP_IMM:
            esp += ops[1].imm if m == "add" else -ops[1].imm
        elif m == "call" or m == "ret":
            return False
        elif m == "mov" and ops[0].type == X.X86_OP_MEM and ops[0].mem.base == X.X86_REG_ESP and \
                ops[0].mem.index == 0 and ops[0].size in (1, 2):
            if 0 <= esp + ops[0].mem.disp + 4 - slot < 4:
                return True
    return False


def _small_record_return(proc, seen):
    addrs = sorted(proc.insns)
    if not proc.seh and not uses_frame(proc):
        return _frameless_pair(proc)
    slot = hi = None
    for i, a in enumerate(addrs):
        if proc.insns[a].mnemonic != "ret":
            continue
        for j in range(i - 1, max(i - 10, -1), -1):
            x = proc.insns[addrs[j]]
            if x.mnemonic == "call" or x.mnemonic.startswith("j"):
                break
            ops = x.operands
            if x.mnemonic == "mov" and ops[0].type == X.X86_OP_REG and ops[1].type == X.X86_OP_MEM \
                    and ops[1].mem.base == X.X86_REG_EBP and ops[1].mem.disp < 0 and ops[1].size == 4:
                # the nearest to the ret, and edx right after it: anything else in edx is the
                # work before (WI_fragSum: mov edx, [ebp-8]; sub...; mov [ebp-8], edx)
                if ops[0].reg == X.X86_REG_EAX:
                    slot = ops[1].mem.disp
                    y = proc.insns[addrs[j + 1]]
                    o = y.operands
                    if y.mnemonic == "mov" and o[0].type == X.X86_OP_REG and o[0].reg == X.X86_REG_EDX \
                            and o[1].type == X.X86_OP_MEM and o[1].mem.base == X.X86_REG_EBP:
                        hi = o[1].mem.disp
                    break
        if slot is not None:
            break
    if slot is None or (hi is not None and hi != slot + 4):
        return None
    size = 8 if hi is not None else 4

    def writes(ins):
        """The frame offset a store goes to, with its width."""
        if len(ins.operands) >= 1 and ins.operands[0].type == X.X86_OP_MEM and \
                ins.operands[0].mem.base == X.X86_REG_EBP and \
                ins.operands[0].mem.index in (0, X.X86_REG_INVALID) and \
                ins.mnemonic in ("mov", "fstp", "fst", "fistp", "fist", "and", "or"):
            return ins.operands[0].mem.disp, ins.operands[0].size
        return None, None
    hi_src = lo_src = None
    for i, a in enumerate(addrs):
        ins = proc.insns[a]
        off, w = writes(ins)
        if ins.mnemonic == "lea" and size == 8 and ins.operands[1].mem.base == X.X86_REG_EBP and \
                ins.operands[1].mem.disp == slot:
            return slot, size           # its address taken: copied as a whole (VPass = v)
        if off is None or not slot <= off < slot + size:
            continue
        if w in (1, 2):
            return slot, size           # (a field narrower than a dword)
        if ins.mnemonic in ("fstp", "fst") and w == 4:
            return slot, size           # (a Single)
        if ins.mnemonic in ("fstp", "fst", "fistp", "fist"):
            return None                 # (the 8 bytes as one number)
        if ins.mnemonic == "mov" and ins.operands[1].type in (X.X86_OP_REG, X.X86_OP_IMM):
            src = ins.operands[1]
            how = None
            if src.type == X.X86_OP_IMM:
                how = ("imm",)
            else:
                for j in range(i - 1, max(i - 8, -1), -1):
                    y = proc.insns[addrs[j]]
                    if y.mnemonic == "call":
                        how = ("call", y, src.reg)
                        break
                    if y.mnemonic in ("mov", "lea") and y.operands[0].type == X.X86_OP_REG and \
                            y.operands[0].reg == src.reg:
                        how = ("mem", y.operands[1]) if y.operands[1].type == X.X86_OP_MEM else ("other",)
                        break
            if off == slot:
                lo_src = how
            else:
                hi_src = how
    if size == 4:
        return None                     # a dword: a Long, or a record nothing shows
    if hi_src is None:
        return None
    if hi_src[0] == "imm":
        return None if lo_src and lo_src[0] == "imm" else (slot, size)
    if hi_src[0] == "call":
        if hi_src[2] == X.X86_REG_EAX:
            return slot, size           # (the upper half a call's eax: no 8-byte value, LLStr.b = Len(s))
        t = hi_src[1].operands[0]
        callee = None
        if t.type == X.X86_OP_IMM and getattr(proc, "model_procs", None):
            callee = proc.model_procs.get(t.imm)
        if callee is None:
            return None                 # (from the runtime: __vbaCyMul...)
        callee.model_procs = proc.model_procs
        return (slot, size) if small_record_return(callee, tuple(seen) + (proc,)) else None
    if hi_src[0] == "mem" and lo_src and lo_src[0] == "mem":
        h, l = hi_src[1].mem, lo_src[1].mem
        if h.base == l.base and h.disp == l.disp + 4 and not (h.base == X.X86_REG_EBP and h.disp > 0):
            return None                 # the two halves of one 8-byte value, through a pointer or
                                        # in the frame; consecutive arguments are two values
    return slot, size


def fixed_string_record(rec):
    """n when record descriptor rec describes one String * n and nothing
    else (its size is the string's), else None."""
    if rec is None or len(rec.fields) != 1 or 0 not in rec.fields:
        return None
    f = rec.fields[0]
    if not isinstance(f, str) or not f.startswith("String * "):
        return None
    try:
        n = int(f.split("*")[1])
    except ValueError:
        return None
    return n if rec.size in (2 * n, (2 * n + 3) & ~3) else None


def ir_line_number(n):
    """A line of its own with just the number: Erl goes by the last one before a failing statement."""
    c = Call(str(n), [])
    c.stmt_fmt = str(n)
    return c


def no_result(proc):
    """A procedure with VB's exception frame whose final epilogue (from the
    continuation after the cleanup's ret to the ret that pops the frame)
    sets no eax / ax / al and loads no FPU value, and that returns nothing
    through a result pointer: a Sub, whatever callers seem to read from eax
    after calling it - eax is what the cleanup's last call left (a target
    Proc_829720 and Proc_EE9580 came out As Long Functions returning
    '<fs0> and an unset v2C: one caller's eax looked live across a join)."""
    if not proc.seh or getattr(proc, "float_ret", False) or variant_return(proc) is not None \
            or result_slot(proc) is not None:
        return False
    addrs = sorted(proc.insns)
    finals = 0
    for i, a in enumerate(addrs):
        if proc.insns[a].mnemonic != "ret":
            continue
        frame, sets = False, False
        for j in range(i - 1, max(i - 16, -1), -1):
            x = proc.insns[addrs[j]]
            if x.mnemonic in ("call", "ret") or x.mnemonic.startswith("j"):
                break
            if x.mnemonic in ("leave",) or x.mnemonic == "pop" and x.op_str == "ebp":
                frame = True
            if x.mnemonic.startswith("fld") or x.mnemonic.startswith("fild"):
                sets = True
            try:
                written = {x.reg_name(r) for r in x.regs_access()[1]}
            except Exception:
                written = {"eax"}
            if written & {"eax", "ax", "al", "ah"}:
                sets = True
        if frame:
            if sets:
                return False
            finals += 1
    return finals > 0


def cleanup_slots(model, proc, nodes):
    """ebp offsets the exit cleanup routine frees: the procedure's own
    String/Variant/Object variables (temporaries are freed inline)."""
    from .cfg import import_name
    out = set()
    starts = {getattr(n, "cleanup", None) for n in nodes.values()}
    starts.discard(None)
    # "push cont; <cleanup inline>; ret": the cleanup starts after the push
    for a in sorted(proc.insns):
        ins = proc.insns[a]
        if ins.mnemonic == "push" and ins.operands[0].type == X.X86_OP_IMM and \
                ins.operands[0].imm in proc.conts:
            nxt = proc.insns.get(a + ins.size)
            if nxt is not None and nxt.mnemonic != "jmp":
                starts.add(a + ins.size)
    for va in starts:
        pc = va
        leas = []
        regs = {}
        for _ in range(200):
            ins = proc.insns.get(pc)
            if ins is None:
                break
            m = ins.mnemonic
            ops = ins.operands
            if m == "lea" and ops[1].mem.base == X.X86_REG_EBP:
                leas.append(ops[1].mem.disp)
            elif m == "mov" and ops[0].type == X.X86_OP_REG and ops[1].type == X.X86_OP_MEM \
                    and ops[1].mem.base == 0 and ops[1].mem.index == 0:
                regs[ops[0].reg] = model.im.imports.get(ops[1].mem.disp)
            elif m == "call":
                if ops[0].type == X.X86_OP_REG:
                    n = regs.get(ops[0].reg) or ""
                else:
                    n = import_name(model, ins) or ""
                if n.startswith("__vbaFree") or n in ("__vbaAryDestruct", "__vbaRecDestruct"):
                    out.update(d for d in leas if d < 0)
                leas = []
            elif m in ("ret", "jmp"):
                break
            pc += ins.size
    return out


def uses_frame(proc):
    """True when the prologue makes ebp the frame pointer."""
    for a in sorted(proc.insns)[:6]:
        ins = proc.insns[a]
        if ins.mnemonic == "mov" and ins.op_str == "ebp, esp":
            return True
    return False


def method_result(proc):
    """A form/class Function returns through its last argument, a pointer:
    "mov eax,[ebp+last]; mov ecx,[ebp-N]; mov [eax],ecx".  Returns N."""
    last = 4 + proc.ret_pop                 # ebp offset of the last argument
    if proc.ret_pop < 8:
        return None
    blk = method_udt_return(proc)
    if blk is not None:
        return blk[0]
    ptr_regs = {}
    for a in sorted(proc.insns):
        ins = proc.insns[a]
        if ins.mnemonic != "mov" or len(ins.operands) != 2:
            # anything else that writes a register ends what it held ("mov edx,[ebp-148h]"
            # as an array offset, later movsx edx,[ecx+54h]; mov [eax],edx: a target
            # A4 Proc_945AA0's offset slot took the Function's name)
            if ins.mnemonic == "call":
                for r in (X.X86_REG_EAX, X.X86_REG_ECX, X.X86_REG_EDX):
                    ptr_regs.pop(r, None)
            elif ins.operands and ins.operands[0].type == X.X86_OP_REG and \
                    ins.mnemonic not in ("cmp", "test", "push"):
                ptr_regs.pop(ins.operands[0].reg, None)
            continue
        d, s = ins.operands
        if d.type == X.X86_OP_REG and s.type != X.X86_OP_MEM:
            ptr_regs.pop(d.reg, None)       # mov edx, ecx / mov edx, 5
            continue
        if d.type == X.X86_OP_REG and s.mem.base != X.X86_REG_EBP:
            ptr_regs.pop(d.reg, None)       # mov edx, [ecx+54h]
            continue
        if d.type == X.X86_OP_REG and s.type == X.X86_OP_MEM and s.mem.base == X.X86_REG_EBP:
            if s.mem.disp == last:
                ptr_regs[d.reg] = "ptr"
            elif s.mem.disp < 0:
                ptr_regs[d.reg] = s.mem.disp
            else:
                ptr_regs.pop(d.reg, None)
            continue
        if d.type == X.X86_OP_MEM and ptr_regs.get(d.mem.base) == "ptr" and d.mem.disp == 0 \
                and s.type == X.X86_OP_REG and isinstance(ptr_regs.get(s.reg), int):
            slot = ptr_regs[s.reg]
            addrs = sorted(proc.insns)
            i = addrs.index(a)
            # the copy out is in the epilogue; before that it is a ByRef last parameter being
            # set (a target A4 Proc_945AA0 ..., row1 As Long: row1 = v24 made it a Function)
            for b in addrs[i + 1:i + 17]:
                y = proc.insns[b]
                if y.mnemonic == "ret":
                    break
                if y.mnemonic == "call" or y.mnemonic.startswith("j"):
                    slot = None
                    break
            else:
                slot = None
            if slot is None:
                continue
            # and nothing reads through a result pointer or hands it on before that copy (only
            # the zeroing at entry writes it).  Without a frame the last statement r = m looks
            # like the copy; what came before used r (tests/t77 Syvenna)
            for j, c in enumerate(addrs[:i]):
                x = proc.insns[c]
                if x.mnemonic != "mov" or x.operands[0].type != X.X86_OP_REG or \
                        x.operands[1].type != X.X86_OP_MEM or \
                        x.operands[1].mem.base != X.X86_REG_EBP or x.operands[1].mem.disp != last:
                    continue
                r = x.operands[0].reg
                for e in addrs[j + 1:j + 5]:
                    y = proc.insns[e]
                    if y.mnemonic == "push" and y.operands[0].type == X.X86_OP_REG and \
                            y.operands[0].reg == r:
                        return None
                    if y.mnemonic == "mov" and y.operands[1].type == X.X86_OP_MEM and \
                            y.operands[1].mem.base == r:
                        return None
                    if y.operands and y.operands[0].type == X.X86_OP_REG and y.operands[0].reg == r:
                        break
            # "mov [eax+4], reg" from [ebp-N+4] right after: an 8-byte result
            for b in addrs[i + 1:i + 5]:
                y = proc.insns[b]
                if y.mnemonic == "mov" and len(y.operands) == 2:
                    yd, ys = y.operands
                    if yd.type == X.X86_OP_REG and ys.type == X.X86_OP_MEM and \
                            ys.mem.base == X.X86_REG_EBP:
                        ptr_regs[yd.reg] = ys.mem.disp if ys.mem.disp < 0 else None
                    elif yd.type == X.X86_OP_MEM and ptr_regs.get(yd.mem.base) == "ptr" and \
                            yd.mem.disp == 4 and ys.type == X.X86_OP_REG and \
                            ptr_regs.get(ys.reg) == slot + 4:
                        proc.result8 = True
                        break
            return slot
    return None


def method_udt_return(proc):
    """A form/class Function returning a UDT: the epilogue block-copies the
    result local through the last argument, "mov edi,[ebp+last];
    mov ecx,N; lea esi,[ebp-X]; rep movsd" (gh_zip pvVfsOpen).
    Returns (X, bytes) or None."""
    if getattr(proc, "_mudt", 0) != 0:
        return proc._mudt
    proc._mudt = None
    last = 4 + proc.ret_pop
    if proc.ret_pop < 8:
        return None
    addrs = sorted(proc.insns)
    for i, a in enumerate(addrs):
        ins = proc.insns[a]
        if not ins.mnemonic.startswith("rep movs"):
            continue
        ptr, src, n = False, None, None
        for j in range(max(0, i - 5), i):
            x = proc.insns[addrs[j]]
            ops = x.operands
            if len(ops) != 2 or ops[0].type != X.X86_OP_REG:
                continue
            if x.mnemonic == "mov" and ops[0].reg == X.X86_REG_EDI and \
                    ops[1].type == X.X86_OP_MEM and ops[1].mem.base == X.X86_REG_EBP and \
                    ops[1].mem.disp == last:
                ptr = True
            elif x.mnemonic == "lea" and ops[0].reg == X.X86_REG_ESI and \
                    ops[1].mem.base == X.X86_REG_EBP and ops[1].mem.disp < 0:
                src = ops[1].mem.disp
            elif x.mnemonic == "mov" and ops[0].reg == X.X86_REG_ECX and \
                    ops[1].type == X.X86_OP_IMM:
                n = ops[1].imm
        if ptr and src is not None and n:
            step = {"d": 4, "w": 2, "b": 1}[ins.mnemonic[-1]]
            proc._mudt = (src, n * step)
            return proc._mudt
    return None


def record_desc_of(model, proc, slot):
    """Record descriptor VA __vbaRecDestruct releases the frame slot with."""
    addrs = sorted(proc.insns)
    for i, a in enumerate(addrs):
        ins = proc.insns[a]
        if ins.mnemonic != "call" or "RecDestruct" not in (cfgmod.call_name(model, proc, ins)
                                                            or ""):
            continue
        desc, hit = None, False
        for j in range(max(0, i - 5), i):
            x = proc.insns[addrs[j]]
            if x.mnemonic == "push" and x.operands[0].type == X.X86_OP_IMM:
                desc = x.operands[0].imm
            if x.mnemonic == "lea" and x.operands[1].type == X.X86_OP_MEM and \
                    x.operands[1].mem.base == X.X86_REG_EBP and x.operands[1].mem.disp == slot:
                hit = True
        if hit and desc:
            return desc
    return None


def seh_bookkeeping(proc):
    """Bytes below ebp used by the SEH frame (handler, saved esp, procdesc)."""
    if not proc.seh:
        return 0
    pushes = 0
    for a in sorted(proc.insns)[:12]:
        ins = proc.insns[a]
        if ins.mnemonic == "sub" and ins.operands[0].type == X.X86_OP_REG and \
           ins.operands[0].reg == X.X86_REG_ESP and ins.operands[1].type == X.X86_OP_IMM:
            return ins.operands[1].imm + 8
        if ins.mnemonic == "push" and ins.operands[0].type == X.X86_OP_REG and \
                ins.operands[0].reg == X.X86_REG_ECX:
            pushes += 1                 # unoptimised code: push ecx for each dword
        elif pushes:
            return 4 * pushes + 8
    return 0x14


def prologue_esp(proc):
    """esp (relative to entry) once the prologue has set up the frame.

    Anything pushed below this is call-argument setup.  An SEH frame
    records the bottom itself with "mov [ebp-N], esp".
    """
    esp = 0
    chk = None                  # "mov eax, N; call __vbaChkstk" allocates N bytes
    made = False                # the frame has its bytes: more is a Variant passed by value
    addrs = sorted(proc.insns)[:48]
    for k, a in enumerate(addrs):
        ins = proc.insns[a]
        m, ops = ins.mnemonic, ins.operands
        if m == "push" and ops[0].type == X.X86_OP_REG and ops[0].reg == X.X86_REG_ECX and \
                all(proc.insns[b].mnemonic == "push" for b in addrs[:k]):
            # ecx means nothing on entry: pushing it is a frame slot, the short sub esp, 4.
            # As an argument area, a store to the slot was lost while its address went to
            # a call (a target Proc_730600: Proc_8B3660 a2, 5 read into record 0)
            esp -= 4
            continue
        if m == "mov" and ops[0].type == X.X86_OP_REG and ops[0].reg == X.X86_REG_EAX and \
                ops[1].type == X.X86_OP_IMM:
            chk = ops[1].imm
            continue
        if m == "push" and ops[0].type == X.X86_OP_IMM and k + 1 < len(addrs):
            nxt = proc.insns[addrs[k + 1]]
            if nxt.mnemonic == "pop" and nxt.operands[0].type == X.X86_OP_REG and \
                    nxt.operands[0].reg == X.X86_REG_EAX:
                chk = ops[0].imm        # "push N; pop eax": the short mov eax, N
                continue                # (unoptimised code, VBDoom)
        if m == "pop" and chk is not None and ops[0].type == X.X86_OP_REG and \
                ops[0].reg == X.X86_REG_EAX:
            continue
        if m == "call" and chk is not None:
            if made and not proc.seh:
                # (t77 o.Kaksi 1, 2 as the first statement: the argument's 16 bytes were
                # counted as frame, one of its dwords was lost and printed '<arg9>, which
                # commented out the rest of the line)
                return esp
            esp -= chk
            chk = None
            made = True
            continue
        if m == "push":
            if not proc.seh and not (ops[0].type == X.X86_OP_REG and ops[0].reg in (
                    X.X86_REG_EBX, X.X86_REG_EBP, X.X86_REG_ESI, X.X86_REG_EDI)):
                break                   # an argument: the prologue is over
            esp -= 4
            continue
        if m == "sub" and ops[0].type == X.X86_OP_REG and ops[0].reg == X.X86_REG_ESP \
                and ops[1].type == X.X86_OP_IMM:
            if made and not proc.seh:
                return esp
            esp -= ops[1].imm
            made = True
            continue
        if m == "mov" and ops[0].type == X.X86_OP_MEM and ops[0].mem.base == X.X86_REG_EBP \
                and ops[1].type == X.X86_OP_REG and ops[1].reg == X.X86_REG_ESP:
            return esp
        if m == "and" and ops[0].type == X.X86_OP_REG and ops[0].reg == X.X86_REG_ESP:
            # (and esp, 0FFFFFFF8h: fast code aligns the frame for Doubles, kept esp-relative.
            # Stopping here left every local below the frame, as arguments: DarwinBots Qpow a_2)
            continue
        if m in ("mov", "lea", "xor", "cld", "nop"):
            continue
        break
    return esp


class ControlArray:
    """What a form's control getter returns for a control array: an object
    of VB's own (__vbaHresultCheckObj gets GUID_NULL for it) whose vtable
    has Item(Index) at 40h, then LBound, UBound and Count (tests_wip/t10carr).
    Item is the default member: chk(i)."""
    kind = 4
    lib = None
    guid = "{00000000-0000-0000-0000-000000000000}"

    def __init__(self, element, name):
        from .typelib import Func, Param, TypeDesc, PARAMFLAG_FIN, PARAMFLAG_FOUT, \
            PARAMFLAG_FRETVAL
        self.name = name + "()"
        out = PARAMFLAG_FOUT | PARAMFLAG_FRETVAL

        def f(name, vtoff, params, iface=None):
            fn = Func()
            fn.name, fn.memid, fn.vtoff, fn.invkind = name, vtoff, vtoff, 2
            fn.params = params
            if iface is not None:
                fn.ret_iface_obj = iface
            return fn
        self.funcs = [
            f("", 0x40, [Param("Index", TypeDesc(3), PARAMFLAG_FIN),
                         Param("ret", TypeDesc(26, TypeDesc(9)), out)], element),
            f("LBound", 0x44, [Param("ret", TypeDesc(26, TypeDesc(2)), out)]),
            f("UBound", 0x48, [Param("ret", TypeDesc(26, TypeDesc(2)), out)]),
            f("Count", 0x4C, [Param("ret", TypeDesc(26, TypeDesc(2)), out)])]

    def all_funcs(self):
        return self.funcs

    def bases(self):
        return []

    def by_vtoff(self, off, invkind=None):
        for fn in self.funcs:
            if fn.vtoff == off and (invkind is None or fn.invkind == invkind):
                return fn
        return None

