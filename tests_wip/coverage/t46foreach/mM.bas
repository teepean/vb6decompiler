Attribute VB_Name = "mM"
Option Explicit

Function Total(a As Variant) As Double
    Dim e As Variant
    For Each e In a
        Total = Total + e
    Next e
End Function

Function TotalP(ParamArray p() As Variant) As Double
    Dim e As Variant
    For Each e In p
        TotalP = TotalP + e
    Next
End Function

Function FirstBig(a() As Long) As Variant
    Dim e As Variant
    For Each e In a
        If e > 2 Then
            FirstBig = e
            Exit Function
        End If
    Next e
    FirstBig = -1
End Function

Sub Main()
    Dim la(3) As Long, sa(1) As String, da(1, 1) As Double, va As Variant, e As Variant, o As Object
    Dim col As Collection, k As cK, ka(1) As cK, kk As cK, i As Long, ct As Long
    Open App.Path & "\out.txt" For Output As #1
    For i = 0 To 3: la(i) = i * i: Next i
    For Each e In la
        Print #1, e;
    Next e
    Print #1,
    sa(0) = "x": sa(1) = "y"
    For Each e In sa
        Print #1, e;
    Next
    Print #1,
    da(0, 0) = 1.5: da(1, 0) = 2.5: da(0, 1) = 3.5: da(1, 1) = 4.5
    For Each e In da
        Print #1, e;
    Next e
    Print #1,
    va = Array(1, "two", 3.5)
    For Each e In va
        Print #1, e; TypeName(e)
    Next e
    Print #1, Total(Array(1, 2, 3)); TotalP(4, 5, 6); FirstBig(la)
    Set col = New Collection
    For i = 1 To 3
        Set k = New cK
        k.Id = i * 10
        col.Add k
    Next i
    For Each k In col
        ct = ct + k.Id
    Next k
    Print #1, ct
    For Each e In col
        ct = ct + e.Id
    Next e
    Print #1, ct
    For Each o In col
        ct = ct + o.Id
        If ct > 130 Then Exit For
    Next o
    Print #1, ct
    Set ka(0) = col(1): Set ka(1) = col(3)
    For Each e In ka
        Print #1, e.Id;
    Next e
    Print #1,
    Close #1
End Sub
