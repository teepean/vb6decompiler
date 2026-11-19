Attribute VB_Name = "mM"
Option Explicit

Function Half(ByVal v As Variant) As Variant
    Half = v / CDec(2)
End Function

Sub Main()
    Dim v As Variant, w As Variant, s As String, l As Long, d As Double, c As Currency
    Open App.Path & "\out.txt" For Output As #1
    v = CDec("1234567890.123456789")
    Print #1, v
    Print #1, TypeName(v); VarType(v)
    Print #1, v * 2
    Print #1, v + CDec(1)
    Print #1, CDec(1) / CDec(3)
    Print #1, CDec(0.1) + CDec(0.2) = CDec(0.3); 0.1 + 0.2 = 0.3
    l = 7: d = 2.5: c = 1.25: s = "3.75"
    Print #1, CDec(l); CDec(d); CDec(c); CDec(s)
    w = CDec(l) * CDec(d)
    Print #1, w; TypeName(w)
    Print #1, Half(CDec(5))
    Print #1, Abs(CDec(-3.5)); Fix(CDec(-3.5)); Int(CDec(-3.5)); Sgn(CDec(-3.5))
    Print #1, CDec(5) > CDec(4); CDec(5) < 4
    s = CStr(CDec("0.000000000000000000000000001"))
    Print #1, s
    d = CDbl(CDec("1.5")) + 1
    Print #1, d
    l = CLng(CDec("41.6"))
    Print #1, l
    v = CDec("79228162514264337593543950335")
    Print #1, v
    On Error Resume Next
    w = v + CDec(1)
    Print #1, Err.Number
    Err.Clear
    w = CDec("not a number")
    Print #1, Err.Number
    Close #1
End Sub
