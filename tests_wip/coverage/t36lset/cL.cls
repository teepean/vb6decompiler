VERSION 1.0 CLASS
BEGIN
  MultiUse = -1  'True
  Persistable = 0  'NotPersistable
  DataBindingBehavior = 0  'vbNone
  DataSourceBehavior  = 0  'vbNone
  MTSTransactionMode  = 0  'NotAnMTSObject
END
Attribute VB_Name = "cL"
Attribute VB_GlobalNameSpace = False
Attribute VB_Creatable = True
Attribute VB_PredeclaredId = False
Attribute VB_Exposed = False
Option Explicit

Private mS As String
Private mF As String * 5

Public Function Go(ByVal a As String) As String
    mS = "12345"
    mF = "zzzzz"
    LSet mS = a
    RSet mF = a
    Go = "[" & mS & "][" & mF & "]"
End Function
