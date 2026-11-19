Attribute VB_Name = "mV"
Option Explicit
Public total As Long

Sub ConstLoops()
    Dim v As Variant
    For v = 2 To 9 Step 3
        Print #1, "up"; v
    Next v
    For v = 10 To 1 Step -2
        Print #1, "down"; v
    Next v
    For v = 1 To 5
        If v = 4 Then Exit For
        Print #1, "exit"; v
    Next v
    Print #1, "after"; v
End Sub

Sub VarLoops(ByVal a As Long, ByVal b As Long, ByVal s As Long)
    Dim v As Variant, w As Variant
    For v = a To b Step s
        For w = 1 To v
            total = total + w
        Next w
    Next v
    Print #1, "total"; total
End Sub

Sub SingleLoops(ByVal top As Single)
    Dim x As Single, d As Single
    For x = 1.5! To 6! Step 1.5!
        Print #1, "s"; x
    Next x
    d = -0.5!
    For x = top To 1! Step d
        Print #1, "sd"; x
    Next x
End Sub

Sub FixedStrings()
    Dim s As String * 10, t As String * 10, n As String * 4
    Dim f As Integer
    f = FreeFile
    Open App.Path & "\rec.dat" For Random As #f Len = 14
    s = "abcdefghij"
    n = "wxyz"
    Put #f, 1, s
    Put #f, 2, n
    Get #f, 1, t
    Print #1, "get1 ["; t; "]"
    Get #f, 2, t
    Print #1, "get2 ["; t; "]"
    Close #f
    Print #1, "len"; FileLen(App.Path & "\rec.dat")
    Print #1, "str ["; Str$(total); "]"
    Kill App.Path & "\rec.dat"
End Sub

Sub Main()
    Open App.Path & "\out.txt" For Output As #1
    ConstLoops
    VarLoops 1, 7, 2
    VarLoops 6, 2, -2
    SingleLoops 2.5!
    FixedStrings
    Close #1
End Sub
