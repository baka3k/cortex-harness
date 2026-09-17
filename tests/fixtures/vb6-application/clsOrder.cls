VERSION 1.0 CLASS
BEGIN
  MultiUse = -1
END
Attribute VB_Name = "clsOrder"
' Class fixture: Property Get/Let Total, method ProcessOrder, Implements IShip.
Option Explicit

Private mTotal As Double

Implements IShip

Public Property Get Total() As Double    ' trailing comment: read the total
    Total = mTotal
End Property

Public Property Let Total(ByVal newValue As Double)
    mTotal = newValue
End Property

Public Sub ProcessOrder(ByVal orderId As Long)
    Total = 42
    Dim amount As Double
    amount = Total
    LogMessage CStr(orderId)
End Sub

Private Sub IShip_Ship_Order(ByVal orderId As Long)
    ProcessOrder orderId
End Sub
