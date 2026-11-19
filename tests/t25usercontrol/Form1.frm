VERSION 5.00
Begin VB.Form Form1 
   Caption         =   "F"
   ClientHeight    =   3000
   ClientLeft      =   60
   ClientTop       =   345
   ClientWidth     =   4000
   ScaleHeight     =   3000
   ScaleWidth      =   4000
   Begin t25usercontrol.UC1 uc2 
      Height          =   600
      Left            =   100
      TabIndex        =   1
      Top             =   800
      Width           =   1500
      _ExtentX        =   2646
      _ExtentY        =   1058
   End
   Begin t25usercontrol.UC1 uc1 
      Height          =   600
      Left            =   100
      TabIndex        =   0
      Top             =   100
      Width           =   1500
      _ExtentX        =   2646
      _ExtentY        =   1058
      Value           =   11
      TagText         =   "design"
   End
End
Attribute VB_Name = "Form1"
Attribute VB_GlobalNameSpace = False
Attribute VB_Creatable = False
Attribute VB_PredeclaredId = True
Attribute VB_Exposed = False
Option Explicit

Private Sub uc1_Changed(ByVal newVal As Long)
    Print #1, "changed"; newVal
End Sub

Private Sub Form_Load()
    Dim o As Object, c As UC1
    Open App.Path & "\out.txt" For Output As #1
    Print #1, uc1.Value; uc1.TagText; uc1.LabelText
    Print #1, uc2.Value; uc2.TagText; uc2.LabelText
    uc1.Value = 21
    Print #1, uc1.Twice(); uc1.LabelText
    uc1.TagText = "run"
    Print #1, uc1.TagText
    Set c = uc2
    c.Value = 5
    Print #1, c.Twice()
    Set o = uc1
    Print #1, o.Value; o.Left; o.Top; o.Width; o.Height; o.Name
    Print #1, uc1.Visible; uc2.TabIndex
    Close #1
    Unload Me
End Sub
