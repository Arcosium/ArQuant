// ArQuant v1.0 사용설명서 생성기 — README.md 기반 사용자 중심 매뉴얼
const fs = require("fs");
const {
  Document, Packer, Paragraph, TextRun, Table, TableRow, TableCell,
  Header, Footer, AlignmentType, LevelFormat, ExternalHyperlink,
  TabStopType, TabStopPosition, TableOfContents, HeadingLevel,
  BorderStyle, WidthType, ShadingType, PageNumber, PageBreak,
} = require("docx");

const FONT = "Malgun Gothic"; // 한글 친화 폰트 (Word가 미설치 시 CJK 폰트로 자동 대체)
const CONTENT_W = 9360;       // US Letter, 1" 여백

// ── helpers ───────────────────────────────────────────────
const P = (text, opts = {}) =>
  new Paragraph({ spacing: { after: 120, line: 312 }, ...opts,
    children: opts.children || [new TextRun({ text, ...(opts.run || {}) })] });

const H1 = (t) => new Paragraph({ heading: HeadingLevel.HEADING_1, children: [new TextRun(t)] });
const H2 = (t) => new Paragraph({ heading: HeadingLevel.HEADING_2, children: [new TextRun(t)] });
const H3 = (t) => new Paragraph({ heading: HeadingLevel.HEADING_3, children: [new TextRun(t)] });

const bullet = (text, level = 0) =>
  new Paragraph({ numbering: { reference: "bullets", level },
    spacing: { after: 80, line: 300 }, children: [new TextRun(text)] });

const num = (text) =>
  new Paragraph({ numbering: { reference: "steps", level: 0 },
    spacing: { after: 80, line: 300 }, children: [new TextRun(text)] });

const note = (label, text) =>
  new Paragraph({
    spacing: { before: 80, after: 160, line: 300 },
    shading: { fill: "FFF6E0", type: ShadingType.CLEAR },
    border: { left: { style: BorderStyle.SINGLE, size: 18, color: "E0A300", space: 8 } },
    children: [new TextRun({ text: `${label}  `, bold: true }), new TextRun(text)],
  });

const border = { style: BorderStyle.SINGLE, size: 1, color: "C9D3DF" };
const borders = { top: border, bottom: border, left: border, right: border,
  insideHorizontal: border, insideVertical: border };

function table(headers, rows, widths) {
  const total = widths.reduce((a, b) => a + b, 0);
  const cell = (txt, w, opts = {}) => new TableCell({
    borders,
    width: { size: w, type: WidthType.DXA },
    margins: { top: 60, bottom: 60, left: 110, right: 110 },
    shading: opts.head ? { fill: "1F3B57", type: ShadingType.CLEAR } : (opts.shade ? { fill: "EEF3F8", type: ShadingType.CLEAR } : undefined),
    children: [new Paragraph({ spacing: { after: 0, line: 276 },
      children: [new TextRun({ text: String(txt), bold: !!opts.head,
        color: opts.head ? "FFFFFF" : undefined, size: 19 })] })],
  });
  const trs = [new TableRow({ tableHeader: true,
    children: headers.map((h, i) => cell(h, widths[i], { head: true })) })];
  rows.forEach((r, ri) => trs.push(new TableRow({
    children: r.map((c, i) => cell(c, widths[i], { shade: ri % 2 === 1 })) })));
  return new Table({ width: { size: total, type: WidthType.DXA },
    columnWidths: widths, rows: trs });
}

const link = (text, url) => new ExternalHyperlink({
  link: url, children: [new TextRun({ text, style: "Hyperlink" })] });

// ── document ──────────────────────────────────────────────
const children = [];

// 표지
children.push(
  new Paragraph({ spacing: { before: 2600, after: 200 }, alignment: AlignmentType.CENTER,
    children: [new TextRun({ text: "ArQuant v1.0", bold: true, size: 64, color: "1F3B57" })] }),
  new Paragraph({ alignment: AlignmentType.CENTER, spacing: { after: 120 },
    children: [new TextRun({ text: "AI 멀티에셋 퀀트 트레이딩 시스템", size: 30, color: "44607A" })] }),
  new Paragraph({ alignment: AlignmentType.CENTER, spacing: { after: 600 },
    children: [new TextRun({ text: "사용설명서", bold: true, size: 40 })] }),
  new Paragraph({ alignment: AlignmentType.CENTER, spacing: { after: 80 },
    children: [link("https://arquant.ai-ve.uk", "https://arquant.ai-ve.uk")] }),
  new Paragraph({ alignment: AlignmentType.CENTER, spacing: { after: 80 },
    children: [new TextRun({ text: "최종 업데이트: 2026-05-18", size: 20, color: "8A99AB" })] }),
  new Paragraph({ children: [new PageBreak()] }),
);

// 목차
children.push(H1("목차"),
  new TableOfContents("Table of Contents", { hyperlink: true, headingStyleRange: "1-2" }),
  new Paragraph({ children: [new PageBreak()] }));

// 1. 소개
children.push(H1("1. ArQuant 소개"));
children.push(P("ArQuant는 9명의 AI 에이전트(+ 운용지원실장 산하 팀장 3명, 뉴스 분류·큐레이터)가 협업하여 글로벌 지수·실시간 시황·3년치 일봉/수급·실시간 분봉·DART 공시·증권 속보를 종합 분석하고, 국내주식·해외주식·국내채권·펀드 보유분을 통합 관리하며 한국투자증권(KIS) OpenAPI로 실거래를 수행하는 자동매매 시스템입니다."));
children.push(P("사용자는 웹 브라우저 또는 모바일 앱으로 접속해 ‘실행’만 누르면, 시스템이 장 개장·정기 주기에 맞춰 스스로 분석하고 매수/매도합니다. 사용자는 전략을 고르거나, 채팅으로 AI에게 직접 지시하거나, 수익률을 지켜보기만 하면 됩니다."));
children.push(H2("1.1 무엇을 자동으로 하나요"));
[
  "장이 열려 있는 동안 1시간 주기 + 개장 순간에 2패스 분석 사이클을 자동 수행합니다.",
  "뉴스를 10분마다 수집하고 한국/미국/공통으로 자동 분류합니다.",
  "리스크 게이트(결정론 규칙) + DART 공시·재무 재심을 통과한 종목만 매수합니다.",
  "보유 종목은 사후관리실장이 매 사이클 매도/유지/익절·손절을 재결정합니다.",
  "모든 의사결정·체결 결과를 한국어로 보고하고 화면에 기록합니다.",
].forEach((t) => children.push(bullet(t)));
children.push(note("핵심", "사용자가 매번 종목을 고를 필요가 없습니다. 전략 프리셋과 위험 한도를 정해두면 나머지는 AI가 처리합니다."));

// 2. 준비물
children.push(H1("2. 시작하기 전에 — 준비물"));
children.push(P("회원가입 시 아래 정보가 필요합니다. OpenRouter API 키는 필수이며, KIS 키와 계좌번호가 있어야 실거래가 가능합니다."));
children.push(table(
  ["항목", "필수", "용도"],
  [
    ["OpenRouter API Key", "필수", "모든 AI 분석 호출에 사용 (비용 발생)"],
    ["KIS App Key / Secret", "거래 시 필수", "한국투자증권 시세 조회·주문"],
    ["KIS 계좌번호", "거래 시 필수", "실거래가 일어나는 계좌"],
    ["Base URL", "거래 시 필수", "KIS 운영/모의 도메인"],
    ["DART API Key", "선택", "공시·재무 재심 (없으면 공시 분석 생략)"],
  ],
  [2600, 1400, 5360]));
children.push(note("비용 안내", "AI 분석은 OpenRouter 크레딧을 사용합니다. 균형형 기준 사이클 1회 약 $0.08–0.15, 일일 약 $2–5, 월 약 $60–150 수준입니다. 잔액은 화면의 💵 비용 배지를 눌러 확인하세요(11장)."));
children.push(P("아래 2.1~2.4는 위 키를 처음 발급받는 분을 위한 단계별 안내입니다. 발급한 키는 3장(회원가입) 화면에 그대로 붙여넣으면 됩니다. (각 사이트의 메뉴 이름은 바뀔 수 있으니, 굵게 표시한 동작 흐름과 주소를 기준으로 따라오세요.)"));

// 2.1 OpenRouter
children.push(H2("2.1 OpenRouter API 키 발급 (필수)"));
children.push(P("OpenRouter는 ArQuant의 모든 AI 분석을 호출하는 통로입니다. 키가 없으면 시스템이 전혀 동작하지 않습니다."));
children.push(P("1) 웹브라우저에서 아래 주소에 접속합니다.", { run: { bold: true } }));
children.push(P(null, { children: [new TextRun("    → "), link("https://openrouter.ai", "https://openrouter.ai")] }));
children.push(P("2) 우측 상단 ‘Sign in’을 눌러 Google·GitHub 또는 이메일로 가입/로그인합니다."));
children.push(P("3) 로그인 후 키 발급 페이지로 이동합니다."));
children.push(P(null, { children: [new TextRun("    → "), link("https://openrouter.ai/settings/keys", "https://openrouter.ai/settings/keys")] }));
children.push(P("4) ‘Create Key’(키 만들기)를 누르고 이름을 자유롭게(예: arquant) 입력한 뒤 생성합니다."));
children.push(P("5) 화면에 표시된 키(sk-or-v1- 로 시작)를 즉시 복사합니다. 이 키는 다시 볼 수 없으니 메모장 등 안전한 곳에 보관하세요."));
children.push(P("6) 크레딧(사용 금액)을 충전합니다. 충전이 없으면 분석이 멈춥니다."));
children.push(P(null, { children: [new TextRun("    → "), link("https://openrouter.ai/settings/credits", "https://openrouter.ai/settings/credits"), new TextRun(" 에서 ‘Add Credits’ → 신용/체크카드로 충전 (처음엔 $10 이상 권장)")] }));
children.push(note("확인", "올바른 키는 sk-or-v1- 로 시작합니다. 크레딧이 0이 되면 AI 분석이 중단되며, 화면의 💵 배지를 누르면 잔액 확인·충전 페이지로 연결됩니다(11장)."));

// 2.2 KIS
children.push(H2("2.2 한국투자증권(KIS) App Key·Secret 발급 (실거래 필수)"));
children.push(P("실제(또는 모의) 주문·시세 조회에 사용합니다. 먼저 한국투자증권 계좌와 로그인 ID가 있어야 합니다. 계좌가 없다면 ‘한국투자증권’ 모바일 앱에서 비대면으로 개설할 수 있습니다."));
children.push(P("1) 한국투자증권 KIS Developers(개발자센터) 포털에 접속해 한국투자증권 ID로 로그인합니다.", { run: { bold: true } }));
children.push(P(null, { children: [new TextRun("    → "), link("https://apiportal.koreainvestment.com", "https://apiportal.koreainvestment.com")] }));
children.push(P("2) ‘API 신청/이용신청’ 메뉴에서 약관에 동의하고 OpenAPI 사용을 신청합니다."));
children.push(P("3) ‘모의투자’ 또는 ‘실전투자’ 중 사용할 유형을 선택해 신청합니다. 처음에는 가짜 돈으로 안전하게 검증할 수 있는 ‘모의투자’를 권장합니다."));
children.push(P("4) 신청이 완료되면 ‘My Page(앱 관리/이용현황)’에서 App Key와 App Secret을 확인하고 둘 다 복사합니다."));
children.push(P("5) 거래할 한국투자증권 계좌번호를 준비합니다. 보통 ‘앞 8자리 + 상품 2자리 = 10자리’ 형태입니다(예: 5012345601). 명세서에 하이픈(-)이 있어도 회원가입 입력 시에는 숫자만 그대로 넣으면 됩니다."));
children.push(P("6) 사용 유형에 맞는 Base URL을 함께 입력해야 합니다."));
children.push(table(
  ["사용 유형", "Base URL", "비고"],
  [
    ["모의투자(권장 시작)", "https://openapivts.koreainvestment.com:29443", "가짜 돈 — 안전 검증용"],
    ["실전투자", "https://openapi.koreainvestment.com:9443", "실제 체결 — 손실 가능"],
  ],
  [2200, 5160, 2000]));
children.push(note("매우 중요", "모의투자 키와 실전투자 키는 서로 다릅니다. 키 종류와 Base URL을 반드시 같은 유형으로 맞추세요. 모의 키에 실전 URL을 넣으면 로그인은 되는데 주문이 계속 실패합니다. App Secret은 비밀번호와 같으니 절대 타인과 공유하지 마세요."));

// 2.3 OpenDART
children.push(H2("2.3 OpenDART API 키 발급 (선택 — 공시·재무 재심)"));
children.push(P("금융감독원 전자공시(DART)의 공시·재무제표를 읽어 매수 종목을 2차로 재심하는 데 사용합니다. 없어도 작동하지만, 공시·재무 기반 안전 점검이 생략됩니다. 무료이므로 발급을 권장합니다."));
children.push(P("1) 아래 OpenDART 사이트에 접속합니다.", { run: { bold: true } }));
children.push(P(null, { children: [new TextRun("    → "), link("https://opendart.fss.or.kr", "https://opendart.fss.or.kr")] }));
children.push(P("2) ‘인증키 신청/관리’ → ‘인증키 신청’으로 이동합니다."));
children.push(P("3) 이름·이메일 등 기본 정보를 입력하고 신청합니다."));
children.push(P("4) 입력한 이메일로 발급되는 인증키(영문·숫자 40자리)를 확인해 복사합니다(대개 즉시~수 분 내 수신)."));
children.push(P("5) 이후 ‘오픈API 이용현황’에서 키를 다시 확인할 수 있으며, 일일 호출 한도(2만 건)까지 무료입니다."));
children.push(note("참고", "DART 키가 없으면 회원가입 시 비워두면 됩니다. 시스템은 정상 작동하되 공시·재무 재심 단계만 건너뜁니다(추후 추가 입력 가능)."));

// 2.4 입력
children.push(H2("2.4 발급한 키를 ArQuant에 입력하기"));
children.push(P("위에서 받은 값을 3장(회원가입) 화면의 해당 칸에 붙여넣습니다."));
[
  "OpenRouter API Key — 필수. 없으면 가입은 되어도 분석이 동작하지 않습니다.",
  "KIS App Key / App Secret / 계좌번호 / Base URL — 실거래(또는 모의) 시 필수. 4개를 한 세트로 같은 유형(모의/실전)으로 맞춥니다.",
  "DART API Key — 선택. 비워도 됩니다.",
].forEach((t) => children.push(bullet(t)));
children.push(note("안전한 시작", "처음에는 ‘모의투자 키 + 모의 Base URL’ 또는 ‘실전이라도 방어형/보수형 프리셋 + 아주 작은 예산 비율’로 시작해 며칠간 동작을 충분히 관찰한 뒤 점차 조정하세요. 입력한 비밀키는 서버에 암호화 저장되며 단말로 내려오지 않습니다."));

// 3. 회원가입 / 로그인
children.push(H1("3. 회원가입 및 로그인"));
children.push(H2("3.1 최초 등록"));
children.push(num("접속 주소(https://arquant.ai-ve.uk) 또는 모바일 앱을 엽니다."));
children.push(num("로그인 화면에서 ‘등록(회원가입)’으로 전환합니다."));
children.push(num("아이디(3자 이상, 중복 확인) · 비밀번호(10자 이상 + 특수문자 1개 이상)를 입력합니다."));
children.push(num("OpenRouter API Key(필수)와 KIS App Key/Secret·계좌번호·Base URL, 선택적으로 DART Key를 입력합니다."));
children.push(num("등록을 완료하면 즉시 로그인되어 대시보드로 이동합니다."));
children.push(H2("3.2 로그인"));
children.push(P("이후로는 아이디와 비밀번호만으로 로그인합니다. 세션은 7일간 유지되며, 서버를 재시작하거나 코드를 갱신해도 계정과 로그인 상태는 보존됩니다."));
children.push(H2("3.3 멀티 계정"));
children.push(P("여러 계정을 등록할 수 있습니다. 단, 매매 봇은 단일 프로세스이므로 ‘활성 계정’ 1개만 봇을 점유합니다. 매매 루프가 도는 중 다른 계정으로 전환하면 안전을 위해 루프를 먼저 정지한 뒤 전환합니다."));
children.push(note("보안", "API 비밀키는 서버에 Fernet 대칭 암호화로 저장되며 단말로 내려오지 않습니다. 비밀번호 분실 시 복구가 어려우니 안전하게 보관하세요."));

// 4. 화면 한눈에 보기
children.push(H1("4. 화면 한눈에 보기"));
children.push(P("상단바에는 좌측에 로고, 우측에 💵 API 비용 배지와 🚪 로그아웃 버튼, 그리고 실행 상태 배지가 있습니다. 본문은 4개의 탭으로 구성됩니다."));
children.push(table(
  ["탭", "역할"],
  [
    ["📊 대시보드", "세션·시간·뉴스·다음 사이클·완료 사이클·실매매·전략·장 상태 + AI 통신 로그"],
    ["💰 수익률", "평가금액 추이 차트 · 보유 종목/잔고 · 전체 거래 내역"],
    ["📰 뉴스", "수집된 증권 속보 (한국/미국/공통 필터)"],
    ["⚙️ 전략", "전략 프리셋 선택·적용 + 커스터마이즈"],
  ],
  [2000, 7360]));

// 5. 기본 사용법
children.push(H1("5. 기본 사용법 — 실행과 중지"));
children.push(num("대시보드에서 ▶ 실행 을 누르면 무한 시장 감시 루프가 시작됩니다."));
children.push(num("이후 1시간 주기 + 한국/미국 장 개장 순간에 분석 사이클이 자동 수행됩니다."));
children.push(num("멈추려면 ⏹ 중지 를 누릅니다."));
children.push(P("재접속하면 실행 버튼 상태가 서버와 자동 동기화됩니다. 서버 재시작 후에는 감시 루프가 멈춰 있을 수 있으니 필요 시 다시 ▶ 실행 을 눌러주세요."));
children.push(H3("거래 트리거 (3가지)"));
[
  "▶ 실행 직후 첫 회 — 장중이면 누적 뉴스로 즉시 1회",
  "장 개장 순간 — 한국 08:50 / 미국 22:30(KST) 진입 시 1회",
  "정기 — 장이 열려 있는 동안 1시간마다",
].forEach((t) => children.push(bullet(t)));
children.push(H3("거래 세션 (KST 기준)"));
children.push(table(
  ["세션", "시간", "동작"],
  [
    ["KR_PRE_MARKET", "08:50–09:00", "매크로 수집·전략 수립·개장 트리거"],
    ["KR_TRADING", "09:00–15:30", "국내 장중 거래·정기 사이클"],
    ["KR_CLOSE_REVIEW", "15:35–15:50", "장 마감 리뷰"],
    ["US_TRADING", "22:30–05:00", "미국 야간 거래·개장 트리거"],
    ["OFF_HOURS", "그 외", "뉴스만 수집, 거래 없음"],
  ],
  [2500, 2000, 4860]));

// 6. 대시보드 탭
children.push(H1("6. 대시보드 탭"));
children.push(P("시스템의 현재 상태를 한눈에 보여줍니다: 현재 세션·KST 시간·감지된 뉴스 수·다음 사이클까지 남은 시간·완료 사이클 수·실매매 체결 수·활성 전략·장 상태(🟢 장중 / ⚪ 장외)."));
children.push(P("하단의 ‘에이전트 통신 로그’에는 각 AI(운용전략실장·계량분석팀장·뉴스분석팀장·트레이딩팀장·리스크관리실장·사후관리실장 등)의 분석과 결정이 한국어로 실시간 표시됩니다. 마크다운 잔여물은 자동 정리됩니다."));

// 7. 수익률 탭
children.push(H1("7. 수익률 탭"));
children.push(H2("7.1 평가금액 추이 차트"));
children.push(P("계좌 평가금액의 변화를 선형 차트로 보여줍니다. 입출금은 수익률에 반영되지 않도록 보정되어, 순수 운용 성과만 표시됩니다. 차트 상단의 버튼으로 보기를 전환할 수 있습니다."));
children.push(table(
  ["보기", "정의"],
  [
    ["실시간", "거래 시간대(한국 09:00–15:30 / 미국 22:30–05:00) 포인트를 10분 단위로 다운샘플"],
    ["일별", "날짜별 마지막 평가금액"],
    ["월별", "월별 마지막 평가금액"],
  ],
  [1600, 7760]));
children.push(note("참고", "차트 위쪽에 표시되던 한 줄 요약 문구(예: “[일별] 2p · 날짜 → 날짜 · 금액 → 금액 (±%)”)는 사용자 피드백(2026-05-18)에 따라 제거되었습니다. 추세는 차트 곡선과 좌측 금액 눈금으로 직접 확인하세요."));
children.push(H2("7.2 보유 종목 / 잔고"));
children.push(P("예수금·총평가·평가손익과 보유 종목 목록을 카테고리 배지(국내주식/해외주식/국내채권)와 함께 보여줍니다. 자동 폴링은 10분 주기이며 장외(OFF_HOURS)에는 일시정지됩니다. 🔄 새로고침 버튼으로 즉시 강제 갱신할 수 있습니다."));
children.push(H2("7.3 전체 거래 내역"));
children.push(P("실제로 체결된 매수/매도 내역이 KST 시각과 함께 나열됩니다. 한 행을 누르면 상세 정보가 그 자리에서 펼쳐집니다."));
[
  "매수: 정확한 체결가(주문 전후 보유 평균단가 차이로 역산) 또는 추정 체결가",
  "매도: 주문 직전 호가 스냅샷 기반 체결가 + 매도 시점 평가손익",
  "FIFO 매칭표: 어떤 매수분이 어떤 매도와 짝지어졌는지 + 로트별 실현 손익",
  "💰 실현 손익 합계 (🟢 이익 / 🔴 손실 색상)",
].forEach((t) => children.push(bullet(t)));
children.push(P("🗑️ 비우기 버튼은 거래 내역 표시만 초기화하며 시스템 로그에는 영향을 주지 않습니다."));

// 8. 뉴스 탭
children.push(H1("8. 뉴스 탭"));
children.push(P("네이버 금융 증권 속보를 10분마다 수집해, 한국(🇰🇷)/미국(🇺🇸)/공통(🌐)으로 자동 분류하여 보여줍니다. 상단 필터 버튼으로 시장별로 걸러 볼 수 있고, 각 헤드라인에는 마켓 배지와 크롤 시각이 표시됩니다. 누적 헤드라인이 많으면 결정론적 큐레이터가 굵직한 40건만 선별해 AI에 전달합니다."));

// 9. 전략 탭
children.push(H1("9. 전략 탭"));
children.push(P("위험 성향에 맞춰 5단계 프리셋(방어형/보수형/균형형/공격형/초공격형) 중 하나를 고르고 ‘적용’하면 즉시 라이브 반영됩니다. 기본값은 균형형입니다."));
children.push(table(
  ["항목", "방어형", "보수형", "균형형(기본)", "공격형", "초공격형"],
  [
    ["1주문 예수금 비율", "3%", "5%", "10%", "20%", "35%"],
    ["사이클 누적 예산", "10%", "15%", "25%", "40%", "70%"],
    ["신규매수 차단 손익", "-2.5%", "-4%", "-5%", "-8%", "-15%"],
    ["단일 종목 비중 한도", "7%", "10%", "15%", "25%", "40%"],
    ["사이클당 최대 매수", "1", "1", "2", "3", "5"],
    ["자동 익절", "6%", "8%", "12%", "18%", "30%"],
    ["자동 손절", "3.5%", "5%", "7%", "10%", "15%"],
    ["데이트레이딩 허용", "OFF", "OFF", "ON", "ON", "ON"],
    ["해외주식 매수", "OFF", "OFF", "ON", "ON", "ON"],
  ],
  [2360, 1400, 1400, 1600, 1300, 1300]));
children.push(H2("9.1 전략 커스터마이즈"));
children.push(P("‘🛠 전략 커스터마이즈’ 박스를 펼치면 각 파라미터를 한국어 라벨과 단위(%, 배, 일, 건)로 직접 입력할 수 있습니다. ‘즉시 적용’으로 라이브 오버라이드하거나, ‘프리셋으로 저장’하면 내 프리셋으로 영구 보관되어 사이드바/목록에 노출됩니다(삭제 가능)."));

// 10. @멘션
children.push(H1("10. AI에게 직접 지시하기 (@멘션)"));
children.push(P("하단 입력창에 `@에이전트명 지시내용` 형식으로 입력하면 해당 AI에게 즉시 지시할 수 있습니다. `@`를 생략하면 운용전략실장에게 자동 전달됩니다."));
[
  "@운용전략실장 미국 기술주 비중 60%로 세팅",
  "@계량분석팀장 005930, 000660 기술적 분석 좀",
  "@뉴스분석팀장 최근 반도체 업종 뉴스 요약",
  "@사후관리실장 보유 종목 점검",
  "@운용지원실장 뉴스 크롤링 주기를 5분으로 바꿔줘",
].forEach((t) => children.push(bullet(t)));
children.push(note("중요", "설정·코드를 실제로 바꾸고 서버를 재시작하는 권한은 ‘운용지원실장’에게만 있습니다. 다른 에이전트에게 설정 변경을 지시하면 시스템이 ‘그 변경은 @운용지원실장에게 지시하세요’라고 안내합니다."));

// 11. API 비용
children.push(H1("11. API 비용 확인"));
children.push(P("상단바의 💵 배지는 최근 1시간 동안의 추정 API 비용과 호출 수를 보여줍니다(예: 💵 $0.074/h (14콜)). OpenRouter 사용량 기준으로 모델별 단가를 곱해 누적 계산한 값입니다."));
children.push(new Paragraph({ spacing: { after: 120, line: 312 }, children: [
  new TextRun("이 배지를 클릭하면 "),
  link("OpenRouter 크레딧 페이지(openrouter.ai/settings/credits)", "https://openrouter.ai/settings/credits"),
  new TextRun("가 새 탭(웹) 또는 외부 브라우저(모바일 앱)로 열립니다. 여기서 잔여 크레딧을 확인하고 충전할 수 있습니다."),
]}));
children.push(note("팁", "크레딧이 소진되면 AI 분석이 중단됩니다. 비용 배지를 주기적으로 확인하고 미리 충전해 두세요."));

// 12. 모바일 앱
children.push(H1("12. 모바일 앱 사용"));
children.push(P("모바일 앱은 웹 대시보드와 동일한 서버를 사용하며, 화면 역시 서버의 웹 화면을 그대로 표시합니다. 따라서 서버에서 기능이나 화면을 수정하면 앱을 다시 설치하지 않아도 다음 실행 시 자동으로 반영됩니다."));
children.push(H2("12.1 로그인"));
children.push(P("앱을 처음 켜면 아이디/비밀번호 로그인 화면이 나옵니다. 로그인하면 그 세션이 내부 화면에 자동 적용되어 다시 로그인할 필요가 없습니다. 웹 화면에서 로그아웃하면 앱도 로그인 화면으로 돌아갑니다."));
children.push(H2("12.2 알림과 위젯 (그대로 유지)"));
[
  "매수/매도 체결·실패 시 시스템 푸시 알림이 옵니다.",
  "홈 화면 위젯에 보유 종목·총평가·수익률이 표시됩니다.",
  "외부 링크(예: 💵 배지의 OpenRouter)는 기기의 기본 브라우저로 열립니다.",
].forEach((t) => children.push(bullet(t)));
children.push(note("자동 반영", "앱 화면은 서버와 연동되어 있어 서버 수정이 즉시 반영됩니다. 단, 알림·위젯 같은 네이티브 기능 자체를 바꾼 경우에만 앱 재빌드·재설치가 필요합니다."));

// 13. 계정·보안
children.push(H1("13. 계정·보안"));
[
  "세션 토큰은 7일 유효하며 서버 재시작 후에도 계정·세션이 유지됩니다.",
  "세션 쿠키는 HttpOnly + Secure + SameSite=Lax로 보호됩니다.",
  "API 비밀키는 서버에 암호화 저장되며 단말로 내려오지 않습니다.",
  "공용 기기에서는 사용 후 반드시 🚪 로그아웃 하세요.",
].forEach((t) => children.push(bullet(t)));

// 14. FAQ
children.push(H1("14. 자주 묻는 질문 (FAQ)"));
const faq = [
  ["실행을 눌렀는데 거래가 안 일어나요.", "장이 열려 있어야 거래합니다(세션표 5장 참고). 장외에는 뉴스만 수집합니다. 또한 전략의 위험 한도(예: 평가손익 마이너스)로 신규 매수가 차단됐을 수 있습니다."],
  ["수익률 차트 위 요약 문구가 사라졌어요.", "정상입니다. 2026-05-18 피드백으로 한 줄 요약은 제거되었습니다. 추세는 차트와 금액 눈금으로 확인하세요."],
  ["💵 배지를 눌렀더니 페이지가 열려요.", "정상입니다. OpenRouter 크레딧 페이지로 연결되어 잔액 확인·충전을 할 수 있습니다."],
  ["서버를 고쳤는데 모바일에 반영이 안 돼요.", "앱은 서버 화면을 그대로 띄우므로 보통 앱을 다시 열면 반영됩니다. 그래도 안 보이면 앱을 완전히 종료 후 재실행하세요(캐시 갱신)."],
  ["AI 분석이 갑자기 멈췄어요.", "OpenRouter 크레딧 소진 가능성이 큽니다. 💵 배지를 눌러 잔액을 확인·충전하세요."],
  ["계정·로그인이 서버 재시작 후에도 유지되나요?", "네. 계정·세션(7일)은 서버 디스크에 영속되어 재시작·코드 갱신 후에도 유지됩니다."],
];
faq.forEach(([q, a]) => {
  children.push(new Paragraph({ spacing: { before: 100, after: 40, line: 300 },
    children: [new TextRun({ text: "Q. " + q, bold: true })] }));
  children.push(new Paragraph({ spacing: { after: 120, line: 300 },
    children: [new TextRun("A. " + a)] }));
});

// 15. 문제 해결
children.push(H1("15. 문제 해결"));
children.push(table(
  ["증상", "조치"],
  [
    ["로그인이 안 됨", "아이디/비밀번호 확인. 비밀번호는 10자 이상 + 특수문자 1개 이상."],
    ["잔고가 0으로 보임", "🔄 새로고침 클릭. KIS 키/계좌번호·Base URL이 정확한지 확인."],
    ["실행 버튼이 회색", "재접속 후 잠시 대기하면 서버 상태와 동기화됩니다. 안 되면 새로고침."],
    ["거래 내역이 비어 있음", "아직 체결이 없거나 🗑️ 비우기로 초기화된 상태입니다."],
    ["모바일에서 화면이 안 뜸", "네트워크 확인 후 앱 재시작. 로그인 화면이 나오면 다시 로그인."],
  ],
  [2600, 6760]));

// 16. 운영 안전·점검 (신규 2026-05-18)
children.push(H1("16. 운영 안전·점검 (신규)"));
children.push(P("실거래 시스템이라 '잘못된 주문을 막는 것'이 최우선입니다. 다음 안전장치가 추가되었습니다."));
children.push(H2("16.1 자동 점검 (배포·설정 변경 전 권장)"));
[
  "터미널에서 python3.11 -m pytest 를 실행하면 주문 검증·사이징·파서 등 '돈이 걸린 결정론 코드'를 2초 안에 56개 케이스로 자가 점검합니다.",
  "특히 한국 주식(원)과 미국 주식(달러) 금액이 섞이지 않는지, 단일 종목 비중·예수금·사이클 예산 한도가 지켜지는지 자동 검증합니다.",
  "전략 프리셋을 비교해 보려면 python3.11 -m backtest.report — 방어형~초공격형의 위험(최대낙폭)·회전율을 표로 보여줍니다(과거 데이터 기반 상대 비교).",
].forEach((t) => children.push(bullet(t)));
children.push(H2("16.2 실패 알림 — 더 이상 조용히 멈추지 않습니다"));
[
  "주문 실패·체결 재확인 실패·평가금액 기록 실패·감시 루프 중단 등은 이제 '운영자 알림'으로 표면화됩니다.",
  "대시보드/모바일에 알림이 실시간으로 뜨고, GET /api/alerts 로도 최근 알림을 볼 수 있습니다.",
  "같은 오류가 반복돼도 30분에 1번만 알려 알림 폭주를 막습니다. 알림이 떴다면 해당 항목을 우선 점검하세요.",
  "사이클 소요시간·주문 성공/실패 추이는 GET /api/metrics 로 확인합니다.",
].forEach((t) => children.push(bullet(t)));
children.push(H2("16.3 자가수정 안전장치"));
[
  "운용지원실장이 코드를 고치다 문법 오류가 나면, 그 변경 전체를 자동으로 원래대로 되돌리고 서버를 재시작하지 않습니다(과거엔 깨진 코드가 남아 다음 재기동 때 전체 중단될 수 있었습니다).",
  "한 번에 너무 큰 코드 변경은 거부되어, 국소적인 안전한 수정만 적용됩니다. 롤백이 일어나면 CRITICAL 알림이 옵니다.",
].forEach((t) => children.push(bullet(t)));

// 17. 안전 유의사항
children.push(H1("17. 안전 유의사항 / 면책"));
[
  "ArQuant는 실제 자금으로 실거래를 수행합니다. 손실 가능성이 있으며 모든 투자 책임은 사용자에게 있습니다.",
  "처음에는 방어형/보수형 프리셋과 작은 예산 비율로 시작해 동작을 충분히 관찰하세요.",
  "AI 분석은 외부 모델·데이터에 의존하므로 오류·지연·중단이 발생할 수 있습니다.",
  "API 키와 계좌 정보는 타인과 공유하지 마세요.",
].forEach((t) => children.push(bullet(t)));
children.push(P("본 문서는 프로젝트 README.md를 바탕으로 작성된 사용자용 안내서입니다. 기술적 세부 구현은 README.md를 참고하세요."));

// ── build ─────────────────────────────────────────────────
const doc = new Document({
  styles: {
    default: { document: { run: { font: FONT, size: 22 } } },
    paragraphStyles: [
      { id: "Heading1", name: "Heading 1", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 32, bold: true, font: FONT, color: "1F3B57" },
        paragraph: { spacing: { before: 320, after: 200 }, outlineLevel: 0,
          border: { bottom: { style: BorderStyle.SINGLE, size: 6, color: "1F3B57", space: 4 } } } },
      { id: "Heading2", name: "Heading 2", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 26, bold: true, font: FONT, color: "2E5276" },
        paragraph: { spacing: { before: 220, after: 140 }, outlineLevel: 1 } },
      { id: "Heading3", name: "Heading 3", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 23, bold: true, font: FONT, color: "44607A" },
        paragraph: { spacing: { before: 160, after: 100 }, outlineLevel: 2 } },
    ],
  },
  numbering: { config: [
    { reference: "bullets", levels: [
      { level: 0, format: LevelFormat.BULLET, text: "•", alignment: AlignmentType.LEFT,
        style: { paragraph: { indent: { left: 620, hanging: 320 } } } },
      { level: 1, format: LevelFormat.BULLET, text: "◦", alignment: AlignmentType.LEFT,
        style: { paragraph: { indent: { left: 1040, hanging: 320 } } } } ] },
    { reference: "steps", levels: [
      { level: 0, format: LevelFormat.DECIMAL, text: "%1.", alignment: AlignmentType.LEFT,
        style: { paragraph: { indent: { left: 620, hanging: 320 } } } } ] },
  ] },
  sections: [{
    properties: { page: {
      size: { width: 12240, height: 15840 },
      margin: { top: 1440, right: 1440, bottom: 1440, left: 1440 } } },
    headers: { default: new Header({ children: [new Paragraph({
      spacing: { after: 0 }, border: { bottom: { style: BorderStyle.SINGLE, size: 4, color: "C9D3DF", space: 6 } },
      children: [new TextRun({ text: "ArQuant v1.0 사용설명서", size: 16, color: "8A99AB" })] })] }) },
    footers: { default: new Footer({ children: [new Paragraph({
      alignment: AlignmentType.CENTER, spacing: { before: 60 },
      children: [new TextRun({ text: "— ", size: 16, color: "8A99AB" }),
        new TextRun({ children: [PageNumber.CURRENT], size: 16, color: "8A99AB" }),
        new TextRun({ text: " —", size: 16, color: "8A99AB" })] })] }) },
    children,
  }],
});

Packer.toBuffer(doc).then((buf) => {
  fs.writeFileSync("사용설명서.docx", buf);
  console.log("WROTE 사용설명서.docx  (" + buf.length + " bytes)");
});
