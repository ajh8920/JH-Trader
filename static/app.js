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
        <strong>Finnhub API Key</strong>
        <p>Copy your API key from the <a href="https://finnhub.io/dashboard" target="_blank" rel="noopener">finnhub.io dashboard</a></p>
      </div>
      <div class="api-key-row">
        <input type="text" id="api-key-input" placeholder="Paste API key" style="width:280px;" />
        <button class="btn-primary" onclick="saveApiKey()">Save &amp; Verify</button>
        <button class="btn-secondary" onclick="document.getElementById('api-key-banner').remove()">Cancel</button>
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
  btn.textContent = 'Verifying...';
  if (errEl) { errEl.style.display = 'none'; errEl.textContent = ''; }

  try {
    await api('POST', '/api/settings/key', { key });
    document.getElementById('api-key-banner')?.remove();
    showToast('API key saved');
  } catch (e) {
    if (errEl) { errEl.textContent = e.message; errEl.style.display = 'block'; }
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = 'Save & Verify'; }
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
}

document.addEventListener('DOMContentLoaded', () => loadMacro());

// ─── 매크로(주요 시황) ────────────────────────────────────────────────────────

async function loadMacro() {
  const el = document.getElementById('macro-content');
  if (!el || el.dataset.loaded === '1') return;
  el.innerHTML = `<div class="loading-msg"><i class="ti ti-loader-2" aria-hidden="true"></i>Loading market overview...</div>`;

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
          <div class="error-msg"><i class="ti ti-alert-circle" aria-hidden="true"></i>${escapeHtml(e.message)}</div>
          <button class="btn-secondary" onclick="refreshMacro()"><i class="ti ti-refresh" aria-hidden="true"></i> Retry</button>`;
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
      ${asOfText ? `<span style="font-size:12px;color:var(--text-muted);margin-right:auto;">As of ${asOfText}</span>` : ''}
      <button class="btn-secondary" onclick="refreshMacro()"><i class="ti ti-refresh" aria-hidden="true"></i> Refresh</button>
    </div>
    ${data.fearGreed ? renderFearGreedCard(data.fearGreed) : ''}
    ${Object.entries(groups).map(([group, list]) => `
      <div class="card">
        <div style="font-size:12px;font-weight:600;color:var(--text-secondary);margin-bottom:10px;text-transform:uppercase;letter-spacing:0.04em;">${escapeHtml(group)}</div>
        <div class="macro-grid">
          ${list.map(m => {
            const hasPrice = m.price !== null && m.price !== undefined;
            const isPos = hasPrice && m.change >= 0;
            const priceText = hasPrice ? formatMacroPrice(m.price, m.unit) : null;
            const changeText = hasPrice ? formatMacroChange(m.change, m.changePct, m.unit) : null;
            const hasSeries = hasPrice && Array.isArray(m.series) && m.series.length > 1;
            return `
            <div class="macro-item">
              <div class="macro-name">${escapeHtml(m.name)}</div>
              <div class="macro-ticker">${escapeHtml(m.ticker)}</div>
              ${hasPrice ? `
                <div class="macro-price">${priceText}</div>
                <div class="macro-change ${isPos ? 'positive' : 'negative'}">
                  <i class="ti ti-trending-${isPos ? 'up' : 'down'}" aria-hidden="true"></i>
                  ${changeText}
                </div>
                ${hasSeries ? buildMacroSparklineSvg(m.series, isPos) : ''}
              ` : `<div class="macro-price" style="color:var(--text-muted);font-size:13px;">No data</div>`}
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
        <span class="ticker-name">${escapeHtml(m.name)}</span>
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
  'extreme fear': { label: 'Extreme Fear', color: '#e5484d' },
  'fear': { label: 'Fear', color: '#f0a058' },
  'neutral': { label: 'Neutral', color: '#dfb945' },
  'greed': { label: 'Greed', color: '#8fc46a' },
  'extreme greed': { label: 'Extreme Greed', color: '#38a973' },
};

function fearGreedRatingInfo(rating) {
  return FEAR_GREED_RATING_KO[(rating || '').toLowerCase()] || { label: rating || '-', color: 'var(--text-muted)' };
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
    <svg viewBox="0 0 ${size} ${size}" role="img" aria-label="공포탐욕지수 ${Math.round(clampedScore)}, ${rating.label}">
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
    { label: 'Previous Close', value: fg.previousClose },
    { label: '1 Week Ago', value: fg.previousWeek },
    { label: '1 Month Ago', value: fg.previousMonth },
    { label: '1 Year Ago', value: fg.previousYear },
  ];
  return `
    <div class="card fear-greed-card">
      <div style="font-size:12px;font-weight:600;color:var(--text-secondary);margin-bottom:4px;text-transform:uppercase;letter-spacing:0.04em;">Fear &amp; Greed Index (CNN)</div>
      <div class="fear-greed-body">
        <div class="fear-greed-gauge">
          <div class="fg-live-badge"><span class="fg-live-dot" aria-hidden="true"></span>LIVE</div>
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
  el.innerHTML = `<div class="loading-msg"><i class="ti ti-loader-2" aria-hidden="true"></i>Loading ${ticker} data...</div>`;

  try {
    const data = await api('GET', `/api/stock/${ticker}`);
    renderStockCard(data, el);
  } catch (e) {
    el.innerHTML = `<div class="error-msg"><i class="ti ti-alert-circle" aria-hidden="true"></i>${e.message}</div>`;
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
          ${d.industry ? `<div class="stock-industry">${escapeHtml(d.industry)}</div>` : ''}
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
        <div class="ohlc-item"><div class="ohlc-label">Open</div><div class="ohlc-value">$${d.open.toFixed(2)}</div></div>
        <div class="ohlc-item"><div class="ohlc-label">High</div><div class="ohlc-value positive">$${d.high.toFixed(2)}</div></div>
        <div class="ohlc-item"><div class="ohlc-label">Low</div><div class="ohlc-value negative">$${d.low.toFixed(2)}</div></div>
        <div class="ohlc-item"><div class="ohlc-label">Prev Close</div><div class="ohlc-value">$${d.prevClose.toFixed(2)}</div></div>
      </div>

      ${d.targetMean ? `
        <div class="meta-grid">
          <div class="meta-item">
            <div class="meta-label">Avg Target</div>
            <div class="meta-value" style="color:${upsideColor};">$${d.targetMean.toFixed(2)}</div>
          </div>
          <div class="meta-item">
            <div class="meta-label">Low Target</div>
            <div class="meta-value">$${d.targetLow?.toFixed(2) ?? '-'}</div>
          </div>
          <div class="meta-item">
            <div class="meta-label">High Target</div>
            <div class="meta-value">$${d.targetHigh?.toFixed(2) ?? '-'}</div>
          </div>
        </div>
        <div class="upside-bar-wrap">
          <div class="bar-label">
            <span>Low $${d.targetLow?.toFixed(0)}</span>
            <span style="color:${upsideColor};font-weight:600;">Upside ${upside >= 0 ? '+' : ''}${upside.toFixed(1)}%</span>
            <span>High $${d.targetHigh?.toFixed(0)}</span>
          </div>
          <div class="bar-track">
            <div class="bar-fill" style="width:${barPct}%;background:${upside >= 0 ? '#639922' : '#E24B4A'};"></div>
          </div>
          <div class="bar-hint">Current price position within target range ${d.targetUpdated ? '· Updated ' + d.targetUpdated : ''}</div>
        </div>
      ` : `<div style="padding:14px 0;color:var(--text-secondary);font-size:13px;"><i class="ti ti-info-circle" aria-hidden="true"></i> No target price data</div>`}

      ${total > 0 ? `
        <div class="analyst-breakdown">
          <div class="breakdown-header">
            <span>${total} Analysts${d.recPeriod ? ' · ' + d.recPeriod : ''}</span>
            ${recLabel ? `<span class="pill pill-rec">${recLabel}</span>` : ''}
          </div>
          <div class="breakdown-bar">
            <div style="flex:${d.recBuy};background:#639922;border-radius:3px 0 0 3px;"></div>
            <div style="flex:${d.recHold};background:#EF9F27;"></div>
            <div style="flex:${d.recSell};background:#E24B4A;border-radius:0 3px 3px 0;"></div>
          </div>
          <div class="analyst-pills">
            ${d.recBuy > 0 ? `<span class="pill pill-buy"><i class="ti ti-thumb-up" aria-hidden="true"></i>Buy ${d.recBuy}</span>` : ''}
            ${d.recHold > 0 ? `<span class="pill pill-hold"><i class="ti ti-minus" aria-hidden="true"></i>Hold ${d.recHold}</span>` : ''}
            ${d.recSell > 0 ? `<span class="pill pill-sell"><i class="ti ti-thumb-down" aria-hidden="true"></i>Sell ${d.recSell}</span>` : ''}
          </div>
        </div>
      ` : ''}

      <div class="action-row">
        <button class="btn-primary" onclick="addToPortfolioFromSearch('${d.ticker}', ${d.price})">
          <i class="ti ti-plus" aria-hidden="true"></i> Add to Portfolio
        </button>
        <button class="btn-secondary" onclick="addAlertFromSearch('${d.ticker}', ${d.targetMean || d.price})">
          <i class="ti ti-bell" aria-hidden="true"></i> Set Alert
        </button>
      </div>
    </div>

    ${d.targetMean ? `
      <div class="card">
        <div style="font-size:13px;font-weight:600;margin-bottom:10px;"><i class="ti ti-chart-bar" aria-hidden="true"></i> Price vs. Target</div>
        <div class="chart-legend">
          <span><span class="legend-dot" style="background:#378ADD;"></span>Current</span>
          <span><span class="legend-dot" style="background:#F09595;"></span>Low Target</span>
          <span><span class="legend-dot" style="background:#97C459;"></span>Avg Target</span>
          <span><span class="legend-dot" style="background:#5DCAA5;"></span>High Target</span>
        </div>
        <div class="chart-wrap">
          <canvas id="target-chart" role="img" aria-label="${d.ticker} current price vs. target chart"></canvas>
        </div>
      </div>
    ` : ''}
  `;

  if (d.targetMean) setTimeout(() => drawChart(d.price, d.targetLow, d.targetMean, d.targetHigh), 80);
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
      labels: ['Current', 'Low Target', 'Avg Target', 'High Target'],
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
  el.innerHTML = `<div class="loading-msg"><i class="ti ti-loader-2" aria-hidden="true"></i>Loading...</div>`;
  try {
    const data = await api('GET', '/api/portfolio');
    renderPortfolio(data);
  } catch (e) {
    el.innerHTML = `<div class="error-msg"><i class="ti ti-alert-circle" aria-hidden="true"></i>${e.message}</div>`;
  }
}

async function refreshPortfolio() {
  const el = document.getElementById('portfolio-content');
  el.innerHTML = `<div class="loading-msg"><i class="ti ti-loader-2" aria-hidden="true"></i>Updating prices...</div>`;
  try {
    const data = await api('POST', '/api/portfolio/refresh');
    renderPortfolio(data);
  } catch (e) {
    el.innerHTML = `<div class="error-msg"><i class="ti ti-alert-circle" aria-hidden="true"></i>${e.message}</div>`;
  }
}

function renderPortfolio(portfolio) {
  const el = document.getElementById('portfolio-content');
  if (!portfolio.length) {
    el.innerHTML = `<div class="empty-state"><i class="ti ti-briefcase" aria-hidden="true"></i><p>Your portfolio is empty</p><small>Search for a stock or add one above</small></div>`;
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
      <div class="meta-item"><div class="meta-label">Total Value</div><div class="meta-value">$${totalValue.toLocaleString('en-US', {maximumFractionDigits:0})}</div></div>
      <div class="meta-item"><div class="meta-label">Total Cost</div><div class="meta-value">$${totalCost.toLocaleString('en-US', {maximumFractionDigits:0})}</div></div>
      <div class="meta-item"><div class="meta-label">Total P/L</div><div class="meta-value ${pnl >= 0 ? 'positive' : 'negative'}">${pnl >= 0 ? '+' : ''}$${Math.abs(pnl).toLocaleString('en-US', {maximumFractionDigits:0})} <span style="font-size:13px;">(${pnlPct.toFixed(1)}%)</span></div></div>
    </div>
    <div class="pf-table-wrap">
      <table class="pf-table">
        <thead><tr><th>Stock</th><th>Price</th><th>Target</th><th>Upside</th><th>Qty</th><th>P/L</th><th></th></tr></thead>
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
    el.innerHTML = `<div class="error-msg"><i class="ti ti-alert-circle" aria-hidden="true"></i>${e.message}</div>`;
  }
}

function renderAlerts(alerts) {
  const el = document.getElementById('alerts-content');
  const active = alerts.filter(a => !a.triggered).length;
  const badge = document.getElementById('alert-badge');
  badge.style.display = active > 0 ? 'inline' : 'none';
  badge.textContent = active;

  if (!alerts.length) {
    el.innerHTML = `<div class="empty-state"><i class="ti ti-bell-off" aria-hidden="true"></i><p>No alerts set</p><small>Add a ticker and price threshold above</small></div>`;
    return;
  }

  el.innerHTML = `<div class="notif-list">${alerts.map(a => `
    <div class="notif-item">
      <div>
        <div class="notif-ticker">${escapeHtml(a.ticker)} <span class="${a.triggered ? 'badge-done' : 'badge-active'}">${a.triggered ? 'Triggered' : 'Active'}</span></div>
        <div class="notif-info">Alert when price ${a.type === 'above' ? 'rises above' : 'falls below'} $${a.price.toFixed(2)} · ${a.created}${a.triggeredAt ? ` · <span style="color:var(--green)">Triggered ${a.triggeredAt}</span>` : ''}</div>
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

  el.innerHTML = `<div class="loading-msg"><i class="ti ti-loader-2" aria-hidden="true"></i>Running backtest for ${ticker}...</div>`;

  try {
    const data = await api('POST', '/api/backtest/infinite-buying', { ticker, start, end, seed, splits, targetReturn, version });
    renderBacktestResult(data);
  } catch (e) {
    el.innerHTML = `<div class="error-msg"><i class="ti ti-alert-circle" aria-hidden="true"></i>${e.message}</div>`;
  }
}

function renderBacktestResult(d) {
  const el = document.getElementById('backtest-result');
  const pnlCls = d.evalPnl >= 0 ? 'positive' : 'negative';
  const holding = d.holding;

  el.innerHTML = `
    <div class="card">
      <div class="bt-summary-grid">
        <div class="meta-item"><div class="meta-label">Capital</div><div class="meta-value">$${d.seed.toLocaleString('en-US', {maximumFractionDigits:0})}</div></div>
        <div class="meta-item"><div class="meta-label">Total Buy Qty</div><div class="meta-value">${d.totalBuyQty.toLocaleString('en-US')} sh</div></div>
        <div class="meta-item"><div class="meta-label">Total Sell Qty</div><div class="meta-value">${d.totalSellQty.toLocaleString('en-US')} sh</div></div>
        <div class="meta-item"><div class="meta-label">Holding Qty</div><div class="meta-value">${holding.qty.toLocaleString('en-US')} sh</div></div>
        <div class="meta-item"><div class="meta-label">Avg Price</div><div class="meta-value">${holding.qty > 0 ? '$' + holding.avgPrice.toFixed(2) : '-'}</div></div>
        <div class="meta-item"><div class="meta-label">Buy Amount</div><div class="meta-value">$${d.totalBuyAmount.toLocaleString('en-US', {maximumFractionDigits:2})}</div></div>
        <div class="meta-item"><div class="meta-label">Sell Amount</div><div class="meta-value">$${d.totalSellAmount.toLocaleString('en-US', {maximumFractionDigits:2})}</div></div>
        <div class="meta-item"><div class="meta-label">Unrealized P/L</div><div class="meta-value ${pnlCls}">${d.evalPnl>=0?'+':''}$${Math.abs(d.evalPnl).toLocaleString('en-US', {maximumFractionDigits:2})}</div></div>
        <div class="meta-item"><div class="meta-label">Return</div><div class="meta-value ${pnlCls}">${d.returnPct>=0?'+':''}${d.returnPct.toFixed(1)}%</div></div>
        <div class="meta-item"><div class="meta-label">Return on Capital</div><div class="meta-value ${pnlCls}">${d.seedReturnPct>=0?'+':''}${d.seedReturnPct.toFixed(1)}%</div></div>
        <div class="meta-item"><div class="meta-label">Target Return</div><div class="meta-value">${d.targetReturnPct}%</div></div>
        <div class="meta-item"><div class="meta-label">Splits</div><div class="meta-value">${d.splits}</div></div>
        <div class="meta-item"><div class="meta-label">Strategy MDD</div><div class="meta-value negative">-${d.mddPct.toFixed(1)}%</div></div>
        <div class="meta-item"><div class="meta-label">${escapeHtml(d.benchmark.label)} Return</div><div class="meta-value ${d.benchmark.returnPct>=0?'positive':'negative'}">${d.benchmark.returnPct>=0?'+':''}${d.benchmark.returnPct.toFixed(1)}%</div></div>
        <div class="meta-item"><div class="meta-label">${escapeHtml(d.benchmark.label)} MDD</div><div class="meta-value negative">-${d.benchmark.mddPct.toFixed(1)}%</div></div>
        <div class="meta-item"><div class="meta-label">Alpha (Excess Return)</div><div class="meta-value ${d.alphaPct>=0?'positive':'negative'}">${d.alphaPct>=0?'+':''}${d.alphaPct.toFixed(1)}%p</div></div>
      </div>

      <div class="bt-holding-box">
        <span class="cycle-pill">${d.version.toUpperCase()}</span>
        <span class="cycle-pill">${d.completedCycles} Cycles Completed</span>
        ${holding.lossCutMode ? `<span class="cycle-pill" style="background:var(--red-bg);color:var(--red);">Quarter Stop-Loss</span>` : ''}
        ${holding.qty > 0
          ? ` · Currently holding: ${holding.qty} sh @ avg $${holding.avgPrice.toFixed(2)} · Price $${holding.currentPrice.toFixed(2)} · Value $${holding.value.toLocaleString('en-US',{maximumFractionDigits:2})} (T ${holding.tValue}/${d.splits})`
          : ` · No position at end of backtest (fully exited)`}
        <div style="font-size:11px;color:var(--text-muted);margin-top:4px;">Period: ${d.start} – ${d.end}</div>
      </div>

      <div style="font-size:13px;font-weight:600;margin:14px 0 8px;">Return Comparison (Strategy vs. ${escapeHtml(d.benchmark.label)})</div>
      <div class="chart-wrap">
        <canvas id="return-chart" role="img" aria-label="Return comparison chart"></canvas>
      </div>
    </div>

    <div class="card">
      <div style="font-size:13px;font-weight:600;margin-bottom:8px;">${escapeHtml(d.ticker)} Price Chart (buy/sell markers)</div>
      <div class="chart-wrap">
        <canvas id="price-chart" role="img" aria-label="${escapeHtml(d.ticker)} price chart"></canvas>
      </div>
    </div>

    <div class="pf-table-wrap">
      <table class="pf-table">
        <thead><tr><th>Cycle</th><th>Date</th><th>Type</th><th>Price</th><th>Qty</th><th>Cum. Qty</th><th>Avg Price</th><th>Return</th><th>Note</th></tr></thead>
        <tbody>
          ${d.trades.length ? d.trades.map(t => `
            <tr>
              <td>${t.cycle}</td>
              <td>${t.date}</td>
              <td><span class="${t.action === 'buy' ? 'negative' : 'positive'}" style="font-weight:600;">${t.action === 'buy' ? 'Buy' : 'Sell'}</span></td>
              <td>$${t.price.toFixed(2)}</td>
              <td>${t.qty}</td>
              <td>${t.qtyAfter}</td>
              <td>${t.avgPriceAfter !== null ? '$' + t.avgPriceAfter.toFixed(2) : '-'}</td>
              <td class="${t.returnPctAfter >= 0 ? 'positive' : 'negative'}">${t.returnPctAfter>=0?'+':''}${t.returnPctAfter.toFixed(1)}%</td>
              <td style="font-size:12px;color:var(--text-secondary);">${escapeHtml(t.note)}</td>
            </tr>`).join('') : `<tr><td colspan="9" style="text-align:center;color:var(--text-secondary);">No trades executed in this period</td></tr>`}
        </tbody>
      </table>
    </div>
  `;

  setTimeout(() => {
    drawReturnChart(d.equityCurve, d.benchmark, d.seed);
    drawPriceChart(d.priceCurve, d.trades, d.ticker);
  }, 80);
}

function drawReturnChart(curve, benchmark, seed) {
  const canvas = document.getElementById('return-chart');
  if (!canvas) return;
  if (equityChart) equityChart.destroy();
  const toPct = v => (v - seed) / seed * 100;
  const datasets = [{
    label: 'Infinite Buying',
    data: curve.map(p => toPct(p.value)),
    borderColor: '#378ADD', backgroundColor: 'rgba(55,138,221,0.12)',
    fill: true, pointRadius: 0, borderWidth: 2, tension: 0.15,
  }];
  if (benchmark?.equityCurve?.length) {
    datasets.push({
      label: benchmark.label,
      data: benchmark.equityCurve.map(p => toPct(p.value)),
      borderColor: '#97C459', backgroundColor: 'transparent',
      fill: false, pointRadius: 0, borderWidth: 2, borderDash: [5, 4], tension: 0.15,
    });
  }
  equityChart = new Chart(canvas, {
    type: 'line',
    data: { labels: curve.map(p => p.date), datasets },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: {
        legend: { display: true, position: 'top', labels: { boxWidth: 12, font: { size: 11 } } },
        tooltip: { callbacks: { label: ctx => ` ${ctx.dataset.label}: ${ctx.parsed.y>=0?'+':''}` + ctx.parsed.y.toFixed(1) + '%' } },
      },
      scales: {
        y: { ticks: { callback: v => (v>=0?'+':'') + v.toFixed(0) + '%' }, grid: { color: 'rgba(128,128,128,0.1)' } },
        x: { ticks: { maxTicksLimit: 8 }, grid: { display: false } },
      },
    },
  });
}

function drawPriceChart(priceCurve, trades, ticker) {
  const canvas = document.getElementById('price-chart');
  if (!canvas) return;
  if (priceChartBt) priceChartBt.destroy();

  const validDates = new Set(priceCurve.map(p => p.date));
  const buyPoints = [], sellPoints = [];
  trades.forEach(t => {
    if (!validDates.has(t.date)) return;
    (t.action === 'buy' ? buyPoints : sellPoints).push({ x: t.date, y: t.price });
  });

  priceChartBt = new Chart(canvas, {
    type: 'line',
    data: {
      labels: priceCurve.map(p => p.date),
      datasets: [
        {
          label: `${ticker} Close`, data: priceCurve.map(p => p.close),
          borderColor: '#888780', backgroundColor: 'transparent',
          fill: false, pointRadius: 0, borderWidth: 1.5, tension: 0.1, order: 3,
        },
        {
          label: 'Buy', data: buyPoints, type: 'scatter',
          backgroundColor: '#E24B4A', borderColor: '#E24B4A',
          pointRadius: 4, pointStyle: 'triangle', order: 1,
        },
        {
          label: 'Sell', data: sellPoints, type: 'scatter',
          backgroundColor: '#378ADD', borderColor: '#378ADD',
          pointRadius: 4, pointStyle: 'rectRot', order: 2,
        },
      ],
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: {
        legend: { display: true, position: 'top', labels: { boxWidth: 12, font: { size: 11 } } },
        tooltip: { callbacks: { label: ctx => ` ${ctx.dataset.label}: $` + ctx.parsed.y.toFixed(2) } },
      },
      scales: {
        y: { ticks: { callback: v => '$' + v.toFixed(0) }, grid: { color: 'rgba(128,128,128,0.1)' } },
        x: { ticks: { maxTicksLimit: 8 }, grid: { display: false } },
      },
    },
  });
}

// ─── 국내(KRX) 단기/스윙 백테스트 ─────────────────────────────────────────────

let krSwingReturnChart = null;
let krSwingPriceChart = null;
const krSwingReturnPanState = { cleanup: null };
const krSwingPricePanState = { cleanup: null };

const KR_SWING_STRATEGY_LABEL = {
  volatility_breakout: 'Volatility Breakout',
  box_breakout: 'Range Breakout',
  ma_pullback: 'MA Pullback',
  combo: 'Combo (Trend + Pullback + Momentum)',
};

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
    list.innerHTML = `<div class="ks-autocomplete-empty">No matching stocks</div>`;
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
  el.innerHTML = `<div class="loading-msg"><i class="ti ti-loader-2" aria-hidden="true"></i>Running backtest for ${escapeHtml(krSwingSelectedName)}...</div>`;

  try {
    const data = await api('POST', '/api/kr-swing/backtest', { strategy, code, start, end, seed, params });
    renderKrSwingResult(data);
  } catch (e) {
    el.innerHTML = `<div class="error-msg"><i class="ti ti-alert-circle" aria-hidden="true"></i>${escapeHtml(e.message)}</div>`;
  }
}

function renderKrSwingResult(d) {
  const el = document.getElementById('krswing-result');
  const pnlCls = d.evalPnl >= 0 ? 'positive' : 'negative';
  const holding = d.holding;

  el.innerHTML = `
    <div class="card">
      <div class="bt-summary-grid">
        <div class="meta-item"><div class="meta-label">Capital</div><div class="meta-value">${formatKrw(d.seed)}</div></div>
        <div class="meta-item"><div class="meta-label">Stock</div><div class="meta-value">${escapeHtml(d.ticker)} (${escapeHtml(d.market)})</div></div>
        <div class="meta-item"><div class="meta-label">Trades</div><div class="meta-value">${d.tradeCount}</div></div>
        <div class="meta-item"><div class="meta-label">Win Rate</div><div class="meta-value">${d.winCount}W / ${d.tradeCount} (${d.winRatePct.toFixed(1)}%)</div></div>
        <div class="meta-item"><div class="meta-label">Avg Hold Days</div><div class="meta-value">${d.avgHoldDays.toFixed(1)}d</div></div>
        <div class="meta-item"><div class="meta-label">Buy Amount</div><div class="meta-value">${formatKrw(d.totalBuyAmount)}</div></div>
        <div class="meta-item"><div class="meta-label">Sell Amount</div><div class="meta-value">${formatKrw(d.totalSellAmount)}</div></div>
        <div class="meta-item"><div class="meta-label">Unrealized P/L</div><div class="meta-value ${pnlCls}">${d.evalPnl>=0?'+':''}${formatKrw(Math.abs(d.evalPnl))}</div></div>
        <div class="meta-item"><div class="meta-label">Return on Capital</div><div class="meta-value ${pnlCls}">${d.seedReturnPct>=0?'+':''}${d.seedReturnPct.toFixed(1)}%</div></div>
        <div class="meta-item"><div class="meta-label">Strategy MDD</div><div class="meta-value negative">-${d.mddPct.toFixed(1)}%</div></div>
        <div class="meta-item"><div class="meta-label">${escapeHtml(d.benchmark.label)} Return</div><div class="meta-value ${d.benchmark.returnPct>=0?'positive':'negative'}">${d.benchmark.returnPct>=0?'+':''}${d.benchmark.returnPct.toFixed(1)}%</div></div>
        <div class="meta-item"><div class="meta-label">Alpha (Excess Return)</div><div class="meta-value ${d.alphaPct>=0?'positive':'negative'}">${d.alphaPct>=0?'+':''}${d.alphaPct.toFixed(1)}%p</div></div>
      </div>

      <div class="bt-holding-box">
        <span class="cycle-pill">${escapeHtml(KR_SWING_STRATEGY_LABEL[d.strategy] || d.strategy)}</span>
        ${holding.qty > 0
          ? ` · Currently holding: ${holding.qty.toLocaleString('en-US')} sh @ avg ${formatKrw(holding.avgPrice)} · Price ${formatKrw(holding.currentPrice)} · Value ${formatKrw(holding.value)}`
          : ` · No position at end of backtest (fully exited)`}
        <div style="font-size:11px;color:var(--text-muted);margin-top:4px;">Period: ${d.start} – ${d.end}</div>
      </div>

      <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px;margin:14px 0 8px;">
        <div style="font-size:13px;font-weight:600;">Return Comparison (Strategy vs. ${escapeHtml(d.benchmark.label)})</div>
        <button class="btn-secondary" onclick="resetKrSwingReturnZoom()" style="padding:4px 10px;font-size:12px;"><i class="ti ti-zoom-reset" aria-hidden="true"></i> Reset Zoom</button>
      </div>
      <div class="chart-wrap">
        <canvas id="krswing-return-chart" role="img" aria-label="Return comparison chart"></canvas>
      </div>
      <div style="font-size:11px;color:var(--text-muted);margin-top:6px;text-align:center;">Scroll to zoom, drag to pan</div>
    </div>

    <div class="card">
      <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px;margin-bottom:8px;">
        <div style="font-size:13px;font-weight:600;">${escapeHtml(d.ticker)} Price Chart (buy/sell markers)</div>
        <button class="btn-secondary" onclick="resetKrSwingPriceZoom()" style="padding:4px 10px;font-size:12px;"><i class="ti ti-zoom-reset" aria-hidden="true"></i> Reset Zoom</button>
      </div>
      <div class="chart-wrap">
        <canvas id="krswing-price-chart" role="img" aria-label="${escapeHtml(d.ticker)} price chart"></canvas>
      </div>
      <div style="font-size:11px;color:var(--text-muted);margin-top:6px;text-align:center;">Scroll to zoom, drag to pan</div>
    </div>

    <div class="pf-table-wrap">
      <table class="pf-table">
        <thead><tr><th>Date</th><th>Type</th><th>Price</th><th>Qty</th><th>P/L %</th><th>Note</th></tr></thead>
        <tbody>
          ${d.trades.length ? d.trades.map(t => `
            <tr>
              <td>${t.date}</td>
              <td><span class="${t.action === 'buy' ? 'negative' : 'positive'}" style="font-weight:600;">${t.action === 'buy' ? 'Buy' : 'Sell'}</span></td>
              <td>${formatKrw(t.price)}</td>
              <td>${t.qty.toLocaleString('en-US')}</td>
              <td>${t.pnlPct !== undefined ? `<span class="${t.pnlPct >= 0 ? 'positive' : 'negative'}">${t.pnlPct>=0?'+':''}${t.pnlPct.toFixed(1)}%</span>` : '-'}</td>
              <td style="font-size:12px;color:var(--text-secondary);">${escapeHtml(t.note)}</td>
            </tr>`).join('') : `<tr><td colspan="6" style="text-align:center;color:var(--text-secondary);">No trades executed in this period</td></tr>`}
        </tbody>
      </table>
    </div>
  `;

  setTimeout(() => {
    drawKrSwingReturnChart(d.equityCurve, d.benchmark, d.seed);
    drawKrSwingPriceChart(d.priceCurve, d.trades, d.ticker);
  }, 80);
}

function drawKrSwingReturnChart(curve, benchmark, seed) {
  const canvas = document.getElementById('krswing-return-chart');
  if (!canvas) return;
  if (krSwingReturnChart) krSwingReturnChart.destroy();
  const dates = curve.map(p => p.date);
  const toPct = v => (v - seed) / seed * 100;
  const datasets = [{
    label: 'Strategy',
    data: curve.map((p, i) => ({ x: i, y: toPct(p.value) })),
    borderColor: '#378ADD', backgroundColor: 'rgba(55,138,221,0.12)',
    fill: true, pointRadius: 0, borderWidth: 2, tension: 0.15,
  }];
  if (benchmark?.equityCurve?.length) {
    datasets.push({
      label: benchmark.label,
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
          label: `${ticker} Close`, data: priceCurve.map((p, i) => ({ x: i, y: p.close })),
          borderColor: '#888780', backgroundColor: 'transparent',
          fill: false, pointRadius: 0, borderWidth: 1.5, tension: 0.1, order: 3,
        },
        {
          label: 'Buy', data: buyPoints, type: 'scatter',
          backgroundColor: '#E24B4A', borderColor: '#E24B4A',
          pointRadius: 4, pointStyle: 'triangle', order: 1,
        },
        {
          label: 'Sell', data: sellPoints, type: 'scatter',
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
      ? `Price cache ready for ${data.priceCacheCount.toLocaleString('en-US')} stocks (updated ${Math.round(data.priceCacheAgeSeconds / 60)}m ago)`
      : `Price cache warming up (takes a few minutes after server start) — screening will be available once ready`;
    el.innerHTML = `<i class="ti ti-database" aria-hidden="true"></i> Fundamentals: ${data.stockCount.toLocaleString('en-US')} stocks (${data.fundamentalRows.toLocaleString('en-US')} records) loaded. ${priceInfo}.`;
  } catch (e) {
    el.innerHTML = `<i class="ti ti-alert-circle" aria-hidden="true"></i> ${escapeHtml(e.message)}`;
  }
}

async function runKrQuantScreen() {
  const el = document.getElementById('krquant-screen-result');
  if (!el) return;
  const topN = parseInt(document.getElementById('kq-screen-topn').value, 10) || 20;
  const minMarketCap = (parseFloat(document.getElementById('kq-screen-mcap').value) || 0) * 100000000;

  el.innerHTML = `<div class="loading-msg"><i class="ti ti-loader-2" aria-hidden="true"></i>Screening...</div>`;
  try {
    const data = await api('GET', `/api/kr-quant/screen?topN=${topN}&minMarketCap=${minMarketCap}`);
    renderKrQuantScreenResult(data);
  } catch (e) {
    el.innerHTML = `<div class="error-msg"><i class="ti ti-alert-circle" aria-hidden="true"></i>${escapeHtml(e.message)}</div>`;
  }
}

function renderKrQuantScreenResult(data) {
  const el = document.getElementById('krquant-screen-result');
  const picks = data.picks || [];
  if (!picks.length) {
    el.innerHTML = `<div class="empty-state" style="padding:1.5rem;"><p>No stocks match the criteria</p></div>`;
    return;
  }
  el.innerHTML = `
    <div style="font-size:11px;color:var(--text-muted);margin-bottom:8px;">As of: ${escapeHtml(data.date)}</div>
    <div class="pf-table-wrap">
      <table class="pf-table">
        <thead><tr><th>Rank</th><th>Name</th><th>Code</th><th>P/E</th><th>ROE</th><th>Market Cap</th><th>Fiscal Year</th></tr></thead>
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
  el.innerHTML = `<div class="loading-msg"><i class="ti ti-loader-2" aria-hidden="true"></i>Running annual rebalance backtest... (querying market-wide prices, may take 5–15 minutes depending on the period)</div>`;
  try {
    const { jobId } = await api('POST', '/api/kr-quant/backtest', { startYear, endYear, seed, topN, minMarketCap });
    await pollKrQuantBacktestJob(jobId, el);
  } catch (e) {
    el.innerHTML = `<div class="error-msg"><i class="ti ti-alert-circle" aria-hidden="true"></i>${escapeHtml(e.message)}</div>`;
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
      el.innerHTML = `<div class="error-msg"><i class="ti ti-alert-circle" aria-hidden="true"></i>${escapeHtml(e.message)}</div>`;
      return;
    }
    if (data.status === 'done') {
      renderKrQuantBacktestResult(data.result);
      return;
    }
    if (data.status === 'error') {
      el.innerHTML = `<div class="error-msg"><i class="ti ti-alert-circle" aria-hidden="true"></i>${escapeHtml(data.error || '백테스트 중 오류가 발생했습니다')}</div>`;
      return;
    }
  }
  el.innerHTML = `<div class="error-msg"><i class="ti ti-alert-circle" aria-hidden="true"></i>백테스트가 예상보다 오래 걸리고 있습니다. 잠시 후 다시 시도해주세요.</div>`;
}

function renderKrQuantBacktestResult(d) {
  const el = document.getElementById('krquant-result');
  const pnlCls = d.totalReturnPct >= 0 ? 'positive' : 'negative';

  el.innerHTML = `
    <div class="card">
      <div class="bt-summary-grid">
        <div class="meta-item"><div class="meta-label">Capital</div><div class="meta-value">${formatKrw(d.seed)}</div></div>
        <div class="meta-item"><div class="meta-label">Final Value</div><div class="meta-value">${formatKrw(d.finalValue)}</div></div>
        <div class="meta-item"><div class="meta-label">Total Return</div><div class="meta-value ${pnlCls}">${d.totalReturnPct>=0?'+':''}${d.totalReturnPct.toFixed(1)}%</div></div>
        <div class="meta-item"><div class="meta-label">MDD</div><div class="meta-value negative">-${d.mddPct.toFixed(1)}%</div></div>
        <div class="meta-item"><div class="meta-label">${escapeHtml(d.benchmark.label)}</div><div class="meta-value ${(d.benchmark.returnPct ?? 0) >= 0 ? 'positive' : 'negative'}">${d.benchmark.returnPct !== null && d.benchmark.returnPct !== undefined ? (d.benchmark.returnPct >= 0 ? '+' : '') + d.benchmark.returnPct.toFixed(1) + '%' : '-'}</div></div>
        <div class="meta-item"><div class="meta-label">Rebalances</div><div class="meta-value">${d.rebalanceDates.length}</div></div>
      </div>
      <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px;margin:14px 0 8px;">
        <div style="font-size:13px;font-weight:600;">Equity Curve (Strategy vs. ${escapeHtml(d.benchmark.label)})</div>
        <button class="btn-secondary" onclick="resetKrQuantZoom()" style="padding:4px 10px;font-size:12px;"><i class="ti ti-zoom-reset" aria-hidden="true"></i> Reset Zoom</button>
      </div>
      <div class="chart-wrap">
        <canvas id="krquant-chart" role="img" aria-label="Equity curve chart"></canvas>
      </div>
      <div style="font-size:11px;color:var(--text-muted);margin-top:6px;text-align:center;">Scroll to zoom, drag to pan</div>
    </div>

    <div class="card">
      <div style="font-size:13px;font-weight:600;margin-bottom:10px;">Selected Stocks by Rebalance Date</div>
      ${d.picksLog.map(pl => `
        <div style="margin-bottom:14px;">
          <div style="font-size:12px;font-weight:600;color:var(--text-secondary);margin-bottom:6px;">${escapeHtml(pl.date)} (${pl.picks.length} stocks)</div>
          <div class="pf-table-wrap">
            <table class="pf-table">
              <thead><tr><th>Rank</th><th>Name</th><th>P/E</th><th>ROE</th><th>Market Cap</th></tr></thead>
              <tbody>
                ${pl.picks.length ? pl.picks.map(p => `
                  <tr><td>${p.combinedRank}</td><td>${escapeHtml(p.name)}</td><td>${p.per.toFixed(2)}</td><td>${p.roe.toFixed(2)}%</td><td>${formatKrw(p.marketCap)}</td></tr>
                `).join('') : `<tr><td colspan="5" style="text-align:center;color:var(--text-secondary);">No stocks selected</td></tr>`}
              </tbody>
            </table>
          </div>
        </div>`).join('')}
    </div>

    <div class="pf-table-wrap">
      <table class="pf-table">
        <thead><tr><th>Date</th><th>Type</th><th>Name</th><th>Price</th><th>Qty</th><th>P/L %</th></tr></thead>
        <tbody>
          ${d.trades.length ? d.trades.map(t => `
            <tr>
              <td>${t.date}</td>
              <td><span class="${t.action === 'buy' ? 'negative' : 'positive'}" style="font-weight:600;">${t.action === 'buy' ? 'Buy' : 'Sell'}</span></td>
              <td>${escapeHtml(t.name)}</td>
              <td>${formatKrw(t.price)}</td>
              <td>${t.qty.toLocaleString('en-US')}</td>
              <td>${t.pnlPct !== undefined ? `<span class="${t.pnlPct >= 0 ? 'positive' : 'negative'}">${t.pnlPct>=0?'+':''}${t.pnlPct.toFixed(1)}%</span>` : '-'}</td>
            </tr>`).join('') : `<tr><td colspan="6" style="text-align:center;color:var(--text-secondary);">No trade history</td></tr>`}
        </tbody>
      </table>
    </div>
  `;

  setTimeout(() => drawKrQuantChart(d.equityCurve, d.benchmark, d.seed), 80);
}

function drawKrQuantChart(curve, benchmark, seed) {
  const canvas = document.getElementById('krquant-chart');
  if (!canvas) return;
  if (krQuantChart) krQuantChart.destroy();
  const dates = curve.map(p => p.date);
  const toPct = v => (v - seed) / seed * 100;
  const datasets = [{
    label: 'Quant Strategy',
    data: curve.map((p, i) => ({ x: i, y: toPct(p.value) })),
    borderColor: '#378ADD', backgroundColor: 'rgba(55,138,221,0.12)',
    fill: true, pointRadius: 3, borderWidth: 2, tension: 0.1,
  }];
  if (benchmark?.equityCurve?.length) {
    datasets.push({
      label: benchmark.label,
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
  ['priceAboveMa150And200', '① Price > MA150 &amp; MA200'],
  ['ma150AboveMa200', '② MA150 > MA200'],
  ['ma200Rising', '③ MA200 rising 1mo+'],
  ['ma50AboveMa150And200', '④ MA50 > MA150 &amp; MA200'],
  ['priceAboveMa50', '⑤ Price > MA50'],
  ['priceAbove52wLowBy30pct', '⑥ +30% above 52w low'],
  ['priceWithin25pctOf52wHigh', '⑦ Within 25% of 52w high'],
  ['rsAboveThreshold', '⑧ RS Rating ≥ 70'],
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
  btn.querySelector('span').textContent = on ? 'Added to Watchlist' : 'Add to Watchlist';
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
      el.innerHTML = `<i class="ti ti-loader-2" aria-hidden="true"></i> Preparing data...`;
      return;
    }
    const asOfText = s.asOf ? formatAsOf(s.asOf) : null;
    el.innerHTML = `<i class="ti ti-database" aria-hidden="true"></i> ${s.count.toLocaleString('en-US')} stocks${asOfText ? ' · As of ' + asOfText : ''}`;
  } catch (e) {
    el.innerHTML = `<i class="ti ti-alert-circle" aria-hidden="true"></i> ${escapeHtml(e.message)}`;
  }
}

async function loadScreenerResults() {
  const el = document.getElementById('screener-result');
  if (!el) return;
  loadScreenerStatus();
  const market = document.getElementById('scr-market').value;
  const onlyPass = document.getElementById('scr-only-pass').checked;

  el.innerHTML = `<div class="loading-msg"><i class="ti ti-loader-2" aria-hidden="true"></i>Loading...</div>`;
  try {
    const data = await api('GET', `/api/screener/results?market=${market}&onlyPass=${onlyPass}`);
    renderScreenerResults(data);
  } catch (e) {
    el.innerHTML = `<div class="error-msg"><i class="ti ti-alert-circle" aria-hidden="true"></i>${escapeHtml(e.message)}</div>`;
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
const FIN_FILTER_CATEGORIES = [
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

function countActiveInCat(cat) {
  if (cat.key === 'consensus') return scrActiveRatings.size;
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
        <span>${cat.icon} ${cat.title}</span>${n ? `<span class="ffcat-tab-count">${n}</span>` : ''}
      </button>`;
  }).join('');

  const detailHtml = activeCat.key === 'consensus' ? `
    <div class="ffrow-detail">
      <div class="ffrow-head"><span class="fflabel">Analyst Rating</span></div>
      <span class="ffchips">
        ${['Buy', 'Hold', 'Sell'].map(label => `
          <span class="ffchip ${scrActiveRatings.has(label) ? 'selected' : ''}" onclick="toggleFinFilterRating('${label}')">${label}</span>
        `).join('')}
      </span>
    </div>` : activeCat.items.map(item => {
    const unitLabel = item.abs ? `(${absUnitLabel()})` : item.unit ? `(${item.unit})` : '';
    const cur = scrActiveFilters[item.key] || {};
    const tiersHtml = item.tiers ? `
      <div class="fftiers">
        ${item.tiers.map(t => `
          <button type="button" class="fftier ${isTierActive(item.key, t) ? 'selected' : ''}" onclick="applyFinTier('${item.key}', ${t.min ?? 'null'}, ${t.max ?? 'null'})">${t.label}</button>
        `).join('')}
      </div>` : '';
    return `
      <div class="ffrow-detail">
        <div class="ffrow-head">
          <span class="fflabel">${escapeHtml(item.label)} <span class="ffunit">${unitLabel}</span></span>
          <span class="ffrange">
            <input type="number" placeholder="Min" data-fkey="${item.key}" data-bound="min" value="${cur.min ?? ''}" onchange="onFinInputChange(this)">
            <span>~</span>
            <input type="number" placeholder="Max" data-fkey="${item.key}" data-bound="max" value="${cur.max ?? ''}" onchange="onFinInputChange(this)">
          </span>
        </div>
        ${tiersHtml}
      </div>`;
  }).join('');

  panel.innerHTML = `
    <div class="ffpanel-head">
      <span class="ffpanel-title">Financial Filters</span>
      <div class="ffpanel-actions">
        <span class="ffbtn" onclick="resetFinFilters()">Reset</span>
      </div>
    </div>
    ${!scrIsUS ? '<div class="ffnote">Some metrics (stability, consensus, EV/EBITDA, etc.) aren\'t available for KR stocks.</div>' : ''}
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

function resetFinFilters() {
  scrActiveFilters = {};
  scrActiveRatings = new Set();
  buildFinFilterPanel();
  updateFinFilterCount();
  renderFilteredScreenerRows();
}

function updateFinFilterCount() {
  const n = Object.keys(scrActiveFilters).length + (scrActiveRatings.size ? 1 : 0);
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
    el.innerHTML = `<div class="empty-state" style="padding:2.5rem 1.5rem;"><i class="ti ti-filter-off" aria-hidden="true"></i><p>No stocks match the criteria</p></div>`;
    return;
  }
  if (!filtered.length) {
    el.innerHTML = `<div class="empty-state" style="padding:2.5rem 1.5rem;"><i class="ti ti-search-off" aria-hidden="true"></i><p>No stocks match your search/filter</p></div>`;
    return;
  }

  el.innerHTML = `
    <div style="font-size:12px;color:var(--text-muted);margin-bottom:8px;">${filtered.length.toLocaleString('en-US')} stocks${filtered.length !== scrRawResults.length ? ` (of ${scrRawResults.length.toLocaleString('en-US')} total)` : ''}</div>
    <div class="scr-table-outer">
    <div class="scr-table-wrap" id="scr-table-wrap">
      <table class="scr-table">
        <thead>
          <tr>
            <th style="width:32px;"></th>
            <th>Name</th><th>Code</th><th>Sector</th><th style="text-align:right;">Price</th><th>RS</th><th>Conditions</th>
            <th style="text-align:right;">Vol (Rel)</th>
            <th style="text-align:right;">Market Cap</th>
            <th style="text-align:right;">P/E</th>
            <th style="text-align:right;">EPS Growth</th>
            <th style="text-align:right;">Div Yield</th>
            <th>Analyst</th>
            <th style="text-align:right;">vs 52w Low</th><th style="text-align:right;">vs 52w High</th>
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
              <td title="${escapeHtml(r.industry || '')}">${r.sector ? `<span class="scr-sector-tag">${escapeHtml(r.sector)}</span>` : '-'}</td>
              <td class="scr-num-cell">${fmtPrice(r.price)}</td>
              <td><span class="scr-rs-badge ${rsBadgeClass(r.rsRating)}">${r.rsRating ?? '-'}</span></td>
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
              <td ${!scrIsUS ? `title="국내 종목은 애널리스트 레이팅 데이터를 제공하지 않습니다"` : ''}>${r.analystRating ? `<span class="scr-rating-badge ${ratingClass(r.analystRating)}">${r.analystRating}</span>` : '-'}</td>
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
}

let scrPopoverEl = null;

function showScrPopover(anchorEl, code) {
  hideScrPopover();
  const r = scrRawResults.find(x => x.code === code);
  if (!r) return;

  const pop = document.createElement('div');
  pop.className = 'scr-popover';
  pop.innerHTML = TREND_CONDITION_LABELS.map(([key, label]) => `
    <div class="scr-popover-row ${r.conditions[key] ? 'pass' : 'fail'}">
      <i class="ti ${r.conditions[key] ? 'ti-check' : 'ti-x'}" aria-hidden="true"></i>
      <span>${label}</span>
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
  body.innerHTML = `<div class="loading-msg"><i class="ti ti-loader-2" aria-hidden="true"></i>Loading...</div>`;
  document.addEventListener('keydown', scrDetailEscHandler);

  try {
    const data = await api('GET', `/api/screener/detail?market=${market}&code=${encodeURIComponent(code)}`);
    renderScreenerDetail(data);
  } catch (e) {
    body.innerHTML = `<div class="error-msg"><i class="ti ti-alert-circle" aria-hidden="true"></i>${escapeHtml(e.message)}</div>`;
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
        ['Rating', d.analystRating || null],
      ],
    },
  ];

  const catsHtml = categories.map((cat, idx) => {
    const available = cat.items.filter(([, v]) => v != null);
    const highlight = available.slice(0, 2).map(([l, v]) => `${l} ${v}`).join(' · ');
    const bodyHtml = available.length
      ? available.map(([label, value]) => {
          const isNeg = typeof value === 'string' && value.trim().startsWith('-');
          const isPos = typeof value === 'string' && value.trim().startsWith('+');
          return `<div class="scr-fin-metric" data-label="${escapeHtml(label)}">
            <span class="label">${escapeHtml(label)}</span>
            <span class="value ${isPos ? 'positive' : isNeg ? 'negative' : ''}">${value}</span>
          </div>`;
        }).join('')
      : `<div class="scr-fin-empty">${isUS ? 'Failed to load data' : 'Not available for KR stocks (no data source)'}</div>`;
    return `
      <div class="scr-fin-acc" data-cat="${cat.key}">
        <div class="scr-fin-acc-head" onclick="toggleFinAcc(this)">
          <span class="chev"><i class="ti ti-chevron-right" aria-hidden="true"></i></span>
          <span class="name">${cat.icon} ${cat.title}</span>
          <span class="hl">${highlight}</span>
        </div>
        <div class="scr-fin-acc-body"><div class="scr-fin-acc-body-inner">${bodyHtml}</div></div>
      </div>`;
  }).join('');

  return `
    <div class="scr-detail-section">
      <div class="scr-detail-section-title">Financial Metrics</div>
      <div class="scr-fin-search">
        <i class="ti ti-search" aria-hidden="true"></i>
        <input type="text" placeholder="Search metrics (e.g. Debt Ratio, ROE)" oninput="filterFinAccordion(this.value)">
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

  const condRows = TREND_CONDITION_LABELS.map(([key, label]) => {
    const pass = !!d.conditions[key];
    return `<div class="scr-detail-cond-row ${pass ? '' : 'fail'}">
      <i class="ti ${pass ? 'ti-check' : 'ti-x'} ${pass ? 'pass' : 'fail'}" aria-hidden="true"></i>
      <span>${label}</span>
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
        <div class="scr-detail-section-title">Financials (DART FY${escapeHtml(f.bsnsYear || '')} Annual Report)</div>
        <div class="scr-detail-grid">
          <div class="scr-detail-stat"><div class="scr-detail-stat-label">Net Income</div><div class="scr-detail-stat-value ${f.netIncome < 0 ? 'negative' : ''}">${fmtBig(f.netIncome)}</div></div>
          <div class="scr-detail-stat"><div class="scr-detail-stat-label">Revenue</div><div class="scr-detail-stat-value">${fmtBig(f.revenue)}</div></div>
          <div class="scr-detail-stat"><div class="scr-detail-stat-label">Total Equity</div><div class="scr-detail-stat-value">${fmtBig(f.totalEquity)}</div></div>
        </div>
      </div>`;
  }

  let targetHtml = '';
  if (d.target) {
    const t = d.target;
    const totalRec = (t.recBuy || 0) + (t.recHold || 0) + (t.recSell || 0);
    const buyPct = totalRec ? t.recBuy / totalRec * 100 : 0;
    const holdPct = totalRec ? t.recHold / totalRec * 100 : 0;
    const sellPct = totalRec ? t.recSell / totalRec * 100 : 0;
    targetHtml = `
      <div class="scr-detail-section">
        <div class="scr-detail-section-title">Analyst Targets &amp; Ratings (Finnhub)</div>
        ${t.targetMean ? `
          <div class="scr-detail-grid" style="margin-bottom:10px;">
            <div class="scr-detail-stat"><div class="scr-detail-stat-label">Avg Target</div><div class="scr-detail-stat-value">$${t.targetMean.toFixed(2)}</div></div>
            <div class="scr-detail-stat"><div class="scr-detail-stat-label">High</div><div class="scr-detail-stat-value">$${t.targetHigh?.toFixed(2) ?? '-'}</div></div>
            <div class="scr-detail-stat"><div class="scr-detail-stat-label">Low</div><div class="scr-detail-stat-value">$${t.targetLow?.toFixed(2) ?? '-'}</div></div>
          </div>` : `<div style="font-size:12px;color:var(--text-muted);margin-bottom:10px;">Target price figures aren't available on the Finnhub free plan.</div>`}
        ${totalRec ? `
          <div class="scr-detail-rec-bar">
            <div style="width:${buyPct}%;background:var(--green);"></div>
            <div style="width:${holdPct}%;background:var(--amber);"></div>
            <div style="width:${sellPct}%;background:var(--red);"></div>
          </div>
          <div class="scr-detail-rec-legend">
            <span><span class="scr-detail-rec-dot" style="background:var(--green);"></span>Buy ${t.recBuy}</span>
            <span><span class="scr-detail-rec-dot" style="background:var(--amber);"></span>Hold ${t.recHold}</span>
            <span><span class="scr-detail-rec-dot" style="background:var(--red);"></span>Sell ${t.recSell}</span>
          </div>` : ''}
      </div>`;
  } else if (d.market === 'US') {
    targetHtml = `<div class="scr-detail-section"><div class="scr-detail-section-title">Analyst Targets &amp; Ratings</div><div style="font-size:12px;color:var(--text-muted);">Failed to load data.</div></div>`;
  }

  body.innerHTML = `
    <div class="scr-detail-header">
      <span class="scr-detail-name">${escapeHtml(d.name)}</span>
      <span class="scr-detail-code">${escapeHtml(d.code)} · ${d.market === 'KR' ? 'KR' : 'US'}</span>
      <span class="scr-rs-badge ${rsBadgeClass(d.rsRating)}">RS ${d.rsRating ?? '-'}</span>
      ${d.industry ? `<span class="scr-industry-tag" style="display:inline-block;">${escapeHtml(d.industry)}</span>` : ''}
    </div>
    <div class="scr-detail-price-row">
      <span class="scr-detail-price">${fmt(d.price)}</span>
      <span class="scr-pass-badge ${d.passCount < 8 ? 'partial' : ''}">Trend Template ${d.passCount}/8</span>
      ${d.volume != null ? `<span class="scr-detail-volume">Volume ${Math.round(d.volume).toLocaleString('en-US')}${d.relVolume != null ? ` (${Number(d.relVolume).toFixed(2)}x)` : ''}</span>` : ''}
    </div>

    ${financialAccordionHtml}

    <div class="scr-detail-actions">
      <button type="button" id="scr-detail-star" class="scr-detail-star-btn ${isInWatchlist(d.market, d.code) ? 'on' : ''}"
              onclick="toggleScreenerWatchlist('${d.market}', '${escapeHtml(d.code)}', '${escapeHtml(d.name).replace(/'/g, "\\'")}', event)">
        <i class="ti ${isInWatchlist(d.market, d.code) ? 'ti-star-filled' : 'ti-star'}" aria-hidden="true"></i>
        <span>${isInWatchlist(d.market, d.code) ? 'Added to Watchlist' : 'Add to Watchlist'}</span>
      </button>
      ${d.market === 'KR' ? `
        <button type="button" class="btn-secondary" onclick="jumpToKrSwingBacktest('${escapeHtml(d.code)}', '${escapeHtml(d.name).replace(/'/g, "\\'")}')">
          <i class="ti ti-chart-candle" aria-hidden="true"></i> Backtest in KR Swing
        </button>` : ''}
    </div>

    <div class="chart-wrap" style="height:260px;">
      <canvas id="scr-detail-chart" role="img" aria-label="${escapeHtml(d.name)} price chart"></canvas>
    </div>
    <div style="font-size:11px;color:var(--text-muted);margin-top:6px;text-align:center;">Scroll to zoom, drag to pan</div>

    <div class="scr-detail-section">
      <div class="scr-detail-section-title">Trend Template — 8 Conditions</div>
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

function drawScreenerDetailChart(d) {
  const canvas = document.getElementById('scr-detail-chart');
  if (!canvas || !d.priceCurve?.length) return;
  if (scrDetailChart) scrDetailChart.destroy();

  const dates = d.priceCurve.map(p => p.date);
  const closes = d.priceCurve.map(p => p.close);
  const ma50 = smaSeries(closes, 50);
  const ma150 = smaSeries(closes, 150);
  const ma200 = smaSeries(closes, 200);
  const isUS = d.market === 'US';
  const fmtY = v => isUS ? `$${v.toFixed(0)}` : `${Number(v).toLocaleString('en-US')}`;

  // data 배열 원소를 null과 {x,y} 객체로 섞어서 넣으면 Chart.js가 그 데이터셋
  // 전체를 파싱하지 못해 선이 아예 안 그려진다(50/150/200일선이 안 보이던 원인) -
  // 항상 {x, y} 형태를 유지하고 y만 null로 둬서 그 구간만 끊기게(gap) 한다.
  const mkLine = (data, color, label, dash) => ({
    label, data: data.map((v, i) => ({ x: i, y: v })),
    borderColor: color, backgroundColor: 'transparent', fill: false,
    pointRadius: 0, borderWidth: label === 'Close' ? 1.5 : 1.2, tension: 0.1, spanGaps: false,
    borderDash: dash || [],
  });

  scrDetailChart = new Chart(canvas, {
    type: 'line',
    data: {
      datasets: [
        mkLine(closes, '#888780', 'Close'),
        mkLine(ma50, '#E24B4A', 'MA50'),
        mkLine(ma150, '#EF9F27', 'MA150'),
        mkLine(ma200, '#378ADD', 'MA200'),
      ],
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      interaction: { mode: 'index', intersect: false },
      plugins: {
        legend: { display: true, position: 'top', labels: { boxWidth: 12, font: { size: 11 } } },
        tooltip: {
          callbacks: {
            title: items => items.length ? (dates[Math.round(items[0].parsed.x)] ?? '') : '',
            label: ctx => ctx.parsed.y == null ? undefined : ` ${ctx.dataset.label}: ${fmtY(ctx.parsed.y)}`,
          },
        },
        zoom: {
          zoom: { wheel: { enabled: true }, pinch: { enabled: true }, mode: 'x' },
          pan: { enabled: false },
          limits: { x: { min: 0, max: Math.max(dates.length - 1, 0), minRange: 10 } },
        },
      },
      scales: {
        y: { ticks: { callback: v => fmtY(Number(v)) }, grid: { color: 'rgba(128,128,128,0.1)' } },
        x: {
          type: 'linear', min: 0, max: Math.max(dates.length - 1, 0),
          ticks: { maxTicksLimit: 8, callback: v => dates[Math.round(v)] ?? '' },
          grid: { display: false },
        },
      },
    },
  });
  attachChartPan(scrDetailChart, canvas, scrDetailPanState);
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
  el.innerHTML = `<div class="loading-msg"><i class="ti ti-loader-2" aria-hidden="true"></i>Loading...</div>`;
  try {
    const positions = await api('GET', '/api/infinite/positions');
    renderInfinitePositions(positions);
  } catch (e) {
    el.innerHTML = `<div class="error-msg"><i class="ti ti-alert-circle" aria-hidden="true"></i>${e.message}</div>`;
  }
}

function renderInfinitePositions(positions) {
  const el = document.getElementById('live-positions');
  if (!positions.length) {
    el.innerHTML = `<div class="empty-state"><i class="ti ti-infinity" aria-hidden="true"></i><p>No active Infinite Buying positions</p><small>Enter a ticker and capital above to start</small></div>`;
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
          <span class="cycle-pill">Cycle ${p.cycle}</span>
          ${p.lossCutMode ? `<span class="cycle-pill" style="background:var(--red-bg);color:var(--red);">Splits Exhausted</span>` : ''}
        </div>
        <button class="btn-icon" onclick="deleteInfinitePosition(${p.id})" aria-label="Delete position"><i class="ti ti-trash" aria-hidden="true"></i></button>
      </div>
      <div class="live-section-label">Basic Info</div>
      <div class="bt-summary-grid live-info-grid">
        <div class="meta-item"><div class="meta-label">Capital</div><div class="meta-value">$${p.seed.toLocaleString('en-US',{maximumFractionDigits:0})}</div></div>
        <div class="meta-item"><div class="meta-label">Capital Used</div><div class="meta-value">$${p.usedSeed.toLocaleString('en-US',{maximumFractionDigits:0})}</div></div>
        <div class="meta-item"><div class="meta-label">Split Amount</div><div class="meta-value">$${p.splitAmount.toLocaleString('en-US',{maximumFractionDigits:0})}</div></div>
      </div>

      <div class="live-section-label">Position Info</div>
      <div class="bt-summary-grid live-info-grid">
        <div class="meta-item"><div class="meta-label">Avg Price</div><div class="meta-value">${p.avgPrice ? '$' + p.avgPrice.toFixed(2) : '-'}</div></div>
        <div class="meta-item"><div class="meta-label">Holding Qty</div><div class="meta-value">${p.holdingQty}</div></div>
        <div class="meta-item"><div class="meta-label">Buy Amount</div><div class="meta-value">$${p.buyAmount.toLocaleString('en-US',{maximumFractionDigits:0})}</div></div>
      </div>

      <div class="live-section-label">Infinite Buying Formula</div>
      <div class="bt-summary-grid live-info-grid">
        <div class="meta-item"><div class="meta-label">T</div><div class="meta-value">${p.tValue}</div></div>
        <div class="meta-item"><div class="meta-label">Target Return</div><div class="meta-value">${p.targetReturnPct}%</div></div>
        <div class="meta-item"><div class="meta-label">Star %</div><div class="meta-value">${p.starPct !== null ? p.starPct.toFixed(2) + '%' : '-'}</div></div>
      </div>

      <div class="live-section-label">Valuation</div>
      <div class="bt-summary-grid live-info-grid">
        <div class="meta-item"><div class="meta-label">Price</div><div class="meta-value">${p.currentPrice ? '$' + p.currentPrice.toFixed(2) : '-'}</div></div>
        <div class="meta-item"><div class="meta-label">Unrealized P/L</div><div class="meta-value ${pnlCls}">${p.evalPnl>=0?'+':''}$${Math.abs(p.evalPnl).toLocaleString('en-US',{maximumFractionDigits:2})}</div></div>
        <div class="meta-item"><div class="meta-label">Return</div><div class="meta-value ${pnlCls}">${p.returnPct>=0?'+':''}${p.returnPct.toFixed(1)}%</div></div>
      </div>

      <div class="${recBoxCls}">
        <div class="live-rec-title"><i class="ti ti-bulb" aria-hidden="true"></i> Infinite Buying Guide <span class="cycle-pill">${p.version.toUpperCase()}</span></div>
        <div class="live-rec-orders">
          ${rec.orders.map(o => `
            <div class="live-rec-order-row">
              <div class="live-rec-pills">
                <span class="pill ${o.action === 'sell' ? 'pill-sell' : 'pill-buy'}">${o.action === 'sell' ? 'Sell' : 'Buy'}</span>
                <span class="pill pill-rec">${o.orderType}</span>
                ${o.pct !== undefined ? `<span style="font-size:12px;color:var(--text-secondary);">${o.pct>=0?'+':''}${o.pct}%</span>` : ''}
              </div>
              <div class="live-rec-order-value">
                ${o.price !== null ? '$' + o.price.toFixed(2) : ''}${o.price !== null && o.qty !== null ? ' × ' : ''}${o.qty !== null ? o.qty + ' sh' : ''}
              </div>
            </div>`).join('')}
        </div>
        <div class="live-rec-note">${escapeHtml(rec.note)}</div>
      </div>

      <div class="add-form">
        <input type="date" id="live-trade-date-${p.id}" style="width:150px;" />
        <select id="live-trade-action-${p.id}" style="width:90px;">
          <option value="buy">Buy</option>
          <option value="sell">Sell</option>
        </select>
        <input type="number" id="live-trade-price-${p.id}" placeholder="Price ($)" style="width:110px;" step="0.01" />
        <input type="number" id="live-trade-qty-${p.id}" placeholder="Qty" style="width:90px;" step="1" min="1" />
        <button class="btn-primary" onclick="addInfiniteTrade(${p.id})"><i class="ti ti-plus" aria-hidden="true"></i> Add Trade</button>
        <button class="btn-secondary" onclick="toggleLiveTrades(${p.id})"><i class="ti ti-list" aria-hidden="true"></i> Trade History (${p.tradeCount})</button>
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
  tradesEl.innerHTML = `<div class="loading-msg"><i class="ti ti-loader-2" aria-hidden="true"></i>Loading...</div>`;
  try {
    const trades = await api('GET', `/api/infinite/positions/${positionId}/trades`);
    if (!trades.length) {
      tradesEl.innerHTML = `<div class="empty-state"><p>No trade history</p></div>`;
      return;
    }
    tradesEl.innerHTML = `
      <div class="pf-table-wrap">
        <table class="pf-table">
          <thead><tr><th>Date</th><th>Type</th><th>Price</th><th>Qty</th><th></th></tr></thead>
          <tbody>
            ${trades.slice().reverse().map(t => `
              <tr>
                <td>${t.date}</td>
                <td><span class="${t.action === 'buy' ? 'negative' : 'positive'}" style="font-weight:600;">${t.action === 'buy' ? 'Buy' : 'Sell'}</span></td>
                <td>$${t.price.toFixed(2)}</td>
                <td>${t.qty}</td>
                <td><button class="btn-icon" onclick="deleteInfiniteTrade(${positionId}, ${t.id})" aria-label="Delete trade"><i class="ti ti-trash" aria-hidden="true"></i></button></td>
              </tr>`).join('')}
          </tbody>
        </table>
      </div>`;
  } catch (e) {
    tradesEl.innerHTML = `<div class="error-msg">${e.message}</div>`;
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
const LAB_UNIT_LABEL = { pt: 'Points', usd: 'Dollars ($)', pct: 'Rate (%)' };

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
  if (labTickers.includes(ticker)) { showToast(`${ticker} is already added`); return; }
  if (labTickers.length >= 8) { showToast('You can compare up to 8 at a time'); return; }
  labTickers.push(ticker);
  input.value = '';
  renderLabChips();
}

function removeLabTicker(ticker) {
  labTickers = labTickers.filter(t => t !== ticker);
  renderLabChips();
}

function renderLabChips() {
  const el = document.getElementById('lab-ticker-chips');
  el.innerHTML = labTickers.map((t, i) => `
    <span class="lab-chip">
      <span class="lab-chip-swatch" style="background:${LAB_COLORS[i % LAB_COLORS.length]};"></span>
      ${escapeHtml(t)}
      <button type="button" onclick="removeLabTicker('${t}')" aria-label="Remove ${t}"><i class="ti ti-x" aria-hidden="true"></i></button>
    </span>`).join('') || `<span style="font-size:12px;color:var(--text-muted);">Add a ticker/index to compare</span>`;
}

let labInterval = 'daily';
let labLogScale = false;

async function runLabCompare() {
  const start = document.getElementById('lab-start').value;
  const end = document.getElementById('lab-end').value;
  const el = document.getElementById('lab-result');

  if (!labTickers.length) { alert('티커/지수를 1개 이상 추가하세요'); return; }
  if (!start || !end) { alert('시작일과 종료일을 입력하세요'); return; }

  el.innerHTML = `<div class="loading-msg"><i class="ti ti-loader-2" aria-hidden="true"></i>Loading prices...</div>`;
  try {
    const data = await api('POST', '/api/lab/series', { tickers: labTickers, start, end });
    labSeriesData = data;
    renderLabResult(data);
  } catch (e) {
    el.innerHTML = `<div class="error-msg"><i class="ti ti-alert-circle" aria-hidden="true"></i>${e.message}</div>`;
  }
}

const LAB_INTERVALS = [
  { key: 'daily', label: 'Daily' },
  { key: 'weekly', label: 'Weekly' },
  { key: 'monthly', label: 'Monthly' },
  { key: 'yearly', label: 'Yearly' },
];

function renderLabResult(data) {
  const el = document.getElementById('lab-result');
  const invalid = data.invalidTickers || [];

  el.innerHTML = `
    ${invalid.length ? `
      <div class="error-msg">
        <i class="ti ti-alert-circle" aria-hidden="true"></i>
        다음 티커는 데이터를 찾을 수 없습니다: ${invalid.map(escapeHtml).join(', ')} —
        실제 존재하는 심볼인지 확인하세요 (지수는 ^ 접두사가 필요합니다. 예: 나스닥종합 ^IXIC, S&amp;P500 ^GSPC, 10년물 국채금리 ^TNX)
      </div>` : ''}

    <div class="card">
      <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px;margin-bottom:10px;">
        <div style="font-size:13px;font-weight:600;">Compare (separate axis per unit)</div>
        <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;">
          <div class="lab-interval-group">
            ${LAB_INTERVALS.map(iv => `<button type="button" class="lab-interval-btn ${iv.key === labInterval ? 'active' : ''}" data-interval="${iv.key}" onclick="setLabInterval('${iv.key}')">${iv.label}</button>`).join('')}
          </div>
          <label style="display:flex;align-items:center;gap:5px;font-size:12px;color:var(--text-secondary);cursor:pointer;">
            <input type="checkbox" id="lab-log-scale" ${labLogScale ? 'checked' : ''} onchange="toggleLabLogScale()" /> Log scale
          </label>
          <button class="btn-secondary" onclick="resetLabZoom()" style="padding:4px 10px;font-size:12px;"><i class="ti ti-zoom-reset" aria-hidden="true"></i> Reset Zoom</button>
        </div>
      </div>
      <div class="chart-wrap">
        <canvas id="lab-chart" role="img" aria-label="Comparison chart"></canvas>
      </div>
      <div style="font-size:11px;color:var(--text-muted);margin-top:6px;text-align:center;">Scroll to zoom, drag to pan</div>
    </div>

    <div class="card">
      <div style="font-size:13px;font-weight:600;margin-bottom:10px;">Find Condition Ranges</div>
      <div id="lab-cond-rows" style="display:flex;flex-direction:column;gap:8px;"></div>
      <div style="display:flex;align-items:center;gap:14px;flex-wrap:wrap;margin-top:10px;">
        <button class="btn-secondary" onclick="addLabConditionRow()" style="padding:4px 10px;font-size:12px;"><i class="ti ti-plus" aria-hidden="true"></i> Add Condition</button>
        <div id="lab-cond-combinator-wrap" style="display:none;align-items:center;gap:10px;font-size:12px;color:var(--text-secondary);">
          Combine:
          <label style="display:flex;align-items:center;gap:4px;cursor:pointer;">
            <input type="radio" name="lab-cond-combinator" value="AND" onchange="setLabCombinator('AND')" /> AND (all match)
          </label>
          <label style="display:flex;align-items:center;gap:4px;cursor:pointer;">
            <input type="radio" name="lab-cond-combinator" value="OR" onchange="setLabCombinator('OR')" /> OR (any match)
          </label>
        </div>
        <button class="btn-primary" onclick="applyLabCondition()"><i class="ti ti-highlight" aria-hidden="true"></i> Highlight Ranges</button>
        <button class="btn-secondary" onclick="clearLabCondition()"><i class="ti ti-x" aria-hidden="true"></i> Reset</button>
      </div>
      <div id="lab-regions"></div>
    </div>
  `;
  labConditionRows = [{ id: labConditionRowSeq++, ticker: data.series[0] ? data.series[0].ticker : '', metric: 'change', op: 'lte', threshold: -3 }];
  labCombinator = 'AND';
  renderLabConditionRowsUi();
  setTimeout(() => drawLabChart(getLabDisplayData(), []), 80);
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
        <option value="change" ${row.metric === 'change' ? 'selected' : ''}>Change vs. prior period (%)</option>
        <option value="close" ${row.metric === 'close' ? 'selected' : ''}>Value (Close)</option>
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
    const t = document.getElementById(`lab-cond-ticker-${row.id}`);
    const m = document.getElementById(`lab-cond-metric-${row.id}`);
    const o = document.getElementById(`lab-cond-op-${row.id}`);
    const th = document.getElementById(`lab-cond-threshold-${row.id}`);
    if (t) row.ticker = t.value;
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
      title: { display: true, text: LAB_UNIT_LABEL[unit], font: { size: 11 } },
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
    const metricLabel = row.metric === 'change' ? 'Change (%)' : 'Value';
    const opLabel = { gte: 'or more', gt: 'more than', lte: 'or less', lt: 'less than' }[row.op];
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
  const joiner = combinator === 'OR' ? ' or ' : ' and ';
  const summary = `Ranges matching ${summaries.map(escapeHtml).join(joiner)}: ${regions.length}`;

  if (!regions.length) {
    el.innerHTML = `<div class="empty-state" style="padding:1.5rem;"><p>${summary}</p><small>No ranges match this condition</small></div>`;
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
