Attribute VB_Name = "mM"
Option Explicit

Public gs As String
Public gl As Long

Sub Main()
    Dim p As cP, s As String
    Open "out.txt" For Output As #1
    Set p = New cP
    gs = "module"
    gl = 7
    s = "local"
    ' literals: two temporaries 8 bytes apart, a small number and a Boolean
    p.Kirjoita 4, 0, s, True
    p.Kirjoita 4, 0, gs, False
    p.Kirjoita 4, 1, gs
    p.Kirjoita gl, 2, gs, True
    Print #1, gs, s, p.Sis(4, gs), p.Sis(40, s)
    Close #1
End Sub
