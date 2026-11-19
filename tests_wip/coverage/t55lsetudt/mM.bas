Attribute VB_Name = "mM"
Option Explicit

Type TLong
    x As Long
End Type
Type TBytes
    b(3) As Byte
End Type
Type TTwo
    lo As Integer
    hi As Integer
End Type

Sub Main()
    Dim tl As TLong, tb As TBytes, t2 As TTwo
    Open App.Path & "\out.txt" For Output As #1
    tl.x = &H1020304
    LSet tb = tl
    Print #1, tb.b(0); tb.b(1); tb.b(2); tb.b(3)
    LSet t2 = tl
    Print #1, t2.lo; t2.hi
    tb.b(3) = 127
    LSet tl = tb
    Print #1, tl.x
    Close #1
End Sub
