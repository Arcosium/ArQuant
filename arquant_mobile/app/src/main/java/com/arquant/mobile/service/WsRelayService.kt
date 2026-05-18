package com.arquant.mobile.service

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Intent
import android.os.Build
import android.os.IBinder
import androidx.core.app.NotificationCompat
import com.arquant.mobile.MainActivity
import com.arquant.mobile.R

/**
 * Foreground service placeholder for keeping WebSocket alive when app is backgrounded.
 * The actual WS connection is managed by WsManager (singleton).
 * This service just provides the persistent notification required by Android.
 */
class WsRelayService : Service() {

    companion object {
        private const val CHANNEL_ID = "arquant_ws_relay"
        private const val NOTIF_ID = 9001
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        createChannel()
        val pi = PendingIntent.getActivity(
            this, 0,
            Intent(this, MainActivity::class.java).apply {
                flags = Intent.FLAG_ACTIVITY_SINGLE_TOP
            },
            PendingIntent.FLAG_IMMUTABLE,
        )
        val notif = NotificationCompat.Builder(this, CHANNEL_ID)
            .setSmallIcon(android.R.drawable.ic_dialog_info)
            .setContentTitle("ArQuant")
            .setContentText("실시간 모니터링 중")
            .setOngoing(true)
            .setContentIntent(pi)
            .build()
        startForeground(NOTIF_ID, notif)
    }

    private fun createChannel() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val nm = getSystemService(NOTIFICATION_SERVICE) as NotificationManager
            if (nm.getNotificationChannel(CHANNEL_ID) == null) {
                nm.createNotificationChannel(
                    NotificationChannel(CHANNEL_ID, "ArQuant 실시간", NotificationManager.IMPORTANCE_LOW).apply {
                        description = "WebSocket 실시간 연결 유지"
                    }
                )
            }
        }
    }
}
