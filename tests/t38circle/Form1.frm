VERSION 5.00
Begin VB.Form Form1 
   AutoRedraw      =   -1  'True
   Caption         =   "F"
   ClientHeight    =   3000
   ClientLeft      =   60
   ClientTop       =   345
   ClientWidth     =   4000
   ScaleHeight     =   200
   ScaleMode       =   3  'Pixel
   ScaleWidth      =   266
   Begin VB.PictureBox p1 
      AutoRedraw      =   -1  'True
      BackColor       =   &H00FFFFFF&
      BorderStyle     =   0  'None
      Height          =   1500
      Left            =   0
      ScaleHeight     =   100
      ScaleMode       =   3  'Pixel
      ScaleWidth      =   100
      TabIndex        =   0
      Top             =   0
      Width           =   1500
   End
End
Attribute VB_Name = "Form1"
Attribute VB_GlobalNameSpace = False
Attribute VB_Creatable = False
Attribute VB_PredeclaredId = True
Attribute VB_Exposed = False
Option Explicit

' Pixels set in a 100x100 box: the count and a checksum of where they are
Private Function Sig(pb As PictureBox) As String
    Dim x As Long, y As Long, n As Long, h As Long, c As Long
    For y = 0 To 99
        For x = 0 To 99
            c = pb.Point(x, y)
            If c <> vbWhite Then
                n = n + 1
                h = (h * 3 + x * 7 + y + (c And &HFF)) And &HFFFFFF
            End If
        Next x
    Next y
    Sig = n & "/" & h
    pb.Cls
End Function

Private Sub Report(ByVal t As String)
    Print #1, t; " "; Sig(p1); p1.CurrentX; p1.CurrentY
End Sub

Private Sub Form_Load()
    Dim sx As Single, sy As Single, r As Single, il As Long, ii As Integer, v As Variant
    Dim d As Double, col As Long, o As Object
    Open App.Path & "\out.txt" For Output As #1
    p1.Circle (50, 50), 20
    Report "plain"
    p1.Circle (50, 50), 20, vbRed
    Report "color"
    p1.Circle (50, 50), 20, , , , 0.5
    Report "aspect"
    p1.Circle (50, 50), 20, vbBlue, , , 2
    Report "color aspect"
    p1.Circle (50, 50), 20, , 0, 1.57
    Report "arc"
    p1.Circle (50, 50), 20, vbGreen, -0.5, -2
    Report "pie"
    p1.Circle (50, 50), 20, , 0.5, 2, 0.5
    Report "arc aspect"
    p1.Circle Step(10, 20), 5
    Report "step"
    p1.CurrentX = 30: p1.CurrentY = 40
    p1.Circle Step(5, 5), 5
    Report "step2"
    sx = 40: sy = 45: r = 12.5
    p1.Circle (sx, sy), r
    Report "single"
    il = 60: ii = 30: d = 10
    p1.Circle (il, ii), d
    Report "long int dbl"
    v = 35
    p1.Circle (v, v + 1), v / 2
    Report "variant"
    col = RGB(1, 2, 3)
    p1.Circle (sx + 1, sy * 2 - 10), r / 2, col
    Report "expr"
    p1.FillStyle = 0
    p1.FillColor = RGB(10, 20, 30)
    p1.Circle (50, 50), 10
    Report "filled"
    p1.FillStyle = 1
    ' (.Circle inside With: VB6 itself says Syntax error)
    p1.Circle (25, 25), 15, vbMagenta
    Report "with"
    Set o = p1
    o.Circle (25, 25), 15, vbCyan
    Report "late"
    o.Circle Step(1, 1), 3, , , , 2
    Report "late step"
    Me.Circle (10, 10), 5, vbRed
    Print #1, Me.Point(15, 10); Me.Point(10, 15); Me.Point(10, 10); Me.CurrentX; Me.CurrentY
    Circle (40, 40), 3
    Print #1, Point(43, 40); Point(40, 40); CurrentX; CurrentY
    Close #1
    Unload Me
End Sub
