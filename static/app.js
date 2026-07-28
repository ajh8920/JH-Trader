'use strict';

let priceChart = null;

function escapeHtml(str) {
  return String(str ?? '').replace(/[&<>"']/g, c => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[c]));
}

// ─── API 호출 (Python 백엔드 경유) ───────────────────────────────────────────

async function api(method, path, body) {
  const opts = { method, headers: { 'Content-Type': 'application/json' } };
  if (body) opts.body = JSON.stringify(body);
  const res = await fetch(path, opts);
  if (res.status === 401) {
    window.location.href = '/login';
    throw new Error('로그인이 필요합니다');
  }
  const data = await res.json();
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
        <strong>Finnhub API 키 변경</strong>
        <p><a href="https://finnhub.io/dashboard" target="_blank" rel="noopener">finnhub.io 대시보드</a>에서 API Key를 복사하세요</p>
      </div>
      <div class="api-key-row">
        <input type="text" id="api-key-input" placeholder="API 키 붙여넣기" style="width:280px;" />
        <button class="btn-primary" onclick="saveApiKey()">저장 및 확인</button>
        <button class="btn-secondary" onclick="document.getElementById('api-key-banner').remove()">취소</button>
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
  btn.textContent = '확인 중...';
  if (errEl) { errEl.style.display = 'none'; errEl.textContent = ''; }

  try {
    await api('POST', '/api/settings/key', { key });
    document.getElementById('api-key-banner')?.remove();
    showToast('API 키가 저장되었습니다');
  } catch (e) {
    if (errEl) { errEl.textContent = e.message; errEl.style.display = 'block'; }
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = '저장 및 확인'; }
  }
}

// ─── 탭 전환 ─────────────────────────────────────────────────────────────────

function switchTab(name) {
  document.querySelectorAll('.tab').forEach((btn, i) => {
    const active = ['macro', 'search', 'portfolio', 'alerts', 'backtest', 'live'][i] === name;
    btn.classList.toggle('active', active);
  });
  document.querySelectorAll('.section').forEach(s => s.classList.remove('active'));
  document.getElementById('sec-' + name).classList.add('active');
  if (name === 'macro') loadMacro();
  if (name === 'portfolio') loadPortfolio();
  if (name === 'alerts') loadAlerts();
  if (name === 'backtest') initBacktestDates();
  if (name === 'live') loadInfinitePositions();
}

document.addEventListener('DOMContentLoaded', () => loadMacro());

// ─── 매크로(주요 시황) ────────────────────────────────────────────────────────

async function loadMacro() {
  const el = document.getElementById('macro-content');
  if (!el || el.dataset.loaded === '1') return;
  el.innerHTML = `<div class="loading-msg"><i class="ti ti-loader-2" aria-hidden="true"></i>주요 시황 불러오는 중...</div>`;
  try {
    const data = await api('GET', '/api/macro');
    renderMacro(data);
    el.dataset.loaded = '1';
  } catch (e) {
    el.innerHTML = `<div class="error-msg"><i class="ti ti-alert-circle" aria-hidden="true"></i>${e.message}</div>`;
  }
}

function renderMacro(items) {
  const el = document.getElementById('macro-content');
  const groups = {};
  items.forEach(item => {
    (groups[item.group] ??= []).push(item);
  });

  el.innerHTML = `
    <div class="add-form" style="justify-content:flex-end;">
      <button class="btn-secondary" onclick="refreshMacro()"><i class="ti ti-refresh" aria-hidden="true"></i> 새로고침</button>
    </div>
    ${Object.entries(groups).map(([group, list]) => `
      <div class="card">
        <div style="font-size:12px;font-weight:600;color:var(--text-secondary);margin-bottom:10px;text-transform:uppercase;letter-spacing:0.04em;">${escapeHtml(group)}</div>
        <div class="macro-grid">
          ${list.map(m => {
            const isPos = m.changePct >= 0;
            const hasPrice = !!m.price;
            return `
            <div class="macro-item">
              <div class="macro-name">${escapeHtml(m.name)}</div>
              <div class="macro-ticker">${escapeHtml(m.ticker)}</div>
              ${hasPrice ? `
                <div class="macro-price">$${m.price.toFixed(2)}</div>
                <div class="macro-change ${isPos ? 'positive' : 'negative'}">
                  <i class="ti ti-trending-${isPos ? 'up' : 'down'}" aria-hidden="true"></i>
                  ${isPos ? '+' : ''}${m.changePct.toFixed(2)}%
                </div>
              ` : `<div class="macro-price" style="color:var(--text-muted);font-size:13px;">데이터 없음</div>`}
            </div>`;
          }).join('')}
        </div>
      </div>`).join('')}
  `;
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
  el.innerHTML = `<div class="loading-msg"><i class="ti ti-loader-2" aria-hidden="true"></i>${ticker} 데이터 불러오는 중...</div>`;

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
        <div class="ohlc-item"><div class="ohlc-label">시가</div><div class="ohlc-value">$${d.open.toFixed(2)}</div></div>
        <div class="ohlc-item"><div class="ohlc-label">고가</div><div class="ohlc-value positive">$${d.high.toFixed(2)}</div></div>
        <div class="ohlc-item"><div class="ohlc-label">저가</div><div class="ohlc-value negative">$${d.low.toFixed(2)}</div></div>
        <div class="ohlc-item"><div class="ohlc-label">전일종가</div><div class="ohlc-value">$${d.prevClose.toFixed(2)}</div></div>
      </div>

      ${d.targetMean ? `
        <div class="meta-grid">
          <div class="meta-item">
            <div class="meta-label">평균 목표가</div>
            <div class="meta-value" style="color:${upsideColor};">$${d.targetMean.toFixed(2)}</div>
          </div>
          <div class="meta-item">
            <div class="meta-label">최저 목표가</div>
            <div class="meta-value">$${d.targetLow?.toFixed(2) ?? '-'}</div>
          </div>
          <div class="meta-item">
            <div class="meta-label">최고 목표가</div>
            <div class="meta-value">$${d.targetHigh?.toFixed(2) ?? '-'}</div>
          </div>
        </div>
        <div class="upside-bar-wrap">
          <div class="bar-label">
            <span>저 $${d.targetLow?.toFixed(0)}</span>
            <span style="color:${upsideColor};font-weight:600;">상승여력 ${upside >= 0 ? '+' : ''}${upside.toFixed(1)}%</span>
            <span>고 $${d.targetHigh?.toFixed(0)}</span>
          </div>
          <div class="bar-track">
            <div class="bar-fill" style="width:${barPct}%;background:${upside >= 0 ? '#639922' : '#E24B4A'};"></div>
          </div>
          <div class="bar-hint">현재가의 저가-고가 목표범위 내 위치 ${d.targetUpdated ? '· 업데이트 ' + d.targetUpdated : ''}</div>
        </div>
      ` : `<div style="padding:14px 0;color:var(--text-secondary);font-size:13px;"><i class="ti ti-info-circle" aria-hidden="true"></i> 목표가 데이터 없음</div>`}

      ${total > 0 ? `
        <div class="analyst-breakdown">
          <div class="breakdown-header">
            <span>애널리스트 ${total}명 의견${d.recPeriod ? ' · ' + d.recPeriod : ''}</span>
            ${recLabel ? `<span class="pill pill-rec">${recLabel}</span>` : ''}
          </div>
          <div class="breakdown-bar">
            <div style="flex:${d.recBuy};background:#639922;border-radius:3px 0 0 3px;"></div>
            <div style="flex:${d.recHold};background:#EF9F27;"></div>
            <div style="flex:${d.recSell};background:#E24B4A;border-radius:0 3px 3px 0;"></div>
          </div>
          <div class="analyst-pills">
            ${d.recBuy > 0 ? `<span class="pill pill-buy"><i class="ti ti-thumb-up" aria-hidden="true"></i>매수 ${d.recBuy}명</span>` : ''}
            ${d.recHold > 0 ? `<span class="pill pill-hold"><i class="ti ti-minus" aria-hidden="true"></i>중립 ${d.recHold}명</span>` : ''}
            ${d.recSell > 0 ? `<span class="pill pill-sell"><i class="ti ti-thumb-down" aria-hidden="true"></i>매도 ${d.recSell}명</span>` : ''}
          </div>
        </div>
      ` : ''}

      <div class="action-row">
        <button class="btn-primary" onclick="addToPortfolioFromSearch('${d.ticker}', ${d.price})">
          <i class="ti ti-plus" aria-hidden="true"></i> 포트폴리오 추가
        </button>
        <button class="btn-secondary" onclick="addAlertFromSearch('${d.ticker}', ${d.targetMean || d.price})">
          <i class="ti ti-bell" aria-hidden="true"></i> 알림 설정
        </button>
      </div>
    </div>

    ${d.targetMean ? `
      <div class="card">
        <div style="font-size:13px;font-weight:600;margin-bottom:10px;"><i class="ti ti-chart-bar" aria-hidden="true"></i> 가격 vs 목표가 비교</div>
        <div class="chart-legend">
          <span><span class="legend-dot" style="background:#378ADD;"></span>현재가</span>
          <span><span class="legend-dot" style="background:#F09595;"></span>최저 목표가</span>
          <span><span class="legend-dot" style="background:#97C459;"></span>평균 목표가</span>
          <span><span class="legend-dot" style="background:#5DCAA5;"></span>최고 목표가</span>
        </div>
        <div class="chart-wrap">
          <canvas id="target-chart" role="img" aria-label="${d.ticker} 현재가 vs 목표가 비교 차트"></canvas>
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
      labels: ['현재가', '최저 목표가', '평균 목표가', '최고 목표가'],
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
  el.innerHTML = `<div class="loading-msg"><i class="ti ti-loader-2" aria-hidden="true"></i>불러오는 중...</div>`;
  try {
    const data = await api('GET', '/api/portfolio');
    renderPortfolio(data);
  } catch (e) {
    el.innerHTML = `<div class="error-msg"><i class="ti ti-alert-circle" aria-hidden="true"></i>${e.message}</div>`;
  }
}

async function refreshPortfolio() {
  const el = document.getElementById('portfolio-content');
  el.innerHTML = `<div class="loading-msg"><i class="ti ti-loader-2" aria-hidden="true"></i>가격 업데이트 중...</div>`;
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
    el.innerHTML = `<div class="empty-state"><i class="ti ti-briefcase" aria-hidden="true"></i><p>포트폴리오가 비어 있습니다</p><small>종목 검색 후 추가하거나 위에서 직접 입력하세요</small></div>`;
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
      <div class="meta-item"><div class="meta-label">총 평가금액</div><div class="meta-value">$${totalValue.toLocaleString('en-US', {maximumFractionDigits:0})}</div></div>
      <div class="meta-item"><div class="meta-label">총 매입금액</div><div class="meta-value">$${totalCost.toLocaleString('en-US', {maximumFractionDigits:0})}</div></div>
      <div class="meta-item"><div class="meta-label">총 손익</div><div class="meta-value ${pnl >= 0 ? 'positive' : 'negative'}">${pnl >= 0 ? '+' : ''}$${Math.abs(pnl).toLocaleString('en-US', {maximumFractionDigits:0})} <span style="font-size:13px;">(${pnlPct.toFixed(1)}%)</span></div></div>
    </div>
    <div class="pf-table-wrap">
      <table class="pf-table">
        <thead><tr><th>종목</th><th>현재가</th><th>목표가</th><th>상승여력</th><th>수량</th><th>손익</th><th></th></tr></thead>
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
              <td><button class="btn-icon" onclick="removePortfolioItem('${p.ticker}')" aria-label="${p.ticker} 삭제"><i class="ti ti-trash" aria-hidden="true"></i></button></td>
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
    el.innerHTML = `<div class="empty-state"><i class="ti ti-bell-off" aria-hidden="true"></i><p>설정된 알림이 없습니다</p><small>위에서 종목과 가격 기준을 입력해 추가하세요</small></div>`;
    return;
  }

  el.innerHTML = `<div class="notif-list">${alerts.map(a => `
    <div class="notif-item">
      <div>
        <div class="notif-ticker">${escapeHtml(a.ticker)} <span class="${a.triggered ? 'badge-done' : 'badge-active'}">${a.triggered ? '달성됨' : '대기중'}</span></div>
        <div class="notif-info">$${a.price.toFixed(2)} ${a.type === 'above' ? '이상 도달' : '이하 하락'} 시 알림 · ${a.created}${a.triggeredAt ? ` · <span style="color:var(--green)">달성 ${a.triggeredAt}</span>` : ''}</div>
      </div>
      <button class="btn-icon" onclick="removeAlert(${a.id})" aria-label="알림 삭제"><i class="ti ti-trash" aria-hidden="true"></i></button>
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

  el.innerHTML = `<div class="loading-msg"><i class="ti ti-loader-2" aria-hidden="true"></i>${ticker} 백테스트 진행 중...</div>`;

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
        <div class="meta-item"><div class="meta-label">시드</div><div class="meta-value">$${d.seed.toLocaleString('en-US', {maximumFractionDigits:0})}</div></div>
        <div class="meta-item"><div class="meta-label">총 매수 수량</div><div class="meta-value">${d.totalBuyQty.toLocaleString('en-US')}주</div></div>
        <div class="meta-item"><div class="meta-label">총 매도 수량</div><div class="meta-value">${d.totalSellQty.toLocaleString('en-US')}주</div></div>
        <div class="meta-item"><div class="meta-label">보유 수량</div><div class="meta-value">${holding.qty.toLocaleString('en-US')}주</div></div>
        <div class="meta-item"><div class="meta-label">평단가</div><div class="meta-value">${holding.qty > 0 ? '$' + holding.avgPrice.toFixed(2) : '-'}</div></div>
        <div class="meta-item"><div class="meta-label">매입 금액</div><div class="meta-value">$${d.totalBuyAmount.toLocaleString('en-US', {maximumFractionDigits:2})}</div></div>
        <div class="meta-item"><div class="meta-label">매도 금액</div><div class="meta-value">$${d.totalSellAmount.toLocaleString('en-US', {maximumFractionDigits:2})}</div></div>
        <div class="meta-item"><div class="meta-label">평가 손익</div><div class="meta-value ${pnlCls}">${d.evalPnl>=0?'+':''}$${Math.abs(d.evalPnl).toLocaleString('en-US', {maximumFractionDigits:2})}</div></div>
        <div class="meta-item"><div class="meta-label">수익률</div><div class="meta-value ${pnlCls}">${d.returnPct>=0?'+':''}${d.returnPct.toFixed(1)}%</div></div>
        <div class="meta-item"><div class="meta-label">시드 대비 수익률</div><div class="meta-value ${pnlCls}">${d.seedReturnPct>=0?'+':''}${d.seedReturnPct.toFixed(1)}%</div></div>
        <div class="meta-item"><div class="meta-label">목표 수익률</div><div class="meta-value">${d.targetReturnPct}%</div></div>
        <div class="meta-item"><div class="meta-label">분할수</div><div class="meta-value">${d.splits}</div></div>
        <div class="meta-item"><div class="meta-label">전략 MDD</div><div class="meta-value negative">-${d.mddPct.toFixed(1)}%</div></div>
        <div class="meta-item"><div class="meta-label">${escapeHtml(d.benchmark.label)} 수익률</div><div class="meta-value ${d.benchmark.returnPct>=0?'positive':'negative'}">${d.benchmark.returnPct>=0?'+':''}${d.benchmark.returnPct.toFixed(1)}%</div></div>
        <div class="meta-item"><div class="meta-label">${escapeHtml(d.benchmark.label)} MDD</div><div class="meta-value negative">-${d.benchmark.mddPct.toFixed(1)}%</div></div>
        <div class="meta-item"><div class="meta-label">알파(초과수익)</div><div class="meta-value ${d.alphaPct>=0?'positive':'negative'}">${d.alphaPct>=0?'+':''}${d.alphaPct.toFixed(1)}%p</div></div>
      </div>

      <div class="bt-holding-box">
        <span class="cycle-pill">${d.version.toUpperCase()}</span>
        <span class="cycle-pill">완료 사이클 ${d.completedCycles}회</span>
        ${holding.lossCutMode ? `<span class="cycle-pill" style="background:var(--red-bg);color:var(--red);">쿼터손절모드</span>` : ''}
        ${holding.qty > 0
          ? ` · 현재 보유 중: ${holding.qty}주 @ 평단 $${holding.avgPrice.toFixed(2)} · 현재가 $${holding.currentPrice.toFixed(2)} · 평가금액 $${holding.value.toLocaleString('en-US',{maximumFractionDigits:2})} (T값 ${holding.tValue}/${d.splits})`
          : ` · 백테스트 종료 시점 보유 없음 (전량 매도 완료)`}
        <div style="font-size:11px;color:var(--text-muted);margin-top:4px;">기간: ${d.start} ~ ${d.end}</div>
      </div>

      <div style="font-size:13px;font-weight:600;margin:14px 0 8px;">수익률 비교 (전략 vs ${escapeHtml(d.benchmark.label)})</div>
      <div class="chart-wrap">
        <canvas id="return-chart" role="img" aria-label="수익률 비교 차트"></canvas>
      </div>
    </div>

    <div class="card">
      <div style="font-size:13px;font-weight:600;margin-bottom:8px;">${escapeHtml(d.ticker)} 가격 차트 (매수·매도 시점 표시)</div>
      <div class="chart-wrap">
        <canvas id="price-chart" role="img" aria-label="${escapeHtml(d.ticker)} 가격 차트"></canvas>
      </div>
    </div>

    <div class="pf-table-wrap">
      <table class="pf-table">
        <thead><tr><th>회차</th><th>날짜</th><th>구분</th><th>가격</th><th>수량</th><th>누적수량</th><th>평단가</th><th>수익률</th><th>메모</th></tr></thead>
        <tbody>
          ${d.trades.length ? d.trades.map(t => `
            <tr>
              <td>${t.cycle}</td>
              <td>${t.date}</td>
              <td><span class="${t.action === 'buy' ? 'negative' : 'positive'}" style="font-weight:600;">${t.action === 'buy' ? '매수' : '매도'}</span></td>
              <td>$${t.price.toFixed(2)}</td>
              <td>${t.qty}</td>
              <td>${t.qtyAfter}</td>
              <td>${t.avgPriceAfter !== null ? '$' + t.avgPriceAfter.toFixed(2) : '-'}</td>
              <td class="${t.returnPctAfter >= 0 ? 'positive' : 'negative'}">${t.returnPctAfter>=0?'+':''}${t.returnPctAfter.toFixed(1)}%</td>
              <td style="font-size:12px;color:var(--text-secondary);">${escapeHtml(t.note)}</td>
            </tr>`).join('') : `<tr><td colspan="9" style="text-align:center;color:var(--text-secondary);">해당 기간 동안 체결된 거래가 없습니다</td></tr>`}
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
    label: '무한매수법',
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
          label: `${ticker} 종가`, data: priceCurve.map(p => p.close),
          borderColor: '#888780', backgroundColor: 'transparent',
          fill: false, pointRadius: 0, borderWidth: 1.5, tension: 0.1, order: 3,
        },
        {
          label: '매수', data: buyPoints, type: 'scatter',
          backgroundColor: '#E24B4A', borderColor: '#E24B4A',
          pointRadius: 4, pointStyle: 'triangle', order: 1,
        },
        {
          label: '매도', data: sellPoints, type: 'scatter',
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
  el.innerHTML = `<div class="loading-msg"><i class="ti ti-loader-2" aria-hidden="true"></i>불러오는 중...</div>`;
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
    el.innerHTML = `<div class="empty-state"><i class="ti ti-infinity" aria-hidden="true"></i><p>진행 중인 무한매수법 포지션이 없습니다</p><small>위에서 티커와 시드를 입력해 시작하세요</small></div>`;
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
          <span class="cycle-pill">사이클 ${p.cycle}</span>
          ${p.lossCutMode ? `<span class="cycle-pill" style="background:var(--red-bg);color:var(--red);">분할소진</span>` : ''}
        </div>
        <button class="btn-icon" onclick="deleteInfinitePosition(${p.id})" aria-label="포지션 삭제"><i class="ti ti-trash" aria-hidden="true"></i></button>
      </div>
      <div class="live-section-label">기본 정보</div>
      <div class="bt-summary-grid" style="grid-template-columns:repeat(3,1fr);">
        <div class="meta-item"><div class="meta-label">시드</div><div class="meta-value">$${p.seed.toLocaleString('en-US',{maximumFractionDigits:0})}</div></div>
        <div class="meta-item"><div class="meta-label">사용한 시드</div><div class="meta-value">$${p.usedSeed.toLocaleString('en-US',{maximumFractionDigits:0})}</div></div>
        <div class="meta-item"><div class="meta-label">1회 투자금</div><div class="meta-value">$${p.splitAmount.toLocaleString('en-US',{maximumFractionDigits:0})}</div></div>
      </div>

      <div class="live-section-label">매입 정보</div>
      <div class="bt-summary-grid" style="grid-template-columns:repeat(3,1fr);">
        <div class="meta-item"><div class="meta-label">평단가</div><div class="meta-value">${p.avgPrice ? '$' + p.avgPrice.toFixed(2) : '-'}</div></div>
        <div class="meta-item"><div class="meta-label">보유 수량</div><div class="meta-value">${p.holdingQty}</div></div>
        <div class="meta-item"><div class="meta-label">매입 금액</div><div class="meta-value">$${p.buyAmount.toLocaleString('en-US',{maximumFractionDigits:0})}</div></div>
      </div>

      <div class="live-section-label">무한매수 공식</div>
      <div class="bt-summary-grid" style="grid-template-columns:repeat(3,1fr);">
        <div class="meta-item"><div class="meta-label">T</div><div class="meta-value">${p.tValue}</div></div>
        <div class="meta-item"><div class="meta-label">목표 수익률</div><div class="meta-value">${p.targetReturnPct}%</div></div>
        <div class="meta-item"><div class="meta-label">Star 값</div><div class="meta-value">${p.starPct !== null ? p.starPct.toFixed(2) + '%' : '-'}</div></div>
      </div>

      <div class="live-section-label">평가</div>
      <div class="bt-summary-grid" style="grid-template-columns:repeat(3,1fr);">
        <div class="meta-item"><div class="meta-label">현재가</div><div class="meta-value">${p.currentPrice ? '$' + p.currentPrice.toFixed(2) : '-'}</div></div>
        <div class="meta-item"><div class="meta-label">평가손익</div><div class="meta-value ${pnlCls}">${p.evalPnl>=0?'+':''}$${Math.abs(p.evalPnl).toLocaleString('en-US',{maximumFractionDigits:2})}</div></div>
        <div class="meta-item"><div class="meta-label">수익률</div><div class="meta-value ${pnlCls}">${p.returnPct>=0?'+':''}${p.returnPct.toFixed(1)}%</div></div>
      </div>

      <div class="${recBoxCls}">
        <div class="live-rec-title"><i class="ti ti-bulb" aria-hidden="true"></i> 무한매수법 가이드 <span class="cycle-pill">${p.version.toUpperCase()}</span></div>
        <div class="live-rec-orders">
          ${rec.orders.map(o => `
            <div class="live-rec-order-row">
              <div class="live-rec-pills">
                <span class="pill ${o.action === 'sell' ? 'pill-sell' : 'pill-buy'}">${o.action === 'sell' ? '매도' : '매수'}</span>
                <span class="pill pill-rec">${o.orderType}</span>
                ${o.pct !== undefined ? `<span style="font-size:12px;color:var(--text-secondary);">${o.pct>=0?'+':''}${o.pct}%</span>` : ''}
              </div>
              <div class="live-rec-order-value">
                ${o.price !== null ? '$' + o.price.toFixed(2) : ''}${o.price !== null && o.qty !== null ? ' × ' : ''}${o.qty !== null ? o.qty + '주' : ''}
              </div>
            </div>`).join('')}
        </div>
        <div class="live-rec-note">${escapeHtml(rec.note)}</div>
      </div>

      <div class="add-form">
        <input type="date" id="live-trade-date-${p.id}" style="width:150px;" />
        <select id="live-trade-action-${p.id}" style="width:90px;">
          <option value="buy">매수</option>
          <option value="sell">매도</option>
        </select>
        <input type="number" id="live-trade-price-${p.id}" placeholder="가격 ($)" style="width:110px;" step="0.01" />
        <input type="number" id="live-trade-qty-${p.id}" placeholder="수량" style="width:90px;" step="1" min="1" />
        <button class="btn-primary" onclick="addInfiniteTrade(${p.id})"><i class="ti ti-plus" aria-hidden="true"></i> 매매 추가</button>
        <button class="btn-secondary" onclick="toggleLiveTrades(${p.id})"><i class="ti ti-list" aria-hidden="true"></i> 매매기록 (${p.tradeCount})</button>
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
  tradesEl.innerHTML = `<div class="loading-msg"><i class="ti ti-loader-2" aria-hidden="true"></i>불러오는 중...</div>`;
  try {
    const trades = await api('GET', `/api/infinite/positions/${positionId}/trades`);
    if (!trades.length) {
      tradesEl.innerHTML = `<div class="empty-state"><p>매매 기록이 없습니다</p></div>`;
      return;
    }
    tradesEl.innerHTML = `
      <div class="pf-table-wrap">
        <table class="pf-table">
          <thead><tr><th>날짜</th><th>구분</th><th>가격</th><th>수량</th><th></th></tr></thead>
          <tbody>
            ${trades.slice().reverse().map(t => `
              <tr>
                <td>${t.date}</td>
                <td><span class="${t.action === 'buy' ? 'negative' : 'positive'}" style="font-weight:600;">${t.action === 'buy' ? '매수' : '매도'}</span></td>
                <td>$${t.price.toFixed(2)}</td>
                <td>${t.qty}</td>
                <td><button class="btn-icon" onclick="deleteInfiniteTrade(${positionId}, ${t.id})" aria-label="거래 삭제"><i class="ti ti-trash" aria-hidden="true"></i></button></td>
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
