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

' A record of numbers in the object: QStack.val(i) is [Me + i * 8 + c], no SAFEARRAY.
Private Type TPinoD
    arvo(20) As Double
    paikka As Integer
End Type
Private q As TPinoD
Private kerrat As Long

Public Sub Tyonna(ByVal x As Double)
    Dim a As Integer
    If q.paikka >= 20 Then
        For a = 0 To 18
            q.arvo(a) = q.arvo(a + 1)
        Next a
        q.paikka = 19
    End If
    q.arvo(q.paikka) = x
    q.paikka = q.paikka + 1
End Sub

Public Function Ota() As Double
    q.paikka = q.paikka - 1
    If q.paikka < 0 Then
        q.paikka = 0
        q.arvo(0) = 0
    End If
    Ota = q.arvo(q.paikka)
End Function

' A Static fixed array of an object lives in a block of its own, with its SAFEARRAY there.
Public Function Keskiarvo(ByVal x As Long) As Long
    Static hist(4) As Long
    Static monta As Long
    Dim i As Integer, s As Long
    hist(monta Mod 5) = x
    monta = monta + 1
    For i = 0 To 4
        s = s + hist(i)
    Next i
    Keskiarvo = s \ 5
    kerrat = kerrat + 1
End Function

Public Property Get Kerrat2() As Long
    Kerrat2 = kerrat
End Property
