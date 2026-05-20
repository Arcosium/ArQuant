package com.arquant.mobile.ui

import android.annotation.SuppressLint
import android.content.Intent
import android.graphics.Color
import android.net.Uri
import android.os.Message
import android.util.Log
import android.widget.Toast
import android.webkit.ConsoleMessage
import android.webkit.CookieManager
import android.webkit.WebChromeClient
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebResourceResponse
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import com.arquant.mobile.BuildConfig
import androidx.activity.compose.BackHandler
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.viewinterop.AndroidView
import okhttp3.OkHttpClient
import okhttp3.Request
import java.io.ByteArrayInputStream
import java.net.URI
import java.util.concurrent.TimeUnit

// adb logcat -s ArQuantWeb:*  로 WebView 로드 단계를 추적 (까만 화면 진단용).
private const val WEB_TAG = "ArQuantWeb"

/**
 * 사장 피드백 2026-05-18: 모바일을 서버와 분리하지 말 것.
 *
 * 네이티브 화면(Kotlin/Compose)으로 서버 웹 UI를 재구현하면 서버를 고칠 때마다
 * 앱도 다시 빌드해야 한다. 그래서 메인 화면을 **서버 index.html 을 그대로 띄우는
 * WebView** 로 바꾼다 → 서버 UI 수정이 앱 재빌드 없이 즉시 반영된다.
 *
 * 인증: 서버(server/app.py)는 세션을 `arquant_session` 쿠키 **또는** `X-Session`
 * 헤더로 검증한다. 네이티브 로그인으로 이미 받은 세션 토큰을 WebView 쿠키 저장소에
 * 심어 두면 임베드된 대시보드가 **재로그인 없이** 곧바로 인증된 상태로 열린다.
 *
 * 푸시 알림은 이 화면과 무관하게 [com.arquant.mobile.network.WsManager] 가
 * WebSocket 으로 직접 발사하므로(WsManager.kt) 네이티브 알림은 그대로 유지된다.
 *
 * ── 까만 화면 버그 수정 (2026-05-18) ──
 * 1) CookieManager.setCookie 는 **비동기**다. 예전 코드는 setCookie 직후 곧바로
 *    loadUrl 을 호출해, 콜드 스타트에서 첫 요청이 쿠키 없이 나가 SPA 가
 *    미인증(어두운 로그인 셸)으로 떠 "까만 화면"처럼 보였다. → 콜백으로 쿠키
 *    커밋을 확인한 뒤 loadUrl.
 * 2) onPageFinished 에서 쿠키가 없으면 무조건 onLoggedOut() 을 불렀는데, 위
 *    경합 때문에 첫 로드에서 바로 로그아웃 → /api/logout 으로 방금 심은 세션을
 *    서버에서 파기 → 로그인↔웹뷰 무한 루프(앱이 안 열림). → 한 번이라도 세션을
 *    확인한 뒤(hadSession) 쿠키가 사라졌을 때만 로그아웃으로 간주.
 * 3) 일시적 네트워크 오류로 영구 블랙되지 않도록 메인 프레임 오류 시 1회 자동 재시도.
 */
@SuppressLint("SetJavaScriptEnabled")
@Composable
fun WebDashboardScreen(
    baseUrl: String,
    initialToken: String,
    cookieName: String,
    onTokenSynced: (String) -> Unit,
    onLoggedOut: () -> Unit,
) {
    val context = LocalContext.current
    val serverHost = remember(baseUrl) { runCatching { URI(baseUrl).host }.getOrNull().orEmpty() }
    val homeUrl = remember(baseUrl) { "$baseUrl/" }

    // 사장 피드백 2026-05-18: WebView 의 Chromium DNS resolver 가 arquant.ai-ve.uk 를
    // ERR_NAME_NOT_RESOLVED 로 실패함(같은 폰의 Chrome·앱 OkHttp 는 정상). 그래서
    // 모든 GET 을 이 OkHttp 로 우회 실행해 WebView 의 망가진 네트워크 스택을 건너뛴다.
    val http = remember {
        OkHttpClient.Builder()
            .connectTimeout(15, TimeUnit.SECONDS)
            .readTimeout(30, TimeUnit.SECONDS)
            .build()
    }

    // 여러 콜백(onPageFinished/onReceivedError)에서 공유되는 상태 —
    // remember 로 리컴포지션과 무관하게 안정적인 단일 인스턴스 유지.
    val state = remember {
        object {
            var view: WebView? = null
            var canGoBack = false
            var hadSession = false   // 쿠키로 인증을 한 번이라도 확인했는가
            var retried = false      // 메인 프레임 오류 자동 재시도(1회)
            var lastBackMs = 0L      // 사장 피드백 2026-05-18: 뒤로가기 2회 종료용 타임스탬프
        }
    }

    // 사장 피드백 2026-05-18: 모바일 뒤로가기 — WebView 가 더 뒤로 갈 수 있으면 goBack,
    // 아니면 2초 안에 한 번 더 누르면 앱 종료 ("한 번 더 누르면 종료" 토스트 안내).
    BackHandler(enabled = true) {
        val v = state.view
        if (v != null && v.canGoBack()) {
            v.goBack()
        } else {
            val now = System.currentTimeMillis()
            if (now - state.lastBackMs in 1L..2000L) {
                (context as? android.app.Activity)?.finish()
            } else {
                state.lastBackMs = now
                Toast.makeText(context, "한 번 더 누르면 앱을 종료합니다", Toast.LENGTH_SHORT).show()
            }
        }
    }

    fun isExternal(url: String?): Boolean {
        if (url.isNullOrBlank()) return false
        val host = runCatching { URI(url).host }.getOrNull().orEmpty()
        return host.isNotBlank() && !host.equals(serverHost, ignoreCase = true)
    }

    fun openExternally(url: String) {
        runCatching {
            context.startActivity(
                Intent(Intent.ACTION_VIEW, Uri.parse(url))
                    .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            )
        }
    }

    // 웹 쪽 로그인/로그아웃을 네이티브 인증 상태와 동기화.
    // - 쿠키 존재 → (재)로그인된 토큰을 네이티브에 저장, hadSession=true
    // - 쿠키 없음 → **이전에 인증을 확인한 경우에만** 로그아웃으로 간주
    //   (첫 로드/일시 경합에서 세션을 파기하지 않도록)
    fun syncSession() {
        val raw = CookieManager.getInstance().getCookie(baseUrl)
        val token = raw?.split(';')
            ?.map { it.trim() }
            ?.firstOrNull { it.startsWith("$cookieName=") }
            ?.substringAfter('=')
            ?.takeIf { it.isNotBlank() }
        if (token != null) {
            state.hadSession = true
            onTokenSynced(token)
        } else if (state.hadSession) {
            onLoggedOut()
        }
        // hadSession==false && 쿠키 없음: 무시 — SPA 가 자체 로그인 화면을 띄우거나
        // 쿠키 커밋을 기다림(여기서 세션을 파기하면 안 됨).
    }

    AndroidView(
        modifier = Modifier.fillMaxSize(),
        factory = { ctx ->
            // 사장 피드백 2026-05-20: 릴리스 빌드는 원격 디버깅 비활성화(웹뷰 인스펙터 차단).
            WebView.setWebContentsDebuggingEnabled(BuildConfig.DEBUG)
            WebView(ctx).apply {
                state.view = this
                // 페이지 로드 전 다크 배경 — 흰 깜빡임/검은 공백 대신 테마색.
                setBackgroundColor(Color.parseColor("#0B1120"))

                val cm = CookieManager.getInstance().apply { setAcceptCookie(true) }
                cm.setAcceptThirdPartyCookies(this, true)

                settings.apply {
                    javaScriptEnabled = true
                    domStorageEnabled = true          // localStorage — 웹 UI 상태 보존
                    databaseEnabled = true
                    cacheMode = WebSettings.LOAD_DEFAULT
                    setSupportMultipleWindows(true)   // target=_blank (OpenRouter 링크)
                    javaScriptCanOpenWindowsAutomatically = true
                    mediaPlaybackRequiresUserGesture = false
                }

                webViewClient = object : WebViewClient() {
                    override fun shouldOverrideUrlLoading(
                        view: WebView,
                        request: WebResourceRequest,
                    ): Boolean {
                        val url = request.url?.toString()
                        if (isExternal(url)) {       // 서버 외 도메인 → 외부 브라우저
                            openExternally(url!!)
                            return true
                        }
                        return false
                    }

                    // ── 핵심 수정 (2026-05-18): WebView 의 DNS(ERR_NAME_NOT_RESOLVED) 우회 ──
                    // 서버 호스트로 가는 GET 요청을 앱의 OkHttp 로 대신 실행한다. OkHttp 는
                    // 시스템 resolver 를 쓰므로 정상 해석된다(네이티브 인증이 이미 그걸로 됨).
                    // 인증은 X-Session 헤더를 직접 부착(서버는 쿠키 OR X-Session 허용) →
                    // WebView 쿠키 문제까지 동시 해소. POST 는 body 접근 불가라 우회 안 함
                    // (세션을 주입하므로 대시보드는 GET 폴링만으로 정상 표시됨).
                    override fun shouldInterceptRequest(
                        view: WebView,
                        request: WebResourceRequest,
                    ): WebResourceResponse? {
                        val u = request.url ?: return null
                        val scheme = u.scheme?.lowercase()
                        if (scheme != "http" && scheme != "https") return null
                        if (!request.method.equals("GET", ignoreCase = true)) return null
                        if (!(u.host ?: "").equals(serverHost, ignoreCase = true)) return null
                        return try {
                            val rb = Request.Builder().url(u.toString()).get()
                            request.requestHeaders.forEach { (k, v) ->
                                if (!k.equals("Host", true)) rb.header(k, v)
                            }
                            if (initialToken.isNotBlank()) rb.header("X-Session", initialToken)
                            val resp = http.newCall(rb.build()).execute()
                            val bytes = resp.body?.bytes() ?: ByteArray(0)
                            val ctype = resp.header("Content-Type") ?: "text/html"
                            val mime = ctype.substringBefore(';').trim().ifEmpty { "text/html" }
                            val enc = Regex("charset=([^;]+)", RegexOption.IGNORE_CASE)
                                .find(ctype)?.groupValues?.getOrNull(1)?.trim() ?: "utf-8"
                            val hdrs = HashMap<String, String>()
                            resp.headers.forEach { (k, v) ->
                                val lk = k.lowercase()
                                if (lk != "content-encoding" && lk != "content-length" &&
                                    lk != "transfer-encoding" && lk != "connection") hdrs[k] = v
                            }
                            val code = resp.code
                            val reason = resp.message.ifBlank { if (code == 200) "OK" else "Status $code" }
                            resp.close()
                            Log.i(WEB_TAG, "proxied GET ${u.encodedPath} -> $code (${bytes.size}b)")
                            WebResourceResponse(mime, enc, code, reason, hdrs,
                                ByteArrayInputStream(bytes))
                        } catch (e: Exception) {
                            Log.e(WEB_TAG, "proxy GET $u failed: ${e.message}")
                            null   // 실패 시 WebView 기본 처리에 위임
                        }
                    }

                    override fun onPageStarted(view: WebView, url: String?, favicon: android.graphics.Bitmap?) {
                        super.onPageStarted(view, url, favicon)
                        Log.i(WEB_TAG, "onPageStarted: $url")
                    }

                    override fun doUpdateVisitedHistory(view: WebView, url: String?, isReload: Boolean) {
                        super.doUpdateVisitedHistory(view, url, isReload)
                        state.canGoBack = view.canGoBack()
                    }

                    override fun onPageFinished(view: WebView, url: String?) {
                        super.onPageFinished(view, url)
                        Log.i(WEB_TAG, "onPageFinished: $url")
                        state.canGoBack = view.canGoBack()
                        state.retried = false        // 성공 로드 — 재시도 플래그 리셋
                        syncSession()
                        // ── WebView 쿠키가 안 먹는 경우 대비 (browser는 되는데 app만 까만 화면) ──
                        // SPA 의 window.fetch 오버라이드는 localStorage 의 arquant_token 을
                        // X-Session 헤더로 자동 부착한다. 토큰을 localStorage 에 심고
                        // **오버라이드된 fetch** 로 auth_status 를 다시 확인해 인증 상태를
                        // 강제 동기화한다 → 쿠키 불문하고 대시보드가 뜬다(수동 웹로그인과 동일 경로).
                        if (url != null && !url.startsWith("data:") && initialToken.isNotBlank()) {
                            val tok = initialToken.replace("\\", "").replace("'", "")
                            view.evaluateJavascript(
                                """(function(){try{
                                  localStorage.setItem('arquant_token','$tok');
                                  if(typeof setToken==='function')setToken('$tok');
                                  fetch(location.origin+'/api/auth_status').then(function(r){return r.json()}).then(function(d){
                                    if(d&&d.authenticated){
                                      if(typeof hideLogin==='function')hideLogin();
                                      if(typeof startApp==='function')startApp();
                                      if(typeof renderAcct==='function')renderAcct(d.active);
                                      console.log('[ArQuantWeb] header-auth OK');
                                    }else{console.log('[ArQuantWeb] header-auth NOT authed');}
                                  }).catch(function(e){console.log('[ArQuantWeb] header-auth err '+e);});
                                }catch(e){console.log('[ArQuantWeb] inject err '+e);}})();""".trimIndent(),
                                null,
                            )
                        }
                    }

                    override fun onReceivedError(
                        view: WebView,
                        request: WebResourceRequest,
                        error: WebResourceError,
                    ) {
                        super.onReceivedError(view, request, error)
                        val main = request.isForMainFrame
                        Log.e(WEB_TAG, "onReceivedError main=$main url=${request.url} " +
                            "code=${error.errorCode} desc=${error.description}")
                        if (!main) return                       // 서브리소스 오류는 무시
                        if (!state.retried) {                   // 1회 자동 재시도
                            state.retried = true
                            view.postDelayed({ view.loadUrl(homeUrl) }, 1500)
                            return
                        }
                        // 자동 재시도까지 실패 — 까만 화면 대신 **진단 가능한 오류 화면**.
                        val html = """
                            <!doctype html><html><head><meta name="viewport"
                            content="width=device-width,initial-scale=1"><style>
                            html,body{margin:0;height:100%;background:#0B1120;color:#e8edf6;
                            font-family:-apple-system,Roboto,sans-serif;display:flex;
                            align-items:center;justify-content:center}
                            .b{max-width:340px;padding:24px;text-align:center}
                            .t{font-size:17px;font-weight:700;margin-bottom:10px}
                            .m{font-size:13px;color:#94a3b8;line-height:1.6;word-break:break-all}
                            button{margin-top:18px;background:#6366f1;color:#fff;border:0;
                            border-radius:8px;padding:11px 22px;font-size:14px;font-weight:600}
                            </style></head><body><div class="b">
                            <div class="t">⚠️ 서버 화면을 불러오지 못했습니다</div>
                            <div class="m">오류 ${error.errorCode}: ${error.description}<br>
                            $homeUrl<br><br>휴대폰의 Wi-Fi/데이터 연결을 확인한 뒤
                            다시 시도하세요.</div>
                            <button onclick="location.href='$homeUrl'">다시 시도</button>
                            </div></body></html>
                        """.trimIndent()
                        view.loadDataWithBaseURL(baseUrl, html, "text/html", "UTF-8", null)
                    }

                    override fun onReceivedHttpError(
                        view: WebView,
                        request: WebResourceRequest,
                        errorResponse: WebResourceResponse,
                    ) {
                        super.onReceivedHttpError(view, request, errorResponse)
                        if (request.isForMainFrame) {
                            Log.e(WEB_TAG, "onReceivedHttpError main url=${request.url} " +
                                "status=${errorResponse.statusCode}")
                        }
                    }
                }

                webChromeClient = object : WebChromeClient() {
                    // JS 콘솔/에러를 네이티브 로그로 — '브라우저는 되는데 앱만 까만 화면'의
                    // 진짜 원인(오래된 WebView 의 JS 비호환 등)을 여기서 잡는다.
                    override fun onConsoleMessage(msg: ConsoleMessage): Boolean {
                        val lvl = msg.messageLevel()
                        val line = "JS[$lvl] ${msg.message()} @${msg.sourceId()}:${msg.lineNumber()}"
                        if (lvl == ConsoleMessage.MessageLevel.ERROR) Log.e(WEB_TAG, line)
                        else Log.i(WEB_TAG, line)
                        return true
                    }

                    override fun onProgressChanged(view: WebView, newProgress: Int) {
                        super.onProgressChanged(view, newProgress)
                        if (newProgress == 100) Log.i(WEB_TAG, "progress 100% ${view.url}")
                    }

                    // target=_blank / window.open → 외부 브라우저로 열기.
                    override fun onCreateWindow(
                        view: WebView,
                        isDialog: Boolean,
                        isUserGesture: Boolean,
                        resultMsg: Message,
                    ): Boolean {
                        val href = view.hitTestResult.extra
                        if (!href.isNullOrBlank()) {
                            openExternally(href)
                            return false
                        }
                        // href 를 즉시 못 얻는 경우: 임시 WebView 로 첫 네비게이션 URL 가로채기.
                        val tmp = WebView(view.context)
                        tmp.webViewClient = object : WebViewClient() {
                            override fun shouldOverrideUrlLoading(
                                v: WebView,
                                req: WebResourceRequest,
                            ): Boolean {
                                req.url?.toString()?.let { openExternally(it) }
                                tmp.destroy()
                                return true
                            }
                        }
                        (resultMsg.obj as WebView.WebViewTransport).webView = tmp
                        resultMsg.sendToTarget()
                        return true
                    }
                }

                // ── 까만 화면 버그 수정 (2026-05-18, 2차) ──
                // 이전 버전은 setCookie(async)의 **콜백 안에서** loadUrl 을 호출했는데,
                // 콜백이 지연/누락되면 loadUrl 이 영원히 호출되지 않아 페이지가 안 뜨고
                // (앱 로그도 안 남고) 까만 화면이 유지됐다. → 콜백 의존을 제거하고
                // **동기적으로 쿠키 설정 + flush 후 즉시·무조건 loadUrl** 한다.
                // 쿠키 인메모리 저장은 동일 WebView·동일 스레드의 직후 요청에 곧바로
                // 반영되므로(디스크 영속만 async) 인증이 보장된다. 추가 안전망으로
                // 메인 문서 요청에 X-Session 헤더도 함께 실어 보낸다(서버가 쿠키 OR
                // X-Session 둘 다 허용).
                if (initialToken.isNotBlank()) {
                    cm.setCookie(baseUrl, "$cookieName=$initialToken; Path=/; Secure; SameSite=Lax")
                    cm.flush()
                    val readback = cm.getCookie(baseUrl)
                    Log.i(WEB_TAG, "cookie seeded (token len=${initialToken.length}); " +
                        "readback=${if (readback?.contains(cookieName) == true) "OK" else "MISSING -> $readback"}; loading $homeUrl")
                    loadUrl(homeUrl, mapOf("X-Session" to initialToken))
                } else {
                    Log.i(WEB_TAG, "no token — loading $homeUrl (SPA login)")
                    loadUrl(homeUrl)                // 토큰 없으면 SPA 자체 로그인 화면
                }
            }
        },
    )
}
