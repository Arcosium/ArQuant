package com.arquant.mobile.network

import com.arquant.mobile.security.TokenManager
import okhttp3.Interceptor
import okhttp3.Response
import javax.inject.Inject
import javax.inject.Singleton

/**
 * 사장 피드백 2026-05-16: CfAccessInterceptor 대체.
 *
 * 앱 자체 로그인 세션 토큰을 모든 REST 요청에 `X-Session` 헤더로 부착한다.
 * (서버 server/app.py 의 app_auth 미들웨어가 쿠키 또는 X-Session 으로 세션을 검증)
 * 토큰이 없으면(미로그인) 헤더를 붙이지 않으며, 서버는 401 JSON 을 돌려주고
 * 호출부(ViewModel)가 로그인 화면으로 보낸다.
 */
@Singleton
class AppAuthInterceptor @Inject constructor(
    private val tokenManager: TokenManager,
) : Interceptor {
    override fun intercept(chain: Interceptor.Chain): Response {
        val req = chain.request()
        val token = tokenManager.get()
        if (token.isBlank()) return chain.proceed(req)
        return chain.proceed(
            req.newBuilder().header("X-Session", token).build()
        )
    }
}
