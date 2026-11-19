"""Whole-program model: project structures + type libraries + procedures,
with name resolution for addresses (imports, Declare thunks, events,
procedures, vtable slots)."""
import os

from .project import Project
from .typelib import TypeLib, Registry
from . import disasm
from . import msfiles

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_LIBS = [msfiles.find("VB6.OLB"), msfiles.find("MSVBVM60.DLL"), msfiles.find("STDOLE2.TLB")]

_loaded = False


def load_libs():
    global _loaded
    if _loaded:
        return
    for p in DEFAULT_LIBS:
        if os.path.exists(p):
            try:
                Registry.add(TypeLib.from_file(p))
            except Exception as e:          # pragma: no cover - diagnostics only
                print("warning: %s: %s" % (p, e))
    # VBRUN, the runtime's second library: PropertyBag, for a UserControl's ReadProperties
    if os.path.exists(DEFAULT_LIBS[1]):
        try:
            Registry.add(TypeLib.from_file(DEFAULT_LIBS[1], 3))
        except Exception as e:              # pragma: no cover - diagnostics only
            print("warning: VBRUN: %s" % e)
    _loaded = True


def read_map(path):
    """LINK /MAP output -> {VA: (object, procedure)} for VB procedures."""
    import re
    out = {}
    for line in open(path, errors="replace"):
        m = re.match(r"\s*[0-9a-f]{4}:[0-9a-f]{8}\s+\?(\w+)@(\w+)@@\S*\s+([0-9a-f]{8})", line)
        if m:
            out[int(m.group(3), 16)] = (m.group(2), m.group(1))
    return out


def event_func(ev, j, nevents=None):
    """The event at a control's event table slot j.  For a VB control the
    events interface is a vtable one and slot j is its vtable slot 0x0c +
    4*j (after IUnknown).  An OCX's events are a dispinterface, and VB puts
    its own extender events (GotFocus, Validate, OLE drag...) first: the
    library's events are the LAST len(funcs) slots of the table
    (tests/t17ocxev: a ListView has 28 slots for 19 events, so slot 12 is
    ItemClick, the library's fourth)."""
    if ev is None:
        return None
    if getattr(ev, "kind", None) == 4:
        fs = list(ev.funcs)
        i = j - ((nevents - len(fs)) if nevents else 0)
        return fs[i] if 0 <= i < len(fs) else None
    for f in ev.funcs:
        if f.vtoff == 0x0c + 4 * j:
            return f
    return None


class Unsupported(Exception):
    """A program this decompiler cannot do, in words for whoever runs it."""


def refuse(prj):
    """What would come out as nonsense, with no error to show for it.  (VB6DEC_TRY: all the same.)"""
    if os.environ.get("VB6DEC_TRY"):
        return
    if any(n.startswith("MSVBVM50.DLL!") for n in prj.im.imports.values()):
        raise Unsupported("This program was made with Visual Basic 5.  Only Visual Basic 6 is supported so far.")
    if prj.pcode:
        raise Unsupported("This program was compiled to p-code.  Only native code is supported so far.")


class Model:
    def __init__(self, path, mapfile=None):
        msfiles.program = path
        load_libs()
        self.truth = read_map(mapfile) if mapfile else {}
        self.fixed_slots = {}       # (proc VA, frame offset) -> n: String * n slots
        self.prj = prj = Project(path)
        self.im = prj.im
        refuse(prj)
        self._load_ocx_for_controls()
        self.rtnames = self._runtime_ordinals()
        self.declares = disasm.find_declare_stubs(prj)
        self.declare_argc = {}
        self.proc_returns = {}      # proc VA -> True when callers use its value
        self.static_slot = {}       # class object index -> member offset of its Static block pointer
        self.global_names = {}
        self.global_types = {}      # global VA -> interface (from __vbaNew2)
        self.param_known = {}       # (proc VA, index) -> True when the type has evidence
        self.ret_types = {}         # proc VA -> result type of a Function
        self.arg_types = {}         # (proc VA, index) -> types callers pass
        self.plists = {}            # proc VA -> [(off, name, type, byref)]
        self.param_udt = {}         # (proc VA, arg index) -> UDT name
        self.param_size = {}        # (proc VA, arg index) -> struct byte size
        self.struct_hint = {}       # proc VA -> {ebp offset: (UDT name, size)}
        self.procs = disasm.discover(prj)
        self.methods = self._method_tables()
        self._build_ifaces()
        self.names = {}
        self._name_procs()

    # ---- object procedures ----------------------------------------------
    public_names = {}

    def ocx_coclass(self, ctl):
        """The coclass of an ActiveX control, from its form's designer data."""
        cache = self.__dict__.setdefault("_ocx_class", None)
        if cache is None:
            cache = self._ocx_class = {}
            from . import frm
            for o in self.prj.objects:
                if o.kind not in ("Form", "UserControl") or not any(c.guid and Registry.find_guid(c.guid) is None
                                               for c in o.controls):
                    continue
                try:
                    tree = frm.form_tree(self, o)
                except Exception:
                    tree = None
                progid = {}

                def walk(c):
                    for ch in c.children:
                        if ch.progid:
                            progid[ch.name.lower()] = ch.progid
                        walk(ch)
                if tree is not None:
                    walk(tree)
                for c in o.controls:
                    p = progid.get(c.name.lower())
                    if p:
                        guid = frm.coclass_guid(p)
                        co = Registry.find_guid(guid) if guid else None
                        if co is not None:
                            cache[c.va] = co
        if ctl.va not in cache and getattr(ctl, "progid", None):
            from . import frm
            guid = frm.coclass_guid(ctl.progid)
            cache[ctl.va] = Registry.find_guid(guid) if guid else None
        return cache.get(ctl.va)

    def control_arrays(self, obj):
        """Names of a form's control arrays (frm.array_names), cached."""
        cache = self.__dict__.setdefault("_control_arrays", {})
        if obj.index not in cache:
            names = set()
            if obj.kind in ("Form", "UserControl"):
                from . import frm
                try:
                    names = frm.array_names(self, obj)
                except Exception:
                    names = set()
            cache[obj.index] = names
        return cache[obj.index]

    def _method_tables(self):
        """Per object, its procedures in declaration order.

        Every procedure of a form/class has a thunk "sub [esp+4],N; jmp proc"
        (N = 0xffff for non-event procedures).  The thunks of one object are
        consecutive and in source order; the object's vtable entries for its
        own procedures follow the same order.
        """
        import struct
        im, prj = self.im, self.prj
        text = [s for s in im.sections if s[2] == ".text"][0]
        data = im.bytes(text[0], prj.code_start - text[0])
        stubs = []
        i = data.find(b"\x81\x6c\x24\x04")
        while i >= 0:
            va = text[0] + i
            if data[i + 8:i + 9] == b"\xe9":
                tgt = va + 13 + struct.unpack_from("<i", data, i + 9)[0]
                stubs.append((va, tgt, struct.unpack_from("<I", data, i + 4)[0]))
            i = data.find(b"\x81\x6c\x24\x04", i + 1)
        runs, cur = [], []
        for st in stubs:
            if cur and st[0] != cur[-1][0] + 13:
                runs.append(cur)
                cur = []
            cur.append(st)
        if cur:
            runs.append(cur)
        out = {}
        self.vtable_order = {}
        for o in prj.objects:
            o.link_slots = self._link_slots(o)
            refs = {stub for c in o.controls for _, stub, _ in c.events}
            found = [run for run in runs if refs & {s[0] for s in run}]
            if not found and any(o.link_slots):
                found = [[]]        # no event stub to find its thunks by (a class
                #                     without Initialize/Terminate): the vtable has them
            for run in found[:1]:
                if True:
                    # a thunk can sit outside its object's run (t04 RetSng): the
                    # object's own vtable lists them all; VA order is source order
                    extra = {t for t in o.link_slots if t is not None}
                    have = {t for _, t, _ in run}
                    for t in extra - have:
                        run = run + [(None, t, 0xffff)]
                    run = sorted(run, key=lambda r: r[1])
                    out[o.index] = [t for _, t, _ in run]
                    # vtable: Public procedures (they keep their names in the
                    # object descriptor) first, then Private ones, each in
                    # source order.  Forms cannot have Public Declares, so extra
                    # (unnamed) entries in the name list are Declares.
                    procs = [t for _, t, _ in run]
                    names = list(o.method_names)
                    if len(names) > len(procs):
                        names = names[len(names) - len(procs):]
                    names += [None] * (len(procs) - len(names))
                    for t, n in zip(procs, names):
                        if n:
                            self.public_names[t] = n
                    pub = [t for t, n in zip(procs, names) if n]
                    priv = [t for t, n in zip(procs, names) if not n]
                    self.vtable_order[o.index] = pub + priv
                    for _, t, adj in run:
                        p = self.procs.get(t)
                        if p is None:
                            p = self.procs[t] = disasm.Proc(prj, t)
                            p.why = None
                        if p.owner is None:
                            p.owner = o
                            p.kind = "method"
        self.procs = dict(sorted(self.procs.items()))
        return out

    def _link_slots(self, o):
        """The object's own vtable (OptionalObjectInfo +0x28 count, +0x30
        array; slot i is at the own-members base + 4*i): per slot the procedure
        it jumps to, or None for the runtime's variable accessors."""
        import struct
        im = self.im
        opt = getattr(o, "opt", None)
        if opt is None:
            return []
        n, arr = im.u16(opt + 0x28), im.u32(opt + 0x30)
        out = []
        for i in range(n if im.valid(arr) and n < 4096 else 0):
            p = im.u32(arr + 4 * i)
            t = None
            if im.valid(p):
                b = im.bytes(p, 13)
                if b[:4] == b"\x81\x6c\x24\x04" and b[8] == 0xe9:
                    t = p + 13 + struct.unpack("<i", b[9:13])[0]
                elif b[0] == 0xe9:
                    t = p + 5 + struct.unpack("<i", b[1:5])[0]
            out.append(t if t is not None and self.prj.code_start <= t < self.prj.code_end else None)
        return out

    def _build_ifaces(self):
        """Member descriptors and vtable interfaces of the project's forms
        and classes (see members.py, projiface.py)."""
        from . import members, projiface
        im = self.im
        self.members = {}           # object index -> ObjectMembers
        self.proj_ifaces = {}       # object index -> ProjIface
        self.iface_by_iid = {}      # default interface IID -> ProjIface
        self.events_of_iid = {}     # event interface IID -> object raising the events
        self.obj_by_info = {}       # ObjectInfo VA (what __vbaNew receives) -> object
        for o in self.prj.objects:      # (a UserControl may hold another)
            self.members[o.index] = members.ObjectMembers(self.prj, o)
        # UserControls first: a form's interface asks for the class of each control on it
        for o in sorted(self.prj.objects, key=lambda x: x.kind != "UserControl"):
            self.obj_by_info[o.info] = o
            om = self.members[o.index]
            if not om.valid:
                continue
            procs = self.methods.get(o.index, [])
            ms = list(om.methods)
            if len(ms) > len(procs):
                ms = ms[len(ms) - len(procs):]      # aligned like the name table
            ms += [None] * (len(procs) - len(ms))
            om.methods = ms
            base = self.me_interface(o) if o.kind != "Class" else None
            pi = projiface.build(self, o, om, procs, base)
            self.proj_ifaces[o.index] = pi
            opt = getattr(o, "opt", None)
            if opt is not None:
                for cnt, tab, d in ((0x10, 0x1c, self.iface_by_iid), (0x18, 0x14, None)):
                    n, t = im.u32(opt + cnt), im.u32(opt + tab)
                    for i in range(n if im.valid(t) and n < 16 else 0):
                        g = im.u32(t + 4 * i)
                        if not im.valid(g):
                            continue
                        if d is not None:
                            d[im.guid(g)] = pi
                            pi.guid = pi.guid or im.guid(g)
                        else:
                            self.events_of_iid[im.guid(g)] = o
                # the object's other names: the GUIDs before its CLSID and after, in a row.  A
                # variable of the form's own type calls by the one right before the CLSID
                # (DarwinBots Charts(i).graf.hWnd, a target's UserControl: vtbl_58, vtbl_35C)
                clsid = im.u32(opt + 4)
                if im.valid(clsid) and im.valid(clsid - 16):
                    for g in (clsid - 16, clsid):
                        self.iface_by_iid.setdefault(im.guid(g), pi)
            # each Public member is the procedure in its vtable slot
            for m in om.methods:
                if m is None or m.vtoff is None:
                    continue
                e = pi.entries.get(m.vtoff)
                p = self.procs.get(e[1]) if e is not None and e[0] == "proc" else None
                if p is not None:
                    p.member = m
                    m.proc = p.va

    # ---- type libraries -------------------------------------------------
    def _load_ocx_for_controls(self):
        """Load OCX type libraries until every control GUID resolves."""
        Registry.extra_dir = True
        missing = {c.guid for o in self.prj.objects for c in o.controls
                   if c.guid and Registry.find_guid(c.guid) is None}
        for path in msfiles.ocx_files() if missing else ():
            lib = TypeLib.cached(path)
            if lib is None:
                continue
            if missing & set(lib.by_guid):
                Registry.add(lib)
                missing -= set(lib.by_guid)
            if not missing:
                break

    def ocx_array_controls(self):
        """The controls that are control arrays, by their address in the control list: an OCX's
        array has an events IID of its own, no sign of the Index its events start with."""
        cache = self.__dict__.get("_ocx_arrays")
        if cache is None:
            cache = self._ocx_arrays = set()
            for o in self.prj.objects:
                if o.kind in ("Form", "UserControl"):
                    arrays = self.control_arrays(o)
                    cache.update(c.va for c in o.controls if (c.name or "").lower() in arrays)
        return cache

    def own_usercontrol(self, ctl):
        """The project's UserControl a control on a form is an instance of, from the class
        name in the form's designer data (t25usercontrol.UC1): the GUID in its control list
        is referenced by nothing else."""
        cache = self.__dict__.get("_own_uc")
        if cache is None:
            cache = self._own_uc = {}
            ucs = {o.name.lower(): o for o in self.prj.objects if o.kind == "UserControl"}
            if ucs:
                from . import frm
                for o in self.prj.objects:
                    if o.kind not in ("Form", "UserControl"):
                        continue
                    try:
                        tree = frm.form_tree(self, o)
                    except Exception:
                        continue
                    found = {}

                    def walk(c):
                        for ch in c.children:
                            if ch.progid:
                                found[ch.name.lower()] = ch.progid
                            walk(ch)
                    if tree is not None:
                        walk(tree)
                    arrays = self.control_arrays(o)
                    for c in o.controls:
                        pid = found.get((c.name or "").lower())
                        uc = ucs.get(pid.rpartition(".")[2].lower()) if pid else None
                        if uc is not None:
                            cache[c.va] = uc
                            if c.name.lower() in arrays:
                                c.array_events = True
        return cache.get(ctl.va)

    def control_class(self, ctl):
        """(coclass, default interface, events interface) for a control."""
        own = self.own_usercontrol(ctl)
        if own is not None:
            from . import projiface
            return None, self.proj_ifaces.get(own.index), projiface.OwnEvents(self.members[own.index])
        ev = Registry.find_guid(ctl.guid) if ctl.guid else None
        if ev is None and ctl.guid:
            # a control array's events (with the Index parameter first) have
            # the class's events IID plus one, in no type library (t10carr:
            # chk {33AD4EFB-..}, CheckBoxEvents {33AD4EFA-..})
            g = ctl.guid
            alt = "{%08X%s" % ((int(g[1:9], 16) - 1) & 0xFFFFFFFF, g[9:])
            ev = Registry.find_guid(alt)
            if ev is not None:
                ctl.array_events = True
        if ev is None:
            # an ActiveX control: the control list has the events IID of VB's
            # own .oca copy of the library, which only the IDE that built the
            # program had; the designer data names the class
            # (MSComctlLib.ProgressBar), whose coclass has both interfaces
            co = self.ocx_coclass(ctl)
            if co is None:
                return None, None, None
            if ctl.va in self.ocx_array_controls():
                ctl.array_events = True     # (Index first: DarwinBots RepopAmountUpDown_Change)
            default = events = None
            for h, f in co.impls:
                r = co.lib.resolve_href(h)
                if f & 2:
                    events = events or r
                elif f & 1 or default is None:
                    default = r if f & 1 or default is None else default
            return co, default, events
        for lib in Registry.libs:
            for t in lib.types:
                if t.kind == 5:
                    impl = [lib.resolve_href(h) for h, f in t.impls]
                    if ev in impl:
                        default = next((lib.resolve_href(h) for h, f in t.impls if f & 1 and not f & 2), None)
                        if default is None:
                            default = impl[0]
                        return t, default, ev
        return None, None, ev

    # ---- names --------------------------------------------------------------
    def _runtime_ordinals(self):
        import pefile
        path = DEFAULT_LIBS[1]
        out = {}
        if os.path.exists(path):
            pe = pefile.PE(path, fast_load=True)
            pe.parse_data_directories(directories=[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_EXPORT"]])
            for e in pe.DIRECTORY_ENTRY_EXPORT.symbols:
                if e.name:
                    out[e.ordinal] = e.name.decode()
        for slot, n in list(self.im.imports.items()):
            if n.startswith("#") and int(n[1:]) in out:
                self.im.imports[slot] = out[int(n[1:])]
        return out

    def implements_of(self, obj):
        """Interfaces an object Implements: the member descriptor lists each as
        a variable of the class type with no vtable slot (vtoff 0xFFFF)."""
        om = self.members.get(obj.index)
        if om is None:
            return set()
        return {v.name for v in om.vars if v.vtoff == 0xFFFF and v.type == "Class"}

    def _name_procs(self):
        prj = self.prj
        for p in self.procs.values():
            why = getattr(p, "why", None)
            mem = getattr(p, "member", None)
            if why and why[0] == "event" and mem is not None and mem.kind != "event" and \
                    p.owner is not None and mem.name and \
                    mem.name.split("_")[0] in self.implements_of(p.owner):
                # an Implements method (IShape_Area): reached through the
                # interface's dispatch table like an event, but a member
                why = None
            if why and why[0] == "event":
                _, c, j = why
                _, _, ev = self.control_class(c)
                evf = event_func(ev, j, getattr(c, "nevents", None))
                evname = evf.name if evf is not None else "Event%d" % j
                # (index -1: the object itself, and its WithEvents variables by their own names, as in
                # projiface.  btn_Click printed Form_Click, without a map: no event ever reached it)
                own = c.index == -1 and (not c.name or c.name == p.owner.name or
                                         c.name in ("Form", "Class", "UserControl"))
                cname = "Form" if c.name == "Form" or own and p.owner.kind == "Form" else c.name
                if p.owner.kind in ("Class",) and own:
                    cname = "Class"
                    if evf is None:
                        # a class's own events have no type library entry; the
                        # slots are Initialize and Terminate (t03c, behaviour test)
                        evname = {0: "Initialize", 1: "Terminate"}.get(j, evname)
                if p.owner.kind == "UserControl" and own:
                    cname = "UserControl"
                p.name = "%s_%s" % (cname, evname)
                p.kind = "event"
                p.event = evf
                # an OCX's events are a dispinterface: its handler's scalars
                # are ByRef and its objects ByVal (typelib.by_reference)
                p.event_dispatch = getattr(ev, "kind", None) == 4 and not getattr(ev, "own", False)
            elif why and why[0] == "submain":
                p.name = "Main"
                p.kind = "proc"
        for p in self.procs.values():
            if p.va in self.truth:
                p.truth = self.truth[p.va]
        for va, n in self.public_names.items():
            p = self.procs.get(va)
            if p is not None:
                if p.name is None:
                    p.name = n
                p.public = True     # an event handler too: other forms call it
                #                     (a target Form16.Command1_Click)
        n = 0
        for p in self.procs.values():
            if p.name is None:
                n += 1
                p.name = "Proc_%X" % p.va
                p.kind = "proc"
            self.names[p.va] = p.name

    def callee_name(self, va):
        if va in self.names:
            return self.names[va]
        if va in self.declares:
            return self.declares[va][1]
        return None


# ---------------------------------------------------------------- additions
import json as _json


def _load_rtinfo():
    p = os.path.join(os.path.dirname(HERE), "data", "runtime_auto.json")
    try:
        return _json.load(open(p))
    except Exception:
        return {}


_RTINFO = None


def _rtinfo():
    global _RTINFO
    if _RTINFO is None:
        _RTINFO = _load_rtinfo()
    return _RTINFO


def _model_rtinfo(self):
    return _rtinfo()


def _global_name(self, va):
    """Name for a .data address (module globals are not named in the EXE)."""
    if va in self.global_names:
        return self.global_names[va]
    return "global_%X" % va


def _class_of_desc(self, va):
    """__vbaNew2 class descriptor {?, &CLSID, &IID, ...} -> (coclass, iface)."""
    im = self.im
    if va is None or not im.valid(va):
        return None, None
    try:
        clsid, iid = im.u32(va + 4), im.u32(va + 8)
        cls = Registry.find_guid(im.guid(clsid)) if im.valid(clsid) else None
        itf = Registry.find_guid(im.guid(iid)) if im.valid(iid) else None
        if itf is None and im.valid(iid):
            itf = self.iface_by_iid.get(im.guid(iid))       # a project form or class
    except Exception:
        return None, None
    return cls, itf


Model.class_of_desc = _class_of_desc


def _me_interface(self, obj):
    """Default interface whose vtable the object's own vtable starts with."""
    base = {"Form": "_Form", "MDIForm": "_MDIForm", "UserControl": "_UserControl",
            "PropertyPage": "_PropertyPage", "UserDocument": "_UserDocument"}.get(obj.kind)
    if base is None:
        return None
    for lib in Registry.libs:
        t = lib.by_name.get(base.lower())
        if t is not None:
            return t
    return None


class DesignerControl:
    """A control the control list leaves out (an ActiveX control without
    event handlers: a target Form10' CommonDialog): found in the form's
    designer data, whose record ID is the getter index for every control."""
    guid = None
    flags = 0
    nevents = 0
    events = ()

    def __init__(self, obj, rec):
        self.name, self.index, self.progid = rec.name, rec.cid, rec.progid
        self.va = ("designer", obj.index, rec.cid)


def _control_by_index(self, obj, idx):
    for c in obj.controls:
        if c.index == idx:
            return c
    extra = self.__dict__.setdefault("_designer_controls", {})
    if obj.index not in extra:
        found = {}
        if obj.kind in ("Form", "UserControl"):
            from . import frm
            listed = {c.index for c in obj.controls}
            try:
                tree = frm.form_tree(self, obj)
            except Exception:
                tree = None

            def walk(c):
                for ch in c.children:
                    if ch.cid not in listed and ch.cid not in found:
                        found[ch.cid] = DesignerControl(obj, ch)
                    walk(ch)
            if tree is not None:
                walk(tree)
        extra[obj.index] = found
    return extra[obj.index].get(idx)


Model.rtinfo = property(_model_rtinfo)
Model.global_name = _global_name
Model.me_interface = _me_interface
Model.control_by_index = _control_by_index


def _all_controls(self, obj):
    """The control list and the controls only the designer data has."""
    self.control_by_index(obj, None)            # (fills the designer cache)
    return list(obj.controls) + list(self.__dict__["_designer_controls"][obj.index].values())


Model.all_controls = _all_controls


def _global_array_templates(self):
    """Bounds of module-level fixed arrays.  Their SAFEARRAY descriptors sit
    in .data (the global slot holding pvData at descriptor + 0x0C) and are
    zero in the file; the runtime fills them from static images in .text:
    u16 cDims, u16 fFeatures (0x92), u32 cbElements, u32 0, u32 0, then
    cDims x (u32 cElements, u32 lLbound) with the LAST dimension first,
    then a u16 VARTYPE.  The images come in the same order as the
    descriptors (gh_qr: LNG_POW2(0 To 31), ECC(0 To 3, 0 To 40), ...).
    Returns [(cbElements, [(lb, n) per dimension, first first], vt)]."""
    import struct
    im = self.im
    out = []
    for sec in im.pe.sections:
        if not sec.Name.rstrip(b"\0") == b".text":
            continue
        start = im.base + sec.VirtualAddress
        data = im.bytes(start, min(sec.Misc_VirtualSize, len(im.mem) - sec.VirtualAddress))
        i = 0
        while True:
            i = data.find(b"\x92\x00", i + 1)
            if i < 2:
                if i < 0:
                    break
                continue
            o = i - 2
            nd = struct.unpack_from("<H", data, o)[0]
            if not 1 <= nd <= 8 or o + 16 + 8 * nd + 2 > len(data):
                continue
            cb, locks, pv = struct.unpack_from("<III", data, o + 4)
            if not 0 < cb <= 0x1000 or locks or pv:
                continue
            bounds = [struct.unpack_from("<Ii", data, o + 16 + 8 * k) for k in range(nd)]
            if any(not 0 < n < 0x1000000 or abs(lb) > 0x1000000 for n, lb in bounds):
                continue
            vt = struct.unpack_from("<H", data, o + 16 + 8 * nd)[0]
            out.append((start + o, cb, [(lb, n) for n, lb in reversed(bounds)], vt))
    return out


Model.global_array_templates = _global_array_templates


def _global_array_slots(self):
    """{pvData slot: (cbElements, [(lb, n) per dimension, first first], vt,
    fFeatures, image VA, record descriptor VA or None)} for module-level fixed
    arrays, from each module's variable
    table (ObjectInfo +08h public_bytes for module_public's data, +0Ch
    static_bytes for module_static's).  In it a u16 pair (offset, flags with
    low byte 5) stands
    16 bytes before every array's static SAFEARRAY image: the offset of the
    array's descriptor in that data, its pvData slot 0Ch further (VBDoom
    g_game: (1Ch, 5) before consistancy's image, module_public 48A9F8h,
    pvData slot 48AA20h).  The image as global_array_templates reads it;
    fFeatures 12h (an array of UDTs) has no VARTYPE after the bounds."""
    import struct
    im = self.im
    tables = []
    for o in self.prj.objects:
        if o.kind != "Module":
            continue
        for tab, base in ((o.public_bytes, o.module_public), (o.static_bytes, o.module_static)):
            if tab and base and im.valid(tab):
                # (a module's Static arrays also by the name decompile.static_slots gives the block's
                # variables: Static KeyList(3374) As Long came out without bounds, never allocated,
                # and the first store was error 91; ChessBrainVB CalcUniqueKey)
                virt = 0x7F000000 + o.index * 0x10000 if tab == o.static_bytes else None
                tables.append((tab, base, virt))
    tables.sort(key=lambda t: t[:2])
    out = {}
    for i, (tab, base, virt) in enumerate(tables):
        # (the table's first word is its length; further on lie other tables, whose fixed arrays
        # look the same: a class's Static hist(4) As Long made K.kierros a module array, t87nested)
        end = tab + im.u16(tab)
        p = tab
        def image(q):
            """(cbElements, bounds, vt, fFeatures, end) of a static SAFEARRAY
            image at q, or None."""
            if not im.valid(q + 18):
                return None
            nd, ff = im.u16(q), im.u16(q + 2)
            if not 1 <= nd <= 8 or ff not in (0x12, 0x92, 0x192) or \
                    not im.valid(q + 16 + 8 * nd + 2):
                return None
            cb, locks, pv = struct.unpack("<III", im.bytes(q + 4, 12))
            bounds = [struct.unpack("<Ii", im.bytes(q + 16 + 8 * k, 8)) for k in range(nd)]
            if not 0 < cb <= 0x10000 or locks or pv or \
                    any(not 0 < n < 0x1000000 or abs(lb) > 0x1000000 for n, lb in bounds):
                return None
            vt = im.u16(q + 16 + 8 * nd) if ff != 0x12 else 0x24
            return cb, [(lb, n) for n, lb in reversed(bounds)], vt, ff, q + 16 + 8 * nd

        def pair(at):
            return im.valid(at + 4) and im.u16(at + 2) & 0xff == 5
        while p + 36 <= end and im.valid(p + 36):
            off, flags = im.u16(p), im.u16(p + 2)
            if flags & 0xff != 5 or flags & 0x100:  # (100h: a dynamic array, no image)
                p += 2
                continue
            # the image 16 bytes on; an array of records has 16 bytes more
            # (VBDoom players: a pointer to the record's descriptor among them)
            img = image(p + 16)
            if img is None and not pair(p + 16):
                img = image(p + 32)
            if img is None:
                p += 2
                continue
            cb, dims, vt, ff, nxt = img
            at = p + 16 if image(p + 16) else p + 32
            # an array of records: its record descriptor 12 bytes after the pair
            rd = im.u32(p + 12) if at == p + 32 and im.valid(p + 16) else 0
            out[base + off + 12] = (cb, dims, vt, ff, at, rd if rd and im.valid(rd) else None)
            if virt is not None:
                out[virt + off + 12] = out[base + off + 12]
            p = nxt
    return out


Model.global_array_slots = _global_array_slots


def _object_array_slots(self, o):
    """{member offset of the pvData slot: (cbElements, [(lb, n)...], vt, record descriptor)} of the
    fixed arrays of a class or form, from its variable tables, and of its Static block's at the
    offsets lift.static_var gives them.  (No procedure need index one for it to be an array:
    cCipher m_EmptyArray only goes to CopyMemory.)"""
    from . import records
    from .lift import Lifter
    cache = self.__dict__.setdefault("_object_slots", {})
    got = cache.get(o.index)
    if got is not None:
        return got
    got = {}
    im = self.im
    for tab, base in ((o.public_bytes, 0), (o.static_bytes, Lifter.STATIC_BASE)):
        if not tab or not im.valid(tab):
            continue
        ents, _ = records.table_entries(im, tab)
        for off, kind, info in ents:
            if info and info[0] == "static":
                got[base + off + 12] = (info[1], info[2], info[5], info[4])
    cache[o.index] = got
    return got


Model.object_array_slots = _object_array_slots


def _module_records(self):
    """{address: (record descriptor VA or None, room in bytes)} of the records among module
    variables, from the modules' variable tables: every one is there (kind 69h with its descriptor,
    09h a record only of numbers, the room up to the next variable the table lists or the end of
    the module's data).  The code need never show one whole: Public K As TKaikki, its fields only
    reached one by one, each came out a variable of its own."""
    from . import records
    cached = self.__dict__.get("_module_recs")
    if cached is not None:
        return cached
    im = self.im
    out = {}
    for o in self.prj.objects:
        if o.kind != "Module":
            continue
        for tab, base in ((o.public_bytes, o.module_public), (o.static_bytes, o.module_static)):
            if not tab or not base or not im.valid(tab):
                continue
            ents, _ = records.table_entries(im, tab)
            # (a public block's last two dwords aren't the program's: the Static block's pointer)
            end = im.u16(tab + 2) - (8 if tab == o.public_bytes else 0)
            offs = sorted({e[0] for e in ents} | {end})
            for off, kind, info in ents:
                if not info or info[0] != "record":
                    continue
                nxt = next((x for x in offs if x > off), off + 4)
                d = info[1] if kind & 0x60 and im.valid(info[1]) else None
                out[base + off] = (d, nxt - off)
    self._module_recs = out
    return out


Model.module_records = _module_records


def _empty_strings(self):
    """Addresses of the program's empty string constant(s): an empty BSTR in
    .text ([u32 0][u16 0], which vb_string refuses: six zero bytes are no
    proof) that some instruction hands to a string runtime function (the
    next call within 8 instructions is an import with "str" in its name:
    mov edx, 4EC3DCh / call __vbaStrCopy).  a target used its one 3199
    times and printed &H4EC3DC 560 times; VBDoom pushes 404206h, which has
    the same bytes, as a Long flag set (MF_SOLID Or ...) to its own DM."""
    got = self.__dict__.get("_empty_strings")
    if got is not None:
        return got
    from capstone import x86 as X
    im = self.im
    cands, got = {}, set()
    for p in self.procs.values():
        addrs = sorted(p.insns)
        for i, a in enumerate(addrs):
            ins = p.insns[a]
            for op in ins.operands:
                if op.type != X.X86_OP_IMM:
                    continue
                v = op.imm & 0xffffffff
                if v in got or cands.get(v) is False:
                    continue
                if v not in cands:
                    try:
                        cands[v] = v not in self.procs and im.valid(v - 4) and \
                            im.valid(v + 1) and im.section_of(v) == ".text" and \
                            im.u32(v - 4) == 0 and im.u16(v) == 0
                    except Exception:
                        cands[v] = False
                if not cands[v]:
                    continue
                for j in range(i + 1, min(i + 8, len(addrs))):
                    y = p.insns[addrs[j]]
                    if y.mnemonic != "call":
                        continue
                    o = y.operands[0]
                    name = im.imports.get(o.mem.disp) if o.type == X.X86_OP_MEM and \
                        o.mem.base == 0 and o.mem.index == 0 else None
                    if name and "str" in name.lower():
                        got.add(v)
                    break
    self._empty_strings = got
    return got


Model.empty_strings = _empty_strings
