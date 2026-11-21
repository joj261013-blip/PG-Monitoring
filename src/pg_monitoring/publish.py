"""공개용 결과 파일(JSON / HTML) 생성.

API 응답 중 '공개해도 되는 항목'(아래 PUBLIC_FIELDS)만 골라 `data.json`, `index.html`을 만듭니다.
- 허용 목록(화이트리스트) 방식: 목록에 없는 필드는 절대 파일에 들어가지 않습니다.
  (담당자 이름·전화번호·이메일, 첨부파일 주소 등은 제외)
- HTML은 모든 값을 이스케이프하고, 스크립트를 전혀 쓰지 않습니다.
- Python 표준 라이브러리만 사용합니다.

공개 항목을 바꾸려면 PUBLIC_FIELDS 의 줄을 추가/삭제하면 됩니다.
(자세한 기준은 docs/publishing.md 참고)
"""

from __future__ import annotations

import html
import json
import re
import urllib.parse
from datetime import datetime
from pathlib import Path

MAX_TEXT = 300  # 한 항목 최대 글자 수
ALLOWED_LINK_HOSTS = ("g2b.go.kr",)  # 링크는 나라장터 주소만 허용

# API 경로(spec.path)별 공개 항목: (API 필드명, JSON 키, 화면 표시 이름)
PUBLIC_FIELDS: dict[str, tuple[tuple[str, str, str], ...]] = {
    "bid": (
        ("bidNtceNo", "bid_no", "공고번호"),
        ("bidNtceOrd", "bid_ord", "차수"),
        ("ntceKindNm", "notice_kind", "공고종류"),
        ("reNtceYn", "re_notice", "재공고여부"),
        ("bidNtceNm", "title", "공고명"),
        ("ntceInsttNm", "notice_org", "공고기관"),
        ("dminsttNm", "demand_org", "수요기관"),
        ("bidNtceDt", "notice_dt", "공고일시"),
        ("bidClseDt", "close_dt", "입찰마감일시"),
        ("opengDt", "open_dt", "개찰일시"),
        ("presmptPrce", "est_price", "추정가격(원)"),
        ("asignBdgtAmt", "budget", "배정예산(원)"),
        ("bidNtceDtlUrl", "url", "상세보기"),
    ),
    "prespec": (
        ("bfSpecRgstNo", "prespec_no", "사전규격등록번호"),
        ("prdctClsfcNoNm", "title", "품명(사업명)"),
        ("orderInsttNm", "order_org", "발주기관"),
        ("rlDminsttNm", "demand_org", "실수요기관"),
        ("rgstDt", "registered_dt", "등록일시"),
        ("rcptDt", "receipt_dt", "접수일시"),
        ("opninRgstClseDt", "opinion_close_dt", "의견등록마감일시"),
        ("asignBdgtAmt", "budget", "배정예산(원)"),
        ("bidNtceNoList", "related_bid_nos", "관련 입찰공고번호"),
        ("chgDt", "changed_dt", "변경일시"),
    ),
}
LINK_KEYS = {"url"}
MONEY_KEYS = {"est_price", "budget"}


def api_kind(path: str) -> str:
    return "prespec" if "HrcspSsstndrdInfoService" in path else "bid"


def _clean_text(value) -> str:
    text = re.sub(r"\s+", " ", str(value if value is not None else "")).strip()
    return text[:MAX_TEXT]


def _safe_link(value) -> str:
    url = _clean_text(value)
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        return ""
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" and parts.scheme != "http":
        return ""
    if not any(host == h or host.endswith("." + h) for h in ALLOWED_LINK_HOSTS):
        return ""
    return url


def to_public_record(kind: str, item: dict) -> dict[str, str]:
    """허용된 항목만 남긴 공개용 레코드."""
    record: dict[str, str] = {}
    for api_field, key, _label in PUBLIC_FIELDS[kind]:
        value = item.get(api_field, "")
        record[key] = _safe_link(value) if key in LINK_KEYS else _clean_text(value)
    return record


def missing_public_fields(kind: str, items: list[dict]) -> list[str]:
    """공개 항목 중 실제 응답에 한 번도 나오지 않은 API 필드명 (명세 확인용)."""
    seen = set().union(*(i.keys() for i in items)) if items else set()
    return [f for f, _k, _l in PUBLIC_FIELDS[kind] if f not in seen]


def build_data(results, generated_at: datetime, params: dict[str, str]) -> dict:
    """results: api_check.CheckResult 목록 (items 에 원본 응답 보관)."""
    apis = []
    for r in results:
        kind = api_kind(r.endpoint)
        apis.append({
            "api": r.api_name,
            "kind": kind,
            "success": r.success,
            "total_count": r.total_count,
            "fields": [{"key": k, "label": label} for _f, k, label in PUBLIC_FIELDS[kind]],
            "items": [to_public_record(kind, i) for i in r.items],
        })
    return {
        "title": "나라장터 PG 모니터링 - API 연결 테스트 샘플",
        "stage": "sample",
        "notice": "API 연결 확인용 소량 샘플입니다. PG 관련 선별이 적용되지 않은 최근 용역 공고입니다.",
        "verification_code": generated_at.strftime("NARA-%Y%m%d-%H%M"),
        "generated_at": generated_at.isoformat(timespec="seconds"),
        "query": {
            "basis": "등록일시",
            "begin": params.get("inqryBgnDt", ""),
            "end": params.get("inqryEndDt", ""),
            "rows_per_api": params.get("numOfRows", ""),
        },
        "source": "공공데이터포털 조달청_나라장터 입찰공고정보서비스 / 사전규격정보서비스",
        "apis": apis,
    }


def _fmt_money(value: str) -> str:
    try:
        return f"{int(float(value)):,}"
    except (TypeError, ValueError):
        return value


def build_html(data: dict) -> str:
    e = html.escape
    out = [
        "<!doctype html>",
        '<html lang="ko">',
        "<head>",
        '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        "<meta http-equiv=\"Content-Security-Policy\" content=\"default-src 'none'; style-src 'unsafe-inline'\">",
        '<meta name="robots" content="noindex, nofollow">',
        '<meta name="referrer" content="no-referrer">',
        f"<title>{e(data['title'])}</title>",
        "<style>body{font-family:sans-serif;max-width:1100px;margin:0 auto;padding:16px;line-height:1.5;color:#111;background:#fff}"
        "table{border-collapse:collapse;width:100%;font-size:14px}th,td{border:1px solid #ccc;padding:4px 6px;text-align:left;vertical-align:top}"
        "th{background:#f2f2f2}.ok{color:#0a7a2f}.fail{color:#b00020}.wrap{overflow-x:auto}</style>",
        "</head>",
        "<body>",
        f"<h1>{e(data['title'])}</h1>",
        f"<p>{e(data['notice'])}</p>",
        "<ul>",
        f"<li>확인코드: <strong>{e(data['verification_code'])}</strong></li>",
        f"<li>생성일시(한국시간): {e(data['generated_at'])}</li>",
        f"<li>조회 기간({e(data['query']['basis'])} 기준): {e(data['query']['begin'])} ~ {e(data['query']['end'])}</li>",
        f"<li>출처: {e(data['source'])}</li>",
        '<li>기계가 읽는 형식: <a href="data.json">data.json</a></li>',
        "</ul>",
    ]
    for api in data["apis"]:
        status = '<span class="ok">정상</span>' if api["success"] else '<span class="fail">실패</span>'
        total = api["total_count"] if api["total_count"] is not None else "-"
        out += [
            f"<h2>{e(api['api'])}</h2>",
            f"<p>연결 상태: {status} · 조회 기간 전체 {e(str(total))}건 중 {len(api['items'])}건 표시</p>",
        ]
        if not api["items"]:
            out.append("<p>표시할 공고가 없습니다.</p>")
            continue
        out += ['<div class="wrap"><table>', "<thead><tr>"]
        out += [f"<th>{e(f['label'])}</th>" for f in api["fields"]]
        out += ["</tr></thead>", "<tbody>"]
        for item in api["items"]:
            cells = []
            for f in api["fields"]:
                value = item.get(f["key"], "")
                if f["key"] in LINK_KEYS:
                    cell = f'<a href="{e(value)}" rel="noopener noreferrer">나라장터</a>' if value else ""
                elif f["key"] in MONEY_KEYS:
                    cell = e(_fmt_money(value))
                else:
                    cell = e(value)
                cells.append(f"<td>{cell}</td>")
            out.append("<tr>" + "".join(cells) + "</tr>")
        out += ["</tbody>", "</table></div>"]
    out += ["</body>", "</html>", ""]
    return "\n".join(out)


def write_site(results, generated_at: datetime, params: dict[str, str], out_dir: Path,
               scrub=lambda s: s) -> list[Path]:
    """data.json, index.html 을 out_dir 에 쓴다. scrub: 출력 직전 한 번 더 거르는 함수(인증키 제거)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    data = build_data(results, generated_at, params)
    files = {
        out_dir / "data.json": json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        out_dir / "index.html": build_html(data),
    }
    for path, text in files.items():
        path.write_text(scrub(text), encoding="utf-8")
    return list(files)
