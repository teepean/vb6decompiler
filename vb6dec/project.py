"""VB5/VB6 executable structures: VB header, project info, object table,
objects (forms, modules, classes), controls and event tables.

Offsets follow the layouts used by the VB runtime (MSVBVM60.DLL); names
follow the community documentation (Semi VB Decompiler, "Visual Basic
Image Internal Structure Format").
"""
from .pe import Image

# fObjectType bits: 0x18001 module, 0x18083 form, 0x1da003 class, 0x1da803 user control
OBJ_HAS_OPTINFO = 0x02
OBJ_FORM = 0x80
OBJ_USERCONTROL = 0x40000       # (t25usercontrol: 1DA003h; a class is 118003h)
OBJ_COMPARE_TEXT = 0x40         # Option Compare Text (SETUP1: 16 of 16 objects)
GUI_KINDS = {0: "Form", 1: "Form", 2: "Form", 3: "UserControl", 4: "PropertyPage",
             5: "UserDocument", 6: "Designer", 7: "Designer"}      # (Form, MDIForm, MDI child, ...)
GUI_STARTUP = 0x10              # in the same word: the form VB loads first


class Control:
    def __init__(self, prj, va):
        im = prj.im
        self.va = va
        self.flags = im.u16(va)
        self.nevents = im.u16(va + 2)
        self.slot = im.u32(va + 4)              # offset of the control in the form instance
        self.guid = im.guid(im.u32(va + 8)) if im.valid(im.u32(va + 8)) else None
        self.index = im.i16(va + 0x0c)          # form vtable index (getter at _Form.cbvft + 4*index)
        self.evtable = im.u32(va + 0x18)
        self.name = im.cstr(im.u32(va + 0x20))
        self.events = []                        # (event index, stub VA, handler VA)
        if im.valid(self.evtable):
            for j in range(self.nevents):
                stub = im.u32(self.evtable + 0x18 + 4 * j)
                if stub:
                    self.events.append((j, stub, prj.stub_target(stub)))


class Object:
    def __init__(self, prj, index, va):
        im = prj.im
        self.prj, self.index, self.va = prj, index, va
        self.info = im.u32(va)
        self.public_bytes = im.u32(va + 0x08)
        self.static_bytes = im.u32(va + 0x0c)
        self.module_public = im.u32(va + 0x10)
        self.module_static = im.u32(va + 0x14)
        self.name = im.cstr(im.u32(va + 0x18))
        self.nmethods = im.u32(va + 0x1c)
        mn = im.u32(va + 0x20)
        self.method_names = []
        if im.valid(mn):
            for i in range(self.nmethods):
                p = im.u32(mn + 4 * i)
                self.method_names.append(im.cstr(p) if im.valid(p) else None)
        self.type = im.u32(va + 0x28)
        oi = self.info
        self.private_object = im.u32(oi + 0x0c)
        self.methods_va = im.u32(oi + 0x24)
        self.nmethods_oi = im.u16(oi + 0x20)
        self.methods = []
        if im.valid(self.methods_va):
            for i in range(self.nmethods_oi):
                p = im.u32(self.methods_va + 4 * i)
                self.methods.append(p if im.valid(p) else None)
        self.nconst = im.u16(oi + 0x28)
        self.constants = im.u32(oi + 0x34)
        self.controls = []
        self.events = []
        self.init_event = self.term_event = None
        if self.type & OBJ_HAS_OPTINFO:
            opt = oi + 0x38
            self.opt = opt
            ncontrols = im.u32(opt + 0x20)
            lpcontrols = im.u32(opt + 0x24)
            self.nevents = im.u16(opt + 0x28)
            self.lpevents = im.u32(opt + 0x30)
            self.controls = [Control(prj, lpcontrols + 0x28 * i) for i in range(ncontrols)]
            if im.valid(self.lpevents):
                for i in range(self.nevents):
                    p = im.u32(self.lpevents + 4 * i)
                    self.events.append(p)

    @property
    def kind(self):
        t = self.type
        if not t & OBJ_HAS_OPTINFO:
            return "Module"
        # what the runtime goes by (LoadExeResources, msvbvm60.dbg): the low nibble of the GUI
        # table entry at +28h.  The type word can't tell a PropertyPage from a UserDocument.
        flags = self.prj.gui_flags().get(self.index)
        if flags is not None:
            return GUI_KINDS.get(flags & 0xF, "Form")
        if t & OBJ_FORM:
            return "Form"
        if t & OBJ_USERCONTROL:
            return "UserControl"
        return "Class"

    def __repr__(self):
        return "<%s %s type=%#x>" % (self.kind, self.name, self.type)


class Project:
    def gui_flags(self):
        """Object index -> the word at +28h of its GUI table entry."""
        cache = self.__dict__.get("_gui_flags")
        if cache is None:
            if "objects" not in self.__dict__:
                return {}
            cache = self._gui_flags = {}
            if self.im.valid(self.gui_table):
                for i in range(self.nforms):
                    e = self.gui_table + 0x50 * i
                    cache[self.im.u32(e + 0x24)] = self.im.u32(e + 0x28)
        return cache

    def __init__(self, path):
        self.im = im = Image(path)
        hdr = im.mem.find(b"VB5!")
        if hdr < 0:
            raise ValueError("not a VB5/VB6 executable")
        self.hdr = h = im.base + hdr
        self.runtime_build = im.u16(h + 4)
        self.lang_dll = im.cstr(h + 6, 14)
        self.lcid = im.u32(h + 0x24)
        self.sub_main = im.u32(h + 0x2c) or None
        self.info = pi = im.u32(h + 0x30)
        self.thread_flags = im.u32(h + 0x3c)
        self.nforms = im.u16(h + 0x44)
        self.nexternals = im.u16(h + 0x46)
        self.gui_table = im.u32(h + 0x4c)
        self.external_table = im.u32(h + 0x50)
        self.com_reg = im.u32(h + 0x54)
        strs = [im.u32(h + 0x58 + 4 * i) for i in range(4)]
        # (the file name VB made, without .exe, whatever the rest of the world calls this field; App.Title; App.HelpFile)
        self.exe_base, self.title, self.help_file, self.name = [
            im.cstr(h + o) if o else None for o in strs]
        self.version = im.u32(pi)
        self.object_table = ot = im.u32(pi + 4)
        self.code_start = im.u32(pi + 0x0c)
        self.code_end = im.u32(pi + 0x10)
        self.data_size = im.u32(pi + 0x14)
        self.thread_space = im.u32(pi + 0x18)
        self.vba_seh = im.u32(pi + 0x1c)
        self.native_code = im.u32(pi + 0x20)
        self.pcode = self.native_code == 0
        self.path_info = im.wstr(pi + 0x24, 264)
        self.ext_table = im.u32(pi + 0x234)
        self.next = im.u32(pi + 0x238)
        self.project_name = im.cstr(im.u32(ot + 0x40))
        self.uuid = im.guid(ot + 0x18)
        self.nobjects = im.u16(ot + 0x2a)
        lpobjs = im.u32(ot + 0x30)
        self.objects = [Object(self, i, lpobjs + 0x30 * i) for i in range(self.nobjects)]
        self.externals = self._externals()

    def stub_target(self, va):
        """Event stub "sub dword [esp+4], N; jmp handler" -> (adjust, handler)."""
        im = self.im
        b = im.bytes(va, 13)
        if b[:4] == b"\x81\x6c\x24\x04" and b[8] == 0xe9:
            return va + 13 + im.i32(va + 9)
        if b[:4] == b"\x83\x6c\x24\x04" and b[5] == 0xe9:
            return va + 10 + im.i32(va + 6)
        if b[0] == 0xe9:
            return va + 5 + im.i32(va + 1)
        return None

    def _externals(self):
        """Declare'd API functions: (type, library, function, VA of descriptor)."""
        im = self.im
        out = []
        for i in range(self.next):
            e = self.ext_table + 8 * i
            typ, ptr = im.u32(e), im.u32(e + 4)
            if typ == 7 and im.valid(ptr):
                lib = im.cstr(im.u32(ptr))
                fn = im.cstr(im.u32(ptr + 4))
                out.append((lib, fn, ptr))
            else:
                out.append((None, None, ptr))
        return out
