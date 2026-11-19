Attribute VB_Name = "mM"
Option Explicit

Sub Main()
    Dim rt As Double, n As Double, pv0 As Double, fv0 As Double, d As Double
    Dim vr As Variant, vn As Variant, vp As Variant
    Dim lg As Long, ty As Long
    Dim cash(3) As Double
    Open App.Path & "\out.txt" For Output As #1
    rt = 0.01: n = 12: pv0 = -1000: fv0 = 100
    vr = 0.01: vn = 12: vp = -1000
    lg = 12: ty = 1
    ' Pmt
    Print #1, "Pmt"; Pmt(0.01, 12, -1000)
    Print #1, Pmt(rt, n, pv0)
    Print #1, Pmt(rt, n, pv0, fv0)
    Print #1, Pmt(rt, n, pv0, fv0, ty)
    Print #1, Pmt(vr, vn, vp)
    Print #1, Pmt(rt, lg, pv0)
    d = Pmt(rt, n, pv0) * 2 + 1
    Print #1, d
    ' IPmt / PPmt
    Print #1, "IPmt"; IPmt(rt, 1, n, pv0)
    Print #1, IPmt(rt, 2, n, pv0, fv0)
    Print #1, IPmt(rt, 2, n, pv0, fv0, ty)
    Print #1, "PPmt"; PPmt(rt, 1, n, pv0)
    Print #1, PPmt(rt, 2, n, pv0, fv0)
    Print #1, PPmt(rt, 2, n, pv0, fv0, ty)
    ' PV / FV
    Print #1, "PV"; PV(rt, n, -50)
    Print #1, PV(rt, n, -50, fv0)
    Print #1, PV(rt, n, -50, fv0, ty)
    Print #1, "FV"; FV(rt, n, -50)
    Print #1, FV(rt, n, -50, pv0)
    Print #1, FV(rt, n, -50, pv0, ty)
    ' NPV / IRR / MIRR
    cash(0) = -1000: cash(1) = 300: cash(2) = 400: cash(3) = 500
    Print #1, "NPV"; NPV(0.05, cash())
    d = NPV(rt, cash())
    Print #1, d
    Print #1, "IRR"; IRR(cash())
    Print #1, IRR(cash(), 0.2)
    Print #1, "MIRR"; MIRR(cash(), 0.1, 0.12)
    d = MIRR(cash(), rt, rt)
    Print #1, d
    ' NPer / Rate
    Print #1, "NPer"; NPer(rt, -100, 1000)
    Print #1, NPer(rt, -100, 1000, 50)
    Print #1, NPer(rt, -100, 1000, 50, ty)
    Print #1, "Rate"; Rate(12, -100, 1000)
    Print #1, Rate(12, -100, 1000, 50)
    Print #1, Rate(12, -100, 1000, 50, ty)
    Print #1, Rate(12, -100, 1000, 50, ty, 0.2)
    ' depreciation
    Print #1, "DDB"; DDB(1000, 100, 5, 1)
    Print #1, DDB(1000, 100, 5, 2, 1.5)
    Print #1, "SLN"; SLN(1000, 100, 5)
    Print #1, "SYD"; SYD(1000, 100, 5, 1)
    d = SLN(pv0 * -1, fv0, n) + SYD(pv0 * -1, fv0, n, 3) + DDB(pv0 * -1, fv0, n, 3)
    Print #1, d
    Close #1
End Sub
