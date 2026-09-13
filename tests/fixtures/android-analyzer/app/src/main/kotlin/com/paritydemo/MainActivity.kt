package com.paritydemo

import android.content.BroadcastReceiver
import android.content.Intent
import android.content.IntentFilter
import android.os.Bundle
import androidx.appcompat.app.AppCompatActivity
import com.paritydemo.data.SyncService
import com.paritydemo.ui.NavGraph
import com.paritydemo.ui.DetailScreen

/**
 * Launcher activity: wires navigation, intents and broadcast plumbing.
 */
class MainActivity : AppCompatActivity() {

    private lateinit var bootReceiver: BroadcastReceiver

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        val detailIntent = Intent(this, DetailActivity::class.java)
        detailIntent.action = "com.paritydemo.ACTION_OPEN_DETAIL"
        startActivity(detailIntent)

        startService(Intent(this, SyncService::class.java))

        val filter = IntentFilter("com.paritydemo.ACTION_PING")
        registerReceiver(bootReceiver, filter)

        sendBroadcast(Intent("com.paritydemo.ACTION_SYNC_NOW"))

        NavGraph.openDetail("home")
    }
}

/**
 * Application entry declared in the manifest.
 */
class ParityApp : android.app.Application() {
    override fun onCreate() {
        super.onCreate()
    }
}
