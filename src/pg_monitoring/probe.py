"""API 명세 확인용 시험 조회: `python -m pg_monitoring.probe`

공공데이터포털 명세 페이지를 직접 볼 수 없는 환경에서, 실제 인증키로 조회 조건을 바꿔가며
응답코드·건수·필드명·날짜 범위만 출력해 동작을 확인한다. (공고 내용·인증키는 출력하지 않음)
GitHub Actions 의 '나라장터 API 연결 테스트' 실행 시 '명세 확인' 옵션으로 실행된다.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta

from pg_monitoring.api_check import KST, load_service_key, redact
from pg_monitoring.nara_client import request_page

BID = "/1230000/ad/BidPublicInfoService02/getBidPblancListInfoServc02"
BID_CHG = "/1230000/ad/BidPublicInfoService02/getBidPblancListInfoChgHstryServc02"
BID_SRCH = "/1230000/ad/BidPublicInfoService02/getBidPblancListInfoServcPPSSrch02"
PRE = "/1230000/ao/HrcspSsstndrdInfoService/getPublicPrcureThngInfoServc"
PRE_SRCH = "/1230000/ao/HrcspSsstndrdInfoService/getPublicPrcureThngInfoServcPPSSrch"
DATE_FIELDS = ("rgstDt", "bidNtceDt", "chgDt", "opengDt", "bidClseDt", "rcptDt", "opninRgstClseDt")


def _dt(d: datetime) -> str:
    return d.strftime("%Y%m%d%H%M")


def _summ(label: str, path: str, params: dict, key: str, show_fields: bool) -> list[str]:
    r = request_page(path, {"type": "json", "pageNo": "1", "numOfRows": "100", **params}, key, retries=1,
                     wait_seconds=3)
    shown = {k: v for k, v in params.items()}
    lines = [f"- {label}", f"    조건: {shown}",
             f"    결과: ok={r.ok} HTTP={r.http_status} 코드={r.result_code} 메시지={r.result_msg} "
             f"전체={r.total_count} 수신={len(r.items)}"]
    if r.error and not r.ok:
        lines.append(f"    오류: {r.error}")
    if r.items:
        for f in DATE_FIELDS:
            vals = sorted(str(i.get(f, "")).strip() for i in r.items if str(i.get(f, "")).strip())
            if vals:
                lines.append(f"    {f}: 최소 {vals[0]} / 최대 {vals[-1]} ({len(vals)}건)")
        if show_fields:
            names = sorted(set().union(*(i.keys() for i in r.items)))
            lines.append(f"    필드: {', '.join(names)}")
        if "ntceKindNm" in r.items[0]:
            kinds = sorted({str(i.get('ntceKindNm', '')) for i in r.items})
            lines.append(f"    ntceKindNm 값 종류: {kinds}")
        if "bsnsDivNm" in r.items[0]:
            kinds = sorted({str(i.get('bsnsDivNm', '')) for i in r.items})
            lines.append(f"    bsnsDivNm 값 종류: {kinds}")
        if "reNtceYn" in r.items[0]:
            kinds = sorted({str(i.get('reNtceYn', '')) for i in r.items})
            lines.append(f"    reNtceYn 값 종류: {kinds}")
        ords = sorted({str(i.get('bidNtceOrd', '')) for i in r.items if i.get('bidNtceOrd')})
        if ords:
            lines.append(f"    bidNtceOrd 값 종류: {ords[:10]}")
    return lines


def main() -> int:
    key = load_service_key()
    if not key:
        print("NARA_API_KEY 가 없습니다.")
        return 2
    now = datetime.now(KST).replace(second=0, microsecond=0)
    d1 = {"inqryBgnDt": _dt(now - timedelta(days=1)), "inqryEndDt": _dt(now)}
    d6 = {"inqryBgnDt": _dt(now - timedelta(days=6)), "inqryEndDt": _dt(now)}
    d30 = {"inqryBgnDt": _dt(now - timedelta(days=30)), "inqryEndDt": _dt(now)}
    out = ["=" * 60, f"API 명세 확인 (시각 {now:%Y-%m-%d %H:%M} KST)", "=" * 60]

    out.append("\n[1] 입찰공고 목록: 조회구분(inqryDiv)별 차이")
    for div in ("1", "2", "3", "4"):
        out += _summ(f"입찰공고 inqryDiv={div}", BID, {"inqryDiv": div, **d1}, key, show_fields=False)

    out.append("\n[2] 입찰공고 변경이력 조회 기능")
    for div in ("1", "2"):
        out += _summ(f"입찰공고 변경이력 inqryDiv={div}", BID_CHG, {"inqryDiv": div, **d1}, key, show_fields=True)

    out.append("\n[3] 사전규격 목록: 조회구분(inqryDiv)별 차이")
    for div in ("1", "2", "3"):
        out += _summ(f"사전규격 inqryDiv={div}", PRE, {"inqryDiv": div, **d1}, key, show_fields=(div == "1"))

    out.append("\n[4] 한 페이지 최대 건수")
    for rows in ("500", "999"):
        r = request_page(BID, {"type": "json", "pageNo": "1", "numOfRows": rows, "inqryDiv": "1", **d1}, key,
                         retries=1, wait_seconds=3)
        out.append(f"- numOfRows={rows}: ok={r.ok} 코드={r.result_code} 전체={r.total_count} 수신={len(r.items)}")

    out.append("\n[5] 키워드 검색 기능 (검증 사례용) 및 조회기간 한도")
    out += _summ("입찰공고 검색 bidNtceNm=세종문화회관 (6일)", BID_SRCH,
                 {"inqryDiv": "1", "bidNtceNm": "세종문화회관", **d6}, key, show_fields=False)
    out += _summ("입찰공고 검색 bidNtceNm=세종문화회관 (30일)", BID_SRCH,
                 {"inqryDiv": "1", "bidNtceNm": "세종문화회관", **d30}, key, show_fields=False)
    out += _summ("사전규격 목록 30일 (기간 한도 확인)", PRE, {"inqryDiv": "1", **d30}, key, show_fields=False)
    out += _summ("사전규격 검색 prdctClsfcNoNm=용역 (6일)", PRE_SRCH,
                 {"inqryDiv": "1", "prdctClsfcNoNm": "용역", **d6}, key, show_fields=True)

    print(redact("\n".join(out), key))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
