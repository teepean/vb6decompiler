Attribute VB_Name = "mMain"
Option Explicit
Sub Main()
    Dim v As Variant, w As Variant, o As Object, d As Double
    v = 42: w = "s": d = 1.5
    Set o = New Collection
    o.Add 7
    Open App.Path & "\out.txt" For Output As #1
    Print #1, v; w; 3
    Print #1, "a"; v; o.Count; d; w
    Print #1, o.Item(1); v
    Close #1
End Sub
