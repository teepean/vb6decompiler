VERSION 5.00
Begin VB.Form Form1 
   Caption         =   "F"
   ClientHeight    =   1000
   ClientLeft      =   60
   ClientTop       =   345
   ClientWidth     =   1000
   ScaleHeight     =   1000
   ScaleWidth      =   1000
   Begin VB.Timer Tm 
      Enabled         =   0   'False
      Interval        =   1
      Left            =   100
      Top             =   100
   End
End
Attribute VB_Name = "Form1"
Attribute VB_GlobalNameSpace = False
Attribute VB_Creatable = False
Attribute VB_PredeclaredId = True
Attribute VB_Exposed = False
Option Explicit

Private Sub Form_Load()
    Open App.Path & "\out.txt" For Output As #1
    Me.Show
    Print #1, Me.Visible; Tm.Interval
    Close #1
    Unload Me
End Sub
