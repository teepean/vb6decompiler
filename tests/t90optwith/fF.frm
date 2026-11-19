VERSION 5.00
Begin VB.Form fF
   Caption         =   "f"
   ClientHeight    =   1000
   ClientLeft      =   60
   ClientTop       =   400
   ClientWidth     =   2000
   LinkTopic       =   "Form1"
   ScaleHeight     =   1000
   ScaleWidth      =   2000
   StartUpPosition =   3
End
Attribute VB_Name = "fF"
Attribute VB_GlobalNameSpace = False
Attribute VB_Creatable = False
Attribute VB_PredeclaredId = True
Attribute VB_Exposed = False
Option Explicit

Private Type TPino
    pos As Integer
    val(10) As Double
End Type

Private Q As TPino
Public Tulos As Double

Private Sub Push(ByVal x As Double)
    Dim a As Integer
    If Q.pos >= 11 Then
        For a = 0 To 9
            Q.val(a) = Q.val(a + 1)
        Next a
        Q.val(10) = 0
        Q.pos = 10
    End If
    Q.val(Q.pos) = x
    Q.pos = Q.pos + 1
End Sub

Private Function Pop() As Double
    Q.pos = Q.pos - 1
    If Q.pos = -1 Then
        Q.pos = 0
        Q.val(0) = 0
    End If
    Pop = Q.val(Q.pos)
End Function

Private Sub Yhd()
    Dim a As Double, b As Double
    b = Pop
    a = Pop
    Push a + b
End Sub

Public Sub Tee()
    Dim i As Integer
    Push 1.5
    Push 2.25
    Yhd
    Tulos = Pop
    For i = 1 To 15
        Push i
    Next i
    Tulos = Tulos + Pop + Pop
    fF.Caption = fF.Caption & "x"
    If Right(fF.Caption, 1) <> "x" Then Tulos = -1
End Sub
