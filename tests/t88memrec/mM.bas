Attribute VB_Name = "mM"
Option Explicit

Public Sub Out(s As String)
    Dim f As Integer
    f = FreeFile
    Open "out.txt" For Append As #f
    Print #f, s
    Close #f
End Sub

Sub Main()
    Dim c As New cT
    c.Fill "abc", 7
    Out "len " & c.AnsiLen
    Out "tip " & c.Tip
    Out "n " & c.Count
    c.Change 513
    Out "n " & c.Count
    Out "first " & c.First
    c.CopyFrom c
    Out "tip " & c.Tip
    Out "n " & c.Count
End Sub
