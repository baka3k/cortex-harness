Attribute VB_Name = "modA"
' Stable-denominator fixture (red-team F4): plain resolvable calls between
' standard modules so M2 recall has enough routine samples in addition to the
' special cases.
Option Explicit

Public Sub A_Run()
    B_Helper 1
    If B_Check(2) Then
        Debug.Print "ok"
    End If
    Dim v As Double
    v = B_Calc(1.5)
End Sub

Public Function A_Calc(ByVal x As Double) As Double
    A_Calc = x * 2
End Function

Public Sub A_UseUtil()
    Dim t As Double
    t = CalcTotal(A_Calc(1), 2)
    LogMessage "a"
End Sub
