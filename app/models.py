"""DONN 공용 데이터 모델 (pydantic v2).

모든 모듈은 이 파일의 타입만 주고받는다. 금액은 정수 원 단위, 금리는 연 % (예: 5.2).
수치는 코드가 계산하고 LLM은 문장만 쓴다. 이 파일을 바꾸면 SPEC.md도 함께 바꾼다.
"""
from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

ENGINE_VERSION = "0.1.0"

DISCLAIMER = (
    "DONN은 금융상품 판매·중개·자문 서비스가 아닌 정보 제공·계산 서비스입니다. "
    "표시된 금리는 금융감독원 및 공공기관 공시 기준이며 실제 적용 금리·한도와 다를 수 있습니다. "
    "최종 결정은 이용자 본인의 판단으로 하며, 상품 가입은 각 금융회사 공식 채널에서 진행하세요."
)
AI_NOTICE = "이 화면의 설명 문장 일부는 생성형 AI가 작성합니다. 수치는 AI가 아닌 계산 엔진이 산출합니다."


# ---------- 사용자·부채 ----------
class RepayMethod(str, Enum):
    EQUAL_PAYMENT = "equal_payment"      # 원리금균등
    EQUAL_PRINCIPAL = "equal_principal"  # 원금균등
    BULLET = "bullet"                    # 만기일시 (매월 이자, 만기 원금)
    REVOLVING = "revolving"              # 마이너스통장·리볼빙 근사 (이자만, 잔액 유지)


class RateType(str, Enum):
    FIXED = "fixed"
    VARIABLE = "variable"


class LoanType(str, Enum):
    CREDIT = "credit"
    MORTGAGE = "mortgage"
    JEONSE = "jeonse"
    STUDENT = "student"
    CARD_LOAN = "card_loan"
    OVERDRAFT = "overdraft"
    POLICY = "policy"
    OTHER = "other"


class LenderGroup(str, Enum):
    BANK = "bank"
    SAVINGS_BANK = "savings_bank"
    CARD = "card"
    CAPITAL = "capital"
    INSURANCE = "insurance"
    POLICY = "policy"
    OTHER = "other"


class Loan(BaseModel):
    id: str
    name: str = Field(description="사용자 표시명. 화면 출력 시 M0 정책(실명 비노출) 적용")
    loan_type: LoanType
    principal: int
    balance: int
    annual_rate: float
    rate_type: RateType = RateType.FIXED
    repay_method: RepayMethod = RepayMethod.EQUAL_PAYMENT
    remaining_months: int
    grace_months: int = 0
    start_date: Optional[date] = None
    prepay_fee_rate: float = 0.0
    prepay_fee_until: Optional[date] = None
    monthly_payment_override: Optional[int] = None
    lender_group: LenderGroup = LenderGroup.BANK


class UserProfile(BaseModel):
    id: str
    display_name: str
    age: Optional[int] = None
    employment: Optional[str] = None  # employee | self_employed | student | retired | other
    monthly_income: int
    fixed_expenses: int
    variable_expenses: int
    emergency_fund: int = 0
    credit_band: Optional[str] = None  # 금감원 공시 신용점수 구간 라벨과 동일하게 유지 (확인 필요)
    loans: list[Loan] = []
    flags: list[str] = []  # delinquency_signal | income_up | job_changed | self_employed | retirement_near
    notes: Optional[str] = None


# ---------- 계산 결과 ----------
class ScheduleRow(BaseModel):
    month: int
    payment: int
    principal: int
    interest: int
    balance: int


class LoanSchedule(BaseModel):
    loan_id: str
    method: RepayMethod
    annual_rate: float
    months: int
    rows: list[ScheduleRow]
    total_payment: int
    total_interest: int
    first_payment: int
    assumptions: list[str] = []


class CapacityBand(str, Enum):
    NEGATIVE = "negative"
    TIGHT = "tight"
    OK = "ok"
    COMFORTABLE = "comfortable"


class Capacity(BaseModel):
    band: CapacityBand
    net_monthly: int
    debt_service: int
    debt_service_ratio: float
    explanation: str
    assumptions: list[str] = []


class Scenario(str, Enum):
    BASE = "base"
    ADVERSE = "adverse"
    FAVORABLE = "favorable"


class CashflowPoint(BaseModel):
    month: int
    income: int
    debt_payment: int
    expenses: int
    net: int
    total_balance: int
    cumulative_net: int


class ScenarioResult(BaseModel):
    scenario: Scenario
    label: str
    assumptions: list[str]
    points: list[CashflowPoint]
    debt_free_month: Optional[int]
    total_interest: int
    min_cumulative_net: int


# ---------- 카드·칩 ----------
class Chip(BaseModel):
    id: str
    text: str
    tier: int = 0  # 0 정적, 1 데이터 파생, 2 LLM 생성(PoC 미사용)
    intent: str    # compare | schedule | scenario | action | spending | onboarding | faq
    params: dict[str, Any] = {}


class ActionCard(BaseModel):
    id: str
    rule_id: str
    title: str
    summary: str
    numbers: dict[str, Any] = {}
    assumptions: list[str] = []
    caveats: list[str] = []
    steps: list[str] = []
    priority: int = 100
    safe_mode: bool = False
    related_loan_ids: list[str] = []
    chip: Optional[Chip] = None


class InsightCard(BaseModel):
    id: str
    kind: Literal["debt", "spending", "progress", "onboarding", "action"]
    tone: Literal["neutral", "positive", "negative"]
    title: str
    body: str
    evidence: dict[str, Any] = {}
    chip: Optional[Chip] = None
    source_rule: str = ""
    explain: str = ""  # 이 분석은 왜 나왔나요? 에 표시할 설명


class HomePayload(BaseModel):
    profile_id: Optional[str]
    cards: list[InsightCard]
    chips: list[Chip]
    capacity: Optional[Capacity]
    top_action: Optional[ActionCard]
    disclaimer: str = DISCLAIMER
    ai_notice: str = AI_NOTICE
    llm_calls: int = 0


# ---------- 상품 스냅샷 ----------
class ProductSource(str, Enum):
    FINLIFE = "finlife"
    DATAGO_HF = "datago_hf"
    DATAGO_KINFA = "datago_kinfa"
    DATAGO_FSC = "datago_fsc"
    CURATED = "curated"


class ProductCategory(str, Enum):
    DEPOSIT = "deposit"
    SAVING = "saving"
    MORTGAGE = "mortgage"
    JEONSE = "jeonse"
    CREDIT = "credit"
    POLICY = "policy"


class RateSemantics(str, Enum):
    OFFER_RATE = "offer_rate"                  # 상품 제시 금리
    DISCLOSED_AVG_RATE = "disclosed_avg_rate"  # 공시 평균금리(예: 신용점수 구간별 전월 평균)
    CURATED = "curated"


class ProductOption(BaseModel):
    rate: Optional[float] = None
    rate_kind: str = "base"  # base | avg | min | max | preferential
    term_months: Optional[int] = None
    credit_band: Optional[str] = None
    repay_method: Optional[str] = None
    rate_type: Optional[str] = None
    note: str = ""
    extra: dict[str, Any] = {}


class ProductSnapshot(BaseModel):
    id: str
    snapshot_id: str
    source: ProductSource
    category: ProductCategory
    lender_group: LenderGroup
    company_code: str
    company_name: str
    product_code: str
    product_name: str
    rate_semantics: RateSemantics
    options: list[ProductOption] = []
    join_conditions: str = ""
    max_amount: Optional[int] = None
    disclosure_month: str = ""
    disclosure_url: str = ""
    raw: dict[str, Any] = {}


# ---------- 비교(M0) ----------
class SortKey(str, Enum):
    TOTAL_COST = "total_cost"
    MONTHLY_PAYMENT = "monthly_payment"
    RATE = "rate"


class CompareContext(BaseModel):
    category: ProductCategory
    amount: int
    term_months: int
    sort_key: SortKey = SortKey.TOTAL_COST
    repay_method: RepayMethod = RepayMethod.EQUAL_PAYMENT
    rate_type: Optional[RateType] = None
    credit_band: Optional[str] = None
    lender_groups: list[LenderGroup] = [LenderGroup.BANK, LenderGroup.SAVINGS_BANK]
    exclude_companies: list[str] = []
    max_rate: Optional[float] = None
    target_loan_id: Optional[str] = None
    estimated_fields: list[str] = []
    user_confirmed: bool = False


class CompareItem(BaseModel):
    rank: int
    anon_label: str          # 예: "A은행 신용대출" (M0에서는 실명 비노출)
    product_ref: str         # ProductSnapshot.id (감사용, 화면 비노출)
    rate: float
    rate_kind: str
    rate_semantics: RateSemantics
    monthly_payment: Optional[int] = None
    total_interest: Optional[int] = None
    vs_current_total_interest: Optional[int] = None
    disclosure_url: str = ""
    notes: list[str] = []


class CompareResult(BaseModel):
    decision_id: str
    result_hash: str
    mode: Literal["M0", "M1"] = "M0"
    context: CompareContext
    items: list[CompareItem]
    sort_explain: str
    assumptions: list[str]
    disclaimer: str = DISCLAIMER
    snapshot_id: str
    rules_version: str
    engine_version: str = ENGINE_VERSION
    created_at: datetime
    candidates_total: int


class DecisionRecord(BaseModel):
    decision_id: str
    kind: str  # compare | action | scenario
    input_fingerprint: str
    result_hash: str
    context: dict[str, Any]
    result: dict[str, Any]
    versions: dict[str, str]
    created_at: datetime


# ---------- 거래·정책 ----------
class Transaction(BaseModel):
    id: str
    date: date
    amount: int
    merchant: str
    category: Optional[str] = None
    kind: Literal["card", "bank"] = "card"
    memo: str = ""


class PolicyParam(BaseModel):
    key: str
    value: Any
    unit: str = ""
    valid_from: Optional[date] = None
    valid_to: Optional[date] = None
    source_url: str = ""
    source_title: str = ""
    verified_at: Optional[date] = None
    needs_verification: bool = True
    note: str = ""


class PolicyParams(BaseModel):
    version: str
    params: dict[str, PolicyParam]

    def value(self, key: str, default: Any = None) -> Any:
        p = self.params.get(key)
        return default if p is None else p.value


# ---------- 대화(P4 전까지 규칙 기반) ----------
class ChatReply(BaseModel):
    reply_text: str
    chips: list[Chip] = []
    action: Optional[dict[str, Any]] = None  # {"type": "open_view" | "prepare_compare", "payload": {...}}
    llm_used: bool = False
    ai_notice: str = AI_NOTICE
    chat_id: Optional[str] = None  # 프로필(페르소나)별 대화 로그 식별자


# ---------- 소비 패턴 (P5, SPEC 2.6) ----------
class SpendingCategory(str, Enum):
    FOOD = "식비"
    CAFE = "카페간식"
    TRANSPORT = "교통"
    HOUSING = "주거"
    TELECOM = "통신"
    SUBSCRIPTION = "구독"
    MEDICAL = "의료"
    SHOPPING = "쇼핑"
    LEISURE = "여가"
    EDUCATION = "교육"
    INSURANCE = "보험"
    TRANSFER = "이체"
    DEBT_REPAYMENT = "대출상환"
    CASH_ADVANCE = "현금서비스"
    SALARY = "급여"
    OTHER = "기타"


class CategoryTotal(BaseModel):
    category: SpendingCategory
    amount: int             # 집계 기간(months) 전체 합계
    count: int               # 집계 기간 전체 건수
    share: float = 0.0       # amount / total_spend (0~1), total_spend가 0이면 0.0
    prev_amount: int = 0     # 집계 기간 내 마지막 달의 "그 이전 달" 금액(윈도우가 1개월이면 0)
    change_pct: Optional[float] = None  # 마지막 달 vs 이전 달 변화율(%). prev_amount가 0이면 None(비교 불가)


class SubscriptionItem(BaseModel):
    merchant: str  # 마스킹된 표시명(앞 2자 + "**")
    amount: int    # 관측된 월 금액 평균(반올림)
    months_seen: int
    category: SpendingCategory


class AnomalyItem(BaseModel):
    category: SpendingCategory
    month: str  # "YYYY-MM"
    amount: int
    prev_amount: int
    change_pct: float
    note: str


class LifeEventSignal(BaseModel):
    kind: Literal["wedding", "childbirth", "job_change", "retirement_near", "refinance_window", "income_drop"]
    confidence: float
    evidence: list[str] = []  # 가맹점 원문 대신 마스킹/범주 수준 설명 문자열만 담는다


class SpendingSummary(BaseModel):
    profile_id: str
    period_start: date
    period_end: date
    months: int
    total_spend: int
    avg_monthly_spend: int
    fixed_spend: int
    variable_spend: int
    discretionary_spend: int
    income_deposits: int
    categories: list[CategoryTotal] = []
    subscriptions: list[SubscriptionItem] = []
    anomalies: list[AnomalyItem] = []
    life_events: list[LifeEventSignal] = []
    top_merchants_masked: list[str] = []


class SpendingFeatures(BaseModel):
    profile_id: str
    computed_at: date  # 재현성을 위해 실행 시각이 아니라 summary.period_end를 쓴다
    months: int
    avg_monthly_spend: int
    fixed_ratio: float
    discretionary_ratio: float
    subscription_total: int
    subscription_count: int
    top_categories: list[str] = []
    spend_trend_pct: Optional[float] = None
    anomaly_count: int = 0
    income_regularity: float = 0.0
    savings_capacity: int = 0
    life_event_kinds: list[str] = []
    spending_consent_at: Optional[datetime] = None  # D8: 명시적 분석 실행 시각(서비스 레이어가 채움)
    # addendum 2.7 MVP 피처 중 이번 스코프(summary+profile만으로 계산 가능한 것)를 추가한다.
    income_monthly_est: int = 0
    net_cash_flow_monthly: int = 0
    data_coverage_days: int = 0
    classification_quality: float = 1.0
