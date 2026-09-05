"""공공데이터포털(data.go.kr) 오픈API 클라이언트 및 정규화.

디코딩된 서비스키를 그대로 requests params로 넘긴다(requests가 자체적으로 URL 인코딩하므로
이중 인코딩하지 않는다).

- 디딤돌 금리: https://apis.data.go.kr/B551408/didimdol-loan-rate/didimdol-info
  (데이터셋 15082028). 2026-09-06 실 호출로 정상 동작 확인. 응답은 resultType 파라미터와
  무관하게 XML 고정(Content-Type: application/xml). 필드는 interest_{10,15,20,30}y_{2000,4000,6000}
  (10/15/20/30년 만기 x 소득 2000/4000/6000만원 이하 구간별 금리) + applyDy(YYYYMMDD).
  소득 구간 라벨("소득 OOOO만원 이하")은 데이터셋 상세 페이지의 필드 설명을 그대로 썼다.
- 서민금융 상품 기본정보: base https://apis.data.go.kr/1160100/service/GetSmallLoanFinanceInstituteInfoService
  (데이터셋 15094787). 오퍼레이션명은 API 명세에 없어 데이터셋 페이지(data.go.kr/data/15094787/openapi.do)에
  임베드된 swagger 스펙에서 확인: "getOrdinaryFinanceInfo". type=json 파라미터로 JSON 응답을 받을 수
  있음을 2026-09-06 실 호출로 확인(총 10,474건 - 서민금융진흥원 상품 + 지자체 정책자금 다수 포함).
- 대출상품한눈에: base https://apis.data.go.kr/B553701/LoanProductSearchingInfo (데이터셋 15106208).
  오퍼레이션명도 데이터셋 페이지의 swagger 스펙으로 "LoanProductSearchingInfo/getLoanProductSearchingInfo"를
  확인했으나, 2026-09-06 기준 이 서비스키로 호출하면 NO_OPENAPI_SERVICE_ERROR(코드 12, "해당 오픈API
  서비스가 없거나 폐기됨")가 반환된다. 경로/파라미터는 스펙과 정확히 일치시켰으므로 계정의 활용신청
  승인 상태 문제로 추정된다 (확인 필요). 승인되면 그대로 동작하도록 구현해두고, 실패 시 예외 대신
  빈 리스트 반환 + 로그로 안전하게 처리한다.
"""
from __future__ import annotations

import json
import logging
import re
import xml.etree.ElementTree as ET
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

CACHE_DIR = Path("data/cache")

DIDIMDOL_URL = "https://apis.data.go.kr/B551408/didimdol-loan-rate/didimdol-info"
FSC_SMALL_LOAN_URL = (
    "https://apis.data.go.kr/1160100/service/GetSmallLoanFinanceInstituteInfoService/getOrdinaryFinanceInfo"
)
KINFA_LOAN_PRODUCT_URL = (
    "https://apis.data.go.kr/B553701/LoanProductSearchingInfo/LoanProductSearchingInfo/getLoanProductSearchingInfo"  # 게이트웨이가 서비스명을 두 번 요구 (2026-09-06 실 호출 확인, 단일 경로는 코드 12)
)  # (확인 필요: 활용신청 승인 상태 - 본문 모듈 docstring 참고)

DISCLOSURE_URL_DIDIMDOL = "https://www.data.go.kr/data/15082028/openapi.do"
DISCLOSURE_URL_FSC = "https://www.data.go.kr/data/15094787/openapi.do"
DISCLOSURE_URL_KINFA = "https://www.data.go.kr/data/15106208/openapi.do"

REPAY_METHOD_TEXT_MAP: dict[str, RepayMethod] = {
    "원리금균등분할상환": RepayMethod.EQUAL_PAYMENT,
    "원(리)금균등분할상환": RepayMethod.EQUAL_PAYMENT,
    "원금균등분할상환": RepayMethod.EQUAL_PRINCIPAL,
    "만기일시상환": RepayMethod.BULLET,
}
RATE_TYPE_TEXT_MAP: dict[str, RateType] = {
    "고정금리": RateType.FIXED,
    "변동금리": RateType.VARIABLE,
}

_RATE_RANGE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*~\s*(\d+(?:\.\d+)?)\s*%?")
_RATE_SINGLE_RE = re.compile(r"^~?\s*(\d+(?:\.\d+)?)\s*%?(?:\s*(?:이내|이하))?$")  # KINFA는 % 없이 "~19.99", "3" 형식
_AMOUNT_RE = re.compile(r"([\d,]+)\s*만\s*원")
_DIDIMDOL_FIELD_RE = re.compile(r"^interest_(\d+)y_(\d+)$")


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


def _parse_xml(text: str) -> tuple[list[dict], dict]:
    """공공데이터포털 표준 XML 응답을 (item dict 목록, header dict)로 변환한다.

    정상 응답: <response><header>...</header><body>...<item>...</item>...</body></response>
    오류 응답: <OpenAPI_ServiceResponse><cmmMsgHeader>...</cmmMsgHeader></OpenAPI_ServiceResponse>
    """
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        logger.warning("datago: XML 파싱 실패(응답 형식 확인 필요)")
        return [], {}

    if root.tag == "OpenAPI_ServiceResponse":
        header_el = root.find("cmmMsgHeader")
        header = {child.tag: (child.text or "") for child in header_el} if header_el is not None else {}
        logger.warning("datago API 오류: %s", header.get("errMsg", "unknown"))
        return [], header

    header_el = root.find(".//header")
    header = {child.tag: (child.text or "") for child in header_el} if header_el is not None else {}
    rows = [
        {child.tag: (child.text or "") for child in item_el}
        for item_el in root.findall(".//item")
    ]
    return rows, header


class DataGoClient:
    """공공데이터포털 오픈API 클라이언트. service_key는 디코딩된 키를 그대로 넘긴다."""

    def __init__(self, service_key: str, timeout: int = 20):
        self.service_key = service_key
        self.timeout = timeout

    def _get(self, url: str, params: dict[str, Any]) -> tuple[list[dict], dict]:
        """공공데이터포털은 오류 시에도 200이 아닌 상태코드에 파싱 가능한 오류 본문을
        함께 주는 경우가 흔하다(예: KINFA 대출상품한눈에는 HTTP 400 + XML
        NO_OPENAPI_SERVICE_ERROR 본문). raise_for_status로 바로 예외를 던지면 이 본문을
        볼 수 없으므로, 상태코드와 무관하게 본문을 먼저 파싱해 정상/오류를 함께 처리한다.
        """
        merged = {"serviceKey": self.service_key, **params}
        resp = requests.get(url, params=merged, timeout=self.timeout)
        ctype = resp.headers.get("Content-Type", "")
        if "json" in ctype.lower():
            try:
                data = resp.json()
            except ValueError:
                logger.warning("datago: JSON 파싱 실패 (status=%s)", resp.status_code)
                return [], {}
            if "OpenAPI_ServiceResponse" in data:
                header = data["OpenAPI_ServiceResponse"].get("cmmMsgHeader", {})
                logger.warning("datago API 오류: %s", header.get("errMsg", "unknown"))
                return [], header
            response = data.get("response", {})
            header = response.get("header", {})
            body = response.get("body", {})
            items = body.get("items")
            if isinstance(items, dict):
                item = items.get("item", [])
                rows = item if isinstance(item, list) else ([item] if item else [])
            elif isinstance(items, list):
                rows = items
            else:
                single = body.get("item")
                rows = single if isinstance(single, list) else ([single] if single else [])
            return rows, header
        return _parse_xml(resp.text)

    def fetch_didimdol(self, page: int = 1, num_of_rows: int = 10) -> list[dict]:
        """주택금융공사 디딤돌대출금리정보(dataset 15082028)."""
        rows, _header = self._get(
            DIDIMDOL_URL, {"pageNo": page, "numOfRows": num_of_rows, "resultType": "json"}
        )
        _write_json(_cache_dir() / "datago_didimdol.json", rows)
        return rows

    def fetch_fsc_small_loan(self, num_of_rows: int = 100, max_pages: int = 5) -> list[dict]:
        """금융위원회 서민금융 상품 기본정보(dataset 15094787, op=getOrdinaryFinanceInfo).

        전체 10,000건 이상이라 PoC 규모에 맞춰 max_pages(기본 5 x 100건=500건)까지만 수집한다.
        """
        all_rows: list[dict] = []
        for page in range(1, max_pages + 1):
            rows, header = self._get(
                FSC_SMALL_LOAN_URL, {"pageNo": page, "numOfRows": num_of_rows, "type": "json"}
            )
            if not rows:
                break
            all_rows.extend(rows)
            total_count = _safe_int(header.get("totalCount")) if header else None
            if total_count is not None and len(all_rows) >= total_count:
                break
        _write_json(_cache_dir() / "datago_fsc_small_loan.json", all_rows)
        return all_rows

    def fetch_kinfa_loan_products(self, num_of_rows: int = 100, max_pages: int = 5) -> list[dict]:
        """서민금융진흥원 대출상품한눈에(dataset 15106208). 모듈 docstring의 (확인 필요) 참고.

        현재 NO_OPENAPI_SERVICE_ERROR가 나면 빈 리스트를 반환한다(예외를 던지지 않는다).
        """
        all_rows: list[dict] = []
        for page in range(1, max_pages + 1):
            rows, _header = self._get(
                KINFA_LOAN_PRODUCT_URL, {"pageNo": page, "numOfRows": num_of_rows}
            )
            if not rows:
                break
            all_rows.extend(rows)
        _write_json(_cache_dir() / "datago_kinfa_loan_products.json", all_rows)
        return all_rows


def normalize_didimdol(rows: list[dict], snapshot_id: str) -> list[ProductSnapshot]:
    """디딤돌대출 금리 row(보통 1건, 현재 시점 기준)를 ProductSnapshot으로 변환한다."""
    snapshots: list[ProductSnapshot] = []
    for idx, row in enumerate(rows):
        options: list[ProductOption] = []
        for key, value in row.items():
            m = _DIDIMDOL_FIELD_RE.match(key)
            if not m:
                continue
            rate = _safe_float(value)
            if rate is None:
                continue
            term_years, income_cap = m.group(1), m.group(2)
            options.append(ProductOption(
                rate=rate,
                rate_kind="base",
                term_months=int(term_years) * 12,
                rate_type=RateType.FIXED.value,
                note=f"소득 {income_cap}만원 이하",
                extra={"income_cap_10k_krw": int(income_cap)},
            ))
        apply_day = row.get("applyDy", "") or ""
        disclosure_month = apply_day[:6] if len(apply_day) >= 6 else ""
        snapshots.append(ProductSnapshot(
            id=f"{snapshot_id}:HF:DIDIMDOL:{idx}",
            snapshot_id=snapshot_id,
            source=ProductSource.DATAGO_HF,
            category=ProductCategory.MORTGAGE,
            lender_group=LenderGroup.POLICY,
            company_code="HF",
            company_name="한국주택금융공사",
            product_code="DIDIMDOL",
            product_name="디딤돌대출",
            rate_semantics=RateSemantics.OFFER_RATE,
            options=options,
            join_conditions="",
            max_amount=None,
            disclosure_month=disclosure_month,
            disclosure_url=DISCLOSURE_URL_DIDIMDOL,
            raw=row,
        ))
    return snapshots


def _parse_curated_rate(text: Optional[str]) -> tuple[Optional[float], str, dict[str, Any]]:
    """서민금융 상품의 자유서식 금리 텍스트(irt)를 최대한 보수적으로 숫자화한다.

    "a~b%" 범위는 평균을, "N%"/"~N%"/"N% 이내" 단일값은 그 값을 쓴다. 그 외(다중값 '/'
    나열, "은행별 상이" 같은 비수치 텍스트)는 임의로 추정하지 않고 rate=None, rate_kind="curated"로
    남기고 원문은 note에 그대로 보존한다.
    """
    if not text:
        return None, "curated", {}
    t = text.strip()
    m = _RATE_RANGE_RE.search(t)
    if m:
        lo, hi = float(m.group(1)), float(m.group(2))
        return round((lo + hi) / 2, 3), "avg", {"rate_min": lo, "rate_max": hi}
    m = _RATE_SINGLE_RE.match(t)
    if m:
        kind = "max" if ("이내" in t or "이하" in t or t.startswith("~")) else "base"
        return float(m.group(1)), kind, {}
    return None, "curated", {}


def _parse_max_amount_10k(text: Optional[str]) -> Optional[int]:
    """"3,000만원" 류 텍스트에서 최대 한도를 원 단위 정수로 추출한다. 확신 없으면 None."""
    if not text:
        return None
    plain = str(text).strip().replace(",", "")
    if plain.isdigit():  # KINFA lnlmt는 단위 없이 만원 숫자만 온다 (예: "2000")
        return int(plain) * 10_000
    matches = _AMOUNT_RE.findall(text)
    if not matches:
        return None
    try:
        values = [int(m.replace(",", "")) for m in matches]
    except ValueError:
        return None
    return max(values) * 10_000


def _normalize_policy_loan_rows(
    rows: list[dict], snapshot_id: str, source: ProductSource, disclosure_url: str
) -> list[ProductSnapshot]:
    """FSC 서민금융 상품 기본정보와 KINFA 대출상품한눈에는 필드 의미가 같지만 키 표기가 다르다.
    FSC: finPrdNm / lnLmt / rdptMthd / suprTgtDtlCond / basYm / snq (camelCase JSON)
    KINFA: finprdnm / lnlmt / rdptmthd / suprtgtdtlcond / seq / maxrdpttrm / usge (소문자 XML, 2026-09-06 실 응답 확인)
    키를 소문자로 정규화해 한 로직으로 처리한다.
    """
    snapshots: list[ProductSnapshot] = []
    for i, row in enumerate(rows):
        r = {str(k).lower(): v for k, v in row.items()}
        bas_ym = r.get("basym", "") or ""
        snq = r.get("snq", "") or r.get("seq", "") or ""
        product_code = f"{bas_ym}-{snq}" if (bas_ym or snq) else ""
        company_name = r.get("hdlinst") or r.get("ofrinstnm") or ""

        rate, rate_kind, rate_extra = _parse_curated_rate(r.get("irt"))
        repay = REPAY_METHOD_TEXT_MAP.get((r.get("rdptmthd") or "").strip())
        rtype = RATE_TYPE_TEXT_MAP.get((r.get("irtctg") or "").strip())
        term_months = None
        rdpt = _safe_int(r.get("maxrdpttrm"))
        if rdpt and 0 < rdpt <= 40:
            term_months = rdpt * 12  # KINFA maxrdpttrm은 연 단위
        option = ProductOption(
            rate=rate,
            rate_kind=rate_kind,
            term_months=term_months,
            repay_method=repay.value if repay else None,
            rate_type=rtype.value if rtype else None,
            note=r.get("irt") or "",
            extra=rate_extra,
        )

        parts: list[str] = []
        if r.get("usge"):
            parts.append(f"용도 {r['usge']}")
        if r.get("trgt"):
            parts.append(f"대상 {r['trgt']}")
        supr = r.get("suprtgtdtlcond")
        if supr and supr not in ("-", ""):
            parts.append(f"세부요건 {supr}")

        snapshots.append(ProductSnapshot(
            id=f"{snapshot_id}:{source.value}:{product_code}:{i}",
            snapshot_id=snapshot_id,
            source=source,
            category=ProductCategory.POLICY,
            lender_group=LenderGroup.POLICY,
            company_code="",
            company_name=company_name,
            product_code=product_code,
            product_name=r.get("finprdnm", "") or "",
            rate_semantics=RateSemantics.CURATED,
            options=[option],
            join_conditions="; ".join(parts),
            max_amount=_parse_max_amount_10k(r.get("lnlmt")),
            disclosure_month=bas_ym,
            disclosure_url=disclosure_url,
            raw=row,
        ))
    return snapshots


def normalize_fsc_small_loan(rows: list[dict], snapshot_id: str) -> list[ProductSnapshot]:
    """금융위원회 서민금융 상품 기본정보 -> POLICY, source DATAGO_FSC."""
    return _normalize_policy_loan_rows(rows, snapshot_id, ProductSource.DATAGO_FSC, DISCLOSURE_URL_FSC)


def normalize_kinfa_loan_products(rows: list[dict], snapshot_id: str) -> list[ProductSnapshot]:
    """서민금융진흥원 대출상품한눈에 -> POLICY, source DATAGO_KINFA."""
    return _normalize_policy_loan_rows(rows, snapshot_id, ProductSource.DATAGO_KINFA, DISCLOSURE_URL_KINFA)
