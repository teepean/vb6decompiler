#!/usr/bin/env python3
"""vb6dec - decompile a natively compiled Visual Basic 6 executable."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from vb6dec.model import Model
from vb6dec.lift import Lifter, param_base
from vb6dec import simplify, render


_ph_t = None
LAST_MODEL = None       # (for run_vb6dec.py's report)


def phase(name):
    """VB6DEC_PHASES=1: wall and CPU seconds since the last phase, peak memory (stderr)."""
    if not os.environ.get("VB6DEC_PHASES"):
        return
    global _ph_t
    import time, resource
    now = time.perf_counter(), time.process_time()
    if _ph_t is not None:
        ru = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        rss = int(open("/proc/self/statm").read().split()[1]) * 4096 >> 20
        print("PHASE %-28s %8.2f s  cpu %8.2f  rss %6d MB  peak %6d MB" % (
            name, now[0] - _ph_t[0], now[1] - _ph_t[1], rss, ru >> 10), file=sys.stderr, flush=True)
    _ph_t = now


def main(argv):
    if not argv:
        print(__doc__.strip() + "\n\nusage: decompile.py [--detect] [--nomap] [--snapshot] EXE [MAPFILE] [-o DIR]")
        return 2
    if "--detect" in argv:                  # what the file is, and nothing more
        from vb6dec import detect
        for path in [a for a in argv if a != "--detect"]:
            print(path)
            print("\n".join(detect.lines(detect.detect(path))))
        return 0
    outdir = None
    nomap = "--nomap" in argv               # decompile as if no linker map existed
    snap = "--snapshot" in argv             # start from first_pass's saved result (snapshot.py)
    argv = [a for a in argv if a not in ("--nomap", "--snapshot")]
    if "-o" in argv:
        i = argv.index("-o")
        outdir = argv[i + 1]
        argv = argv[:i] + argv[i + 2:]
    exe = argv[0]
    mp = argv[1] if len(argv) > 1 else None
    # Python's cycle collector re-scans the growing IR (millions of nodes)
    # again and again: VBDoom 19.3 s -> 14.9 s without it, for 825 -> 956 MB
    # at the peak (little of it is cyclic garbage)
    import gc
    gc.disable()
    if mp is None and not nomap:
        cand = os.path.splitext(exe)[0] + ".map"
        mp = cand if os.path.exists(cand) else None
    phase("start")
    if snap:
        m, bodies = first_pass_snapshot(exe, mp)
        phase("first pass (snapshot)")
    else:
        global LAST_MODEL
        m = LAST_MODEL = Model(exe, mp)
        phase("Model(exe)")
        bodies = first_pass(m)
    prj = m.prj
    for va, (lf, body) in bodies.items():
        if lf is None:
            # (they vanish from the output, and their callers don't compile)
            print("decompile: %s at %X failed: %s: %s" % (m.procs[va].name, va, type(body).__name__,
                                                          body), file=sys.stderr)
    if outdir:
        from vb6dec.writer import ProjectWriter
        for va, (lf, body) in bodies.items():      # parameter lists for call sites
            if lf is not None:
                try:
                    render.proc_source(m, lf, body)
                except Exception:
                    pass
        phase("render 5 (main)")
        files = ProjectWriter(m, bodies).write(outdir)
        phase("ProjectWriter.write")
        for kind, fn, name in files:
            print("%-7s %s" % (kind, os.path.join(outdir, fn)))
        return 0
    for va, (lf, body) in bodies.items():          # parameter lists for call sites
        if lf is not None:
            try:
                render.proc_source(m, lf, body)
            except Exception:
                pass
    print("' project %s%s" % (prj.name, "  (map: %s)" % os.path.basename(mp) if mp else ""))
    for o in prj.objects:
        print("\n'=== %s %s %s" % (o.kind, o.name, "=" * 30))
        for c in o.controls:
            cc, _, _ = m.control_class(c)
            print("' control %-14s As %s" % (c.name, cc.name if cc else "?"))
        for p in m.procs.values():
            if owner_of(m, p) is not o:
                continue
            try:
                lf, body = bodies[p.va]
                if lf is None:
                    raise body
                print()
                print(render.proc_source(m, lf, body))
            except Exception as e:
                print("\n' !! %s: %s: %s" % (p.name, type(e).__name__, e))
                if os.environ.get("VB6DEC_DEBUG") and hasattr(e, "tb"):
                    print(e.tb)
    return 0


def first_pass_snapshot(exe, mp=None):
    """(model, bodies) after first_pass, from a snapshot if there is one for this
    EXE and this source (vb6dec/snapshot.py), else made and saved."""
    from vb6dec import snapshot
    p = snapshot.find(exe, mp)
    if p:
        return snapshot.load(p)
    m = Model(exe, mp)
    out = first_pass(m)
    snapshot.save(exe, mp, m, out)
    return m, out


def member_extents(m, out):
    """m.member_extents: {object index: [(base, stride, count)]}, the fixed arrays among each object's
    members as far as its procedures' bounds checks show them.  One array's elements seen at other
    offsets are inside its span (simplify.member_element_parts)."""
    seen = {}
    for va, x in out.items():
        lf = x[0] if isinstance(x, tuple) else None
        if lf is None or lf.obj is None:
            continue
        for k, info in lf.arrays.items():
            # (from 10000h on: slots made up for Static variables, no layout: t87nested, hist(4)
            # and monta at 10010h and 1001Ch)
            if k[0] == "member" and isinstance(k[1], int) and k[1] < 0x10000 and \
                    not info.get("dyn") and info.get("n") and (info.get("size") or 0) >= 2 and \
                    info.get("inline_member"):
                d = seen.setdefault(lf.obj.index, {})
                t = (k[1], info["size"])
                d[t] = max(d.get(t, 0), info["n"])
    ext = {}
    for obj, d in seen.items():
        spans = []
        # (records first: one procedure's reads of a field made it look an array of Longs, and
        # the graph of Charts(10).graf printed m400(21).SetValues)
        for (base, stride), n in sorted(d.items(), key=lambda kv: (-kv[0][1], kv[0][0])):
            if any(s % stride == 0 and b <= base < b + c * s for b, s, c in spans):
                continue
            spans.append((base, stride, n))
        ext[obj] = spans
    m.member_extents = ext


def first_pass(m):
    """Lift everything once, then record which procedures' values callers use."""
    from vb6dec.ir import Call, ExprStmt, Assign, Var
    from vb6dec.lift import method_result
    for p in m.procs.values():
        if p.owner is not None and p.owner.kind != "Module" and method_result(p) is not None \
                and getattr(getattr(p, "member", None), "returns", True):
            m.proc_returns[p.va] = True
    out = {}
    from vb6dec import par
    jobs = par.jobs(m)
    if jobs > 1:
        # (Lifter in order: it numbers the record types and fills the Model as it goes)
        lfs = {}
        for p in m.procs.values():
            try:
                lfs[p.va] = Lifter(m, p).run()
                out[p.va] = None
            except Exception as e:
                out[p.va] = (None, e)
        phase("lift 1: Lifter (one process)")

        def simp(va):
            try:
                return lfs[va], simplify.run(lfs[va])
            except Exception as e:
                return None, e
        got = par.run(m, list(lfs), simp, jobs) or {va: simp(va) for va in lfs}
        out.update(got)
        phase("lift 1: simplify (workers)")
    else:
        for p in m.procs.values():
            try:
                lf = Lifter(m, p).run()
                out[p.va] = (lf, simplify.run(lf))
            except Exception as e:
                out[p.va] = (None, e)
    phase("lift 1 (all)")
    # Functions returning a Single / Double that the disassembly did not show
    # (no fld right before the ret): a caller reads st(0) right after calling
    # them (lift / insns.fpu).  Lift them and their callers again, until no
    # more come up (a chain: TimeElapsed's value is TimerDiff's)
    for _ in range(4):
        new = {va for va in m.__dict__.get("float_callees", set())
               if va in m.procs and not getattr(m.procs[va], "float_ret", False)}
        if not new:
            break
        for va in new:
            m.procs[va].float_ret = True
        again = new | {q.va for q in m.procs.values() if q.calls & new}
        for va in again:
            try:
                lf = Lifter(m, m.procs[va]).run()
                out[va] = (lf, simplify.run(lf))
            except Exception as e:
                out[va] = (None, e)
    phase("float relifts")
    if static_slots(m, out):                # Static locals of classes: lift once more
        for p in m.procs.values():
            try:
                lf = Lifter(m, p).run()
                out[p.va] = (lf, simplify.run(lf))
            except Exception as e:
                out[p.va] = (None, e)
    phase("static_slots + relift")
    pointer_results(m, out)                 # before struct_hints: a result pointer is no struct
    struct_array_params(m, out)
    struct_hints(m, out)
    global_struct_hints(m, out)
    udt_results(m, out)
    share_arrays(m, out)
    member_extents(m, out)
    phase("hints (pointer..share_arrays)")
    # A call whose value only flows out as the caller's own (first-lift) return
    # value - eax left by the caller's last statement - is used only if that
    # caller returns a value itself (VBDoom: InitCheats ends with cht_MakeSeq,
    # which made the Sub cht_MakeSeq a Function)
    # (or kept in eax across a join for that return: AM_Ticker ends with "If
    # followplayer Then AM_doFollowPlayer")
    from vb6dec.ir import Return
    tail_of = {}                            # callee VA -> callers returning its value

    def eax(v):
        return isinstance(v, Var) and v.space == "reg" and (
            v.off == "eax" or isinstance(v.off, tuple) and v.off[0] == "eax")
    def eax_read_after(lf, body, bi, si):
        """Is the eax statement si of block bi writes read before it is
        overwritten - other than by a Return?"""
        idx = {a: i for i, (a, _, _) in enumerate(body)}
        work, seen = [(bi, si + 1)], set()
        while work:
            b, j = work.pop()
            a, ss, br = body[b]
            stop = False
            for s in ss[j:]:
                if isinstance(s, Return):
                    stop = True
                    break
                for e in getattr(s, "exprs", lambda: ())():
                    if not (isinstance(s, Assign) and e is s.dst) and any(eax(x) for x in e.walk()):
                        return True
                if isinstance(s, Assign) and eax(s.dst):
                    stop = True
                    break
            if stop:
                continue
            if br is not None and any(eax(x) for x in br.cond.walk()):
                return True
            for t in simplify.node_succs(lf, a):
                if t in idx and idx[t] not in seen:
                    seen.add(idx[t])
                    work.append((idx[t], 0))
        return False
    def only_returned(body, s):
        """Nothing reads the temporary s sets but the Return that leaves eax as it is.  (A Sub whose
        last statement is another Sub's call on one way out: UpdateMoveStats' UpdateContHistStats
        made that one a Function, with eax printed as its value, ChessBrainVB.)"""
        t = s.dst.key()
        for _, ss, br in body:
            for o in ss:
                if o is s or isinstance(o, Return):
                    continue
                for e in getattr(o, "exprs", lambda: ())():
                    if not (isinstance(o, Assign) and e is o.dst) and \
                            any(isinstance(x, Var) and x.key() == t for x in e.walk()):
                        return False
            if br is not None and any(isinstance(x, Var) and x.key() == t for x in br.cond.walk()):
                return False
        return True
    for va, (lf, body) in out.items():
        if lf is None:
            continue
        deferrable = lf.result_slot is None and not m.proc_returns.get(va)
        for bi, (_, ss, br) in enumerate(body):
            for si, s in enumerate(ss):
                top = s.e if isinstance(s, ExprStmt) else None
                tail = deferrable and (isinstance(s, Return) or isinstance(s, Assign) and
                                       isinstance(s.src, Call) and
                                       (eax(s.dst) and not eax_read_after(lf, body, bi, si) or
                                        isinstance(s.dst, Var) and s.dst.space == "tmp" and
                                        only_returned(body, s)))
                for e in getattr(s, "exprs", lambda: ())():
                    for x in e.walk():
                        if isinstance(x, Call) and isinstance(x.raw, int) and x is not top:
                            # (the outermost call only: PushIntStack BitToNumber(v) as the
                            # last statement uses BitToNumber's value, DarwinBots)
                            if tail and x is e:
                                tail_of.setdefault(x.raw, set()).add(va)
                            else:
                                m.proc_returns[x.raw] = True
            if br is not None:
                for x in br.cond.walk():
                    if isinstance(x, Call) and isinstance(x.raw, int):
                        m.proc_returns[x.raw] = True
    changed = True
    while changed:
        changed = False
        for callee, callers in tail_of.items():
            if not m.proc_returns.get(callee) and any(m.proc_returns.get(c) for c in callers):
                m.proc_returns[callee] = True
                changed = True
    # no caller of an AddressOf target is in the program to say it reads eax: CallWindowProc(AddressOf
    # Cb, ...) printed the address it had left there (t66addrmin).  A Sub made a Function gives back
    # what eax held, as the Sub did.
    for p in m.procs.values():
        for t in getattr(p, "addressof", ()):
            if t in m.procs:
                m.proc_returns[t] = True
    # a member descriptor knows: a Sub returns nothing, whatever slot looks like
    # a result (a target Form2.PrintPage(obj): its 12 callers' only
    # argument was taken for the result's out-pointer and dropped)
    from vb6dec.lift import no_result
    for p in m.procs.values():
        mem = getattr(p, "member", None)
        if mem is not None and not mem.returns:
            m.proc_returns.pop(p.va, None)
        elif mem is None and (p.owner is None or p.owner.kind == "Module") and \
                m.proc_returns.get(p.va) and no_result(p):
            m.proc_returns.pop(p.va, None)  # (its own epilogue says Sub: lift.no_result)
    m.returns_known = True
    phase("proc_returns scan")
    for va, (lf, body) in out.items():      # parameter lists for the struct prepass
        if lf is not None:
            try:
                render.proc_source(m, lf, body)
            except Exception:
                pass
    phase("render 1")
    m.proc_arrays = proc_arrays(m, out)     # needs the parameter lists just rendered
    phase("proc_arrays 1")
    m.object_slots = object_slots(m, out)
    # second lift: COM callers can now bind method results
    def lift2(va):
        try:
            lf = Lifter(m, m.procs[va]).run()
            return lf, simplify.run(lf)
        except Exception as e:
            import traceback
            e.tb = traceback.format_exc()
            return None, e
    got = par.run(m, list(m.procs), lift2, jobs) if jobs > 1 else None
    if got is not None:
        out.update(got)
    else:
        for p in m.procs.values():
            out[p.va] = lift2(p.va)
    phase("lift 'second' (all)")
    # element types learnt in the second lift travel along the calls once more
    m.proc_arrays = proc_arrays(m, out)
    by_proc = {}                            # (one walk of proc_arrays, not one per procedure)
    for (pva, key), info in m.proc_arrays.items():
        by_proc.setdefault(pva, []).append((key, info))
    for va, (lf, body) in out.items():
        if lf is None:
            continue
        for key, info in by_proc.get(va, ()):
            cur = lf.arrays.setdefault(key, {"size": None, "dyn": True, "lb": None, "n": None,
                                             "var": Var(key[0], key[1])})
            for kk, vv in info.items():
                if cur.get(kk) is None:
                    cur[kk] = vv
            if info.get("udt") and not lf.udt.get(key):
                lf.udt[key] = info["udt"]
    phase("proc_arrays 2 + merge")
    for va, (lf, body) in out.items():      # plists again, now final
        if lf is not None:
            try:
                render.proc_source(m, lf, body)
            except Exception:
                pass
    phase("render 2")
    from vb6dec import udt
    udt.prepass(m, out)
    phase("udt.prepass 4 rounds")
    # once more: parameter types the struct types just decided (a field
    # passed ByRef As Integer) feed back into the struct types
    for va, (lf, body) in out.items():
        if lf is not None:
            try:
                render.proc_source(m, lf, body)
            except Exception:
                pass
    phase("render 3")
    udt.prepass(m, out, rounds=1)
    phase("udt.prepass 1 round")
    # arrays the second lift found (fixed arrays seen only through element
    # addresses: VBDoom player_arrow) get their bounds from the descriptors too
    share_arrays(m, out)
    for va, (lf, body) in out.items():
        if lf is None:
            continue
        for key, info in lf.arrays.items():
            sh = m.shared_arrays.get((None, key)) if key[0] == "global" else None
            if sh and sh.get("fixed_bounds"):
                for kk in ("lb", "n", "dims", "size", "record"):
                    if sh.get(kk) is not None or kk == "dims":
                        info[kk] = sh.get(kk)
    phase("share_arrays final")
    return out


def struct_array_params(m, out):
    """Parameters used as arrays (ReDim, UBound, elements through *p) that
    are also read at a field offset: pointers to a UDT whose first field is a
    dynamic array (VBDoom cheatseq_t: sequence() As Byte, p As Long).  ReDim
    p(..) and ReDim p.field0(..) compile alike; only the other field tells
    them apart.  The next lift reads them as that field."""
    from vb6dec.ir import Deref, Var
    found, params = set(), set()
    for va, (lf, body) in out.items():
        if lf is None:
            continue
        arr = {k[1] for k in lf.arrays if k[0] == "arg"}
        if not arr:
            continue
        params |= {(va, k[1]) for k, info in lf.arrays.items()
                   if k[0] == "arg" and info.get("dyn")}
        for _, ss, br in body:
            for s in ss:
                for e in getattr(s, "exprs", lambda: ())():
                    for x in e.walk():
                        if isinstance(x, Deref) and x.off > 0 and isinstance(x.e, Var) and \
                                x.e.space == "arg" and x.e.off in arr:
                            found.add((va, x.e.off))
    # a parameter given the very variables another one of those is given is
    # one too (VBDoom cht_GetParam reads only cht.sequence, but gets the
    # cheatseq_t variables cht_CheckCheat, which reads cht.p, gets)
    from vb6dec.ir import Call, AddrOf, Const
    from vb6dec import simplify
    given = {}                          # variable (as passed) -> {(callee VA, arg offset)}
    for va, (lf, body) in out.items():
        if lf is None:
            continue
        for _, ss, br in simplify.flatten(lf):
            for s_ in ss:
                for e in getattr(s_, "exprs", lambda: ())():
                    for x in e.walk():
                        if not (isinstance(x, Call) and isinstance(x.raw, int)):
                            continue
                        clf = out.get(x.raw, (None, None))[0]
                        first = param_base(clf) if clf is not None else 4
                        for i, a in enumerate(x.args):
                            if isinstance(a, Const) and isinstance(a.value, int) and \
                                    m.im.section_of(a.value & 0xffffffff) == ".data":
                                v = ("global", a.value & 0xffffffff)
                            elif isinstance(a, AddrOf) and isinstance(a.e, Var):
                                v = (va, a.e.key()) if a.e.space != "global" else a.e.key()
                            else:
                                continue
                            given.setdefault(v, set()).add((x.raw, first + 4 * i))
    changed = True
    while changed:
        changed = False
        for v, targets in given.items():
            if targets & found and not targets <= found:
                for t in targets:
                    if t in params and t not in found:
                        found.add(t)
                        changed = True
    m.udt_array_params = found
    # array parameters, for simplify.fixed_arrays_by_ref (what proc_arrays
    # keeps needs an element type, often found later)
    m.array_params = params - found


def udt_results(m, out):
    """A Function returning a UDT: the caller passes a result buffer as a
    hidden first argument and the callee block-copies its value into it
    (rep movsd / __vbaCopyBytes into *arg) on the way out (t06m MakeRec).
    The body works on a variable of its own: a procedure that also reads or
    writes fields through the pointer has a ByRef UDT parameter instead
    (VBDoom G_BuildTiccmd(cmd As ticcmd_t) starts with cmd = blankcmd)."""
    from vb6dec.ir import ExprStmt, Call, Var, Deref
    from vb6dec import simplify
    for va, (lf, body) in out.items():
        if lf is None or lf.obj is not None:
            continue
        raw = simplify.flatten(lf)
        copies, through = 0, 0
        for _, ss, br in raw:
            for s in ss:
                e = s.e if isinstance(s, ExprStmt) else None
                if isinstance(e, Call) and e.name == "LSet" and e.raw == "__vbaCopyBytes" and \
                        e.args and e.args[0] == Var("arg", 4):
                    copies += 1
                    continue
                for x in (y for ex in getattr(s, "exprs", lambda: ())() for y in ex.walk()):
                    if isinstance(x, Deref) and x.e == Var("arg", 4):
                        through += 1
        if copies and not through:
            lf.p.udt_ret = True


def share_arrays(m, out):
    """Arrays that are object members or globals, as any procedure saw them
    (bounds, element size): every procedure's next lift starts out knowing
    them, so element addresses in code that only stores are recognised too."""
    shared = {}
    for va, (lf, body) in out.items():
        if lf is None:
            continue
        owner = lf.obj.index if lf.obj is not None else None
        for key, info in lf.arrays.items():
            if key[0] == "member" and owner is not None:
                k = (owner, key)
            elif key[0] == "global":
                k = (None, key)
            else:
                continue
            cur = shared.setdefault(k, {kk: vv for kk, vv in info.items() if kk != "var"})
            for kk, vv in info.items():
                if kk != "var" and cur.get(kk) in (None, False) and vv not in (None, False):
                    cur[kk] = vv
    m.shared_arrays = shared
    global_array_bounds(m, shared)


def global_array_bounds(m, shared):
    """The real bounds of module-level fixed arrays, from the static SAFEARRAY
    images the runtime copies into their descriptors (Model.global_array_
    templates).  Descriptors follow each other in .data as [u32 vt][SAFEARRAY]
    in template order, in runs: one per module, other variables between them
    (VBDoom: 131 arrays, no single chain explains them all).  Each run is
    anchored on a used array and followed through the templates (unused
    arrays included) as long as no used array falls inside a descriptor and
    the element sizes agree with the accesses (and, for one dimension, the
    counts the code's bounds checks allow); the longest run wins, no template
    serves two runs, and a run must explain two used arrays to be trusted.  Without this a read of LNG_POW2(i - 1) looks like a 1-based
    array (wrong UBound), and an array of 8-byte UDTs like Doubles."""
    from vb6dec import records
    import bisect
    used = {key[1]: info for (o, key), info in shared.items()
            if key[0] == "global" and not info.get("dyn")}
    if not used:
        return
    # exact first: each module's variable table says where every image's
    # array is (Model.global_array_slots); the runs below only for the rest
    exact = m.global_array_slots()
    for slot in [s for s in used if s in exact]:
        cb, dims, vt, ff, img, rdesc = exact[slot]
        info = used.pop(slot)
        info["size"] = cb
        info["lb"], info["n"] = dims[0]
        info["dims"] = dims if len(dims) > 1 else None
        info["fixed_bounds"] = True
        if vt == 0x24:
            info["record"] = True       # elements are UDTs, whatever their size
            rec = records.model_record(m, rdesc) if rdesc else None
            if rec is not None:
                info["udt"] = rec.name  # the record Type (VBDoom menus() As menu_t)
        elif vt in (2, 3, 4, 5, 6, 7, 8, 9, 11, 12, 13, 17) and not info.get("vt"):
            info["vt"] = vt             # the elements' VARTYPE (player_names: Strings)
    if not used:
        return
    known = {e[4] for e in exact.values()}
    tpls = [t for t in m.global_array_templates() if 2 <= t[3] <= 0x24 and t[3] != 24 and
            t[0] not in known]
    slots = sorted(used)

    def inside(lo, hi):
        """A used slot strictly between lo and hi."""
        k = bisect.bisect_right(slots, lo)
        return k < len(slots) and slots[k] < hi

    # what an earlier call decided stays (the second one, for arrays only the
    # second lift found, no longer has the counts the code checks: they are
    # the templates' by then)
    prev = {slot: k for slot, k in getattr(m, "array_template_of", {}).items() if slot in used}
    taken = set(prev.values())          # templates given to a run already

    def run(s0, k0):
        plan, pos, k = {}, s0, k0
        while k < len(tpls) and pos <= slots[-1] and k not in taken:
            va, cb, dims, vt = tpls[k]
            if pos in used:
                sz = used[pos].get("size")
                if sz and sz != cb:
                    break
                total = 1
                for lb, n in dims:
                    total *= n
                nc = used[pos].get("n")
                if nc and len(dims) == 1 and nc != total and not used[pos].get("fixed_bounds"):
                    # the code's bounds checks allow another count (VBDoom
                    # g_game: forwardmove(0 To 1) is checked against 2, the
                    # run had given it angleturn's 3); for several dimensions the
                    # code's count is only a guess at the total
                    break
                plan[pos] = k
            nxt = pos + 4 + 16 + 8 * len(dims)
            if inside(pos, nxt):
                break                       # a used array inside this descriptor
            pos, k = nxt, k + 1
        return plan
    # (modules need not lie in .data in the order of their templates in
    # .text: any template not taken yet can start a run - VBDoom g_game's
    # were behind another module's run, so its arrays got shifted bounds)
    assigned = dict(prev)
    for s0 in slots:
        if s0 in assigned:
            continue
        best = {}
        for k0 in range(len(tpls)):
            if k0 in taken:
                continue
            sz = used[s0].get("size")
            if sz and sz != tpls[k0][1]:
                continue
            plan = run(s0, k0)
            if len(plan) > len(best):
                best = plan
        if len(best) < 2:
            continue                        # one array alone: any template of its size fits
        assigned.update(best)
        taken.update(best.values())
    m.array_template_of = assigned
    for slot, k in assigned.items():
        va, cb, dims, vt = tpls[k]
        info = shared[(None, ("global", slot))]
        info["size"] = cb
        info["lb"], info["n"] = dims[0]
        info["dims"] = dims if len(dims) > 1 else None
        info["fixed_bounds"] = True
        if vt == 0x24:
            info["record"] = True       # VT_RECORD: elements are UDTs, whatever their size
        elif vt in (2, 3, 4, 5, 6, 7, 8, 9, 11, 12, 13, 17) and not info.get("vt"):
            info["vt"] = vt             # the elements' VARTYPE


def object_slots(m, out):
    """The variables holding a Picture or a Font, across the program.  VB reads their members by
    DISPID (__vbaLateIdCallLd), and nothing at the read says what the object is: m78.Width in
    a target's UserControl1, m78 set from a Picture parameter, or a6.Type in a procedure
    it is passed to.  {("member", object index, offset), ("arg", va, offset), ("local" or "stk",
    va, slot): "Picture" or "Font"}."""
    from vb6dec import ir
    from vb6dec.types import param_args
    classes = {"Picture": "Picture", "StdPicture": "Picture", "IPictureDisp": "Picture",
               "Font": "Font", "StdFont": "Font", "IFontDisp": "Font"}
    found = {}
    for va, plist in m.plists.items():
        for off, _, t, _ in plist:
            if t in classes:
                found[("arg", va, off)] = classes[t]

    def slot(lf, e):
        if isinstance(e, ir.AddrOf):
            e = e.e
        if isinstance(e, ir.Deref) and e.off == 0:
            e = e.e
        if not isinstance(e, ir.Var):
            return None
        if e.space == "member" and lf.p.owner is not None:
            return ("member", lf.p.owner.index, e.off)
        if e.space in ("arg", "stk"):
            return (e.space, lf.p.va, e.off)
        if e.space == "local":
            return ("local", lf.p.va, e.off[0] if isinstance(e.off, tuple) else e.off)
        return None
    flows = {}
    for va, (lf, body) in out.items():
        if lf is None:
            continue
        for _, ss, br in body:
            for s in ss:
                if isinstance(s, ir.Assign):
                    a, b = slot(lf, s.src), slot(lf, s.dst)
                    if a and b:
                        flows.setdefault(a, set()).add(b)
                for e in getattr(s, "exprs", lambda: ())():
                    for x in e.walk():
                        plist = m.plists.get(x.raw) if isinstance(x, ir.Call) and \
                            isinstance(x.raw, int) else None
                        if plist:
                            for a, pi in param_args(plist, x.args):
                                k = slot(lf, a)
                                if k:
                                    flows.setdefault(k, set()).add(("arg", x.raw, plist[pi][0]))
    work = list(found)
    while work:
        k = work.pop()
        for t in flows.get(k, ()):
            if t not in found:
                found[t] = found[k]
                work.append(t)
    return found


def proc_arrays(m, out):
    """Array parameters and the arrays passed to them share an element type:
    a callee that only passes its parameter on (or a caller that only hands
    a Variant's array over) learns it from the other side.  {(proc VA, var
    key): info}, merged over a few rounds so it travels along call chains."""
    from vb6dec.ir import Call, AddrOf, Var, Const, BinOp
    from vb6dec import simplify
    known = {}

    def merge(k, info):
        cur = known.setdefault(k, {"dyn": True})
        changed = False
        for kk in ("size", "vt", "udt"):
            if cur.get(kk) is None and info.get(kk) is not None:
                cur[kk] = info[kk]
                changed = True
                if kk == "size" and info.get("size_weak"):
                    # (a record's first field read: DetokenizeDNA dna() As block stayed an Integer
                    # array, udt.unify_array_params leaving what had a real size alone)
                    cur["size_weak"] = True
        if cur.get("size_weak") and info.get("size") and not info.get("size_weak"):
            cur["size"] = info["size"]
            del cur["size_weak"]
            changed = True
        return changed
    links = []                          # ((caller, var key), (callee, arg key))
    struct = getattr(m, "udt_array_params", set())
    for va, (lf, body) in out.items():
        if lf is None:
            continue
        for key, info in lf.arrays.items():
            if key[0] == "arg" and (va, key[1]) in struct:
                continue                # a UDT whose first field is the array
            # (module arrays too: VBDoom W_InitMultipleFiles wadfilenames, a
            # Public String() its parameter learns its type from)
            if key[0] in ("arg", "local", "global") and info.get("dyn"):
                if lf.udt.get(key) and not info.get("udt"):
                    info = dict(info, udt=lf.udt[key])      # an array of UDTs
                merge((va, key), info)
        from vb6dec.ir import Assign
        from vb6dec.types import VT_NAMES
        names_vt = {v: k for k, v in VT_NAMES.items()}
        # a Public member's descriptor gives its array parameters' types (DrawGrid(ga() As Boolean))
        member = getattr(lf.p, "member", None)
        if member is not None and getattr(lf.p, "event", None) is None:
            off = 8 if lf.obj is not None else 4
            for prm in member.params:
                if prm.array and names_vt.get(prm.type):
                    merge((va, ("arg", off)), {"vt": names_vt[prm.type]})
                off += 4 * (1 if prm.byref or prm.array else
                            {"Double": 2, "Currency": 2, "Date": 2, "Variant": 4}.get(prm.type, 1))
        for _, ss, br in body:          # arr = F(): F returns T()
            for s in ss:
                if isinstance(s, Assign) and isinstance(s.dst, Var) and \
                        s.dst.key() in lf.arrays and isinstance(s.src, Call) and \
                        isinstance(s.src.raw, int):
                    rt = m.ret_types.get(s.src.raw) or ""
                    if rt.endswith("()") and rt[:-2] in names_vt:
                        merge((va, s.dst.key()), {"vt": names_vt[rt[:-2]]})
        # frame slots the compiler also uses as scratch (a fixed array's
        # descriptor address, a frame address): not array variables, whatever
        # lf.arrays says (ChessBrainVB Think's [ebp-244h] passes descriptors
        # to CopyIntArr and Erase, and GetMax's result ByRef to
        # SendThinkInfo's ActDepth).  (Not a pointer copied from elsewhere:
        # gh_qr baData = TextOrByteArray.)
        from vb6dec.machine import SP as _SP
        scratch = set()
        for _, ss, br in simplify.flatten(lf):
            for s in ss:
                if isinstance(s, Assign) and isinstance(s.dst, Var) and \
                        s.dst.space == "local" and (isinstance(s.src, _SP) or
                                                    lf.data_address(s.src) or
                                                    # (a Static array's descriptor: the block's pointer
                                                    # plus its offset; cCipher, where the slot also holds
                                                    # the 8 given to a ByRef Long)
                                                    isinstance(s.src, BinOp) and s.src.op == "+" and
                                                    isinstance(s.src.a, Var) and s.src.a.space == "member" and
                                                    lf.obj is not None and
                                                    s.src.a.off == m.static_slot.get(lf.obj.index)):
                    scratch.add(s.dst.key())
        # registers that only ever copy one parameter pass that parameter on
        copies = {}
        for _, ss, br in body:
            for s in ss:
                if isinstance(s, Assign) and isinstance(s.dst, Var) and s.dst.space == "reg":
                    copies.setdefault(s.dst.key(), set()).add(
                        s.src.key() if isinstance(s.src, Var) and s.src.space == "arg" else None)
        copies = {k: Var(*next(iter(v))) for k, v in copies.items() if len(v) == 1 and
                  None not in v}
        for _, ss, br in list(simplify.flatten(lf)) + list(body):
            for s in ss:
                for e in getattr(s, "exprs", lambda: ())():
                    for x in e.walk():
                        if not (isinstance(x, Call) and isinstance(x.raw, int)):
                            continue
                        plist = m.plists.get(x.raw) or ()
                        for i, a in enumerate(x.args):
                            v = a.e if isinstance(a, AddrOf) else a
                            if isinstance(v, Var) and v.key() in copies:
                                v = copies[v.key()]
                            if isinstance(v, Var) and v.key() in scratch:
                                # (not from the callee either: DarwinBots UpdateSim's v12C holds
                                # &descriptor for ListCells clist(), and a Single elsewhere)
                                continue
                            # an array, or any local passed by reference (it is an
                            # array if the parameter is one: baTemp only passed on)
                            if isinstance(v, Var) and i < len(plist) and (
                                    v.key() in lf.arrays or
                                    isinstance(a, AddrOf) and v.space == "local" or
                                    # a ByRef parameter handed on as it is
                                    v.space == "arg" and not isinstance(a, AddrOf)):
                                # a local the caller does not know as an array only
                                # learns from the callee: optimised code reuses a
                                # frame slot to pass an array here and a ByRef Long
                                # there (ChessBrainVB: SendThinkInfo's ActDepth and
                                # ReadIntArr2's i1 became arrays)
                                both = v.key() in lf.arrays and v.key() not in scratch or \
                                    v.space == "arg"
                                links.append(((va, v.key()), (x.raw, ("arg", plist[i][0])),
                                              both))
    dbg = os.environ.get("VB6DEC_PADBG")    # "VA:off": how that parameter became an array
    if dbg:
        dva, doff = dbg.split(":")
        dkey = (int(dva, 16), ("arg", int(doff, 0)))
        print("PADBG seed", dkey in known, known.get(dkey))
    for _ in range(6):
        changed = False
        for a, b, both in links:
            had = dbg and dkey in known
            if a in known and b in known:
                changed |= merge(a, known[b])
                if both:
                    changed |= merge(b, known[a])
            elif a in known and both:
                changed |= merge(b, known[a])
            elif b in known:
                changed |= merge(a, known[b])
            if dbg and not had and dkey in known:
                names = {v: p.name for v, p in m.procs.items()}
                print("PADBG via link", (names.get(a[0]), a[1]), "->", (names.get(b[0]), b[1]),
                      "both" if both else "one-way", known.get(a), known.get(b))
        if not changed:
            break
    # (array parameters without an element type too: a caller that hands its
    # parameter on must declare it an array - VBDoom STlib_initPercent pl())
    linked = {b for _, b, _ in links} | {a for a, _, _ in links}
    return {k: v for k, v in known.items()
            if v.get("size") or v.get("vt") or v.get("udt") or
            k[1][0] == "arg" and k in linked}


def static_slots(m, out):
    """A class with Static locals keeps them in a block (laid out by the
    record descriptor at object+0x0C) whose pointer is in a member slot: a
    member that is never assigned, never used as a value, only dereferenced
    within the block (gh_zip cZipArchive: +0xBC).  Returns True if found."""
    from vb6dec import records
    from vb6dec.ir import Var, Deref, BinOp, Const, Assign, Return
    found = False
    # at the last but one dword of the object's data, as in a module below (gh_zip cZipArchive +0BCh of
    # 0C4h, t87nested cP +0E8h of 0F0h, DarwinBots Form1 +45Ch of 464h, a target cCipher +194h of 19Ch)
    for o in m.prj.objects:
        if o.kind == "Module" or not m.im.valid(o.static_bytes) or not m.im.valid(o.public_bytes):
            continue
        rec = records.read(m.im, o.static_bytes)
        n = m.im.u16(o.static_bytes + 2)
        if (rec is None or not rec.size) and not 0 < n < 0x10000:
            continue
        size = m.im.u16(o.public_bytes + 2)
        if size >= 8:
            m.static_slot[o.index] = size - 8
            found = True
    # a module's: the pointer is the last but one dword of the module's public data, in every module
    # with a static table (ChessBrainVB basSearch GetPonderMove: mov edx, [44A624h]; add edx, 1Ch;
    # push edx for ClearMove LastPonderMove; DarwinBots Common: gasdev's iset and gset, restarter's
    # called, rndy's y: global_889020.f8, a "Type" no procedure saw all of)
    for o in m.prj.objects:
        if o.kind != "Module" or not m.im.valid(o.static_bytes) or not o.module_public or \
                not m.im.valid(o.public_bytes) or not m.im.u16(o.static_bytes + 2):
            continue
        slot = o.module_public + m.im.u16(o.public_bytes + 2) - 8
        # (no address of the program's: the block is its own, and the module's Private data
        # is used at the same offsets from module_static)
        m.__dict__.setdefault("module_static_slot", {})[slot] = 0x7F000000 + o.index * 0x10000
        found = True
    return found


def pointer_results(m, out):
    """Object procedures that return through their last argument without a
    frame slot (frameless Friend methods: mov ecx,[esp+N]; mov [ecx],eax):
    Functions.  Friend methods check Me's vtable (__vbaFailedFriend)."""
    from vb6dec.ir import Assign, Deref, Var
    for va, (lf, body) in out.items():
        if lf is None or lf.obj is None:
            continue
        p = lf.p
        if "__vbaFailedFriend" in [m.im.imports.get(i.operands[0].mem.disp)
                                   for i in p.insns.values() if i.mnemonic == "call" and
                                   i.operands and i.operands[0].type == 3 and
                                   i.operands[0].mem.base == 0]:
            p.friend = True
        if lf.result_slot is not None or getattr(p, "kind", None) == "event" or p.ret_pop < 8:
            continue
        last = p.ret_pop
        writes, other = 0, 0
        from vb6dec.machine import Hi
        from vb6dec.ir import Const
        from vb6dec import ir
        def through(s, off):
            return isinstance(s, Assign) and isinstance(s.dst, Deref) and s.dst.off == off and \
                isinstance(s.dst.e, Var) and s.dst.e.space == "arg" and s.dst.e.off == last
        for _, ss, br in body:
            exprs = []
            lo = None
            for s in ss:
                if through(s, 0):
                    writes += 1
                    lo = s.src
                    exprs.append(s.src)
                    if s.dst.size == 8:
                        p.result8 = True    # both halves in one (simplify.merge_hi_stores)
                elif through(s, 4) and lo is not None and (
                        isinstance(s.src, Hi) and s.src.of.key() == lo.key() or
                        isinstance(s.src, Const) and isinstance(lo, Const) or
                        # the two halves of an 8-byte local (pvFromFileTime's Date)
                        isinstance(s.src, Var) and isinstance(lo, Var) and
                        s.src.space == lo.space == "local" and isinstance(lo.off, int) and
                        s.src.off == lo.off + 4 or
                        # the two halves of an array's element (optimised code: Form1 PopQStack,
                        # mov [ecx], eax; mov [ecx + 4], esi)
                        isinstance(s.src, ir.Index) and isinstance(lo, ir.Index) and
                        isinstance(s.src.arr, Var) and isinstance(lo.arr, Var) and
                        s.src.arr.space == lo.arr.space and isinstance(lo.arr.off, int) and
                        s.src.arr.off == lo.arr.off + 4 and
                        [i.key() for i in s.src.idx] == [i.key() for i in lo.idx]):
                    exprs.append(s.src)     # the upper half of an 8-byte result
                    p.result8 = True
                else:
                    exprs += list(getattr(s, "exprs", lambda: ())())
            if br is not None:
                exprs.append(br.cond)
            for e in exprs:
                for x in e.walk():
                    if isinstance(x, Var) and x.space == "arg" and x.off == last:
                        other += 1          # read, or used at another offset
        # a result pointer is only ever written, at offset 0; a ByRef UDT
        # parameter (pvVfsClose uFile: Handle = 0 ...) is read and written
        if writes and not other:
            p.ptr_result = True
            m.proc_returns[va] = True


def struct_params(m, out):
    """{(proc VA, argument index): struct byte size} for parameters a
    procedure uses as a struct (fields read or written at several offsets),
    or hands on to another procedure's struct parameter as it is (VBDoom
    AM_drawMline passes ml on to AM_clipMline: without this its callers'
    ml was no struct, and its field stores went to loose slots)."""
    from vb6dec import udt
    from vb6dec.ir import Call, Var
    res = {}
    passes = []         # (proc VA, its argument index, callee VA, callee argument index)
    offsets = {}        # (proc VA, argument index) -> offsets it is dereferenced at
    structy = set()     # (proc VA, argument index) a struct by other evidence (scalar_params)
    unused = set()      # (proc VA, argument index) never read or written at all
    for va, (lf, body) in out.items():
        if lf is None:
            continue
        ev = udt.collect(lf, body)
        first = param_base(lf)
        for off in range(first, 4 + (lf.p.ret_pop or 0), 4):
            if ("arg", off) not in lf.sizes:
                unused.add((va, (off - first) // 4))
        for key in set(ev.access) | set(ev.sub) | set(ev.size) | set(ev.farrays) | \
                set(ev.arrays) | set(ev.fixed) | set(lf.arrays) | ev.whole:
            if key[0] != "arg" or not isinstance(key[1], int) or key[1] < first or \
                    getattr(lf.p, "ptr_result", False) and key == ("arg", lf.p.ret_pop):
                continue
            i = (key[1] - first) // 4
            if key in ev.access and not (key in lf.arrays or key in ev.sub or key in ev.size or
                                         key in ev.farrays or key in ev.arrays or
                                         key in ev.fixed or key in ev.whole):
                offsets[(va, i)] = set(ev.access[key])
            else:
                structy.add((va, i))
        # fields of the raw lift too: a store to a local nothing reads yet is dead in this first
        # simplify (mixrgb.g = c1.g * ...: its result is no record yet, and only c1.r was left, so
        # c As R_G_B in the caller stayed loose slots)
        access = {k: dict(v) for k, v in ev.access.items()}
        from vb6dec import simplify
        from vb6dec.ir import Deref
        for _, ss, br in simplify.flatten(lf):
            for s in ss:
                for e in getattr(s, "exprs", lambda: ())():
                    for x in e.walk():
                        if isinstance(x, Deref) and isinstance(x.e, Var) and x.e.space == "arg" \
                                and isinstance(x.e.off, int) and x.e.off >= first and \
                                isinstance(x.off, int) and (x.e.key() in access or x.off):
                            d = access.setdefault(x.e.key(), {})
                            d[x.off] = max(d.get(x.off, 0), x.size or 0)
        for key, acc in access.items():
            if getattr(lf.p, "ptr_result", False) and key == ("arg", lf.p.ret_pop):
                continue                    # the result pointer (8-byte halves)
            if key[0] == "arg" and isinstance(key[1], int) and len(acc) >= 2 and \
                    key not in lf.arrays:   # an array parameter's SAFEARRAY fields: no struct
                # a ByRef Variant copied dword by dword through __vbaVarVargNofree's result
                # (a target Proc_926D00 .CurrentX = x): as a "16 byte record" it made a
                # Single and the Variant above it, passed to that parameter, one v88.f0, v88.f4
                # (all four dwords: three are a record's, DarwinBots mixrgb(c1 As R_G_B, ...): its
                # caller's c.r, c.g, c.b were loose slots, and only c.r reached the call)
                if key in lf.variants and set(acc) <= {0, 4, 8, 12} and 12 in acc:
                    continue
                # the last field's end, padded to the widest field's alignment
                # (VB packs a UDT on min(size, 4)): ChessBrainVB TMOVE ends with
                # an Integer at 1Ah and is 28 bytes, not 1Ah + 4
                end = max(o + max(sz, 1) for o, sz in acc.items())
                al = max(min(max(sz, 1), 4) for sz in acc.values())
                res[(va, (key[1] - first) // 4)] = -(-end // al) * al
                if os.environ.get("VB6DEC_SPDBG"):
                    print("SP %x a%d %d %s" % (va, (key[1] - first) // 4, -(-end // al) * al,
                                               sorted(acc.items())), file=sys.stderr)
        for key, size in ev.size.items():
            # copied as a whole (LSet, CopyMemory with Len): VBDoom D_PostEvent
            # events(eventhead) = ev touches no field of ev
            if key[0] == "arg" and isinstance(key[1], int) and isinstance(size, int) and \
                    size > 4 and key not in lf.arrays and key[1] >= first:
                i = (key[1] - first) // 4
                res[(va, i)] = max(res.get((va, i), 0), size)
        for _, ss, br in body:
            for s in ss:
                for e in getattr(s, "exprs", lambda: ())():
                    for x in e.walk():
                        if not (isinstance(x, Call) and isinstance(x.raw, int)):
                            continue
                        for i, a in enumerate(x.args):
                            if isinstance(a, Var) and a.space == "arg" and \
                                    isinstance(a.off, int) and a.off >= first and \
                                    a.key() not in lf.arrays and \
                                    not (getattr(lf.p, "ptr_result", False) and
                                         a.off == lf.p.ret_pop):
                                passes.append((va, (a.off - first) // 4, x.raw, i))
    changed = True
    while changed:
        changed = False
        for va, pi, callee, i in passes:
            size = res.get((callee, i))
            if size and res.get((va, pi), 0) < size:
                if os.environ.get("VB6DEC_SPDBG"):
                    print("SPPASS %x a%d <- %x a%d %d" % (va, pi, callee, i, size),
                          file=sys.stderr)
                res[(va, pi)] = size
                changed = True
    m.struct_param_sizes = res              # (udt.collect: a ByVal-looking struct parameter)
    m._param_evidence = (offsets, structy, passes, unused)     # for scalar_params
    return res


def scalar_params(m):
    """(proc VA, argument index) of the ByRef parameters no record can be
    passed to: dereferenced at offset 0 only, or only handed on as they are
    to such parameters, with nothing else showing a struct (no second field,
    no copy as a whole, no record descriptor, no API Type).  VB passes the
    address of a record and of its first field alike, so a caller's record
    says nothing about them (a target ReadAll(h, a): a caller passing
    rec.h made h a 728-byte TRec11, every one of its 432 callers' h
    variables after it, and 49 scalar module variables used as numbers)."""
    offsets, structy, passes, unused = m.__dict__.get("_param_evidence",
                                                      ({}, set(), [], set()))
    known = set(m.struct_param_sizes) | set(m.__dict__.get("rec_params", {})) | structy
    scalar = {k for k, offs in offsets.items() if offs == {0} and k not in known}
    # one never used at all takes what every caller can pass: a record's
    # first field as well as a number (a target Name2$(h, a) never reads h,
    # and became h As TRec4 from callers passing Form1(i) - against those
    # passing Form1(i).f4)
    scalar |= {k for k in unused if k not in known and k not in offsets}
    targets = {}
    for va, pi, callee, i in passes:
        targets.setdefault((va, pi), set()).add((callee, i))
    changed = True
    while changed:
        changed = False
        for k, ts in targets.items():
            if k not in scalar and k not in known and not offsets.get(k) and ts <= scalar:
                scalar.add(k)
                changed = True
    m.scalar_params = scalar
    return scalar


def _dbg(va, off, tag):
    import os
    if os.environ.get("VB6DEC_HINTDBG") == "%x" % va:      # which rule made a local a struct
        print("HINT va=%x off=%s from %s" % (va, off, tag), file=__import__("sys").stderr)


def global_struct_hints(m, out):
    """Module-level UDT variables.  The code reaches their fields at absolute
    addresses (doomcom.data.checksum is just [48953Ch]), so each field looks
    like a variable of its own; a struct shows where the program copies the
    bytes as a whole (LSet between module variables: VBDoom reboundstore =
    doomcom.data) or passes the address to a procedure's struct parameter.
    {start: byte size}, for the next lift (Lifter.global_var): every address
    inside one is its field.  A range inside another one is left out (part
    of the bigger variable)."""
    from vb6dec import apidb
    from vb6dec.ir import Call, Const, AddrOf, Var
    from vb6dec import simplify
    sparams = struct_params(m, out)
    found = {}
    reach = {}      # only as far as a procedure's accesses reach: a lower bound on the size, no size

    def start(a):
        if isinstance(a, Const) and isinstance(a.value, int):
            va = a.value & 0xffffffff
        elif isinstance(a, AddrOf) and isinstance(a.e, Var) and a.e.space == "global" and \
                isinstance(a.e.off, int):
            va = a.e.off
        else:
            return None
        return va if m.im.section_of(va) == ".data" else None
    from vb6dec import records
    for va, (lf, body) in out.items():
        if lf is None:
            continue
        # a module variable a record descriptor names (Put #f, , rec; LSet;
        # __vbaRecAssign): its fields are at its address and after (a target
        # global_F06878 = CByte(...) for global_F06878.f0, As TRec21)
        slots = m.__dict__.get("_array_slots")
        if slots is None:
            slots = m._array_slots = m.global_array_slots()
        for k, rva in getattr(lf, "rec_of", {}).items():
            if k[0] == "global" and isinstance(k[1], int) and m.im.section_of(k[1]) == ".data" \
                    and k not in lf.arrays and k[1] not in slots:   # (not an array of them)
                rec = records.model_record(m, rva)
                if rec is not None and rec.size > 4:
                    found[k[1]] = max(found.get(k[1], 0), rec.size)
        # the raw lift only: in the first one's simplified body a struct local
        # nobody knew yet can be inlined away (AM_drawGrid: ml.a.x = m_x made
        # AM_drawMline &m_x, which would make m_x a struct)
        for _, ss, br in simplify.flatten(lf):
            for st in ss:
                for e in getattr(st, "exprs", lambda: ())():
                    for x in e.walk():
                        if not isinstance(x, Call):
                            continue
                        desc = getattr(x, "owner_desc", None)
                        if isinstance(desc, Const) and isinstance(desc.value, int):
                            # Put #f, n, rec of a record without Strings: a target
                            # global_F06056 = CByte(1) stood for rec.f0, F06057 for
                            # rec.f1, and Put wrote one byte
                            n = records.io_size(m.im, desc.value & 0xffffffff)
                            if n:
                                gaps = records.io_gaps(m.im, desc.value & 0xffffffff)
                                if gaps is not None:
                                    records.IO_GAPS.setdefault(n, set()).update(gaps)
                            g = start(x.args[-1])
                            if g is not None and n and g not in slots:  # (no array of them)
                                found[g] = max(found.get(g, 0), n)
                        n = getattr(x, "put_size", None)
                        if n and n > 8:
                            # the same for __vbaPut4 / __vbaGet4, a record of plain numbers
                            # (a target Put #f, n, global_F0580C of ten colours wrote one)
                            g = start(x.args[-1])
                            if g is not None and g not in slots:
                                found[g] = max(found.get(g, 0), n)
                        if x.name == "LSet" and len(x.args) == 3 and \
                                isinstance(x.args[2], Const) and isinstance(x.args[2].value, int):
                            n = x.args[2].value
                            for a in x.args[:2]:
                                g = start(a)
                                if g is not None and n > 4:
                                    found[g] = max(found.get(g, 0), n)
                        elif isinstance(x.raw, int):
                            for i, a in enumerate(x.args):
                                n = sparams.get((x.raw, i))
                                g = start(a)
                                if g is not None and n and n > 4:
                                    if found.get(g, 0) < n:
                                        reach[g] = n
                                    found[g] = max(found.get(g, 0), n)
                        elif isinstance(x.raw, str) and "!" in x.raw:
                            # an API's ByRef UDT parameter given a module variable's
                            # address (VBDoom CreateDIBSection memDC, bmi, ...)
                            api = apidb.lookup(x.raw.split("!", 1)[1])
                            for (byval, pname, ptype), a in zip(api.params if api else (),
                                                                 x.args):
                                lay = apidb.layout(ptype) if not byval else None
                                g = start(a)
                                if lay is not None and g is not None and lay[1] > 4:
                                    found[g] = max(found.get(g, 0), lay[1])
    exact = {g for g in found if g not in reach or found[g] != reach[g]}
    # what the modules' variable tables say (Model.module_records)
    for g, (d, room) in m.module_records().items():
        rec = records.model_record(m, d) if d else None
        if rec is None and found.get(g):
            # room is only where the next variable the table lists starts: plain numbers after the
            # record aren't listed.  What the code copies or passes says more (ChessBrainVB
            # FinalMove As TMOVE, room for 40, made the Long after it global_44A758.f1C)
            continue
        n = rec.size if rec is not None else room
        if n > 4:
            found[g] = max(found.get(g, 0), n)
            if rec is not None:
                exact.add(g)
    hints = {}
    for g, n in sorted(found.items()):
        if any(h <= g < h + hn for h, hn in hints.items()):
            continue                    # inside one already kept
        hints[g] = n
    m.global_struct_hint = hints
    m.global_struct_reach = {g for g, n in reach.items() if found.get(g) == n}
    m.global_struct_exact = {g for g in exact if g in hints}    # (not just room: at least so big)


def struct_hints(m, out):
    """Struct locals found in the first lift (API UDT parameters, CopyMemory
    sizes, locals passed to a procedure's struct parameter): the next lift
    treats every slot inside them as a field."""
    from vb6dec import apidb, udt
    from vb6dec.ir import Call, AddrOf, Var, Assign, Deref, Const
    from vb6dec import simplify
    sparams = struct_params(m, out)
    # parameters a callee knows to be a record from its descriptor (it frees
    # or copies the record: __vbaRecDestruct / __vbaRecAssign): a caller's
    # local passed there is that record (a target: ~1900 locals typed Long or
    # Variant were passed to As TRecN parameters)
    recs = m.__dict__.get("records", {})
    rec_params = {}
    for cva, (clf, _) in out.items():
        if clf is None:
            continue
        first = param_base(clf)
        for key, rva in getattr(clf, "rec_of", {}).items():
            rec = recs.get(rva)
            if key[0] == "arg" and isinstance(key[1], int) and rec is not None and rec.size:
                rec_params[(cva, (key[1] - first) // 4)] = rec
    m.rec_params = rec_params
    scalar_params(m)
    for va, (lf, body) in out.items():
        if lf is None:
            continue
        hints = {}
        for _, ss, br in simplify.flatten(lf):
            for s in ss:
                for e in getattr(s, "exprs", lambda: ())():
                    for x in e.walk():
                        if isinstance(x, Call) and isinstance(x.raw, int):
                            for i, a in enumerate(x.args):
                                rec = rec_params.get((x.raw, i))
                                v = a.e if isinstance(a, AddrOf) else a
                                if rec is not None and isinstance(v, Var) and \
                                        v.space == "local" and isinstance(v.off, int) and \
                                        v.off not in hints and v.key() not in lf.arrays:
                                    hints[v.off] = (rec.name, rec.size)
                                    lf.rec_of.setdefault(v.key(), rec.va)
                                    _dbg(va, v.off, 'rec-param')
        # passed to a decompiled procedure's struct (the raw lift: simplifying
        # may have inlined a field's value into the argument)
        for _, ss, br in simplify.flatten(lf):
            for s in ss:
                for e in getattr(s, "exprs", lambda: ())():
                    for x in e.walk():
                        if isinstance(x, Call) and isinstance(x.raw, int):
                            for i, a in enumerate(x.args):
                                size = sparams.get((x.raw, i))
                                # (only its address: a slot's value passed on is a pointer that
                                # slot holds, and no struct - t85recretno: mov eax, [ebp-16Ch];
                                # push eax, an element's field for VDot)
                                v = a.e if isinstance(a, AddrOf) else None
                                # (the longest any procedure reaches: the first one's left the fields past
                                # it loose variables, not the record the others were given; ChessBrainVB
                                # SearchRoot CurrentMove.IsChecking, v1E, never set by SetMove v38)
                                if size and isinstance(v, Var) and v.space == "local" and \
                                        isinstance(v.off, int) and \
                                        (v.off not in hints or hints[v.off][0] is None and hints[v.off][1] < size) and \
                                        v.key() not in lf.arrays:   # (a SAFEARRAY, no struct)
                                    hints[v.off] = (None, size); _dbg(va, v.off, 'arg-struct %x #%d' % (x.raw, i))
        for key, name in lf.udt.items():
            if key[0] == "local" and isinstance(key[1], int) and key not in lf.arrays:
                lay = apidb.layout(name)
                if lay:
                    hints[key[1]] = (name, lay[1]); _dbg(va, key[1], 'lf.udt')
        ev = udt.collect(lf, body)
        for key, subs in ev.sub.items():        # passed to an API as a struct
            name = subs.get(0)
            if key[0] == "local" and isinstance(key[1], int) and name and key[1] not in hints \
                    and key not in lf.arrays:
                lay = apidb.layout(name)
                if lay:
                    hints[key[1]] = (name, lay[1]); _dbg(va, key[1], 'api-sub')
        for key, rva in getattr(lf, "rec_of", {}).items():    # UDTs with Strings
            rec = m.__dict__.get("records", {}).get(rva)
            if rec is not None and key[0] == "local" and isinstance(key[1], int) and \
                    key not in lf.arrays and key[1] not in hints:
                hints[key[1]] = (rec.name, rec.size)
        # a Function returning a fixed UDT: its result slot is that UDT's
        # buffer, delivered dword by dword through the caller's pointer
        u = getattr(lf.p, "udt_ret", 0)
        if u and isinstance(lf.result_slot, int) and \
                ("local", lf.result_slot) not in lf.arrays:
            hints.setdefault(lf.result_slot, (None, u)); _dbg(va, lf.result_slot, 'callee-udt_ret')
        # and the caller side: the buffer a UDT Function's result is assigned to
        for _, ss, br in simplify.flatten(lf):
            for s in ss:
                if isinstance(s, Assign) and isinstance(s.src, Call) and \
                        isinstance(s.src.raw, int):
                    cp = m.procs.get(s.src.raw)
                    u = getattr(cp, "udt_ret", 0) if cp is not None else 0
                    if u and isinstance(s.dst, Var) and s.dst.space == "local" and \
                            isinstance(s.dst.off, int) and s.dst.off not in hints:
                        hints[s.dst.off] = (None, u); _dbg(va, s.dst.off, 'caller-udt_ret@%x' % s.src.raw)
        # Put / Get #f, , v of more bytes than a scalar has: a UDT that long
        # (VBDoom M_WriteBMP Put #f, , ih: 40 bytes; its fields stored one by
        # one were separate locals, and the Put printed the first one's value)
        for _, ss, br in simplify.flatten(lf):
            for s in ss:
                for e in getattr(s, "exprs", lambda: ())():
                    for x in e.walk():
                        n = getattr(x, "put_size", None)
                        if not (isinstance(x, Call) and n and n > 8 and x.args):
                            continue
                        v = x.args[-1]
                        v = v.e if isinstance(v, AddrOf) else None
                        if isinstance(v, Var) and v.space == "local" and \
                                isinstance(v.off, int) and v.off not in hints and \
                                v.key() not in lf.arrays:
                            hints[v.off] = (None, n); _dbg(va, v.off, 'put-size')
        # copied with Len(struct): from the raw lift too, where no copy of a
        # field has been folded into the copy's address yet (t06m MakeRec)
        raw_ev = udt.collect(lf, simplify.flatten(lf))
        sizes = dict(raw_ev.size)
        sizes.update(ev.size)
        for key, size in sizes.items():
            # CopyMemory x, y, 4 copies a Long (or half of an 8-byte value:
            # gh_zip pvToInt64), no struct
            if key[0] == "local" and isinstance(key[1], int) and key[1] not in hints and \
                    size > 4:
                cands = udt.candidates_size(ev if key in ev.size else raw_ev, key)
                hints[key[1]] = (cands[0] if len(cands) == 1 else None, size); _dbg(va, key[1], 'copy-size')
        # VarPtr(local) handed on (a UDT given to machine code, gh_zip DeflateBlob
        # VarPtr(uBuf)): the slots written right above it belong to it
        written = {}                # slots read or written (OutLen is only read back)
        for _, ss, br in simplify.flatten(lf):
            for st in ss:
                for e in getattr(st, "exprs", lambda: ())():
                    for x in e.walk():
                        if isinstance(x, Var) and x.space == "local" and isinstance(x.off, int):
                            written.setdefault(x.off, 4)
                        if isinstance(x, Deref) and isinstance(x.e, Var) and \
                                x.e.space == "local" and isinstance(x.e.off, int):
                            written[x.e.off] = None     # a pointer (a SAFEARRAY): no field
        # (what the compiler frees one by one is its own temporary, no field: __vbaFreeStrList's
        # three Strings for the ANSI copies of an API's arguments, typed Long as T_C_1.f8, were an
        # error 13 in Proc_438C00, and __vbaFreeVar's Variant tag was a field of what came before)
        from vb6dec.ir import Free
        freed = set()
        for _, ss, br in simplify.flatten(lf):
            for st in ss:
                if isinstance(st, Free):
                    for a in st.targets:
                        if type(a).__name__ == "SP":
                            freed.add(a.off + 4)
                        elif isinstance(a, AddrOf) and isinstance(a.e, Var) and \
                                a.e.space == "local" and isinstance(a.e.off, int):
                            freed.add(a.e.off)
                        elif isinstance(a, AddrOf) and isinstance(a.e, Deref) and \
                                isinstance(a.e.e, Var) and a.e.e.space == "local" and \
                                isinstance(a.e.e.off, int):
                            freed.add(a.e.e.off + a.e.off)
        for off in freed:
            written[off] = None
        # (a slot with its own address given is a variable of its own, not a field of the one below:
        # CryptStringToBinaryW ..., VarPtr(lSize), 0, VarPtr(dwDummy) printed v20.f4 of a v20 no Type
        # was found for)
        taken = set()
        for _, ss, br in simplify.flatten(lf):
            for st in ss:
                for e in getattr(st, "exprs", lambda: ())():
                    for x in e.walk():
                        if isinstance(x, Call) and x.name == "VarPtr" and x.args:
                            v = x.args[0].e if isinstance(x.args[0], AddrOf) else x.args[0]
                            if isinstance(v, Var) and v.space == "local" and isinstance(v.off, int):
                                taken.add(v.off)
        for _, ss, br in simplify.flatten(lf):
            for st in ss:
                for e in getattr(st, "exprs", lambda: ())():
                    for x in e.walk():
                        if not (isinstance(x, Call) and x.name == "VarPtr" and x.args):
                            continue
                        v = x.args[0]
                        v = v.e if isinstance(v, AddrOf) else v
                        if not (isinstance(v, Var) and v.space == "local" and
                                isinstance(v.off, int)) or v.off in hints or \
                                v.key() in lf.arrays or v.off in lf.named or \
                                v.off == lf.result_slot:
                            continue                # (VarPtr(F): the Function's 8-byte value)
                        sizes = {z for z, _ in getattr(lf, "load_sizes", {}).get(v.key(), ())}
                        if v.off in freed:
                            continue
                        if sizes and max(sizes) < 4:
                            # read as a word or a byte: a variable of its own for an API to fill, and
                            # a field As Long read too much (a target Proc_438C00: RegQueryValueExA
                            # gave a Boolean 2 bytes, a6 = v60.f0 was 65535, and the program showed the
                            # disclaimer it was told not to show)
                            continue
                        end = v.off + 4
                        while written.get(end) and end not in lf.named and end not in taken and \
                                ("local", end) not in lf.arrays and \
                                ("local", end) not in lf.variants and end < 0:
                            end += 4
                        if end - v.off >= 8:
                            hints[v.off] = (None, end - v.off); _dbg(va, v.off, 'varptr')
        for _, ss, _ in simplify.flatten(lf):
            for st in ss:
                # the value of a Function returning a small record (lift.small_record_return)
                if isinstance(st, Assign) and isinstance(st.dst, Var) and st.dst.space == "local" \
                        and isinstance(st.dst.off, int) and isinstance(st.src, Call) and \
                        getattr(st.src, "small_rec", None) and st.dst.off not in hints:
                    hints[st.dst.off] = (None, st.src.small_rec); _dbg(va, st.dst.off, 'small-rec')
                # __vbaCopyBytes between two locals: VB copies only records that way
                e = getattr(st, "e", None)
                if isinstance(e, Call) and e.name == "LSet" and len(e.args) == 3 and \
                        isinstance(e.args[2], Const) and e.args[2].value in (4, 6, 8):
                    for a in e.args[:2]:
                        if isinstance(a, AddrOf) and isinstance(a.e, Var) and \
                                a.e.space == "local" and isinstance(a.e.off, int) and \
                                a.e.off not in hints and ("local", a.e.off) not in lf.arrays:
                            hints[a.e.off] = (None, e.args[2].value); _dbg(va, a.e.off, 'lset-pair')
        for key in getattr(lf, "sa_alias", {}):     # a locked array's temporary: no struct
            if key[0] == "local":
                hints.pop(key[1], None)
        if hints:
            m.struct_hint[va] = hints


def owner_of(m, p):
    if p.owner is not None:
        return p.owner
    t = getattr(p, "truth", None)
    if t:
        for o in m.prj.objects:
            if o.name == t[0]:
                return o
    return m.prj.objects[-1]


def fast_exit(code):
    """Leave without freeing the heap, one object at a time (a target: 24 s,
    a tenth of the run; every file is written and closed by now).  Not under a
    profiler, which reports after the program ends (VB6DEC_NOFASTEXIT, set by
    tools/pysample.py)."""
    if not os.environ.get("VB6DEC_NOFASTEXIT"):
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(code or 0)
    return code


if __name__ == "__main__":
    sys.exit(fast_exit(main(sys.argv[1:])))
