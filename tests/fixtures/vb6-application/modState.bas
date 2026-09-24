Attribute VB_Name = "modState"
' Anchor fixture (plan 260924): `Global` keyword (S1: VisibilityEnum.GLOBAL
' must keep is_global=true) + module constant + public state written/read by
' form handlers (golden UI trace M5).
Option Explicit

Global AppStatus As Integer

Public Const MAX_LOGIN_TRIES As Integer = 3

Public Sub ResetStatus()
    ' module state write + const read anchor
    AppStatus = 0
    If AppStatus >= MAX_LOGIN_TRIES Then
        AppStatus = MAX_LOGIN_TRIES
    End If
End Sub
