Attribute VB_Name = "mM"
Option Explicit

Function ByValP(ByVal a As IA) As String
    ByValP = a.Speak(2)
End Function

Function ByRefP(a As IA) As Long
    ByRefP = a.Legs
End Function

Sub Main()
    Dim a As IA
    Open App.Path & "\out.txt" For Output As #1
    Set a = New cD
    Print #1, ByValP(a); ByRefP(a); ByValP(New cD)
    Close #1
End Sub
