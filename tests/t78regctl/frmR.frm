VERSION 5.00
Begin VB.Form frmR 
   Caption         =   "R"
   ClientHeight    =   3000
   ClientLeft      =   60
   ClientTop       =   345
   ClientWidth     =   4000
   LinkTopic       =   "Form1"
   ScaleHeight     =   3000
   ScaleWidth      =   4000
   StartUpPosition =   3  'Windows Default
   Begin VB.ListBox lst 
      Height          =   450
      Left            =   120
      TabIndex        =   1
      Top             =   1200
      Width           =   1500
   End
   Begin VB.TextBox txt 
      Height          =   285
      Left            =   120
      TabIndex        =   0
      Text            =   "teksti"
      Top             =   800
      Width           =   1500
   End
   Begin VB.Label lbl 
      Caption         =   "otsikko"
      Height          =   255
      Left            =   120
      TabIndex        =   2
      Top             =   300
      Width           =   1500
   End
End
Attribute VB_Name = "frmR"
Attribute VB_GlobalNameSpace = False
Attribute VB_Creatable = False
Attribute VB_PredeclaredId = True
Attribute VB_Exposed = False
Option Explicit

Private Type TRivi
    a As Long
    b As Integer
    c As Integer
    d As Integer
    e As Integer
    n As String
    p(0 To 4) As Long
End Type
Private mR() As TRivi

' The compiler merges the same last statements of two arms: a control is left in a register
' across the If, and a register's later webs had no object type (in a real program a label's
' Caption was taken for the label, and its .Top raised error 424).
Private mC As New Collection

Private Sub Siirra(a As Single)
    Dim h As Single
    h = lst.Height
    If txt.Width > a Then
        txt.Width = a
        h = lst.Top
        lst.Height = Abs(ScaleHeight - h - 520)
        lbl.Top = Abs(ScaleHeight - 420)
    Else
        txt.Height = a
        h = lst.Top
        lst.Height = Abs(ScaleHeight - h - 520)
        lbl.Top = Abs(ScaleHeight - 420)
    End If
    Print #1, "siirra", txt.Width, lbl.Top, lst.Height
End Sub

' A Dim As New is tested with cmp [var], 0 before each use: no m = 0 on a Nothing.
Private Sub Kerro(s As String)
    mC.Add s
    If mC.Count > 1 Then
        mC.Add s & "2"
    End If
    Print #1, "kerro", mC.Count, mC(1)
End Sub

' A handler starting with the auto-instancing test of a form: its first block is empty after
' simplifying and no edge reaches it (a real program's Form_Load lost its Unload Me).
Private Sub Putoa(n As Integer)
    On Error GoTo h
    If n > 0 Then Err.Raise 5
    Print #1, "ei virhetta", n
    Exit Sub
h:
    frmR.Tag = "h" & n
    Print #1, "kasittelija", Err.Number, frmR.Tag
End Sub

' An Integer field read by late binding goes through a VT_BYREF Variant: its address read as 4
' bytes made it a Long, and a Long moves every field after it (seen in a real program).
Private Sub Kasvata(x As Long)
    x = x + 1
End Sub

Private Sub Rivit(k As Integer)
    Dim o As Object
    ReDim mR(1 To 3, 1 To 2)
    Set o = Me
    mR(2, 1).a = 7: mR(2, 1).b = 1: mR(2, 1).c = 22: mR(2, 1).d = 3: mR(2, 1).e = 4: mR(2, 1).n = "nimi"
    mR(2, 2) = mR(2, 1)
    mR(2, 2).d = mR(2, 1).e
    If o.TextWidth(mR(2, 1).c) > 0 Then mR(2, 2).e = -1
    ' a subscript minus one, an array in the record, by reference
    mR(2, 2).p(k - 1) = 40
    Kasvata mR(2, 2).p(k - 1)
    Print #1, "rivit", mR(2, 2).a, mR(2, 2).b, mR(2, 2).c, mR(2, 2).d, mR(2, 2).e, mR(2, 2).n, mR(2, 2).p(k - 1), Len(mR(2, 2).n)
End Sub

Private Sub Form_Load()
    Open "out.txt" For Output As #1
    Siirra 100
    Siirra 5000
    Kerro "a"
    Kerro "b"
    Putoa 0
    Putoa 1
    Rivit 2
    Close #1
    End
End Sub
