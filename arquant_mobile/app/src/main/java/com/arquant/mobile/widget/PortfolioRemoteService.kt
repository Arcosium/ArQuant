package com.arquant.mobile.widget

import android.content.Context
import android.content.Intent
import android.widget.RemoteViews
import android.widget.RemoteViewsService
import com.arquant.mobile.R
import org.json.JSONArray
import org.json.JSONObject

/**
 * 포트폴리오 위젯 RemoteViewsService — 보유 종목 목록을 ListView로 제공.
 * 종목명, 코드, 수량, 평단→현재가, 수익률(%) 표시.
 */
class PortfolioRemoteService : RemoteViewsService() {
    override fun onGetViewFactory(intent: Intent): RemoteViewsFactory =
        HoldingsFactory(applicationContext)
}

private class HoldingsFactory(private val ctx: Context) : RemoteViewsService.RemoteViewsFactory {
    private var items: List<JSONObject> = emptyList()

    override fun onCreate() {}
    override fun onDestroy() { items = emptyList() }
    override fun getCount(): Int = items.size
    override fun getLoadingView(): RemoteViews? = null
    override fun getViewTypeCount(): Int = 1
    override fun getItemId(position: Int): Long = position.toLong()
    override fun hasStableIds(): Boolean = true

    override fun onDataSetChanged() {
        val data = WidgetHttp.fetchBalance(ctx) ?: return
        val arr: JSONArray = data.optJSONArray("holdings") ?: JSONArray()
        val parsed = mutableListOf<JSONObject>()
        for (i in 0 until arr.length()) {
            arr.optJSONObject(i)?.let { parsed.add(it) }
        }
        items = parsed
    }

    override fun getViewAt(position: Int): RemoteViews {
        val rv = RemoteViews(ctx.packageName, R.layout.widget_portfolio_item)
        val o = items.getOrNull(position) ?: return rv

        val name = o.optString("name", "").ifBlank { o.optString("code", "") }
        val code = o.optString("code", "")
        val qty = o.optInt("qty", 0)
        val avgPrice = o.optDouble("avg_price", 0.0)
        val curPrice = o.optDouble("cur_price", 0.0)
        val pnlPct = o.optDouble("pnl_pct", 0.0)
        val category = o.optString("category", "국내주식")
        val ccy = if (o.optString("ccy", "KRW") == "USD") "$" else ""

        rv.setTextViewText(R.id.widget_item_name, "$name ($code)")
        rv.setTextViewText(R.id.widget_item_qty, "${qty}주 · $category")
        rv.setTextViewText(
            R.id.widget_item_price,
            "${ccy}${"%,.0f".format(avgPrice)} → ${ccy}${"%,.0f".format(curPrice)}"
        )

        val pnlText = "${if (pnlPct >= 0) "+" else ""}${"%.1f".format(pnlPct)}%"
        rv.setTextViewText(R.id.widget_item_pnl, pnlText)
        val pnlColor = if (pnlPct >= 0) 0xFF10B981.toInt() else 0xFFEF4444.toInt()
        rv.setTextColor(R.id.widget_item_pnl, pnlColor)

        return rv
    }
}
