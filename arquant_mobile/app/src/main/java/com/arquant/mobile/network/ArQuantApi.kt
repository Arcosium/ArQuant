package com.arquant.mobile.network

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import retrofit2.http.*

// ─── Data Models ──────────────────────────────────────────

@Serializable
data class StatusResponse(
    @SerialName("is_running") val isRunning: Boolean = false,
    @SerialName("current_state") val currentState: String = "IDLE",
    val session: String = "-",
    @SerialName("time_kst") val timeKst: String = "-",
    @SerialName("is_trading") val isTrading: Boolean = false,
    @SerialName("cycle_history_count") val cycleCount: Int = 0,
    @SerialName("trades_executed") val tradesExecuted: Int = 0,
    @SerialName("next_cycle_sec") val nextCycleSec: Int? = null,
    @SerialName("news_monitor") val newsMonitor: NewsMonitorInfo? = null,
    val strategy: StrategyBrief? = null,
)

@Serializable
data class NewsMonitorInfo(
    @SerialName("total_articles") val totalArticles: Int = 0,
)

@Serializable
data class StrategyBrief(
    val name: String = "-",
)

@Serializable
data class BuyingPower(
    val cash: Double = 0.0,
    @SerialName("total_eval") val totalEval: Double = 0.0,
    @SerialName("pnl_ratio") val pnlRatio: Double = 0.0,
    val ok: Boolean = false,
)

@Serializable
data class Holding(
    val code: String = "",
    val name: String = "",
    val qty: Int = 0,
    @SerialName("avg_price") val avgPrice: Double = 0.0,
    @SerialName("cur_price") val curPrice: Double = 0.0,
    @SerialName("pnl_pct") val pnlPct: Double = 0.0,
    val category: String = "국내주식",
    val ccy: String = "KRW",
)

@Serializable
data class BalanceResponse(
    @SerialName("buying_power") val buyingPower: BuyingPower = BuyingPower(),
    val holdings: List<Holding> = emptyList(),
)

@Serializable
data class NewsArticle(
    val title: String = "",
    val url: String = "",
    val time: String = "",
)

@Serializable
data class NewsResponse(
    val articles: List<NewsArticle> = emptyList(),
)

@Serializable
data class AgentInfo(
    val name: String = "",
    val role: String = "",
    val model: String = "",
)

@Serializable
data class AgentsResponse(
    val agents: List<AgentInfo> = emptyList(),
)

@Serializable
data class EquityPoint(
    val ts: String = "",
    @SerialName("total_eval") val totalEval: Double = 0.0,
    val cash: Double = 0.0,
    @SerialName("pnl_ratio") val pnlRatio: Double = 0.0,
    val label: String = "",
)

@Serializable
data class EquityResponse(
    val series: List<EquityPoint> = emptyList(),
    val view: String = "realtime",
)

// 사장 피드백 2026-05-15 (5차): 거래내역 상세보기 — FIFO 매칭 매수 ↔ 매도 + 실현 손익.
@Serializable
data class TradeMatch(
    @SerialName("buy_qty") val buyQty: Int = 0,
    @SerialName("buy_price") val buyPrice: Double? = null,
    @SerialName("buy_ts") val buyTs: String = "",
    @SerialName("sell_price") val sellPrice: Double? = null,
    val pnl: Double = 0.0,
    @SerialName("pnl_pct") val pnlPct: Double = 0.0,
    @SerialName("sell_price_inferred") val sellPriceInferred: Boolean = false,
)

@Serializable
data class TradeDetail(
    val unfilled: Boolean = false,
    val note: String = "",
    @SerialName("buy_price") val buyPrice: Double? = null,
    @SerialName("sell_price") val sellPrice: Double? = null,
    val qty: Int = 0,
    val currency: String = "KRW",
    val matched: List<TradeMatch> = emptyList(),
    @SerialName("total_pnl") val totalPnl: Double = 0.0,
    @SerialName("unmatched_qty") val unmatchedQty: Int = 0,
    @SerialName("sell_price_inferred") val sellPriceInferred: Boolean = false,
    @SerialName("price_source") val priceSource: String = "unknown",  // actual | estimated | unknown
)

@Serializable
data class TradeEvent(
    val type: String = "",
    val side: String = "",
    val ticker: String = "",
    val qty: Int = 0,
    val filled: Boolean = false,
    val message: String = "",
    val ts: String = "",
    @SerialName("est_price") val estPrice: Double? = null,
    @SerialName("est_currency") val estCurrency: String = "KRW",
    @SerialName("cycle_id") val cycleId: Int? = null,
    @SerialName("pnl_pct_hint") val pnlPctHint: Double? = null,
    val detail: TradeDetail? = null,
)

@Serializable
data class TradesResponse(
    val trades: List<TradeEvent> = emptyList(),
)

@Serializable
data class EventItem(
    val type: String = "",
    val state: String = "",
    val message: String = "",
    val agent: String = "",
    val ts: String = "",
    val articles: List<String> = emptyList(),
    @SerialName("trades_total") val tradesTotal: Int? = null,
    val side: String = "",
    val ticker: String = "",
    val qty: Int = 0,
    val filled: Boolean = false,
)

@Serializable
data class EventsResponse(
    val events: List<EventItem> = emptyList(),
)

@Serializable
data class StrategyPreset(
    val name: String = "",
    val label: String = "",
    val active: Boolean = false,
    val params: Map<String, kotlinx.serialization.json.JsonElement> = emptyMap(),
)

@Serializable
data class ActiveStrategy(
    val name: String = "",
    val label: String = "",
    val since: String = "",
    val params: Map<String, kotlinx.serialization.json.JsonElement> = emptyMap(),
)

@Serializable
data class StrategyResponse(
    val active: ActiveStrategy? = null,
    val presets: List<StrategyPreset> = emptyList(),
)

@Serializable
data class CeoRequest(val message: String)

@Serializable
data class CeoResponse(val response: String = "")

@Serializable
data class SimpleMessage(val message: String = "")

@Serializable
data class StartRequest(val directive: String? = null)

@Serializable
data class StrategySetRequest(val name: String)

// ─── Auth (사장 피드백 2026-05-16: CF Access 제거 → 앱 자체 로그인) ───────────
// 필드 매핑: KIS App Key=아이디 / KIS App Secret=비밀번호 / OpenRouter=필수 키
@Serializable
data class LoginRequest(
    val username: String,
    val password: String,
    val remember: Boolean = true,
)

@Serializable
data class RegisterRequest(
    val username: String,
    val password: String,
    @SerialName("openrouter_key") val openrouterKey: String,
    @SerialName("kis_app_key") val kisAppKey: String,
    @SerialName("kis_app_secret") val kisAppSecret: String,
    @SerialName("kis_account_no") val kisAccountNo: String,
    @SerialName("kis_base_url") val kisBaseUrl: String = "https://openapi.koreainvestment.com:9443",
    @SerialName("dart_key") val dartKey: String = "",
    val label: String = "",
    val remember: Boolean = true,
)

@Serializable
data class UsernameCheck(val ok: Boolean = false, val available: Boolean = false)

@Serializable
data class AuthResponse(
    val ok: Boolean = false,
    @SerialName("user_id") val userId: Int = 0,
    val token: String = "",
)

@Serializable
data class AuthStatusResponse(
    @SerialName("has_accounts") val hasAccounts: Boolean = false,
    val authenticated: Boolean = false,
)

@Serializable
data class MeResponse(
    @SerialName("user_id") val userId: Int = 0,
    val username: String? = null,
    val label: String? = null,
    @SerialName("kis_account_no_masked") val kisAccountNoMasked: String? = null,
    @SerialName("has_dart") val hasDart: Boolean = false,
)

// ─── API Interface ──────────────────────────────────────────

interface ArQuantApi {

    @GET("health")
    suspend fun health(): Map<String, String>

    // ─── Auth ───────────────────────────────────────────────
    @GET("api/auth_status")
    suspend fun authStatus(): AuthStatusResponse

    @GET("api/check_username")
    suspend fun checkUsername(@Query("u") u: String): UsernameCheck

    @POST("api/login")
    suspend fun login(@Body req: LoginRequest): AuthResponse

    @POST("api/register")
    suspend fun register(@Body req: RegisterRequest): AuthResponse

    @POST("api/logout")
    suspend fun logout(): SimpleMessage

    @GET("api/me")
    suspend fun me(): MeResponse

    @GET("api/status")
    suspend fun status(): StatusResponse

    @POST("api/start")
    suspend fun start(@Body req: StartRequest = StartRequest()): SimpleMessage

    @POST("api/stop")
    suspend fun stop(): SimpleMessage

    @POST("api/ceo")
    suspend fun ceo(@Body req: CeoRequest): CeoResponse

    @GET("api/balance")
    suspend fun balance(): BalanceResponse

    @GET("api/news")
    suspend fun news(): NewsResponse

    @GET("api/agents")
    suspend fun agents(): AgentsResponse

    @GET("api/equity")
    suspend fun equity(
        @Query("view") view: String = "realtime",
        @Query("limit") limit: Int = 500,
    ): EquityResponse

    @GET("api/trades")
    suspend fun trades(@Query("limit") limit: Int = 400): TradesResponse

    @POST("api/trades/clear")
    suspend fun clearTrades(): SimpleMessage

    @GET("api/events")
    suspend fun events(@Query("limit") limit: Int = 600): EventsResponse

    @POST("api/events/clear")
    suspend fun clearEvents(): SimpleMessage

    @GET("api/strategy")
    suspend fun strategy(): StrategyResponse

    @POST("api/strategy")
    suspend fun setStrategy(@Body req: StrategySetRequest): Map<String, kotlinx.serialization.json.JsonElement>
}
