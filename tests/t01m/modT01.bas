Attribute VB_Name = "modT01"
Option Explicit

Public Type TPoint
    X As Long
    Y As Long
    Name As String
End Type

Public Declare Function GetTickCount Lib "kernel32" () As Long
Public Declare Function MessageBeep Lib "user32" (ByVal wType As Long) As Long

Public Const MAXITEMS As Long = 10

Public gCounter As Long
Public gName As String
Public gValue As Double
Private mFlag As Boolean
Private mItems(1 To 10) As Integer
Private mDyn() As Long

Public Function AddLongs(ByVal a As Long, ByVal b As Long) As Long
    AddLongs = a + b
End Function

Public Function AddInts(ByVal a As Integer, ByVal b As Integer) As Integer
    AddInts = a + b
End Function

Public Function MulSub(ByVal a As Long, ByVal b As Long, ByVal c As Long) As Long
    MulSub = a * b - c
End Function

Public Function DivTest(ByVal a As Long, ByVal b As Long) As Long
    DivTest = a \ b + a Mod b
End Function

Public Function FloatTest(ByVal x As Double, ByVal y As Single) As Double
    FloatTest = x * y / 2# + 1.5
End Function

Public Sub ByRefTest(ByRef a As Long, ByRef s As String)
    a = a + 1
    s = s & "!"
End Sub

Public Function StrConcat(ByVal a As String, ByVal b As String) As String
    StrConcat = a & " " & b
End Function

Public Function StrFuncs(ByVal s As String) As String
    Dim t As String
    t = UCase$(Left$(s, 3)) & LCase$(Mid$(s, 2, 2)) & Right$(s, 1)
    t = t & CStr(Len(s)) & Chr$(65) & Trim$("  x  ")
    StrFuncs = t & CStr(InStr(1, s, "a")) & CStr(Asc(s))
End Function

Public Function IfTest(ByVal n As Long) As String
    If n < 0 Then
        IfTest = "neg"
    ElseIf n = 0 Then
        IfTest = "zero"
    ElseIf n > 100 And n < 200 Then
        IfTest = "mid"
    Else
        IfTest = "pos"
    End If
End Function

Public Function SelectTest(ByVal n As Integer) As Long
    Select Case n
    Case 1
        SelectTest = 10
    Case 2, 3
        SelectTest = 20
    Case 4 To 9
        SelectTest = 30
    Case Is > 100
        SelectTest = 40
    Case Else
        SelectTest = -1
    End Select
End Function

Public Function ForTest(ByVal n As Long) As Long
    Dim i As Long, total As Long
    For i = 1 To n
        total = total + i
    Next i
    For i = n To 1 Step -2
        total = total - 1
    Next
    ForTest = total
End Function

Public Function DoLoopTest(ByVal n As Long) As Long
    Dim k As Long
    Do While n > 0
        n = n - 1
        k = k + 2
    Loop
    Do
        k = k - 1
    Loop Until k < 5
    While k < 10
        k = k + 3
    Wend
    DoLoopTest = k
End Function

Public Function BoolTest(ByVal a As Boolean, ByVal b As Boolean) As Boolean
    BoolTest = (a And Not b) Or (b Xor mFlag)
End Function

Public Function VariantTest(ByVal v As Variant) As Variant
    Dim w As Variant
    w = v + 1
    VariantTest = w & "x"
End Function

Public Sub ArrayTest()
    Dim i As Integer
    For i = 1 To 10
        mItems(i) = i * 2
    Next
    ReDim mDyn(5)
    mDyn(0) = mItems(3)
    ReDim Preserve mDyn(UBound(mDyn) + 5)
    gCounter = UBound(mDyn) - LBound(mDyn)
    Erase mDyn
End Sub

Public Function UdtTest() As Long
    Dim p As TPoint
    p.X = 3
    p.Y = 4
    p.Name = "pt"
    UdtTest = p.X * p.X + p.Y * p.Y + Len(p.Name)
End Function

Public Sub GlobalsTest()
    gCounter = gCounter + 1
    gName = "count=" & gCounter
    gValue = gCounter / 3
    mFlag = Not mFlag
End Sub

Public Function ApiTest() As Long
    Dim t As Long
    t = GetTickCount()
    MessageBeep 0
    If t <> 0 Then ApiTest = 1   ' deterministic for the behaviour test
End Function

Public Function CallTest() As Long
    Dim s As String
    Dim n As Long
    n = 5
    s = "a"
    ByRefTest n, s
    CallTest = AddLongs(n, Len(s)) + MulSub(1, 2, 3)
End Function

Public Function ErrorTest(ByVal d As Long) As Long
    On Error GoTo Failed
    ErrorTest = 100 \ d
    Exit Function
Failed:
    ErrorTest = -Err.Number
End Function

Public Function ResumeNextTest() As Long
    On Error Resume Next
    ResumeNextTest = 1 \ 0
    ResumeNextTest = Err.Number
End Function

Public Function ConvTest(ByVal s As String) As Long
    Dim d As Double, i As Integer, b As Byte
    d = CDbl(s)
    i = CInt(d)
    b = CByte(i And 255)
    ConvTest = CLng(i) + b + Val(s)
End Function

Public Function GotoTest(ByVal n As Long) As Long
    If n = 0 Then GoTo Done
    n = n * 2
Done:
    GotoTest = n
End Function

Public Sub RunAll()
    Dim n As Long, s As String, v As Variant
    n = AddLongs(1, 2) + AddInts(3, 4) + MulSub(5, 6, 7) + DivTest(17, 5)
    gValue = FloatTest(1.25, 2.5)
    s = "abc"
    ByRefTest n, s
    s = StrFuncs(StrConcat(s, "def"))
    s = IfTest(n) & CStr(SelectTest(3)) & CStr(ForTest(4)) & CStr(DoLoopTest(3))
    mFlag = BoolTest(True, False)
    v = VariantTest(5)
    ArrayTest
    n = UdtTest() + ApiTest() + CallTest() + ErrorTest(0) + ResumeNextTest()
    n = n + ConvTest("12") + GotoTest(3)
    GlobalsTest
    gName = s & CStr(v) & CStr(n)
End Sub
