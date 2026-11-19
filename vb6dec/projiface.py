"""Interfaces of the project's own forms and classes.

VB gives every form and class a COM interface: IDispatch (classes) or
_Form plus one getter per control (forms), then the object's own members.
Public variables come first (accessor pairs: Get, Let; Variants also Set),
then Public procedures, then every other procedure in source order, then
the hidden accessors of WithEvents variables (Get, Let, Set).  The member
descriptors (members.py) give the Public part with names and types; the
rest follows from the procedure order.

The interface is shaped like a type library one (all_funcs, Func.vtoff,
invkind, params) so calls through it resolve the way control calls do.
"""
from .typelib import TypeDesc, Param, Func
from . import members as M

VT_OF = {"Byte": 17, "Boolean": 11, "Integer": 2, "Long": 3, "Single": 4, "Double": 5,
         "Currency": 6, "Date": 7, "String": 8, "Object": 9, "Variant": 12, "Class": 9}
INVKIND = {"method": 1, "get": 2, "let": 4, "set": 8}
FORM_OWN = 0x400        # forms: own members start 256 control getters after _Form
CLASS_OWN = 0x1c        # classes: after IDispatch


def tdesc(type_name, byref=False):
    t = TypeDesc(VT_OF.get(type_name, 9))   # (else another library's object: As Control, As Form)
    return TypeDesc(26, t) if byref else t


class ProjIface:
    """Stand-in for a TypeInfo describing a project form or class."""

    kind = 4

    def __init__(self, obj, base=None):
        self.obj = obj
        self.name = obj.name
        self.guid = None
        self.base = base
        self.funcs = []
        self.lib = None
        self.impls = []
        self.cbvft = base.cbvft if base is not None else CLASS_OWN
        self.entries = {}       # vtable offset -> ("proc", va) / ("var", name, k) / ...

    def __repr__(self):
        return "<project %s %s>" % (self.obj.kind, self.name)

    def bases(self):
        return [self.base] if self.base is not None else []

    def all_funcs(self):
        out = list(self.base.all_funcs()) if self.base is not None else []
        return out + self.funcs

    def by_vtoff(self, off, invkind=None):
        for f in self.all_funcs():
            if f.vtoff == off and (invkind is None or f.invkind == invkind):
                return f
        return None


def func(name, vtoff, invkind, params):
    f = Func()
    f.name, f.vtoff, f.invkind, f.params = name, vtoff, invkind, params
    return f


def accessors(type_name, slots):
    return ["get", "let", "set"] if slots >= 3 else ["get", "let"]


def build(model, o, om, procs, base):
    """ProjIface for object o.  procs: its procedure VAs in source order,
    parallel to om.methods."""
    pi = ProjIface(o, base)
    own = (base.cbvft + FORM_OWN) if base is not None else CLASS_OWN
    if base is not None:
        # frmX.cmdRun from another module: the control getter, as on Me
        for c in model.all_controls(o):
            if c.index >= 0 and c.name:
                f = func(c.name, base.cbvft + 4 * c.index, 2, [])    # returned in eax
                f.control = c
                f.ret_iface_obj = model.control_class(c)[1]
                if c.name.lower() in model.control_arrays(o):
                    from .lift import ControlArray      # frmX.chk(i) from elsewhere
                    f.ret_iface_obj = ControlArray(f.ret_iface_obj, c.name)
                pi.funcs.append(f)
    end = own
    for v in om.vars:
        for k, kind in enumerate(accessors(v.type, v.slots)):
            off = v.vtoff + 4 * k
            if kind == "get":
                ps = [Param("ret", tdesc(v.type or "Variant", True), 8 | 2)]
            else:
                # a Variant variable's Let/Set takes the value by reference (t04 VVar)
                ps = [Param("v", tdesc(v.type or "Variant", (v.type or "Variant") == "Variant"), 1)]
            pi.funcs.append(func(v.name, off, INVKIND[kind], ps))
            pi.entries[off] = ("var", v, kind)
            end = max(end, off + 4)
    slots = getattr(o, "link_slots", None) or []
    for i, t in enumerate(slots):          # the object's own vtable, exactly
        if t is not None:
            pi.entries[own + 4 * i] = ("proc", t)
    public = []
    for va, m in zip(procs, om.methods):
        if m is None or m.vtoff is None:
            continue
        if slots:
            e = pi.entries.get(m.vtoff)
            va = e[1] if e is not None and e[0] == "proc" else va
        m.proc = va
        public.append(va)
        ps = []
        for p in m.params:
            # the stack takes one dword for anything passed by reference
            prm = Param(p.name, tdesc(p.type or "Variant", p.byref or p.array),
                        16 if p.optional else 1)
            prm.paramarray = p.paramarray
            prm.default = p.default
            prm.array = p.array             # baOutput() As Byte
            ps.append(prm)
        if m.returns:
            ps.append(Param("ret", tdesc(m.ret_type or "Variant", True), 8 | 2))
        f = func(m.name, m.vtoff, INVKIND[m.kind], ps)
        f.memid = m.dispid                  # (a UserControl's members are called late, by this)
        if m.returns and m.ret_array:
            f.ret_array = VT_OF.get(m.ret_type, 12)    # returns an array of this VARTYPE
        pi.funcs.append(f)
        pi.entries[m.vtoff] = ("proc", va)
        end = max(end, m.vtoff + 4)
    if slots:
        end = own + 4 * len(slots)
        free = [own + 4 * i for i, t in enumerate(slots) if t is None and
                own + 4 * i not in pi.entries]
    else:
        for va in procs:
            if va not in public:
                pi.entries[end] = ("proc", va)
                end += 4
        free = None
    # WithEvents variables are listed with the controls (index -1, the
    # event source's IID as their "class"); their accessors (Get, Let, Set)
    # take the last slots, after the procedures
    we = [c for c in o.controls if c.index == -1 and c.name and c.name != o.name and
          c.name not in ("Form", "Class", "UserControl")]
    if free is not None:
        free = free[len(free) - 3 * len(we):] if len(free) >= 3 * len(we) else []
    k = 0
    for c in we:
        for kind in ("get", "let", "set"):
            if free is not None:
                if k < len(free):
                    pi.entries[free[k]] = ("withevents", c, kind)
                k += 1
            else:
                pi.entries[end] = ("withevents", c, kind)
                end += 4
    return pi


class OwnEvents:
    """The events a UserControl declares, shaped like an OCX's dispinterface: VB puts the
    extender's events first in a form's event table, the control's own take the last slots
    (t25usercontrol: 10 slots, Changed is slot 9).  Passing as declared, not a dispatch's."""

    kind = 4
    own = True
    name = None

    def __init__(self, om):
        self.funcs = []
        for e in om.events:
            ps = [Param(p.name, tdesc(p.type or "Variant", p.byref or p.array), 1) for p in e.params]
            self.funcs.append(func(e.name, None, 1, ps))
