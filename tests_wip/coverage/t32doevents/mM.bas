Attribute VB_Name = "mM"
Option Explicit

Public Function Pump(ByVal k As Long) As Long
    Dim i As Long
    For i = 1 To k
        DoEvents
    Next i
    Pump = DoEvents() + k
End Function
