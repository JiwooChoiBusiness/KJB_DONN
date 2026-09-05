/* =========================================================
   DONN 부채 코치 PoC - app.js
   구성 순서: 0 상수/라벨, 1 DOM 헬퍼, 2 포맷 헬퍼, 3 API 헬퍼,
             4 상태, 5 라우터, 6 공용 UI 컴포넌트,
             7~12 화면별 렌더 함수, 13 채팅, 14 사이드바/설정,
             15 초기화 및 이벤트 연결.
   프레임워크 없음, 빌드 없음. 모든 동적 텍스트는 DOM 텍스트 노드로
   삽입하여 이스케이프한다(innerHTML로 서버 문자열을 넣지 않는다).
   ========================================================= */

/* ---------- 0. 상수 & 라벨 맵 ---------- */

const API_BASE = '/api';

const FALLBACK_DISCLAIMER =
  'DONN은 금융상품 판매·중개·자문 서비스가 아닌 정보 제공·계산 서비스입니다. ' +
  '표시된 금리는 금융감독원 및 공공기관 공시 기준이며 실제 적용 금리·한도와 다를 수 있습니다. ' +
  '최종 결정은 이용자 본인의 판단으로 하며, 상품 가입은 각 금융회사 공식 채널에서 진행하세요.';
const FALLBACK_AI_NOTICE =
  '이 화면의 설명 문장 일부는 생성형 AI가 작성합니다. 수치는 AI가 아닌 계산 엔진이 산출합니다.';

const ROUTES = ['home', 'debts', 'compare', 'spending', 'personas', 'decisions'];

const LOAN_TYPE_LABELS = {
  credit: '신용대출', mortgage: '주택담보대출', jeonse: '전세자금대출',
  student: '학자금대출', card_loan: '카드론', overdraft: '마이너스통장',
  policy: '정책상품대출', other: '기타',
};
const REPAY_METHOD_LABELS = {
  equal_payment: '원리금균등', equal_principal: '원금균등',
  bullet: '만기일시', revolving: '마이너스통장/리볼빙',
};
const RATE_TYPE_LABELS = { fixed: '고정금리', variable: '변동금리' };
const LENDER_GROUP_LABELS = {
  bank: '은행', savings_bank: '저축은행', card: '카드',
  capital: '캐피탈', insurance: '보험', policy: '정책기관', other: '기타',
};
const CATEGORY_LABELS = {
  deposit: '예금', saving: '적금', mortgage: '주택담보대출',
  jeonse: '전세자금대출', credit: '신용대출', policy: '정책상품',
};
const SORT_KEY_LABELS = {
  total_cost: '총이자(총비용)', monthly_payment: '월 납입액', rate: '금리',
};
const RATE_KIND_LABELS = {
  base: '기준금리', avg: '평균금리', min: '최저금리', max: '최고금리', preferential: '우대금리',
};
const CAPACITY_BAND_LABELS = { negative: '위험', tight: '빠듯', ok: '양호', comfortable: '여유' };
const FLAG_LABELS = {
  delinquency_signal: '연체 신호 있음', income_up: '소득 증가', job_changed: '이직/전직',
  self_employed: '자영업자', retirement_near: '은퇴 임박',
};
const DECISION_KIND_LABELS = { compare: '공시 비교', action: '행동 결정', scenario: '시나리오' };

const SVG_NS = 'http://www.w3.org/2000/svg';
const ICONS = {
  plus: [['line', { x1: 12, y1: 5, x2: 12, y2: 19 }], ['line', { x1: 5, y1: 12, x2: 19, y2: 12 }]],
  wallet: [['rect', { x: 3, y: 6, width: 18, height: 13, rx: 2 }], ['line', { x1: 3, y1: 10, x2: 21, y2: 10 }]],
  bars: [['line', { x1: 5, y1: 20, x2: 5, y2: 11 }], ['line', { x1: 12, y1: 20, x2: 12, y2: 4 }], ['line', { x1: 19, y1: 20, x2: 19, y2: 15 }]],
  receipt: [['path', { d: 'M6 2h12v20l-3-2-3 2-3-2-3 2V2z' }], ['line', { x1: 9, y1: 8, x2: 15, y2: 8 }], ['line', { x1: 9, y1: 12, x2: 15, y2: 12 }]],
  user: [['circle', { cx: 12, cy: 8, r: 4 }], ['path', { d: 'M4 20c0-4.4 3.6-7 8-7s8 2.6 8 7' }]],
  history: [['circle', { cx: 12, cy: 12, r: 8 }], ['polyline', { points: '12 8 12 12 15 14' }]],
  sliders: [
    ['line', { x1: 4, y1: 6, x2: 20, y2: 6 }], ['circle', { cx: 9, cy: 6, r: 2 }],
    ['line', { x1: 4, y1: 12, x2: 20, y2: 12 }], ['circle', { cx: 15, cy: 12, r: 2 }],
    ['line', { x1: 4, y1: 18, x2: 20, y2: 18 }], ['circle', { cx: 9, cy: 18, r: 2 }],
  ],
  send: [['line', { x1: 5, y1: 12, x2: 19, y2: 12 }], ['polyline', { points: '12 5 19 12 12 19' }]],
  hamburger: [['line', { x1: 4, y1: 7, x2: 20, y2: 7 }], ['line', { x1: 4, y1: 12, x2: 20, y2: 12 }], ['line', { x1: 4, y1: 17, x2: 20, y2: 17 }]],
  close: [['line', { x1: 6, y1: 6, x2: 18, y2: 18 }], ['line', { x1: 18, y1: 6, x2: 6, y2: 18 }]],
  chevronDown: [['polyline', { points: '6 9 12 15 18 9' }]],
  chevronLeft: [['polyline', { points: '15 6 9 12 15 18' }]],
  download: [['path', { d: 'M12 3v11' }], ['polyline', { points: '7 10 12 15 17 10' }], ['path', { d: 'M4 19h16' }]],
};

/* ---------- 1. DOM 헬퍼 ---------- */

function applyAttrs(node, attrs) {
  if (!attrs) return;
  for (const key of Object.keys(attrs)) {
    const v = attrs[key];
    if (v === null || v === undefined || v === false) continue;
    if (key === 'class') {
      node.setAttribute('class', v);
    } else if (key.startsWith('on') && typeof v === 'function') {
      node.addEventListener(key.slice(2).toLowerCase(), v);
    } else if (key === 'checked' || key === 'selected' || key === 'disabled' || key === 'required' || key === 'open') {
      node.setAttribute(key, '');
      if (key in node) node[key] = true;
    } else if (key === 'value' && 'value' in node) {
      node.value = v;
    } else {
      node.setAttribute(key, v);
    }
  }
}

function appendChildren(node, children) {
  const flat = children.flat(Infinity);
  for (const c of flat) {
    if (c === null || c === undefined || c === false) continue;
    node.appendChild(c.nodeType ? c : document.createTextNode(String(c)));
  }
}

function h(tag, attrs, ...children) {
  const node = document.createElement(tag);
  applyAttrs(node, attrs);
  appendChildren(node, children);
  return node;
}

function svgEl(tag, attrs, ...children) {
  const node = document.createElementNS(SVG_NS, tag);
  applyAttrs(node, attrs);
  appendChildren(node, children);
  return node;
}

function icon(name, size) {
  size = size || 16;
  const defs = ICONS[name] || [];
  const node = svgEl('svg', {
    viewBox: '0 0 24 24', width: size, height: size, fill: 'none',
    stroke: 'currentColor', 'stroke-width': 1.8,
    'stroke-linecap': 'round', 'stroke-linejoin': 'round',
    'aria-hidden': 'true', focusable: 'false',
  });
  defs.forEach(([tag2, a]) => node.appendChild(svgEl(tag2, a)));
  return node;
}

function clearNode(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
}

function mountView(viewClass) {
  renderToken += 1;
  const myToken = renderToken;
  const root = document.getElementById('viewRoot');
  clearNode(root);
  root.className = 'main-inner view-' + viewClass;
  return { root, isStale: () => myToken !== renderToken };
}

function focusMainAfterRender() {
  const main = document.getElementById('mainContent');
  if (!main) return;
  main.scrollTop = 0;
  try { main.focus({ preventScroll: true }); } catch (_) { main.focus(); }
}

/* ---------- 2. 포맷 헬퍼 ---------- */

function fmtWon(n) {
  if (n === null || n === undefined || Number.isNaN(Number(n))) return '-';
  return Math.round(Number(n)).toLocaleString('ko-KR') + '원';
}
function fmtWonSigned(n) {
  if (n === null || n === undefined || Number.isNaN(Number(n))) return '-';
  const v = Math.round(Number(n));
  return (v > 0 ? '+' : '') + v.toLocaleString('ko-KR') + '원';
}
function fmtPct(n) {
  if (n === null || n === undefined || Number.isNaN(Number(n))) return '-';
  return `${n}%`;
}
function fmtMonths(n) {
  if (n === null || n === undefined) return '-';
  return `${n}개월`;
}
function fmtDateTime(iso) {
  if (!iso) return '-';
  try { return new Date(iso).toLocaleString('ko-KR'); } catch (_) { return String(iso); }
}
function timeAgo(iso) {
  if (!iso) return '';
  try {
    const diffMin = Math.floor((Date.now() - new Date(iso).getTime()) / 60000);
    if (diffMin < 1) return '방금';
    if (diffMin < 60) return `${diffMin}분 전`;
    const diffH = Math.floor(diffMin / 60);
    if (diffH < 24) return `${diffH}시간 전`;
    const diffD = Math.floor(diffH / 24);
    if (diffD < 7) return `${diffD}일 전`;
    return new Date(iso).toLocaleDateString('ko-KR');
  } catch (_) { return ''; }
}
function toInt(v) {
  const n = Math.round(Number(v));
  return Number.isFinite(n) ? n : 0;
}
function toFloat(v) {
  const n = Number(v);
  return Number.isFinite(n) ? n : 0;
}
function lsGetBool(key, def) {
  try { const v = localStorage.getItem(key); return v === null ? def : v === '1'; } catch (_) { return def; }
}
function lsSetBool(key, val) {
  try { localStorage.setItem(key, val ? '1' : '0'); } catch (_) { /* noop */ }
}
function lsGetStr(key, def) {
  try { const v = localStorage.getItem(key); return v === null ? def : v; } catch (_) { return def; }
}
function lsSetStr(key, val) {
  try {
    if (val === null || val === undefined) localStorage.removeItem(key);
    else localStorage.setItem(key, val);
  } catch (_) { /* noop */ }
}

/* ---------- 3. API 헬퍼 ---------- */

function extractErrorMessage(data, res) {
  if (data && typeof data === 'object') {
    if (typeof data.detail === 'string') return data.detail;
    if (Array.isArray(data.detail) && data.detail[0] && data.detail[0].msg) return data.detail[0].msg;
    if (typeof data.message === 'string') return data.message;
  }
  return res.statusText || '알 수 없는 오류';
}

async function apiGet(path) {
  try {
    const res = await fetch(API_BASE + path, { headers: { Accept: 'application/json' } });
    let data = null;
    try { data = await res.json(); } catch (_) { /* 본문 없음 */ }
    if (!res.ok) return { ok: false, status: res.status, data, error: extractErrorMessage(data, res) };
    return { ok: true, status: res.status, data };
  } catch (e) {
    return { ok: false, status: 0, data: null, error: String((e && e.message) || e) };
  }
}

async function apiSend(method, path, body) {
  try {
    const res = await fetch(API_BASE + path, {
      method,
      headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    let data = null;
    try { data = await res.json(); } catch (_) { /* 본문 없음 */ }
    if (!res.ok) return { ok: false, status: res.status, data, error: extractErrorMessage(data, res) };
    return { ok: true, status: res.status, data };
  } catch (e) {
    return { ok: false, status: 0, data: null, error: String((e && e.message) || e) };
  }
}

const Api = {
  health: () => apiGet('/health'),
  personas: () => apiGet('/personas'),
  loadPersona: (id) => apiSend('POST', `/session/persona/${encodeURIComponent(id)}`),
  clearSession: () => apiSend('DELETE', '/session'),
  getProfile: () => apiGet('/profile'),
  putProfile: (profile) => apiSend('PUT', '/profile', profile),
  getHome: () => apiGet('/home'),
  getLoanSchedule: (loanId, extra) =>
    apiGet(`/loans/${encodeURIComponent(loanId)}/schedule${extra ? `?extra=${encodeURIComponent(extra)}` : ''}`),
  getScenarios: (horizon) => apiGet(`/scenarios${horizon ? `?horizon=${encodeURIComponent(horizon)}` : ''}`),
  getActions: () => apiGet('/actions'),
  comparePrepare: (intent, params) => apiSend('POST', '/compare/prepare', { intent, params: params || {} }),
  compareRun: (ctx) => apiSend('POST', '/compare/run', ctx),
  productsStats: () => apiGet('/products/stats'),
  getDecisions: (limit) => apiGet(`/decisions${limit ? `?limit=${encodeURIComponent(limit)}` : ''}`),
  getDecision: (id) => apiGet(`/decisions/${encodeURIComponent(id)}`),
  replayDecision: (id) => apiSend('POST', `/decisions/${encodeURIComponent(id)}/replay`),
  chat: (message) => apiSend('POST', '/chat', { message }),
  syntheticCsvUrl: (personaId) => `${API_BASE}/synthetic/${encodeURIComponent(personaId)}/transactions.csv`,
};

/* ---------- 4. 상태 ---------- */

let renderToken = 0;

const state = {
  profile: null,
  home: null,
  health: null,
  decisionsRecent: [],
  currentView: 'home',
  sidebarCollapsed: lsGetBool('donn.sidebarCollapsed', false),
  mobileSidebarOpen: false,
  lastPersonaId: lsGetStr('donn.lastPersonaId', null),
  chat: { messages: [], pending: false },
  compare: { context: null, result: null, step: 1, queuedPrepareParams: null },
  debts: { selectedLoanId: null, editingLoanId: null, schedule: null, pendingFocusLoanId: null },
};

/* ---------- 5. 라우터 ---------- */

function currentRouteFromHash() {
  const r = (location.hash || '#home').replace('#', '');
  return ROUTES.includes(r) ? r : 'home';
}

function navigateTo(view) {
  if (!ROUTES.includes(view)) view = 'home';
  closeMobileSidebar();
  if (currentRouteFromHash() === view) {
    renderCurrentView();
  } else {
    location.hash = '#' + view;
  }
}

function renderCurrentView() {
  state.currentView = currentRouteFromHash();
  updateSidebarActiveState();
  switch (state.currentView) {
    case 'debts': renderDebts(); break;
    case 'compare': renderCompare(); break;
    case 'spending': renderSpending(); break;
    case 'personas': renderPersonas(); break;
    case 'decisions': renderDecisions(); break;
    default: renderHome(); break;
  }
}

/* ---------- 6. 공용 UI 컴포넌트 ---------- */

function noticeBox(message, opts) {
  opts = opts || {};
  const box = h('div', { class: 'notice-box' + (opts.error ? ' error' : '') });
  box.appendChild(h('span', {}, message));
  if (opts.onRetry) {
    box.appendChild(h('button', { type: 'button', class: 'retry-btn', onClick: opts.onRetry }, '다시 시도'));
  }
  return box;
}

function badge(text, cls) {
  return h('span', { class: 'badge' + (cls ? ' ' + cls : '') }, text);
}

function chipButton(chip) {
  return h('button', {
    type: 'button', class: 'chip', 'aria-label': chip.text,
    onClick: () => handleChipClick(chip),
  }, chip.text);
}

function renderChipRow(chips) {
  const row = h('div', { class: 'chip-row' });
  (chips || []).forEach((c) => row.appendChild(chipButton(c)));
  return row;
}

function evidenceRow(evidenceObj) {
  const keys = evidenceObj ? Object.keys(evidenceObj) : [];
  if (!keys.length) return null;
  const row = h('div', { class: 'evidence-row' });
  keys.forEach((k) => row.appendChild(h('span', { class: 'evidence-pill' }, `${k} ${evidenceObj[k]}`)));
  return row;
}

function plainList(items, cls) {
  if (!items || !items.length) return null;
  const ul = h('ul', { class: cls || 'plain-list' });
  items.forEach((s) => ul.appendChild(h('li', {}, s)));
  return ul;
}

function labelWithBadge(text, forId, estimated) {
  const label = h('label', { for: forId }, text);
  if (estimated) label.appendChild(badge('추정', 'badge-estimated estimated-tag'));
  return label;
}

function fieldText(name, label, value) {
  const id = 'f_' + name;
  const input = h('input', { type: 'text', id, name, value: value || '' });
  const wrap = h('div', { class: 'form-field' }, h('label', { for: id }, label), input);
  return { input, wrap };
}
function fieldNumber(name, label, value, opts) {
  opts = opts || {};
  const id = 'f_' + name;
  const input = h('input', {
    type: 'number', id, name,
    value: value === null || value === undefined ? '' : value,
    step: opts.step || 1, min: opts.min,
  });
  const wrap = h('div', { class: 'form-field' }, h('label', { for: id }, label), input);
  return { input, wrap };
}
function fieldDate(name, label, value) {
  const id = 'f_' + name;
  const input = h('input', { type: 'date', id, name, value: value || '' });
  const wrap = h('div', { class: 'form-field' }, h('label', { for: id }, label), input);
  return { input, wrap };
}
function selectField(name, label, labelsMap, value) {
  const id = 'f_' + name;
  const select = h('select', { id, name });
  Object.keys(labelsMap).forEach((key) => select.appendChild(h('option', { value: key, selected: key === value }, labelsMap[key])));
  const wrap = h('div', { class: 'form-field' }, h('label', { for: id }, label), select);
  return { select, wrap };
}

function fieldTextWithBadge(name, label, value, estimated) {
  const id = 'f_' + name;
  const input = h('input', { type: 'text', id, name, value: value || '' });
  const wrap = h('div', { class: 'form-field' }, labelWithBadge(label, id, estimated), input);
  return { input, wrap };
}
function fieldNumberWithBadge(name, label, value, estimated, opts) {
  opts = opts || {};
  const id = 'f_' + name;
  const input = h('input', {
    type: 'number', id, name,
    value: value === null || value === undefined ? '' : value,
    step: opts.step || 1,
  });
  const wrap = h('div', { class: 'form-field' }, labelWithBadge(label, id, estimated), input);
  return { input, wrap };
}
function selectFieldWithBadge(name, label, labelsMap, value, estimated) {
  const id = 'f_' + name;
  const select = h('select', { id, name });
  Object.keys(labelsMap).forEach((key) => select.appendChild(h('option', { value: key, selected: key === value }, labelsMap[key])));
  const wrap = h('div', { class: 'form-field' }, labelWithBadge(label, id, estimated), select);
  return { select, wrap };
}

/* 금액 입력: 화면에는 천 단위 구분자, 상태에는 정수 원 그대로 */
function parseMoney(raw) {
  const cleaned = String(raw === null || raw === undefined ? '' : raw).replace(/[^0-9-]/g, '');
  if (cleaned === '' || cleaned === '-') return 0;
  const n = parseInt(cleaned, 10);
  return Number.isFinite(n) ? n : 0;
}
function formatMoneyInput(raw) {
  const cleaned = String(raw === null || raw === undefined ? '' : raw).replace(/[^0-9-]/g, '');
  if (cleaned === '' || cleaned === '-') return '';
  const n = parseInt(cleaned, 10);
  return Number.isFinite(n) ? n.toLocaleString('ko-KR') : '';
}

function moneyInput(id, name, value) {
  const input = h('input', {
    type: 'text', id, name, inputmode: 'numeric', autocomplete: 'off',
    class: 'money-input',
    value: value === null || value === undefined || value === '' ? '' : formatMoneyInput(value),
  });
  input.dataset.raw = String(parseMoney(value));
  input.addEventListener('focus', () => { input.value = input.value.replace(/,/g, ''); });
  input.addEventListener('input', () => { input.dataset.raw = String(parseMoney(input.value)); });
  input.addEventListener('blur', () => {
    input.value = formatMoneyInput(input.value);
    input.dataset.raw = String(parseMoney(input.value));
  });
  return input;
}

function fieldMoney(name, label, value) {
  const id = 'f_' + name;
  const input = moneyInput(id, name, value);
  const wrap = h('div', { class: 'form-field' },
    h('label', { for: id }, label),
    h('div', { class: 'input-with-unit' }, input, h('span', { class: 'input-unit' }, '원')));
  return { input, wrap };
}
function fieldMoneyWithBadge(name, label, value, estimated) {
  const id = 'f_' + name;
  const input = moneyInput(id, name, value);
  const wrap = h('div', { class: 'form-field' },
    labelWithBadge(label, id, estimated),
    h('div', { class: 'input-with-unit' }, input, h('span', { class: 'input-unit' }, '원')));
  return { input, wrap };
}

function buildInsightCard(card) {
  const wrap = h('div', { class: 'insight-card tone-' + (card.tone || 'neutral') });
  wrap.appendChild(h('div', { class: 'insight-card-title' }, card.title || ''));
  wrap.appendChild(h('div', { class: 'insight-card-body' }, card.body || ''));

  const ev = evidenceRow(card.evidence);
  if (ev) wrap.appendChild(ev);

  const explainBox = card.explain ? h('div', { class: 'explain-text is-hidden' }, card.explain) : null;
  const actionsRow = h('div', { class: 'card-actions-row' });
  if (card.chip) actionsRow.appendChild(chipButton(card.chip));
  if (explainBox) {
    const toggleBtn = h('button', {
      type: 'button', class: 'explain-toggle', 'aria-expanded': 'false',
      onClick: () => {
        const expanded = toggleBtn.getAttribute('aria-expanded') === 'true';
        toggleBtn.setAttribute('aria-expanded', String(!expanded));
        explainBox.classList.toggle('is-hidden', expanded);
      },
    }, '이 분석은 왜 나왔나요?');
    actionsRow.appendChild(toggleBtn);
  }
  if (actionsRow.childNodes.length) wrap.appendChild(actionsRow);
  if (explainBox) wrap.appendChild(explainBox);
  return wrap;
}

function buildActionCard(action) {
  const wrap = h('div', { class: 'action-card' + (action.safe_mode ? ' safe-mode' : '') });
  const head = h('div', { class: 'action-card-head' });
  head.appendChild(h('div', { class: 'action-card-title' }, action.title || ''));
  const badges = h('div', { class: 'action-card-badges' });
  if (action.safe_mode) badges.appendChild(badge('안전 모드', 'badge-safe'));
  if (action.priority !== undefined && action.priority !== null) badges.appendChild(badge(`우선순위 ${action.priority}`, ''));
  head.appendChild(badges);
  wrap.appendChild(head);
  wrap.appendChild(h('div', { class: 'action-card-summary' }, action.summary || ''));

  const ev = evidenceRow(action.numbers);
  if (ev) wrap.appendChild(ev);

  const steps = action.steps && action.steps.length ? h('ol', { class: 'step-list' }) : null;
  if (steps) { action.steps.forEach((s) => steps.appendChild(h('li', {}, s))); wrap.appendChild(steps); }

  const caveats = plainList(action.caveats, 'caveat-list');
  if (caveats) wrap.appendChild(caveats);

  const assumptions = plainList(action.assumptions, 'plain-list');
  if (assumptions) wrap.appendChild(assumptions);

  if (action.chip) {
    const row = h('div', { class: 'card-actions-row' });
    row.appendChild(chipButton(action.chip));
    wrap.appendChild(row);
  }
  return wrap;
}

/* ---------- 7. 화면: 홈 ---------- */

async function renderHome() {
  const { root, isStale } = mountView('home');
  focusMainAfterRender();

  const hero = h('div', { class: 'home-hero' });
  hero.appendChild(h('h1', { class: 'home-title' }, 'DONN'));
  hero.appendChild(h('p', { class: 'home-subtitle' }, '빚의 다음 한 걸음을 숫자로'));
  root.appendChild(hero);

  const cardsWrap = h('div', { class: 'insight-cards-wrap' });
  cardsWrap.appendChild(h('p', { class: 'loading-text' }, '불러오는 중...'));
  root.appendChild(cardsWrap);

  const inputCard = buildChatInputCard();
  root.appendChild(inputCard.node);

  const transcriptWrap = h('div', { class: 'chat-transcript', id: 'chatTranscriptWrap' });
  root.appendChild(transcriptWrap);

  const homeChipsWrap = h('div', { class: 'chip-row', id: 'homeChipsWrap' });
  root.appendChild(homeChipsWrap);

  root.appendChild(buildShortcutRow());

  renderChatTranscript();

  const res = await loadAndSetHome();
  if (isStale()) return;

  clearNode(cardsWrap);
  if (!res.ok) {
    cardsWrap.appendChild(noticeBox('서버에 연결되지 않았습니다. 잠시 후 다시 시도해주세요.', { error: true, onRetry: renderHome }));
    return;
  }
  const payload = res.data || {};
  (payload.cards || []).forEach((c) => cardsWrap.appendChild(buildInsightCard(c)));
  clearNode(homeChipsWrap);
  (payload.chips || []).forEach((c) => homeChipsWrap.appendChild(chipButton(c)));
}

function buildChatInputCard() {
  const input = h('input', {
    type: 'text', id: 'chatInput', class: 'chat-input-field',
    placeholder: '어떤 부채 고민을 도와드릴까요?', 'aria-label': '채팅 메시지 입력', autocomplete: 'off',
  });
  const left = h('div', { class: 'chat-input-row-left' });
  left.appendChild(h('button', {
    type: 'button', class: 'round-btn-outline', 'aria-label': '새 대화 시작', onClick: startNewChat,
  }, icon('plus', 18)));
  left.appendChild(h('span', { class: 'mode-badge' }, '모드: M0 공시 비교'));

  const sendBtn = h('button', {
    type: 'submit', class: 'send-btn', id: 'chatSendBtn', 'aria-label': '메시지 보내기',
  }, icon('send', 18));
  if (state.chat.pending) {
    sendBtn.disabled = true;
    sendBtn.setAttribute('aria-busy', 'true');
  }

  const row = h('div', { class: 'chat-input-row' }, left, sendBtn);
  const form = h('form', { id: 'chatForm', class: 'chat-input-card', onSubmit: onChatSubmit }, input, row);
  return { node: form, input };
}

function onChatSubmit(e) {
  e.preventDefault();
  if (state.chat.pending) return;
  const input = document.getElementById('chatInput');
  if (!input) return;
  const text = input.value;
  input.value = '';
  sendChatMessage(text);
}

function buildShortcutRow() {
  const row = h('div', { class: 'shortcut-row' });
  const items = [
    { view: 'debts', label: '내 부채', iconName: 'wallet' },
    { view: 'compare', label: '공시 비교', iconName: 'bars' },
    { view: 'spending', label: '소비 패턴', iconName: 'receipt' },
    { view: 'personas', label: '페르소나', iconName: 'user' },
  ];
  items.forEach((it) => {
    const btn = h('button', { type: 'button', class: 'shortcut', 'aria-label': it.label, onClick: () => navigateTo(it.view) });
    btn.appendChild(h('span', { class: 'icon-circle-lg' }, icon(it.iconName, 24)));
    btn.appendChild(h('span', {}, it.label));
    row.appendChild(btn);
  });
  return row;
}

/* ---------- 8. 화면: 내 부채 ---------- */

function emptyProfileSkeleton() {
  return {
    id: 'me', display_name: '나', age: null, employment: null,
    monthly_income: 0, fixed_expenses: 0, variable_expenses: 0, emergency_fund: 0,
    credit_band: null, loans: [], flags: [], notes: null,
  };
}

async function renderDebts() {
  const { root, isStale } = mountView('debts');
  focusMainAfterRender();
  root.appendChild(h('h1', { class: 'view-header' }, '내 부채'));
  root.appendChild(h('p', { class: 'loading-text' }, '불러오는 중...'));

  const res = await Api.getProfile();
  if (isStale()) return;
  clearNode(root);
  root.appendChild(h('h1', { class: 'view-header' }, '내 부채'));
  root.appendChild(h('p', { class: 'view-lead' }, '프로필과 대출을 입력하면 상환표와 시나리오를 계산합니다.'));

  if (!res.ok && res.status !== 404) {
    root.appendChild(noticeBox('서버에 연결되지 않았습니다.', { error: true, onRetry: renderDebts }));
    return;
  }

  const hasProfile = !!res.ok;
  state.profile = hasProfile ? res.data : null;
  const profile = hasProfile ? res.data : emptyProfileSkeleton();

  if (!hasProfile) {
    root.appendChild(noticeBox('아직 저장된 프로필이 없습니다. 아래에서 정보를 입력하거나 페르소나를 선택해보세요.'));
  }

  root.appendChild(buildProfileSummaryCard(profile, hasProfile));
  root.appendChild(buildLoansSection(profile, hasProfile));

  const actionsSection = h('div', { id: 'debtsActionsSection' });
  root.appendChild(actionsSection);
  loadDebtsActions(actionsSection);
}

function buildProfileSummaryCard(profile, hasProfile) {
  const card = h('div', { class: 'panel-card' });
  const head = h('div', { class: 'panel-card-head' });
  const headLeft = h('div', {});
  headLeft.appendChild(h('h2', {}, '프로필'));
  headLeft.appendChild(h('p', { class: 'panel-card-sub' }, hasProfile
    ? `월소득 ${fmtWon(profile.monthly_income)} · 고정지출 ${fmtWon(profile.fixed_expenses)} · 변동지출 ${fmtWon(profile.variable_expenses)} · 비상금 ${fmtWon(profile.emergency_fund)}`
    : '아래 정보를 입력하고 저장하면 프로필이 생성됩니다.'));
  head.appendChild(headLeft);
  if (state.home && state.home.capacity) {
    head.appendChild(badge('여력 ' + (CAPACITY_BAND_LABELS[state.home.capacity.band] || state.home.capacity.band), 'badge-accent'));
  }
  card.appendChild(head);

  const form = h('form', { 'aria-label': '프로필 편집' });
  const grid = h('div', { class: 'form-grid' });

  const nameField = fieldText('display_name', '이름(별칭)', profile.display_name || '나');
  const incomeField = fieldMoney('monthly_income', '월소득', profile.monthly_income || 0);
  const fixedField = fieldMoney('fixed_expenses', '고정지출', profile.fixed_expenses || 0);
  const variableField = fieldMoney('variable_expenses', '변동지출', profile.variable_expenses || 0);
  const emergencyField = fieldMoney('emergency_fund', '비상금', profile.emergency_fund || 0);
  const creditField = fieldText('credit_band', '신용 구간', profile.credit_band || '');
  [nameField, incomeField, fixedField, variableField, emergencyField, creditField].forEach((f) => grid.appendChild(f.wrap));
  form.appendChild(grid);

  const flagsFieldset = h('fieldset', { class: 'field-group' });
  flagsFieldset.appendChild(h('legend', {}, '플래그'));
  const flagsGrid = h('div', { class: 'checkbox-grid' });
  const flagChecks = {};
  const activeFlags = new Set(profile.flags || []);
  Object.keys(FLAG_LABELS).forEach((key) => {
    const id = 'flag_' + key;
    const cb = h('input', { type: 'checkbox', id, checked: activeFlags.has(key) });
    flagChecks[key] = cb;
    flagsGrid.appendChild(h('div', { class: 'checkbox-field' }, cb, h('label', { for: id }, FLAG_LABELS[key])));
  });
  flagsFieldset.appendChild(flagsGrid);
  form.appendChild(flagsFieldset);

  const msgSlot = h('div', {});
  const saveBtn = h('button', { type: 'submit', class: 'btn btn-primary' }, '프로필 저장');
  form.appendChild(h('div', { class: 'form-actions' }, saveBtn));
  form.appendChild(msgSlot);

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const base = hasProfile ? { ...profile } : emptyProfileSkeleton();
    base.display_name = nameField.input.value.trim() || '나';
    base.monthly_income = parseMoney(incomeField.input.value);
    base.fixed_expenses = parseMoney(fixedField.input.value);
    base.variable_expenses = parseMoney(variableField.input.value);
    base.emergency_fund = parseMoney(emergencyField.input.value);
    base.credit_band = creditField.input.value.trim() || null;
    base.flags = Object.keys(flagChecks).filter((k) => flagChecks[k].checked);
    base.loans = (hasProfile ? profile.loans : []) || [];
    clearNode(msgSlot);
    saveBtn.disabled = true;
    const r = await saveProfile(base);
    saveBtn.disabled = false;
    if (!r.ok) msgSlot.appendChild(noticeBox('저장하지 못했습니다. ' + (r.error || ''), { error: true }));
    else renderDebts();
  });

  card.appendChild(form);
  return card;
}

async function saveProfile(profileObj) {
  const r = await Api.putProfile(profileObj);
  if (r.ok) {
    state.profile = r.data || profileObj;
    state.debts.selectedLoanId = null;
    state.debts.schedule = null;
    await refreshSidebarData();
  }
  return r;
}

function nextLoanId(loans) {
  let max = 0;
  (loans || []).forEach((l) => {
    const m = /^L(\d+)$/.exec(l.id || '');
    if (m) max = Math.max(max, parseInt(m[1], 10));
  });
  let n = max + 1;
  const ids = new Set((loans || []).map((l) => l.id));
  while (ids.has('L' + n)) n += 1;
  return 'L' + n;
}

function buildLoansSection(profile, hasProfile) {
  const wrap = h('div', {});
  wrap.appendChild(h('h2', { class: 'section-title' }, '대출 목록'));

  const loans = profile.loans || [];
  if (!loans.length) {
    wrap.appendChild(noticeBox('등록된 대출이 없습니다. 아래에서 대출을 추가해보세요.'));
  } else {
    const tableWrap = h('div', { class: 'table-wrap' });
    const table = h('table', { class: 'data-table stackable' });
    table.appendChild(h('thead', {}, h('tr', {},
      h('th', { class: 'text-left' }, '종류'), h('th', {}, '잔액'), h('th', {}, '금리'),
      h('th', {}, '남은 개월'), h('th', {}, '상환방식'), h('th', {}, '월 납입'), h('th', {}, ''),
    )));
    const tbody = h('tbody', { id: 'loansTbody' });
    loans.forEach((loan) => tbody.appendChild(buildLoanRow(loan, profile)));
    table.appendChild(tbody);
    tableWrap.appendChild(table);
    wrap.appendChild(tableWrap);
  }

  const scheduleArea = h('div', { id: 'scheduleArea' });
  wrap.appendChild(scheduleArea);
  wrap.appendChild(buildLoanForm(profile, hasProfile));

  if (state.debts.pendingFocusLoanId) {
    const pendingId = state.debts.pendingFocusLoanId;
    state.debts.pendingFocusLoanId = null;
    if (loans.some((l) => l.id === pendingId)) {
      setTimeout(() => selectLoan(pendingId, profile, scheduleArea), 0);
    }
  }

  return wrap;
}

function buildLoanRow(loan, profile) {
  const tr = h('tr', { class: 'selectable', 'data-loan-id': loan.id, tabindex: '0', role: 'button', 'aria-label': `${LOAN_TYPE_LABELS[loan.loan_type] || loan.loan_type} 상환표 보기` });
  if (state.debts.selectedLoanId === loan.id) tr.classList.add('selected');

  tr.appendChild(h('td', { class: 'text-left', 'data-label': '종류' }, LOAN_TYPE_LABELS[loan.loan_type] || loan.loan_type));
  tr.appendChild(h('td', { 'data-label': '잔액' }, fmtWon(loan.balance)));
  tr.appendChild(h('td', { 'data-label': '금리' }, fmtPct(loan.annual_rate)));
  tr.appendChild(h('td', { 'data-label': '남은 개월' }, fmtMonths(loan.remaining_months)));
  tr.appendChild(h('td', { 'data-label': '상환방식' }, REPAY_METHOD_LABELS[loan.repay_method] || loan.repay_method));
  tr.appendChild(h('td', { class: 'loan-payment-cell', 'data-label': '월 납입' },
    state.debts.selectedLoanId === loan.id && state.debts.schedule ? fmtWon(state.debts.schedule.first_payment) : '-'));

  const editTd = h('td', { class: 'row-actions-cell' });
  const actions = h('div', { class: 'row-actions' });
  actions.appendChild(h('button', {
    type: 'button', class: 'btn btn-secondary btn-sm', 'aria-label': `${loan.name || loan.id} 수정`,
    onClick: (e) => { e.stopPropagation(); startEditLoan(loan); },
  }, '수정'));
  actions.appendChild(h('button', {
    type: 'button', class: 'btn btn-danger btn-sm', 'aria-label': `${loan.name || loan.id} 삭제`,
    onClick: async (e) => {
      e.stopPropagation();
      if (!confirm('이 대출을 삭제할까요?')) return;
      const updated = { ...profile, loans: (profile.loans || []).filter((l) => l.id !== loan.id) };
      await saveProfile(updated);
      renderDebts();
    },
  }, '삭제'));
  editTd.appendChild(actions);
  tr.appendChild(editTd);

  const onSelect = () => selectLoan(loan.id, profile, document.getElementById('scheduleArea'));
  tr.addEventListener('click', onSelect);
  tr.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onSelect(); } });
  return tr;
}

async function selectLoan(loanId, profile, scheduleArea) {
  state.debts.selectedLoanId = loanId;
  document.querySelectorAll('#loansTbody tr').forEach((tr) => tr.classList.toggle('selected', tr.dataset.loanId === loanId));
  if (!scheduleArea) scheduleArea = document.getElementById('scheduleArea');
  if (!scheduleArea) return;

  clearNode(scheduleArea);
  scheduleArea.appendChild(h('p', { class: 'loading-text' }, '상환표 불러오는 중...'));

  const [schedRes, scenRes] = await Promise.all([Api.getLoanSchedule(loanId), Api.getScenarios(60)]);
  clearNode(scheduleArea);

  if (!schedRes.ok) {
    scheduleArea.appendChild(noticeBox('상환표를 불러오지 못했습니다.', { error: true }));
  } else {
    state.debts.schedule = schedRes.data;
    scheduleArea.appendChild(h('h3', { class: 'section-title' }, '상환표 (첫 12개월)'));
    scheduleArea.appendChild(buildScheduleTable(schedRes.data));
    document.querySelectorAll('#loansTbody tr').forEach((tr) => {
      if (tr.dataset.loanId === loanId) {
        const cell = tr.querySelector('.loan-payment-cell');
        if (cell) cell.textContent = fmtWon(schedRes.data.first_payment);
      }
    });
  }

  if (!scenRes.ok) {
    scheduleArea.appendChild(noticeBox('시나리오를 불러오지 못했습니다.', { error: true }));
  } else {
    scheduleArea.appendChild(h('h3', { class: 'section-title' }, '시나리오 요약'));
    scheduleArea.appendChild(buildScenarioTable(scenRes.data));
  }
}

function buildScheduleTable(schedule) {
  const wrap = h('div', {});
  const scroller = h('div', { class: 'table-wrap table-scroll' });
  const table = h('table', { class: 'data-table' });
  table.appendChild(h('thead', {}, h('tr', {},
    h('th', { class: 'text-left' }, '회차'), h('th', {}, '납입액'), h('th', {}, '원금'), h('th', {}, '이자'), h('th', {}, '잔액'),
  )));
  const tbody = h('tbody', {});
  (schedule.rows || []).slice(0, 12).forEach((r) => {
    tbody.appendChild(h('tr', {},
      h('td', { class: 'text-left' }, String(r.month)), h('td', {}, fmtWon(r.payment)),
      h('td', {}, fmtWon(r.principal)), h('td', {}, fmtWon(r.interest)), h('td', {}, fmtWon(r.balance)),
    ));
  });
  table.appendChild(tbody);
  table.appendChild(h('tfoot', {}, h('tr', {},
    h('td', { class: 'text-left' }, '합계'), h('td', {}, fmtWon(schedule.total_payment)),
    h('td', {}, ''), h('td', {}, fmtWon(schedule.total_interest)), h('td', {}, ''),
  )));
  scroller.appendChild(table);
  wrap.appendChild(scroller);
  const assumptions = plainList(schedule.assumptions, 'plain-list');
  if (assumptions) wrap.appendChild(assumptions);
  return wrap;
}

function buildScenarioTable(scenarios) {
  const wrap = h('div', { class: 'table-wrap' });
  const table = h('table', { class: 'data-table' });
  table.appendChild(h('thead', {}, h('tr', {},
    h('th', { class: 'text-left' }, '시나리오'), h('th', {}, '총이자'), h('th', {}, '부채 완료 월'), h('th', {}, '최소 누적 순현금'),
  )));
  const tbody = h('tbody', {});
  (scenarios || []).forEach((s) => {
    tbody.appendChild(h('tr', {},
      h('td', { class: 'text-left' }, s.label || s.scenario),
      h('td', {}, fmtWon(s.total_interest)),
      h('td', {}, s.debt_free_month !== null && s.debt_free_month !== undefined ? fmtMonths(s.debt_free_month) : '-'),
      h('td', { class: s.min_cumulative_net < 0 ? 'value-negative' : '' }, fmtWon(s.min_cumulative_net)),
    ));
  });
  table.appendChild(tbody);
  wrap.appendChild(table);
  return wrap;
}

function startEditLoan(loan) {
  state.debts.editingLoanId = loan.id;
  renderDebts();
}
function cancelEditLoan() {
  state.debts.editingLoanId = null;
  renderDebts();
}

function buildLoanForm(profile, hasProfile) {
  const loans = profile.loans || [];
  const editing = state.debts.editingLoanId ? loans.find((l) => l.id === state.debts.editingLoanId) : null;

  const card = h('div', { class: 'panel-card' });
  card.appendChild(h('h2', {}, editing ? '대출 수정' : '대출 추가'));

  const form = h('form', { 'aria-label': editing ? '대출 수정 폼' : '대출 추가 폼' });
  const grid = h('div', { class: 'form-grid' });

  const typeSel = selectField('loan_type', '종류', LOAN_TYPE_LABELS, editing ? editing.loan_type : 'credit');
  const balanceField = fieldMoney('balance', '잔액', editing ? editing.balance : 0);
  const rateField = fieldNumber('annual_rate', '금리(연 %)', editing ? editing.annual_rate : 0, { step: 0.1 });
  const monthsField = fieldNumber('remaining_months', '남은 개월', editing ? editing.remaining_months : 12);
  const methodSel = selectField('repay_method', '상환방식', REPAY_METHOD_LABELS, editing ? editing.repay_method : 'equal_payment');
  [typeSel, balanceField, rateField, monthsField, methodSel].forEach((f) => grid.appendChild(f.wrap));
  form.appendChild(grid);

  const details = h('details', { class: 'collapsible' });
  details.appendChild(h('summary', {}, '추가 정보'));
  const detailGrid = h('div', { class: 'form-grid' });
  const nameField = fieldText('name', '표시 이름', editing ? editing.name : '');
  const principalField = fieldMoney('principal', '최초 원금', editing ? editing.principal : 0);
  const rateTypeSel = selectField('rate_type', '금리 유형', RATE_TYPE_LABELS, editing ? editing.rate_type : 'fixed');
  const graceField = fieldNumber('grace_months', '거치 개월', editing ? editing.grace_months : 0);
  const prepayRateField = fieldNumber('prepay_fee_rate', '중도상환수수료율(%)', editing ? editing.prepay_fee_rate : 0, { step: 0.1 });
  const prepayUntilField = fieldDate('prepay_fee_until', '수수료 적용 종료일', editing ? editing.prepay_fee_until : '');
  const lenderSel = selectField('lender_group', '금융권역', LENDER_GROUP_LABELS, editing ? editing.lender_group : 'bank');
  [nameField, principalField, rateTypeSel, graceField, prepayRateField, prepayUntilField, lenderSel].forEach((f) => detailGrid.appendChild(f.wrap));
  details.appendChild(detailGrid);
  form.appendChild(details);

  const msgSlot = h('div', {});
  const actions = h('div', { class: 'form-actions' });
  actions.appendChild(h('button', { type: 'submit', class: 'btn btn-primary' }, editing ? '대출 저장' : '대출 추가'));
  if (editing) actions.appendChild(h('button', { type: 'button', class: 'btn btn-secondary', onClick: cancelEditLoan }, '취소'));
  form.appendChild(actions);
  form.appendChild(msgSlot);

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const balanceVal = parseMoney(balanceField.input.value);
    const loanObj = {
      id: editing ? editing.id : nextLoanId(loans),
      name: nameField.input.value.trim() || (LOAN_TYPE_LABELS[typeSel.select.value] || '대출'),
      loan_type: typeSel.select.value,
      principal: parseMoney(principalField.input.value) || balanceVal,
      balance: balanceVal,
      annual_rate: toFloat(rateField.input.value),
      rate_type: rateTypeSel.select.value,
      repay_method: methodSel.select.value,
      remaining_months: toInt(monthsField.input.value),
      grace_months: toInt(graceField.input.value),
      prepay_fee_rate: toFloat(prepayRateField.input.value),
      prepay_fee_until: prepayUntilField.input.value || null,
      lender_group: lenderSel.select.value,
    };
    const nextLoans = editing
      ? loans.map((l) => (l.id === editing.id ? { ...l, ...loanObj } : l))
      : [...loans, loanObj];
    const base = hasProfile ? { ...profile, loans: nextLoans } : { ...emptyProfileSkeleton(), loans: nextLoans };
    clearNode(msgSlot);
    const r = await saveProfile(base);
    if (!r.ok) {
      msgSlot.appendChild(noticeBox('저장하지 못했습니다. ' + (r.error || ''), { error: true }));
    } else {
      state.debts.editingLoanId = null;
      renderDebts();
    }
  });

  card.appendChild(form);
  return card;
}

async function loadDebtsActions(container) {
  container.appendChild(h('h2', { class: 'section-title' }, '행동 제안'));
  const listWrap = h('div', {});
  listWrap.appendChild(h('p', { class: 'loading-text' }, '불러오는 중...'));
  container.appendChild(listWrap);

  const res = await Api.getActions();
  clearNode(listWrap);
  if (!res.ok) {
    if (res.status === 404) listWrap.appendChild(h('p', { class: 'empty-text' }, '프로필을 먼저 입력하면 행동 제안이 나타나요.'));
    else listWrap.appendChild(noticeBox('행동 제안을 불러오지 못했습니다.', { error: true }));
    return;
  }
  const actions = Array.isArray(res.data) ? res.data : [];
  if (!actions.length) {
    listWrap.appendChild(h('p', { class: 'empty-text' }, '현재 표시할 행동 제안이 없습니다.'));
    return;
  }
  actions.forEach((a) => listWrap.appendChild(buildActionCard(a)));
}

/* ---------- 9. 화면: 공시 비교 ---------- */

function defaultCompareContext() {
  return {
    category: 'credit', amount: 10000000, term_months: 36, sort_key: 'total_cost',
    repay_method: 'equal_payment', rate_type: null, credit_band: null,
    lender_groups: ['bank', 'savings_bank'], exclude_companies: [], max_rate: null,
    target_loan_id: null, estimated_fields: [], user_confirmed: false,
  };
}

async function renderCompare() {
  const { root, isStale } = mountView('compare');
  focusMainAfterRender();
  root.appendChild(h('h1', { class: 'view-header' }, '공시 비교'));
  root.appendChild(h('p', { class: 'view-lead' }, '금융감독원 공시 자료를 같은 조건으로 계산해 나란히 보여줍니다. 상품 실명은 표시하지 않습니다.'));

  const body = h('div', { id: 'compareBody' });
  root.appendChild(body);

  if (state.compare.result && state.compare.step === 2) {
    body.appendChild(buildCompareStep2(state.compare.result));
    return;
  }
  if (state.compare.context) {
    body.appendChild(buildCompareStep1(state.compare.context));
    return;
  }

  body.appendChild(h('p', { class: 'loading-text' }, '조건을 준비하는 중...'));
  const params = state.compare.queuedPrepareParams || {};
  state.compare.queuedPrepareParams = null;
  const res = await Api.comparePrepare('compare', params);
  if (isStale()) return;
  clearNode(body);
  if (!res.ok) {
    body.appendChild(noticeBox('조건을 자동으로 준비하지 못했습니다. 아래에서 직접 입력해 비교할 수 있어요.', { error: true }));
    state.compare.context = defaultCompareContext();
  } else {
    const ctx = res.data;
    // 칩·대화에서 넘어온 조건이 이미 추정 목록을 갖고 있으면 유지한다
    // (모든 값을 넘기면 서버 응답의 estimated_fields가 비어 배지가 사라진다).
    if ((!ctx.estimated_fields || !ctx.estimated_fields.length)
        && Array.isArray(params.estimated_fields) && params.estimated_fields.length) {
      ctx.estimated_fields = params.estimated_fields.slice();
    }
    state.compare.context = ctx;
  }
  body.appendChild(buildCompareStep1(state.compare.context));
}

function compareStepIndicator(step) {
  return h('div', { class: 'compare-step-indicator' },
    h('span', { class: 'step' + (step === 1 ? ' current' : '') }, '1단계 조건 확인'),
    h('span', { class: 'step-sep' }, '›'),
    h('span', { class: 'step' + (step === 2 ? ' current' : '') }, '2단계 결과'));
}

function compareSummarySentence(ctx) {
  const parts = [
    CATEGORY_LABELS[ctx.category] || ctx.category,
    fmtWon(ctx.amount),
    fmtMonths(ctx.term_months),
    REPAY_METHOD_LABELS[ctx.repay_method] || ctx.repay_method,
  ];
  return parts.filter(Boolean).join(' · ');
}

function buildCompareStep1(ctx) {
  const wrap = h('div', {});
  wrap.appendChild(compareStepIndicator(1));

  const estimated = new Set(ctx.estimated_fields || []);
  const card = h('div', { class: 'panel-card confirm-card' });
  const head = h('div', { class: 'panel-card-head' });
  const headLeft = h('div', {});
  headLeft.appendChild(h('h2', {}, '이 조건으로 비교할까요?'));
  headLeft.appendChild(h('p', { class: 'panel-card-sub' }, '아래 조건으로 금융감독원 공시 자료를 비교합니다. 값을 바꾸면 결과도 달라집니다.'));
  head.appendChild(headLeft);
  card.appendChild(head);

  const summary = h('div', { class: 'confirm-summary' });
  summary.appendChild(h('span', {}, compareSummarySentence(ctx)));
  if (estimated.size) summary.appendChild(badge(`추정 ${estimated.size}개`, 'badge-estimated'));
  card.appendChild(summary);

  const form = h('form', { 'aria-label': '비교 조건' });
  const grid = h('div', { class: 'form-grid' });

  const categorySel = selectFieldWithBadge('category', '카테고리', CATEGORY_LABELS, ctx.category, estimated.has('category'));
  const amountField = fieldMoneyWithBadge('amount', '금액', ctx.amount, estimated.has('amount'));
  const termField = fieldNumberWithBadge('term_months', '기간(개월)', ctx.term_months, estimated.has('term_months'));
  const methodSel = selectFieldWithBadge('repay_method', '상환방식', REPAY_METHOD_LABELS, ctx.repay_method, estimated.has('repay_method'));
  const rateTypeSel = selectFieldWithBadge('rate_type', '금리 유형', { '': '무관', fixed: '고정', variable: '변동' }, ctx.rate_type || '', estimated.has('rate_type'));
  const creditField = fieldTextWithBadge('credit_band', '신용 구간', ctx.credit_band || '', estimated.has('credit_band'));
  const maxRateField = fieldNumberWithBadge('max_rate', '최대 금리(%, 선택)', ctx.max_rate === null || ctx.max_rate === undefined ? '' : ctx.max_rate, estimated.has('max_rate'), { step: 0.1 });
  const excludeField = fieldTextWithBadge('exclude_companies', '제외할 회사(쉼표로 구분)', (ctx.exclude_companies || []).join(', '), estimated.has('exclude_companies'));
  [categorySel, amountField, termField, methodSel, rateTypeSel, creditField, maxRateField, excludeField].forEach((f) => grid.appendChild(f.wrap));
  form.appendChild(grid);

  const sortFieldset = h('fieldset', { class: 'field-group' });
  sortFieldset.appendChild(labelWithBadgeLegend('정렬 기준', estimated.has('sort_key')));
  const sortRow = h('div', { class: 'radio-row' });
  const sortRadios = {};
  Object.keys(SORT_KEY_LABELS).forEach((key) => {
    const id = 'sort_' + key;
    const radio = h('input', { type: 'radio', name: 'sort_key', id, value: key, checked: ctx.sort_key === key });
    sortRadios[key] = radio;
    sortRow.appendChild(h('div', { class: 'radio-field' }, radio, h('label', { for: id }, SORT_KEY_LABELS[key])));
  });
  sortFieldset.appendChild(sortRow);
  form.appendChild(sortFieldset);

  const lenderFieldset = h('fieldset', { class: 'field-group' });
  lenderFieldset.appendChild(labelWithBadgeLegend('취급 기관', estimated.has('lender_groups')));
  const lenderGrid = h('div', { class: 'checkbox-grid' });
  const lenderChecks = {};
  const currentLenders = new Set(ctx.lender_groups || []);
  Object.keys(LENDER_GROUP_LABELS).forEach((key) => {
    const id = 'lender_' + key;
    const cb = h('input', { type: 'checkbox', id, checked: currentLenders.has(key) });
    lenderChecks[key] = cb;
    lenderGrid.appendChild(h('div', { class: 'checkbox-field' }, cb, h('label', { for: id }, LENDER_GROUP_LABELS[key])));
  });
  lenderFieldset.appendChild(lenderGrid);
  form.appendChild(lenderFieldset);

  const msgSlot = h('div', {});
  const submitBtn = h('button', { type: 'submit', class: 'btn btn-primary btn-lg' }, '이 조건으로 비교');
  const actionsRow = h('div', { class: 'form-actions' }, submitBtn);
  if (estimated.size) {
    actionsRow.appendChild(h('span', { class: 'form-actions-hint' }, '"추정" 표시는 프로필에서 자동으로 채운 값입니다.'));
  }
  form.appendChild(actionsRow);
  form.appendChild(msgSlot);

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const newCtx = {
      ...ctx,
      category: categorySel.select.value,
      amount: parseMoney(amountField.input.value),
      term_months: toInt(termField.input.value),
      repay_method: methodSel.select.value,
      rate_type: rateTypeSel.select.value || null,
      credit_band: creditField.input.value.trim() || null,
      max_rate: maxRateField.input.value === '' ? null : toFloat(maxRateField.input.value),
      exclude_companies: excludeField.input.value.split(',').map((s) => s.trim()).filter(Boolean),
      sort_key: Object.keys(sortRadios).find((k) => sortRadios[k].checked) || ctx.sort_key,
      lender_groups: Object.keys(lenderChecks).filter((k) => lenderChecks[k].checked),
      user_confirmed: true,
    };
    clearNode(msgSlot);
    submitBtn.disabled = true;
    submitBtn.textContent = '비교하는 중...';
    const res = await Api.compareRun(newCtx);
    submitBtn.disabled = false;
    submitBtn.textContent = '이 조건으로 비교';
    state.compare.context = newCtx;
    if (!res.ok) {
      msgSlot.appendChild(noticeBox('비교를 실행하지 못했습니다. ' + (res.error || ''), { error: true }));
      return;
    }
    state.compare.result = res.data;
    state.compare.step = 2;
    refreshRecentDecisions();
    renderCompare();
  });

  card.appendChild(form);
  wrap.appendChild(card);
  return wrap;
}

function labelWithBadgeLegend(text, estimated) {
  const legend = h('legend', {}, text);
  if (estimated) legend.appendChild(badge('추정', 'badge-estimated estimated-tag'));
  return legend;
}

function rateKindLabel(item) {
  if (item.rate_semantics === 'disclosed_avg_rate') return '전월 평균';
  return RATE_KIND_LABELS[item.rate_kind] || item.rate_kind || '금리';
}

function metricBlock(label, value) {
  return h('div', { class: 'metric-block' }, h('div', { class: 'metric-label' }, label), h('div', { class: 'metric-value' }, value));
}

function buildCompareItemCard(item) {
  const card = h('div', { class: 'compare-item-card' });

  const head = h('div', { class: 'compare-item-head' });
  head.appendChild(h('span', { class: 'compare-item-rank', 'aria-label': `${item.rank}순위` }, String(item.rank)));
  head.appendChild(h('span', { class: 'compare-item-label' }, item.anon_label));
  head.appendChild(h('span', { class: 'compare-item-rate-wrap' },
    h('span', { class: 'compare-item-rate' }, `${item.rate}%`),
    badge(rateKindLabel(item), 'badge-accent')));
  card.appendChild(head);

  const metrics = h('div', { class: 'compare-item-metrics' });
  metrics.appendChild(metricBlock('월 납입', fmtWon(item.monthly_payment)));
  metrics.appendChild(metricBlock('총이자', fmtWon(item.total_interest)));
  const v = item.vs_current_total_interest;
  if (v === null || v === undefined) {
    const block = metricBlock('현재 대비', '-');
    block.querySelector('.metric-value').classList.add('value-neutral');
    metrics.appendChild(block);
  } else {
    const cls = v < 0 ? 'value-positive' : v > 0 ? 'value-negative' : 'value-neutral';
    const block = metricBlock('현재 대비', fmtWonSigned(v));
    block.querySelector('.metric-value').classList.add(cls);
    metrics.appendChild(block);
  }
  card.appendChild(metrics);

  const foot = h('div', { class: 'compare-item-foot' });
  if (item.disclosure_url && /^https?:\/\//i.test(item.disclosure_url)) {
    foot.appendChild(h('a', {
      href: item.disclosure_url, target: '_blank', rel: 'noopener noreferrer', class: 'disclosure-link',
      'aria-label': `${item.anon_label} 공시 열람`,
    }, '금융상품한눈에에서 확인'));
  }
  if (foot.childNodes.length) card.appendChild(foot);

  const notes = plainList(item.notes, 'plain-list');
  if (notes) card.appendChild(notes);
  return card;
}

function buildCompareStep2(result) {
  const wrap = h('div', {});
  wrap.appendChild(compareStepIndicator(2));

  wrap.appendChild(h('p', { class: 'sort-explain' }, result.sort_explain || ''));
  wrap.appendChild(h('p', { class: 'compare-count' },
    `전체 ${result.candidates_total ?? '-'}개 상품 중 상위 ${(result.items || []).length}개`));

  const items = result.items || [];
  if (!items.length) {
    wrap.appendChild(noticeBox('조건에 맞는 공시 상품이 없습니다. 조건을 넓혀서 다시 시도해보세요.'));
  }
  items.forEach((item) => wrap.appendChild(buildCompareItemCard(item)));

  const assumptions = plainList(result.assumptions, 'plain-list');
  if (assumptions) {
    const details = h('details', { class: 'collapsible quiet' });
    details.appendChild(h('summary', {}, '가정 보기'));
    details.appendChild(assumptions);
    wrap.appendChild(details);
  }

  wrap.appendChild(h('p', { class: 'result-meta-line' },
    `decision_id ${result.decision_id} · result_hash ${result.result_hash} · snapshot ${result.snapshot_id} · engine ${result.engine_version} · ${fmtDateTime(result.created_at)}`));

  wrap.appendChild(h('div', { class: 'form-actions' },
    h('button', {
      type: 'button', class: 'btn btn-secondary',
      onClick: () => { state.compare.step = 1; state.compare.result = null; renderCompare(); },
    }, '조건 바꿔서 다시 보기'),
  ));

  return wrap;
}

/* ---------- 10. 화면: 소비 패턴 ---------- */

async function renderSpending() {
  const { root } = mountView('spending');
  focusMainAfterRender();
  root.appendChild(h('h1', { class: 'view-header' }, '소비 패턴'));
  root.appendChild(h('p', { class: 'view-lead' }, '카드·계좌 합성 거래내역으로 데이터 형태를 미리 확인할 수 있어요.'));

  const card = h('div', { class: 'panel-card' });
  const head = h('div', { class: 'panel-card-head' });
  head.appendChild(h('h2', {}, '준비 중'));
  head.appendChild(badge('P5 단계', ''));
  card.appendChild(head);
  card.appendChild(h('p', { class: 'panel-card-sub' },
    '소비 패턴 분석은 아직 준비 중입니다. 아래에서 합성 거래내역을 내려받아 데이터 형태를 확인해보세요.'));

  const personaId = state.lastPersonaId || (state.profile ? state.profile.id : null);
  const actions = h('div', { class: 'form-actions' });
  if (personaId) {
    actions.appendChild(h('a', {
      href: Api.syntheticCsvUrl(personaId), download: `${personaId}_transactions.csv`,
      class: 'btn btn-primary', 'aria-label': '합성 거래내역 CSV 다운로드',
    }, icon('download', 16), ' 합성 거래내역 내려받기'));
    card.appendChild(actions);
  } else {
    card.appendChild(noticeBox('페르소나를 먼저 선택하면 합성 거래내역을 내려받을 수 있어요.'));
    actions.appendChild(h('button', {
      type: 'button', class: 'btn btn-secondary', onClick: () => navigateTo('personas'),
    }, '페르소나 선택하러 가기'));
    card.appendChild(actions);
  }
  root.appendChild(card);
}

/* ---------- 11. 화면: 페르소나 ---------- */

async function loadPersonaAndGoHome(personaId) {
  const res = await Api.loadPersona(personaId);
  if (!res.ok) return { ok: false, error: res.error };
  state.profile = res.data;
  state.lastPersonaId = personaId;
  lsSetStr('donn.lastPersonaId', personaId);
  await refreshSidebarData();
  navigateTo('home');
  return { ok: true };
}

async function clearSessionProfile() {
  const res = await Api.clearSession();
  if (!res.ok) return { ok: false, error: res.error };
  state.profile = null;
  state.lastPersonaId = null;
  lsSetStr('donn.lastPersonaId', null);
  await refreshSidebarData();
  return { ok: true };
}

async function renderPersonas() {
  const { root, isStale } = mountView('personas');
  focusMainAfterRender();
  root.appendChild(h('h1', { class: 'view-header' }, '페르소나 테스트'));
  root.appendChild(h('p', { class: 'view-lead' }, '데모 페르소나를 선택하면 해당 프로필로 화면을 체험할 수 있어요.'));

  const msgSlot = h('div', {});
  root.appendChild(msgSlot);

  const resetRow = h('div', { class: 'form-actions', style: 'margin:0 0 16px;' });
  resetRow.appendChild(h('button', {
    type: 'button', class: 'btn btn-secondary', 'aria-label': '프로필 초기화',
    onClick: async () => {
      clearNode(msgSlot);
      const r = await clearSessionProfile();
      if (r.ok) msgSlot.appendChild(noticeBox('프로필이 초기화되었습니다.'));
      else msgSlot.appendChild(noticeBox('초기화하지 못했습니다. ' + (r.error || ''), { error: true }));
    },
  }, '프로필 초기화'));
  root.appendChild(resetRow);

  const grid = h('div', { class: 'persona-grid' });
  grid.appendChild(h('p', { class: 'loading-text' }, '불러오는 중...'));
  root.appendChild(grid);

  const res = await Api.personas();
  if (isStale()) return;
  clearNode(grid);
  if (!res.ok) {
    grid.appendChild(noticeBox('서버에 연결되지 않았습니다.', { error: true, onRetry: renderPersonas }));
    return;
  }
  const personas = Array.isArray(res.data) ? res.data : [];
  if (!personas.length) {
    grid.appendChild(h('p', { class: 'empty-text' }, '표시할 페르소나가 없습니다.'));
    return;
  }
  personas.forEach((p) => {
    const card = h('div', { class: 'persona-card' });
    card.appendChild(h('div', { class: 'persona-name' }, p.display_name || p.id));
    card.appendChild(h('div', { class: 'persona-oneliner' }, p.one_liner || ''));
    const stats = h('div', { class: 'persona-stats' });
    stats.appendChild(h('span', {}, `대출 ${p.loans_count ?? '-'}건`));
    stats.appendChild(h('span', {}, `총잔액 ${fmtWon(p.total_balance)}`));
    card.appendChild(stats);
    const cardMsg = h('div', {});
    card.appendChild(h('button', {
      type: 'button', class: 'btn btn-primary', 'aria-label': `${p.display_name || p.id} 페르소나로 보기`,
      onClick: async () => {
        clearNode(cardMsg);
        const r = await loadPersonaAndGoHome(p.id);
        if (!r.ok) cardMsg.appendChild(noticeBox('페르소나를 불러오지 못했습니다.', { error: true }));
      },
    }, '이 페르소나로 보기'));
    card.appendChild(cardMsg);
    grid.appendChild(card);
  });
}

/* ---------- 12. 화면: 결정 기록 ---------- */

function buildDecisionRow(rec) {
  const wrap = h('div', { style: 'width:100%;' });
  const row = h('div', { class: 'decision-row' });
  const info = h('div', { class: 'decision-info' });
  info.appendChild(h('span', { class: 'decision-kind' }, DECISION_KIND_LABELS[rec.kind] || rec.kind));
  info.appendChild(h('span', { class: 'decision-sub' },
    `${fmtDateTime(rec.created_at)} · ${String(rec.result_hash || '').slice(0, 10)}`));
  row.appendChild(info);

  const detailPre = h('pre', { class: 'decision-detail-pre is-hidden' });
  const resultSpan = h('span', { class: 'replay-result' });

  const actions = h('div', { class: 'decision-actions' });
  actions.appendChild(h('button', {
    type: 'button', class: 'btn btn-secondary btn-sm', 'aria-label': '결정 재현',
    onClick: async () => {
      resultSpan.textContent = '확인 중...';
      resultSpan.className = 'replay-result';
      const r = await Api.replayDecision(rec.decision_id);
      if (!r.ok) { resultSpan.textContent = '실패'; resultSpan.className = 'replay-result value-negative'; return; }
      const match = r.data && r.data.match;
      resultSpan.textContent = match ? '일치' : '불일치';
      resultSpan.className = 'replay-result ' + (match ? 'value-positive' : 'value-negative');
    },
  }, '재현'));
  actions.appendChild(h('button', {
    type: 'button', class: 'btn btn-secondary btn-sm', 'aria-label': '상세 보기',
    onClick: async () => {
      const isHidden = detailPre.classList.contains('is-hidden');
      if (isHidden && !detailPre.dataset.loaded) {
        detailPre.textContent = '불러오는 중...';
        const r = await Api.getDecision(rec.decision_id);
        detailPre.textContent = r.ok ? JSON.stringify(r.data, null, 2) : '상세 정보를 불러오지 못했습니다.';
        detailPre.dataset.loaded = '1';
      }
      detailPre.classList.toggle('is-hidden');
    },
  }, '상세'));
  actions.appendChild(resultSpan);
  row.appendChild(actions);

  wrap.appendChild(row);
  wrap.appendChild(detailPre);
  return wrap;
}

async function renderDecisions() {
  const { root, isStale } = mountView('decisions');
  focusMainAfterRender();
  root.appendChild(h('h1', { class: 'view-header' }, '결정 기록'));
  root.appendChild(h('p', { class: 'view-lead' }, '같은 조건으로 다시 계산했을 때 결과가 일치하는지 확인할 수 있어요.'));

  const listWrap = h('div', {});
  listWrap.appendChild(h('p', { class: 'loading-text' }, '불러오는 중...'));
  root.appendChild(listWrap);

  const res = await Api.getDecisions(20);
  if (isStale()) return;
  clearNode(listWrap);
  if (!res.ok) {
    listWrap.appendChild(noticeBox('서버에 연결되지 않았습니다.', { error: true, onRetry: renderDecisions }));
    return;
  }
  const items = Array.isArray(res.data) ? res.data : [];
  if (!items.length) {
    listWrap.appendChild(h('p', { class: 'empty-text' }, '아직 결정 기록이 없습니다.'));
    return;
  }
  items.forEach((rec) => listWrap.appendChild(buildDecisionRow(rec)));
}

/* ---------- 13. 채팅 ---------- */

function buildPendingRow() {
  const dots = h('span', { class: 'chat-pending-dots', 'aria-hidden': 'true' }, h('i', {}), h('i', {}), h('i', {}));
  const box = h('div', { class: 'chat-pending', role: 'status', 'aria-live': 'polite' }, dots, h('span', {}, '생각하는 중'));
  return h('div', { class: 'chat-bubble-row from-reply' }, box);
}

function renderChatTranscript(scrollToEnd) {
  const wrap = document.getElementById('chatTranscriptWrap');
  if (!wrap) return;
  clearNode(wrap);
  state.chat.messages.forEach((msg) => {
    if (msg.role === 'user') {
      wrap.appendChild(h('div', { class: 'chat-bubble-row from-user' }, h('div', { class: 'chat-bubble user' }, msg.text)));
    } else if (msg.role === 'reply') {
      const row = h('div', { class: 'chat-bubble-row from-reply' });
      row.appendChild(h('div', { class: 'chat-reply-meta' },
        msg.llm_used ? badge('AI 응답', 'badge-accent') : badge('규칙 기반 응답', 'badge-rule')));
      row.appendChild(h('div', { class: 'chat-bubble reply' }, msg.text));
      if (msg.chips && msg.chips.length) row.appendChild(renderChipRow(msg.chips));
      wrap.appendChild(row);
    } else {
      wrap.appendChild(h('div', { class: 'chat-bubble-row from-reply' }, h('div', { class: 'chat-bubble error' }, msg.text)));
    }
  });
  if (state.chat.pending) wrap.appendChild(buildPendingRow());

  if (scrollToEnd && wrap.lastChild && wrap.lastChild.scrollIntoView) {
    try { wrap.lastChild.scrollIntoView({ block: 'nearest' }); } catch (_) { /* noop */ }
  }
}

function setChatPending(pending) {
  state.chat.pending = pending;
  const btn = document.getElementById('chatSendBtn');
  if (btn) {
    btn.disabled = pending;
    btn.setAttribute('aria-busy', String(pending));
  }
  const input = document.getElementById('chatInput');
  if (input) input.setAttribute('aria-busy', String(pending));
  renderChatTranscript(true);
}

async function sendChatMessage(rawText) {
  const text = (rawText || '').trim();
  if (!text || state.chat.pending) return;
  state.chat.messages.push({ role: 'user', text });
  setChatPending(true);

  const res = await Api.chat(text);

  if (!res.ok) {
    state.chat.messages.push({ role: 'error', text: '응답을 받지 못했습니다. 잠시 후 다시 시도해주세요.' });
    setChatPending(false);
    return;
  }
  const reply = res.data || {};
  state.chat.messages.push({
    role: 'reply', text: reply.reply_text || '', llm_used: !!reply.llm_used, chips: reply.chips || [],
  });
  setChatPending(false);
  refreshRecentDecisions();
  if (reply.action) handleChatAction(reply.action);
}

function startNewChat() {
  state.chat.messages = [];
  state.chat.pending = false;
  navigateTo('home');
  renderChatTranscript();
  setTimeout(() => { const el = document.getElementById('chatInput'); if (el) el.focus(); }, 0);
}

function handleChatAction(action) {
  if (!action || !action.type) return;
  const payload = action.payload || {};
  if (action.type === 'open_view') {
    const view = payload.view || payload.name || payload.target || (typeof payload === 'string' ? payload : null);
    if (view) navigateTo(String(view).replace('#', ''));
  } else if (action.type === 'prepare_compare') {
    const params = payload.params || payload;
    goToCompareWithPrepare(params);
  }
}

async function handleChipClick(chip) {
  if (!chip) return;
  switch (chip.intent) {
    case 'compare':
      goToCompareWithPrepare(chip.params || {});
      break;
    case 'schedule':
    case 'scenario':
      state.debts.pendingFocusLoanId = (chip.params && (chip.params.target_loan_id || chip.params.loan_id)) || null;
      navigateTo('debts');
      break;
    case 'onboarding':
      navigateTo(state.profile ? 'debts' : 'personas');
      break;
    default:
      await sendChatMessage(chip.text);
      break;
  }
}

function goToCompareWithPrepare(params) {
  state.compare.queuedPrepareParams = params || {};
  state.compare.context = null;
  state.compare.result = null;
  state.compare.step = 1;
  navigateTo('compare');
}

/* ---------- 14. 사이드바 & 설정 ---------- */

function updateSidebarActiveState() {
  document.querySelectorAll('#resourceNav a[data-view]').forEach((a) => {
    const active = a.getAttribute('data-view') === state.currentView;
    a.classList.toggle('active', active);
    if (active) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current');
  });
}

function setFooterTexts(home) {
  const disclaimerEl = document.getElementById('disclaimerText');
  const noticeEl = document.getElementById('aiNoticeText');
  const disclaimer = (home && home.disclaimer) || FALLBACK_DISCLAIMER;
  const notice = (home && home.ai_notice) || FALLBACK_AI_NOTICE;
  if (disclaimerEl) {
    disclaimerEl.textContent = disclaimer;
    disclaimerEl.setAttribute('title', disclaimer);
  }
  if (noticeEl) {
    noticeEl.textContent = notice;
    noticeEl.setAttribute('title', notice);
  }
}

function renderPinnedAction() {
  const btn = document.getElementById('pinnedAction');
  if (!btn) return;
  clearNode(btn);
  const top = state.home && state.home.top_action;
  btn.appendChild(h('span', { class: 'label' }, top && top.title ? top.title : '부채를 입력하면 나타나요'));
  btn.onclick = () => {
    if (top && top.chip) handleChipClick(top.chip);
    else navigateTo('home');
  };
}

function renderRecentList() {
  const list = document.getElementById('recentList');
  if (!list) return;
  clearNode(list);
  const items = (state.decisionsRecent || []).slice(0, 5);
  if (!items.length) {
    list.appendChild(h('li', { class: 'sidebar-recent-empty' }, '결정 기록이 없어요'));
    return;
  }
  items.forEach((rec) => {
    const label = DECISION_KIND_LABELS[rec.kind] || rec.kind || '결정';
    const li = h('li', {});
    const btn = h('button', { type: 'button', 'aria-label': `${label} 기록 보기`, onClick: () => navigateTo('decisions') });
    btn.appendChild(h('span', { class: 'recent-label' }, label));
    btn.appendChild(h('span', { class: 'recent-time' }, timeAgo(rec.created_at)));
    li.appendChild(btn);
    list.appendChild(li);
  });
}

async function loadAndSetHome() {
  const res = await Api.getHome();
  state.home = res.ok ? res.data : null;
  renderPinnedAction();
  setFooterTexts(state.home);
  return res;
}
async function refreshRecentDecisions() {
  const res = await Api.getDecisions(5);
  state.decisionsRecent = res.ok && Array.isArray(res.data) ? res.data : [];
  renderRecentList();
}
async function refreshSidebarData() {
  await Promise.all([loadAndSetHome(), refreshRecentDecisions()]);
}

function applySidebarCollapsedState() {
  const sidebar = document.getElementById('sidebar');
  const btn = document.getElementById('sidebarCollapseBtn');
  if (!sidebar) return;
  sidebar.classList.toggle('collapsed', state.sidebarCollapsed);
  if (btn) btn.setAttribute('aria-expanded', String(!state.sidebarCollapsed));
}
function toggleSidebarCollapsed() {
  state.sidebarCollapsed = !state.sidebarCollapsed;
  lsSetBool('donn.sidebarCollapsed', state.sidebarCollapsed);
  applySidebarCollapsedState();
}
function openMobileSidebar() {
  state.mobileSidebarOpen = true;
  const sb = document.getElementById('sidebar');
  const bd = document.getElementById('sidebarBackdrop');
  const hb = document.getElementById('hamburgerBtn');
  if (sb) sb.classList.add('mobile-open');
  if (bd) { bd.classList.add('mobile-open'); bd.classList.remove('is-hidden'); }
  if (hb) hb.setAttribute('aria-expanded', 'true');
}
function closeMobileSidebar() {
  if (!state.mobileSidebarOpen) return;
  state.mobileSidebarOpen = false;
  const sb = document.getElementById('sidebar');
  const bd = document.getElementById('sidebarBackdrop');
  const hb = document.getElementById('hamburgerBtn');
  if (sb) sb.classList.remove('mobile-open');
  if (bd) { bd.classList.remove('mobile-open'); bd.classList.add('is-hidden'); }
  if (hb) hb.setAttribute('aria-expanded', 'false');
}
function toggleMobileSidebar() {
  if (state.mobileSidebarOpen) closeMobileSidebar(); else openMobileSidebar();
}

function onSettingsKeydown(e) {
  if (e.key === 'Escape') closeSettingsModal();
}
function openSettingsModal() {
  const modal = document.getElementById('settingsModal');
  modal.classList.remove('is-hidden');
  modal.setAttribute('aria-hidden', 'false');
  document.addEventListener('keydown', onSettingsKeydown);
  loadSettingsBody();
  const closeBtn = document.getElementById('settingsCloseBtn');
  if (closeBtn) closeBtn.focus();
}
function closeSettingsModal() {
  const modal = document.getElementById('settingsModal');
  modal.classList.add('is-hidden');
  modal.setAttribute('aria-hidden', 'true');
  document.removeEventListener('keydown', onSettingsKeydown);
  const btn = document.getElementById('settingsBtn');
  if (btn) btn.focus();
}
async function loadSettingsBody() {
  const body = document.getElementById('settingsBody');
  clearNode(body);
  body.appendChild(h('p', { class: 'loading-text' }, '불러오는 중...'));
  const res = await Api.health();
  clearNode(body);
  if (!res.ok) {
    body.appendChild(noticeBox('서버에 연결되지 않았습니다.', { error: true, onRetry: loadSettingsBody }));
    return;
  }
  state.health = res.data;
  const rows = [
    ['상태', res.data.status],
    ['엔진 버전', res.data.engine_version],
    ['상품 스냅샷', res.data.snapshot_id || '-'],
    ['LLM 제공자', res.data.llm_provider || '-'],
    ['LLM 사용 가능', res.data.llm_available ? '예' : '아니오'],
  ];
  rows.forEach(([k, v]) => {
    body.appendChild(h('div', { class: 'settings-row' }, h('span', { class: 'k' }, k), h('span', { class: 'v' }, String(v))));
  });

  body.appendChild(h('div', { class: 'settings-note-title' }, '면책 고지'));
  body.appendChild(h('p', { class: 'settings-note' },
    (state.home && state.home.disclaimer) || FALLBACK_DISCLAIMER));
  body.appendChild(h('div', { class: 'settings-note-title' }, 'AI 고지'));
  body.appendChild(h('p', { class: 'settings-note' },
    (state.home && state.home.ai_notice) || FALLBACK_AI_NOTICE));
}

/* ---------- 15. 초기화 & 이벤트 연결 ---------- */

function mountStaticIcons() {
  document.querySelectorAll('[data-icon]').forEach((el) => el.appendChild(icon(el.dataset.icon, 16)));
  const newChatIcon = document.getElementById('newChatIcon');
  if (newChatIcon) newChatIcon.appendChild(icon('plus', 14));
  const hamburgerBtn = document.getElementById('hamburgerBtn');
  if (hamburgerBtn) hamburgerBtn.appendChild(icon('hamburger', 20));
  const collapseBtn = document.getElementById('sidebarCollapseBtn');
  if (collapseBtn) collapseBtn.appendChild(icon('chevronLeft', 18));
  const settingsCloseBtn = document.getElementById('settingsCloseBtn');
  if (settingsCloseBtn) settingsCloseBtn.appendChild(icon('close', 18));
}

function wireGlobalEvents() {
  document.getElementById('hamburgerBtn').addEventListener('click', toggleMobileSidebar);
  document.getElementById('sidebarCollapseBtn').addEventListener('click', toggleSidebarCollapsed);
  document.getElementById('sidebarBackdrop').addEventListener('click', closeMobileSidebar);
  document.getElementById('newChatBtn').addEventListener('click', startNewChat);
  document.getElementById('settingsBtn').addEventListener('click', openSettingsModal);
  document.getElementById('settingsCloseBtn').addEventListener('click', closeSettingsModal);
  document.getElementById('settingsModal').addEventListener('click', (e) => {
    if (e.target.id === 'settingsModal') closeSettingsModal();
  });
  document.getElementById('resourceNav').addEventListener('click', (e) => {
    const a = e.target.closest('a[data-view]');
    if (!a) return;
    e.preventDefault();
    navigateTo(a.getAttribute('data-view'));
  });
  window.addEventListener('hashchange', renderCurrentView);
}

async function initApp() {
  mountStaticIcons();
  wireGlobalEvents();
  applySidebarCollapsedState();
  setFooterTexts(null);

  renderCurrentView();

  const profRes = await Api.getProfile();
  state.profile = profRes.ok ? profRes.data : null;

  await refreshSidebarData();
}

document.addEventListener('DOMContentLoaded', initApp);
