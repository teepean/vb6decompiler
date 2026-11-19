"""Form designer data -> the "Begin VB.Form ... End" part of a .frm file.

A compiled form keeps its design-time properties as a byte stream.  Each
control is a record

    u32 length  u8 id  u16 namelen  name  u8 0  u8 type
    { u8 opcode  value }*
    u16 separator      FF01 children follow, FF03 next sibling, FF02 end of
                       the container's children

The opcode of a property is its DISPID - 0x10000 in VB6.OLB, so the type
library gives the name and the type that decides the encoding.  A few
properties use special encodings (the Left/Top/Width/Height block, fonts,
pictures, the form's flag bytes).  The format follows Semi VB Decompiler's
form reader; details were checked against the .frm sources of the test
projects.
"""
import struct

from .typelib import Registry

# control type byte -> VB6.OLB coclass
CTYPES = {0: "PictureBox", 1: "Label", 2: "TextBox", 3: "Frame", 4: "CommandButton",
          5: "CheckBox", 6: "OptionButton", 7: "ComboBox", 8: "ListBox", 9: "HScrollBar",
          10: "VScrollBar", 11: "Timer", 13: "Form", 16: "DriveListBox", 17: "DirListBox",
          18: "FileListBox", 19: "Menu", 20: "MDIForm", 22: "Shape", 23: "Line",
          24: "Image", 37: "Data", 38: "OLE", 40: "UserControl", 41: "PropertyPage",
          42: "UserDocument"}

SEP_CHILDREN, SEP_END, SEP_SIBLING, SEP_FORMEND, SEP_MENU = 0x01FF, 0x02FF, 0x03FF, 0x04FF, 0x05FF

# Enum-valued properties: the type library declares them Integer, but the
# designer stream stores one byte (list taken from Semi VB Decompiler's
# per-control readers).
BYTE_PROPS = {
    "MousePointer", "BorderStyle", "Alignment", "ScrollBars", "DragMode", "Appearance",
    "OLEDropMode", "OLEDragMode", "Style", "BackStyle", "MultiSelect", "FillStyle",
    "DrawStyle", "DrawMode", "WindowState", "LinkMode", "StartUpPosition", "PaletteMode",
    "IMEMode", "ScaleMode", "Align", "Shape", "Negotiate", "BOFAction", "EOFAction",
    "RecordsetType", "DefaultType", "DefaultCursorType", "OLEType", "UpdateOptions",
    "SizeMode", "AutoActivate", "OLETypeAllowed", "ClipBehavior", "HitBehavior",
    "LockControls", "NegotiateMenus", "Value", "WhatsThisHelp"}

# Properties a class stores that its interface does not list
EXTRA = {11: {7: ("Left", "i32"), 8: ("Top", "i32")},     # Timer
         # (Single in VB6.OLB, stored as dwords of twips: DarwinBots InfoForm X1 = 3 of a
         # ScaleMode 3 form is 2Dh; read as floats they printed X1 = 6.3e-44)
         23: {3: ("X1", "twips"), 4: ("Y1", "twips"), 5: ("X2", "twips"), 6: ("Y2", "twips")},
         2: {0x28: ("IMEMode", "byte")},                     # TextBox (no DISPID)
         # design-time only, so not in VB6.OLB (tools/frmprobe.py)
         13: {0x3D: ("LockControls", "bool"), 0x3E: ("NegotiateMenus", "bool")},
         19: {6: ("_Separator", "byte"), 7: ("_Indent", "byte"), 8: ("Shortcut", "shortcut")}}

# Menu Shortcut: the Menu Editor's list (tools/frmprobe.py): 1-26 ^A-^Z,
# then F1-F12 plain, with Ctrl, Shift and Shift+Ctrl, then five more
SHORTCUTS = ["^" + chr(65 + i) for i in range(26)] + \
    ["%s{F%d}" % (m, i) for m in ("", "^", "+", "+^") for i in range(1, 13)] + \
    ["^{INSERT}", "+{INSERT}", "{DEL}", "+{DEL}", "%{BKSP}"]

# The runtime's own table of designer properties (the *_rgbpprop tables named in msvbvm60.dbg,
# tools/proptab.py): class -> [opcode, name, storage].  The type library names almost all of them;
# this fills in what it lacks, so one more property doesn't lose the rest of the record.
STORAGE = {1: "str", 2: "i16", 3: "i32", 4: "bool", 5: "i32", 6: "byte", 7: "f32", 13: "wstr",
           68: "bool", 70: "byte", 130: "byte", 132: "bool", 133: "i32", 134: "byte"}


def _prop_table():
    import json
    import os
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "ctlprops.json")) as f:
        raw = json.load(f)
    return {cls: {op: (name, st) for op, name, st in rows} for cls, rows in raw.items()}


PROP_TABLE = _prop_table()

OCX = 0xFF          # control type of an ActiveX control: its class name follows
# The extender properties VB keeps for an ActiveX control (VBControlExtender,
# dispatch-only, so found with tools/frmprobe.py); the control's own state is
# the stream of 2Dh, written by its IPersistStreamInit::Save
OCX_EXTENDER = {0x01: ("_Index", "skip16"),        # (a control array's: also before the name)
                0x03: ("Left", "geometry"), 0x07: ("Visible", "bool"),
                0x09: ("DragMode", "byte"), 0x0B: ("Tag", "wstr"), 0x0E: ("TabStop", "bool"),
                0x0F: ("TabIndex", "i16"), 0x2D: ("_stream", "stream"),
                0x32: ("HelpContextID", "i32"),
                0x39: ("Left", "i32"), 0x3A: ("Top", "i32"),   # no size at run time
                0x3B: ("Align", "byte"),
                0x46: ("WhatsThisHelpID", "i32"), 0x48: ("CausesValidation", "bool"),
                0x4A: ("ToolTipText", "str")}


class Prop:
    def __init__(self, name, value, kind="value"):
        self.name, self.value, self.kind = name, value, kind


class Control:
    def __init__(self, name, ctype, cid):
        self.name, self.ctype, self.cid = name, ctype, cid
        self.props = []
        self.children = []
        self.index = None
        self.cls = None
        self.unknown = None     # first undecoded opcode, if any
        self.progid = None      # an ActiveX control's class ("MSComctlLib.ImageList")
        self.stream = None      # and its persisted state
        self.own = []           # its own properties (written after the extender's)
        self.own_lines = None   # or the .frm lines the control itself wrote (ocxprops)

    @property
    def vbclass(self):
        if self.progid:
            return self.progid
        return "VB." + CTYPES.get(self.ctype, "Unknown%d" % self.ctype)


class Reader:
    def __init__(self, data, pos=0):
        self.d, self.p = data, pos

    def u8(self):
        v = self.d[self.p]
        self.p += 1
        return v

    def u16(self):
        v = struct.unpack_from("<H", self.d, self.p)[0]
        self.p += 2
        return v

    def i16(self):
        v = struct.unpack_from("<h", self.d, self.p)[0]
        self.p += 2
        return v

    def i32(self):
        v = struct.unpack_from("<i", self.d, self.p)[0]
        self.p += 4
        return v

    def u32(self):
        v = struct.unpack_from("<I", self.d, self.p)[0]
        self.p += 4
        return v

    def f32(self):
        v = struct.unpack_from("<f", self.d, self.p)[0]
        self.p += 4
        return v

    def raw(self, n):
        v = self.d[self.p:self.p + n]
        self.p += n
        return v

    def string(self):
        n = self.u16()
        s = self.raw(n).decode("latin-1")
        if self.p < len(self.d) and self.d[self.p] == 0:
            self.p += 1
        return s


def interface_for(ctype_name):
    """VB6.OLB default interface (_TextBox etc.) for a control class."""
    for lib in Registry.libs:
        t = lib.by_name.get(ctype_name.lower())
        if t is not None and t.kind == 5:
            for h, f in t.impls:
                if f & 1 and not f & 2:
                    return lib.resolve_href(h)
            return lib.resolve_href(t.impls[0][0])
    return None


def member_table(iface):
    """opcode -> (name, type descriptor) from the interface's property puts/gets."""
    out = {}
    if iface is None:
        return out
    for f in iface.all_funcs():
        if f.memid is None or not (0x10000 <= f.memid < 0x10100):
            continue
        op = f.memid - 0x10000
        if f.invkind in (4, 8) and f.params:          # let/set: last param is the value
            out[op] = (f.name, f.params[-1].type)
        elif f.invkind == 2 and op not in out and f.params:
            t = f.params[-1].type
            out[op] = (f.name, t.sub if t.vt == 26 and t.sub is not None else t)
    return out


def type_kind(td):
    """Storage class of a property type: str, bool, i16, i32, f32, byte, pic, font, var."""
    while td.vt == 26 and td.sub is not None:      # by-reference / object pointer
        td = td.sub
    vt = td.vt
    if vt == 29:                                      # user defined: enum, alias, object
        ti = td.lib.resolve_href(td.href) if td.lib is not None else None
        name = getattr(ti, "name", "") or ""
        guid = getattr(ti, "guid", "") or ""
        if guid in ("{BEF6E003-A874-101A-8BBA-00AA00300CAB}",       # Font / IFontDisp
                    "{BEF6E002-A874-101A-8BBA-00AA00300CAB}"):
            return "font"
        if guid in ("{7BF80981-BF32-101A-8BBB-00AA00300CAB}",       # Picture / IPictureDisp
                    "{7BF80980-BF32-101A-8BBB-00AA00300CAB}"):
            return "pic"
        kind = getattr(ti, "kind", -1)
        if kind == 0:
            return "byte"
        if kind == 6 and getattr(ti, "alias", None) is not None:
            return type_kind(ti.alias)
        if name in ("OLE_COLOR",):
            return "i32"
        if name in ("Picture", "IPictureDisp", "StdPicture", "IPicture"):
            return "pic"
        if name in ("Font", "IFontDisp", "StdFont", "IFont"):
            return "font"
        if name in ("OLE_XPOS_CONTAINER", "OLE_YPOS_CONTAINER", "OLE_XSIZE_CONTAINER",
                    "OLE_YSIZE_CONTAINER"):
            return "f32"
        return "obj"
    return {8: "str", 11: "bool", 2: "i16", 3: "i32", 4: "f32", 5: "f64", 17: "byte",
            18: "i16", 19: "i32", 22: "i32", 23: "i32", 12: "var", 6: "cy"}.get(vt, "unk")


class FormDecoder:
    def __init__(self, model, data, pos, frx_name, collect=None):
        self.m = model
        self.r = Reader(data, pos)
        self.frx = bytearray()          # .frx contents (pictures, list items, ...)
        self.frx_name = frx_name
        self.collect = collect          # [(class, state)] of the ActiveX controls met
        # (class, state) -> (.frm lines, .frx bytes), from ocx_results()
        self.bags = getattr(model, "ocx_bags", None) or {}

    # ------------------------------------------------------------------ records
    def control(self, is_form=False):
        r = self.r
        start = r.p
        length = r.u32()
        cid = r.u8()
        index = None
        if length & 0x80000000:     # a control array element: its Index follows the ID
            length &= 0x7FFFFFFF
            index = r.u16()
        name = r.string()
        ctype = r.u8()
        c = Control(name, ctype, cid)
        c.index = index
        end = start + length - 1          # the separator follows the properties
        if ctype == OCX:
            c.progid = r.string()
            self.ocx(c, end)
            r.p = end
            return c
        iface = interface_for(CTYPES.get(ctype, ""))
        table = member_table(iface)
        while r.p < end:
            op = r.u8()
            if not self.prop(c, op, table, is_form, end):
                c.unknown = op
                r.p = end
                break
        r.p = end
        return c

    def ocx(self, c, end):
        """An ActiveX control's extender properties and persisted state."""
        r = self.r
        while r.p < end:
            op = r.u8()
            ent = OCX_EXTENDER.get(op)
            if ent is None:
                c.unknown = op
                return
            name, kind = ent
            if kind == "geometry":
                l, t, w, h = self.geometry()
                for n, v in (("Height", h), ("Left", l), ("Top", t), ("Width", w)):
                    c.props.append(Prop(n, v))
            elif kind == "skip16":
                r.u16()
            elif kind == "bool":
                c.props.append(Prop(name, r.u8() != 0, "bool"))
            elif kind == "byte":
                c.props.append(Prop(name, r.u8()))
            elif kind == "i16":
                c.props.append(Prop(name, r.i16()))
            elif kind == "i32":
                c.props.append(Prop(name, r.i32()))
            elif kind == "str":
                c.props.append(Prop(name, r.string(), "str"))
            elif kind == "wstr":
                n = r.u16()
                c.props.append(Prop(name, r.raw(2 * n).decode("utf-16-le"), "str"))
            elif kind == "stream":
                r.u32()                     # 0009424Ch
                c.stream = bytes(r.raw(r.u32()))
                if c.stream[:4] == b"\x93\xb2\0\0":
                    c.own_lines = self.property_bag(c.stream)
                    continue
                if self.collect is not None:
                    self.collect.append((c.progid, c.stream))
                bag = self.bags.get((c.progid, c.stream))
                if bag is not None:
                    from . import ocxprops
                    lines, frx = bag
                    c.own_lines = ocxprops.rebase(lines, len(self.frx), self.frx_name)
                    self.frx += frx
                else:
                    c.own += stream_props(c.stream)

    def text_prop(self, c, name, s):
        """A String property: in the .frm line, or when a line can't hold it in the .frx as VB
        does: $"x.frx":offset, the bytes with a dword length (DarwinBots InfoForm's Captions;
        frmAbout's Text with a line break cut its line in two)."""
        if len(s) > 250 or any(ord(ch) < 32 for ch in s):
            off = len(self.frx)
            raw = s.encode("cp1252", "replace") if any(ord(ch) > 255 for ch in s) else s.encode("latin-1")
            self.frx += struct.pack("<I", len(raw)) + raw
            c.props.append(Prop(name, '$"%s":%04X' % (self.frx_name, off), "raw"))
        else:
            c.props.append(Prop(name, s, "str"))

    def geometry(self):
        """Left, Top, Width, Height: words, or when one doesn't fit (a control on a hidden SSTab
        page at Left -74640) the word 8000h and four dwords (DarwinBots OptionsForm: read as words
        it lost its place, and the rest of the form with it)."""
        r = self.r
        if struct.unpack_from("<H", r.d, r.p)[0] == 0x8000:
            r.u16()
            return r.i32(), r.i32(), r.i32(), r.i32()
        return r.i16(), r.i16(), r.i16(), r.i16()

    def property_bag(self, st):
        """A UserControl's WriteProperties, as saved in the form: B293h, the size, then for each
        (vt, characters in the name, a hash, the entry's size, negative for the last, the name in
        lower case, the value).  ReadProperty finds names in any case."""
        out = []
        p = 8
        while p + 12 <= len(st):
            vt, nc, _, size = struct.unpack_from("<HHIi", st, p)
            name = st[p + 12:p + 12 + 2 * nc].decode("utf-16-le", "replace")
            q = p + 12 + 2 * nc
            name = {"_extentx": "_ExtentX", "_extenty": "_ExtentY"}.get(name, name)
            out += self.bag_value(name, vt, st, q)
            if size <= 0:
                break
            p += size
        return out

    def bag_value(self, name, vt, st, q):
        line = "%-15s =   %%s" % name
        fmt = {2: "<h", 3: "<i", 17: "<B"}.get(vt)
        if fmt:
            return [line % struct.unpack_from(fmt, st, q)[0]]
        if vt == 11:
            return [line % ("-1  'True" if struct.unpack_from("<h", st, q)[0] else "0   'False")]
        if vt == 4:
            return [line % ("%g" % struct.unpack_from("<f", st, q)[0])]
        if vt in (5, 7):
            return [line % repr(struct.unpack_from("<d", st, q)[0])]
        if vt == 6:
            return [line % (struct.unpack_from("<q", st, q)[0] / 10000)]
        if vt == 8:
            n = struct.unpack_from("<I", st, q)[0]
            text = st[q + 4:q + 4 + 2 * n].decode("utf-16-le", "replace")
            if any(ord(ch) < 32 for ch in text):
                # a line break can't be in a .frm string; VB puts these in the .frx
                off = len(self.frx)
                raw = text.encode("cp1252", "replace")
                self.frx += struct.pack("<I", len(raw)) + raw
                return [line % ('$"%s":%04X' % (self.frx_name, off))]
            return [line % ('"%s"' % text.replace('"', '""'))]
        if vt == 13:
            g, q = st[q:q + 16], q + 16
            if g == bytes.fromhex("0352e30b918fce119de300aa004bb851"):      # StdFont
                flags = st[q + 3]
                weight = struct.unpack_from("<H", st, q + 4)[0]
                size = struct.unpack_from("<I", st, q + 6)[0] / 10000.0
                n = st[q + 10]
                # (with its class: this is a property bag's, which only the id says is a Font; VB6
                # looked for {00000000-...} loading a target's UserControl1: error 713)
                out = ["BeginProperty %s {0BE35203-8F91-11CE-9DE3-00AA004BB851} " % name,
                       '   Name            =   "%s"' % st[q + 11:q + 11 + n].decode("latin-1"),
                       "   Size            =   %g" % size,
                       "   Charset         =   %d" % st[q + 1],
                       "   Weight          =   %d" % weight]
                for k, b in (("Underline", 4), ("Italic", 2), ("Strikethrough", 8)):
                    out.append("   %-15s =   %s" % (k, "-1  'True" if flags & b else "0   'False"))
                return out + ["EndProperty"]
            if g == bytes.fromhex("0452e30b918fce119de300aa004bb851"):      # StdPicture: lt, size, bytes
                n = struct.unpack_from("<I", st, q + 4)[0]
                if not n:
                    return []       # (no picture)
                off = len(self.frx)
                self.frx += st[q:q + 8 + n]
                return [line % ('"%s":%04X' % (self.frx_name, off))]
        return ["' vb6dec: property %s of type %d not decoded" % (name, vt)]

    def prop(self, c, op, table, is_form, end):
        r = self.r
        ent = table.get(op)
        name = ent[0] if ent else None
        # special encodings
        if is_form and op == 0x19 or c.ctype == 0 and op == 0x1A:
            # ScaleMode + flag bytes (the Form's 19h, a PictureBox's 1Ah: read
            # as one byte, its 0 0x42 0 became a string opcode that swallowed
            # the rest of the record)
            mode = r.u8()
            r.u8()
            default_scale = False
            if mode == 0:
                vals = [r.f32() for _ in range(4)]
                # (0, 0, 1, 1) is what's stored when no scale was ever set: the original
                # runs in twips.  As User scale every Top = ScaleHeight - 450 is thousands
                # of screens away (a target Form7: 16383 x 16383, seen by menucrawl)
                default_scale = vals == [0.0, 0.0, 1.0, 1.0]
                if not default_scale:
                    c.user_scale = vals     # (resolve_scales: left, top, twips per unit x, y)
            fl = r.u8()
            r.u8()
            if mode != 1 and not default_scale:
                c.props.append(Prop("ScaleMode", mode))
            c.scale_mode = 1 if default_scale else mode
            if fl & 0x20:
                c.props.append(Prop("AutoRedraw", True, "bool"))
            if not fl & 0x02:
                c.props.append(Prop("FontTransparent", False, "bool"))
            return True
        if is_form and op == 0x35:                      # ClientLeft/Top/Width/Height
            vals = [r.i32() for _ in range(4)]
            for n, v in zip(("ClientLeft", "ClientTop", "ClientWidth", "ClientHeight"), vals):
                c.props.append(Prop(n, v))
            return True
        if name == "Left" and self.has_size(table):
            l, t, w, h = self.geometry()
            for n, v in (("Height", h), ("Left", l), ("Top", t), ("Width", w)):
                c.props.append(Prop(n, v))
            return True
        extra = EXTRA.get(c.ctype, {}).get(op)
        known = PROP_TABLE.get(CTYPES.get(c.ctype), {}).get(op)
        if extra is not None:
            name, kind = extra
        elif ent is None:
            if known is None or known[1] not in STORAGE:
                return False
            name, kind = known[0], STORAGE[known[1]]
        else:
            kind = type_kind(ent[1])
            if kind == "i16" and name in BYTE_PROPS:
                kind = "byte"
            if kind == "bool" and name in BYTE_PROPS:
                kind = "byte"
            if known is not None and known[1] == 2 and kind == "byte":
                kind = "i16"        # (a scrollbar's Value: 300 is 2C 01, not two properties)
            if known is not None and known[1] == 13:
                kind = "wstr"       # (Tag, and the Data control's Connect, RecordSource...)
        if kind == "wstr":
            # UTF-16, count of characters: one digit read as one byte (and the NUL its "end") hid
            # it; "2200" lost the rest of the record (DarwinBots OptionsForm, 33 controls)
            n = r.u16()
            text = r.raw(2 * n).decode("utf-16-le", "replace")
            if name != "_":
                self.text_prop(c, name, text)
            return True
        if name == "_":
            # stored, but no property the .frm can name
            r.raw({"bool": 1, "byte": 1, "i16": 2, "i32": 4, "f32": 4}.get(kind, 1))
            return True
        if name in ("List", "ItemData") and c.ctype in (7, 8):
            # ListBox / ComboBox items: count, then (when there are any) the
            # longest item's length and each item as u16 length + text
            # (ItemData's numbers too).  A .frx keeps the same bytes.
            start = r.p
            n = r.u16()
            if n:
                r.u16()
                for _ in range(n):
                    r.raw(r.u16())
            if n:
                off = len(self.frx)
                self.frx += self.r.d[start:r.p]
                c.props.append(Prop(name, '"%s":%04X' % (self.frx_name, off), "raw"))
            return True
        if kind == "shortcut":
            v = r.i16()
            if 1 <= v <= len(SHORTCUTS):
                c.props.append(Prop(name, SHORTCUTS[v - 1], "raw"))
            return True
        if kind == "twips":
            c.props.append(Prop(name, r.i32(), "twips"))
            return True
        if kind == "str":
            if r.p + 2 > end or r.p + 2 + struct.unpack_from("<H", r.d, r.p)[0] > end:
                # no room for a string: the opcode alone (a target's Dropdown
                # List combos store Text so, with nothing after it)
                return True
            s = r.string()
            if name == "Name":
                return True
            self.text_prop(c, name, s)
        elif kind == "bool":
            v = r.u8()
            c.props.append(Prop(name, v != 0, "bool"))
        elif kind == "byte":
            c.props.append(Prop(name, r.u8()))
        elif kind == "i16":
            v = r.i16()
            if name == "Index":
                c.index = v
                return True
            c.props.append(Prop(name, v))
        elif kind == "i32":
            v = r.i32()
            if name in ("BackColor", "ForeColor", "FillColor", "BorderColor", "MaskColor"):
                c.props.append(Prop(name, v & 0xffffffff, "color"))
            else:
                c.props.append(Prop(name, v))
        elif kind == "f32":
            c.props.append(Prop(name, r.f32(), "single"))
        elif kind == "f64":
            c.props.append(Prop(name, struct.unpack("<d", r.raw(8))[0], "single"))
        elif kind == "font":
            self.font(c, name)
        elif kind == "pic":
            n = r.u32()
            if n != 0xFFFFFFFF:
                blob = r.raw(n)
                off = len(self.frx)
                self.frx += struct.pack("<I", n) + blob
                c.props.append(Prop(name, '"%s":%04X' % (self.frx_name, off), "raw"))
        else:
            return False
        return True

    @staticmethod
    def has_size(table):
        names = {v[0] for v in table.values()}
        return "Width" in names and "Height" in names

    def font(self, c, name):
        r = self.r
        r.u8()                              # version
        charset = r.u8()
        r.u8()
        flags = r.u8()
        weight = r.u16()
        size = r.u32()
        n = r.u8()
        face = r.raw(n).decode("latin-1")
        c.props.append(Prop(name, {"Name": face, "Size": size / 10000.0, "Charset": charset,
                                   "Weight": weight, "Underline": bool(flags & 4),
                                   "Italic": bool(flags & 2), "Strikethrough": bool(flags & 8)},
                            "font"))

    # ------------------------------------------------------------------ tree
    def menus(self, parent):
        """Menu items after FF05.  An item with the indent flag (opcode 7)
        owns the items that follow; after each item comes FF and then codes:
        02 next item, 03 up one level, 04 end of the form."""
        r = self.r
        stack = [parent]
        while True:
            m = self.control()
            stack[-1].children.append(m)
            indent = any(p.name == "_Indent" for p in m.props)
            m.props = [p for p in m.props if not p.name.startswith("_")]
            if indent:
                stack.append(m)
            if r.p >= len(r.d) or r.u8() != 0xFF:
                return
            while True:
                code = r.u8()
                if code == 0x03:
                    if len(stack) > 1:
                        stack.pop()
                    continue
                break
            if code != 0x02:
                return

    def tree(self):
        r = self.r
        form = self.control(is_form=True)
        names = {p.name for p in form.props}
        if form.ctype != 20 and not names & {"ScaleWidth", "ScaleHeight", "ScaleMode"}:
            # (an MDIForm has no scale: "The property name ScaleHeight in MDIForm1 is invalid")
            # twips scale: VB writes the scale size, equal to the client size
            vals = {p.name: p.value for p in form.props}
            if "ClientWidth" in vals:
                form.props.append(Prop("ScaleWidth", vals["ClientWidth"]))
                form.props.append(Prop("ScaleHeight", vals["ClientHeight"]))
        stack = [form]
        last = form
        # FFh, then codes: 01 children follow, 03 a sibling, 02 the container
        # closes - and after 02 the next code comes without another FFh
        # (a target Start1: FF 02 03 closes Frame3 and goes on to Frame2; read
        # as the word 1E03 it ended the form there, 25 of 32 controls short)
        code = None
        while r.p < len(r.d):
            if code is None:
                if r.u8() != 0xFF or r.p >= len(r.d):
                    break
                code = r.u8()
            sep, code = 0xFF | code << 8, None
            if sep == SEP_CHILDREN:
                stack.append(last)
            elif sep == SEP_SIBLING:
                pass
            elif sep == SEP_END:
                if len(stack) <= 1:
                    break
                stack.pop()
                if r.p < len(r.d) and r.d[r.p] != 0xFF:
                    code = r.u8()
                continue
            elif sep == SEP_MENU:
                self.menus(stack[0])
                break
            elif sep in (SEP_FORMEND,):
                break
            else:
                break
            c = self.control()
            stack[-1].children.append(c)
            last = c
            if c.unknown is not None:
                pass
        return form


def stream_props(data):
    """The properties a .frm needs from a control's persisted state that do
    not depend on the control: the extent and version Microsoft's VB-built
    controls (MSCOMCTL, COMDLG32, ...) start their stream with
    (12344321h, 8, extent x, y in HIMETRIC, a class signature, version)."""
    if len(data) < 24 or struct.unpack_from("<II", data) != (0x12344321, 8):
        return []
    x, y, _, ver = struct.unpack_from("<iiII", data, 8)
    return [Prop("_ExtentX", x), Prop("_ExtentY", y), Prop("_Version", ver)]


# ------------------------------------------------------------------ text output
def fmt_value(p):
    v = p.value
    if p.kind == "str":
        return '"%s"' % v.replace('"', '""')
    if p.kind == "bool":
        return "-1  'True" if v else "0   'False"
    if p.kind == "color":
        return "&H%08X&" % v
    if p.kind == "single":
        return ("%g" % v)
    if p.kind == "raw":
        return v
    return str(v)


def emit(c, out, ind=0):
    pad = "   " * ind
    out.append("%sBegin %s %s " % (pad, c.vbclass, c.name))
    for p in sorted(c.props, key=lambda p: p.name.lower()):
        if p.kind == "font":
            f = p.value
            out.append("%s   BeginProperty %s " % (pad, p.name))
            out.append("%s      Name            =   \"%s\"" % (pad, f["Name"]))
            out.append("%s      Size            =   %s" % (pad, ("%g" % f["Size"])))
            out.append("%s      Charset         =   %d" % (pad, f["Charset"]))
            out.append("%s      Weight          =   %d" % (pad, f["Weight"]))
            for k in ("Underline", "Italic", "Strikethrough"):
                out.append("%s      %-15s =   %s" % (pad, k, "-1  'True" if f[k] else "0   'False"))
            out.append("%s   EndProperty" % pad)
            continue
        out.append("%s   %-15s =   %s" % (pad, p.name, fmt_value(p)))
    if c.index is not None:
        out.append("%s   %-15s =   %d" % (pad, "Index", c.index))
    for p in c.own:
        out.append("%s   %-15s =   %s" % (pad, p.name, fmt_value(p)))
    for ln in c.own_lines or ():
        out.append("%s   %s" % (pad, ln))
    if c.unknown is not None:
        out.append("%s   ' vb6dec: property opcode %d not decoded; rest of this control skipped"
                   % (pad, c.unknown))
    for ch in c.children:
        emit(ch, out, ind + 1)
    out.append("%sEnd" % pad)


# twips per unit of each ScaleMode (x, y); 3 at the usual 15 twips a pixel
SCALE_UNITS = {1: (1, 1), 2: (20, 20), 3: (15, 15), 4: (120, 240), 5: (1440, 1440), 6: (56.7, 56.7),
               7: (567, 567)}


def client_size(c):
    """A form's or PictureBox's inside, in twips."""
    v = {p.name: p.value for p in c.props}
    if "ClientWidth" in v:
        return v["ClientWidth"], v["ClientHeight"]
    if "Width" not in v:
        return None
    border = 0
    if v.get("BorderStyle", 1):
        border = 60 if v.get("Appearance", 1) else 30       # 2 or 1 pixels on each side
    return v["Width"] - border, v["Height"] - border


def resolve_scales(c, unit=(1, 1, 0, 0)):
    """What depends on the coordinate system around a control.  A user scale is stored as left,
    top and twips per unit, ScaleWidth is its result (DarwinBots Contest_Form: 101.6 stored,
    4935 / 101.6 = ScaleWidth 48.573; printed as the size it made every coordinate wrong).  A Line
    is stored in twips, and written in its container's units."""
    mode = getattr(c, "scale_mode", None)
    us = getattr(c, "user_scale", None)
    if us is not None:
        ux, uy = us[2] or 1, us[3] or 1
        unit = (ux, uy, us[0], us[1])
        c.props.append(Prop("ScaleLeft", "%.7g" % us[0], "raw"))
        c.props.append(Prop("ScaleTop", "%.7g" % us[1], "raw"))
        size = client_size(c)
        if size is not None:
            c.props.append(Prop("ScaleWidth", "%.7g" % (size[0] / ux), "raw"))
            c.props.append(Prop("ScaleHeight", "%.7g" % (size[1] / uy), "raw"))
    elif mode in SCALE_UNITS:
        unit = SCALE_UNITS[mode] + (0, 0)
    for p in c.props:
        if p.kind == "twips":
            ux, uy, lx, ly = unit
            x = p.name.startswith("X")
            p.value = "%.7g" % (p.value / (ux if x else uy) + (lx if x else ly))
            p.kind = "raw"
    for ch in c.children:
        resolve_scales(ch, unit)


def array_names(model, obj):
    """Names of the form's control arrays, in lower case (the designer data
    gives each element an Index; the control list does not tell them apart,
    and may spell a name in another case: a target Form5 name1 / name1)."""
    form = form_tree(model, obj)
    out = set()

    def walk(c):
        for ch in c.children:
            if ch.index is not None:
                out.add(ch.name.lower())
            walk(ch)
    if form is not None:
        walk(form)
    return out


def form_tree(model, obj):
    """The form's designer data as a Control tree, or None."""
    prj, im = model.prj, model.im
    if not im.valid(prj.gui_table):
        return None
    for i in range(prj.nforms):
        e = prj.gui_table + 0x50 * i
        if im.u32(e + 0x24) != obj.index:
            continue
        fp = im.u32(e + 0x48)
        size = im.u32(e + 0x40)
        if not (size and im.valid(fp + size - 1)):
            size = 0x10000 if im.valid(fp + 0x10000) else im.base + len(im.mem) - fp
        data = bytes(im.bytes(fp, size))
        start = find_form_record(data, obj.name)
        if start is None:
            return None
        return FormDecoder(model, data, start, obj.name + ".frx").tree()
    return None


def usercontrol_props(misc, flags):
    """A UserControl's properties that are in no record of the form but in its OLEMISC status, in the
    GUI table (tools/ucprops.py found them).  Without DefaultCancel, Ambient.DisplayAsDefault stays False
    and a UserControl1 with the focus draws no focus rectangle: the only difference on a target's
    main window."""
    out = [Prop(name, True, "bool") for bit, name in (
        (0x400, "InvisibleAtRuntime"), (0x1000, "DefaultCancel"), (0x2000, "ForwardFocus"),
        (0x8000, "Alignable"), (0x10000, "ControlContainer")) if misc & bit]
    if misc & 0x4000:
        out.append(Prop("CanGetFocus", False, "bool"))
    if flags & 0x8000:
        out.append(Prop("Windowless", True, "bool"))
    return out


def decode_form(model, obj, frx_name=None, collect=None):
    """Designer text and .frx bytes for a form object, or (None, None).
    collect: a list that gets (class, state) of each ActiveX control."""
    prj = model.prj
    im = model.im
    gui = prj.gui_table
    if not im.valid(gui):
        return None, None
    for i in range(prj.nforms):
        e = gui + 0x50 * i
        if im.u32(e + 0x24) != obj.index:
            continue
        fp = im.u32(e + 0x48)
        # +40h is the size of the form's data (a target Form13:
        # 55BABh, pictures included); 64 KB when it does not fit the image
        size = im.u32(e + 0x40)
        if not (size and im.valid(fp + size - 1)):
            size = 0x10000 if im.valid(fp + 0x10000) else im.base + len(im.mem) - fp
        data = bytes(im.bytes(fp, size))
        start = find_form_record(data, obj.name)
        if start is None:
            return None, None
        dec = FormDecoder(model, data, start, frx_name or (obj.name + ".frx"), collect)
        form = dec.tree()
        resolve_scales(form)
        form.name = obj.name    # as VB_Name spells it (else "Conflicting names")
        if obj.kind == "UserControl":
            form.props.extend(usercontrol_props(im.u32(e + 0x2c), im.u32(e + 0x28)))
        out = []
        emit(form, out)
        return "\n".join(out), bytes(dec.frx)
    return None, None


def ocx_results(model):
    """{(class, state): (.frm lines, .frx bytes)} for every ActiveX control of
    the project's forms, from the controls themselves (ocxprops: under Wine);
    {} when they cannot be asked."""
    from . import ocxprops
    met = []
    for o in model.prj.objects:
        if o.kind in ("Form", "UserControl"):
            try:
                decode_form(model, o, collect=met)
            except Exception:
                pass
    keys = sorted(set(met), key=lambda k: (k[0], k[1]))
    jobs, todo = [], []
    for progid, state in keys:
        clsid = coclass_guid(progid)
        if clsid:
            jobs.append((clsid, state))
            todo.append((progid, state))
    got = ocxprops.properties(jobs)
    return {todo[i]: res for i, res in got.items()}


def coclass_guid(progid):
    """CLSID of a control class named Library.Class, from its type library."""
    lib_name, _, cls = progid.partition(".")
    for lib in Registry.libs:
        if getattr(lib, "lib_name", None) == lib_name:
            t = lib.by_name.get(cls.lower())
            if t is not None and t.guid:
                return t.guid
    return None


def find_form_record(data, name):
    """The form's own record (u32 length, u8 id, name) is preceded by a dword
    with the size of all records.  The name's case can differ from the
    object table's (a target: record "color1", object "color1")."""
    key = struct.pack("<H", len(name)) + name.encode("latin-1") + b"\0"
    i = data.find(key)
    if i < 0:
        i = data.decode("latin-1").lower().find(key.decode("latin-1").lower())
    if i < 5:
        return None
    return i - 5
