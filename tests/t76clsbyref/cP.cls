VERSION 1.0 CLASS
BEGIN
  MultiUse = -1  'True
END
Attribute VB_Name = "cP"
Attribute VB_GlobalNameSpace = False
Attribute VB_Creatable = True
Attribute VB_PredeclaredId = False
Attribute VB_Exposed = False
Option Explicit

Public Sub Kirjoita(ByRef tyyppi As Long, nro As Integer, teksti As String, Optional siirra As Boolean = False)
    Print #1, tyyppi, nro, teksti, siirra
    teksti = teksti & "+"
End Sub

Public Function Sis(tyyppi As Long, teksti As String) As Boolean
    Sis = (Len(teksti) > tyyppi)
End Function
