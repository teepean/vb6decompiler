VERSION 1.0 CLASS
BEGIN
  MultiUse = -1  'True
  Persistable = 0  'NotPersistable
  DataBindingBehavior = 0  'vbNone
  DataSourceBehavior  = 0  'vbNone
  MTSTransactionMode  = 0  'NotAnMTSObject
END
Attribute VB_Name = "cD"
Attribute VB_GlobalNameSpace = False
Attribute VB_Creatable = True
Attribute VB_PredeclaredId = False
Attribute VB_Exposed = False
Option Explicit
Implements IA

Private Function IA_Speak(ByVal n As Long) As String
    IA_Speak = "w" & n
End Function

Private Property Get IA_Legs() As Long
    IA_Legs = 4
End Property

Public Function Own() As String
    Own = "own"
End Function
