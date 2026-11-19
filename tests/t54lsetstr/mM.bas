Attribute VB_Name = "mM"
Option Explicit

Type TO1
    f As String * 3
    v As String
End Type

Public gS As String
Public gF As String * 6

Sub Fill(s As String, ByVal t As String)
    LSet s = t
End Sub

Sub Main()
    Dim s As String, f As String * 5, a(1) As String, r As TO1, v As Variant
    Open App.Path & "\out.txt" For Output As #1
    s = "abcdef"
    LSet s = "xy"
    Print #1, "["; s; "]"
    LSet s = "longer than six"
    Print #1, "["; s; "]"
    f = "12345"
    LSet f = "a"
    Print #1, "["; f; "]"
    LSet f = "toolongvalue"
    Print #1, "["; f; "]"
    a(0) = "....": a(1) = "...."
    LSet a(1) = "q"
    Print #1, "["; a(0); "]["; a(1); "]"
    r.f = "xxx": r.v = "yyyy"
    LSet r.f = "1"
    LSet r.v = "2"
    Print #1, "["; r.f; "]["; r.v; "]"
    gS = "01234": gF = "ABCDEF"
    LSet gS = "m" & "n"
    LSet gF = "m" & "n"
    Print #1, "["; gS; "]["; gF; "]"
    s = "4 chars"
    Fill s, "t"
    Print #1, "["; s; "]"
    v = 12
    s = "abcd"
    LSet s = v
    Print #1, "["; s; "]"
    Close #1
End Sub
