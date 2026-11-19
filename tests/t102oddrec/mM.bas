Attribute VB_Name = "mM"
Option Explicit

' Put's descriptor gives Len(), which counts no alignment: this Type is Len 9 and 12 bytes in memory.  An odd size
' said no Long could be there: n came out two Bytes and an Integer, and a colour in it overflowed.
Type TOdd
    a As Byte
    b As Byte
    c As Byte
    n As Long
    e As Byte
    f As Byte
End Type

Public gR As TOdd
Public gS As TOdd

Sub Main()
    Dim b() As Byte, i As Long, s As String
    On Error GoTo bad
    Open App.Path & "\out.txt" For Output As #1
    gR.a = 1
    gR.b = 2
    gR.c = 3
    gR.n = RGB(1, 2, 128)
    gR.e = 5
    gR.f = 6
    Print #1, gR.n; Len(gR); LenB(gR)
    Open App.Path & "\rec.bin" For Random As #2 Len = 20
    Put #2, 1, gR
    Put #2, 2, gR
    Get #2, 2, gS
    Close #2
    Print #1, gS.a; gS.b; gS.c; gS.n; gS.e; gS.f
    Open App.Path & "\rec.bin" For Binary As #2
    ReDim b(LOF(2) - 1)
    Get #2, , b
    Close #2
    For i = 0 To UBound(b)
        s = s & Hex$(b(i)) & " "
    Next
    Print #1, s
    Kill App.Path & "\rec.bin"
    Close #1
    Exit Sub
bad:
    Print #1, "error"; Err.Number; Err.Description
    Close #1
End Sub
