"""8명의 테스트 페르소나(P1~P8)와 합성 거래내역 생성기.

페르소나 정의는 docs/DONN_PLAN_v0.1.md 8장(1319~1452행, 8.1 페르소나 표)을 그대로 따른다.
문서가 이미 각 인물을 P1~P8로 번호를 매겨 두었으므로 그 번호·이름·나이·부채 구성을 그대로
쓴다(SPEC.md 2.2가 나열한 "P1 다중 소액부채 청년..." 같은 유형 설명 문구의 순서와는 완전히
일치하지 않는데 - 예: SPEC은 P6을 "학자금 사회초년생", P7을 "은퇴 앞둔 50대"라 부르지만
본안 표의 P6은 은퇴를 앞둔 한정우, P7은 연체 45일차 한도윤이다 - 이 구현은 이름·나이·정확한
부채 금액까지 있는 본안 표를 우선한다(작업 지시상 "본안 정의를 따른다"가 SPEC의 한 줄 설명보다
우선). 상세는 최종 보고의 deviations 항목 참고.

금리별 월이자 기대값(본안 표의 "사전 계산 기대 수치")과 맞도록 principal/balance/annual_rate/
remaining_months를 역산해 채웠다(예: P1 마통 500만원 x 6.5%/12 = 27,083원). 배우자·파트너
소득/부채는 본안 5.2.6 원칙에 따라 구간값으로만 notes에 남기고 Loan으로 개별 저장하지 않는다.
숫자는 기준일 2026-09-06 기준 고정값이다(페르소나는 정적 데이터라 SPEC 원칙 2의 "same input
same output"과 무관하다).

credit_band는 본안에 실제 신용점수가 나온 페르소나(P1=790, P3=640)에만 채우고, finlife.py가
실 API 응답에서 확인한 구간 라벨(CRDT_GRADE_LABELS)을 그대로 재사용해 화면 라벨과 어긋나지
않게 한다. 나머지 페르소나는 점수가 문서에 없어 임의로 지어내지 않고 None으로 둔다.
"""
from __future__ import annotations

import calendar
import csv
import io
import random
from datetime import date, timedelta

from app.data.finlife import CRDT_GRADE_LABELS
from app.models import (
    Assets,
    Goal,
    LenderGroup,
    Loan,
    LoanType,
    PensionAssets,
    RateType,
    RepayMethod,
    Transaction,
    UserProfile,
)

# ---------------------------------------------------------------------------
# 페르소나 (본안 8.1)
# ---------------------------------------------------------------------------

PERSONAS: list[UserProfile] = [
    UserProfile(
        id="P1",
        display_name="김서연",
        age=29,
        employment="employee",
        monthly_income=2_700_000,
        fixed_expenses=1_750_000,
        variable_expenses=400_000,
        emergency_fund=200_000,
        credit_band=CRDT_GRADE_LABELS["crdt_grad_5"],  # 701~800 (자가신고 790점)
        flags=[],
        notes=(
            "사회초년생 마케터, 서울 월세(고정지출에 월세 65만원 포함). "
            "신용점수(자가신고) 790, 연체 없음. 비상금 목표 50만원."
        ),
        assets=Assets(
            liquid=200_000, investment=800_000,
            pension=PensionAssets(
                national_pension_months_paid=60, db_dc_balance=3_000_000,
                irp_pension_savings_balance=500_000, isa_balance=2_000_000,
            ),
        ),
        goals=[
            Goal(id="P1-G1", kind="emergency", label="비상자금", target_amount=500_000,
                 target_date=date(2027, 3, 6), saved_amount=200_000),
        ],
        dependents=0,
        risk_tolerance="mid",
        income_type="regular",
        retirement_age=65,
        target_retirement_monthly_expense=1_600_000,
        loans=[
            Loan(
                id="P1-L1", name="학자금대출(한국장학재단)", loan_type=LoanType.STUDENT,
                principal=11_500_000, balance=11_500_000, annual_rate=1.7,
                rate_type=RateType.FIXED, repay_method=RepayMethod.EQUAL_PAYMENT,
                remaining_months=96, lender_group=LenderGroup.POLICY,
            ),
            Loan(
                id="P1-L2", name="마이너스통장", loan_type=LoanType.OVERDRAFT,
                principal=10_000_000, balance=5_000_000, annual_rate=6.5,
                rate_type=RateType.VARIABLE, repay_method=RepayMethod.REVOLVING,
                remaining_months=12, lender_group=LenderGroup.BANK,
            ),
            Loan(
                id="P1-L3", name="카드 리볼빙", loan_type=LoanType.CARD_LOAN,
                principal=2_400_000, balance=2_400_000, annual_rate=17.5,
                rate_type=RateType.FIXED, repay_method=RepayMethod.REVOLVING,
                remaining_months=12, lender_group=LenderGroup.CARD,
            ),
            Loan(
                id="P1-L4", name="무이자 할부", loan_type=LoanType.OTHER,
                principal=900_000, balance=900_000, annual_rate=0.0,
                rate_type=RateType.FIXED, repay_method=RepayMethod.EQUAL_PRINCIPAL,
                remaining_months=3, lender_group=LenderGroup.CARD,
            ),
        ],
    ),
    UserProfile(
        id="P2",
        display_name="박준호",
        age=38,
        employment="employee",
        monthly_income=4_200_000,
        fixed_expenses=1_800_000,
        variable_expenses=900_000,
        emergency_fund=8_000_000,
        credit_band=None,
        flags=["income_up"],
        notes=(
            "맞벌이, 경기 아파트. 배우자 소득 구간 250~300만원(구간값, 개별 저장하지 않음). "
            "최근 승진으로 연봉 12% 상승."
        ),
        assets=Assets(
            liquid=8_000_000, investment=20_000_000, real_estate=500_000_000,
            pension=PensionAssets(
                national_pension_months_paid=156, db_dc_balance=45_000_000,
                irp_pension_savings_balance=5_000_000, isa_balance=10_000_000,
            ),
        ),
        goals=[
            Goal(id="P2-G1", kind="emergency", label="예비 비상자금 확대", target_amount=15_000_000,
                 target_date=date(2028, 3, 1), saved_amount=8_000_000),
        ],
        dependents=0,
        risk_tolerance="mid",
        income_type="regular",
        retirement_age=65,
        target_retirement_monthly_expense=2_200_000,
        loans=[
            Loan(
                id="P2-L1", name="주택담보대출(혼합형, 2028-09 변동 전환 예정)",
                loan_type=LoanType.MORTGAGE, principal=350_000_000, balance=330_000_000,
                annual_rate=3.9, rate_type=RateType.FIXED, repay_method=RepayMethod.EQUAL_PAYMENT,
                remaining_months=324, start_date=date(2023, 9, 1),
                prepay_fee_rate=1.43, prepay_fee_until=date(2026, 9, 30),
                lender_group=LenderGroup.BANK,
            ),
            Loan(
                id="P2-L2", name="신용대출", loan_type=LoanType.CREDIT,
                principal=30_000_000, balance=30_000_000, annual_rate=5.8,
                rate_type=RateType.FIXED, repay_method=RepayMethod.BULLET,
                remaining_months=4, lender_group=LenderGroup.BANK,
            ),
            Loan(
                id="P2-L3", name="자동차 할부", loan_type=LoanType.OTHER,
                principal=12_000_000, balance=12_000_000, annual_rate=5.5,
                rate_type=RateType.FIXED, repay_method=RepayMethod.EQUAL_PAYMENT,
                remaining_months=24, lender_group=LenderGroup.CAPITAL,
            ),
        ],
    ),
    UserProfile(
        id="P3",
        display_name="이미라",
        age=45,
        employment="self_employed",
        monthly_income=2_500_000,
        fixed_expenses=3_000_000,
        variable_expenses=0,
        emergency_fund=0,
        credit_band=CRDT_GRADE_LABELS["crdt_grad_6"],  # 601~700 (신용점수 640)
        flags=["self_employed"],
        notes=(
            "부산 카페 운영 개인사업자. 월 매출 1,800만원, 순수익 250~400만원 변동(최저 구간을 "
            "monthly_income 기준값으로 사용). 다중채무 돌려막기 중이나 연체는 없음. 불면 호소."
        ),
        assets=Assets(
            liquid=0, investment=0,
            pension=PensionAssets(national_pension_months_paid=150),
        ),
        goals=[
            Goal(id="P3-G1", kind="emergency", label="비상자금", target_amount=3_000_000,
                 target_date=date(2027, 6, 1), saved_amount=0),
        ],
        dependents=0,
        risk_tolerance="low",
        income_type="variable",
        retirement_age=65,
        target_retirement_monthly_expense=2_300_000,
        loans=[
            Loan(
                id="P3-L1", name="보증부 사업자대출", loan_type=LoanType.OTHER,
                principal=80_000_000, balance=80_000_000, annual_rate=6.0,
                rate_type=RateType.FIXED, repay_method=RepayMethod.BULLET,
                remaining_months=8, lender_group=LenderGroup.BANK,
            ),
            Loan(
                id="P3-L2", name="카드론", loan_type=LoanType.CARD_LOAN,
                principal=15_000_000, balance=15_000_000, annual_rate=15.9,
                rate_type=RateType.FIXED, repay_method=RepayMethod.EQUAL_PAYMENT,
                remaining_months=24, lender_group=LenderGroup.CARD,
            ),
            Loan(
                id="P3-L3", name="저축은행 신용대출", loan_type=LoanType.CREDIT,
                principal=20_000_000, balance=20_000_000, annual_rate=15.9,
                rate_type=RateType.FIXED, repay_method=RepayMethod.EQUAL_PAYMENT,
                remaining_months=36, lender_group=LenderGroup.SAVINGS_BANK,
            ),
            Loan(
                id="P3-L4", name="카드 리볼빙", loan_type=LoanType.CARD_LOAN,
                principal=3_000_000, balance=3_000_000, annual_rate=18.0,
                rate_type=RateType.FIXED, repay_method=RepayMethod.REVOLVING,
                remaining_months=12, lender_group=LenderGroup.CARD,
            ),
        ],
    ),
    UserProfile(
        id="P4",
        display_name="최동현",
        age=33,
        employment="employee",
        monthly_income=4_200_000,
        fixed_expenses=1_600_000,
        variable_expenses=700_000,
        emergency_fund=5_000_000,
        credit_band=None,
        flags=[],
        notes=(
            "서울 IT 개발자, 2027-05-09 결혼 예정. 결혼 비용 3,000만원 필요, 신혼 전세 목표 3.5억원. "
            "파트너 소득 구간 250~300만원, 파트너 학자금대출 구간 500만~1,000만원은 제3자 정보라 "
            "개별 Loan으로 저장하지 않고 가구 합산 구간으로만 참고한다."
        ),
        assets=Assets(
            liquid=5_000_000, investment=10_000_000,
            pension=PensionAssets(
                national_pension_months_paid=84, db_dc_balance=25_000_000,
                irp_pension_savings_balance=3_000_000, isa_balance=5_000_000,
            ),
        ),
        goals=[
            Goal(id="P4-G1", kind="wedding", label="결혼 비용", target_amount=30_000_000,
                 target_date=date(2027, 5, 9), saved_amount=10_000_000),
            Goal(id="P4-G2", kind="housing", label="신혼 전세자금", target_amount=350_000_000,
                 target_date=date(2027, 5, 9), saved_amount=50_000_000),
        ],
        dependents=0,
        risk_tolerance="mid",
        income_type="regular",
        retirement_age=65,
        target_retirement_monthly_expense=1_900_000,
        loans=[
            Loan(
                id="P4-L1", name="전세자금대출", loan_type=LoanType.JEONSE,
                principal=200_000_000, balance=200_000_000, annual_rate=3.9,
                rate_type=RateType.VARIABLE, repay_method=RepayMethod.BULLET,
                remaining_months=11, lender_group=LenderGroup.BANK,
            ),
            Loan(
                id="P4-L2", name="신용대출(1년 만기일시 갱신)", loan_type=LoanType.CREDIT,
                principal=20_000_000, balance=20_000_000, annual_rate=5.5,
                rate_type=RateType.FIXED, repay_method=RepayMethod.BULLET,
                remaining_months=12, lender_group=LenderGroup.BANK,
            ),
        ],
    ),
    UserProfile(
        id="P5",
        display_name="정수민",
        age=26,
        employment="other",
        monthly_income=1_900_000,
        fixed_expenses=1_200_000,
        variable_expenses=300_000,
        emergency_fund=0,
        credit_band=None,
        flags=["delinquency_signal"],
        notes=(
            "프리랜서 영상편집, 소득 불규칙(월 80~350만원, 평균 190만원). 카드대금 12일 연체, "
            "추심 연락 시작. 통신비 2개월 미납(Loan으로 저장하지 않음). 안전모드(연체 30일 이하) 대상."
        ),
        assets=Assets(
            liquid=0, investment=0,
            pension=PensionAssets(national_pension_months_paid=18),
        ),
        goals=[
            Goal(id="P5-G1", kind="emergency", label="비상자금", target_amount=1_000_000,
                 target_date=date(2027, 9, 1), saved_amount=0),
        ],
        dependents=0,
        risk_tolerance="low",
        income_type="variable",
        retirement_age=65,
        target_retirement_monthly_expense=1_200_000,
        loans=[
            Loan(
                id="P5-L1", name="카드대금(연체)", loan_type=LoanType.CARD_LOAN,
                principal=1_450_000, balance=1_450_000, annual_rate=20.0,
                rate_type=RateType.FIXED, repay_method=RepayMethod.BULLET,
                remaining_months=1, lender_group=LenderGroup.CARD,
            ),
            Loan(
                id="P5-L2", name="2금융권 소액 신용대출", loan_type=LoanType.CREDIT,
                principal=5_000_000, balance=5_000_000, annual_rate=19.9,
                rate_type=RateType.FIXED, repay_method=RepayMethod.BULLET,
                remaining_months=24, lender_group=LenderGroup.SAVINGS_BANK,
            ),
        ],
    ),
    UserProfile(
        id="P6",
        display_name="한정우",
        age=52,
        employment="employee",
        monthly_income=5_800_000,
        fixed_expenses=2_500_000,
        variable_expenses=600_000,
        emergency_fund=10_000_000,
        credit_band=None,
        flags=["retirement_near"],
        notes=(
            "대전 제조업 부장, 2029년 정년 예정. 배우자 소득 구간 100~150만원(구간값). 퇴직금 "
            "예상 1.8억원, 국민연금 63세 개시 예정. 자녀 등록금 연 900만원 x 2년 잔여(고정지출에 "
            "월 평균으로 반영). \"빚 남기고 은퇴하는 게 창피하다\"는 정서적 부담 호소."
        ),
        assets=Assets(
            liquid=10_000_000, investment=15_000_000, real_estate=350_000_000,
            pension=PensionAssets(
                national_pension_months_paid=300, db_dc_balance=180_000_000,
                irp_pension_savings_balance=15_000_000, isa_balance=8_000_000,
            ),
        ),
        goals=[
            Goal(id="P6-G1", kind="education", label="자녀 등록금", target_amount=18_000_000,
                 target_date=date(2028, 2, 28), saved_amount=0),
        ],
        dependents=1,
        risk_tolerance="low",
        income_type="regular",
        retirement_age=55,
        target_retirement_monthly_expense=2_800_000,
        loans=[
            Loan(
                id="P6-L1", name="주택담보대출", loan_type=LoanType.MORTGAGE,
                principal=120_000_000, balance=120_000_000, annual_rate=4.5,
                rate_type=RateType.VARIABLE, repay_method=RepayMethod.EQUAL_PAYMENT,
                remaining_months=96, lender_group=LenderGroup.BANK,
            ),
            Loan(
                id="P6-L2", name="마이너스통장(자녀 등록금 용도)", loan_type=LoanType.OVERDRAFT,
                principal=25_000_000, balance=25_000_000, annual_rate=5.4,
                rate_type=RateType.VARIABLE, repay_method=RepayMethod.REVOLVING,
                remaining_months=12, lender_group=LenderGroup.BANK,
            ),
        ],
    ),
    UserProfile(
        id="P7",
        display_name="한도윤",
        age=31,
        employment="employee",
        monthly_income=2_100_000,
        fixed_expenses=1_200_000,
        variable_expenses=350_000,
        emergency_fund=0,
        credit_band=None,
        flags=["delinquency_signal"],
        notes=(
            "계약직, 연체 45일차. 추심 연락 시작, 불안·불면 호소. 안전모드(연체 31~89일, "
            "사전채무조정 단계) 대상."
        ),
        assets=Assets(
            liquid=0, investment=0,
            pension=PensionAssets(national_pension_months_paid=60),
        ),
        goals=[
            Goal(id="P7-G1", kind="emergency", label="비상자금", target_amount=2_000_000,
                 target_date=date(2027, 12, 1), saved_amount=0),
        ],
        dependents=0,
        risk_tolerance="low",
        income_type="regular",
        retirement_age=65,
        target_retirement_monthly_expense=1_300_000,
        loans=[
            Loan(
                id="P7-L1", name="은행 신용대출", loan_type=LoanType.CREDIT,
                principal=20_000_000, balance=20_000_000, annual_rate=7.0,
                rate_type=RateType.FIXED, repay_method=RepayMethod.BULLET,
                remaining_months=6, lender_group=LenderGroup.BANK,
            ),
            Loan(
                id="P7-L2", name="카드론", loan_type=LoanType.CARD_LOAN,
                principal=10_000_000, balance=10_000_000, annual_rate=17.0,
                rate_type=RateType.FIXED, repay_method=RepayMethod.EQUAL_PAYMENT,
                remaining_months=24, lender_group=LenderGroup.CARD,
            ),
            Loan(
                id="P7-L3", name="등록 대부업 대출", loan_type=LoanType.OTHER,
                principal=15_000_000, balance=15_000_000, annual_rate=20.0,
                rate_type=RateType.FIXED, repay_method=RepayMethod.EQUAL_PAYMENT,
                remaining_months=12, lender_group=LenderGroup.OTHER,
            ),
        ],
    ),
    UserProfile(
        id="P8",
        display_name="한소희",
        age=23,
        employment="student",
        monthly_income=900_000,
        fixed_expenses=700_000,
        variable_expenses=100_000,
        emergency_fund=0,
        credit_band=None,
        flags=[],
        notes=(
            "대학생·아르바이트. 부모님 모르게 해결하고 싶어함. 통신비 2개월 연체(Loan으로 "
            "저장하지 않음). 취약 청년 보호 대상."
        ),
        assets=Assets(liquid=0, investment=0, pension=PensionAssets(national_pension_months_paid=0)),
        goals=[
            Goal(id="P8-G1", kind="emergency", label="비상자금", target_amount=500_000,
                 target_date=date(2027, 8, 1), saved_amount=0),
        ],
        dependents=0,
        risk_tolerance="low",
        income_type="variable",
        retirement_age=65,
        target_retirement_monthly_expense=700_000,
        loans=[
            Loan(
                id="P8-L1", name="카드 리볼빙", loan_type=LoanType.CARD_LOAN,
                principal=2_500_000, balance=2_500_000, annual_rate=18.0,
                rate_type=RateType.FIXED, repay_method=RepayMethod.REVOLVING,
                remaining_months=12, lender_group=LenderGroup.CARD,
            ),
            Loan(
                id="P8-L2", name="현금서비스", loan_type=LoanType.CARD_LOAN,
                principal=800_000, balance=800_000, annual_rate=19.0,
                rate_type=RateType.FIXED, repay_method=RepayMethod.BULLET,
                remaining_months=1, lender_group=LenderGroup.CARD,
            ),
        ],
    ),
]

_PERSONA_BY_ID: dict[str, UserProfile] = {p.id: p for p in PERSONAS}


def get_persona(persona_id: str) -> UserProfile:
    persona = _PERSONA_BY_ID.get(persona_id)
    if persona is None:
        raise ValueError(f"unknown persona_id: {persona_id}")
    return persona


# ---------------------------------------------------------------------------
# 합성 거래내역
# ---------------------------------------------------------------------------

# (상호, 최소금액, 최대금액)
_MERCHANTS: dict[str, list[tuple[str, int, int]]] = {
    "식비": [
        ("스타벅스", 4_500, 9_000), ("배달의민족", 12_000, 35_000),
        ("김밥천국", 5_000, 12_000), ("GS25", 3_000, 15_000),
        ("맘스터치", 7_000, 15_000), ("교촌치킨", 20_000, 32_000),
        ("본죽", 8_000, 14_000), ("이마트24", 3_000, 20_000),
    ],
    "교통": [
        ("카카오T", 4_000, 25_000), ("티머니 교통카드", 1_250, 3_000),
        ("SRT", 20_000, 60_000), ("쏘카", 15_000, 60_000),
    ],
    "쇼핑": [
        ("쿠팡", 15_000, 120_000), ("무신사", 30_000, 150_000),
        ("올리브영", 10_000, 60_000), ("다이소", 3_000, 20_000),
    ],
    "여가": [
        ("CGV", 12_000, 30_000), ("야놀자", 50_000, 180_000),
        ("골프존", 30_000, 80_000), ("여기어때", 40_000, 150_000),
    ],
    "의료": [
        ("정형외과", 8_000, 45_000), ("온누리약국", 5_000, 25_000), ("치과", 20_000, 150_000),
    ],
    "주거": [
        ("아파트관리비", 120_000, 220_000), ("도시가스요금", 15_000, 60_000),
        ("한국전력공사", 30_000, 80_000),
    ],
    "통신": [("SKT", 55_000, 89_000), ("KT", 50_000, 85_000), ("LG유플러스", 48_000, 82_000)],
    "구독": [
        ("넷플릭스", 13_500, 17_000), ("유튜브프리미엄", 14_900, 14_900),
        ("멜론", 10_900, 10_900), ("쿠팡와우", 7_890, 7_890), ("왓챠", 7_900, 12_900),
    ],
    "이체": [
        ("가족송금", 50_000, 500_000), ("토스이체", 10_000, 300_000),
        ("카카오페이송금", 10_000, 200_000),
    ],
}

_OUTLIERS: list[tuple[str, str, int, int]] = [
    ("응급실 진료비", "의료", 80_000, 400_000),
    ("해외여행 항공권", "여가", 350_000, 1_200_000),
    ("가전제품 구매", "쇼핑", 300_000, 1_500_000),
    ("경조사비", "이체", 100_000, 500_000),
    ("노트북 수리비", "쇼핑", 150_000, 600_000),
]

# 카드/은행 결제 구분
_CARD_CATEGORIES = {"식비", "교통", "쇼핑", "여가", "의료", "구독"}

# 소득 구간별 변동지출 항목당 월 발생 건수 범위
_COUNT_RANGES: dict[str, dict[str, tuple[int, int]]] = {
    "low": {"식비": (4, 8), "교통": (3, 6), "쇼핑": (1, 2), "여가": (0, 1), "의료": (0, 1)},
    "mid": {"식비": (6, 12), "교통": (4, 8), "쇼핑": (2, 4), "여가": (1, 2), "의료": (0, 1)},
    "high": {"식비": (8, 15), "교통": (5, 10), "쇼핑": (2, 5), "여가": (1, 3), "의료": (0, 2)},
}


def _income_tier(monthly_income: int) -> str:
    if monthly_income < 1_500_000:
        return "low"
    if monthly_income < 3_500_000:
        return "mid"
    return "high"


def _shift_month(year: int, month: int, delta: int) -> tuple[int, int]:
    idx = (year * 12 + (month - 1)) + delta
    return idx // 12, idx % 12 + 1


def _month_bounds(end: date, months_back: int) -> tuple[date, date]:
    y, m = _shift_month(end.year, end.month, -months_back)
    first = date(y, m, 1)
    if months_back == 0:
        last = end
    else:
        ny, nm = _shift_month(y, m, 1)
        last = date(ny, nm, 1) - timedelta(days=1)
    return first, last


def _estimate_monthly_payment(loan: Loan) -> int:
    """거래내역용 대략치. 정확한 상환 스케줄은 app/core가 계산하므로 여기서는 1회차
    이자(또는 무이자 할부의 균등 분할액) 수준의 현실적인 근사값만 만든다.
    """
    if loan.annual_rate > 0:
        amount = round(loan.balance * loan.annual_rate / 100 / 12)
    else:
        months = max(loan.remaining_months, 1)
        amount = round(loan.principal / months)
    return max(amount, 1_000)


def generate_transactions(
    persona_id: str, *, months: int = 3, seed: int = 42, end: date
) -> list[Transaction]:
    """persona_id/seed/end가 같으면 항상 같은 결과를 내는 합성 카드·은행 거래내역.

    각 월에 고정성 지출(주거/통신/구독/대출상환)과 변동 지출(식비/교통/쇼핑/여가/의료)을
    섞고, 전체 기간에 1~2건의 이상치(고액 비정기 지출)를 넣는다.
    """
    persona = get_persona(persona_id)
    rng = random.Random(f"{persona_id}:{seed}")
    tier = _income_tier(persona.monthly_income)

    rows: list[Transaction] = []
    counter = 0

    def add(d: date, amount: int, merchant: str, category: str, kind: str, memo: str = "") -> None:
        nonlocal counter
        rows.append(Transaction(
            id=f"{persona_id}-{d.isoformat()}-{counter:04d}",
            date=d, amount=amount, merchant=merchant, category=category, kind=kind, memo=memo,
        ))
        counter += 1

    # 페르소나 전체 기간 동안 고정으로 쓰는 통신사·구독 목록(월별로 반복)
    telecom_merchant, telecom_lo, telecom_hi = rng.choice(_MERCHANTS["통신"])
    n_subs = rng.randint(1, 3)
    subs = rng.sample(_MERCHANTS["구독"], k=min(n_subs, len(_MERCHANTS["구독"])))

    for months_back in range(months - 1, -1, -1):
        first, last = _month_bounds(end, months_back)
        span_days = (last - first).days + 1

        def rand_day(_span: int = span_days, _first: date = first) -> date:
            return _first + timedelta(days=rng.randrange(_span))

        # 주거 (P1은 월세가 서사의 핵심이라 명시적으로 반영)
        if persona_id == "P1":
            rent = round(650_000 * rng.uniform(0.98, 1.0))
            add(_first_days(first, 1, 5, rng), rent, "월세", "주거", "bank")
        else:
            m_name, m_lo, m_hi = rng.choice(_MERCHANTS["주거"])
            add(_first_days(first, 1, 5, rng), rng.randint(m_lo, m_hi), m_name, "주거", "bank")

        # 통신
        add(_first_days(first, 1, 10, rng), rng.randint(telecom_lo, telecom_hi), telecom_merchant, "통신", "bank")

        # 구독
        for name, lo, hi in subs:
            add(_first_days(first, 1, 15, rng), rng.randint(lo, hi), name, "구독", "card")

        # 변동 지출
        for category, (lo_n, hi_n) in _COUNT_RANGES[tier].items():
            n = rng.randint(lo_n, hi_n)
            for _ in range(n):
                name, lo, hi = rng.choice(_MERCHANTS[category])
                kind = "card" if category in _CARD_CATEGORIES else "bank"
                add(rand_day(), rng.randint(lo, hi), name, category, kind)

        # 이체
        for _ in range(rng.randint(0, 2)):
            name, lo, hi = rng.choice(_MERCHANTS["이체"])
            add(rand_day(), rng.randint(lo, hi), name, "이체", "bank")

        # 대출상환 (보유 대출마다 월 1건)
        for loan in persona.loans:
            pay_day = min(25 + rng.randint(0, 3), last.day)
            pay_date = date(first.year, first.month, pay_day)
            add(pay_date, _estimate_monthly_payment(loan), loan.name, "대출상환", "bank")

    # 이상치 1~2건
    n_outliers = rng.choice([1, 1, 2])
    for i in range(n_outliers):
        months_back = rng.randrange(months)
        first, last = _month_bounds(end, months_back)
        d = first + timedelta(days=rng.randrange((last - first).days + 1))
        name, category, lo, hi = rng.choice(_OUTLIERS)
        kind = "bank" if category == "이체" else "card"
        add(d, rng.randint(lo, hi), name, category, kind, memo="비정기 지출")

    rows.sort(key=lambda t: t.date)
    return rows


def _first_days(first: date, lo: int, hi: int, rng: random.Random) -> date:
    """월초 며칠(lo~hi일) 사이의 날짜. 그 달 실제 일수를 넘지 않게 자른다."""
    days_in_month = calendar.monthrange(first.year, first.month)[1]
    day = rng.randint(lo, min(hi, days_in_month))
    return date(first.year, first.month, day)


def transactions_to_csv(rows: list[Transaction]) -> str:
    """id,date,amount,merchant,category,kind,memo 헤더의 CSV 문자열을 만든다."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["id", "date", "amount", "merchant", "category", "kind", "memo"])
    for t in rows:
        writer.writerow([t.id, t.date.isoformat(), t.amount, t.merchant, t.category or "", t.kind, t.memo])
    return buf.getvalue()
