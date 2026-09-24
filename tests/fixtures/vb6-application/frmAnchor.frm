VERSION 5.00
Begin VB.Form frmAnchor
   Caption         =   "Anchor"
   ClientHeight    =   2400
   ClientLeft      =   60
   ClientTop       =   345
   ClientWidth     =   5000
   LinkTopic       =   "Form1"
   ScaleHeight     =   2400
   ScaleWidth      =   5000
   StartUpPosition =   3
   Begin VB.CommandButton cmdSubmit
      Caption         =   "Submit"
      Height          =   495
      Left            =   120
      TabIndex        =   0
      Top             =   240
      Width           =   1215
   End
   Begin VB.TextBox txtUsername
      Height          =   285
      Left            =   120
      TabIndex        =   1
      Text            =   ""
      Top             =   900
      Width           =   3000
   End
   Begin VB.TextBox txtPassword
      Height          =   285
      Left            =   120
      TabIndex        =   2
      Text            =   ""
      Top             =   1300
      Width           =   3000
   End
   Begin VB.ComboBox cboType
      Height          =   315
      Left            =   120
      TabIndex        =   3
      Text            =   ""
      Top             =   1700
      Width           =   3000
   End
End
Attribute VB_Name = "frmAnchor"
Attribute VB_GlobalNameSpace = False
Attribute VB_Creatable = False
Attribute VB_PredeclaredId = True
Attribute VB_Exposed = False
' Golden UI-trace fixture (plan 260924 M5): cmdSubmit_Click reads control
' state (txtUsername/txtPassword .Text), writes module state (AppStatus),
' navigates through `With <form> ... .Show` (corpus addbook.frm:529 pattern),
' configures a control through With (cboType.AddItem, superadmin_createdb
' pattern), ReDims a buffer, and calls a cross-module reset. Form_QueryUnload
' exercises the pseudo-control -> Type wiring.
Option Explicit

Private Sub cmdSubmit_Click()
    Dim filled As Boolean
    filled = Len(txtUsername.Text) > 0
    If Len(txtPassword.Text) = 0 Then
        txtPassword.SetFocus
        Exit Sub
    End If
    AppStatus = 1
    With frmMain
        .Show
    End With
    With cboType
        .AddItem "admin"
        .ListIndex = 0
    End With
    Dim buffer() As String
    ReDim Preserve buffer(10)
    modState.ResetStatus
End Sub

Private Sub Form_Load()
    AppStatus = 0
End Sub

Private Sub Form_QueryUnload(Cancel As Integer, UnloadMode As Integer)
    modState.ResetStatus
End Sub
