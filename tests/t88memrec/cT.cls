VERSION 1.0 CLASS
BEGIN
  MultiUse = -1  'True
END
Attribute VB_Name = "cT"
Attribute VB_GlobalNameSpace = False
Attribute VB_Creatable = True
Attribute VB_PredeclaredId = False
Attribute VB_Exposed = False
Option Explicit

Private Declare Function LenA Lib "kernel32" Alias "lstrlenA" (u As Any) As Long

Private Type TU
    Tip As String * 8
    Size As Long
    Count As Long
    First As Integer
End Type

Private m As TU
Private mName As String

Public Sub Fill(s As String, n As Long)
    mName = s
    With m
        .Tip = mName
        .Size = n
        .Count = n * 2
        .First = 3
    End With
End Sub

Public Sub Change(n As Long)
    m.Count = n
End Sub

Public Function AnsiLen() As Long
    AnsiLen = LenA(m)
End Function

Public Function Tip() As String
    Tip = RTrim$(m.Tip)
End Function

Public Sub CopyFrom(c As cT)
    Dim u As TU
    u.Tip = "zz"
    u.Size = 9
    u.Count = 12
    m = u
End Sub

Public Function Count() As Long
    Count = m.Count
End Function

Public Function First() As Integer
    First = m.First
End Function
