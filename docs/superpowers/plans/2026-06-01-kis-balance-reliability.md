# KIS 잔고측정 신뢰성 강화 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. 테스트는 반드시 `python3.11 -m pytest`.

**Goal:** KIS 공식 open-trading-api 표준 로직을 이식해 ArQuant의 국내(KR)·해외(US) 잔고측정과 주문 사이징을 단단·신뢰성 있게 만든다.

**Architecture:** `infra/kis_broker.py`에 (1) 연속조회 페이징 헬퍼 + 부분성공 보존, (2) 전역 호출간격 락, (3) 신규 권위조회 메서드(매수가능 TTTC8908R/TTTS3007R, 매도가능 TTTC8408R, 통합총자산 CTRP6548R, 결제기준잔고 CTRP6010R, 실현손익 TTTC8494R)를 추가한다. `portfolio_holdings`/equity 기록은 **곡선=D+2식+결제기준 해외**(안정) 유지, **대시보드 현재총자산 표시만 KIS tot_asst_amt**(HTS 일치). 주문 사이징(`main_swarm.py`)은 권위 매수/매도가능 조회로 clamp. 모의계정 해외평가는 KIS 미지원이므로 per-uid 보유내역 파일로 라이브 주입.

**Tech Stack:** Python 3.11, aiohttp, pytest, asyncio. KIS REST OpenAPI.

---

## 라이브 검증된 API 사실 (2026-06-01, 실거래 uid=1 / 모의 uid=2 read-only 확인)

**국내 inquire-balance(TTTC8434R) output2 — `_raw_balance`**
- `prvs_rcdl_excc_amt` = D+2 정산예수금 ← **우리가 쓰는 값**(곡선/사이징). 실측 7,975,828
- `dnca_tot_amt` = D0 ← **금지**(부풀림·유령점프). 실측 6,552,999 (D+2와 1.42M 차이!)
- `nxdy_excc_amt`=D+1, `scts_evlu_amt`=국내유가증권평가, `nass_amt`=순자산

**CTRP6548R 투자계좌자산현황(KR+US 통합) — 실전 전용**
- URL `/uapi/domestic-stock/v1/trading/inquire-account-balance`, params `{CANO, ACNT_PRDT_CD, INQR_DVSN_1:"", BSPR_BF_DT_APLY_YN:""}`
- `output2.tot_asst_amt` = 통합총자산(**D0 예수금 기반**) 실측 8,666,418 → **대시보드 '현재총자산' 표시 전용**(곡선엔 절대 안 씀)
- `output2.tot_dncl_amt`(D0), `nass_tot_amt`, `evlu_amt_smtl`, `frcr_evlu_tota`, `ovrs_stck_evlu_amt1`

**CTRP6504R 해외 체결기준현재잔고(실시간) — 실전 전용. NATN_CD '000' 권장**
- URL `/uapi/overseas-stock/v1/trading/inquire-present-balance`, params `{CANO,ACNT_PRDT_CD,WCRC_FRCR_DVSN_CD:"02",NATN_CD:"000",TR_MKET_CD:"00",INQR_DVSN_CD:"00"}`
- `output3.frcr_evlu_tota`=외화평가총액(**현행 유지·표시용**) 실측 154,093
- `output3.tot_asst_amt`(해외 총자산), `tot_dncl_amt`, `tot_evlu_pfls_amt`, `evlu_amt_smtl_amt`(해외주식 KRW), `frcr_use_psbl_amt`
- `output2[0].frcr_dncl_amt_2`=외화예수금, `frst_bltn_exrt`=환율
- `output1[]` 종목: `pdno, cblc_qty13, ovrs_now_pric1, avg_unpr3, frcr_evlu_amt2, evlu_pfls_amt2, evlu_pfls_rt1, item_lnkg_excg_cd, ovrs_excg_cd`(거래소 명시→중복노출 없음)

**CTRP6010R 해외 결제기준잔고 — 실전 전용**
- URL `/uapi/overseas-stock/v1/trading/inquire-paymt-stdr-balance`, params `{CANO,ACNT_PRDT_CD,BASS_DT:<오늘KST yyyymmdd>,WCRC_FRCR_DVSN_CD:"01",INQR_DVSN_CD:"00"}`
- `output3.frcr_cblc_wcrc_evlu_amt_smtl`=외화잔고 원화평가합계(**곡선용 결제기준 해외평가**) 실측 154,093
- `output3.tot_asst_amt2`=결제기준 총자산 8,665(7,285,493), `tot_evlu_pfls_amt`, `wcrc_evlu_amt_smtl`, `tot_dncl_amt`

**TTTS3007R 해외 매수가능 — 실전/모의(VTTS3007R)**
- URL `/uapi/overseas-stock/v1/trading/inquire-psamount`, params `{CANO,ACNT_PRDT_CD,OVRS_EXCG_CD,OVRS_ORD_UNPR,ITEM_CD}`
- `output.ord_psbl_frcr_amt`=USD 주문가능 실측 102.51, `max_ord_psbl_qty`=2, `ovrs_ord_psbl_amt`, `exrt`, `frcr_ord_psbl_amt1`

**TTTC8908R 국내 매수가능 — 실전/모의(VTTC8908R). ORD_DVSN='01'(시장가) 필수**
- URL `/uapi/domestic-stock/v1/trading/inquire-psbl-order`, params `{CANO,ACNT_PRDT_CD,PDNO,ORD_UNPR,ORD_DVSN:"01",CMA_EVLU_AMT_ICLD_YN:"N",OVRS_ICLD_YN:"N"}`
- `output.nrcvb_buy_qty`=미수없는매수가능수량 실측 16, `nrcvb_buy_amt`, `ord_psbl_cash`, `max_buy_qty`

**TTTC8408R 국내 매도가능 — 실전/모의(VTTC8408R)**
- URL `/uapi/domestic-stock/v1/trading/inquire-psbl-sell`, params `{CANO,ACNT_PRDT_CD,PDNO}`
- `output.ord_psbl_qty`=주문가능수량(보유 0이면 빈값)

**모의(uid=2) 한계 확정:** VTTS3012R rt_cd=0이나 0행, CTRP6504R rt_cd=1 'TR 아님'. → 모든 CTRP류·결제기준은 `is_mock`이면 호출 skip. 모의 해외평가는 라이브 주입(Task 13).

---

## 파일 구조

- **Modify** `infra/kis_broker.py` — 페이징 헬퍼·rate-lock·신규 권위조회 6종·portfolio_holdings 배선·모의 주입
- **Modify** `main_swarm.py` — 매수/매도 사이징 clamp(권위조회), 실현손익 감사대조, 대시보드 현재총자산
- **Modify** `server/app.py` — `/api/balance`에 현재총자산(KIS)·모의 배지 필드
- **Modify** `server/static/index.html` — 현재총자산 표시
- **Create** `data/<uid>/overseas_manual_holdings.json` — 모의 해외 보유내역(라이브 평가 입력)
- **Create** `tests/test_kis_paging.py`, `tests/test_buying_power_trs.py`, `tests/test_overseas_settled_curve.py`, `tests/test_dashboard_total_asset.py`, `tests/test_mock_overseas_inject.py`, `tests/test_sell_qty_clamp.py`

테스트는 라이브 KIS 미사용 — aiohttp 응답을 monkeypatch/stub 한다(기존 `tests/` 패턴 따름).

---

## Group A — 인프라 기반 (kis_broker.py)

### Task 1: 전역 호출간격 rate-lock (env별 최소간격)
**Files:** Modify `infra/kis_broker.py` (`__init__`, `_authed_json`, `_get_json`); Test `tests/test_kis_paging.py`

- [ ] **Step 1** 실패 테스트: 두 동시 호출이 `_min_interval` 이상 간격으로 직렬화되는지(monotonic 스텁으로 호출시각 기록) 검증.
- [ ] **Step 2** `python3.11 -m pytest tests/test_kis_paging.py -k rate_lock -v` → FAIL.
- [ ] **Step 3** 구현: `__init__`에 `self._rate_lock = asyncio.Lock()`, `self._last_call = 0.0`, `self._min_interval = 0.5 if self.is_mock else 0.06`. 신규 `async def _pace(self)`: lock 안에서 `now=loop.time()`; `wait=self._min_interval-(now-self._last_call)`; `if wait>0: await asyncio.sleep(wait)`; `self._last_call=loop.time()`. `_authed_json`/`_get_json` 요청 직전과 페이징 루프 사이에서 `await self._pace()`.
- [ ] **Step 4** PASS 확인.
- [ ] **Step 5** Commit `feat(kis): 전역 호출간격 rate-lock 추가`.

### Task 2: `_mock_tr` 변환조건 'T'→('T','J','C')
**Files:** Modify `infra/kis_broker.py:238-243`; Test `tests/test_model_override_tools.py` 또는 신규.

- [ ] **Step 1** 실패 테스트: `is_mock=True`에서 `_mock_tr("CTRP6504R")=="VTRP6504R"`, `_mock_tr("JTTT...")=="VTTT..."`, 오버라이드맵(TTTT1006U→VTTT1001U) 우선 유지.
- [ ] **Step 2** FAIL 확인.
- [ ] **Step 3** 구현: 조건을 `tr_id[0] in ('T','J','C')`로 확장(오버라이드맵 먼저 검사).
- [ ] **Step 4-5** PASS·Commit `fix(kis): 모의 tr_id 변환범위 T/J/C로 확장(샘플 표준)`.

### Task 3: 연속조회 페이징 헬퍼 + 부분성공 보존
**Files:** Modify `infra/kis_broker.py`; Test `tests/test_kis_paging.py`

신규 `async def _paged_get(self, url, tr_id, params, fk_key, nk_key, out_keys=("output1",), max_depth=10)`:
- 첫 요청 tr_cont="". 응답 헤더 `tr_cont`(aiohttp `r.headers`)가 'F'/'M'이면 다음 요청 헤더 tr_cont="N", 파라미터에 `params[fk_key]=resp_body[fk_key.lower()]`, `params[nk_key]=...nk` 채워 재호출. 각 out_key 리스트 누적.
- rt_cd≠0: page==0이면 `ok=False`로 누적분 반환; page>0이면 누적분을 `ok=True, partial=True`로 반환(KIS present-balance 패턴). 페이지 사이 `await self._pace()`.
- 반환 `{out_key: [...], "ok":bool, "rt_cd":..., "msg1":..., "msg_cd":..., "partial":bool}`.

- [ ] **Step 1** 실패 테스트: 헤더 tr_cont 'M'→'F'→'D' 시퀀스 스텁으로 2페이지 누적 + 후반 실패 시 부분보존(partial=True) 확인.
- [ ] **Step 2** FAIL.
- [ ] **Step 3** 구현.
- [ ] **Step 4-5** PASS·Commit `feat(kis): tr_cont 연속조회 헬퍼+부분성공 보존`.

---

## Group B — 신규 권위조회 메서드 (kis_broker.py)

### Task 4: `kr_psbl_order(code, unpr)` — TTTC8908R 국내 매수가능 (ORD_DVSN='01')
**Files:** Modify `infra/kis_broker.py`; Test `tests/test_buying_power_trs.py`

- [ ] **Step 1** 실패 테스트: stub 응답 `{"rt_cd":"0","output":{"nrcvb_buy_qty":"16","ord_psbl_cash":"6552999"}}` → 반환 `{"ok":True,"buy_qty":16,"cash":6552999.0}`.
- [ ] **Step 2** FAIL.
- [ ] **Step 3** 구현: tr_id `TTTC8908R`(모의 `VTTC8908R`), params 위 표대로 ORD_DVSN="01", ORD_UNPR=str(int(unpr or 0)). `nrcvb_buy_qty`→int. 실패/모의미지원 시 `{"ok":False,"buy_qty":None}`.
- [ ] **Step 4-5** PASS·Commit.

### Task 5: `kr_psbl_sell_qty(code)` — TTTC8408R 국내 매도가능
**Files:** Modify `infra/kis_broker.py`; Test `tests/test_buying_power_trs.py`

- [ ] **Step 1** 실패 테스트: stub `{"rt_cd":"0","output":{"ord_psbl_qty":"3"}}` → `3`. 빈/실패 → `None`.
- [ ] **Step 2-5** FAIL→구현(tr_id TTTC8408R/VTTC8408R, params {CANO,ACNT_PRDT_CD,PDNO})→PASS·Commit.

### Task 6: `us_buying_power(ticker, unpr, excg)` — TTTS3007R 해외 매수가능
**Files:** Modify `infra/kis_broker.py`; Test `tests/test_buying_power_trs.py`

- [ ] **Step 1** 실패 테스트: stub `{"rt_cd":"0","output":{"ord_psbl_frcr_amt":"102.51","max_ord_psbl_qty":"2","exrt":"1503.2"}}` → `{"ok":True,"usd":102.51,"qty":2,"exrt":1503.2}`.
- [ ] **Step 2-5** FAIL→구현(tr_id TTTS3007R/VTTS3007R, params {CANO,ACNT_PRDT_CD,OVRS_EXCG_CD:excg or "NASD",OVRS_ORD_UNPR:f"{unpr:.4f}",ITEM_CD:ticker})→PASS·Commit.

### Task 7: `kr_account_asset()` — CTRP6548R 통합총자산(대시보드 표시용, 실전만)
**Files:** Modify `infra/kis_broker.py`; Test `tests/test_dashboard_total_asset.py`

- [ ] **Step 1** 실패 테스트: stub output2 `tot_asst_amt=8666418` → `{"ok":True,"tot_asst_amt":8666418.0,"tot_dncl_amt":...}`. `is_mock`이면 호출없이 `{"ok":False}`.
- [ ] **Step 2-5** FAIL→구현(is_mock skip 게이트 포함)→PASS·Commit.

### Task 8: `_overseas_settled_krw()` — CTRP6010R 결제기준 해외평가(곡선용, 실전만)
**Files:** Modify `infra/kis_broker.py`; Test `tests/test_overseas_settled_curve.py`

- [ ] **Step 1** 실패 테스트: stub output3 `frcr_cblc_wcrc_evlu_amt_smtl=154093, tot_asst_amt2=7285493` → `{"ok":True,"krw":154093.0,"tot_asst_amt2":...}`. is_mock skip.
- [ ] **Step 2-5** FAIL→구현(BASS_DT=오늘KST, is_mock skip)→PASS·Commit.

### Task 9: `kr_realized_pnl_audit()` — TTTC8494R 실현손익 감사(실전만, 주문 무영향)
**Files:** Modify `infra/kis_broker.py`; Test `tests/test_buying_power_trs.py`

- [ ] **Step 1** 실패 테스트: stub output1 rlzt_pfls 합 → 반환 `{"ok":True,"realized":<sum>}`. is_mock skip.
- [ ] **Step 2-5** FAIL→구현(inquire-balance 파라미터+COST_ICLD_YN='Y', PRCS_DVSN='00')→PASS·Commit. (필드명 rlzt_pfls는 실전 1회 라이브 덤프로 재확인 후 확정.)

---

## Group C — 해외 잔고 페이징·필드 보강 (kis_broker.py)

### Task 10: `_overseas_holdings` 페이징 적용 + present-balance output1 1차소스
**Files:** Modify `infra/kis_broker.py:638-673`; Test `tests/test_kis_paging.py`

- [ ] **Step 1** 실패 테스트: 거래소 응답이 2페이지(tr_cont 'M'→'D')일 때 양 페이지 종목 모두 포함.
- [ ] **Step 2-3** FAIL→각 거래소 호출을 Task3 `_paged_get`로 교체(fk/nk = CTX_AREA_FK200/NK200). 기존 거래소 중복 dedupe 유지.
- [ ] **Step 4-5** PASS·Commit.

### Task 11: `_overseas_present_krw` 페이징 + output3 추가필드 + NATN_CD '000'
**Files:** Modify `infra/kis_broker.py:706-738`; Test `tests/test_overseas_settled_curve.py`

- [ ] **Step 1** 실패 테스트: stub output3에 `tot_asst_amt, tot_dncl_amt, tot_evlu_pfls_amt` 포함 → 반환 dict에 키 존재. output2 `frcr_dncl_amt_2` 파싱.
- [ ] **Step 2-3** FAIL→NATN_CD "000", 반환 dict 확장(`{ok,krw_value(frcr_evlu_tota),stock_value,exrt,tot_asst_amt,deposit_krw(frcr_dncl_amt_2×exrt)}`), 페이징 적용.
- [ ] **Step 4-5** PASS·Commit.

---

## Group D — 평가 배선 (곡선=결제기준/표시=KIS총자산)

### Task 12: `portfolio_holdings` 배선 — 곡선 해외분=결제기준 우선, bp에 현재총자산/검증 부착
**Files:** Modify `infra/kis_broker.py:860-949`; Test `tests/test_overseas_settled_curve.py`, `tests/test_dashboard_total_asset.py`

곡선용 해외분 우선순위: ① `_overseas_settled_krw()`(결제기준, 실전) → ② 실패시 실시간 `_overseas_present_krw().krw_value` → ③ 캐시폴백(현행). `bp["total_eval"]`에 더하는 해외분을 이 우선순위로. 별도로 `bp["display_total_asset"]` = `kr_account_asset().tot_asst_amt`(실전, 대시보드 표시 전용, 곡선식 total_eval과 분리). 두 값 괴리 >1% 면 `logger.warning`.

- [ ] **Step 1** 실패 테스트: 결제기준 성공 시 total_eval 해외분=결제기준값; display_total_asset=KIS tot_asst_amt; 괴리 경고 로깅.
- [ ] **Step 2-5** FAIL→구현→PASS·Commit.

### Task 13: 모의계정 해외 라이브 주입
**Files:** Modify `infra/kis_broker.py` (`_overseas_holdings` 또는 `portfolio_holdings`); Create `data/<uid>/overseas_manual_holdings.json`; Test `tests/test_mock_overseas_inject.py`

`is_mock`일 때만: per-uid `overseas_manual_holdings.json`(`[{"ticker":"NVDA","qty":100,"avg_price":175.5}]`) 읽어 각 티커 `us_last_price`×`get_usdkrw`로 평가, holdings(ccy=USD, krw_value)에 주입하고 total_eval에 합산. 파일 없으면 무동작.

- [ ] **Step 1** 실패 테스트: is_mock + 파일에 1종목 → holdings에 USD종목 등장 + total_eval 증가. 파일 없으면 변화 없음.
- [ ] **Step 2-5** FAIL→구현→PASS·Commit. **데이터 입력 대기: 사장님이 모의 US 보유(티커+수량) 제공 시 파일 작성.**

---

## Group E — 주문 사이징 clamp (main_swarm.py)

### Task 14: 국내 매수 — kr_psbl_order로 qty clamp
**Files:** Modify `main_swarm.py:1918-1938`; Test `tests/test_order_disposition.py`(확장)

- [ ] **Step 1** 실패 테스트: 사이징 qty=20이지만 `kr_psbl_order.buy_qty=16` → 최종 16. ok=False면 현행 qty 유지(주문 드롭 금지).
- [ ] **Step 2-3** FAIL→매수 orders.append 직전 `pb=await self.broker.kr_psbl_order(code, price)`; `if pb["ok"] and pb["buy_qty"] is not None: qty=min(qty, pb["buy_qty"])`. qty<1로 떨어지면 노트 기록(0주 주문 금지).
- [ ] **Step 4-5** PASS·Commit.

### Task 15: 해외 매수 — us_buying_power로 USD 사이징(KRW 합성 제거)
**Files:** Modify `main_swarm.py:1948-1974`; Test `tests/test_us_fill_and_valuation.py`(확장)

- [ ] **Step 1** 실패 테스트: `us_buying_power.qty=2, usd=102.51` + 예산 충분 → 최종 qty=min(예산qty, 2). ok=False면 현행 합성 폴백.
- [ ] **Step 2-3** FAIL→해외 분기에서 `up=await self.broker.us_buying_power(tk, us_px, excg)`; ok면 `qty_us=min(qty_us, up["qty"])` 및 환율은 `up["exrt"]` 우선. ok=False면 기존 `cash/_krw_per_usd` 합성 유지(폴백).
- [ ] **Step 4-5** PASS·Commit.

### Task 16: 매도 — kr_psbl_sell_qty로 clamp + 글리치 폴백
**Files:** Modify `main_swarm.py` 매도 조립부(`_assemble_sell_orders` 호출 전/후) 및 `infra/kis_broker.py:kr_sell`; Test `tests/test_sell_qty_clamp.py`

- [ ] **Step 1** 실패 테스트: 매도 qty=5, hldg_qty=5, `kr_psbl_sell_qty=3` → 최종 3. `ord_psbl_qty=0 & cblc>0`(글리치) → 1회 재조회 후에도 0이면 hldg_qty로 전송(드롭 금지)+warning.
- [ ] **Step 2-5** FAIL→구현(KR 매도만; US는 _overseas_holdings ord_psbl_qty1 차후)→PASS·Commit.

---

## Group F — 실현손익 감사 (main_swarm.py)

### Task 17: KIS 실현손익 주기 대조 경고
**Files:** Modify `main_swarm.py`(`_equity_poller` 또는 별도 주기); Test `tests/test_buying_power_trs.py`

- [ ] **Step 1** 실패 테스트: `kr_realized_pnl_audit.realized`와 우리 누적 실현손익 괴리 임계 초과 시 경고 emit. is_mock skip.
- [ ] **Step 2-5** FAIL→구현(주문·표시 무영향, 로그/통신로그 경고만)→PASS·Commit.

---

## Group G — 서버/프론트 (현재총자산 표시)

### Task 18: `/api/balance`에 현재총자산(KIS)·모의배지
**Files:** Modify `server/app.py:977,1010`; Test 기존 balance 테스트 확장

- [ ] **Step 1** 실패 테스트: 응답에 `display_total_asset`(실전) 또는 `overseas_unsupported:true`(모의) 포함.
- [ ] **Step 2-5** FAIL→구현→PASS·Commit.

### Task 19: 프론트 현재총자산 표시
**Files:** Modify `server/static/index.html`
- [ ] 대시보드 상단 '현재 총자산'(KIS, HTS 일치) 표시. 모의면 'US 평가 모의 API 미지원' 배지. (WebView 재빌드 불필요.)
- [ ] Commit.

---

## Group H — 통합 검증 & 배포

### Task 20: 전체 테스트 + 라이브 read-only 스모크
- [ ] `python3.11 -m pytest` 전체 그린.
- [ ] 실거래(uid=1)·모의(uid=2) read-only 스모크: portfolio_holdings/매수가능/매도가능 호출이 정상 dict 반환, 곡선식 total_eval이 D+2 기반 유지, display_total_asset이 KIS tot_asst_amt와 일치.
- [ ] **사장님 확인 후** `sudo systemctl restart arquant.service` → `status` 헬스체크(port 8500). 재시작은 단 1회.

---

## Self-Review 체크
- 스펙 커버리지: 결정 6종(구현순서·총자산표시·결제기준·매수가능3종·모의주입·KPI감사) + 부수(페이징·부분성공·rate-lock·NATN000·frcr_dncl_amt_2·mock_tr·msg_cd) 모두 Task에 매핑됨.
- 안전장치: 모든 신규 CTRP/결제기준/감사조회는 `is_mock` skip 게이트. 모든 clamp는 ok=False/글리치 시 **현행 폴백**(주문 절대 드롭 금지 준수). 곡선식은 D+2 불변(유령점프 방지).
- 미확정 데이터: 모의 US 보유내역(Task13), TTTC8494R `rlzt_pfls` 필드명(Task9) — 실전 1회 라이브 덤프로 확정.
