Attribute VB_Name = "mMain"
Option Explicit

Sub Main()
    RunAll
    Dim fOut As Integer
    fOut = FreeFile
    Open App.Path & "\out.txt" For Output As #fOut
    Print #fOut, CStr(gCounter) & "|" & gName & "|" & CStr(gValue)
    Close #fOut
End Sub
