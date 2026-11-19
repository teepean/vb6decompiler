"""Which module a module procedure belongs to, without a map.

Native code keeps no table of a module's procedures, but the linker lays
the modules' code out one after another in object table order (VBDoom: all
58 modules, one procedure apart), and form and class procedures, whose
owners are known, mark where objects lie.  The module procedures between two
known ones belong to the modules between those two objects, in order; where
one module ends and the next begins is decided by the module variables each
procedure uses (a module's Private data area is used by its own procedures
only; its Public one mostly), with each module holding at most as many
procedures as its source had (ObjectInfo's method count: VB drops the ones
nothing calls).  Procedures without evidence are spread in proportion to
those counts.
"""
from .ir import Var

PRIVATE_VOTE, PUBLIC_VOTE = 10, 1


def data_ranges(prj):
    """(start, end, module, vote) for every module's Public and Private data."""
    im = prj.im
    out = []
    for o in prj.objects:
        if o.kind != "Module":
            continue
        for start, desc, vote in ((o.module_public, o.public_bytes, PUBLIC_VOTE),
                                  (o.module_static, o.static_bytes, PRIVATE_VOTE)):
            if start and im.valid(desc):
                size = im.u16(desc + 2)
                if size:
                    out.append((start, start + size, o, vote))
    return out


def votes_of(body, ranges):
    """{module index: votes} for the module variables a body uses."""
    seen = set()
    for _, ss, br in body:
        es = [e for s in ss for e in getattr(s, "exprs", lambda: ())()]
        if br is not None:
            es.append(br.cond)
        for e in es:
            for x in e.walk():
                if isinstance(x, Var) and x.space == "global" and isinstance(x.off, int):
                    seen.add(x.off)
    out = {}
    for va in seen:
        for start, end, o, vote in ranges:
            if start <= va < end:
                out[o.index] = out.get(o.index, 0) + vote
    return out


def split(run, mods, votes, caps):
    """Cut run (procedures in address order) into consecutive pieces, one a
    module of mods (in order, possibly empty), best by votes; a piece holds
    at most its module's cap; sizes near the caps break ties."""
    n, k = len(run), len(mods)
    if sum(caps) < n:
        caps = [n] * k                  # (more procedures than the counts allow)
    # prefix[j][i]: votes of run[:i] for mods[j]
    prefix = []
    for o in mods:
        acc, row = 0, [0]
        for p in run:
            acc += votes.get(p.va, {}).get(o.index, 0)
            row.append(acc)
        prefix.append(row)
    NEG = float("-inf")
    best = [0.0] + [NEG] * n
    back = []
    for j, o in enumerate(mods):
        cap, row = caps[j], prefix[j]
        nxt, arg = [NEG] * (n + 1), [0] * (n + 1)
        for i in range(n + 1):
            lo = max(0, i - cap)
            top, at = NEG, i
            for t in range(lo, i + 1):
                b = best[t]
                if b == NEG:
                    continue
                v = b + 1000 * (row[i] - row[t]) - abs((i - t) - cap) / (cap + 1.0)
                if v > top:
                    top, at = v, t
            nxt[i], arg[i] = top, at
        back.append(arg)
        best = nxt
    out, i = {}, n
    for j in range(k - 1, -1, -1):
        t = back[j][i]
        for p in run[t:i]:
            out[p.va] = mods[j]
        i = t
    return out


def guess(m, bodies):
    """{procedure VA: module object} for module procedures nothing names."""
    prj = m.prj
    pos = {o.index: i for i, o in enumerate(prj.objects)}
    mods = [o for o in prj.objects if o.kind == "Module"]
    if not mods:
        return {}
    procs = sorted(m.procs.values(), key=lambda p: p.va)
    unknown = [p for p in procs if p.owner is None and not getattr(p, "truth", None)]
    if not unknown:
        return {}
    ranges = data_ranges(prj)
    votes = {}
    for p in unknown:
        lf, body = bodies.get(p.va, (None, None))
        if lf is not None:
            votes[p.va] = votes_of(body, ranges)
    out = {}
    run, prev = [], -1
    for p in procs + [None]:
        if p is not None and p.owner is None and not getattr(p, "truth", None):
            run.append(p)
            continue
        here = len(prj.objects) if p is None else pos[(p.owner or _named(prj, p)).index]
        if run:
            cand = [o for o in mods if prev < pos[o.index] < here] or \
                   [o for o in mods if pos[o.index] <= max(prev, 0)][-1:] or mods[:1]
            out.update(split(run, cand, votes, [max(o.nmethods, 1) for o in cand]))
            run = []
        if p is not None:
            prev = max(prev, here)
    return out


def _named(prj, p):
    for o in prj.objects:
        if o.name == p.truth[0]:
            return o
    return prj.objects[-1]
