VERSION 1.0 CLASS
BEGIN
  MultiUse = -1
END
Attribute VB_Name = "clsShip"
' Class fixture: second IShip implementer so interface dispatch is genuinely
' ambiguous for static resolution.
Option Explicit

Implements IShip

Public Sub Ship_Order(ByVal orderId As Long)
    LogMessage "ship " & CStr(orderId)
End Sub

Private Sub IShip_Ship_Order(ByVal orderId As Long)
    Ship_Order orderId
End Sub
