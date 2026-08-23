'use strict';

let priceChart = null;

function escapeHtml(str) {
  return String(str ?? '').replace(/[&<>"']/g, c => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[c]));
}

// 서버가 주는 UTC ISO 시각을 사용자 브라우저의 로컬 시간대로 "YYYY-MM-DD HH:MM"
// 형태로 바꾼다. 데이터가 언제 기준인지 화면 곳곳에서 같은 형식으로 보여주기 위한 공용 헬퍼.
function formatAsOf(iso) {
  if (!iso) return null;
  const d = new Date(iso);
  if (isNaN(d.getTime())) return null;
  const pad = n => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

// ─── 테마(라이트/다크/시스템) ─────────────────────────────────────────────────

const THEME_ICONS = { system: 'ti-device-desktop', light: 'ti-sun', dark: 'ti-moon' };
const THEME_LABELS = { system: 'System Default', light: 'Light Mode', dark: 'Dark Mode' };

function applyTheme(theme) {
  if (theme === 'light' || theme === 'dark') {
    document.documentElement.setAttribute('data-theme', theme);
  } else {
    document.documentElement.removeAttribute('data-theme');
  }
  const icon = document.getElementById('theme-icon');
  const btn = document.getElementById('theme-toggle-btn');
  if (icon) icon.className = 'ti ' + THEME_ICONS[theme];
  if (btn) btn.title = `Theme: ${THEME_LABELS[theme]} (click to change)`;
}

function cycleTheme() {
  const order = ['system', 'light', 'dark'];
  const current = localStorage.getItem('theme') || 'system';
  const next = order[(order.indexOf(current) + 1) % order.length];
  localStorage.setItem('theme', next);
  applyTheme(next);
  showToast(`Theme: ${THEME_LABELS[next]}`);
}

document.addEventListener('DOMContentLoaded', () => applyTheme(localStorage.getItem('theme') || 'dark'));

// ─── 언어(한국어/영어) ────────────────────────────────────────────────────────
// 핵심 UI(탭·버튼·라벨·테이블 헤더 등)만 대상으로 한다 - 안내문/에러 메시지처럼
// 긴 설명형 텍스트는 RULES.md R12에 따라 계속 한국어 고정이다. 테마와 달리
// 언어는 페이지 전체를 다시 그려야 해서(수십 개 렌더 함수가 t()를 호출) CSS
// 속성 하나로 못 바꾸고, 전환 시 새로고침한다 - localStorage에 저장해둔 값을
// 다음 로드 때 다시 읽어 그 언어로 처음부터 렌더링하는 방식.
let currentLang = localStorage.getItem('lang') || 'ko';

function t(key) {
  const entry = I18N[key];
  if (!entry) return key;
  return entry[currentLang] ?? entry.ko ?? key;
}

function applyLang(lang) {
  currentLang = lang;
  const btn = document.getElementById('lang-toggle-btn');
  if (btn) btn.textContent = lang === 'ko' ? 'EN' : '한';
  if (btn) btn.title = lang === 'ko' ? 'Switch to English' : '한국어로 전환';
}

function cycleLang() {
  const next = currentLang === 'ko' ? 'en' : 'ko';
  localStorage.setItem('lang', next);
  location.reload();
}

// Jinja가 서버에서 한 번만 렌더링하는 정적 HTML(탭/버튼/라벨 등)은 app.js의
// 동적 렌더링과 달리 매번 다시 그려지지 않으므로, data-i18n 계열 속성을 붙여두고
// 페이지 로드 시 한 번 훑어서 텍스트/placeholder/title을 교체한다.
function applyI18nToDom() {
  document.querySelectorAll('[data-i18n]').forEach(el => {
    el.textContent = t(el.dataset.i18n);
  });
  document.querySelectorAll('[data-i18n-placeholder]').forEach(el => {
    el.placeholder = t(el.dataset.i18nPlaceholder);
  });
  document.querySelectorAll('[data-i18n-title]').forEach(el => {
    const text = t(el.dataset.i18nTitle);
    el.title = text;
    if (el.hasAttribute('aria-label')) el.setAttribute('aria-label', text);
  });
}

document.addEventListener('DOMContentLoaded', () => {
  applyLang(currentLang);
  applyI18nToDom();
});

// 서버가 돌려주는 값(매크로 종목명/그룹명, 애널리스트 등급 등) → 한국어.
// 키 기반 I18N과 달리 여기 키는 "실제 화면에 뜨는 영어 값" 그 자체다.
const BACKEND_TEXT_KO = {
  // 매크로 그룹
  'Indices': '지수', 'Volatility': '변동성', 'Rates': '금리', 'FX': '환율', 'Commodities': '원자재',
  // 매크로 종목명
  'Nasdaq 100': '나스닥 100', 'Dow Jones': '다우존스', 'Russell 2000': '러셀 2000',
  'US 2Y Treasury Yield': '美 2년물 국채금리', 'US 5Y Treasury Yield': '美 5년물 국채금리',
  'US 10Y Treasury Yield': '美 10년물 국채금리', 'US 30Y Treasury Yield': '美 30년물 국채금리',
  'Dollar Index': '달러 인덱스', 'Gold Futures': '금 선물', 'WTI Crude Futures': 'WTI 원유 선물',
  // 애널리스트 등급/추천
  'Buy': '매수', 'Hold': '보유', 'Sell': '매도', 'Strong Buy': '적극매수',
  // 국내 스윙 전략명
  'Volatility Breakout': '변동성 돌파', 'Range Breakout': '박스권 돌파', 'MA Pullback': '이동평균 눌림목',
  'Combo (Trend + Pullback + Momentum)': '복합전략(추세+눌림목+모멘텀)',
  // 실전현황 주문 라벨(infinite_buying.py)
  'Day 1 Buy': '1일차 매수', 'Stop-Loss Forced Sell (1/4)': '손절모드 강제매도(1/4)',
  'Buy (Avg Price)': '매수(평단가)', 'Buy (Threshold)': '매수(임계값)',
  'Sell (Quarter 1/4)': '매도(쿼터 1/4)', 'Sell (Target 3/4)': '매도(목표 3/4)',
  // 스크리너 재무 필터 - 카테고리명
  'Balance Sheet': '재무상태표', 'Income Statement': '손익계산서', 'Profitability': '수익성',
  'Growth (YoY)': '성장성(전년비)', 'Stability': '안정성', 'Valuation': '밸류에이션', 'Consensus': '컨센서스',
  // 스크리너 재무 필터 - 항목명
  'Total Assets': '총자산', 'Total Liabilities': '총부채', 'Total Equity': '총자본',
  'Equity Attributable to Owners': '지배주주지분', 'Issued Capital': '자본금',
  'Revenue': '매출액', 'Gross Profit': '매출총이익', 'Operating Income': '영업이익',
  'Pre-Tax Income': '세전이익', 'Net Income': '당기순이익', 'Net Income Attributable to Owners': '지배주주순이익',
  'Gross Margin': '매출총이익률', 'Operating Margin': '영업이익률', 'Net Margin': '순이익률',
  'Revenue Growth': '매출성장률', 'Operating Income Growth': '영업이익성장률', 'EPS Growth': 'EPS 성장률',
  'Current Ratio': '유동비율', 'Quick Ratio': '당좌비율', 'Debt Ratio': '부채비율', 'Net Debt Ratio': '순부채비율',
  'Dividend Yield': '배당수익률',
  // 스크리너 재무 필터 - 이상적 수치 티어 라벨
  'Weak': '약함', 'Fair': '보통', 'Good': '양호', 'Excellent': '우수',
  'Declining': '감소', 'Slow': '저성장', 'Strong': '고성장', 'Explosive': '폭발적', 'None': '없음',
  // 스크리너 재무 필터 - 기타
  'Analyst Rating': '애널리스트 의견', 'Financial Filters': '재무 필터', 'Reset': '초기화', 'Rating': '등급',
  'Strategy Presets': '전략 프리셋',
  "Some metrics (stability, consensus, EV/EBITDA, etc.) aren't available for KR stocks.":
    '일부 지표(안정성, 컨센서스, EV/EBITDA 등)는 국내 종목에서 제공되지 않습니다.',
  // 스크리너 재무 필터 - 미너비니 스테이지
  'Stage (Minervini)': '단계(미너비니)', 'Trend Stage': '추세 단계',
  "Stage is an approximate classification based on moving-average alignment and position within the 52-week range (it doesn't factor in breakout volume or trend duration).":
    '단계는 이동평균 정배열/역배열과 52주 레인지 내 위치를 이용한 근사 분류입니다(돌파 거래량이나 추세 지속 기간은 반영하지 않습니다).',
  // 스크리닝 - 미국 종목 업종(industry, GICS 세부업종). us_stocks.json에 이미
  // 한국어로 들어있는 값도 있고 영어로 남아있는 값도 섞여 있어, 값 그대로
  // tv()에 통과시켜 여기 등록된 영어 값만 골라 번역한다.
  'Advertising': '광고', 'Aerospace & Defense': '항공우주 및 국방',
  'Agricultural & Farm Machinery': '농업 및 농기계', 'Agricultural Products & Services': '농산물 및 농업 서비스',
  'Air Freight & Logistics': '항공화물 및 물류', 'Apparel Retail': '의류 소매',
  'Apparel, Accessories & Luxury Goods': '의류·액세서리 및 명품', 'Application Software': '응용 소프트웨어',
  'Asset Management & Custody Banks': '자산운용 및 수탁은행', 'Automobile Manufacturers': '자동차 제조',
  'Automotive Parts & Equipment': '자동차 부품 및 장비', 'Automotive Retail': '자동차 소매',
  'Biotechnology': '생명공학', 'Brewers': '맥주 양조', 'Broadcasting': '방송', 'Broadline Retail': '종합 소매',
  'Building Products': '건축자재', 'Cable & Satellite': '케이블 및 위성방송',
  'Cargo Ground Transportation': '화물 육상운송', 'Casinos & Gaming': '카지노 및 게이밍',
  'Commodity Chemicals': '범용화학', 'Communications Equipment': '통신장비',
  'Computer & Electronics Retail': '컴퓨터·전자제품 소매', 'Construction & Engineering': '건설 및 엔지니어링',
  'Construction Machinery & Heavy Transportation Equipment': '건설기계 및 중장비',
  'Construction Materials': '건설자재', 'Consumer Electronics': '가전제품', 'Consumer Finance': '소비자금융',
  'Consumer Staples Merchandise Retail': '생활필수품 소매', 'Copper': '구리', 'Data Center REITs': '데이터센터 리츠',
  'Data Processing & Outsourced Services': '데이터 처리 및 아웃소싱 서비스',
  'Distillers & Vintners': '증류주 및 와인', 'Distributors': '유통업체', 'Diversified Banks': '종합은행',
  'Diversified Support Services': '종합 지원서비스', 'Electric Utilities': '전력 유틸리티',
  'Electrical Components & Equipment': '전기 부품 및 장비', 'Electronic Components': '전자부품',
  'Electronic Equipment & Instruments': '전자장비 및 계측기',
  'Electronic Manufacturing Services': '전자제품 위탁생산(EMS)',
  'Environmental & Facilities Services': '환경 및 시설 서비스',
  'Fertilizers & Agricultural Chemicals': '비료 및 농업화학', 'Financial Exchanges & Data': '금융거래소 및 데이터',
  'Food Distributors': '식품 유통', 'Food Retail': '식품 소매', 'Footwear': '신발',
  'Gas Utilities': '가스 유틸리티', 'Gold': '금(金)', 'Health Care Distributors': '헬스케어 유통',
  'Health Care Equipment': '헬스케어 장비', 'Health Care Facilities': '헬스케어 시설',
  'Health Care REITs': '헬스케어 리츠', 'Health Care Services': '헬스케어 서비스',
  'Health Care Supplies': '헬스케어 소모품', 'Health Care Technology': '헬스케어 기술',
  'Heavy Electrical Equipment': '중전기기', 'Home Improvement Retail': '홈 인테리어 소매',
  'Homebuilding': '주택건설', 'Homefurnishing Retail': '가구·인테리어 소매',
  'Hotel & Resort REITs': '호텔·리조트 리츠', 'Hotels, Resorts & Cruise Lines': '호텔·리조트·크루즈',
  'Household Products': '생활용품', 'Human Resource & Employment Services': '인적자원 및 고용 서비스',
  'IT Consulting & Other Services': 'IT 컨설팅 및 기타 서비스',
  'Independent Power Producers & Energy Traders': '독립발전 및 에너지 트레이딩',
  'Industrial Conglomerates': '산업 복합기업', 'Industrial Gases': '산업용 가스',
  'Industrial Machinery & Supplies & Components': '산업용 기계·자재·부품', 'Industrial REITs': '산업용 리츠',
  'Insurance Brokers': '보험중개', 'Integrated Oil & Gas': '종합 석유·가스',
  'Integrated Telecommunication Services': '종합 통신서비스',
  'Interactive Home Entertainment': '인터랙티브 홈 엔터테인먼트(게임)',
  'Interactive Media & Services': '인터랙티브 미디어 및 서비스',
  'Internet Services & Infrastructure': '인터넷 서비스 및 인프라',
  'Investment Banking & Brokerage': '투자은행 및 증권중개', 'Leisure Products': '레저용품',
  'Life & Health Insurance': '생명·건강보험', 'Life Sciences Tools & Services': '생명과학 도구 및 서비스',
  'Managed Health Care': '건강관리기구(HMO)', 'Metal, Glass & Plastic Containers': '금속·유리·플라스틱 용기',
  'Movies & Entertainment': '영화 및 엔터테인먼트', 'Multi-Family Residential REITs': '다세대주택 리츠',
  'Multi-Sector Holdings': '복합업종 지주회사', 'Multi-Utilities': '복합 유틸리티',
  'Multi-line Insurance': '종합보험', 'Office REITs': '오피스 리츠',
  'Oil & Gas Equipment & Services': '석유·가스 장비 및 서비스',
  'Oil & Gas Exploration & Production': '석유·가스 탐사 및 생산',
  'Oil & Gas Refining & Marketing': '석유·가스 정제 및 판매',
  'Oil & Gas Storage & Transportation': '석유·가스 저장 및 운송', 'Other Specialized REITs': '기타 특수 리츠',
  'Other Specialty Retail': '기타 전문소매', 'Packaged Foods & Meats': '가공식품 및 육류',
  'Paper & Plastic Packaging Products & Materials': '종이·플라스틱 포장재', 'Passenger Airlines': '여객 항공',
  'Passenger Ground Transportation': '여객 육상운송', 'Personal Care Products': '퍼스널케어 제품',
  'Pharmaceuticals': '제약', 'Property & Casualty Insurance': '손해보험', 'Publishing': '출판',
  'Rail Transportation': '철도운송', 'Real Estate Services': '부동산 서비스', 'Regional Banks': '지역은행',
  'Reinsurance': '재보험', 'Research & Consulting Services': '리서치 및 컨설팅 서비스',
  'Restaurants': '외식업', 'Retail REITs': '상업용 리츠', 'Self-Storage REITs': '셀프스토리지 리츠',
  'Semiconductor Materials & Equipment': '반도체 소재 및 장비', 'Semiconductors': '반도체',
  'Single-Family Residential REITs': '단독주택 리츠',
  'Soft Drinks & Non-alcoholic Beverages': '청량음료 및 무알콜음료',
  'Specialized Consumer Services': '특수 소비자서비스', 'Specialty Chemicals': '특수화학', 'Steel': '철강',
  'Systems Software': '시스템 소프트웨어', 'Technology Distributors': '기술제품 유통',
  'Technology Hardware, Storage & Peripherals': '기술 하드웨어·저장장치·주변기기',
  'Telecom Tower REITs': '통신타워 리츠', 'Timber REITs': '임업 리츠', 'Tobacco': '담배',
  'Trading Companies & Distributors': '상사 및 유통',
  'Transaction & Payment Processing Services': '결제 처리 서비스', 'Water Utilities': '상수도 유틸리티',
  'Wireless Telecommunication Services': '무선통신 서비스',
  // 백테스트 거래내역 비고(note) - 고정 문구(동적으로 숫자가 끼는 것들은
  // BACKEND_TEXT_PATTERNS_KO에서 정규식으로 처리한다)
  'Day 1 close buy': '1일차 종가 매수',
  'Quarter (1/4) LOC sell': '쿼터(1/4) LOC 매도',
  'Target return limit sell (3/4)': '목표수익률 지정가 매도(3/4)',
  'Stop-loss (splits exhausted) MOC sell (1/4)': '손절(분할 소진) MOC 매도(1/4)',
  'Second-half buy (threshold LOC)': '후반전 매수(임계값 LOC)',
};

// 거래내역 비고(note)는 종목당 파라미터(수량·이평선 기간·K값 등)가 그대로 문자열에
// 끼어 있어 BACKEND_TEXT_KO처럼 정확히 일치하는 키로 등록할 수 없다 - 정규식으로
// 숫자만 뽑아내 한국어 문장에 다시 끼워 넣는다(국내 스윙 kr_swing.py, 무한매수법
// backtest.py의 note 문구와 1:1로 맞춰뒀다).
const BACKEND_TEXT_PATTERNS_KO = [
  [/^Full sell \(quarter (\d+) sh \+ target (\d+) sh\), restart$/, m => `전량 매도(쿼터 ${m[1]}주 + 목표 ${m[2]}주), 재시작`],
  [/^First-half buy \(avg price (\d+) sh \+ threshold (\d+) sh\)$/, m => `전반전 매수(평단가 ${m[1]}주 + 임계값 ${m[2]}주)`],
  [/^First-half buy \(avg price (\d+) sh\)$/, m => `전반전 매수(평단가 ${m[1]}주)`],
  [/^First-half buy \(threshold (\d+) sh\)$/, m => `전반전 매수(임계값 ${m[1]}주)`],
  [/^Volatility breakout \(K=([\d.]+)\) buy$/, m => `변동성 돌파(K=${m[1]}) 매수`],
  [/^Stop-loss sell \(([-\d.]+)%\)$/, m => `손절 매도(${m[1]}%)`],
  [/^Holding period \((\d+)d\) reached, open sell$/, m => `보유기간(${m[1]}일) 도달, 시가 매도`],
  [/^(\d+)-day high breakout buy$/, m => `${m[1]}일 신고가 돌파 매수`],
  [/^(\d+)-day low breakdown \(trend end\) sell$/, m => `${m[1]}일 신저가 이탈(추세 종료) 매도`],
  [/^(\d+)-day low breakdown sell$/, m => `${m[1]}일 신저가 이탈 매도`],
  [/^MA(\d+) uptrend pullback to MA(\d+), rebound buy$/, m => `MA${m[1]} 상승추세 눌림목(MA${m[2]}), 반등 매수`],
  [/^Target return \(([-\d.]+)%\) reached, sell$/, m => `목표수익률(${m[1]}%) 도달, 매도`],
  [/^MA(\d+) breakdown sell$/, m => `MA${m[1]} 이탈 매도`],
  [/^Trend\(MA(\d+)\)\+pullback\(MA(\d+)\)\+momentum\(K=([\d.]+)\) confirmed buy$/, m => `추세(MA${m[1]})+눌림목(MA${m[2]})+모멘텀(K=${m[3]}) 확인 매수`],
];

// "{티커} Buy & Hold" 처럼 값 일부에 티커가 끼어 있는 백엔드 문자열은 정확히
// 일치하는 키를 미리 등록해둘 수 없어 접미사 치환으로 처리한다.
function tv(value) {
  if (currentLang !== 'ko' || value == null) return value;
  if (BACKEND_TEXT_KO[value] !== undefined) return BACKEND_TEXT_KO[value];
  if (value.endsWith(' Buy & Hold')) return value.slice(0, -' Buy & Hold'.length) + ' 매수후보유';
  for (const [re, fn] of BACKEND_TEXT_PATTERNS_KO) {
    const m = value.match(re);
    if (m) return fn(m);
  }
  return value;
}

const I18N = {
  // API 키 배너
  apiKeyTitle: { en: 'Finnhub API Key Required', ko: 'Finnhub API 키가 필요합니다' },
  apiKeyCopyFrom: { en: 'Get your free API key from the', ko: '무료 API 키는 아래에서 발급받으세요:' },
  apiKeyPlaceholder: { en: 'Enter your Finnhub API key', ko: 'Finnhub API 키를 입력하세요' },
  saveVerify: { en: 'Save & Verify', ko: '저장 및 확인' },
  cancel: { en: 'Cancel', ko: '취소' },
  verifying: { en: 'Verifying...', ko: '확인 중...' },
  apiKeySaved: { en: 'API key saved', ko: 'API 키가 저장되었습니다' },

  // 매크로
  loadingMarketOverview: { en: 'Loading market overview...', ko: '시황 정보를 불러오는 중...' },
  retry: { en: 'Retry', ko: '다시 시도' },
  asOf: { en: 'As of', ko: '기준' },
  refresh: { en: 'Refresh', ko: '새로고침' },
  forceRefreshAdmin: { en: 'Force Refresh (Admin)', ko: '지금 새로고침(관리자)' },
  forceRefreshConfirm: {
    en: 'This recomputes the trend screening cache right now (takes several minutes). Proceed?',
    ko: '트렌드 스크리닝 캐시를 지금 바로 재계산합니다(수 분~십수 분 소요). 진행할까요?',
  },
  forceRefreshStarted: {
    en: 'Recompute started in the background. It takes several minutes to finish — use the Refresh button afterward to see the latest results.',
    ko: '재계산을 시작했습니다. 완료까지 수 분~십수 분 걸리며, 완료되면 새로고침 버튼으로 최신 결과를 확인할 수 있습니다.',
  },
  noData: { en: 'No data', ko: '데이터 없음' },

  // 공포·탐욕 지수
  fgTitle: { en: 'Fear & Greed Index', ko: '공포·탐욕 지수' },
  fgPrevClose: { en: 'Previous Close', ko: '전일 종가' },
  fg1WeekAgo: { en: '1 Week Ago', ko: '1주일 전' },
  fg1MonthAgo: { en: '1 Month Ago', ko: '1개월 전' },
  fg1YearAgo: { en: '1 Year Ago', ko: '1년 전' },
  live: { en: 'LIVE', ko: '실시간' },
  fgExtremeFear: { en: 'Extreme Fear', ko: '극도의 공포' },
  fgFear: { en: 'Fear', ko: '공포' },
  fgNeutral: { en: 'Neutral', ko: '중립' },
  fgGreed: { en: 'Greed', ko: '탐욕' },
  fgExtremeGreed: { en: 'Extreme Greed', ko: '극도의 탐욕' },

  // 종목 검색 / 상세 카드
  loadingTickerData: { en: 'Loading {ticker} data...', ko: '{ticker} 데이터를 불러오는 중...' },
  open: { en: 'Open', ko: '시가' },
  high: { en: 'High', ko: '고가' },
  low: { en: 'Low', ko: '저가' },
  prevClose: { en: 'Prev Close', ko: '전일 종가' },
  avgTarget: { en: 'Avg Target', ko: '평균 목표가' },
  lowTarget: { en: 'Low Target', ko: '최저 목표가' },
  highTarget: { en: 'High Target', ko: '최고 목표가' },
  upside: { en: 'Upside', ko: '상승여력' },
  priceWithinTargetRange: { en: "Current price's position within the analyst target range", ko: '애널리스트 목표가 구간 내 현재가 위치' },
  updated: { en: 'Updated', ko: '업데이트' },
  noTargetPriceData: { en: 'No analyst target price data available', ko: '애널리스트 목표가 데이터가 없습니다' },
  analysts: { en: 'analysts', ko: '명의 애널리스트' },
  buy: { en: 'Buy', ko: '매수' },
  hold: { en: 'Hold', ko: '보유' },
  sell: { en: 'Sell', ko: '매도' },
  addToPortfolio: { en: 'Add to Portfolio', ko: '포트폴리오에 추가' },
  setAlert: { en: 'Set Alert', ko: '알림 설정' },
  priceVsTarget: { en: 'Price vs. Target', ko: '현재가 대비 목표가' },
  current: { en: 'Current', ko: '현재가' },
  priceVsTargetChart: { en: 'price vs. target chart', ko: '현재가 대비 목표가 차트' },

  // 포트폴리오
  loading: { en: 'Loading...', ko: '불러오는 중...' },
  updatingPrices: { en: 'Updating prices...', ko: '가격 업데이트 중...' },
  portfolioEmpty: { en: 'Your portfolio is empty', ko: '포트폴리오가 비어 있습니다' },
  portfolioEmptyHint: { en: 'Search for a stock above and add it', ko: '위에서 종목을 검색해 추가해보세요' },
  totalValue: { en: 'Total Value', ko: '총 평가금액' },
  totalCost: { en: 'Total Cost', ko: '총 매입금액' },
  totalPL: { en: 'Total P/L', ko: '총 손익' },
  stock: { en: 'Stock', ko: '종목' },
  price: { en: 'Price', ko: '현재가' },
  target: { en: 'Target', ko: '목표가' },
  qty: { en: 'Qty', ko: '수량' },
  pl: { en: 'P/L', ko: '손익' },

  // 알림
  noAlertsSet: { en: 'No alerts set', ko: '설정된 알림이 없습니다' },
  noAlertsHint: { en: 'Set a price alert from a stock search result', ko: '종목 검색 결과에서 가격 알림을 설정해보세요' },
  triggered: { en: 'Triggered', ko: '발동됨' },
  active: { en: 'Active', ko: '대기중' },
  alertWhenPrice: { en: 'Alert when price', ko: '가격이' },
  risesAbove: { en: 'rises above', ko: '이상으로 상승 시' },
  fallsBelow: { en: 'falls below', ko: '이하로 하락 시' },

  // 백테스트(무한매수법) / 국내 스윙 공용
  runningBacktestFor: { en: 'Running backtest for {ticker}...', ko: '{ticker} 백테스트 실행 중...' },
  capital: { en: 'Capital', ko: '투자원금' },
  totalBuyQty: { en: 'Total Buy Qty', ko: '총 매수수량' },
  sh: { en: 'sh', ko: '주' },
  totalSellQty: { en: 'Total Sell Qty', ko: '총 매도수량' },
  holdingQty: { en: 'Holding Qty', ko: '보유수량' },
  avgPrice: { en: 'Avg Price', ko: '평균단가' },
  buyAmount: { en: 'Buy Amount', ko: '총 매수금액' },
  sellAmount: { en: 'Sell Amount', ko: '총 매도금액' },
  unrealizedPL: { en: 'Unrealized P/L', ko: '평가손익' },
  return: { en: 'Return', ko: '수익률' },
  returnOnCapital: { en: 'Return on Capital', ko: '원금 대비 수익률' },
  targetReturn: { en: 'Target Return', ko: '목표수익률' },
  splits: { en: 'Splits', ko: '분할수' },
  strategyMDD: { en: 'Strategy MDD', ko: '전략 MDD' },
  alphaExcessReturn: { en: 'Alpha (Excess Return)', ko: '알파(초과수익률)' },
  cyclesCompleted: { en: 'cycles completed', ko: '회차 완료' },
  quarterStopLoss: { en: 'Quarter Stop-Loss', ko: '쿼터손절' },
  currentlyHolding: { en: 'Currently Holding', ko: '현재 보유중' },
  avg: { en: 'avg', ko: '평균' },
  value: { en: 'value', ko: '평가금액' },
  noPositionAtEnd: { en: 'No position at period end', ko: '기간 종료 시점 보유 없음' },
  period: { en: 'Period', ko: '기간' },
  returnComparison: { en: 'Return Comparison', ko: '수익률 비교' },
  strategy: { en: 'Strategy', ko: '전략' },
  returnComparisonChart: { en: 'return comparison chart', ko: '수익률 비교 차트' },
  priceChartBuySell: { en: 'Price Chart (Buy/Sell Points)', ko: '가격 차트(매수/매도 시점)' },
  priceChart: { en: 'price chart', ko: '가격 차트' },
  cycle: { en: 'Cycle', ko: '회차' },
  date: { en: 'Date', ko: '날짜' },
  type: { en: 'Type', ko: '구분' },
  cumQty: { en: 'Cum. Qty', ko: '누적수량' },
  note: { en: 'Note', ko: '비고' },
  noTradesInPeriod: { en: 'No trades in this period', ko: '이 기간에 거래 내역이 없습니다' },
  infiniteBuying: { en: 'Infinite Buying', ko: '무한매수법' },
  close: { en: 'Close', ko: '종가' },

  // 국내 스윙
  noMatchingStocks: { en: 'No matching stocks', ko: '일치하는 종목이 없습니다' },
  trades: { en: 'Trades', ko: '거래횟수' },
  winRate: { en: 'Win Rate', ko: '승률' },
  avgHoldDays: { en: 'Avg Hold Days', ko: '평균 보유일' },
  plPercent: { en: 'P/L %', ko: '손익률' },
  cagr: { en: 'CAGR', ko: 'CAGR(연복리수익률)' },
  calmarRatio: { en: 'CALMAR', ko: 'CALMAR' },
  profitLossRatio: { en: 'Profit/Loss Ratio', ko: '손익비' },
  scrollToZoom: { en: 'Scroll to zoom, drag to pan', ko: '스크롤로 확대/축소, 드래그로 이동' },
  resetZoom: { en: 'Reset Zoom', ko: '확대/축소 초기화' },
  stratVolBreakout: { en: 'Volatility Breakout', ko: '변동성 돌파' },
  stratRangeBreakout: { en: 'Range Breakout', ko: '박스권 돌파' },
  stratMaPullback: { en: 'MA Pullback', ko: '이동평균 눌림목' },
  stratCombo: { en: 'Combo (Trend + Pullback + Momentum)', ko: '복합전략(추세+눌림목+모멘텀)' },

  // 국내 퀀트
  priceCacheReady: { en: 'Price cache ready for', ko: '가격 캐시 준비 완료:' },
  stocksUnit: { en: 'stocks', ko: '종목' },
  minutesAgo: { en: 'm ago', ko: '분 전 업데이트' },
  priceCacheWarming: { en: 'Price cache warming up (takes a few minutes after server start) — screening will be available once ready', ko: '가격 캐시를 준비하는 중입니다(서버 시작 후 몇 분 정도 소요) — 준비가 끝나면 스크리닝을 사용할 수 있습니다' },
  fundamentalsLoaded: { en: 'Fundamentals:', ko: '재무 데이터:' },
  recordsUnit: { en: 'records', ko: '건' },
  screening: { en: 'Screening...', ko: '스크리닝 중...' },
  noStocksMatchCriteria: { en: 'No stocks match the criteria', ko: '조건에 맞는 종목이 없습니다' },
  rank: { en: 'Rank', ko: '순위' },
  name: { en: 'Name', ko: '종목명' },
  code: { en: 'Code', ko: '종목코드' },
  marketCap: { en: 'Market Cap', ko: '시가총액' },
  fiscalYear: { en: 'Fiscal Year', ko: '회계연도' },
  runningQuantBacktest: { en: 'Running annual rebalance backtest... (querying market-wide prices, may take 5–15 minutes depending on the period)', ko: '연간 리밸런싱 백테스트 실행 중... (전체 시장 가격을 조회하며 기간에 따라 5~15분 정도 소요될 수 있습니다)' },
  finalValue: { en: 'Final Value', ko: '최종 평가금액' },
  totalReturn: { en: 'Total Return', ko: '총 수익률' },
  rebalances: { en: 'Rebalances', ko: '리밸런싱 횟수' },
  equityCurve: { en: 'Equity Curve', ko: '자산 곡선' },
  scrollZoomDragPan: { en: 'Scroll to zoom, drag to pan', ko: '스크롤로 확대/축소, 드래그로 이동' },
  selectedStocksByRebalanceDate: { en: 'Selected Stocks by Rebalance Date', ko: '리밸런싱 시점별 선정 종목' },
  noStocksSelected: { en: 'No stocks selected', ko: '선정된 종목이 없습니다' },
  noTradeHistory: { en: 'No trade history', ko: '거래 내역이 없습니다' },
  quantStrategy: { en: 'Quant Strategy', ko: '퀀트 전략' },

  // 스크리닝 백테스트
  tabScreenBacktest: { en: 'Screen Backtest', ko: '스크리닝 백테스트' },
  market: { en: 'Market', ko: '시장' },
  startDate: { en: 'Start Date', ko: '시작일' },
  endDate: { en: 'End Date', ko: '종료일' },
  stopLossPct: { en: 'Stop-Loss %', ko: '손절률(%)' },
  maxPositions: { en: 'Max Positions', ko: '최대 보유 종목 수' },
  runBacktest: { en: 'Run Backtest', ko: '백테스트 실행' },
  runningScreeningBacktest: {
    en: 'Running screening backtest... (re-evaluating the universe weekly, may take several to tens of minutes)',
    ko: '스크리닝 백테스트 실행 중... (주 단위로 전체 유니버스를 재평가하며 수 분~수십 분 소요될 수 있습니다)',
  },
  stopLoss: { en: 'Stop-Loss', ko: '손절' },
  conditionExit: { en: 'Condition Exit', ko: '조건 이탈' },
  periodEnd: { en: 'Period End', ko: '기간 종료' },
  tradeLog: { en: 'Trade Log', ko: '매매 내역' },
  buyDate: { en: 'Buy Date', ko: '매수일' },
  buyPrice: { en: 'Buy Price', ko: '매수가' },
  quantity: { en: 'Qty', ko: '수량' },
  sellDate: { en: 'Sell Date', ko: '매도일' },
  sellPrice: { en: 'Sell Price', ko: '매도가' },
  returnPct: { en: 'Return', ko: '수익률' },
  exitReason: { en: 'Exit Reason', ko: '매도 사유' },
  checkDateRange: { en: 'Please check the date range', ko: '기간을 확인하세요' },
  checkCapital: { en: 'Please check the capital', ko: '시드를 확인하세요' },
  errorDuringBacktest: { en: 'An error occurred during the backtest', ko: '백테스트 중 오류가 발생했습니다' },
  backtestTakingLong: { en: 'The backtest is taking longer than expected. Please try again shortly.', ko: '백테스트가 예상보다 오래 걸리고 있습니다. 잠시 후 다시 시도해주세요.' },

  // 모의투자
  tabPaperTrading: { en: 'Paper Trading', ko: '모의투자' },
  paperTradingIntroTitle: { en: 'Start automated paper trading', ko: '자동 모의투자를 시작하세요' },
  paperTradingIntroBody: {
    en: 'Once started, the "Minervini v2" strategy (Trend Template + liquidity entry, ATR-based risk-managed exit) '
      + 'runs automatically every day with no real money — entering and exiting positions on its own and logging '
      + 'every trade with the exact reason it sold.',
    ko: '시작하면 "미너비니 v2" 전략(트렌드템플릿+유동성 진입, ATR 기반 리스크관리 청산)이 실제 돈 없이 '
      + '매일 자동으로 매수·매도를 진행하며, 팔 때마다 정확히 어떤 사유로 팔았는지까지 전부 기록합니다.',
  },
  paperTradingStartNote: {
    en: 'Runs in the background even while you\'re away — progress accumulates from the next trading day.',
    ko: '접속해 있지 않아도 서버가 백그라운드에서 계속 진행합니다 — 다음 거래일부터 결과가 쌓입니다.',
  },
  startedOn: { en: 'Started', ko: '시작일' },
  lastProcessedDate: { en: 'Last Updated', ko: '마지막 반영일' },
  currentDrawdown: { en: 'Current Drawdown', ko: '현재 낙폭' },
  cashBalance: { en: 'Cash', ko: '현금' },
  openPositions: { en: 'Open Positions', ko: '보유 포지션' },
  currentPrice: { en: 'Current Price', ko: '현재가' },
  stopPrice: { en: 'Stop Price', ko: '손절가' },
  stopState: { en: 'Stop Type', ko: '손절 단계' },
  noOpenPositions: { en: 'No open positions', ko: '보유 중인 포지션이 없습니다' },
  noTradesYet: { en: 'No trades yet', ko: '아직 거래 내역이 없습니다' },
  referenceResultTitle: { en: 'Reference result (pre-computed)', ko: '참고 결과(사전 계산됨)' },
  referenceResultNote: {
    en: 'Pre-computed locally with the same parameters — click "Run Backtest" above for a fresh calculation on the latest data (numbers may differ slightly due to newly accumulated trading days or data revisions).',
    ko: '동일 파라미터로 로컬에서 미리 계산해둔 결과입니다 — 최신 데이터로 다시 계산하려면 위 "백테스트 실행" 버튼을 눌러주세요(그 사이 쌓인 거래일·데이터 소급수정 등으로 수치가 약간 달라질 수 있습니다).',
  },
  countUnit: { en: ' trades', ko: '건' },
  annualizedReturn: { en: 'Annualized Return (CAGR)', ko: '연평균 수익률(CAGR)' },
  referenceTradesLoadError: { en: 'Failed to load trade history. Please try again shortly.', ko: '거래 내역을 불러오지 못했습니다. 잠시 후 다시 시도해주세요.' },

  // 스크리닝
  addedToWatchlist: { en: 'Added to Watchlist', ko: '관심종목에 추가됨' },
  addToWatchlist: { en: 'Add to Watchlist', ko: '관심종목에 추가' },
  preparingData: { en: 'Preparing data...', ko: '데이터 준비 중...' },
  noStocksMatchSearchFilter: { en: 'No stocks match your search/filter', ko: '검색/필터 조건에 맞는 종목이 없습니다' },
  ofTotal: { en: 'of {n} total', ko: '전체 {n}종목 중' },
  sector: { en: 'Sector', ko: '섹터' },
  conditions: { en: 'Conditions', ko: '조건' },
  volRel: { en: 'Vol (Rel)', ko: '거래량(상대)' },
  epsGrowth: { en: 'EPS Growth', ko: 'EPS 성장률' },
  divYield: { en: 'Div Yield', ko: '배당수익률' },
  analyst: { en: 'Analyst', ko: '애널리스트' },
  vs52wLow: { en: 'vs 52w Low', ko: '52주 저가 대비' },
  vs52wHigh: { en: 'vs 52w High', ko: '52주 고가 대비' },
  min: { en: 'Min', ko: '최소' },
  max: { en: 'Max', ko: '최대' },
  stage: { en: 'Stage', ko: '단계' },
  stage1Short: { en: 'Stage 1', ko: '1단계' },
  stage2Short: { en: 'Stage 2', ko: '2단계' },
  stage3Short: { en: 'Stage 3', ko: '3단계' },
  stage4Short: { en: 'Stage 4', ko: '4단계' },
  bollingerBands: { en: 'Bollinger Bands', ko: '볼린저 밴드' },
  stratCanslimTitle: { en: 'CANSLIM', ko: 'CANSLIM' },
  stratCanslimNote: {
    en: "William O'Neil's growth-stock criteria, approximated: Stage 2+ uptrend, strong EPS growth, and high relative strength.",
    ko: '윌리엄 오닐의 성장주 발굴 기준 근사: 2단계 이상 상승 추세 + 높은 EPS 성장률 + 강한 상대강도(RS).',
  },
  stratTrendTemplateTitle: { en: 'Minervini Trend Template', ko: '미너비니 트렌드 템플릿' },
  stratTrendTemplateNote: {
    en: "Stocks that satisfy all 8 Trend Template conditions — this screener's own core criteria.",
    ko: '트렌드 템플릿 8개 조건을 모두 만족하는 종목(이 화면의 기본 스크리닝 기준).',
  },
  stratValueTitle: { en: 'Deep Value', ko: '딥 밸류(저평가 가치주)' },
  stratValueNote: {
    en: 'Graham/Buffett-style value approximation: low P/E, low P/B, solid ROE.',
    ko: '그레이엄·버핏류 가치투자 근사: 저PER + 저PBR + 견조한 ROE.',
  },
  stratReboundTitle: { en: 'Rebound from Lows', ko: '저점 반등 초입' },
  stratReboundNote: {
    en: 'Stage 1 (basing) stocks just beginning to rebound — 30-60% above the 52-week low.',
    ko: '1단계(바닥 다지기)에서 막 반등을 시작한 종목(52주 저점 대비 +30~60%).',
  },
  btPresetCustom: { en: '⚙️ Custom', ko: '⚙️ 직접 설정' },
  btPresetMinerviniV2: { en: '🎯 Minervini v2', ko: '🎯 미너비니 v2' },
  btPresetMinerviniV21: { en: '🎯 Minervini v2.1', ko: '🎯 미너비니 v2.1' },
  btPresetRelaxedVcp: { en: '🎯 Relaxed VCP Strategy', ko: '🎯 완화 VCP 전략' },
  btPresetAnonymous: { en: '🐢 Anonymous', ko: '🐢 어나니머스' },
  stratMinerviniV21Title: { en: 'Minervini v2.1', ko: '미너비니 v2.1' },
  stratAnonymousTitle: { en: 'Anonymous', ko: '어나니머스' },
  exitInitialStop: { en: 'Stop-loss (2×ATR)', ko: '초기 손절(2×ATR)' },
  exitBreakevenStop: { en: 'Breakeven stop', ko: '본전 손절' },
  exitTrailingStop: { en: 'Trailing stop', ko: '트레일링 손절' },
  exitTimeStop: { en: 'Time stop', ko: '시간 손절' },
  exitPartialProfit: { en: 'Partial profit (25%)', ko: '분할 익절(25%)' },
  exitMaBreak: { en: 'MA50 breakdown', ko: 'MA50 이탈' },
  exitMaxHold: { en: 'Max hold days', ko: '최대보유 도달' },
  stratMinerviniV2Title: { en: 'Minervini v2', ko: '미너비니 v2' },
  stratMinerviniV2Note: {
    en: 'Trend Template pass + liquid enough to trade (avg. daily trading value ≥ ₩300M) — the exact entry '
      + 'filter used by the "Minervini v2" paper-trading strategy.',
    ko: '트렌드 템플릿 통과 + 실제로 매매 가능한 유동성(최근 20일 평균 거래대금 3억원 이상) — 모의투자 '
      + '"미너비니 v2" 전략이 신규 진입에 실제로 쓰는 조건과 동일합니다.',
  },
  stratAnonymousNote: {
    en: 'Donchian channel breakout (15-day high) + avg. daily trading value ≥ ₩100M — the exact entry filter '
      + 'used by the "Anonymous" paper-trading strategy. Does NOT require Trend Template pass, so turn off '
      + '"Passed all conditions only" above to see the full candidate pool.',
    ko: '돈치안 채널 브레이크아웃(직전 15거래일 고가 돌파) + 평균 일 거래대금 1억원 이상 — 모의투자 '
      + '"어나니머스" 전략이 신규 진입에 실제로 쓰는 조건과 동일합니다. 트렌드템플릿 통과 여부는 요구하지 '
      + '않으니, 정확한 후보를 보려면 위 "전체 조건 통과만" 체크를 해제하세요.',
  },
  stage1: { en: 'Stage 1 (Basing)', ko: '1단계(바닥 다지기)' },
  stage2: { en: 'Stage 2 (Advancing)', ko: '2단계(상승 추세)' },
  stage3: { en: 'Stage 3 (Topping)', ko: '3단계(천장)' },
  stage4: { en: 'Stage 4 (Declining)', ko: '4단계(하락 추세)' },
  // 단계 필터 버튼 라벨 - "이상(threshold)" 방식이라 종목 자체의 단계 라벨(stage1~4)과는
  // 별개 문구를 쓴다. 4단계는 최상단이라 "이상"을 붙이지 않는다.
  stageFilter1: { en: 'Stage 1+', ko: '1단계 이상' },
  stageFilter2: { en: 'Stage 2+', ko: '2단계 이상' },
  stageFilter3: { en: 'Stage 3+', ko: '3단계 이상' },
  stageFilter4: { en: 'Stage 4', ko: '4단계' },
  failedToLoadData: { en: 'Failed to load data', ko: '데이터를 불러오지 못했습니다' },
  notAvailableForKrStocks: { en: 'Not available for KR stocks (no data source)', ko: '국내 종목은 제공되지 않습니다(데이터 소스 없음)' },
  financialMetrics: { en: 'Financial Metrics', ko: '재무 지표' },
  searchMetricsPlaceholder: { en: 'Search metrics (e.g. Debt Ratio, ROE)', ko: '지표 검색 (예: 부채비율, ROE)' },
  financialsDartAnnual: { en: 'Financials (DART FY{year} Annual Report)', ko: '재무제표 (DART {year}년 사업보고서)' },
  netIncome: { en: 'Net Income', ko: '당기순이익' },
  analystTargetsRatings: { en: 'Analyst Targets & Ratings', ko: '애널리스트 목표가 및 투자의견' },
  targetPriceUnavailable: { en: "Target price figures aren't available on the Finnhub free plan.", ko: '목표가 데이터는 Finnhub 무료 플랜에서 제공되지 않습니다.' },
  trendTemplate: { en: 'Trend Template', ko: '트렌드 템플릿' },
  volume: { en: 'Volume', ko: '거래량' },
  backtestInKrSwing: { en: 'Backtest in KR Swing', ko: '국내 스윙에서 백테스트' },
  trendTemplate8Conditions: { en: 'Trend Template — 8 Conditions', ko: '트렌드 템플릿 — 8가지 조건' },
  priceAboveMa150And200: { en: '① Price > MA150 &amp; MA200', ko: '① 현재가 > MA150 &amp; MA200' },
  ma150AboveMa200: { en: '② MA150 > MA200', ko: '② MA150 > MA200' },
  ma200Rising: { en: '③ MA200 rising 1mo+', ko: '③ MA200 1개월 이상 상승' },
  ma50AboveMa150And200: { en: '④ MA50 > MA150 &amp; MA200', ko: '④ MA50 > MA150 &amp; MA200' },
  priceAboveMa50: { en: '⑤ Price > MA50', ko: '⑤ 현재가 > MA50' },
  priceAbove52wLowBy30pct: { en: '⑥ +30% above 52w low', ko: '⑥ 52주 최저가 대비 +30% 이상' },
  priceWithin25pctOf52wHigh: { en: '⑦ Within 25% of 52w high', ko: '⑦ 52주 최고가 대비 25% 이내' },
  rsAboveThreshold: { en: '⑧ RS Rating ≥ 70', ko: '⑧ RS 등급 ≥ 70' },

  // 실전현황
  noActiveInfinitePositions: { en: 'No active Infinite Buying positions', ko: '진행 중인 무한매수법 포지션이 없습니다' },
  enterTickerToStart: { en: 'Enter a ticker and capital above to start', ko: '위에서 티커와 투자원금을 입력해 시작하세요' },
  splitsExhausted: { en: 'Splits Exhausted', ko: '분할 소진' },
  basicInfo: { en: 'Basic Info', ko: '기본 정보' },
  capitalUsed: { en: 'Capital Used', ko: '사용된 원금' },
  splitAmount: { en: 'Split Amount', ko: '분할 금액' },
  positionInfo: { en: 'Position Info', ko: '포지션 정보' },
  infiniteBuyingFormula: { en: 'Infinite Buying Formula', ko: '무한매수법 공식' },
  starPct: { en: 'Star %', ko: '★ 비율' },
  valuation: { en: 'Valuation', ko: '평가' },
  unrealizedPl: { en: 'Unrealized P/L', ko: '평가손익' },
  infiniteBuyingGuide: { en: 'Infinite Buying Guide', ko: '무한매수법 가이드' },
  sharesUnit: { en: 'sh', ko: '주' },
  addTrade: { en: 'Add Trade', ko: '거래 추가' },
  tradeHistory: { en: 'Trade History', ko: '거래 내역' },

  // 실험실
  alreadyAdded: { en: 'is already added', ko: '은(는) 이미 추가되어 있습니다' },
  canCompareUpTo8: { en: 'You can compare up to 8 at a time', ko: '한 번에 최대 8개까지 비교할 수 있습니다' },
  addTickerIndexToCompare: { en: 'Add a ticker/index to compare', ko: '비교할 티커/지수를 추가하세요' },
  loadingPrices: { en: 'Loading prices...', ko: '가격 데이터를 불러오는 중...' },
  compareAxisPerUnit: { en: 'Compare (separate axis per unit)', ko: '비교(단위별 별도 축)' },
  logScale: { en: 'Log scale', ko: '로그 스케일' },
  findConditionRanges: { en: 'Find Condition Ranges', ko: '조건 구간 찾기' },
  addCondition: { en: 'Add Condition', ko: '조건 추가' },
  combine: { en: 'Combine', ko: '결합 방식' },
  andAllMatch: { en: 'AND (all match)', ko: 'AND(모두 충족)' },
  orAnyMatch: { en: 'OR (any match)', ko: 'OR(하나라도 충족)' },
  highlightRanges: { en: 'Highlight Ranges', ko: '구간 강조 표시' },
  reset: { en: 'Reset', ko: '초기화' },
  changeVsPriorPeriod: { en: 'Change vs. prior period (%)', ko: '직전 대비 변동률(%)' },
  valueClose: { en: 'Value (Close)', ko: '값(종가)' },
  daily: { en: 'Daily', ko: '일간' },
  weekly: { en: 'Weekly', ko: '주간' },
  monthly: { en: 'Monthly', ko: '월간' },
  yearly: { en: 'Yearly', ko: '연간' },
  unitPoints: { en: 'Points', ko: '포인트' },
  unitDollars: { en: 'Dollars ($)', ko: '달러($)' },
  unitRate: { en: 'Rate (%)', ko: '금리(%)' },
  changePct: { en: 'Change (%)', ko: '변동률(%)' },
  valueClose2: { en: 'Value', ko: '값' },
  orMore: { en: 'or more', ko: '이상' },
  moreThan: { en: 'more than', ko: '초과' },
  orLess: { en: 'or less', ko: '이하' },
  lessThan: { en: 'less than', ko: '미만' },
  or: { en: 'or', ko: '또는' },
  and: { en: 'and', ko: '그리고' },
  rangesMatching: { en: 'Ranges matching', ko: '조건에 맞는 구간' },
  noRangesMatchCondition: { en: 'No ranges match this condition', ko: '이 조건에 맞는 구간이 없습니다' },

  // 정적 HTML(templates/index.html) - 헤더/탭/폼 라벨 등 (data-i18n 계열 속성으로 연결)
  manageUsers: { en: 'Manage Users', ko: '사용자 관리' },
  changeTheme: { en: 'Change theme', ko: '테마 변경' },
  apiKeySettings: { en: 'API Key', ko: 'API 키' },
  logOut: { en: 'Log out', ko: '로그아웃' },
  marketLabel: { en: 'MARKET', ko: '시장' },
  tabMacro: { en: 'Macro', ko: '매크로' },
  tabSearch: { en: 'Search', ko: '검색' },
  tabPortfolio: { en: 'Portfolio', ko: '포트폴리오' },
  tabAlerts: { en: 'Alerts', ko: '알림' },
  tabBacktest: { en: 'Backtest', ko: '백테스트' },
  tabLive: { en: 'Live', ko: '실전현황' },
  tabLab: { en: 'Lab', ko: '실험실' },
  tabKrSwing: { en: 'KR Swing', ko: '국내 스윙' },
  tabKrQuant: { en: 'KR Quant', ko: '국내 퀀트' },
  tabScreener: { en: 'Screener', ko: '스크리닝' },
  searchTickerPlaceholder: { en: 'Enter ticker (e.g. AAPL, TSLA, NVDA)', ko: '티커 입력 (예: AAPL, TSLA, NVDA)' },
  quickPicks: { en: 'Quick picks:', ko: '빠른 선택:' },
  tickerExamplePlaceholder: { en: 'Ticker (AAPL)', ko: '티커 (AAPL)' },
  costBasisPlaceholder: { en: 'Cost basis ($)', ko: '매입단가 ($)' },
  add: { en: 'Add', ko: '추가' },
  tickerLabel: { en: 'Ticker', ko: '티커' },
  priceThresholdPlaceholder: { en: 'Price threshold ($)', ko: '기준가격 ($)' },
  optionRisesAbove: { en: 'Rises above', ko: '상승 시' },
  optionFallsBelow: { en: 'Falls below', ko: '하락 시' },
  addAlertBtn: { en: 'Add alert', ko: '알림 추가' },
  version: { en: 'Version', ko: '버전' },
  startDate: { en: 'Start date', ko: '시작일' },
  endDate: { en: 'End date', ko: '종료일' },
  capitalUsdLabel: { en: 'Capital ($)', ko: '투자원금 ($)' },
  targetReturnPctLabel: { en: 'Target return (%)', ko: '목표수익률 (%)' },
  runBacktestBtn: { en: 'Run backtest', ko: '백테스트 실행' },
  startNewPosition: { en: 'Start new position', ko: '새 포지션 시작' },
  labTickerPlaceholder: { en: 'Enter ticker/index (e.g. ^IXIC, SOXL, ^TNX)', ko: '티커/지수 입력 (예: ^IXIC, SOXL, ^TNX)' },
  compareBtn: { en: 'Compare', ko: '비교' },
  stockNameLabel: { en: 'Stock name', ko: '종목명' },
  capitalKrwLabel: { en: 'Capital (₩)', ko: '투자원금 (₩)' },
  volatilityFactorK: { en: 'Volatility factor K', ko: '변동성 계수 K' },
  holdingDays: { en: 'Holding days', ko: '보유일수' },
  stopLossPct: { en: 'Stop-loss (%)', ko: '손절 (%)' },
  breakoutPeriodDays: { en: 'Breakout period (days)', ko: '돌파 기간 (일)' },
  exitPeriodDays: { en: 'Exit period (days)', ko: '청산 기간 (일)' },
  longMaDays: { en: 'Long MA (days)', ko: '장기 이평선 (일)' },
  shortMaDays: { en: 'Short MA (days)', ko: '단기 이평선 (일)' },
  trendMaDays: { en: 'Trend MA (days)', ko: '추세 이평선 (일)' },
  pullbackMaDays: { en: 'Pullback MA (days)', ko: '눌림목 이평선 (일)' },
  momentumFactorK: { en: 'Momentum factor K', ko: '모멘텀 계수 K' },
  trendBreakWindowDays: { en: 'Trend-break window (days)', ko: '추세이탈 판단기간 (일)' },
  checkingFundamentals: { en: 'Checking fundamentals data...', ko: '재무 데이터 확인 중...' },
  currentScreenTitle: { en: 'Current Screen (Low P/E + High ROE)', ko: '현재 스크리닝 결과 (저PER + 고ROE)' },
  stockCountLabel: { en: 'Stock count', ko: '종목 수' },
  minMarketCapLabel: { en: 'Min market cap (₩100M)', ko: '최소 시가총액 (억원)' },
  annualRebalanceBacktestTitle: { en: 'Annual Rebalance Backtest', ko: '연간 리밸런싱 백테스트' },
  startYearLabel: { en: 'Start year', ko: '시작 연도' },
  endYearLabel: { en: 'End year', ko: '종료 연도' },
  holdingsLabel: { en: 'Holdings', ko: '보유 종목 수' },
  passAll8Conditions: { en: 'Pass all 8 conditions', ko: '8개 조건 모두 충족' },
  searchByNameOrCode: { en: 'Search by name or code', ko: '종목명 또는 코드로 검색' },
  allLabel: { en: 'All', ko: '전체' },
  rs90Leaders: { en: 'RS 90+ Leaders', ko: 'RS 90+ 주도주' },
  nearHigh10: { en: 'Near High (10%)', ko: '고점 근접 (10%)' },
  earlyBreakout: { en: 'Early Breakout', ko: '초기 돌파' },
  watchlistLabel: { en: 'Watchlist', ko: '관심종목' },
  filtersLabel: { en: 'Filters', ko: '필터' },
  closeLabel: { en: 'Close', ko: '닫기' },
};

// ─── API 호출 (Python 백엔드 경유) ───────────────────────────────────────────

async function api(method, path, body) {
  const opts = { method, headers: { 'Content-Type': 'application/json' } };
  if (body) opts.body = JSON.stringify(body);

  let res;
  try {
    res = await fetch(path, opts);
  } catch (e) {
    throw new Error('서버에 연결할 수 없습니다. 네트워크 상태를 확인하고 다시 시도해주세요.');
  }

  if (res.status === 401) {
    window.location.href = '/login';
    throw new Error('로그인이 필요합니다');
  }

  let data;
  try {
    data = await res.json();
  } catch (e) {
    // 서버가 JSON이 아닌 응답(HTML 에러 페이지 등)을 준 경우 — 배포 플랫폼이 깨어나는 중이거나
    // 일시적인 오류일 수 있으므로 원문 파싱 에러 대신 사람이 읽을 수 있는 메시지로 바꾼다.
    throw new Error(
      res.status >= 500
        ? '서버가 일시적으로 응답하지 않습니다. 잠시 후 다시 시도해주세요.'
        : `예상치 못한 응답을 받았습니다 (${res.status}). 다시 시도해주세요.`
    );
  }

  if (!res.ok) throw new Error(data.error || `오류 (${res.status})`);
  return data;
}

// ─── API 키 설정 ─────────────────────────────────────────────────────────────

function showKeyBanner() {
  const existing = document.getElementById('api-key-banner');
  if (existing) { existing.style.display = 'block'; return; }

  const banner = document.createElement('div');
  banner.id = 'api-key-banner';
  banner.className = 'api-banner';
  banner.innerHTML = `
    <div class="api-banner-inner">
      <div>
        <strong>${t('apiKeyTitle')}</strong>
        <p>${t('apiKeyCopyFrom')} <a href="https://finnhub.io/dashboard" target="_blank" rel="noopener">finnhub.io dashboard</a></p>
      </div>
      <div class="api-key-row">
        <input type="text" id="api-key-input" placeholder="${t('apiKeyPlaceholder')}" style="width:280px;" />
        <button class="btn-primary" onclick="saveApiKey()">${t('saveVerify')}</button>
        <button class="btn-secondary" onclick="document.getElementById('api-key-banner').remove()">${t('cancel')}</button>
      </div>
      <div id="key-error" class="key-error" style="display:none;"></div>
    </div>`;
  document.querySelector('.app').prepend(banner);
}

async function saveApiKey() {
  const key = document.getElementById('api-key-input')?.value.trim();
  const errEl = document.getElementById('key-error');
  if (!key) return;

  const btn = document.querySelector('#api-key-banner .btn-primary');
  btn.disabled = true;
  btn.textContent = t('verifying');
  if (errEl) { errEl.style.display = 'none'; errEl.textContent = ''; }

  try {
    await api('POST', '/api/settings/key', { key });
    document.getElementById('api-key-banner')?.remove();
    showToast(t('apiKeySaved'));
  } catch (e) {
    if (errEl) { errEl.textContent = e.message; errEl.style.display = 'block'; }
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = t('saveVerify'); }
  }
}

// ─── 탭 전환 ─────────────────────────────────────────────────────────────────

function switchTab(name) {
  document.querySelectorAll('.tab').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.tab === name);
  });
  document.querySelectorAll('.section').forEach(s => s.classList.remove('active'));
  document.getElementById('sec-' + name).classList.add('active');
  if (name === 'macro') loadMacro();
  if (name === 'portfolio') loadPortfolio();
  if (name === 'alerts') loadAlerts();
  if (name === 'backtest') initBacktestDates();
  if (name === 'live') loadInfinitePositions();
  if (name === 'lab') initLabTab();
  if (name === 'krswing') initKrSwingDates();
  if (name === 'krquant') initKrQuantTab();
  if (name === 'screener') initScreenerTab();
  if (name === 'screenbt') initScreeningBacktestTab();
  if (name === 'papertrade') loadPaperTrading();
}

document.addEventListener('DOMContentLoaded', () => loadMacro());

// ─── 매크로(주요 시황) ────────────────────────────────────────────────────────

async function loadMacro() {
  const el = document.getElementById('macro-content');
  if (!el || el.dataset.loaded === '1') return;
  el.innerHTML = `<div class="loading-msg"><i class="ti ti-loader-2" aria-hidden="true"></i>${t('loadingMarketOverview')}</div>`;

  const maxAttempts = 3;
  let lastData = null;
  for (let attempt = 1; attempt <= maxAttempts; attempt++) {
    try {
      const data = await api('GET', '/api/macro');
      lastData = data;
      renderTicker(data);
      const allOk = (data.instruments || []).every(i => i.price !== null && i.price !== undefined) && !!data.fearGreed;
      if (allOk || attempt === maxAttempts) {
        renderMacro(data);
        el.dataset.loaded = '1';
        return;
      }
    } catch (e) {
      if (attempt === maxAttempts) {
        el.innerHTML = `
          <div class="error-msg"><i class="ti ti-alert-circle" aria-hidden="true"></i><span>${escapeHtml(e.message)}</span></div>
          <button class="btn-secondary" onclick="refreshMacro()"><i class="ti ti-refresh" aria-hidden="true"></i> ${t('retry')}</button>`;
        return;
      }
    }
    await new Promise(r => setTimeout(r, 700));
  }
  if (lastData) {
    renderMacro(lastData);
    el.dataset.loaded = '1';
  }
}

function renderMacro(data) {
  const el = document.getElementById('macro-content');
  const items = data.instruments || [];
  const groups = {};
  items.forEach(item => {
    (groups[item.group] ??= []).push(item);
  });

  const asOfText = formatAsOf(data.asOf);

  el.innerHTML = `
    <div class="add-form" style="justify-content:flex-end;align-items:center;">
      ${asOfText ? `<span style="font-size:12px;color:var(--text-muted);margin-right:auto;">${t('asOf')} ${asOfText}</span>` : ''}
      <button class="btn-secondary" onclick="refreshMacro()"><i class="ti ti-refresh" aria-hidden="true"></i> ${t('refresh')}</button>
    </div>
    ${data.fearGreed ? renderFearGreedCard(data.fearGreed) : ''}
    ${Object.entries(groups).map(([group, list]) => `
      <div class="card">
        <div style="font-size:12px;font-weight:600;color:var(--text-secondary);margin-bottom:10px;text-transform:uppercase;letter-spacing:0.04em;">${escapeHtml(tv(group))}</div>
        <div class="macro-grid">
          ${list.map(m => {
            const hasPrice = m.price !== null && m.price !== undefined;
            const isPos = hasPrice && m.change >= 0;
            const priceText = hasPrice ? formatMacroPrice(m.price, m.unit) : null;
            const changeText = hasPrice ? formatMacroChange(m.change, m.changePct, m.unit) : null;
            const hasSeries = hasPrice && Array.isArray(m.series) && m.series.length > 1;
            return `
            <div class="macro-item">
              <div class="macro-name">${escapeHtml(tv(m.name))}</div>
              <div class="macro-ticker">${escapeHtml(m.ticker)}</div>
              ${hasPrice ? `
                <div class="macro-price">${priceText}</div>
                <div class="macro-change ${isPos ? 'positive' : 'negative'}">
                  <i class="ti ti-trending-${isPos ? 'up' : 'down'}" aria-hidden="true"></i>
                  <span>${changeText}</span>
                </div>
                ${hasSeries ? buildMacroSparklineSvg(m.series, isPos) : ''}
              ` : `<div class="macro-price" style="color:var(--text-muted);font-size:13px;">${t('noData')}</div>`}
            </div>`;
          }).join('')}
        </div>
      </div>`).join('')}
  `;

  animateFearGreedRing(el);
  requestAnimationFrame(() => {
    requestAnimationFrame(() => {
      el.querySelectorAll('.macro-sparkline').forEach(svg => svg.classList.add('in'));
    });
  });
}

// 제목과 탭 사이의 전광판(무한 스크롤 티커). 매크로 탭이 열려있지 않아도 페이지
// 로드 시 loadMacro()가 항상 한 번은 도니(§ 매크로 섹션 상단 참고) 그 결과를 그대로 재사용한다.
// 애니메이션은 내용을 두 벌 이어붙여 절반(-50%) 이동했을 때 이음매 없이 반복되게 한다 -
// 두 번째 벌은 스크린리더에 중복 노출되지 않도록 aria-hidden 처리한다.
function renderTicker(data) {
  const track = document.getElementById('ticker-track');
  if (!track) return;
  const items = (data.instruments || []).filter(m => m.price !== null && m.price !== undefined);
  if (!items.length) return;

  const piece = items.map(m => {
    const isPos = m.change >= 0;
    return `<span class="ticker-item">
        <span class="ticker-name">${escapeHtml(tv(m.name))}</span>
        <span class="ticker-price">${formatMacroPrice(m.price, m.unit)}</span>
        <span class="ticker-change ${isPos ? 'positive' : 'negative'}"><i class="ti ti-trending-${isPos ? 'up' : 'down'}" aria-hidden="true"></i>${formatMacroChange(m.change, m.changePct, m.unit)}</span>
      </span>`;
  }).join('<span class="ticker-sep">·</span>');

  track.innerHTML = `<span class="ticker-set">${piece}</span><span class="ticker-set" aria-hidden="true">${piece}</span>`;

  const asOfEl = document.getElementById('ticker-asof');
  if (asOfEl) {
    const asOfText = formatAsOf(data.asOf);
    asOfEl.textContent = asOfText ? asOfText.slice(5) : ''; // "MM-DD HH:MM"만 - 티커 라벨은 자리가 좁다
  }
}

const FEAR_GREED_SEGMENTS = [
  { from: 0, to: 20, color: '#e5484d' },
  { from: 20, to: 40, color: '#f0a058' },
  { from: 40, to: 60, color: '#dfb945' },
  { from: 60, to: 80, color: '#8fc46a' },
  { from: 80, to: 100, color: '#38a973' },
];

const FEAR_GREED_RATING_KO = {
  'extreme fear': { key: 'fgExtremeFear', color: '#e5484d' },
  'fear': { key: 'fgFear', color: '#f0a058' },
  'neutral': { key: 'fgNeutral', color: '#dfb945' },
  'greed': { key: 'fgGreed', color: '#8fc46a' },
  'extreme greed': { key: 'fgExtremeGreed', color: '#38a973' },
};

function fearGreedRatingInfo(rating) {
  const entry = FEAR_GREED_RATING_KO[(rating || '').toLowerCase()];
  return entry ? { label: t(entry.key), color: entry.color } : { label: rating || '-', color: 'var(--text-muted)' };
}

// 원형 링을 stroke-dasharray/dashoffset으로 그려서, 최초 렌더 시 0%에서 실제
// 점수만큼 부드럽게 채워지는 애니메이션을 CSS transition만으로 건다(매 프레임
// path를 다시 그릴 필요가 없다). 링 색상은 등급별 의미(공포=빨강~탐욕=초록)를
// 그대로 유지해 정보량이 줄지 않게 한다. feGaussianBlur로 채워진 부분에만
// 은은한 글로우를 준다.
function buildFearGreedGaugeSvg(score) {
  const size = 172, cx = size / 2, cy = size / 2, r = 66, sw = 14;
  const circumference = 2 * Math.PI * r;
  const clampedScore = Math.max(0, Math.min(100, score));
  const rating = fearGreedRatingInfo(fearGreedScoreToRating(clampedScore));
  const targetOffset = circumference * (1 - clampedScore / 100);
  const glowId = 'fg-glow-' + Math.random().toString(36).slice(2, 8);

  return `
    <svg viewBox="0 0 ${size} ${size}" role="img" aria-label="${t('fgTitle')} ${Math.round(clampedScore)}, ${rating.label}">
      <defs>
        <filter id="${glowId}" x="-60%" y="-60%" width="220%" height="220%">
          <feGaussianBlur stdDeviation="4.5" result="blur"></feGaussianBlur>
          <feMerge><feMergeNode in="blur"></feMergeNode><feMergeNode in="SourceGraphic"></feMergeNode></feMerge>
        </filter>
      </defs>
      <circle cx="${cx}" cy="${cy}" r="${r}" fill="none" stroke="var(--bg-tertiary)" stroke-width="${sw}"></circle>
      <circle class="fg-ring" cx="${cx}" cy="${cy}" r="${r}" fill="none" stroke="${rating.color}" stroke-width="${sw}"
        stroke-linecap="round" stroke-dasharray="${circumference.toFixed(1)}"
        stroke-dashoffset="${circumference.toFixed(1)}" data-target-offset="${targetOffset.toFixed(1)}"
        transform="rotate(-90 ${cx} ${cy})" filter="url(#${glowId})"></circle>
      <text x="${cx}" y="${cy - 1}" text-anchor="middle" font-size="34" font-weight="800" fill="var(--text)" class="fg-score-text">${Math.round(clampedScore)}</text>
      <text x="${cx}" y="${cy + 21}" text-anchor="middle" font-size="12.5" font-weight="700" fill="${rating.color}">${escapeHtml(rating.label)}</text>
    </svg>`;
}

// 최초 렌더 직후 dashoffset을 원둘레 전체(빈 링)에서 실제 목표치로 CSS
// transition이 걸리도록, 삽입된 다음 프레임에 적용한다(같은 프레임이면 씹힌다).
function animateFearGreedRing(root) {
  const ring = (root || document).querySelector('.fg-ring');
  if (!ring) return;
  const target = ring.dataset.targetOffset;
  requestAnimationFrame(() => {
    requestAnimationFrame(() => {
      ring.style.strokeDashoffset = target;
    });
  });
}

function renderFearGreedCard(fg) {
  const history = [
    { label: t('fgPrevClose'), value: fg.previousClose },
    { label: t('fg1WeekAgo'), value: fg.previousWeek },
    { label: t('fg1MonthAgo'), value: fg.previousMonth },
    { label: t('fg1YearAgo'), value: fg.previousYear },
  ];
  return `
    <div class="card fear-greed-card">
      <div style="font-size:12px;font-weight:600;color:var(--text-secondary);margin-bottom:4px;text-transform:uppercase;letter-spacing:0.04em;">${t('fgTitle')}</div>
      <div class="fear-greed-body">
        <div class="fear-greed-gauge">
          <div class="fg-live-badge"><span class="fg-live-dot" aria-hidden="true"></span>${t('live')}</div>
          ${buildFearGreedGaugeSvg(fg.score)}
          <div class="fear-greed-legend">
            ${Object.values(FEAR_GREED_RATING_KO).map(r => `<span class="fear-greed-legend-item"><span class="fear-greed-dot" style="background:${r.color};"></span>${escapeHtml(r.label)}</span>`).join('')}
          </div>
        </div>
        <div class="fear-greed-history">
          ${history.map(h => {
            const r = fearGreedRatingInfo(fearGreedScoreToRating(h.value));
            return `
            <div class="fear-greed-history-row">
              <div>
                <div class="fear-greed-history-label">${h.label}</div>
                <div class="fear-greed-history-rating" style="color:${r.color};">${escapeHtml(r.label)}</div>
              </div>
              <div class="fear-greed-badge" style="border-color:${r.color};color:${r.color};">${Math.round(h.value)}</div>
            </div>`;
          }).join('')}
        </div>
      </div>
    </div>`;
}

function fearGreedScoreToRating(score) {
  if (score < 20) return 'extreme fear';
  if (score < 40) return 'fear';
  if (score < 60) return 'neutral';
  if (score < 80) return 'greed';
  return 'extreme greed';
}

// 1개월 종가 시계열로 작은 영역 스파크라인을 그린다. 절대 레벨이 아니라 추세가
// 목적이라 min~max를 뷰박스에 꽉 채워서(정규화) 미세한 변동도 잘 보이게 한다.
function buildMacroSparklineSvg(series, isPos) {
  const w = 100, h = 32, pad = 2;
  const min = Math.min(...series), max = Math.max(...series);
  const range = (max - min) || 1;
  const stepX = (w - pad * 2) / (series.length - 1);
  const xy = (v, i) => [pad + i * stepX, h - pad - ((v - min) / range) * (h - pad * 2)];
  const pts = series.map((v, i) => xy(v, i));
  const line = pts.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(' ');
  const area = `${pad},${h - pad} ${line} ${w - pad},${h - pad}`;
  const gid = 'spark-' + Math.random().toString(36).slice(2, 9);
  const color = isPos ? 'var(--green)' : 'var(--red)';
  return `
    <svg class="macro-sparkline" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" aria-hidden="true">
      <defs>
        <linearGradient id="${gid}" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stop-color="${color}" stop-opacity="0.35"></stop>
          <stop offset="100%" stop-color="${color}" stop-opacity="0"></stop>
        </linearGradient>
      </defs>
      <polygon points="${area}" fill="url(#${gid})"></polygon>
      <polyline points="${line}" fill="none" stroke="${color}" stroke-width="1.5" stroke-linejoin="round" stroke-linecap="round"></polyline>
    </svg>`;
}

function formatMacroPrice(price, unit) {
  if (unit === 'pct') return price.toFixed(2) + '%';
  if (unit === 'usd') return '$' + price.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  return price.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function formatMacroChange(change, changePct, unit) {
  const sign = change >= 0 ? '+' : '';
  if (unit === 'pct') return `${sign}${change.toFixed(2)}%p`;
  return `${sign}${changePct.toFixed(2)}%`;
}

async function refreshMacro() {
  const el = document.getElementById('macro-content');
  if (el) el.dataset.loaded = '0';
  await loadMacro();
}

// ─── 종목 검색 ───────────────────────────────────────────────────────────────

function quickSearch(ticker) {
  document.getElementById('ticker-input').value = ticker;
  searchStock();
}

async function searchStock() {
  const ticker = document.getElementById('ticker-input').value.trim().toUpperCase();
  if (!ticker) return;

  const el = document.getElementById('search-result');
  el.innerHTML = `<div class="loading-msg"><i class="ti ti-loader-2" aria-hidden="true"></i>${t('loadingTickerData').replace('{ticker}', ticker)}</div>`;

  try {
    const data = await api('GET', `/api/stock/${ticker}`);
    renderStockCard(data, el);
  } catch (e) {
    el.innerHTML = `<div class="error-msg"><i class="ti ti-alert-circle" aria-hidden="true"></i><span>${e.message}</span></div>`;
  }
}

function renderStockCard(d, el) {
  const isPos = d.change >= 0;
  const upside = d.targetMean ? ((d.targetMean - d.price) / d.price * 100) : null;
  const upsideColor = upside !== null ? (upside >= 0 ? 'var(--green)' : 'var(--red)') : 'var(--text-muted)';
  const barPct = (d.targetMean && d.targetHigh > d.targetLow)
    ? Math.max(0, Math.min(100, (d.price - d.targetLow) / (d.targetHigh - d.targetLow) * 100)) : 50;
  const total = d.recBuy + d.recHold + d.recSell;
  const recLabel = getRecLabel(d.recBuy, d.recHold, d.recSell, total);

  el.innerHTML = `
    <div class="card">
      <div class="stock-header">
        <div>
          <span class="ticker-badge">${escapeHtml(d.ticker)}</span>
          <div class="stock-name">${escapeHtml(d.name)}</div>
          ${d.industry ? `<div class="stock-industry">${escapeHtml(tv(d.industry))}</div>` : ''}
        </div>
        <div>
          <div class="price-big">$${d.price.toFixed(2)}</div>
          <div class="price-change ${isPos ? 'positive' : 'negative'}">
            <i class="ti ti-trending-${isPos ? 'up' : 'down'}" aria-hidden="true"></i>
            ${isPos ? '+' : ''}${d.change.toFixed(2)} (${isPos ? '+' : ''}${d.changePct.toFixed(2)}%)
          </div>
        </div>
      </div>

      <div class="ohlc-grid">
        <div class="ohlc-item"><div class="ohlc-label">${t('open')}</div><div class="ohlc-value">$${d.open.toFixed(2)}</div></div>
        <div class="ohlc-item"><div class="ohlc-label">${t('high')}</div><div class="ohlc-value positive">$${d.high.toFixed(2)}</div></div>
        <div class="ohlc-item"><div class="ohlc-label">${t('low')}</div><div class="ohlc-value negative">$${d.low.toFixed(2)}</div></div>
        <div class="ohlc-item"><div class="ohlc-label">${t('prevClose')}</div><div class="ohlc-value">$${d.prevClose.toFixed(2)}</div></div>
      </div>

      ${d.targetMean ? `
        <div class="meta-grid">
          <div class="meta-item">
            <div class="meta-label">${t('avgTarget')}</div>
            <div class="meta-value" style="color:${upsideColor};">$${d.targetMean.toFixed(2)}</div>
          </div>
          <div class="meta-item">
            <div class="meta-label">${t('lowTarget')}</div>
            <div class="meta-value">$${d.targetLow?.toFixed(2) ?? '-'}</div>
          </div>
          <div class="meta-item">
            <div class="meta-label">${t('highTarget')}</div>
            <div class="meta-value">$${d.targetHigh?.toFixed(2) ?? '-'}</div>
          </div>
        </div>
        <div class="upside-bar-wrap">
          <div class="bar-label">
            <span>${t('low')} $${d.targetLow?.toFixed(0)}</span>
            <span style="color:${upsideColor};font-weight:600;">${t('upside')} ${upside >= 0 ? '+' : ''}${upside.toFixed(1)}%</span>
            <span>${t('high')} $${d.targetHigh?.toFixed(0)}</span>
          </div>
          <div class="bar-track">
            <div class="bar-fill" style="width:${barPct}%;background:${upside >= 0 ? '#639922' : '#E24B4A'};"></div>
          </div>
          <div class="bar-hint">${t('priceWithinTargetRange')}${d.targetUpdated ? ' · ' + t('updated') + ' ' + d.targetUpdated : ''}</div>
        </div>
      ` : `<div style="padding:14px 0;color:var(--text-secondary);font-size:13px;"><i class="ti ti-info-circle" aria-hidden="true"></i> ${t('noTargetPriceData')}</div>`}

      ${total > 0 ? `
        <div class="analyst-breakdown">
          <div class="breakdown-header">
            <span>${total} ${t('analysts')}${d.recPeriod ? ' · ' + d.recPeriod : ''}</span>
            ${recLabel ? `<span class="pill pill-rec">${tv(recLabel)}</span>` : ''}
          </div>
          <div class="breakdown-bar">
            <div style="flex:${d.recBuy};background:#639922;border-radius:3px 0 0 3px;"></div>
            <div style="flex:${d.recHold};background:#EF9F27;"></div>
            <div style="flex:${d.recSell};background:#E24B4A;border-radius:0 3px 3px 0;"></div>
          </div>
          <div class="analyst-pills">
            ${d.recBuy > 0 ? `<span class="pill pill-buy"><i class="ti ti-thumb-up" aria-hidden="true"></i>${t('buy')} ${d.recBuy}</span>` : ''}
            ${d.recHold > 0 ? `<span class="pill pill-hold"><i class="ti ti-minus" aria-hidden="true"></i>${t('hold')} ${d.recHold}</span>` : ''}
            ${d.recSell > 0 ? `<span class="pill pill-sell"><i class="ti ti-thumb-down" aria-hidden="true"></i>${t('sell')} ${d.recSell}</span>` : ''}
          </div>
        </div>
      ` : ''}

      <div class="action-row">
        <button class="btn-primary" onclick="addToPortfolioFromSearch('${d.ticker}', ${d.price})">
          <i class="ti ti-plus" aria-hidden="true"></i> ${t('addToPortfolio')}
        </button>
        <button class="btn-secondary" onclick="addAlertFromSearch('${d.ticker}', ${d.targetMean || d.price})">
          <i class="ti ti-bell" aria-hidden="true"></i> ${t('setAlert')}
        </button>
      </div>
    </div>

    ${d.targetMean ? `
      <div class="card">
        <div style="font-size:13px;font-weight:600;margin-bottom:10px;"><i class="ti ti-chart-bar" aria-hidden="true"></i> ${t('priceVsTarget')}</div>
        <div class="chart-legend">
          <span><span class="legend-dot" style="background:#378ADD;"></span>${t('current')}</span>
          <span><span class="legend-dot" style="background:#F09595;"></span>${t('lowTarget')}</span>
          <span><span class="legend-dot" style="background:#97C459;"></span>${t('avgTarget')}</span>
          <span><span class="legend-dot" style="background:#5DCAA5;"></span>${t('highTarget')}</span>
        </div>
        <div class="chart-wrap">
          <canvas id="target-chart" role="img" aria-label="${d.ticker} ${t('priceVsTargetChart')}"></canvas>
        </div>
      </div>
    ` : ''}
  `;

  // 고정 지연(setTimeout) 대신 두 번의 requestAnimationFrame으로 레이아웃/페인트가
  // 끝난 뒤 그려서, Chart.js의 ResizeObserver가 레이아웃 안정 전에 반복 리사이즈되며
  // 화면이 흔들리는 문제를 피한다(스크리닝 상세 모달에서 처음 발견된 것과 같은 문제).
  if (d.targetMean) requestAnimationFrame(() => requestAnimationFrame(() => drawChart(d.price, d.targetLow, d.targetMean, d.targetHigh)));
}

function getRecLabel(buy, hold, sell, total) {
  if (!total) return '';
  const br = buy / total;
  const sr = sell / total;
  if (br >= 0.6) return 'Strong Buy';
  if (br >= 0.4) return 'Buy';
  if (sr >= 0.4) return 'Sell';
  return 'Hold';
}

function drawChart(cur, low, mean, high) {
  const canvas = document.getElementById('target-chart');
  if (!canvas) return;
  if (priceChart) priceChart.destroy();
  priceChart = new Chart(canvas, {
    type: 'bar',
    data: {
      labels: [t('current'), t('lowTarget'), t('avgTarget'), t('highTarget')],
      datasets: [{ data: [cur, low, mean, high], backgroundColor: ['#378ADD','#F09595','#97C459','#5DCAA5'], borderRadius: 5, borderSkipped: false }]
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { display: false }, tooltip: { callbacks: { label: ctx => ' $' + ctx.parsed.y.toFixed(2) } } },
      scales: { y: { beginAtZero: false, ticks: { callback: v => '$' + v.toFixed(0) }, grid: { color: 'rgba(128,128,128,0.1)' } }, x: { grid: { display: false } } }
    }
  });
}

// ─── 포트폴리오 ──────────────────────────────────────────────────────────────

function addToPortfolioFromSearch(ticker, price) {
  document.getElementById('pf-ticker').value = ticker;
  document.getElementById('pf-price').value = price.toFixed(2);
  switchTab('portfolio');
  document.getElementById('pf-qty').focus();
}

async function addPortfolioItem() {
  const ticker = document.getElementById('pf-ticker').value.trim().toUpperCase();
  const qty = parseFloat(document.getElementById('pf-qty').value) || 0;
  const buyPrice = parseFloat(document.getElementById('pf-price').value) || 0;
  if (!ticker) { alert('티커를 입력하세요'); return; }
  try {
    await api('POST', '/api/portfolio', { ticker, qty, buyPrice });
    document.getElementById('pf-ticker').value = '';
    document.getElementById('pf-qty').value = '';
    document.getElementById('pf-price').value = '';
    await refreshPortfolio();
  } catch (e) { alert(e.message); }
}

async function loadPortfolio() {
  const el = document.getElementById('portfolio-content');
  el.innerHTML = `<div class="loading-msg"><i class="ti ti-loader-2" aria-hidden="true"></i>${t('loading')}</div>`;
  try {
    const data = await api('GET', '/api/portfolio');
    renderPortfolio(data);
  } catch (e) {
    el.innerHTML = `<div class="error-msg"><i class="ti ti-alert-circle" aria-hidden="true"></i><span>${e.message}</span></div>`;
  }
}

async function refreshPortfolio() {
  const el = document.getElementById('portfolio-content');
  el.innerHTML = `<div class="loading-msg"><i class="ti ti-loader-2" aria-hidden="true"></i>${t('updatingPrices')}</div>`;
  try {
    const data = await api('POST', '/api/portfolio/refresh');
    renderPortfolio(data);
  } catch (e) {
    el.innerHTML = `<div class="error-msg"><i class="ti ti-alert-circle" aria-hidden="true"></i><span>${e.message}</span></div>`;
  }
}

function renderPortfolio(portfolio) {
  const el = document.getElementById('portfolio-content');
  if (!portfolio.length) {
    el.innerHTML = `<div class="empty-state"><i class="ti ti-briefcase" aria-hidden="true"></i><p>${t('portfolioEmpty')}</p><small>${t('portfolioEmptyHint')}</small></div>`;
    return;
  }

  let totalValue = 0, totalCost = 0;
  portfolio.forEach(p => {
    if (p.currentPrice && p.qty) {
      totalValue += p.currentPrice * p.qty;
      totalCost += (p.buyPrice || p.currentPrice) * p.qty;
    }
  });
  const pnl = totalValue - totalCost;
  const pnlPct = totalCost > 0 ? pnl / totalCost * 100 : 0;

  el.innerHTML = `
    <div class="summary-grid">
      <div class="meta-item"><div class="meta-label">${t('totalValue')}</div><div class="meta-value">$${totalValue.toLocaleString('en-US', {maximumFractionDigits:0})}</div></div>
      <div class="meta-item"><div class="meta-label">${t('totalCost')}</div><div class="meta-value">$${totalCost.toLocaleString('en-US', {maximumFractionDigits:0})}</div></div>
      <div class="meta-item"><div class="meta-label">${t('totalPL')}</div><div class="meta-value ${pnl >= 0 ? 'positive' : 'negative'}">${pnl >= 0 ? '+' : ''}$${Math.abs(pnl).toLocaleString('en-US', {maximumFractionDigits:0})} <span style="font-size:13px;">(${pnlPct.toFixed(1)}%)</span></div></div>
    </div>
    <div class="pf-table-wrap">
      <table class="pf-table">
        <thead><tr><th>${t('stock')}</th><th>${t('price')}</th><th>${t('target')}</th><th>${t('upside')}</th><th>${t('qty')}</th><th>${t('pl')}</th><th></th></tr></thead>
        <tbody>
          ${portfolio.map(p => {
            const cur = p.currentPrice || 0;
            const tgt = p.targetPrice;
            const upside = tgt ? ((tgt - cur) / cur * 100) : null;
            const itemPnl = (p.qty && p.buyPrice) ? (cur - p.buyPrice) * p.qty : null;
            const itemPnlPct = p.buyPrice ? (cur - p.buyPrice) / p.buyPrice * 100 : null;
            const cls = (p.changePct || 0) >= 0 ? 'positive' : 'negative';
            return `<tr>
              <td><div style="font-weight:600;">${escapeHtml(p.ticker)}</div><div style="font-size:11px;color:var(--text-secondary);">${escapeHtml(p.name || '')}</div></td>
              <td><div>${cur ? '$' + cur.toFixed(2) : '-'}</div>${cur ? `<div class="${cls}" style="font-size:11px;">${(p.changePct||0)>=0?'+':''}${(p.changePct||0).toFixed(2)}%</div>` : ''}</td>
              <td>${tgt ? '$' + tgt.toFixed(2) : '<span style="color:var(--text-muted)">-</span>'}</td>
              <td>${upside !== null ? `<span style="color:${upside>=0?'var(--green)':'var(--red)'};font-weight:600;">${upside>=0?'+':''}${upside.toFixed(1)}%</span>` : '-'}</td>
              <td>${p.qty || '-'}</td>
              <td>${itemPnl !== null ? `<div class="${itemPnl>=0?'positive':'negative'}" style="font-weight:600;">${itemPnl>=0?'+':''}$${Math.abs(itemPnl).toFixed(0)}</div><div style="font-size:11px;color:var(--text-secondary);">(${itemPnlPct>=0?'+':''}${itemPnlPct.toFixed(1)}%)</div>` : '-'}</td>
              <td><button class="btn-icon" onclick="removePortfolioItem('${p.ticker}')" aria-label="Delete ${p.ticker}"><i class="ti ti-trash" aria-hidden="true"></i></button></td>
            </tr>`;
          }).join('')}
        </tbody>
      </table>
    </div>`;
}

async function removePortfolioItem(ticker) {
  if (!confirm(`${ticker}을(를) 포트폴리오에서 삭제하시겠습니까?`)) return;
  await api('DELETE', `/api/portfolio/${ticker}`);
  loadPortfolio();
}

// ─── 알림 ────────────────────────────────────────────────────────────────────

function addAlertFromSearch(ticker, price) {
  document.getElementById('al-ticker').value = ticker;
  document.getElementById('al-price').value = price.toFixed(2);
  switchTab('alerts');
  document.getElementById('al-price').focus();
}

async function addAlert() {
  const ticker = document.getElementById('al-ticker').value.trim().toUpperCase();
  const price = parseFloat(document.getElementById('al-price').value);
  const type = document.getElementById('al-type').value;
  if (!ticker || isNaN(price)) { alert('티커와 가격을 입력하세요'); return; }
  try {
    await api('POST', '/api/alerts', { ticker, price, type });
    document.getElementById('al-ticker').value = '';
    document.getElementById('al-price').value = '';
    loadAlerts();
  } catch (e) { alert(e.message); }
}

async function loadAlerts() {
  const el = document.getElementById('alerts-content');
  try {
    const alerts = await api('GET', '/api/alerts');
    renderAlerts(alerts);
  } catch (e) {
    el.innerHTML = `<div class="error-msg"><i class="ti ti-alert-circle" aria-hidden="true"></i><span>${e.message}</span></div>`;
  }
}

function renderAlerts(alerts) {
  const el = document.getElementById('alerts-content');
  const active = alerts.filter(a => !a.triggered).length;
  const badge = document.getElementById('alert-badge');
  badge.style.display = active > 0 ? 'inline' : 'none';
  badge.textContent = active;

  if (!alerts.length) {
    el.innerHTML = `<div class="empty-state"><i class="ti ti-bell-off" aria-hidden="true"></i><p>${t('noAlertsSet')}</p><small>${t('noAlertsHint')}</small></div>`;
    return;
  }

  el.innerHTML = `<div class="notif-list">${alerts.map(a => `
    <div class="notif-item">
      <div>
        <div class="notif-ticker">${escapeHtml(a.ticker)} <span class="${a.triggered ? 'badge-done' : 'badge-active'}">${a.triggered ? t('triggered') : t('active')}</span></div>
        <div class="notif-info">${t('alertWhenPrice')} ${a.type === 'above' ? t('risesAbove') : t('fallsBelow')} $${a.price.toFixed(2)} · ${a.created}${a.triggeredAt ? ` · <span style="color:var(--green)">${t('triggered')} ${a.triggeredAt}</span>` : ''}</div>
      </div>
      <button class="btn-icon" onclick="removeAlert(${a.id})" aria-label="Delete alert"><i class="ti ti-trash" aria-hidden="true"></i></button>
    </div>`).join('')}</div>`;
}

async function removeAlert(id) {
  await api('DELETE', `/api/alerts/${id}`);
  loadAlerts();
}

// ─── 무한매수법 백테스트 ─────────────────────────────────────────────────────

let equityChart = null;
let priceChartBt = null;
const equityChartPanState = { cleanup: null };
const priceChartBtPanState = { cleanup: null };

const BACKTEST_VERSION_DEFAULTS = {
  v2: { splits: 40 },
  v3: { splits: 20 },
  v4: { splits: 20 },
};

const TICKER_TARGET_DEFAULTS = { SOXL: 12, KORU: 20 };

function applyTickerTargetDefault() {
  const ticker = document.getElementById('bt-ticker').value.trim().toUpperCase();
  document.getElementById('bt-target').value = TICKER_TARGET_DEFAULTS[ticker] ?? 10;
}

function applyBacktestVersionDefaults() {
  const version = document.getElementById('bt-version').value;
  const d = BACKTEST_VERSION_DEFAULTS[version];
  document.getElementById('bt-splits').value = d.splits;
  applyTickerTargetDefault();
}

function initBacktestDates() {
  const startEl = document.getElementById('bt-start');
  const endEl = document.getElementById('bt-end');
  if (endEl.value && startEl.value) return;
  const today = new Date();
  const past = new Date();
  past.setMonth(past.getMonth() - 3);
  const fmt = d => d.toISOString().slice(0, 10);
  if (!endEl.value) endEl.value = fmt(today);
  if (!startEl.value) startEl.value = fmt(past);
}

async function runBacktest() {
  const version = document.getElementById('bt-version').value;
  const ticker = document.getElementById('bt-ticker').value.trim().toUpperCase();
  const start = document.getElementById('bt-start').value;
  const end = document.getElementById('bt-end').value;
  const seed = parseFloat(document.getElementById('bt-seed').value);
  const splits = parseInt(document.getElementById('bt-splits').value, 10);
  const targetReturn = parseFloat(document.getElementById('bt-target').value);
  const el = document.getElementById('backtest-result');

  if (!ticker) { alert('티커를 입력하세요'); return; }
  if (!start || !end) { alert('시작일과 종료일을 입력하세요'); return; }
  if (!seed || seed <= 0) { alert('시드를 입력하세요'); return; }
  if (!splits || splits < 2) { alert('분할수를 확인하세요'); return; }
  if (!targetReturn || targetReturn <= 0) { alert('목표수익률을 확인하세요'); return; }

  el.innerHTML = `<div class="loading-msg"><i class="ti ti-loader-2" aria-hidden="true"></i>${t('runningBacktestFor').replace('{ticker}', ticker)}</div>`;

  try {
    const data = await api('POST', '/api/backtest/infinite-buying', { ticker, start, end, seed, splits, targetReturn, version });
    renderBacktestResult(data);
  } catch (e) {
    el.innerHTML = `<div class="error-msg"><i class="ti ti-alert-circle" aria-hidden="true"></i><span>${e.message}</span></div>`;
  }
}

function renderBacktestResult(d) {
  const el = document.getElementById('backtest-result');
  const pnlCls = d.evalPnl >= 0 ? 'positive' : 'negative';
  const holding = d.holding;

  el.innerHTML = `
    <div class="card">
      <div class="bt-summary-grid">
        <div class="meta-item"><div class="meta-label">${t('capital')}</div><div class="meta-value">$${d.seed.toLocaleString('en-US', {maximumFractionDigits:0})}</div></div>
        <div class="meta-item"><div class="meta-label">${t('totalBuyQty')}</div><div class="meta-value">${d.totalBuyQty.toLocaleString('en-US')} ${t('sh')}</div></div>
        <div class="meta-item"><div class="meta-label">${t('totalSellQty')}</div><div class="meta-value">${d.totalSellQty.toLocaleString('en-US')} ${t('sh')}</div></div>
        <div class="meta-item"><div class="meta-label">${t('holdingQty')}</div><div class="meta-value">${holding.qty.toLocaleString('en-US')} ${t('sh')}</div></div>
        <div class="meta-item"><div class="meta-label">${t('avgPrice')}</div><div class="meta-value">${holding.qty > 0 ? '$' + holding.avgPrice.toFixed(2) : '-'}</div></div>
        <div class="meta-item"><div class="meta-label">${t('buyAmount')}</div><div class="meta-value">$${d.totalBuyAmount.toLocaleString('en-US', {maximumFractionDigits:2})}</div></div>
        <div class="meta-item"><div class="meta-label">${t('sellAmount')}</div><div class="meta-value">$${d.totalSellAmount.toLocaleString('en-US', {maximumFractionDigits:2})}</div></div>
        <div class="meta-item"><div class="meta-label">${t('unrealizedPL')}</div><div class="meta-value ${pnlCls}">${d.evalPnl>=0?'+':''}$${Math.abs(d.evalPnl).toLocaleString('en-US', {maximumFractionDigits:2})}</div></div>
        <div class="meta-item"><div class="meta-label">${t('return')}</div><div class="meta-value ${pnlCls}">${d.returnPct>=0?'+':''}${d.returnPct.toFixed(1)}%</div></div>
        <div class="meta-item"><div class="meta-label">${t('returnOnCapital')}</div><div class="meta-value ${pnlCls}">${d.seedReturnPct>=0?'+':''}${d.seedReturnPct.toFixed(1)}%</div></div>
        <div class="meta-item"><div class="meta-label">${t('targetReturn')}</div><div class="meta-value">${d.targetReturnPct}%</div></div>
        <div class="meta-item"><div class="meta-label">${t('splits')}</div><div class="meta-value">${d.splits}</div></div>
        <div class="meta-item"><div class="meta-label">${t('strategyMDD')}</div><div class="meta-value negative">-${d.mddPct.toFixed(1)}%</div></div>
        <div class="meta-item"><div class="meta-label">${escapeHtml(tv(d.benchmark.label))} ${t('return')}</div><div class="meta-value ${d.benchmark.returnPct>=0?'positive':'negative'}">${d.benchmark.returnPct>=0?'+':''}${d.benchmark.returnPct.toFixed(1)}%</div></div>
        <div class="meta-item"><div class="meta-label">${escapeHtml(tv(d.benchmark.label))} MDD</div><div class="meta-value negative">-${d.benchmark.mddPct.toFixed(1)}%</div></div>
        <div class="meta-item"><div class="meta-label">${t('alphaExcessReturn')}</div><div class="meta-value ${d.alphaPct>=0?'positive':'negative'}">${d.alphaPct>=0?'+':''}${d.alphaPct.toFixed(1)}%p</div></div>
      </div>

      <div class="bt-holding-box">
        <span class="cycle-pill">${d.version.toUpperCase()}</span>
        <span class="cycle-pill">${d.completedCycles} ${t('cyclesCompleted')}</span>
        ${holding.lossCutMode ? `<span class="cycle-pill" style="background:var(--red-bg);color:var(--red);">${t('quarterStopLoss')}</span>` : ''}
        ${holding.qty > 0
          ? ` · ${t('currentlyHolding')}: ${holding.qty} ${t('sh')} @ ${t('avg')} $${holding.avgPrice.toFixed(2)} · ${t('price')} $${holding.currentPrice.toFixed(2)} · ${t('value')} $${holding.value.toLocaleString('en-US',{maximumFractionDigits:2})} (T ${holding.tValue}/${d.splits})`
          : ` · ${t('noPositionAtEnd')}`}
        <div style="font-size:11px;color:var(--text-muted);margin-top:4px;">${t('period')}: ${d.start} – ${d.end}</div>
      </div>

      <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px;margin:14px 0 8px;">
        <div style="font-size:13px;font-weight:600;">${t('returnComparison')} (${t('strategy')} vs. ${escapeHtml(tv(d.benchmark.label))})</div>
        <button class="btn-secondary" onclick="resetReturnChartZoom()" style="padding:4px 10px;font-size:12px;"><i class="ti ti-zoom-reset" aria-hidden="true"></i> ${t('resetZoom')}</button>
      </div>
      <div class="chart-wrap">
        <canvas id="return-chart" role="img" aria-label="${t('returnComparisonChart')}"></canvas>
      </div>
      <div style="font-size:11px;color:var(--text-muted);margin-top:6px;text-align:center;">${t('scrollToZoom')}</div>
    </div>

    <div class="card">
      <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px;margin-bottom:8px;">
        <div style="font-size:13px;font-weight:600;">${escapeHtml(d.ticker)} ${t('priceChartBuySell')}</div>
        <button class="btn-secondary" onclick="resetPriceChartZoom()" style="padding:4px 10px;font-size:12px;"><i class="ti ti-zoom-reset" aria-hidden="true"></i> ${t('resetZoom')}</button>
      </div>
      <div class="chart-wrap">
        <canvas id="price-chart" role="img" aria-label="${escapeHtml(d.ticker)} ${t('priceChart')}"></canvas>
      </div>
      <div style="font-size:11px;color:var(--text-muted);margin-top:6px;text-align:center;">${t('scrollToZoom')}</div>
    </div>

    <div class="pf-table-wrap">
      <table class="pf-table">
        <thead><tr><th>${t('cycle')}</th><th>${t('date')}</th><th>${t('type')}</th><th>${t('price')}</th><th>${t('qty')}</th><th>${t('cumQty')}</th><th>${t('avgPrice')}</th><th>${t('return')}</th><th>${t('note')}</th></tr></thead>
        <tbody>
          ${d.trades.length ? d.trades.map(t2 => `
            <tr>
              <td>${t2.cycle}</td>
              <td>${t2.date}</td>
              <td><span class="${t2.action === 'buy' ? 'negative' : 'positive'}" style="font-weight:600;">${t2.action === 'buy' ? t('buy') : t('sell')}</span></td>
              <td>$${t2.price.toFixed(2)}</td>
              <td>${t2.qty}</td>
              <td>${t2.qtyAfter}</td>
              <td>${t2.avgPriceAfter !== null ? '$' + t2.avgPriceAfter.toFixed(2) : '-'}</td>
              <td class="${t2.returnPctAfter >= 0 ? 'positive' : 'negative'}">${t2.returnPctAfter>=0?'+':''}${t2.returnPctAfter.toFixed(1)}%</td>
              <td style="font-size:12px;color:var(--text-secondary);">${escapeHtml(tv(t2.note))}</td>
            </tr>`).join('') : `<tr><td colspan="9" style="text-align:center;color:var(--text-secondary);">${t('noTradesInPeriod')}</td></tr>`}
        </tbody>
      </table>
    </div>
  `;

  // 두 번의 requestAnimationFrame으로 레이아웃/페인트가 끝난 뒤 그린다(이유는
  // 스크리닝 상세 모달의 같은 패턴 주석 참고 - ResizeObserver 반복 리사이즈로
  // 인한 화면 흔들림 방지).
  requestAnimationFrame(() => requestAnimationFrame(() => {
    drawReturnChart(d.equityCurve, d.benchmark, d.seed);
    drawPriceChart(d.priceCurve, d.trades, d.ticker);
  }));
}

function drawReturnChart(curve, benchmark, seed) {
  const canvas = document.getElementById('return-chart');
  if (!canvas) return;
  if (equityChart) equityChart.destroy();
  const dates = curve.map(p => p.date);
  const toPct = v => (v - seed) / seed * 100;
  const datasets = [{
    label: t('infiniteBuying'),
    data: curve.map((p, i) => ({ x: i, y: toPct(p.value) })),
    borderColor: '#378ADD', backgroundColor: 'rgba(55,138,221,0.12)',
    fill: true, pointRadius: 0, borderWidth: 2, tension: 0.15,
  }];
  if (benchmark?.equityCurve?.length) {
    datasets.push({
      label: tv(benchmark.label),
      data: benchmark.equityCurve.map((p, i) => ({ x: i, y: toPct(p.value) })),
      borderColor: '#97C459', backgroundColor: 'transparent',
      fill: false, pointRadius: 0, borderWidth: 2, borderDash: [5, 4], tension: 0.15,
    });
  }
  equityChart = new Chart(canvas, {
    type: 'line',
    data: { datasets },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: {
        legend: { display: true, position: 'top', labels: { boxWidth: 12, font: { size: 11 } } },
        tooltip: {
          callbacks: {
            title: items => items.length ? (dates[Math.round(items[0].parsed.x)] ?? '') : '',
            label: ctx => ` ${ctx.dataset.label}: ${ctx.parsed.y>=0?'+':''}` + ctx.parsed.y.toFixed(1) + '%',
          },
        },
        zoom: {
          zoom: { wheel: { enabled: true }, pinch: { enabled: true }, mode: 'x' },
          pan: { enabled: false },
          limits: { x: { min: 0, max: Math.max(dates.length - 1, 0), minRange: 5 } },
        },
      },
      scales: {
        y: { ticks: { callback: v => (v>=0?'+':'') + v.toFixed(0) + '%' }, grid: { color: 'rgba(128,128,128,0.1)' } },
        x: {
          type: 'linear', min: 0, max: Math.max(dates.length - 1, 0),
          ticks: { maxTicksLimit: 8, callback: v => dates[Math.round(v)] ?? '' },
          grid: { display: false },
        },
      },
    },
  });
  attachChartPan(equityChart, canvas, equityChartPanState);
}

function resetReturnChartZoom() {
  if (equityChart) equityChart.resetZoom();
}

function drawPriceChart(priceCurve, trades, ticker) {
  const canvas = document.getElementById('price-chart');
  if (!canvas) return;
  if (priceChartBt) priceChartBt.destroy();

  const dates = priceCurve.map(p => p.date);
  const dateIndex = new Map(dates.map((d, i) => [d, i]));
  const buyPoints = [], sellPoints = [];
  trades.forEach(t4 => {
    const idx = dateIndex.get(t4.date);
    if (idx === undefined) return;
    (t4.action === 'buy' ? buyPoints : sellPoints).push({ x: idx, y: t4.price });
  });

  priceChartBt = new Chart(canvas, {
    type: 'line',
    data: {
      datasets: [
        {
          label: `${ticker} ${t('close')}`, data: priceCurve.map((p, i) => ({ x: i, y: p.close })),
          borderColor: '#888780', backgroundColor: 'transparent',
          fill: false, pointRadius: 0, borderWidth: 1.5, tension: 0.1, order: 3,
        },
        {
          label: t('buy'), data: buyPoints, type: 'scatter',
          backgroundColor: '#E24B4A', borderColor: '#E24B4A',
          pointRadius: 4, pointStyle: 'triangle', order: 1,
        },
        {
          label: t('sell'), data: sellPoints, type: 'scatter',
          backgroundColor: '#378ADD', borderColor: '#378ADD',
          pointRadius: 4, pointStyle: 'rectRot', order: 2,
        },
      ],
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: {
        legend: { display: true, position: 'top', labels: { boxWidth: 12, font: { size: 11 } } },
        tooltip: {
          callbacks: {
            title: items => items.length ? (dates[Math.round(items[0].parsed.x)] ?? '') : '',
            label: ctx => ` ${ctx.dataset.label}: $` + ctx.parsed.y.toFixed(2),
          },
        },
        zoom: {
          zoom: { wheel: { enabled: true }, pinch: { enabled: true }, mode: 'x' },
          pan: { enabled: false },
          limits: { x: { min: 0, max: Math.max(dates.length - 1, 0), minRange: 5 } },
        },
      },
      scales: {
        y: { ticks: { callback: v => '$' + v.toFixed(0) }, grid: { color: 'rgba(128,128,128,0.1)' } },
        x: {
          type: 'linear', min: 0, max: Math.max(dates.length - 1, 0),
          ticks: { maxTicksLimit: 8, callback: v => dates[Math.round(v)] ?? '' },
          grid: { display: false },
        },
      },
    },
  });
  attachChartPan(priceChartBt, canvas, priceChartBtPanState);
}

function resetPriceChartZoom() {
  if (priceChartBt) priceChartBt.resetZoom();
}

// ─── 국내(KRX) 단기/스윙 백테스트 ─────────────────────────────────────────────

let krSwingReturnChart = null;
let krSwingPriceChart = null;
const krSwingReturnPanState = { cleanup: null };
const krSwingPricePanState = { cleanup: null };

const KR_SWING_STRATEGY_KEY = {
  volatility_breakout: 'stratVolBreakout',
  box_breakout: 'stratRangeBreakout',
  ma_pullback: 'stratMaPullback',
  combo: 'stratCombo',
};
function krSwingStrategyLabel(strategy) {
  const key = KR_SWING_STRATEGY_KEY[strategy];
  return key ? t(key) : strategy;
}

function initKrSwingDates() {
  const startEl = document.getElementById('ks-start');
  const endEl = document.getElementById('ks-end');
  if (!endEl.value) endEl.value = new Date().toISOString().slice(0, 10);
  if (!startEl.value) startEl.value = '2020-01-01';
}

function onKrSwingStrategyChange() {
  const strategy = document.getElementById('ks-strategy').value;
  document.querySelectorAll('.ks-params').forEach(el => {
    el.style.display = el.id === `ks-params-${strategy}` ? '' : 'none';
  });
}

// ─── 국내 스윙 종목명 자동완성 ─────────────────────────────────────────────────

let krSwingSelectedName = '삼성전자';
let krSwingSuggestions = [];
let krSwingActiveSuggestionIndex = -1;
let krSwingSearchTimer = null;
let krSwingSearchSeq = 0;

function onKrSwingCodeInput(value) {
  clearTimeout(krSwingSearchTimer);
  const q = value.trim();
  if (!q) { closeKrSwingAutocomplete(); return; }
  krSwingSearchTimer = setTimeout(() => runKrSwingSearch(q), 150);
}

async function runKrSwingSearch(q) {
  const seq = ++krSwingSearchSeq;
  let data;
  try {
    data = await api('GET', `/api/kr-swing/search-stocks?q=${encodeURIComponent(q)}`);
  } catch (e) {
    return;
  }
  if (seq !== krSwingSearchSeq) return; // 더 최근 검색 응답이 이미 왔으면 이 결과는 버린다
  krSwingSuggestions = data.results || [];
  krSwingActiveSuggestionIndex = -1;
  renderKrSwingAutocomplete();
}

function renderKrSwingAutocomplete() {
  const list = document.getElementById('ks-autocomplete-list');
  if (!list) return;
  if (!krSwingSuggestions.length) {
    list.innerHTML = `<div class="ks-autocomplete-empty">${t('noMatchingStocks')}</div>`;
    list.style.display = 'block';
    return;
  }
  list.innerHTML = krSwingSuggestions.map((s, i) => `
    <div class="ks-autocomplete-item ${i === krSwingActiveSuggestionIndex ? 'active' : ''}"
         data-code="${escapeHtml(s.code)}" data-name="${escapeHtml(s.name)}"
         onmousedown="selectKrSwingStock(this.dataset.code, this.dataset.name)">
      <span>${escapeHtml(s.name)} <span style="color:var(--text-muted);">${escapeHtml(s.code)}</span></span>
      <span class="ks-ac-market">${escapeHtml(s.market)}</span>
    </div>`).join('');
  list.style.display = 'block';
}

function selectKrSwingStock(code, name) {
  document.getElementById('ks-code').value = code;
  document.getElementById('ks-code-input').value = name;
  krSwingSelectedName = name;
  closeKrSwingAutocomplete();
}

function closeKrSwingAutocomplete() {
  const list = document.getElementById('ks-autocomplete-list');
  if (list) list.style.display = 'none';
  krSwingActiveSuggestionIndex = -1;
}

function onKrSwingCodeKeydown(event) {
  const list = document.getElementById('ks-autocomplete-list');
  const visible = list && list.style.display !== 'none';
  if (!visible || !krSwingSuggestions.length) return;
  if (event.key === 'ArrowDown') {
    event.preventDefault();
    krSwingActiveSuggestionIndex = Math.min(krSwingActiveSuggestionIndex + 1, krSwingSuggestions.length - 1);
    renderKrSwingAutocomplete();
  } else if (event.key === 'ArrowUp') {
    event.preventDefault();
    krSwingActiveSuggestionIndex = Math.max(krSwingActiveSuggestionIndex - 1, 0);
    renderKrSwingAutocomplete();
  } else if (event.key === 'Enter') {
    if (krSwingActiveSuggestionIndex >= 0) {
      event.preventDefault();
      const s = krSwingSuggestions[krSwingActiveSuggestionIndex];
      selectKrSwingStock(s.code, s.name);
    }
  } else if (event.key === 'Escape') {
    closeKrSwingAutocomplete();
  }
}

function getKrSwingParams(strategy) {
  if (strategy === 'volatility_breakout') {
    return {
      k: document.getElementById('ks-vb-k').value,
      holdDays: document.getElementById('ks-vb-hold').value,
      stopLossPct: document.getElementById('ks-vb-stop').value,
    };
  }
  if (strategy === 'box_breakout') {
    return {
      entryN: document.getElementById('ks-bb-entry').value,
      exitN: document.getElementById('ks-bb-exit').value,
      stopLossPct: document.getElementById('ks-bb-stop').value,
    };
  }
  if (strategy === 'ma_pullback') {
    return {
      longMa: document.getElementById('ks-ma-long').value,
      shortMa: document.getElementById('ks-ma-short').value,
      stopLossPct: document.getElementById('ks-ma-stop').value,
      targetPct: document.getElementById('ks-ma-target').value,
    };
  }
  return {
    trendMa: document.getElementById('ks-combo-trend').value,
    pullbackMa: document.getElementById('ks-combo-pullback').value,
    breakoutK: document.getElementById('ks-combo-k').value,
    stopLossPct: document.getElementById('ks-combo-stop').value,
    trailingExitN: document.getElementById('ks-combo-trailing').value,
    targetPct: document.getElementById('ks-combo-target').value,
  };
}

function formatKrw(v) {
  return '₩' + Math.round(v).toLocaleString('en-US');
}

async function runKrSwingBacktest() {
  const strategy = document.getElementById('ks-strategy').value;
  const codeInputEl = document.getElementById('ks-code-input');
  const code = document.getElementById('ks-code').value.trim();
  const start = document.getElementById('ks-start').value;
  const end = document.getElementById('ks-end').value;
  const seed = parseFloat(document.getElementById('ks-seed').value);
  const el = document.getElementById('krswing-result');

  if (!code || codeInputEl.value.trim() !== krSwingSelectedName) {
    alert('목록에서 종목을 선택하세요');
    return;
  }
  if (!start || !end) { alert('시작일과 종료일을 입력하세요'); return; }
  if (!seed || seed <= 0) { alert('시드를 입력하세요'); return; }

  const params = getKrSwingParams(strategy);
  el.innerHTML = `<div class="loading-msg"><i class="ti ti-loader-2" aria-hidden="true"></i>${t('runningBacktestFor').replace('{ticker}', escapeHtml(krSwingSelectedName))}</div>`;

  try {
    const data = await api('POST', '/api/kr-swing/backtest', { strategy, code, start, end, seed, params });
    renderKrSwingResult(data);
  } catch (e) {
    el.innerHTML = `<div class="error-msg"><i class="ti ti-alert-circle" aria-hidden="true"></i><span>${escapeHtml(e.message)}</span></div>`;
  }
}

function renderKrSwingResult(d) {
  const el = document.getElementById('krswing-result');
  const pnlCls = d.evalPnl >= 0 ? 'positive' : 'negative';
  const holding = d.holding;

  el.innerHTML = `
    <div class="card">
      <div class="bt-summary-grid">
        <div class="meta-item"><div class="meta-label">${t('capital')}</div><div class="meta-value">${formatKrw(d.seed)}</div></div>
        <div class="meta-item"><div class="meta-label">${t('stock')}</div><div class="meta-value">${escapeHtml(d.ticker)} (${escapeHtml(d.market)})</div></div>
        <div class="meta-item"><div class="meta-label">${t('trades')}</div><div class="meta-value">${d.tradeCount}</div></div>
        <div class="meta-item"><div class="meta-label">${t('winRate')}</div><div class="meta-value">${d.winCount}W / ${d.tradeCount} (${d.winRatePct.toFixed(1)}%)</div></div>
        <div class="meta-item"><div class="meta-label">${t('avgHoldDays')}</div><div class="meta-value">${d.avgHoldDays.toFixed(1)}d</div></div>
        <div class="meta-item"><div class="meta-label">${t('buyAmount')}</div><div class="meta-value">${formatKrw(d.totalBuyAmount)}</div></div>
        <div class="meta-item"><div class="meta-label">${t('sellAmount')}</div><div class="meta-value">${formatKrw(d.totalSellAmount)}</div></div>
        <div class="meta-item"><div class="meta-label">${t('unrealizedPL')}</div><div class="meta-value ${pnlCls}">${d.evalPnl>=0?'+':''}${formatKrw(Math.abs(d.evalPnl))}</div></div>
        <div class="meta-item"><div class="meta-label">${t('returnOnCapital')}</div><div class="meta-value ${pnlCls}">${d.seedReturnPct>=0?'+':''}${d.seedReturnPct.toFixed(1)}%</div></div>
        <div class="meta-item"><div class="meta-label">${t('strategyMDD')}</div><div class="meta-value negative">-${d.mddPct.toFixed(1)}%</div></div>
        <div class="meta-item"><div class="meta-label">${escapeHtml(tv(d.benchmark.label))} ${t('return')}</div><div class="meta-value ${d.benchmark.returnPct>=0?'positive':'negative'}">${d.benchmark.returnPct>=0?'+':''}${d.benchmark.returnPct.toFixed(1)}%</div></div>
        <div class="meta-item"><div class="meta-label">${t('alphaExcessReturn')}</div><div class="meta-value ${d.alphaPct>=0?'positive':'negative'}">${d.alphaPct>=0?'+':''}${d.alphaPct.toFixed(1)}%p</div></div>
        <div class="meta-item"><div class="meta-label">${t('cagr')}</div><div class="meta-value ${d.cagrPct>=0?'positive':'negative'}">${d.cagrPct>=0?'+':''}${d.cagrPct.toFixed(1)}%</div></div>
        <div class="meta-item"><div class="meta-label">${t('calmarRatio')}</div><div class="meta-value">${d.calmarRatio !== null && d.calmarRatio !== undefined ? d.calmarRatio.toFixed(2) : '-'}</div></div>
        <div class="meta-item"><div class="meta-label">${t('profitLossRatio')}</div><div class="meta-value">${d.profitLossRatio !== null && d.profitLossRatio !== undefined ? d.profitLossRatio.toFixed(2) : (d.winCount > 0 ? '∞' : '-')}</div></div>
      </div>

      <div class="bt-holding-box">
        <span class="cycle-pill">${escapeHtml(krSwingStrategyLabel(d.strategy))}</span>
        ${holding.qty > 0
          ? ` · ${t('currentlyHolding')}: ${holding.qty.toLocaleString('en-US')} ${t('sh')} @ ${t('avg')} ${formatKrw(holding.avgPrice)} · ${t('price')} ${formatKrw(holding.currentPrice)} · ${t('value')} ${formatKrw(holding.value)}`
          : ` · ${t('noPositionAtEnd')}`}
        <div style="font-size:11px;color:var(--text-muted);margin-top:4px;">${t('period')}: ${d.start} – ${d.end}</div>
      </div>

      <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px;margin:14px 0 8px;">
        <div style="font-size:13px;font-weight:600;">${t('returnComparison')} (${t('strategy')} vs. ${escapeHtml(tv(d.benchmark.label))})</div>
        <button class="btn-secondary" onclick="resetKrSwingReturnZoom()" style="padding:4px 10px;font-size:12px;"><i class="ti ti-zoom-reset" aria-hidden="true"></i> ${t('resetZoom')}</button>
      </div>
      <div class="chart-wrap">
        <canvas id="krswing-return-chart" role="img" aria-label="${t('returnComparisonChart')}"></canvas>
      </div>
      <div style="font-size:11px;color:var(--text-muted);margin-top:6px;text-align:center;">${t('scrollToZoom')}</div>
    </div>

    <div class="card">
      <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px;margin-bottom:8px;">
        <div style="font-size:13px;font-weight:600;">${escapeHtml(d.ticker)} ${t('priceChartBuySell')}</div>
        <button class="btn-secondary" onclick="resetKrSwingPriceZoom()" style="padding:4px 10px;font-size:12px;"><i class="ti ti-zoom-reset" aria-hidden="true"></i> ${t('resetZoom')}</button>
      </div>
      <div class="chart-wrap">
        <canvas id="krswing-price-chart" role="img" aria-label="${escapeHtml(d.ticker)} ${t('priceChart')}"></canvas>
      </div>
      <div style="font-size:11px;color:var(--text-muted);margin-top:6px;text-align:center;">${t('scrollToZoom')}</div>
    </div>

    <div class="pf-table-wrap">
      <table class="pf-table">
        <thead><tr><th>${t('date')}</th><th>${t('type')}</th><th>${t('price')}</th><th>${t('qty')}</th><th>${t('plPercent')}</th><th>${t('note')}</th></tr></thead>
        <tbody>
          ${d.trades.length ? d.trades.map(t3 => `
            <tr>
              <td>${t3.date}</td>
              <td><span class="${t3.action === 'buy' ? 'negative' : 'positive'}" style="font-weight:600;">${t3.action === 'buy' ? t('buy') : t('sell')}</span></td>
              <td>${formatKrw(t3.price)}</td>
              <td>${t3.qty.toLocaleString('en-US')}</td>
              <td>${t3.pnlPct !== undefined ? `<span class="${t3.pnlPct >= 0 ? 'positive' : 'negative'}">${t3.pnlPct>=0?'+':''}${t3.pnlPct.toFixed(1)}%</span>` : '-'}</td>
              <td style="font-size:12px;color:var(--text-secondary);">${escapeHtml(tv(t3.note))}</td>
            </tr>`).join('') : `<tr><td colspan="6" style="text-align:center;color:var(--text-secondary);">${t('noTradesInPeriod')}</td></tr>`}
        </tbody>
      </table>
    </div>
  `;

  // 두 번의 requestAnimationFrame으로 레이아웃/페인트가 끝난 뒤 그린다(이유는
  // 스크리닝 상세 모달의 같은 패턴 주석 참고 - ResizeObserver 반복 리사이즈로
  // 인한 화면 흔들림 방지).
  requestAnimationFrame(() => requestAnimationFrame(() => {
    drawKrSwingReturnChart(d.equityCurve, d.benchmark, d.seed);
    drawKrSwingPriceChart(d.priceCurve, d.trades, d.ticker);
  }));
}

function drawKrSwingReturnChart(curve, benchmark, seed) {
  const canvas = document.getElementById('krswing-return-chart');
  if (!canvas) return;
  if (krSwingReturnChart) krSwingReturnChart.destroy();
  const dates = curve.map(p => p.date);
  const toPct = v => (v - seed) / seed * 100;
  const datasets = [{
    label: t('strategy'),
    data: curve.map((p, i) => ({ x: i, y: toPct(p.value) })),
    borderColor: '#378ADD', backgroundColor: 'rgba(55,138,221,0.12)',
    fill: true, pointRadius: 0, borderWidth: 2, tension: 0.15,
  }];
  if (benchmark?.equityCurve?.length) {
    datasets.push({
      label: tv(benchmark.label),
      data: benchmark.equityCurve.map((p, i) => ({ x: i, y: toPct(p.value) })),
      borderColor: '#97C459', backgroundColor: 'transparent',
      fill: false, pointRadius: 0, borderWidth: 2, borderDash: [5, 4], tension: 0.15,
    });
  }
  krSwingReturnChart = new Chart(canvas, {
    type: 'line',
    data: { datasets },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: {
        legend: { display: true, position: 'top', labels: { boxWidth: 12, font: { size: 11 } } },
        tooltip: {
          callbacks: {
            title: items => items.length ? (dates[Math.round(items[0].parsed.x)] ?? '') : '',
            label: ctx => ` ${ctx.dataset.label}: ${ctx.parsed.y>=0?'+':''}` + ctx.parsed.y.toFixed(1) + '%',
          },
        },
        zoom: {
          zoom: { wheel: { enabled: true }, pinch: { enabled: true }, mode: 'x' },
          pan: { enabled: false },
          limits: { x: { min: 0, max: Math.max(dates.length - 1, 0), minRange: 5 } },
        },
      },
      scales: {
        y: { ticks: { callback: v => (v>=0?'+':'') + v.toFixed(0) + '%' }, grid: { color: 'rgba(128,128,128,0.1)' } },
        x: {
          type: 'linear', min: 0, max: Math.max(dates.length - 1, 0),
          ticks: { maxTicksLimit: 8, callback: v => dates[Math.round(v)] ?? '' },
          grid: { display: false },
        },
      },
    },
  });
  attachChartPan(krSwingReturnChart, canvas, krSwingReturnPanState);
}

function resetKrSwingReturnZoom() {
  if (krSwingReturnChart) krSwingReturnChart.resetZoom();
}

function drawKrSwingPriceChart(priceCurve, trades, ticker) {
  const canvas = document.getElementById('krswing-price-chart');
  if (!canvas) return;
  if (krSwingPriceChart) krSwingPriceChart.destroy();

  const dates = priceCurve.map(p => p.date);
  const dateIndex = new Map(dates.map((d, i) => [d, i]));
  const buyPoints = [], sellPoints = [];
  trades.forEach(t => {
    const idx = dateIndex.get(t.date);
    if (idx === undefined) return;
    (t.action === 'buy' ? buyPoints : sellPoints).push({ x: idx, y: t.price });
  });

  krSwingPriceChart = new Chart(canvas, {
    type: 'line',
    data: {
      datasets: [
        {
          label: `${ticker} ${t('close')}`, data: priceCurve.map((p, i) => ({ x: i, y: p.close })),
          borderColor: '#888780', backgroundColor: 'transparent',
          fill: false, pointRadius: 0, borderWidth: 1.5, tension: 0.1, order: 3,
        },
        {
          label: t('buy'), data: buyPoints, type: 'scatter',
          backgroundColor: '#E24B4A', borderColor: '#E24B4A',
          pointRadius: 4, pointStyle: 'triangle', order: 1,
        },
        {
          label: t('sell'), data: sellPoints, type: 'scatter',
          backgroundColor: '#378ADD', borderColor: '#378ADD',
          pointRadius: 4, pointStyle: 'rectRot', order: 2,
        },
      ],
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: {
        legend: { display: true, position: 'top', labels: { boxWidth: 12, font: { size: 11 } } },
        tooltip: {
          callbacks: {
            title: items => items.length ? (dates[Math.round(items[0].parsed.x)] ?? '') : '',
            label: ctx => ` ${ctx.dataset.label}: ` + formatKrw(ctx.parsed.y),
          },
        },
        zoom: {
          zoom: { wheel: { enabled: true }, pinch: { enabled: true }, mode: 'x' },
          pan: { enabled: false },
          limits: { x: { min: 0, max: Math.max(dates.length - 1, 0), minRange: 5 } },
        },
      },
      scales: {
        y: { ticks: { callback: v => Number(v).toLocaleString('en-US') }, grid: { color: 'rgba(128,128,128,0.1)' } },
        x: {
          type: 'linear', min: 0, max: Math.max(dates.length - 1, 0),
          ticks: { maxTicksLimit: 8, callback: v => dates[Math.round(v)] ?? '' },
          grid: { display: false },
        },
      },
    },
  });
  attachChartPan(krSwingPriceChart, canvas, krSwingPricePanState);
}

function resetKrSwingPriceZoom() {
  if (krSwingPriceChart) krSwingPriceChart.resetZoom();
}

// ─── 국내 퀀트(재무지표 팩터) 스크리닝/백테스트 ───────────────────────────────

let krQuantChart = null;
const krQuantPanState = { cleanup: null };

function initKrQuantTab() {
  loadKrQuantStatus();
  runKrQuantScreen();
}

async function loadKrQuantStatus() {
  const el = document.getElementById('krquant-status');
  if (!el) return;
  try {
    const data = await api('GET', '/api/kr-quant/status');
    const priceInfo = data.priceCacheReady
      ? `${t('priceCacheReady')} ${data.priceCacheCount.toLocaleString('en-US')} ${t('stocksUnit')} (${t('updated')} ${Math.round(data.priceCacheAgeSeconds / 60)}${t('minutesAgo')})`
      : t('priceCacheWarming');
    el.innerHTML = `<i class="ti ti-database" aria-hidden="true"></i> <span>${t('fundamentalsLoaded')} ${data.stockCount.toLocaleString('en-US')} ${t('stocksUnit')} (${data.fundamentalRows.toLocaleString('en-US')} ${t('recordsUnit')}). ${priceInfo}.</span>`;
  } catch (e) {
    el.innerHTML = `<i class="ti ti-alert-circle" aria-hidden="true"></i> <span>${escapeHtml(e.message)}</span>`;
  }
}

async function runKrQuantScreen() {
  const el = document.getElementById('krquant-screen-result');
  if (!el) return;
  const topN = parseInt(document.getElementById('kq-screen-topn').value, 10) || 20;
  const minMarketCap = (parseFloat(document.getElementById('kq-screen-mcap').value) || 0) * 100000000;

  el.innerHTML = `<div class="loading-msg"><i class="ti ti-loader-2" aria-hidden="true"></i>${t('screening')}</div>`;
  try {
    const data = await api('GET', `/api/kr-quant/screen?topN=${topN}&minMarketCap=${minMarketCap}`);
    renderKrQuantScreenResult(data);
  } catch (e) {
    el.innerHTML = `<div class="error-msg"><i class="ti ti-alert-circle" aria-hidden="true"></i><span>${escapeHtml(e.message)}</span></div>`;
  }
}

function renderKrQuantScreenResult(data) {
  const el = document.getElementById('krquant-screen-result');
  const picks = data.picks || [];
  if (!picks.length) {
    el.innerHTML = `<div class="empty-state" style="padding:1.5rem;"><p>${t('noStocksMatchCriteria')}</p></div>`;
    return;
  }
  el.innerHTML = `
    <div style="font-size:11px;color:var(--text-muted);margin-bottom:8px;">${t('asOf')}: ${escapeHtml(data.date)}</div>
    <div class="pf-table-wrap">
      <table class="pf-table">
        <thead><tr><th>${t('rank')}</th><th>${t('name')}</th><th>${t('code')}</th><th>P/E</th><th>ROE</th><th>${t('marketCap')}</th><th>${t('fiscalYear')}</th></tr></thead>
        <tbody>
          ${picks.map(p => `
            <tr>
              <td>${p.combinedRank}</td>
              <td>${escapeHtml(p.name)}</td>
              <td>${escapeHtml(p.code)}</td>
              <td>${p.per.toFixed(2)}</td>
              <td>${p.roe.toFixed(2)}%</td>
              <td>${formatKrw(p.marketCap)}</td>
              <td>${escapeHtml(p.bsnsYear)}</td>
            </tr>`).join('')}
        </tbody>
      </table>
    </div>`;
}

async function runKrQuantBacktest() {
  const startYear = parseInt(document.getElementById('kq-bt-start').value, 10);
  const endYear = parseInt(document.getElementById('kq-bt-end').value, 10);
  const seed = parseFloat(document.getElementById('kq-bt-seed').value);
  const topN = parseInt(document.getElementById('kq-bt-topn').value, 10);
  const minMarketCap = (parseFloat(document.getElementById('kq-bt-mcap').value) || 0) * 100000000;
  const el = document.getElementById('krquant-result');

  if (!startYear || !endYear || startYear >= endYear) { alert('연도 범위를 확인하세요'); return; }
  if (!seed || seed <= 0) { alert('시드를 입력하세요'); return; }
  if (!topN || topN <= 0) { alert('종목 수를 확인하세요'); return; }

  // 전체 시장의 과거 시점 가격을 조회해야 해서 몇 분씩 걸릴 수 있어, 서버가
  // 요청 안에서 바로 계산하지 않고 작업(job)만 만들어 즉시 id를 돌려준다.
  // 여기서는 그 작업이 끝날 때까지 몇 초 간격으로 상태를 확인(폴링)한다.
  el.innerHTML = `<div class="loading-msg"><i class="ti ti-loader-2" aria-hidden="true"></i>${t('runningQuantBacktest')}</div>`;
  try {
    const { jobId } = await api('POST', '/api/kr-quant/backtest', { startYear, endYear, seed, topN, minMarketCap });
    await pollKrQuantBacktestJob(jobId, el);
  } catch (e) {
    el.innerHTML = `<div class="error-msg"><i class="ti ti-alert-circle" aria-hidden="true"></i><span>${escapeHtml(e.message)}</span></div>`;
  }
}

async function pollKrQuantBacktestJob(jobId, el) {
  const maxAttempts = 300; // 4초 간격 최대 20분 (실측: 7개 리밸런싱 시점 기준 약 10분)
  for (let attempt = 1; attempt <= maxAttempts; attempt++) {
    await new Promise(r => setTimeout(r, 4000));
    let data;
    try {
      data = await api('GET', `/api/kr-quant/backtest/${jobId}`);
    } catch (e) {
      el.innerHTML = `<div class="error-msg"><i class="ti ti-alert-circle" aria-hidden="true"></i><span>${escapeHtml(e.message)}</span></div>`;
      return;
    }
    if (data.status === 'done') {
      renderKrQuantBacktestResult(data.result);
      return;
    }
    if (data.status === 'error') {
      el.innerHTML = `<div class="error-msg"><i class="ti ti-alert-circle" aria-hidden="true"></i><span>${escapeHtml(data.error || '백테스트 중 오류가 발생했습니다')}</span></div>`;
      return;
    }
  }
  el.innerHTML = `<div class="error-msg"><i class="ti ti-alert-circle" aria-hidden="true"></i><span>백테스트가 예상보다 오래 걸리고 있습니다. 잠시 후 다시 시도해주세요.</span></div>`;
}

function renderKrQuantBacktestResult(d) {
  const el = document.getElementById('krquant-result');
  const pnlCls = d.totalReturnPct >= 0 ? 'positive' : 'negative';

  el.innerHTML = `
    <div class="card">
      <div class="bt-summary-grid">
        <div class="meta-item"><div class="meta-label">${t('capital')}</div><div class="meta-value">${formatKrw(d.seed)}</div></div>
        <div class="meta-item"><div class="meta-label">${t('finalValue')}</div><div class="meta-value">${formatKrw(d.finalValue)}</div></div>
        <div class="meta-item"><div class="meta-label">${t('totalReturn')}</div><div class="meta-value ${pnlCls}">${d.totalReturnPct>=0?'+':''}${d.totalReturnPct.toFixed(1)}%</div></div>
        <div class="meta-item"><div class="meta-label">MDD</div><div class="meta-value negative">-${d.mddPct.toFixed(1)}%</div></div>
        <div class="meta-item"><div class="meta-label">${escapeHtml(tv(d.benchmark.label))}</div><div class="meta-value ${(d.benchmark.returnPct ?? 0) >= 0 ? 'positive' : 'negative'}">${d.benchmark.returnPct !== null && d.benchmark.returnPct !== undefined ? (d.benchmark.returnPct >= 0 ? '+' : '') + d.benchmark.returnPct.toFixed(1) + '%' : '-'}</div></div>
        <div class="meta-item"><div class="meta-label">${t('rebalances')}</div><div class="meta-value">${d.rebalanceDates.length}</div></div>
      </div>
      <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px;margin:14px 0 8px;">
        <div style="font-size:13px;font-weight:600;">${t('equityCurve')} (${t('strategy')} vs. ${escapeHtml(tv(d.benchmark.label))})</div>
        <button class="btn-secondary" onclick="resetKrQuantZoom()" style="padding:4px 10px;font-size:12px;"><i class="ti ti-zoom-reset" aria-hidden="true"></i> ${t('resetZoom')}</button>
      </div>
      <div class="chart-wrap">
        <canvas id="krquant-chart" role="img" aria-label="Equity curve chart"></canvas>
      </div>
      <div style="font-size:11px;color:var(--text-muted);margin-top:6px;text-align:center;">${t('scrollZoomDragPan')}</div>
    </div>

    <div class="card">
      <div style="font-size:13px;font-weight:600;margin-bottom:10px;">${t('selectedStocksByRebalanceDate')}</div>
      ${d.picksLog.map(pl => `
        <div style="margin-bottom:14px;">
          <div style="font-size:12px;font-weight:600;color:var(--text-secondary);margin-bottom:6px;">${escapeHtml(pl.date)} (${pl.picks.length} ${t('stocksUnit')})</div>
          <div class="pf-table-wrap">
            <table class="pf-table">
              <thead><tr><th>${t('rank')}</th><th>${t('name')}</th><th>P/E</th><th>ROE</th><th>${t('marketCap')}</th></tr></thead>
              <tbody>
                ${pl.picks.length ? pl.picks.map(p => `
                  <tr><td>${p.combinedRank}</td><td>${escapeHtml(p.name)}</td><td>${p.per.toFixed(2)}</td><td>${p.roe.toFixed(2)}%</td><td>${formatKrw(p.marketCap)}</td></tr>
                `).join('') : `<tr><td colspan="5" style="text-align:center;color:var(--text-secondary);">${t('noStocksSelected')}</td></tr>`}
              </tbody>
            </table>
          </div>
        </div>`).join('')}
    </div>

    <div class="pf-table-wrap">
      <table class="pf-table">
        <thead><tr><th>${t('date')}</th><th>${t('type')}</th><th>${t('name')}</th><th>${t('price')}</th><th>${t('qty')}</th><th>P/L %</th></tr></thead>
        <tbody>
          ${d.trades.length ? d.trades.map(t4 => `
            <tr>
              <td>${t4.date}</td>
              <td><span class="${t4.action === 'buy' ? 'negative' : 'positive'}" style="font-weight:600;">${t4.action === 'buy' ? t('buy') : t('sell')}</span></td>
              <td>${escapeHtml(t4.name)}</td>
              <td>${formatKrw(t4.price)}</td>
              <td>${t4.qty.toLocaleString('en-US')}</td>
              <td>${t4.pnlPct !== undefined ? `<span class="${t4.pnlPct >= 0 ? 'positive' : 'negative'}">${t4.pnlPct>=0?'+':''}${t4.pnlPct.toFixed(1)}%</span>` : '-'}</td>
            </tr>`).join('') : `<tr><td colspan="6" style="text-align:center;color:var(--text-secondary);">${t('noTradeHistory')}</td></tr>`}
        </tbody>
      </table>
    </div>
  `;

  // 두 번의 requestAnimationFrame으로 레이아웃/페인트가 끝난 뒤 그린다(이유는
  // 스크리닝 상세 모달의 같은 패턴 주석 참고 - ResizeObserver 반복 리사이즈로
  // 인한 화면 흔들림 방지).
  requestAnimationFrame(() => requestAnimationFrame(() => drawKrQuantChart(d.equityCurve, d.benchmark, d.seed)));
}

function drawKrQuantChart(curve, benchmark, seed) {
  const canvas = document.getElementById('krquant-chart');
  if (!canvas) return;
  if (krQuantChart) krQuantChart.destroy();
  const dates = curve.map(p => p.date);
  const toPct = v => (v - seed) / seed * 100;
  const datasets = [{
    label: t('quantStrategy'),
    data: curve.map((p, i) => ({ x: i, y: toPct(p.value) })),
    borderColor: '#378ADD', backgroundColor: 'rgba(55,138,221,0.12)',
    fill: true, pointRadius: 3, borderWidth: 2, tension: 0.1,
  }];
  if (benchmark?.equityCurve?.length) {
    datasets.push({
      label: tv(benchmark.label),
      data: benchmark.equityCurve.map((p, i) => ({ x: i, y: toPct(p.value) })),
      borderColor: '#97C459', backgroundColor: 'transparent',
      fill: false, pointRadius: 0, borderWidth: 2, borderDash: [5, 4], tension: 0.1,
    });
  }
  krQuantChart = new Chart(canvas, {
    type: 'line',
    data: { datasets },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: {
        legend: { display: true, position: 'top', labels: { boxWidth: 12, font: { size: 11 } } },
        tooltip: {
          callbacks: {
            title: items => items.length ? (dates[Math.round(items[0].parsed.x)] ?? '') : '',
            label: ctx => ` ${ctx.dataset.label}: ${ctx.parsed.y>=0?'+':''}` + ctx.parsed.y.toFixed(1) + '%',
          },
        },
        zoom: {
          zoom: { wheel: { enabled: true }, pinch: { enabled: true }, mode: 'x' },
          pan: { enabled: false },
          limits: { x: { min: 0, max: Math.max(dates.length - 1, 0), minRange: 1 } },
        },
      },
      scales: {
        y: { ticks: { callback: v => (v>=0?'+':'') + v.toFixed(0) + '%' }, grid: { color: 'rgba(128,128,128,0.1)' } },
        x: {
          type: 'linear', min: 0, max: Math.max(dates.length - 1, 0),
          ticks: { maxTicksLimit: 8, callback: v => dates[Math.round(v)] ?? '' },
          grid: { display: false },
        },
      },
    },
  });
  attachChartPan(krQuantChart, canvas, krQuantPanState);
}

function resetKrQuantZoom() {
  if (krQuantChart) krQuantChart.resetZoom();
}

// ─── 트렌드 템플릿(미너비니 스타일) 스크리닝 ──────────────────────────────────

const TREND_CONDITION_LABELS = [
  ['priceAboveMa150And200'],
  ['ma150AboveMa200'],
  ['ma200Rising'],
  ['ma50AboveMa150And200'],
  ['priceAboveMa50'],
  ['priceAbove52wLowBy30pct'],
  ['priceWithin25pctOf52wHigh'],
  ['rsAboveThreshold'],
];

function ratingClass(r) {
  return r === 'Buy' ? 'buy' : r === 'Sell' ? 'sell' : r === 'Hold' ? 'hold' : '';
}

function initScreenerTab() {
  loadScreenerStatus();
  loadScreenerResults();
  loadScreenerWatchlist();
}

let scrWatchlistSet = new Set();

async function loadScreenerWatchlist() {
  try {
    const data = await api('GET', '/api/screener/watchlist');
    scrWatchlistSet = new Set((data.items || []).map(it => `${it.market}:${it.code}`));
  } catch (e) {
    // 관심종목 로드 실패는 스크리닝 결과 자체를 막을 정도는 아니라 조용히 무시
  }
}

function isInWatchlist(market, code) {
  return scrWatchlistSet.has(`${market}:${code}`);
}

async function toggleScreenerWatchlist(market, code, name, event) {
  if (event) event.stopPropagation();
  const key = `${market}:${code}`;
  try {
    if (scrWatchlistSet.has(key)) {
      await api('DELETE', `/api/screener/watchlist?market=${market}&code=${encodeURIComponent(code)}`);
      scrWatchlistSet.delete(key);
    } else {
      await api('POST', '/api/screener/watchlist', { market, code, name });
      scrWatchlistSet.add(key);
    }
  } catch (e) {
    alert(e.message);
    return;
  }
  renderFilteredScreenerRows();
  const starBtn = document.getElementById('scr-detail-star');
  if (starBtn) updateScrDetailStarButton(starBtn, market, code, name);
}

function updateScrDetailStarButton(btn, market, code) {
  const on = isInWatchlist(market, code);
  btn.classList.toggle('on', on);
  btn.querySelector('i').className = `ti ${on ? 'ti-star-filled' : 'ti-star'}`;
  btn.querySelector('span').textContent = on ? t('addedToWatchlist') : t('addToWatchlist');
}

function jumpToKrSwingBacktest(code, name) {
  closeScreenerDetail();
  switchTab('krswing');
  selectKrSwingStock(code, name);
}

async function loadScreenerStatus() {
  const el = document.getElementById('screener-status');
  if (!el) return;
  const market = document.getElementById('scr-market').value;
  try {
    const data = await api('GET', '/api/screener/status');
    const s = data[market];
    if (!s.ready) {
      el.innerHTML = `<i class="ti ti-loader-2" aria-hidden="true"></i> <span>${t('preparingData')}</span>`;
      return;
    }
    const asOfText = s.asOf ? formatAsOf(s.asOf) : null;
    el.innerHTML = `<i class="ti ti-database" aria-hidden="true"></i> <span>${s.count.toLocaleString('en-US')} ${t('stocksUnit')}${asOfText ? ' · ' + t('asOf') + ' ' + asOfText : ''}</span>`;
  } catch (e) {
    el.innerHTML = `<i class="ti ti-alert-circle" aria-hidden="true"></i> <span>${escapeHtml(e.message)}</span>`;
  }
}

async function loadScreenerResults() {
  const el = document.getElementById('screener-result');
  if (!el) return;
  loadScreenerStatus();
  const market = document.getElementById('scr-market').value;
  const onlyPass = document.getElementById('scr-only-pass').checked;

  el.innerHTML = `<div class="loading-msg"><i class="ti ti-loader-2" aria-hidden="true"></i>${t('loading')}</div>`;
  try {
    const data = await api('GET', `/api/screener/results?market=${market}&onlyPass=${onlyPass}`);
    renderScreenerResults(data);
  } catch (e) {
    el.innerHTML = `<div class="error-msg"><i class="ti ti-alert-circle" aria-hidden="true"></i><span>${escapeHtml(e.message)}</span></div>`;
  }
}

async function forceTrendScreenRefresh() {
  const btn = document.getElementById('scr-force-refresh-btn');
  if (!confirm(t('forceRefreshConfirm') || '트렌드 스크리닝 캐시를 지금 바로 재계산합니다(수 분~십수 분 소요). 진행할까요?')) return;
  if (btn) { btn.disabled = true; }
  try {
    await api('POST', '/api/admin/trend-screen-refresh', { markets: ['KR', 'US'] });
    alert(t('forceRefreshStarted') || '재계산을 시작했습니다. 완료까지 수 분~십수 분 걸리며, 완료되면 새로고침 버튼으로 최신 결과를 확인할 수 있습니다.');
  } catch (e) {
    alert(e.message);
  } finally {
    if (btn) { btn.disabled = false; }
  }
}

function rsBadgeClass(rs) {
  if (rs == null) return 'weak';
  if (rs >= 90) return 'strong';
  if (rs >= 70) return 'mid';
  return 'weak';
}

let scrRawResults = [];
let scrIsUS = false;
let scrPreset = 'all';

const SCR_PRESETS = {
  all: () => true,
  rsStrong: r => r.rsRating != null && r.rsRating >= 90,
  nearHigh: r => r.pctBelow52wHigh != null && r.pctBelow52wHigh <= 10,
  freshBreakout: r => r.pctAbove52wLow != null && r.pctAbove52wLow >= 30 && r.pctAbove52wLow <= 60,
  watchlist: r => isInWatchlist(document.getElementById('scr-market').value, r.code),
};

function renderScreenerResults(data) {
  scrRawResults = data.results || [];
  scrIsUS = data.market === 'US';
  buildFinFilterPanel();
  renderFilteredScreenerRows();
}

// ─── 재무 지표 필터 패널 ────────────────────────────────────────────────────
// 종목 상세 아코디언(buildFinancialAccordion)과 같은 지표 키를 쓰되, 여기서는
// 각 항목을 "값에 접근하는 함수"로 정의해 절대금액(abs)/퍼센트(unit)를
// 구분한다 - 절대금액은 억원(국내)·백만달러(미국) 단위로 입력받아 원 단위로
// 환산해서 비교한다(그대로 원 단위로 입력하게 하면 숫자가 너무 커서 비실용적).
// tiers: 각 지표의 "이상적인 값" 4단계 프리셋. 값 투자·표준 스크리닝(Finviz/GuruFocus류)
// 관행을 참고한 근사 기준이며 업종별 예외는 반영하지 않는다 - 정밀 판단용이 아니라
// 빠른 1차 필터용 버튼이다. 왼쪽→오른쪽은 항상 "수치가 낮음→높음" 순서로 두고
// (부채비율처럼 낮을수록 좋은 지표는 라벨만 반대로 붙는다), 성장률처럼 실제로
// 음수가 나올 수 있는 지표는 그대로 음수 구간을 첫 번째 단계로 둔다.
// 잘 알려진 스크리닝 전략을 한 번의 클릭으로 적용하는 프리셋. 각 전략은 이미
// 이 화면이 계산해둔 필드(stage/rsRating/epsGrowth/peRatio/metrics.*)만으로
// 근사한 것으로, 원 저자의 정확한 기준(예: CANSLIM의 기관 매수세, 실적 서프라이즈
// 등)을 전부 반영하지는 못한다 - 빠른 1차 후보군 추리기용이다.
// vcp_strategy.is_preferred_stock(Python)과 동일한 근사 규칙 - 국내 우선주는
// 종목명이 "우"/"우B"/"우C" 등으로 끝난다(예: 삼성전자우, LG화학우, 두산퓨얼셀1우).
function isPreferredStockName(name) {
  if (!name) return false;
  const tail = name.slice(-3);
  return name.endsWith('우') || tail.includes('우B') || tail.includes('우C');
}

const SCREENER_STRATEGY_PRESETS = [
  {
    key: 'canslim', icon: '🚀', titleKey: 'stratCanslimTitle', noteKey: 'stratCanslimNote',
    predicate: r => r.stage >= 2 && r.epsGrowth != null && r.epsGrowth >= 25 && r.rsRating != null && r.rsRating >= 80,
  },
  {
    key: 'trendtemplate', icon: '🏆', titleKey: 'stratTrendTemplateTitle', noteKey: 'stratTrendTemplateNote',
    predicate: r => !!r.allPass,
  },
  {
    key: 'value', icon: '💎', titleKey: 'stratValueTitle', noteKey: 'stratValueNote',
    predicate: r => r.peRatio != null && r.peRatio <= 18 && r.metrics?.pbr != null && r.metrics.pbr <= 3
      && r.metrics?.roe != null && r.metrics.roe >= 15,
  },
  {
    key: 'rebound', icon: '🌅', titleKey: 'stratReboundTitle', noteKey: 'stratReboundNote',
    predicate: r => r.stage === 1 && r.pctAbove52wLow != null && r.pctAbove52wLow >= 30 && r.pctAbove52wLow <= 60,
  },
  {
    key: 'minervini_v2', icon: '🎯', titleKey: 'stratMinerviniV2Title', noteKey: 'stratMinerviniV2Note',
    predicate: r => !!r.allPass && r.avgTradeValue != null && r.avgTradeValue >= 300_000_000,
  },
  {
    // 트렌드템플릿 통과 여부와 무관(모의투자 "어나니머스"의 실제 신규진입 조건과 동일 -
    // 돈치안 채널 15일 신고가 돌파 + 유동성 최소선). "전체 조건 통과만" 체크를 끄지
    // 않으면 서버가 이미 all_pass=True로만 필터링해서 내려주므로 후보가 크게 줄어든다.
    key: 'anonymous', icon: '🐢', titleKey: 'stratAnonymousTitle', noteKey: 'stratAnonymousNote',
    predicate: r => !isPreferredStockName(r.name) && r.donchianHigh15 != null && r.price > r.donchianHigh15
      && r.avgTradeValue != null && r.avgTradeValue >= 100_000_000,
  },
];

const FIN_FILTER_CATEGORIES = [
  // 'strategy'/'stage' 카테고리는 숫자 범위(items)가 아니라 각각 전략 카드/1~4단계
  // 선택 버튼이라 items를 비워두고 buildFinFilterPanel에서 별도 분기로 렌더링한다.
  { key: 'strategy', icon: '🧭', title: 'Strategy Presets', items: [] },
  { key: 'stage', icon: '🔄', title: 'Stage (Minervini)', items: [] },
  { key: 'bs', icon: '🧾', title: 'Balance Sheet', items: [
    { key: 'totalAssets', label: 'Total Assets', abs: true, get: r => r.metrics?.totalAssets },
    { key: 'totalLiabilities', label: 'Total Liabilities', abs: true, get: r => r.metrics?.totalLiabilities },
    { key: 'totalEquity', label: 'Total Equity', abs: true, get: r => r.metrics?.totalEquity },
    { key: 'equityAttributable', label: 'Equity Attributable to Owners', abs: true, get: r => r.metrics?.equityAttributable },
    { key: 'issuedCapital', label: 'Issued Capital', abs: true, get: r => r.metrics?.issuedCapital },
  ] },
  { key: 'ic', icon: '📄', title: 'Income Statement', items: [
    { key: 'revenue', label: 'Revenue', abs: true, get: r => r.metrics?.revenue },
    { key: 'grossProfit', label: 'Gross Profit', abs: true, get: r => r.metrics?.grossProfit },
    { key: 'operatingIncome', label: 'Operating Income', abs: true, get: r => r.metrics?.operatingIncome },
    { key: 'profitBeforeTax', label: 'Pre-Tax Income', abs: true, get: r => r.metrics?.profitBeforeTax },
    { key: 'netIncome', label: 'Net Income', abs: true, get: r => r.metrics?.netIncome },
    { key: 'netIncomeAttributable', label: 'Net Income Attributable to Owners', abs: true, get: r => r.metrics?.netIncomeAttributable },
  ] },
  { key: 'profit', icon: '📈', title: 'Profitability', items: [
    { key: 'grossMargin', label: 'Gross Margin', unit: '%', get: r => r.metrics?.grossMargin,
      tiers: [{ label: 'Weak', max: 20 }, { label: 'Fair', min: 20, max: 40 }, { label: 'Good', min: 40, max: 60 }, { label: 'Excellent', min: 60 }] },
    { key: 'operatingMargin', label: 'Operating Margin', unit: '%', get: r => r.metrics?.operatingMargin,
      tiers: [{ label: 'Weak', max: 5 }, { label: 'Fair', min: 5, max: 15 }, { label: 'Good', min: 15, max: 25 }, { label: 'Excellent', min: 25 }] },
    { key: 'netMargin', label: 'Net Margin', unit: '%', get: r => r.metrics?.netMargin,
      tiers: [{ label: 'Weak', max: 3 }, { label: 'Fair', min: 3, max: 10 }, { label: 'Good', min: 10, max: 20 }, { label: 'Excellent', min: 20 }] },
    { key: 'roe', label: 'ROE', unit: '%', get: r => r.metrics?.roe,
      tiers: [{ label: 'Weak', max: 5 }, { label: 'Fair', min: 5, max: 15 }, { label: 'Good', min: 15, max: 20 }, { label: 'Excellent', min: 20 }] },
    { key: 'roa', label: 'ROA', unit: '%', get: r => r.metrics?.roa,
      tiers: [{ label: 'Weak', max: 2 }, { label: 'Fair', min: 2, max: 5 }, { label: 'Good', min: 5, max: 10 }, { label: 'Excellent', min: 10 }] },
  ] },
  { key: 'growth', icon: '🌱', title: 'Growth (YoY)', items: [
    { key: 'revenueGrowth', label: 'Revenue Growth', unit: '%', get: r => r.metrics?.revenueGrowth,
      tiers: [{ label: 'Declining', max: 0 }, { label: 'Slow', min: 0, max: 10 }, { label: 'Strong', min: 10, max: 25 }, { label: 'Explosive', min: 25 }] },
    { key: 'opIncomeGrowth', label: 'Operating Income Growth', unit: '%', get: r => r.metrics?.opIncomeGrowth,
      tiers: [{ label: 'Declining', max: 0 }, { label: 'Slow', min: 0, max: 10 }, { label: 'Strong', min: 10, max: 25 }, { label: 'Explosive', min: 25 }] },
    { key: 'epsGrowth', label: 'EPS Growth', unit: '%', get: r => r.epsGrowth,
      tiers: [{ label: 'Declining', max: 0 }, { label: 'Slow', min: 0, max: 10 }, { label: 'Strong', min: 10, max: 25 }, { label: 'Explosive', min: 25 }] },
  ] },
  { key: 'stability', icon: '🛡️', title: 'Stability', items: [
    { key: 'currentRatio', label: 'Current Ratio', unit: '%', get: r => r.metrics?.currentRatio,
      tiers: [{ label: 'Weak', max: 100 }, { label: 'Fair', min: 100, max: 150 }, { label: 'Good', min: 150, max: 200 }, { label: 'Excellent', min: 200 }] },
    { key: 'quickRatio', label: 'Quick Ratio', unit: '%', get: r => r.metrics?.quickRatio,
      tiers: [{ label: 'Weak', max: 50 }, { label: 'Fair', min: 50, max: 100 }, { label: 'Good', min: 100, max: 150 }, { label: 'Excellent', min: 150 }] },
    { key: 'debtRatio', label: 'Debt Ratio', unit: '%', get: r => r.metrics?.debtRatio,
      tiers: [{ label: 'Excellent', max: 30 }, { label: 'Good', min: 30, max: 60 }, { label: 'Fair', min: 60, max: 100 }, { label: 'Weak', min: 100 }] },
    { key: 'netDebtRatio', label: 'Net Debt Ratio', unit: '%', get: r => r.metrics?.netDebtRatio,
      tiers: [{ label: 'Excellent', max: 0 }, { label: 'Good', min: 0, max: 30 }, { label: 'Fair', min: 30, max: 60 }, { label: 'Weak', min: 60 }] },
  ] },
  { key: 'value', icon: '💰', title: 'Valuation', items: [
    { key: 'marketCap', label: 'Market Cap', abs: true, get: r => r.marketCap },
    { key: 'peRatio', label: 'P/E', unit: 'x', get: r => r.peRatio,
      tiers: [{ label: 'Excellent', max: 10 }, { label: 'Good', min: 10, max: 18 }, { label: 'Fair', min: 18, max: 25 }, { label: 'Weak', min: 25 }] },
    { key: 'pbr', label: 'P/B', unit: 'x', get: r => r.metrics?.pbr,
      tiers: [{ label: 'Excellent', max: 1 }, { label: 'Good', min: 1, max: 3 }, { label: 'Fair', min: 3, max: 5 }, { label: 'Weak', min: 5 }] },
    { key: 'psr', label: 'P/S', unit: 'x', get: r => r.metrics?.psr,
      tiers: [{ label: 'Excellent', max: 1 }, { label: 'Good', min: 1, max: 2 }, { label: 'Fair', min: 2, max: 4 }, { label: 'Weak', min: 4 }] },
    { key: 'evEbitda', label: 'EV/EBITDA', unit: 'x', get: r => r.metrics?.evEbitda,
      tiers: [{ label: 'Excellent', max: 8 }, { label: 'Good', min: 8, max: 12 }, { label: 'Fair', min: 12, max: 16 }, { label: 'Weak', min: 16 }] },
    { key: 'dividendYield', label: 'Dividend Yield', unit: '%', get: r => r.dividendYield,
      tiers: [{ label: 'None', max: 0 }, { label: 'Fair', min: 0, max: 2 }, { label: 'Good', min: 2, max: 4 }, { label: 'Excellent', min: 4 }] },
  ] },
];

let scrActiveFilters = {}; // { [itemKey]: {min, max} }
let scrActiveRatings = new Set(); // 컨센서스(투자의견) 다중 선택
let scrActiveStage = null; // 미너비니 단계(1~4) 단일 선택 - 종목당 단계가 하나뿐이라 다중선택은 의미가 없어 단일 선택으로 둔다
let scrActiveStrategy = null; // 전략 프리셋(SCREENER_STRATEGY_PRESETS) 단일 선택
let scrFilterActiveCat = FIN_FILTER_CATEGORIES[0].key; // 지금 하단에 펼쳐진 카테고리

function absScale() { return scrIsUS ? 1e6 : 1e8; }
function absUnitLabel() { return scrIsUS ? '$M' : '₩100M'; }

function finFilterCatList() {
  return scrIsUS ? [...FIN_FILTER_CATEGORIES, { key: 'consensus', icon: '🎯', title: 'Consensus', items: [] }] : FIN_FILTER_CATEGORIES;
}

// tier 프리셋 버튼이 지금 이 필터와 "선택된 상태로 보이는지"는 min/max 값이
// 정확히 일치하는지로 판단한다(직접 입력한 값이 우연히 같아도 같은 걸로 취급 -
// 사용자 입장에서는 결과가 같으니 구분할 필요가 없다).
function isTierActive(key, tier) {
  const cur = scrActiveFilters[key];
  if (!cur) return false;
  return (cur.min ?? null) === (tier.min ?? null) && (cur.max ?? null) === (tier.max ?? null);
}

// tier 버튼에 '약함'/'보통' 같은 등급 이름 대신 실제 구간 숫자를 보여준다 -
// 등급 이름만으로는 기준이 얼마인지 알 수 없다는 피드백에 따른 변경. 언어별로
// "미만"/"이상"의 어순이 달라 단어 대신 기호(</+/~)로 통일해 언어 무관하게 쓴다.
function tierRangeLabel(tier) {
  const min = tier.min ?? null, max = tier.max ?? null;
  if (min == null && max != null) return `<${max}`;
  if (min != null && max == null) return `${min}+`;
  if (min != null && max != null) return `${min}~${max}`;
  return '';
}

function countActiveInCat(cat) {
  if (cat.key === 'consensus') return scrActiveRatings.size;
  if (cat.key === 'stage') return scrActiveStage != null ? 1 : 0;
  if (cat.key === 'strategy') return scrActiveStrategy != null ? 1 : 0;
  return cat.items.filter(it => scrActiveFilters[it.key]).length;
}

function buildFinFilterPanel() {
  const panel = document.getElementById('scr-filter-panel');
  if (!panel) return;
  const cats = finFilterCatList();
  if (!cats.some(c => c.key === scrFilterActiveCat)) scrFilterActiveCat = cats[0].key;
  const activeCat = cats.find(c => c.key === scrFilterActiveCat);

  const tabsHtml = cats.map(cat => {
    const n = countActiveInCat(cat);
    return `
      <button type="button" class="ffcat-tab ${cat.key === scrFilterActiveCat ? 'active' : ''}" onclick="setFinFilterCat('${cat.key}')">
        <span>${cat.icon} ${tv(cat.title)}</span>${n ? `<span class="ffcat-tab-count">${n}</span>` : ''}
      </button>`;
  }).join('');

  const detailHtml = activeCat.key === 'consensus' ? `
    <div class="ffrow-detail">
      <div class="ffrow-head"><span class="fflabel">${tv('Analyst Rating')}</span></div>
      <span class="ffchips">
        ${['Buy', 'Hold', 'Sell'].map(label => `
          <span class="ffchip ${scrActiveRatings.has(label) ? 'selected' : ''}" onclick="toggleFinFilterRating('${label}')">${tv(label)}</span>
        `).join('')}
      </span>
    </div>` : activeCat.key === 'stage' ? `
    <div class="ffrow-detail">
      <div class="ffrow-head"><span class="fflabel">${tv('Trend Stage')}</span></div>
      <span class="ffchips">
        ${[1, 2, 3, 4].map(n => `
          <span class="ffchip ${scrActiveStage === n ? 'selected' : ''}" onclick="toggleFinFilterStage(${n})"><i class="ti ti-arrow-up" aria-hidden="true"></i>${t('stageFilter' + n)}</span>
        `).join('')}
      </span>
    </div>
    <div class="ffnote">${tv("Stage is an approximate classification based on moving-average alignment and position within the 52-week range (it doesn't factor in breakout volume or trend duration).")}</div>` : activeCat.key === 'strategy' ? `
    <div class="ffstrategy-list">
      ${SCREENER_STRATEGY_PRESETS.map(s => `
        <div class="ffstrategy-card ${scrActiveStrategy === s.key ? 'selected' : ''}" onclick="toggleFinFilterStrategy('${s.key}')">
          <div class="ffstrategy-head"><span class="ffstrategy-icon">${s.icon}</span><span class="ffstrategy-title">${t(s.titleKey)}</span></div>
          <div class="ffstrategy-note">${t(s.noteKey)}</div>
        </div>
      `).join('')}
    </div>` : activeCat.items.map(item => {
    const unitLabel = item.abs ? `(${absUnitLabel()})` : item.unit ? `(${item.unit})` : '';
    const cur = scrActiveFilters[item.key] || {};
    const tiersHtml = item.tiers ? `
      <div class="fftiers">
        ${item.tiers.map(tr => `
          <button type="button" class="fftier ${isTierActive(item.key, tr) ? 'selected' : ''}" title="${escapeHtml(tv(tr.label))}" onclick="applyFinTier('${item.key}', ${tr.min ?? 'null'}, ${tr.max ?? 'null'})">${tierRangeLabel(tr)}</button>
        `).join('')}
      </div>` : '';
    return `
      <div class="ffrow-detail">
        <div class="ffrow-head">
          <span class="fflabel">${escapeHtml(tv(item.label))} <span class="ffunit">${unitLabel}</span></span>
          <span class="ffrange">
            <input type="number" placeholder="${t('min')}" data-fkey="${item.key}" data-bound="min" value="${cur.min ?? ''}" onchange="onFinInputChange(this)">
            <span>~</span>
            <input type="number" placeholder="${t('max')}" data-fkey="${item.key}" data-bound="max" value="${cur.max ?? ''}" onchange="onFinInputChange(this)">
          </span>
        </div>
        ${tiersHtml}
      </div>`;
  }).join('');

  panel.innerHTML = `
    <div class="ffpanel-head">
      <span class="ffpanel-title">${tv('Financial Filters')}</span>
      <div class="ffpanel-actions">
        <span class="ffbtn" onclick="resetFinFilters()">${tv('Reset')}</span>
      </div>
    </div>
    ${!scrIsUS ? `<div class="ffnote">${tv("Some metrics (stability, consensus, EV/EBITDA, etc.) aren't available for KR stocks.")}</div>` : ''}
    <div class="ffcat-tabs">${tabsHtml}</div>
    <div class="ffcat-detail">${detailHtml}</div>`;
}

function setFinFilterCat(key) {
  scrFilterActiveCat = key;
  buildFinFilterPanel();
}

function toggleFinFilterPanel() {
  const panel = document.getElementById('scr-filter-panel');
  const btn = document.getElementById('scr-filter-btn');
  const show = panel.style.display === 'none';
  panel.style.display = show ? 'block' : 'none';
  btn.classList.toggle('active', show);
}

// 프리셋(tier) 버튼: 이미 선택된 걸 다시 누르면 그 필터를 끄고(toggle off),
// 아니면 그 구간을 즉시 적용한다 - 직접 입력과 달리 "적용" 버튼 없이 바로 반영된다.
function applyFinTier(key, min, max) {
  const tier = { min: min === null ? null : Number(min), max: max === null ? null : Number(max) };
  if (isTierActive(key, tier)) {
    delete scrActiveFilters[key];
  } else {
    scrActiveFilters[key] = tier;
  }
  buildFinFilterPanel();
  updateFinFilterCount();
  renderFilteredScreenerRows();
}

// 직접 입력(min/max)은 focus를 잃거나 Enter를 눌렀을 때(change 이벤트)만 반영한다 -
// 매 키 입력마다 패널을 다시 그리면 입력 중 포커스가 날아가 버리기 때문이다.
function onFinInputChange(inputEl) {
  const key = inputEl.dataset.fkey;
  const bound = inputEl.dataset.bound;
  const raw = inputEl.value.trim();
  const cur = { ...(scrActiveFilters[key] || {}) };
  if (!raw || Number.isNaN(Number(raw))) delete cur[bound];
  else cur[bound] = Number(raw);
  if (cur.min === undefined && cur.max === undefined) delete scrActiveFilters[key];
  else scrActiveFilters[key] = cur;
  buildFinFilterPanel();
  updateFinFilterCount();
  renderFilteredScreenerRows();
}

function toggleFinFilterRating(label) {
  if (scrActiveRatings.has(label)) scrActiveRatings.delete(label);
  else scrActiveRatings.add(label);
  buildFinFilterPanel();
  updateFinFilterCount();
  renderFilteredScreenerRows();
}

function toggleFinFilterStage(n) {
  scrActiveStage = scrActiveStage === n ? null : n;
  buildFinFilterPanel();
  updateFinFilterCount();
  renderFilteredScreenerRows();
}

function toggleFinFilterStrategy(key) {
  scrActiveStrategy = scrActiveStrategy === key ? null : key;
  buildFinFilterPanel();
  updateFinFilterCount();
  renderFilteredScreenerRows();
}

function resetFinFilters() {
  scrActiveFilters = {};
  scrActiveRatings = new Set();
  scrActiveStage = null;
  scrActiveStrategy = null;
  buildFinFilterPanel();
  updateFinFilterCount();
  renderFilteredScreenerRows();
}

function updateFinFilterCount() {
  const n = Object.keys(scrActiveFilters).length + (scrActiveRatings.size ? 1 : 0) + (scrActiveStage != null ? 1 : 0)
    + (scrActiveStrategy != null ? 1 : 0);
  const badge = document.getElementById('scr-filter-count');
  if (!badge) return;
  badge.textContent = n;
  badge.style.display = n ? '' : 'none';
}

function passesFinFilters(r) {
  const allItems = FIN_FILTER_CATEGORIES.flatMap(c => c.items);
  for (const [key, range] of Object.entries(scrActiveFilters)) {
    const item = allItems.find(it => it.key === key);
    if (!item) continue;
    let v = item.get(r);
    if (v == null) return false;
    if (item.abs) v = v / absScale();
    if (range.min != null && v < range.min) return false;
    if (range.max != null && v > range.max) return false;
  }
  if (scrActiveRatings.size && !scrActiveRatings.has(r.analystRating)) return false;
  if (scrActiveStage != null && (r.stage == null || r.stage < scrActiveStage)) return false;
  if (scrActiveStrategy != null) {
    const strat = SCREENER_STRATEGY_PRESETS.find(s => s.key === scrActiveStrategy);
    if (strat && !strat.predicate(r)) return false;
  }
  return true;
}

function setScreenerPreset(preset) {
  scrPreset = preset;
  document.querySelectorAll('#scr-pills .scr-pill').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.preset === preset);
  });
  renderFilteredScreenerRows();
}

function renderFilteredScreenerRows() {
  const el = document.getElementById('screener-result');
  if (!el) return;
  const query = (document.getElementById('scr-search')?.value || '').trim().toLowerCase();
  const presetFn = SCR_PRESETS[scrPreset] || SCR_PRESETS.all;

  const filtered = scrRawResults.filter(r => {
    if (!presetFn(r)) return false;
    if (!passesFinFilters(r)) return false;
    if (!query) return true;
    return r.name.toLowerCase().includes(query) || r.code.toLowerCase().includes(query);
  });

  const fmtPrice = v => v == null ? '-' : (scrIsUS ? `$${Number(v).toFixed(2)}` : `₩${Math.round(v).toLocaleString('en-US')}`);
  const fmtMarketCap = v => {
    if (v == null) return '-';
    if (scrIsUS) {
      const usd = v * 1e6; // Finnhub 단위: 백만 달러
      if (usd >= 1e12) return `$${(usd / 1e12).toFixed(2)}T`;
      if (usd >= 1e9) return `$${(usd / 1e9).toFixed(2)}B`;
      return `$${(usd / 1e6).toFixed(0)}M`;
    }
    if (v >= 1e12) return `₩${(v / 1e12).toFixed(2)}T`;
    if (v >= 1e9) return `₩${(v / 1e9).toFixed(1)}B`;
    return `₩${(v / 1e6).toFixed(0)}M`;
  };
  const fmtPe = v => v == null ? '-' : Number(v).toFixed(1);
  const fmtPct1 = v => v == null ? '-' : `${v > 0 ? '+' : ''}${Number(v).toFixed(1)}%`;
  const fmtVolume = v => {
    if (v == null) return '-';
    if (v >= 1e9) return `${(v / 1e9).toFixed(1)}B`;
    if (v >= 1e6) return `${(v / 1e6).toFixed(1)}M`;
    if (v >= 1e3) return `${(v / 1e3).toFixed(1)}K`;
    return `${Math.round(v).toLocaleString('en-US')}`;
  };

  if (!scrRawResults.length) {
    el.innerHTML = `<div class="empty-state" style="padding:2.5rem 1.5rem;"><i class="ti ti-filter-off" aria-hidden="true"></i><p>${t('noStocksMatchCriteria')}</p></div>`;
    return;
  }
  if (!filtered.length) {
    el.innerHTML = `<div class="empty-state" style="padding:2.5rem 1.5rem;"><i class="ti ti-search-off" aria-hidden="true"></i><p>${t('noStocksMatchSearchFilter')}</p></div>`;
    return;
  }

  el.innerHTML = `
    <div style="font-size:12px;color:var(--text-muted);margin-bottom:8px;">${filtered.length.toLocaleString('en-US')} ${t('stocksUnit')}${filtered.length !== scrRawResults.length ? ` (${t('ofTotal').replace('{n}', scrRawResults.length.toLocaleString('en-US'))})` : ''}</div>
    <div class="scr-table-outer">
    <div class="scr-table-wrap" id="scr-table-wrap">
      <table class="scr-table">
        <thead>
          <tr>
            <th style="width:32px;"></th>
            <th>${t('name')}</th><th>${t('code')}</th><th>${t('sector')}</th><th style="text-align:right;">${t('price')}</th><th>RS</th><th>${t('stage')}</th><th>${t('conditions')}</th>
            <th style="text-align:right;">${t('volRel')}</th>
            <th style="text-align:right;">${t('marketCap')}</th>
            <th style="text-align:right;">P/E</th>
            <th style="text-align:right;">${t('epsGrowth')}</th>
            <th style="text-align:right;">${t('divYield')}</th>
            <th>${t('analyst')}</th>
            <th style="text-align:right;">${t('vs52wLow')}</th><th style="text-align:right;">${t('vs52wHigh')}</th>
          </tr>
        </thead>
        <tbody>
          ${filtered.map(r => `
            <tr onclick="openScreenerDetail('${escapeHtml(r.code)}')">
              <td onclick="toggleScreenerWatchlist('${scrIsUS ? 'US' : 'KR'}', '${escapeHtml(r.code)}', '${escapeHtml(r.name).replace(/'/g, "\\'")}', event)">
                <i class="ti ${isInWatchlist(scrIsUS ? 'US' : 'KR', r.code) ? 'ti-star-filled scr-star on' : 'ti-star scr-star'}" aria-hidden="true"></i>
              </td>
              <td class="scr-name-cell" title="${escapeHtml(r.name)}">
                <span class="scr-name-text">${escapeHtml(r.name)}</span>
              </td>
              <td class="scr-code-cell">${escapeHtml(r.code)}</td>
              <td title="${escapeHtml(tv(r.industry || ''))}">${r.sector ? `<span class="scr-sector-tag">${escapeHtml(tv(r.sector))}</span>` : '-'}</td>
              <td class="scr-num-cell">${fmtPrice(r.price)}</td>
              <td><span class="scr-rs-badge ${rsBadgeClass(r.rsRating)}">${r.rsRating ?? '-'}</span></td>
              <td>${r.stage ? `<span class="scr-stage-badge stage-${r.stage}" title="${escapeHtml(t('stage' + r.stage))}">${t('stage' + r.stage + 'Short')}</span>` : '-'}</td>
              <td>
                <span class="scr-pass-badge ${r.passCount < 8 ? 'partial' : ''}">${r.passCount}/8</span>
                <span class="scr-dots" onmouseenter="showScrPopover(this, '${escapeHtml(r.code)}')" onmouseleave="hideScrPopover()">
                  ${TREND_CONDITION_LABELS.map(([key]) => `<span class="scr-dot ${r.conditions[key] ? 'on' : ''}"></span>`).join('')}
                </span>
              </td>
              <td class="scr-num-cell">${fmtVolume(r.volume)}${r.relVolume != null ? `<span class="scr-relvol">${r.relVolume.toFixed(2)}x</span>` : ''}</td>
              <td class="scr-num-cell">${fmtMarketCap(r.marketCap)}</td>
              <td class="scr-num-cell">${fmtPe(r.peRatio)}</td>
              <td class="scr-num-cell ${r.epsGrowth > 0 ? 'positive' : ''}" ${!scrIsUS ? `title="발행주식수 변동을 반영하지 않은 순이익 증가율 근사치입니다"` : ''}>${fmtPct1(r.epsGrowth)}</td>
              <td class="scr-num-cell" ${!scrIsUS ? `title="국내 종목은 배당수익률 데이터를 제공하지 않습니다"` : ''}>${r.dividendYield != null ? Number(r.dividendYield).toFixed(2) + '%' : '-'}</td>
              <td ${!scrIsUS ? `title="국내 종목은 애널리스트 레이팅 데이터를 제공하지 않습니다"` : ''}>${r.analystRating ? `<span class="scr-rating-badge ${ratingClass(r.analystRating)}">${tv(r.analystRating)}</span>` : '-'}</td>
              <td class="scr-num-cell ${r.pctAbove52wLow >= 30 ? 'positive' : ''}">${r.pctAbove52wLow != null ? '+' + r.pctAbove52wLow.toFixed(1) + '%' : '-'}</td>
              <td class="scr-num-cell ${r.pctBelow52wHigh <= 25 ? 'positive' : ''}">${r.pctBelow52wHigh != null ? '-' + r.pctBelow52wHigh.toFixed(1) + '%' : '-'}</td>
            </tr>`).join('')}
        </tbody>
      </table>
    </div>
    <div class="scr-scrollbar-thumb" id="scr-scrollbar-thumb"></div>
    </div>`;

  setupScrFloatingScrollbar();
}

function setupScrFloatingScrollbar() {
  const wrap = document.getElementById('scr-table-wrap');
  const thumb = document.getElementById('scr-scrollbar-thumb');
  if (!wrap || !thumb) return;

  const HEAD_H = 38; // thead 높이만큼 막대 시작 위치를 아래로 내려 헤더와 안 겹치게 함
  function update() {
    const trackH = wrap.clientHeight - HEAD_H;
    const ratio = wrap.clientHeight / wrap.scrollHeight;
    if (ratio >= 1 || trackH <= 0) {
      thumb.style.height = '0px';
      return;
    }
    const h = Math.max(24, trackH * ratio);
    const maxTop = trackH - h;
    const scrollRatio = wrap.scrollTop / (wrap.scrollHeight - wrap.clientHeight);
    thumb.style.height = h + 'px';
    thumb.style.top = (HEAD_H + maxTop * scrollRatio) + 'px';
  }
  wrap.addEventListener('scroll', update);
  update();

  // 썸을 드래그해서 직접 스크롤을 움직일 수 있게 한다 - 예전엔 pointer-events:none이라
  // 시각적으로만 표시되고 클릭/드래그가 안 됐다. mousemove/mouseup을 document에 걸되
  // 드래그가 끝나면 바로 제거한다 - 테이블은 필터가 바뀔 때마다 통째로 다시 그려져
  // 이 함수가 매번 재호출되므로, 계속 걸어두면 재호출마다 리스너가 쌓인다.
  thumb.addEventListener('mousedown', e => {
    e.preventDefault();
    const startY = e.clientY;
    const startScrollTop = wrap.scrollTop;
    const trackH = wrap.clientHeight - HEAD_H;
    const maxTop = trackH - thumb.offsetHeight;
    if (maxTop <= 0) return;
    thumb.classList.add('dragging');

    function onMove(ev) {
      const scrollableH = wrap.scrollHeight - wrap.clientHeight;
      const scrollDelta = (ev.clientY - startY) / maxTop * scrollableH;
      wrap.scrollTop = Math.max(0, Math.min(scrollableH, startScrollTop + scrollDelta));
    }
    function onUp() {
      thumb.classList.remove('dragging');
      document.removeEventListener('mousemove', onMove);
      document.removeEventListener('mouseup', onUp);
    }
    document.addEventListener('mousemove', onMove);
    document.addEventListener('mouseup', onUp);
  });
}

let scrPopoverEl = null;

function showScrPopover(anchorEl, code) {
  hideScrPopover();
  const r = scrRawResults.find(x => x.code === code);
  if (!r) return;

  const pop = document.createElement('div');
  pop.className = 'scr-popover';
  pop.innerHTML = TREND_CONDITION_LABELS.map(([key]) => `
    <div class="scr-popover-row ${r.conditions[key] ? 'pass' : 'fail'}">
      <i class="ti ${r.conditions[key] ? 'ti-check' : 'ti-x'}" aria-hidden="true"></i>
      <span>${t(key)}</span>
    </div>`).join('');
  document.body.appendChild(pop);

  const rect = anchorEl.getBoundingClientRect();
  const popRect = pop.getBoundingClientRect();
  let top = rect.top - popRect.height - 8;
  if (top + window.scrollY < 8) top = rect.bottom + 8;
  let left = rect.left + rect.width / 2 - popRect.width / 2;
  left = Math.max(8, Math.min(left, window.innerWidth - popRect.width - 8));
  pop.style.top = `${top + window.scrollY}px`;
  pop.style.left = `${left + window.scrollX}px`;
  scrPopoverEl = pop;
}

function hideScrPopover() {
  if (scrPopoverEl) { scrPopoverEl.remove(); scrPopoverEl = null; }
}

// ─── 스크리닝 종목 상세 모달 ────────────────────────────────────────────────────

let scrDetailChart = null;
const scrDetailPanState = { cleanup: null };

async function openScreenerDetail(code) {
  const market = document.getElementById('scr-market').value;
  const overlay = document.getElementById('scr-detail-overlay');
  const body = document.getElementById('scr-detail-body');
  overlay.style.display = 'flex';
  body.innerHTML = `<div class="loading-msg"><i class="ti ti-loader-2" aria-hidden="true"></i>${t('loading')}</div>`;
  document.addEventListener('keydown', scrDetailEscHandler);

  try {
    const data = await api('GET', `/api/screener/detail?market=${market}&code=${encodeURIComponent(code)}`);
    renderScreenerDetail(data);
  } catch (e) {
    body.innerHTML = `<div class="error-msg"><i class="ti ti-alert-circle" aria-hidden="true"></i><span>${escapeHtml(e.message)}</span></div>`;
  }
}

function scrDetailEscHandler(e) {
  if (e.key === 'Escape') closeScreenerDetail();
}

function closeScreenerDetail() {
  document.getElementById('scr-detail-overlay').style.display = 'none';
  document.removeEventListener('keydown', scrDetailEscHandler);
  if (scrDetailChart) { scrDetailChart.destroy(); scrDetailChart = null; }
  if (scrDetailPanState.cleanup) { scrDetailPanState.cleanup(); scrDetailPanState.cleanup = null; }
}

function smaSeries(values, period) {
  const out = new Array(values.length).fill(null);
  let sum = 0;
  for (let i = 0; i < values.length; i++) {
    sum += values[i];
    if (i >= period) sum -= values[i - period];
    if (i >= period - 1) out[i] = sum / period;
  }
  return out;
}

// 표준 RSI(Wilder's smoothing, 기본 14일). 최초 period일은 단순평균으로 시작하고
// 그 이후는 지수 가중(1/period)으로 갱신하는 와일더 공식을 그대로 따른다.
function rsiSeries(closes, period = 14) {
  const out = new Array(closes.length).fill(null);
  if (closes.length <= period) return out;
  let gainSum = 0, lossSum = 0;
  for (let i = 1; i <= period; i++) {
    const diff = closes[i] - closes[i - 1];
    if (diff >= 0) gainSum += diff; else lossSum -= diff;
  }
  let avgGain = gainSum / period, avgLoss = lossSum / period;
  out[period] = avgLoss === 0 ? 100 : 100 - 100 / (1 + avgGain / avgLoss);
  for (let i = period + 1; i < closes.length; i++) {
    const diff = closes[i] - closes[i - 1];
    const gain = diff > 0 ? diff : 0, loss = diff < 0 ? -diff : 0;
    avgGain = (avgGain * (period - 1) + gain) / period;
    avgLoss = (avgLoss * (period - 1) + loss) / period;
    out[i] = avgLoss === 0 ? 100 : 100 - 100 / (1 + avgGain / avgLoss);
  }
  return out;
}

// 표준 볼린저 밴드: n일 단순이동평균 ± k * n일 표준편차(기본 20일, k=2).
function bollingerBands(closes, period = 20, mult = 2) {
  const mid = smaSeries(closes, period);
  const upper = new Array(closes.length).fill(null);
  const lower = new Array(closes.length).fill(null);
  for (let i = period - 1; i < closes.length; i++) {
    const window = closes.slice(i - period + 1, i + 1);
    const mean = mid[i];
    const variance = window.reduce((s, v) => s + (v - mean) ** 2, 0) / period;
    const sd = Math.sqrt(variance);
    upper[i] = mean + mult * sd;
    lower[i] = mean - mult * sd;
  }
  return { mid, upper, lower };
}

function buildFinancialAccordion(d, isUS, fmtMarketCapDetail) {
  const m = d.metrics || {};
  const fmtPct = v => v == null ? null : `${v > 0 ? '+' : ''}${Number(v).toFixed(1)}%`;
  const fmtPctPlain = v => v == null ? null : `${Number(v).toFixed(1)}%`;
  const fmtMult = v => v == null ? null : `${Number(v).toFixed(1)}x`;
  // 재무상태표/포괄손익계산서 절대금액 포맷 - 시가총액과 달리 Finnhub
  // financials-reported는 "백만 달러"가 아니라 달러 원단위 그대로 온다.
  const fmtAbs = v => {
    if (v == null) return null;
    const neg = v < 0;
    const av = Math.abs(v);
    let s;
    if (isUS) {
      if (av >= 1e12) s = `$${(av / 1e12).toFixed(2)}T`;
      else if (av >= 1e9) s = `$${(av / 1e9).toFixed(2)}B`;
      else if (av >= 1e6) s = `$${(av / 1e6).toFixed(0)}M`;
      else s = `$${Math.round(av).toLocaleString('en-US')}`;
    } else {
      if (av >= 1e12) s = `₩${(av / 1e12).toFixed(2)}T`;
      else if (av >= 1e9) s = `₩${(av / 1e9).toFixed(1)}B`;
      else s = `₩${(av / 1e6).toFixed(0)}M`;
    }
    return neg ? `-${s}` : s;
  };

  const categories = [
    {
      key: 'bs', icon: '🧾', title: 'Balance Sheet',
      items: [
        ['Total Assets', fmtAbs(m.totalAssets)],
        ['Total Liabilities', fmtAbs(m.totalLiabilities)],
        ['Total Equity', fmtAbs(m.totalEquity)],
        ['Equity Attributable to Owners', fmtAbs(m.equityAttributable)],
        ['Issued Capital', fmtAbs(m.issuedCapital)],
      ],
    },
    {
      key: 'ic', icon: '📄', title: 'Income Statement',
      items: [
        ['Revenue', fmtAbs(m.revenue)],
        ['Gross Profit', fmtAbs(m.grossProfit)],
        ['Operating Income', fmtAbs(m.operatingIncome)],
        ['Pre-Tax Income', fmtAbs(m.profitBeforeTax)],
        ['Net Income', fmtAbs(m.netIncome)],
        ['Net Income Attributable to Owners', fmtAbs(m.netIncomeAttributable)],
      ],
    },
    {
      key: 'profit', icon: '📈', title: 'Profitability',
      items: [
        ['Gross Margin', fmtPctPlain(m.grossMargin)],
        ['Operating Margin', fmtPctPlain(m.operatingMargin)],
        ['Net Margin', fmtPctPlain(m.netMargin)],
        ['ROE', fmtPctPlain(m.roe)],
        ['ROA', fmtPctPlain(m.roa)],
      ],
    },
    {
      key: 'growth', icon: '🌱', title: 'Growth (YoY)',
      items: [
        ['Revenue Growth', fmtPct(m.revenueGrowth)],
        ['Operating Income Growth', fmtPct(m.opIncomeGrowth)],
        ['EPS Growth', fmtPct(d.epsGrowth)],
      ],
    },
    {
      key: 'stability', icon: '🛡️', title: 'Stability',
      items: [
        ['Current Ratio', fmtPctPlain(m.currentRatio)],
        ['Quick Ratio', fmtPctPlain(m.quickRatio)],
        ['Debt Ratio', fmtPctPlain(m.debtRatio)],
        ['Net Debt Ratio', fmtPctPlain(m.netDebtRatio)],
      ],
    },
    {
      key: 'value', icon: '💰', title: 'Valuation',
      items: [
        ['Market Cap', d.marketCap != null ? fmtMarketCapDetail(d.marketCap) : null],
        ['P/E', d.peRatio != null ? Number(d.peRatio).toFixed(1) : null],
        ['P/B', fmtMult(m.pbr)],
        ['P/S', fmtMult(m.psr)],
        ['EV/EBITDA', fmtMult(m.evEbitda)],
        ['Dividend Yield', d.dividendYield != null ? Number(d.dividendYield).toFixed(2) + '%' : null],
      ],
    },
    {
      key: 'consensus', icon: '🎯', title: 'Consensus',
      items: [
        ['Rating', d.analystRating ? tv(d.analystRating) : null],
      ],
    },
  ];

  const catsHtml = categories.map((cat, idx) => {
    const available = cat.items.filter(([, v]) => v != null);
    const highlight = available.slice(0, 2).map(([l, v]) => `${tv(l)} ${v}`).join(' · ');
    const bodyHtml = available.length
      ? available.map(([label, value]) => {
          const isNeg = typeof value === 'string' && value.trim().startsWith('-');
          const isPos = typeof value === 'string' && value.trim().startsWith('+');
          return `<div class="scr-fin-metric" data-label="${escapeHtml(tv(label))}">
            <span class="label">${escapeHtml(tv(label))}</span>
            <span class="value ${isPos ? 'positive' : isNeg ? 'negative' : ''}">${value}</span>
          </div>`;
        }).join('')
      : `<div class="scr-fin-empty">${isUS ? t('failedToLoadData') : t('notAvailableForKrStocks')}</div>`;
    return `
      <div class="scr-fin-acc" data-cat="${cat.key}">
        <div class="scr-fin-acc-head" onclick="toggleFinAcc(this)">
          <span class="chev"><i class="ti ti-chevron-right" aria-hidden="true"></i></span>
          <span class="name">${cat.icon} ${tv(cat.title)}</span>
          <span class="hl">${highlight}</span>
        </div>
        <div class="scr-fin-acc-body"><div class="scr-fin-acc-body-inner">${bodyHtml}</div></div>
      </div>`;
  }).join('');

  return `
    <div class="scr-detail-section">
      <div class="scr-detail-section-title">${t('financialMetrics')}</div>
      <div class="scr-fin-search">
        <i class="ti ti-search" aria-hidden="true"></i>
        <input type="text" placeholder="${t('searchMetricsPlaceholder')}" oninput="filterFinAccordion(this.value)">
      </div>
      <div class="scr-fin-list">${catsHtml}</div>
    </div>`;
}

function toggleFinAcc(headEl) {
  headEl.parentElement.classList.toggle('open');
}

function filterFinAccordion(query) {
  const q = query.trim();
  document.querySelectorAll('.scr-fin-acc').forEach(acc => {
    if (!q) {
      acc.style.display = '';
      acc.querySelectorAll('.scr-fin-metric').forEach(m => { m.style.display = ''; });
      return;
    }
    let anyMatch = false;
    acc.querySelectorAll('.scr-fin-metric').forEach(m => {
      const match = m.dataset.label.includes(q);
      m.style.display = match ? '' : 'none';
      if (match) anyMatch = true;
    });
    acc.style.display = anyMatch ? '' : 'none';
    if (anyMatch) acc.classList.add('open');
  });
}

function renderScreenerDetail(d) {
  const body = document.getElementById('scr-detail-body');
  const isUS = d.market === 'US';
  const fmt = v => v == null ? '-' : (isUS ? `$${Number(v).toFixed(2)}` : `₩${Math.round(v).toLocaleString('en-US')}`);
  const fmtBig = v => {
    if (v == null) return '-';
    if (isUS) return `$${(v / 1000).toFixed(1)}B`; // profile2 marketCap 단위=백만달러
    if (v >= 1e12) return `₩${(v / 1e12).toFixed(1)}T`;
    if (v >= 1e8) return `₩${(v / 1e6).toFixed(0)}M`;
    return `₩${Math.round(v).toLocaleString('en-US')}`;
  };

  const condRows = TREND_CONDITION_LABELS.map(([key]) => {
    const pass = !!d.conditions[key];
    return `<div class="scr-detail-cond-row ${pass ? '' : 'fail'}">
      <i class="ti ${pass ? 'ti-check' : 'ti-x'} ${pass ? 'pass' : 'fail'}" aria-hidden="true"></i>
      <span>${t(key)}</span>
    </div>`;
  }).join('');

  const fmtMarketCapDetail = v => {
    if (v == null) return '-';
    if (isUS) {
      const usd = v * 1e6; // Finnhub 단위: 백만 달러
      if (usd >= 1e12) return `$${(usd / 1e12).toFixed(2)}T`;
      if (usd >= 1e9) return `$${(usd / 1e9).toFixed(2)}B`;
      return `$${(usd / 1e6).toFixed(0)}M`;
    }
    if (v >= 1e12) return `₩${(v / 1e12).toFixed(1)}T`;
    if (v >= 1e9) return `₩${(v / 1e9).toFixed(1)}B`;
    return `₩${(v / 1e6).toFixed(0)}M`;
  };

  const financialAccordionHtml = buildFinancialAccordion(d, isUS, fmtMarketCapDetail);

  let financialsHtml = '';
  if (d.market === 'KR' && d.financials) {
    const f = d.financials;
    financialsHtml = `
      <div class="scr-detail-section">
        <div class="scr-detail-section-title">${t('financialsDartAnnual').replace('{year}', escapeHtml(f.bsnsYear || ''))}</div>
        <div class="scr-detail-grid">
          <div class="scr-detail-stat"><div class="scr-detail-stat-label">${t('netIncome')}</div><div class="scr-detail-stat-value ${f.netIncome < 0 ? 'negative' : ''}">${fmtBig(f.netIncome)}</div></div>
          <div class="scr-detail-stat"><div class="scr-detail-stat-label">${tv('Revenue')}</div><div class="scr-detail-stat-value">${fmtBig(f.revenue)}</div></div>
          <div class="scr-detail-stat"><div class="scr-detail-stat-label">${tv('Total Equity')}</div><div class="scr-detail-stat-value">${fmtBig(f.totalEquity)}</div></div>
        </div>
      </div>`;
  }

  let targetHtml = '';
  if (d.target) {
    const tgt = d.target;
    const totalRec = (tgt.recBuy || 0) + (tgt.recHold || 0) + (tgt.recSell || 0);
    const buyPct = totalRec ? tgt.recBuy / totalRec * 100 : 0;
    const holdPct = totalRec ? tgt.recHold / totalRec * 100 : 0;
    const sellPct = totalRec ? tgt.recSell / totalRec * 100 : 0;
    targetHtml = `
      <div class="scr-detail-section">
        <div class="scr-detail-section-title">${t('analystTargetsRatings')} (Finnhub)</div>
        ${tgt.targetMean ? `
          <div class="scr-detail-grid" style="margin-bottom:10px;">
            <div class="scr-detail-stat"><div class="scr-detail-stat-label">${t('avgTarget')}</div><div class="scr-detail-stat-value">$${tgt.targetMean.toFixed(2)}</div></div>
            <div class="scr-detail-stat"><div class="scr-detail-stat-label">${t('high')}</div><div class="scr-detail-stat-value">$${tgt.targetHigh?.toFixed(2) ?? '-'}</div></div>
            <div class="scr-detail-stat"><div class="scr-detail-stat-label">${t('low')}</div><div class="scr-detail-stat-value">$${tgt.targetLow?.toFixed(2) ?? '-'}</div></div>
          </div>` : `<div style="font-size:12px;color:var(--text-muted);margin-bottom:10px;">${t('targetPriceUnavailable')}</div>`}
        ${totalRec ? `
          <div class="scr-detail-rec-bar">
            <div style="width:${buyPct}%;background:var(--green);"></div>
            <div style="width:${holdPct}%;background:var(--amber);"></div>
            <div style="width:${sellPct}%;background:var(--red);"></div>
          </div>
          <div class="scr-detail-rec-legend">
            <span><span class="scr-detail-rec-dot" style="background:var(--green);"></span>${t('buy')} ${tgt.recBuy}</span>
            <span><span class="scr-detail-rec-dot" style="background:var(--amber);"></span>${t('hold')} ${tgt.recHold}</span>
            <span><span class="scr-detail-rec-dot" style="background:var(--red);"></span>${t('sell')} ${tgt.recSell}</span>
          </div>` : ''}
      </div>`;
  } else if (d.market === 'US') {
    targetHtml = `<div class="scr-detail-section"><div class="scr-detail-section-title">${t('analystTargetsRatings')}</div><div style="font-size:12px;color:var(--text-muted);">${t('failedToLoadData')}</div></div>`;
  }

  body.innerHTML = `
    <div class="scr-detail-header">
      <span class="scr-detail-name">${escapeHtml(d.name)}</span>
      <span class="scr-detail-code">${escapeHtml(d.code)} · ${d.market === 'KR' ? 'KR' : 'US'}</span>
      <span class="scr-rs-badge ${rsBadgeClass(d.rsRating)}">RS ${d.rsRating ?? '-'}</span>
      ${d.stage ? `<span class="scr-stage-badge stage-${d.stage}">${t('stage' + d.stage)}</span>` : ''}
      ${d.industry ? `<span class="scr-industry-tag" style="display:inline-block;">${escapeHtml(tv(d.industry))}</span>` : ''}
    </div>
    <div class="scr-detail-price-row">
      <span class="scr-detail-price">${fmt(d.price)}</span>
      <span class="scr-pass-badge ${d.passCount < 8 ? 'partial' : ''}">${t('trendTemplate')} ${d.passCount}/8</span>
      ${d.volume != null ? `<span class="scr-detail-volume">${t('volume')} ${Math.round(d.volume).toLocaleString('en-US')}${d.relVolume != null ? ` (${Number(d.relVolume).toFixed(2)}x)` : ''}</span>` : ''}
    </div>

    ${financialAccordionHtml}

    <div class="scr-detail-actions">
      <button type="button" id="scr-detail-star" class="scr-detail-star-btn ${isInWatchlist(d.market, d.code) ? 'on' : ''}"
              onclick="toggleScreenerWatchlist('${d.market}', '${escapeHtml(d.code)}', '${escapeHtml(d.name).replace(/'/g, "\\'")}', event)">
        <i class="ti ${isInWatchlist(d.market, d.code) ? 'ti-star-filled' : 'ti-star'}" aria-hidden="true"></i>
        <span>${isInWatchlist(d.market, d.code) ? t('addedToWatchlist') : t('addToWatchlist')}</span>
      </button>
      ${d.market === 'KR' ? `
        <button type="button" class="btn-secondary" onclick="jumpToKrSwingBacktest('${escapeHtml(d.code)}', '${escapeHtml(d.name).replace(/'/g, "\\'")}')">
          <i class="ti ti-chart-candle" aria-hidden="true"></i> ${t('backtestInKrSwing')}
        </button>` : ''}
    </div>

    <div class="chart-wrap" style="height:460px;">
      <canvas id="scr-detail-chart" role="img" aria-label="${escapeHtml(d.name)} price/volume/RSI chart"></canvas>
    </div>
    <div style="font-size:11px;color:var(--text-muted);margin-top:6px;text-align:center;">${t('scrollZoomDragPan')}</div>

    <div class="scr-detail-section">
      <div class="scr-detail-section-title">${t('trendTemplate8Conditions')}</div>
      <div class="scr-detail-cond-list">${condRows}</div>
    </div>

    ${financialsHtml}
    ${targetHtml}
  `;

  // 모달 레이아웃(특히 재무 아코디언)이 완전히 자리잡기 전에 Chart.js가 초기화되면
  // 캔버스 크기를 감지하는 ResizeObserver가 레이아웃이 안정되는 동안 계속 리사이즈를
  // 반복해 화면이 깜박이는 문제가 있었다. 고정 지연(setTimeout) 대신 두 번의
  // requestAnimationFrame으로 레이아웃/페인트가 실제로 끝난 뒤에 그리도록 한다.
  requestAnimationFrame(() => requestAnimationFrame(() => drawScreenerDetailChart(d)));
}

// 가격+거래량+RSI를 캔버스 하나에 세 개의 수직 패널로 합쳐서 그린다. 예전에는
// 캔버스 3개(별도 Chart.js 인스턴스)로 나눠 그렸는데, 가격 차트에만 확대/축소·
// 드래그 이동이 걸려 있어 그걸 조작하면 거래량/RSI 차트의 날짜축과 어긋나
// "차트마다 날짜가 다르다"는 불편함이 있었다. 캔버스 하나·x축 하나를 공유하면
// 확대/이동이 항상 세 패널에 동시에 적용되어 이 문제 자체가 사라진다.
// Chart.js는 기본적으로 여러 y축을 세로로 겹쳐 그리므로, afterLayout 훅에서
// 각 축의 top/bottom을 수동으로 나눠 위(가격)·중간(거래량)·아래(RSI) 밴드로
// 분할한다(거래량/RSI 축은 숫자 눈금 없이 기준선+텍스트 라벨만 그린다).
const SCR_DETAIL_PANEL_RATIOS = { price: 0.56, volume: 0.15, rsi: 0.29 };
const SCR_DETAIL_PANEL_GAP = 12;

const scrDetailMultiPanelPlugin = {
  id: 'scrDetailMultiPanel',
  afterLayout(chart) {
    const { top, bottom } = chart.chartArea;
    const yPrice = chart.scales.y, yVol = chart.scales.yVolume, yRsi = chart.scales.yRsi;
    if (!yPrice || !yVol || !yRsi) return;
    const totalH = bottom - top;
    const priceH = totalH * SCR_DETAIL_PANEL_RATIOS.price;
    const volH = totalH * SCR_DETAIL_PANEL_RATIOS.volume;
    const rsiH = totalH - priceH - volH - SCR_DETAIL_PANEL_GAP * 2;
    yPrice.top = top; yPrice.bottom = top + priceH; yPrice.height = priceH;
    yVol.top = yPrice.bottom + SCR_DETAIL_PANEL_GAP; yVol.bottom = yVol.top + volH; yVol.height = volH;
    yRsi.top = yVol.bottom + SCR_DETAIL_PANEL_GAP; yRsi.bottom = bottom; yRsi.height = rsiH;
    // top/bottom을 바꾸는 것만으로는 부족하다 - Chart.js는 각 스케일의 실제 픽셀
    // 변환에 쓰는 _startPixel/_length를 configure() 시점에 top/bottom으로부터
    // 미리 캐싱해두는데, 그 캐싱이 이미 끝난 뒤(레이아웃 단계)에 top/bottom만
    // 덮어쓰면 눈금 위치(afterDraw에서 쓰는 top)는 바뀌어도 실제 막대/선은 여전히
    // 옛 캐시값 그대로 그려진다(거래량 막대·RSI 선이 가격 패널 범위 전체에 걸쳐
    // 그려지던 원인). configure()를 다시 호출해 새 top/bottom으로 캐시를 갱신한다.
    yPrice.configure(); yVol.configure(); yRsi.configure();
  },
  afterDraw(chart) {
    const { ctx, chartArea, scales } = chart;
    if (!scales.yVolume || !scales.yRsi) return;
    ctx.save();
    ctx.font = '11px sans-serif';
    ctx.fillStyle = 'rgba(148,148,148,0.85)';
    ctx.textBaseline = 'top';
    ctx.fillText(t('volume'), chartArea.left + 2, scales.yVolume.top + 2);
    ctx.fillText('RSI(14)', chartArea.left + 2, scales.yRsi.top + 2);
    ctx.restore();
  },
};

function drawScreenerDetailChart(d) {
  const canvas = document.getElementById('scr-detail-chart');
  if (!canvas || !d.priceCurve?.length) return;
  if (scrDetailChart) scrDetailChart.destroy();

  const dates = d.priceCurve.map(p => p.date);
  const closes = d.priceCurve.map(p => p.close);
  const volumes = d.priceCurve.map(p => p.volume);
  const n = dates.length;
  const ma50 = smaSeries(closes, 50);
  const ma150 = smaSeries(closes, 150);
  const ma200 = smaSeries(closes, 200);
  const bb = bollingerBands(closes, 20, 2);
  const rsi = rsiSeries(closes, 14);
  const isUS = d.market === 'US';
  const fmtY = v => isUS ? `$${v.toFixed(0)}` : `${Number(v).toLocaleString('en-US')}`;
  const fmtVol = v => {
    if (v == null) return '-';
    if (v >= 1e9) return `${(v / 1e9).toFixed(1)}B`;
    if (v >= 1e6) return `${(v / 1e6).toFixed(1)}M`;
    if (v >= 1e3) return `${(v / 1e3).toFixed(1)}K`;
    return `${Math.round(v).toLocaleString('en-US')}`;
  };

  // data 배열 원소를 null과 {x,y} 객체로 섞어서 넣으면 Chart.js가 그 데이터셋
  // 전체를 파싱하지 못해 선이 아예 안 그려진다(50/150/200일선이 안 보이던 원인) -
  // 항상 {x, y} 형태를 유지하고 y만 null로 둬서 그 구간만 끊기게(gap) 한다.
  const mkLine = (data, color, label, extra) => ({
    label, data: data.map((v, i) => ({ x: i, y: v })), yAxisID: 'y',
    borderColor: color, backgroundColor: 'transparent', fill: false,
    pointRadius: 0, borderWidth: label === t('close') ? 1.5 : 1.2, tension: 0.1, spanGaps: false,
    ...extra,
  });
  // 전일 대비 상승/하락으로 거래량 막대 색을 나눈다(첫 캔들은 비교 대상이 없어 중립색).
  const volColors = closes.map((c, i) => i === 0 || closes[i - 1] == null || c == null ? 'rgba(136,135,128,0.6)'
    : c >= closes[i - 1] ? 'rgba(34,197,94,0.6)' : 'rgba(239,68,68,0.6)');
  // RSI 30/70 기준선은 별도 플러그인 없이 처음/끝 두 점만 있는 평평한 선으로 그린다.
  const flatLine = (y, color, label) => ({
    label, data: [{ x: 0, y }, { x: n - 1, y }], yAxisID: 'yRsi', borderColor: color, borderWidth: 1,
    borderDash: [4, 4], pointRadius: 0, fill: false,
  });

  const LEGEND_ALLOW = new Set([t('close'), 'MA50', 'MA150', 'MA200', t('bollingerBands')]);

  scrDetailChart = new Chart(canvas, {
    type: 'line',
    data: {
      datasets: [
        mkLine(bb.upper, 'rgba(168,133,225,0.55)', t('bollingerBands'), { borderDash: [3, 3], borderWidth: 1 }),
        mkLine(bb.lower, 'rgba(168,133,225,0.55)', 'BB Lower', { borderDash: [3, 3], borderWidth: 1, fill: '-1', backgroundColor: 'rgba(168,133,225,0.08)' }),
        mkLine(closes, '#888780', t('close')),
        mkLine(ma50, '#E24B4A', 'MA50'),
        mkLine(ma150, '#EF9F27', 'MA150'),
        mkLine(ma200, '#378ADD', 'MA200'),
        { label: t('volume'), type: 'bar', yAxisID: 'yVolume', data: volumes.map((v, i) => ({ x: i, y: v })),
          backgroundColor: volColors, barPercentage: 1, categoryPercentage: 1 },
        { label: 'RSI(14)', data: rsi.map((v, i) => ({ x: i, y: v })), yAxisID: 'yRsi', borderColor: '#a855f7',
          backgroundColor: 'transparent', fill: false, pointRadius: 0, borderWidth: 1.3, tension: 0.1, spanGaps: false },
        flatLine(70, 'rgba(239,68,68,0.45)', 'RSI 70'),
        flatLine(30, 'rgba(34,197,94,0.45)', 'RSI 30'),
      ],
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      interaction: { mode: 'index', intersect: false },
      plugins: {
        legend: {
          display: true, position: 'top', labels: { boxWidth: 12, font: { size: 11 }, filter: item => LEGEND_ALLOW.has(item.text) },
        },
        tooltip: {
          filter: ctx => ctx.dataset.label !== 'RSI 70' && ctx.dataset.label !== 'RSI 30',
          callbacks: {
            title: items => items.length ? (dates[Math.round(items[0].parsed.x)] ?? '') : '',
            label: ctx => {
              const v = ctx.parsed.y;
              if (v == null) return undefined;
              const lbl = ctx.dataset.label;
              if (lbl === t('volume')) return ` ${t('volume')}: ${fmtVol(v)}`;
              if (lbl === 'RSI(14)') return ` RSI(14): ${v.toFixed(1)}`;
              return ` ${lbl}: ${fmtY(v)}`;
            },
          },
        },
        zoom: {
          zoom: { wheel: { enabled: true }, pinch: { enabled: true }, mode: 'x' },
          pan: { enabled: false },
          limits: { x: { min: 0, max: Math.max(n - 1, 0), minRange: 10 } },
        },
      },
      scales: {
        x: {
          type: 'linear', min: 0, max: Math.max(n - 1, 0),
          ticks: { maxTicksLimit: 8, callback: v => dates[Math.round(v)] ?? '' },
          grid: { display: false },
        },
        y: { position: 'left', ticks: { callback: v => fmtY(Number(v)) }, grid: { color: 'rgba(128,128,128,0.1)' } },
        yVolume: { display: false, min: 0 },
        yRsi: { display: false, min: 0, max: 100 },
      },
    },
    plugins: [scrDetailMultiPanelPlugin],
  });
  attachChartPan(scrDetailChart, canvas, scrDetailPanState);
}

// ─── 스크리닝 백테스트 ────────────────────────────────────────────────────────

let sbtPreset = null; // null(직접 설정) | 'minervini_v2'

function initScreeningBacktestTab() {
  const startEl = document.getElementById('sbt-start');
  const endEl = document.getElementById('sbt-end');
  if (!endEl.value) endEl.value = new Date().toISOString().slice(0, 10);
  if (!startEl.value) {
    const d = new Date();
    d.setFullYear(d.getFullYear() - 1);
    startEl.value = d.toISOString().slice(0, 10);
  }
}

const SBT_PRESET_KEYS = ['minervini_v2', 'minervini_v21', 'relaxed_vcp', 'anonymous'];
const SBT_PRESET_RENDERERS = {
  minervini_v2: () => renderMinerviniV2Reference,
  minervini_v21: () => renderMinerviniV21Reference,
  relaxed_vcp: () => renderRelaxedVcpReference,
  anonymous: () => renderAnonymousReference,
};
const SBT_PRESET_DEFAULT_START = {
  minervini_v2: '2020-01-01', minervini_v21: '2020-01-01', relaxed_vcp: '2017-01-01', anonymous: '2017-01-01',
};
// 어나니머스는 슬롯당 고정 금액 상한(2,500만원 = 시드 5천만원/10슬롯의 5배)을
// 전제로 튜닝됐다 - 기본 시드(1천만원)로 돌리면 그 상한 대비 계좌가 작아 상한이
// 거의 안 걸려 실측 결과와 괴리가 생긴다.
const SBT_PRESET_DEFAULT_SEED = { anonymous: 50_000_000 };

function setScreeningBacktestPreset(preset) {
  sbtPreset = preset;
  document.getElementById('sbt-preset-default').classList.toggle('selected', preset === null);
  for (const key of SBT_PRESET_KEYS) {
    document.getElementById(`sbt-preset-${key}`).classList.toggle('selected', preset === key);
    document.getElementById(`sbt-info-${key}`).style.display = preset === key ? 'flex' : 'none';
    const refEl = document.getElementById(`sbt-reference-${key}`);
    if (preset === key) {
      document.getElementById('sbt-start').value = SBT_PRESET_DEFAULT_START[key];
      document.getElementById('sbt-end').value = new Date().toISOString().slice(0, 10);
      document.getElementById('sbt-seed').value = SBT_PRESET_DEFAULT_SEED[key] || 10_000_000;
      SBT_PRESET_RENDERERS[key]()(refEl);
      refEl.style.display = 'block';
    } else {
      refEl.style.display = 'none';
      refEl.innerHTML = '';
    }
  }
  const locked = preset !== null;
  for (const id of ['sbt-field-market', 'sbt-field-strategy', 'sbt-field-stoploss', 'sbt-field-maxpos']) {
    document.getElementById(id).style.opacity = locked ? '0.45' : '1';
    document.getElementById(id).querySelectorAll('select, input').forEach(elm => { elm.disabled = locked; });
  }
  document.getElementById('sbt-info-default').style.display = locked ? 'none' : 'flex';
}

// 2020-01-01~2026-08-21(로컬 캐시로 계산, run_risk_managed_backtest와 동일 파라미터 -
// MINERVINI_V2_PARAMS) 기준 사전 계산 결과. 실행 버튼을 눌러 직접 돌리면 최신
// 데이터로 다시 계산되며 이 값과 약간 다를 수 있다(그 사이 새로 쌓인 거래일,
// 데이터 소급수정 등) - 그 전까지 참고용으로 미리 보여주기 위한 스냅샷이다.
const MINERVINI_V2_REFERENCE = {
  start: '2020-01-01', end: '2026-08-21', seed: 10000000, finalValue: 33280012.49,
  returnPct: 232.8, cagrPct: 19.86, mddPct: 22.73, tradeCount: 1512, winCount: 298, winRatePct: 19.7,
  avgHoldDays: 12.4, profitLossRatio: 3.94, alphaPct: 35.3,
  benchmarkLabel: 'KOSPI Buy & Hold', benchmarkReturnPct: 197.5,
  exitReasonCounts: { initialStop: 324, breakevenStop: 266, trailingStop: 294, timeStop: 618, periodEnd: 10 },
};

let sbtRefTradesLoaded = false;

function renderMinerviniV2Reference(el) {
  const d = MINERVINI_V2_REFERENCE;
  const fmt = v => `₩${Math.round(v).toLocaleString('en-US')}`;
  const reasons = Object.entries(d.exitReasonCounts)
    .map(([k, v]) => `${PAPER_TRADING_EXIT_REASON_LABEL(k)} ${v}${t('countUnit') || '건'}`).join(', ');
  el.innerHTML = `
    <div class="card">
      <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px;margin-bottom:6px;">
        <div style="font-size:13px;font-weight:700;">📌 ${t('referenceResultTitle')}</div>
        <div style="font-size:11.5px;color:var(--text-muted);">${escapeHtml(d.start)} ~ ${escapeHtml(d.end)}</div>
      </div>
      <div class="bt-summary-grid">
        <div class="meta-item"><div class="meta-label">${t('capital')}</div><div class="meta-value">${fmt(d.seed)}</div></div>
        <div class="meta-item"><div class="meta-label">${t('finalValue')}</div><div class="meta-value">${fmt(d.finalValue)}</div></div>
        <div class="meta-item"><div class="meta-label">${t('totalReturn')}</div><div class="meta-value positive">+${d.returnPct.toFixed(1)}%</div></div>
        <div class="meta-item"><div class="meta-label">${t('annualizedReturn')}</div><div class="meta-value positive">+${d.cagrPct.toFixed(1)}%</div></div>
        <div class="meta-item"><div class="meta-label">MDD</div><div class="meta-value negative">-${d.mddPct.toFixed(1)}%</div></div>
        <div class="meta-item"><div class="meta-label">${t('winRate')}</div><div class="meta-value">${d.winRatePct.toFixed(1)}% (${d.winCount}/${d.tradeCount})</div></div>
        <div class="meta-item"><div class="meta-label">${t('avgHoldDays')}</div><div class="meta-value">${d.avgHoldDays}</div></div>
        <div class="meta-item"><div class="meta-label">${t('profitLossRatio')}</div><div class="meta-value">${d.profitLossRatio}</div></div>
        <div class="meta-item"><div class="meta-label">${t('alphaExcessReturn')}</div><div class="meta-value positive">+${d.alphaPct.toFixed(1)}%p</div></div>
        <div class="meta-item"><div class="meta-label">${escapeHtml(d.benchmarkLabel)}</div><div class="meta-value positive">+${d.benchmarkReturnPct.toFixed(1)}%</div></div>
      </div>
      <div style="font-size:11.5px;color:var(--text-muted);margin-top:10px;line-height:1.5;">
        ${t('exitReason')}: ${escapeHtml(reasons)}<br>
        ${t('referenceResultNote')}
      </div>
    </div>
    <div class="card">
      <div style="font-size:13px;font-weight:600;margin-bottom:10px;">${t('tradeLog')} (${d.tradeCount})</div>
      <div id="sbt-ref-trades-body"><div class="loading-msg"><i class="ti ti-loader-2" aria-hidden="true"></i></div></div>
    </div>`;
  loadMinerviniV2ReferenceTrades();
}

async function loadMinerviniV2ReferenceTrades() {
  const body = document.getElementById('sbt-ref-trades-body');
  if (!body) return;
  // 1,512건 전체를 app.js에 항상 박아두면 다른 탭만 쓰는 사용자도 매번 그 용량을
  // 받아야 해서, 이 프리셋을 실제로 열었을 때만 별도 JSON을 지연 로드한다.
  try {
    const res = await fetch('/static/minervini_v2_reference.json');
    if (!res.ok) throw new Error('fetch failed');
    const data = await res.json();
    const exitReasonLabel = r => ({
      stopLoss: t('stopLoss'), conditionExit: t('conditionExit'), periodEnd: t('periodEnd'),
      initialStop: t('exitInitialStop'), breakevenStop: t('exitBreakevenStop'),
      trailingStop: t('exitTrailingStop'), timeStop: t('exitTimeStop'),
      partialProfit: t('exitPartialProfit'), maBreak: t('exitMaBreak'),
    }[r] || r);
    const fmtPrice = v => `₩${Math.round(v).toLocaleString('en-US')}`;
    body.innerHTML = `
      <div class="pf-table-wrap pf-table-wrap-scroll">
        <table class="pf-table">
          <thead><tr>
            <th>${t('name')}</th><th>${t('code')}</th>
            <th style="text-align:right;">${t('buyDate')}</th><th style="text-align:right;">${t('buyPrice')}</th>
            <th style="text-align:right;">${t('sellDate')}</th><th style="text-align:right;">${t('sellPrice')}</th>
            <th style="text-align:right;">${t('returnPct')}</th><th>${t('exitReason')}</th>
          </tr></thead>
          <tbody>
            ${data.trades.map(tr => `
              <tr>
                <td>${escapeHtml(tr.name)}</td><td>${escapeHtml(tr.code)}</td>
                <td style="text-align:right;">${escapeHtml(tr.entryDate)}</td><td style="text-align:right;">${fmtPrice(tr.entryPrice)}</td>
                <td style="text-align:right;">${escapeHtml(tr.exitDate)}</td><td style="text-align:right;">${fmtPrice(tr.exitPrice)}</td>
                <td style="text-align:right;" class="${tr.pnlPct >= 0 ? 'positive' : 'negative'}">${tr.pnlPct>=0?'+':''}${tr.pnlPct.toFixed(1)}%</td>
                <td>${exitReasonLabel(tr.exitReason)}</td>
              </tr>`).join('')}
          </tbody>
        </table>
      </div>`;
  } catch (e) {
    body.innerHTML = `<div class="error-msg"><i class="ti ti-alert-circle" aria-hidden="true"></i><span>${t('referenceTradesLoadError')}</span></div>`;
  }
}

// v2.1 참고 결과 - v2와 진입 신호는 같고 청산 규칙만 다름(승률/손익비 튜닝 스윕
// 결과로 확정: breakeven_lock_r=1.0, atr_mult=2.5, market_regime_filter=True).
const MINERVINI_V21_REFERENCE = {
  start: '2020-01-01', end: '2026-08-21', seed: 10000000, finalValue: 43985007.97,
  returnPct: 339.85, cagrPct: 25.01, mddPct: 11.12, tradeCount: 1110, winCount: 371, winRatePct: 33.4,
  avgHoldDays: 10.9, profitLossRatio: 2.94, alphaPct: 142.35,
  benchmarkLabel: 'KOSPI Buy & Hold', benchmarkReturnPct: 197.5,
  exitReasonCounts: { initialStop: 121, breakevenStop: 290, trailingStop: 77, timeStop: 612, periodEnd: 10 },
};

function renderMinerviniV21Reference(el) {
  const d = MINERVINI_V21_REFERENCE;
  const fmt = v => `₩${Math.round(v).toLocaleString('en-US')}`;
  const reasons = Object.entries(d.exitReasonCounts)
    .map(([k, v]) => `${PAPER_TRADING_EXIT_REASON_LABEL(k)} ${v}${t('countUnit') || '건'}`).join(', ');
  el.innerHTML = `
    <div class="card">
      <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px;margin-bottom:6px;">
        <div style="font-size:13px;font-weight:700;">📌 ${t('referenceResultTitle')}</div>
        <div style="font-size:11.5px;color:var(--text-muted);">${escapeHtml(d.start)} ~ ${escapeHtml(d.end)}</div>
      </div>
      <div class="bt-summary-grid">
        <div class="meta-item"><div class="meta-label">${t('capital')}</div><div class="meta-value">${fmt(d.seed)}</div></div>
        <div class="meta-item"><div class="meta-label">${t('finalValue')}</div><div class="meta-value">${fmt(d.finalValue)}</div></div>
        <div class="meta-item"><div class="meta-label">${t('totalReturn')}</div><div class="meta-value positive">+${d.returnPct.toFixed(1)}%</div></div>
        <div class="meta-item"><div class="meta-label">${t('annualizedReturn')}</div><div class="meta-value positive">+${d.cagrPct.toFixed(1)}%</div></div>
        <div class="meta-item"><div class="meta-label">MDD</div><div class="meta-value negative">-${d.mddPct.toFixed(1)}%</div></div>
        <div class="meta-item"><div class="meta-label">${t('winRate')}</div><div class="meta-value">${d.winRatePct.toFixed(1)}% (${d.winCount}/${d.tradeCount})</div></div>
        <div class="meta-item"><div class="meta-label">${t('avgHoldDays')}</div><div class="meta-value">${d.avgHoldDays}</div></div>
        <div class="meta-item"><div class="meta-label">${t('profitLossRatio')}</div><div class="meta-value">${d.profitLossRatio}</div></div>
        <div class="meta-item"><div class="meta-label">${t('alphaExcessReturn')}</div><div class="meta-value positive">+${d.alphaPct.toFixed(1)}%p</div></div>
        <div class="meta-item"><div class="meta-label">${escapeHtml(d.benchmarkLabel)}</div><div class="meta-value positive">+${d.benchmarkReturnPct.toFixed(1)}%</div></div>
      </div>
      <div style="font-size:11.5px;color:var(--text-muted);margin-top:10px;line-height:1.5;">
        ${t('exitReason')}: ${escapeHtml(reasons)}<br>
        ${t('referenceResultNote')}
      </div>
    </div>
    <div class="card">
      <div style="font-size:13px;font-weight:600;margin-bottom:10px;">${t('tradeLog')} (${d.tradeCount})</div>
      <div id="sbt-ref21-trades-body"><div class="loading-msg"><i class="ti ti-loader-2" aria-hidden="true"></i></div></div>
    </div>`;
  loadMinerviniV21ReferenceTrades();
}

async function loadMinerviniV21ReferenceTrades() {
  const body = document.getElementById('sbt-ref21-trades-body');
  if (!body) return;
  try {
    const res = await fetch('/static/minervini_v21_reference.json');
    if (!res.ok) throw new Error('fetch failed');
    const data = await res.json();
    const exitReasonLabel = r => ({
      stopLoss: t('stopLoss'), conditionExit: t('conditionExit'), periodEnd: t('periodEnd'),
      initialStop: t('exitInitialStop'), breakevenStop: t('exitBreakevenStop'),
      trailingStop: t('exitTrailingStop'), timeStop: t('exitTimeStop'),
      partialProfit: t('exitPartialProfit'), maBreak: t('exitMaBreak'),
    }[r] || r);
    const fmtPrice = v => `₩${Math.round(v).toLocaleString('en-US')}`;
    body.innerHTML = `
      <div class="pf-table-wrap pf-table-wrap-scroll">
        <table class="pf-table">
          <thead><tr>
            <th>${t('name')}</th><th>${t('code')}</th>
            <th style="text-align:right;">${t('buyDate')}</th><th style="text-align:right;">${t('buyPrice')}</th>
            <th style="text-align:right;">${t('sellDate')}</th><th style="text-align:right;">${t('sellPrice')}</th>
            <th style="text-align:right;">${t('returnPct')}</th><th>${t('exitReason')}</th>
          </tr></thead>
          <tbody>
            ${data.trades.map(tr => `
              <tr>
                <td>${escapeHtml(tr.name)}</td><td>${escapeHtml(tr.code)}</td>
                <td style="text-align:right;">${escapeHtml(tr.entryDate)}</td><td style="text-align:right;">${fmtPrice(tr.entryPrice)}</td>
                <td style="text-align:right;">${escapeHtml(tr.exitDate)}</td><td style="text-align:right;">${fmtPrice(tr.exitPrice)}</td>
                <td style="text-align:right;" class="${tr.pnlPct >= 0 ? 'positive' : 'negative'}">${tr.pnlPct>=0?'+':''}${tr.pnlPct.toFixed(1)}%</td>
                <td>${exitReasonLabel(tr.exitReason)}</td>
              </tr>`).join('')}
          </tbody>
        </table>
      </div>`;
  } catch (e) {
    body.innerHTML = `<div class="error-msg"><i class="ti ti-alert-circle" aria-hidden="true"></i><span>${t('referenceTradesLoadError')}</span></div>`;
  }
}

// 완화 VCP 전략 참고 결과 - vcp_strategy.RELAXED_VCP_PARAMS로 계산. cash_equitize
// (현금 유휴화 방지, 지수노출 상한 70%) 도입까지의 경위는 이전 버전 주석 참고.
// 이후 "9년이면 최소 1000건 이상 거래해야 한다"는 요청으로 거래빈도를 다시 크게
// 늘렸다 - 미너비니 트렌드템플릿 통과기준(min_trend_pass_count, 8개 전부 -> 3개
// 이상)과 VCP 판정(ADX≥10, 최종수축비율 95%, 최소지속 1일, 최근성 60일), 최대
// 포지션(10->40)을 함께 풀어 11개 조합을 스윕한 결과 "거래빈도를 늘릴수록 손익비가
// 낮아지는" 트레이드오프가 뚜렷했다(트렌드템플릿+슬롯만 완화: 9.6년환산 315건/
// 손익비4.3 vs 전면 완화: 9.6년환산 1158건/손익비3.66) - "1000건+손익비 상승"을
// 동시에 만족하는 조합은 없어서, 사용자가 "1000건 이상 달성"을 우선해 아래 조합을
// 채택했다. 손익비는 4.51->3.75로 낮아졌지만 거래빈도(139->1611건)와 CAGR
// (16.95%->37.28%)/알파(+113.96%p->+1778.37%p)는 크게 개선됐다.
const RELAXED_VCP_REFERENCE = {
  start: '2017-01-01', end: '2026-08-21', seed: 10000000, finalValue: 211658357.73,
  returnPct: 2016.58, cagrPct: 37.28, mddPct: 20.47, tradeCount: 1611, winCount: 1028, winRatePct: 63.8,
  avgHoldDays: 14.0, profitLossRatio: 3.75, alphaPct: 1778.37,
  benchmarkLabel: 'KOSPI Buy & Hold', benchmarkReturnPct: 238.21,
  exitReasonCounts: { breakevenStop: 189, trailingStop: 542, initialStop: 257, partialProfit: 485, maBreak: 116, timeStop: 14, periodEnd: 8 },
};

function renderRelaxedVcpReference(el) {
  const d = RELAXED_VCP_REFERENCE;
  const fmt = v => `₩${Math.round(v).toLocaleString('en-US')}`;
  const reasons = Object.entries(d.exitReasonCounts)
    .map(([k, v]) => `${PAPER_TRADING_EXIT_REASON_LABEL(k)} ${v}${t('countUnit') || '건'}`).join(', ');
  el.innerHTML = `
    <div class="card">
      <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px;margin-bottom:6px;">
        <div style="font-size:13px;font-weight:700;">📌 ${t('referenceResultTitle')}</div>
        <div style="font-size:11.5px;color:var(--text-muted);">${escapeHtml(d.start)} ~ ${escapeHtml(d.end)}</div>
      </div>
      <div class="bt-summary-grid">
        <div class="meta-item"><div class="meta-label">${t('capital')}</div><div class="meta-value">${fmt(d.seed)}</div></div>
        <div class="meta-item"><div class="meta-label">${t('finalValue')}</div><div class="meta-value">${fmt(d.finalValue)}</div></div>
        <div class="meta-item"><div class="meta-label">${t('totalReturn')}</div><div class="meta-value positive">+${d.returnPct.toFixed(1)}%</div></div>
        <div class="meta-item"><div class="meta-label">${t('annualizedReturn')}</div><div class="meta-value positive">+${d.cagrPct.toFixed(1)}%</div></div>
        <div class="meta-item"><div class="meta-label">MDD</div><div class="meta-value negative">-${d.mddPct.toFixed(1)}%</div></div>
        <div class="meta-item"><div class="meta-label">${t('winRate')}</div><div class="meta-value">${d.winRatePct.toFixed(1)}% (${d.winCount}/${d.tradeCount})</div></div>
        <div class="meta-item"><div class="meta-label">${t('avgHoldDays')}</div><div class="meta-value">${d.avgHoldDays}</div></div>
        <div class="meta-item"><div class="meta-label">${t('profitLossRatio')}</div><div class="meta-value">${d.profitLossRatio}</div></div>
        <div class="meta-item"><div class="meta-label">${t('alphaExcessReturn')}</div><div class="meta-value ${d.alphaPct >= 0 ? 'positive' : 'negative'}">${d.alphaPct >= 0 ? '+' : ''}${d.alphaPct.toFixed(1)}%p</div></div>
        <div class="meta-item"><div class="meta-label">${escapeHtml(d.benchmarkLabel)}</div><div class="meta-value positive">+${d.benchmarkReturnPct.toFixed(1)}%</div></div>
      </div>
      <div style="font-size:11.5px;color:var(--text-muted);margin-top:10px;line-height:1.5;">
        ${t('exitReason')}: ${escapeHtml(reasons)}<br>
        ${t('referenceResultNote')}
      </div>
    </div>
    <div class="card">
      <div style="font-size:13px;font-weight:600;margin-bottom:10px;">${t('tradeLog')} (${d.tradeCount})</div>
      <div id="sbt-refvcp-trades-body"><div class="loading-msg"><i class="ti ti-loader-2" aria-hidden="true"></i></div></div>
    </div>`;
  loadRelaxedVcpReferenceTrades();
}

async function loadRelaxedVcpReferenceTrades() {
  const body = document.getElementById('sbt-refvcp-trades-body');
  if (!body) return;
  try {
    const res = await fetch('/static/relaxed_vcp_reference.json');
    if (!res.ok) throw new Error('fetch failed');
    const data = await res.json();
    const exitReasonLabel = r => ({
      stopLoss: t('stopLoss'), conditionExit: t('conditionExit'), periodEnd: t('periodEnd'),
      initialStop: t('exitInitialStop'), breakevenStop: t('exitBreakevenStop'),
      trailingStop: t('exitTrailingStop'), timeStop: t('exitTimeStop'),
      partialProfit: t('exitPartialProfit'), maBreak: t('exitMaBreak'),
    }[r] || r);
    const fmtPrice = v => `₩${Math.round(v).toLocaleString('en-US')}`;
    body.innerHTML = `
      <div class="pf-table-wrap pf-table-wrap-scroll">
        <table class="pf-table">
          <thead><tr>
            <th>${t('name')}</th><th>${t('code')}</th>
            <th style="text-align:right;">${t('buyDate')}</th><th style="text-align:right;">${t('buyPrice')}</th>
            <th style="text-align:right;">${t('sellDate')}</th><th style="text-align:right;">${t('sellPrice')}</th>
            <th style="text-align:right;">${t('returnPct')}</th><th>${t('exitReason')}</th>
          </tr></thead>
          <tbody>
            ${data.trades.map(tr => `
              <tr>
                <td>${escapeHtml(tr.name)}</td><td>${escapeHtml(tr.code)}</td>
                <td style="text-align:right;">${escapeHtml(tr.entryDate)}</td><td style="text-align:right;">${fmtPrice(tr.entryPrice)}</td>
                <td style="text-align:right;">${escapeHtml(tr.exitDate)}</td><td style="text-align:right;">${fmtPrice(tr.exitPrice)}</td>
                <td style="text-align:right;" class="${tr.pnlPct >= 0 ? 'positive' : 'negative'}">${tr.pnlPct>=0?'+':''}${tr.pnlPct.toFixed(1)}%</td>
                <td>${exitReasonLabel(tr.exitReason)}</td>
              </tr>`).join('')}
          </tbody>
        </table>
      </div>`;
  } catch (e) {
    body.innerHTML = `<div class="error-msg"><i class="ti ti-alert-circle" aria-hidden="true"></i><span>${t('referenceTradesLoadError')}</span></div>`;
  }
}

// 어나니머스 참고 결과 - vcp_strategy.ANONYMOUS_PARAMS(돈치안15일 브레이크아웃,
// 초기손절1.5×ATR/최대6%, 챈들리어8×ATR, 250일선6일이탈, 시간손절20일/0.2R,
// 피라미딩 최대5회, 슬롯10개 고정, 재평가3일)로 계산. 사용자가 아는 실제 매매
// 방법론(9년 1,476건·승률31.57%·손익비9.30·평균수익+52.38%·평균손실-5.63%·
// CAGR+50.76%·누적+5,049%·MDD-44.45%)에 최대한 근접시킨 결과다. 슬롯을 10개로
// 고정한 상태에서는 "10슬롯×9.6년 거래일/평균보유일수"가 산수적 상한이라(평균
// 보유 25일 기준 최대 940건) 재평가 주기를 주간→3일→매일로 단축해도 거래수가
// 1,000건을 못 넘었다. 이후 "종목마다 동일금액 매매+시가총액/유동성 필터 제거"
// 요청으로 position_sizing_mode="equal_weight"(변동성과 무관하게 슬롯당 총자산/
// max_positions 균등 배분)를 추가했다 - 필터 제거는 거래수에 거의 영향이 없었지만
// (551→570건, 시총/유동성 병목이 아니었음을 재확인) 동일금액 배분은 CAGR을
// 27.43%→35.71%로, 누적수익률을 934%→1,798%로 크게 끌어올렸다(손익비 8.2→7.9,
// MDD -31.24%→-33.83%로 소폭 트레이드오프). 거래수·승률은 여전히 10슬롯 산수
// 상한에 막혀 목표에 못 미친다.
// "동일금액+필터제거"만으로는 계좌가 복리로 커질수록 실제로는 체결 불가능한
// 금액을 그대로 태운다고 가정하는 초복리 아티팩트가 나왔다(누적수익률이 수십만
// %까지 치솟음 - 사용자가 "만 단위 수익률이 정상이냐"고 지적해 발견). 원인은
// min_avg_trade_value(유동성 필터)가 "후보 자격"만 거를 뿐 실제 매수 금액은
// 제한하지 않는다는 것 - max_pct_of_avg_trade_value(종목 자신의 평균거래대금
// 대비 10% 이내)와 max_position_value_abs(계좌가 아무리 커져도 넘지 않는 고정
// 금액 상한 - "전략 용량" 가정을 명시적으로 반영)로 이중 제한해 현실적인 수준으로
// 눌렀다. 시드 5,000만원 기준 고정상한 1,500/2,500/5,000만원을 비교한 결과
// 승률·손익비·거래수는 거의 그대로인 채(상한은 "계좌가 커졌을 때 얼마나 더
// 태울 수 있다고 가정할지"만 다름) CAGR만 달라졌다(47.0/53.43/62.81%) - 목표
// (CAGR 50.76%, 승률 31.57%, 거래수 1,476건)에 가장 근접한 2,500만원(초기
// 슬롯당 배분액 500만원의 5배)을 최종 채택했다.
const ANONYMOUS_REFERENCE = {
  start: '2017-01-01', end: '2026-08-23', seed: 50000000, finalValue: 3097662480.02,
  returnPct: 6095.32, cagrPct: 53.43, mddPct: 32.25, tradeCount: 1596, winCount: 498, winRatePct: 31.2,
  avgHoldDays: 14.1, profitLossRatio: 5.12, alphaPct: 5897.29,
  benchmarkLabel: 'KOSPI Buy & Hold', benchmarkReturnPct: 198.03,
  exitReasonCounts: { initialStop: 1058, trailingStop: 130, maxHold: 388, maBreak: 10, periodEnd: 10 },
};

function renderAnonymousReference(el) {
  const d = ANONYMOUS_REFERENCE;
  const fmt = v => `₩${Math.round(v).toLocaleString('en-US')}`;
  const reasons = Object.entries(d.exitReasonCounts)
    .map(([k, v]) => `${PAPER_TRADING_EXIT_REASON_LABEL(k)} ${v}${t('countUnit') || '건'}`).join(', ');
  el.innerHTML = `
    <div class="card">
      <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px;margin-bottom:6px;">
        <div style="font-size:13px;font-weight:700;">📌 ${t('referenceResultTitle')}</div>
        <div style="font-size:11.5px;color:var(--text-muted);">${escapeHtml(d.start)} ~ ${escapeHtml(d.end)}</div>
      </div>
      <div class="bt-summary-grid">
        <div class="meta-item"><div class="meta-label">${t('capital')}</div><div class="meta-value">${fmt(d.seed)}</div></div>
        <div class="meta-item"><div class="meta-label">${t('finalValue')}</div><div class="meta-value">${fmt(d.finalValue)}</div></div>
        <div class="meta-item"><div class="meta-label">${t('totalReturn')}</div><div class="meta-value positive">+${d.returnPct.toFixed(1)}%</div></div>
        <div class="meta-item"><div class="meta-label">${t('annualizedReturn')}</div><div class="meta-value positive">+${d.cagrPct.toFixed(1)}%</div></div>
        <div class="meta-item"><div class="meta-label">MDD</div><div class="meta-value negative">-${d.mddPct.toFixed(1)}%</div></div>
        <div class="meta-item"><div class="meta-label">${t('winRate')}</div><div class="meta-value">${d.winRatePct.toFixed(1)}% (${d.winCount}/${d.tradeCount})</div></div>
        <div class="meta-item"><div class="meta-label">${t('avgHoldDays')}</div><div class="meta-value">${d.avgHoldDays}</div></div>
        <div class="meta-item"><div class="meta-label">${t('profitLossRatio')}</div><div class="meta-value">${d.profitLossRatio}</div></div>
        <div class="meta-item"><div class="meta-label">${t('alphaExcessReturn')}</div><div class="meta-value ${d.alphaPct >= 0 ? 'positive' : 'negative'}">${d.alphaPct >= 0 ? '+' : ''}${d.alphaPct.toFixed(1)}%p</div></div>
        <div class="meta-item"><div class="meta-label">${escapeHtml(d.benchmarkLabel)}</div><div class="meta-value positive">+${d.benchmarkReturnPct.toFixed(1)}%</div></div>
      </div>
      <div style="font-size:11.5px;color:var(--text-muted);margin-top:10px;line-height:1.5;">
        ${t('exitReason')}: ${escapeHtml(reasons)}<br>
        ${t('referenceResultNote')}
      </div>
    </div>
    <div class="card">
      <div style="font-size:13px;font-weight:600;margin-bottom:10px;">${t('tradeLog')} (${d.tradeCount})</div>
      <div id="sbt-refanon-trades-body"><div class="loading-msg"><i class="ti ti-loader-2" aria-hidden="true"></i></div></div>
    </div>`;
  loadAnonymousReferenceTrades();
}

async function loadAnonymousReferenceTrades() {
  const body = document.getElementById('sbt-refanon-trades-body');
  if (!body) return;
  try {
    const res = await fetch('/static/anonymous_reference.json');
    if (!res.ok) throw new Error('fetch failed');
    const data = await res.json();
    const exitReasonLabel = r => ({
      stopLoss: t('stopLoss'), conditionExit: t('conditionExit'), periodEnd: t('periodEnd'),
      initialStop: t('exitInitialStop'), breakevenStop: t('exitBreakevenStop'),
      trailingStop: t('exitTrailingStop'), timeStop: t('exitTimeStop'),
      partialProfit: t('exitPartialProfit'), maBreak: t('exitMaBreak'), maxHold: t('exitMaxHold'),
    }[r] || r);
    const fmtPrice = v => `₩${Math.round(v).toLocaleString('en-US')}`;
    body.innerHTML = `
      <div class="pf-table-wrap pf-table-wrap-scroll">
        <table class="pf-table">
          <thead><tr>
            <th>${t('name')}</th><th>${t('code')}</th>
            <th style="text-align:right;">${t('buyDate')}</th><th style="text-align:right;">${t('buyPrice')}</th>
            <th style="text-align:right;">${t('sellDate')}</th><th style="text-align:right;">${t('sellPrice')}</th>
            <th style="text-align:right;">${t('returnPct')}</th><th>${t('exitReason')}</th>
          </tr></thead>
          <tbody>
            ${data.trades.map(tr => `
              <tr>
                <td>${escapeHtml(tr.name)}</td><td>${escapeHtml(tr.code)}</td>
                <td style="text-align:right;">${escapeHtml(tr.entryDate)}</td><td style="text-align:right;">${fmtPrice(tr.entryPrice)}</td>
                <td style="text-align:right;">${escapeHtml(tr.exitDate)}</td><td style="text-align:right;">${fmtPrice(tr.exitPrice)}</td>
                <td style="text-align:right;" class="${tr.pnlPct >= 0 ? 'positive' : 'negative'}">${tr.pnlPct>=0?'+':''}${tr.pnlPct.toFixed(1)}%</td>
                <td>${exitReasonLabel(tr.exitReason)}</td>
              </tr>`).join('')}
          </tbody>
        </table>
      </div>`;
  } catch (e) {
    body.innerHTML = `<div class="error-msg"><i class="ti ti-alert-circle" aria-hidden="true"></i><span>${t('referenceTradesLoadError')}</span></div>`;
  }
}

async function runScreeningBacktest() {
  const market = document.getElementById('sbt-market').value;
  const strategy = document.getElementById('sbt-strategy').value;
  const start = document.getElementById('sbt-start').value;
  const end = document.getElementById('sbt-end').value;
  const stopLossPct = parseFloat(document.getElementById('sbt-stoploss').value);
  const maxPositions = parseInt(document.getElementById('sbt-maxpos').value, 10);
  const seed = parseFloat(document.getElementById('sbt-seed').value);
  const el = document.getElementById('screenbt-result');

  if (!start || !end || start >= end) { alert(t('checkDateRange') || '기간을 확인하세요'); return; }
  if (!seed || seed <= 0) { alert(t('checkCapital') || '시드를 확인하세요'); return; }

  el.innerHTML = `<div class="loading-msg"><i class="ti ti-loader-2" aria-hidden="true"></i>${t('runningScreeningBacktest')}</div>`;
  try {
    const { jobId } = await api('POST', '/api/screening-backtest', {
      market, strategy, start, end, stopLossPct, maxPositions, seed, preset: sbtPreset,
    });
    await pollScreeningBacktestJob(jobId, el);
  } catch (e) {
    el.innerHTML = `<div class="error-msg"><i class="ti ti-alert-circle" aria-hidden="true"></i><span>${escapeHtml(e.message)}</span></div>`;
  }
}

async function pollScreeningBacktestJob(jobId, el) {
  // 국내 전체 유니버스 기준 가격 히스토리를 한 번 받는 데만 로컬 실측 약 9분,
  // 여기에 재평가 시점 수(주 단위)만큼의 계산이 더 붙는다 - 국내 퀀트 백테스트보다
  // 오래 걸릴 수 있어 폴링 한도를 넉넉히 40분으로 둔다.
  const maxAttempts = 480;
  for (let attempt = 1; attempt <= maxAttempts; attempt++) {
    await new Promise(r => setTimeout(r, 5000));
    let data;
    try {
      data = await api('GET', `/api/screening-backtest/${jobId}`);
    } catch (e) {
      el.innerHTML = `<div class="error-msg"><i class="ti ti-alert-circle" aria-hidden="true"></i><span>${escapeHtml(e.message)}</span></div>`;
      return;
    }
    if (data.status === 'done') {
      renderScreeningBacktestResult(data.result);
      return;
    }
    if (data.status === 'error') {
      el.innerHTML = `<div class="error-msg"><i class="ti ti-alert-circle" aria-hidden="true"></i><span>${escapeHtml(data.error || t('errorDuringBacktest') || '백테스트 중 오류가 발생했습니다')}</span></div>`;
      return;
    }
  }
  el.innerHTML = `<div class="error-msg"><i class="ti ti-alert-circle" aria-hidden="true"></i><span>${t('backtestTakingLong') || '백테스트가 예상보다 오래 걸리고 있습니다. 잠시 후 다시 시도해주세요.'}</span></div>`;
}

function renderScreeningBacktestResult(d) {
  const el = document.getElementById('screenbt-result');
  const pnlCls = d.returnPct >= 0 ? 'positive' : 'negative';
  const isUS = d.market === 'US';
  const fmtCap = v => isUS ? `$${Number(v).toLocaleString('en-US')}` : formatKrw(v);
  const fmtPrice = v => isUS ? `$${Number(v).toFixed(2)}` : `₩${Math.round(v).toLocaleString('en-US')}`;
  const exitReasonLabel = r => ({
    stopLoss: t('stopLoss'), conditionExit: t('conditionExit'), periodEnd: t('periodEnd'),
    initialStop: t('exitInitialStop'), breakevenStop: t('exitBreakevenStop'),
    trailingStop: t('exitTrailingStop'), timeStop: t('exitTimeStop'),
    partialProfit: t('exitPartialProfit'), maBreak: t('exitMaBreak'),
  }[r] || r);

  el.innerHTML = `
    <div class="card">
      <div class="bt-summary-grid">
        <div class="meta-item"><div class="meta-label">${t('capital')}</div><div class="meta-value">${fmtCap(d.seed)}</div></div>
        <div class="meta-item"><div class="meta-label">${t('finalValue')}</div><div class="meta-value">${fmtCap(d.finalValue)}</div></div>
        <div class="meta-item"><div class="meta-label">${t('totalReturn')}</div><div class="meta-value ${pnlCls}">${d.returnPct>=0?'+':''}${d.returnPct.toFixed(1)}%</div></div>
        <div class="meta-item"><div class="meta-label">MDD</div><div class="meta-value negative">-${d.mddPct.toFixed(1)}%</div></div>
        <div class="meta-item"><div class="meta-label">${t('winRate')}</div><div class="meta-value">${d.winRatePct != null ? d.winRatePct.toFixed(1) + '%' : '-'} (${d.winCount}/${d.tradeCount})</div></div>
        <div class="meta-item"><div class="meta-label">${t('avgHoldDays')}</div><div class="meta-value">${d.avgHoldDays ?? '-'}</div></div>
        <div class="meta-item"><div class="meta-label">${t('profitLossRatio')}</div><div class="meta-value">${d.profitLossRatio ?? '-'}</div></div>
        <div class="meta-item"><div class="meta-label">${t('alphaExcessReturn')}</div><div class="meta-value ${d.alphaPct >= 0 ? 'positive' : 'negative'}">${d.alphaPct != null ? (d.alphaPct >= 0 ? '+' : '') + d.alphaPct.toFixed(1) + '%p' : '-'}</div></div>
        <div class="meta-item"><div class="meta-label">${escapeHtml(tv(d.benchmark.label))}</div><div class="meta-value ${(d.benchmark.returnPct ?? 0) >= 0 ? 'positive' : 'negative'}">${d.benchmark.returnPct != null ? (d.benchmark.returnPct >= 0 ? '+' : '') + d.benchmark.returnPct.toFixed(1) + '%' : '-'}</div></div>
      </div>
      <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px;margin:14px 0 8px;">
        <div style="font-size:13px;font-weight:600;">${t('equityCurve')} (${escapeHtml(d.strategyLabel)} vs. ${escapeHtml(tv(d.benchmark.label))})</div>
        <button class="btn-secondary" onclick="resetScreeningBacktestZoom()" style="padding:4px 10px;font-size:12px;"><i class="ti ti-zoom-reset" aria-hidden="true"></i> ${t('resetZoom')}</button>
      </div>
      <div class="chart-wrap">
        <canvas id="screenbt-chart" role="img" aria-label="Screening backtest equity curve"></canvas>
      </div>
      <div style="font-size:11px;color:var(--text-muted);margin-top:6px;text-align:center;">${t('scrollZoomDragPan')}</div>
    </div>

    <div class="card">
      <div style="font-size:13px;font-weight:600;margin-bottom:10px;">${t('tradeLog')} (${d.tradeCount})</div>
      ${d.trades.length ? `
      <div class="pf-table-wrap pf-table-wrap-scroll">
        <table class="pf-table">
          <thead><tr>
            <th>${t('name')}</th><th>${t('code')}</th>
            <th style="text-align:right;">${t('buyDate')}</th><th style="text-align:right;">${t('buyPrice')}</th>
            <th style="text-align:right;">${t('sellDate')}</th><th style="text-align:right;">${t('sellPrice')}</th>
            <th style="text-align:right;">${t('returnPct')}</th><th>${t('exitReason')}</th>
          </tr></thead>
          <tbody>
            ${d.trades.map(tr => `
              <tr>
                <td>${escapeHtml(tr.name)}</td><td>${escapeHtml(tr.code)}</td>
                <td style="text-align:right;">${escapeHtml(tr.entryDate)}</td><td style="text-align:right;">${fmtPrice(tr.entryPrice)}</td>
                <td style="text-align:right;">${escapeHtml(tr.exitDate)}</td><td style="text-align:right;">${fmtPrice(tr.exitPrice)}</td>
                <td style="text-align:right;" class="${tr.pnlPct >= 0 ? 'positive' : 'negative'}">${tr.pnlPct>=0?'+':''}${tr.pnlPct.toFixed(1)}%</td>
                <td>${exitReasonLabel(tr.exitReason)}</td>
              </tr>`).join('')}
          </tbody>
        </table>
      </div>` : `<div class="empty-state" style="padding:1.5rem;"><p>${t('noStocksSelected')}</p></div>`}
    </div>`;

  requestAnimationFrame(() => requestAnimationFrame(() => drawScreeningBacktestChart(d.equityCurve, d.seed, d.benchmark)));
}

let screenbtChart = null;
const screenbtPanState = { cleanup: null };

function drawScreeningBacktestChart(curve, seed, benchmark) {
  const canvas = document.getElementById('screenbt-chart');
  if (!canvas || !curve?.length) return;
  if (screenbtChart) screenbtChart.destroy();
  const dates = curve.map(p => p.date);
  const toPct = v => (v - seed) / seed * 100;

  const datasets = [{
    label: t('strategy'),
    data: curve.map((p, i) => ({ x: i, y: toPct(p.value) })),
    borderColor: '#378ADD', backgroundColor: 'rgba(55,138,221,0.12)',
    fill: true, pointRadius: 0, borderWidth: 2, tension: 0.1,
  }];
  if (benchmark?.equityCurve?.length) {
    datasets.push({
      label: tv(benchmark.label),
      data: benchmark.equityCurve.map((p, i) => ({ x: i, y: toPct(p.value) })),
      borderColor: '#97C459', backgroundColor: 'transparent',
      fill: false, pointRadius: 0, borderWidth: 2, borderDash: [5, 4], tension: 0.1,
    });
  }

  screenbtChart = new Chart(canvas, {
    type: 'line',
    data: { datasets },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: {
        legend: { display: true, position: 'top', labels: { boxWidth: 12, font: { size: 11 } } },
        tooltip: {
          callbacks: {
            title: items => items.length ? (dates[Math.round(items[0].parsed.x)] ?? '') : '',
            label: ctx => ` ${ctx.dataset.label}: ${ctx.parsed.y >= 0 ? '+' : ''}${ctx.parsed.y.toFixed(1)}%`,
          },
        },
        zoom: {
          zoom: { wheel: { enabled: true }, pinch: { enabled: true }, mode: 'x' },
          pan: { enabled: false },
          limits: { x: { min: 0, max: Math.max(dates.length - 1, 0), minRange: 1 } },
        },
      },
      scales: {
        y: { ticks: { callback: v => (v >= 0 ? '+' : '') + v.toFixed(0) + '%' }, grid: { color: 'rgba(128,128,128,0.1)' } },
        x: {
          type: 'linear', min: 0, max: Math.max(dates.length - 1, 0),
          ticks: { maxTicksLimit: 8, callback: v => dates[Math.round(v)] ?? '' },
          grid: { display: false },
        },
      },
    },
  });
  attachChartPan(screenbtChart, canvas, screenbtPanState);
}

function resetScreeningBacktestZoom() {
  if (screenbtChart) screenbtChart.resetZoom();
}

// ─── 모의투자 (실시간 자동 페이퍼 트레이딩) ─────────────────────────────────
// 실제 돈 없이 "미너비니 v2" 전략(트렌드템플릿+유동성 진입, ATR 기반 리스크관리
// 청산)을 매일 자동으로 그대로 따라가는 가상 계좌. 무거운 계산은 서버
// 백그라운드 리프레셔가 하고, 이 화면은 이미 반영된 계좌 상태만 폴링해서 보여준다.

const PAPER_TRADING_EXIT_REASON_LABEL = r => ({
  initialStop: t('exitInitialStop'), breakevenStop: t('exitBreakevenStop'),
  trailingStop: t('exitTrailingStop'), timeStop: t('exitTimeStop'), periodEnd: t('periodEnd'),
  partialProfit: t('exitPartialProfit'), maBreak: t('exitMaBreak'), maxHold: t('exitMaxHold'),
}[r] || r);

const PAPER_STRATEGY_LIST = [
  { key: 'minervini_v2', titleKey: 'stratMinerviniV2Title', emoji: '🎯', defaultSeed: 10_000_000 },
  { key: 'minervini_v21', titleKey: 'stratMinerviniV21Title', emoji: '🎯', defaultSeed: 10_000_000 },
  // 슬롯당 고정 금액 상한(2,500만원 = 시드 5천만원/10슬롯의 5배)을 전제로 튜닝된
  // 전략이라 기본 시드도 맞춰둔다.
  { key: 'anonymous', titleKey: 'stratAnonymousTitle', emoji: '🐢', defaultSeed: 50_000_000 },
];

async function loadPaperTrading() {
  const el = document.getElementById('papertrade-body');
  el.innerHTML = `<div class="loading-msg"><i class="ti ti-loader-2" aria-hidden="true"></i></div>`;
  try {
    const results = await Promise.all(
      PAPER_STRATEGY_LIST.map(s => api('GET', `/api/paper-trading/status?strategy=${s.key}`))
    );
    el.innerHTML = PAPER_STRATEGY_LIST.map((s, i) => `<div id="papertrade-panel-${s.key}"></div>`).join('');
    PAPER_STRATEGY_LIST.forEach((s, i) => {
      const panel = document.getElementById(`papertrade-panel-${s.key}`);
      const data = results[i];
      if (!data.exists) {
        renderPaperTradingStart(panel, s);
      } else {
        renderPaperTradingDashboard(panel, data, s);
      }
    });
  } catch (e) {
    el.innerHTML = `<div class="error-msg"><i class="ti ti-alert-circle" aria-hidden="true"></i><span>${escapeHtml(e.message)}</span></div>`;
  }
}

function renderPaperTradingStart(el, strategyInfo) {
  el.innerHTML = `
    <div class="card" style="text-align:center;padding:2.5rem 1.5rem;">
      <div style="font-size:15px;font-weight:700;margin-bottom:6px;">${t('paperTradingIntroTitle')} — ${strategyInfo.emoji} ${t(strategyInfo.titleKey)}</div>
      <div style="font-size:13px;color:var(--text-muted);max-width:560px;margin:0 auto 20px;line-height:1.6;">
        ${t('paperTradingIntroBody')}
      </div>
      <div style="display:flex;justify-content:center;align-items:center;gap:10px;margin-bottom:16px;">
        <label for="pt-start-seed-${strategyInfo.key}" style="font-size:13px;color:var(--text-secondary);">${t('capital')}</label>
        <input type="number" id="pt-start-seed-${strategyInfo.key}" value="${strategyInfo.defaultSeed || 10000000}" step="1000000" min="1" style="width:160px;" />
      </div>
      <button class="bt-preset-btn selected" style="padding:12px 22px;font-size:14px;" onclick="startPaperTrading('${strategyInfo.key}')">
        ${strategyInfo.emoji} ${t(strategyInfo.titleKey)}
      </button>
      <div style="font-size:11.5px;color:var(--text-muted);margin-top:14px;">${t('paperTradingStartNote')}</div>
    </div>`;
}

async function startPaperTrading(strategyKey) {
  const seed = parseFloat(document.getElementById(`pt-start-seed-${strategyKey}`).value);
  if (!seed || seed <= 0) { alert(t('checkCapital') || '시드를 확인하세요'); return; }
  try {
    await api('POST', '/api/paper-trading/start', { strategy: strategyKey, seed });
    await loadPaperTrading();
  } catch (e) {
    alert(e.message);
  }
}

function renderPaperTradingDashboard(el, d, strategyInfo) {
  const pnlCls = d.returnPct >= 0 ? 'positive' : 'negative';
  const fmt = v => `₩${Math.round(v).toLocaleString('en-US')}`;
  el.innerHTML = `
    <div class="card">
      <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px;margin-bottom:6px;">
        <div style="font-size:14px;font-weight:700;">${strategyInfo.emoji} ${t(strategyInfo.titleKey)}</div>
        <div style="font-size:11.5px;color:var(--text-muted);">
          ${t('startedOn')} ${escapeHtml(d.startedOn || '-')} · ${t('lastProcessedDate')} ${escapeHtml(d.lastProcessedDate || '-')}
        </div>
      </div>
      <div class="bt-summary-grid">
        <div class="meta-item"><div class="meta-label">${t('capital')}</div><div class="meta-value">${fmt(d.seed)}</div></div>
        <div class="meta-item"><div class="meta-label">${t('finalValue')}</div><div class="meta-value">${fmt(d.equity)}</div></div>
        <div class="meta-item"><div class="meta-label">${t('totalReturn')}</div><div class="meta-value ${pnlCls}">${d.returnPct>=0?'+':''}${d.returnPct.toFixed(1)}%</div></div>
        <div class="meta-item"><div class="meta-label">${t('currentDrawdown')}</div><div class="meta-value negative">-${d.drawdownPct.toFixed(1)}%</div></div>
        <div class="meta-item"><div class="meta-label">${t('winRate')}</div><div class="meta-value">${d.winRatePct != null ? d.winRatePct.toFixed(1) + '%' : '-'} (${d.tradeCount})</div></div>
        <div class="meta-item"><div class="meta-label">${t('cashBalance')}</div><div class="meta-value">${fmt(d.cash)}</div></div>
      </div>
    </div>

    <div class="card">
      <div style="font-size:13px;font-weight:600;margin-bottom:10px;">${t('openPositions')} (${d.positions.length})</div>
      ${d.positions.length ? `
      <div class="pf-table-wrap">
        <table class="pf-table">
          <thead><tr>
            <th>${t('name')}</th><th>${t('code')}</th>
            <th style="text-align:right;">${t('buyDate')}</th><th style="text-align:right;">${t('buyPrice')}</th>
            <th style="text-align:right;">${t('quantity')}</th>
            <th style="text-align:right;">${t('currentPrice')}</th><th style="text-align:right;">${t('returnPct')}</th>
            <th style="text-align:right;">${t('stopPrice')}</th><th>${t('stopState')}</th>
          </tr></thead>
          <tbody>
            ${d.positions.map(p => `
              <tr>
                <td>${escapeHtml(p.name)}</td><td>${escapeHtml(p.code)}</td>
                <td style="text-align:right;">${escapeHtml(p.entryDate)}</td><td style="text-align:right;">₩${Math.round(p.entryPrice).toLocaleString('en-US')}</td>
                <td style="text-align:right;">${p.shares.toLocaleString('en-US')}${t('sharesUnit')}</td>
                <td style="text-align:right;">₩${Math.round(p.currentPrice).toLocaleString('en-US')}</td>
                <td style="text-align:right;" class="${p.unrealizedPct >= 0 ? 'positive' : 'negative'}">${p.unrealizedPct>=0?'+':''}${p.unrealizedPct.toFixed(1)}%</td>
                <td style="text-align:right;">₩${Math.round(p.stopPrice).toLocaleString('en-US')}</td>
                <td>${PAPER_TRADING_EXIT_REASON_LABEL(p.stopState)}</td>
              </tr>`).join('')}
          </tbody>
        </table>
      </div>` : `<div class="empty-state" style="padding:1.5rem;"><p>${t('noOpenPositions')}</p></div>`}
    </div>

    <div class="card">
      <div style="font-size:13px;font-weight:600;margin-bottom:10px;">${t('tradeLog')} (${d.trades.length})</div>
      ${d.trades.length ? `
      <div class="pf-table-wrap pf-table-wrap-scroll">
        <table class="pf-table">
          <thead><tr>
            <th>${t('name')}</th><th>${t('code')}</th>
            <th style="text-align:right;">${t('buyDate')}</th><th style="text-align:right;">${t('buyPrice')}</th>
            <th style="text-align:right;">${t('quantity')}</th>
            <th style="text-align:right;">${t('sellDate')}</th><th style="text-align:right;">${t('sellPrice')}</th>
            <th style="text-align:right;">${t('returnPct')}</th><th>${t('exitReason')}</th>
          </tr></thead>
          <tbody>
            ${d.trades.map(tr => `
              <tr>
                <td>${escapeHtml(tr.name)}</td><td>${escapeHtml(tr.code)}</td>
                <td style="text-align:right;">${escapeHtml(tr.entryDate)}</td><td style="text-align:right;">₩${Math.round(tr.entryPrice).toLocaleString('en-US')}</td>
                <td style="text-align:right;">${tr.shares.toLocaleString('en-US')}${t('sharesUnit')}</td>
                <td style="text-align:right;">${escapeHtml(tr.exitDate)}</td><td style="text-align:right;">₩${Math.round(tr.exitPrice).toLocaleString('en-US')}</td>
                <td style="text-align:right;" class="${tr.pnlPct >= 0 ? 'positive' : 'negative'}">${tr.pnlPct>=0?'+':''}${tr.pnlPct.toFixed(1)}%</td>
                <td>${PAPER_TRADING_EXIT_REASON_LABEL(tr.exitReason)}</td>
              </tr>`).join('')}
          </tbody>
        </table>
      </div>` : `<div class="empty-state" style="padding:1.5rem;"><p>${t('noTradesYet')}</p></div>`}
    </div>`;
}

// ─── 무한매수법 실전 현황 ────────────────────────────────────────────────────

function applyLiveTickerTargetDefault() {
  const ticker = document.getElementById('live-ticker').value.trim().toUpperCase();
  document.getElementById('live-target').value = TICKER_TARGET_DEFAULTS[ticker] ?? 10;
}

async function addInfinitePosition() {
  const ticker = document.getElementById('live-ticker').value.trim().toUpperCase();
  const version = document.getElementById('live-version').value;
  const splits = parseInt(document.getElementById('live-splits').value, 10);
  const targetReturn = parseFloat(document.getElementById('live-target').value);
  const seed = parseFloat(document.getElementById('live-seed').value);

  if (!ticker) { alert('티커를 입력하세요'); return; }
  if (!seed || seed <= 0) { alert('시드를 입력하세요'); return; }
  if (!splits || splits < 2) { alert('분할수를 확인하세요'); return; }
  if (!targetReturn || targetReturn <= 0) { alert('목표수익률을 확인하세요'); return; }

  try {
    await api('POST', '/api/infinite/positions', { ticker, version, splits, targetReturn, seed });
    document.getElementById('live-ticker').value = '';
    loadInfinitePositions();
  } catch (e) { alert(e.message); }
}

async function deleteInfinitePosition(id) {
  if (!confirm('이 포지션과 매매 기록을 모두 삭제하시겠습니까?')) return;
  await api('DELETE', `/api/infinite/positions/${id}`);
  loadInfinitePositions();
}

async function loadInfinitePositions() {
  const el = document.getElementById('live-positions');
  el.innerHTML = `<div class="loading-msg"><i class="ti ti-loader-2" aria-hidden="true"></i>${t('loading')}</div>`;
  try {
    const positions = await api('GET', '/api/infinite/positions');
    renderInfinitePositions(positions);
  } catch (e) {
    el.innerHTML = `<div class="error-msg"><i class="ti ti-alert-circle" aria-hidden="true"></i><span>${e.message}</span></div>`;
  }
}

function renderInfinitePositions(positions) {
  const el = document.getElementById('live-positions');
  if (!positions.length) {
    el.innerHTML = `<div class="empty-state"><i class="ti ti-infinity" aria-hidden="true"></i><p>${t('noActiveInfinitePositions')}</p><small>${t('enterTickerToStart')}</small></div>`;
    return;
  }

  el.innerHTML = positions.map(p => {
    const pnlCls = p.evalPnl >= 0 ? 'positive' : 'negative';
    const rec = p.recommendation;
    const recBoxCls = rec.lossCutMode ? 'live-rec-box live-rec-warn' : 'live-rec-box';
    return `
    <div class="card" id="live-card-${p.id}">
      <div class="stock-header">
        <div>
          <span class="ticker-badge">${escapeHtml(p.ticker)}</span>
          <span class="cycle-pill">${p.version.toUpperCase()}</span>
          <span class="cycle-pill">${t('cycle')} ${p.cycle}</span>
          ${p.lossCutMode ? `<span class="cycle-pill" style="background:var(--red-bg);color:var(--red);">${t('splitsExhausted')}</span>` : ''}
        </div>
        <button class="btn-icon" onclick="deleteInfinitePosition(${p.id})" aria-label="Delete position"><i class="ti ti-trash" aria-hidden="true"></i></button>
      </div>
      <div class="live-section-label">${t('basicInfo')}</div>
      <div class="bt-summary-grid live-info-grid">
        <div class="meta-item"><div class="meta-label">${t('capital')}</div><div class="meta-value">$${p.seed.toLocaleString('en-US',{maximumFractionDigits:0})}</div></div>
        <div class="meta-item"><div class="meta-label">${t('capitalUsed')}</div><div class="meta-value">$${p.usedSeed.toLocaleString('en-US',{maximumFractionDigits:0})}</div></div>
        <div class="meta-item"><div class="meta-label">${t('splitAmount')}</div><div class="meta-value">$${p.splitAmount.toLocaleString('en-US',{maximumFractionDigits:0})}</div></div>
      </div>

      <div class="live-section-label">${t('positionInfo')}</div>
      <div class="bt-summary-grid live-info-grid">
        <div class="meta-item"><div class="meta-label">${t('avgPrice')}</div><div class="meta-value">${p.avgPrice ? '$' + p.avgPrice.toFixed(2) : '-'}</div></div>
        <div class="meta-item"><div class="meta-label">${t('holdingQty')}</div><div class="meta-value">${p.holdingQty}</div></div>
        <div class="meta-item"><div class="meta-label">${t('buyAmount')}</div><div class="meta-value">$${p.buyAmount.toLocaleString('en-US',{maximumFractionDigits:0})}</div></div>
      </div>

      <div class="live-section-label">${t('infiniteBuyingFormula')}</div>
      <div class="bt-summary-grid live-info-grid">
        <div class="meta-item"><div class="meta-label">T</div><div class="meta-value">${p.tValue}</div></div>
        <div class="meta-item"><div class="meta-label">${t('targetReturn')}</div><div class="meta-value">${p.targetReturnPct}%</div></div>
        <div class="meta-item"><div class="meta-label">${t('starPct')}</div><div class="meta-value">${p.starPct !== null ? p.starPct.toFixed(2) + '%' : '-'}</div></div>
      </div>

      <div class="live-section-label">${t('valuation')}</div>
      <div class="bt-summary-grid live-info-grid">
        <div class="meta-item"><div class="meta-label">${t('price')}</div><div class="meta-value">${p.currentPrice ? '$' + p.currentPrice.toFixed(2) : '-'}</div></div>
        <div class="meta-item"><div class="meta-label">${t('unrealizedPl')}</div><div class="meta-value ${pnlCls}">${p.evalPnl>=0?'+':''}$${Math.abs(p.evalPnl).toLocaleString('en-US',{maximumFractionDigits:2})}</div></div>
        <div class="meta-item"><div class="meta-label">${t('return')}</div><div class="meta-value ${pnlCls}">${p.returnPct>=0?'+':''}${p.returnPct.toFixed(1)}%</div></div>
      </div>

      <div class="${recBoxCls}">
        <div class="live-rec-title"><i class="ti ti-bulb" aria-hidden="true"></i> <span>${t('infiniteBuyingGuide')}</span> <span class="cycle-pill">${p.version.toUpperCase()}</span></div>
        <div class="live-rec-orders">
          ${rec.orders.map(o => `
            <div class="live-rec-order-row">
              <div class="live-rec-pills">
                <span class="pill ${o.action === 'sell' ? 'pill-sell' : 'pill-buy'}">${o.action === 'sell' ? t('sell') : t('buy')}</span>
                <span class="pill pill-rec">${tv(o.orderType)}</span>
                ${o.pct !== undefined ? `<span style="font-size:12px;color:var(--text-secondary);">${o.pct>=0?'+':''}${o.pct}%</span>` : ''}
              </div>
              <div class="live-rec-order-value">
                ${o.price !== null ? '$' + o.price.toFixed(2) : ''}${o.price !== null && o.qty !== null ? ' × ' : ''}${o.qty !== null ? o.qty + ' ' + t('sharesUnit') : ''}
              </div>
            </div>`).join('')}
        </div>
        <div class="live-rec-note">${escapeHtml(rec.note)}</div>
      </div>

      <div class="add-form">
        <input type="date" id="live-trade-date-${p.id}" style="width:150px;" />
        <select id="live-trade-action-${p.id}" style="width:90px;">
          <option value="buy">${t('buy')}</option>
          <option value="sell">${t('sell')}</option>
        </select>
        <input type="number" id="live-trade-price-${p.id}" placeholder="${t('price')} ($)" style="width:110px;" step="0.01" />
        <input type="number" id="live-trade-qty-${p.id}" placeholder="${t('qty')}" style="width:90px;" step="1" min="1" />
        <button class="btn-primary" onclick="addInfiniteTrade(${p.id})"><i class="ti ti-plus" aria-hidden="true"></i> ${t('addTrade')}</button>
        <button class="btn-secondary" onclick="toggleLiveTrades(${p.id})"><i class="ti ti-list" aria-hidden="true"></i> ${t('tradeHistory')} (${p.tradeCount})</button>
      </div>
      <div id="live-trades-${p.id}" style="display:none;"></div>
    </div>`;
  }).join('');
}

async function addInfiniteTrade(positionId) {
  const date = document.getElementById(`live-trade-date-${positionId}`).value;
  const action = document.getElementById(`live-trade-action-${positionId}`).value;
  const price = parseFloat(document.getElementById(`live-trade-price-${positionId}`).value);
  const qty = parseInt(document.getElementById(`live-trade-qty-${positionId}`).value, 10);

  if (!date) { alert('날짜를 입력하세요'); return; }
  if (!price || price <= 0) { alert('가격을 입력하세요'); return; }
  if (!qty || qty <= 0) { alert('수량을 입력하세요'); return; }

  const wasOpen = document.getElementById(`live-trades-${positionId}`)?.style.display !== 'none';
  try {
    await api('POST', `/api/infinite/positions/${positionId}/trades`, { date, action, price, qty });
    await loadInfinitePositions();
    if (wasOpen) await showLiveTrades(positionId);
  } catch (e) { alert(e.message); }
}

async function toggleLiveTrades(positionId) {
  const tradesEl = document.getElementById(`live-trades-${positionId}`);
  if (!tradesEl) return;
  if (tradesEl.style.display === 'none') {
    await showLiveTrades(positionId);
  } else {
    tradesEl.style.display = 'none';
  }
}

async function showLiveTrades(positionId) {
  const tradesEl = document.getElementById(`live-trades-${positionId}`);
  tradesEl.style.display = 'block';
  tradesEl.innerHTML = `<div class="loading-msg"><i class="ti ti-loader-2" aria-hidden="true"></i>${t('loading')}</div>`;
  try {
    const trades = await api('GET', `/api/infinite/positions/${positionId}/trades`);
    if (!trades.length) {
      tradesEl.innerHTML = `<div class="empty-state"><p>${t('noTradeHistory')}</p></div>`;
      return;
    }
    tradesEl.innerHTML = `
      <div class="pf-table-wrap">
        <table class="pf-table">
          <thead><tr><th>${t('date')}</th><th>${t('type')}</th><th>${t('price')}</th><th>${t('qty')}</th><th></th></tr></thead>
          <tbody>
            ${trades.slice().reverse().map(t6 => `
              <tr>
                <td>${t6.date}</td>
                <td><span class="${t6.action === 'buy' ? 'negative' : 'positive'}" style="font-weight:600;">${t6.action === 'buy' ? t('buy') : t('sell')}</span></td>
                <td>$${t6.price.toFixed(2)}</td>
                <td>${t6.qty}</td>
                <td><button class="btn-icon" onclick="deleteInfiniteTrade(${positionId}, ${t6.id})" aria-label="Delete trade"><i class="ti ti-trash" aria-hidden="true"></i></button></td>
              </tr>`).join('')}
          </tbody>
        </table>
      </div>`;
  } catch (e) {
    tradesEl.innerHTML = `<div class="error-msg"><span>${e.message}</span></div>`;
  }
}

async function deleteInfiniteTrade(positionId, tradeId) {
  if (!confirm('이 매매 기록을 삭제하시겠습니까?')) return;
  await api('DELETE', `/api/infinite/positions/${positionId}/trades/${tradeId}`);
  await loadInfinitePositions();
  await showLiveTrades(positionId);
}

// ─── 실험실(종목/지수 비교) ───────────────────────────────────────────────────

const LAB_COLORS = ['#378ADD', '#E24B4A', '#639922', '#EF9F27', '#8b5fbf', '#00a99d', '#d6606d', '#5c6bc0'];
let labTickers = [];
let labSeriesData = null;
let labChart = null;
const labPanState = { cleanup: null };
let labConditionRows = [];
let labConditionRowSeq = 0;
let labCombinator = 'AND';

// 종목/지수마다 값의 단위가 다르므로(지수=포인트, 개별종목·ETF=달러, 금리=%),
// 축을 단위별로 나눠 표시한다. ^TNX 등 금리류만 예외적으로 %이고, 나머지
// ^ 접두사는 지수(포인트)로 취급한다.
const LAB_RATE_TICKERS = new Set(['^TNX', '^IRX', '^FVX', '^TYX']);
const LAB_UNIT_ORDER = ['pt', 'pct', 'usd'];
const LAB_UNIT_LABEL_KEY = { pt: 'unitPoints', usd: 'unitDollars', pct: 'unitRate' };

function getLabUnit(ticker) {
  if (LAB_RATE_TICKERS.has(ticker)) return 'pct';
  if (ticker.startsWith('^')) return 'pt';
  return 'usd';
}

function formatLabValue(v, unit) {
  if (v === null || v === undefined) return '-';
  if (unit === 'pct') return v.toFixed(2) + '%';
  if (unit === 'usd') return '$' + v.toLocaleString('en-US', { maximumFractionDigits: 2 });
  return v.toLocaleString('en-US', { maximumFractionDigits: 2 });
}

const labRegionPlugin = {
  id: 'labRegionPlugin',
  beforeDatasetsDraw(chart) {
    const regions = chart.__labRegions;
    if (!regions || !regions.length) return;
    const { ctx, chartArea, scales } = chart;
    const xScale = scales.x;
    ctx.save();
    ctx.fillStyle = 'rgba(216,75,74,0.18)';
    regions.forEach(r => {
      const x1 = xScale.getPixelForValue(r.startIdx);
      const x2 = xScale.getPixelForValue(r.endIdx);
      const left = Math.min(x1, x2);
      const width = Math.max(Math.abs(x2 - x1), 2);
      ctx.fillRect(left, chartArea.top, width, chartArea.bottom - chartArea.top);
    });
    ctx.restore();
  },
};
Chart.register(labRegionPlugin);
// CDN의 UMD 빌드는 보통 Chart 전역을 감지해 자동 등록되지만, 로드 순서 등의
// 이유로 자동 등록되지 않는 경우를 대비해 명시적으로 한 번 더 등록한다.
// (이미 등록돼 있으면 Chart.js가 무시하므로 안전하다.)
if (typeof window.ChartZoom !== 'undefined') {
  Chart.register(window.ChartZoom);
}

function initLabTab() {
  if (labTickers.length === 0) {
    labTickers = ['^IXIC', '^TNX'];
    renderLabChips();
  }
  const startEl = document.getElementById('lab-start');
  const endEl = document.getElementById('lab-end');
  if (!endEl.value) endEl.value = new Date().toISOString().slice(0, 10);
  if (!startEl.value) startEl.value = '2010-01-01';
}

function addLabTicker() {
  const input = document.getElementById('lab-ticker-input');
  const ticker = input.value.trim().toUpperCase();
  if (!ticker) return;
  if (labTickers.includes(ticker)) { showToast(`${ticker} ${t('alreadyAdded')}`); return; }
  if (labTickers.length >= 8) { showToast(t('canCompareUpTo8')); return; }
  labTickers.push(ticker);
  input.value = '';
  renderLabChips();
}

function removeLabTicker(ticker) {
  labTickers = labTickers.filter(tk => tk !== ticker);
  renderLabChips();
}

function renderLabChips() {
  const el = document.getElementById('lab-ticker-chips');
  el.innerHTML = labTickers.map((tk, i) => `
    <span class="lab-chip">
      <span class="lab-chip-swatch" style="background:${LAB_COLORS[i % LAB_COLORS.length]};"></span>
      ${escapeHtml(tk)}
      <button type="button" onclick="removeLabTicker('${tk}')" aria-label="Remove ${tk}"><i class="ti ti-x" aria-hidden="true"></i></button>
    </span>`).join('') || `<span style="font-size:12px;color:var(--text-muted);">${t('addTickerIndexToCompare')}</span>`;
}

let labInterval = 'daily';
let labLogScale = false;

async function runLabCompare() {
  const start = document.getElementById('lab-start').value;
  const end = document.getElementById('lab-end').value;
  const el = document.getElementById('lab-result');

  if (!labTickers.length) { alert('티커/지수를 1개 이상 추가하세요'); return; }
  if (!start || !end) { alert('시작일과 종료일을 입력하세요'); return; }

  el.innerHTML = `<div class="loading-msg"><i class="ti ti-loader-2" aria-hidden="true"></i>${t('loadingPrices')}</div>`;
  try {
    const data = await api('POST', '/api/lab/series', { tickers: labTickers, start, end });
    labSeriesData = data;
    renderLabResult(data);
  } catch (e) {
    el.innerHTML = `<div class="error-msg"><i class="ti ti-alert-circle" aria-hidden="true"></i><span>${e.message}</span></div>`;
  }
}

const LAB_INTERVALS = [
  { key: 'daily', labelKey: 'daily' },
  { key: 'weekly', labelKey: 'weekly' },
  { key: 'monthly', labelKey: 'monthly' },
  { key: 'yearly', labelKey: 'yearly' },
];

function renderLabResult(data) {
  const el = document.getElementById('lab-result');
  const invalid = data.invalidTickers || [];

  el.innerHTML = `
    ${invalid.length ? `
      <div class="error-msg">
        <i class="ti ti-alert-circle" aria-hidden="true"></i>
        <span>
        다음 티커는 데이터를 찾을 수 없습니다: ${invalid.map(escapeHtml).join(', ')} —
        실제 존재하는 심볼인지 확인하세요 (지수는 ^ 접두사가 필요합니다. 예: 나스닥종합 ^IXIC, S&amp;P500 ^GSPC, 10년물 국채금리 ^TNX)
        </span>
      </div>` : ''}

    <div class="card">
      <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px;margin-bottom:10px;">
        <div style="font-size:13px;font-weight:600;">${t('compareAxisPerUnit')}</div>
        <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;">
          <div class="lab-interval-group">
            ${LAB_INTERVALS.map(iv => `<button type="button" class="lab-interval-btn ${iv.key === labInterval ? 'active' : ''}" data-interval="${iv.key}" onclick="setLabInterval('${iv.key}')">${t(iv.labelKey)}</button>`).join('')}
          </div>
          <label style="display:flex;align-items:center;gap:5px;font-size:12px;color:var(--text-secondary);cursor:pointer;">
            <input type="checkbox" id="lab-log-scale" ${labLogScale ? 'checked' : ''} onchange="toggleLabLogScale()" /> ${t('logScale')}
          </label>
          <button class="btn-secondary" onclick="resetLabZoom()" style="padding:4px 10px;font-size:12px;"><i class="ti ti-zoom-reset" aria-hidden="true"></i> ${t('resetZoom')}</button>
        </div>
      </div>
      <div class="chart-wrap">
        <canvas id="lab-chart" role="img" aria-label="Comparison chart"></canvas>
      </div>
      <div style="font-size:11px;color:var(--text-muted);margin-top:6px;text-align:center;">${t('scrollZoomDragPan')}</div>
    </div>

    <div class="card">
      <div style="font-size:13px;font-weight:600;margin-bottom:10px;">${t('findConditionRanges')}</div>
      <div id="lab-cond-rows" style="display:flex;flex-direction:column;gap:8px;"></div>
      <div style="display:flex;align-items:center;gap:14px;flex-wrap:wrap;margin-top:10px;">
        <button class="btn-secondary" onclick="addLabConditionRow()" style="padding:4px 10px;font-size:12px;"><i class="ti ti-plus" aria-hidden="true"></i> ${t('addCondition')}</button>
        <div id="lab-cond-combinator-wrap" style="display:none;align-items:center;gap:10px;font-size:12px;color:var(--text-secondary);">
          ${t('combine')}:
          <label style="display:flex;align-items:center;gap:4px;cursor:pointer;">
            <input type="radio" name="lab-cond-combinator" value="AND" onchange="setLabCombinator('AND')" /> ${t('andAllMatch')}
          </label>
          <label style="display:flex;align-items:center;gap:4px;cursor:pointer;">
            <input type="radio" name="lab-cond-combinator" value="OR" onchange="setLabCombinator('OR')" /> ${t('orAnyMatch')}
          </label>
        </div>
        <button class="btn-primary" onclick="applyLabCondition()"><i class="ti ti-highlight" aria-hidden="true"></i> ${t('highlightRanges')}</button>
        <button class="btn-secondary" onclick="clearLabCondition()"><i class="ti ti-x" aria-hidden="true"></i> ${t('reset')}</button>
      </div>
      <div id="lab-regions"></div>
    </div>
  `;
  labConditionRows = [{ id: labConditionRowSeq++, ticker: data.series[0] ? data.series[0].ticker : '', metric: 'change', op: 'lte', threshold: -3 }];
  labCombinator = 'AND';
  renderLabConditionRowsUi();
  // 두 번의 requestAnimationFrame으로 레이아웃/페인트가 끝난 뒤 그린다(이유는
  // 스크리닝 상세 모달의 같은 패턴 주석 참고 - ResizeObserver 반복 리사이즈로
  // 인한 화면 흔들림 방지).
  requestAnimationFrame(() => requestAnimationFrame(() => drawLabChart(getLabDisplayData(), [])));
}

function renderLabConditionRowsUi() {
  const wrap = document.getElementById('lab-cond-rows');
  if (!wrap) return;
  const data = getLabDisplayData();

  wrap.innerHTML = labConditionRows.map((row, i) => `
    <div class="lab-cond-row" data-row-id="${row.id}">
      ${labConditionRows.length > 1 ? `<span style="font-size:11px;color:var(--text-secondary);flex:0 0 auto;">#${i + 1}</span>` : ''}
      <select id="lab-cond-ticker-${row.id}">
        ${data.series.map(s => `<option value="${escapeHtml(s.ticker)}" ${s.ticker === row.ticker ? 'selected' : ''}>${escapeHtml(s.ticker)}</option>`).join('')}
      </select>
      <select id="lab-cond-metric-${row.id}">
        <option value="change" ${row.metric === 'change' ? 'selected' : ''}>${t('changeVsPriorPeriod')}</option>
        <option value="close" ${row.metric === 'close' ? 'selected' : ''}>${t('valueClose')}</option>
      </select>
      <select id="lab-cond-op-${row.id}">
        <option value="lte" ${row.op === 'lte' ? 'selected' : ''}>≤</option>
        <option value="lt" ${row.op === 'lt' ? 'selected' : ''}>&lt;</option>
        <option value="gte" ${row.op === 'gte' ? 'selected' : ''}>≥</option>
        <option value="gt" ${row.op === 'gt' ? 'selected' : ''}>&gt;</option>
      </select>
      <input type="number" id="lab-cond-threshold-${row.id}" placeholder="e.g. -3 or 4.5" step="0.01" value="${row.threshold}" />
      <button class="btn-secondary" onclick="removeLabConditionRow(${row.id})" ${labConditionRows.length <= 1 ? 'disabled' : ''} style="flex:0 0 auto;padding:4px 8px;" title="Remove condition"><i class="ti ti-trash" aria-hidden="true"></i></button>
    </div>
  `).join('');

  const comboWrap = document.getElementById('lab-cond-combinator-wrap');
  if (comboWrap) {
    comboWrap.style.display = labConditionRows.length > 1 ? 'flex' : 'none';
    comboWrap.querySelectorAll('input[name="lab-cond-combinator"]').forEach(r => {
      r.checked = r.value === labCombinator;
    });
  }
}

function syncLabConditionRowsFromDom() {
  labConditionRows.forEach(row => {
    const tEl = document.getElementById(`lab-cond-ticker-${row.id}`);
    const m = document.getElementById(`lab-cond-metric-${row.id}`);
    const o = document.getElementById(`lab-cond-op-${row.id}`);
    const th = document.getElementById(`lab-cond-threshold-${row.id}`);
    if (tEl) row.ticker = tEl.value;
    if (m) row.metric = m.value;
    if (o) row.op = o.value;
    if (th) row.threshold = th.value;
  });
}

function addLabConditionRow() {
  syncLabConditionRowsFromDom();
  const data = getLabDisplayData();
  labConditionRows.push({ id: labConditionRowSeq++, ticker: data.series[0] ? data.series[0].ticker : '', metric: 'change', op: 'lte', threshold: -3 });
  renderLabConditionRowsUi();
}

function removeLabConditionRow(id) {
  syncLabConditionRowsFromDom();
  if (labConditionRows.length <= 1) return;
  labConditionRows = labConditionRows.filter(r => r.id !== id);
  renderLabConditionRowsUi();
}

function setLabCombinator(v) {
  labCombinator = v;
}

function labBucketKey(dateStr, interval) {
  if (interval === 'monthly') return dateStr.slice(0, 7);
  if (interval === 'yearly') return dateStr.slice(0, 4);
  if (interval === 'weekly') {
    const d = new Date(dateStr + 'T00:00:00Z');
    const day = d.getUTCDay() || 7;
    d.setUTCDate(d.getUTCDate() + 4 - day);
    const yearStart = new Date(Date.UTC(d.getUTCFullYear(), 0, 1));
    const week = Math.ceil((((d - yearStart) / 86400000) + 1) / 7);
    return `${d.getUTCFullYear()}-W${String(week).padStart(2, '0')}`;
  }
  return dateStr;
}

function getLabDisplayData() {
  if (!labSeriesData) return { dates: [], series: [] };
  if (labInterval === 'daily') return labSeriesData;

  const lastIdxByBucket = new Map();
  labSeriesData.dates.forEach((d, i) => { lastIdxByBucket.set(labBucketKey(d, labInterval), i); });
  const idxList = Array.from(lastIdxByBucket.values()).sort((a, b) => a - b);

  return {
    dates: idxList.map(i => labSeriesData.dates[i]),
    series: labSeriesData.series.map(s => ({ ticker: s.ticker, closes: idxList.map(i => s.closes[i]) })),
  };
}

function setLabInterval(interval) {
  labInterval = interval;
  document.querySelectorAll('.lab-interval-btn').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.interval === interval);
  });
  const regionsEl = document.getElementById('lab-regions');
  if (regionsEl) regionsEl.innerHTML = '';
  drawLabChart(getLabDisplayData(), []);
}

function toggleLabLogScale() {
  labLogScale = document.getElementById('lab-log-scale').checked;
  drawLabChart(getLabDisplayData(), labChart ? labChart.__labRegions : []);
}

function resetLabZoom() {
  if (labChart) labChart.resetZoom();
}

function drawLabChart(data, regions) {
  const canvas = document.getElementById('lab-chart');
  if (!canvas) return;
  if (labChart) labChart.destroy();

  const dates = data.dates;
  const unitsPresent = LAB_UNIT_ORDER.filter(u => data.series.some(s => getLabUnit(s.ticker) === u));

  const datasets = data.series.map((s, i) => {
    const unit = getLabUnit(s.ticker);
    return {
      label: s.ticker,
      data: s.closes.map((v, idx) => ({ x: idx, y: v })),
      yAxisID: `y-${unit}`,
      borderColor: LAB_COLORS[i % LAB_COLORS.length],
      backgroundColor: 'transparent',
      fill: false, pointRadius: 0, borderWidth: 2, tension: 0.1, spanGaps: true,
    };
  });

  const scales = {
    x: {
      type: 'linear',
      min: 0, max: Math.max(dates.length - 1, 0),
      ticks: { maxTicksLimit: 8, callback: v => dates[Math.round(v)] ?? '' },
      grid: { display: false },
    },
  };
  unitsPresent.forEach((unit, i) => {
    scales[`y-${unit}`] = {
      type: labLogScale ? 'logarithmic' : 'linear',
      position: i === 0 ? 'left' : 'right',
      grid: { drawOnChartArea: i === 0, color: 'rgba(128,128,128,0.1)' },
      ticks: { callback: v => formatLabValue(Number(v), unit) },
      title: { display: true, text: t(LAB_UNIT_LABEL_KEY[unit]), font: { size: 11 } },
    };
  });

  labChart = new Chart(canvas, {
    type: 'line',
    data: { datasets },
    options: {
      responsive: true, maintainAspectRatio: false,
      interaction: { mode: 'index', intersect: false },
      plugins: {
        legend: { display: true, position: 'top', labels: { boxWidth: 12, font: { size: 11 } } },
        tooltip: {
          callbacks: {
            title: items => items.length ? (dates[Math.round(items[0].parsed.x)] ?? '') : '',
            label: ctx => ` ${ctx.dataset.label}: ${formatLabValue(ctx.parsed.y, getLabUnit(ctx.dataset.label))}`,
          },
        },
        zoom: {
          zoom: { wheel: { enabled: true }, pinch: { enabled: true }, mode: 'x' },
          // 플러그인 내장 드래그-팬 감지가 환경에 따라 씹히는 경우가 있어,
          // 아래에서 mousedown/mousemove로 직접 chart.pan()을 호출하는
          // 커스텀 팬으로 대체한다. 내장 팬은 꺼서 이중 동작을 막는다.
          pan: { enabled: false },
          limits: { x: { min: 0, max: Math.max(dates.length - 1, 0), minRange: 5 } },
        },
      },
      scales,
    },
  });
  labChart.__labRegions = regions || [];
  labChart.update();
  attachChartPan(labChart, canvas, labPanState);
}

// chartjs-plugin-zoom의 내장 드래그-팬 감지가 (로드 순서/이벤트 캡처 등의
// 이유로) 동작하지 않는 경우를 대비해, 마우스 드래그를 직접 감지해 플러그인의
// 공개 API인 chart.pan()을 호출하는 방식으로 확실하게 동작시킨다.
// state는 차트별로 독립된 정리(cleanup) 함수를 들고 있는 { cleanup } 객체로,
// 화면에 동시에 여러 개의 확대/축소 차트가 있어도 서로 리스너를 덮어쓰지 않게 한다.
function attachChartPan(chart, canvas, state) {
  if (state.cleanup) {
    state.cleanup();
    state.cleanup = null;
  }

  let dragging = false;
  let lastX = 0;

  const onDown = (e) => {
    dragging = true;
    lastX = e.clientX;
    canvas.style.cursor = 'grabbing';
    e.preventDefault();
  };
  const onMove = (e) => {
    if (!dragging) return;
    const dx = e.clientX - lastX;
    lastX = e.clientX;
    if (dx !== 0) chart.pan({ x: dx }, undefined, 'none');
  };
  const onUp = () => {
    dragging = false;
    canvas.style.cursor = 'grab';
  };

  canvas.style.cursor = 'grab';
  canvas.addEventListener('mousedown', onDown);
  window.addEventListener('mousemove', onMove);
  window.addEventListener('mouseup', onUp);

  state.cleanup = () => {
    canvas.removeEventListener('mousedown', onDown);
    window.removeEventListener('mousemove', onMove);
    window.removeEventListener('mouseup', onUp);
  };
}

function computeLabFlags(closes, metric, op, threshold) {
  const values = metric === 'change'
    ? closes.map((v, i) => {
        const prev = i > 0 ? closes[i - 1] : null;
        if (v === null || v === undefined || prev === null || prev === undefined || prev === 0) return null;
        return (v - prev) / prev * 100;
      })
    : closes;

  const cmp = {
    gte: (v, t) => v >= t, gt: (v, t) => v > t,
    lte: (v, t) => v <= t, lt: (v, t) => v < t,
  }[op];

  return values.map(v => (v === null || v === undefined) ? false : cmp(v, threshold));
}

function flagsToRegions(dates, flags) {
  const regions = [];
  let startIdx = null;
  for (let i = 0; i <= flags.length; i++) {
    const on = i < flags.length && flags[i];
    if (on && startIdx === null) startIdx = i;
    if (!on && startIdx !== null) {
      regions.push({ startIdx, endIdx: i - 1, startDate: dates[startIdx], endDate: dates[i - 1], days: i - startIdx });
      startIdx = null;
    }
  }
  return regions;
}

function applyLabCondition() {
  if (!labSeriesData) return;
  syncLabConditionRowsFromDom();

  const displayData = getLabDisplayData();
  const combinator = labConditionRows.length > 1 ? labCombinator : 'AND';

  const perConditionFlags = [];
  const summaries = [];
  for (const row of labConditionRows) {
    const threshold = parseFloat(row.threshold);
    if (isNaN(threshold)) { alert('모든 조건에 기준값을 입력하세요'); return; }
    const series = displayData.series.find(s => s.ticker === row.ticker);
    if (!series) continue;

    perConditionFlags.push(computeLabFlags(series.closes, row.metric, row.op, threshold));
    const metricLabel = row.metric === 'change' ? t('changePct') : t('valueClose2');
    const opLabel = { gte: t('orMore'), gt: t('moreThan'), lte: t('orLess'), lt: t('lessThan') }[row.op];
    summaries.push(`${row.ticker} ${metricLabel} ${threshold} ${opLabel}`);
  }
  if (!perConditionFlags.length) return;

  const combined = displayData.dates.map((_, i) =>
    combinator === 'OR'
      ? perConditionFlags.some(flags => flags[i])
      : perConditionFlags.every(flags => flags[i])
  );

  const regions = flagsToRegions(displayData.dates, combined);
  drawLabChart(displayData, regions);
  renderLabRegions(regions, summaries, combinator);
}

function clearLabCondition() {
  if (!labSeriesData) return;
  drawLabChart(getLabDisplayData(), []);
  document.getElementById('lab-regions').innerHTML = '';
}

function renderLabRegions(regions, summaries, combinator) {
  const el = document.getElementById('lab-regions');
  const joiner = combinator === 'OR' ? ` ${t('or')} ` : ` ${t('and')} `;
  const summary = `${t('rangesMatching')} ${summaries.map(escapeHtml).join(joiner)}: ${regions.length}`;

  if (!regions.length) {
    el.innerHTML = `<div class="empty-state" style="padding:1.5rem;"><p>${summary}</p><small>${t('noRangesMatchCondition')}</small></div>`;
    return;
  }

  el.innerHTML = `
    <div style="font-size:12px;color:var(--text-secondary);margin-top:14px;margin-bottom:6px;">${summary}</div>
    <div class="lab-region-list">
      ${regions.map(r => `
        <div class="lab-region-item">
          <span>${r.startDate}${r.startDate !== r.endDate ? ' ~ ' + r.endDate : ''}</span>
          <span style="color:var(--text-secondary);">${r.days}d</span>
        </div>`).join('')}
    </div>`;
}

// ─── 유틸 ────────────────────────────────────────────────────────────────────

function showToast(msg) {
  const t = document.createElement('div');
  t.className = 'toast';
  t.textContent = msg;
  document.body.appendChild(t);
  setTimeout(() => t.remove(), 2500);
}

// 알림 상태 30초마다 갱신
setInterval(() => {
  const el = document.getElementById('alerts-content');
  if (el && document.getElementById('sec-alerts').classList.contains('active')) loadAlerts();
}, 30000);
