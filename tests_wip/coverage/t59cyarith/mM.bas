Attribute VB_Name = "mM"
Option Explicit

Function Twice(ByVal c As Currency) As Currency
    Twice = c * 2
End Function

Sub Inc(c As Currency, ByVal by As Currency)
    c = c + by
End Sub

Sub Main()
    Dim c As Currency, e As Currency, l As Long, d As Double, v As Variant, s As String, a(1) As Currency
    Open App.Path & "\out.txt" For Output As #1
    l = 123456: d = 9.87654
    c = l: Print #1, c
    c = d: Print #1, c
    c = CCur(l) + CCur(d): Print #1, c
    c = 1234.5678
    e = -2.5
    l = c: Print #1, l
    d = c: Print #1, d
    s = c: Print #1, s
    v = c: Print #1, v; TypeName(v)
    Print #1, c + e; c - e; c * e; c / e; -c
    Print #1, c > e; c < e; c = e; c <> e
    Print #1, Twice(e); Twice(1.25); Twice(l)
    Inc c, 0.0001
    Print #1, c
    Inc a(1), e
    Print #1, a(0); a(1)
    Close #1
End Sub
