package com.arquant.mobile.widget

import android.content.Context
import android.util.Log
import com.arquant.mobile.BuildConfig
import okhttp3.OkHttpClient
import okhttp3.Request
import org.json.JSONObject
import java.util.concurrent.TimeUnit

/**
 * Widget 전용 경량 HTTP 헬퍼. Hilt DI를 쓸 수 없는 AppWidgetProvider/RemoteViewsService에서
 * /api/balance 를 직접 호출한다. 동기 호출이므로 반드시 백그라운드 스레드에서 사용할 것.
 *
 * 사장 피드백 2026-05-16: Cloudflare Access 제거 → 앱 자체 로그인 세션 토큰을
 * TokenManager 와 동일한 SharedPreferences("arquant_auth"/"session_token")에서 직접 읽어
 * X-Session 헤더로 전송한다 (위젯은 Hilt 주입 불가라 prefs 직접 접근).
 */
internal object WidgetHttp {
    private const val TAG = "WidgetHttp"

    private val client by lazy {
        OkHttpClient.Builder()
            .connectTimeout(10, TimeUnit.SECONDS)
            .readTimeout(15, TimeUnit.SECONDS)
            .followRedirects(false)
            .build()
    }

    private fun sessionToken(context: Context): String =
        context.getSharedPreferences("arquant_auth", Context.MODE_PRIVATE)
            .getString("session_token", "")
            .orEmpty()

    private fun base(): String =
        BuildConfig.ARQUANT_BASE_URL.let { if (it.endsWith("/")) it.dropLast(1) else it }

    /**
     * GET /api/balance → { buying_power: {...}, holdings: [...] }
     * 반환: 전체 JSON 또는 실패(미로그인 401 포함) 시 null.
     */
    fun fetchBalance(context: Context): JSONObject? {
        val url = "${base()}/api/balance"
        return runCatching {
            val token = sessionToken(context)
            val req = Request.Builder()
                .url(url)
                .header("User-Agent", "ArQuant-Android-Widget/1.0")
                .apply { if (token.isNotBlank()) header("X-Session", token) }
                .build()
            client.newCall(req).execute().use { resp ->
                if (!resp.isSuccessful) {
                    Log.w(TAG, "fetchBalance non-2xx: ${resp.code}")
                    return@use null
                }
                val body = resp.body?.string() ?: return@use null
                JSONObject(body)
            }
        }.onFailure { Log.e(TAG, "fetchBalance failed", it) }
            .getOrNull()
    }
}
