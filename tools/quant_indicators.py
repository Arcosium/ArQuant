"""
NPS Swarm v1.0 - Quant Indicators Tool
Extracted and adapted from KRX Quant Simulator's quant_logic.py.
Provides technical analysis, candle patterns, and financial metric calculations.
"""
import pandas as pd
import numpy as np
from typing import Optional, Dict, List
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta


# ─── Technical Indicator Constants ──────────────────────────────────────────
TECH_INDICATORS_MAP = {
    '골든크로스': 'GC_5_20', '데드크로스': 'DC_5_20',
    '5일선': 'SMA_5', '20일선': 'SMA_20', '60일선': 'SMA_60',
    '1년선': 'SMA_240', '3년선': 'SMA_720',
    '거래대금': 'TradingValue',
    'RSI': 'RSI', 'MACD': 'MACD',
    'STOCH_K': 'Stoch_K', 'STOCH_D': 'Stoch_D', 'OBV': 'OBV',
    '기관순매수': 'Inst_Net_Amt', '외국인순매수': 'Foreign_Net_Amt',
    '볼린저밴드상단돌파': 'BB_Upper_Break', '볼린저밴드하단돌파': 'BB_Lower_Break',
}

CORP_ACTION_GAP_THRESHOLD = 0.70


def adjust_for_corporate_actions(df: pd.DataFrame) -> pd.DataFrame:
    """
    Adjust raw Naver price data for corporate actions (stock splits, bonus issues).
    Ported from KRX Quant Simulator TechnicalAnalysis.adjust_for_corporate_actions.
    """
    if df is None or df.empty or len(df) < 2:
        return df
    df = df.sort_index().copy()
    n = len(df)
    opens = df['Open'].to_numpy(dtype=float)
    closes = df['Close'].to_numpy(dtype=float)

    cf = np.ones(n)
    running = 1.0
    for i in range(n - 1, 0, -1):
        cf[i] = running
        if i == 1:
            continue
        ratio = _corp_action_ratio(closes[i - 1], opens[i], closes[i])
        if ratio is not None and 0 < ratio < 50:
            running *= ratio
    cf[0] = running

    for col in ('Open', 'High', 'Low', 'Close'):
        if col in df.columns:
            df[col] = df[col].to_numpy(dtype=float) * cf
    if 'Volume' in df.columns:
        df['Volume'] = df['Volume'].to_numpy(dtype=float) / np.where(cf == 0, 1.0, cf)
    return df


def _corp_action_ratio(prev_close, today_open, today_close):
    if prev_close <= 0 or today_open <= 0:
        return None
    gap = today_open / prev_close
    th = CORP_ACTION_GAP_THRESHOLD
    if gap < th or gap > 1.0 / th:
        return gap
    return None


def add_technical_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """
    Add all technical indicators to a price DataFrame.
    Ported from KRX Quant Simulator TechnicalAnalysis.add_indicators.
    
    Expected columns: Open, High, Low, Close, Volume
    """
    if df.empty:
        return df

    # Trading value (in 억원)
    df['TradingValue'] = (df['Close'] * df['Volume']) / 1e8

    # Moving averages
    for window in [5, 20, 60, 120, 240, 720]:
        df[f'SMA_{window}'] = df['Close'].rolling(window=window).mean()

    # Golden / Dead cross
    prev_sma5 = df['SMA_5'].shift(1)
    prev_sma20 = df['SMA_20'].shift(1)
    df['GC_5_20'] = (prev_sma5 < prev_sma20) & (df['SMA_5'] > df['SMA_20'])
    df['DC_5_20'] = (prev_sma5 > prev_sma20) & (df['SMA_5'] < df['SMA_20'])

    # RSI (14)
    delta = df['Close'].diff()
    gain = delta.where(delta > 0, 0).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    rs = gain / loss
    df['RSI'] = 100 - (100 / (1 + rs))

    # MACD
    ema12 = df['Close'].ewm(span=12, adjust=False).mean()
    ema26 = df['Close'].ewm(span=26, adjust=False).mean()
    df['MACD'] = ema12 - ema26

    # Stochastic
    low_min = df['Low'].rolling(window=14).min()
    high_max = df['High'].rolling(window=14).max()
    denom = (high_max - low_min).replace(0, np.nan)
    df['Stoch_K'] = 100 * ((df['Close'] - low_min) / denom)
    df['Stoch_D'] = df['Stoch_K'].rolling(window=3).mean()

    # OBV
    df['OBV'] = (np.sign(df['Close'].diff()) * df['Volume']).fillna(0).cumsum()

    # Bollinger Bands
    std_20 = df['Close'].rolling(window=20).std()
    bb_upper = df['SMA_20'] + (std_20 * 2)
    bb_lower = df['SMA_20'] - (std_20 * 2)
    prev_close = df['Close'].shift(1)
    df['BB_Upper_Break'] = (prev_close < bb_upper.shift(1)) & (df['Close'] > bb_upper)
    df['BB_Lower_Break'] = (prev_close > bb_lower.shift(1)) & (df['Close'] < bb_lower)

    # Candle patterns
    O, C, H, L = df['Open'], df['Close'], df['High'], df['Low']
    body = np.abs(C - O)
    upper_shadow = H - df[['Close', 'Open']].max(axis=1)
    lower_shadow = df[['Close', 'Open']].min(axis=1) - L

    df['Is_Yangbong'] = C > O
    df['Is_Yinbong'] = C < O
    body_pct = (body / O) * 100
    df['Is_Big_Yang'] = df['Is_Yangbong'] & (body_pct >= 5.0)
    df['Is_Big_Yin'] = df['Is_Yinbong'] & (body_pct >= 5.0)
    df['Is_Doji'] = body <= (O * 0.003)
    df['Is_Hammer'] = (lower_shadow > body * 2) & (upper_shadow < body * 0.5)
    df['Is_Inv_Hammer'] = (upper_shadow > body * 2) & (lower_shadow < body * 0.5)

    prev_C = C.shift(1)
    prev_O = O.shift(1)
    df['Is_Bull_Engulf'] = df['Is_Yinbong'].shift(1) & df['Is_Yangbong'] & (C > prev_O) & (O < prev_C)
    df['Is_Bear_Engulf'] = df['Is_Yangbong'].shift(1) & df['Is_Yinbong'] & (C < prev_O) & (O > prev_C)

    # Consecutive limits
    pct_chg = df['Close'].pct_change() * 100
    df['Is_UpperLimit'] = pct_chg >= 29.5
    df['Is_LowerLimit'] = pct_chg <= -29.5

    # 52-week high/low
    df['Is_52W_High'] = df['Close'] >= df['High'].rolling(252, min_periods=1).max()
    df['Is_52W_Low'] = df['Close'] <= df['Low'].rolling(252, min_periods=1).min()

    return df


async def analyze_stock_technical(ticker: str, market: str = "KR") -> str:
    """
    Perform full technical analysis on a stock ticker.
    Agent-facing tool function.

    Args:
        ticker: Stock code (e.g., "005930" for Samsung)
        market: "KR" for Korean stocks, "US" for US stocks

    Returns:
        Formatted technical analysis summary
    """
    if market == "KR":
        df = _fetch_naver_stock_data(ticker)
    else:
        return f"[Quant] 해외 주식 ({ticker}) 기술적 분석은 KIS API를 통해 수행합니다."

    if df is None or df.empty:
        return f"[Quant] {ticker} 데이터를 불러올 수 없습니다."

    df = adjust_for_corporate_actions(df)
    df = add_technical_indicators(df)
    latest = df.iloc[-1]

    lines = [f"[기술적 분석] {ticker} | 최근 거래일 기준\n"]
    lines.append(f"  종가: {latest['Close']:,.0f} | 거래대금: {latest.get('TradingValue', 0):.1f}억")
    lines.append(f"  SMA5: {latest.get('SMA_5', 0):,.0f} | SMA20: {latest.get('SMA_20', 0):,.0f} | SMA60: {latest.get('SMA_60', 0):,.0f}")
    lines.append(f"  RSI: {latest.get('RSI', 0):.1f} | MACD: {latest.get('MACD', 0):.2f}")
    lines.append(f"  Stoch K: {latest.get('Stoch_K', 0):.1f} | Stoch D: {latest.get('Stoch_D', 0):.1f}")

    signals = []
    if latest.get('GC_5_20', False):
        signals.append("🟢 골든크로스 발생")
    if latest.get('DC_5_20', False):
        signals.append("🔴 데드크로스 발생")
    if latest.get('BB_Upper_Break', False):
        signals.append("⬆️ 볼린저 상단 돌파")
    if latest.get('BB_Lower_Break', False):
        signals.append("⬇️ 볼린저 하단 돌파")
    if latest.get('Is_52W_High', False):
        signals.append("🔥 52주 신고가")
    if latest.get('Is_52W_Low', False):
        signals.append("❄️ 52주 신저가")
    if latest.get('Is_Bull_Engulf', False):
        signals.append("📈 상승장악형 캔들")
    if latest.get('Is_Bear_Engulf', False):
        signals.append("📉 하락장악형 캔들")

    rsi_val = latest.get('RSI', 50)
    if rsi_val > 70:
        signals.append("⚠️ RSI 과매수 구간")
    elif rsi_val < 30:
        signals.append("💡 RSI 과매도 구간")

    if signals:
        lines.append(f"\n  🚨 시그널: {' | '.join(signals)}")
    else:
        lines.append(f"\n  ℹ️ 특별한 시그널 없음")

    return "\n".join(lines)


def _fetch_naver_stock_data(code: str, years: int = 2) -> Optional[pd.DataFrame]:
    """
    Fetch stock price data from Naver Finance.
    Ported from KRX Quant Simulator CrawlerUtil.fetch_naver_stock_html.
    """
    result = []
    max_pages = int(years * 26) + 10
    target_date_limit = datetime.today() - timedelta(days=years * 365)
    headers = {'User-Agent': 'Mozilla/5.0'}

    try:
        for page in range(1, min(max_pages, 200) + 1):
            url = f"https://finance.naver.com/item/sise_day.nhn?code={code}&page={page}"
            res = requests.get(url, headers=headers, timeout=10)
            soup = BeautifulSoup(res.text, 'lxml')
            rows = soup.select('table.type2 tr')
            valid_rows = 0
            for row in rows:
                cols = row.find_all('td')
                if len(cols) != 7:
                    continue
                try:
                    date_text = cols[0].text.strip()
                    if not date_text:
                        continue
                    date = pd.to_datetime(date_text)
                    if date < target_date_limit:
                        break
                    close = int(cols[1].text.replace(',', ''))
                    open_ = int(cols[3].text.replace(',', ''))
                    high = int(cols[4].text.replace(',', ''))
                    low = int(cols[5].text.replace(',', ''))
                    volume = int(cols[6].text.replace(',', ''))
                    result.append({
                        'Date': date, 'Open': open_, 'High': high,
                        'Low': low, 'Close': close, 'Volume': volume
                    })
                    valid_rows += 1
                except Exception:
                    continue
            if valid_rows == 0 and page > 1:
                break
    except Exception:
        pass

    if not result:
        return pd.DataFrame()

    df = pd.DataFrame(result).drop_duplicates(subset=['Date']).sort_values('Date')
    df = df.set_index('Date')
    return df
