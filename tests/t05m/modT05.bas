Attribute VB_Name = "modT05"
Option Explicit

Public Enum Shade
    shLight = 1
    shMid = 5
    shDark = 9
End Enum

Private Const BASE_NAME As String = "t05"
Private Const LIMIT As Long = 40

Public Type TPoint
    X As Long
    Y As Long
End Type

Public gLog As String

Public Function SafeDiv(ByVal a As Long, ByVal b As Long) As Long
    On Error GoTo Fail
    SafeDiv = a \ b
    Exit Function
Fail:
    gLog = gLog & "E" & CStr(Err.Number)
    SafeDiv = -1
End Function

Public Function ResumeTest(ByVal n As Long) As Long
    Dim r As Long
    On Error Resume Next
    r = 100 \ n
    If Err.Number <> 0 Then
        r = 0
        Err.Clear
    End If
    ResumeTest = r
End Function

Public Function Counter() As Long
    Static calls As Long
    calls = calls + 1
    Counter = calls
End Function

Public Function ShadeName(ByVal s As Shade) As String
    Select Case s
        Case shLight
            ShadeName = "light"
        Case shMid
            ShadeName = "mid"
        Case Else
            ShadeName = "dark"
    End Select
End Function

Public Function FindFirst(a() As Long, ByVal v As Long) As Long
    Dim i As Long
    FindFirst = -1
    For i = LBound(a) To UBound(a)
        If a(i) = v Then
            FindFirst = i
            Exit For
        End If
    Next i
End Function

Public Function Strings(ByVal s As String) As String
    Dim t As String
    t = UCase$(Left$(s, 3)) & LCase$(Mid$(s, 4, 2)) & Right$(s, 1)
    t = t & CStr(Len(s)) & CStr(InStr(1, s, "b")) & Replace(s, "a", "o")
    t = t & Trim$("  x  ") & String$(3, "z") & Chr$(65) & CStr(Asc("B"))
    Strings = t & Format$(12.5, "0.00")
End Function

Public Function FileTest() As String
    Dim f As Integer
    Dim ln As String
    Dim n As Long
    Dim p As String
    p = App.Path & "\" & BASE_NAME & ".txt"     ' (not TEMP: the suite runs two builds at once)
    f = FreeFile
    Open p For Output As #f
    Print #f, "alpha"
    Print #f, "beta"
    Close #f
    f = FreeFile
    Open p For Input As #f
    Do While Not EOF(f)
        Line Input #f, ln
        n = n + Len(ln)
    Loop
    Close #f
    Kill p
    FileTest = CStr(n)
End Function

Public Function CollTest() As Long
    Dim c As Collection
    Dim v As Variant
    Dim total As Long
    Set c = New Collection
    c.Add 3, "a"
    c.Add 4, "b"
    c.Add 5
    For Each v In c
        total = total + v
    Next v
    total = total + c("b") + c.Count
    c.Remove 1
    CollTest = total + c.Count
End Function

Public Function LateTest() As String
    Dim o As Object
    Set o = New Collection
    o.Add "x"
    o.Add "y"
    o.Add "z", "k"
    LateTest = o.Item(2) & CStr(o.Count) & o.Item("k")
End Function

Public Function WithTest() As Long
    Dim p As TPoint
    With p
        .X = 3
        .Y = .X * 2
    End With
    WithTest = p.X + p.Y
End Function

Public Function LoopTest() As Long
    Dim i As Long
    Dim s As Long
    Do
        i = i + 1
        If i Mod 3 = 0 Then s = s + i
        If i > LIMIT Then Exit Do
    Loop
    While s > 100
        s = s - 7
    Wend
    LoopTest = s
End Function

Public Sub RunAll5()
    Dim a(1 To 5) As Long
    Dim i As Long
    For i = 1 To 5
        a(i) = i * i
    Next i
    gLog = CStr(SafeDiv(7, 2)) & CStr(SafeDiv(1, 0)) & CStr(ResumeTest(0))
    gLog = gLog & CStr(Counter() + Counter()) & ShadeName(shMid) & CStr(FindFirst(a, 16))
    gLog = gLog & Strings("abcdefab") & FileTest() & CStr(CollTest()) & LateTest()
    gLog = gLog & CStr(WithTest()) & CStr(LoopTest())
End Sub
