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
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.input.VisualTransformation
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.arquant.mobile.network.RegisterRequest
import com.arquant.mobile.ui.theme.AqColors
import com.arquant.mobile.viewmodel.AuthPhase
import com.arquant.mobile.viewmodel.AuthState

/**
 * 사장 피드백 2026-05-16 (2차): 로그인 정체성을 사용자 지정 아이디/비밀번호로 변경.
 * 로그인 = 아이디 + 비밀번호만. 등록 = 아이디(중복확인) + 비밀번호(10자↑·특수문자1↑) + API 키.
 */
private fun pwError(p: String): String? = when {
    p.length < 10 -> "10자 이상 필요"
    !p.any { !it.isLetterOrDigit() && !it.isWhitespace() } -> "특수문자 1개 이상 필요"
    else -> null
}

@Composable
fun LoginScreen(
    state: AuthState,
    onLogin: (username: String, password: String, remember: Boolean) -> Unit,
    onRegister: (RegisterRequest) -> Unit,
    onToggleMode: (register: Boolean) -> Unit,
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
    var dartKey by remember { mutableStateOf("") }
    var label by remember { mutableStateOf("") }
    var remember7 by remember { mutableStateOf(true) }

    val pwErr = if (register) pwError(password) else null

    Box(
        modifier = Modifier.fillMaxSize().background(AqColors.Background),
        contentAlignment = Alignment.Center,
    ) {
        Column(
            modifier = Modifier
                .widthIn(max = 420.dp)
                .fillMaxWidth()
                .padding(24.dp)
                .verticalScroll(rememberScrollState()),
        ) {
            Text("ArQuant", fontSize = 22.sp, fontWeight = FontWeight.ExtraBold,
                color = AqColors.TextPrimary)
            Text("Multi-Asset AI Trading — 로그인", fontSize = 12.sp, color = AqColors.TextDim)
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
                Field("OpenRouter API Key (필수)", openrouter, { openrouter = it })
                Field("한국투자증권 App Key", appKey, { appKey = it })
                Field("한국투자증권 App Secret", appSecret, { appSecret = it }, isPassword = true)
                Field("KIS 계좌번호 (예: 12345678-01)", accountNo, { accountNo = it })
                Text("거래 환경", fontSize = 11.sp, color = AqColors.TextDim,
                    modifier = Modifier.padding(top = 6.dp, bottom = 4.dp))
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    ModeTab("실전투자", !mock) { mock = false }
                    ModeTab("모의투자", mock) { mock = true }
                }
                Spacer(Modifier.height(6.dp))
                Field("DART API Key (선택 — 없으면 공시 분석 생략)", dartKey, { dartKey = it })
                Field("계정 이름 (선택)", label, { label = it })
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
                            kisBaseUrl = baseUrl.trim(), dartKey = dartKey.trim(),
                            label = label.trim(), remember = remember7,
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
        }
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
