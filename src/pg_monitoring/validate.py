"""검증 사례 조회 (삼다수 PG, 인터넷등기소, 학점은행제·독학학위제, 세종문화회관 등).

- 입찰공고: 나라장터 검색 기능(공고명 검색)으로 최근 lookback_days 일을 search_chunk_days 단위로 조회
- 사전규격: 지금까지 수집된 이력에서 품명(사업명)으로 검색
- 실제 조회된 공고만 결과에 넣는다. 없으면 '조회되지 않음', 조회 오류면 '조회 실패'로 표시
- 찾은 공고에 현재 키워드 판정을 적용해, 자동 선별이 이 공고를 잡았는지(직접/검토/낮음) 보여준다
"""

from __future__ import annotations

from datetime import datetime, timedelta

from pg_monitoring.collect import split_chunks
from pg_monitoring.history import ID_FIELDS, iso, parse_dt
from pg_monitoring.nara_client import fetch_all


def _fmt(dt: datetime) -> str:
    return dt.strftime("%Y%m%d%H%M")


def run_validation(store, judge, service_key: str, now: datetime, conf: dict, fetcher=fetch_all) -> dict:
    days = int(conf.get("lookback_days", 60))
    chunk = float(conf.get("search_chunk_days", 6))
    path = conf.get("bid_search_path")
    start = now - timedelta(days=days)
    pre_dates = [parse_dt(r["api"].get("rgstDt")) for r in store.records["prespec"].values()]
    pre_dates = [d for d in pre_dates if d]
    result = {"run_at": iso(now), "bid_search": {"period_start": iso(start), "period_end": iso(now), "days": days},
              "prespec_search": {"period_start": iso(min(pre_dates)) if pre_dates else None,
                                 "period_end": iso(max(pre_dates)) if pre_dates else None,
                                 "basis": "수집된 사전규격 이력 내 검색"},
              "calls": 0, "cases": []}
    for case in conf.get("cases", []):
        found, errors = {}, []
        for term in case.get("terms", []):
            for cs, ce in split_chunks(start, now, chunk):
                fr = fetcher(path, {"inqryDiv": "1", "bidNtceNm": term, "inqryBgnDt": _fmt(cs), "inqryEndDt": _fmt(ce)},
                             service_key, id_fields=ID_FIELDS["bid"], page_size=100, max_pages=20, retries=2,
                             wait_seconds=3)
                result["calls"] += fr.calls
                errors += [f"{term} {cs:%m-%d}~{ce:%m-%d}: {e}" for e in fr.errors]
                for it in fr.items:
                    rid = f"{it.get('bidNtceNo', '')}-{it.get('bidNtceOrd', '')}"
                    rel = judge.judge("bid", it)
                    found[rid] = {"notice_type": "입찰공고", "record_id": rid, "title": it.get("bidNtceNm") or None,
                                  "org": it.get("ntceInsttNm") or None, "notice_dt": it.get("bidNtceDt") or None,
                                  "notice_kind": it.get("ntceKindNm") or None, "matched_term": term,
                                  "relevance_level": rel.level, "relevance_reason": rel.reason}
            for rid, rec in store.records["prespec"].items():
                title = rec["api"].get("prdctClsfcNoNm", "")
                if term.replace(" ", "") in title.replace(" ", ""):
                    rel = rec.get("relevance", {})
                    found[rid] = {"notice_type": "사전규격", "record_id": rid, "title": title or None,
                                  "org": rec["api"].get("orderInsttNm") or None,
                                  "notice_dt": rec["api"].get("rgstDt") or None, "notice_kind": None,
                                  "matched_term": term, "relevance_level": rel.get("level"),
                                  "relevance_reason": rel.get("reason")}
        items = sorted(found.values(), key=lambda x: x.get("notice_dt") or "", reverse=True)
        caught = sum(1 for i in items if i["relevance_level"] in ("direct", "review"))
        status = "확인됨" if items else ("조회 실패" if errors else "조회되지 않음")
        result["cases"].append({"name": case.get("name"), "terms": case.get("terms", []), "status": status,
                                "found_count": len(items), "caught_by_filter": caught,
                                "items": items[:30], "errors": errors[:5]})
    return result
