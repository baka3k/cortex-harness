Attribute VB_Name = "modB"
' Stable-denominator fixture (red-team F4): callee side of the modA/modB pair.
Option Explicit

Public Sub B_Run()
    A_Run
    Dim v As Double
    v = A_Calc(2.5)
End Sub

Public Sub B_Helper(ByVal n As Long)
    Debug.Print n
End Sub

Public Function B_Check(ByVal n As Long) As Boolean
    B_Check = (n > 0)
End Function

Public Function B_Calc(ByVal x As Double) As Double
    B_Calc = A_Calc(x) + 1
End Function
