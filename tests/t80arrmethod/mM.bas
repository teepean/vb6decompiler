Attribute VB_Name = "mM"
Option Explicit

' A local dynamic array filled by a class method, then read element by element: the array's
' only declaration is the call (a registry class's EnumerateSections(sSect() As String, n)).
Sub Lue(c As cR)
    Dim a() As String
    Dim n As Long
    Dim i As Long
    Dim ok As Boolean
    ok = c.Tayta(a, n)
    If n > 0 Then
        For i = 1 To n
            Print #1, i, a(i), Len(a(i))
        Next i
    End If
    If ok Then Print #1, UCase$(a(1))
    ok = c.Tayta(a, n)
    For i = n To 1 Step -1
        Print #1, a(i)
    Next i
End Sub

' The same with On Error Resume Next: the offset goes through frame slots between the bounds
' check and the element (seen in a real program)
Sub Lue2(c As cR)
    Dim a() As String
    Dim n As Long
    Dim i As Long
    Dim s As String
    On Error Resume Next
    i = c.Tayta(a, n)
    For i = 1 To n
        s = UCase$(a(i)) & " " & i
        Print #1, s, c.Tayta2(a(i), " x")
    Next i
End Sub

Sub Main()
    Dim c As New cR
    Open "out.txt" For Output As #1
    Lue c
    Lue2 c
    Close #1
End Sub
