Attribute VB_Name = "modT03"
Option Explicit

Public gResult As String

Public Type TItem
    Id As Long
    Price As Double
    Title As String
End Type

Public Function VarMath(ByVal a As Variant, ByVal b As Variant) As Variant
    Dim r As Variant
    r = a + b
    r = r * 2 - a / 4
    If r > 10 Then r = r & " big"
    VarMath = r
End Function

Public Function VarTypes() As String
    Dim v As Variant
    v = Empty
    If IsEmpty(v) Then VarTypes = "e"
    v = Null
    If IsNull(v) Then VarTypes = VarTypes & "n"
    v = 3.5
    VarTypes = VarTypes & TypeName(v) & CStr(VarType(v))
    v = "12"
    If IsNumeric(v) Then VarTypes = VarTypes & CStr(v + 1)
End Function

Public Function ArrayFixed() As Long
    Dim a(1 To 5) As Long
    Dim m(2, 3) As Integer
    Dim i As Long, j As Long, t As Long
    For i = 1 To 5
        a(i) = i * i
    Next i
    For i = 0 To 2
        For j = 0 To 3
            m(i, j) = CInt(i * 10 + j)
        Next j
    Next i
    For i = 1 To 5
        t = t + a(i)
    Next i
    ArrayFixed = t + m(2, 3) + UBound(a) + UBound(m, 2)
End Function

Public Function UdtUse() As String
    Dim it As TItem
    Dim list(1 To 3) As TItem
    it.Id = 7
    it.Price = 1.5
    it.Title = "one"
    list(1) = it
    list(2).Id = 8
    list(2).Title = "two"
    UdtUse = list(1).Title & CStr(list(2).Id) & CStr(it.Price)
End Function

Public Function ClassUse() As String
    Dim c As clsCounter
    Dim d As New clsCounter
    Set c = New clsCounter
    c.Name = "a"
    c.Value = 5
    c.Increment
    c.Increment 3
    d.Name = "b"
    ClassUse = c.Describe() & d.Describe() & CStr(c.Value)
    Set c = Nothing
End Function

Public Sub RunAll3()
    Dim s As String
    s = CStr(VarMath(3, 4)) & VarTypes() & CStr(ArrayFixed())
    s = s & UdtUse() & ClassUse()
    gResult = s
End Sub
