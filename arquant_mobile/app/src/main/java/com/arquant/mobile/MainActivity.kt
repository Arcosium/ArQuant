package com.arquant.mobile

import android.Manifest
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import android.widget.Toast
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.core.content.ContextCompat
import androidx.core.splashscreen.SplashScreen.Companion.installSplashScreen
import androidx.lifecycle.viewmodel.compose.viewModel
import com.arquant.mobile.ui.WebDashboardScreen
import com.arquant.mobile.ui.screens.LoginScreen
import com.arquant.mobile.ui.theme.ArQuantTheme
import com.arquant.mobile.viewmodel.AuthPhase
import com.arquant.mobile.viewmodel.AuthViewModel
import com.arquant.mobile.viewmodel.DashViewModel
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import com.arquant.mobile.ui.theme.AqColors
import com.arquant.mobile.security.TokenManager
import dagger.hilt.android.AndroidEntryPoint
import javax.inject.Inject

@AndroidEntryPoint
class MainActivity : ComponentActivity() {

    // WebView 쿠키 주입/동기화를 위해 세션 토큰 저장소를 직접 주입.
    @Inject lateinit var tokenManager: TokenManager

    private val notifPermLauncher = registerForActivityResult(
        ActivityResultContracts.RequestPermission()
    ) { granted ->
        if (!granted) {
            Toast.makeText(this, "알림 권한을 허용하면 매수/매도 알림을 받을 수 있습니다", Toast.LENGTH_LONG).show()
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        installSplashScreen()
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()

        // Android 13+ 알림 권한 요청
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            if (ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS)
                != PackageManager.PERMISSION_GRANTED
            ) {
                notifPermLauncher.launch(Manifest.permission.POST_NOTIFICATIONS)
            }
        }

        setContent {
            ArQuantTheme {
                // 사장 피드백 2026-05-16: 인증 게이트 — AUTHED 전엔 DashViewModel(WS/폴링) 미생성.
                val auth: AuthViewModel = viewModel()
                val authState by auth.state.collectAsState()

                when (authState.phase) {
                    AuthPhase.LOADING -> Box(
                        Modifier.fillMaxSize().background(AqColors.Background),
                        contentAlignment = Alignment.Center,
                    ) { CircularProgressIndicator(color = AqColors.Primary) }

                    AuthPhase.NEED_LOGIN, AuthPhase.NEED_REGISTER -> LoginScreen(
                        state = authState,
                        onLogin = auth::login,
                        onRegister = auth::register,
                        onToggleMode = auth::setMode,
                        onRecoveryToggle = auth::setRecoveryVisible,
                        onRecoveryTabChange = auth::setRecoveryTab,
                        onRecoverId = auth::recoverId,
                        onRecoverPassword = auth::recoverPassword,
                    )

                    AuthPhase.AUTHED -> AuthedApp(onLogout = auth::logout)
                }
            }
        }
    }

    @androidx.compose.runtime.Composable
    private fun AuthedApp(onLogout: () -> Unit) {
        // 사장 지시 2026-05-21: 인증되면 WsRelayService(foreground service)를 기동해 앱이
        // 백그라운드여도 WebSocket 연결을 유지 → 체결/장마감 등 푸시 알림이 계속 도착한다.
        // 로그아웃으로 이 화면이 사라지면 onDispose 에서 서비스를 멈춰 연결을 정리한다.
        // (회전 등은 manifest configChanges 로 Activity 가 재생성되지 않아 churn 없음)
        val svcCtx = androidx.compose.ui.platform.LocalContext.current
        androidx.compose.runtime.DisposableEffect(Unit) {
            val intent = android.content.Intent(svcCtx, com.arquant.mobile.service.WsRelayService::class.java)
            androidx.core.content.ContextCompat.startForegroundService(svcCtx, intent)
            onDispose { svcCtx.stopService(intent) }
        }
        // DashViewModel 은 화면 렌더에 쓰지 않지만, 생성 시 WsManager.connect() 가
        // 호출되어 WebSocket 기반 네이티브 푸시 알림(TradeNotifier)이 계속 동작한다.
        // UI 자체는 서버 웹 페이지를 그대로 띄우는 WebView 가 담당한다.
        val vm: DashViewModel = viewModel()
        val state by vm.state.collectAsState()

        // Toast (WS 이벤트/오류 알림 표시는 유지)
        state.toastMessage?.let {
            Toast.makeText(this, it, Toast.LENGTH_SHORT).show()
            vm.consumeToast()
        }

        WebDashboardScreen(
            baseUrl = BuildConfig.ARQUANT_BASE_URL,
            initialToken = tokenManager.get(),
            cookieName = "arquant_session",
            onTokenSynced = { tokenManager.save(it) },
            onLoggedOut = onLogout,
        )
    }
}

