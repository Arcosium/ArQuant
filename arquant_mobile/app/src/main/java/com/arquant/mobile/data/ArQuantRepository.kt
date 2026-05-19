package com.arquant.mobile.data

import com.arquant.mobile.network.*
import com.arquant.mobile.security.TokenManager
import javax.inject.Inject
import javax.inject.Singleton

@Singleton
class ArQuantRepository @Inject constructor(
    private val api: ArQuantApi,
    private val tokenManager: TokenManager,
) {
    // ─── Auth (사장 피드백 2026-05-16) ──────────────────────────────────────
    fun isLoggedIn(): Boolean = tokenManager.isLoggedIn()

    suspend fun authStatus(): AuthStatusResponse = api.authStatus()

    suspend fun checkUsername(u: String): Boolean =
        runCatching { api.checkUsername(u).available }.getOrDefault(false)

    suspend fun login(username: String, password: String, remember: Boolean): AuthResponse {
        val r = api.login(LoginRequest(username, password, remember))
        if (r.token.isNotBlank()) tokenManager.save(r.token)
        return r
    }

    suspend fun register(req: RegisterRequest): AuthResponse {
        val r = api.register(req)
        if (r.token.isNotBlank()) tokenManager.save(r.token)
        return r
    }

    suspend fun me(): MeResponse = api.me()

    // ─── Recovery (5-2) ────────────────────────────────────────────────────
    suspend fun recoverId(
        kisAccountNo: String,
        kisAppSecret: String,
    ): RecoverIdResponse = api.recoverId(
        RecoverIdRequest(kisAccountNo = kisAccountNo, kisAppSecret = kisAppSecret)
    )

    suspend fun recoverPassword(
        username: String,
        kisAccountNo: String,
        kisAppSecret: String,
        newPassword: String,
    ): RecoverPwResponse = api.recoverPassword(
        RecoverPwRequest(
            username = username,
            kisAccountNo = kisAccountNo,
            kisAppSecret = kisAppSecret,
            newPassword = newPassword,
        )
    )

    suspend fun logout() {
        runCatching { api.logout() }
        tokenManager.clear()
    }

    // ─── Data ──────────────────────────────────────────────────────────────
    suspend fun status(): StatusResponse = api.status()
    suspend fun balance(): BalanceResponse = api.balance()
    suspend fun news(): NewsResponse = api.news()
    suspend fun agents(): AgentsResponse = api.agents()
    suspend fun equity(view: String): EquityResponse = api.equity(view)
    suspend fun trades(): TradesResponse = api.trades()
    suspend fun events(): EventsResponse = api.events()
    suspend fun strategy(): StrategyResponse = api.strategy()

    suspend fun start(): SimpleMessage = api.start()
    suspend fun stop(): SimpleMessage = api.stop()
    suspend fun ceo(msg: String): CeoResponse = api.ceo(CeoRequest(msg))
    suspend fun clearEvents(): SimpleMessage = api.clearEvents()
    suspend fun clearTrades(): SimpleMessage = api.clearTrades()
    suspend fun setStrategy(name: String) = api.setStrategy(StrategySetRequest(name))
}
