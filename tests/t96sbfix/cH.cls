VERSION 1.0 CLASS
BEGIN
  MultiUse = -1  'True
END
Attribute VB_Name = "cH"
Attribute VB_GlobalNameSpace = False
Attribute VB_Creatable = True
Attribute VB_PredeclaredId = False
Attribute VB_Exposed = False
Option Explicit

' Object members next to each other, nothing in them yet; a Picture whose members go by DISPID.
Private mPic As Picture
Private mObj As Object
Private mOther As Long

Public Property Set Pic(ByVal x As Picture)
    Set mPic = x
End Property

Public Function Empty1() As Boolean
    Empty1 = mPic Is Nothing
End Function

Public Function Empty2() As Boolean
    Empty2 = mObj Is Nothing
End Function

Private Function Make() As Variant
    Set Make = New cP
End Function

Public Sub Fill()
    Set mObj = Make()
    mOther = 7
End Sub

Public Function PicType() As Long
    PicType = mPic.Type
End Function

Public Function PicWide() As Long
    PicWide = Measure(mPic)
End Function

Private Function Measure(ByVal p As Picture) As Long
    Measure = p.Width + p.Height + p.Type
End Function
