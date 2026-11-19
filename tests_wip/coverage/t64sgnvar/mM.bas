Attribute VB_Name = "mM"
Option Explicit

Sub Main()
    Dim a As Variant, l As Long, d As Double, s As Single, i As Integer, b As Byte
    Open App.Path & "\out.txt" For Output As #1
    a = -5.5: l = 3: d = -2.5: s = 1.5: i = -7: b = 9
    Print #1, Sgn(a)
    a = "3"
    Print #1, Sgn(a)
    Print #1, Sgn(l); Sgn(-l); Sgn(0)
    Print #1, Sgn(d); Sgn(s); Sgn(i); Sgn(b)
    Print #1, Sgn(-2.5)
    Close #1
End Sub
