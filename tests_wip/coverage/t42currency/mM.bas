Attribute VB_Name = "mM"
Option Explicit

Type TAcc
    bal As Currency
    nm As String
End Type

Const FEE As Currency = 1.5

Function Twice(ByVal c As Currency) As Currency
    Twice = c * 2
End Function

Sub Inc(c As Currency, ByVal by As Currency)
    c = c + by
End Sub

Sub Main()
    Dim c As Currency, e As Currency, l As Long, i As Integer, b As Byte, sg As Single
    Dim d As Double, s As String, v As Variant, f As Boolean, a(2) As Currency, u As TAcc, dt As Date
    Open App.Path & "\out.txt" For Output As #1
    l = 123456: i = -321: b = 200: sg = 1.25: d = 9.87654: s = "42.125": v = "7.5": f = True
    c = CCur(l): Print #1, c
    c = CCur(i): Print #1, c
    c = CCur(b): Print #1, c
    c = CCur(sg): Print #1, c
    c = CCur(d): Print #1, c
    c = CCur(s): Print #1, c
    c = CCur(v): Print #1, c
    c = CCur(f): Print #1, c
    c = l: Print #1, c
    c = i: Print #1, c
    c = b: Print #1, c
    c = sg: Print #1, c
    c = d: Print #1, c
    c = s: Print #1, c
    c = v: Print #1, c
    c = 1234.5678
    l = c: Print #1, l
    i = c: Print #1, i
    b = CCur(99.5): Print #1, b
    sg = c: Print #1, sg
    d = c: Print #1, d
    s = c: Print #1, s
    v = c: Print #1, v; TypeName(v); VarType(v)
    f = c: Print #1, f
    Print #1, CLng(c); CInt(c); CByte(CCur(255)); CSng(c); CDbl(c); CStr(c); CBool(c)
    e = -2.5
    Print #1, c + e; c - e; c * e; c / e; c \ 3; c Mod 7; -c; c ^ 2
    Print #1, Abs(e); Fix(e); Int(e); Sgn(e); Sgn(c); Sgn(CCur(0))
    Print #1, c > e; c < e; c = e; c <> e; c = 1234.5678
    Print #1, c + 1; c * 2; c + l; c + 0.5; c * 1.5; c + sg
    Print #1, CVar(c) + 1; TypeName(CVar(c) * 2); TypeName(c + 1); TypeName(c * 1.5); TypeName(c / 2)
    Print #1, Twice(e); Twice(1.25); Twice(l)
    Inc c, 0.0001
    Print #1, c
    Inc a(1), e
    Inc a(1), FEE
    Print #1, a(0); a(1); a(2)
    u.bal = 10
    Inc u.bal, 0.25
    u.bal = u.bal * 3
    Print #1, u.bal; CStr(u.bal)
    Print #1, Format$(c, "0.00"); Format(e); Format$(u.bal, "#,##0.0000")
    Print #1, Round(c, 2); Round(CCur(2.5)); Round(CCur(3.5))
    Print #1, CCur("900000000000000.5")
    dt = CDate(CCur(36526.5))
    Print #1, CDbl(dt); CCur(dt); IsNumeric(c); Str$(e); Val(Str$(e))
    On Error Resume Next
    c = CCur("900000000000000")
    c = c * 2
    Print #1, Err.Number
    Err.Clear
    c = CCur("zzz")
    Print #1, Err.Number
    Close #1
End Sub
