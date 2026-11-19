VERSION 1.0 CLASS
BEGIN
  MultiUse = -1  'True
  Persistable = 0  'NotPersistable
  DataBindingBehavior = 0  'vbNone
  DataSourceBehavior  = 0  'vbNone
  MTSTransactionMode  = 0  'NotAnMTSObject
END
Attribute VB_Name = "cLis"
Attribute VB_GlobalNameSpace = False
Attribute VB_Creatable = True
Attribute VB_PredeclaredId = False
Attribute VB_Exposed = False
Option Explicit

Private WithEvents s As cSrc
Attribute s.VB_VarHelpID = -1
Public Log As String

Public Sub Attach(ByVal src As cSrc)
    Set s = src
End Sub

Private Sub s_Tick(ByVal n As Long)
    Log = Log & "L" & n
    If n = 2 Then
        s.Fire 3
    End If
End Sub

Private Sub s_Multi(ByVal t As String, v As Variant, ByVal d As Double)
    v = v + d
    Log = Log & t
End Sub
