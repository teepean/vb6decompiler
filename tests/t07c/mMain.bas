Attribute VB_Name = "mMain"
Option Explicit

Sub Main()
    Dim f As Integer
    RunAll7
    f = FreeFile
    Open App.Path & "\out.txt" For Output As #f
    Print #f, gLog
    Close #f
End Sub
