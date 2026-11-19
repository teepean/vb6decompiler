VERSION 1.0 CLASS
BEGIN
  MultiUse = -1  'True
  Persistable = 0  'NotPersistable
  DataBindingBehavior = 0  'vbNone
  DataSourceBehavior  = 0  'vbNone
  MTSTransactionMode  = 0  'NotAnMTSObject
END
Attribute VB_Name = "cDog"
Attribute VB_GlobalNameSpace = False
Attribute VB_Creatable = True
Attribute VB_PredeclaredId = False
Attribute VB_Exposed = False
Option Explicit
Implements IAnimal
Implements ILog

Private mLegs As Long
Private mField As Long
Private mOwner As Object
Private mName As String
Private mLines As Long

Private Sub Class_Initialize()
    mLegs = 4
    mName = "dog"
End Sub

Private Property Let IAnimal_Field(ByVal RHS As Long)
    mField = RHS * 2
End Property

Private Property Get IAnimal_Field() As Long
    IAnimal_Field = mField + 1
End Property

Private Function IAnimal_Speak(ByVal n As Long) As String
    IAnimal_Speak = String$(n, "w") & mName
End Function

Private Property Get IAnimal_Legs() As Long
    IAnimal_Legs = mLegs
End Property

Private Property Let IAnimal_Legs(ByVal v As Long)
    mLegs = v
End Property

Private Property Get IAnimal_Owner() As Object
    Set IAnimal_Owner = mOwner
End Property

Private Property Set IAnimal_Owner(ByVal o As Object)
    Set mOwner = o
End Property

Private Sub IAnimal_Rename(s As String, Optional ByVal suffix As String = "!")
    mName = s & suffix
    s = "was " & s
End Sub

Private Function IAnimal_Mate() As IAnimal
    Set IAnimal_Mate = New cCat
End Function

Private Function IAnimal_Weigh(ByVal a As Double, b As Variant, Optional c As Variant) As Double
    If IsMissing(c) Then
        IAnimal_Weigh = a + b
    Else
        IAnimal_Weigh = a + b + c
    End If
End Function

Private Sub ILog_Note(ByVal s As String)
    mLines = mLines + Len(s)
End Sub

Private Function ILog_Lines() As Long
    ILog_Lines = mLines
End Function

Public Function OwnMethod() As String
    OwnMethod = "own" & mName & mLines
End Function
