Attribute VB_Name = "mM"
Option Explicit

' Fast code moves floats with integer instructions: a Single literal is a mov of its bits, a Double
' goes in two moves or two compares.  Nothing here may print a number made from a float's bits.
Public Type TTie
    port As Integer
    ang As Single
    len_ As Single
    k As Single
    b As Single
End Type

Public Type TBot
    on_ As Boolean
    sw(3) As Boolean
    sw2(3) As Boolean
    poisoned As Boolean
    pcount As Single
    weight As Single
    ties(3) As TTie
End Type

Public Bots(4) As TBot
Public GOne As Double
Public GHalf As Double
Public GRate As Single
Public GMoved As Single
Private OutF As Integer

Private Function Clip(ByVal a As Single) As Single
    If a <= 0 Then
        Clip = 1
    Else
        Clip = 1 - 1 / a
    End If
End Function

Private Function Dbl2(ByVal a As Single) As Double
    Dbl2 = a * 2
End Function

Private Sub Setup(ByVal n As Integer)
    Dim j As Integer
    With Bots(n)
        .on_ = True
        .poisoned = False
        .pcount = 32000
        .weight = 0.25
        For j = 0 To 3
            .ties(j).port = j
            .ties(j).ang = 1.5
            .ties(j).len_ = 1500
            .ties(j).k = 0.05
            .ties(j).b = 0.1
            .sw(j) = (j = 1)
            .sw2(j) = (j = 2)
        Next j
    End With
End Sub

Private Sub Uses(ByVal n As Integer, ByVal k As Integer)
    With Bots(n)
        If .sw(k - 1) Then .ties(k - 1).ang = .ties(k - 1).ang + 1
        If .sw2(k - 1) Then .ties(k - 1).len_ = .ties(k - 1).len_ * 2
        .sw(k - 1) = False
        .sw2(k - 1) = False
        If .poisoned Then
            .pcount = .pcount - 1
            If .pcount < 1 Then .poisoned = False
        End If
    End With
End Sub

Private Sub Ceil(a As Single, b As Single)
    a = 5
    b = 7
End Sub

Private Function Lower() As Long
    Dim a As Single
    Dim b As Single
    Ceil a, b
    Lower = IIf(a > b, b, a)
End Function

Private Function Upper() As Long
    Dim a As Single
    Dim b As Single
    a = 3
    b = 8
    Upper = IIf(a > b, a, b)
End Function

Private Sub Keep(ByVal id As Long, ByVal mx As Double)
    Static oldid As Long
    Static oldmx As Double
    Print #OutF, "was"; oldid; oldmx
    If oldid = id And mx > oldmx Then Print #OutF, "more"
    oldid = id
    oldmx = mx
End Sub

Private Sub SetConst()
    Dim x As Double
    GOne = Rnd() * 0
    If GOne < 1 Then
        GOne = 1
    End If
    GHalf = 0.5
    GRate = 2.5
    x = GOne + GHalf
    Print #OutF, x; GRate
End Sub

Private Function Which(ByVal d As Double, ByVal e As Double) As String
    If d = 0.01 And e = 0.0000001 Then
        Which = "thin"
    ElseIf d = 0.0005 And e = 0.0000001 Then
        Which = "normal"
    ElseIf d = 0 And e = 0 Then
        Which = "none"
    Else
        Which = "custom"
    End If
End Function

Private Sub ByPointer(n As Integer, w As Single, ByVal m As Long, s As Single)
    Print #OutF, n; w; m; s
End Sub

Private Function Twist(a As Single, b As Single) As Single
    Twist = a - b
End Function

Private Function Norm(ByVal a As Single) As Single
    Norm = a + 1
End Function

Private Function Both(ByVal x As Single, ByVal y As Single) As Single
    Dim p As Single
    Dim q As Single
    p = Norm(x)
    q = Norm(y)
    Both = Twist(p, q)
End Function

Private Function Big() As Long
    Big = 16777217
End Function

Private Sub PushIt(ByVal n As Long)
    Print #OutF, n
End Sub

' a Single in between rounds: gone by inlining, the digits it lost come back
Private Sub Rounding()
    Dim a As Single
    Dim b As Single
    Dim c As Double
    a = Big
    b = Sqr(a) * 3.3
    PushIt b
    a = Big
    PushIt a
    a = Big
    c = a / 3
    Print #OutF, c
    b = Big
    a = b Mod 2000000
    c = a + b
    Print #OutF, c
    a = 1.1
    b = 2.2
    c = a * b
    Print #OutF, c
    b = a * b
    c = b + 1
    Print #OutF, c
End Sub

Sub Main()
    Dim i As Integer
    OutF = FreeFile
    Open "out.txt" For Output As #OutF
    Setup 1
    Bots(1).sw(1) = True
    Bots(1).sw2(2) = True
    Bots(1).poisoned = True
    Uses 1, 2
    Uses 1, 3
    With Bots(1)
        Print #OutF, .pcount; .weight; .poisoned
        For i = 0 To 3
            Print #OutF, .ties(i).port; .ties(i).ang; .ties(i).len_; .ties(i).k; .ties(i).b
        Next i
    End With
    Print #OutF, Clip(0); Clip(4); Dbl2(1.5)
    Print #OutF, Lower(); Upper()
    Keep 3, 1.5
    Keep 3, 2.5
    Keep 4, 0.5
    SetConst
    Print #OutF, Which(0.01, 0.0000001); Which(0.0005, 0.0000001); Which(0, 0); Which(1, 2)
    ByPointer 2, 1, 7, 0.75
    Print #OutF, Both(1.5, 0.25)
    GMoved = GRate
    Print #OutF, GMoved
    Rounding
    Close #OutF
End Sub
