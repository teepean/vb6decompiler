Attribute VB_Name = "mM"
Option Explicit

Sub Main()
    Dim d As cD, a As IA
    Open App.Path & "\out.txt" For Output As #1
    Set d = New cD
    Set a = d
    Print #1, a.Speak(1); a.Legs; d.Own()
    Close #1
End Sub
