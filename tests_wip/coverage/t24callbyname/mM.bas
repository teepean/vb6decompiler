Attribute VB_Name = "mM"
Option Explicit

Sub Main()
    Dim o As cT, o2 As cT, x As Object, r As Long, v As Variant, nm As String
    Dim ct As VbCallType, s As String, a(1) As Long
    Open App.Path & "\out.txt" For Output As #1
    Set o = New cT
    Set o2 = New cT
    CallByName o, "V", VbLet, 42
    Print #1, CallByName(o, "V", VbGet)
    r = CallByName(o, "Add", VbMethod, 2, 3)
    Print #1, r
    v = CallByName(o, "Add", VbMethod, r, 30)
    Print #1, v
    s = CallByName(o, "Add", VbMethod, 1, 1)
    Print #1, s & "|"
    nm = "Opt"
    Print #1, CallByName(o, nm, VbMethod, 7)
    Print #1, CallByName(o, nm, VbMethod, 7, 2)
    CallByName o, "Bump", VbMethod
    CallByName o, "Bump", VbMethod
    Print #1, o.V
    CallByName o, "Nm", VbLet, "abc"
    Print #1, o.Nm
    ct = VbGet
    Print #1, CallByName(o, "Nm", ct)
    o2.V = 77
    CallByName o, "Obj", VbSet, o2
    Set x = CallByName(o, "Obj", VbGet)
    Print #1, x.V
    Print #1, CallByName(CallByName(o, "Obj", VbGet), "V", VbGet)
    a(0) = 100: a(1) = 200
    Print #1, CallByName(o, "Add", VbMethod, a(0), a(1)) + 1
    Print #1, CallByName(o, "Cat", VbMethod, 1, "x", 2.5)
    Set x = o
    Print #1, CallByName(x, "V", VbGet)
    On Error Resume Next
    CallByName o, "NoSuch", VbMethod
    Print #1, Err.Number
    Err.Clear
    v = CallByName(o, "V", VbSet, 1)
    Print #1, Err.Number
    Close #1
End Sub
