package com.arquant.mobile.ui.screens

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.input.VisualTransformation
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.foundation.Canvas
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.StrokeJoin
import androidx.compose.ui.graphics.drawscope.Stroke
import com.arquant.mobile.network.RegisterRequest
import com.arquant.mobile.ui.theme.AqColors
import com.arquant.mobile.viewmodel.AuthPhase
import com.arquant.mobile.viewmodel.AuthState
import com.arquant.mobile.viewmodel.RecoverTab
import com.arquant.mobile.viewmodel.RecoveryState

/**
 * 사장 피드백 2026-05-16 (2차): 로그인 정체성을 사용자 지정 아이디/비밀번호로 변경.
 * 로그인 = 아이디 + 비밀번호만. 등록 = 아이디(중복확인) + 비밀번호(10자↑·특수문자1↑) + API 키.
 *
 * 2026-05-19 Plan3-Android:
 *  5-1: 웹 대시보드 로고와 동일한 인디고→시안 그라디언트 박스 + 꺾은선 차트 아이콘.
 *  5-2: 아이디/비밀번호 찾기 복구 UI.
 *  5-4: 등록 폼 하단 navigationBarsPadding + imePadding + verticalScroll.
 *  5-5: DART API Key, 계정 이름(선택) 필드 제거; RegisterRequest 에서도 삭제.
 *  5-6: 계좌번호 → "한국투자증권 계좌번호", App Key/Secret 레이블 명시.
 */
private fun pwError(p: String): String? = when {
    p.length < 10 -> "10자 이상 필요"
    !p.any { !it.isLetterOrDigit() && !it.isWhitespace() } -> "특수문자 1개 이상 필요"
    else -> null
}

// 5-1: 웹 로그인 오버레이 로고와 동일한 처리.
// width:30px, height:30px, 135deg gradient #6366f1→#06b6d4, border-radius:9dp
// SVG 내부: circle + 꺾은선 path + filled circle (미니 line-chart)
@Composable
private fun ArQuantLogoBox() {
    Box(
        modifier = Modifier
            .size(38.dp)
            .clip(RoundedCornerShape(9.dp))
            .background(
                Brush.linearGradient(
                    colors = listOf(AqColors.GradStart, AqColors.GradEnd),
                    // 135deg: top-left → bottom-right
                    start = Offset(0f, 0f),
                    end = Offset(Float.POSITIVE_INFINITY, Float.POSITIVE_INFINITY),
                )
            ),
        contentAlignment = Alignment.Center,
    ) {
        // Reproduce the SVG line-chart icon from index.html:
        // viewBox 0 0 24 24; circle cx=12 cy=12 r=9.2 opacity=.35;
        // path M5 16.5l4-5 3.2 3 4.3-6.2; circle cx=16.5 cy=8.3 r=1.6 fill=#fff
        Canvas(modifier = Modifier.size(21.dp)) {
            val scale = size.width / 24f

            // Background circle (opacity 0.35)
            drawCircle(
                color = Color.White.copy(alpha = 0.35f),
                radius = 9.2f * scale,
                center = Offset(12f * scale, 12f * scale),
                style = Stroke(width = 2.2f * scale, cap = StrokeCap.Round),
            )

            // Line-chart path: M5 16.5 L9 11.5 L12.2 14.5 L16.5 8.3
            val path = androidx.compose.ui.graphics.Path().apply {
                moveTo(5f * scale, 16.5f * scale)
                lineTo(9f * scale, 11.5f * scale)
                lineTo(12.2f * scale, 14.5f * scale)
                lineTo(16.5f * scale, 8.3f * scale)
            }
            drawPath(
                path = path,
                color = Color.White,
                style = Stroke(
                    width = 2.2f * scale,
                    cap = StrokeCap.Round,
                    join = StrokeJoin.Round,
                ),
            )

            // Filled endpoint circle: cx=16.5 cy=8.3 r=1.6
            drawCircle(
                color = Color.White,
                radius = 1.6f * scale,
                center = Offset(16.5f * scale, 8.3f * scale),
            )
        }
    }
}

@Composable
fun LoginScreen(
    state: AuthState,
    onLogin: (username: String, password: String, remember: Boolean) -> Unit,
    onRegister: (RegisterRequest) -> Unit,
    onToggleMode: (register: Boolean) -> Unit,
    onRecoveryToggle: (Boolean) -> Unit,
    onRecoveryTabChange: (RecoverTab) -> Unit,
    onRecoverId: (kisAccountNo: String, kisAppSecret: String) -> Unit,
    onRecoverPassword: (username: String, kisAccountNo: String, kisAppSecret: String, newPassword: String) -> Unit,
) {
    val register = state.phase == AuthPhase.NEED_REGISTER
    var username by remember { mutableStateOf("") }
    var password by remember { mutableStateOf("") }
    var openrouter by remember { mutableStateOf("") }
    var appKey by remember { mutableStateOf("") }
    var appSecret by remember { mutableStateOf("") }
    var accountNo by remember { mutableStateOf("") }
    // 사장 피드백 2026-05-16: KIS Base URL 직접 입력 제거 → 실전/모의 토글.
    var mock by remember { mutableStateOf(false) }
    val baseUrl = if (mock) "https://openapivts.koreainvestment.com:29443"
                  else "https://openapi.koreainvestment.com:9443"
    var remember7 by remember { mutableStateOf(true) }

    val pwErr = if (register) pwError(password) else null

    // 5-4: 등록 모드에서 홈바/소프트키보드에 의해 버튼이 가려지지 않도록
    // navigationBarsPadding + imePadding 적용. 전체가 verticalScroll 가능.
    Box(
        modifier = Modifier
            .fillMaxSize()
            .background(AqColors.Background)
            .navigationBarsPadding()
            .imePadding(),
        contentAlignment = Alignment.Center,
    ) {
        Column(
            modifier = Modifier
                .widthIn(max = 420.dp)
                .fillMaxWidth()
                .verticalScroll(rememberScrollState())
                .padding(24.dp),
        ) {
            // 5-1: 로고 박스 + 텍스트 (웹 로그인 오버레이와 동일한 처리)
            Row(
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(9.dp),
            ) {
                ArQuantLogoBox()
                Column {
                    Text("ArQuant", fontSize = 16.sp, fontWeight = FontWeight.Bold,
                        color = AqColors.TextPrimary)
                    Text("Multi-Asset AI Trading", fontSize = 10.sp, color = AqColors.TextDim)
                }
            }
            Spacer(Modifier.height(18.dp))

            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                ModeTab("로그인", !register) { onToggleMode(false) }
                ModeTab("최초 등록", register) { onToggleMode(true) }
            }
            Spacer(Modifier.height(16.dp))

            Field("아이디", username, { username = it })
            Field("비밀번호" + if (register) " (10자 이상 · 특수문자 1개 이상)" else "",
                password, { password = it }, isPassword = true)
            if (register && password.isNotEmpty()) {
                Text(
                    if (pwErr == null) "✓ 사용 가능한 비밀번호" else "✗ $pwErr",
                    color = if (pwErr == null) AqColors.Green else AqColors.Red,
                    fontSize = 11.sp, modifier = Modifier.padding(bottom = 4.dp),
                )
            }

            if (register) {
                // 5-6: 필드 레이블 명시
                Field("OpenRouter API Key (필수)", openrouter, { openrouter = it })
                Field("한국투자증권 App Key", appKey, { appKey = it })
                Field("한국투자증권 App Secret", appSecret, { appSecret = it }, isPassword = true)
                // 5-6: KIS 계좌번호 레이블
                Field("한국투자증권 계좌번호 (예: 12345678-01)", accountNo, { accountNo = it })
                Text("거래 환경", fontSize = 11.sp, color = AqColors.TextDim,
                    modifier = Modifier.padding(top = 6.dp, bottom = 4.dp))
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    ModeTab("실전투자", !mock) { mock = false }
                    ModeTab("모의투자", mock) { mock = true }
                }
                Spacer(Modifier.height(6.dp))
                // 5-5: DART API Key 및 계정 이름(선택) 필드 제거됨
            }

            Row(verticalAlignment = Alignment.CenterVertically,
                modifier = Modifier.padding(vertical = 6.dp)) {
                Checkbox(checked = remember7, onCheckedChange = { remember7 = it })
                Text("이 기기에서 7일간 로그인 유지", fontSize = 12.sp, color = AqColors.TextDim)
            }

            state.error?.let {
                Text("❌ $it", color = AqColors.Red, fontSize = 12.sp,
                    modifier = Modifier.padding(bottom = 8.dp))
            }

            Button(
                onClick = {
                    if (register) onRegister(
                        RegisterRequest(
                            username = username.trim(), password = password,
                            openrouterKey = openrouter.trim(), kisAppKey = appKey.trim(),
                            kisAppSecret = appSecret.trim(), kisAccountNo = accountNo.trim(),
                            kisBaseUrl = baseUrl.trim(), remember = remember7,
                        )
                    ) else onLogin(username, password, remember7)
                },
                enabled = !state.busy && username.trim().length >= 3 && password.isNotBlank()
                    && (!register || (pwErr == null && openrouter.isNotBlank()
                        && appKey.isNotBlank() && appSecret.isNotBlank() && accountNo.isNotBlank())),
                modifier = Modifier.fillMaxWidth().height(46.dp),
                shape = RoundedCornerShape(9.dp),
                colors = ButtonDefaults.buttonColors(containerColor = AqColors.Primary),
            ) {
                Text(
                    if (state.busy) "확인 중…" else if (register) "등록하고 시작" else "로그인",
                    fontWeight = FontWeight.SemiBold,
                )
            }

            // 5-2: 아이디/비밀번호 찾기 — 로그인 모드에서만 표시
            if (!register) {
                Spacer(Modifier.height(14.dp))
                Box(modifier = Modifier.fillMaxWidth(), contentAlignment = Alignment.Center) {
                    TextButton(
                        onClick = { onRecoveryToggle(!state.recovery.visible) },
                        contentPadding = PaddingValues(0.dp),
                    ) {
                        Text(
                            "아이디/비밀번호를 잊으셨나요?",
                            fontSize = 11.sp,
                            color = AqColors.TextDim,
                        )
                    }
                }

                if (state.recovery.visible) {
                    Spacer(Modifier.height(4.dp))
                    HorizontalDivider(color = AqColors.Border, thickness = 1.dp)
                    Spacer(Modifier.height(14.dp))
                    RecoveryPanel(
                        recoveryState = state.recovery,
                        onTabChange = onRecoveryTabChange,
                        onRecoverId = onRecoverId,
                        onRecoverPassword = onRecoverPassword,
                    )
                }
            }

            // 5-4: 하단 여백 (등록 모드에서 navigationBarsPadding 보완)
            Spacer(Modifier.height(16.dp))
        }
    }
}

// 5-2: 복구 패널 (아이디 찾기 / 비밀번호 재설정)
@Composable
private fun RecoveryPanel(
    recoveryState: RecoveryState,
    onTabChange: (RecoverTab) -> Unit,
    onRecoverId: (String, String) -> Unit,
    onRecoverPassword: (String, String, String, String) -> Unit,
) {
    // 탭 전환
    Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
        ModeTab("아이디 찾기", recoveryState.tab == RecoverTab.ID) { onTabChange(RecoverTab.ID) }
        ModeTab("비밀번호 재설정", recoveryState.tab == RecoverTab.PASSWORD) { onTabChange(RecoverTab.PASSWORD) }
    }
    Spacer(Modifier.height(12.dp))

    when (recoveryState.tab) {
        RecoverTab.ID -> RecoverIdForm(
            busy = recoveryState.busy,
            message = recoveryState.message,
            error = recoveryState.error,
            onSubmit = onRecoverId,
        )
        RecoverTab.PASSWORD -> RecoverPwForm(
            busy = recoveryState.busy,
            message = recoveryState.message,
            error = recoveryState.error,
            onSubmit = onRecoverPassword,
        )
    }
}

@Composable
private fun RecoverIdForm(
    busy: Boolean,
    message: String?,
    error: String?,
    onSubmit: (kisAccountNo: String, kisAppSecret: String) -> Unit,
) {
    var kisAccountNo by remember { mutableStateOf("") }
    var kisAppSecret by remember { mutableStateOf("") }

    Field("한국투자증권 계좌번호", kisAccountNo, { kisAccountNo = it })
    Field("한국투자증권 App Secret", kisAppSecret, { kisAppSecret = it }, isPassword = true)

    error?.let {
        Spacer(Modifier.height(8.dp))
        Text(it, color = AqColors.Red, fontSize = 11.sp,
            modifier = Modifier
                .fillMaxWidth()
                .background(Color(0x1FEF4444), RoundedCornerShape(7.dp))
                .padding(8.dp))
    }
    message?.let {
        Spacer(Modifier.height(8.dp))
        Text(it, color = AqColors.Green, fontSize = 12.sp,
            modifier = Modifier
                .fillMaxWidth()
                .background(Color(0x1F10B981), RoundedCornerShape(7.dp))
                .padding(8.dp))
    }

    Spacer(Modifier.height(10.dp))
    Button(
        onClick = { onSubmit(kisAccountNo, kisAppSecret) },
        enabled = !busy && kisAccountNo.isNotBlank() && kisAppSecret.isNotBlank(),
        modifier = Modifier.fillMaxWidth().height(42.dp),
        shape = RoundedCornerShape(9.dp),
        colors = ButtonDefaults.buttonColors(containerColor = AqColors.Primary),
    ) {
        Text(if (busy) "확인 중…" else "아이디 찾기", fontWeight = FontWeight.SemiBold, fontSize = 13.sp)
    }
}

@Composable
private fun RecoverPwForm(
    busy: Boolean,
    message: String?,
    error: String?,
    onSubmit: (username: String, kisAccountNo: String, kisAppSecret: String, newPassword: String) -> Unit,
) {
    var username by remember { mutableStateOf("") }
    var kisAccountNo by remember { mutableStateOf("") }
    var kisAppSecret by remember { mutableStateOf("") }
    var newPassword by remember { mutableStateOf("") }
    val pwErr = pwError(newPassword)

    Field("아이디", username, { username = it })
    Field("한국투자증권 계좌번호", kisAccountNo, { kisAccountNo = it })
    Field("한국투자증권 App Secret", kisAppSecret, { kisAppSecret = it }, isPassword = true)
    Field("새 비밀번호 (10자 이상 · 특수문자 1개 이상)", newPassword, { newPassword = it }, isPassword = true)
    if (newPassword.isNotEmpty()) {
        Text(
            if (pwErr == null) "✓ 사용 가능한 비밀번호" else "✗ $pwErr",
            color = if (pwErr == null) AqColors.Green else AqColors.Red,
            fontSize = 11.sp, modifier = Modifier.padding(bottom = 4.dp),
        )
    }

    error?.let {
        Spacer(Modifier.height(8.dp))
        Text(it, color = AqColors.Red, fontSize = 11.sp,
            modifier = Modifier
                .fillMaxWidth()
                .background(Color(0x1FEF4444), RoundedCornerShape(7.dp))
                .padding(8.dp))
    }
    message?.let {
        Spacer(Modifier.height(8.dp))
        Text(it, color = AqColors.Green, fontSize = 12.sp,
            modifier = Modifier
                .fillMaxWidth()
                .background(Color(0x1F10B981), RoundedCornerShape(7.dp))
                .padding(8.dp))
    }

    Spacer(Modifier.height(10.dp))
    Button(
        onClick = { onSubmit(username, kisAccountNo, kisAppSecret, newPassword) },
        enabled = !busy && username.isNotBlank() && kisAccountNo.isNotBlank()
            && kisAppSecret.isNotBlank()
            && pwErr == null && newPassword.isNotBlank(),
        modifier = Modifier.fillMaxWidth().height(42.dp),
        shape = RoundedCornerShape(9.dp),
        colors = ButtonDefaults.buttonColors(containerColor = AqColors.Primary),
    ) {
        Text(if (busy) "확인 중…" else "비밀번호 재설정", fontWeight = FontWeight.SemiBold, fontSize = 13.sp)
    }
}

@Composable
private fun ModeTab(text: String, active: Boolean, onClick: () -> Unit) {
    Surface(
        onClick = onClick,
        color = if (active) AqColors.Surface2 else AqColors.Surface,
        shape = RoundedCornerShape(8.dp),
    ) {
        Text(text, fontSize = 12.sp,
            color = if (active) AqColors.TextPrimary else AqColors.TextDim,
            modifier = Modifier.padding(horizontal = 16.dp, vertical = 9.dp))
    }
}

@Composable
private fun Field(
    label: String,
    value: String,
    onChange: (String) -> Unit,
    isPassword: Boolean = false,
) {
    OutlinedTextField(
        value = value,
        onValueChange = onChange,
        label = { Text(label, fontSize = 11.sp) },
        singleLine = true,
        visualTransformation = if (isPassword) PasswordVisualTransformation() else VisualTransformation.None,
        keyboardOptions = KeyboardOptions.Default,
        modifier = Modifier.fillMaxWidth().padding(vertical = 5.dp),
        colors = OutlinedTextFieldDefaults.colors(
            focusedBorderColor = AqColors.Primary,
            unfocusedBorderColor = AqColors.Border,
            focusedContainerColor = AqColors.Surface2,
            unfocusedContainerColor = AqColors.Surface2,
            focusedTextColor = AqColors.TextPrimary,
            unfocusedTextColor = AqColors.TextPrimary,
        ),
    )
}
