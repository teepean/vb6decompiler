Attribute VB_Name = "mM"
Option Explicit

' Optimised code: how Currency Functions and small records come back in edx:eax,
' and what tells them apart.
Public Type TVec
    x As Single
    y As Single
End Type

Public Type TLL
    a As Long
    b As Long
End Type

Public Type TDbl
    d As Double
End Type

Public Type TCy
    c As Currency
End Type

Public Function CyIf(ByVal a As Long) As Currency
    If a > 3 Then
        CyIf = 1.5@
    Else
        CyIf = a
    End If
End Function

Public Function CyLoop(ByVal n As Long) As Currency
    Dim i As Long
    For i = 1 To n
        CyLoop = CyLoop + i * 0.25@
    Next
End Function

Public Function CyConst() As Currency
    CyConst = 15
End Function

Public Function CyAdd(a As Currency, b As Currency) As Currency
    CyAdd = a + b
End Function

Public Function CyByVal(ByVal a As Currency) As Currency
    CyByVal = a
End Function

Public Function CyLocal(ByVal a As Long) As Currency
    Dim c As Currency, d As Currency
    c = a
    d = c * 2
    If a > 2 Then d = d + c
    CyLocal = d
End Function

Public Function LLConst() As TLL
    LLConst.a = 1
    LLConst.b = 2
End Function

Public Function LLZero() As TLL
End Function

Public Function LLOne(ByVal a As Long) As TLL
    LLOne.b = a
End Function

Public Function LLIf(ByVal a As Long) As TLL
    If a > 3 Then
        LLIf.a = a
    Else
        LLIf.b = a
    End If
End Function

Public Function LLFrom(p As TLL) As TLL
    LLFrom.a = p.b
    LLFrom.b = p.a
End Function

Public Function LLCopy(p As TLL) As TLL
    Dim t As TLL
    t = p
    t.a = t.a + 1
    LLCopy = t
End Function

Public Function LLStr(s As String) As TLL
    LLStr.a = Len(s)
    LLStr.b = Len(s & "x")
End Function

Public Function VecCalc(ByVal a As Single) As TVec
    VecCalc.x = Sqr(a)
    VecCalc.y = a + 1
End Function

Public Function LongFn(ByVal a As Long) As Long
    LongFn = a + 1
End Function

Sub Main()
    Dim c As Currency, p As TLL, q As TLL, v As TVec, d As TDbl, e As TCy, s As String
    Open App.Path & "\out.txt" For Output As #1
    c = CyIf(2): Print #1, c
    c = CyIf(5): Print #1, c
    Print #1, CyLoop(4)
    Print #1, CyConst()
    c = 2.5@
    Print #1, CyAdd(c, c)
    Print #1, CyByVal(c)
    Print #1, CyLocal(1), CyLocal(3)
    p = LLConst(): Print #1, p.a, p.b
    p = LLZero(): Print #1, p.a, p.b
    p = LLOne(7): Print #1, p.a, p.b
    p = LLIf(7): Print #1, p.a, p.b
    p = LLIf(1): Print #1, p.a, p.b
    p.a = 5: p.b = 9
    q = LLFrom(p): Print #1, q.a, q.b
    q = LLCopy(p): Print #1, q.a, q.b
    s = "abc"
    q = LLStr(s): Print #1, q.a, q.b
    v = VecCalc(16): Print #1, v.x, v.y
    Print #1, LongFn(2)
    Close #1
End Sub
