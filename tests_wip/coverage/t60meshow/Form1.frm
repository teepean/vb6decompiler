VERSION 5.00
Begin VB.Form Form1 
   Caption         =   "F"
   ClientHeight    =   1000
   ClientLeft      =   60
   ClientTop       =   345
   ClientWidth     =   1000
   ScaleHeight     =   1000
   ScaleWidth      =   1000
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
    Print #1, Me.Visible
    Form2.Show
    Print #1, Form2.Visible
    Form2.Hide
    Print #1, Form2.Visible
    Form2.Show vbModeless
    Print #1, Form2.Visible
    Unload Form2
    Close #1
    Unload Me
End Sub
