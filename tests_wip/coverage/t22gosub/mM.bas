Attribute VB_Name = "mM"
Option Explicit

Function SumTo(ByVal n As Long) As Long
    Dim i As Long, acc As Long
    For i = 1 To n
        GoSub AddIt
    Next i
    SumTo = acc
    Exit Function
AddIt:
    acc = acc + i
    Return
End Function

Sub Nested(ByVal n As Long, s As String)
    Dim d As Long
    s = ""
    d = n
    GoSub L1
    s = s & "end" & d
    Exit Sub
L1:
    s = s & "a"
    d = d * 2
    GoSub L2
    s = s & "b"
    Return
L2:
    s = s & "c"
    d = d + 1
    Return
End Sub

Function Cond(ByVal k As Long) As String
    Dim t As String
    t = "s"
    If k > 2 Then GoSub Big Else GoSub Small
    Cond = t & k
    Exit Function
Big:
    t = t & "B"
    If k > 5 Then
        t = t & "!"
        Return
    End If
    t = t & "m"
    Return
Small:
    t = t & "S"
    Return
End Function

Function Twice(ByVal x As Double) As Double
    Dim r As Double, s As String, v As Variant
    r = x
    GoSub Dbl
    GoSub Dbl
    s = CStr(r)
    v = r + 1
    GoSub Fin
    Twice = v
    Exit Function
Dbl:
    r = r * 2
    Return
Fin:
    v = v + Len(s)
    Return
End Function

Function ExitInside(ByVal k As Long) As String
    ExitInside = "start"
    GoSub Chk
    ExitInside = "after" & k
    Exit Function
Chk:
    If k = 1 Then Exit Function
    Return
End Function

Function Fact(ByVal n As Long) As Long
    Dim m As Long
    If n <= 1 Then
        Fact = 1
        Exit Function
    End If
    GoSub Rec
    Fact = m * n
    Exit Function
Rec:
    m = Fact(n - 1)
    Return
End Function

Function WithErr(ByVal k As Long) As String
    Dim z As Long
    On Error GoTo bad
    GoSub Div
    WithErr = "ok" & z
    Exit Function
Div:
    z = 10 \ k
    Return
bad:
    WithErr = "err" & Err.Number
End Function

Sub Main()
    Dim s As String
    Open App.Path & "\out.txt" For Output As #1
    Print #1, "SumTo"; SumTo(5); SumTo(0)
    Nested 3, s
    Print #1, s
    Print #1, Cond(1); Cond(3); Cond(7)
    Print #1, Twice(1.5)
    Print #1, ExitInside(0); ExitInside(1)
    Print #1, Fact(5)
    Print #1, WithErr(2); WithErr(0)
    Close #1
End Sub
