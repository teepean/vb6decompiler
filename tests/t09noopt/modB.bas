Attribute VB_Name = "modB"
Option Explicit

Sub Main()
    Dim f As Integer, t As String, i As Long
    RunA
    t = Log_
    For i = 1 To 10
        t = t & pub1(i) & " "
    Next
    t = t & vbCrLf & "net31 " & net(3, 1).a & " " & net(3, 1).b & " " & net(3, 1).s & vbCrLf
    net(1, 1).e = 42
    t = t & "net11e " & net(1, 1).e & vbCrLf
    f = FreeFile
    Open App.Path & "\out.txt" For Output As #f
    Print #f, t
    Close #f
End Sub
