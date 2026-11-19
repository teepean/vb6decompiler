Attribute VB_Name = "mMain"
Option Explicit

Sub Main()
    RunAll3
    Dim fOut As Integer
    fOut = FreeFile
    Open App.Path & "\out.txt" For Output As #fOut
    Print #fOut, gResult
    Close #fOut
End Sub
