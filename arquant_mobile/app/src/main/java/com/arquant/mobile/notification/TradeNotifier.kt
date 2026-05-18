package com.arquant.mobile.notification

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.os.Build
import androidx.core.app.NotificationCompat
import com.arquant.mobile.MainActivity
import com.arquant.mobile.network.EventItem

/**
 * 거래 체결/실패 시 시스템 푸시 알림을 전송한다.
 *
 * - trade_executed → 🔼 매수 또는 🔻 매도 체결 알림 (HIGH 중요도)
 * - trade_failed → ⚠️ 주문 실패 알림
 * - cycle_complete → 사이클 완료 요약
 */
object TradeNotifier {

    private const val CHANNEL_ID = "arquant_trades"
    private const val CHANNEL_NAME = "거래 알림"
    private const val CHANNEL_DESC = "매수/매도 체결 및 주문 실패 푸시 알림"

    private var channelCreated = false

    fun maybeNotify(context: Context, ev: EventItem) {
        when (ev.type) {
            "trade_executed" -> {
                val side = if (ev.side.lowercase() == "sell") "매도" else "매수"
                val icon = if (ev.side.lowercase() == "sell") "🔻" else "🔼"
                val title = "$icon $side 체결 — ${ev.ticker}"
                val body = buildString {
                    append("${ev.ticker} ×${ev.qty}")
                    if (ev.filled) append(" ✓체결")
                    if (ev.message.isNotBlank()) append("\n${cleanMsg(ev.message)}")
                    if (ev.tradesTotal != null) append("\n누적 실매매 ${ev.tradesTotal}건")
                }
                fire(context, title, body, ev.hashCode())
            }
            "trade_failed" -> {
                val title = "⚠️ 주문 실패 — ${ev.ticker}"
                val body = cleanMsg(ev.message).ifBlank { "주문 처리 중 오류 발생" }
                fire(context, title, body, ev.hashCode())
            }
            "cycle_complete" -> {
                val title = "🏁 분석 사이클 완료"
                val body = buildString {
                    append(cleanMsg(ev.message).ifBlank { "사이클이 완료되었습니다" })
                    if (ev.tradesTotal != null) append(" · 누적 실매매 ${ev.tradesTotal}건")
                }
                fire(context, title, body, ev.hashCode(), lowPriority = true)
            }
        }
    }

    private fun fire(
        context: Context,
        title: String,
        body: String,
        notifId: Int,
        lowPriority: Boolean = false,
    ) {
        ensureChannel(context)
        val nm = context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager

        val pi = PendingIntent.getActivity(
            context, notifId,
            Intent(context, MainActivity::class.java).apply {
                flags = Intent.FLAG_ACTIVITY_SINGLE_TOP or Intent.FLAG_ACTIVITY_CLEAR_TOP
            },
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )

        val importance = if (lowPriority) NotificationCompat.PRIORITY_DEFAULT
                         else NotificationCompat.PRIORITY_HIGH

        val notif = NotificationCompat.Builder(context, CHANNEL_ID)
            .setSmallIcon(android.R.drawable.ic_dialog_info)
            .setContentTitle(title)
            .setContentText(if (body.length > 120) body.take(117) + "…" else body)
            .setStyle(NotificationCompat.BigTextStyle().bigText(body))
            .setContentIntent(pi)
            .setAutoCancel(true)
            .setPriority(importance)
            .setVisibility(NotificationCompat.VISIBILITY_PUBLIC)
            .setDefaults(NotificationCompat.DEFAULT_ALL)
            .setSubText("ArQuant")
            .build()

        nm.notify(notifId and 0x7FFFFFFF, notif)
    }

    private fun ensureChannel(context: Context) {
        if (channelCreated) return
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val nm = context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
            if (nm.getNotificationChannel(CHANNEL_ID) == null) {
                nm.createNotificationChannel(
                    NotificationChannel(CHANNEL_ID, CHANNEL_NAME, NotificationManager.IMPORTANCE_HIGH).apply {
                        description = CHANNEL_DESC
                        enableLights(true)
                        enableVibration(true)
                    }
                )
            }
        }
        channelCreated = true
    }

    private fun cleanMsg(t: String): String =
        t.replace(Regex("#+"), " ").replace(Regex("\\*+"), " ").replace(Regex("[ \\t]{3,}"), "  ").trim()
}
