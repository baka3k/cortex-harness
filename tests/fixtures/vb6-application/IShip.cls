VERSION 1.0 CLASS
BEGIN
  MultiUse = -1
END
Attribute VB_Name = "IShip"
' Interface fixture: a plain VB6 class whose procedures are only Implements
' targets. Emitted as an Interface node so IMPLEMENTS edges have a target.
Option Explicit

Public Sub Ship_Order(ByVal orderId As Long)
End Sub

Public Function Ship_Status(ByVal orderId As Long) As Long
    Ship_Status = 0
End Function
