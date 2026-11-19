Attribute VB_Name = "mM"
Option Explicit

Sub Main()
    Dim a As IO1
    Open App.Path & "\out.txt" For Output As #1
    Set a = New cO
    Print #1, a.W(1.5); a.W(1.5, 3)
    Close #1
End Sub
