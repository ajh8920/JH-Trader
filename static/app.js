'use strict';

let priceChart = null;

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
    const active = ['search', 'portfolio', 'alerts'][i] === name;
    btn.classList.toggle('active', active);
  });
  document.querySelectorAll('.section').forEach(s => s.classList.remove('active'));
  document.getElementById('sec-' + name).classList.add('active');
  if (name === 'portfolio') loadPortfolio();
  if (name === 'alerts') loadAlerts();
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
          <span class="ticker-badge">${d.ticker}</span>
          <div class="stock-name">${d.name}</div>
          ${d.industry ? `<div class="stock-industry">${d.industry}</div>` : ''}
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
              <td><div style="font-weight:600;">${p.ticker}</div><div style="font-size:11px;color:var(--text-secondary);">${p.name || ''}</div></td>
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
        <div class="notif-ticker">${a.ticker} <span class="${a.triggered ? 'badge-done' : 'badge-active'}">${a.triggered ? '달성됨' : '대기중'}</span></div>
        <div class="notif-info">$${a.price.toFixed(2)} ${a.type === 'above' ? '이상 도달' : '이하 하락'} 시 알림 · ${a.created}${a.triggeredAt ? ` · <span style="color:var(--green)">달성 ${a.triggeredAt}</span>` : ''}</div>
      </div>
      <button class="btn-icon" onclick="removeAlert(${a.id})" aria-label="알림 삭제"><i class="ti ti-trash" aria-hidden="true"></i></button>
    </div>`).join('')}</div>`;
}

async function removeAlert(id) {
  await api('DELETE', `/api/alerts/${id}`);
  loadAlerts();
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
