# Native VB6 executables: findings

What the decompiler relies on, learned from VB6 output (the test projects)
and from MSVBVM60.  Until 2026-10-28 every oracle was built by VB6 as first
released (VB6.EXE 6.0.81.76, C2 12.0.8041, LINK 6.0.8168), on Windows 98 and
under Wine; since then the Windows 98 VB6 has Service Pack 6 (6.0.97.82, C2
12.0.8783, LINK 6.0.8447).
These are the working notes kept while building vb6dec, newest
sections last.  Some findings came from programs whose decompiled output
stays private: those describe the construct only ("a real program"), and
their examples are made up in the same shape.
Offsets are hex. "VA" = virtual address in the EXE.

## Object structures

- VB header -> ProjectInfo -> object table -> one ObjectInfo per module,
  class and form.
- ObjectInfo+0x0C: the private descriptor ("aSmallRecord"); -1 for modules.
- OptionalObjectInfo = ObjectInfo+0x38 (forms and classes):
  - +04 CLSID, +10 default IID count, +14 events IID table, +18 events IID count
  - +1C default IID table, +20 control count, +24 controls
  - +28 u16 own-vtable slot count, +30 own vtable array
- Own vtable: slot i lives at own_base + 4*i, where own_base is 0x1C for
  classes and 0x6F8 for forms (`_Form`'s 0x2F8 + 0x400). Entries point at
  `jmp` thunks; slots that are not procedures are runtime accessors for
  Public variables.
- Vtable order: Public variables (Get/Let; Object, Variant and class-typed
  variables get three slots: Get/Let/Set) -> Public procedures -> other
  procedures in source order (event handlers included) -> WithEvents
  accessors (Get/Let/Set).
- A Variant variable's Let/Set accessor takes the value ByRef.
- Control getters return the object in eax.
- WithEvents variables appear in the control list with index -1, flags
  0x2E and the source's events IID as GUID.
- Control arrays (tests/t10carr): one control list entry per array, like a
  single control except its GUID: the class's events IID plus one
  (chk {33AD4EFB-..}, CheckBoxEvents {33AD4EFA-..}), an events interface in
  no type library whose events take Index As Integer (ByRef) first.  The
  designer data has a record per element (bit 31 of the length, then the
  Index).  The form's getter returns an object of VB's own
  (__vbaHresultCheckObj gets GUID_NULL): 40h Item(Index As Long, out
  element), 44h LBound, 48h UBound, 4Ch Count (out Integer).  `Load chk(3)`
  is Item(3) then VBGlobal.Load (+0Ch) on the element; `For Each c In txt`
  is __vbaForEachCollObj on the array object.
- Event names are the unreferenced strings in the name pool after the
  method names, before the tObject+0x08 cleanup list.
- Module procedures (2026-10-28, VBDoom's map): no table lists them, but the
  linker lays the modules' code out one after another in object table order
  (58 modules, one stray procedure), between the forms' and classes' code.
  A module's tObject has its source's procedure count (+1C; VB drops the
  ones nothing calls: doomdef 14 / 7 compiled) and its data areas: +10
  Public variables, +14 Private ones, each sized by the u16 at +2 of the
  descriptor at +08 / +0C.  vb6dec/owners.py places module procedures from
  that (tools/ownercheck.py: VBDoom 761 of 775 right with the map hidden,
  the misses first or last procedures of a module that use no variables).

## Form designer data (frm.py)

- GUI table: one 50h-byte entry per form: +24 object index, +40 size of the
  form's data, +48 its address.  The size matters: a form holding pictures
  can be far larger than 64 KB (one in a real program was), so a fixed 64 KB
  read ran off the end.
- Record: u32 length, u8 ID, name (u16 length, text, NUL), u8 control type,
  then (opcode, value) pairs up to start + length - 1, where the separator
  follows: FFh, then a code - 01 children follow, 03 a sibling, 02 the
  container closes, 04 the form ends, 05 menus follow - and after 02 the
  next code comes at once, without another FFh (FF 02 03: close Frame3, on
  to its sibling Frame2; FF 02 02 03 closes two).  Reading the separator as
  a word ended most forms of a large real program early.
  Bit 31 of the length marks a control array element; its Index (u16)
  follows the ID.
- Not every stored property has a DISPID in VB6.OLB: TextBox 28h is
  IMEMode (byte; 3 when PasswordChar is set), Form 3Dh / 3Eh LockControls /
  NegotiateMenus.
- The form's own record can spell its name in another case than the object
  table (a real program: record "Form2", object "form2").
- Form opcodes 3Dh LockControls and 3Eh NegotiateMenus (byte) are
  design-time only, so VB6.OLB's _Form does not list them.
- ListBox/ComboBox List and ItemData: u16 count, then (if any) u16 longest
  item length and each item as u16 length + text; ItemData's numbers are
  text too.  A .frx holds the same bytes (`List = "F.frx":0000`; VB6's ODBC
  Log In template: `00 00` for an empty list).  VB recomputes the length
  word when it builds.
- Type FFh is an ActiveX control: its class name follows (u16, text, NUL:
  "MSComctlLib.ImageList", the `Begin` name in a .frm).  Then VB's extender
  properties (VBControlExtender, dispatch-only, found with
  tools/frmprobe.py): 03 Left/Top/Width/Height (4 x i16), 07 Visible, 09
  DragMode, 0B Tag (u16 count + UTF-16), 0E TabStop, 0F TabIndex (i16), 2D
  the control's state, 32 HelpContextID (i32), 39/3A Left/Top (i32, for
  controls without a size at run time: CommonDialog, ImageList), 3B Align,
  46 WhatsThisHelpID (i32), 48 CausesValidation, 4A ToolTipText (u16 length,
  ANSI, NUL).
- 2D: u32 0009424Ch, u32 n, then n bytes of IPersistStreamInit::Save
  output.  Microsoft's VB-built controls (MSCOMCTL, COMDLG32) start it with
  12344321h, 8, the extent (x, y in HIMETRIC), a per-class signature and
  the _Version (00060000h = 393216); the control's own properties follow
  (ProgressBar: Max as a Single).  A .frm has property-bag text instead, so
  the state cannot be copied: `OleObjectBlob = "F.frx":0000` is refused
  ("Property OleObjectBlob in pb2 could not be set").

## Member descriptor (members.py)

- hdr+10: u16 variable count, u16 event count
- hdr+18: method descriptor array (parallel to the method names)
- hdr+20: variable descriptor array; hdr+24: event descriptor array
- Variable descriptor: +0 name, +C DISPID, +10 vtable offset << 16,
  +14 instance offset, +18 type code, +1C ObjectInfo when class-typed.
- Method descriptor:
  - byte 0 = (slots << 2) | kind (0 method, 1 Get, 2 Let, 3 Set)
  - byte 1: bit 0 returns a value, & 0xFC = ParamArray
  - high word: vtable offset (0xFFFF for an event)
  - +4: 0xFFFF, or the event slot this procedure handles (WithEvents)
  - +8: defaults {u32 size, u32 ptr} -> entries of u16 VT + value;
    strings as u16 length + ANSI, padded to even; VT_EMPTY = no default;
    trailing parameters without defaults are omitted
  - +C DISPID, +10 parameter names
  - +20 type stream: lead byte (0x1E method, 0x00 event), then one code
    per slot | 0x20 ByRef | 0x40 array (then a 2-aligned u16) | 0x80
    Optional; then, 4-aligned, one dword ObjectInfo per class-typed (0x13)
    slot.
- Type codes: 03 Boolean, 05 Byte, 06 Integer, 08 Long, 0A Single,
  0B Double, 0C Date, 0D Currency, 0F Variant, 10 String, 1B Object,
  13 a project class. A class-typed slot's ObjectInfo dword follows its
  code inline (aligned to 4) before the next code: `13 00 00 | OI | 90`.
- Optional parameters without a default: code | 0x80 and no defaults
  entry; callers pass the type's empty value (0 = null BSTR for String,
  VT_ERROR 0x80020004 for Variant).  The caller often builds that Variant
  in a *frame slot* and pushes the slot's four dwords: `fold_variant_slot_reads`
  turns the tag-half read into `Missing`, so the pushed chunk can arrive at
  `calls.byval_variant` as `[Missing, Var, Const(0x80020004), Var]` — the
  payload half is the sentinel's own value, not an argument.  A `Missing`
  tag half makes the whole argument `Missing`.
- Implements: the class lists the interface as a variable of type 13
  with vtoff 0xFFFF; the `Iface_Method` procedures are ordinary members in
  the own vtable, but their descriptor +4 holds the method's offset in the
  interface vtable (like a WithEvents handler's event slot).
- `__vbaCheckType(obj, &IID)` / `__vbaCheckTypeVar(&v, &IID)`: TypeOf obj
  Is C, where IID is C's default interface; `__vbaObjIs(a, b)`: a Is b;
  `__vbaCastObj(obj, &IID)` / `__vbaCastObjVar(&v, &IID)`: the implicit
  interface conversion of a Set.

## Records (UDTs) and cleanup lists (records.py)

- Descriptor entry kinds (tests/t16rec, a Type with every field type): 1
  BSTR, 2 Variant, 3 Object, 5 array, 9 embedded UDT, 10 `String * n` (the
  u16 after it is n), 11 a plain numeric field.  Bit 0x2000 adds two u16s
  after the entry: the field's BYTE SIZE and its offset in the ANSI copy of
  the record.  That size types a kind-11 field exactly (1 Byte, 2 Integer,
  8 Double; 4 Long/Single and 8 Double/Currency/Date stay ambiguous) - every
  one of them used to come out `As Long`, so a record's Integers, Bytes and
  Doubles were wrong (and a 2-byte field claimed 4 bytes, which dropped the
  field that followed it).


- `__vbaRecAssign(desc, dst, src)`, `__vbaRecDestruct`, `__vbaRecUniToAnsi`
  get a descriptor for each UDT that holds Strings/Objects/Variants:
  u16 ?, u16 size, u16 0, u16 count, u32 ?, then count x (u16 offset,
  u16 kind) with kind 1 = String, 3 = Object.
- A class's String/Object members are listed in the same format at
  tObject+0x08.
- UDTs are packed to 4 bytes: each field on min(its size, 4), the whole
  padded to its widest field's alignment.  A `String * n` field is not
  aligned at all (for example `String * 1` at 0Dh after a Byte, `String *
  2` at 0Fh; tests/t19fixrec), though it holds n UTF-16 characters.
- A record's descriptor lists only the fields after the first one whose
  ANSI offset differs (plus every String / Object / Variant / array):
  fields before it come from the code's accesses alone.  A Type ending in an Integer is
  not rounded up to the next dword (ChessBrainVB TMOVE: 8 Integers, 2
  Longs, 2 Booleans = 28 bytes, the last field at 1Ah).
- Entries are variable length (gh_zip ZipVfsType, ZipFileInfo):
  - kind & 0x3f: 1 String, 2 Variant, 3 Object, 5 dynamic array
    (0x0105; 6 more bytes follow: u16 ?, u32 ?), 9 embedded UDT
    (u32 descriptor of the inner Type, then 16 more bytes), 10
    `String * n` (u16 n follows), 11 Long.
  - kind | 0x2000: the entry has 4 more bytes right after (off, kind):
    u16 ?, u16 offset of the field in the ANSI copy of the record. Used
    once the ANSI layout differs (after a fixed-length string or a UDT
    holding one), so later plain Longs get entries too (kind 11).
  - kind 0x40 flag on an embedded UDT (0x2049): the inner Type needs
    ANSI conversion (it has `String * n`).
  - The header's first u16 is 0x10 for plain records, 0x22/0x50 seen
    for records with arrays / embedded UDTs.
- Synthetic Types must respect VB's alignment (Long/Double/Currency on
  4, Integer on 2): a dword access at 0xA (two Integers copied at once)
  emitted `fA As Long`, which VB moves to 0xC and shifts every later
  field by 4 — every field name after it then named the wrong bytes.
- Nested UDTs leave no trace in the code except whole-struct copies: `fl.b =
  tmp` (fline_t holding two fpoint_t) is `__vbaCopyBytes(8, &fl + 8, &tmp)`.
  A copy whose size is a whole UDT's, into an offset of a bigger UDT, makes
  that offset a field of the copied type (udt.nest_types); reads inside it
  then print as `fl.f8.f0` (apidb.field_at already walks nested types).
- A UDT whose first field is a dynamic array, passed ByRef: `ReDim
  p.field0(...)` pushes p itself, exactly like `ReDim arrayParam(...)`.  Only
  a field access at another offset through p tells them apart
  (decompile.struct_array_params).
- Arrays of UDTs: the element type has to come from the element accesses
  (`Deref(Index(arr), size, off)`), merged over every procedure for a module
  array (udt.array_element_types); the array's own variable also reads the
  SAFEARRAY header (pvData at +0Ch, lLbound at +14h), so those reads are no
  fields.  16-byte elements stay Variants unless the descriptor template says
  otherwise (gh_json arr(2) As Variant: tag at 0, value at 8).
- A module variable is one variable however many procedures use it: struct
  groups, element types and record types are shared program-wide
  (udt.group_structs node (None, key), udt.share_global_udts).
- A ByRef argument can be a record or its first field: `F rec` and `F rec.h`
  push the same address.  So a caller's record says nothing about a
  parameter the callee reads at offset 0 only; only the callee's own use
  does (a second field, a whole copy, a record descriptor, an API Type).
  decompile.scalar_params keeps record types off those parameters in every
  direction - in a large real program one caller passing rec.h to F(h, a)
  had made h a record, then the h variables of hundreds of callers and
  dozens of module variables used only as numbers (fixing it nearly halved
  vbcheck's complaints).
- `__vbaRecDestruct(desc, p)` returns p: `Get #f, n, arr(i)` frees the
  element and passes eax on to `__vbaGetOwner4` (the lift had eax still at
  i * the element size from the address arithmetic).

## Calls and runtime helpers

- `__vbaNew` / `__vbaNew2` take the ObjectInfo of a project class or form.
- `__vbaHresultCheckObj(hr, obj, IID, vtoff)`: the IID identifies the
  receiver's interface. An IID the type libraries don't know (form members)
  means "the object's own interface"; use its object type.
- Friend procedures are called directly with the receiver as first
  argument; the vtable is checked first (`__vbaFailedFriend` on mismatch,
  never returns). Results come back through a pointer, frameless.
- Calling a Public member of your own object goes through Me's vtable,
  e.g. `Set mSrc = New clsT04` in a form is
  `__vbaObjSet(&tmp, __vbaNew(...))`, then `Me.vtbl[0x768](Me, tmp)` (the
  WithEvents Set accessor), then `__vbaFreeObj(&tmp)`.
- `__vbaRaiseEvent(Me, event number, argc, Variants by value...)`, cdecl.
  The Variants are in DISPPARAMS order: the one nearest the fixed
  arguments is the **last** argument (found 2026-10-24 with t04w EvAll;
  earlier output had them reversed and still compiled).
- Late binding: `__vbaLateMemCall(obj, &name, argc, Variants...)` and
  `__vbaLateMemCallLd(&result, obj, &name, argc, Variants...)`, cdecl;
  `name` is a NUL-terminated UTF-16 string (no BSTR length prefix);
  the Variants are in the same reversed order (verified with
  `o.Add "z", "k"`). Ld returns &result.
- `__vbaNew(&ObjectInfo)` for project classes; for a library class
  (`New Collection`) the argument is `{0, &CLSID, &IID}` instead, e.g.
  CLSID A4C4671C-499F-101B-BB78-00AA00383CBB (VBA Collection) and IID
  A4C46780-... (`_Collection`).
- For Each over a collection: `t = __vbaForEachCollVar(&enum, &v, coll)`,
  loop while t <> 0, `t = __vbaNextEachCollVar(&enum, &v)` at the end.
- `__vbaPut3(cb, &v, filenum)` / `__vbaPut4(cb, &v, rec, filenum)` (Get the
  same): cb is v's byte count - 2 an Integer, 4 a Long, 40 a 40-byte UDT
  without Strings (VBDoom M_WriteBMP Put #f, , ih).  Arrays and UDTs with
  Strings go through `__vbaPutOwner3/4(descriptor, &v, filenum[, rec])`.
- File I/O: `__vbaFileOpen(mode, reclen, filenum, name)` with mode 1
  Input, 2 Output, 4 Random, 8 Append, 0x20 Binary and reclen -1 when no
  `Len =`; `__vbaPrintFile(&desc, filenum, items...)` cdecl, same
  descriptor as Print; `__vbaFileClose(n)`; `__vbaLineInputStr(&s, n)`;
  `rtcFreeFile(&Variant range)` (impure: the next free number changes),
  `rtcEndOfFile(n)`, `rtcKillFiles(&Variant)`, `rtcEnvironBstr(&Variant)`.
- `rtcStringBstr(n, &Variant char)`, `rtcBstrFromFormatVar(&expr,
  &format, firstday, firstweek)` (1, 1 = defaults),
  `rtcReplace(expr, find, repl, start, count, compare)` (1, -1, 0
  defaults).
- MSVBVM60's rtc* functions are imported by ordinal (e.g. #606
  rtcStringBstr, #648 rtcFreeFile, #650 rtcBstrFromFormatVar); the
  model resolves them from MSVBVM60.DLL's export table.
- Variants that VB builds for literals are tagged with 0x8000 in vt.
- `__vbaVarTst*` and `__vbaVarAdd` take the right operand first.
- ParamArray call: VB builds a temporary Variant array (ReDim, then
  VarCopy/VarMove into pvData + (i - lb) * 16), calls, then Erase.
- `__vbaAryConstruct2(&SA, &template, vt)`: the template is a SAFEARRAY
  image with the bounds in reverse order.
- Doubles are passed as two dword pushes; `__vbaFpCy` returns edx:eax.
- Print: descriptor = u16 count + one byte per item (low 6 bits VT; 0x40
  `;`, 0x80 newline, otherwise `,`).
- Line flags: 01 Step on point 1, 02 color, 04 point 1 given, 08 Step on
  point 2, 10 B, 20 BF. Coordinates are passed as Single bits.

## Dynamic array elements in optimised code with bounds checks

- One dimension: `cmp [psa], 0` / `cmp word [psa], 1` (allocated, cDims
  1), `t = i - [psa+14h]`, `cmp t, [psa+10h]; jae BoundsError`, then
  `t * K` stored in a frame slot - on the error path the slot gets eax, so
  the value crosses a join - and the access `[pvData + slot + field]`.
- Two dimensions: the bounds are kept last dimension first, so for
  a(x, y) the offset is ((x - [psa+1Ch]) + [psa+18h] * (y - [psa+14h])) *
  K (x's count at +18h multiplies y).
- Only after the slot is inlined does the element show; matched as a
  polynomial over the descriptor's fields (simplify.refold_dyn_index).

## For loops with a Single counter (tests/t18varfor)

A constant step is inline FPU code (fld x: fadd [1.5!]: fstp x, then fcomp
[end]).  A step held in a variable goes through
`__vbaR4ForNextCheck(counter, end, step)` (each a Single's dword, stdcall),
nonzero while the loop goes on, before the first pass and after each step.

## Omitted Optional arguments (tests/t20misc)

- An omitted Optional Variant is a Variant VB builds in place: tag 10
  (VT_ERROR) at +0, DISP_E_PARAMNOTFOUND (80020004h) at +8.  A ByVal one is
  pushed as those four dwords; a ByRef one as the address of such a
  temporary (`&Missing` once folded).  Callers leaving an argument out
  make the parameter Optional, whether or not the callee tests IsMissing.
- `Name old As new` is `__vbaNameFile(new, old)`.

## For loops with a Variant counter (tests/t18varfor)

- `For v = a To b Step s` with v a Variant: `t = __vbaVarForInit(&v, &s1,
  &s2, &a, &b, &s)`, `Do While t <> 0 ... t = __vbaVarForNext(&v, &s1,
  &s2) Loop`.  s1 / s2 are two Variants the runtime keeps the end and the
  step in; a, b, s are Variants the compiler builds inline (tag at +0 from
  a register: `mov ebx, 2` once for every VT_I2 of the procedure).
- An Exit For jumps past the loop; the Next then sits at the end of every
  other path through the body (structure.next_at_ends, shared with For
  Each).
- Get / Put of a `String * n` call `__vbaGetFxStr3(n, &v, file)`,
  `__vbaGetFxStr4(n, &v, record, file)`, `__vbaPutFxStr3/4` the same way:
  n first, like __vbaPut3/4's byte count.  The variable must be declared
  `String * n`: in a Random file a variable-length String is written with a
  2-byte length first.

## Resume

- `__vbaResume(n)`: -1 is `Resume Next`, -2 `Resume` (the failing
  statement again), 0 followed by a jump `Resume label` (tests/t12resume;
  a real program's handlers printed `Resume -1`, a syntax error).

## ActiveX control events (tests/t17ocxev)

- An OCX's events interface is a dispinterface (TKIND_DISPATCH), a VB
  control's a vtable interface.  For a vtable one, event table slot j is
  the interface's vtable slot 0x0C + 4*j.  For a dispinterface VB puts its
  own extender events first and the library's events are the LAST
  len(funcs) slots: a ListView has 28 slots for 19 events, so slot 12 is
  ItemClick, the library's fourth (reading it as the vtable slot named the
  handlers right from the map but gave them another event's parameters).
- The handler's parameters: an object at pointer depth 1 is ByVal (`ByVal
  Item As ListItem`; VB6 refuses it ByRef), everything else is ByRef - a
  dispinterface's scalars included, whatever depth the library gives them
  (VB6 refuses `ByVal Shift As Integer` for ListView KeyDown).  A vtable
  interface follows the depth: one level more than the type's own
  (`Source As Control` is a pointer to an interface pointer).
- The OLE aliases are written as VB's own types: OLE_XPOS_PIXELS and the
  other position/size aliases as Single in a handler, OLE_CANCELBOOL as
  Boolean, OLE_COLOR and OLE_HANDLE as Long.

## ActiveX controls in code (tests/t13ocx, t14ocxext)

- The control list gives an ActiveX control the events IID of VB's own .oca
  copy of the library ({34B90E64-..} for a ProgressBar), which no shipped
  type library has; the designer data names the class
  (MSComctlLib.ProgressBar), whose coclass gives both interfaces.
- An ActiveX control without event handlers is not in the control list at
  all (a CommonDialog, for example); its designer record's ID byte is its
  getter index - as it is for every listed control of a large real program.
- Members go through IDispatch by DISPID: `__vbaLateIdSt(obj, dispid,
  Variant by value)` stdcall (and ...StAd for Set), `__vbaLateIdCallLd(&r,
  obj, dispid, argc, ...)` / `__vbaLateIdCall(obj, dispid, argc, ...)`
  cdecl.  The control's own members by their memid (ProgressBar Max 1, Min
  2, Value 5, Refresh -550); the extender's are 80010000h + the designer
  opcode (Name 00, Left 03, Top 04, Width 05, Height 06, Visible 07,
  Parent 08, DragMode 09, DragIcon 0A, Tag 0B, TabStop 0E, TabIndex 0F,
  Object 1C, HelpContextID 32, Align 3B, WhatsThisHelpID 46, Container 47,
  CausesValidation 48, ToolTipText 4A) and 80011000h on for methods
  (ZOrder, Move).
- A property of a dispinterface comes back by DISPID as a Variant; when its
  declared type is an interface, VB casts it at once:
  `__vbaCastObjVar(&v, &IID)` (or `__vbaCastObj(obj, &IID)`) and Sets the
  result into a temporary freed after the statement.  The IID is the
  expression's static type (the property's declared type), not the type of
  the variable it lands in: `Set o = pb1.DragIcon` casts to IPictureDisp
  with `o As Object` (tests/t14ocxext).  Members through it go by DISPID
  again when that interface is a dispinterface (IFontDisp:
  `Me.ListView1.Font.Bold = True` on a ListView).  Wine's VB6 rejects
  `lv.Font.Bold = True` on an MSComctlLib ListView ("Variable uses an
  Automation type not supported in Visual Basic"); VB6 on Windows 98 builds
  it.

## Visibility and parameters

- An event handler other objects call is Public in the source: the form's
  public method names list it (a real program's Form2.Command1_Click;
  printed Private, "Method or data member not found" in the caller).
- `IsMissing(p)` in the body: p is Optional (callers that leave it out pass
  a ByRef Variant temporary holding the missing value, which the earlier
  test for Missing arguments did not see).

## Line, PSet, Circle (tests/t15gfx)

- The methods take a flags word first, then the values in source order:
  Line (flags, x1, y1, x2, y2, color): 1 Step on the first point, 2 a color,
  4 a first point, 8 Step on the second, 10h B, 20h BF; PSet (flags, x, y,
  color): 1 Step, 2 a color; Circle (flags, x, y, r, color, start, end,
  aspect): 1 Step, 2 a color, 40h start, 80h end, 100h aspect.
  Early-bound the coordinates are Singles by value; late-bound (`o As
  Object`: __vbaLateMemCall "Line", the name as typed - a real program wrote
  `obj.line`) every argument is a Variant of its own type.

## Print # items

- `__vbaPrintFile(desc, file, items...)`: desc is a u16 count, then a byte
  per item: the VT in the low 6 bits, 40h on every item but the last, 80h
  on the last (tests/t11print: 48 4C 4C 45 8C for "a"; v; o.Count; d; w).
  A Variant goes by reference, one dword, variable or temporary alike; a
  Double / Currency / Date as two dwords; anything else as one.  (Taking
  four for a Variant read the stack below it: t10carr's c.Text came out as
  the address of the name "Text".)

## Code generation details

- Debugging a decompiled build that fails at run time under Wine:
  `WINEDEBUG=trace+msgbox wine x.exe` prints the runtime error text
  (`MSGBOX_OnInit L"Run-time error '6'..."`). To find the procedure,
  add a first-entry trace (Static counter + Open/Print/Close) to every
  procedure of a copy of the output and rebuild (`tools/instr.py`);
  the last entries logged point at the culprit.
- `__vbaFpCmpCy(lo, hi)` returns Sgn(cy - st0).
- `__vbaVarIndexStore(&v, n, value Variant, n index Variants)` (cdecl):
  `v(i) = value`; `__vbaRedimVar(vt, &v, nDims, ub, lb...)` (cdecl):
  `ReDim v(lb To ub) As <vt>`; `__vbaVargVarCopy(ecx=ByRef Variant
  param, edx=src)`; `__vbaAryRecCopy(desc, &dst, &src)`;
  `__vbaVarCmpEq/Ne/Lt/Le/Gt/Ge(&res, b, a)` Variant-valued compares.
- A 64-bit Currency loop test is three branches: hi < hi2 -> body,
  hi > hi2 -> out, lo >=u lo2 -> out.
- A Boolean comparison used as a value is `neg; sbb; inc; neg`,
  i.e. -((a <> b) + 1) = (a = b).

- Class `Static` locals live in a per-instance block; its pointer is in a
  member slot (cZipArchive: Me+0xBC, never written by the code) and its
  layout is the record descriptor at the public object descriptor +0x0C
  (same format as UDT descriptors: size, String/Object/array entries).
- `__vbaVarZero(ecx=dst, edx=src)` copies a Variant into an uninitialised
  one (the items of `Array(...)`); `rtcArray(&result, &paramarray)`.
- `__vbaLateMemSt(obj, &L"name", Variant by value)`: `obj.name = v`
  (stdcall, 6 dwords). `rtcCreateObject2(&res, class, server)`.
- The compiler writes `x And &HFFFFFFFE` as `and al, 0FEh`, and builds a
  Boolean True as a word store of 0FFFFh.
- C2 shares a procedure's last statements between paths: each Case of a
  Select fills the same Variant temporary (tag at k, value at k+8, the
  tag sometimes from a register) and jumps into one `lea edx,[temp];
  VarCopy; exit`.
- `test ah, 40h` after a call reads bit 14 (VT_BYREF of a Variant tag).

- A form/class Function returning a UDT: the caller passes a result
  buffer as the last argument; the epilogue block-copies the result
  local into it (`mov edi,[ebp+last]; mov ecx,N; lea esi,[ebp-X];
  rep movsd`). The local is released with `__vbaRecDestruct` on the
  error path only (gh_zip pvVfsOpen/pvVfsCreate As ZipVfsType).
- `With a(i)` keeps the element address in a hidden frame slot, set
  once and cleared to 0 at End With. Uses are `[p+k]`, `[[p+k]+j]`,
  `p+k` passed ByRef (a field) and `p` passed ByRef (the element when
  the parameter is the UDT, else its first field).
- Currency arithmetic: `__vbaCyAdd/Sub/Mul(lo1, hi1, lo2, hi2)`,
  `__vbaCyMulI2(i2, lo, hi)`, `__vbaCyI2/I4`, `__vbaCyVar(&v)` return
  the int64 in edx:eax (stdcall); `__vbaI4Cy(lo, hi)`, `__vbaR8Cy(lo,
  hi)` (st0), `__vbaFpCmpCy(lo, hi)` compares st0 with a Currency.
- A Currency compared with a constant is an inline 64-bit compare:
  `x <= C` = `(hi <= H) And ((hi < H) Or (lo <=u L))` (unsigned low
  half), `x > C` the Or/And mirror image (pvOutputLocalHeader
  `cFileSize > 2 ^ 32 - 2`).
- A ByVal Currency/Double/Date argument is two pushed dwords
  (`lo, Hi(lo)`); a frameless method writes an 8-byte result through
  the last-argument pointer in two halves (pvToInt64).
- `Now` = rtcGetPresentDate (ordinal 546) into a Variant temp, then
  `__vbaDateVar` for a Date parameter.

- A class's own events (no type library): slot 0 Class_Initialize,
  slot 1 Class_Terminate. Misnaming them still compiles but they never
  run.

- A procedure whose only statement is a call to another procedure with
  the same arguments compiles to a bare `jmp` (Sub Main -> RunAll5).
  Following the jump copies the callee's body into the caller.
- `or reg, -1` is the compiler's short form of `mov reg, -1`.
- Register values that live across a block edge are computed from the
  block's entry state; storing them is a parallel copy (order matters:
  `edx = (ecx + 1) Mod 3` must be stored before `ecx = ecx + 1`).
- VB evaluates operands of `&` in an order that is not left to right
  when calls are involved (`o.Item(2) & CStr(o.Count)` calls Count
  first); keep the temporaries.
- A ByRef array parameter `a() As Long` is a SAFEARRAY**: `[[arg]+0x0C]`
  is pvData, `[[arg]+0x14]` the lower bound; `__vbaLbound`/`Ubound`
  get `[arg]`.
- The compiler folds simple UDT With blocks to constants (WithTest
  returns 9 directly).
- Static locals are module-level variables (only one procedure uses
  them).

- `rep movsd` (with `ecx` = dword count) copies UDTs: into a Function's
  hidden result buffer, or between UDT variables.
- A Function returning a UDT takes a hidden first argument: the caller's
  result buffer. The callee builds the value in a local, copies it into
  the buffer and returns the buffer address in eax.
- `String * n` inside a UDT occupies 2n bytes (UTF-16); e.g. TRec {Long,
  String * 4, Double} is 0x14 bytes with the Double at 0x0C.
- Runtime argument conventions found with t06m: `__vbaR8Sgn(double)` ->
  eax; `rtcSqr(double)`, `__vbaPowerR8(a, b)` -> st(0); `__vbaFPInt`,
  `__vbaFPFix` take and return st(0); `rtcRound(&res, &v, digits)`;
  `rtcSplit(&res, s, &delim, limit, compare)` then
  `__vbaAryVar(vt | 0x2000, &v)` -> SAFEARRAY*; `rtcJoin(&Variant(byref
  array), &delim)`; `rtcChoose(&res, index As Single, &ParamArray)`;
  `rtcPackDate(&res, y, m, d)`; `rtcDateAdd(&res, interval, number (2
  dwords), &date)`; `rtcGetYear/GetMonthOfYear/GetDayOfMonth(&res, &d)`,
  `rtcGetDayOfWeek(&res, &d, firstday)`; `__vbaDateVar(&v)` -> st(0);
  `__vbaMidStmtBstr(0, src, length, start, &target)`;
  `__vbaLsetFixstr(n, &dst, src)`, `__vbaStrFixstr(n, &src)`.
- `n Mod 2` compiles to `x And &H80000001`, then for negative x
  `((x - 1) Or -2) + 1`.

- `StrPtr(s)`, `ObjPtr(o)` and `VarPtr(x)` are all calls to MSVBVM60's
  `VarPtr` export; StrPtr/ObjPtr push the value, VarPtr the address.
- `__vbaVarVargNofree(ecx=&tmp, edx=&v)` returns &v (or what a VT_BYREF v
  points to): every read of a ByRef Variant parameter goes through it.
- `__vbaVar2Vec(&sa, &v)`: array = Variant; `__vbaStr2Vec(&sa, s)`:
  Byte array = String; `__vbaAryLock(&tmp, sa)` ... `__vbaAryUnlock(&tmp)`
  bracket a ByRef pass of an element (`baRetVal(0)`) or a With on one.
- A Function returning a record UDT returns like a Variant Function
  (hidden result pointer, slot copied at the end) but its slot is
  released with `__vbaRecDestruct(&slot, desc)`.
- The result pointer of a Variant Function is usually loaded into eax for
  the copy (`mov eax, [ebp+8]; mov ecx, [ebp-N]; mov [eax], ecx` ...), but
  can be in edx (`mov edx, [ebp+8]; mov eax, [ebp-30h]; mov [edx], eax`
  ... `mov eax, [ebp+8]`): three Functions of a large real program, all
  with On Error and the epilogue after a one-byte `ret` cleanup (push cont;
  jmp ret).
  A ByRef parameter is loaded the same way; the copy covering +0 and +8
  and eax reloaded from [ebp+8] before the ret tell them apart.
- Record descriptor entry kind 0x0105: a dynamic array field (low byte
  5; 1 = String, 3 = Object).
- `__vbaRedim`'s "vt" argument is the record descriptor address for an
  array of record UDTs.
- A Boolean/Integer Function's epilogue loads `ax` (`mov ax, word ptr
  [ebp-N]`), not eax.
- One-operand `imul r`: VB's overflow-checked Long multiply.
- Module-level fixed arrays: the global slot holds pvData and is the
  pvData field of a SAFEARRAY descriptor in .data ([u32 vt][SAFEARRAY],
  descriptors one after another in declaration order). The descriptors are
  zero in the file; static images in .text (u16 cDims, u16 0x92, u32
  cbElements, 0, 0, bounds last dimension first, u16 vt) supply them in
  the same order. Local fixed arrays use images too, but code passes
  their address (to `__vbaAryConstruct2`) and their vt word is 24.
- Division by a constant: `x \ d` = q + (q >>> 31) with q = Hi(m * x) >> s
  (sar), or q = (Hi(m * x) + x) >> s when m >= 2^31; m = ceil(2^(32+s)/d).
- A frameless procedure may store a local into a parameter's slot once the
  parameter's value is in a register (pvGetTotalBits: UBound over the
  array pointer).
- `With a(i)` keeps &a(i) in a register for the block.
- `__vbaVarIndexLoad(&res, &v, n, index Variants...)` cdecl: v(i);
  `__vbaRefVarAry(&v)` -> &(SAFEARRAY* in v).  A typed variable used
  where a Variant index is wanted (`FileInfo(lIdx)`, the parameter being
  `As Variant`) is boxed **by reference**: tag `VT_BYREF | VT_I4`
  (0x4003) at slot k, the variable's address at k+8, no copy of the
  value.  Such a Variant reaches the call as four consecutive local
  slots (the 16-byte VARIANT copy), which is how it is recognised again.
- `Public Property Set PropVar(ByVal v As Variant)` called as
  `Set k.PropVar = k` (t04w): the object pointer goes into the local
  Variant's data slot first and the tag (`VT_DISPATCH`, 9) only just
  before `__vbaVerifyVarObj(&v, obj)` and the vtable call, so that
  tag/value pair has to be read backwards.

- `Select Case` over dense small integers compiles to a jump table:
  `cmp idx, N; ja default; jmp dword ptr [idx*4 + table]`; the N + 1
  entries follow the procedure's code in .text (data inside the code
  section). `case - 1` is often the index (`lea eax, [ecx - 1]`).
- C2 shares tails between Select cases (the `Mod 2` sign fix), so the
  output keeps a few GoTos there; the meaning is unaffected.
- Integer arithmetic width shows the source type: 32-bit add/sub/imul
  means Long operands even when the value came from a Byte element.
- The overflow check `jo` targets __vbaErrorOverflow stubs (and, at
  least once, the middle of an instruction): never real control flow.
- `Abs(x)` for a Long: `cdq; xor eax, edx; sub eax, edx` (edx = 0 or -1 by
  the sign).  `x \ 2^k` for a Long: `cdq; and edx, 2^k - 1; add eax, edx; sar
  eax, k` (or `cdq; sub eax, edx; sar eax, 1` for 2).  insns.SignOf carries
  the halves of both.
- A Function's value is eax at the exit only if a caller uses it.  A Sub whose
  last statement is a call (or that keeps the call's eax across a join to the
  exit) seems to return the callee's value; a call counts as used only when
  that is not its only way out (decompile.first_pass fixpoint).

## Types that travel in loops (2026-10-28)

- Evidence flows both ways along copies (x = y types y as x and x as y)
  and ByRef parameters (a variable passed to a ByRef Variant must be one;
  a parameter takes what its callers pass).  Variables joined that way form
  loops, and types.ORDER puts String, Variant, Object, Date, Double, Single
  above Long: one stray Variant or Double anywhere in a loop wins in all
  of it (in a large real program a global came out As Variant and then
  As Double, with hundreds of ByRef mismatches).
- The machine code breaks the loop: VB never loads a Variant's first dword
  as a value (Variant to Long is __vbaI4Var), and Singles / Doubles are
  loaded and stored by the FPU (fld / fstp; fild and fistp are integer
  accesses).  A dword loaded as a value with no FPU access is a Long-sized
  integer, String or Object.  Currency is computed in integer registers and
  with fild qword, and Dates are mostly moved, so neither is ruled out by
  that.  A module variable's FPU accesses count program-wide (a Double may
  only be copied in most procedures).

## Typing a parameter from its call sites

- `Types.collect` unions the types callers pass; `pick` then takes the
  first of ORDER, which starts with String.  With many call sites a few
  wrongly typed arguments decide the parameter: in a large real program a
  procedure doing `Get #f, n, ...` (n a Long record number) had thousands
  of callers passing a Long and a handful looking like String / Menu /
  TextBox, so it came out `As String` - and every one of those calls was
  then a ByRef type mismatch, more than half of the whole project's static
  problems.  `types.dominant`
  drops the stragglers when one type has at least 8 call sites and 3x the
  rest.

## What an IR pass may not assume

- A statement that runs under `On Error Resume Next` can stop part-way: the
  store it performs never happens and the variable keeps its previous value.
  VBA-JSON depends on it - `json_LBound2D = -1` and then
  `json_LBound2D = VBA.LBound(JsonValue, 2)` (error 9 for a 1-D array)
  leaves the -1 alive, which is how `json_LBound2D >= 0` learns "no second
  dimension"; the slot's zero initialiser would make every 1-D array 2-D.
  Dead-store elimination, liveness, temp renaming and inlining all assume a
  store definitely executes, so they must skip such statements:
  `simplify.mark_error_regions` tags them (query with `may_skip`) and the
  liveness passes keep the old value live across them.
- A Variant literal built in a scratch slot and handed to the result slot by
  `__vbaVarCopy` (`lea edx,[ebp-scratch]; lea ecx,[ebp-result]; mov
  [scratch+8], v; mov [scratch], 0Bh; call __vbaVarCopy`) lifts to a single
  `result = Const(v, "Boolean")`: the whole value, tag included.  A later
  read of that slot must not re-read its first dword as a tag word -
  `json_ParseValue = False` is `Const(0, "Boolean")` and a bare 0 there is
  VT_EMPTY, which turned every JSON `false` into Empty in gh_json (the entry
  then vanished from ConvertToJson output, whose marshalled text for Empty
  is "").  A `Const` carrying `vbtype` is a value, never a tag.

- A slot `fold_variant_temps` merged from (tag, payload) holds the whole value
  (`v = 9` for VT_I2 9); it is marked `whole_variant` and is never a tag word
  for a later pass.  Read as a tag, 9 is VT_DISPATCH and the stale payload slot
  above it became the argument (gh_dict `TryAdd(d, "a", 9)` -> `&0`).  `&v` of
  such a temporary passed ByRef *is* that value (VB builds it for the call:
  `json_BufferAppend buf, s & "]"`), so it must still be resolved under AddrOf.
- A statement that runs before an `Exit` can't be moved behind it:
  `structure.last_block` looks past trailing Exits, so `hoist_common` (both
  arms' identical last statement after the If) must skip arms that leave.

- Block keys must stay unique.  split_merges names the half it splits off
  a merge block a + 0.5 and duplicate_small_joins its copies a + 0.25k/P;
  both run in every round, and inlining can make the same block split
  again (VBDoom P_GroupLines).  Two blocks under one key overwrite each
  other's set in the liveness fixpoint, which then never settles: the
  decompile hung.  free_key picks a free key; block_live_in raises after
  len(body) + 2 rounds (enough for a may-analysis in any block order).
- Passes must stay near-linear in the procedure's size: real programs have
  procedures of thousands of IR statements.  The single-use inliner once
  re-walked the procedure for every candidate after every inline (cubic)
  and dead-store elimination counted uses per assignment (quadratic);
  var_index gives all counts and places in one walk.
- decompile.py runs with Python's cycle collector off: with millions of IR
  nodes alive, its passes over them were a quarter of a VBDoom decompile,
  and little of the heap is cyclic garbage (peak 956 MB instead of 825 MB).
  Turn it back on (gc.enable) if a much larger program runs out of memory.
- What is written must not depend on the order of a set of strings: Python
  randomises string hashes per process (PYTHONHASHSEED), so a set of names
  iterates differently on every run (a set of ints does not: an int is its
  own hash).  writer.api_types walked each object's Declare names (a set
  from uses()) for the Types their parameters need; VBDoom's i_video declares
  SetDIBColorTable (RGBQUAD) and CreateDIBSection (BITMAPINFO), so RGBQUAD or
  BITMAPINFOHEADER came first at random.  Sort first.
  `tools/cmpdec.py . . OUT EXE... --seeds 1,2` checks for this.

- A frame slot's known value is only good until a variable in it changes.
  The lifter forwards what a slot was last given to later reads of the slot
  (an argument slot reused as scratch); `pl = lastvisplane: lastvisplane =
  lastvisplane + 1: visplanes(pl).minx = start` (VBDoom R_CheckPlane) then
  read the new lastvisplane.  Lifter.forget_known drops such values when the
  variable is written (globals, members, statics, fields, other slots); the
  read falls back to the slot's own variable, which holds the old value.
- Machine arithmetic width is not VB's: a 32-bit shl / add / imul of a
  zero-extended Byte is Long arithmetic in the source (`CLng(b) * 256&`,
  compiled as shl), while VB would do `b * 256` in Integers (sign-wrapping
  from 128 with overflow checks off); the FPU's `fild a; fimul b` is
  `CDbl(a) * b`, while `a * b` of two Longs overflows.  BinOps carry `width`
  (4: 32-bit integer) and `fp` (FPU) for the renderer, which widens one
  operand when VB would otherwise pick a narrower type.

## Unoptimised code (OptimizationType=2; VBDoom, tests/t09noopt)

- Prologue without an SEH frame: `push ebp; mov ebp, esp; push N; pop eax;
  call __vbaChkstk` (or `mov eax, N`).  With one: `push ecx; push ecx` reserve
  the bookkeeping ([ebp-4] procedure descriptor, [ebp-8] saved esp), then the
  SEH record and the same allocation.  Procedures with On Error still use the
  optimised layout (`sub esp, 18h`, statement number at [ebp-4]).  Epilogue
  `leave; ret N`.
- Every temporary lives in the frame; values cross blocks through memory
  (`mov [ebp-0B8h], offset Global` on both paths, read at the join).
- Stores of constants: `and [x], 0` (0) and `or [x], -1` / `or word [x], 0FFFFh`
  / `or byte [x], 0FFh` (all ones).
- Runtime helpers are called through the import thunks (`call rel32` ->
  `jmp [iat]`); procedures are not aligned to 16 bytes.
- Dynamic array elements: `push i; push psa; call __vbaDerefAry1` returns the
  element's address (bounds checked inside).  Fixed arrays: an inline check
  `cmp i, n; jae __vbaGenerateBoundsError`, then `pvData + i * size` built with
  separate adds - one check per index of a multi-dimensional array.
- Module-level fixed arrays of UDTs have static templates with fFeatures 12h
  (static | fixed size, no VARTYPE, no FADF_RECORD) and the UDT size as
  cbElements; scalar ones 92h (+100h for Strings), with the VARTYPE after the
  bounds.  Templates come in declaration order; the descriptors in .data put
  Public arrays first, then Private ones.
- The templates sit in each module's variable table (ObjectInfo +08h
  public_bytes for the data at +10h module_public, +0Ch static_bytes for
  +14h module_static): a u16 pair (offset, flags) per variable, flags low
  byte 01h a scalar, 05h a fixed array (100h set: a dynamic one, no image),
  and 16 bytes after an array's pair its static SAFEARRAY image (32 for an
  array of records: a pointer to the record descriptor comes first).  The
  offset is the descriptor's within the module's data, its pvData slot 0Ch
  further: VBDoom g_game (module_public 48A9F8h) lists (1Ch, 05h) before
  consistancy(0 To 3, 0 To 11), whose pvData slot is 48AA20h.  This gives
  every module array's bounds exactly (Model.global_array_slots: 146 in
  VBDoom); matching runs of templates to slots (global_array_bounds) had
  shifted them where modules lie in .data in another order than their
  templates in .text.
- A Boolean passed to a Variant parameter is built in place: tag 0Bh, payload
  the variable (`ok`), so only the Variant says it is a Boolean.
- `ret N` is the parameters' exact size (stdcall): a ByVal Double, Currency
  or Date needs 8 of those bytes.  UDbl(ByVal a As Long) As Double ends with
  `ret 4`; typed from its result it had become ByVal Double, and SlopeDiv's
  two Longs one Double through it.  A ByRef parameter is 4 bytes whatever
  its type.
- A procedure's parameter handed to a ByRef parameter: VB pushes the copy's
  address (`lea eax, [ebp+8]; push eax`) for a ByVal one, the pointer itself
  (`push [ebp+8]`) for a ByRef one - so a pushed parameter value means ByRef
  (gh_json ConvertToIso(utc_LocalDate As Date) -> ConvertToUtc ->
  utc_DateToSystemTime).  Not for Variants: their own conventions.

## Optimised code (OptimizationType=0, Pentium Pro, checks removed; ChessBrainVB)

- `Resume label` is `push 0; call __vbaResume` followed by a plain `jmp label`
  (the call only leaves the handler state).  Where the label's code only ends
  the procedure, C2 copies that code (or just the exit: `call __vbaExitProc;
  push cont; jmp cleanup`) right after the call instead of jumping.  The
  handler's jump lands in the middle of code the normal flow reaches: a block
  starts there.
- The FPU stack carries values across blocks: `fild Nodes`, on one path
  `fimul NoOfThreads`, then `fcom` at the join.  The status word is not
  EFLAGS: the scheduler puts integer code between `fcomp` and `fnstsw ax`.
- One-operand FPU pop forms are `st(i) = st(i) op st(0)`, then a pop:
  `fdivp st(1)` is DE F9 (Intel FDIVP ST(1), ST(0)); `fdiv st(1)` (D8 F1) is
  `st(0) = st(0) / st(1)`; two operands (`fdiv st(2), st(0)`, DC F2) write
  st(i) without a pop.  Capstone names them as Intel does (DE F1 is fdivrp).
- A Single literal is stored with an integer mov of its bits (`mov [esp+18h],
  3FC00000h` for 1.5); a Single is loaded with `fld dword`, a Long with
  `fild`.  A Function returning a Single / Double may end `call other: ret`
  (TimeElapsed) or with ret alone in its block (TimerDiff): its callers read
  st(0) right after the call.  A tail jump into the runtime (`jmp
  [__vbaFpI4]`) returns the runtime function's result.
- A fixed module array's pvData is loaded into a register once and indexed
  from there; loops step a pointer (p = pvData + 4: ... *p: p = p + 4) or a
  byte offset (edx = 0, 4, 8, ...; compared with 28 for "To 7") instead of
  the source's index; 2-D elements are pvData + (i + j * n) * size, a field
  of one + the field offset; constants multiply as lea / shl / add steps
  (x * 34000).  Parameter slots are reused as locals (CalcTime's Boolean
  TimeTypeIsOptimum slot holds the Single Ratio later), and one register can
  hold several variables that start at the same value (Ply = 0, False and a
  loop index all in edi after `xor edi, edi`).
- The module variable table's VARTYPE of a fixed array is exact: a Long or
  Integer array is no array of UDTs, whatever the accesses suggest.
- A Function's result reaches eax in its final epilogue, after the exit's
  cleanup routine (whose calls leave eax at whatever the last one returned):
  `mov eax, [ebp-N]` / `mov ax, ...` / `fld`, or the hidden result
  pointer.  A procedure with the exception frame whose final epilogue
  sets none of them is a Sub, whatever a caller seems to read from eax
  after calling it (many module procedures of a large real program had
  been taken for Functions that way).
- The program's empty string literal is an empty BSTR constant in .text
  ([u32 0][u16 0], one shared address, used thousands of times in a large
  real program).  The
  same six bytes occur elsewhere (VBDoom pushes 404206h as a Long), so it
  counts as "" only where it is handed to a string runtime function.
- C2 cross-jumps identical code: several Cases end in a jump into one
  copy of "reload the control and its vtable pointer from frame slots,
  push, call [edx+off]", and calls share one HRESULT check ("call [eax+
  204h]; jmp shared" where shared is "test eax, eax; fnclex; jge ok; jmp
  check").  What the ways in agree on (the same control) is still known at
  the join; the check's IID names the interface.
- Optimised code can end a procedure in a jump to another procedure's
  entry (a tail call): `If IsNull(x) Then Proc_401000 Else ...` jumped to
  Proc_401000's exception-frame prologue.
- A procedure can exceed 64 KB (one in a large real program is over 90 KB,
  its On Error handler near its end).  A handler the normal flow falls into
  still needs its label (`ErrHandler:` after the last statement).
- A constant kept in a callee-saved register (edi = 2, the VT_I2 type word
  of every Integer Variant temporary) can be reused for something else
  inside a loop (an object pointer) and set back before the back edge
  (tests/t21font).  Which constant a register holds at a node is a reaching
  definitions question: a write reaches until the next write of the
  register on the path.  The procedure's entry value is never read.
- A fixed array's pvData is copied between registers (t2 = t_ecx in a loop)
  and loaded again in several places; its uses can sit in branch conditions
  only (`Do While Board(Target) <> FRAME`).  The pvData slot of a fixed
  module array never changes, so every copy of it is the array itself.
- Byte offsets are computed whole, not stepped from an index: SqBetween(sq,
  sq1, sq2) is pvData + (sq + (sq1 + sq2 * 120) * 120) * 2, then stepped by
  dir * 28800 in sq2's loop; a loop test compares the old value plus the
  step with the scaled limit (t1 + 4 <= 28 for "To 7").  The descriptor's
  bounds (first subscript first, stride 1) split a flat element number back
  into subscripts; a variable holding all of it cannot be split.
- A parameter's value pushed to another procedure is an address only if the
  callee's parameter is ByRef: GetMax(ByVal x1, ByVal x2) receives every
  Long of the program by value.  A ByRef struct parameter copied as a whole
  (VBDoom WI_initVariables, LSet / CopyMemory) looks ByVal until it has a
  Type - its size from the copy says otherwise.

## Win32 Declares

- `ByVal x As Any` (WIN32API.TXT style) does not accept an Integer literal:
  `InsertMenu h, 0, f, id, 0` is "Type mismatch". Writing the literal as a
  Long (`0&`) compiles. Found 2026-10-24 on the sample program prjMenu; the
  decompiler now writes such constants with `&`.
- `ByRef ... As Any` arguments that are values (not addresses) need
  `ByVal` at the call.
- Strings passed to `As String`/`As Any` parameters are pre-converted to
  ANSI literals by VB; decode them back to `"..."`.
- A Declare WIN32API.TXT does not know is written `ByVal aN As Long` per
  dword, except where a call passes an address: an element's
  (`__vbaDerefAry1`'s result: VBDoom `FillMemory vp_top(i * 322), 322, &HFF`)
  or a variable's makes it `aN As Any` with the element or variable passed.
  As ByVal Long the element's value went out and RtlFillMemory wrote through
  it (R_FindPlane hung).

## VB6 compiler behaviour

- Compile errors report "Line N", counted from 0 after the last
  `Attribute` line: file line = last_attribute_line + N + 1 (1-based).
- A module of more than 65,534 lines makes `VB6 /make` quit at once with an
  empty log (exit code 1, no message box): seen with all of a large real
  program's module procedures in one module.  Lines longer than 1,023
  characters are refused; a statement may continue over 24 lines (` _` after
  a comma or an operator).
- Source files are read in the ANSI code page (cp1252 here): a .vbp or
  .frm written as UTF-8 turns a file name with an accented letter into
  "File not found" and every accented letter in a string literal into two
  characters.
- `/make PROJ.vbp /out make.log` appends to make.log; read the last entry.
- "Could not execute 'LINK.EXE'" / "'C2.EXE'" in a Windows 98 VM is
  transient (seen once with 256 MB free); just rebuild.
- VB6 runs C2.EXE once per module (`-il <IL> -f <src> -W 3 -Gy -G5
  -Gs4096 -dos -Zl -Fo<obj>`); the .OBJ files are per-procedure code.
  `<IL>` is a temporary IL store per module (%TEMP%\VB<n>), a PDB-format
  file C2 reads through MSPDB60.DLL (ILStoreOpen, ILStoreGetILMod, ILModGetIL;
  C2.EXE+0x90d30); then LINK.EXE with the .OBJ files, VBAEXE6.LIB, `/ENTRY:__vbaS`
  and the project's LinkSwitches.
- C2.EXE (REA/Ghidra, 2026-10-27): "Microsoft (R) 32-bit C/C++ Optimizing
  Compiler", the VC6 back end (utc2 P2) with a `-basic` switch; 2174 functions,
  nearly pure C (49 MSVCRT imports, 2 KERNEL32, 1 USER32, 16 MSPDB60); fatal
  errors end the process.  LINK.EXE: Incremental Linker 6.00.8168, 1231
  functions, C++ (37 KERNEL32, 115 MSVCRT, 3 MSPDB60); it can spawn
  cvtres.exe and cvpack.exe.
- Their cost under Wine 11.20 (tools/wineprof.py): Wine's cold start 0.25 s;
  C2 about 40 ms wall and 13 ms CPU per module, each with its own
  conhost.exe; LINK 20-50 ms.  VBDoom's 58 modules build in 2.8 s, a
  2-module test in 0.42 s.  The x86 code runs natively under Wine, so
  recompiling C2 or LINK would only save per-process overhead.
- Builds can run side by side in one Wine prefix (2026-10-28): each VB6
  instance names its IL stores %TEMP%\VB<6 digits> from a random base, one
  higher per module (eight builds started within 10 ms: 309590, 091514,
  730346, ...), not GetTempFileNameA's VBxxxx.tmp.  124 C2 runs of 52
  concurrent builds used 124 different names; rtsuite -j 8 without the
  cache passed twice.
- VB6 runs under Wine 11.20.  Forms build too: on 2026-10-28 all eight
  VM-built form projects (the four sample programs with source, t01-t04)
  and one-control forms of every intrinsic control built and ran; the crash
  of 2026-10-24 (VB6.EXE+0x581F1, after C2, before LINK) did not come back.
  Their EXEs differ from the VM's in fonts Wine lacks (Comic Sans MS is
  stored as "Liberation Sans").  Setup: tools `tools/vbmake.py`,
  `tools/roundtrip.py`, `tools/rtsuite.py`.
- ActiveX controls need tools/vb6fix: the first time VB6 uses an OCX it
  copies the OCX's type library into a cache library (MSCOMCTL.oca), and
  four of Wine's type library functions break that (each found from a
  WINEDEBUG=+typelib,+ole trace and Wine's typelib.c):
  1. ICreateTypeLib2::SetHelpStringContext and ICreateTypeInfo2's
     per-member help-context and custom-data setters are E_NOTIMPL stubs
     ("System Error &H80004001 Not implemented").
  2. GetRefTypeInfo makes a new copy of a dual interface's other view each
     time; VB6 walks from view to view until it meets one it has seen, so it
     recursed through ITabStrip's two views until the stack overflowed.
  3. ITypeInfo2::GetDocumentation2 does not look in base interfaces
     (IUnknown's members in a dual's dispatch view): "Object library
     invalid".
  4. SaveAllChanges fails (E_UNEXPECTED, "Unexpected error") on an empty
     string in the library - VB6 sets the help string DLL to "" before each
     type from another library.
  And one in Wine's pictures, for any form with an Icon (or other .frx
  picture), OCX or not:
  5. IPicture::SaveAsFile fails for icons and bitmaps unless fSaveMemCopy
     is TRUE; VB6 copies the picture with FALSE (!KeepOriginalFormat), gets
     no stream and reads through the NULL (VB6.EXE+D58D, "Unhandled page
     fault on read access to 00000010"; VB6 then waits on Wine's crash
     dialog - a build that never ends).  Retried with TRUE, Wine writes the
     original data, as Windows does for FALSE; the icon comes back byte
     for byte.
  vb6fix.dll replaces those vtable entries when it loads (all of Wine's
  type infos, libraries and pictures share their vtables); tools/patchvb6.py gives
  VB6.EXE an import of it (a new section with the import directory; VB6's
  code is unchanged).  The OCX also needs its type library's dependencies
  registered: MSDATSRC.TLB for MSCOMCTL (tools/vb6fix/regtlb.exe).  Service
  Pack 6 alone changes nothing here.
- OptimizationType=2 (no optimisation, VBDoom): procedures are not aligned
  to 16 bytes, and runtime helpers are called through the import thunks
  (`call rel32` to `jmp [iat]`) as well as `call [iat]`.
- The .res file: VB adds the version block (RT_VERSION, from the .vbp) and
  its own icons (RT_ICON 30001.., and the RT_GROUP_ICON listing them) to the
  EXE; every other resource came from the project's .res.  resources.py
  writes those back as RC.EXE would (VBDoom: byte-identical doomvb.res).

## Compile options in the code (resources.compile_options)

Checked with one function built four ways (all off, then each of
FlPointCheck, FDIVCheck, UnroundedFP removed):
- Integer overflow checks: `jo` to a call of `__vbaErrorOverflow`.
- Floating-point error checks: `fnstsw ax; test al, 0Dh; jne` after FP
  operations (`__vbaFPException` is imported by every EXE either way).
- Pentium FDIV check: every `fdiv` is `cmp dword [flag], 0; jne` around it
  plus a call of an `_adj_fdiv_*` routine (also always imported).
- Array bounds checks: calls of `__vbaGenerateBoundsError`.
- Unrounded FP allowed: only the int-to-Double conversions differ (`fimul`
  instead of a Double temporary); same values at the 53-bit precision
  Windows sets, so left at the default.
A check absent from the code is written as removed; for a program without
that kind of arithmetic both settings compile the same.  It matters for
behaviour: with overflow checks removed (VBDoom) Long arithmetic wraps,
with them it raises error 6.
- Optimisation on or off (tools/optprobe.py, 2026-10-28): unoptimised code
  calls the runtime only through the `jmp [iat]` thunks (DoomVB 13,643 of
  13,643 calls, t09noopt 198 of 198) and opens procedures with `push ecx`;
  optimised code calls `[iat]` directly (93-100% of the calls in the 21
  OptimizationType=0 suite EXEs; the rest are the startup code's) and opens
  with `sub esp, imm8`.  Right for all 23 suite EXEs.  A large real program
  built with optimisation made about 2% of its calls through the thunks and
  had no unoptimised prologue.
  Fast vs Small code, Favor Pentium Pro, No Aliasing and Unrounded FP are
  not told apart (no marker found yet; a Small-code build to compare with
  is missing).  The .vbp still says OptimizationType=0 whatever the EXE.
  The four checks never disagree with a source .vbp the other way (written
  "kept" where the source removed them).
- Twip sizes in the designer stream are rounded to whole pixels at 15
  twips each (ClientHeight 2000 comes back as 1995).

## gh_dict findings (2026-10-25)

- A class's member array is often reached through a frame temporary that holds its
  *address*: `lea ebx,[esi+38h]; mov [ebp-0D8h],ebx`, then `__vbaRedim(.., ebx, ..)`
  and element access via `[[ebp-0D8h]]+0Ch`.  The temporary is `&dict_pKeys`, not a
  copy of the array.
- `Items = dict_pItems` (Variant = array): VB builds a frame Variant with vt 200Ch
  (VT_ARRAY|VT_VARIANT) and the SAFEARRAY pointer at +8, then `__vbaVarCopy`s it
  into the result.
- `Array(a, ByRefVariantParam, ...)`: `__vbaVargVarRef(ecx=&tmp, edx=param)` returns
  eax=&tmp, then `__vbaVarZero(ecx=&element, edx=eax)` stores it into the ParamArray SAFEARRAY.
- `v Is o` with v a Variant goes through `__vbaUnkVar(&v)`, which says nothing about v's declared type.
- `VBA.CStr(x)` written with the library prefix compiles to `rtBstrFromErrVar` (ordinal 634).
  Plain `CStr(x)` compiles to `__vbaStrVarVal`/`__vbaStrVarCopy`.
- A private class Function returning a Variant is called through Me's vtable with a
  hidden result pointer as the last argument.  The caller may address that buffer
  through ebp (`lea edx,[ebp-28h]`) instead of esp.
- Passing an object whose default member needs an argument (a Dictionary) as a key
  to code that Let-assigns it raises error 449 in the *original* program.  Do not put
  that in a test.
- `IsEmpty(v)` & co. (`rtcIsEmpty`, `rtcIsNull`, `rtcIsObject`, ...) take a
  `VARIANT*`; a frame address argument is the variable behind it.  Left as the
  raw address, the passes see no reader of the variable and drop the store that
  feeds it (`Exists = Not IsEmpty(dict_GetKeyValue(Key))` lost its call value).
- A parameter's raw dword handed to a method's ByRef Variant parameter
  (`mov eax,[ebp+0Ch]; push eax; call [ecx+38h]`) proves the parameter is
  itself `ByRef ... As Variant`: for any other type VB builds a VT_BYREF
  wrapper Variant.  Call-site literals (`TryAdd(d, "a", 9)`) said String/Long.
- `CLng(7)` passed as a Variant is a VT_I4 literal; written back as a bare `7`
  VB makes it VT_I2 (`TypeName` Integer instead of Long).  It must be `7&`.
- `Set FunctionName = obj` in a Variant Function is `__vbaVarSetObjAddref(&result,
  obj)`; the zeroed result slot is the destination, not an Empty literal.

## VB evaluation order with late-bound calls (tests/t08late)

Measured with a logging class reached through Variants (`O(tag)` returns one,
`.Cnt` logs when it runs):

| source | order |
|---|---|
| `"x" & O(1).Cnt & " " & F(2)` | O1 F2 Cnt1 |
| `F(1) & O(2).Cnt & F(3)` | F1 O2 F3 Cnt2 |
| `O(1).Cnt & O(2).Cnt & F(3)` | O1 O2 F3 Cnt1 Cnt2 |
| `O(1).Me2.Cnt & F(2)` | O1 F2 Me2 Cnt |
| `O(1).Cnt + N(2)`, `If O(1).Cnt = N(2)` | O1 N2 Cnt1 |
| `G O(1).Cnt, F(2)` (a Sub's arguments) | O1 Cnt1 F2 G |
| `CStr(O(1).Cnt) & F(2)` | O1 Cnt1 F2 |

So in an operator expression VB runs the early-bound calls (and the receivers
and arguments of late-bound ones) left to right first, and the late-bound member
calls after them, innermost/leftmost first.  The arguments of an early-bound
call (a Sub, `CStr`) are each evaluated completely, late calls included.  The
inliner (`simplify.vb_order`) uses this order instead of the text order when a
statement holds a late call; gh_dict's `d("obj").Count & " " & TypeName(d("obj"))`
runs Item, Item, TypeName, Count and only inlines back with this rule.
- Pure runtime calls (`TypeName`, `CStr`) are placed where their value is used,
  not where they ran, so their position is no evidence of order.
- A temporary that only serves as the receiver of late calls must hold an
  object; when it can't be inlined it is assigned with `Set` (a Let calls the
  object's default member: error 450 for a Collection).

## Hash seeds and output (2026-11-02)

Python's hash seed used to change the output (up to 39 diff lines on a large real program):
udt.group_structs, resolve and adopt_passed_types walked sets of variable keys.
They are sorted now.  group_structs sorts in reverse only because that order
reproduces the earlier output; neither order is known to be the right one.

## A two-dimensional fixed array's stride is no element size

a(0 To 99, 0 To 9) As String is pvData + (i + j * 100) * 4.  The imul by 100
is the first dimension's count, so element_address must not take the largest
multiplier as the element size when the dimensions are known.  A constant 0
second subscript of a dynamic array compiles to 0 - lLbound, with no count term.

## A small integer in a frame slot is not always a Variant tag

Slots holding 2..17 look like a Variant's type word.  x = 10 is VT_ERROR, v = 4 is
VT_R4.  Require the rest of the evidence: VT_ERROR has payload 80020004h, and a
whole Variant stored by __vbaVarMove says nothing about the slot's tag.  These bugs
compile, so only running the program (or reading the output) shows them.

## Fixed strings and API calls

Dim s As String * n in a procedure may be __vbaFixstrConstruct(n, &slot): the slot holds
the address, and __vbaLsetFixstr(0, [slot], v) writes to it.  n = 0 also means LSet on a
variable-length String.  Given to an API, VB converts to ANSI, calls, converts back with
__vbaStrToUnicode and writes back with __vbaLsetFixstr; none of that is in the source.

## Random file records have no alignment gaps (2026-11-02)

Put and Get on a Random file write a UDT field by field: memory alignment gaps are not
on disk, and a String * n takes n bytes there (2n in memory).  In a real program the
Open ... Len values matched the record types' sizes exactly when the gaps are not
declared as fields.  A declared pad field counts as data: a record longer than Len gives
run-time error 59.

## A single store does not mean a single value (2026-11-02)

A local is zero until stored to.  `found = True` inside a loop has one store, and the
read after the loop can be reached around it, so it may not be folded to the constant.

## With on a local object (2026-11-02)

VB stores the variable's address in a frame slot (lea, mov [slot]) and reads through it.

## ReDim arguments and Owner descriptors (2026-11-02)

__vbaRedim pushes bounds last dimension first: (flags, cb, &a, vt, nDims, ubN, lbN, ... ub1, lb1).
__vbaPutOwner4 / GetOwner4 of a record without Strings takes a table starting u32 memory
size, u32 count; it is not the Record descriptor (u16 at +2 is 0), and it can read as a
string literal.  Its size minus Open's Len is the alignment gaps.

## Jump tables, unsigned guards, VT_BYREF tags (2026-11-02)

- Two-table Select Case comes in two forms: optimised `movzx eax, byte ptr [eax +
  bytes]`, unoptimised `xor eax, eax; mov al, byte ptr [ecx + bytes]`.  An unrecognised
  `jmp [reg*4 + table]` lifts as an exit: code vanishes without any compile error.
- The guard `cmp index, n; ja default` is unsigned: index = x - low is negative below
  the lowest case and takes the default.  VB compares signed: index < 0 Or index > n.
- `Error$(Err)` passes a frame Variant (tag 9, payload rtcErrObj's result); `Error$`
  passes a missing one.
- A Variant built around a variable (tag 0x4000 | vt, pointer at +8) can be used in a
  later block, since VB's auto-instancing test of Printer lies in between, or in a merged
  call.  Read as a value, it's the tag: a number like 16386 (I2), 16388 (R4), 16392
  (BSTR) in the source is this bug.

## Bugs that compile, found by running a real program's rebuild (2026-11-03)

`tools/menucrawl.py` opens every menu item of two builds and compares the windows, the
error log and the data; `tools/unassigned.py` lists locals read but never assigned.  Both
find what compiling cannot: all of these compile.

- **`push ecx` in a frameless procedure reserves a slot.**  A procedure with no error
  handler, strings or objects has no ebp frame; its first temporary is `push ecx`, the
  short form of `sub esp, 4`.  `prologue_esp` took it for an argument, so frame stores were
  "pushed arguments" and vanished: `Proc_401000 a2, v0` for `Proc_401000 a2, 5`, reading
  into the wrong record.  Frame offsets above that can be positive (`[esp+0x12]`): names
  like `v-2` did not compile, now `vp2`.
- **A handler's reads are live everywhere.**  The handler is entered from any statement
  and `Resume` returns to any statement; neither is a graph edge.  Liveness dropped
  `n = n + 1` before `Resume` (a locked file was retried for ever) and `v = -1` read only by
  a handler.  `handler_live`: the locals and arguments live into a handler are live
  at every block.
- **`v = record.f: Proc v` is a copy, and it matters.**  `inline_temps` made it
  `Proc record.f`, a pointer to the field; if the callee reads over the record, its
  parameter changes under it (a recursive procedure reading the next record into the
  array element the field belonged to).  The copy stays when the argument goes by address
  to a project procedure.
- **`__vbaVargVar` takes the parameter in ecx**; the pushed value is only the temporary.
  `obj.Line (x, y)-...` read Empty for x and y.
- **`mov [v+8], x` after the tag, computed from a slot stored before it.**  Pairing tag and
  value moved the value up to the tag, ahead of the store it reads (`t = a(k)`, tag,
  `v+8 = t / 6`).  The merged assignment now takes the value's place.
- **A ByRef parameter's pointer put in a VT_BYREF Variant does not make its register that
  Variant**: `now_in` turned `a1 = Mid(a1, k)` into an assignment to the Variant's
  payload, so a1 never changed.
- **Slot reused for a number and typed Object**: `v = 10: g(v + 1)` compiled (late bound)
  and failed with error 91.  `arrbase.number_objects`: Long if it is never used as an object.
- **Single bits**: hex literals for procedure arguments are NOT reliably Singles (gh_zip's
  table of machine code, Doom's angles).  Only graphics coordinates, For bounds, CLng(&H...)
  and parameters declared As Single are converted.
- **Forms**: stored Scale mode 0 with (0,0,1,1) means twips, no Scale properties.
  **CreateObject**'s second argument 0 is a null BSTR.  **Show** with the missing-parameter
  error value 0x80020004 is the omitted Modal.  **Select Case on a Double** compares its two
  dwords.  **Mixed control classes in one slot** want Object.

More from the same comparison, all compiling, all wrong (test t77pushecx has each):

- **A store a handler reads must survive a later store.**  `v = 1: Print ...: v = -1: Error
  5: v = 2` with `H: If v = -1` kept only `v = 2`.  Three places killed it: the liveness walk
  back over `v = 2` inside the block, the block's kill set, and `rename_block_temps`, which
  gave each store a fresh name.  Handler-live variables are now never killed.
- **`__vbaError` is `Error n`.**  It had no entry, so it printed a bare `Error`: a syntax
  error that the error census had never met.
- **`__vbaVargVar` returns a pointer.**  `o.Kaksi a` copies 16 bytes through that pointer
  with 4 x `movsd`.  Treated as a value, the arguments printed as `(a2 + 8)` and made the
  parameter a Long.  The copy's `esi + 4` must also stay a displacement, not an expression.
- **A ByRef Variant copied dword by dword is no record.**  `.CurrentX = x` through
  `__vbaVarVargNofree` reads the parameter at +0 and +12, which `struct_params` took for a
  16 byte record.  The size passed up to every caller, and a Single passed by a VT_BYREF
  Variant, plus the Variant variable next to it, became `v88.f0` and `v88.f4`, a Long.  So
  ScaleHeight was truncated.  `VB6DEC_HINTDBG=<va>` names the rule behind a hint.
- **A Single For loop's limit is only moved.**  `__vbaR4ForNextCheck(counter, end, step)` is
  its only use, so `For x = 6 To 30 Step 6` kept 30 as `v210 As Long = &H41F00000`, which
  loops a billion times.  The check now marks all three as Single.

Error 424 in one window, seen under Wine and confirmed on Windows:

- **A later web of a register lost its object type.**  `split_register_webs` gives each web a
  new name, and `lf.objtype` had nothing for the new name.  In the Form_Resize of a form
  opened by another form, `t = Me.Label1` printed without Set, so the variable took the
  caption and `t.Top` raised 424.  The web now takes the type of what it holds.  The
  same shape was in four more event procedures (Form_Resize, Form_Unload and Click
  handlers).  How it was found: every `Form_Resize` given a handler and the lines numbered.
  The first guess, the form the menu item opens, was wrong; the failing form was another.
- **A member `As New` went through a register holding its address.**  `lea esi, [ecx + 34h]`
  in one block, `__vbaNew2(desc, esi)` in another: the New was stored in the register and the
  member stayed empty, so each use made a new object (a form's FileSystemObject member,
  and t78's Collection lost its items).  `alias_addr_temps` now maps a register that only ever
  holds one member's address, as it did for frame slots.
- **`If m34 = 0` on an object is `Is Nothing`.**  `arrbase.object_tests` rewrites the test and
  adds Set to `t_x = m34`, from the declarations.  The test as printed reads a default
  member, which fails on Nothing.

VT_BYREF tags printing as values, and other leftovers:

- **The literals 16392, 16393, 16386 were still three different bugs.**  `Proc_401000 16392` was
  a String array element (the payload is `pvData + i * 4`: `Lifter.by_ref` and
  `merged_byref_arg` now take an element's address).  `Proc_402000 ..., 16393` was an Object
  parameter kept in a register, so `a5.CurrentY` would fail with 424 when printing
  (`fold_variant_reaching` follows the register or saved slot back to the parameter).
  `Proc_403000(a1, 3, 16386, v24)` was `3, 3, v24`: `fold_variant_temps` took a plain 3 for a
  type word and the next Variant's tag for its value.
- **A Variant passed by value as the first statement was counted as frame.**  In a procedure
  without SEH `prologue_esp` added the argument's `__vbaChkstk` or `sub esp` to the frame.  One
  dword was lost and printed as `'<arg9>`, which comments out the rest of the line, so
  `a1.Kaksi '<arg9>, 2` compiled and failed when it ran.
- **Someone at the keyboard changes the crawl.**  Stray letters in a text box and MouseMove
  errors made 5 items differ, and all 5 matched when repeated.  `menucrawl` now tries a
  difference twice, says so in the report, and lists the original's error log too.
- **A fixed 2 dimensional array of a form lost a dimension.**  `m(i + j * 100)` of
  `m(99)` raised a subscript error for each pixel of an area in a drawing: thousands of
  logged errors, none in the original, and the windows looked the same.  Clicks find an
  item through that table.  One bounds check per subscript gives both bounds
  (`Lifter.checked_two_dims`): `m(99, 49)`.
- **`Set x.Picture = Nothing` goes through a temporary.**  `Set t = 0` looked like the prologue's
  zeroing, which left `x.Picture = t`, of an empty t: error 91 (`simplify.set_nothing`).
- **A record's first field used only through the element was padding.**  `m3C(i).pad1(1) = n`
  wrote one byte of a record number.  A leading gap is now `f0` (`udt`).
- **Names and DISPIDs.**  A late call's name can read as a String literal, a length in front
  of it by chance: `v.__late` is `v.Files`.  `Printer.__lateid` is `Printer.EndDoc`: the object
  is known only after folding, so the DISPID is kept for the renderer.  A DISPID pushed before a
  control's getter arrives as a stack variable (`Me.List1.ZOrder`).
- **`byrefwrap` now copies when the callee hands the parameter on too**, with a comment on the
  line: a possibly lost store rather than a whole procedure emptied.
- **`VarForInit` / `VarForNext`** that the structuring can't rebuild are written as procedures of
  the module.  Only compiled so far, not run.

## 2026-11-03: what a button crawl and the error census of a large real program found

- **An error handler lost whole (`simplify.thread_empty`).**  Cancel in a file dialog: the original shows "Object was
  unloaded, 364", ours opened the form.  The handler of a `Form_Load` starts with the auto-instancing test of a form;
  after the test is dropped that block is empty, `thread_empty` forwards its predecessors (none, nothing has an edge to a
  handler) and deletes it, so the code behind it is unreachable.  render then prints the bare label (its branch for a
  handler inside the last block).  Handler entries now stay.  `tools/emptyhandlers.py DIR` lists On Error targets with
  nothing after the label: 2 in a large real program before the fix, 0 after.  Test: t78 Putoa.
- **`lift.method_result` read stale registers.**  It follows mov only, so `mov edx, [ebp-100h]` (an array offset) ...
  `movsx edx, [ecx+10h]; mov eax, [ebp+14h]; mov [eax], edx` named slot -100h the Function's result.  Two silently wrong
  signatures in a real program: a Sub with a ByRef last parameter, printed a Function, array offsets assigned to its
  name, recursive calls with 3 arguments to 2 parameters; and `x = Proc_401000(a)`, really `Proc_401000 a, x`, a path
  argument lost.  Three rules now: anything writing a register forgets it; the copy has to be in the epilogue (a ret
  within 16 instructions, no call or jump); nothing reads through the pointer or pushes it before the copy (the zeroing
  at entry only writes).  Test: t77 cK.Syvenna (without a frame its `r = m` is the last statement).
- **Arrays inside a local record**: `[ebp + i*4 - N]`; a local array has a SAFEARRAY, so a directly indexed frame slot is
  a record field.  `lift.frame_record_elem`, the record from `self.structs` (known here from Put / Get).
  `(v0 + i * 4).f-100 = 0` is `v100.f0(i) = 0`.  Test: t77 Kaks.
- **An omitted argument stored in a slot** (`a_2 = ` with nothing after it): tag 10 and DISP_E_PARAMNOTFOUND written in
  a block before the calls.  VB has no literal for it; arrbase.missing_values writes `a_2 = MissingArg()`, a private
  `Function MissingArg(Optional v As Variant) As Variant`.  No test of its own yet: only compiled in a real program.
- **Cross-jumped element store**: the compiler merges `a(i) = x` of two arms at the store, each arm leaving the offset in
  eax.  `simplify.offset_still_good`: the same value if each computation ends its block right before the only block
  using it and nothing it reads changes on the way.
- **`udt.caller_records`**: a parameter with a synthetic Type when every caller passes one program record takes that
  record (a parameter that touches no field had a synthetic `T_` Type, its callers a program record).
- **Waiting (census)**: the element address of a fixed record array with a variable index, passed ByRef (printed as
  `g + i * N`), and a member array's base kept in a temporary across blocks.
- **`simplify.array_bases_in_registers`**: optimised code keeps a SAFEARRAY pointer in a register across blocks
  (`t_ecx = a1 ... (a1.fC + (x - t_ecx.f14) * 4)`); where all reaching definitions hold one array (through register
  copies) the read is of that array and the element folds.  In a real program
  `If t1(t1.f18 * (1 - t1.f14) + (i - t1.f1C)).f0 <> x` was wrong code that compiled; it is `g(i, 1)`.
- **globals_by_ref** did not visit what it had just swapped in: `s(i, f(s(j).f0))` kept the inner `s(j)` an address
  sum.
- **Record fields from a VT_BYREF Variant's address (a silent layout error).**  `obj.TextWidth(a(i, j).f6)` of an
  Integer field gave an access of 4 bytes at +6 (the address path has no width), so the record's Type got `f6 As Long`.
  In memory a Long aligns to 8, so all later fields sat 2 bytes off and names collided: `.f4` was printed for +4 and +6,
  code that moved a row copied the wrong fields; compiled and ran, printed page layouts were wrong.
  `udt.record_bodies` now drops a 4 at an offset no Long can have, or with another field in its upper half.
  Test: t78 Rivit.
- **f4(k) for f8(k - 1)**: linear() merges the subscript's - 1 into the field offset; render.inline_element looks a
  stride or more above when nothing holds the raw offset.
- **t78 Rivit found four more, all in dynamic 2-D record arrays**: `*(&m34)` not reduced to m34 before the alias of the
  lea register is known (fold_static_derefs); an inner fixed array in a 2-D element (dyn_subscripts, split_inner_const);
  a record copy `r(i, j) = r(k, l)` printed `.f0 = .f0` (a silent lost copy; refold returns the element when the access
  is its whole size); and that copy's size at offset 0 swallowed the fields up to the next String (records.body: no field
  wider than 8).  A raw inner array offset on a descriptor's String moves up a stride (records.body, entries merged: a
  plain overwrite lost a bounds count and made a wrong array of records, caught by comparing two successive decompiles).
- Way of working that caught the regressions: after each change, diff a large program's decompile with the previous one
  with the `T_n_m` / `TRecN` numbers normalised; numbering shifts are otherwise most of the diff.
- **Cycle 40's suite: 4 failures from the two newest changes**, gh_json (`Proc(vC0.f8)`: `*(&v)` reduced everywhere;
  now only a member's SAFEARRAY fields) and gh_doom (`f8(0 To 0)`: an array moved off a descriptor field that was itself
  a record array; now only off String / Object).  Both tests' decompiles equal the previous cycle's again.
- **An array parameter known only by its lower bound** (`a1.f14`) came out As Long; `array_bases_in_registers` now
  registers it and folds offsets in a shared register by reaching definitions (`offset_at`);
  `arrbase.array_params_in_registers` drops the register copy (`t_ecx = a2`); `udt.caller_records` gives an array
  parameter its callers' element type (`a1() As String`).


## UserControls (2026-11-03, tests/t25usercontrol)

- Object type: a UserControl is 1DA003h, a class 118003h; bit 40000h tells them apart.  They are in the GUI table with the forms.
- A form's control list GUID for one is referenced by nothing else; the designer data's class name (`Project.UC1`) says which object it is.
- Its public members are called late, with the DISPID in the member descriptor (+0Ch): 6803xxxxh properties, 6003xxxxh methods.
- A form's event table holds the extender's events first; the control's own are the last slots (10 slots, 1 own: slot 9).
- [Me+10h] holds the UserControl object (`UserControl.ScaleWidth`).  In a control's code `Me.lbl` crashes VB6 at compile time: write `lbl`.
- Saved properties in the form's record (opcode 2Dh): B293h, size, then entries of vt, name length, hash, entry size (negative: the last), lower case name, value; fonts and pictures are vt 13: the class GUID, then the object's own stream.
- MSVBVM60.DLL has two type libraries: resource 1 VBA, 3 VBRUN (PropertyBag).
- A store with __vbaVargVarCopy / __vbaVargVarMove through a ByRef parameter makes it a Variant whatever the values; judged by values it came out As String and a number came back as text (tests_wip/t79varref).
- A local's own address passed to a procedure's ByRef parameter means the local and the parameter have that type, when the local is a Variant: VB gives anything else a converted copy (`types.variant_params`).  Same for a method's array parameter (`calls.note_array_args`), but only a variable's own address: a slot's value can be a pointer to another's array (t04w).
- A literal for a ByRef Long goes in a temporary with no other evidence: the API's parameter types it.
- Unoptimised code: element offset through reused frame slots, `v100 = i - a.f14`, `v104 = v100 * 4`, `[a.fC + v104]` (`simplify.frame_offsets`).  Other shapes of it are still printed raw.
- `x = a1` of a ByRef object parameter needs Set; without it, under On Error Resume Next, the procedure just did nothing (t80arrmethod).
- From the 47Hunter47/vb6decompile notes, not verified here: object type 1018083h is an embedded DataReport; `kind` would call it a Form.

## DarwinBots, compiled here from its source (2026-11-03)

The first target with source for its forms and its own compile: `tools/frmcmp.py SOURCE DECOMPILED` compares the designer data property by property.  Found so:

- Geometry that doesn't fit in words: 8000h, then four dwords (Left -74640 on a hidden SSTab page).  Read as words it lost the record.
- `Tag`, and the Data control's Connect, DatabaseName and RecordSource (agent, not run here): UTF-16, count of characters, no NUL.  A one-digit Tag worked by accident.
- Line X1, Y1, X2, Y2: dwords of twips, written in the container's units.  Read as floats they were 6.3e-44.  **A large real program too** (several forms have Lines with such values): lines at the origin in any window that doesn't set them at run time; no crawl sees that, a Line has no window.
- User scale, form and PictureBox: stored left, top, twips per unit x, y.  ScaleWidth is the inside width divided by it (Contest_Form 4935 / 101.6 = 48.573).  PictureBox border: 60 twips in 3D, 30 flat, 0 without a border (console, frmAbout1 agree).
- OCX control array: an extra opcode 1 with the index, and the Index parameter on its events; its events IID has no relation to the class's.
- MDIForm has no scale: "The property name ScaleHeight in MDIForm1 is invalid".
- A .frm line can't hold a line break: `$"x.frx":offset`, dword length, bytes.

From the runtime (`msvbvm60.dbg`, agent's report; `vb6dec/ctlprops.json`, `tools/proptab.py`): `*_rgbpprop` tables give every class's designer opcodes, names and storage.  Object kind: GUI entry +28h low nibble (0 Form, 1 MDIForm, 2 MDI child, 3 UserControl, 4 PropertyPage, 5 UserDocument, 6/7 Designer), bit 10h the startup form.  Object type bit 40h: Option Compare Text, with __vbaStrTextCmp and friends.  __vbaLateMemStAd and __vbaVarLateMemStAd: cdecl.

`With a(i)` (unoptimised): the element's address in a frame slot, read again in every block.  Lifted now as slot = i and a(slot) in the block (`lift.with_slots`): exact even if i changes inside.  Blocks are lifted out of order, so a read goes by address to the latest With at or before it.

**A Function returning a record of up to 8 bytes** comes back in eax or edx:eax, loaded from the result's frame slot (t84recret); 12 bytes and more go through the hidden pointer, by `rep movsd` or short single `movsd`s, also without an exception frame (TIso was an empty Sub).  Before this VectorAdd(...) As vector lost y, silently.  Currency uses the same registers: `lift.small_record_return` looks at what fills the slot (a field narrower than a dword, an FPU dword store, the slot's address taken, two separate sources: a record; the two halves of one 8-byte value, or from a __vbaCy function: a Currency).  Two consecutive ByVal arguments look like a record, so `Function F(ByVal c As Currency) As Currency` would be a compile error, not silent.  Frameless Functions (fast-code build, tests_wip/t85recretno) aren't covered yet.

Fields read or written with the FPU's dword forms are Single (`lift.load` fp4, `udt.fp_fields`); record fields were all Long, the bits read as numbers.  A field copied from a known Type, or given a small record's value, is of that Type.

Record descriptors, complete (2026-11-03; `records.read`, tests/t86recarr).  It used to stop at the first fixed array of records: DarwinBots `rob` lost 62 fields after it, DNA() and the Strings among them.  An entry is u16 offset, u16 kind, and with bit 2000h a u16 size and the u16 offset in the record's ANSI copy.  Then by kind:
- fixed array (base 5, bit 80h): the element's kind (u16, its upper word and the next dword can hold rubbish).  2069h: a record, u32 kind, u32 descriptor, 16 bytes.  2009h: a record of numbers, no descriptor.  Then the compiler's SAFEARRAY: cDims, fFeatures, cbElements, 0, 0, then (cElements, lLbound) per dimension, last dimension first; with fFeatures bit 80h a VARTYPE word pair after it.  So bounds and element types come straight from the EXE.
- dynamic array (base 5, no 80h): the element's kind: a record 24 bytes as above; with bit 2000h on both, the word alone (2 bytes); else 6 bytes.
- embedded record (base 9, or 2069h): u32 descriptor, 16 bytes; descriptor 0 for one of numbers.
- Fields before the first one needing conversion aren't listed: a record's leading numeric arrays are known only from the code (below).

Field types beyond the bytes: FPU dword forms make a Single; Print's item codes give Integer/Boolean, Long/Single, Double/Currency/Date, Byte (`udt.fp_fields`).  Synthetic Types differ by which fields are Single, or a Long record shares a Type with a TVec.  An alias to a real record now needs string evidence at its String offsets: VBDoom's five-Long records got hu_textline_t, fC As String, and failed at run time with error 9.
A parameter given a record that starts with a record, reaching only that first one, is the inner Type (`udt.narrow_first_record`).

Still open: LBound/UBound of a record's fixed array with no descriptor entry (VB builds a SAFEARRAY on the frame from a template, __vbaAryConstruct2: the bounds are there, unused), Date elements in that leading part read by constant subscripts only, frameless small-record Functions (t85recretno).

## Module tables, statics, stubs: from DarwinBots' compile errors (2026-11-04)

Worked from the census against the source's Types (`tools/typecmp.py SRC DEC Src=Dec...` compares two layouts field by field, `tools/errline.py` finds the statement of a run time error, `tools/modtable.py` dumps a module's variable table).  Found, each with its test (tests/t87nested unless named):

**A module's variable table** (ObjectInfo public_bytes / static_bytes) is a record descriptor: u16 length of the table, u16 size of the data, u16 0, u16 count, u32, then the entries as in a record (`records.table_entries`).  It lists every String, Variant, object, array **and record** of the module, 69h with the record's descriptor, 09h a record only of numbers.  So a module variable's record is known without the code ever showing one whole (`Model.module_records`): `Public K As TKaikki`, only ever reached field by field, came out as a dozen variables.  The first word as the length also stopped `global_array_slots` scanning on into other tables: a class's Static array there made K.kierros a module array.  Between the entries lie bytes of the compiler's stack, so a descriptor dword is good only for kinds 69h/29h.

**Static locals.**  A class's or form's block has its pointer in a member; a module's in a module variable just after its public data (offsets within the static table's size).  `decompile.static_slots` finds both now; each Static comes out as a variable of the module.  A Static fixed array has its SAFEARRAY in the block, and the block's descriptor another shape (u16 at +4 is 1): `records.read` said None, so Form1's slot was never found.

**call stub; jmp [iat]**: code optimised for size has no `call [iat]`.  `is_noreturn_call` looked only at those, so a procedure ending in __vbaFPException or __vbaErrorOverflow's call ran on into the next procedure, took its `ret 4` and its code: Subs came out as Functions with a parameter (DNAaddstore, renormalize_mutations: mod32000's).  (`__vbaError` is no longer "no return": Resume Next goes on after it, and the code is always there.)

**A form's other IID.**  A variable of a form's own type calls with the HRESULT check naming the GUID right before the CLSID in the data (`{616A...}`, CLSID - 16), not the one in the default IID table; the same for a UserControl.  Also, the check may fetch the object again first: __vbaDerefAry1 or a bounds check between the call and __vbaHresultCheckObj.

**__vbaDerefAry** (cdecl): dims, psa, subscripts last first; Buckets(x, y).  __vbaWriteFile as __vbaPrintFile: `Write #`.  __vbaInputFile's codes are 80h + the item's VARTYPE (the type of a field only read by Input).  Late DISPID 8001000Dh is the extender's Enabled.  A Variant around an array is VT_ARRAY|VT_BYREF, the payload pointing at a slot holding the SAFEARRAY's address.

**Records inside records**: `f18(0 To 77) As TRec2` had padding in TRec2 for every number.  What the code does at a raw offset inside a fixed array of records is about the element's Type (`udt.element_accesses`).  A stride smaller than the element: an array inside it, and the index is both subscripts added up, i * 182 + j (`render.nested_element`; one variable in both: i * 100 is 96 + 4).  A whole record copied (LSet) makes the field of that Type (`udt.lset_fields`); a field passed to struct parameters links those parameters' Types (`group_structs`, node "field").

**An address is no access**: `lea` for a ByRef argument reads as a 4-byte Deref under AddrOf.  It made rob(i).Ties(k).pnt, at +2, part of a Long at 0: Port and pnt one field, silently; and arrays "didn't fit" their stride.

**Field retyped as a record**: the fields it covers go (`udt.retype_field`).  Leaving them moved everything after by 4 bytes in `robot`, 8 in `shot`: at run time every later field wrong, for all that it compiled.

**Unoptimised code keeps temporaries in one pool of frame dwords**, an index crossing the blocks of its bounds check: Set v50 = optionsform.CurrSpec.  `simplify.mixed_frame_slots`: a slot of the compiler's that holds objects and values gets a variable per web.  With (p), p a ByRef parameter or a frame record: `lift.with_param`.  With on a module record's array: the slot takes the index, as for a(i).

**cfg.new_guard**: `cmp [G], 0; jne T` with __vbaNew2 in the other arm, for a form's variable or the Global object, is no If in the source: the arm goes at once (it left `If  = 0 Then`, or arms alike but for a label).

LBound/UBound of a record's fixed array: the frame SAFEARRAY's template has the bounds, which are constants: printed as such, as the array can't always be told from its first element's first field at the same address (`simplify.alias_safearrays` .view).  They're also evidence for the array's count.

A Variant Function returns by rep movsd too, unoptimised: 4 dwords with the slot's address taken besides the copy's own lea esi is no record.

## From DarwinBots' last compile errors to a build (2026-11-04)

DarwinBots' decompiled source builds: 1158 procedures, no failures left (census 240 -> 0 over two days).  What the last 22 needed:

- **Member type code 1Dh is another library's object** (`ctl As Control`, `Public WithEvents f As Form`): like 13h (a project class) a reference follows the code inline, 4-aligned, and it points at `{?, &IID, ...}`; a public variable has it at +1Ch of its entry.  `Registry.find_guid(IID)` names it.  Read as unknown, the next codes were the reference's bytes: `(ctl As Variant, ByVal parent As Variant, ByVal superparent As Variant, masterparent() As Variant)`, 16-byte Variants pop 4 dwords, the stack height went off for the rest of the procedure, and arguments came from the frame: `m70.SetRectToCtrl m64, Me.SSTab1, '<arg8>, '<arg10>`, and in another procedure `__r8('<arg0>, o1355)` thousands of instructions on.  `tools/espdump.py` prints the height per block and finds the call that pops too much.  `projiface.tdesc`: a name it doesn't know is an object (VT_DISPATCH), not a Variant.
- **`__vbaRecUniToAnsi(desc, &copy, &udt)` names a member's type too.**  `Private theTray As NOTIFYICONDATA`, reached through With (`mov [ebp-3Ch], Me+3Ch`, then `[slot+14h]`) came out as loose `m3C, m40, ...` and `(m3C + 24) = s`.  Its fields are plain member slots in the code, or `*(&m3C + k)`: `udt.record_bodies` counts both as accesses of the record; the writer's member_structs prints them `m3C.f14`.  The record must go to the API whole, or VB makes no ANSI copy: `As Any` marshals exactly like the Type would (checked: a test with `As Any` imports `__vbaRecUniToAnsi`), so the Declare says As Any (t88memrec: lstrlenA of the record is 9, 1 if Unicode).
- **Dead stores in the first pass hide fields.**  `mixrgb.g = c1.g * ...`: its result is no record yet in the first simplify, the store is dead with what it reads, and c1 seems to have one field; its callers' `c As R_G_B` stay loose slots and `mixrgb(hsltorgb, CLng(127.5), ...)` passes the first's value.  `struct_params` reads the raw lift too.
- **A 16-byte element with a field at 4 or 0Ch is no Variant** (a Variant has its tag at 0, its value at 8): ScriptList(i) of four Strings.
- **`Input #f, rec` is a Type mismatch**: the address of a record is its first field.  Print's and Write's descriptor codes are the items' types, like Input's: `Out(s As String)` that only prints s came out `a1 As Long`, which compiles and fails at run time on every caller's String.  `Open path For ...` says String too.
- **An array's size read from one element's width is weak** even after it travels through `proc_arrays` (`size_weak` was dropped: `dna() As block` read `.tipo` first and stayed an Integer array).  A Public member's descriptor says its array parameters' types; a group of arrays passed to one another takes a scalar type too (`rob(n).ga`, `DrawGrid(ga() As Boolean)`, Private `DrawStat`); `ReDim rob(n).ga(k)` names the array field's element type by its VARTYPE.
- **Nested `__vbaAryLock`**: rob(n).dna(k) locks rob, then the element's array in terms of the first temporary; the substitution has to compose or `Dim v48` is left over.
- **A parameter given a computed number is no record**, whatever else it is given: `LoadOrganism s, randomV.x, randomV.y` passes the address of a record's first field, which grouped X with the record; `LoadOrganism path, tele.x + w / 2, y` says Single.
- **The HRESULT slot**: vtable Let/Set leave the HRESULT in eax, not the value before.  A test whose failing arm only goes in the last passes keeps its slot alive and with it `vD4 = datirob.infoupdate(...)` of a Sub, "Expected Function or variable".  simplify.run threads the empty blocks and runs liveness once more at the end.
- **A pooled temporary keeps no type through a Set of something else** (`App.Major`, then `Set t = late call`, `Set lastSim = t`: Dim lastSim As App).
- **A Function's value of a record type** wins over a guess from fields read, and passes through LSet to the variable it is copied to.
- **A control's String property can't always be on a line**: `TextRTF` from a RichTextBox's bag has line breaks: VB says "datirob.frm could not be loaded" and no compile error at all (the census's "no compile error to act on").  `ocxprops.spill_strings` puts them in the .frx as VB does: `$"x.frx":offset`, a dword length and the bytes.  `tools/builderr.py` prints the line VB means (its numbers aren't the file's), or the log's last line.
- errcensus's text for a statement can be wrong by lines (Input of a record was shown as the Boolean after it): don't believe the line, read around it.

## DarwinBots: behaviour, against its own original (2026-11-04)

With the source available the build can be tested against the original numerically: a helper script starts a build unattended (it writes the settings itself, `restartmode.gset` 7 starts an evolution round with no clicks, and a save file can be given on the command line), the program saves `saves\lastautosave.sim` every 2000 cycles.  Two runs of the original from one save agree in all of 3 376 877 bytes but one, a run time in seconds.  **The decompiled build now writes the same file, the same one byte apart**: 2000 cycles of robots (DNA executed, physics, ties, shots) and the whole of Get and Put.

Under Wine `Random(-32000, 32000)`, Variants of two Integers, overflows: Wine's VarSub wraps (`32000 - -32000` is the Integer -1536; TypeName and value checked with a small test) where Windows widens to a Long.  The original fails there too; for these runs the source has `-32000&, 32000&`, in a copy, and that build is what's decompiled.

How the differences were found, each in a few minutes: `tools/firsterr.py --lines PROC`; a `Tr "S" & Seek(n)` after every Get in copies of both trees (the first file position that differs is the Get with the wrong width); `tools/tracecmp.py` on both builds' procedure traces (the first call that differs, 6.5 million calls in); `Tr` lines printing `CDbl(x)` of chosen fields, compared with a diff (one bit of a Single).  `tools/iowidth.py` lists Get / Put whose byte count differs from the recovered field's width.

What it found (all compiled, all silently wrong):

- **`If c Then a(x) = 1 Else a(0) = 1` lost its test**: structure compares statements by `repr` to move common tails out of an If, and `Prop.__repr__` left the arguments out.  Printed `DecayOption(x - 1).Value = True` alone: error 341.
- **Fields of a Type that only strings are stored in**: `array_element_types` had no String evidence: `ScriptList(i).Action = ""` into a Long, Type mismatch at run time.
- **`__vbaStrCopy` reads a String**: a ByRef parameter read only that way was `a1 As Long`, and every caller's word failed.
- **Get and Put**: `__vbaGet3(n, &x, f)` gives x's width.  A field only ever reached by them was 2 bytes by the "address taken" rule (`mt As Single`); a field that is a record has its first field read (`Get #n, , .pos.x`, printed `.pos`: 8 bytes for 4), as deep as the bytes say; a whole fixed array in a record goes through a descriptor built in the frame (`Get #n, , .mem()`: `__vbaGetOwner3(desc, &sa, f)` with `sa.pvData` set just before; printed `.f2B38(0)`, 2 bytes for 2002, and with a With slot in between `Get #1, , k`).  The Call keeps the descriptor argument for the passes, and `whole_array` says what is printed.  A nested Type's first field takes the width too (`Mutables.Mutations`), and Types of one shape with a Long guessed by a gap merge into the narrower.  A local Put with a count of 1, 2 or 8 has that width (`Fe = 254` a Byte).
- **Singles**: `fstp dword` makes a local a Single whatever the expression's type says (renamed slots had no sizes left: `Impulse As Single` came out `o1002 As Double`); a Function's value is as wide as the `fld` before its `ret` (`SetAimFunc` As Double kept digits); a `fmul dword ptr [const]` constant is a Single literal (`200!`: `x * 200.0` is a Double in VB, `Round(.aim * 200)` then parts on a .5 sooner or later).
- **`fild`**: an integer converted into a temporary for a ByRef Single is `CSng(i)` (without it: a ByRef mismatch, which `byrefwrap` turned into `k As Variant`, whose arithmetic rounds otherwise: one bit of `ImpulseInd.x` in the first cycle); two of them in FPU arithmetic are `CSng(a) - CSng(b)`, not an Integer subtraction.
- `mov [ebp-3Ch], 0FFFF8300h` with tag VT_I2 is `-32000`, not the Long `&HFFFF8300`.

A second comparison starts from nothing: a source copy with the clock's seeds fixed (`Randomize 9`, `UserSeedNumber = 123456`), `restartmode.gset` 7, until the evolution round promotes a robot and calls Restarter.exe, which isn't there: both builds stop at the same "File not found", with the same Test.txt, Test.mrate, stage files and gset files, and logs the same but for their times.  19.3 million procedure calls traced the same before that.  What that added:

- **Word operands of a 32-bit product**: `CLng(rob(n).DnaLen) * CLng(k)` is `movsx; movsx; imul r32; jo`, and printed without `CLng` it's an Integer product: Overflow as soon as DNA is over 30 long.  `narrow` only knew types: a Deref read as a word or byte is one, and `Types.field_type` now follows an element of an array of records (`rob(i).DnaLen`), dynamic array fields being no scalar.
- **`switch_guard` on a copied tail**: "`cmp index, n; ja default`" before a jump table compares unsigned, so it prints `index < 0 Or index > n`.  It took any successor key at or above 1 << 40 for a table's test, but copied tails (2 << 40) are there too, and `fcomp; fnstsw; sahf; jbe` isn't an integer compare at all: `If Mx > oldMx` printed `If Mx < 0 Or Mx > oldMx`.  Silently wrong; found by reading, the run hadn't reached it.
- **`Print #1, ""`**: the empty string constant's address went through as a number and printed `4413576`.

Also new: GoSub.  `call __vbaGosub(&frame); test eax, eax; jne next; jmp L` is a statement and goes on to next; L is a root like an error handler (a negative key in `proc.handlers`), `__vbaGosubReturn` ends a node but is no failed check, and liveness gets the flow no edge shows (`node.flow_extra`: GoSub to L, Return to whatever follows a GoSub; `r = 33: Return` looked dead).  `On n GoSub` compiles to `__vbaOnGoCheck` and a test per target: it prints as a Select Case with a GoSub in each.  `On Error GoTo -1` is `__vbaOnError(-3)`.  `RSet` is `__vbaRsetFixstr`.  `rtcDateDiff(&res, interval, &d1, &d2, firstDOW, firstWOY)`, `rtcDatePart` the same with one date.  tests/t83obscure passes.

## Fast code: what VB6's optimiser does to the patterns (2026-11-04, DarwinBots built with OptimizationType=0, tests_wip/t85recretno, t89cyopt, t90optwith)

Fast code is VB6's default, so most programs in the wild look like this.  Procedures with On Error, or with objects and strings to clean up, keep a frame; the rest lose it, and the optimiser allocates registers across statements.

**Wine and Windows build the same code.**  `tools/execmp.py A B` compares two builds procedure by procedure, addresses aside.  The decompiled DarwinBots built with VB6 on Windows against the same source built under Wine: 1066 of 1069 procedures identical (533167 instructions).  The other three differ in one thing: an omitted optional String argument (`CreateObject`'s server name, among them) is `""` under Wine and NULL on Windows.  The Wine build is 20 KB larger: its forms store each control's Font, because Wine's default font size comes out 8 against Windows' 8.25.

**Small records return in edx:eax, and may never touch the frame.**  `VSet.x = x: VSet.y = y` is `mov edx, [esp+10h]; xor eax, eax; mov [esp], eax; mov [esp+4], eax; mov eax, [esp+0Ch]; add esp, 8; ret 8`: both halves straight from the parameters, the frame slot zeroed and never used.  `LLZero` is `xor edx, edx; mov eax, edx; ret`; `LLIf` keeps the pair in registers over a join.  Only the callers tell: edx does not survive a call, so a read of it right after the call means an 8-byte result.  `push edx; push eax` passes it by value, which only a Currency can be; storing it and `__vbaCopyBytes(8, ...)` only happens to records (`lift.pair_callers`).  A Currency Function: eax:edx from a `__vbaCy*` call, or from registers holding one (`mov edi, eax; mov ebx, edx`).

**The upper half may be stored first** (`mov [esp+0Ch], edx; mov [esp+8], eax`), and a Double is copied in two integer moves, a record's two Singles the same way: only the types tell them apart.

**With in a register.**  `With rob(n)` is `esi = pvData + (n - lb) * K`, and a second register for a field (`lea edi, [esi+28h]`, `.vel`), with `.Ties(k).x` as `[esi + k*68 + 72h]`; a frame slot where a register won't do.  A scaled subscript is kept too: `v0 = b * 2 + 11064` for `.mem(b)`, `t_eax = i * 91` read as `[t_eax*4 + address]`.  A slot may hold an element's address for the next argument (`mov [ebp-16Ch], eax`).

**A constant in a subscript joins the address.**  `.Ties(k - 1)` is raw offset 2Ch, 68 below the array and in the fields before it; `TmpOpts.Specie(n - 1)` lands in the tail of the record before it.  The array starts where most fields are seen (`records.inline_arrays`), and render's "below" path prints `k - 1`.  `(x - 1) * 8 + 4` and `x * 8 - 4` look alike: a whole number of elements below an array, or a field of the variable before, only the layout tells.

**One check for several accesses.**  Bounds checks are shared, so "the last check of this subscript" says nothing after the lift, and even during it: For k = 0 To 10 of `.Ties(k)` got the 15 of a later loop, too long to fit, and the whole record fell apart.  `lf.bounds_log` keeps each check's address; `udt.check_counts` takes the one right before a statement, and none when one subscript goes into arrays of two strides there.

**Other register habits.**  `mov ebx, [__vbaHresultCheckObj]` is loaded far from its `call ebx`, so a name by address order is wrong (`listing.vtable_iface_map` follows the flow now).  `mov edi, eax; test edi, edi; jge ok` with the object fetched again through its array's checks for the report: several blocks, no statement (`cfg.refetch_check`).  The form creation check is `mov ebx, [G]; mov word [x], -1; test ebx, ebx; jne T` with stores scheduled in between (`cfg.new_guard`).  An HRESULT sits in a frame slot used for other things (`simplify.hresult_stores`).  Me's own slot is used as a temporary once Me is in a register (`fstp [esp+3Ch]; fld [esp+3Ch]`: Me.Circle (Me, Me), Me).  `or ax, 0FFFFh` is a short `mov ax, -1`, and `cmp ax, 0FFFFh` compares with -1.

**A disassembly bug only fast code showed.**  The first sweep for "which registers cache imports" followed `jo` into the overflow stub and past it into the next procedures, and ran out of its 4000 instructions there.  So a `call ebx` further on still looked like the bounds error (noreturn): SaveSimulation ended there, losing its `ret 4` and its parameter.

**Member arrays.**  A fixed array in a form's Private Type is inline in the object.  `QStack.val(a) = QStack.val(a + 1)` is `mov ecx, [esi+ebp*8+78h]`, `mov [esi+ebx*8+78h], ecx`, the same at 7Ch, with ebp = a + 1: lifted as four arrays m78, m7C, m80, m84 that did not share their values.  `decompile.member_extents` collects the spans the checks show, `simplify.member_element_parts` and `merge_element_halves` put it together.  Not offsets from 10000h (slots made up for Static variables) and not arrays with a descriptor (a plain `Private a(100) As String`, whose member slot is followed by other variables).


**More fast code habits, found on the way to a build.**

- *A SAFEARRAY's check, compact.*  `test ecx, ecx; je E; cmp word ptr [ecx], 1; jne E`, where E is `call <register>` and a reload, nothing pushed.  The register may hold other things in other Cases, so its name can't be told from the addresses: ExecuteDNA's `rob(a1).ga(n)` printed an If on the array.  `cfg.sa_guard`.
- *A failed HRESULT's report fetches the object again* through its array's bounds checks, several blocks long: openconsole printed `If rob(i).console.textout("...") < 0 Then`.  `cfg.refetch_check`.
- *Register pairs.*  A Double or Currency handed around in two registers or two dwords, written upper half first: `simplify.fold_register_pairs` (a `__r8(a, b)` of two registers, an upper slot nobody wrote) and `merge_hi_stores` scanning forward as well.
- *A subscript that keeps its bias.*  `esi = i - lb`, read as `(esi)`; `unbias_index_regs` turns the register back into i when every definition has `- lb`.  Its counterpart `scaled_index_slots`: a slot holding `i * k + c`, only ever used as a term of a sum or as `[slot + .data address]`.
- *Copying a return value with no frame.*  `rep movs` into the caller's buffer through `[esp+arg]`: `lift.rep_movs_frameless`.
- *The status word.*  `FSW`/`FSWHigh` (machine.py) had no `children`, so passes walking an expression did not see the values a compare reads.
- *An `Input #1, v` item is written, not read.*  `v = 0` from the frame's zeroing and one `&v` looks like a temporary with one use, and came out as `Input #1, 0` (`Input #1, unused` in LoadGlobalSettings).  `var_index` treats Input items as address taken, like Get.
- *ReDim of an array in a With element.*  `ReDim Preserve .arr(.size + 5)` pushes esi, the element's own address, the array being first in the record.  Lifted as an array of esi's: its constant went into the address, `.fA(.size)`, a stride 2 at raw 0Ah, and that smaller stride swallowed the real inner array (`.adjBucket(z)`, 9 vectors) into `f8(0 To 3) As Integer`.  `fold_elem_pointers` now takes the ReDim's first argument as the array's descriptor address.
- *An element read, then read through.*  `mov ecx, [pvData + off]; mov edx, [ecx + 0Ch]` lifts to `Deref(Index(G, off / 4), 4, 0Ch)`, which `refold_dyn_index` took for a field 0Ch into the record: `(G(x, y).fC + (a - G(x, y).f14) * 2)` of what is `.arr(a)`.  An offset at or past the Index's unit can't be in the element: it is a pointer read.
- *Statements know their instruction.*  `s.va` was the block's start for most statements; with bounds checks folded a block runs on past its checks, so "the check just before this statement" found nothing (`f8(0 To 0)`).  Now the instruction's.  That showed the next trap: `.mem(t + 455) = .occurr(t)` has one subscript in two arrays, and the last check (1001) is `.mem`'s; `check_counts` gives no count when a subscript goes into two arrays.
- *A fixed local array's data pointer in a register, past a branch.*  `t_ecx = v64 ... LSet v38 = t_ecx + 16`: `alias_param_reg_copies` takes the register for the slot, `fold_static_derefs` makes the sum an element.
- *A slot holding a field's address.*  `v1B0 = &rob(n).DNA`, `UBound(*v1B0)`, `*(*v1B0 + 0Ch)`: read through, so a pointer, not a variable holding a VarPtr; `fold_elem_pointers` folds it as it does registers (Amplification now reads `LSet v24(i) = rob(a1).dna(j)` and `MakeSpace rob(a1).dna, ...`).
- *Float return with a plain `ret`.*  The disassembly looked for `fld` before a `ret N`.  A parameterless Function ends `fld [esp]; add esp, 8; ret`, and its callers may read st(0) only instructions after the call, so `float_callees` misses them as well: `calc_handycap` came out `Sub` with the FPU stack empty in its callers (`x = y + '<st0>`).
- *A Single's literal is a dword.*  Fast code stores and compares Single constants with integer instructions: `.b = 0.1` is `mov [esi+...], 3DCCCCCDh`, `If x = 1` is `cmp [x], 3F800000h`.  248 of them in the fast-code DarwinBots decompile printed `&H3DCCCCCD`, and VB takes that as the number 1036831949, whose Single is another value: silently wrong, nothing fails.  The unoptimised build has none, since it goes through the FPU.  New `floatlit.py`, on the written source because it needs the final Types: a literal stored in or compared with something declared Single is a float.  Module variables the FPU only touches as dwords are Single even when the evidence says Double (`LFOR = LFOR - x / y`: the quotient is a Double, `fstp dword` says what the variable is).
- *A field read with the FPU, once the address is folded.*  The lift marks `fld dword` and `fstp dword` targets (`fp4`) and `udt.fp_fields` makes those fields Single.  `fold_dyn_elements` and `refold_dyn_index` built new Derefs without the mark, so `rob.Ties(k).ang` and most of the record stayed Long with the bits in them.  The mark now goes through `Expr.replace`.  `tools/fp4trace.py EXE MAP VA` says which pass loses it.
- *A parameter's slot reused for the FPU's temporary.*  `movsx esi, word [esp+10h]`, later `fstp [esp+10h]`: `Uses(ByVal n As Integer)` came out `a1 As Single`, and callers passed `1.4E-45`.  A parameter also read as a word isn't Single.  The temporary's own variable (`Var("local", ("arg", n))`) was looked up in the Dim line under `("reg", ...)`: every `Dim t_arg2001` was an untyped Variant holding what `fstp dword` meant as a Single, keeping digits a Single doesn't have.
- *IIf of two locals.*  `IIf(a > b, b, a)` builds VT_BYREF Variants holding `lea` results.  `byref_target` had no case for a stack address, so it read `*SP`, which reads no variable, and the stores to a and b died: `IIf(-t, v18, v14)` of two zeroes.  **Both builds had it** (DNAceil in the unoptimised one too; the 2000-cycle comparison never ran that gene).
- *A record's array window below its start.*  `.ties(k - 1).ang` is raw 0Ch + k * 20 for an array at 1Ch.  `inline_arrays` took 0Ch for the start, merged it with the Booleans before it into one array of Integers, and every later field was `pad2(k * 20 + 1)`, which overflows at run time.  An unchecked offset within two strides below a checked one is the same array's; render looks for an array of exactly the stride a step or two on, before one the offset merely lands in.
- *Scalar windows reach as far as any of them.*  Several Boolean arrays side by side, each seen at its own offset and with - 1, are one Integer array as far as the code can tell, which is correct by value as long as it covers them all.  A check's count can be another array's (`.TieLenOverwrite(k - 1)` inherits `Ties(k)`'s 11), so an access wider than the stride now ends the array: `f3C8(10) = &H46FA0000` was `.Poisoncount = 32000` in an Integer.
- *Weak evidence from a literal.*  `x = &H41200000` said Long, and with `dword_read` and no FPU access `global_types` then threw away what Input # and CStr said.  A value that could be a Single someone wrote (six digits or fewer, more than one bit set) is `Long&` now: a Long only if nothing else says otherwise.
- *A Double tested against a constant.*  `If x = 0.01` compiles to two integer compares, joined by `merge_conditions` into `lo = L And hi = H`, and the upper half of a parameter printed as the next parameter.  `fold_double_eq`.  The same for `oldMx = Mx`, two moves from `[ebp+0Ch]`, `[ebp+10h]`: `merge_arg_halves`.  A module Double given a constant goes as two stores: merged when any procedure reads it with `fld qword` (`global_fp8`).
- *A dword load of a word.*  In .data fast code reads an Integer with `mov eax, [856280h]` and looks at ax: `cmp ax, 4Ch`.  Field and module variable widths took the widest access, so `SimOpts.SpeciesNum` became a Long and Put wrote 4 bytes for 2: the save file ran out of step at that field, which shows at run time only as a wrong value much later (Subscript out of range in `ReDim .spermDNA(n)`).  A word store now decides, whatever reads it, in records (`forced`) and module variables (`store_sizes`).
- *A dynamic array's descriptor is not its record.*  `UBound(rob(n).mem)` has `[rob + 14h]` in it, lLbound, which `collect` counted as a Long field at 14h of the element.  `NewMove As Boolean` became a Long the same way.
- *A register holding the Static block's pointer over a join.*  `mov ecx, [864AFCh]` ... `mov word ptr [ecx], ax`: the lift only sees through the register in the block it was loaded in, and a Deref at offset 0 of a register prints as the register, so `oldpop1 = x` printed `t_ecx = x` and the Static never changed; `Dim t_ecx` was an untyped Variant, and `t_ecx.f4` compiled.  `simplify.static_block_regs`.
- *A tag kept in a scratch register over a loop's head.*  `mov edx, 8008h` on both ways into `While Keys(k) <> key`, then `mov [ebp-9Ch], edx`.  `fixed_regs` knew such constants for ebx, esi and edi only, so at the head edx was unknown, the Variant wasn't recognised, and its tag came out as the value: `a2(v14) <> 32776`, a Type mismatch at run time.  It now follows eax, ecx and edx too, a call counting as a write.
- *A branch's flags at a spill.*  `mov edi, eax; ...; call EOF; test ax, ax; jne`.  At the block's end the registers are spilled, and flags reading a register's value are renamed to it, one register at a time: eax's turn made the test read `Var(reg,eax)`, the new eax, and edi, holding the old eax under that very name, took the test over.  `If Not EOF(1)` became a test of a loop counter, `If CheckRobot(x)` a test of whatever was in the register: one of the most damaging bugs here, silent, and only with a register copy before a call in one block, which fast code does all the time.  The renaming now looks at the flags as they were.
- *An argument moved past its register's redefinition.*  `add ebx, 44h; push ebx; ...; call` with the call shared: `unmerge_calls` copies the call back into each way in, at the end of the block, with the value it recorded: `ebx + 44h`.  But by the end ebx = ebx + 44h, and it read as ebx + 88h: `.stored` of the next record, Subscript out of range at the last one.  The argument is ebx itself then.
- *A Get/Put descriptor's String.*  Entries are u32 bytes, u16 kind, u32 count, u16 n, and `io_gaps` read a String's n as a u32 of its own, losing its place: no gaps known for a record with a String * n after its first run, so a pad alignment could explain was dropped.  `a As Integer: un As Integer: b As Long` with un never used then wrote 2 bytes less.  (t75seltab passed before by accident: a dword load made a a Long.)
- *An array in an array of records.*  `.ties(k).mem(j)`: the stride 2 window inside the stride 24 array made one array of Integers, with `.k As Single` among them.  With fields of other widths in the element the small array now goes inside its Type, with a lower bound when its window starts inside the field before (`mem(1 To 3)`, raw 2 below).  `.sp(i).mean(i)` is one stride of 28 where the descriptor says records of 24: that's 24 + 4.
- *A dynamic array of records no parameter names.*  `.dna(i).value` by Get alone: `f18() As Variant`.  The element's Type comes from the fields read (`T_4_o18`; a guess by its name, so a parameter's Type goes over it).
- *An 8-byte type from one dword.*  `VectorSet(Cos(x), Sin(x))`: Cos is a Double in the evidence, `fstp dword [esp]` pushes it as a Single, and `VectorSet(ByVal a1 As Double)` ate a2, the callee's read of it an undeclared Variant.  All of VectorSet's callers were wrong; vision first of all.  A wide type now needs its upper half in the next dword.
- *fstp rounds, and an inlined expression doesn't.*  `a = PopIntStack: b = Cos(a / 200) * 32000: PushIntStack b` printed `PushIntStack CLng(Cos(PopIntStack() / 200!) * 32000.0)`: every statement means the same, and a value differs in the last bits or by much more (a Long over 2^24 loses digits in the Single).  Frameless code's `fild; fstp dword` now keeps its `CSng`, and a computed value stored with fstp is no candidate for `inline_temps`: where the compiler had no reason to store, the source had a variable; where it had one (a call in the middle of an expression), the temporary stores at the same place.  **Measured: under Wine this FPU keeps 64 bits** (`((a + b) - a) / b` with a = 1, b = 2^-60 gives 1), where Windows starts a process at 53, so under Wine a Double store rounds as well.  `tools/fpcmp.py` compares the FPU instructions of two builds procedure by procedure.
- *Two Singles side by side, copied to two side by side.*  regang: `.ang = AngDiff(angnorm(a), angnorm(b))` puts the two results in [ebp-28h], [ebp-2Ch], copies them to [ebp-20h], [ebp-24h] and passes those by reference.  `merge_hi_stores` read the two copies as one 8-byte value in halves, and the slots' own stores died: `AngDiff(v20, ...)` with v20 unset, and `angnorm v1C` as a bare statement.  The rule now wants the upper slot to have no value of its own.
- *An SEH frame reading fs:[0] first.*  posto has a 13 KB frame, and for those fast code writes `mov eax, fs:[0]; push handler; push eax; mov eax, 3580h; ...; call __vbaChkstk`.  `seh_prologue` looked for `push` straight after `sub esp`, so the record's 13 KB were taken for an argument area and `Dim blank As robot` lost its type: `global_8647D0(t_edi).f0 = v3584`.  Only posto has this form among DarwinBots' 1077 procedures.
- *A Currency compare in a loop test and register pairs.*  `fold_register_pairs` (new then) merged `mov ecx, [x+14h]; mov eax, [x+18h]` into one Currency before `fold_cy_branches` had seen them, and the test printed `If t_edx >= '<Hi(Var(reg,ecx))>` (gh_zip frCopyCompressed).  The branch form is folded first now.
- *A known subscript.*  PushIntStack: `mov ax, 64h` in one block, `movsx esi, ax; cmp esi, 65h; jb ...` and `mov [esi*4 + 86454Ch], edx` after the check.  The constant reaches the address, which printed `400.f86454C = a1` (`0.f86454C` in PopIntStack): a constant base with an address as displacement is that global.  `fold_static_derefs`.
- *An array copy of a field of an element.*  `dna = rob(n).dna` hands `__vbaAryCopy` a sum as source or destination; taken as a record variable it printed `v18.f0 = ...`.  `calls` now goes through `dyn_field` / `dyn_element_linear` and copies from the field.
- *An inline array whose first subscript carries a constant.*  `.bit(i - 1)` of a `bit(0 To 31) As Integer` reads raw -2 with the index register at i.  `inline_arrays` took the array's base from the first access it saw, so `f0` was not recognised as an array (`v40.f0(t_esi)`: Expected array).  The base is now the first access at an offset of zero or more, and the count the largest seen (DoubleWord comes out `f0(0 To 31) As Integer`).
- *A call in a tail statement is not always the tail call.*  `first_pass` defers a call that ends a procedure: its value, eax left behind, is used only if that caller returns a value itself (VBDoom's InitCheats ends with cht_MakeSeq, a Sub).  It deferred every call in that statement, so in `PushIntStack BitToNumber(v)` as the last statement of a Sub the inner call counted as unused and BitToNumber came out a Sub, with a body that fits (`call __vbaFpI4; mov [esp+0Ch], eax ... neg eax; ret 4`, the value kept in eax across a loop).  Only the outermost call is deferred now.  Found with `tools/passtrace.py --raw` (what the first pass decides from).
- *A record's address in a callee-saved register over a loop.*  `lea edi, [esp+...]` before a loop, `mov [edi+esi*4+4], eax` inside: at the loop head the back edge has not been lifted, so the merge dropped the register's value and printed `(t_edi + t_esi2002 * 4).f4`.  `lift.frame_regs_over_loop`: ebx, esi or edi holding a stack address on every way in that has been lifted, and not written in the natural loop, keeps its value (now `v124.f4(t_esi2002) = v240`).
- *Subscripts of a two-dimensional array with constants.*  `dnamatrix(tipo - 2, value - 1)` has both constants folded into one offset.  `lift`'s subscript split divided rounding down, and printed `dnamatrix(a1 + 7, t_esi - 2)`: the same address, but a different element for every bound check and a run-time error on the edges.  When a lower dimension has a subscript the constant now goes to the nearest stride (`(const + stride // 2) // stride`).
- *A plain sweep that stops at 4000 instructions.*  `disasm._sweep_plain` is what finds `mov ebx, [import]`: fast code caches `__vbaGenerateBoundsError` and other imports in a callee-saved register.  Past its limit the load wasn't seen, so `call ebx` was taken for the never-returning bounds error and the procedure ended there.  Reproduce lost everything after `makeoccurrlist`: DnaLen, CountGenes, the tie to its parent.  Nothing complained: it compiled, and the first sign was a save file with no birth ties in it at cycle 17862.  The limit is 200000 now (Form_Load is 52000 instructions).  A silent cap like this is worth looking for whenever a procedure's tail seems missing.
- *A slot holding a descriptor's address learnt an array type from the callee.*  For `ListCells clist()` of a fixed local array the code is `lea ecx, [ebp-50h]; mov [ebp-12Ch], ecx; lea edx, [ebp-12Ch]; push edx`, and fast code uses that same slot for Singles elsewhere.  `decompile.proc_arrays` knew such slots as scratch for what callers tell callees, but still let the callee's `a1() As Integer` type the caller's slot: `Dim v12C() As Integer`, then `arrbase` made that `Dim v12C As Integer`.  So `v12C = VectorMagnitude(v)` rounded a Single, without a compile error until a Single parameter met it ByRef.  Scratch slots take no link at all now.
- *A For's limit is read once; a test reads it every time.*  `For t = 1 To .DnaLen - 1` compiles to `v11C = .DnaLen - 1` before the loop and `cmp eax, [v11C]`, and a Delete in the body shortens .DnaLen.  `inline_temps` put the field in the test: the temporary has one definition and one read, and it only checked that the variables in the value were not reassigned, not memory.  Three random numbers short in MajorDeletion, and every number after that differed.  `reads_memory` + `repeats_without` now refuse when the use can run again without the definition running (`cycle_groups` finds the cycles first, so a procedure of thousands of blocks pays nothing for straight code).  `structure.fold_for_limits` gives a real `For` its expression back, as a For reads it once: unoptimised code still prints `For v18 = 1 To .f3480 - 1`.
- *A procedure between a read and its use.*  `safe_between` only knew about stores to the same element, so `x = a.b: Foo: y = x` became `Foo: y = a.b`.  A call that isn't pure now stops a value read from memory.  Two exceptions it needs: an element's offset, `(i - lb) * size`, is always a compiler's temporary held for the statement's own subscript (without this, `.f14` of the descriptor reached the output, Invalid qualifier), and a ByRef parameter counts only if the call is given that parameter.  Temporaries tried again after an inline keep `Bar global_x, Foo()` whole.
- *A compare's operand overwritten before its branch.*  `fcomp [esp+1Ch]; mov [esp+1Ch], 1; fnstsw ax; test ah, 41h; jne`: fast code builds a flag in the very slot it has just compared, so the pending compare must keep the old value.  `preserve` looked after registers, pushed arguments and the FPU stack, not the flags, so `Abs(x) < r` printed `v14 = 1: If r > v14`, with the Abs lost as a dead store, and DarwinBots simplecoll ignored the X distance.  Operands that are the stored value itself stay out of it: `now_in` reads those from the variable, as the source does.
- *A For in fast code.*  The counter is in a register as well as in its variable, and in one of several shapes: `v18 = 1: eax = 1: L: If eax > limit ...: v18 = v18 + 1: eax = v18: GoTo L`; or stored at the top, `ecx = 1: L: v24 = ecx: If ecx > limit ...: ecx = v24 + 1: GoTo L`; and the limit may be reloaded into a register each round.  `as_for` wants the variable in the test and its step last.  `simplify.register_copies` works out where a register or temporary holds a variable's value (available copies over the blocks, constants included: `mov eax, 1 ... mov [v18], eax` crosses a block) and reads the variable there; a local passed by reference counts too, up to the next call that isn't pure (`Delete a, t` inside `For t`).  `counter_in_register` handles the second shape.  `preserve` leaves alone a register holding the very value being stored, so `now_in` makes it the variable, instead of a copy of the old value and one added to it.  `flatten_else` also moves the other arm out when the Else leaves (`If a(t) <> -1 Then t = t + 1 Else a(t) = n: Exit Sub`).  `as_for`: the start's constant may be in the guard (`v1C = 1: If v20 >= 1 Then`); it refuses a limit that reads memory or that the loop assigns, and a start with anything but statements after it (both were printed as a For, wrongly, if the code really was a While).  DarwinBots: For 187 to 374 of the source's 558, `Dim t_` 1474 to 989, 2700 lines fewer, and the same saves.
- *A constant in an array of Doubles.*  `a(0) = 1.5` is `mov [eax], 0; mov [eax + 4], 3FF80000h`, in fast code and unoptimised alike, and printed `v20(0).f0 = 0: v20(0).f4 = &H3FF80000` in a made-up `T_8_1` (tests_wip/t95arrconst).  The type was there all along: `__vbaAryConstruct2(&sa, template, VT)`, `__vbaRedim`'s VT, a module's table, and callers tell an array parameter.  `simplify.merge_number_elements` joins the halves when the array is Double, Currency or Date (the first function of that name was already taken by member arrays, and Python let the later one win silently: `merge_element_halves`).
- *By-value Variants in the runtime's parameters.*  `GetSetting(a, b, c, Default)` pushes the Variant's four dwords, not its address, tag first.  With no spec the fallback took every dword for an argument, and DeleteSetting's two Optional Variants made nine.  `runtime.byval_variant`.  VT_ERROR with 80020004h two dwords on is an argument left out.
- *A Variant tag with VT_ARRAY says nothing about its payload slot.*  For `Filter(s, "p")` VB builds VT_BYREF | VT_ARRAY | VT_BSTR around a slot holding the descriptor's address, and fast code uses that slot for a number later.  `typed_ref` read the 8 in 6008h and made the slot a String: `Print #1, QBColor(4)` printed `128` for ` 128 `.
- *A Currency literal is ten thousand times the number.*  `x = 15000` of a Currency `x` is `1.5@`: `floatlit.currency_store`.  In the same pass, a Single's literal in a comparison, sum or argument (`x <> 1065353216`) prints as `1!` once the final Types are known.  A Print item's code (4 for Single, 5 for Double) types a Function's result: `types.printed_results`.

## Runtime functions and language features no target had used (2026-11-04, tests_wip/coverage, t94rtc)

`tools/rtcmissing.py MSVBVM60.DLL` lists the runtime's exports the decompiler has no entry for (134 of 523 at the start).  Written tests for what programs really use, one cause each:

- *Implements.*  `Set a = d` with `a As IAnimal`, `d As cDog`: VB casts with `__vbaCastObj(d, &IID_IAnimal)`.  The variable took the source's type from the copy, `Dim a As cDog`, and `a.Speak` failed to compile, or as `Object` failed at run time with 438 (the interface's methods aren't on the default dispatch).  A cast to one of the program's own interfaces is the variable's declared type; a library's isn't (t14ocxext: `Set o = pb1.DragIcon` casts to IPictureDisp with `o As Object`).  A ByVal object parameter's copy had the type and the parameter didn't: `alias_byval_copies` passes it on.
- *ParamArray and Optional.*  Byte 1 of a member descriptor's first dword is FCh for a ParamArray, and other values count something else: `(a, b, Optional c As Variant)` has 05h.  `& 0xFC` took any of them for a ParamArray.
- *GoSub.*  `If k > 2 Then GoSub A Else GoSub B` lost the test: both statements had the repr `GoSub()`, and `hoist_common` moved the common one out.  `On k GoTo`'s range check (`__vbaOnGoCheck`, error 5) now stays as `If k < 0 Or k > 255 Then Error 5`.
- *AddressOf.*  Fast code's frameless procedures have no prologue to recognise.  A pushed address in the code area that is aligned to 16 and not followed by a jmp (a cleanup's continuation is) is a procedure.  Nobody in the program calls it, so `proc_returns` said Sub, and `CallWindowProc(AddressOf Cb, ...)` got back whatever was in eax: an AddressOf target counts as a Function.
- *Dead stores that can fail.*  `d = CDate("nope")` with `d` never read, then `Print Err.Number`.  Conversions keep their statement (`may_raise`), and under On Error Resume Next every runtime function does (`raises_here`): `n = QBColor(16)`.
- *A Print item's type.*  The descriptor gives each item's VARTYPE, and fast code may have folded the conversion away: `Print #1, CDbl(dt)` printed a date, `Print #1, a > b` printed -1.  `render.PRINT_AS` wraps the item when its expression's type isn't that.
- *By-value Date parameters.*  `ByVal d As Date` whose only use is `Format$(d, ...)`: the callee has VT_BYREF | VT_DATE around its slot, callers pass all sorts.  The tag is the declaration: `types.var_type` believes it for a parameter (`Iso(o1008, '<Hi(...)>)` before).
- *Late-bound and Variant.*  `Set v = GetObject(...)` then `v.Add`: the late call made `v` an Object, and `IsEmpty(v)` after a failure gave False.  A Variant stays one.
- *ParamArray results.*  `fold_paramarrays` runs late; the temporary it frees has to be inlined after it: `Set x = CallByName(o, "Obj", VbGet)` stayed `t = CallByName(...): Set x = t`, the Let of an object (438).
- *Arrays of a class.*  `__vbaAryConstruct2`'s third argument is a VARTYPE or, for `Dim ka(1) As cK`, the address of the class's IID.  It went into `vt` as a number and printed `As Long`.
- *For Each over an array.*  `__vbaForEachAry(VARTYPE, &state, &state, &element, array)` also says the parameter is an array of that type.  `as_foreach` allows plain register copies after the first call and after the next one (fast code).
- *For with a Currency counter.*  `__vbaCyForInit`, `__vbaCyForNext`, laid out like the Variant ones so `as_varfor` takes them; with no temporary as in optimised code, `as_runtime_for`; what isn't recognised calls helpers written into the module, as for Variants.
- *WithEvents on a control.*  `Private WithEvents btn As CommandButton`: its class comes from the events IID in its control record (As Object doesn't compile).  `rename_object_temps` took a member for a temporary and dropped `Set btn = Command1`.  Without a map its handler was named `Form_Click`, as index -1 in a form's control list was taken for the form itself.
- *CDec.*  `rtDecFromVar(&source, &result)`, result second, returning an HRESULT.
- *A vacuous pass.*  `Print #` keeps its text until Close, so a run that dies leaves an empty out.txt on both sides: t94rtc "passed" with a runtime error 5 in the test itself.  `roundtrip.py` now fails when the original's output is empty.
- *Three old tests that compared nothing.*  With empty output now a failure, t12resume and t83obscure turned out to hang (the tests' own bug: `Resume` runs the failing statement again, and in `If n < 3 Then Err.Raise 5` that is the Raise, not the test) and t19fixrec to stop with error 59 (a record with a String in a `Len = Len(r)` file).  Repaired, they found three real bugs:
  - *Resume's flow.*  `h: z = 2: Resume` lost the `z = 2`: Resume comes back to a statement no edge shows.  `handler_live` now counts everything read anywhere in a procedure that has a bare `Resume` or `Resume Next`.
  - *`F (k)`.*  VB copies `k` into a temporary and passes that by reference; `inline_temps` took the copy out and `k` changed.  A copy of a variable read as `&t` stays (it prints as `t = k: F t`, the same thing).
  - *Erl.*  `mov [ebp - 4], k` is the statement's number, for Resume and for Erl.  The procedure descriptor's +18h points at {count, u16 per number}, the source's line numbers, read as `line[k - 1]`.  If the program imports `rtcGetErl`, `lift.line_marks` prints a line of its own with the number where it changes, so `Erl` gives what it gave.  Such a procedure gets no tail copies (`cfg.numbered_lines`): a label only once.

Still failing there: t36lset (`LSet` from an array element of a one-Long record: the element took the target's two-Integer type), t59cyarith (Currency `=` in fast code, halves in registers: `Hi(CCur(-2.5))` reaches the output).

## A program with UserControls: from the census to the original's screens (2026-11-05, unoptimised native code)

The first screens and the main window with the program's real data in a ListView: 0 pixels differ from the original under Wine, from the same registry state.

- *VB6 runs a UserControl's code when it loads a form that holds one*, compiling each procedure on demand.  So a /make can stop on a run-time error box (nothing closes it), or crash: to report a compile error there VB6 opens its main window, which calls `_MsoSetLocale` from MSO97RT.DLL, absent from the prefix, through a null pointer.  `tools/errcensus.py` renames the `UserControl_*` procedures in its copy (they still compile, none runs); `tools/vbmake.py` notices a small "Microsoft Visual Basic" window, saves it as `make_box.png`, ends that VB6 and reports it, instead of waiting 300 s with the box on the desktop.  The prefix has `HKCU\Software\Wine\WineDbg ShowCrashDialog=0` now, so a crash no longer leaves a dialog waiting either.
- *A member with another one within 16 bytes is no Variant* (`writer.member_types`), and one class among its assignments says more than Object.  `m40 As Variant` from `Set m40 = PropBag.ReadProperty(...)`: it is Empty until then, and `Empty Is Nothing` is the 424 of the first builds.  It is `As Picture` now, from `Set m40 = pic`.
- *Members of a Picture or Font go by DISPID* with nothing at the read to say what the object is.  `decompile.object_slots` follows Picture and Font parameters through Sets and calls across the program; `calls.lateid_member` asks it, through `lf.obj_copies` for a ByVal object parameter's own frame slot: `m40.Width`, `a1.Type`.
- *Me's own procedure at a join*: Cases that jump to one `call [eax+700h]` with `push esi` printed `vtbl_700`.  `do_vtable` takes the receiver from the top of the stack when it is Me.
- *Two object variables: Set.*  `a_1 = o1` (a merged call's argument) and `o2 = m44` before a join, without Set, read the default property of an object that isn't there yet.
- *A UserControl's Font in a form needs its class*: `BeginProperty font {0BE35203-8F91-11CE-9DE3-00AA004BB851}`.  Without it VB6 looks for CLSID {0}: error 713, loading the form.
- *What an API fills is no Variant.*  `OleTranslateColor a1, 0, VarPtr(v0)` with `Dim v0`, for lack of other evidence: the API wrote over the tag, error 458 on the next read.  `VarPtr` or `&` of a local to a Declare is weak evidence for Long.
- *The `VarPtr` rule for records was too eager.*  Written for a real `VarPtr(uBuf)`, it made records of whatever lay above any local given to an API that no list knows.  Now not when the slot is read narrower than a dword (a Boolean an API fills: `a1 = v60.f0` was 65535 and the program showed a screen it had been told not to), nor with slots the compiler frees one by one (`__vbaFreeStrList`'s ANSI copies, typed Long: error 13) or Variants.
- *An unknown API's Strings.*  `__vbaStrToAnsi` marks its result (`marshalled`), `do_declare` keeps `Call.ansi_args`, `writer.unknown_api_strings` declares `ByVal aN As String`, or `aN As Any` with ByVal at each call where others pass a number or a pointer.  `ByVal a2 As Long` turned a setting's name into error 13, and On Error made that the default value: the program went on quietly with the wrong settings.  ANSI literals to such an API are Strings too.
- *`__vbaVar2Vec` and `__vbaStr2Vec` make Byte arrays.*  Any other array goes through `__vbaAryVar`.
- *`__vbaVarLateMemCallSt`*: the value first, then the subscripts from the last, as DISPPARAMS.  `v30.SubItems("text") = 1` came from the first, wrong reading.
- *An offset slot with its uses inlined*: `frame_offsets` dropped `o = i - lb` while the read `a(o + LBound(a))` stayed, which `alias_arg_copies` then tied to another object in the same pooled slot: 60 wrong subscripts that compiled.  It now sees scaled and copied slots, and an assignment goes only if nothing reads what it gave.
- *A zeroed Variant filled by a call is no Empty* (`If Empty = True`), and *a Variant built around a variable means a Variant parameter* (`wrapped_params`), see below.

What running it past the first screens found (`tools/guidiff.py`: both builds from one folder and one saved prefix, then windows, the whole registry and the files compared; every one of these compiled):

- *`DefaultCancel` and its like are in no record of the form.*  They live in the OLEMISC dword at +2Ch of the object's GUI table entry: 400h InvisibleAtRuntime, 1000h DefaultCancel, 2000h ForwardFocus, 4000h CanGetFocus = False, 8000h Alignable, 10000h ControlContainer; Windowless is 8000h in the flags at +28h (`tools/ucprops.py` builds one project per property and diffs them, with what two plain builds differ in taken out).  Without DefaultCancel `Ambient.DisplayAsDefault` stays False, which was the only pixel difference on the main window.
- *A late call's result that goes on to `__vbaCastObjVar` is an object*, and is `Set`.  `v60 = Me.ListView1.ListItems` reads ListItems' default member, which wants an index: error 450.
- *A Variant by value, its four dwords pushed.*  `fold_variant_slot_reads` turned the tag dword into the value and left the third dword a read of the pooled slot, whose store had gone: `Proc_401000 ..., "B", vC8, v40` for `False`.  With vC8 a String, `v40 = False` was a type mismatch under On Error Resume Next, and the reloaded list lost its bold.  Now the tag stays and the third dword gets the value.
- *A frame address for a listed API's ByRef parameter stays by reference* (`Call.addr_args`): the temporary's 199 was inlined, `declare_fixes` took that for a value and declared `ByVal nSize As Long`, and the API took 199 for a pointer.  Every GetUserName in the program went with it, and the handler just stopped.
- *A parameter with no evidence is a Long, and that can be wrong at run time only.*  `Proc_401000(a1)` only passes a1 to a late bound method; the tag on the Variant (4008h) and the callers' `p & "\x"` both say String.  Both count now.  As Long every path was a type mismatch, reported by the program as a corrupted file.
- *`Dir$`, `LOF` and `FileLen` are not pure.*  A register kept over a join printed `t_edx = Dir$(v44, 39)`, a second call, and Dir$ moves on with each.
- *A late bound read whose value goes unused* stays an assignment (`o1 = v18.Value`).  As a statement it is a method call, and a Property Get answers that with error 450; dropped, its side effects are lost (t96sbfix).
- *A value that looks like a tag.*  `-8Ch = 2; -94h = 2; -9Ch = 3; -0A4h = 3; ...` are two Variants, value then tag, one above the other.  `fold_variant_temps` took the 3 at -9Ch for a tag whose value was the Variant just made at -94h, and the real tag at -0A4h for one whose value was that: `ScaleY(y, 2, 1)` where the source has vbPixels, and two pixels of a progress bar were all there was to show for it.  A Variant already made is nobody's value half, a store with a tag for the slot 8 below right after it is a value, and a tag takes the nearer of the value before and after, none across a call on an object.
- *14h is a SAFEARRAY's lower bound only in a SAFEARRAY.*  `fold_offset_slots` took a record's field there for a bound, and moved its read past a store: SwapMove lost `.f14` (ChessBrainVB: other nodes from the first search).  It compiled, and all 67 suite projects passed.
- *A fixed array's slot value for a listed `ByVal As String`* is the first element, and a mixed parameter is As Any with ByVal at each String (`lstrcpyn ByVal s, ...`, `lstrcpyn b(0), ...`).

Driving the program: a UserControl's Click needs the button down in GetKeyState during MouseDown, or VB takes no capture and no Click comes, and it needs time between down and up for the focus message it posts itself.  `winpoke at` attaches to the program's thread for its input state, which leaves the real mouse and keyboard alone, sets VK_LBUTTON there, and sends the messages with a pause.  On a click to a control whose form isn't active, focus still lands as it should.

Compared with the original:

- Every screen, page and tab reached: 0 pixels.
- Actions that write the registry: the registry the same.
- Files the program writes: the same, but for random padding and a date, which differ from run to run.
- Encrypted files: decrypted with a small program of the same algorithm, then the same line by line.
- Not run: what needs the network.

## When an error goes on to the next statement (2026-11-05, simplify.pin_statement_variables, rename_statement_temps, tests t99 to t101)

Running a Windows build of a large real program's decompiled source under Wine next to the original stopped at once: error 91 in a procedure where the original asks whether to go on.  It compiled and the census was clean.  That led to everything here, and none of it shows at compile time.  With On Error Resume Next not even at run time: only the values can tell, so tests/t100ornobj and t101ornall print them.

**A file number typed As Object.**  `types.walk` gives Object to the receiver of a call on a ByRef parameter (`Sub strings(frm)`, DarwinBots), and `Print #f, s` is a Call with its file number as `obj`.  A parameter only printed to came out `f As Object`, and by the rule "a declared ByRef As Object takes an Object variable" the caller's `n = FreeFile` too: `Dim n As Object: n = FreeFile(1)`, error 91.  Several procedures of a large real program had it.  tests/t99filepar fails with error 91 with the fix off.

**VB keeps nothing in a register from one statement to the next in a procedure that may resume**, and pools its temporaries in frame slots, for any type, for the whole procedure.  Three things came of that:
- `rename_object_temps` knew controls only.  `mov [ebp-48h], Form2` is a plain copy without AddRef, the slot goes on to hold other objects, and its one entry in objtype said the last of them.  It printed `v48 = Form2` (no Set: error 91, resumed from, so the caption read as empty), the Global object's copy not at all, and then `v48.App`.  Another real program: `Set v1 = v2.App`, so a file of its own was opened in the root; DarwinBots newdata.  Each fetch of a form's predeclared instance or of VB's Global gets its own name now, and they fold: `Form2.Tag = "first"`, `App.EXEName`.
- `v94 = Me` in the same procedure: `mov [ebp-94h], eax` right after a method call is the HRESULT, and eax still read as the object pushed before the call.  With its own name it is dead; the lift still says that.
- A plain copy into a variable typed exactly Object now always prints Set (render).  A wrong type shows as a compile error then, which is the better way.

**An error leaves the assignment undone, and the next statement runs.**  `mark_error_regions` knew it for liveness only; everything else folded as if no statement could fail:

| source | decompiled | under Resume Next |
| --- | --- | --- |
| `n = 32767: n = n + 1: Print #1, n` | `Print #1, 32767 + 1` | the whole line lost |
| `b = 200: b = b + 100` | `b = CByte(CByte(200) + 100&)` | b is 0, not 200 |
| `i = 5: i = CLng("abc"): Print #1, i` | a new variable for the second | 0, not 5 |
| `i = Inner(0): Print #1, i` | `Print #1, Inner(0)` | the whole line lost |

`mov [ebp-4], k` before each statement says where statements begin, and they are laid out in source order, so an address gives its statement.  VB's temporaries live and die inside one.  So, in a procedure with On Error Resume Next or a Resume (`pin_statement_variables`, run before anything folds):
- a slot read in a statement that did not write it first is a variable of the source, or a hidden one: `lf.pinned`, which `is_named_local` and `lf.kept` count like the epilogue's String and Variant locals.  All its stores stay, it is neither renamed nor inlined.  (Not in `lf.named` itself: `decompile.struct_hints` reads that to find what a record's fields are, and gh_zip's `Dim uCtx As ...` fell apart into Longs.)
- a slot handed over by address may be written there (`F(&temp)`, a Function's result), so that counts as a write if it is read later in the same statement; if not it is a ByRef variable (`Foo x: Bar x`).
- what is left are temporaries, and `rename_statement_temps` gives each one name per statement.  That also ends one type for a slot's every use (`vF0 = v24 - 1`, `Dim vF0 As Object`), and what is single can go back where it came from: decompiled code in these procedures reads like the source now (`vE8 = Err.Number`, `Err.Clear`, `v38(0 + 1&, 1 + 1&) = 7`).
- A write must be strictly before the read: `x = x + 1` reads another statement's x.
- A statement has to have its own address for any of this.  What a block's last instruction emitted had the block's start (`lift`: an element's store seemed to come first in its block, and its subscript's slot to cross statements); fixed there.
- Not what the lift leaves in registers: a copy into one counts only if the same statement goes on to read that register (over a join in an expression).  The ones materialised at a back edge read slots of the statement before, and the saved copy of a slot about to be overwritten (`Var(tmp) = v`, for flags no one uses in the end) read like crossings: `v698 = Not CBool(...)`, then `v694 = v698.Number`, one slot for a Boolean and for Err, Invalid qualifier.
- Not a With's pointer to an element: a slot set to a sum or an address and read through.  It crosses statements, and it is no value of the source; it folds as ever.  (gh_zip AddFile: `Dim v614 As T_50_1`, `v614 = m90(m8C)`, the fields written to a copy.)
- For Each's flag and a Variant For's (`__vbaVarForInit`) go from the head to the Next by way of eax and a slot: pinned, or two names and no loop came out, and nothing ran (a list file a real program writes was never written; running its scenarios caught that one).
- A jump table's tests have no instructions (`cfg.build`, keys above 1 << 40): `branch_address` finds the table's block in the key.  Without it Select Case read the slot under another name than the statement's, no Case matched, and a data file the program writes had spaces where its flags belong.
- `rename_block_temps` takes a statement's name again when duplicate_tails has copied its block, one name per copy, or it has two definitions and nothing inlines.  `scaled_index_slots` had to learn the tuple names.

**Also found by t101:** `Set g = Nothing` was evidence that g is a Long, so a global object variable set to Nothing somewhere came out `As Long` ("Object required"); `drop_zero_init` never looked whether the next store reads what it overwrites, so `d = 1.5` in two stores lost its lower half to `d = d * 2`, in any first block; a Double field only ever printed, written in two stores, was two Longs and printed its lower half (now 8 bytes from the Print's two pushes); subscripts of a two-dimensional array in pooled slots, `x + y * 2`, split to the nearest stride as `(x - 1, y + 2)`, the same element if both are valid and a subscript error alone (a constant of 0 means each term is already relative to its lower bound).

**What is still not the same:**
- A statement VB ran as one and the source must split: `Print #1, v(1); UBound(v); v(7); Err.Number`.  v(7) failing skips the whole statement in the original; split, the Print still runs with what it has.  Inlining puts most of them back in one piece; `Err.Number` read before a Print that may fail cannot be moved.
- `For i = 1 To 1 / z`, a limit that raises.  It printed as a Do While and its i = 1 runs; in the original nothing of the header does.  (Printed as a For now its step folds; not checked for that case.)
- A constant subscript past the end of a record's array (`gP.v(9)`, no bounds check, past the record): an undeclared field and a compile error.  Undefined in the original too.

## A record's size from Put is where its last field ends (2026-11-05, records.io_gaps, udt.synth_body, tests/t102oddrec)

3 Bytes, a Long, 2 Bytes: Len 9, 12 in memory, and `__vbaPutOwner4`'s descriptor says size 10, then entries of bytes to copy and gaps: copy 3, gap 1, copy 6, gap 2.  Disk has 9 bytes.  A record of a large real program had the same shape: a last gap past the size, and a Len, as its `Open ... Len =` says, of the bytes copied.

`io_gaps` gave up when a gap ran past the size, which the last one always may.  And `synth_body` took any size that is no multiple of 4 as "no 4-aligned field fits" (right for a size from an array's stride), so `mov dword ptr [rec + 4], eax` came out `f4 As Byte, f5 As Byte, f6 As Integer`, and `.f4 = RGB(0, 0, 128)` raised Overflow.  A size in IO_GAPS comes from a descriptor: a Long may be there, and a hole in its gaps is VB's and gets no `pad` of ours (one more byte for Len, Bad record length on a Random file).  A real program goes there when it creates a data file.  A Windows build of its decompiled source raised the error too under Wine in one new folder, though in another, also new, it did not, with the same EXE and data: what differed between the two runs was not found (a setting in the prefix's registry is the likeliest).  tests/t102oddrec.

## A real Windows build against the original (2026-11-05, tools/execalls.py, tools/fixpoint.py)

The decompiled source of a large real program was compiled with VB6 on real Windows: every file, no error.  So the few procedures the census cannot build under Wine were never errors: "Variable uses an Automation type not supported" is VB6 under Wine's.  Those have only ever run from a Windows compile.

**What the project's properties leave, and what the decompiled .vbp did not give back.**  Nothing checked this until a rebuilt EXE sat beside the original:

| | original | rebuilt before | what it is |
| --- | --- | --- | --- |
| Version block | version number, company, description, copyright, trademarks, product | 1.0.0.0, nothing | `MajorVer`, `MinorVer`, `RevisionVer`, `Version*` in the .vbp.  App.Major and the like read it at run time: a rebuild would report 1.0.0, not the original's version |
| File name | Prog.exe | PROG.exe | `ExeName32` |
| EXE's icon | Form3's | VB's default | `IconForm`; left out, VB takes the startup form's |
| Help file | | | `HelpFile`, App.HelpFile |
| Unattended | | | `Unattended`, App.UnattendedApp (+3Ch bit 4) |
| Optimisation | Small, or none | always Fast | `OptimizationType` (detect.options) |

- **The header's four strings at +58h were misnamed.**  What every description of the header calls the project description is the name of the file VB made, without .exe (`toinennimi` with `ExeName32="toinennimi.exe"` and `Description="Kuvaus"`); the second is the Title, not the EXE name.  `project.exe_base`, `project.title` now.  The .vbp's own `Description=` ends up in the EXE too, but behind no pointer we know, and nothing reads it at run time: not restored.
- **A key left out is not left empty.**  VB fills in the machine's registered company when `VersionCompanyName` is missing: the Windows build had the build machine's registered company as its CompanyName.  Every key is written now, `""` when the original has none (under Wine that gives no CompanyName; not yet seen on Windows).
- VB takes up to 9999 for each number (`MajorVer=12345`: "contains invalid 'MajorVer' key value", and the build fails) and a string as it stands between the outer quotes: `"a ""b"" c"` stays doubled in the block, `"a "b" c"` is right.
- `Retained=1` with `Unattended=1` in a Standard EXE of no forms: "Retained Project cannot be unloaded", no EXE.  With a form, no flag in the header.  So only an ActiveX project could have it to carry.
- `tools/roundtrip.py` now fails a test whose rebuilt EXE differs in any of these (`same_project`), and when the decompiled project builds no EXE by the original's name: before, that passed in silence.  tests/t97vbpinfo (all of them, two forms, a second form's icon), tests/t98unattended.

**Declares: fewer rebuilt, none lost.**  Most of the missing ones have no caller in the original: VB keeps a thunk for those, and the decompiled source has no reason to declare them.  The others are the same functions spelled `user32` for `user32.dll`.  Every called one is in the rebuild.

**The same calls?  (`tools/execalls.py ORIG REBUILT`, no maps.)**  Procedures: a few event handlers of the original with an empty body are not in the rebuilt EXE (see below), and the rebuild has 3 helpers more (VarForInit, VarForNext, a Variant copy).  Of the pairs about three quarters make the same calls in the same order, under a tenth the same calls in another order, the rest differ.  Differing is mostly not losing, and four things look like a loss and aren't:
- *Merged arms.*  Identical code in several arms becomes one statement after them (each Case ending in the same assignment; a `Close #n` in several arms).  Same behaviour, fewer calls.
- *VB's own cross-jumping*: the compiler jumps into an identical call of another arm.  A procedure with 5 MsgBox statements has 5 calls in the original and 4 in the rebuild.
- *A result nobody takes.*  `Replace s, ".", ""` as a statement (`call rtcReplace; __vbaStrMove to a temporary; __vbaFreeStr`) has no effect, and the decompiler drops it.  The original's own bug, kept out of sight.
- *Early against late*: `__vbaLateIdSt` against `__vbaLateMemSt`, `__vbaObjVar` against `__vbaVarIndexLoadRef`.  The same call, with a Variant or Object where the original knew the type.

Word by word in the first source against the original's calls, what is really missing: a few dozen calls of the program's own procedures in under 1% of its procedures, the ones looked at merged arms; a handful of runtime calls, the same.  So no statement has been found lost.  What the comparison cannot tell is whether a merge was right: it was in the ones read, and that was a sample.

*Empty procedures.*  VB compiles a Sub of no statements to one stub for the whole program, `xor eax, eax; ret 4`, every thunk jumping there.  The decompiler gives it the first owner and name it meets: an empty `Public Sub` printed in one form under the name of another form's procedure.  Harmless, and the real procedure of that name is named by its vtable slot (`p.member`), so it is right.  `p.name` from `model._method_tables` can be one off for a form whose name list holds a Declare first or last: printing does not use it for public members, tools reading `p.name` should not trust it.  An event handler the original has with a frame and no statements (39 instructions, AddRef and Release of Me) comes back as an empty one, which the compiler folds.

## What can be read from a VB6 EXE before decompiling it (2026-11-05, vb6dec/detect.py, tools/optmatrix.py)

One small program built with each compile option under Wine (`tools/optmatrix.py`), and each result read back:

| .vbp | what it leaves | read as |
| --- | --- | --- |
| CompilationType=-1 | project information +20h is 0; imports ProcCallEngine | p-code |
| OptimizationType=0 | calls the runtime through `call [iat]`, or `mov edi, [iat]; call edi` | Fast code |
| OptimizationType=1 | calls only `jmp [iat]` thunks (5 bytes against 6), but keeps variables in registers | Small code |
| OptimizationType=2 | calls only thunks; variables in memory, esi/edi/ebx barely used | no optimisation |
| BoundsCheck=-1 | no `__vbaGenerateBoundsError`, no `__vbaDerefAry1` | checks off |
| OverflowCheck=-1 | no `__vbaErrorOverflow` | checks off |
| FlPointCheck=-1 | no `fnstsw ax; test al, 0Dh` | checks off |
| FDIVCheck=-1 | no `_adj_fdiv_*` | checks off |
| CodeViewDebugInfo=-1 | debug directory with the PDB's full path | the path |
| FavorPentiumPro, NoAliasing | same size, other bytes; no mark found | not read |
| UnroundedFP=-1 | a little smaller; no mark found | not read |

Small against none, in instructions using esi, edi or ebx other than push and pop (`tools/regstats.py`): no optimisation 0.16% (t09noopt) and 2.60% (VBDoom); Small 6.8 to 15.1% (four suite projects rebuilt); the line is at 4.5%.  Fast code doesn't need it: 4.2 to 19.3%, told by its direct calls.  Only those samples back that line, so the output says "from the code".

**`compile_options` read bounds checks as removed when they were on**, and that went into the decompiled `.vbp` (BoundsCheck=-1): the rebuilt program lost its error 9.  `call [iat]` and `call thunk` were all it looked for.  Fast code keeps a pointer it calls often in a register, and a dynamic array's element goes through the runtime's checked `__vbaDerefAry1`.  Now any reference to the slot but the thunk counts, jumps to the thunk too, and `__vbaDerefAry1` means checks on.

The VB header's +34h says which of VB's own controls the project uses.  Bits as in `detect.MODEL_OBJECTS` (Print, Form, Screen, Clipboard and App always set): in 119 suite and coverage projects the same as the `Begin VB.x` lines of the source, none differing (`tools/mdlcheck.py`).

Standard EXE against ActiveX EXE: the COM registration data lists a Standard EXE's UserControls too (+24h = 1); a class there has 2.  Read from an OCX and from EXEs: VB6 under Wine stops with "Automation error" building an ActiveX EXE or DLL, so **no real ActiveX EXE has been checked**.

## Microsoft's files, measured (2026-11-05, tools/needfiles.py on a real program)

Share of the output's lines that change with each taken away: VB6.OLB 81%, MSVBVM60.DLL 32%, the OCXs 19%, WIN32API.TXT 4%, Wine for ocxbag 1%, STDOLE2.TLB 0.3%, the system folder's DLLs 0.  With only VB6.OLB there: 55%.  A form project without VB6.OLB loses its designer data and exits 0, so run_vb6dec.py stops without VB6.OLB or MSVBVM60.DLL and warns about the others.

On Windows (`vb6dec/msfiles.py`): VB6.OLB from VB6's setup key in the registry or its registered type library, MSVBVM60.DLL and STDOLE2.TLB from SysWOW64 (Windows has both whether or not VB6 is there), an OCX from the registry by interface or class, then the `.ocx` files beside the program and in SysWOW64.  Not every DLL in SysWOW64: there are thousands.

One process instead of forked workers (Windows has no fork): a large real program in 5 min 39 s against about 4.5, and 0 lines of code differ.

## A large program again: what fixes made for another broke, and what the census said (2026-11-05)

A census of today's code on a large real program, the first since work on another: every cause below is a fix made for another program or a loose rule it exposed.  What remains is "Variable uses an Automation type not supported" on `ListView.Font` or `il.ListImages(1).Picture`: VB6 under Wine cannot do those and VB6 on Windows can (tests/t21font, `tools/winetestfix.py`), so they are no errors in the output.  They did not show in an earlier census; why not was never established.

- *The evidence for an element's fixed array adds up.*  A global array of records lost its element's flat `f0(0 To n)` when the record type was built from the first evidence met; `array_element_types` now gathers it in `model.elem_farrays`, and `unify_array_params` prefers the type with the most evidence (`.f0(i * 2& + 1)` is back).
- *A whole element onto another of the same array* keeps no field 0: `a1(a2) = a1(a3)`.
- *A conversion nothing reads stays when it holds a call.*  `x = CInt(Proc_401000() - g)`: it may raise, and the call runs.  `dead_by_liveness` keeps a Call in CONVERSIONS as an Assign.
- *A register holding what was just computed is no conversion to keep.*  The opposite case: `a(i) = CLng(b(i))` leaves `eax` the same `CLng(...)`.  Kept because CLng may raise, it printed `t_eax = CLng((b.fC + o))` (Invalid qualifier) and broke the `For`.  `repeats_earlier`: a dead register whose expression a statement before it holds is dropped.  The `For i = n To 1 Step -1` of those loops came back with it.
- *A declared ByRef As Object takes an Object variable.*  `Set o = Form2.Picture1`, then `Form2.Draw o, ...` with `target As Object` in the member table: a Variant reached the local round a loop of the parameter rules, and VB said ByRef argument type mismatch.  The member table's word wins now.  A typed object would go through a cast temporary, so a plain address of a local given there is the Object itself.
- *`Set` printed without being a Set.*  After `__vbaCastObj`, the late read before it gets `vbtype = "Object"`: render prints Set, and as its kind stays the temporary still folds, `Me.List1.Font.Bold = True`.  (Kind `set` there kept `Set o1038 = Me.List1.Font`.)

Three shapes of tests/t96sbfix were tried for the last two causes (counters in memory by a ByRef, On Error, the class's own method, an object handed on to a module procedure); with the two fixes off they still give the same source, so **the suite does not pin these two**: only the census of the large program does.  The cases stay in t96sbfix as behaviour tests and say so.

## ChessBrainVB: from 56 compile errors to a build (2026-11-04, fast code, no bounds checks)

ChessBrainVB is compiled with OptimizationType=0 and every check removed, so array accesses are plain address arithmetic with nothing that names the array but its data pointer.  What it took, each a general habit of that kind of code:

- *Where a module's Static block pointer is.*  The last but one dword of the module's public data: `module_public + public size - 8`, in every module with a static table (4 modules of DarwinBots, 5 of ChessBrainVB, checked).  `decompile.static_slots` guessed it from how the slot was used and missed `GetPonderMove`: `mov edx, [44A624h]; add edx, 1Ch; push edx` is `ClearMove LastPonderMove`, where the block's start is the first Static and `+1Ch` the second.  `simplify.map_statics` now does modules too, addresses as well as reads; `static_block_regs` does the same through a register.  The public table's last 8 bytes are no room for a record either (`_module_records`).
- *Room is an upper bound.*  A kind 09h entry (a record only of numbers) has no descriptor, and what `module_records` knows is where the next listed variable starts.  Plain numbers aren't listed: `FinalMove As TMOVE` with room for 40 made the Long after it `global_44A758.f1C`.  An LSet or a parameter that says less wins over room; `global_struct_exact` holds the sizes that are not just room.
- *An array lock over a join.*  `With HashTable(i)`, fast code: `mov eax, [HashTable]`, a join, `push eax` for `__vbaAryLock`.  The marker named the register, the temporary became an alias of whatever eax held next (`mov eax, [ZobristHash1]`), and the table printed as `global_44A020(i).fE`; `resolve_lock_regs` now finds the register's reaching definition.
- *A fixed array's slot holds its data.*  Read as a value it is the address of the first element.  `VarPtr(HashCluster(0))` compiles to `push [slot]` for the one export VarPtr and StrPtr share, and StaticEvalArr(0) given ByRef to a Long is the same push.  A parameter that is an array takes the same form in the IR (the whole array, by its descriptor, lifted to the same Var), so `fold_fixed_sums` asks `proc_arrays` which it is.  `now_in` leaves a register holding a data pointer as that array when it is copied to a frame slot: `o1007.f8` was sInput(2).
- *Pointer webs* (`fold_pointer_ivs`, ColImbalance, IsAttackedByW/B, IsCheckingMove, MovePossible).  What used to stop it: a pointer kept in the frame (`mov [esp + 10h], edi`, now candidates when unnamed); `&a(i, j)` as a definition (`flat_address`); `mov edi, [Theirs]; sub edi, [Ours]; mov eax, [edi + ecx]`, the difference of two arrays' pointers, which makes `[edi + ecx]` an element of the other array; and a step in a register of its own, `shl esi, 2 ... mov ecx, [edx + esi]; add edx, esi` (`scaled`: the register then holds the direction itself, as long as nothing reads it as a number).
- *A With slot may be a pointer in the making.*  `lift._store` takes `&a(i, j)` in an unnamed slot for a With block and reads it by address.  A store of the slot's own element plus something (`mov edx, [esp + 14h]; add edx, 8; mov [esp + 14h], edx`) says otherwise: `p.stepped_slots`, and the procedure is lifted again.  Before this ColImbalance read the one element for ever.
- *`Deref(register)` printed the register*, silently: `vC = t_ecx * t_esi` for `[ecx] * [esi]`, which compiles and is wrong.  `VB6DEC_DEREFLOG=1` lists every one; ChessBrainVB is at 0 now.  This is the thing to check on a new fast code target.
- *Records side by side.*  `With Killer(ss + 2): ClearMove .Killer1: ClearMove .Killer2` gives one parameter the addresses +0, +1Ch, +38h: `group_structs` composite records make the parameter's bound 28 and `nest_types` nests that Type at each offset.  A field's address at any offset given to a record parameter nests too (`SetMove .BestMove`, a TMOVE at +8 of TMovePicker).  A record whose first field is the parameter's Type gets `.f0` (`byrefwrap.record_case`).  A module variable copied whole has a lower bound of that size (`lb`), so giving `.Killer1` to ClearMove doesn't make it a TMOVE; `resolve` must not spread the parameter's Type to it (`nested` holds module variables under `(None, key)`).
- *A parameter's slot reused.*  `reused_args` was decided in the order blocks are lifted; `reused_arg_reads` does it by flow.  `ClearMove Move` printed `ClearMove v40008`, and that slot, an element's byte offset on other ways through, became a TMOVE.
- *Whole byte offsets.*  `t_esi = t_esi + 7168` with `[pv + t_esi]`, where the subscripts are gone: `fold_fixed_sums` prints `a(t \ K)`, each dimension worked out of the flat number (`t \ 28 Mod 151, t \ 28 \ 151`).  It runs after `fold_fixed_array_copies`, so a register that turns out to be an element's address needs `fold_elem_pointers` again after it.
- *The byte it changes.*  `x = x Or &H200` on a Long or Integer element: `or byte ptr [x + 1], 2`.  `fold_byte_ops` (Eval's attack bits).
- *Reciprocal division among other terms.*  `Depth \ 3 + t + 4` comes with the two parts of the division apart in the sum, and several divisions can share one; negative divisors have the negated multiplier (`Score - a * b \ 100` is `x \ -100`, multiplier AE147AE1h).  `fold_magic_divs` flattens the sum.
- *Three dimensions* in `dyn_subscripts` (`ReadIntArr3`, an array parameter): `((k - lb0) * n1 + j - lb1) * n2 + i - lb2`, the counts at +18h and +20h, the bounds at +14h, +1Ch, +24h.  The first subscript's terms have neither count, the second's n2, the last's both.
- *A Sub that ends with another Sub's call.*  On one way out eax is that call's, and `proc_returns` read the temporary holding it as a use: `only_returned`.  `UpdateContHistStats = global_44A29C` was eax left holding the array's data pointer.
- *Bits of the same kind found earlier in this round.*  `pair_callers` stops at one-operand `imul` (GetMin came out returning a record); `array_at` with a second dimension of the same element size (`[p + i*8 + 4]` of a(1, 119) is a(1, i)); a global's size known only from how far a callee reaches is no upper bound (`global_struct_reach`).

**From a build to the original's output.**  All silent: it compiled, and played chess badly.  Method: `tools/runcmp.py` (both builds with the same arguments, the first line that differs), `tools/firsterr.py` and `tools/tracecmp.py --ignore HashKeyCompute,HashKeyComputeMap` (the first call that differs; those two have one address, the linker folds identical procedures), then a line of logging in both sources at the place, dumping the arrays.  In the order they were found:

- *A Static array in a module's block had no bounds* (`Static KeyList(3374) As Long`): the table gives them, under the block's address only (`global_array_slots`).  Unallocated, the first store was error 91: an access violation near 0 in native code comes out as "Object variable not set".
- *`scaled_index_slots` gave a register all its definitions' last value.*  `lea edx, [edi*4]` in four places became `t_edx = t_edi1010`, another block's edi, unset; CheckLegalNotInCheck never ended.
- *A memory read in a register is an expression until it is used.*  `mov ebx, [ecx + eax*4]; mov [ecx + eax*4], 0Dh` read the new value at ebx's first use (MakeMove: PieceFrom = Board(From): Board(From) = NO_PIECE, no piece ever reached its target).  `Lifter.preserve_element` keeps the old value in a temporary before a store that may overlap (same variable, not provably other bytes); `save_over_call` does the same for what ebx, esi, edi, ebp read from memory a called procedure can change (not arrays' own pointers, not objects).  Unoptimised code never kept such values, which is why this could wait until now.
- *`and al, 3; neg al; sbb eax, eax` tests the byte.*  The and keeps the register's upper bits, so the mask is -253; the compare must not be on all of it.  `CBool(WAttack(sq) And 3)` was true for any other attacker (`neg` on a byte register; the flags of `and al, n`).
- *A byte of a longer element.*  `cmp byte ptr [edx + esi*2], 0` and `mov ax, [p + i*2]; test al, al` are `(x And 255) = 0`, not `x = 0`: `part_of_element`, `low_value`.  Read-modify-write keeps the whole element (`whole_element`, `fold_byte_ops`).
- *An element's address in a frame slot, given on.*  `v2C = a(i)` printed a copy, MakeMove changed the copy, and UnmakeMove got the real element (CapturedNumber 0, the piece never came back).  `fold_elem_pointers` takes such slots now.  `VB6DEC_DEREFLOG=1` also lists addresses kept in variables ("address kept"), which print as if they were values.
- *A record local's size from the first procedure it is given to* (`struct_hints`): SEEGreaterOrEqual reaches 12 bytes, so CurrentMove.IsChecking stayed a loose variable SetMove never wrote.  The longest reach now.
- *`sar x, 31` is `x < 0`.*  It printed `x \ &H80000000`, which is 0 in VB, so a For with a step known only at run time, `(sar step, 31) Xor i <= (sar step, 31) Xor limit`, tested the wrong way going down.  What's left of `shr x, 31` after the reciprocal divisions is `-(x < 0)`.
- *One conditional jump, two compares.*  `cmp [mem], 1` falling into `jne X`, and elsewhere `cmp eax, 5; jmp` to the same `jne`.  A block holding only a jump is copied for each way in: `cfg.split_flag_branches`.  This is the compiler sharing tails, and it must be everywhere in fast code.
- *`Log` inline.*  `fldln2; fild x; fyl2x`, no runtime call.  `?asm` was a comment and the program ran on: `Reductions()` were all 0.  Added fyl2x, fpatan (`fld1`), fsqrt, fsin, fcos, fptan and the constants.  **A `?asm` in the output is a bug that compiles:** worth a check in vbcheck.
- *What the machine computes in 32 bits is Long arithmetic.*  `(Piece * 119 + Target) * 1786` of two Integer fields, on a register, wrapped at 16 bits in VB and gave the wrong history entry.  The rule was the other way round (only instructions that said so widened; `lea` and every fold's rebuilt sum didn't).  Now `width` 2 marks the 16-bit instructions and everything else prints with `&` or `CLng`, and `types.typeof` agrees.
- *`-(x <> 0)` is no Boolean.*  `setne cl` gives 1 or 0, and a variable typed Boolean by it turned 1 into True, -1, where the code compared it with a 0/1 side number (Eval_KRKP, one position in 200 000).  A negated comparison is an Integer; a double negation is folded away earlier and stays a comparison.

**How far it goes:** tests_wip/gh_chess at depth 13, perft and eight searches, `out.txt` the same as the original's, byte for byte, 4.4 s against 2.4 s.  The differences found here that the suite and DarwinBots had never shown came from registers holding values across stores and calls, from low bytes, and from shared jumps: all things the compiler only does with OptimizationType=0 and checks removed.

## A program with UserControls: from the 424 at load to the census (2026-11-05)

- *`Nothing` as a default.*  `PropBag.ReadProperty("Pic", Nothing)` is a Variant by value, tag 9, value 0, printed 0: `Set m40 = 0` was the 424 that stopped the build, since VB6 runs a UserControl's code when it loads a project.  The Variant is built in a frame slot and its 16 bytes copied, so the 0 may only be in `lf.known` (`calls.byval_variant`, `lift.variant_arg`).
- *Static blocks, once more, in classes and forms.*  The pointer is at `public size - 8` there too (`decompile.static_slots`: cZipArchive +0BCh of 0C4h, t87nested cP +0E8h of 0F0h, and a form and a class of a real program).  The guess failed on that class and the whole block printed as fields of the pointer's own member (`.f0.f30`).
- *Fixed arrays of a class come from its tables.*  `Model.object_array_slots`, as `global_array_slots` for modules: a class's arrays of two and of seven dimensions, and one that only goes to CopyMemory.  Table entries start on a dword (`records.table_entries` read the second array two bytes off).  A member descriptor's own address stands for the array (`map_statics`): `Proc m10010` was `(m10004)`.
- *Shared runtime calls.*  `lea edx, [tmp]; lea ecx, [v74]; jmp` to the `call __vbaVarMove` of an earlier statement: registers across a join holding addresses of frame slots, which printed `t_ecx = t_edx` and lost `v = v + 1`.  `cfg.split_register_joins` gives each way in its own call (with a `lea` before it that doesn't depend on the way in).  The third form of the same habit, after `split_flag_branches` and the tails.
- *An offset slot whose variable changes elsewhere* (`o = v30 - lb`, `v30 = v30 + 1` next time round): `fold_offset_slots` replaces reads where nothing has changed since, by forward flow.  What's left: `fold_dyn_leftovers` prints `a(o + LBound(a))` and `LBound(a)`, always right, if not pretty.
- *`[slot + pvData + 1]`* with `(i - lb) * 3` in the slot lifts as an array of the slot's own; inlined it is `Index(BinOp, ...)`: `refold_dyn_index` looks at the sum (RGB triples).
- *`__vbaVarLateMemCallSt`* (obj, &name, argc, arguments, value): `v44.SubItems(1) = x` on a late bound object.
- *A byte's False in the first block* is no prologue zero-fill: `missing_slots` made `.Visible = False` `.Visible =` (`Const` with a vbtype stays).
- *A read alone is no statement.*  Once what used a value has gone, `v28(i)` or `Me.ListView1.ListItems` print nothing.
- *A counter is no String.*  `x = x + c` says nothing about a pooled slot's Strings elsewhere: `Types.counters` and `writer.global_types` drop String for a variable counted like that, and `For` counters stop being typed String.
- *An App temp is no Frame.*  `web_object_type` looks up a pooled local's source by the interface last set in the same block, so a slot reused for App and for a Frame gets the right one in each place (`rename_block_temps` carries it over).
- *`__vbaVar2Vec`'s Variant is what the caller gave.*  Decoded as a value (`Empty = StrConv(...)`) the destination slot looked like a literal Empty; do_runtime keeps argument 0 plain for it, as for `varres` and `varset`.
- *A Variant around a variable means a Variant parameter.*  `mov [ebp-6Ch], offset global; mov [ebp-74h], 4003h; push &[ebp-74h]; call Proc`: VB builds that (VT_BYREF | vt) only for a ByRef Variant parameter; a parameter of another type would get the variable itself, or a converted copy.  `lift.through_variant` marks the `&var` `by_ref` makes of it (`merged_byref_arg` too), `Types.collect` records `wrapped_params`, and that beats `not_variant_params`, the rule that says one caller's typed variable is against a Variant.  `Proc_401000(a1 As String...)` got a Long by reference from one caller and a String from another.
- *A slot given to a call is no longer known.*  `Dim v24; Proc_401000 ..., v24; If v24 = True` printed `If Empty = True`: the zero the frame was filled with is a known tag 0, and `variant_arg(empty_ok=True)` reads a tag 0 as the literal.  `do_call` forgets a tag of 0 or 1 for each frame address that a procedure, method or Declare received.  This one compiled, so only reading the output found it.
