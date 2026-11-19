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

Public Pub As Long
Public Next1 As cT
Private m As String

Public Property Get Val1() As String
    Val1 = "[" & m & "]"
End Property

Public Property Let Val1(ByVal s As String)
    m = s
End Property

Public Function Twice(ByVal k As Long) As Long
    Twice = k * 2
End Function

Public Sub Fail()
    Err.Raise 1001, "cT", "from the class"
End Sub

Public Function Add(ByRef r As Long) As cT
    r = r + Pub
    Set Add = Me
End Function

' the object in itself, in a With and through a parameter, with errors resumed from
Public Sub InClass(o As cT)
    On Error Resume Next
    With o
        .Pub = Pub + 1
        .Val1 = m & "+"
    End With
    Print #1, "in class"; o.Pub; o.Val1
    Me.Fail
    Print #1, "after fail"; Err.Number
    Err.Clear
End Sub
