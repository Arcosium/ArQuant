package com.arquant.mobile.viewmodel

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.arquant.mobile.data.ArQuantRepository
import com.arquant.mobile.network.RegisterRequest
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import javax.inject.Inject

/**
 * 사장 피드백 2026-05-16: Cloudflare Access 제거 → 앱 자체 로그인 게이트.
 * AUTHED 가 되기 전엔 MainActivity 가 DashViewModel 을 생성하지 않으므로
 * WS/폴링은 로그인 후에만 시작된다.
 */
enum class AuthPhase { LOADING, NEED_LOGIN, NEED_REGISTER, AUTHED }

// 5-2: 아이디/비밀번호 찾기 화면 상태
enum class RecoverTab { ID, PASSWORD }

data class RecoveryState(
    val visible: Boolean = false,
    val tab: RecoverTab = RecoverTab.ID,
    val busy: Boolean = false,
    val message: String? = null,   // success message (green)
    val error: String? = null,     // error message (red)
)

data class AuthState(
    val phase: AuthPhase = AuthPhase.LOADING,
    val busy: Boolean = false,
    val error: String? = null,
    val recovery: RecoveryState = RecoveryState(),
)

@HiltViewModel
class AuthViewModel @Inject constructor(
    private val repo: ArQuantRepository,
) : ViewModel() {

    private val _state = MutableStateFlow(AuthState())
    val state: StateFlow<AuthState> = _state.asStateFlow()

    init {
        refresh()
    }

    /** 토큰 유효성 + 계정 존재 여부 확인 → 초기 화면 결정. */
    fun refresh() {
        viewModelScope.launch {
            _state.update { it.copy(phase = AuthPhase.LOADING, error = null) }
            try {
                val s = repo.authStatus()
                _state.update {
                    it.copy(phase = when {
                        s.authenticated -> AuthPhase.AUTHED
                        s.hasAccounts -> AuthPhase.NEED_LOGIN
                        else -> AuthPhase.NEED_REGISTER
                    })
                }
            } catch (e: Exception) {
                // 서버 불가 등 — 일단 로그인 화면으로
                _state.update { it.copy(phase = AuthPhase.NEED_LOGIN,
                    error = "서버 확인 실패: ${e.message}") }
            }
        }
    }

    fun setMode(register: Boolean) {
        _state.update {
            it.copy(phase = if (register) AuthPhase.NEED_REGISTER else AuthPhase.NEED_LOGIN,
                error = null)
        }
    }

    fun login(username: String, password: String, remember: Boolean) {
        viewModelScope.launch {
            _state.update { it.copy(busy = true, error = null) }
            try {
                val r = repo.login(username.trim(), password, remember)
                if (r.ok) _state.update { it.copy(busy = false, phase = AuthPhase.AUTHED) }
                else _state.update { it.copy(busy = false, error = "로그인 실패") }
            } catch (e: Exception) {
                _state.update { it.copy(busy = false, error = parseErr(e)) }
            }
        }
    }

    fun register(req: RegisterRequest) {
        viewModelScope.launch {
            _state.update { it.copy(busy = true, error = null) }
            try {
                val r = repo.register(req)
                if (r.ok) _state.update { it.copy(busy = false, phase = AuthPhase.AUTHED) }
                else _state.update { it.copy(busy = false, error = "등록 실패") }
            } catch (e: Exception) {
                _state.update { it.copy(busy = false, error = parseErr(e)) }
            }
        }
    }

    fun logout() {
        viewModelScope.launch {
            runCatching { repo.logout() }
            _state.update { it.copy(phase = AuthPhase.NEED_LOGIN, error = null) }
        }
    }

    // ─── Recovery (5-2) ────────────────────────────────────────────────────

    fun setRecoveryVisible(visible: Boolean) {
        _state.update { it.copy(recovery = RecoveryState(visible = visible)) }
    }

    fun setRecoveryTab(tab: RecoverTab) {
        _state.update { it.copy(recovery = it.recovery.copy(tab = tab, message = null, error = null)) }
    }

    fun recoverId(kisAppKey: String, kisAppSecret: String, openrouterKey: String) {
        viewModelScope.launch {
            _state.update { it.copy(recovery = it.recovery.copy(busy = true, message = null, error = null)) }
            try {
                val r = repo.recoverId(kisAppKey.trim(), kisAppSecret.trim(), openrouterKey.trim())
                _state.update { it.copy(recovery = it.recovery.copy(busy = false,
                    message = "아이디: ${r.username}")) }
            } catch (e: Exception) {
                _state.update { it.copy(recovery = it.recovery.copy(busy = false,
                    error = parseRecoverErr(e))) }
            }
        }
    }

    fun recoverPassword(
        username: String,
        kisAppKey: String,
        kisAppSecret: String,
        openrouterKey: String,
        newPassword: String,
    ) {
        viewModelScope.launch {
            _state.update { it.copy(recovery = it.recovery.copy(busy = true, message = null, error = null)) }
            try {
                repo.recoverPassword(username.trim(), kisAppKey.trim(), kisAppSecret.trim(),
                    openrouterKey.trim(), newPassword)
                _state.update { it.copy(recovery = it.recovery.copy(busy = false,
                    message = "비밀번호가 재설정되었습니다. 새 비밀번호로 로그인하세요.")) }
            } catch (e: Exception) {
                _state.update { it.copy(recovery = it.recovery.copy(busy = false,
                    error = parseRecoverErr(e))) }
            }
        }
    }

    private fun parseRecoverErr(e: Exception): String {
        val m = e.message ?: "오류"
        return when {
            "400" in m -> "비밀번호 정책 오류: 10자 이상, 특수문자 1개 이상 필요합니다."
            "404" in m -> "일치하는 계정을 찾을 수 없습니다."
            "429" in m -> "요청이 너무 많습니다. 잠시 후 다시 시도하세요."
            else -> "오류가 발생했습니다. 다시 시도해 주세요."
        }
    }

    private fun parseErr(e: Exception): String {
        val m = e.message ?: "오류"
        return when {
            "401" in m -> "아이디 또는 비밀번호가 일치하지 않습니다."
            "409" in m -> "이미 사용 중인 아이디입니다."
            "400" in m -> "검증 실패 — 아이디/비밀번호 정책 또는 KIS·OpenRouter 키를 확인하세요."
            else -> m
        }
    }
}
