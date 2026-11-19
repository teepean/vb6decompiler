Attribute VB_Name = "mM"
Option Explicit

Sub Main()
    Dim a As cSrc, b As cSrc, l1 As cLis, l2 As cLis
    Open App.Path & "\out.txt" For Output As #1
    Set a = New cSrc
    Set b = New cSrc
    Set l1 = New cLis
    Set l2 = New cLis
    l1.Attach a
    l2.Attach a
    a.Fire 2
    Print #1, l1.Log; " "; l2.Log
    Print #1, a.Fan(); l1.Log
    Print #1, a.Query("yes"); a.Query("no"); l1.Log
    l2.Attach b
    a.Fire 5
    b.Fire 6
    Print #1, l1.Log; " "; l2.Log
    Close #1
End Sub
