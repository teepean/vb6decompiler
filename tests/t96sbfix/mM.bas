Attribute VB_Name = "mM"
Option Explicit

' What a real program's decompile got wrong at run time with nothing to show for it at compile time.
Private Declare Function lstrlenA Lib "kernel32" (ByVal s As String) As Long
Private Declare Function lstrcpynA Lib "kernel32" (d As Any, ByVal s As String, ByVal n As Long) As Long
Private Declare Function OleTranslateColor Lib "oleaut32.dll" (ByVal c As Long, ByVal h As Long, r As Long) As Long
Private Declare Sub RtlFillMemory Lib "kernel32" (d As Any, ByVal n As Long, ByVal v As Byte)

Type TSix
    a As Long
    b As Long
    c As Long
    d As Long
    e As Long
    f As Long
End Type

Public gVars() As Variant
Public gTwo() As Long
Public gCalls As Long
Public gBig As Long

' A variable through a Variant around it, of whatever type
Sub Bump(x As Variant)
    x = x + x
End Sub

Sub Shown(ByVal k As String, x As Variant)
    Print #1, k; " ["; x; "]"
End Sub

Sub Fill(x As Variant)
    x = True
End Sub

Sub Apis()
    Dim s As String, b(7) As Byte, r As Long
    s = "kuusi"
    Print #1, lstrlenA(s); lstrlenA("abc")
    ' a String buffer and a byte: one parameter As Any
    Dim buf As String
    buf = String$(8, "x")
    r = lstrcpynA(ByVal buf, "hello", 4)
    Print #1, Left$(buf, 3)
    r = lstrcpynA(b(0), "hi", 3)
    Print #1, b(0); b(1); b(2)
    ' a local an API fills
    Dim c As Long
    r = OleTranslateColor(&HFF, 0, c)
    Print #1, r; c
End Sub

Sub Narrow()
    ' a Boolean an API fills, and the Long next to it
    Dim f As Boolean, rc As Long, v As Variant
    rc = 99
    RtlFillMemory f, 2, 255
    rc = rc + 1
    v = f
    Print #1, rc; v; v = True; TypeName(v)
End Sub

Sub Wrapped()
    Dim n As Long, s As String
    n = 5
    s = "viisi"
    Bump n
    Bump s
    Print #1, n; s
    Shown "n", n
    Shown "s", s
    Shown "sum", n + 1
End Sub

Sub Filled()
    Dim v As Variant
    Fill v
    If v = True Then
        Print #1, "filled"
    Else
        Print #1, "not filled"
    End If
End Sub

Sub Elements()
    ' an element by reference and read again, in a loop: its offset goes through frame slots
    Dim a() As String, i As Long, o As Object
    ReDim a(1 To 3)
    For i = 1 To 3
        a(i) = "e" & i
    Next i
    For i = 1 To 3
        Shown CStr(i), a(i)
        Shown UCase$(a(i)), a(i)
    Next i
End Sub

Sub Bytes()
    Dim b() As Byte, s As String
    s = "abc"
    b = StrConv(s, vbFromUnicode)
    Print #1, UBound(b); b(0); b(2)
    b = "de"
    Print #1, UBound(b); b(0); b(2)
End Sub

Sub Late()
    Dim o As Object, v As Variant, k As Long
    Set o = New cP
    o.Item(2) = "toinen"
    o.Cell(1, 2) = 12
    o.Cell(2, 1) = 21
    Print #1, o.Item(2); o.Cell(1, 2); o.Cell(2, 1)
    Set v = New cP
    v.Item(3) = "kolmas"
    v.Cell(3, 1) = 31
    Print #1, v.Item(3); v.Cell(3, 1)
    k = o.Reads + 1
    Print #1, o.Reads
End Sub

Sub Pics()
    Dim h As New cH
    Print #1, h.Empty1; h.Empty2
    h.Fill
    Print #1, h.Empty1; h.Empty2
    Set h.Pic = LoadPicture()
    Print #1, h.Empty1; h.PicType; h.PicWide
End Sub

Sub Swap(p As TSix, q As TSix)
    ' a field at 14h, where a SAFEARRAY has its bound: its read stays before the store
    Dim t As Long
    t = p.f: p.f = q.f: q.f = t
    t = p.e: p.e = q.e: q.e = t
End Sub

Sub Fields()
    Dim p As TSix, q As TSix
    p.e = 1: p.f = 2: q.e = 3: q.f = 4
    Swap p, q
    Print #1, p.e; p.f; q.e; q.f
End Sub

Sub Touch(x As Long)
    x = x + 0
End Sub

Sub Apu(ByVal a As Long, o As Object)
    Print #1, a; o.Item(1)
End Sub

Sub Converted()
    ' a Long from an element of Variants, in a For loop, counters in memory.  (Near what left a dead
    ' t_eax = CLng(...) in another real program, but this does not bring that back.)
    Dim v() As Variant, n() As Long, i As Long, t As Long
    On Error GoTo bad
    ReDim v(1 To 3), n(1 To 3), gTwo(1 To 3, 1 To 2)
    Touch i
    Touch t
    For i = 1 To 3
        v(i) = i * 2.25
    Next i
    For i = 1 To 3
        n(i) = CLng(v(i))
    Next i
    Print #1, n(1); n(2); n(3)
    ReDim gVars(1 To 3)
    gVars(1) = 1.75: gVars(2) = 2.75: gVars(3) = 3.75
    For i = 3 To 1 Step -1
        t = t + 1
        gTwo(t, 2) = CLng(gVars(i))
    Next i
    Print #1, gTwo(1, 2); gTwo(2, 2); gTwo(3, 2)
    Exit Sub
bad:
    Print #1, "virhe"; Err.Number
End Sub

Sub GivenObject()
    ' a method's declared ByRef As Object: nothing but an Object variable goes there.  (Near what made
    ' a Variant of the local in another real program, but this does not bring that back.)
    Dim o As Object, p As New cP
    Set o = New cP
    p.Hand o, "annettu"
    Print #1, o.Item(1)
    p.Hand o, "toisen kerran"
    Print #1, o.Item(1)
    p.Both 1
    p.Both 2
End Sub

Sub CopyOne(a() As TSix, ByVal i As Long, ByVal j As Long)
    ' a whole element of a record, onto another of the same array
    a(i) = a(j)
End Sub

Sub Elems()
    Dim r() As TSix
    ReDim r(1 To 3)
    r(3).a = 31: r(3).f = 36
    CopyOne r, 1, 3
    Print #1, r(1).a; r(1).f; r(2).a
End Sub

Function Seven() As Long
    gCalls = gCalls + 1
    Seven = 7
End Function

Sub Unused()
    ' a conversion nothing reads may still stop the program, and the call in it runs
    Dim k As Integer
    gBig = 100000
    On Error Resume Next
    k = CInt(Seven() + gBig)
    Print #1, gCalls; Err.Number
    On Error GoTo 0
End Sub

Sub Main()
    Open App.Path & "\out.txt" For Output As #1
    Converted
    GivenObject
    Elems
    Unused
    Apis
    Narrow
    Wrapped
    Filled
    Elements
    Bytes
    Late
    Pics
    Fields
    Close #1
End Sub
