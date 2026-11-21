"""수집 엔진 테스트: 전체 페이지, 건수 검증, 재시도, 구간 계산, 부분 실패 (네트워크 사용 안 함)."""

from datetime import datetime

from pg_monitoring import nara_client as nc
from pg_monitoring.api_check import KST
from pg_monitoring.collect import collect_source, plan_window, split_chunks
from pg_monitoring.settings import Source

NOW = datetime(2026, 11, 21, 15, 30, tzinfo=KST)
BID = Source("bid", "입찰공고(용역)", "/x/bid", "bid", ("1",), True)
PRE = Source("prespec", "사전규격(용역)", "/x/pre", "prespec", ("1", "3"), True)
CONF = {"overlap_hours": 6, "max_backfill_days": 30, "initial_lookback_days": 14, "chunk_days": 3,
        "page_size": 100, "max_pages": 300, "retries": 0, "retry_wait_seconds": 0}


def fake_pages(total, fail_pages=(), dup_shift=0):
    """total 건을 page_size 단위로 돌려주는 가짜 requester."""
    calls = []

    def requester(path, params, key, retries=0, wait_seconds=0):
        page, size = int(params["pageNo"]), int(params["numOfRows"])
        calls.append(page)
        if page in fail_pages:
            return nc.PageResult(False, 200, "01", "서버오류", error="응답코드 01")
        start = (page - 1) * size - (dup_shift if page > 1 else 0)
        items = [{"bidNtceNo": f"N{i:05d}", "bidNtceOrd": "000"} for i in range(max(start, 0), min(start + size, total))]
        return nc.PageResult(True, 200, "00", "정상", total_count=total, items=items)
    return requester, calls


def test_fetch_all_reads_every_page():
    req, calls = fake_pages(250)
    fr = nc.fetch_all("/p", {}, "k", id_fields=("bidNtceNo", "bidNtceOrd"), page_size=100, requester=req)
    assert fr.ok and fr.total_count == 250 and len(fr.items) == 250
    assert calls == [1, 2, 3] and fr.pages_expected == 3


def test_fetch_all_page_failure_is_partial():
    req, _ = fake_pages(250, fail_pages=(2,))
    fr = nc.fetch_all("/p", {}, "k", id_fields=("bidNtceNo", "bidNtceOrd"), page_size=100, requester=req)
    assert not fr.ok and len(fr.items) == 150 and "2페이지" in fr.errors[0]


def test_fetch_all_detects_missing_after_dedup():
    req, _ = fake_pages(250, dup_shift=-5)  # 조회 중 앞쪽 데이터가 빠져 페이지 경계가 밀림 → 5건 누락
    fr = nc.fetch_all("/p", {}, "k", id_fields=("bidNtceNo", "bidNtceOrd"), page_size=100, requester=req)
    assert not fr.ok and len(fr.items) == 245 and "245건 < 전체 250건" in fr.errors[0]


def test_fetch_all_no_data_code_is_ok():
    def req(*a, **k):
        return nc.PageResult(True, 200, "03", "데이터없음", total_count=0)
    fr = nc.fetch_all("/p", {}, "k", id_fields=("bidNtceNo",), requester=req)
    assert fr.ok and fr.total_count == 0 and fr.items == []


def test_request_page_retries_transient_then_succeeds():
    bodies = iter([(503, ""), (200, '{"response":{"header":{"resultCode":"00"},"body":{"totalCount":0}}}')])
    waits = []
    r = nc.request_page("/p", {}, "k", retries=2, wait_seconds=1, http_get=lambda url: next(bodies),
                        sleep=waits.append)
    assert r.ok and waits == [1]


def test_request_page_does_not_retry_auth_error():
    body = '{"OpenAPI_ServiceResponse":{"cmmMsgHeader":{"errMsg":"SERVICE_KEY_IS_NOT_REGISTERED_ERROR","returnReasonCode":"30"}}}'
    calls = []
    r = nc.request_page("/p", {}, "k", retries=3, wait_seconds=0, http_get=lambda u: calls.append(u) or (403, body),
                        sleep=lambda s: None)
    assert not r.ok and r.result_code == "30" and len(calls) == 1


def test_request_page_error_never_contains_key():
    def boom(url):
        raise RuntimeError(f"failed {url}")
    r = nc.request_page("/p", {}, "SECRETKEY123", retries=0, http_get=boom)
    assert "SECRETKEY123" not in (r.error or "")


def test_plan_window_first_run_and_overlap_and_backfill_cap():
    s, e = plan_window({}, NOW, CONF)
    assert (e - s).days == 14
    s, e = plan_window({"last_success_end": "2026-11-21T08:50:00+09:00"}, NOW, CONF)
    assert s.hour == 2 and s.minute == 50  # 6시간 겹침
    s, e = plan_window({"last_success_end": "2026-09-01T08:50:00+09:00"}, NOW, CONF)
    assert (e - s).days == 30  # 최대 소급 30일


def test_split_chunks():
    s, e = plan_window({}, NOW, CONF)
    ch = split_chunks(s, e, 3)
    assert len(ch) == 5 and ch[0][0] == s and ch[-1][1] == e
    assert all((b - a).days <= 3 for a, b in ch)


def _fetcher(fail_chunk_index=None, per_chunk=3):
    seen = []

    def fetcher(path, params, key, **kw):
        idx = len(seen)
        seen.append(params)
        if fail_chunk_index is not None and idx == fail_chunk_index:
            return nc.FetchResult(ok=False, errors=["1페이지: 응답코드 01"])
        items = [{"bidNtceNo": f"{params['inqryBgnDt']}-{i}", "bidNtceOrd": "000",
                  "bfSpecRgstNo": f"{params['inqryDiv']}{params['inqryBgnDt']}-{i}"} for i in range(per_chunk)]
        return nc.FetchResult(ok=True, total_count=per_chunk, items=items, pages_expected=1, pages_fetched=1, calls=1)
    return fetcher, seen


def test_collect_source_success():
    f, seen = _fetcher()
    r = collect_source(BID, "k", {}, NOW, CONF, fetcher=f)
    assert r.status == "success" and len(seen) == 5 and len(r.items) == 15 and r.covered_until == r.window_end


def test_collect_source_partial_keeps_coverage_before_failure():
    f, _ = _fetcher(fail_chunk_index=2)
    r = collect_source(BID, "k", {}, NOW, CONF, fetcher=f)
    assert r.status == "partial" and len(r.items) == 12
    assert r.covered_until == r.chunks[1].end  # 3번째 구간 실패 → 2번째 구간 끝까지만 인정


def test_collect_source_all_fail():
    def f(*a, **k):
        return nc.FetchResult(ok=False, errors=["1페이지: 네트워크 오류"])
    r = collect_source(BID, "k", {}, NOW, CONF, fetcher=f)
    assert r.status == "failed" and r.covered_until is None


def test_collect_prespec_uses_both_query_modes():
    f, seen = _fetcher()
    r = collect_source(PRE, "k", {"last_success_end": "2026-11-21T08:50:00+09:00"}, NOW, CONF, fetcher=f)
    assert [p["inqryDiv"] for p in seen] == ["1", "3"] and r.status == "success"


def test_parse_dt_formats():
    from pg_monitoring.history import parse_dt
    assert parse_dt("2026-11-21T08:50:00+09:00") == datetime(2026, 11, 21, 8, 50)
    assert parse_dt("2026-11-20 23:29:44") == datetime(2026, 11, 20, 23, 29, 44)
    assert parse_dt("202611202142") == datetime(2026, 11, 20, 21, 42)
    assert parse_dt("2026-11-20 23:29:44.0") == datetime(2026, 11, 20, 23, 29, 44)
    assert parse_dt("") is None and parse_dt("없음") is None


def test_auth_error_stops_source_early():
    calls = []

    def f(path, params, key, **kw):
        calls.append(params)
        return nc.FetchResult(ok=False, errors=["1페이지: 공공데이터포털 오류 코드 30 (SERVICE_KEY_IS_NOT_REGISTERED_ERROR)"])
    r = collect_source(PRE, "k", {}, NOW, CONF, fetcher=f)
    assert len(calls) == 1 and r.status == "failed" and r.covered_until is None
