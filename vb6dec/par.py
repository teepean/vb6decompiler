"""Lift / simplify procedures in forked workers, the results back by pickle
(VB6DEC_JOBS workers; decompile.first_pass).

A worker sees the whole Model through fork and works on it as first_pass would
in one process; what comes back is what would be left behind: each procedure's
(Lifter, body).  What may run this way is what does not depend on the other
procedures of the same round, and leaves nothing behind in the Model:
 - simplify.run: reads the parameter lists (not changed in a round);
 - Lifter + simplify in the second lift: the Model's facts are all in by then.
 A worker's changes to the Model would be lost, so each reports its size
(check()): if a dict grew, the round is done again in the main process.

Objects made before the fork come back as references to the main process's
own (reduce_identity): the Model, procedures, type libraries ...; instructions
by address.
"""
import gc
import io
import os
import pickle
import sys
import threading

from . import apidb
from . import disasm
from . import lift as liftmod

_table = {}


def _resolve(i):
    return _table[i]


def _insn_at(va):
    return _im._decoded[va]


def _lifter(state):
    lf = object.__new__(liftmod.Lifter)
    lf.__dict__.update(state)
    lf.m = _m
    lf.im = _m.im
    lf.declare_argc = _m.declare_argc
    return lf


_m = _im = None


def jobs(m):
    """How many workers: VB6DEC_JOBS, else up to 8; 1 for a small EXE, where a
    fork costs more than it saves (VB6DEC_PAR_MIN: instructions, default 200000)."""
    if not hasattr(os, "fork"):         # (Windows: everything in this process)
        return 1
    n = os.environ.get("VB6DEC_JOBS")
    n = int(n) if n else min(8, os.cpu_count() or 1)
    if sum(len(p.insns) for p in m.procs.values()) < int(os.environ.get("VB6DEC_PAR_MIN", 200000)):
        return 1
    return n


def build_table(m):
    """Objects a result may refer to that the main process has: {id: object}."""
    global _m, _im
    _m, _im = m, m.im
    _table.clear()
    mods = ("vb6dec.model", "vb6dec.project", "vb6dec.typelib", "vb6dec.records", "vb6dec.members",
            "vb6dec.disasm", "vb6dec.pe", "vb6dec.projiface", "vb6dec.frm", "vb6dec.apidb")
    for o in gc.get_objects():
        t = type(o)
        if t.__module__ in mods and t is not disasm.Template and t is not disasm.Insn:
            _table[id(o)] = o
    classes = {type(o) for o in _table.values()}
    return classes


def _reduce_table(o):
    i = id(o)
    if i in _table:
        return _resolve, (i,)
    return o.__reduce_ex__(pickle.HIGHEST_PROTOCOL)


def _reduce_insn(o):
    return _insn_at, (o.address,)


def _reduce_lifter(o):
    d = dict(o.__dict__)
    for k in ("m", "im", "declare_argc"):
        d.pop(k, None)
    return _lifter, (d,)


def sizes(m):
    """What a worker's round may not change, in short."""
    out = {k: len(v) for k, v in m.__dict__.items() if isinstance(v, (dict, set, list))}
    out["#types"] = len(apidb._types or ())
    return out


def _balance(vas, m, n):
    """vas in n groups of about equal work (instructions), largest first."""
    groups = [[0, []] for _ in range(n)]
    for va in sorted(vas, key=lambda a: -len(m.procs[a].insns)):
        g = min(groups, key=lambda x: x[0])
        g[0] += len(m.procs[va].insns) + 20
        g[1].append(va)
    return [g[1] for g in groups if g[1]]


def run(m, vas, work, n):
    """{va: work(va)} for the procedures vas, in n forked workers.  work must
    follow the rules above.  None if a worker failed or changed the Model: the
    caller does the round itself."""
    classes = build_table(m)
    base = sizes(m)
    keys0 = set(m.__dict__)
    groups = _balance(vas, m, n)
    sys.stdout.flush()
    sys.stderr.flush()
    pids = []
    for g in groups:
        r, w = os.pipe()
        pid = os.fork()
        if pid == 0:
            code = 1
            try:
                os.close(r)
                for _, r2 in pids:
                    os.close(r2)
                res = [(va, work(va)) for va in g]
                now = sizes(m)
                grew = {k: (v, now.get(k)) for k, v in base.items() if now.get(k) != v}
                new = {k: m.__dict__[k] for k in m.__dict__ if k not in keys0}
                f = io.BytesIO()
                p = pickle.Pickler(f, protocol=pickle.HIGHEST_PROTOCOL)
                p.dispatch_table = {c: _reduce_table for c in classes}
                p.dispatch_table[disasm.Insn] = _reduce_insn
                p.dispatch_table[liftmod.Lifter] = _reduce_lifter
                sys.setrecursionlimit(200000)
                p.dump((res, grew, new))
                data = f.getbuffer()
                with os.fdopen(w, "wb") as out:
                    out.write(data)
                code = 0
            except BaseException as e:
                print("par: worker: %s: %s" % (type(e).__name__, e), file=sys.stderr, flush=True)
            finally:
                os._exit(code)
        os.close(w)
        pids.append((pid, r))
    blobs = [None] * len(pids)

    def read(i, fd):
        with os.fdopen(fd, "rb") as f:
            blobs[i] = f.read()
    threads = [threading.Thread(target=read, args=(i, fd)) for i, (_, fd) in enumerate(pids)]
    for t in threads:
        t.start()
    ok = True
    for pid, _ in pids:
        _, st = os.waitpid(pid, 0)
        ok = ok and st == 0
    for t in threads:
        t.join()
    if not ok:
        print("par: a worker failed: the round runs in one process", file=sys.stderr)
        return None
    out = {}
    sys.setrecursionlimit(max(sys.getrecursionlimit(), 200000))
    enabled = gc.isenabled()
    gc.disable()
    for b in blobs:
        res, grew, new = pickle.loads(b)
        if grew:
            print("par: a worker changed the Model %s: the round runs in one process" % (grew,),
                  file=sys.stderr)
            return None
        out.update(res)
        for k, v in new.items():
            m.__dict__.setdefault(k, v)
    if enabled:
        gc.enable()
    return out
