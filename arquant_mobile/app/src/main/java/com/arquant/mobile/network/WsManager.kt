package com.arquant.mobile.network

import android.content.Context
import android.util.Log
import com.arquant.mobile.BuildConfig
import com.arquant.mobile.notification.TradeNotifier
import com.arquant.mobile.security.TokenManager
import dagger.hilt.android.qualifiers.ApplicationContext
import kotlinx.coroutines.*
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.asSharedFlow
import kotlinx.serialization.json.Json
import okhttp3.*
import javax.inject.Inject
import javax.inject.Singleton

/**
 * OkHttp WebSocket wrapper that auto-reconnects, emits parsed EventItems,
 * and fires push notifications for trade events.
 */
@Singleton
class WsManager @Inject constructor(
    private val client: OkHttpClient,
    private val json: Json,
    private val tokenManager: TokenManager,
    @ApplicationContext private val appContext: Context,
) {
    companion object {
        private const val TAG = "WsManager"
        private const val RECONNECT_MS = 3000L
    }

    private val _events = MutableSharedFlow<EventItem>(extraBufferCapacity = 256)
    val events = _events.asSharedFlow()

    private var ws: WebSocket? = null
    private var scope: CoroutineScope? = null
    private var running = false

    fun connect() {
        if (running) return
        running = true
        scope = CoroutineScope(Dispatchers.IO + SupervisorJob())
        doConnect()
    }

    fun disconnect() {
        running = false
        ws?.close(1000, "bye")
        ws = null
        scope?.cancel()
        scope = null
    }

    private fun doConnect() {
        if (!running) return
        // 사장 피드백 2026-05-16: WS는 헤더 주입이 까다로워 세션 토큰을 ?token= 쿼리로 전달.
        // (서버 ws_ep 가 쿠키 또는 ?token= 으로 세션 검증)
        val base = BuildConfig.ARQUANT_WS_URL
        val token = tokenManager.get()
        val url = if (token.isBlank()) base
                  else base + (if (base.contains("?")) "&" else "?") + "token=" +
                       java.net.URLEncoder.encode(token, "UTF-8")
        val req = Request.Builder().url(url).build()
        ws = client.newWebSocket(req, object : WebSocketListener() {
            override fun onOpen(webSocket: WebSocket, response: Response) {
                Log.i(TAG, "WS connected")
            }

            override fun onMessage(webSocket: WebSocket, text: String) {
                try {
                    val ev = json.decodeFromString<EventItem>(text)
                    _events.tryEmit(ev)
                    // 매수/매도/실패/사이클완료 → 푸시 알림
                    if (ev.type in setOf("trade_executed", "trade_failed", "cycle_complete")) {
                        TradeNotifier.maybeNotify(appContext, ev)
                    }
                } catch (e: Exception) {
                    Log.w(TAG, "Parse error: ${e.message}")
                }
            }

            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                Log.w(TAG, "WS failure: ${t.message}")
                scheduleReconnect()
            }

            override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
                Log.i(TAG, "WS closed: $code")
                scheduleReconnect()
            }
        })
    }

    private fun scheduleReconnect() {
        if (!running) return
        scope?.launch {
            delay(RECONNECT_MS)
            doConnect()
        }
    }
}
