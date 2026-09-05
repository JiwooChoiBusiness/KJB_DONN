"""금융감독원 '금융상품한눈에' 오픈API(finlifeapi) 클라이언트 및 정규화.

주소 https://finlife.fss.or.kr/finlifeapi/{service}.json, 파라미터 auth/topFinGrpNo/pageNo.
http 요청은 307로 https로 리다이렉트되므로 처음부터 https를 쓴다. 응답은 UTF-8 JSON.

실제 필드명은 2026-09-06 실 응답으로 확인했다(auth 키로 직접 호출, 캐시 파일 참고):
- baseList 공통: dcls_month, fin_co_no, fin_prdt_cd, kor_co_nm, fin_prdt_nm, join_way,
  dcls_strt_day, dcls_end_day, fin_co_subm_day
- deposit/saving baseList 추가: mtrt_int, spcl_cnd, join_deny, join_member, etc_note, max_limit
- mortgage/rent baseList 추가: loan_inci_expn, erly_rpay_fee, dly_rate, loan_lmt(텍스트, LTV 설명이지
  금액이 아님)
- credit baseList 추가: crdt_prdt_type(1 일반신용대출/2 마이너스한도대출/3 기타), cb_name, crdt_prdt_type_nm
- deposit optionList: intr_rate_type(S 단리/M 복리), intr_rate_type_nm, save_trm(개월, 문자열),
  intr_rate(기본금리), intr_rate2(최고우대금리)
- saving optionList: 위 + rsrv_type(적립유형), rsrv_type_nm
- mortgage/rent optionList: mrtg_type(아파트/기타, mortgage만), rpay_type(D 분할상환방식/S 만기일시상환방식),
  rpay_type_nm, lend_rate_type(C 변동금리/F 고정금리), lend_rate_type_nm,
  lend_rate_min, lend_rate_max, lend_rate_avg
- credit optionList: crdt_lend_rate_type(A 대출금리/B 기준금리/C 가산금리/D 가감조정금리),
  crdt_lend_rate_type_nm, crdt_grad_1/4/5/6/10/11/12/13(신용점수 구간별 금리), crdt_grad_avg(전체 평균)
  신용점수 구간 라벨(900점 초과 ~ 300점 이하)은 API 응답 자체에는 텍스트로 없어, 같은 도메인의
  개인신용대출 비교 화면(https://finlife.fss.or.kr/finlife/ldng/indvlCrdt/list.do?menuNo=700009)
  테이블 헤더에서 crdtGrad1..13 컬럼 라벨을 그대로 확인해 CRDT_GRADE_LABELS로 옮겼다.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import requests

from app.models import (
    LenderGroup,
    ProductCategory,
    ProductOption,
    ProductSnapshot,
    ProductSource,
    RateSemantics,
    RateType,
    RepayMethod,
)

logger = logging.getLogger(__name__)

BASE_URL = "https://finlife.fss.or.kr/finlifeapi/{service}.json"
CACHE_DIR = Path("data/cache")

# 서비스 -> 상품 카테고리 (SPEC 2.2)
SERVICE_CATEGORY: dict[str, ProductCategory] = {
    "depositProductsSearch": ProductCategory.DEPOSIT,
    "savingProductsSearch": ProductCategory.SAVING,
    "mortgageLoanProductsSearch": ProductCategory.MORTGAGE,
    "rentHouseLoanProductsSearch": ProductCategory.JEONSE,
    "creditLoanProductsSearch": ProductCategory.CREDIT,
}
FINLIFE_SERVICES: list[str] = list(SERVICE_CATEGORY.keys())

# 서비스 -> 금감원 비교공시 페이지 (실제 사이트 내비게이션에서 확인한 주소, 2026-09-06)
DISCLOSURE_URLS: dict[str, str] = {
    "depositProductsSearch": "https://finlife.fss.or.kr/finlife/svings/fdrmDpst/list.do?menuNo=700002",
    "savingProductsSearch": "https://finlife.fss.or.kr/finlife/svings/fdrmEnty/list.do?menuNo=700003",
    "mortgageLoanProductsSearch": "https://finlife.fss.or.kr/finlife/ldng/houseMrtg/list.do?menuNo=700007",
    "rentHouseLoanProductsSearch": "https://finlife.fss.or.kr/finlife/ldng/lfstsFunds/list.do?menuNo=700008",
    "creditLoanProductsSearch": "https://finlife.fss.or.kr/finlife/ldng/indvlCrdt/list.do?menuNo=700009",
}

# 권역코드(topFinGrpNo) -> 업권. 030200(여신전문)은 상호로 카드/캐피탈을 구분한다(공식 세부코드
# 미제공, 확인 필요).
GROUP_LENDER_MAP: dict[str, LenderGroup] = {
    "020000": LenderGroup.BANK,
    "030300": LenderGroup.SAVINGS_BANK,
    "050000": LenderGroup.INSURANCE,
    "060000": LenderGroup.OTHER,
}

RPAY_METHOD_MAP: dict[str, RepayMethod] = {
    "D": RepayMethod.EQUAL_PAYMENT,  # 분할상환방식 (원금균등/원리금균등 세부 구분은 API 미제공)
    "S": RepayMethod.BULLET,  # 만기일시상환방식
}
RATE_TYPE_MAP: dict[str, RateType] = {
    "C": RateType.VARIABLE,  # 변동금리
    "F": RateType.FIXED,  # 고정금리
}
JOIN_DENY_LABELS: dict[str, str] = {
    "1": "제한없음",
    "2": "서민전용",
    "3": "일부제한",
}

# 개인신용대출 비교 화면(위 URL) 테이블 헤더에서 확인한 신용점수 구간 라벨.
CRDT_GRADE_LABELS: dict[str, str] = {
    "crdt_grad_1": "900초과",
    "crdt_grad_4": "801~900",
    "crdt_grad_5": "701~800",
    "crdt_grad_6": "601~700",
    "crdt_grad_10": "501~600",
    "crdt_grad_11": "401~500",
    "crdt_grad_12": "301~400",
    "crdt_grad_13": "300이하",
}


def _safe_int(value: Any) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _safe_float(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _cache_dir() -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return CACHE_DIR


def _write_json(path: Path, data: Any) -> None:
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


class FinlifeClient:
    """금융감독원 금융상품한눈에 오픈API(finlifeapi) 클라이언트."""

    def __init__(self, auth_key: str, timeout: int = 20):
        self.auth_key = auth_key
        self.timeout = timeout

    def fetch(self, service: str, top_fin_grp_no: str, page: int = 1) -> dict:
        """단일 페이지 원본 응답(JSON dict)을 반환하고 캐시에 남긴다."""
        url = BASE_URL.format(service=service)
        resp = requests.get(
            url,
            params={"auth": self.auth_key, "topFinGrpNo": top_fin_grp_no, "pageNo": page},
            timeout=self.timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        _write_json(_cache_dir() / f"finlife_{service}_{top_fin_grp_no}_p{page}.json", data)
        return data

    def fetch_all(self, service: str, top_fin_grp_no: str) -> tuple[list[dict], list[dict]]:
        """max_page_no까지 모두 모아 (baseList, optionList)를 반환한다.

        병합 결과는 data/cache/finlife_{service}_{group}.json에 저장한다(페이지별 원본은
        _p{n}.json으로 별도 보관).
        """
        first = self.fetch(service, top_fin_grp_no, 1)
        result = first.get("result", {})
        err_cd = result.get("err_cd")
        if err_cd != "000":
            logger.warning(
                "finlife %s(group=%s) err_cd=%s err_msg=%s",
                service, top_fin_grp_no, err_cd, result.get("err_msg"),
            )
            return [], []

        base_rows = list(result.get("baseList") or [])
        option_rows = list(result.get("optionList") or [])
        max_page = _safe_int(result.get("max_page_no")) or 1
        for page in range(2, max_page + 1):
            page_result = self.fetch(service, top_fin_grp_no, page).get("result", {})
            base_rows.extend(page_result.get("baseList") or [])
            option_rows.extend(page_result.get("optionList") or [])

        _write_json(
            _cache_dir() / f"finlife_{service}_{top_fin_grp_no}.json",
            {
                "baseList": base_rows,
                "optionList": option_rows,
                "cached_at": datetime.now().isoformat(timespec="seconds"),
            },
        )
        return base_rows, option_rows


def _lender_group_for(group: str, company_name: str) -> LenderGroup:
    if group == "030200":  # 여신전문 (확인 필요: 공식 세부코드 없이 상호로 구분)
        return LenderGroup.CARD if "카드" in (company_name or "") else LenderGroup.CAPITAL
    return GROUP_LENDER_MAP.get(group, LenderGroup.OTHER)


def _join_conditions(base: dict) -> str:
    parts: list[str] = []
    member = base.get("join_member")
    if member:
        parts.append(f"가입대상 {member}")
    deny = base.get("join_deny")
    if deny:
        parts.append(f"가입제한 {JOIN_DENY_LABELS.get(deny, deny)}")
    way = base.get("join_way")
    if way:
        parts.append(f"가입방법 {way}")
    return "; ".join(parts)


def _deposit_options(opt_rows: list[dict]) -> list[ProductOption]:
    """예금/적금: 기본금리(intr_rate)와, 있으면 최고우대금리(intr_rate2)를 별도 옵션으로."""
    options: list[ProductOption] = []
    for row in opt_rows:
        term = _safe_int(row.get("save_trm"))
        base_rate = _safe_float(row.get("intr_rate"))
        pref_rate = _safe_float(row.get("intr_rate2"))
        extra: dict[str, Any] = {}
        if row.get("intr_rate_type_nm"):
            extra["intr_rate_type_nm"] = row["intr_rate_type_nm"]
        if row.get("rsrv_type_nm"):
            extra["rsrv_type_nm"] = row["rsrv_type_nm"]
        if base_rate is not None:
            options.append(ProductOption(
                rate=base_rate, rate_kind="base", term_months=term,
                note=row.get("intr_rate_type_nm", ""), extra=dict(extra),
            ))
        if pref_rate is not None and pref_rate != base_rate:
            options.append(ProductOption(
                rate=pref_rate, rate_kind="preferential", term_months=term,
                note="최고우대금리", extra=dict(extra),
            ))
    return options


def _loan_rate_options(opt_rows: list[dict]) -> list[ProductOption]:
    """주담대/전세자금대출: 평균(없으면 최저)금리를 대표값으로, min/max는 extra에 보존."""
    options: list[ProductOption] = []
    for row in opt_rows:
        repay = RPAY_METHOD_MAP.get(row.get("rpay_type"))
        rtype = RATE_TYPE_MAP.get(row.get("lend_rate_type"))
        avg = _safe_float(row.get("lend_rate_avg"))
        lo = _safe_float(row.get("lend_rate_min"))
        hi = _safe_float(row.get("lend_rate_max"))
        rate = avg if avg is not None else lo
        rate_kind = "avg" if avg is not None else ("min" if lo is not None else "base")
        extra: dict[str, Any] = {}
        if lo is not None:
            extra["lend_rate_min"] = lo
        if hi is not None:
            extra["lend_rate_max"] = hi
        if row.get("mrtg_type_nm"):
            extra["mrtg_type_nm"] = row["mrtg_type_nm"]
        note = "/".join(p for p in (row.get("rpay_type_nm"), row.get("lend_rate_type_nm")) if p)
        options.append(ProductOption(
            rate=rate, rate_kind=rate_kind, term_months=None,
            repay_method=repay.value if repay else None,
            rate_type=rtype.value if rtype else None,
            note=note, extra=extra,
        ))
    return options


def _credit_options(opt_rows: list[dict]) -> list[ProductOption]:
    """신용대출: crdt_lend_rate_type == 'A'(대출금리) 행만 쓴다.

    B(기준금리)/C(가산금리)/D(가감조정금리)는 대출금리를 구성하는 하위 요소라 공시
    평균금리(DISCLOSED_AVG_RATE) 자체가 아니므로 제외한다.
    """
    options: list[ProductOption] = []
    for row in opt_rows:
        if row.get("crdt_lend_rate_type") != "A":
            continue
        for field, label in CRDT_GRADE_LABELS.items():
            rate = _safe_float(row.get(field))
            if rate is None:
                continue
            options.append(ProductOption(
                rate=rate, rate_kind="avg", credit_band=label,
                note="대출금리(전월 신규취급 평균)",
            ))
        avg_rate = _safe_float(row.get("crdt_grad_avg"))
        if avg_rate is not None:
            options.append(ProductOption(
                rate=avg_rate, rate_kind="avg", credit_band=None,
                note="전체 평균금리",
            ))
    return options


def normalize_finlife(
    service: str,
    group: str,
    base_rows: list[dict],
    option_rows: list[dict],
    snapshot_id: str,
) -> list[ProductSnapshot]:
    """finlifeapi 원본 baseList/optionList를 ProductSnapshot 목록으로 정규화한다."""
    category = SERVICE_CATEGORY.get(service)
    if category is None:
        raise ValueError(f"unknown finlife service: {service}")

    rate_semantics = (
        RateSemantics.DISCLOSED_AVG_RATE
        if service == "creditLoanProductsSearch"
        else RateSemantics.OFFER_RATE
    )
    disclosure_url = DISCLOSURE_URLS.get(service, "")

    # (fin_co_no, fin_prdt_cd)만으로는 조인 키가 부족하다: 실 데이터(2026-09-06,
    # creditLoanProductsSearch/020000)에서 같은 은행의 서로 다른 두 상품(마이너스한도대출과
    # 카드대출)이 fin_prdt_cd를 공유하는 사례를 확인했다. crdt_prdt_type이 있으면 키에 더해
    # 이런 경우를 구분한다(신용대출 외 서비스에는 이 필드가 없어 항상 빈 문자열로 no-op).
    def _key(row: dict) -> tuple[str, str, str]:
        return (
            row.get("fin_co_no", "") or "",
            row.get("fin_prdt_cd", "") or "",
            row.get("crdt_prdt_type", "") or "",
        )

    options_by_key: dict[tuple[str, str, str], list[dict]] = {}
    for row in option_rows:
        options_by_key.setdefault(_key(row), []).append(row)

    snapshots: list[ProductSnapshot] = []
    for base in base_rows:
        fin_co_no = base.get("fin_co_no", "") or ""
        fin_prdt_cd = base.get("fin_prdt_cd", "") or ""
        prdt_type = base.get("crdt_prdt_type", "") or ""
        opt_rows = options_by_key.get(_key(base), [])
        lender_group = _lender_group_for(group, base.get("kor_co_nm", ""))

        if service == "creditLoanProductsSearch":
            options = _credit_options(opt_rows)
            max_amount = None
        elif service in ("mortgageLoanProductsSearch", "rentHouseLoanProductsSearch"):
            options = _loan_rate_options(opt_rows)
            max_amount = None  # loan_lmt는 LTV 등 텍스트 설명이라 금액이 아님
        else:  # depositProductsSearch, savingProductsSearch
            options = _deposit_options(opt_rows)
            max_amount = _safe_int(base.get("max_limit"))

        snapshots.append(ProductSnapshot(
            # service(+crdt_prdt_type)를 id에 포함한다: fin_co_no+fin_prdt_cd만으로는 부족하다.
            # (a) 서로 다른 서비스가 우연히 같은 조합을 쓸 수 있고, (b) 신용대출은 크레딧
            # 상품유형이 달라도 fin_prdt_cd를 공유하는 사례를 실 데이터로 확인했다(2026-09-06,
            # 977건 중 8~9건 충돌). 포함하지 않으면 DB INSERT OR REPLACE 시 서로 다른 상품이
            # 같은 id로 덮어써진다.
            id=f"{snapshot_id}:{service}:{fin_co_no}:{fin_prdt_cd}:{prdt_type}",
            snapshot_id=snapshot_id,
            source=ProductSource.FINLIFE,
            category=category,
            lender_group=lender_group,
            company_code=fin_co_no,
            company_name=base.get("kor_co_nm", "") or "",
            product_code=fin_prdt_cd,
            product_name=base.get("fin_prdt_nm", "") or "",
            rate_semantics=rate_semantics,
            options=options,
            join_conditions=_join_conditions(base),
            max_amount=max_amount,
            disclosure_month=base.get("dcls_month", "") or "",
            disclosure_url=disclosure_url,
            raw={"base": base, "options": opt_rows},
        ))
    return snapshots
