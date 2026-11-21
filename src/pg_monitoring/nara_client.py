"""나라장터 API 호출: 한 페이지 요청(재시도 포함)과 조회 구간 전체 페이지 수집.

인증키는 인자로만 받고, 어떤 메시지에도 넣지 않습니다. (오류 메시지는 종류·코드만 기록)
Python 표준 라이브러리만 사용합니다.
"""

from __future__ import annotations

import json
import math
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

from pg_monitoring.api_check import (
    API_HOST,
    _find_gateway_error,
    _items_as_list,
    _parse_json_response,
    _parse_xml_response,
    build_url,
)

TIMEOUT_SECONDS = 30
# 다시 시도해 볼 만한(일시적) 오류코드
RETRYABLE_GATEWAY = {"1", "4", "99"}
RETRYABLE_RESULT = {"01", "99"}
NO_DATA_CODE = "03"


@dataclass
class PageResult:
    ok: bool
    http_status: int | None = None
    result_code: str | None = None
    result_msg: str | None = None
    total_count: int | None = None
    items: list[dict] = field(default_factory=list, repr=False)
    error: str | None = None  # 사람이 읽는 오류 요약 (인증키 없음)
    retryable: bool = False


def _http_get(url: str) -> tuple[int, str]:
    req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "pg-monitoring"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        try:
            body = e.read().decode("utf-8", errors="replace")
        except Exception:
            body = ""
        return e.code, body


def parse_page(http_status: int, body: str) -> PageResult:
    gw = _find_gateway_error(body)
    if gw is not None:
        code, msg = gw
        return PageResult(False, http_status, code or None, msg or None,
                          error=f"공공데이터포털 오류 코드 {code} ({msg})", retryable=code in RETRYABLE_GATEWAY)
    if http_status >= 500:
        return PageResult(False, http_status, error=f"HTTP {http_status}", retryable=True)
    if http_status != 200:
        return PageResult(False, http_status, error=f"HTTP {http_status}")
    parsed = _parse_json_response(body) or _parse_xml_response(body)
    if parsed is None:
        snippet = " ".join(body.split())[:200]
        return PageResult(False, http_status, error=f"응답 해석 불가: {snippet}", retryable=True)
    header, body_obj = parsed
    code = str(header.get("resultCode", "")).strip()
    msg = str(header.get("resultMsg", "")).strip()
    if code == NO_DATA_CODE:
        return PageResult(True, http_status, code, msg, total_count=0)
    if code != "00":
        return PageResult(False, http_status, code, msg, error=f"응답코드 {code} ({msg})",
                          retryable=code in RETRYABLE_RESULT)
    try:
        total = int(body_obj.get("totalCount") or 0)
    except (TypeError, ValueError):
        total = None
    return PageResult(True, http_status, code, msg, total_count=total, items=_items_as_list(body_obj.get("items")))


def request_page(path: str, params: dict[str, str], service_key: str, *, retries: int = 3,
                 wait_seconds: float = 5.0, http_get=_http_get, sleep=time.sleep) -> PageResult:
    """한 페이지 요청. 일시적 오류는 retries 번까지 간격을 늘려가며 다시 시도한다."""
    url = build_url(path, params, service_key)
    last = PageResult(False, error="요청하지 않음")
    for attempt in range(retries + 1):
        try:
            status, body = http_get(url)
            last = parse_page(status, body)
        except urllib.error.URLError as e:
            last = PageResult(False, error=f"네트워크 오류 ({type(e.reason).__name__})", retryable=True)
        except TimeoutError:
            last = PageResult(False, error="응답 시간 초과", retryable=True)
        except Exception as e:  # 예외 메시지에 URL(인증키)이 섞일 수 있어 종류만 기록
            last = PageResult(False, error=f"예상하지 못한 오류 ({type(e).__name__})", retryable=True)
        if last.ok or not last.retryable or attempt == retries:
            return last
        sleep(wait_seconds * (attempt + 1))
    return last


@dataclass
class FetchResult:
    ok: bool  # 모든 페이지를 받았고 건수가 맞으면 True
    total_count: int = 0
    items: list[dict] = field(default_factory=list, repr=False)
    pages_expected: int = 0
    pages_fetched: int = 0
    calls: int = 0
    errors: list[str] = field(default_factory=list)


def fetch_all(path: str, base_params: dict[str, str], service_key: str, *, id_fields: tuple[str, ...],
              page_size: int = 100, max_pages: int = 300, retries: int = 3, wait_seconds: float = 5.0,
              requester=request_page) -> FetchResult:
    """조회 구간의 모든 페이지를 받는다.

    - 첫 페이지의 totalCount 로 전체 페이지 수를 계산해 끝까지 요청한다.
    - 페이지 사이에 데이터가 추가되면 같은 공고가 두 번 올 수 있으므로 id_fields 로 중복 제거한다.
    - 실패한 페이지가 있거나, 중복 제거 후 건수가 totalCount 보다 적으면 ok=False (일부 수집).
    """
    res = FetchResult(ok=False)
    seen: dict[str, dict] = {}
    page_no, pages_expected = 1, 1
    while page_no <= pages_expected:
        params = {**base_params, "pageNo": str(page_no), "numOfRows": str(page_size), "type": "json"}
        page = requester(path, params, service_key, retries=retries, wait_seconds=wait_seconds)
        res.calls += 1
        if not page.ok:
            res.errors.append(f"{page_no}페이지: {page.error}")
            # 첫 페이지가 실패하면 전체 건수를 알 수 없으므로 중단
            if page_no == 1:
                return res
            page_no += 1
            continue
        if page_no == 1:
            res.total_count = page.total_count or 0
            pages_expected = max(1, math.ceil(res.total_count / page_size))
            if pages_expected > max_pages:
                res.errors.append(f"전체 {pages_expected}페이지가 안전 한도 {max_pages}페이지를 넘어 일부만 수집")
                pages_expected = max_pages
            res.pages_expected = pages_expected
        res.pages_fetched += 1
        for item in page.items:
            key = "-".join(str(item.get(f, "")).strip() for f in id_fields)
            seen[key] = item
        page_no += 1
    res.items = list(seen.values())
    if len(res.items) < res.total_count and not res.errors:
        res.errors.append(f"수집 {len(res.items)}건 < 전체 {res.total_count}건 (조회 중 데이터 변동 가능)")
    res.ok = not res.errors
    return res


def describe_url(path: str) -> str:
    """로그용 주소 (쿼리·인증키 제외)."""
    return f"{API_HOST}{path}"


def dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2)
