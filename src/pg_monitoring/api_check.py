"""나라장터 공식 API 연결 테스트: `python -m pg_monitoring.api_check`

입찰공고정보서비스와 사전규격정보서비스에서 최근 공고를 소량(기본 5건) 조회해
HTTP 상태, API 응답코드, 실제 공고 데이터 존재 여부를 확인합니다.

보안 원칙
- 인증키는 환경변수 `NARA_API_KEY`에서만 읽습니다. (GitHub Actions Secrets → 환경변수)
- 인증키는 전체든 일부든 화면·로그·오류 메시지에 절대 출력하지 않습니다.
  요청 URL에는 인증키가 들어 있으므로 URL은 쿼리 없이 경로만 출력하고,
  모든 출력은 `redact()`로 한 번 더 걸러냅니다.
- 인증키를 다루는 작업에서 외부 패키지를 설치하지 않도록 Python 표준 라이브러리만 사용합니다.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from pg_monitoring import publish

KST = timezone(timedelta(hours=9))
API_HOST = "https://apis.data.go.kr"
TIMEOUT_SECONDS = 30
DEFAULT_ROWS = 5
DEFAULT_DAYS = 7
KEY_ENV_NAMES = ("NARA_API_KEY", "G2B_SERVICE_KEY")  # G2B_SERVICE_KEY 는 이전 이름(호환용)


@dataclass(frozen=True)
class ApiSpec:
    name: str
    path: str  # 서비스 경로 + 오퍼레이션
    number_fields: tuple[str, ...]  # 공고번호를 만드는 필드 (앞에서부터 이어붙임)
    title_fields: tuple[str, ...]  # 공고명 후보 필드 (첫 번째로 값이 있는 것 사용)


# 공공데이터포털 '조달청_나라장터 입찰공고정보서비스' / '조달청_나라장터 사전규격정보서비스'
# ※ 요청 주소는 공공데이터포털 '활용신청 현황 → 서비스 상세 → 기능 미리보기' 창의 주소와 같아야 합니다.
#   입찰공고정보서비스는 버전 02(서비스·오퍼레이션 이름 끝에 02)로 승인되어 있어,
#   02가 없는 주소는 같은 키로도 '등록되지 않은 인증키(코드 30)'가 됩니다.
#   (이전 경로인 BidPublicInfoService04 등은 폐기됨)
APIS: tuple[ApiSpec, ...] = (
    ApiSpec(
        name="입찰공고정보서비스 (용역 입찰공고 목록)",
        path="/1230000/ad/BidPublicInfoService02/getBidPblancListInfoServc02",
        number_fields=("bidNtceNo", "bidNtceOrd"),
        title_fields=("bidNtceNm",),
    ),
    ApiSpec(
        name="사전규격정보서비스 (용역 사전규격 목록)",
        path="/1230000/ao/HrcspSsstndrdInfoService/getPublicPrcureThngInfoServc",
        number_fields=("bfSpecRgstNo",),
        title_fields=("prdctClsfcNoNm", "bsnsNm", "prdctClsfcNm"),
    ),
)

# 공공데이터포털 공통 오류코드(returnReasonCode) → (원인, 해결 방법)
GATEWAY_ERRORS: dict[str, tuple[str, str]] = {
    "1": ("API 서버 내부 오류(APPLICATION_ERROR)", "잠시 후 다시 실행하세요. 계속되면 공공데이터포털에 문의하세요."),
    "4": ("HTTP 오류(HTTP_ERROR)", "잠시 후 다시 실행하세요."),
    "10": ("잘못된 요청 파라미터(INVALID_REQUEST_PARAMETER_ERROR)", "요청 파라미터 형식을 확인해야 합니다. 개발자에게 문의하세요."),
    "12": ("해당 API 서비스가 없거나 폐기됨(NO_OPENAPI_SERVICE_ERROR)", "API 주소(오퍼레이션)가 변경되었을 수 있습니다. 공공데이터포털의 최신 명세를 확인하세요."),
    "20": ("서비스 접근 거부(SERVICE_ACCESS_DENIED_ERROR) - 인증키가 전달되지 않았거나 이 API 활용신청이 안 되어 있음",
           "GitHub Secrets에 NARA_API_KEY가 등록되어 있는지, 공공데이터포털에서 해당 API 활용신청이 '승인' 상태인지 확인하세요."),
    "22": ("일일 요청 한도 초과(LIMITED_NUMBER_OF_SERVICE_REQUESTS_EXCEEDS_ERROR)", "내일 다시 실행하거나 공공데이터포털에서 트래픽 증가를 신청하세요."),
    "30": ("등록되지 않은 인증키(SERVICE_KEY_IS_NOT_REGISTERED_ERROR)",
           "공공데이터포털 마이페이지의 '일반 인증키(Decoding)'를 공백 없이 그대로 NARA_API_KEY에 다시 등록하세요. "
           "신규 발급·활용신청 직후라면 반영까지 1~2시간 걸릴 수 있습니다."),
    "31": ("인증키 사용기간 만료(DEADLINE_HAS_EXPIRED_ERROR)", "공공데이터포털에서 활용기간을 연장하세요."),
    "32": ("등록되지 않은 IP(UNREGISTERED_IP_ERROR)", "공공데이터포털 활용신청 정보에서 IP 제한을 해제하세요."),
    "99": ("기타 오류(UNKNOWN_ERROR)", "잠시 후 다시 실행하세요."),
}

# 서비스 응답코드(header.resultCode) → (원인, 해결 방법). "00" 이 정상.
RESULT_ERRORS: dict[str, tuple[str, str]] = {
    "01": ("API 서버 내부 오류", "잠시 후 다시 실행하세요."),
    "03": ("조회 기간에 데이터 없음", "조회 기간을 늘려 다시 실행하세요."),
    "06": ("날짜 형식 오류", "개발자에게 문의하세요. (inqryBgnDt/inqryEndDt 는 YYYYMMDDHHMM 형식)"),
    "07": ("조회 기간 초과", "조회 기간(일수)을 줄여서 다시 실행하세요."),
    "08": ("필수 파라미터 누락", "개발자에게 문의하세요."),
    "10": ("잘못된 요청 파라미터", "개발자에게 문의하세요."),
    "12": ("해당 API 서비스가 없거나 폐기됨", "공공데이터포털의 최신 명세를 확인하세요."),
    "20": ("서비스 접근 거부", "공공데이터포털에서 해당 API 활용신청 상태를 확인하세요."),
    "22": ("일일 요청 한도 초과", "내일 다시 실행하세요."),
    "30": ("등록되지 않은 인증키", "NARA_API_KEY 값을 다시 확인하세요."),
    "31": ("인증키 사용기간 만료", "공공데이터포털에서 활용기간을 연장하세요."),
    "32": ("등록되지 않은 IP", "활용신청 정보에서 IP 제한을 해제하세요."),
}


# ---------------------------------------------------------------------------
# 인증키 처리
# ---------------------------------------------------------------------------
def load_service_key() -> str:
    for name in KEY_ENV_NAMES:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return ""


def normalize_service_key(key: str) -> str:
    """인증키를 '디코딩된 원래 값'으로 통일해 중복 URL 인코딩을 막는다.

    공공데이터포털은 같은 키를 Encoding(예: `%2B`, `%3D`)/Decoding(예: `+`, `=`) 두 형태로 제공한다.
    Encoding 키를 그대로 쓰면 요청 시 `%` 가 다시 `%25` 로 인코딩되어(이중 인코딩) 인증에 실패하므로,
    여기서 먼저 디코딩하고 요청 URL을 만들 때 정확히 한 번만 인코딩한다.
    (`unquote` 는 `+` 를 공백으로 바꾸지 않으며, Decoding 키에는 `%` 가 없으므로 그대로 유지된다.)
    """
    key = key.strip()
    if "%" in key:
        key = urllib.parse.unquote(key)
    return key


def build_url(path: str, params: dict[str, str], service_key: str) -> str:
    """serviceKey 를 포함한 요청 URL. 모든 값은 여기서 '한 번만' 인코딩된다."""
    query = {"serviceKey": normalize_service_key(service_key), **params}
    return f"{API_HOST}{path}?{urllib.parse.urlencode(query, quote_via=urllib.parse.quote, safe='')}"


def redact(text: str, service_key: str) -> str:
    """텍스트에서 인증키(원본/디코딩/인코딩 형태 모두)를 지운다."""
    if not service_key:
        return text
    raw = service_key.strip()
    decoded = normalize_service_key(raw)
    variants = {raw, decoded, urllib.parse.quote(decoded, safe=""), urllib.parse.quote_plus(decoded)}
    for v in sorted((v for v in variants if v), key=len, reverse=True):
        text = text.replace(v, "[인증키-숨김]")
        text = text.replace(v.lower(), "[인증키-숨김]")
    # 혹시 남은 serviceKey=... 쿼리값도 제거
    out, i, marker = [], 0, "serviceKey="
    while True:
        j = text.find(marker, i)
        if j < 0:
            out.append(text[i:])
            break
        out.append(text[i : j + len(marker)] + "[인증키-숨김]")
        k = j + len(marker)
        while k < len(text) and text[k] not in "&\"' \n\t<>":
            k += 1
        i = k
    return "".join(out)


# ---------------------------------------------------------------------------
# 응답 해석
# ---------------------------------------------------------------------------
@dataclass
class CheckResult:
    api_name: str
    endpoint: str  # 쿼리(인증키) 없는 경로만
    success: bool = False
    http_status: int | None = None
    result_code: str | None = None
    result_msg: str | None = None
    total_count: int | None = None
    examples: list[tuple[str, str]] = field(default_factory=list)
    items: list[dict] = field(default_factory=list, repr=False)  # 원본 응답(출력 금지, 공개 파일은 화이트리스트로만 생성)
    raw_snippet: str | None = None  # 해석 불가 응답의 앞부분 (출력 전 인증키 제거됨)
    error_cause: str | None = None
    error_fix: str | None = None


def _find_gateway_error(body: str) -> tuple[str, str] | None:
    """공공데이터포털 게이트웨이 오류(OpenAPI_ServiceResponse, XML/JSON)를 찾아 (코드, 메시지) 반환."""
    if "OpenAPI_ServiceResponse" not in body:
        return None
    try:
        hdr = json.loads(body)["OpenAPI_ServiceResponse"]["cmmMsgHeader"]
        return str(hdr.get("returnReasonCode", "")).strip(), str(hdr.get("errMsg", "")).strip()
    except (ValueError, KeyError, TypeError):
        pass
    try:
        root = ET.fromstring(body)
        return (root.findtext(".//returnReasonCode") or "").strip(), (root.findtext(".//errMsg") or "").strip()
    except ET.ParseError:
        return "", ""


def _items_as_list(items) -> list[dict]:
    """body.items 는 리스트, {"item": [...]}, {"item": {...}}, "" 등 여러 형태로 올 수 있다."""
    if isinstance(items, dict):
        items = items.get("item", [])
    if isinstance(items, dict):
        items = [items]
    if not isinstance(items, list):
        return []
    return [i for i in items if isinstance(i, dict)]


def _example(spec: ApiSpec, item: dict) -> tuple[str, str]:
    number = "-".join(str(item.get(f, "")).strip() for f in spec.number_fields if str(item.get(f, "")).strip())
    title = next((str(item[f]).strip() for f in spec.title_fields if str(item.get(f, "")).strip()), "")
    return number or "(공고번호 없음)", title or "(공고명 없음)"


def _parse_json_response(body: str) -> tuple[dict, dict] | None:
    try:
        response = json.loads(body)["response"]
        return response.get("header", {}) or {}, response.get("body") or {}
    except (ValueError, KeyError, TypeError, AttributeError):
        return None


def _parse_xml_response(body: str) -> tuple[dict, dict] | None:
    """type=json 을 무시하고 XML로 답하는 경우: <response><header>..</header><body><items><item>.. 형태."""
    try:
        root = ET.fromstring(body.strip())
    except ET.ParseError:
        return None
    header_el = root.find(".//header")
    if header_el is None:
        return None
    header = {c.tag: (c.text or "").strip() for c in header_el}
    body_el = root.find(".//body")
    body_obj: dict = {}
    if body_el is not None:
        body_obj["totalCount"] = (body_el.findtext("totalCount") or "").strip()
        body_obj["items"] = [
            {f.tag: (f.text or "").strip() for f in item} for item in body_el.iter("item")
        ]
    return header, body_obj


def interpret(spec: ApiSpec, http_status: int, body: str, result: CheckResult) -> CheckResult:
    result.http_status = http_status

    gw = _find_gateway_error(body)
    if gw is not None:
        code, msg = gw
        result.result_code = code or None
        result.result_msg = msg or None
        result.error_cause, result.error_fix = GATEWAY_ERRORS.get(
            code, (f"공공데이터포털 오류({msg or '알 수 없음'})", "잠시 후 다시 실행하세요.")
        )
        return result

    if http_status != 200:
        result.error_cause = f"HTTP 오류 (상태코드 {http_status})"
        result.error_fix = "API 서버가 일시적으로 응답하지 않을 수 있습니다. 잠시 후 다시 실행하세요."
        return result

    parsed = _parse_json_response(body) or _parse_xml_response(body)
    if parsed is None:
        result.error_cause = "응답 형식을 해석할 수 없음 (JSON/XML 형식이 아님)"
        result.error_fix = "아래 '응답 앞부분'을 개발자에게 전달하세요."
        result.raw_snippet = " ".join(body.split())[:400] or "(빈 응답)"
        return result
    header, body_obj = parsed

    result.result_code = str(header.get("resultCode", "")).strip() or None
    result.result_msg = str(header.get("resultMsg", "")).strip() or None
    if result.result_code != "00":
        result.error_cause, result.error_fix = RESULT_ERRORS.get(
            result.result_code or "",
            (f"API 응답 오류 ({result.result_msg or '메시지 없음'})", "잠시 후 다시 실행하세요."),
        )
        return result

    try:
        result.total_count = int(body_obj.get("totalCount") or 0)
    except (TypeError, ValueError):
        result.total_count = None
    items = _items_as_list(body_obj.get("items"))
    result.items = items
    result.examples = [_example(spec, i) for i in items]

    if not items:
        result.error_cause = "연결과 인증은 정상이지만 조회 기간에 공고 데이터가 없음"
        result.error_fix = "조회 기간(일수)을 늘려서 다시 실행하세요."
        return result

    result.success = True
    return result


# ---------------------------------------------------------------------------
# 실행
# ---------------------------------------------------------------------------
def query_params(now: datetime, days: int, rows: int) -> dict[str, str]:
    begin = now - timedelta(days=days)
    return {
        "pageNo": "1",
        "numOfRows": str(rows),
        "type": "json",
        "inqryDiv": "1",  # 1: 등록(게시)일시 기준 조회
        "inqryBgnDt": begin.strftime("%Y%m%d%H%M"),
        "inqryEndDt": now.strftime("%Y%m%d%H%M"),
    }


def check_api(spec: ApiSpec, service_key: str, params: dict[str, str]) -> CheckResult:
    result = CheckResult(api_name=spec.name, endpoint=f"{API_HOST}{spec.path}")
    request = urllib.request.Request(
        build_url(spec.path, params, service_key),
        headers={"Accept": "application/json", "User-Agent": "pg-monitoring-api-check"},
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as resp:
            status, body = resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:  # 4xx/5xx 도 본문에 오류코드가 들어 있으므로 해석한다
        status = e.code
        try:
            body = e.read().decode("utf-8", errors="replace")
        except Exception:
            body = ""
    except urllib.error.URLError as e:
        result.error_cause = f"네트워크 연결 실패 ({redact(str(e.reason), service_key)})"
        result.error_fix = "GitHub Actions 또는 API 서버의 일시적 네트워크 문제일 수 있습니다. 잠시 후 다시 실행하세요."
        return result
    except TimeoutError:
        result.error_cause = f"응답 시간 초과 ({TIMEOUT_SECONDS}초)"
        result.error_fix = "API 서버가 느리게 응답하고 있습니다. 잠시 후 다시 실행하세요."
        return result
    except Exception as e:  # 예외 메시지에 URL(인증키 포함)이 섞일 수 있으므로 종류만 표시
        result.error_cause = f"예상하지 못한 오류 ({type(e).__name__})"
        result.error_fix = "잠시 후 다시 실행하고, 계속되면 개발자에게 문의하세요."
        return result
    return interpret(spec, status, body, result)


def adjust_for_shared_key(results: list[CheckResult]) -> list[CheckResult]:
    """두 API는 같은 인증키를 쓴다. 한쪽이 성공(=키 정상)했는데 다른 쪽이 인증 오류라면
    키 문제가 아니라 그 API의 활용신청(승인) 문제이므로 안내를 바꾼다."""
    if not any(r.http_status == 200 and r.result_code == "00" for r in results):
        return results
    for r in results:
        if not r.success and r.result_code in ("20", "30"):
            r.error_cause = (
                "인증키는 정상입니다(다른 API는 같은 키로 성공). "
                "이 API에 대한 활용신청이 없거나, 승인이 아직 반영되지 않았습니다."
            )
            r.error_fix = (
                "공공데이터포털 → 마이페이지 → 데이터활용 → Open API → 활용신청 현황에서 "
                f"'{r.api_name.split(' (')[0]}'가 '승인' 상태인지 확인하세요. "
                "없으면 해당 API 페이지에서 '활용신청'을 하고, 승인 직후라면 1~2시간 뒤 다시 실행하세요."
            )
    return results


# 인증 오류가 한 API에서만 날 때, 같은 서비스의 다른 오퍼레이션도 같은 키로 시도해
# '서비스 전체 문제'인지 '특정 오퍼레이션 문제'인지 가린다. (결과코드만 출력)
DIAGNOSE_OPERATIONS: dict[str, tuple[str, ...]] = {
    "/1230000/ad/BidPublicInfoService02": (
        "getBidPblancListInfoServc02", "getBidPblancListInfoThng02", "getBidPblancListInfoCnstwk02",
        "getBidPblancListInfoEtc02", "getBidPblancListInfoServcPPSSrch02",
    ),
    "/1230000/ao/HrcspSsstndrdInfoService": (
        "getPublicPrcureThngInfoServc", "getPublicPrcureThngInfoThng", "getPublicPrcureThngInfoCnstwk",
    ),
}


def needs_diagnosis(results: list[CheckResult]) -> list[CheckResult]:
    """다른 API는 성공했는데 인증 오류(20/30)가 난 결과 목록."""
    if not any(r.success for r in results):
        return []
    return [r for r in results if not r.success and r.result_code in ("20", "30")]


def diagnose(service_key: str, params: dict[str, str], failed: list[CheckResult]) -> str:
    lines = ["", "■ 추가 진단 (같은 인증키로 같은 서비스의 다른 오퍼레이션 시도, 결과코드만 표시)"]
    probe = {**params, "numOfRows": "1"}
    for r in failed:
        path = r.endpoint.removeprefix(API_HOST)
        service = path.rsplit("/", 1)[0]
        codes = []
        for op in DIAGNOSE_OPERATIONS.get(service, ()):
            spec = ApiSpec(name=op, path=f"{service}/{op}", number_fields=(), title_fields=())
            pr = check_api(spec, service_key, probe)
            code = pr.result_code or "-"
            codes.append(code)
            lines.append(f"  {service}/{op}: HTTP {pr.http_status or '-'} / 코드 {code}")
        codes = [c for c in codes if c != "-"]  # 네트워크 오류로 응답이 없던 시도는 판정에서 제외
        if codes and all(c in ("20", "30") for c in codes):
            lines.append("  → 판정: 이 서비스 주소의 모든 오퍼레이션에서 같은 키가 거부됩니다.")
            lines.append("    해결 1: 공공데이터포털 활용신청 현황 → 이 서비스 상세 → 기능 '확인'(미리보기)으로 호출한 뒤, "
                         "결과 창 주소의 서비스·오퍼레이션 이름(예: 끝의 02 같은 버전 번호)이 위 요청 주소와 같은지 비교하세요. "
                         "다르면 api_check.py 의 APIS 주소를 포털과 같게 고치면 됩니다.")
            lines.append("    해결 2: 주소가 같은데도 포털에서만 성공하면, 공공데이터포털 1:1 문의(오류신고)에 접수하세요. "
                         "(인증키는 문의에 적지 마세요)")
        elif any(c == "00" or c == "03" for c in codes):
            lines.append("  → 판정: 일부 오퍼레이션은 정상입니다. 사용하는 오퍼레이션 문제이므로 개발자에게 이 진단 결과를 전달하세요.")
    return "\n".join(lines)


def format_report(results: list[CheckResult], params: dict[str, str]) -> str:
    lines = [
        "=" * 60,
        "나라장터 API 연결 테스트 결과",
        f"조회 기간: {params['inqryBgnDt']} ~ {params['inqryEndDt']} (한국시간, 등록일시 기준)",
        f"요청 건수: API별 최대 {params['numOfRows']}건",
        "=" * 60,
    ]
    for r in results:
        lines += [
            "",
            f"■ API 이름      : {r.api_name}",
            f"  요청 주소     : {r.endpoint}",
            f"  연결 성공 여부: {'✅ 성공' if r.success else '❌ 실패'}",
            f"  HTTP 상태     : {r.http_status if r.http_status is not None else '응답 없음'}",
            f"  API 응답코드  : {r.result_code or '-'} {('(' + r.result_msg + ')') if r.result_msg else ''}".rstrip(),
            f"  조회된 공고 건수: 전체 {r.total_count if r.total_count is not None else '-'}건 중 {len(r.examples)}건 수신",
        ]
        if r.examples:
            lines.append("  공고 예시:")
            lines += [f"    - [{no}] {title}" for no, title in r.examples]
        if r.items:
            kind = publish.api_kind(r.endpoint)
            names = sorted(set().union(*(i.keys() for i in r.items)))
            missing = publish.missing_public_fields(kind, r.items)
            lines.append(f"  응답 필드 목록(값 제외, 명세 확인용): {', '.join(names)}")
            lines.append(f"  공개 항목 중 응답에 없는 필드: {', '.join(missing) if missing else '없음'}")
        if r.error_cause:
            lines += [f"  오류 원인     : {r.error_cause}", f"  해결 방법     : {r.error_fix}"]
        if r.raw_snippet:
            lines.append(f"  응답 앞부분   : {r.raw_snippet}")
    ok = sum(r.success for r in results)
    lines += ["", "=" * 60, f"종합: {len(results)}개 API 중 {ok}개 정상", "=" * 60]
    return "\n".join(lines)


def _int_env(name: str, default: int, low: int, high: int) -> int:
    try:
        value = int(os.environ.get(name, "").strip() or default)
    except ValueError:
        value = default
    return max(low, min(value, high))


def main(argv: list[str] | None = None) -> int:
    days = _int_env("CHECK_DAYS", DEFAULT_DAYS, 1, 30)
    rows = _int_env("CHECK_ROWS", DEFAULT_ROWS, 1, 20)

    service_key = load_service_key()
    if not service_key:
        print("❌ 인증키가 설정되지 않았습니다.")
        print("   원인: 환경변수 NARA_API_KEY 가 비어 있습니다.")
        print("   해결: GitHub 저장소 Settings → Secrets and variables → Actions 에 NARA_API_KEY 를 등록하세요.")
        return 2

    now = datetime.now(KST).replace(second=0, microsecond=0)
    params = query_params(now, days, rows)
    results = adjust_for_shared_key([check_api(spec, service_key, params) for spec in APIS])
    all_ok = all(r.success for r in results)

    site_dir = os.environ.get("SITE_DIR", "").strip()
    site_line = ""
    if site_dir and all_ok:
        written = publish.write_site(results, now, params, Path(site_dir), scrub=lambda t: redact(t, service_key))
        site_line = f"공개용 샘플 파일 생성: {', '.join(str(p) for p in written)}"
    elif site_dir:
        site_line = "공개용 샘플 파일 생성: 건너뜀 (두 API가 모두 성공해야 생성합니다)"

    report = format_report(results, params)
    failed = needs_diagnosis(results)
    if failed:
        report += "\n" + diagnose(service_key, params, failed)
    report = redact(report, service_key)
    if site_line:
        report += "\n" + site_line
    print(report)

    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as f:
            f.write("## 나라장터 API 연결 테스트 결과\n\n```text\n" + report + "\n```\n")

    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
