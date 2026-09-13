package com.paritydemo.ui

import androidx.compose.runtime.Composable
import androidx.compose.ui.tooling.preview.Preview
import com.paritydemo.data.UserRepository

/**
 * Navigation graph: every route targets a @Composable destination.
 */
@Composable
fun ParityNavHost(startRoute: String) {
    wireRoutes(startRoute)
}

private fun wireRoutes(startRoute: String) {
    routeTable("home", content = { HomeScreen() })
    routeTable("detail/{userId}", content = { DetailScreen(userId = "7") })
    routeTable(target = "settings", via = ::SettingsScreen)
}

@Composable
fun HomeScreen() {
    headline()
    UserRepository().displayName("7")
}

@Composable
fun DetailScreen(userId: String) {
    headline()
    UserRepository().displayName(userId)
}

@Preview
@Composable
fun SettingsScreen() {
    headline()
}

@Composable
private fun headline() {
    androidcompose.Text("parity")
}
