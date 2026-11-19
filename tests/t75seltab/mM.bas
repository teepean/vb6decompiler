Attribute VB_Name = "mM"
Option Explicit

' a record with an unused Integer before a Long and a String: Put writes it
Type TR
    a As Integer
    un As Integer
    b As Long
    s As String * 3
    c As Integer
    c2 As Integer
    d As Long
End Type

Dim r As TR

' a record's first field read as the element: a(i), values over a Byte
Type TF
    a As Long
    b As Integer
    c As Integer
End Type

Dim fa() As TF

' Select Case over sparse small numbers: two tables, index - low guarded unsigned.
' Below the lowest case the index is negative and must reach Case Else.
Function Sparse(ByVal n As Integer) As String
    Dim r As String
    Select Case n
    Case 132: r = "a"
    Case 133: r = "b"
    Case 150: r = "c"
    Case 164: r = "d"
    Case 165: r = "e"
    Case 169: r = "f"
    Case 182: r = "g"
    Case 188: r = "h"
    Case Else: r = "other"
    End Select
    Sparse = r
End Function

Function Dense(ByVal n As Long) As Long
    Select Case n
    Case 10: Dense = 1
    Case 11: Dense = 2
    Case 12: Dense = 3
    Case 13: Dense = 4
    Case 14: Dense = 5
    Case 15: Dense = 6
    Case Else: Dense = -1
    End Select
End Function

' a Variant built around a ByRef Integer in calls VB merged into one
Sub Show(a As Variant, b As Integer, Optional c As Variant)
    If IsMissing(c) Then
        Print #1, "show", CStr(a), b, "missing"
    Else
        Print #1, "show", CStr(a), b, CStr(c)
    End If
End Sub

Sub Merged(ByVal which As Integer, k As Integer)
    Dim s As String
    s = CStr(which)
    If which = 1 Then
        Show k, k, s
    ElseIf which = 2 Then
        Show k, k, s
    Else
        Show k, which, k
    End If
    k = k + 1
End Sub

Sub Handler(ByVal n As Long)
    On Error GoTo bad
    Err.Raise n
    Exit Sub
bad:
    Print #1, "err", Error$(Err), Err.Number
    Print #1, "err2", Error$, Err.Number
    Print #1, "err3", Error$(5)
    Resume Next
End Sub

' a compare's carry as a value (VB: x = y And a < b on Bytes)
Function Carry(ByVal a As Byte, ByVal b As Byte, ByVal c As Byte, ByVal d As Byte) As Integer
    If c = d And a < b Then
        Carry = 1
    ElseIf a < b Then
        Carry = 2
    Else
        Carry = 3
    End If
End Function

Sub Rec()
    Dim q As TR
    r.a = 1: r.b = 2: r.s = "abc": r.c = 3: r.c2 = 5: r.d = 4
    Open "rec.dat" For Random As #2 Len = 19
    Put #2, 1, r
    Get #2, 1, q
    Close #2
    Print #1, q.a, q.b, q.s, q.c, q.c2, q.d, FileLen("rec.dat")
End Sub

' a Variant around a variable, read in a later block than it was built (the
' auto-instanced Collection test lies between)
Function ByVar(ByVal k As Integer, s As String) As String
    Dim c As Collection
    Set c = New Collection
    c.Add "first", "1"
    c.Add "second", "2"
    If k > 0 Then
        ByVar = c(k) & Left(s, k)
    Else
        ByVar = c(1) & Left(s, 1)
    End If
End Function

Sub FirstField(ByVal n As Long)
    Dim i As Integer
    ReDim fa(1 To 3)
    For i = 1 To 3
        fa(i).a = n * i
        fa(i).c = i
    Next i
    If fa(2).a > 70000 And fa(2).c = 2 Then Print #1, fa(2).a, fa(3).a, fa(1).c
End Sub

' Select Case on a Double: its dwords compared one at a time
Function SelD(ByVal s As String) As String
    Select Case Val(s)
    Case 0: SelD = "zero"
    Case 1: SelD = "one"
    Case 2: SelD = "two"
    Case 3: SelD = "three"
    Case 2.5: SelD = "half"
    Case Else: SelD = "else"
    End Select
End Function

' Single literals: bounds of a Single For, a literal for a ByRef Single
Sub TakeS(k As Integer, x As Single)
    Print #1, k, Format$(x, "0.00")
End Sub

Sub SingleLoops()
    Dim s As Single, n As Long, a(0 To 70) As Long
    For s = 32 To 41
        n = n + 1
    Next s
    Print #1, n, s
    For s = 0.5 To 3 Step 0.5
        a(CLng(s * 2)) = a(CLng(s * 2)) + 1
    Next s
    s = 64
    a(CLng(s)) = 7
    Print #1, a(64), a(1), a(6)
    TakeS 1, 0.6
    TakeS 2, -1.5
End Sub

Sub Main()
    Dim i As Integer, k As Integer
    Open "out.txt" For Output As #1
    For i = 128 To 192 Step 2
        Print #1, i, Sparse(i), Sparse(i + 1)
    Next i
    Print #1, Sparse(-5), Sparse(0)
    For i = -2 To 18
        Print #1, i, Dense(CLng(i))
    Next i
    k = 16
    Merged 1, k
    Merged 2, k
    Merged 3, k
    Print #1, k
    Print #1, Carry(1, 2, 3, 3), Carry(1, 2, 3, 4), Carry(2, 1, 3, 3), Carry(2, 1, 3, 4)
    Rec
    SingleLoops
    Print #1, SelD("0"), SelD("1"), SelD("2"), SelD("3"), SelD("2.5"), SelD("7")
    FirstField 70000
    Print #1, ByVar(2, "abcd"), ByVar(0, "wxyz")
    Handler 53
    Handler 9
    Close #1
End Sub
