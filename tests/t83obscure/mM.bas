Attribute VB_Name = "mM"
Option Explicit
DefInt I-K
DefStr S
DefLng L
DefSng X
DefDbl Y
DefBool B
DefByte C

Global Yht As Long
Dim Shared_ As Integer
Public Const Vakio As Long = &O777
Const Teksti = "teksti" & "pari"

' GoSub and Return, a target by line number, On x GoSub and GoTo
Private Function Hyppy(ByVal n As Long) As Long
    Dim r As Long
    r = 1
    GoSub Lisaa
    GoSub Lisaa
    Hyppy = r
    Exit Function
Lisaa:
    r = r * 2 + n
    Return
End Function

Private Function OnGoSub(ByVal n As Long) As Long
    Dim r As Long
    On n GoSub A1, A2, A3
    OnGoSub = r
    Exit Function
A1:
    r = 11
    Return
A2:
    r = 22
    Return
A3:
    r = 33
    Return
End Function

Private Function OnGoTo(ByVal n As Long) As Long
    On n GoTo B1, B2
    OnGoTo = -1
    Exit Function
B1:
    OnGoTo = 100
    Exit Function
B2:
    OnGoTo = 200
End Function

' line numbers, Erl, Resume Next, Resume line, On Error GoTo 0, On Error GoTo -1
Private Function Virhe(ByVal n As Long) As String
    Dim t As String, x As Long
10  On Error GoTo V
20  x = 1
30  If n = 1 Then x = x / 0
40  If n = 2 Then Error 5
50  t = "ok" & x
60  Virhe = t
70  Exit Function
V:
    t = "virhe" & Err.Number & " rivi" & Erl
    If n = 1 Then Resume Next
    Virhe = t
End Function

Private Function Virhe2() As String
    Dim k As Long
    On Error GoTo X1
    k = 1
    Error 6
    k = 2
    Virhe2 = "ei"
    Exit Function
X1:
    Virhe2 = "x1:" & Err.Number
    On Error GoTo -1
    On Error GoTo X2
    Error 7
    Virhe2 = "ei2"
    Exit Function
X2:
    Virhe2 = Virhe2 & " x2:" & Err.Number
    Err.Clear
    On Error GoTo 0
End Function

Private Function Virhe3() As Long
    Static kerta As Long
    On Error GoTo X3
    kerta = kerta + 1
    Virhe3 = 6 \ (kerta - 1)
    Exit Function
X3:
    kerta = kerta + 1
    Resume
End Function

Private Function Virhe4() As Long
    Dim k As Long
    On Error GoTo X4
    k = 5
    Error 11
    k = 6
L1:
    Virhe4 = k
    Exit Function
X4:
    k = 7
    Resume L1
End Function

' all of a Sub's locals are Static
Private Static Sub Laskuri(n As Long)
    Dim a As Long, s2 As String
    a = a + 1
    s2 = s2 & "x"
    n = a * 10 + Len(s2)
End Sub

Private Function Operaattorit(ByVal a As Long, ByVal b As Long) As String
    Dim s1 As String
    s1 = (a Eqv b) & " " & (a Imp b) & " " & (a Xor b) & " " & (Not a) & " " & (a Mod 3) & " " & (a \ 3)
    s1 = s1 & " " & (2 ^ 10) & " " & (-a) & " " & Abs(-a) & " " & Sgn(-a)
    Operaattorit = s1
End Function

Private Function Pyoristys(ByVal d As Double) As String
    Pyoristys = Int(d) & " " & Fix(d) & " " & CInt(d) & " " & CLng(d) & " " & Round(d) & " " & Round(d, 1)
End Function

Private Function Sanat() As String
    Dim a As String, b As String, f As String * 6
    a = "ab" + "cd"
    b = "1" + "2"
    LSet f = "xy"
    a = a & "[" & f & "]"
    RSet f = "pq"
    a = a & "[" & f & "]"
    Mid$(b, 1, 1) = "9"
    a = a & b & Len(f) & LenB(f)
    a = a & Asc("A") & AscW("B") & Chr$(67) & ChrW$(68)
    a = a & UCase$("e") & LCase$("F") & Space$(2) & String$(2, "g") & String$(2, 72)
    a = a & Left$("ijk", 1) & Right$("ijk", 1) & Mid$("ijk", 2, 1) & Mid$("ijk", 2)
    a = a & LTrim$("  l") & RTrim$("m  ") & Trim$(" n ")
    a = a & StrComp("a", "A", vbTextCompare) & StrComp("a", "A", vbBinaryCompare)
    a = a & InStr(2, "abcabc", "b") & InStr(1, "ABC", "b", vbTextCompare) & InStrRev("abcabc", "b")
    a = a & StrConv("xY", vbProperCase) & StrReverse("abc") & Hex$(255) & Oct$(8)
    a = a & Format$(3.14159, "0.00") & Format$(1234, "#,##0") & Val("12x") & Str$(5)
    Sanat = a
End Function

Private Function Silmukat() As String
    Dim i As Integer, j As Integer, r As String, n As Long
    i = 0
    While i < 3
        i = i + 1
        r = r & i
    Wend
    For i = 1 To 2
        For j = 1 To 2
            r = r & i * j
    Next j, i
    Do
        n = n + 1
        If n = 2 Then Exit Do
    Loop
    Do Until n >= 4
        n = n + 1
    Loop
    Do
        n = n + 1
    Loop While n < 6
    Do
        n = n + 1
    Loop Until n > 7
    For i = 10 To 1 Step -3
        r = r & "," & i
    Next
    For n = 1 To 3 Step 2
        If n = 3 Then Exit For
        r = r & "n" & n
    Next
    Silmukat = r & n
End Function

Private Sub Sulut(x As Long)
    x = x + 1
End Sub

Private Function Kutsut() As String
    Dim k As Long
    k = 1
    Sulut k
    Sulut (k)
    Call Sulut(k)
    Kutsut = "k" & k
End Function

Private Function Rivihyppy(ByVal n As Long) As Long
    If n = 1 Then 10 Else 20
10  Rivihyppy = 1
    Exit Function
20  Rivihyppy = 2
End Function

Private Function Valitsimet(ByVal n As Long) As String
    Valitsimet = IIf(n > 0, "pos", "neg") & Choose(2, "a", "b", "c") & Switch(n = 1, "yy", n = 2, "kk")
End Function

Private Function Oletukset() As String
    ' DefInt, DefStr, DefLng, DefSng, DefDbl, DefBool, DefByte
    Dim i1, s1, l1, x1, y1, b1, c1
    i1 = 40000 \ 2: s1 = "a": l1 = 70000: x1 = 1.5: y1 = 1.25: b1 = True: c1 = 200
    Oletukset = TypeName(i1) & TypeName(s1) & TypeName(l1) & TypeName(x1) & TypeName(y1) & TypeName(b1) & TypeName(c1)
End Function

Private Function Numerot() As String
    Dim v As Variant
    v = &O17
    Numerot = v & " " & &HFF & " " & &HFFFF& & " " & Vakio & " " & Teksti & " " & 1.5E+3 & " " & 2# & " " & 3! & " " & 5@
End Function

Private Function Pvm() As String
    Dim d As Date
    d = #1/15/2004 3:04:05 PM#
    Pvm = Year(d) & Month(d) & Day(d) & Hour(d) & Minute(d) & Second(d) & Weekday(d) & _
        DateDiff("d", #1/1/2004#, d) & DatePart("yyyy", d) & Format$(DateAdd("m", 1, d), "yyyy-mm-dd") & _
        Format$(DateSerial(2001, 2, 3), "yyyymmdd") & Format$(TimeSerial(1, 2, 3), "hhmmss")
End Function

Public Sub Main()
    Dim n As Long, i As Long
    Open App.Path & "\out.txt" For Output As #1
    Print #1, Hyppy(3), OnGoSub(1), OnGoSub(2), OnGoSub(3), OnGoSub(4), OnGoTo(1), OnGoTo(2), OnGoTo(3)
    Print #1, Virhe(0)
    Print #1, Virhe(1)
    Print #1, Virhe(2)
    Print #1, Virhe2()
    Print #1, Virhe3(), Virhe4()
    Laskuri n
    Laskuri n
    Print #1, n
    Print #1, Operaattorit(6, 3)
    Print #1, Pyoristys(2.5), Pyoristys(-3.75)
    Print #1, Sanat()
    Print #1, Silmukat()
    Print #1, Kutsut()
    Print #1, Rivihyppy(1), Rivihyppy(2)
    Print #1, Valitsimet(1), Valitsimet(2)
    Print #1, Oletukset()
    Print #1, Numerot()
    Print #1, Pvm()
    Print #1, Sama("AbC", "aBc"), Suurempi("B", "a"), Mallina("abz"), Paikka("xxcd"), Korvaa("aXb"), Valinta("KAKSI"), Pohja()
    Yht = 5: Shared_ = 2
    Print #1, Yht * Shared_
    Close #1
    Reset
End Sub
