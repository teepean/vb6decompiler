Attribute VB_Name = "mM"
Option Explicit

Sub Main()
    Dim o As Object, v As Variant, cl As String, s As String
    Open App.Path & "\out.txt" For Output As #1
    On Error Resume Next
    Set o = GetObject(, "NoSuch.ClassName")
    Print #1, Err.Number; o Is Nothing
    Err.Clear
    cl = "NoSuch.Other"
    Set o = GetObject("", cl)
    Print #1, Err.Number; o Is Nothing
    Err.Clear
    s = App.Path & "\nosuch.xyz"
    Set o = GetObject(s)
    Print #1, Err.Number; o Is Nothing
    Err.Clear
    Set v = GetObject(s, cl)
    Print #1, Err.Number; IsEmpty(v)
    Err.Clear
    On Error GoTo 0
    Set o = GetObject("", "Scripting.Dictionary")
    o.Add "a", 1
    o.Add "b", 2
    Print #1, TypeName(o); o.Count; o.Item("b")
    cl = "Scripting.Dictionary"
    Set v = GetObject("", cl)
    v.Add "x", 5
    Print #1, TypeName(v); v.Count
    Print #1, GetObject("", cl).Count
    Close #1
End Sub
