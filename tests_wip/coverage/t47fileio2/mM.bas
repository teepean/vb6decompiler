Attribute VB_Name = "mM"
Option Explicit

Sub Main()
    Dim p As String, a As Long, b As String, c As Double, d As Boolean, s As String, v As Variant, n As Long
    Dim m As Variant, by() As Byte, t As Variant
    Open App.Path & "\out.txt" For Output As #1
    p = App.Path & "\w.tmp"
    Open p For Output As #2
    Write #2, 12, "two words", 3.5, True
    Write #2,
    Write #2, "x"; 7
    a = 9: b = "b""q": c = -1.25
    Write #2, a, b, c
    Close #2
    Open p For Input As #2
    Line Input #2, s: Print #1, s
    Line Input #2, s: Print #1, "["; s; "]"
    Line Input #2, s: Print #1, s
    Line Input #2, s: Print #1, s
    Seek #2, 1
    Input #2, a, b, c, d
    Print #1, a; b; c; d
    Close #2
    Open p For Input As #2
    s = Input(5, #2)
    Print #1, "["; s; "]"
    n = 4
    v = Input(n, 2)
    Print #1, "["; v; "]"; TypeName(v)
    s = Input$(3, #2)
    Print #1, "["; s; "]"; Loc(2); LOF(2)
    Close #2
    Open p For Binary As #2
    s = InputB(4, #2)
    Print #1, Len(s); LenB(s); AscB(s)
    Close #2
    Open p For Random Shared As #2 Len = 8
    Lock #2
    Unlock #2
    Lock #2, 1
    Unlock #2, 1
    Lock #2, 2 To 3
    Unlock #2, 2 To 3
    Print #1, "locked ok"; LOF(2)
    Close #2
    m = "abcdef"
    Mid$(m, 2, 2) = "XY"
    Print #1, m
    Mid(m, 5) = "ZZZZZ"
    Print #1, m; TypeName(m)
    s = "abcdef"
    MidB$(s, 3, 2) = "Q"
    Print #1, s
    t = "hello"
    Print #1, LenB(t); InStrB(t, "l"); InStrB(s, "c"); InStrB(3, t, "l"); LeftB$(t, 2); RightB$(t, 2)
    Close #1
End Sub
