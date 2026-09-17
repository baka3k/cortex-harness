VERSION 5.00
Begin VB.Form frmAbout
   Caption         =   "About"
   ClientHeight    =   1815
   ClientLeft      =   60
   ClientTop       =   345
   ClientWidth     =   3375
   LinkTopic       =   "Form2"
   ScaleHeight     =   1815
   ScaleWidth      =   3375
   StartUpPosition =   3
   Begin VB.Label lblInfo
      Caption         =   "Sample"
      Height          =   255
      Left            =   240
      TabIndex        =   0
      Top             =   240
      Width           =   2895
   End
End
Attribute VB_Name = "frmAbout"
Attribute VB_GlobalNameSpace = False
Attribute VB_Creatable = False
Attribute VB_PredeclaredId = True
Attribute VB_Exposed = False
' Second form fixture: collides on TestSameName with frmMain so unqualified
' calls from standard modules are ambiguous.
Option Explicit

Public Sub TestSameName()
    Debug.Print "frmAbout.TestSameName"
End Sub

Public Sub ShowAbout()
    ' form default instance self reference
    frmAbout.Refresh
End Sub
