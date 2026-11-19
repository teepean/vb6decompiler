Attribute VB_Name = "modT07"
Option Explicit

Public gLog As String

Public Function Describe(ByVal o As Object) As String
    If o Is Nothing Then
        Describe = "nothing"
    ElseIf TypeOf o Is clsSquare Then
        Describe = "sq" & CStr(o.Side)
    ElseIf TypeOf o Is IShape Then
        Describe = "shape:" & TypeName(o)
    Else
        Describe = "?"
    End If
End Function

Public Sub RunAll7()
    Dim b As clsBag
    Dim q As clsSquare
    Dim c As clsCircle
    Dim sh As IShape
    Dim o As Object
    Set b = New clsBag
    Set q = New clsSquare
    q.Side = 2
    Set c = New clsCircle
    c.R = 1
    b.Add q, "q"
    b.Add c
    b.Add q.Grow(1.5)
    b = "bag"
    gLog = b & ":" & CStr(b.Count) & ":" & CStr(b.Total()) & ":" & b.Names()
    Set sh = b.Item("q")
    gLog = gLog & ":" & sh.Name & CStr(sh.Area)
    gLog = gLog & ":" & Describe(q) & "," & Describe(c) & "," & Describe(o)
    Set o = sh
    If o Is q Then gLog = gLog & ":same"
    Set b = Nothing
End Sub
