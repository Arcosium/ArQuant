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

data class AuthState(
    val phase: AuthPhase = AuthPhase.LOADING,
    val busy: Boolean = false,
    val error: String? = null,
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
