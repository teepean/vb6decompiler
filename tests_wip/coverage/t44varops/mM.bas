Attribute VB_Name = "mM"
Option Explicit

Sub Main()
    Dim a As Variant, b As Variant, l As Long, s As String, t As String, r As Variant, f As Boolean
    Dim x(1) As Boolean
    Open App.Path & "\out.txt" For Output As #1
    a = True: b = False
    Print #1, a Eqv b; a Eqv a; a Imp b; b Imp a
    a = 12: b = 10
    Print #1, a Eqv b; a Imp b; 12& Eqv 10&; 12& Imp 10&
    l = 12
    Print #1, l Eqv 10; l Imp 10
    f = (l > 1) Imp (l < 0)
    x(1) = (l > 1) Eqv (l > 2)
    Print #1, f; x(1)
    r = a ^ b
    Print #1, r; TypeName(r); a ^ 2; 2 ^ b; l ^ 2; l ^ 0.5; a ^ -1
    a = "abc": b = "a*"
    Print #1, a Like b; a Like "a?c"; a Like "[x-z]*"; a Like "*C"
    s = "abc": t = "*b*"
    Print #1, s Like t; s Like "ab#"; s Like a; a Like t
    f = s Like "[!x]bc"
    Print #1, f
    a = -5.5
    Print #1, Sgn(a); Sgn(l); Sgn(-l); Sgn(0); Sgn(-2.5)
    b = "3"
    Print #1, Sgn(b)
    Print #1, Switch(l = 1, "one", l = 12, "twelve", True, "other")
    Print #1, IsNull(Switch(l = 1, "one", l = 2, "two"))
    r = Switch(a > 0, 1, a < 0, -1)
    Print #1, r
    Print #1, Choose(2, "a", "b", "c"); IsNull(Choose(9, "a"))
    Print #1, IIf(l > 1, "big", "small"); IIf(l < 1, 1, 2.5)
    Print #1, StrComp("abc", "ABC"); StrComp("abc", "ABC", vbTextCompare); StrComp(a, b); StrComp(s, "abd", vbBinaryCompare)
    Print #1, VarType(a); VarType(s); VarType(l); VarType(f); VarType(Null); VarType(Empty)
    Close #1
End Sub
