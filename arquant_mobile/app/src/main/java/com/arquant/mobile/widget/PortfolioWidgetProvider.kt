package com.arquant.mobile.widget

import android.app.PendingIntent
import android.appwidget.AppWidgetManager
import android.appwidget.AppWidgetProvider
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.util.Log
import android.widget.RemoteViews
import com.arquant.mobile.MainActivity
import com.arquant.mobile.R
import org.json.JSONObject

/**
 * 포트폴리오 위젯 — 4x3 셀.
 *
 * 상단: 총평가 / 예수금 / 수익률 요약
 * 하단: 보유 종목 리스트 (RemoteViews ListView, 스크롤 가능)
 *
 * 30분마다 자동 갱신 + 새로고침 버튼으로 즉시 갱신.
 */
class PortfolioWidgetProvider : AppWidgetProvider() {

    override fun onUpdate(
        context: Context,
        appWidgetManager: AppWidgetManager,
        appWidgetIds: IntArray
    ) {
        for (id in appWidgetIds) {
            runCatching { updateOne(context, appWidgetManager, id) }
                .onFailure { Log.w("PortfolioWidget", "update failed (id=$id)", it) }
        }
    }

    override fun onReceive(context: Context, intent: Intent) {
        super.onReceive(context, intent)
        runCatching {
            if (intent.action == ACTION_REFRESH) {
                refreshAll(context)
            }
        }.onFailure { Log.w("PortfolioWidget", "onReceive failed (${intent.action})", it) }
    }

    private fun refreshAll(context: Context) {
        val mgr = AppWidgetManager.getInstance(context)
        val ids = mgr.getAppWidgetIds(ComponentName(context, PortfolioWidgetProvider::class.java))
        // 헤더(요약) 갱신을 위해 전체 업데이트
        for (id in ids) {
            runCatching { updateOne(context, mgr, id) }
        }
        // 리스트 데이터 갱신
        mgr.notifyAppWidgetViewDataChanged(ids, R.id.widget_portfolio_list)
    }

    private fun updateOne(context: Context, mgr: AppWidgetManager, id: Int) {
        val views = RemoteViews(context.packageName, R.layout.widget_portfolio)

        // 요약 데이터 (백그라운드에서 호출됨 — onUpdate는 이미 worker thread에서 안전)
        val pending = goAsync()
        Thread {
            try {
                val data = WidgetHttp.fetchBalance(context)
                val bp = data?.optJSONObject("buying_power")
                if (bp != null) {
                    val totalEval = bp.optDouble("total_eval", 0.0)
                    val cash = bp.optDouble("cash", 0.0)
                    val pnlRatio = bp.optDouble("pnl_ratio", 0.0)
                    val pnlPct = pnlRatio * 100

                    views.setTextViewText(R.id.widget_total_eval, "%,.0f원".format(totalEval))
                    views.setTextViewText(R.id.widget_cash, "예수금 %,.0f원".format(cash))

                    val pnlText = "${if (pnlPct >= 0) "+" else ""}${"%.2f".format(pnlPct)}%"
                    views.setTextViewText(R.id.widget_pnl, pnlText)
                    val pnlColor = if (pnlPct >= 0) 0xFF10B981.toInt() else 0xFFEF4444.toInt()
                    views.setTextColor(R.id.widget_pnl, pnlColor)

                    val holdings = data.optJSONArray("holdings")
                    val count = holdings?.length() ?: 0
                    views.setTextViewText(R.id.widget_holdings_count, "보유 ${count}종목")
                } else {
                    views.setTextViewText(R.id.widget_total_eval, "연결 실패")
                    views.setTextViewText(R.id.widget_cash, "-")
                    views.setTextViewText(R.id.widget_pnl, "-")
                    views.setTextViewText(R.id.widget_holdings_count, "-")
                }

                // ListView adapter
                val svc = Intent(context, PortfolioRemoteService::class.java).apply {
                    putExtra(AppWidgetManager.EXTRA_APPWIDGET_ID, id)
                    this.data = Uri.parse(toUri(Intent.URI_INTENT_SCHEME))
                }
                views.setRemoteAdapter(R.id.widget_portfolio_list, svc)
                views.setEmptyView(R.id.widget_portfolio_list, R.id.widget_portfolio_empty)

                // 헤더 클릭 → 앱 열기 (수익률 탭)
                val openPI = PendingIntent.getActivity(
                    context, 3001,
                    Intent(context, MainActivity::class.java).apply {
                        flags = Intent.FLAG_ACTIVITY_SINGLE_TOP or Intent.FLAG_ACTIVITY_CLEAR_TOP
                    },
                    PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
                )
                views.setOnClickPendingIntent(R.id.widget_portfolio_header, openPI)

                // 새로고침 버튼
                val refreshPI = PendingIntent.getBroadcast(
                    context, 3002,
                    Intent(context, PortfolioWidgetProvider::class.java).apply { action = ACTION_REFRESH },
                    PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
                )
                views.setOnClickPendingIntent(R.id.widget_portfolio_refresh, refreshPI)

                mgr.updateAppWidget(id, views)
                mgr.notifyAppWidgetViewDataChanged(id, R.id.widget_portfolio_list)
            } catch (e: Exception) {
                Log.e("PortfolioWidget", "updateOne bg failed", e)
            } finally {
                pending.finish()
            }
        }.start()
    }

    companion object {
        const val ACTION_REFRESH = "com.arquant.mobile.widget.PORTFOLIO_REFRESH"
    }
}
