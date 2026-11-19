Attribute VB_Name = "mM"
Option Explicit

Private gLine As String

Sub Main()
    Dim h As cH, va(1) As Variant
    Open App.Path & "\out.txt" For Output As #1
    Open App.Path & "\in.tmp" For Output As #2
    Print #2, "one"
    Print #2, "two"
    Print #2, "three"
    Close #2
    Open App.Path & "\in.tmp" For Input As #2
    Line Input #2, gLine
    Print #1, gLine
    Set h = New cH
    Print #1, h.ReadOne(2)
    Line Input #2, va(1)
    Print #1, va(1)
    Close #2
    Close #1
End Sub
