VERSION 1.0 CLASS
BEGIN
  MultiUse = -1  'True
  Persistable = 0  'NotPersistable
  DataBindingBehavior = 0  'vbNone
  DataSourceBehavior  = 0  'vbNone
  MTSTransactionMode  = 0  'NotAnMTSObject
END
Attribute VB_Name = "IAnimal"
Attribute VB_GlobalNameSpace = False
Attribute VB_Creatable = True
Attribute VB_PredeclaredId = False
Attribute VB_Exposed = False
Option Explicit

Public Field As Long

Public Function Speak(ByVal n As Long) As String
End Function

Public Property Get Legs() As Long
End Property

Public Property Let Legs(ByVal v As Long)
End Property

Public Property Get Owner() As Object
End Property

Public Property Set Owner(ByVal o As Object)
End Property

Public Sub Rename(s As String, Optional ByVal suffix As String = "!")
End Sub

Public Function Mate() As IAnimal
End Function

Public Function Weigh(ByVal a As Double, b As Variant, Optional c As Variant) As Double
End Function
