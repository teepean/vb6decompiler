Attribute VB_Name = "modT04"
Option Explicit

Public Type TPair
    a As Long
    b As Long
End Type

Public gOut As String
Public gSink As clsSink

Public Sub RunAll4()
    Dim k As clsT04
    Dim b As Byte, f As Boolean, i As Integer, l As Long, s As Single, d As Double
    Dim cu As Currency, t As Date, st As String, o As Object, v As Variant
    Dim al(1 To 2) As Long
    Dim ast() As String, av() As Variant, ra() As Long
    Dim p As TPair
    Set k = New clsT04
    k.ByVals 1, True, 2, 3, 4.5, 5.5, 6.5, #5/6/2002#, "x", k, 8, k
    k.ByRefs b, f, i, l, s, d, cu, t, st, o, v, k
    ReDim ast(0 To 2)
    ReDim av(1)
    k.Arrays al, ast, av
    k.Optionals
    k.Optionals 1, 2, "x", 3.5, False, "v", 2.25, #7/8/2003#, 9, 1.5, 11
    k.Optionals , , "only"
    k.Params 1
    k.Params 1, "a", 2, 3.5
    b = k.RetByte
    f = k.RetBool
    i = k.RetInt
    l = k.RetLong
    s = k.RetSng
    d = k.RetDbl
    cu = k.RetCur
    t = k.RetDate
    st = k.RetStr
    Set o = k.RetObj
    v = k.RetVar
    Set k = k.RetSelf
    ra = k.RetArr
    k.VLong = 5
    k.VStr = "s"
    Set k.VObj = k
    k.VVar = 3
    Set k.VSelf = k
    l = k.VLong + Len(k.VStr) + k.VByte + k.VInt
    k.PropVar = 1
    v = k.PropVar
    Set k.PropVar = k   ' (v = k.PropVar now would raise 438: no default member)
    k.Item(2) = "two"
    st = k.Item(2)
    p.a = 1
    p.b = 2
    l = l + k.FriendUdt(p)
    gSink.FByte = b
    gSink.FCur = cu
    gOut = st & CStr(l) & CStr(d) & CStr(ra(1)) & gSink.FormOpt(2)
    Set o = Nothing
    Set k = Nothing
End Sub

Sub Main()
    Set gSink = New clsSink
    gSink.Run
    RunAll4
    gOut = gOut & gSink.FormOpt() & gSink.FormOpt(4) & gSink.FormOpt(, "w")
    Dim fOut As Integer
    fOut = FreeFile
    Open App.Path & "\out.txt" For Output As #fOut
    Print #fOut, gOut
    Close #fOut
End Sub
