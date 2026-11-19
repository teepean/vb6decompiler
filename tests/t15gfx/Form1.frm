VERSION 5.00
Begin VB.Form Form1 
   Caption = "F"
   ClientHeight = 3000
   ClientLeft = 60
   ClientTop = 345
   ClientWidth = 4000
   ScaleHeight = 3000
   ScaleWidth = 4000
   Begin VB.PictureBox p1 
      Height = 2000
      Left = 100
      ScaleHeight = 1940
      ScaleWidth = 2940
      TabIndex = 0
      Top = 100
      Width = 3000
   End
End
Attribute VB_Name = "Form1"
Attribute VB_GlobalNameSpace = False
Attribute VB_Creatable = False
Attribute VB_PredeclaredId = True
Attribute VB_Exposed = False
Private Sub Form_Load()
    Dim o As Object
    Set o = p1
    Open App.Path & "\out.txt" For Output As #1
    p1.Line (10, 20)-(30, 40)
    p1.Line -(30, 40)
    p1.Line Step(1, 2)-Step(3, 4)
    p1.Line (10, 20)-(30, 40), vbRed
    p1.Line (10, 20)-(30, 40), , B
    p1.Line (10, 20)-(30, 40), vbRed, BF
    p1.Line (10, 20)-Step(3, 4), , BF
    p1.PSet (5, 6)
    p1.PSet Step(5, 6), vbBlue
    p1.Circle (50, 60), 7
    p1.Circle (50, 60), 7, vbGreen, 0.5, 1.5, 2
    o.Line (10, 20)-(30, 40)
    o.Line -(30, 40)
    o.Line Step(1, 2)-Step(3, 4)
    o.Line (10, 20)-(30, 40), vbRed
    o.Line (10, 20)-(30, 40), , B
    o.Line (10, 20)-(30, 40), vbRed, BF
    o.Line (10, 20)-Step(3, 4), , BF
    o.PSet (5, 6)
    o.PSet Step(5, 6), vbBlue
    o.Circle (50, 60), 7
    o.Circle (50, 60), 7, vbGreen, 0.5, 1.5, 2
    Print #1, p1.CurrentX; p1.CurrentY
    o.Scale (0, 0)-(200, 100)
    Print #1, o.ScaleWidth; o.ScaleHeight
    p1.Scale (10, 10)-(50, 30)
    Print #1, p1.ScaleWidth; p1.ScaleHeight
    p1.Scale
    Print #1, p1.ScaleMode
    Close #1
    Unload Me
End Sub
