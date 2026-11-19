Attribute VB_Name = "mM"
Option Explicit

Sub Main()
    Dim l As Long, a As Variant, r As Variant, s As String
    Open App.Path & "\out.txt" For Output As #1
    l = 12: a = -5.5
    Print #1, Switch(l = 1, "one", l = 12, "twelve", True, "other")
    Print #1, IsNull(Switch(l = 1, "one", l = 2, "two"))
    r = Switch(a > 0, 1, a < 0, -1)
    Print #1, r
    s = Switch(l > 0, "pos")
    Print #1, s
    Print #1, Choose(2, "a", "b", "c"); IsNull(Choose(9, "a"))
    Print #1, IIf(l > 1, "big", "small"); IIf(l < 1, 1, 2.5)
    Close #1
End Sub
