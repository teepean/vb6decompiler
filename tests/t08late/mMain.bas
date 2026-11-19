Attribute VB_Name = "mMain"
Option Explicit
Public L As String

Public Function O(ByVal tag As String) As Variant
    L = L & tag & " "
    Dim c As cL
    Set c = New cL
    c.Tag = tag
    Set O = c
End Function

Public Function F(ByVal tag As String) As String
    L = L & tag & " "
    F = tag
End Function

Public Function N(ByVal tag As String) As Long
    L = L & tag & " "
    N = 1
End Function

Public Sub G(ByVal a As Variant, ByVal b As Variant)
    L = L & "G "
End Sub

Sub Main()
    Dim s As String, t As String, k As Long, v As Variant
    L = "": s = "x" & O("o1").Cnt & " " & F("f2"): t = t & "1: " & L & vbCrLf
    L = "": s = F("f1") & O("o2").Cnt & F("f3"): t = t & "2: " & L & vbCrLf
    L = "": k = O("o1").Cnt + N("n2"): t = t & "3: " & L & vbCrLf
    L = "": G O("o1").Cnt, F("f2"): t = t & "4: " & L & vbCrLf
    L = "": s = O("o1").Cnt & O("o2").Cnt & F("f3"): t = t & "5: " & L & vbCrLf
    L = "": v = O("o1").Me2.Cnt & F("f2"): t = t & "6: " & L & vbCrLf
    L = "": s = CStr(O("o1").Cnt) & F("f2"): t = t & "7: " & L & vbCrLf
    L = "": k = Len(F("f1")) + O("o2").Cnt + N("n3"): t = t & "8: " & L & vbCrLf
    L = "": s = "x" & O("o1").Cnt & " " & TypeName(O("o2")): t = t & "9: " & L & vbCrLf
    L = "": s = "x" & O("o1").Cnt & " " & TypeName(O("o2")) & O("o3").Cnt: t = t & "10: " & L & vbCrLf
    L = "": s = TypeName(O("o1")) & O("o2").Cnt & F("f3") & O("o4").Cnt: t = t & "11: " & L & vbCrLf
    L = "": If O("o1").Cnt = N("n2") Then t = t & ""
    t = t & "12: " & L & vbCrLf
    Dim fh As Integer
    fh = FreeFile
    Open App.Path & "\out.txt" For Output As #fh
    Print #fh, t
    Close #fh
End Sub
