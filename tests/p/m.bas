Attribute VB_Name = "m"
Option Explicit
Public gX As Long

Public Function AddL(ByVal a As Long, ByVal b As Long) As Long
    AddL = a + b
End Function

Sub Main()
    gX = AddL(2, 3)
    Dim fOut As Integer
    fOut = FreeFile
    Open App.Path & "\out.txt" For Output As #fOut
    Print #fOut, CStr(gX)
    Close #fOut
End Sub
