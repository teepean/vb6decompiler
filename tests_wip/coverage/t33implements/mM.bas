Attribute VB_Name = "mM"
Option Explicit

Function Describe(ByVal a As IAnimal) As String
    Describe = a.Speak(2) & ":" & a.Legs & ":" & a.Field
End Function

Sub SetLegs(a As IAnimal, ByVal n As Long)
    a.Legs = n
End Sub

Sub Main()
    Dim a As IAnimal, b As IAnimal, d As cDog, l As ILog, o As Object, s As String
    Dim c As Collection, v As Variant, q As Variant, i As Long
    Open App.Path & "\out.txt" For Output As #1
    Set d = New cDog
    Set a = d
    a.Field = 21
    Print #1, a.Field
    Print #1, Describe(a); Describe(New cCat)
    SetLegs a, 6
    Print #1, a.Legs
    Set l = a
    l.Note "abc"
    l.Note "de"
    Print #1, l.Lines
    Print #1, d.OwnMethod()
    Set o = d
    Print #1, o.OwnMethod()
    s = "rex"
    a.Rename s
    Print #1, s; a.Speak(1)
    s = "max"
    a.Rename s, "?"
    Print #1, s; a.Speak(1)
    Set a.Owner = d
    Print #1, TypeName(a.Owner); a.Owner.OwnMethod()
    Set b = a.Mate()
    Print #1, b.Speak(9); TypeName(b)
    Print #1, b.Mate().Mate().Speak(3)
    Print #1, TypeOf a Is ILog; TypeOf b Is ILog; TypeOf b Is IAnimal; TypeOf a Is cDog; TypeOf b Is cDog
    q = 2
    Print #1, a.Weigh(1.5, q); a.Weigh(1.5, q, 3); b.Weigh(1.5, q)
    Set c = New Collection
    c.Add d
    c.Add New cCat
    For Each v In c
        Set a = v
        Print #1, a.Speak(1)
    Next v
    For i = 1 To c.Count
        Set a = c(i)
        Print #1, a.Legs
    Next i
    On Error Resume Next
    b.Legs = 1
    Print #1, Err.Number
    Err.Clear
    Set l = b
    Print #1, Err.Number
    Close #1
End Sub
