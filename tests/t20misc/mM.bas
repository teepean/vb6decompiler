Attribute VB_Name = "mM"
Option Explicit

Function Opt3(ByVal i As Integer, n As Long, Optional x As Variant) As String
    If IsMissing(x) Then
        Opt3 = "missing" & i & n
    Else
        Opt3 = "given" & i & n & x
    End If
End Function

Function Opt3b(ByVal i As Integer, n As Long, Optional x As Variant) As Long
    Opt3b = i + n
End Function

Sub Main()
    Dim n As Long, p As String, q As String
    Open App.Path & "\out.txt" For Output As #1
    n = 5
    Print #1, Opt3(1, n); Opt3(2, n, "z"); Opt3b(3, n); Opt3b(4, n, 1)
    p = App.Path & "\a.tmp"
    q = App.Path & "\b.tmp"
    Open p For Output As #2
    Print #2, "x"
    Close #2
    Name p As q
    Dim ia As Long, ib As Integer, ic As String
    Open q For Output As #2
    Print #2, 12; 34; "xy"
    Close #2
    Open q For Input As #2
    Input #2, ia, ib, ic
    Close #2
    Print #1, ia; ib; ic
    Print #1, Len(Dir$(p)); Len(Dir$(q))
    Kill q
    Close #1
End Sub
