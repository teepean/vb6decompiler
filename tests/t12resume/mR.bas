Attribute VB_Name = "mR"
Option Explicit
Public n As Long
Sub RNext()
    On Error GoTo h
    Err.Raise 6
    Exit Sub
h:
    n = 7
    Resume Next
End Sub
Sub RSame()
    Dim z As Long
    On Error GoTo h
    n = 6 \ z
    Exit Sub
h:
    z = 2
    Resume
End Sub
Sub RLabel()
    On Error GoTo h
    Err.Raise 5
q:
    n = 9
    Exit Sub
h:
    Resume q
End Sub
Sub Main()
    Open App.Path & "\out.txt" For Output As #1
    n = 0: Call RNext: Print #1, "next"; n
    n = 0: RSame: Print #1, "same"; n
    n = 0: RLabel: Print #1, "label"; n
    Close #1
End Sub
