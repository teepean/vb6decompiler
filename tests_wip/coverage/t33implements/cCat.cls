VERSION 1.0 CLASS
BEGIN
  MultiUse = -1  'True
  Persistable = 0  'NotPersistable
  DataBindingBehavior = 0  'vbNone
  DataSourceBehavior  = 0  'vbNone
  MTSTransactionMode  = 0  'NotAnMTSObject
END
Attribute VB_Name = "cCat"
Attribute VB_GlobalNameSpace = False
Attribute VB_Creatable = True
Attribute VB_PredeclaredId = False
Attribute VB_Exposed = False
Option Explicit
Implements IAnimal

Private mField As Long

Private Property Let IAnimal_Field(ByVal RHS As Long)
    mField = RHS
End Property

Private Property Get IAnimal_Field() As Long
    IAnimal_Field = mField
End Property

Private Function IAnimal_Speak(ByVal n As Long) As String
    IAnimal_Speak = "meow" & n
End Function

Private Property Get IAnimal_Legs() As Long
    IAnimal_Legs = 3
End Property

Private Property Let IAnimal_Legs(ByVal v As Long)
    Err.Raise 5
End Property

Private Property Get IAnimal_Owner() As Object
End Property

Private Property Set IAnimal_Owner(ByVal o As Object)
End Property

Private Sub IAnimal_Rename(s As String, Optional ByVal suffix As String = "!")
    s = s & "cat" & suffix
End Sub

Private Function IAnimal_Mate() As IAnimal
    Set IAnimal_Mate = Me
End Function

Private Function IAnimal_Weigh(ByVal a As Double, b As Variant, Optional c As Variant) As Double
    IAnimal_Weigh = a * b
End Function
