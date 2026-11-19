Attribute VB_Name = "mM"
Option Explicit

Type TLong
    x As Long
End Type
Type TBytes
    b(3) As Byte
End Type
Type TTwo
    lo As Integer
    hi As Integer
End Type
Type TStr
    s As String * 4
    n As Long
End Type
Type TOuter
    f As String * 3
    v As String
End Type

Public gS As String
Public gF As String * 6

Sub Fill(s As String, ByVal t As String)
    LSet s = t
End Sub

Sub Main()
    Dim s As String, f As String * 5, a(1) As String, r As TOuter, fa(1) As String * 3
    Dim tl As TLong, tb As TBytes, t2 As TTwo, ts1 As TStr, ts2 As TStr, c As cL
    Dim tla(1) As TLong, v As Variant
    Open App.Path & "\out.txt" For Output As #1
    s = "abcdef"
    LSet s = "xy"
    Print #1, "["; s; "]"
    RSet s = "xy"
    Print #1, "["; s; "]"
    LSet s = "longer than six"
    Print #1, "["; s; "]"
    f = "12345"
    LSet f = "a"
    Print #1, "["; f; "]"
    RSet f = "b"
    Print #1, "["; f; "]"
    LSet f = "toolongvalue"
    Print #1, "["; f; "]"
    RSet f = "toolongvalue"
    Print #1, "["; f; "]"
    a(0) = "....": a(1) = "...."
    LSet a(0) = "q"
    RSet a(1) = "q"
    Print #1, "["; a(0); "]["; a(1); "]"
    r.f = "xxx": r.v = "yyyy"
    LSet r.f = "1"
    RSet r.v = "2"
    Print #1, "["; r.f; "]["; r.v; "]"
    fa(1) = "abc"
    LSet fa(1) = "Z"
    Print #1, "["; fa(1); "]"
    gS = "01234": gF = "ABCDEF"
    LSet gS = "m" & "n"
    RSet gF = "m" & "n"
    Print #1, "["; gS; "]["; gF; "]"
    s = "4 chars"
    Fill s, "t"
    Print #1, "["; s; "]"
    v = 12
    s = "abcd"
    LSet s = v
    Print #1, "["; s; "]"
    RSet s = 5 + 6
    Print #1, "["; s; "]"
    tl.x = &H1020304
    LSet tb = tl
    Print #1, tb.b(0); tb.b(1); tb.b(2); tb.b(3)
    LSet t2 = tl
    Print #1, t2.lo; t2.hi
    tb.b(3) = 127
    LSet tl = tb
    Print #1, tl.x
    tla(1).x = 65537
    LSet t2 = tla(1)
    Print #1, t2.lo; t2.hi
    ts1.s = "wxyz": ts1.n = 9
    LSet ts2 = ts1
    Print #1, ts2.s; ts2.n
    Set c = New cL
    Print #1, c.Go("hi")
    Close #1
End Sub
