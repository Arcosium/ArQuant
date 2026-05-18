"""5개 프리셋을 동일 신호·동일 종목군에 돌려 비교표 출력.

    python3.11 -m backtest.report

⚠️ engine.py 의 '정직성 경계' 를 반드시 함께 읽을 것 — 이 표는 종목 선정
능력이 아니라 **프리셋 규칙 레이어의 상대 리스크/회전율** 비교다.
"""
from __future__ import annotations

import sys

import config
from backtest.engine import load_prices, run_backtest


def main() -> int:
    prices = load_prices()
    if not prices:
        print("❌ data/daily_*.csv 가 없습니다 — 백테스트 불가.")
        return 1
    n_days = max(len(v) for v in prices.values())
    print(f"📊 ArQuant 프리셋 백테스트 — 종목 {len(prices)}개 · 최대 {n_days}거래일\n"
          f"   (동일 SMA 돌파 신호 위에서 프리셋 규칙만 변경 — 상대 비교용)\n")

    rows = []
    for name in config.STRATEGY_PRESETS:  # config 정의 순서 = 방어→초공격
        try:
            rows.append(run_backtest(name, prices))
        except Exception as e:
            print(f"⚠ {name}: 백테스트 실패 — {e}")

    hdr = f"{'프리셋':<16}{'수익률%':>9}{'MDD%':>9}{'샤프*':>8}{'매매':>7}{'승률%':>8}{'최종평가':>14}"
    print(hdr)
    print("─" * len(hdr))
    for r in rows:
        print(f"{r['preset']:<16}{r['total_return_pct']:>9}{r['max_drawdown_pct']:>9}"
              f"{r['sharpe_like']:>8}{r['trades']:>7}{r['win_rate_pct']:>8}"
              f"{r['final_eval']:>14,}")
    print("\n해석: MDD(최대낙폭)가 작고 샤프*가 높을수록 그 프리셋의 규칙이 "
          "해당 신호에서 리스크 대비 효율적. 절대 수익률은 신호 프록시에 "
          "종속되므로 프리셋 간 *상대* 차이만 신뢰할 것.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
