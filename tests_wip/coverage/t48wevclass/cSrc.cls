VERSION 1.0 CLASS
BEGIN
  MultiUse = -1  'True
  Persistable = 0  'NotPersistable
  DataBindingBehavior = 0  'vbNone
  DataSourceBehavior  = 0  'vbNone
  MTSTransactionMode  = 0  'NotAnMTSObject
END
Attribute VB_Name = "cSrc"
Attribute VB_GlobalNameSpace = False
Attribute VB_Creatable = True
Attribute VB_PredeclaredId = False
Attribute VB_Exposed = False
Option Explicit

Public Event Tick(ByVal n As Long)
Public Event Ask(q As String, Cancel As Boolean)
Public Event Multi(ByVal s As String, v As Variant, ByVal d As Double)
Public Name As String

Public Sub Fire(ByVal n As Long)
    RaiseEvent Tick(n)
End Sub

Public Function Query(ByVal s As String) As String
    Dim c As Boolean
    RaiseEvent Ask(s, c)
    If c Then
        Query = "cancelled " & s
    Else
        Query = "ok " & s
    End If
End Function

Public Function Fan() As Variant
    Dim v As Variant
    v = 1
    RaiseEvent Multi("a", v, 2.5)
    Fan = v
End Function
