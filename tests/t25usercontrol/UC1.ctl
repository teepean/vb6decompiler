VERSION 5.00
Begin VB.UserControl UC1 
   ClientHeight    =   600
   ClientLeft      =   0
   ClientTop       =   0
   ClientWidth     =   1500
   ScaleHeight     =   600
   ScaleWidth      =   1500
   Begin VB.Label lbl 
      Caption         =   "x"
      Height          =   255
      Left            =   60
      TabIndex        =   0
      Top             =   60
      Width           =   900
   End
End
Attribute VB_Name = "UC1"
Attribute VB_GlobalNameSpace = False
Attribute VB_Creatable = True
Attribute VB_PredeclaredId = False
Attribute VB_Exposed = False
Option Explicit

Public Event Changed(ByVal newVal As Long)
Private mVal As Long
Private mTag As String

Public Property Get Value() As Long
    Value = mVal
End Property

Public Property Let Value(ByVal v As Long)
    mVal = v
    lbl.Caption = CStr(v)
    PropertyChanged "Value"
    RaiseEvent Changed(v)
End Property

Public Property Get TagText() As String
    TagText = mTag
End Property

Public Property Let TagText(ByVal s As String)
    mTag = s
    PropertyChanged "TagText"
End Property

Public Function Twice() As Long
    Twice = mVal * 2
End Function

Public Property Get LabelText() As String
    LabelText = lbl.Caption
End Property

Private Sub UserControl_InitProperties()
    mVal = 7
    mTag = "init"
    lbl.Caption = "i"
End Sub

Private Sub UserControl_ReadProperties(PropBag As PropertyBag)
    mVal = PropBag.ReadProperty("Value", 7)
    mTag = PropBag.ReadProperty("TagText", "dflt")
    lbl.Caption = "r" & mVal
End Sub

Private Sub UserControl_WriteProperties(PropBag As PropertyBag)
    PropBag.WriteProperty "Value", mVal, 7
    PropBag.WriteProperty "TagText", mTag, "dflt"
End Sub

Private Sub UserControl_Resize()
    lbl.Width = UserControl.ScaleWidth
End Sub
