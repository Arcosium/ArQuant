package com.arquant.mobile.security

import android.content.Context
import dagger.hilt.android.qualifiers.ApplicationContext
import javax.inject.Inject
import javax.inject.Singleton

/**
 * 사장 피드백 2026-05-16: Cloudflare Access 제거 → 앱 자체 로그인 세션 토큰 보관.
 *
 * 저장되는 값은 **불투명한 7일 세션 토큰**일 뿐이며, 실제 API 비밀(KIS App Secret /
 * OpenRouter 키 등)은 서버에 Fernet 암호화되어 있고 단말로 내려오지 않는다.
 * 따라서 앱 샌드박스 전용 SharedPreferences 로 충분하다 (브라우저 세션 쿠키와 동급 위험).
 */
@Singleton
class TokenManager @Inject constructor(
    @ApplicationContext context: Context,
) {
    private val prefs = context.getSharedPreferences("arquant_auth", Context.MODE_PRIVATE)

    fun get(): String = prefs.getString(KEY, "").orEmpty()

    fun save(token: String) {
        prefs.edit().putString(KEY, token).apply()
    }

    fun clear() {
        prefs.edit().remove(KEY).apply()
    }

    fun isLoggedIn(): Boolean = get().isNotBlank()

    private companion object {
        const val KEY = "session_token"
    }
}
