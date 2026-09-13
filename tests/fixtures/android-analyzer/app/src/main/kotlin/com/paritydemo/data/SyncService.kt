package com.paritydemo.data

import android.app.Service
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.os.IBinder

/**
 * Foreground sync service declared in the manifest.
 */
class SyncService : Service() {
    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        pump()
        return START_STICKY
    }

    fun pump() {
        UserRepository().wireAll()
    }
}

/**
 * Receiver declared in the manifest with two intent actions.
 */
class BootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context?, intent: Intent?) {
        val action = intent?.action
        if (action == "android.intent.action.BOOT_COMPLETED") {
            schedule(context)
        }
    }

    fun schedule(context: Context?) {
        context?.startService(Intent(context, SyncService::class.java))
    }
}

class NotesProvider {
    fun notes(): List<User> {
        return emptyList()
    }
}
