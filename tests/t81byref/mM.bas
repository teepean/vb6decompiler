Attribute VB_Name = "mM"
Option Explicit

' Every way a variable meets a ByRef parameter.  A String or Integer given to a ByRef Variant goes
' in a VT_BYREF wrapper and stays what it is; a Variant goes by its own address; a parameter passed
' on keeps its type.  VarType says which one each variable really is.
Private Function AsetaV(r As Variant, ByVal k As Long) As Long
    Select Case k
    Case 0: r = "teksti"
    Case 1: r = 42&
    Case 2: r = True
    Case 3: r = 2.5
    End Select
    AsetaV = VarType(r)
End Function

Private Sub AsetaS(s As String)
    s = s & "+"
End Sub

Private Sub AsetaI(i As Integer)
    i = i + 1
End Sub

Private Function LueS(s As String) As Long
    LueS = Len(s)
End Function

Private Function LueV(v As Variant) As String
    LueV = TypeName(v) & ":" & CStr(v)
End Function

' passed on, to a plain type and to a Variant
Private Sub EteenS(s As String)
    AsetaS s
    Print #1, LueS(s), LueV(s)
End Sub

Private Sub EteenV(v As Variant)
    Print #1, AsetaV(v, 1), LueV(v)
End Sub

' takes both kinds in one call, in various positions
Private Sub Sekaisin(a As String, b As Variant, c As Integer, d As Variant)
    a = a & TypeName(b)
    b = c + 1
    c = c * 2
    d = a
End Sub

Private Sub Rivi(ByVal n As String, v As Variant)
    Print #1, n, VarType(v), v
End Sub

Sub Main()
    Dim s As String, s2 As String
    Dim i As Integer, l As Long
    Dim v As Variant, w As Variant
    Dim k As Long
    Open "out.txt" For Output As #1

    ' a Variant by its address: every value type
    For k = 0 To 3
        Print #1, k, AsetaV(v, k)
        Rivi "v", v
    Next k
    ' a String through a wrapper: stays a String whatever is assigned
    s = "alku"
    Print #1, AsetaV(s, 0), s, VarType(s)
    Print #1, AsetaV(s, 1), s, VarType(s)
    ' an Integer through a wrapper
    i = 7
    Print #1, AsetaV(i, 1), i, VarType(i)
    l = 8
    Print #1, AsetaV(l, 1), l, VarType(l)
    ' the same String to both kinds of parameter
    s2 = "x"
    AsetaS s2
    Print #1, LueS(s2), LueV(s2)
    EteenS s2
    Print #1, s2
    ' the same Variant to Variant parameters, and converted for plain ones
    w = "y"
    EteenV w
    Rivi "w", w
    w = "z"
    s = w
    AsetaS s
    Print #1, s, LueV(w)
    ' literals by reference
    Print #1, AsetaV(1, 1), LueV("kirjain"), LueV(3)
    AsetaS "kopio"
    AsetaI 5
    ' all kinds in one call
    s = "a": v = 1: i = 3: w = 0
    Sekaisin s, v, i, w
    Print #1, s, v, i, w, VarType(v), VarType(w)
    Dim t As String, n As Integer
    t = "b": n = 4
    Sekaisin t, t, n, t
    Print #1, t, n
    Sekaisin t, n, n, l
    Print #1, t, n, l
    Close #1
End Sub
