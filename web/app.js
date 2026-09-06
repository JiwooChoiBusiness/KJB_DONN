/* =========================================================
   DONN 부채 전문 AI Agent PoC - app.js
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
/* app/kb/search.py 의 DISCLAIMER 와 동일하게 유지한다. */
const KB_DISCLAIMER = '제도 설명은 참고용이며 최신 내용은 관련 기관 안내를 확인하세요.';

const ROUTES = ['home', 'chat', 'debts', 'compare', 'spending', 'lifecycle', 'personas', 'decisions'];

/* 대화 화면 상단의 모델 라벨. 실제 모델명은 응답마다 배지로 따로 보여준다. */
const CHAT_NEW_TITLE = '새 대화';

/* SPEC 2.11 리소스 패널: kind 별 묶음 제목 */
const RESOURCE_KIND_LABELS = {
  profile: '내 정보', loan: '내 대출', calc: '계산 결과', kb: '제도 안내',
  products: '공시 자료', policy: '기준값', external: '외부 출처',
};
const RESOURCE_KIND_ORDER = ['profile', 'loan', 'calc', 'kb', 'products', 'policy', 'external'];

/* 인라인 액션 카드(open_view)의 화면 이름 버튼 문구 */
const OPEN_VIEW_LABELS = {
  home: '홈으로', debts: '내 부채 열기', compare: '공시 비교 열기',
  spending: '소비 패턴 열기', lifecycle: '생애 흐름 열기',
  decisions: '결정 기록 열기', personas: '계정 선택 열기',
};

/* 노드 카드 상태 표기 */
const STAGE_STATUS_LABELS = { start: '진행 중', done: '완료', fallback: '대체 경로', skip: '건너뜀' };

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
/* PMO 결정 D5: M0 에서 예금·적금은 순위 비교 대상이 아니고 공시 열람만 제공한다.
   서버도 이 카테고리로 들어온 비교 요청을 422 로 돌려준다. */
const COMPARE_VIEW_ONLY_CATEGORIES = ['deposit', 'saving'];
const COMPARE_VIEW_ONLY_NOTICE = '예·적금은 순위 비교 대상이 아닙니다. 공시 열람만 제공합니다.';
/* 정책상품 공시는 정책기관 권역에만 있다. */
const COMPARE_POLICY_CATEGORY = 'policy';
const COMPARE_POLICY_LENDER_GROUP = 'policy';
const SORT_KEY_LABELS = {
  total_cost: '총이자(총비용)', monthly_payment: '월 납입액', rate: '금리',
};
const RATE_KIND_LABELS = {
  base: '기준금리', avg: '평균금리', min: '최저금리', max: '최고금리', preferential: '우대금리',
};
const CAPACITY_BAND_LABELS = { negative: '위험', tight: '빠듯', ok: '양호', comfortable: '여유' };
/* 생애 이벤트 신호(app/models.py LifeEventSignal.kind)의 화면 라벨.
   상품·회사 표현 없이 중립적인 질문으로만 보여준다(SPEC 2.6 IC05). */
const LIFE_EVENT_LABELS = {
  wedding: '결혼 준비', childbirth: '출산·육아', job_change: '소득 변화',
  income_drop: '소득 변화', retirement_near: '은퇴 준비', refinance_window: '대출 조건 재점검',
};
const LIFE_EVENT_QUESTIONS = {
  wedding: '결혼 준비와 관련된 지출 흐름이 보여요. 지금 계획과 맞는지 살펴볼까요?',
  childbirth: '육아와 관련된 지출 흐름이 보여요. 앞으로의 지출 계획을 살펴볼까요?',
  job_change: '소득 흐름이 달라진 신호가 있어요. 상환 계획을 다시 살펴볼까요?',
  income_drop: '소득이 줄어든 신호가 있어요. 상환 계획을 다시 살펴볼까요?',
  retirement_near: '은퇴 시점이 가까워진 신호가 있어요. 남은 상환 계획을 살펴볼까요?',
  refinance_window: '지금 대출 조건을 다시 확인할 시점일 수 있어요. 공시 조건과 비교해볼까요?',
};
const FLAG_LABELS = {
  delinquency_signal: '연체 신호 있음', income_up: '소득 증가', job_changed: '이직/전직',
  self_employed: '자영업자', retirement_near: '은퇴 임박',
};
const DECISION_KIND_LABELS = { compare: '공시 비교', action: '행동 결정', scenario: '시나리오' };

/* 생애주기 층(P7, SPEC 2.7) 화면 라벨.
   Goal.kind, UserProfile.risk_tolerance/income_type 의 서버 값과 1:1로 맞춘다. */
const GOAL_KIND_LABELS = {
  wedding: '결혼', childbirth: '출산·육아', housing: '주거', education: '교육',
  retirement: '은퇴', emergency: '비상자금', other: '기타',
};
const RISK_TOLERANCE_LABELS = { '': '선택 안 함', low: '낮음', mid: '중간', high: '높음' };
const INCOME_TYPE_LABELS = { '': '선택 안 함', regular: '정기', variable: '변동', none: '무소득' };

/* 재무비율 5종. thresholdKey 는 FinancialRatios.thresholds 의 키이며
   비율 이름과 다르다(부채비율·투자자산비율은 단계 기준값이 없다). */
const RATIO_META = [
  { key: 'liquidity_months', label: '유동성 비율', unit: 'months', thresholdKey: 'min_liquidity_months', dir: 'min' },
  { key: 'saving_rate', label: '저축률', unit: 'ratio', thresholdKey: 'min_saving_rate', dir: 'min' },
  { key: 'debt_ratio', label: '부채비율', unit: 'ratio', thresholdKey: null, dir: null },
  { key: 'debt_service_ratio', label: '원리금상환비율', unit: 'ratio', thresholdKey: 'max_debt_service_ratio', dir: 'max' },
  { key: 'investment_ratio', label: '투자자산비율', unit: 'ratio', thresholdKey: null, dir: null },
];

/* 순자산 곡선: 기준선만 진한 실선, 낙관·비관은 연한 파선으로 둔다. */
const LC_SCENARIO_STYLE = {
  '기준': { color: '#4F46E5', dash: null, width: 2.2 },
  '낙관': { color: '#818CF8', dash: '7 4', width: 1.7 },
  '비관': { color: '#A5B4FC', dash: '2 4', width: 1.7 },
};
/* 그리는 순서(기준선이 맨 위)와 읽는 순서(기준 먼저)를 따로 둔다. */
const LC_SCENARIO_ORDER = ['낙관', '비관', '기준'];
const LC_LEGEND_ORDER = ['기준', '낙관', '비관'];
const LC_DEBT_COLOR = '#9CA3AF';

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
  upload: [['path', { d: 'M12 20V9' }], ['polyline', { points: '7 13 12 8 17 13' }], ['path', { d: 'M4 4h16' }]],
  lifeline: [['polyline', { points: '4 4 4 20 20 20' }], ['polyline', { points: '7 16 11 11 14 14 19 7' }]],
  check: [['polyline', { points: '4 12.5 9.5 18 20 6.5' }]],
  alert: [['path', { d: 'M12 4.5 21 19.5H3z' }], ['line', { x1: 12, y1: 10, x2: 12, y2: 14 }], ['line', { x1: 12, y1: 16.6, x2: 12, y2: 16.7 }]],
  minus: [['line', { x1: 6, y1: 12, x2: 18, y2: 12 }]],
  external: [['path', { d: 'M14 4h6v6' }], ['line', { x1: 20, y1: 4, x2: 11, y2: 13 }], ['path', { d: 'M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5' }]],
  chevronRight: [['polyline', { points: '9 6 15 12 9 18' }]],
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

/* DONN 마크: 인디고 라운드 스퀘어(반경 7/32 = 21.9%) 안에 흰 막대 3개.
   막대의 왼쪽 끝은 각지게 붙여 D의 기둥을 만들고, 오른쪽 끝은 반원으로 굴려
   위에서 아래로 짧아진다(부채가 줄어드는 계단). 16px에서도 뭉개지지 않도록
   좌표를 32단위 격자의 0.5 배수에만 둔다. index.html 의 favicon 데이터 URI와 같은 도형. */
const DONN_LOGO_BARS = [
  'M8 3.5H20.5a3.5 3.5 0 0 1 0 7H8Z',
  'M8 12.5H18a3.5 3.5 0 0 1 0 7H8Z',
  'M8 21.5H15.5a3.5 3.5 0 0 1 0 7H8Z',
];

function donnLogo(size) {
  size = size || 28;
  const node = svgEl('svg', {
    viewBox: '0 0 32 32', width: size, height: size,
    'aria-hidden': 'true', focusable: 'false', class: 'donn-logo',
  });
  node.appendChild(svgEl('rect', { width: 32, height: 32, rx: 7, fill: '#4F46E5' }));
  const bars = svgEl('g', { fill: '#ffffff' });
  DONN_LOGO_BARS.forEach((d) => bars.appendChild(svgEl('path', { d })));
  node.appendChild(bars);
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
  /* 대화 화면만 본문 대신 스레드가 스크롤한다(입력 카드를 하단에 고정하기 위해서). */
  const main = document.getElementById('mainContent');
  if (main) main.classList.toggle('is-chat', viewClass === 'chat');
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
/* 비율 표기는 소수점 1자리로 통일한다. fmtPct1 은 이미 퍼센트인 수,
   fmtRatioPct1 은 0~1 비율을 받는다. */
function fmtPct1(n) {
  if (n === null || n === undefined || Number.isNaN(Number(n))) return '-';
  return `${Number(n).toFixed(1)}%`;
}
function fmtPctSigned1(n) {
  if (n === null || n === undefined || Number.isNaN(Number(n))) return '-';
  const v = Number(n);
  return `${v > 0 ? '+' : ''}${v.toFixed(1)}%`;
}
function fmtRatioPct1(r) {
  if (r === null || r === undefined || Number.isNaN(Number(r))) return '-';
  return `${(Number(r) * 100).toFixed(1)}%`;
}
function fmtCount(n) {
  if (n === null || n === undefined || Number.isNaN(Number(n))) return '-';
  return `${Math.round(Number(n)).toLocaleString('ko-KR')}건`;
}
/* 축 눈금처럼 자리가 좁은 곳에서만 쓰는 축약 표기(1억 이상은 억, 1만 이상은 만). */
function fmtWonShort(n) {
  if (n === null || n === undefined || Number.isNaN(Number(n))) return '-';
  const v = Math.round(Number(n));
  const sign = v < 0 ? '-' : '';
  const abs = Math.abs(v);
  if (abs >= 100000000) return `${sign}${(abs / 100000000).toFixed(1)}억`;
  if (abs >= 10000) return `${sign}${Math.round(abs / 10000).toLocaleString('ko-KR')}만`;
  return `${sign}${abs.toLocaleString('ko-KR')}`;
}
/* 개월 수는 소수점 1자리. 유동성 비율 표기에 쓴다. */
function fmtMonths1(n) {
  if (n === null || n === undefined || Number.isNaN(Number(n))) return '-';
  return `${Number(n).toFixed(1)}개월`;
}
/* 'YYYY-MM-DD' 목표일까지 남은 달 수. 지났으면 0 이하. */
function monthsUntilDate(iso) {
  if (!iso) return null;
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(iso));
  if (!m) return null;
  const now = new Date();
  let diff = (Number(m[1]) - now.getFullYear()) * 12 + (Number(m[2]) - 1 - now.getMonth());
  if (Number(m[3]) < now.getDate()) diff -= 1;
  return diff;
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
/* 서버가 보낸 수치를 숫자로 바꾼다. null·빈 값·숫자가 아닌 값은 null 로 돌려
   "값이 없다"와 "값이 0이다"를 구분한다. */
function numOrNull(v) {
  if (v === null || v === undefined || v === '') return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
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

/* 서버 오류 원문(Pydantic 의 영어 검증 메시지, fetch 예외 문자열 등)은 화면에 절대
   내보내지 않는다. 상태 코드별 고정 한국어 문장만 보여주고 원문은 console.debug 로만 남긴다. */
const API_ERROR_MESSAGES = {
  badRequest: '입력값을 확인해 주세요.',
  notFound: '데이터가 없습니다.',
  server: '서버 오류가 발생했습니다.',
  network: '서버에 연결되지 않았습니다.',
  unknown: '잠시 후 다시 시도해 주세요.',
};

function statusErrorMessage(status) {
  if (!status) return API_ERROR_MESSAGES.network;
  if (status === 400 || status === 422) return API_ERROR_MESSAGES.badRequest;
  if (status === 404) return API_ERROR_MESSAGES.notFound;
  if (status >= 500) return API_ERROR_MESSAGES.server;
  return API_ERROR_MESSAGES.unknown;
}

/* 응답 본문에서 개발자용 원문을 뽑는다(로그 전용). */
function rawErrorDetail(data, res) {
  if (data && typeof data === 'object') {
    if (typeof data.detail === 'string') return data.detail;
    if (Array.isArray(data.detail)) {
      const msgs = data.detail.map((d) => (d && d.msg) || '').filter(Boolean);
      if (msgs.length) return msgs.join(' / ');
    }
    if (typeof data.message === 'string') return data.message;
  }
  return (res && res.statusText) || '';
}

/* 우리 백엔드가 사람에게 보여주려고 쓴 한국어 안내 문장인지 본다.
   (예: "예·적금은 순위 비교 대상이 아닙니다. 공시 열람만 제공합니다.")
   한글 비중이 낮거나 지나치게 긴 문자열은 내부 오류로 보고 쓰지 않는다. */
function isKoreanNotice(text) {
  if (typeof text !== 'string') return false;
  const s = text.trim();
  if (!s || s.length > 200) return false;
  const hangul = (s.match(/[가-힣]/g) || []).length;
  return hangul >= 2 && hangul / s.length >= 0.3;
}

function toUserErrorMessage(path, res, data) {
  const detail = rawErrorDetail(data, res);
  if (detail) console.debug('[DONN] API 오류', res.status, path, detail);
  /* 422 는 백엔드가 정책 안내(D5 등)를 한국어 문장으로 돌려주는 경우가 있어 그대로 쓴다. */
  if (res.status === 422 && isKoreanNotice(detail)) return detail.trim();
  return statusErrorMessage(res.status);
}

async function apiGet(path) {
  try {
    const res = await fetch(API_BASE + path, { headers: { Accept: 'application/json' } });
    let data = null;
    try { data = await res.json(); } catch (_) { /* 본문 없음 */ }
    if (!res.ok) return { ok: false, status: res.status, data, error: toUserErrorMessage(path, res, data) };
    return { ok: true, status: res.status, data };
  } catch (e) {
    console.debug('[DONN] 네트워크 오류', path, (e && e.message) || e);
    return { ok: false, status: 0, data: null, error: API_ERROR_MESSAGES.network };
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
    if (!res.ok) return { ok: false, status: res.status, data, error: toUserErrorMessage(path, res, data) };
    return { ok: true, status: res.status, data };
  } catch (e) {
    console.debug('[DONN] 네트워크 오류', path, (e && e.message) || e);
    return { ok: false, status: 0, data: null, error: API_ERROR_MESSAGES.network };
  }
}

/* SSE(text/event-stream) 프레임 파서.
   프레임은 "event: 이름" 줄과 하나 이상의 "data: ..." 줄이고 빈 줄로 끝난다.
   네트워크 청크는 프레임 경계와 무관하게 잘려 오므로 버퍼에 모았다가 "\n\n" 로만 자른다.
   onEvent(name, dataObject) 를 호출하고, 파싱 실패한 프레임은 조용히 버린다. */
function parseSseChunk(buffer, onEvent) {
  const frames = buffer.split(/\r?\n\r?\n/);
  const rest = frames.pop();
  frames.forEach((frame) => {
    let name = 'message';
    const dataLines = [];
    frame.split(/\r?\n/).forEach((line) => {
      if (line.startsWith('event:')) name = line.slice(6).trim();
      else if (line.startsWith('data:')) dataLines.push(line.slice(5).replace(/^ /, ''));
    });
    if (!dataLines.length) return;
    let payload = null;
    try { payload = JSON.parse(dataLines.join('\n')); } catch (_) { return; }
    onEvent(name, payload);
  });
  return rest;
}

/* POST /api/chat/stream 을 읽어 stage/reply/error 이벤트를 콜백으로 넘긴다.
   비 2xx, 스트림 미지원, 네트워크 오류면 false 를 돌려주고 호출부가 /api/chat 으로 폴백한다. */
async function streamChatRequest(message, chatId, handlers) {
  let res;
  try {
    res = await fetch(API_BASE + '/chat/stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
      body: JSON.stringify({ message, chat_id: chatId || null }),
    });
  } catch (e) {
    console.debug('[DONN] 스트림 연결 실패', (e && e.message) || e);
    return false;
  }
  if (!res.ok || !res.body || typeof res.body.getReader !== 'function') {
    console.debug('[DONN] 스트림 사용 불가', res.status);
    return false;
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder('utf-8');
  let buffer = '';
  let sawReply = false;
  try {
    for (;;) {
      if (handlers.isStale && handlers.isStale()) {
        try { await reader.cancel(); } catch (_) { /* noop */ }
        return sawReply;
      }
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      buffer = parseSseChunk(buffer, (name, data) => {
        if (name === 'stage') handlers.onStage(data);
        else if (name === 'reply') { sawReply = true; handlers.onReply(data); }
        else if (name === 'error') handlers.onError(data);
      });
    }
    buffer += decoder.decode();
    parseSseChunk(buffer + '\n\n', (name, data) => {
      if (name === 'stage') handlers.onStage(data);
      else if (name === 'reply') { sawReply = true; handlers.onReply(data); }
      else if (name === 'error') handlers.onError(data);
    });
  } catch (e) {
    console.debug('[DONN] 스트림 읽기 중단', (e && e.message) || e);
    return sawReply;
  }
  return sawReply;
}

/* 실패한 API 응답을 화면 문장으로 바꾼다. 앞머리(무엇을 못 했는지)와 원인 문장을 붙인다. */
function apiErrorText(res, prefix) {
  const msg = (res && res.error) || API_ERROR_MESSAGES.unknown;
  return prefix ? `${prefix} ${msg}` : msg;
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
  getLifecycle: () => apiGet('/lifecycle'),
  comparePrepare: (intent, params) => apiSend('POST', '/compare/prepare', { intent, params: params || {} }),
  compareRun: (ctx) => apiSend('POST', '/compare/run', ctx),
  productsStats: () => apiGet('/products/stats'),
  getDecisions: (limit) => apiGet(`/decisions${limit ? `?limit=${encodeURIComponent(limit)}` : ''}`),
  getDecision: (id) => apiGet(`/decisions/${encodeURIComponent(id)}`),
  replayDecision: (id) => apiSend('POST', `/decisions/${encodeURIComponent(id)}/replay`),
  chat: (message, chatId) => apiSend('POST', '/chat', { message, chat_id: chatId || null }),
  /* 대화창 파일 첨부(SPEC 2.12): 원본 파일이 아니라 브라우저가 정규화한 거래 행만 보낸다. */
  chatAttach: (chatId, filename, transactions, months) => apiSend('POST', '/chat/attach', {
    chat_id: chatId || null, filename, months: months || 3, transactions,
  }),
  compareExplain: (decisionId) => apiSend('POST', `/compare/${encodeURIComponent(decisionId)}/explain`, {}),
  getCompareExplain: (decisionId) => apiGet(`/compare/${encodeURIComponent(decisionId)}/explain`),
  actionExplain: (actionId) => apiSend('POST', `/actions/${encodeURIComponent(actionId)}/explain`, {}),
  listChats: () => apiGet('/chats'),
  chatMessages: (chatId) => apiGet(`/chats/${encodeURIComponent(chatId)}/messages`),
  deleteChat: (chatId) => apiSend('DELETE', `/chats/${encodeURIComponent(chatId)}`),
  kbDoc: (slug) => apiGet(`/kb/${encodeURIComponent(slug)}`),
  syntheticCsvUrl: (personaId) => `${API_BASE}/synthetic/${encodeURIComponent(personaId)}/transactions.csv`,
  getSpending: () => apiGet('/spending'),
  analyzeSpending: (transactions, months) => apiSend('POST', '/spending/analyze', { transactions, months }),
  analyzeSpendingSynthetic: (personaId, months) =>
    apiSend('POST', '/spending/analyze-synthetic', { persona_id: personaId || null, months }),
  deleteSpending: () => apiSend('DELETE', '/spending'),
};

/* ---------- 4. 상태 ---------- */

let renderToken = 0;

const state = {
  profile: null,
  home: null,
  health: null,
  recentChats: [],
  currentView: 'home',
  sidebarCollapsed: lsGetBool('donn.sidebarCollapsed', false),
  mobileSidebarOpen: false,
  lastPersonaId: lsGetStr('donn.lastPersonaId', null),
  personaNotice: null,
  kbOpener: null,
  /* 대화: messages 는 화면에 그린 순서 그대로다. resources 는 "가장 최근 응답"의 자료 목록이며
     오른쪽 리소스 패널이 이것을 그린다. streamSeq 는 늦게 도착한 스트림 응답을 버리는 데 쓴다. */
  chat: {
    messages: [], pending: false, chatId: null, title: '',
    resources: [], streamSeq: 0, queuedSend: null, queuedAttach: null, liveRow: null,
  },
  compare: {
    context: null, result: null, step: 1, queuedPrepareParams: null,
    /* 설명 문장(SPEC 2.8): decisionId 를 대조해 늦게 온 응답을 버린다. */
    explain: { decisionId: null, status: 'idle', data: null },
  },
  /* 행동 카드 "AI 설명 보기" 캐시: action_id -> ExplainResult (첫 화면 로드에서는 채우지 않는다) */
  actionExplains: {},
  debts: { selectedLoanId: null, editingLoanId: null, schedule: null, pendingFocusLoanId: null },
  /* 소비 패턴: data 는 서버 응답 {summary, features, cards},
     upload 는 브라우저에서 읽은 파일의 파싱 상태(서버로 보내지 않는다). */
  spending: {
    data: null, loaded: false, upload: null, lastSyntheticMonths: null, busy: false,
    /* 대화창 첨부에서 넘어왔을 때만 true. 소비 패턴 화면이 열 지정 칸으로 시선을 옮긴다. */
    focusMapping: false,
  },
  /* 생애 흐름: data 는 GET /api/lifecycle 응답, goal 편집 상태는 화면에서만 쓴다. */
  lifecycle: { data: null, editingGoalId: null, goalFormOpen: false, hover: null },
  nav: { depth: 0 },
};

/* ---------- 5. 라우터 ---------- */

function currentRouteFromHash() {
  const r = (location.hash || '#home').replace('#', '');
  return ROUTES.includes(r) ? r : 'home';
}

/* 앱이 직접 쌓은 히스토리 항목에만 depth 를 표시한다.
   depth > 0 이면 뒤로 가기가 앱 안의 이전 화면으로 돌아간다는 뜻이다. */
function syncNavDepth() {
  let st = null;
  try { st = history.state; } catch (_) { st = null; }
  if (st && typeof st.donnDepth === 'number') {
    state.nav.depth = st.donnDepth;
    return;
  }
  state.nav.depth = (state.nav.depth || 0) + 1;
  try { history.replaceState({ donnDepth: state.nav.depth }, ''); } catch (_) { /* noop */ }
}

function initNavDepth() {
  let st = null;
  try { st = history.state; } catch (_) { st = null; }
  state.nav.depth = st && typeof st.donnDepth === 'number' ? st.donnDepth : 0;
  try { history.replaceState({ donnDepth: state.nav.depth }, ''); } catch (_) { /* noop */ }
}

function goBack() {
  if (state.nav.depth > 0) {
    history.back();
    return;
  }
  navigateTo('home');
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
    case 'chat': renderChat(); break;
    case 'debts': renderDebts(); break;
    case 'compare': renderCompare(); break;
    case 'spending': renderSpending(); break;
    case 'lifecycle': renderLifecycle(); break;
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

/* 홈이 아닌 모든 화면의 헤더 왼쪽 위에 붙는 "뒤로" 버튼 */
function backButton(onBack) {
  const btn = h('button', {
    type: 'button', class: 'back-btn', 'aria-label': '뒤로 가기',
    onClick: onBack || goBack,
  });
  btn.appendChild(icon('chevronLeft', 16));
  btn.appendChild(h('span', {}, '뒤로'));
  return h('div', { class: 'view-topline' }, btn);
}

function appendViewHeader(root, title, lead, onBack) {
  root.appendChild(backButton(onBack));
  root.appendChild(h('h1', { class: 'view-header' }, title));
  if (lead) root.appendChild(h('p', { class: 'view-lead' }, lead));
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

/* 검증되지 않은 규제·기준 수치는 서버가 문장 안에 "(확인 필요)"로 표시해 보낸다.
   그 표시를 배지로 빼고 어디서 온 값인지("참고 문서 기준")를 함께 적는다. */
const NEEDS_VERIFY_MARK = /\s*\(확인\s*필요\)\s*\.?/;
const VERIFY_SOURCE_NOTE = '참고 문서 기준';

function verifiableItem(text) {
  const raw = String(text || '');
  const li = h('li', {});
  if (!NEEDS_VERIFY_MARK.test(raw)) { li.appendChild(document.createTextNode(raw)); return li; }
  li.appendChild(document.createTextNode(raw.replace(NEEDS_VERIFY_MARK, ' ').trim()));
  li.appendChild(badge('(확인 필요)', 'badge-estimated estimated-tag'));
  li.appendChild(h('span', { class: 'verify-note' }, VERIFY_SOURCE_NOTE));
  return li;
}

function verifiableList(items, cls) {
  if (!items || !items.length) return null;
  const ul = h('ul', { class: cls || 'plain-list' });
  items.forEach((s) => ul.appendChild(verifiableItem(s)));
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
/* opts.disabledKeys 로 고를 수 없는 항목을 표시한다(목록에서 지우지 않고 이유를 함께 보여준다).
   opts.disabledSuffix 는 그 항목 뒤에 괄호로 붙는 짧은 설명이다. */
function selectFieldWithBadge(name, label, labelsMap, value, estimated, opts) {
  opts = opts || {};
  const disabledKeys = new Set(opts.disabledKeys || []);
  const id = 'f_' + name;
  const select = h('select', { id, name });
  Object.keys(labelsMap).forEach((key) => {
    const off = disabledKeys.has(key);
    const text = off && opts.disabledSuffix ? `${labelsMap[key]} (${opts.disabledSuffix})` : labelsMap[key];
    select.appendChild(h('option', { value: key, selected: key === value, disabled: off }, text));
  });
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

/* ---------- 6-2. 설명 문장(SPEC 2.8) 공용 UI ---------- */

/* 설명 상자 머리줄: 누가 쓴 문장인지(AI/규칙)와 모델명·지연을 밝힌다. */
function explainMetaRow(data) {
  const row = h('div', { class: 'explain-meta' });
  if (data && data.source === 'llm') {
    row.appendChild(badge('AI 응답', 'badge-accent'));
    const sec = Number(data.latency_ms || 0) / 1000;
    if (sec >= 0.05) row.appendChild(h('span', { class: 'explain-meta-text' }, `${sec.toFixed(1)}초`));
  } else {
    row.appendChild(badge('규칙 기반 설명', 'badge-rule'));
  }
  return row;
}

/* 설명 블록 옆에는 언제나 AI 고지가 보인다(SPEC 3절). */
function explainNoticeLine(data) {
  const text = (data && data.ai_notice) || (state.home && state.home.ai_notice) || FALLBACK_AI_NOTICE;
  return h('p', { class: 'explain-notice' }, text);
}

function buildExplainBody(data) {
  const box = h('div', { class: 'explain-box' });
  box.appendChild(explainMetaRow(data));
  box.appendChild(h('p', { class: 'explain-summary' }, (data && data.summary) || ''));
  box.appendChild(explainNoticeLine(data));
  return box;
}

/* 행동 카드의 "AI 설명 보기". 클릭했을 때만 POST 하고 결과는 메모리에 캐시한다.
   첫 화면 로드에서는 절대 호출되지 않는다(CLAUDE.md 절대 규칙). */
function attachActionExplain(wrap, actionId) {
  if (!actionId) return;
  const slot = h('div', { class: 'explain-slot is-hidden' });
  const btn = h('button', {
    type: 'button', class: 'explain-toggle', 'aria-expanded': 'false',
    'aria-label': '이 행동 제안의 AI 설명 보기',
  }, 'AI 설명 보기');

  let busy = false;
  btn.addEventListener('click', async () => {
    const expanded = btn.getAttribute('aria-expanded') === 'true';
    if (expanded) {
      btn.setAttribute('aria-expanded', 'false');
      slot.classList.add('is-hidden');
      return;
    }
    btn.setAttribute('aria-expanded', 'true');
    slot.classList.remove('is-hidden');

    const cached = state.actionExplains[actionId];
    if (cached) { clearNode(slot); slot.appendChild(buildExplainBody(cached)); return; }
    if (busy) return;
    busy = true;
    clearNode(slot);
    slot.appendChild(explainPlaceholder());
    const res = await Api.actionExplain(actionId);
    busy = false;
    clearNode(slot);
    if (!res.ok) {
      slot.appendChild(h('p', { class: 'explain-fail' }, '설명을 불러오지 못했어요.'));
      return;
    }
    state.actionExplains[actionId] = res.data;
    slot.appendChild(buildExplainBody(res.data));
  });

  let row = wrap.querySelector(':scope > .card-actions-row');
  if (!row) { row = h('div', { class: 'card-actions-row' }); wrap.appendChild(row); }
  row.appendChild(btn);
  wrap.appendChild(slot);
}

/* 설명을 기다리는 동안의 자리표시(문장 + 얇은 진행 바) */
function explainPlaceholder() {
  const box = h('div', { class: 'explain-box is-pending' });
  box.appendChild(h('p', { class: 'explain-pending-text' }, 'AI가 계산 결과를 읽고 설명을 쓰는 중이에요'));
  box.appendChild(h('div', { class: 'explain-progress', role: 'progressbar', 'aria-label': '설명 작성 중' }, h('i', {})));
  return box;
}

function buildInsightCard(card, actionId) {
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
  /* 홈의 top_action 카드에는 "AI 설명 보기"가 붙는다(클릭했을 때만 LLM 호출). */
  if (card.kind === 'action' && actionId) attachActionExplain(wrap, actionId);
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

  const row = h('div', { class: 'card-actions-row' });
  if (action.chip) row.appendChild(chipButton(action.chip));
  wrap.appendChild(row);
  attachActionExplain(wrap, action.id);
  if (!row.childNodes.length) row.remove();
  return wrap;
}

/* ---------- 7. 화면: 홈 ---------- */

async function renderHome() {
  const { root, isStale } = mountView('home');
  focusMainAfterRender();

  const hero = h('div', { class: 'home-hero' });
  hero.appendChild(h('h1', { class: 'home-title' }, 'DONN'));
  hero.appendChild(h('p', { class: 'home-subtitle' }, '부채 걱정을 덜어드리는 금융 AI Agent'));
  root.appendChild(hero);

  const cardsWrap = h('div', { class: 'insight-cards-wrap' });
  cardsWrap.appendChild(h('p', { class: 'loading-text' }, '불러오는 중...'));
  root.appendChild(cardsWrap);

  /* 홈은 입력만 받고 대화는 #chat 에서 이어간다(첫 화면 LLM 0회 유지). */
  const inputCard = buildChatInputCard();
  root.appendChild(inputCard.node);

  const homeChipsWrap = h('div', { class: 'chip-row', id: 'homeChipsWrap' });
  root.appendChild(homeChipsWrap);

  const res = await loadAndSetHome();
  if (isStale()) return;

  clearNode(cardsWrap);
  if (!res.ok) {
    cardsWrap.appendChild(noticeBox('서버에 연결되지 않았습니다. 잠시 후 다시 시도해주세요.', { error: true, onRetry: renderHome }));
    return;
  }
  const payload = res.data || {};
  const topActionId = (payload.top_action && payload.top_action.id) || null;
  (payload.cards || []).forEach((c) => cardsWrap.appendChild(buildInsightCard(c, topActionId)));
  clearNode(homeChipsWrap);
  (payload.chips || []).forEach((c) => homeChipsWrap.appendChild(chipButton(c)));
}

function buildChatInputCard() {
  const input = h('input', {
    type: 'text', id: 'chatInput', class: 'chat-input-field',
    placeholder: '어떤 부채 고민을 도와드릴까요?', 'aria-label': '채팅 메시지 입력', autocomplete: 'off',
  });
  /* "+" 는 거래내역 파일 첨부다(SPEC 2.12). 새 대화는 사이드바 "새 대화" 버튼에서만 시작한다.
     파일은 브라우저에서만 읽고 서버로 올리지 않는다. */
  const fileInput = h('input', {
    type: 'file', id: 'chatAttachInput', class: 'visually-hidden', accept: SPENDING_FILE_ACCEPT,
    tabindex: '-1',  // 초점은 "+" 버튼이 받는다(보이지 않는 탭 정거장을 만들지 않는다)
  });
  const fileLabel = h('label', { for: 'chatAttachInput', class: 'visually-hidden' },
    '거래내역 파일 선택 (CSV 또는 XLSX)');
  fileInput.addEventListener('change', () => {
    const file = fileInput.files && fileInput.files[0];
    fileInput.value = '';
    if (file) attachTransactionFile(file);
  });

  const left = h('div', { class: 'chat-input-row-left' });
  left.appendChild(h('button', {
    type: 'button', class: 'round-btn-outline', id: 'chatAttachBtn',
    'aria-label': '거래내역 파일 첨부', title: '거래내역 CSV·XLSX 첨부',
    onClick: () => fileInput.click(),
  }, icon('plus', 18)));
  left.appendChild(fileLabel);
  left.appendChild(fileInput);

  const sendBtn = h('button', {
    type: 'submit', class: 'send-btn', id: 'chatSendBtn', 'aria-label': '메시지 보내기',
  }, icon('send', 18), h('span', { class: 'send-spinner', 'aria-hidden': 'true' }));

  const row = h('div', { class: 'chat-input-row' }, left, sendBtn);
  const form = h('form', { id: 'chatForm', class: 'chat-input-card', onSubmit: onChatSubmit }, input, row);
  /* Enter 로 전송. 한글 입력 중(IME 조합)에 눌린 Enter 는 글자를 확정하는 키라 보내지 않는다. */
  input.addEventListener('keydown', (e) => {
    if (e.key !== 'Enter' || e.shiftKey) return;
    if (e.isComposing || e.keyCode === 229) return;
    onChatSubmit(e);
  });
  /* 응답 생성 중이면 입력과 보내기를 잠근다(진행 표시는 버튼 안 스피너). */
  applyChatPendingToInputs(form);
  return { node: form, input };
}

/* 입력 카드가 새로 그려질 때마다 현재 pending 상태를 반영한다. */
function applyChatPendingToInputs(scope) {
  const root = scope || document;
  const pending = !!state.chat.pending;
  const btn = root.querySelector ? root.querySelector('#chatSendBtn') : null;
  if (btn) {
    btn.disabled = pending;
    btn.setAttribute('aria-busy', String(pending));
    btn.classList.toggle('is-busy', pending);
    btn.setAttribute('aria-label', pending ? '응답을 받는 중' : '메시지 보내기');
  }
  const attachBtn = root.querySelector ? root.querySelector('#chatAttachBtn') : null;
  if (attachBtn) attachBtn.disabled = pending;
  const input = root.querySelector ? root.querySelector('#chatInput') : null;
  if (input) {
    input.disabled = pending;
    input.setAttribute('aria-busy', String(pending));
    input.placeholder = pending ? '응답을 받는 중이에요' : '어떤 부채 고민을 도와드릴까요?';
  }
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

/* ---------- 8. 화면: 내 부채 ---------- */

function emptyProfileSkeleton() {
  return {
    id: 'me', display_name: '나', age: null, employment: null,
    monthly_income: 0, fixed_expenses: 0, variable_expenses: 0, emergency_fund: 0,
    credit_band: null, loans: [], flags: [], notes: null,
    assets: null, goals: [], dependents: 0, risk_tolerance: null, income_type: null,
    life_stage_override: null, retirement_age: null, target_retirement_monthly_expense: null,
  };
}

/* 프로필의 자산 트리는 비어 있을 수 있어 화면에서 항상 같은 모양으로 만들어 쓴다. */
function assetsOf(profile) {
  const a = (profile && profile.assets) || {};
  const p = a.pension || {};
  return {
    liquid: a.liquid || 0,
    investment: a.investment || 0,
    real_estate: a.real_estate || 0,
    pension: {
      national_pension_months_paid: p.national_pension_months_paid || 0,
      db_dc_balance: p.db_dc_balance || 0,
      irp_pension_savings_balance: p.irp_pension_savings_balance || 0,
      isa_balance: p.isa_balance || 0,
      expected_national_pension_monthly: p.expected_national_pension_monthly === null
        || p.expected_national_pension_monthly === undefined ? null : p.expected_national_pension_monthly,
    },
  };
}

async function renderDebts() {
  const { root, isStale } = mountView('debts');
  focusMainAfterRender();
  appendViewHeader(root, '내 부채');
  root.appendChild(h('p', { class: 'loading-text' }, '불러오는 중...'));

  const res = await Api.getProfile();
  if (isStale()) return;
  clearNode(root);
  appendViewHeader(root, '내 부채', '프로필과 대출을 입력하면 상환표와 시나리오를 계산합니다.');

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

  const lifeFields = buildProfileLifeFields(profile);
  form.appendChild(lifeFields.wrap);

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
    lifeFields.applyTo(base);
    clearNode(msgSlot);
    saveBtn.disabled = true;
    const r = await saveProfile(base);
    saveBtn.disabled = false;
    if (!r.ok) msgSlot.appendChild(noticeBox(apiErrorText(r, '저장하지 못했습니다.'), { error: true }));
    else renderDebts();
  });

  card.appendChild(form);
  return card;
}

/* 자산·연금·생애 정보(P7): 프로필 폼 안의 접이식 구역. 저장은 같은 PUT /api/profile 로 나간다.
   반환한 applyTo(base) 가 UserProfile 모양(assets.pension 중첩 포함)으로 값을 채운다. */
function buildProfileLifeFields(profile) {
  const a = assetsOf(profile);
  const wrap = h('div', { class: 'life-fields' });

  const link = h('button', {
    type: 'button', class: 'link-btn life-fields-link',
    'aria-label': '생애 흐름 화면 열기',
    onClick: () => navigateTo('lifecycle'),
  }, '생애 흐름 보기');
  wrap.appendChild(link);

  const details = h('details', { class: 'collapsible' });
  details.appendChild(h('summary', {}, '자산·연금·생애 정보'));
  details.appendChild(h('p', { class: 'form-hint' },
    '입력한 자산과 연금은 재무비율과 노후 자금 시나리오 계산에만 쓰이며 외부로 보내지 않습니다.'));

  const assetGrid = h('div', { class: 'form-grid' });
  const liquidF = fieldMoney('assets_liquid', '유동자산', a.liquid);
  const investF = fieldMoney('assets_investment', '투자자산', a.investment);
  const realF = fieldMoney('assets_real_estate', '부동산', a.real_estate);
  const npMonthsF = fieldNumber('np_months', '국민연금 가입 개월', a.pension.national_pension_months_paid, { min: 0 });
  const npMonthlyF = fieldMoney('np_monthly', '예상 국민연금 월액', a.pension.expected_national_pension_monthly);
  const dbdcF = fieldMoney('db_dc', '퇴직연금(DB·DC) 잔액', a.pension.db_dc_balance);
  const irpF = fieldMoney('irp', 'IRP·연금저축 잔액', a.pension.irp_pension_savings_balance);
  const isaF = fieldMoney('isa', 'ISA 잔액', a.pension.isa_balance);
  const dependentsF = fieldNumber('dependents', '부양가족 수', profile.dependents || 0, { min: 0 });
  const riskF = selectField('risk_tolerance', '위험감내도', RISK_TOLERANCE_LABELS, profile.risk_tolerance || '');
  const incomeTypeF = selectField('income_type', '소득 유형', INCOME_TYPE_LABELS, profile.income_type || '');
  const retireAgeF = fieldNumber('retirement_age', '은퇴 예정 나이', profile.retirement_age, { min: 0 });
  const retireExpF = fieldMoney('target_retirement_expense', '은퇴 후 목표 월 생활비', profile.target_retirement_monthly_expense);

  const fields = [liquidF, investF, realF, npMonthsF, npMonthlyF, dbdcF, irpF, isaF,
    dependentsF, riskF, incomeTypeF, retireAgeF, retireExpF];
  fields.forEach((f) => assetGrid.appendChild(f.wrap));
  details.appendChild(assetGrid);
  details.appendChild(h('p', { class: 'form-hint' },
    '예상 국민연금 월액을 비워두면 가입 개월과 월소득으로 추정한 교육용 값을 씁니다.'));
  wrap.appendChild(details);

  /* 빈 칸은 0 이 아니라 "값 없음"으로 보내야 서버가 추정값을 쓴다. */
  const moneyOrNull = (input) => {
    const raw = String(input.value || '').trim();
    return raw === '' ? null : parseMoney(raw);
  };
  const intOrNull = (input) => {
    const raw = String(input.value || '').trim();
    return raw === '' ? null : toInt(raw);
  };

  function applyTo(base) {
    base.assets = {
      liquid: parseMoney(liquidF.input.value),
      investment: parseMoney(investF.input.value),
      real_estate: parseMoney(realF.input.value),
      pension: {
        national_pension_months_paid: toInt(npMonthsF.input.value),
        db_dc_balance: parseMoney(dbdcF.input.value),
        irp_pension_savings_balance: parseMoney(irpF.input.value),
        isa_balance: parseMoney(isaF.input.value),
        expected_national_pension_monthly: moneyOrNull(npMonthlyF.input),
      },
    };
    base.dependents = toInt(dependentsF.input.value);
    base.risk_tolerance = riskF.select.value || null;
    base.income_type = incomeTypeF.select.value || null;
    base.retirement_age = intOrNull(retireAgeF.input);
    base.target_retirement_monthly_expense = moneyOrNull(retireExpF.input);
    base.goals = (profile && profile.goals) || [];
    base.life_stage_override = (profile && profile.life_stage_override) || null;
  }

  return { wrap, applyTo };
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
      msgSlot.appendChild(noticeBox(apiErrorText(r, '저장하지 못했습니다.'), { error: true }));
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
  const atStep2 = !!(state.compare.result && state.compare.step === 2);
  appendViewHeader(
    root, '공시 비교',
    '금융감독원 공시 자료를 같은 조건으로 계산해 나란히 보여줍니다. 상품 실명은 표시하지 않습니다.',
    atStep2 ? backToCompareStep1 : null,
  );

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

/* 2단계에서 1단계로: 조건(context)은 그대로 두고 결과만 지운다. */
function backToCompareStep1() {
  state.compare.step = 1;
  state.compare.result = null;
  renderCompare();
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

  /* D5: 예·적금은 M0 에서 순위 비교 대상이 아니다. 대화나 칩에서 이 카테고리로 넘어오면
     조건 폼 대신 안내만 보여주고, 대출 카테고리로 이어갈 길만 남긴다. */
  if (COMPARE_VIEW_ONLY_CATEGORIES.includes(ctx.category)) {
    wrap.appendChild(noticeBox(COMPARE_VIEW_ONLY_NOTICE));
    wrap.appendChild(h('p', { class: 'field-hint' },
      '예금과 적금은 금융감독원 공시를 그대로 열람하는 화면에서만 다룹니다.'));
    wrap.appendChild(h('div', { class: 'form-actions' },
      h('button', {
        type: 'button', class: 'btn btn-primary',
        onClick: () => {
          state.compare.context = { ...ctx, category: 'credit', estimated_fields: [], user_confirmed: false };
          renderCompare();
        },
      }, '대출 조건으로 비교하기')));
    return wrap;
  }

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

  const categorySel = selectFieldWithBadge('category', '카테고리', CATEGORY_LABELS, ctx.category, estimated.has('category'),
    { disabledKeys: COMPARE_VIEW_ONLY_CATEGORIES, disabledSuffix: '공시 열람만' });
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
  /* 정책상품은 정책기관 공시에만 있다. 취급 기관에서 정책기관이 빠지면 결과가 0건이 되므로
     카테고리가 정책상품이면 정책기관을 자동으로 켜고 이유를 적는다. */
  const policyHint = h('p', { class: 'field-hint is-hidden' },
    '정책상품은 정책기관 공시에만 있어 "정책기관"을 자동으로 포함합니다.');
  lenderFieldset.appendChild(policyHint);
  form.appendChild(lenderFieldset);

  function syncLenderGroupsWithCategory() {
    const isPolicy = categorySel.select.value === COMPARE_POLICY_CATEGORY;
    const policyCb = lenderChecks[COMPARE_POLICY_LENDER_GROUP];
    if (policyCb) {
      if (isPolicy) { policyCb.checked = true; policyCb.disabled = true; } else { policyCb.disabled = false; }
    }
    policyHint.classList.toggle('is-hidden', !isPolicy);
  }
  categorySel.select.addEventListener('change', syncLenderGroupsWithCategory);
  syncLenderGroupsWithCategory();

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
    const category = categorySel.select.value;
    const lenderGroups = Object.keys(lenderChecks).filter((k) => lenderChecks[k].checked);
    /* 체크박스를 못 건드린 경로(브라우저 복원 등)까지 대비해 한 번 더 강제한다. */
    if (category === COMPARE_POLICY_CATEGORY && !lenderGroups.includes(COMPARE_POLICY_LENDER_GROUP)) {
      lenderGroups.push(COMPARE_POLICY_LENDER_GROUP);
    }
    const newCtx = {
      ...ctx,
      category,
      amount: parseMoney(amountField.input.value),
      term_months: toInt(termField.input.value),
      repay_method: methodSel.select.value,
      rate_type: rateTypeSel.select.value || null,
      credit_band: creditField.input.value.trim() || null,
      max_rate: maxRateField.input.value === '' ? null : toFloat(maxRateField.input.value),
      exclude_companies: excludeField.input.value.split(',').map((s) => s.trim()).filter(Boolean),
      sort_key: Object.keys(sortRadios).find((k) => sortRadios[k].checked) || ctx.sort_key,
      lender_groups: lenderGroups,
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
      msgSlot.appendChild(noticeBox(apiErrorText(res, '비교를 실행하지 못했습니다.'), { error: true }));
      return;
    }
    state.compare.result = res.data;
    state.compare.step = 2;
    state.compare.explain = { decisionId: null, status: 'idle', data: null };
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

/* 공시 링크 문구는 링크가 가리키는 기관에 따라 다르다. 정책상품은 금융상품한눈에가 아니라
   서민금융진흥원·주택금융공사 안내로 가고, 공공데이터포털(data.go.kr) 원본 주소처럼
   일반 이용자가 볼 화면이 아닌 곳은 링크를 만들지 않는다. */
const DISCLOSURE_HOST_LABELS = [
  { host: 'finlife.fss.or.kr', label: '금융상품한눈에에서 확인' },
  { host: 'kinfa.or.kr', label: '서민금융진흥원 안내' },
  { host: 'hf.go.kr', label: '주택금융공사 안내' },
];

/* 링크로 만들 수 있으면 문구를, 아니면 null 을 돌려준다(http/https 만 허용). */
function disclosureLinkLabel(url) {
  if (typeof url !== 'string' || !/^https?:\/\//i.test(url.trim())) return null;
  let host;
  try {
    const parsed = new URL(url.trim());
    if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') return null;
    host = parsed.hostname.toLowerCase().replace(/^www\./, '');
  } catch (_) {
    return null;
  }
  const hit = DISCLOSURE_HOST_LABELS.find((e) => host === e.host || host.endsWith('.' + e.host));
  return hit ? hit.label : null;
}

/* 금리가 없거나 0 이하이면 순위 계산의 근거가 없으므로 금액 지표를 그리지 않는다.
   (백엔드가 이런 항목을 걸러내지만 화면에서도 한 번 더 막는다.) */
function hasUsableRate(item) {
  if (!item || item.rate === null || item.rate === undefined || item.rate === '') return false;
  const n = Number(item.rate);
  return Number.isFinite(n) && n > 0;
}

function buildCompareItemCard(item, reason) {
  const card = h('div', { class: 'compare-item-card' });
  const rateOk = hasUsableRate(item);

  const head = h('div', { class: 'compare-item-head' });
  head.appendChild(h('span', { class: 'compare-item-rank', 'aria-label': `${item.rank}순위` }, String(item.rank)));
  head.appendChild(h('span', { class: 'compare-item-label' }, item.anon_label));
  head.appendChild(rateOk
    ? h('span', { class: 'compare-item-rate-wrap' },
      h('span', { class: 'compare-item-rate' }, `${item.rate}%`),
      badge(rateKindLabel(item), 'badge-accent'))
    : h('span', { class: 'compare-item-rate-wrap' },
      h('span', { class: 'compare-item-rate is-missing' }, '금리 미공시')));
  card.appendChild(head);

  if (rateOk) {
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
  } else {
    card.appendChild(h('p', { class: 'compare-item-nometric' },
      '금리가 공시되지 않아 월 납입과 총이자를 계산하지 않았습니다.'));
  }

  /* 상위 3개 항목에는 설명 문장(SPEC 2.8 item_reasons)을 한 줄로 넣는다. */
  if (reason) card.appendChild(h('p', { class: 'compare-item-reason' }, reason));

  const foot = h('div', { class: 'compare-item-foot' });
  const linkLabel = disclosureLinkLabel(item.disclosure_url);
  if (linkLabel) {
    foot.appendChild(h('a', {
      href: item.disclosure_url, target: '_blank', rel: 'noopener noreferrer', class: 'disclosure-link',
      'aria-label': `${item.anon_label} 공시 열람`,
    }, linkLabel));
  } else {
    foot.appendChild(h('span', { class: 'disclosure-none' }, '공시 링크 없음'));
  }
  card.appendChild(foot);

  const notes = plainList(item.notes, 'plain-list');
  if (notes) card.appendChild(notes);
  return card;
}

/* ---- 출처 표기 ----
   공시 월은 assumptions 의 "공시 자료 기준: 2026년 8월" 문장에서 읽고,
   기관 이름은 항목의 disclosure_url 호스트로 정한다(정책상품은 정책기관). */
const DISCLOSURE_SOURCE_NAMES = [
  { host: 'finlife.fss.or.kr', name: '금융감독원 금융상품한눈에' },
  { host: 'kinfa.or.kr', name: '서민금융진흥원' },
  { host: 'hf.go.kr', name: '주택금융공사' },
];

function disclosureMonthText(assumptions) {
  const list = Array.isArray(assumptions) ? assumptions : [];
  for (const a of list) {
    const m = /공시\s*자료\s*기준\s*[:：]\s*(.+?)\s*$/.exec(String(a || ''));
    if (m) return m[1].trim();
  }
  return '';
}

function disclosureSourceNames(items) {
  const found = [];
  (items || []).forEach((it) => {
    const url = String((it && it.disclosure_url) || '').trim();
    if (!/^https?:\/\//i.test(url)) return;
    let host = '';
    try { host = new URL(url).hostname.toLowerCase().replace(/^www\./, ''); } catch (_) { return; }
    const hit = DISCLOSURE_SOURCE_NAMES.find((e) => host === e.host || host.endsWith('.' + e.host));
    if (hit && found.indexOf(hit.name) === -1) found.push(hit.name);
  });
  return found;
}

function buildDisclosureCitation(result) {
  const names = disclosureSourceNames(result.items);
  const fallback = result.context && result.context.category === COMPARE_POLICY_CATEGORY
    ? ['서민금융진흥원', '주택금융공사'] : ['금융감독원 금융상품한눈에'];
  const source = (names.length ? names : fallback).join(' · ');
  const month = disclosureMonthText(result.assumptions);
  const text = month ? `출처: ${source} · ${month} 공시` : `출처: ${source} 공시 자료`;
  return h('p', { class: 'source-citation' }, text);
}

/* "왜 이 순서인가요?" 블록. 결과를 먼저 그린 뒤 채운다(첫 화면 LLM 0회 유지).
   다른 비교로 넘어갔으면 늦게 온 응답은 decision_id 대조로 버린다. */
function fillCompareExplain(slot, result) {
  const decisionId = result.decision_id;
  if (!decisionId) { slot.classList.add('is-hidden'); return; }

  const cached = state.compare.explain;
  if (cached && cached.decisionId === decisionId && cached.status === 'done' && cached.data) {
    renderCompareExplain(slot, result, cached.data);
    return;
  }

  state.compare.explain = { decisionId, status: 'loading', data: null };
  clearNode(slot);
  slot.classList.remove('is-hidden');
  slot.appendChild(h('h3', { class: 'explain-block-title' }, '왜 이 순서인가요?'));
  slot.appendChild(explainPlaceholder());

  Api.compareExplain(decisionId).then((res) => {
    if (state.compare.explain.decisionId !== decisionId) return;  // 다른 비교로 넘어갔다
    if (!document.body.contains(slot)) return;                    // 화면을 떠났다
    if (!res.ok) {
      state.compare.explain = { decisionId, status: 'failed', data: null };
      slot.classList.add('is-hidden');
      clearNode(slot);
      return;
    }
    state.compare.explain = { decisionId, status: 'done', data: res.data };
    renderCompareExplain(slot, result, res.data);
  });
}

function renderCompareExplain(slot, result, data) {
  clearNode(slot);
  slot.classList.remove('is-hidden');
  slot.appendChild(h('h3', { class: 'explain-block-title' }, '왜 이 순서인가요?'));
  slot.appendChild(buildExplainBody(data));

  /* 항목 카드의 한 줄 이유는 결과가 이미 그려진 뒤 채운다. */
  const reasons = (data && data.item_reasons) || {};
  const scope = slot.parentNode || document;
  (result.items || []).forEach((item) => {
    const holder = scope.querySelector(`[data-reason-rank="${item.rank}"]`);
    if (!holder) return;
    const text = reasons[String(item.rank)];
    clearNode(holder);
    if (text) holder.appendChild(h('p', { class: 'compare-item-reason' }, text));
  });
}

function buildCompareStep2(result) {
  const wrap = h('div', {});
  wrap.appendChild(compareStepIndicator(2));

  wrap.appendChild(h('p', { class: 'sort-explain' }, result.sort_explain || ''));

  const explainSlot = h('div', { class: 'explain-block is-hidden' });
  wrap.appendChild(explainSlot);

  wrap.appendChild(h('p', { class: 'compare-count' },
    `전체 ${result.candidates_total ?? '-'}개 상품 중 상위 ${(result.items || []).length}개`));

  const items = result.items || [];
  if (!items.length) {
    wrap.appendChild(noticeBox('조건에 맞는 공시 상품이 없습니다. 조건을 넓혀서 다시 시도해보세요.'));
  }
  items.forEach((item) => {
    const card = buildCompareItemCard(item);
    card.appendChild(h('div', { class: 'reason-slot', 'data-reason-rank': String(item.rank) }));
    wrap.appendChild(card);
  });

  if (items.length) wrap.appendChild(buildDisclosureCitation(result));

  const assumptions = plainList(result.assumptions, 'plain-list');
  if (assumptions) {
    const details = h('details', { class: 'collapsible quiet' });
    details.appendChild(h('summary', {}, '가정 보기'));
    details.appendChild(assumptions);
    wrap.appendChild(details);
  }

  const tech = h('details', { class: 'collapsible quiet' });
  tech.appendChild(h('summary', {}, '기술 정보'));
  tech.appendChild(h('p', { class: 'result-meta-line' },
    `decision_id ${result.decision_id} · result_hash ${result.result_hash} · snapshot ${result.snapshot_id} · engine ${result.engine_version} · ${fmtDateTime(result.created_at)}`));
  wrap.appendChild(tech);

  wrap.appendChild(h('div', { class: 'form-actions' },
    h('button', { type: 'button', class: 'btn btn-secondary', onClick: backToCompareStep1 }, '조건 바꿔서 다시 보기'),
  ));

  /* 결과(숫자)를 다 그린 다음에 설명을 요청한다. */
  setTimeout(() => fillCompareExplain(explainSlot, result), 0);

  return wrap;
}

/* ---------- 10. 화면: 소비 패턴 ---------- */
/* 이 화면은 파일을 서버로 올리지 않는다(SPEC 2.6 D3/D4). 브라우저가 CSV/XLSX 를 읽어
   거래 행으로 정규화한 뒤 그 행만 POST /api/spending/analyze 로 보내고, 서버는 집계된
   summary/features 만 저장한다. 분석은 사용자가 버튼을 눌렀을 때만 실행한다(D8). */

const SHEETJS_URL = 'https://cdnjs.cloudflare.com/ajax/libs/xlsx/0.18.5/xlsx.full.min.js';
const SPENDING_FILE_ACCEPT = '.csv,.xlsx,.xls';
const SPENDING_MAX_BYTES = 8 * 1024 * 1024;
const SPENDING_MAX_ROWS = 10000;
const SPENDING_PREVIEW_ROWS = 5;
const SPENDING_PRIVACY_NOTE =
  '파일은 서버로 보내지 않습니다. 위 표에서 확인한 거래 행만 보내고, 서버는 집계 결과만 저장합니다.';

/* app/core/spending.py 의 급여·이체 키워드와 같은 목록을 쓴다. 입금 행은 이 키워드에
   걸릴 때만 보낸다(그 외 입금은 서버에서 소비로 잘못 잡히므로 보내지 않는다). */
const SALARY_TOKENS = ['급여', '월급', '상여'];
const TRANSFER_TOKENS = ['이체', '송금', '경조사'];

/* 은행·카드사마다 열 이름이 달라 동의어로 자동 매칭한다(사용자가 고칠 수 있다). */
const SPENDING_HEADER_SYNONYMS = {
  date: ['날짜', '거래일', '거래일자', '거래일시', '거래날짜', '승인일', '승인일자', '이용일', '이용일자',
    '매출일자', '결제일', '사용일', 'date', 'trans_date', 'transactiondate'],
  amount: ['금액', '거래금액', '이용금액', '승인금액', '결제금액', '사용금액', '출금', '출금액', '출금금액',
    '지출', '출금액원', 'amount', 'withdrawal', 'debit', 'spend'],
  deposit: ['입금', '입금액', '입금금액', '수입', 'deposit', 'credit'],
  merchant: ['가맹점', '가맹점명', '거래처', '내용', '적요', '내역', '거래내용', '사용처', '상호', '상호명',
    'merchant', 'description', 'store', 'payee'],
  memo: ['메모', '비고', '참고', '메모내용', 'memo', 'note', 'remark'],
  kind: ['kind', '구분', '거래구분', '유형', '거래유형', '결제수단', 'type'],
};
const SPENDING_ROLE_LABELS = {
  date: '날짜', amount: '금액(지출)', deposit: '입금(선택)', merchant: '가맹점', memo: '메모(선택)',
};

/* ---- 10-1. 파일 읽기(브라우저 안에서만) ---- */

/* 한국 은행·카드사 CSV 는 EUC-KR 로 내려오는 경우가 많다. UTF-8 로 먼저 엄격하게
   읽어보고 실패하면 EUC-KR 로 다시 읽는다. */
function decodeTextBytes(buffer) {
  const bytes = new Uint8Array(buffer);
  let text = null;
  try {
    text = new TextDecoder('utf-8', { fatal: true }).decode(bytes);
  } catch (_) {
    try { text = new TextDecoder('euc-kr').decode(bytes); } catch (_2) { text = null; }
  }
  if (text === null) text = new TextDecoder('utf-8').decode(bytes);
  if (text.charCodeAt(0) === 0xFEFF) text = text.slice(1);
  return text;
}

/* 안내 문구가 앞에 붙은 파일도 있으므로 앞쪽 여러 줄을 함께 보고 구분자를 고른다. */
function sniffDelimiter(text) {
  const lines = String(text).split(/\r?\n/).filter((l) => l.trim()).slice(0, 10);
  const candidates = [',', ';', '\t', '|'];
  let best = ',';
  let bestCount = 0;
  candidates.forEach((d) => {
    let count = 0;
    lines.forEach((line) => { count += line.split(d).length - 1; });
    if (count > bestCount) { bestCount = count; best = d; }
  });
  return best;
}

/* 따옴표 안의 구분자·줄바꿈까지 처리하는 최소 CSV 파서(직접 구현). */
function parseCsvText(text, delimiter) {
  const delim = delimiter || ',';
  const rows = [];
  let row = [];
  let field = '';
  let inQuotes = false;
  let touched = false;
  for (let i = 0; i < text.length; i += 1) {
    const ch = text[i];
    if (inQuotes) {
      if (ch === '"') {
        if (text[i + 1] === '"') { field += '"'; i += 1; } else { inQuotes = false; }
      } else {
        field += ch;
      }
      continue;
    }
    if (ch === '"' && field === '') { inQuotes = true; touched = true; continue; }
    if (ch === delim) { row.push(field); field = ''; touched = true; continue; }
    if (ch === '\r') continue;
    if (ch === '\n') { row.push(field); rows.push(row); row = []; field = ''; touched = false; continue; }
    field += ch;
    touched = true;
  }
  if (touched || field !== '' || row.length) { row.push(field); rows.push(row); }
  return rows;
}

function loadSheetJs() {
  if (window.XLSX) return Promise.resolve(window.XLSX);
  if (!loadSheetJs.pending) {
    loadSheetJs.pending = new Promise((resolve, reject) => {
      const script = document.createElement('script');
      script.src = SHEETJS_URL;
      script.async = true;
      script.onload = () => (window.XLSX ? resolve(window.XLSX) : reject(new Error('xlsx-missing')));
      script.onerror = () => reject(new Error('xlsx-load-failed'));
      document.head.appendChild(script);
    }).catch((e) => { loadSheetJs.pending = null; throw e; });
  }
  return loadSheetJs.pending;
}

function readFileAsArrayBuffer(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = () => reject(new Error('read-failed'));
    reader.readAsArrayBuffer(file);
  });
}

/* ---- 10-2. 열 매칭과 값 정규화 ---- */

function normHeaderText(s) {
  return String(s === null || s === undefined ? '' : s)
    .replace(/\s+/g, '')
    .replace(/[()[\]{}._-]/g, '')
    .toLowerCase();
}

function detectColumn(headerCells, role) {
  const syns = SPENDING_HEADER_SYNONYMS[role] || [];
  const norm = (headerCells || []).map(normHeaderText);
  for (let s = 0; s < syns.length; s += 1) {
    const idx = norm.indexOf(normHeaderText(syns[s]));
    if (idx >= 0) return idx;
  }
  for (let s = 0; s < syns.length; s += 1) {
    const needle = normHeaderText(syns[s]);
    if (needle.length < 2) continue;
    for (let i = 0; i < norm.length; i += 1) {
      const cell = norm[i];
      if (!cell || cell.indexOf(needle) < 0) continue;
      if (role === 'amount' && cell.indexOf('입금') >= 0) continue;
      if (role === 'deposit' && cell.indexOf('출금') >= 0) continue;
      return i;
    }
  }
  return -1;
}

/* 조회기간 안내 같은 머리글 앞 줄이 있어도 실제 헤더 줄을 찾아낸다. */
function findHeaderRowIndex(rows) {
  const limit = Math.min(rows.length, 15);
  let bestIdx = -1;
  let bestScore = 0;
  for (let i = 0; i < limit; i += 1) {
    const cells = rows[i] || [];
    if (!cells.some((c) => String(c === null || c === undefined ? '' : c).trim())) continue;
    let score = 0;
    ['date', 'amount', 'deposit', 'merchant'].forEach((role) => { if (detectColumn(cells, role) >= 0) score += 2; });
    ['memo', 'kind'].forEach((role) => { if (detectColumn(cells, role) >= 0) score += 1; });
    if (score > bestScore) { bestScore = score; bestIdx = i; }
  }
  if (bestIdx >= 0) return bestIdx;
  for (let i = 0; i < rows.length; i += 1) {
    if ((rows[i] || []).some((c) => String(c === null || c === undefined ? '' : c).trim())) return i;
  }
  return 0;
}

function pad2(n) { return n < 10 ? `0${n}` : String(n); }

function isoFromParts(y, m, d) {
  const year = Number(y);
  const month = Number(m);
  const day = Number(d);
  if (!Number.isFinite(year) || !Number.isFinite(month) || !Number.isFinite(day)) return null;
  if (year < 1990 || year > 2100 || month < 1 || month > 12 || day < 1 || day > 31) return null;
  return `${year}-${pad2(month)}-${pad2(day)}`;
}

/* 2026-08-01, 2026.8.1, 2026/08/01, 20260801, 26.08.01, "2026-08-01 13:22:11",
   엑셀 날짜 일련번호까지 받아 YYYY-MM-DD 로 만든다. */
function parseDateCell(raw) {
  if (raw === null || raw === undefined) return null;
  if (raw instanceof Date && !Number.isNaN(raw.getTime())) {
    return isoFromParts(raw.getFullYear(), raw.getMonth() + 1, raw.getDate());
  }
  if (typeof raw === 'number') {
    if (raw > 20000 && raw < 80000) {
      const ms = Date.UTC(1899, 11, 30) + Math.round(raw) * 86400000;
      const dt = new Date(ms);
      return isoFromParts(dt.getUTCFullYear(), dt.getUTCMonth() + 1, dt.getUTCDate());
    }
    raw = String(raw);
  }
  const s = String(raw).trim();
  if (!s) return null;
  let m = /(\d{4})\s*[-./년]\s*(\d{1,2})\s*[-./월]\s*(\d{1,2})/.exec(s);
  if (m) return isoFromParts(m[1], m[2], m[3]);
  m = /^(\d{2})\s*[-./]\s*(\d{1,2})\s*[-./]\s*(\d{1,2})/.exec(s);
  if (m) return isoFromParts(2000 + Number(m[1]), m[2], m[3]);
  const digits = s.replace(/[^0-9]/g, '');
  if (digits.length >= 8) return isoFromParts(digits.slice(0, 4), digits.slice(4, 6), digits.slice(6, 8));
  return null;
}

/* "1,234원", "₩1,234", "-1,234", "(1,234)", "1234.00" 을 정수 원으로 만든다. */
function parseAmountCell(raw) {
  if (raw === null || raw === undefined) return null;
  if (typeof raw === 'number') return Number.isFinite(raw) ? Math.round(raw) : null;
  let s = String(raw).trim();
  if (!s) return null;
  let negative = false;
  if (/^\(.*\)$/.test(s)) { negative = true; s = s.slice(1, -1); }
  if (/^\s*-/.test(s) || /-\s*$/.test(s)) negative = true;
  s = s.replace(/[^0-9.]/g, '');
  if (!s || s === '.') return null;
  const n = Number(s);
  if (!Number.isFinite(n)) return null;
  const v = Math.round(n);
  return negative ? -v : v;
}

function containsToken(text, tokens) {
  const t = String(text || '');
  return tokens.some((tok) => t.indexOf(tok) >= 0);
}

function normalizeKindCell(raw, fallback) {
  const t = String(raw === null || raw === undefined ? '' : raw).trim().toLowerCase();
  if (!t) return fallback;
  if (t === 'card' || t.indexOf('카드') >= 0 || t.indexOf('체크') >= 0 || t.indexOf('신용') >= 0) return 'card';
  if (t === 'bank' || t.indexOf('계좌') >= 0 || t.indexOf('은행') >= 0 || t.indexOf('이체') >= 0
      || t.indexOf('입금') >= 0 || t.indexOf('출금') >= 0) return 'bank';
  return fallback;
}

function cellAt(cells, idx) {
  if (idx === null || idx === undefined || idx < 0) return '';
  const v = cells[idx];
  return v === null || v === undefined ? '' : v;
}

/* 매핑 상태(upload.map, upload.signMode)로 데이터 행을 Transaction 목록으로 만든다.
   결과는 화면 미리보기와 전송에 함께 쓴다. */
function normalizeUploadRows(upload) {
  const map = upload.map;
  /* 입금 열이 있거나 금액에 부호가 섞여 있으면 계좌 내역으로 보고 기본 구분을 계좌로 둔다. */
  const defaultKind = (map.deposit >= 0 || upload.signMode === 'negative') ? 'bank' : 'card';
  const rows = [];
  const stats = { read: 0, sent: 0, noDate: 0, noAmount: 0, deposit: 0, capped: 0 };
  (upload.dataRows || []).forEach((cells, idx) => {
    if (!cells || !cells.some((c) => String(c === null || c === undefined ? '' : c).trim())) return;
    stats.read += 1;
    if (rows.length >= SPENDING_MAX_ROWS) { stats.capped += 1; return; }

    const iso = parseDateCell(cellAt(cells, map.date));
    if (!iso) { stats.noDate += 1; return; }

    const rawAmount = parseAmountCell(cellAt(cells, map.amount));
    const rawDeposit = map.deposit >= 0 ? parseAmountCell(cellAt(cells, map.deposit)) : null;
    let isDeposit = false;
    let value = 0;
    if (rawDeposit !== null && Math.abs(rawDeposit) > 0 && (rawAmount === null || Math.abs(rawAmount) === 0)) {
      isDeposit = true;
      value = Math.abs(rawDeposit);
    } else if (rawAmount !== null && Math.abs(rawAmount) !== 0) {
      if (upload.signMode === 'negative') {
        if (rawAmount < 0) value = -rawAmount; else { isDeposit = true; value = rawAmount; }
      } else if (rawAmount > 0) {
        value = rawAmount;
      } else {
        isDeposit = true;
        value = -rawAmount;
      }
    }
    if (!value) { stats.noAmount += 1; return; }

    const merchant = String(cellAt(cells, map.merchant) || '').trim();
    if (isDeposit && !containsToken(merchant, SALARY_TOKENS) && !containsToken(merchant, TRANSFER_TOKENS)) {
      stats.deposit += 1;
      return;
    }
    const memo = String(cellAt(cells, map.memo) || '').trim();
    const kind = isDeposit ? 'bank' : normalizeKindCell(cellAt(cells, map.kind), defaultKind);
    rows.push({
      id: `up-${idx + 1}`, date: iso, amount: value, merchant, category: null, kind, memo,
    });
  });
  stats.sent = rows.length;
  return { rows, stats };
}

/* 금액 열에 음수가 많으면 "음수가 지출"인 계좌 내역으로 보고 기본값을 바꾼다. */
function guessSignMode(dataRows, amountIdx) {
  if (amountIdx < 0) return 'positive';
  let neg = 0;
  let pos = 0;
  dataRows.slice(0, 300).forEach((cells) => {
    const v = parseAmountCell(cellAt(cells, amountIdx));
    if (v === null || v === 0) return;
    if (v < 0) neg += 1; else pos += 1;
  });
  if (neg + pos === 0) return 'positive';
  return neg / (neg + pos) >= 0.3 ? 'negative' : 'positive';
}

function buildUploadState(fileName, matrix) {
  const headerIdx = findHeaderRowIndex(matrix);
  const headers = (matrix[headerIdx] || []).map((c) => String(c === null || c === undefined ? '' : c).trim());
  const dataRows = matrix.slice(headerIdx + 1);
  const map = {
    date: detectColumn(headers, 'date'),
    amount: detectColumn(headers, 'amount'),
    deposit: detectColumn(headers, 'deposit'),
    merchant: detectColumn(headers, 'merchant'),
    memo: detectColumn(headers, 'memo'),
    kind: detectColumn(headers, 'kind'),
  };
  if (map.amount >= 0 && map.amount === map.deposit) map.deposit = -1;
  return {
    fileName, headers, dataRows, map,
    signMode: guessSignMode(dataRows, map.amount),
    months: 3,
    notice: null,
    noticeError: false,
  };
}

/* ---- 10-3. 화면 ---- */

function currentPersonaId() {
  return (state.profile && state.profile.id) || state.lastPersonaId || null;
}

function spendingMonthsSelect(id, value) {
  const sel = h('select', { id, name: id, 'aria-label': '분석 기간(개월)' });
  for (let m = 1; m <= 6; m += 1) {
    sel.appendChild(h('option', { value: String(m), selected: m === (value || 3) }, `${m}개월`));
  }
  return sel;
}

async function renderSpending() {
  const { root, isStale } = mountView('spending');
  focusMainAfterRender();
  appendViewHeader(root, '소비 패턴',
    '거래내역을 브라우저에서 읽어 집계만 서버에 보냅니다. 원본 거래는 저장하지 않습니다.');

  root.appendChild(h('div', { class: 'consent-line', id: 'spendingConsent' }));
  root.appendChild(h('div', { id: 'spendingSource' }, buildSpendingSourcePanel()));
  root.appendChild(h('div', { id: 'spendingResult' }));
  updateSpendingConsent();
  /* 읽어 둔 파일(대화창 첨부에서 넘어온 것 포함)이 있으면 열 지정 패널을 다시 그린다. */
  restoreSpendingMapping();

  if (state.spending.loaded) { renderSpendingResults(); return; }

  const slot = document.getElementById('spendingResult');
  slot.appendChild(h('p', { class: 'loading-text' }, '저장된 분석 결과를 확인하는 중...'));
  const res = await Api.getSpending();
  if (isStale()) return;
  state.spending.loaded = true;
  if (res.ok) {
    state.spending.data = res.data;
  } else if (res.status !== 404) {
    clearNode(slot);
    slot.appendChild(noticeBox('저장된 분석 결과를 불러오지 못했습니다.', { error: true, onRetry: renderSpending }));
    return;
  }
  updateSpendingConsent();
  renderSpendingResults();
}

/* 화면을 다시 그렸을 때 열 지정 패널을 복원한다. 대화창에서 넘어온 경우에는
   날짜 열 선택 칸으로 초점을 옮겨 무엇을 해야 하는지 바로 보이게 한다. */
function restoreSpendingMapping() {
  if (!state.spending.upload) { state.spending.focusMapping = false; return; }
  renderSpendingMapping();
  if (!state.spending.focusMapping) return;
  state.spending.focusMapping = false;
  const target = document.getElementById('spendingCol_date') || document.getElementById('spendingMapping');
  if (!target) return;
  try { target.scrollIntoView({ block: 'center' }); } catch (_) { target.scrollIntoView(); }
  if (target.focus) { try { target.focus({ preventScroll: true }); } catch (_) { target.focus(); } }
}

function updateSpendingConsent() {
  const el = document.getElementById('spendingConsent');
  if (!el) return;
  clearNode(el);
  el.appendChild(h('span', {}, '분석은 아래 버튼을 눌렀을 때만 실행됩니다. 자동으로 실행되지 않습니다.'));
  const at = state.spending.data && state.spending.data.features
    ? state.spending.data.features.spending_consent_at : null;
  if (at) el.appendChild(h('span', { class: 'consent-time' }, `마지막 실행 ${fmtDateTime(at)}`));
}

function buildSpendingSourcePanel() {
  const card = h('div', { class: 'panel-card' });
  const head = h('div', { class: 'panel-card-head' });
  const headLeft = h('div', {});
  headLeft.appendChild(h('h2', {}, '분석 실행'));
  headLeft.appendChild(h('p', { class: 'panel-card-sub' },
    '합성 거래내역으로 바로 보거나, 내 거래내역 파일을 브라우저에서 읽어 분석할 수 있어요.'));
  head.appendChild(headLeft);
  card.appendChild(head);

  card.appendChild(buildSyntheticPath());
  card.appendChild(buildUploadPath());
  return card;
}

function buildSyntheticPath() {
  const wrap = h('div', { class: 'source-path' });
  wrap.appendChild(h('h3', { class: 'source-path-title' }, '합성 거래내역으로 분석'));
  wrap.appendChild(h('p', { class: 'source-path-sub' },
    '데모용으로 만든 카드·계좌 거래내역을 서버가 생성해 그 자리에서 집계합니다.'));

  const personaId = currentPersonaId();
  const msgSlot = h('div', { id: 'spendingSyntheticMsg' });

  if (!personaId) {
    wrap.appendChild(noticeBox('계정을 먼저 선택하면 합성 거래내역으로 분석할 수 있어요.'));
    wrap.appendChild(h('div', { class: 'form-actions' }, h('button', {
      type: 'button', class: 'btn btn-secondary', onClick: () => navigateTo('personas'),
    }, '계정 선택하러 가기')));
    return wrap;
  }

  const monthsSel = spendingMonthsSelect('spendingSyntheticMonths', state.spending.lastSyntheticMonths || 3);
  const monthsField = h('div', { class: 'form-field inline-field' },
    h('label', { for: 'spendingSyntheticMonths' }, '분석 기간'), monthsSel);

  const runBtn = h('button', { type: 'button', class: 'btn btn-primary' }, '합성 거래내역으로 분석');
  runBtn.addEventListener('click', () => {
    runSyntheticAnalysis(toInt(monthsSel.value) || 3, runBtn, msgSlot);
  });

  const csvLink = h('a', {
    href: Api.syntheticCsvUrl(personaId), download: `${personaId}_transactions.csv`,
    class: 'inline-link', 'aria-label': '합성 거래내역 CSV 내려받기',
  }, icon('download', 14), h('span', {}, '합성 CSV 내려받기'));

  wrap.appendChild(h('div', { class: 'source-row' }, monthsField, runBtn, csvLink));
  wrap.appendChild(h('p', { class: 'source-hint' },
    '내려받은 CSV 를 아래 업로드 영역에 올리면 파일로 분석하는 흐름도 그대로 확인할 수 있어요.'));
  wrap.appendChild(msgSlot);
  return wrap;
}

async function runSyntheticAnalysis(months, btn, msgSlot) {
  if (state.spending.busy) return;
  state.spending.busy = true;
  const label = btn ? btn.textContent : '';
  if (btn) { btn.disabled = true; btn.textContent = '분석하는 중...'; }
  if (msgSlot) clearNode(msgSlot);

  const res = await Api.analyzeSpendingSynthetic(currentPersonaId(), months);
  state.spending.busy = false;
  if (btn) { btn.disabled = false; btn.textContent = label; }

  if (!res.ok) {
    if (msgSlot) msgSlot.appendChild(noticeBox(apiErrorText(res, '분석하지 못했습니다.'), { error: true }));
    return;
  }
  state.spending.data = res.data;
  state.spending.loaded = true;
  state.spending.lastSyntheticMonths = months;
  updateSpendingConsent();
  renderSpendingResults();
  await loadAndSetHome();
}

function buildUploadPath() {
  const wrap = h('div', { class: 'source-path' });
  wrap.appendChild(h('h3', { class: 'source-path-title' }, '내 거래내역 파일로 분석'));
  wrap.appendChild(h('p', { class: 'source-path-sub' },
    'CSV 또는 XLSX 파일을 브라우저에서 직접 읽습니다. 파일은 서버로 올라가지 않습니다.'));

  const fileInput = h('input', {
    type: 'file', id: 'spendingFileInput', accept: SPENDING_FILE_ACCEPT, class: 'visually-hidden',
    'aria-label': '거래내역 파일 선택 (CSV 또는 XLSX)',
  });
  fileInput.addEventListener('change', () => {
    const file = fileInput.files && fileInput.files[0];
    if (file) handleSpendingFile(file);
    fileInput.value = '';
  });

  const drop = h('div', {
    class: 'drop-zone', id: 'spendingDrop', role: 'group', 'aria-label': '거래내역 파일 올리기',
  });
  drop.appendChild(h('span', { class: 'drop-icon', 'aria-hidden': 'true' }, icon('upload', 20)));
  drop.appendChild(h('p', { class: 'drop-text' }, '여기에 파일을 끌어다 놓거나 아래 버튼으로 고르세요'));
  drop.appendChild(h('p', { class: 'drop-sub' }, 'CSV, XLSX (최대 8MB)'));
  drop.appendChild(h('button', {
    type: 'button', class: 'btn btn-secondary', onClick: () => fileInput.click(),
  }, '파일 고르기'));
  drop.appendChild(fileInput);

  ['dragenter', 'dragover'].forEach((evt) => {
    drop.addEventListener(evt, (e) => { e.preventDefault(); drop.classList.add('is-over'); });
  });
  ['dragleave', 'dragend'].forEach((evt) => {
    drop.addEventListener(evt, () => drop.classList.remove('is-over'));
  });
  drop.addEventListener('drop', (e) => {
    e.preventDefault();
    drop.classList.remove('is-over');
    const file = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0];
    if (file) handleSpendingFile(file);
  });

  wrap.appendChild(drop);
  wrap.appendChild(h('div', { id: 'spendingMapping' }));
  return wrap;
}

function setUploadNotice(message, isError) {
  if (!state.spending.upload) {
    const slot = document.getElementById('spendingMapping');
    if (slot) {
      clearNode(slot);
      slot.appendChild(noticeBox(message, { error: !!isError }));
    }
    return;
  }
  state.spending.upload.notice = message;
  state.spending.upload.noticeError = !!isError;
  renderSpendingMapping();
}

/* 확장자로 읽기 방식을 고른다(csv 는 직접 파싱, sheet 는 SheetJS). */
function spendingFileKind(name) {
  const lower = String(name || '').toLowerCase();
  if (/\.(csv|txt)$/.test(lower)) return 'csv';
  if (/\.(xlsx|xls)$/.test(lower)) return 'sheet';
  return null;
}

/* 파일을 브라우저 안에서만 읽어 표 행렬로 만든다. 소비 패턴 화면과 대화창 첨부가 같이 쓴다.
   예외를 던지지 않고 {matrix} 또는 {error: 화면 문장} 을 돌려준다. */
async function readTransactionFileMatrix(file) {
  const kind = spendingFileKind(file && file.name);
  if (!kind) return { error: 'CSV 또는 XLSX 파일만 읽을 수 있습니다.' };
  if (file.size > SPENDING_MAX_BYTES) {
    return { error: '파일이 너무 큽니다. 8MB 이하 파일로 다시 시도해주세요.' };
  }

  let matrix = null;
  try {
    const buffer = await readFileAsArrayBuffer(file);
    if (kind === 'sheet') {
      let XLSXlib = null;
      try {
        XLSXlib = await loadSheetJs();
      } catch (_) {
        return { error: 'XLSX 읽기 도구를 불러오지 못했습니다. 파일을 CSV 로 저장해서 올려주세요.' };
      }
      const book = XLSXlib.read(new Uint8Array(buffer), { type: 'array' });
      const sheetName = book.SheetNames && book.SheetNames[0];
      const sheet = sheetName ? book.Sheets[sheetName] : null;
      if (!sheet) return { error: '시트를 찾지 못했습니다. 다른 파일로 시도해주세요.' };
      matrix = XLSXlib.utils.sheet_to_json(sheet, { header: 1, raw: false, defval: '', blankrows: false });
    } else {
      const text = decodeTextBytes(buffer);
      matrix = parseCsvText(text, sniffDelimiter(text));
    }
  } catch (_) {
    return { error: '파일을 읽지 못했습니다. 다른 파일로 시도해주세요.' };
  }

  if (!matrix || matrix.length < 2) {
    return { error: '읽을 수 있는 거래 행이 없습니다. 머리글과 거래 행이 있는 파일인지 확인해주세요.' };
  }
  return { matrix };
}

/* 날짜와 (금액 또는 입금) 열을 자동으로 찾았는지. 못 찾으면 사용자가 열을 직접 골라야 한다. */
function uploadMappingReady(upload) {
  if (!upload || !upload.map) return false;
  return upload.map.date >= 0 && (upload.map.amount >= 0 || upload.map.deposit >= 0);
}

async function handleSpendingFile(file) {
  state.spending.upload = null;

  const slot = document.getElementById('spendingMapping');
  if (slot) {
    clearNode(slot);
    slot.appendChild(h('p', { class: 'loading-text' }, '파일을 읽는 중...'));
  }

  const read = await readTransactionFileMatrix(file);
  if (read.error) { setUploadNotice(read.error, true); return; }

  const upload = buildUploadState(String(file.name || ''), read.matrix);
  if (!uploadMappingReady(upload)) {
    upload.notice = '날짜와 금액 열을 자동으로 찾지 못했습니다. 아래에서 열을 직접 지정해주세요.';
    upload.noticeError = false;
  }
  state.spending.upload = upload;
  renderSpendingMapping();
}

function columnSelect(role, upload) {
  const id = `spendingCol_${role}`;
  const sel = h('select', { id, name: id, 'aria-label': `${SPENDING_ROLE_LABELS[role]} 열 선택` });
  const optional = role === 'deposit' || role === 'memo';
  sel.appendChild(h('option', { value: '-1', selected: upload.map[role] < 0 }, optional ? '사용 안 함' : '열 선택'));
  upload.headers.forEach((hd, i) => {
    const text = hd ? `${i + 1}. ${hd}` : `${i + 1}. (이름 없음)`;
    sel.appendChild(h('option', { value: String(i), selected: upload.map[role] === i }, text));
  });
  sel.addEventListener('change', () => {
    upload.map[role] = parseInt(sel.value, 10);
    if (role === 'amount') upload.signMode = guessSignMode(upload.dataRows, upload.map.amount);
    upload.notice = null;
    renderSpendingMapping();
  });
  return h('div', { class: 'form-field' }, h('label', { for: id }, SPENDING_ROLE_LABELS[role]), sel);
}

function renderSpendingMapping() {
  const slot = document.getElementById('spendingMapping');
  if (!slot) return;
  clearNode(slot);
  const upload = state.spending.upload;
  if (!upload) return;

  const panel = h('div', { class: 'mapping-panel' });
  const head = h('div', { class: 'mapping-head' });
  head.appendChild(h('span', { class: 'mapping-file' }, upload.fileName));
  head.appendChild(h('button', {
    type: 'button', class: 'btn btn-secondary btn-sm',
    onClick: () => { state.spending.upload = null; renderSpendingMapping(); },
  }, '파일 지우기'));
  panel.appendChild(head);

  if (upload.notice) panel.appendChild(noticeBox(upload.notice, { error: upload.noticeError }));

  const grid = h('div', { class: 'form-grid mapping-grid' });
  ['date', 'amount', 'deposit', 'merchant', 'memo'].forEach((role) => grid.appendChild(columnSelect(role, upload)));

  const signId = 'spendingSignMode';
  const signSel = h('select', { id: signId, name: signId, 'aria-label': '금액 부호 기준' });
  signSel.appendChild(h('option', { value: 'positive', selected: upload.signMode !== 'negative' }, '양수를 지출로'));
  signSel.appendChild(h('option', { value: 'negative', selected: upload.signMode === 'negative' }, '음수를 지출로'));
  signSel.addEventListener('change', () => { upload.signMode = signSel.value; renderSpendingMapping(); });
  grid.appendChild(h('div', { class: 'form-field' }, h('label', { for: signId }, '금액 부호'), signSel));
  panel.appendChild(grid);

  const { rows, stats } = normalizeUploadRows(upload);

  const tableWrap = h('div', { class: 'table-wrap' });
  const table = h('table', { class: 'data-table stackable' });
  table.appendChild(h('thead', {}, h('tr', {},
    h('th', { class: 'text-left' }, '날짜'), h('th', {}, '금액'),
    h('th', { class: 'text-left' }, '가맹점'), h('th', {}, '구분'), h('th', { class: 'text-left' }, '메모'),
  )));
  const tbody = h('tbody', {});
  rows.slice(0, SPENDING_PREVIEW_ROWS).forEach((r) => {
    tbody.appendChild(h('tr', {},
      h('td', { class: 'text-left', 'data-label': '날짜' }, r.date),
      h('td', { 'data-label': '금액' }, fmtWon(r.amount)),
      h('td', { class: 'text-left', 'data-label': '가맹점' }, r.merchant || '(없음)'),
      h('td', { 'data-label': '구분' }, r.kind === 'bank' ? '계좌' : '카드'),
      h('td', { class: 'text-left', 'data-label': '메모' }, r.memo || ''),
    ));
  });
  table.appendChild(tbody);
  tableWrap.appendChild(table);
  if (rows.length) {
    panel.appendChild(h('p', { class: 'mapping-caption' }, `보낼 내용 미리보기 (앞 ${Math.min(rows.length, SPENDING_PREVIEW_ROWS)}행)`));
    panel.appendChild(tableWrap);
  }

  const skipped = stats.noDate + stats.noAmount + stats.deposit + stats.capped;
  const counts = h('div', { class: 'mapping-counts' });
  counts.appendChild(h('span', {}, `읽은 행 ${fmtCount(stats.read)}`));
  counts.appendChild(h('span', {}, `보낼 행 ${fmtCount(stats.sent)}`));
  counts.appendChild(h('span', { class: skipped ? 'value-neutral' : '' }, `건너뛴 행 ${fmtCount(skipped)}`));
  panel.appendChild(counts);

  const reasons = [];
  if (stats.noDate) reasons.push(`날짜를 읽지 못한 행 ${fmtCount(stats.noDate)}`);
  if (stats.noAmount) reasons.push(`금액이 없거나 0인 행 ${fmtCount(stats.noAmount)}`);
  if (stats.deposit) reasons.push(`급여·이체로 보이지 않는 입금 행 ${fmtCount(stats.deposit)}`);
  if (stats.capped) reasons.push(`한 번에 보낼 수 있는 ${SPENDING_MAX_ROWS.toLocaleString('ko-KR')}행을 넘은 행 ${fmtCount(stats.capped)}`);
  if (reasons.length) panel.appendChild(h('p', { class: 'mapping-skip' }, `건너뛴 이유: ${reasons.join(' · ')}`));

  const monthsSel = spendingMonthsSelect('spendingUploadMonths', upload.months);
  monthsSel.addEventListener('change', () => { upload.months = toInt(monthsSel.value) || 3; });
  const monthsField = h('div', { class: 'form-field inline-field' },
    h('label', { for: 'spendingUploadMonths' }, '분석 기간'), monthsSel);

  const msgSlot = h('div', {});
  const sendBtn = h('button', { type: 'button', class: 'btn btn-primary' }, '이 내용으로 분석');
  if (!rows.length) sendBtn.disabled = true;
  sendBtn.addEventListener('click', () => runUploadAnalysis(sendBtn, msgSlot));

  panel.appendChild(h('div', { class: 'source-row send-row' }, monthsField, sendBtn,
    h('span', { class: 'privacy-note' }, SPENDING_PRIVACY_NOTE)));
  panel.appendChild(msgSlot);
  slot.appendChild(panel);
}

async function runUploadAnalysis(btn, msgSlot) {
  const upload = state.spending.upload;
  if (!upload || state.spending.busy) return;
  const { rows } = normalizeUploadRows(upload);
  if (!rows.length) return;

  state.spending.busy = true;
  const label = btn.textContent;
  btn.disabled = true;
  btn.textContent = '분석하는 중...';
  clearNode(msgSlot);

  const res = await Api.analyzeSpending(rows, upload.months || 3);
  state.spending.busy = false;
  btn.disabled = false;
  btn.textContent = label;

  if (!res.ok) {
    msgSlot.appendChild(noticeBox(apiErrorText(res, '분석하지 못했습니다.'), { error: true }));
    return;
  }
  state.spending.data = res.data;
  state.spending.loaded = true;
  state.spending.lastSyntheticMonths = null;
  msgSlot.appendChild(noticeBox(`거래 ${fmtCount(rows.length)}을 분석했습니다. 아래에서 결과를 확인하세요.`));
  updateSpendingConsent();
  renderSpendingResults();
  await loadAndSetHome();
}

/* ---- 10-4. 결과 ---- */

function statTile(label, value, sub, valueClass) {
  const tile = h('div', { class: 'stat-tile' });
  tile.appendChild(h('div', { class: 'stat-label' }, label));
  tile.appendChild(h('div', { class: `stat-value${valueClass ? ' ' + valueClass : ''}` }, value));
  if (sub) tile.appendChild(h('div', { class: 'stat-sub' }, sub));
  return tile;
}

function buildSpendingTiles(summary, features) {
  const wrap = h('div', {});
  const tiles = h('div', { class: 'stat-tiles' });
  tiles.appendChild(statTile('월평균 지출', fmtWon(features.avg_monthly_spend), `최근 ${summary.months}개월 기준`));
  tiles.appendChild(statTile('고정지출 비율', fmtRatioPct1(features.fixed_ratio),
    `고정 ${fmtWon(summary.fixed_spend)}`));
  tiles.appendChild(statTile('구독 합계', fmtWon(features.subscription_total),
    `${fmtCount(features.subscription_count)} 관측`));
  const net = features.net_cash_flow_monthly;
  tiles.appendChild(statTile('저축 여력', fmtWon(net), '월 순현금흐름',
    net > 0 ? 'value-positive' : net < 0 ? 'value-negative' : 'value-neutral'));
  wrap.appendChild(tiles);
  wrap.appendChild(h('p', { class: 'period-caption' },
    `${summary.period_start} ~ ${summary.period_end} · ${summary.months}개월 · 데이터 ${summary.months ? features.data_coverage_days : 0}일 · 자동 분류율 ${fmtRatioPct1(features.classification_quality)}`));
  return wrap;
}

function changeBadge(changePct) {
  if (changePct === null || changePct === undefined) return badge('전월 대비 확인 불가', '');
  const v = Number(changePct);
  const cls = v < 0 ? 'badge-positive' : v > 0 ? 'badge-negative' : '';
  return badge(`전월 대비 ${fmtPctSigned1(v)}`, cls);
}

function buildCategoryBars(summary) {
  const list = h('ul', { class: 'cat-bars' });
  (summary.categories || []).forEach((c) => {
    const share = Math.max(0, Math.min(1, toFloat(c.share)));
    const li = h('li', { class: 'cat-bar' });
    li.appendChild(h('div', { class: 'cat-bar-top' },
      h('span', { class: 'cat-name' }, c.category),
      h('span', { class: 'cat-amount' }, fmtWon(c.amount))));
    li.appendChild(h('div', { class: 'cat-track' },
      h('span', { class: 'cat-fill', style: `width:${(share * 100).toFixed(1)}%` })));
    li.appendChild(h('div', { class: 'cat-bar-bottom' },
      h('span', { class: 'cat-share' }, `${fmtPct1(share * 100)} · ${fmtCount(c.count)}`),
      changeBadge(c.change_pct)));
    list.appendChild(li);
  });
  return list;
}

function buildAnomalyList(summary) {
  const list = h('ul', { class: 'anomaly-list' });
  (summary.anomalies || []).forEach((a) => {
    const li = h('li', { class: 'anomaly-item' });
    const head = h('div', { class: 'anomaly-head' });
    head.appendChild(h('span', { class: 'anomaly-cat' }, a.category));
    head.appendChild(h('span', { class: 'anomaly-month' }, a.month));
    head.appendChild(badge(fmtPctSigned1(a.change_pct), 'badge-negative'));
    li.appendChild(head);
    li.appendChild(h('p', { class: 'anomaly-note' }, a.note || ''));
    li.appendChild(h('div', { class: 'evidence-row' },
      h('span', { class: 'evidence-pill' }, `이번 달 ${fmtWon(a.amount)}`),
      h('span', { class: 'evidence-pill' }, `이전 달 ${fmtWon(a.prev_amount)}`)));
    list.appendChild(li);
  });
  return list;
}

function buildSubscriptionTable(summary) {
  const wrap = h('div', { class: 'table-wrap' });
  const table = h('table', { class: 'data-table stackable' });
  table.appendChild(h('thead', {}, h('tr', {},
    h('th', { class: 'text-left' }, '가맹점'), h('th', {}, '월 금액'),
    h('th', {}, '관측 개월'), h('th', {}, '분류'),
  )));
  const tbody = h('tbody', {});
  (summary.subscriptions || []).forEach((s) => {
    tbody.appendChild(h('tr', {},
      h('td', { class: 'text-left', 'data-label': '가맹점' }, s.merchant),
      h('td', { 'data-label': '월 금액' }, fmtWon(s.amount)),
      h('td', { 'data-label': '관측 개월' }, fmtMonths(s.months_seen)),
      h('td', { 'data-label': '분류' }, s.category),
    ));
  });
  table.appendChild(tbody);
  wrap.appendChild(table);
  return wrap;
}

function buildLifeEventCard(signal) {
  const kind = signal.kind;
  const card = h('div', { class: 'signal-card' });
  const head = h('div', { class: 'signal-head' });
  head.appendChild(h('span', { class: 'signal-kicker' }, LIFE_EVENT_LABELS[kind] || '변화 신호'));
  head.appendChild(badge(`신호 확신도 ${fmtRatioPct1(signal.confidence)}`, ''));
  card.appendChild(head);
  card.appendChild(h('p', { class: 'signal-q' },
    LIFE_EVENT_QUESTIONS[kind] || '생활에 변화가 있었는지 살펴볼까요?'));

  const evidence = plainList(signal.evidence, 'plain-list');
  if (evidence) card.appendChild(evidence);

  const isRefi = kind === 'refinance_window';
  const row = h('div', { class: 'chip-row' });
  row.appendChild(h('button', {
    type: 'button', class: 'chip',
    onClick: () => { if (isRefi) goToCompareWithPrepare({}); else navigateTo('debts'); },
  }, isRefi ? '공시 조건 비교해보기' : '내 부채 살펴보기'));
  card.appendChild(row);
  return card;
}

function spendingSectionTitle(text, countText) {
  const title = h('h2', { class: 'section-title' }, text);
  if (countText) title.appendChild(h('span', { class: 'section-count' }, countText));
  return title;
}

function renderSpendingResults() {
  const slot = document.getElementById('spendingResult');
  if (!slot) return;
  clearNode(slot);

  const data = state.spending.data;
  if (!data || !data.summary || !data.features) {
    slot.appendChild(h('h2', { class: 'section-title' }, '분석 결과'));
    slot.appendChild(h('p', { class: 'empty-text' },
      '아직 분석 결과가 없습니다. 위에서 합성 거래내역으로 분석하거나 거래내역 파일을 올리면 월평균 지출, 고정지출 비율, 구독, 급증 항목을 함께 보여드려요.'));
    return;
  }

  const summary = data.summary;
  const features = data.features;

  slot.appendChild(h('h2', { class: 'section-title' }, '분석 결과'));
  slot.appendChild(buildSpendingTiles(summary, features));

  slot.appendChild(spendingSectionTitle('카테고리별 지출', fmtCount((summary.categories || []).length)));
  if ((summary.categories || []).length) slot.appendChild(buildCategoryBars(summary));
  else slot.appendChild(h('p', { class: 'empty-text' }, '집계된 카테고리가 없습니다.'));

  slot.appendChild(spendingSectionTitle('전월 대비 급증', fmtCount((summary.anomalies || []).length)));
  if ((summary.anomalies || []).length) slot.appendChild(buildAnomalyList(summary));
  else slot.appendChild(h('p', { class: 'empty-text' }, '전월 대비 크게 늘어난 카테고리가 없습니다.'));

  slot.appendChild(spendingSectionTitle('정기 결제로 보이는 항목', fmtCount((summary.subscriptions || []).length)));
  if ((summary.subscriptions || []).length) {
    slot.appendChild(buildSubscriptionTable(summary));
    slot.appendChild(h('p', { class: 'table-note' }, '가맹점 표시는 앞 두 글자만 남기고 가립니다.'));
  } else {
    slot.appendChild(h('p', { class: 'empty-text' }, '반복 결제로 보이는 항목이 없습니다.'));
  }

  const signals = summary.life_events || [];
  if (signals.length) {
    slot.appendChild(spendingSectionTitle('변화 신호', fmtCount(signals.length)));
    slot.appendChild(h('p', { class: 'section-lead' },
      '거래 흐름에서 보이는 신호일 뿐이라 실제와 다를 수 있어요. 맞는지 확인하는 질문으로만 보여드립니다.'));
    const wrap = h('div', { class: 'signal-list' });
    signals.forEach((s) => wrap.appendChild(buildLifeEventCard(s)));
    slot.appendChild(wrap);
  }

  const cards = data.cards || [];
  if (cards.length) {
    slot.appendChild(h('h2', { class: 'section-title' }, '이 분석에서 나온 카드'));
    const wrap = h('div', { class: 'insight-cards-wrap' });
    cards.forEach((c) => wrap.appendChild(buildInsightCard(c)));
    slot.appendChild(wrap);
  }

  const msgSlot = h('div', {});
  const actions = h('div', { class: 'form-actions' });
  const againBtn = h('button', { type: 'button', class: 'btn btn-secondary' }, '다시 분석');
  againBtn.addEventListener('click', () => {
    const months = state.spending.lastSyntheticMonths;
    if (months) {
      runSyntheticAnalysis(months, againBtn, msgSlot);
      return;
    }
    const source = document.getElementById('spendingSource');
    if (source && source.scrollIntoView) {
      try { source.scrollIntoView({ block: 'start' }); } catch (_) { /* noop */ }
    }
    const fileBtn = document.querySelector('#spendingDrop .btn');
    if (fileBtn) fileBtn.focus();
  });
  const clearBtn = h('button', { type: 'button', class: 'btn btn-danger' }, '이 결과 지우기');
  clearBtn.addEventListener('click', async () => {
    if (!confirm('저장된 소비 패턴 분석 결과를 지울까요?')) return;
    clearBtn.disabled = true;
    const res = await Api.deleteSpending();
    clearBtn.disabled = false;
    if (!res.ok) {
      msgSlot.appendChild(noticeBox('결과를 지우지 못했습니다.', { error: true }));
      return;
    }
    state.spending.data = null;
    state.spending.lastSyntheticMonths = null;
    updateSpendingConsent();
    renderSpendingResults();
    await loadAndSetHome();
  });
  actions.appendChild(againBtn);
  actions.appendChild(clearBtn);
  slot.appendChild(actions);
  slot.appendChild(msgSlot);
}

/* ---------- 10-2. 화면: 생애 흐름 (P7) ----------
   GET /api/lifecycle 의 LifecycleView 를 그대로 그린다. 수치는 서버가 계산하고
   화면은 포맷과 배치만 맡는다. 목표 편집만 PUT /api/profile 로 되돌려 보낸다. */

const LIFECYCLE_LEAD = '참고 시나리오이며 특정 상품이나 자산 배분을 권하지 않습니다';

function ratioValueText(meta, value) {
  if (value === null || value === undefined) return '계산 불가';
  return meta.unit === 'months' ? fmtMonths1(value) : fmtRatioPct1(value);
}

/* thresholds 는 비율 이름이 아니라 min_/max_ 접두 키를 쓴다(SPEC 2.7).
   부채비율·투자자산비율처럼 단계 기준값이 없는 비율은 null 을 돌려준다. */
function ratioThresholdValue(meta, thresholds) {
  if (!meta.thresholdKey || !thresholds) return null;
  const t = thresholds[meta.thresholdKey];
  return t === null || t === undefined ? null : Number(t);
}

function ratioThresholdText(meta, t) {
  if (t === null) return '단계 기준값 없음';
  const num = meta.unit === 'months' ? `${t.toFixed(0)}개월` : fmtRatioPct1(t);
  return `기준 ${num} ${meta.dir === 'max' ? '이하' : '이상'}`;
}

function buildRatioTiles(ratios) {
  const wrap = h('div', {});
  const flags = ratios.flags || {};
  const grid = h('div', { class: 'ratio-tiles' });
  RATIO_META.forEach((meta) => {
    const threshold = ratioThresholdValue(meta, ratios.thresholds);
    const raw = flags[meta.key] || 'na';
    /* 기준값이 없으면 서버가 "ok"를 주더라도 "기준 충족"이라고 쓰지 않는다. */
    const flag = raw === 'ok' && threshold === null ? 'ref' : raw;
    const tile = h('div', { class: `ratio-tile is-${flag}` });
    const head = h('div', { class: 'ratio-tile-head' });
    head.appendChild(h('span', { class: 'ratio-label' }, meta.label));
    head.appendChild(h('span', { class: 'ratio-flag' },
      flag === 'ok' ? '기준 충족' : flag === 'warn' ? '점검 필요' : flag === 'ref' ? '참고 지표' : '자료 부족'));
    tile.appendChild(head);
    tile.appendChild(h('div', { class: 'ratio-value' }, ratioValueText(meta, ratios[meta.key])));
    const thresholdRow = h('div', { class: 'ratio-threshold' }, ratioThresholdText(meta, threshold));
    /* 기준값은 아직 검증되지 않은 참고 문서 값이다(SPEC 2.7 가정 문장과 같은 출처). */
    if (threshold !== null) thresholdRow.appendChild(h('span', { class: 'verify-note' }, VERIFY_SOURCE_NOTE));
    tile.appendChild(thresholdRow);
    const sentence = (ratios.interpretations || {})[meta.key];
    if (sentence) tile.appendChild(h('p', { class: 'ratio-note' }, sentence));
    grid.appendChild(tile);
  });
  wrap.appendChild(grid);
  wrap.appendChild(h('p', { class: 'period-caption' },
    `총자산 ${fmtWon(ratios.total_assets)} · 순자산 ${fmtWon(ratios.net_worth)}`));
  return wrap;
}

function buildStageCard(stage) {
  const card = h('div', { class: 'panel-card stage-card' });
  const head = h('div', { class: 'panel-card-head' });
  const headLeft = h('div', {});
  headLeft.appendChild(h('h2', {}, '지금의 생애 단계'));
  headLeft.appendChild(h('p', { class: 'panel-card-sub' }, '나이와 함께 소득 안정성, 부양가족, 부채 상황을 같이 봅니다.'));
  head.appendChild(headLeft);
  head.appendChild(badge(stage.label || '', 'badge-accent stage-badge'));
  card.appendChild(head);

  const reasons = plainList(stage.reasons, 'plain-list');
  if (reasons) card.appendChild(reasons);

  const cols = h('div', { class: 'stage-cols' });
  const makeCol = (title, items, cls) => {
    const col = h('div', { class: `stage-col ${cls}` });
    col.appendChild(h('h3', { class: 'stage-col-title' }, title));
    if (items && items.length) {
      const ul = h('ul', { class: 'stage-list' });
      items.forEach((s) => ul.appendChild(h('li', {}, s)));
      col.appendChild(ul);
    } else {
      col.appendChild(h('p', { class: 'empty-text' }, '해당 항목이 없습니다.'));
    }
    return col;
  };
  cols.appendChild(makeCol('지금 우선순위', stage.priorities, 'is-priority'));
  cols.appendChild(makeCol('피해야 할 행동', stage.avoid, 'is-avoid'));
  card.appendChild(cols);

  if (stage.accounts_note) card.appendChild(h('p', { class: 'stage-note' }, stage.accounts_note));
  return card;
}

function retirementRow(label, value, valueClass) {
  return h('div', { class: 'scn-row' },
    h('span', { class: 'scn-row-label' }, label),
    h('span', { class: `scn-row-value${valueClass ? ' ' + valueClass : ''}` }, value));
}

function buildRetirementCard(proj) {
  const isBase = proj.scenario === '기준';
  const card = h('div', { class: 'scenario-card' + (isBase ? ' is-base' : '') });

  const head = h('div', { class: 'scenario-card-head' });
  head.appendChild(h('span', { class: 'scenario-name' }, proj.scenario || ''));
  if (isBase) head.appendChild(badge('기준선', 'badge-accent'));
  card.appendChild(head);
  card.appendChild(h('p', { class: 'scenario-sub' },
    `실질수익률 ${fmtRatioPct1(proj.real_return)} · 물가 ${fmtRatioPct1(proj.inflation)} · 은퇴 ${proj.retirement_age}세(${proj.years_to_retirement}년 뒤)`));

  const rows = h('div', { class: 'scn-rows' });
  rows.appendChild(retirementRow('은퇴 시점 생활비', fmtWon(proj.retirement_living_cost)));
  rows.appendChild(retirementRow('확정 소득(월)', fmtWon(proj.guaranteed_income_monthly)));
  rows.appendChild(retirementRow('월 부족액', fmtWon(proj.monthly_gap)));
  rows.appendChild(retirementRow('필요 자금', fmtWon(proj.required_fund_pv)));
  rows.appendChild(retirementRow('예상 적립', fmtWon(proj.projected_fund_fv)));

  const shortfall = Number(proj.shortfall || 0);
  rows.appendChild(retirementRow(
    shortfall > 0 ? '부족' : '여유',
    fmtWon(Math.abs(shortfall)),
    shortfall > 0 ? 'value-negative' : 'value-positive'));
  rows.appendChild(retirementRow('필요 월 저축', fmtWon(proj.required_monthly_saving)));
  card.appendChild(rows);

  if (proj.assumptions && proj.assumptions.length) {
    const details = h('details', { class: 'collapsible quiet' });
    details.appendChild(h('summary', {}, '가정 보기'));
    details.appendChild(verifiableList(proj.assumptions, 'plain-list'));
    card.appendChild(details);
  }
  return card;
}

function buildRetirementSection(list) {
  const wrap = h('div', { class: 'scenario-cards' });
  (list || []).forEach((p) => wrap.appendChild(buildRetirementCard(p)));
  return wrap;
}

/* 순자산 곡선: 외부 차트 라이브러리 없이 인라인 SVG 로만 그린다.
   viewBox 를 쓰므로 폭이 좁아지면 그대로 축소된다. */
const NW_W = 720;
const NW_H = 300;
const NW_PAD = { left: 62, right: 16, top: 16, bottom: 32 };

function groupPathRows(rows) {
  const byScenario = new Map();
  (rows || []).forEach((r) => {
    const key = r.scenario || '기준';
    if (!byScenario.has(key)) byScenario.set(key, []);
    byScenario.get(key).push(r);
  });
  byScenario.forEach((arr) => arr.sort((a, b) => Number(a.age) - Number(b.age)));
  return byScenario;
}

function buildNetWorthChart(rows) {
  const byScenario = groupPathRows(rows);
  const base = byScenario.get('기준') || byScenario.values().next().value || [];
  if (!base.length) return noticeBox('순자산 경로를 계산할 자료가 부족합니다.');

  const ages = base.map((r) => Number(r.age));
  const ageMin = ages[0];
  const ageMax = ages[ages.length - 1];
  const ageSpan = Math.max(1, ageMax - ageMin);

  let yMax = 0;
  let yMin = 0;
  byScenario.forEach((arr) => arr.forEach((r) => {
    yMax = Math.max(yMax, Number(r.net_worth) || 0);
    yMin = Math.min(yMin, Number(r.net_worth) || 0);
  }));
  base.forEach((r) => { yMax = Math.max(yMax, Number(r.debt_balance) || 0); });
  if (yMax === yMin) yMax = yMin + 1;

  const px = (age) => NW_PAD.left + ((age - ageMin) / ageSpan) * (NW_W - NW_PAD.left - NW_PAD.right);
  const py = (v) => NW_PAD.top + ((yMax - v) / (yMax - yMin)) * (NW_H - NW_PAD.top - NW_PAD.bottom);
  const linePath = (arr, valueKey) => arr
    .map((r, i) => `${i === 0 ? 'M' : 'L'}${px(Number(r.age)).toFixed(1)} ${py(Number(r[valueKey]) || 0).toFixed(1)}`)
    .join(' ');

  const svg = svgEl('svg', {
    viewBox: `0 0 ${NW_W} ${NW_H}`, class: 'nw-chart', role: 'img',
    'aria-label': `${ageMin}세부터 ${ageMax}세까지 시나리오별 순자산과 부채 잔액 추이. 자세한 수치는 아래 표에 있습니다.`,
  });

  /* 가로 눈금과 y축 라벨 */
  const ticks = 4;
  for (let i = 0; i <= ticks; i += 1) {
    const v = yMin + ((yMax - yMin) * i) / ticks;
    const y = py(v);
    svg.appendChild(svgEl('line', {
      x1: NW_PAD.left, x2: NW_W - NW_PAD.right, y1: y.toFixed(1), y2: y.toFixed(1),
      stroke: '#E5E7EB', 'stroke-width': 1,
    }));
    svg.appendChild(svgEl('text', {
      x: NW_PAD.left - 8, y: (y + 4).toFixed(1), 'text-anchor': 'end', class: 'nw-axis-text',
    }, fmtWonShort(v)));
  }
  if (yMin < 0) {
    svg.appendChild(svgEl('line', {
      x1: NW_PAD.left, x2: NW_W - NW_PAD.right, y1: py(0).toFixed(1), y2: py(0).toFixed(1),
      stroke: '#9CA3AF', 'stroke-width': 1, 'stroke-dasharray': '3 3',
    }));
  }

  /* x축 라벨: 5년 간격과 마지막 나이 */
  const xAges = [];
  for (let a = ageMin; a <= ageMax; a += 5) xAges.push(a);
  if (xAges[xAges.length - 1] !== ageMax) xAges.push(ageMax);
  xAges.forEach((a) => {
    svg.appendChild(svgEl('text', {
      x: px(a).toFixed(1), y: NW_H - 10, 'text-anchor': 'middle', class: 'nw-axis-text',
    }, `${a}세`));
  });
  svg.appendChild(svgEl('text', { x: 4, y: 12, class: 'nw-axis-title' }, '순자산(원)'));

  /* 부채선을 먼저, 기준 시나리오를 마지막에 그려 위로 올린다. */
  svg.appendChild(svgEl('path', {
    d: linePath(base, 'debt_balance'), fill: 'none', stroke: LC_DEBT_COLOR,
    'stroke-width': 1.5, 'stroke-dasharray': '5 3', 'stroke-linejoin': 'round',
  }));
  LC_SCENARIO_ORDER.forEach((name) => {
    const arr = byScenario.get(name);
    if (!arr || !arr.length) return;
    const style = LC_SCENARIO_STYLE[name] || LC_SCENARIO_STYLE['기준'];
    svg.appendChild(svgEl('path', {
      d: linePath(arr, 'net_worth'), fill: 'none', stroke: style.color,
      'stroke-width': style.width, 'stroke-dasharray': style.dash,
      'stroke-linejoin': 'round', 'stroke-linecap': 'round',
    }));
  });

  /* 마우스를 올리면 세로 안내선과 아래 읽기 줄이 함께 움직인다. */
  const guide = svgEl('line', {
    y1: NW_PAD.top, y2: NW_H - NW_PAD.bottom, stroke: '#4F46E5',
    'stroke-width': 1, 'stroke-dasharray': '2 2', class: 'nw-guide is-hidden',
    x1: NW_PAD.left, x2: NW_PAD.left,
  });
  svg.appendChild(guide);

  const readout = h('p', { class: 'nw-readout' }, '그래프 위에 마우스를 올리면 그 나이의 값을 보여줍니다.');
  const hit = svgEl('rect', {
    x: NW_PAD.left, y: NW_PAD.top,
    width: NW_W - NW_PAD.left - NW_PAD.right, height: NW_H - NW_PAD.top - NW_PAD.bottom,
    fill: 'transparent', class: 'nw-hit',
  });
  const showAt = (idx) => {
    const row = base[idx];
    if (!row) return;
    const age = Number(row.age);
    guide.setAttribute('x1', px(age).toFixed(1));
    guide.setAttribute('x2', px(age).toFixed(1));
    guide.classList.remove('is-hidden');
    const parts = [`${age}세(${row.year}년)`];
    LC_LEGEND_ORDER.forEach((name) => {
      const arr = byScenario.get(name);
      if (!arr) return;
      const found = arr.find((r) => Number(r.age) === age);
      if (found) parts.push(`${name} ${fmtWonShort(found.net_worth)}원`);
    });
    parts.push(`부채 ${fmtWonShort(row.debt_balance)}원`);
    readout.textContent = parts.join(' · ');
  };
  hit.addEventListener('pointermove', (e) => {
    const rect = svg.getBoundingClientRect();
    if (!rect.width) return;
    const svgX = ((e.clientX - rect.left) / rect.width) * NW_W;
    const ratio = (svgX - NW_PAD.left) / (NW_W - NW_PAD.left - NW_PAD.right);
    const age = ageMin + Math.max(0, Math.min(1, ratio)) * ageSpan;
    let bestIdx = 0;
    base.forEach((r, i) => {
      if (Math.abs(Number(r.age) - age) < Math.abs(Number(base[bestIdx].age) - age)) bestIdx = i;
    });
    showAt(bestIdx);
  });
  hit.addEventListener('pointerleave', () => {
    guide.classList.add('is-hidden');
    readout.textContent = '그래프 위에 마우스를 올리면 그 나이의 값을 보여줍니다.';
  });
  svg.appendChild(hit);

  const legend = h('div', { class: 'nw-legend' });
  LC_LEGEND_ORDER.forEach((name) => {
    if (!byScenario.get(name)) return;
    const style = LC_SCENARIO_STYLE[name] || LC_SCENARIO_STYLE['기준'];
    const sw = h('span', { class: 'nw-swatch' + (style.dash ? ' is-dashed' : '') });
    sw.style.background = style.color;
    sw.style.color = style.color;
    legend.appendChild(h('span', { class: 'nw-legend-item' }, sw, `${name} 순자산`));
  });
  const debtSw = h('span', { class: 'nw-swatch is-dashed' });
  debtSw.style.background = LC_DEBT_COLOR;
  debtSw.style.color = LC_DEBT_COLOR;
  legend.appendChild(h('span', { class: 'nw-legend-item' }, debtSw, '부채 잔액'));

  return h('div', { class: 'nw-chart-wrap' }, svg, readout, legend);
}

function buildNetWorthTable(rows) {
  const byScenario = groupPathRows(rows);
  const base = byScenario.get('기준') || byScenario.values().next().value || [];
  if (!base.length) return null;
  const startAge = Number(base[0].age);
  const picked = base.filter((r, i) => (Number(r.age) - startAge) % 5 === 0 || i === base.length - 1);

  const table = h('table', { class: 'data-table stackable' });
  table.appendChild(h('thead', {}, h('tr', {},
    h('th', { class: 'text-left' }, '나이'), h('th', {}, '순자산 기준'), h('th', {}, '부채'))));
  const tbody = h('tbody', {});
  picked.forEach((r) => {
    tbody.appendChild(h('tr', {},
      h('td', { class: 'text-left', 'data-label': '나이' }, `${r.age}세 (${r.year}년)`),
      h('td', { 'data-label': '순자산 기준' }, fmtWon(r.net_worth)),
      h('td', { 'data-label': '부채' }, fmtWon(r.debt_balance))));
  });
  table.appendChild(tbody);
  return h('div', { class: 'table-wrap' }, table);
}

/* 소득 공백 지도의 numbers 는 구간마다 키가 다르다.
   "...률"로 끝나면 비율, 1만 이상 정수는 금액, 나머지는 그대로 센다. */
function gapNumberText(key, value) {
  if (typeof value !== 'number') return String(value === null || value === undefined ? '-' : value);
  if (/[률율]$/.test(key) || !Number.isInteger(value)) return fmtRatioPct1(value);
  if (Math.abs(value) >= 10000) return fmtWon(value);
  return value.toLocaleString('ko-KR');
}

function buildIncomeGapMap(list) {
  const wrap = h('div', { class: 'gap-list' });
  (list || []).forEach((row) => {
    const item = h('div', { class: 'gap-item' });
    item.appendChild(h('div', { class: 'gap-period' }, row.period || ''));
    const flows = h('div', { class: 'gap-flows' });
    flows.appendChild(h('div', { class: 'gap-flow' },
      h('span', { class: 'gap-flow-label' }, '유입'), h('span', {}, row.inflow || '-')));
    flows.appendChild(h('div', { class: 'gap-flow' },
      h('span', { class: 'gap-flow-label' }, '유출'), h('span', {}, row.outflow || '-')));
    item.appendChild(flows);
    if (row.key_question) item.appendChild(h('p', { class: 'gap-question' }, row.key_question));
    const numbers = row.numbers || {};
    const keys = Object.keys(numbers);
    if (keys.length) {
      const pills = h('div', { class: 'evidence-row' });
      keys.forEach((k) => pills.appendChild(h('span', { class: 'evidence-pill' }, `${k} ${gapNumberText(k, numbers[k])}`)));
      item.appendChild(pills);
    }
    wrap.appendChild(item);
  });
  return wrap;
}

/* ----- 목표 ----- */

function nextGoalId(goals) {
  let max = 0;
  (goals || []).forEach((g) => {
    const m = /^G(\d+)$/.exec(g.id || '');
    if (m) max = Math.max(max, parseInt(m[1], 10));
  });
  let n = max + 1;
  const ids = new Set((goals || []).map((g) => g.id));
  while (ids.has('G' + n)) n += 1;
  return 'G' + n;
}

async function saveGoals(nextGoals) {
  const profile = state.profile;
  if (!profile) return { ok: false, error: '저장된 프로필이 없습니다.' };
  const r = await saveProfile({ ...profile, goals: nextGoals });
  return r;
}

function buildGoalItem(goal, profile) {
  const item = h('div', { class: 'goal-item' });
  const head = h('div', { class: 'goal-head' });
  const headLeft = h('div', { class: 'goal-head-left' });
  headLeft.appendChild(h('span', { class: 'goal-label' }, goal.label || ''));
  headLeft.appendChild(badge(GOAL_KIND_LABELS[goal.kind] || goal.kind || '기타', ''));
  head.appendChild(headLeft);

  const actions = h('div', { class: 'row-actions' });
  actions.appendChild(h('button', {
    type: 'button', class: 'btn btn-secondary btn-sm', 'aria-label': `${goal.label || '목표'} 수정`,
    onClick: () => {
      state.lifecycle.editingGoalId = goal.id;
      state.lifecycle.goalFormOpen = true;
      renderLifecycle();
    },
  }, '수정'));
  actions.appendChild(h('button', {
    type: 'button', class: 'btn btn-danger btn-sm', 'aria-label': `${goal.label || '목표'} 삭제`,
    onClick: async () => {
      if (!confirm('이 목표를 삭제할까요?')) return;
      const nextGoals = ((profile && profile.goals) || []).filter((g) => g.id !== goal.id);
      const r = await saveGoals(nextGoals);
      if (r.ok) renderLifecycle();
    },
  }, '삭제'));
  head.appendChild(actions);
  item.appendChild(head);

  const target = Number(goal.target_amount) || 0;
  const saved = Number(goal.saved_amount) || 0;
  const pct = goal.progress_pct === null || goal.progress_pct === undefined
    ? (target > 0 ? Math.min(100, (saved / target) * 100) : 0)
    : Number(goal.progress_pct);

  item.appendChild(h('div', { class: 'cat-bar-top' },
    h('span', { class: 'cat-name' }, `${fmtWon(saved)} / ${fmtWon(target)}`),
    h('span', { class: 'cat-amount' }, fmtPct1(pct))));
  item.appendChild(h('div', { class: 'cat-track' },
    h('span', { class: 'cat-fill', style: `width:${Math.max(0, Math.min(100, pct)).toFixed(1)}%` })));

  const remaining = Math.max(0, target - saved);
  /* 남은 개월과 월 필요 저축액은 서버(app/services/lifecycle.py)가 계산해서 내려준다.
     값이 오면 그대로 쓰고, 없을 때만 화면에서 목표일로 되짚어 계산한다. */
  const serverMonths = numOrNull(goal.remaining_months);
  const months = serverMonths !== null ? serverMonths : monthsUntilDate(goal.target_date);
  const serverNeeded = numOrNull(goal.monthly_needed);

  const bits = [`목표일 ${goal.target_date || '-'}`];
  if (months === null) bits.push('남은 기간 확인 불가');
  else if (months > 0) bits.push(`남은 기간 ${months}개월`);
  else bits.push('목표일이 지났습니다');

  if (remaining === 0) {
    bits.push('목표 금액을 채웠습니다');
  } else if (months !== null && months > 0) {
    const needed = serverNeeded !== null ? serverNeeded : Math.ceil(remaining / months);
    if (needed > 0) bits.push(`남은 기간으로 나누면 월 ${fmtWon(needed)}`);
  }
  item.appendChild(h('div', { class: 'cat-bar-bottom' }, h('span', { class: 'goal-meta' }, bits.join(' · '))));
  return item;
}

function buildGoalForm(profile) {
  const goals = (profile && profile.goals) || [];
  const editing = state.lifecycle.editingGoalId
    ? goals.find((g) => g.id === state.lifecycle.editingGoalId)
    : null;

  const card = h('div', { class: 'panel-card' });
  card.appendChild(h('h3', {}, editing ? '목표 수정' : '목표 추가'));

  const form = h('form', { 'aria-label': editing ? '목표 수정 폼' : '목표 추가 폼' });
  const grid = h('div', { class: 'form-grid' });
  const kindSel = selectField('goal_kind', '종류', GOAL_KIND_LABELS, editing ? editing.kind : 'housing');
  const labelF = fieldText('goal_label', '이름', editing ? editing.label : '');
  const amountF = fieldMoney('goal_target_amount', '목표 금액', editing ? editing.target_amount : 0);
  const dateF = fieldDate('goal_target_date', '목표일', editing ? editing.target_date : '');
  const savedF = fieldMoney('goal_saved_amount', '지금까지 모은 금액', editing ? editing.saved_amount : 0);
  const changeF = fieldNumber('goal_income_change', '목표 시점 소득 변화율(예: -0.3)',
    editing ? editing.monthly_income_change_pct : 0, { step: 0.05 });
  const priorityF = fieldNumber('goal_priority', '우선순위(작을수록 먼저)', editing ? editing.priority : 100, { min: 0 });
  [kindSel, labelF, amountF, dateF, savedF, changeF, priorityF].forEach((f) => grid.appendChild(f.wrap));
  form.appendChild(grid);

  const msgSlot = h('div', {});
  const actions = h('div', { class: 'form-actions' });
  const submitBtn = h('button', { type: 'submit', class: 'btn btn-primary' }, editing ? '목표 저장' : '목표 추가');
  actions.appendChild(submitBtn);
  actions.appendChild(h('button', {
    type: 'button', class: 'btn btn-secondary',
    onClick: () => {
      state.lifecycle.editingGoalId = null;
      state.lifecycle.goalFormOpen = false;
      renderLifecycle();
    },
  }, '닫기'));
  form.appendChild(actions);
  form.appendChild(msgSlot);

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    clearNode(msgSlot);
    const label = labelF.input.value.trim();
    const targetDate = dateF.input.value;
    const targetAmount = parseMoney(amountF.input.value);
    if (!targetDate) {
      msgSlot.appendChild(noticeBox('목표일을 입력해주세요.', { error: true }));
      return;
    }
    if (targetAmount <= 0) {
      msgSlot.appendChild(noticeBox('목표 금액을 0보다 크게 입력해주세요.', { error: true }));
      return;
    }
    const goalObj = {
      id: editing ? editing.id : nextGoalId(goals),
      kind: kindSel.select.value,
      label: label || GOAL_KIND_LABELS[kindSel.select.value] || '목표',
      target_amount: targetAmount,
      target_date: targetDate,
      priority: toInt(priorityF.input.value),
      saved_amount: parseMoney(savedF.input.value),
      monthly_income_change_pct: toFloat(changeF.input.value),
    };
    const nextGoals = editing
      ? goals.map((g) => (g.id === editing.id ? { ...g, ...goalObj } : g))
      : [...goals, goalObj];
    submitBtn.disabled = true;
    const r = await saveGoals(nextGoals);
    submitBtn.disabled = false;
    if (!r.ok) {
      msgSlot.appendChild(noticeBox(apiErrorText(r, '저장하지 못했습니다.'), { error: true }));
      return;
    }
    state.lifecycle.editingGoalId = null;
    state.lifecycle.goalFormOpen = false;
    renderLifecycle();
  });

  card.appendChild(form);
  return card;
}

function buildGoalsSection(goalViews, profile) {
  const wrap = h('div', {});
  const list = goalViews || [];
  if (!list.length) {
    wrap.appendChild(noticeBox('등록된 목표가 없습니다. 목표를 넣으면 진행률과 필요한 월 저축액을 함께 보여드려요.'));
  } else {
    const listWrap = h('div', { class: 'goal-list' });
    list.forEach((g) => listWrap.appendChild(buildGoalItem(g, profile)));
    wrap.appendChild(listWrap);
  }

  if (state.lifecycle.goalFormOpen) {
    wrap.appendChild(buildGoalForm(profile));
  } else {
    const row = h('div', { class: 'form-actions' });
    row.appendChild(h('button', {
      type: 'button', class: 'btn btn-secondary',
      onClick: () => {
        state.lifecycle.editingGoalId = null;
        state.lifecycle.goalFormOpen = true;
        renderLifecycle();
      },
    }, '목표 추가'));
    wrap.appendChild(row);
  }
  return wrap;
}

async function renderLifecycle() {
  const { root, isStale } = mountView('lifecycle');
  focusMainAfterRender();
  appendViewHeader(root, '생애 흐름', LIFECYCLE_LEAD);
  root.appendChild(h('p', { class: 'loading-text' }, '불러오는 중...'));

  const [lifeRes, profileRes] = await Promise.all([Api.getLifecycle(), Api.getProfile()]);
  if (isStale()) return;
  clearNode(root);
  appendViewHeader(root, '생애 흐름', LIFECYCLE_LEAD);

  if (profileRes.ok) state.profile = profileRes.data;

  if (!lifeRes.ok) {
    if (lifeRes.status === 404) {
      root.appendChild(noticeBox('아직 저장된 프로필이 없습니다. 계정을 고르거나 내 부채 화면에서 정보를 입력해주세요.'));
      const row = h('div', { class: 'form-actions' });
      row.appendChild(h('button', { type: 'button', class: 'btn btn-primary', onClick: () => navigateTo('personas') }, '계정 고르기'));
      row.appendChild(h('button', { type: 'button', class: 'btn btn-secondary', onClick: () => navigateTo('debts') }, '내 부채로 가기'));
      root.appendChild(row);
      return;
    }
    root.appendChild(noticeBox('서버에 연결되지 않았습니다.', { error: true, onRetry: renderLifecycle }));
    return;
  }

  const data = lifeRes.data || {};
  state.lifecycle.data = data;
  const profile = state.profile;

  root.appendChild(buildStageCard(data.stage || {}));

  root.appendChild(h('h2', { class: 'section-title' }, '재무 비율'));
  root.appendChild(h('p', { class: 'section-lead' },
    '생애 단계별 기준값과 비교한 결과입니다. 기준값은 참고용이며 개인 상황에 따라 다를 수 있습니다.'));
  root.appendChild(buildRatioTiles(data.ratios || {}));

  const retirement = data.retirement || [];
  if (retirement.length) {
    root.appendChild(h('h2', { class: 'section-title' }, '노후 자금 시뮬레이션'));
    root.appendChild(h('p', { class: 'section-lead' },
      '낙관·기준·비관 세 가지 가정으로 같은 계산을 돌린 결과입니다.'));
    root.appendChild(buildRetirementSection(retirement));
  }

  const path = data.net_worth_path || [];
  if (path.length) {
    root.appendChild(h('h2', { class: 'section-title' }, '순자산 흐름'));
    root.appendChild(buildNetWorthChart(path));
    const table = buildNetWorthTable(path);
    if (table) {
      root.appendChild(table);
      root.appendChild(h('p', { class: 'table-note' }, '5년 간격으로 추린 값이며 연 단위 근사입니다.'));
    }
  }

  const gapMap = data.income_gap_map;
  if (gapMap && gapMap.length) {
    root.appendChild(h('h2', { class: 'section-title' }, '소득 공백 지도'));
    root.appendChild(h('p', { class: 'section-lead' },
      '퇴직부터 연금 개시까지 소득이 비는 구간을 네 시기로 나눠 봅니다.'));
    root.appendChild(buildIncomeGapMap(gapMap));
  }

  root.appendChild(h('h2', { class: 'section-title' }, '목표'));
  root.appendChild(buildGoalsSection(data.goals || [], profile));

  const assumptions = data.assumptions || [];
  const footer = h('div', { class: 'lifecycle-footer' });
  if (assumptions.length) {
    const details = h('details', { class: 'collapsible quiet' });
    details.appendChild(h('summary', {}, `이 화면이 쓴 가정 ${assumptions.length}개 보기`));
    details.appendChild(verifiableList(assumptions, 'plain-list'));
    footer.appendChild(details);
  }
  footer.appendChild(h('p', { class: 'lifecycle-disclaimer' }, data.disclaimer || LIFECYCLE_LEAD));
  root.appendChild(footer);
}

/* ---------- 11. 화면: 페르소나 ---------- */

/* 계정(페르소나)이 바뀌면 대화는 그 계정의 것이므로 화면에서 비운다. */
function resetChatForProfileChange() {
  state.chat.streamSeq += 1;
  state.chat.messages = [];
  state.chat.chatId = null;
  state.chat.pending = false;
  state.chat.title = '';
  state.chat.resources = [];
  state.chat.queuedSend = null;
  state.chat.liveRow = null;
  state.actionExplains = {};
}

/* 소비 패턴 분석 결과도 계정별이므로 계정이 바뀌면 다시 불러온다. */
function resetSpendingForProfileChange() {
  state.spending.data = null;
  state.spending.loaded = false;
  state.spending.upload = null;
  state.spending.lastSyntheticMonths = null;
  state.spending.busy = false;
}

async function loadPersonaAndGoHome(personaId) {
  const res = await Api.loadPersona(personaId);
  if (!res.ok) return { ok: false, error: res.error };
  state.profile = res.data;
  state.lastPersonaId = personaId;
  lsSetStr('donn.lastPersonaId', personaId);
  resetChatForProfileChange();
  resetSpendingForProfileChange();
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
  resetChatForProfileChange();
  resetSpendingForProfileChange();
  await refreshSidebarData();
  return { ok: true };
}

async function renderPersonas() {
  const { root, isStale } = mountView('personas');
  focusMainAfterRender();
  appendViewHeader(root, '계정 선택 (PoC)', '로그인 대신 페르소나를 골라 그 사람으로 앱을 봅니다');

  const msgSlot = h('div', {});
  if (state.personaNotice) {
    msgSlot.appendChild(noticeBox(state.personaNotice));
    state.personaNotice = null;
  }
  root.appendChild(msgSlot);

  const resetRow = h('div', { class: 'form-actions', style: 'margin:0 0 16px;' });
  resetRow.appendChild(h('button', {
    type: 'button', class: 'btn btn-secondary', 'aria-label': '프로필 초기화',
    onClick: async () => {
      clearNode(msgSlot);
      const r = await clearSessionProfile();
      if (!r.ok) { msgSlot.appendChild(noticeBox(apiErrorText(r, '초기화하지 못했습니다.'), { error: true })); return; }
      state.personaNotice = '프로필이 초기화되었습니다.';
      renderPersonas();
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
  const currentId = state.profile ? state.profile.id : null;
  personas.forEach((p) => {
    const isCurrent = !!currentId && currentId === p.id;
    const card = h('div', { class: 'persona-card' + (isCurrent ? ' is-current' : '') });
    const nameRow = h('div', { class: 'persona-name-row' });
    nameRow.appendChild(h('div', { class: 'persona-name' }, p.display_name || p.id));
    if (isCurrent) nameRow.appendChild(badge('사용 중', 'badge-accent'));
    card.appendChild(nameRow);
    card.appendChild(h('div', { class: 'persona-oneliner' }, p.one_liner || ''));
    const stats = h('div', { class: 'persona-stats' });
    stats.appendChild(h('span', {}, `대출 ${p.loans_count ?? '-'}건`));
    stats.appendChild(h('span', {}, `총잔액 ${fmtWon(p.total_balance)}`));
    card.appendChild(stats);
    const cardMsg = h('div', {});
    card.appendChild(h('button', {
      type: 'button', class: 'btn ' + (isCurrent ? 'btn-secondary' : 'btn-primary'),
      'aria-label': `${p.display_name || p.id} 계정으로 보기`,
      onClick: async () => {
        clearNode(cardMsg);
        const r = await loadPersonaAndGoHome(p.id);
        if (!r.ok) cardMsg.appendChild(noticeBox('계정을 불러오지 못했습니다.', { error: true }));
      },
    }, '이 계정으로 보기'));
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

  const detailWrap = h('div', { class: 'decision-detail is-hidden' });
  const explainSlot = h('div', {});
  const detailPre = h('pre', { class: 'decision-detail-pre' });
  const techDetails = h('details', { class: 'collapsible quiet' });
  techDetails.appendChild(h('summary', {}, '기술 정보'));
  techDetails.appendChild(detailPre);
  detailWrap.appendChild(explainSlot);
  detailWrap.appendChild(techDetails);
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
      const isHidden = detailWrap.classList.contains('is-hidden');
      if (isHidden && !detailWrap.dataset.loaded) {
        detailPre.textContent = '불러오는 중...';
        detailWrap.dataset.loaded = '1';
        const r = await Api.getDecision(rec.decision_id);
        detailPre.textContent = r.ok ? JSON.stringify(r.data, null, 2) : '상세 정보를 불러오지 못했습니다.';
        /* 저장된 설명만 보여준다(생성하지 않는다, SPEC 2.8). 없으면 그냥 생략한다. */
        if (rec.kind === 'compare') {
          const ex = await Api.getCompareExplain(rec.decision_id);
          if (ex.ok && ex.data && ex.data.summary) {
            clearNode(explainSlot);
            explainSlot.appendChild(h('h3', { class: 'explain-block-title' }, '왜 이 순서인가요?'));
            explainSlot.appendChild(buildExplainBody(ex.data));
          }
        }
      }
      detailWrap.classList.toggle('is-hidden');
    },
  }, '상세'));
  actions.appendChild(resultSpan);
  row.appendChild(actions);

  wrap.appendChild(row);
  wrap.appendChild(detailWrap);
  return wrap;
}

async function renderDecisions() {
  const { root, isStale } = mountView('decisions');
  focusMainAfterRender();
  appendViewHeader(root, '결정 기록', '같은 조건으로 다시 계산했을 때 결과가 일치하는지 확인할 수 있어요.');

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

/* ---------- 13. 대화 화면 (#chat) ----------
   SPEC 2.9 / 2.11. 홈에서 메시지를 보내면 이 화면으로 넘어와 스트림을 받는다.
   그리는 순서: (1) 사용자 말풍선 (2) 노드 카드 묶음("생각 과정")
   (3) 응답 문장(타자 효과) (4) 인라인 액션 카드 (5) 칩.
   서버 문자열은 전부 텍스트 노드로 넣는다(innerHTML 사용 금지). */

/* ---- 13-1. 마크다운 라이트 렌더러 ----
   answer_format="markdown" 응답만 여기로 온다. 제목(#~####), **굵게**, "- " 목록,
   "1. " 번호 목록, 빈 줄 단락만 처리하고 나머지는 전부 평문으로 이스케이프한다.
   링크는 만들지 않는다(주소는 리소스 패널에만 둔다). */

function appendInlineMarkdown(parent, text) {
  const src = String(text || '');
  const re = /\*\*([^*\n]+)\*\*/g;
  let last = 0;
  let m = re.exec(src);
  while (m !== null) {
    if (m.index > last) parent.appendChild(document.createTextNode(src.slice(last, m.index)));
    parent.appendChild(h('strong', {}, m[1]));
    last = m.index + m[0].length;
    m = re.exec(src);
  }
  if (last < src.length) parent.appendChild(document.createTextNode(src.slice(last)));
}

function renderMarkdownLite(text) {
  const frag = document.createDocumentFragment();
  const lines = String(text || '').replace(/\r\n/g, '\n').split('\n');
  let list = null;
  let listTag = null;
  let para = null;
  const flushList = () => { if (list) { frag.appendChild(list); list = null; listTag = null; } };
  const flushPara = () => { if (para) { frag.appendChild(para); para = null; } };

  lines.forEach((raw) => {
    const line = raw.trim();
    if (!line) { flushList(); flushPara(); return; }

    const head = /^(#{1,4})\s+(.+)$/.exec(line);
    if (head) {
      flushList(); flushPara();
      const el = h(head[1].length <= 2 ? 'h3' : 'h4', { class: 'md-h' });
      appendInlineMarkdown(el, head[2]);
      frag.appendChild(el);
      return;
    }

    const bullet = /^[-*]\s+(.+)$/.exec(line);
    if (bullet) {
      flushPara();
      if (!list || listTag !== 'ul') { flushList(); list = h('ul', { class: 'md-ul' }); listTag = 'ul'; }
      const li = h('li', {});
      appendInlineMarkdown(li, bullet[1]);
      list.appendChild(li);
      return;
    }

    const num = /^(\d{1,2})[.)]\s+(.+)$/.exec(line);
    if (num) {
      flushPara();
      if (!list || listTag !== 'ol') { flushList(); list = h('ol', { class: 'md-ol' }); listTag = 'ol'; }
      const li = h('li', {});
      appendInlineMarkdown(li, num[2]);
      list.appendChild(li);
      return;
    }

    flushList();
    if (!para) para = h('p', { class: 'md-p' });
    else para.appendChild(document.createTextNode('\n'));
    appendInlineMarkdown(para, line);
  });

  flushList();
  flushPara();
  return frag;
}

/* answer_format 에 따라 본문 노드를 만든다. text 면 줄바꿈만 살린다. */
function buildAnswerBody(text, format) {
  const box = h('div', { class: 'answer-body' });
  if (format === 'markdown') {
    box.classList.add('is-markdown');
    box.appendChild(renderMarkdownLite(text));
  } else {
    box.classList.add('is-text');
    box.appendChild(document.createTextNode(String(text || '')));
  }
  return box;
}

/* ---- 13-2. 타자 효과 ----
   완성된 DOM 을 먼저 만들고 텍스트 노드만 앞에서부터 채운다(마크다운 구조를 지킨다).
   글자당 12ms 기준, 전체 2.5초를 넘지 않도록 한 틱에 여러 글자를 드러낸다.
   클릭하면 즉시 전체 표시. 동작 최소화 설정이면 처음부터 전부 보여준다. */

const TYPE_TICK_MS = 16;
const TYPE_PER_CHAR_MS = 12;
const TYPE_MAX_MS = 2500;

function prefersReducedMotion() {
  try { return window.matchMedia('(prefers-reduced-motion: reduce)').matches; } catch (_) { return false; }
}

function typeIntoElement(el) {
  const parts = [];
  let total = 0;
  const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT, null);
  let node = walker.nextNode();
  while (node) {
    parts.push({ node, full: node.data });
    total += node.data.length;
    node = walker.nextNode();
  }
  if (!total || prefersReducedMotion()) return { finish: () => {} };

  parts.forEach((p) => { p.node.data = ''; });
  el.classList.add('is-typing');

  /* 남은 시간이 아니라 "지난 시간"으로 진행도를 계산한다. 배경 탭처럼 타이머가
     느려지는 환경에서도 정해진 시간 안에 문장이 다 나온다. */
  const duration = Math.min(TYPE_MAX_MS, Math.max(240, total * TYPE_PER_CHAR_MS));
  const started = Date.now();
  let shown = 0;
  let idx = 0;
  let offset = 0;
  let timer = null;

  const revealTo = (target) => {
    while (shown < target && idx < parts.length) {
      const p = parts[idx];
      const take = Math.min(target - shown, p.full.length - offset);
      offset += take;
      shown += take;
      p.node.data = p.full.slice(0, offset);
      if (offset >= p.full.length) { idx += 1; offset = 0; }
    }
  };

  const finish = () => {
    if (timer) { clearInterval(timer); timer = null; }
    parts.forEach((p) => { p.node.data = p.full; });
    el.classList.remove('is-typing');
    el.removeEventListener('click', finish);
  };

  timer = setInterval(() => {
    const ratio = (Date.now() - started) / duration;
    if (ratio >= 1) { finish(); return; }
    revealTo(Math.max(1, Math.floor(total * ratio)));
  }, TYPE_TICK_MS);

  el.addEventListener('click', finish);
  return { finish };
}

/* ---- 13-3. 노드 카드("생각 과정") ---- */

function stageStatusIcon(status) {
  if (status === 'done') return icon('check', 13);
  if (status === 'fallback') return icon('alert', 13);
  if (status === 'skip') return icon('minus', 13);
  return h('span', { class: 'node-spinner', 'aria-hidden': 'true' });
}

function buildNodeCard(stage) {
  const status = stage.status || 'start';
  const card = h('div', { class: `node-card is-${status}` });

  const top = h('div', { class: 'node-card-top' });
  const mark = h('span', {
    class: 'node-status', 'aria-label': STAGE_STATUS_LABELS[status] || status, role: 'img',
  }, stageStatusIcon(status));
  top.appendChild(mark);
  top.appendChild(h('span', { class: 'node-label' }, stage.label || stage.id || '단계'));
  const ms = Number(stage.ms || 0);
  if (ms > 0) top.appendChild(h('span', { class: 'node-ms' }, ms >= 1000 ? `${(ms / 1000).toFixed(1)}초` : `${ms}ms`));
  card.appendChild(top);

  if (stage.detail) card.appendChild(h('p', { class: 'node-detail' }, stage.detail));
  /* 모델명 등 기술 정보(stage.tech)는 화면에 표시하지 않는다(PMO 지시). */

  if (Array.isArray(stage.steps) && stage.steps.length) {
    const ul = h('ul', { class: 'node-steps' });
    stage.steps.forEach((s) => ul.appendChild(h('li', {}, String(s))));
    card.appendChild(ul);
  }

  const refs = Array.isArray(stage.resource_refs) ? stage.resource_refs.filter(Boolean) : [];
  if (refs.length) card.appendChild(h('span', { class: 'node-refs' }, `리소스 ${refs.length}개`));

  return card;
}

function traceTotalSeconds(trace) {
  const ms = (trace || []).reduce((sum, s) => sum + Number((s && s.ms) || 0), 0);
  return ms / 1000;
}

/* 응답이 끝나면 묶음을 한 줄 요약으로 접는다. 클릭하면 카드들이 펼쳐진다. */
function buildTraceSummary(trace, model) {
  const list = Array.isArray(trace) ? trace.filter(Boolean) : [];
  if (!list.length) return null;
  /* 모델명은 응답 배지에 이미 보이므로 요약 줄에는 단계 수와 시간만 쓴다. */
  const parts = [`${list.length}단계로 생각했어요`, `${traceTotalSeconds(list).toFixed(1)}초`];

  const details = h('details', { class: 'node-group is-collapsed' });
  details.appendChild(h('summary', { class: 'node-group-summary' }, parts.join(' · ')));
  const body = h('div', { class: 'node-cards' });
  list.forEach((s) => body.appendChild(buildNodeCard(s)));
  details.appendChild(body);
  return details;
}

/* 스트리밍 중에는 stage 이벤트마다 카드를 다시 그린다. */
function buildLiveNodeGroup() {
  const wrap = h('div', { class: 'node-group is-live' });
  const head = h('div', { class: 'node-group-head' });
  head.appendChild(h('span', { class: 'node-group-title' }, '생각하는 중'));
  const countEl = h('span', { class: 'node-group-count' }, '0단계');
  head.appendChild(countEl);
  wrap.appendChild(head);
  const cards = h('div', { class: 'node-cards', role: 'status', 'aria-live': 'polite' });
  wrap.appendChild(cards);

  const order = [];
  const byId = Object.create(null);

  const redraw = () => {
    clearNode(cards);
    order.forEach((id) => cards.appendChild(buildNodeCard(byId[id])));
    countEl.textContent = `${order.length}단계`;
  };

  return {
    node: wrap,
    isEmpty: () => order.length === 0,
    stages: () => order.map((id) => byId[id]),
    push: (ev) => {
      if (!ev || !ev.id) return;
      if (!byId[ev.id]) { byId[ev.id] = {}; order.push(ev.id); }
      Object.keys(ev).forEach((k) => {
        const v = ev[k];
        if (v === '' && byId[ev.id][k]) return;
        byId[ev.id][k] = v;
      });
      redraw();
    },
  };
}

/* ---- 13-4. 리소스 (SPEC 2.11) ---- */

function groupResources(list) {
  const seen = Object.create(null);
  const order = [];
  (Array.isArray(list) ? list : []).forEach((r) => {
    if (!r || !r.kind) return;
    if (!seen[r.kind]) { seen[r.kind] = []; order.push(r.kind); }
    seen[r.kind].push(r);
  });
  const known = RESOURCE_KIND_ORDER.filter((k) => seen[k]);
  const extra = order.filter((k) => RESOURCE_KIND_ORDER.indexOf(k) === -1);
  return known.concat(extra).map((k) => ({ kind: k, items: seen[k] }));
}

function resourceCount(list) {
  return (Array.isArray(list) ? list : []).filter(Boolean).length;
}

/* 리소스를 클릭했을 때 여는 화면. 열 곳이 없으면 null 을 돌려준다. */
function resourceOpenAction(r) {
  const kind = r.kind;
  if (kind === 'kb') {
    const slug = String(r.ref || '').split('#')[0];
    if (!slug) return null;
    return () => openKbPanel(slug, r.title);
  }
  if (kind === 'calc') return () => navigateTo('decisions');
  if (kind === 'loan' || kind === 'profile') return () => navigateTo('debts');
  if (kind === 'products') return () => navigateTo('compare');
  return null;
}

function resourceItemNode(r) {
  const li = h('li', { class: `resource-item kind-${r.kind || 'other'}` });
  const openFn = resourceOpenAction(r);
  const inner = openFn
    ? h('button', { type: 'button', class: 'resource-open', 'aria-label': `${r.title || '자료'} 열기`, onClick: openFn })
    : h('div', { class: 'resource-open is-static' });

  inner.appendChild(h('span', { class: 'resource-title' }, r.title || '자료'));
  if (r.detail) inner.appendChild(h('span', { class: 'resource-detail' }, r.detail));

  const meta = h('span', { class: 'resource-meta' });
  if (r.verified_at) meta.appendChild(h('span', { class: 'resource-date' }, `확인 ${r.verified_at}`));
  if (r.needs_verification) meta.appendChild(badge('(확인 필요)', 'badge-estimated estimated-tag'));
  if (meta.childNodes.length) inner.appendChild(meta);
  li.appendChild(inner);

  if (typeof r.url === 'string' && /^https?:\/\//i.test(r.url)) {
    const link = h('a', {
      class: 'resource-link', href: r.url, target: '_blank', rel: 'noopener noreferrer',
      'aria-label': `${r.title || '자료'} 새 창으로 열기`,
    }, '새 창에서 열기');
    link.appendChild(icon('external', 12));
    li.appendChild(link);
  }
  return li;
}

function buildResourceGroups(list) {
  const frag = document.createDocumentFragment();
  groupResources(list).forEach((g) => {
    const box = h('div', { class: 'resource-group' });
    box.appendChild(h('div', { class: 'resource-group-head' },
      h('span', { class: 'resource-group-title' }, RESOURCE_KIND_LABELS[g.kind] || '자료'),
      h('span', { class: 'resource-group-count' }, `${g.items.length}개`)));
    const ul = h('ul', { class: 'resource-list' });
    g.items.forEach((r) => ul.appendChild(resourceItemNode(r)));
    box.appendChild(ul);
    frag.appendChild(box);
  });
  return frag;
}

/* 오른쪽 고정 열. 리소스가 없으면 열 자체를 숨긴다. */
function renderResourcePanel() {
  const col = document.getElementById('chatResourceCol');
  const body = document.getElementById('chatResourceBody');
  const countEl = document.getElementById('chatResourceCount');
  if (!col || !body) return;
  const list = state.chat.resources || [];
  const n = resourceCount(list);
  col.classList.toggle('is-hidden', n === 0);
  if (countEl) countEl.textContent = `${n}개`;
  clearNode(body);
  if (n) body.appendChild(buildResourceGroups(list));
}

/* 각 응답 말풍선 아래의 접이식 "리소스 N개" (좁은 화면에서 패널 대신 쓰인다). */
function buildResourceToggle(list) {
  const n = resourceCount(list);
  if (!n) return null;
  const details = h('details', { class: 'resource-inline' });
  details.appendChild(h('summary', {}, `리소스 ${n}개`));
  details.appendChild(buildResourceGroups(list));
  return details;
}

/* ---- 13-5. 인라인 액션 카드 (자동 화면 이동 없음) ---- */

const PREPARE_FIELD_LABELS = {
  category: '카테고리', amount: '금액', term_months: '기간', repay_method: '상환 방식',
  credit_band: '신용 구간', sort_key: '정렬 기준', target_loan_id: '대상 대출',
  rate_type: '금리 유형', max_rate: '금리 상한',
};

function prepareRow(key, value, estimated) {
  const row = h('div', { class: 'prep-row' });
  const label = h('span', { class: 'prep-label' }, PREPARE_FIELD_LABELS[key] || key);
  if (estimated) label.appendChild(badge('추정', 'badge-estimated estimated-tag'));
  row.appendChild(label);
  row.appendChild(h('span', { class: 'prep-value' }, value));
  return row;
}

function buildPrepareCompareCard(params) {
  const p = params || {};
  const est = Array.isArray(p.estimated_fields) ? p.estimated_fields : [];
  const card = h('div', { class: 'inline-action-card' });
  card.appendChild(h('div', { class: 'inline-action-head' },
    h('span', { class: 'inline-action-kicker' }, '공시 비교 조건'),
    h('span', { class: 'inline-action-title' }, '이 조건으로 나란히 비교할 수 있어요')));

  const rows = h('div', { class: 'prep-rows' });
  rows.appendChild(prepareRow('category', CATEGORY_LABELS[p.category] || p.category || '-', est.indexOf('category') >= 0));
  rows.appendChild(prepareRow('amount', fmtWon(p.amount), est.indexOf('amount') >= 0));
  rows.appendChild(prepareRow('term_months', fmtMonths(p.term_months), est.indexOf('term_months') >= 0));
  rows.appendChild(prepareRow('repay_method', REPAY_METHOD_LABELS[p.repay_method] || p.repay_method || '-', est.indexOf('repay_method') >= 0));
  if (p.credit_band) rows.appendChild(prepareRow('credit_band', String(p.credit_band), est.indexOf('credit_band') >= 0));
  card.appendChild(rows);

  card.appendChild(h('div', { class: 'inline-action-foot' }, h('button', {
    type: 'button', class: 'btn btn-primary btn-sm', 'aria-label': '조건 확인하고 비교하기',
    onClick: () => goToCompareWithPrepare(p),
  }, '조건 확인하고 비교하기')));
  return card;
}

function buildOpenViewCard(payload) {
  const raw = payload.view || payload.name || payload.target || (typeof payload === 'string' ? payload : '');
  const view = String(raw || '').replace('#', '');
  if (!view) return null;
  const label = OPEN_VIEW_LABELS[view] || '화면 열기';
  const card = h('div', { class: 'inline-action-card is-compact' });
  card.appendChild(h('button', {
    type: 'button', class: 'btn btn-secondary btn-sm', 'aria-label': label,
    onClick: () => navigateTo(view),
  }, label));
  return card;
}

function buildInlineActionCard(action) {
  if (!action || !action.type) return null;
  const payload = action.payload || {};
  if (action.type === 'prepare_compare') return buildPrepareCompareCard(payload.params || payload);
  if (action.type === 'open_view') return buildOpenViewCard(payload);
  if (action.type === 'search_suggestions') return buildSearchSuggestionsCard(payload);
  return null;  // open_kb 는 본문 자체가 제도 안내 카드다
}

/* Google 검색 그라운딩을 쓰면 Google이 준 검색 제안 마크업을 그대로 보여줘야 한다(이용약관).
   서버가 준 HTML은 우리 DOM에 직접 넣지 않고 스크립트가 막힌 iframe(srcdoc)에 격리해 그린다.
   링크는 새 창으로만 열린다(allow-popups). */
function buildSearchSuggestionsCard(payload) {
  const html = payload && typeof payload.html === 'string' ? payload.html : '';
  if (!html.trim()) return null;
  const card = h('div', { class: 'inline-action-card search-suggestions-card' });
  card.appendChild(h('div', { class: 'inline-action-title' }, 'Google 검색 제안'));
  const frame = document.createElement('iframe');
  frame.className = 'search-suggestions-frame';
  frame.setAttribute('sandbox', 'allow-popups allow-popups-to-escape-sandbox');
  frame.setAttribute('referrerpolicy', 'no-referrer');
  frame.setAttribute('title', 'Google 검색 제안');
  frame.setAttribute('loading', 'lazy');
  frame.srcdoc = '<!doctype html><meta charset="utf-8"><base target="_blank">'
    + '<style>html,body{margin:0;padding:0;background:transparent;font-family:Pretendard,system-ui,sans-serif}</style>'
    + html;
  card.appendChild(frame);
  return card;
}

/* ---- 13-6. 제도 안내(KB) 카드 ---- */

/* 본문에 섞여 오는 주의·면책 문장. 본문이 아니라 작은 회색 노트 줄로 뺀다. */
const KB_NOTE_MARKERS = ['확인이 필요한 항목', '제도 설명은 참고용', '참고용이며'];
const KB_SENTENCE_RE = /[^.!?]+[.!?]+|[^.!?]+$/g;

/* text 형식 응답을 (요약, 핵심 목록, 노트)로 나눈다. markdown 은 렌더러가 처리한다. */
function splitKbBody(text) {
  const lines = String(text || '').replace(/\r\n/g, '\n').split('\n');
  const notes = [];
  const bullets = [];
  const sentences = [];

  lines.forEach((raw) => {
    const line = raw.trim();
    if (!line) return;
    if (/^[-*]\s+/.test(line)) { bullets.push(line.replace(/^[-*]\s+/, '')); return; }
    (line.match(KB_SENTENCE_RE) || [line]).forEach((s) => {
      const st = s.trim();
      if (!st) return;
      if (KB_NOTE_MARKERS.some((mk) => st.indexOf(mk) >= 0)) notes.push(st);
      else sentences.push(st);
    });
  });

  /* "OO 안내입니다."처럼 제목을 되풀이하는 첫 문장은 카드 제목과 겹치므로 뺀다. */
  if (sentences.length > 1 && /안내입니다\.?$/.test(sentences[0])) sentences.shift();

  const lead = sentences.length ? sentences[0] : '';
  const rest = sentences.slice(1);
  const points = bullets.length ? bullets.slice(0, 3) : rest.slice(0, 3);
  return { lead, points, notes };
}

function kbSourceLine(source) {
  const line = h('p', { class: 'kb-source-line' });
  line.appendChild(h('span', { class: 'kb-source-prefix' }, '출처: '));
  const title = source.title || source.url || '출처';
  if (source.url && /^https?:\/\//i.test(source.url)) {
    line.appendChild(h('a', {
      href: source.url, target: '_blank', rel: 'noopener noreferrer',
      'aria-label': `${title} 새 창으로 열기`,
    }, title));
  } else {
    line.appendChild(h('span', {}, title));
  }
  if (source.accessed) line.appendChild(h('span', { class: 'kb-source-date' }, ` · 확인 ${source.accessed}`));
  return line;
}

function buildKbReplyCard(msg) {
  const payload = (msg.action && msg.action.payload) || {};
  const card = h('div', { class: 'kb-card' });

  const head = h('div', { class: 'kb-card-head' });
  head.appendChild(h('span', { class: 'kb-card-kicker' }, '제도 안내'));
  head.appendChild(h('span', { class: 'kb-card-title' }, payload.title || '제도 안내'));
  card.appendChild(head);

  if (msg.answer_format === 'markdown') {
    const body = h('div', { class: 'answer-body is-markdown kb-card-md' });
    body.appendChild(renderMarkdownLite(msg.text || ''));
    card.appendChild(body);
  } else {
    const parts = splitKbBody(msg.text || '');
    if (parts.lead) card.appendChild(h('p', { class: 'kb-card-lead' }, parts.lead));
    if (parts.points.length) {
      const ul = h('ul', { class: 'kb-card-points' });
      parts.points.forEach((p) => ul.appendChild(h('li', {}, p)));
      card.appendChild(ul);
    }
    parts.notes.forEach((n) => card.appendChild(h('p', { class: 'kb-card-note' }, n)));
  }

  const sources = Array.isArray(payload.sources) ? payload.sources.filter(Boolean) : [];
  sources.forEach((s) => card.appendChild(kbSourceLine(s)));

  if (payload.slug) {
    card.appendChild(h('div', { class: 'kb-card-actions' }, h('button', {
      type: 'button', class: 'btn btn-secondary btn-sm',
      'aria-label': `${payload.title || '제도 안내'} 자세히 보기`,
      onClick: () => openKbPanel(payload.slug, payload.title),
    }, '자세히 보기')));
  }
  return card;
}

/* ---- 13-7. 말풍선 ---- */

function buildPendingRow(text) {
  const dots = h('span', { class: 'chat-pending-dots', 'aria-hidden': 'true' }, h('i', {}), h('i', {}), h('i', {}));
  return h('div', { class: 'chat-pending', role: 'status', 'aria-live': 'polite' },
    dots, h('span', {}, text || '응답을 쓰는 중'));
}

function replyBadgeRow(msg) {
  const row = h('div', { class: 'chat-reply-meta' });
  if (msg.llm_used) {
    row.appendChild(badge('AI 응답', 'badge-accent'));
  } else {
    row.appendChild(badge('규칙 기반 응답', 'badge-rule'));
  }
  if (msg.route === 'external') row.appendChild(badge('외부 검색 요약(확인 필요)', 'badge-estimated'));
  if (msg.route === 'safety') row.appendChild(badge('안전 안내', 'badge-safe'));
  return row;
}

/* 응답 한 건의 본문을 row 에 붙인다. typing 이면 타자 효과로 문장을 드러낸다. */
function appendReplyBody(row, msg, opts) {
  const typing = !!(opts && opts.typing);
  row.appendChild(replyBadgeRow(msg));

  const isKb = !!(msg.action && msg.action.type === 'open_kb');
  let bodyEl;
  if (isKb) {
    bodyEl = buildKbReplyCard(msg);
  } else {
    bodyEl = h('div', { class: 'chat-bubble reply' });
    bodyEl.appendChild(buildAnswerBody(msg.text || '', msg.answer_format));
  }
  row.appendChild(bodyEl);
  if (typing) typeIntoElement(bodyEl);

  const resToggle = buildResourceToggle(msg.resources);
  if (resToggle) row.appendChild(resToggle);

  const actionCard = buildInlineActionCard(msg.action);
  if (actionCard) row.appendChild(actionCard);

  if (msg.chips && msg.chips.length) row.appendChild(renderChipRow(msg.chips));
}

function buildReplyRow(msg, opts) {
  const row = h('div', { class: 'chat-bubble-row from-reply' });
  const summary = buildTraceSummary(msg.trace, msg.model);
  if (summary) row.appendChild(summary);
  appendReplyBody(row, msg, opts);
  return row;
}

function renderChatTranscript(scrollToEnd) {
  const wrap = document.getElementById('chatTranscriptWrap');
  if (!wrap) return;
  clearNode(wrap);

  if (!state.chat.messages.length && !state.chat.pending) {
    wrap.appendChild(h('p', { class: 'chat-empty' },
      '아래 입력창에 궁금한 점을 적어주세요. 계산은 엔진이 하고 설명만 AI가 씁니다.'));
  }

  state.chat.messages.forEach((msg) => {
    if (msg.role === 'user') {
      wrap.appendChild(h('div', { class: 'chat-bubble-row from-user' }, h('div', { class: 'chat-bubble user' }, msg.text)));
    } else if (msg.role === 'attach-map') {
      wrap.appendChild(h('div', { class: 'chat-bubble-row from-reply' }, buildAttachMappingCard(msg)));
    } else if (msg.role === 'reply') {
      wrap.appendChild(buildReplyRow(msg, { typing: false }));
    } else {
      wrap.appendChild(h('div', { class: 'chat-bubble-row from-reply' }, h('div', { class: 'chat-bubble error' }, msg.text)));
    }
  });

  /* 응답을 받는 중에 다른 화면에 갔다 돌아오면 진행 중인 줄을 그대로 다시 붙인다. */
  if (state.chat.pending && state.chat.liveRow) wrap.appendChild(state.chat.liveRow);

  if (scrollToEnd) scrollChatToEnd();
}

function scrollChatToEnd() {
  const box = document.getElementById('chatTranscriptWrap') || document.getElementById('mainContent');
  if (!box) return;
  try { box.scrollTop = box.scrollHeight; } catch (_) { /* noop */ }
}

/* ---- 13-8. 전송과 스트리밍 ---- */

function setChatPending(pending) {
  state.chat.pending = pending;
  applyChatPendingToInputs(document);
}

function sendChatMessage(rawText) {
  const text = (rawText || '').trim();
  if (!text || state.chat.pending) return;
  state.chat.messages.push({ role: 'user', text });
  state.chat.queuedSend = text;
  setChatPending(true);

  if (currentRouteFromHash() !== 'chat') {
    navigateTo('chat');  // renderChat 이 그린 뒤 이어서 보낸다
    return;
  }
  renderChatTranscript(true);
  flushQueuedChatSend();
}

function flushQueuedChatSend() {
  const text = state.chat.queuedSend;
  if (!text) return;
  state.chat.queuedSend = null;
  performChatSend(text);
}

async function performChatSend(text) {
  const seq = state.chat.streamSeq + 1;
  state.chat.streamSeq = seq;

  const wrap = document.getElementById('chatTranscriptWrap');
  const row = h('div', { class: 'chat-bubble-row from-reply is-live' });
  const group = buildLiveNodeGroup();
  const bodySlot = h('div', { class: 'live-body-slot' });
  bodySlot.appendChild(buildPendingRow());
  row.appendChild(group.node);
  row.appendChild(bodySlot);
  state.chat.liveRow = row;
  if (wrap) { wrap.appendChild(row); scrollChatToEnd(); }

  let reply = null;
  let streamed = false;
  streamed = await streamChatRequest(text, state.chat.chatId, {
    onStage: (s) => {
      if (seq !== state.chat.streamSeq) return;
      group.push(s);
      scrollChatToEnd();
    },
    onReply: (r) => { reply = r; },
    onError: (e) => { console.debug('[DONN] 대화 스트림 오류', (e && e.message) || e); },
    isStale: () => seq !== state.chat.streamSeq,
  });
  if (seq !== state.chat.streamSeq) return;

  if (!streamed || !reply) {
    /* 스트림 실패: 같은 요청을 일반 엔드포인트로 한 번 더 보내고 생각 과정 없이 그린다. */
    const res = await Api.chat(text, state.chat.chatId);
    if (seq !== state.chat.streamSeq) return;
    if (!res.ok) {
      if (row.parentNode) row.parentNode.removeChild(row);
      state.chat.liveRow = null;
      state.chat.messages.push({ role: 'error', text: '응답을 받지 못했습니다. 잠시 후 다시 시도해주세요.' });
      setChatPending(false);
      renderChatTranscript(true);
      return;
    }
    reply = res.data || {};
    clearNode(group.node);
  }

  finishChatReply(row, group, reply);
}

/* ---- 13-8-1. 거래내역 파일 첨부 (SPEC 2.12) ----
   입력 카드의 "+" 로 고른 파일을 소비 패턴 화면과 같은 파서·열 자동 매핑으로 읽는다.
   원본 파일은 서버로 보내지 않고 정규화한 거래 행만 POST /api/chat/attach 로 보낸다. */

const CHAT_ATTACH_FAIL_TEXT = '파일을 분석하지 못했어요. 소비 패턴 화면에서 다시 시도해 주세요.';

function attachTransactionFile(file) {
  if (!file || state.chat.pending) return;
  state.chat.queuedAttach = file;
  setChatPending(true);

  /* 큰 파일은 읽는 데 시간이 걸리므로 먼저 자리표시를 세운다. */
  const row = h('div', { class: 'chat-bubble-row from-reply is-live' });
  row.appendChild(buildPendingRow('파일을 읽는 중이에요'));
  state.chat.liveRow = row;

  if (currentRouteFromHash() !== 'chat') {
    navigateTo('chat');  // renderChat 이 그린 뒤 이어서 보낸다
    return;
  }
  renderChatTranscript(true);
  flushQueuedChatAttach();
}

function flushQueuedChatAttach() {
  const file = state.chat.queuedAttach;
  if (!file) return;
  state.chat.queuedAttach = null;
  performChatAttach(file);
}

/* 오류 말풍선 한 줄로 끝낸다(진행 중이던 자리표시는 지운다). */
function failChatAttach(row, text) {
  if (row && row.parentNode) row.parentNode.removeChild(row);
  state.chat.liveRow = null;
  state.chat.messages.push({ role: 'error', text });
  setChatPending(false);
  renderChatTranscript(true);
}

/* 정규화한 행에서 첫 날짜와 끝 날짜를 찾는다(파일이 날짜순이 아닐 수 있다). */
function transactionDateRange(rows) {
  let first = null;
  let last = null;
  (rows || []).forEach((r) => {
    const d = r && r.date;
    if (!d) return;
    if (first === null || d < first) first = d;
    if (last === null || d > last) last = d;
  });
  return { first, last };
}

function attachBubbleText(fileName, rows) {
  const range = transactionDateRange(rows);
  const count = `${rows.length.toLocaleString('ko-KR')}행`;
  const period = range.first && range.last ? `, ${range.first}~${range.last}` : '';
  return `파일 첨부: ${fileName} (${count}${period})`;
}

async function performChatAttach(file) {
  const seq = state.chat.streamSeq + 1;
  state.chat.streamSeq = seq;  // 진행 중이던 다른 요청 결과는 버린다
  const row = state.chat.liveRow;
  const fileName = String((file && file.name) || '파일');

  const read = await readTransactionFileMatrix(file);
  if (seq !== state.chat.streamSeq) return;
  if (read.error) { failChatAttach(row, read.error); return; }

  const upload = buildUploadState(fileName, read.matrix);
  const parsed = uploadMappingReady(upload) ? normalizeUploadRows(upload) : { rows: [] };

  if (!uploadMappingReady(upload) || !parsed.rows.length) {
    /* 자동 매핑 실패: 파싱 결과를 넘겨받은 소비 패턴 화면에서 열을 직접 고르게 한다. */
    const mapped = uploadMappingReady(upload);
    upload.notice = mapped
      ? '보낼 수 있는 거래 행을 찾지 못했습니다. 아래에서 열을 직접 지정해주세요.'
      : '날짜와 금액 열을 자동으로 찾지 못했습니다. 아래에서 열을 직접 지정해주세요.';
    upload.noticeError = false;
    if (row && row.parentNode) row.parentNode.removeChild(row);
    state.chat.liveRow = null;
    state.chat.messages.push({
      role: 'attach-map',
      upload,
      text: mapped
        ? `${fileName}에서 보낼 수 있는 거래 행을 찾지 못했어요. 열을 직접 지정하면 분석할 수 있어요.`
        : `${fileName}에서 날짜와 금액 열을 자동으로 찾지 못했어요.`,
    });
    setChatPending(false);
    renderChatTranscript(true);
    return;
  }

  const rows = parsed.rows;
  state.chat.messages.push({ role: 'user', text: attachBubbleText(fileName, rows) });
  if (row) {
    clearNode(row);
    row.appendChild(buildPendingRow());
  }
  renderChatTranscript(true);

  const res = await Api.chatAttach(state.chat.chatId, fileName, rows, upload.months || 3);
  if (seq !== state.chat.streamSeq) return;
  if (!res.ok) { failChatAttach(row, CHAT_ATTACH_FAIL_TEXT); return; }

  /* 스트림이 없으므로 노드 카드는 응답의 trace 로 접힌 요약만 그린다. */
  finishChatReply(row, { stages: () => [] }, res.data || {});
}

/* 대화 스레드에 들어가는 안내 카드. 버튼을 누르면 소비 패턴 화면의 열 지정 단계로 간다. */
function buildAttachMappingCard(msg) {
  const card = h('div', { class: 'inline-action-card' });
  card.appendChild(h('div', { class: 'inline-action-head' },
    h('span', { class: 'inline-action-kicker' }, '첨부 파일'),
    h('span', { class: 'inline-action-title' }, '열을 지정해야 해요')));
  card.appendChild(h('p', { class: 'inline-action-text' }, msg.text));
  card.appendChild(h('div', { class: 'inline-action-foot' }, h('button', {
    type: 'button', class: 'btn btn-primary btn-sm', 'aria-label': '소비 패턴 화면에서 열 지정하기',
    onClick: () => {
      state.spending.upload = msg.upload;
      state.spending.focusMapping = true;
      navigateTo('spending');
    },
  }, '소비 패턴 화면에서 열 지정하기')));
  return card;
}

function finishChatReply(row, group, reply) {
  if (reply.chat_id) state.chat.chatId = reply.chat_id;

  const msg = {
    role: 'reply',
    text: reply.reply_text || '',
    llm_used: !!reply.llm_used,
    model: reply.model || null,
    route: reply.route || 'internal',
    answer_format: reply.answer_format === 'markdown' ? 'markdown' : 'text',
    chips: reply.chips || [],
    action: reply.action || null,
    trace: Array.isArray(reply.trace) && reply.trace.length ? reply.trace : group.stages(),
    resources: Array.isArray(reply.resources) ? reply.resources : [],
  };
  state.chat.messages.push(msg);
  state.chat.liveRow = null;
  state.chat.resources = msg.resources;
  setChatPending(false);

  if (!document.body.contains(row)) {
    /* 응답이 오는 동안 다른 화면에 가 있었다면 전체를 다시 그린다(타자 효과 없음). */
    renderChatTranscript(true);
  } else {
    clearNode(row);
    row.classList.remove('is-live');
    const summary = buildTraceSummary(msg.trace, msg.model);
    if (summary) row.appendChild(summary);
    appendReplyBody(row, msg, { typing: true });
  }

  renderResourcePanel();
  scrollChatToEnd();

  refreshRecentChats().then(syncChatTitleFromRecent);
}

/* ---- 13-9. 화면 ---- */

function chatTitleText() {
  return state.chat.title || CHAT_NEW_TITLE;
}

/* 대화 제목은 서버가 만든 것(마스킹본 앞 30자)을 쓴다. */
function syncChatTitleFromRecent() {
  const id = state.chat.chatId;
  if (!id) return;
  const hit = (state.recentChats || []).find((c) => c && c.id === id);
  if (!hit || !hit.title) return;
  state.chat.title = hit.title;
  const el = document.getElementById('chatHeadTitle');
  if (el) el.textContent = hit.title;
}

function buildChatHeader() {
  const head = h('div', { class: 'chat-head' });
  const back = h('button', {
    type: 'button', class: 'back-btn', 'aria-label': '뒤로 가기',
    onClick: () => navigateTo('home'),
  });
  back.appendChild(icon('chevronLeft', 16));
  back.appendChild(h('span', {}, '뒤로'));
  head.appendChild(back);

  head.appendChild(h('h1', { class: 'chat-head-title', id: 'chatHeadTitle' }, chatTitleText()));

  const right = h('div', { class: 'chat-head-right' });
  right.appendChild(h('span', { class: 'avatar chat-head-avatar', 'aria-hidden': 'true' }, profileInitial()));
  head.appendChild(right);
  return head;
}

function buildResourceColumn() {
  const col = h('aside', { class: 'chat-col-side', id: 'chatResourceCol', 'aria-label': '리소스' });
  const panel = h('div', { class: 'resource-panel' });
  panel.appendChild(h('div', { class: 'resource-panel-head' },
    h('span', { class: 'resource-panel-title' }, '리소스'),
    h('span', { class: 'resource-panel-count', id: 'chatResourceCount' }, '0개')));
  panel.appendChild(h('div', { class: 'resource-panel-body', id: 'chatResourceBody' }));
  col.appendChild(panel);
  return col;
}

function renderChat() {
  const { root } = mountView('chat');
  focusMainAfterRender();

  const shell = h('div', { class: 'chat-shell' });
  const main = h('div', { class: 'chat-col-main' });
  main.appendChild(buildChatHeader());
  main.appendChild(h('div', { class: 'chat-thread chat-transcript', id: 'chatTranscriptWrap' }));

  const composer = h('div', { class: 'chat-composer' });
  composer.appendChild(buildChatInputCard().node);
  composer.appendChild(h('p', { class: 'chat-composer-notice' },
    (state.home && state.home.ai_notice) || FALLBACK_AI_NOTICE));
  main.appendChild(composer);

  shell.appendChild(main);
  shell.appendChild(buildResourceColumn());
  root.appendChild(shell);

  renderChatTranscript(false);
  renderResourcePanel();
  scrollChatToEnd();
  flushQueuedChatSend();
  flushQueuedChatAttach();
}

/* 사이드바 "최근"에서 고른 대화를 대화 화면으로 불러온다. */
async function openChat(chatId) {
  if (!chatId) return;
  state.chat.streamSeq += 1;  // 진행 중이던 스트림 결과는 버린다
  const hit = (state.recentChats || []).find((c) => c && c.id === chatId);
  state.chat.title = (hit && hit.title) || '';

  const res = await Api.chatMessages(chatId);
  if (!res.ok) {
    state.chat.chatId = null;
    state.chat.messages = [{ role: 'error', text: '대화를 불러오지 못했습니다.' }];
    state.chat.resources = [];
  } else {
    state.chat.chatId = chatId;
    state.chat.messages = (Array.isArray(res.data) ? res.data : []).map((m) => ({
      role: m.role === 'user' ? 'user' : 'reply',
      text: m.text || '',
      llm_used: !!m.llm_used,
      model: m.model || null,
      route: m.route || 'internal',
      answer_format: m.answer_format === 'markdown' ? 'markdown' : 'text',
      chips: m.chips || [],
      action: m.action || null,
      trace: Array.isArray(m.trace) ? m.trace : [],
      resources: Array.isArray(m.resources) ? m.resources : [],
    }));
    const lastReply = state.chat.messages.filter((m) => m.role === 'reply').pop();
    state.chat.resources = (lastReply && lastReply.resources) || [];
  }
  state.chat.pending = false;
  state.chat.queuedSend = null;
  state.chat.queuedAttach = null;
  state.chat.liveRow = null;
  navigateTo('chat');
  renderChatTranscript(true);
  renderResourcePanel();
  renderRecentList();
}

function startNewChat() {
  state.chat.streamSeq += 1;
  state.chat.messages = [];
  state.chat.pending = false;
  state.chat.chatId = null;  // 서버가 첫 메시지에서 새 대화를 만든다
  state.chat.title = '';
  state.chat.resources = [];
  state.chat.queuedSend = null;
  state.chat.queuedAttach = null;
  state.chat.liveRow = null;
  navigateTo('home');
  renderRecentList();
  setTimeout(() => { const el = document.getElementById('chatInput'); if (el) el.focus(); }, 0);
}

async function handleChipClick(chip) {
  if (!chip) return;
  if (chip.intent === 'faq' && chip.params && chip.params.slug) {
    openKbPanel(chip.params.slug, chip.params.title || chip.text);
    return;
  }
  switch (chip.intent) {
    case 'compare':
      goToCompareWithPrepare(chip.params || {});
      break;
    case 'schedule':
    case 'scenario':
      state.debts.pendingFocusLoanId = (chip.params && (chip.params.target_loan_id || chip.params.loan_id)) || null;
      navigateTo('debts');
      break;
    case 'spending':
      navigateTo('spending');
      break;
    case 'lifecycle':
    case 'retirement':
    case 'saving':
    case 'liquidity':
      navigateTo('lifecycle');
      break;
    case 'onboarding':
      navigateTo(state.profile ? 'debts' : 'personas');
      break;
    default:
      sendChatMessage(chip.text);
      break;
  }
}

function goToCompareWithPrepare(params) {
  state.compare.queuedPrepareParams = params || {};
  state.compare.context = null;
  state.compare.result = null;
  state.compare.step = 1;
  state.compare.explain = { decisionId: null, status: 'idle', data: null };
  navigateTo('compare');
}

/* 제도 안내 패널과 결정 기록에서 쓰는 출처 목록(대화 카드는 kbSourceLine 을 쓴다). */
function kbSourceList(sources, cls) {
  const list = Array.isArray(sources) ? sources.filter(Boolean) : [];
  if (!list.length) return null;
  const ul = h('ul', { class: cls || 'kb-source-list' });
  list.forEach((s) => {
    const li = h('li', {});
    const title = s.title || s.url || '출처';
    if (s.url && /^https?:\/\//i.test(s.url)) {
      li.appendChild(h('a', { href: s.url, target: '_blank', rel: 'noopener noreferrer' }, title));
    } else {
      li.appendChild(h('span', {}, title));
    }
    if (s.accessed) li.appendChild(h('span', { class: 'kb-source-date' }, `확인 ${s.accessed}`));
    ul.appendChild(li);
  });
  return ul;
}

/* ---------- 13-2. 제도 안내 패널 (KB) ---------- */

/* 섹션 본문: "- "로 시작하는 줄은 목록으로, 나머지는 문단으로 그린다. */
function buildKbSectionBody(text) {
  const nodes = [];
  const lines = String(text || '').split('\n');
  let bullets = null;
  const flush = () => { if (bullets) { nodes.push(bullets); bullets = null; } };
  lines.forEach((rawLine) => {
    const line = rawLine.trim();
    if (!line) { flush(); return; }
    if (line.startsWith('- ')) {
      if (!bullets) bullets = h('ul', { class: 'kb-bullets' });
      bullets.appendChild(h('li', {}, line.slice(2).trim()));
      return;
    }
    flush();
    nodes.push(h('p', { class: 'kb-para' }, line));
  });
  flush();
  return nodes;
}

function onKbPanelKeydown(e) {
  if (e.key === 'Escape') closeKbPanel();
}

function closeKbPanel() {
  const panel = document.getElementById('kbPanel');
  if (!panel) return;
  panel.classList.add('is-hidden');
  panel.setAttribute('aria-hidden', 'true');
  document.removeEventListener('keydown', onKbPanelKeydown);
  const opener = state.kbOpener;
  state.kbOpener = null;
  if (opener && document.contains(opener)) { try { opener.focus(); } catch (_) { /* noop */ } }
}

async function openKbPanel(slug, title) {
  const panel = document.getElementById('kbPanel');
  const body = document.getElementById('kbPanelBody');
  const titleEl = document.getElementById('kbPanelTitle');
  if (!panel || !body) return;

  state.kbOpener = document.activeElement && document.activeElement.focus ? document.activeElement : null;
  panel.classList.remove('is-hidden');
  panel.setAttribute('aria-hidden', 'false');
  document.addEventListener('keydown', onKbPanelKeydown);
  if (titleEl) titleEl.textContent = title || '제도 안내';
  clearNode(body);
  body.appendChild(h('p', { class: 'loading-text' }, '불러오는 중...'));
  const closeBtn = document.getElementById('kbPanelCloseBtn');
  if (closeBtn) closeBtn.focus();

  const res = await Api.kbDoc(slug);
  if (panel.classList.contains('is-hidden')) return;
  clearNode(body);
  if (!res.ok) {
    body.appendChild(noticeBox('제도 안내를 불러오지 못했습니다.', { error: true, onRetry: () => openKbPanel(slug, title) }));
    return;
  }
  const doc = res.data || {};
  if (titleEl) titleEl.textContent = doc.title || title || '제도 안내';

  const metaRow = h('div', { class: 'kb-meta-row' });
  if (doc.needs_verification) metaRow.appendChild(badge('(확인 필요)', 'badge-estimated'));
  if (doc.verified_at) metaRow.appendChild(h('span', { class: 'kb-meta-date' }, `확인일 ${doc.verified_at}`));
  if (metaRow.childNodes.length) body.appendChild(metaRow);

  const sections = doc.sections && typeof doc.sections === 'object' ? doc.sections : {};
  Object.keys(sections).forEach((heading) => {
    body.appendChild(h('h3', { class: 'kb-section-title' }, heading));
    buildKbSectionBody(sections[heading]).forEach((n) => body.appendChild(n));
  });
  if (!Object.keys(sections).length) {
    body.appendChild(h('p', { class: 'empty-text' }, '표시할 내용이 없습니다.'));
  }

  const sources = kbSourceList(doc.sources, 'kb-source-list kb-source-list-panel');
  if (sources) {
    body.appendChild(h('h3', { class: 'kb-section-title' }, '출처'));
    body.appendChild(sources);
  }
  body.appendChild(h('p', { class: 'kb-disclaimer' }, KB_DISCLAIMER));
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
  /* 두 줄 모두 화면에서 전체가 보이므로(styles.css .footer-line) title 툴팁은 두지 않는다. */
  if (disclaimerEl) disclaimerEl.textContent = disclaimer;
  if (noticeEl) noticeEl.textContent = notice;
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

function profileInitial() {
  const name = state.profile && state.profile.display_name ? String(state.profile.display_name).trim() : '';
  return name ? Array.from(name)[0] : '?';
}

/* 상단 바 아바타와 사이드바 계정 카드는 항상 현재 페르소나를 그대로 비춘다. */
function renderAccountUi() {
  const name = state.profile && state.profile.display_name ? String(state.profile.display_name) : '';
  const initial = profileInitial();

  const avatarBtn = document.getElementById('avatarBtn');
  if (avatarBtn) {
    avatarBtn.textContent = initial;
    avatarBtn.setAttribute('title', name || '계정을 선택하세요');
    avatarBtn.setAttribute('aria-label', name ? `${name} 계정, 계정 선택 열기` : '계정 선택 열기');
  }

  const accAvatar = document.getElementById('accountAvatar');
  const accName = document.getElementById('accountName');
  const accSub = document.getElementById('accountSub');
  if (accAvatar) accAvatar.textContent = initial;
  if (accName) accName.textContent = name || '계정을 선택하세요';
  if (accSub) {
    accSub.textContent = name ? '이 계정으로 보는 중' : '';
    accSub.classList.toggle('is-hidden', !name);
  }
  const card = document.getElementById('accountCard');
  if (card) card.classList.toggle('is-empty', !name);
}

function buildRecentChatItem(chat) {
  const title = chat.title || '새 대화';
  const li = h('li', { class: state.chat.chatId === chat.id ? 'is-current' : '' });
  const btn = h('button', {
    type: 'button', class: 'recent-open', 'aria-label': `${title} 대화 열기`,
    onClick: () => openChat(chat.id),
  });
  btn.appendChild(h('span', { class: 'recent-label' }, title));
  const meta = [timeAgo(chat.updated_at || chat.created_at)];
  if (chat.message_count !== undefined && chat.message_count !== null) meta.push(`${chat.message_count}개`);
  btn.appendChild(h('span', { class: 'recent-time' }, meta.filter(Boolean).join(' · ')));
  li.appendChild(btn);
  li.appendChild(h('button', {
    type: 'button', class: 'recent-del', 'aria-label': `${title} 대화 삭제`, title: '대화 삭제',
    onClick: async (e) => {
      e.stopPropagation();
      if (!confirm('이 대화를 삭제할까요?')) return;
      const r = await Api.deleteChat(chat.id);
      if (!r.ok) return;
      if (state.chat.chatId === chat.id) {
        state.chat.chatId = null;
        state.chat.messages = [];
        renderChatTranscript();
      }
      refreshRecentChats();
    },
  }, '×'));
  return li;
}

function renderRecentList() {
  const list = document.getElementById('recentList');
  if (!list) return;
  clearNode(list);
  const items = (state.recentChats || []).slice(0, 8);
  if (!items.length) {
    list.appendChild(h('li', { class: 'sidebar-recent-empty' }, '대화 기록이 없어요'));
    return;
  }
  items.forEach((chat) => list.appendChild(buildRecentChatItem(chat)));
}

async function loadAndSetHome() {
  const res = await Api.getHome();
  state.home = res.ok ? res.data : null;
  renderPinnedAction();
  setFooterTexts(state.home);
  return res;
}
async function refreshRecentChats() {
  const res = await Api.listChats();
  state.recentChats = res.ok && Array.isArray(res.data) ? res.data : [];
  renderRecentList();
}
async function refreshSidebarData() {
  renderAccountUi();
  await Promise.all([loadAndSetHome(), refreshRecentChats()]);
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
  const logoMark = document.getElementById('logoMark');
  if (logoMark) logoMark.appendChild(donnLogo(28));
  const newChatIcon = document.getElementById('newChatIcon');
  if (newChatIcon) newChatIcon.appendChild(icon('plus', 14));
  const hamburgerBtn = document.getElementById('hamburgerBtn');
  if (hamburgerBtn) hamburgerBtn.appendChild(icon('hamburger', 20));
  const collapseBtn = document.getElementById('sidebarCollapseBtn');
  if (collapseBtn) collapseBtn.appendChild(icon('chevronLeft', 18));
  const settingsCloseBtn = document.getElementById('settingsCloseBtn');
  if (settingsCloseBtn) settingsCloseBtn.appendChild(icon('close', 18));
  const kbCloseBtn = document.getElementById('kbPanelCloseBtn');
  if (kbCloseBtn) kbCloseBtn.appendChild(icon('close', 18));
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
  document.getElementById('kbPanelCloseBtn').addEventListener('click', closeKbPanel);
  document.getElementById('kbPanel').addEventListener('click', (e) => {
    if (e.target.id === 'kbPanel') closeKbPanel();
  });
  document.getElementById('avatarBtn').addEventListener('click', () => navigateTo('personas'));
  document.getElementById('accountCard').addEventListener('click', () => navigateTo('personas'));
  document.getElementById('accountSwitchBtn').addEventListener('click', () => navigateTo('personas'));
  document.getElementById('resourceNav').addEventListener('click', (e) => {
    const a = e.target.closest('a[data-view]');
    if (!a) return;
    e.preventDefault();
    navigateTo(a.getAttribute('data-view'));
  });
  window.addEventListener('hashchange', () => {
    syncNavDepth();
    renderCurrentView();
  });
}

async function initApp() {
  mountStaticIcons();
  wireGlobalEvents();
  applySidebarCollapsedState();
  initNavDepth();
  setFooterTexts(null);
  renderAccountUi();

  renderCurrentView();

  const profRes = await Api.getProfile();
  state.profile = profRes.ok ? profRes.data : null;

  await refreshSidebarData();
}

document.addEventListener('DOMContentLoaded', initApp);
