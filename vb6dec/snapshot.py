"""The first pass's result on disk, for what comes after it.

first_pass takes most of a decompile and its result does not depend on what is
done with it later, so a debugging tool that looks at one procedure
(tools/passtrace.py), or a run that only changes what the writer does, can
start from a saved copy.  A snapshot is valid for one EXE (and map) and one
state of the decompiler's source: the source's hash is stored at the start of
the file, so an edit makes the tools run the first pass again.
VB6DEC_SNAPSHOT_ANY=1 takes the EXE's snapshot whatever the source says: for
changes that cannot touch the first pass (the writer; a pass being traced).

The directory is VB6DEC_SNAPSHOTS (default ~/.cache/vb6dec/snapshots, or under
XDG_CACHE_HOME).  One file per EXE and map, replaced atomically by the next
save: nothing is ever deleted to make room, and a run reading the old file
keeps it until it closes it.  A snapshot is a pickle, and loading a pickle runs
code, so only a file of ours in a directory only we can write to is used.
"""
import glob
import hashlib
import os
import pickle
import stat
import sys
import time

DIR = os.environ.get("VB6DEC_SNAPSHOTS") or os.path.join(
    os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache"), "vb6dec", "snapshots")
HERE = os.path.dirname(os.path.abspath(__file__))
# module-level state that first_pass and what it calls change or fill in
# (not apiarity._cache: open system DLLs, which cannot be pickled and are simply
# opened again; VBDoom's save died on kernel32's mmap and took the run with it)
STATE = [("vb6dec.apidb", ("_db", "_types", "_lower", "_consts", "_layouts", "_arrays", "_busy")),
         ("vb6dec.model", ("_loaded", "_RTINFO")),
         ("vb6dec.disasm", ("_trie",))]


def source_hash():
    h = hashlib.sha256()
    root = os.path.dirname(HERE)
    files = sorted(glob.glob(os.path.join(HERE, "*.py"))) + [os.path.join(root, "decompile.py")] + \
        sorted(glob.glob(os.path.join(root, "data", "*")))
    for fn in files:
        h.update(os.path.basename(fn).encode())
        with open(fn, "rb") as f:
            h.update(f.read())
    return h.hexdigest()


def _exe_hash(exe, mp):
    h = hashlib.sha256()
    for fn in (exe, mp):
        if fn:
            with open(fn, "rb") as f:
                h.update(f.read())
    return h.hexdigest()


def _path(exe, mp):
    return os.path.join(DIR, "%s.%s.snap" % (os.path.basename(exe), _exe_hash(exe, mp)[:16]))


def _private(path, want):
    """path is what it should be (not through a symlink), ours, and nobody else can write it."""
    try:
        st = os.lstat(path)
    except OSError:
        return False
    return want(st.st_mode) and st.st_uid == os.getuid() and not st.st_mode & 0o022


def _usable(path):
    if _private(os.path.dirname(path), stat.S_ISDIR) and _private(path, stat.S_ISREG):
        return True
    print("snapshot: ignoring %s (not private to this user)" % path, file=sys.stderr)
    return False


def save(exe, mp, m, out):
    """Write the model and the bodies (procedures that failed keep their exception's text)."""
    from .typelib import Registry, TypeLib
    t0 = time.perf_counter()
    os.makedirs(DIR, mode=0o700, exist_ok=True)
    if not _private(DIR, stat.S_ISDIR):
        print("snapshot: not saving, %s is not private to this user" % DIR, file=sys.stderr)
        return None
    final = _path(exe, mp)
    tmp = "%s.%d.tmp" % (final, os.getpid())
    state = {mod: {n: getattr(sys.modules[mod], n) for n in names if mod in sys.modules}
             for mod, names in STATE if mod in sys.modules}
    cls = (Registry.libs, Registry.extra_dir, Registry._scanned, TypeLib._files)
    sys.setrecursionlimit(max(sys.getrecursionlimit(), 200000))
    # (O_EXCL: this process's own new file, never one already there)
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as f:
            pickle.dump(source_hash(), f, protocol=pickle.HIGHEST_PROTOCOL)
            pickle.dump((m, out, state, cls), f, protocol=pickle.HIGHEST_PROTOCOL)
        os.replace(tmp, final)
    except (OSError, TypeError, AttributeError, RecursionError, pickle.PicklingError) as e:
        # a snapshot only saves time: the run goes on without one
        print("snapshot: not saved: %s" % e, file=sys.stderr)
        os.unlink(tmp)                  # (exactly the file made above)
        return None
    print("snapshot: saved %s (%.0f MB, %.1f s)" % (final, os.path.getsize(final) / 1e6,
                                                    time.perf_counter() - t0), file=sys.stderr)
    return final


def find(exe, mp):
    """The snapshot to use for this EXE, or None."""
    p = _path(exe, mp)
    if not os.path.exists(p) or not _usable(p):
        return None
    with open(p, "rb") as f:
        src = pickle.load(f)
    if src == source_hash():
        return p
    if os.environ.get("VB6DEC_SNAPSHOT_ANY"):
        print("snapshot: VB6DEC_SNAPSHOT_ANY: %s is from another source state" % p,
              file=sys.stderr)
        return p
    return None


def load(path):
    from .typelib import Registry, TypeLib
    import importlib
    import gc
    t0 = time.perf_counter()
    enabled = gc.isenabled()
    gc.disable()
    # (the classes pickle by name: their modules first)
    for mod, _ in STATE:
        importlib.import_module(mod)
    with open(path, "rb") as f:
        pickle.load(f)                  # (the source hash: find looked at it)
        m, out, state, cls = pickle.load(f)
    if enabled:
        gc.enable()
    # (a set of id()s, from the process that wrote it: render.is_vbglobal makes it again)
    m.__dict__.pop("_vbglobal_funcs", None)
    for mod, vals in state.items():
        for n, v in vals.items():
            setattr(sys.modules[mod], n, v)
    Registry.libs[:] = cls[0]
    Registry.extra_dir, Registry._scanned = cls[1], cls[2]
    TypeLib._files = cls[3]
    print("snapshot: loaded %s in %.1f s" % (path, time.perf_counter() - t0), file=sys.stderr)
    return m, out
