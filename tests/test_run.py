"""전체 실행 흐름 테스트 (가짜 수집기 사용, 네트워크 없음)."""

import json
from datetime import datetime, timedelta

from pg_monitoring.api_check import KST
from pg_monitoring.collect import ChunkResult, SourceResult
from pg_monitoring.run import execute
from pg_monitoring.settings import load_keywords, load_settings

SETTINGS = load_settings()
KEYWORDS = load_keywords()
T0 = datetime(2026, 11, 23, 8, 50, tzinfo=KST)
KEY = "AbC%2BSecretKey%3D%3D"


def bid(no, title, ord_="000", reg="2026-11-23 07:00:00", **kw):
    return {"bidNtceNo": no, "bidNtceOrd": ord_, "bidNtceNm": title, "ntceKindNm": "등록공고", "reNtceYn": "N",
            "ntceInsttNm": "OO공단", "dminsttNm": "OO공단", "bidNtceDt": reg, "rgstDt": reg,
            "bidClseDt": "2026-12-05 10:00:00", "asignBdgtAmt": "120000000",
            "bidNtceDtlUrl": "https://www.g2b.go.kr/link?no=" + no, "ntceInsttOfclNm": "홍길동",
            "ntceInsttOfclTelNo": "02-111-2222", "ntceInsttOfclEmailAdrs": "hong@example.go.kr", **kw}


def pre(no, title, reg="2026-11-23 06:00:00", **kw):
    return {"bfSpecRgstNo": no, "prdctClsfcNoNm": title, "bsnsDivNm": "일반용역", "orderInsttNm": "OO대학교",
            "rgstDt": reg, "opninRgstClseDt": "2026-11-28 23:59:00", "asignBdgtAmt": "50000000",
            "ofclNm": "김담당", "ofclTelNo": "010-0000-0000", **kw}


def collector_factory(data: dict, fail: set = frozenset(), partial: set = frozenset()):
    def collector(src, key, meta, now, conf):
        start = now - timedelta(days=3)
        if src.key in fail:
            ch = ChunkResult(start, now, "1", False, 0, 0, 0, 4, ["1페이지: 네트워크 오류"])
            return SourceResult(src.key, src.label, src.kind, "failed", start, now, None, [], [ch])
        items = data.get(src.key, [])
        ok = src.key not in partial
        ch = ChunkResult(start, now, "1", ok, len(items), len(items), 1, 1, [] if ok else ["2페이지: 응답코드 01"])
        return SourceResult(src.key, src.label, src.kind, "success" if ok else "partial", start, now,
                            now if ok else None, items, [ch])
    return collector


DATA1 = {
    "bid": [bid("R26BK00000001", "2027년 전자지급결제대행(PG) 서비스 운영 용역"),
            bid("R26BK00000002", "청사 청소 용역"),
            bid("R26BK00000003", "응시료 수납 시스템 유지보수")],
    "prespec": [pre("R26BD00000001", "통합수납 시스템 고도화 용역"), pre("R26BD00000002", "조경 관리 용역")],
}


def run(tmp_path, name, state, data, now=T0, **kw):
    out = tmp_path / name
    r = execute(state, out, kw.pop("key", KEY), now, settings=SETTINGS, keywords=KEYWORDS,
                collector=collector_factory(data, **kw.pop("cf", {})), **kw)
    return r, out


def load(out, name="latest.json"):
    return json.loads((out / "site" / name).read_text(encoding="utf-8"))


def test_first_run_success(tmp_path):
    r, out = run(tmp_path, "r1", None, DATA1)
    assert r["status"] == "success"
    d = load(out)
    for k in ("schema_version", "generated_at", "last_success_at", "collection_status", "coverage_start",
              "coverage_end", "bid_notices", "pre_specs", "changed_notices", "data_completeness"):
        assert k in d, k
    assert d["collection_status"] == "success" and d["last_success_at"] == "2026-11-23T08:50:00+09:00"
    ids = {x["record_id"]: x for x in d["bid_notices"]}
    assert ids["R26BK00000001-000"]["relevance_level"] == "direct"
    assert ids["R26BK00000003-000"]["relevance_level"] == "review"
    assert "R26BK00000002-000" not in ids  # 낮음은 공개 안 함
    assert d["counts"]["stored_by_relevance"]["bid"]["low"] == 1  # 하지만 이력에는 보관
    assert [x["record_id"] for x in d["pre_specs"]] == ["R26BD00000001"]
    assert ids["R26BK00000001-000"]["source_url"].startswith("https://www.g2b.go.kr/")
    assert d["pre_specs"][0]["source_url"] is None  # 없는 값은 만들지 않음


def test_outputs_have_no_personal_info_or_key(tmp_path):
    r, out = run(tmp_path, "r1", None, DATA1)
    texts = [p.read_text(encoding="utf-8") for p in (out / "site").rglob("*") if p.is_file()]
    texts += [p.read_text(encoding="utf-8") for p in (out / "state").rglob("*.json")]
    blob = "\n".join(texts)
    for bad in ("홍길동", "02-111-2222", "hong@example.go.kr", "김담당", "010-0000-0000", "SecretKey", KEY):
        assert bad not in blob, bad


def test_failed_run_keeps_previous_data(tmp_path):
    _, out1 = run(tmp_path, "r1", None, DATA1)
    r2, out2 = run(tmp_path, "r2", out1 / "state", {}, now=T0 + timedelta(hours=7),
                   cf={"fail": {"bid", "prespec"}})
    assert r2["status"] == "failed" and r2["produced"]
    d = load(out2)
    assert d["collection_status"] == "failed" and d["data_completeness"]["status"] == "stale"
    assert d["last_success_at"] == "2026-11-23T08:50:00+09:00"  # 마지막 정상 시각 유지
    meta = json.loads((out2 / "state" / "meta.json").read_text(encoding="utf-8"))
    assert meta["sources"]["bid"]["last_success_end"] == "2026-11-23T08:50:00+09:00"  # 앞당기지 않음
    bids = json.loads((out2 / "site" / "data" / "bid_notices.json").read_text(encoding="utf-8"))
    assert bids["count"] == 2  # 기존 PG 관련 공고 그대로 보존
    assert "수집 실패" in (out2 / "site" / "index.html").read_text(encoding="utf-8")


def test_missing_key_is_failed_but_preserves(tmp_path):
    _, out1 = run(tmp_path, "r1", None, DATA1)
    r2, out2 = run(tmp_path, "r2", out1 / "state", DATA1, key="")
    assert r2["status"] == "failed" and load(out2)["data_completeness"]["sources"]["bid"]["last_success_end"]


def test_partial_status(tmp_path):
    r, out = run(tmp_path, "r1", None, DATA1, cf={"partial": {"prespec"}})
    assert r["status"] == "partial"
    d = load(out)
    assert d["collection_status_label"] == "일부 수집" and d["last_success_at"] is None


def test_second_run_dedup_and_change_detected(tmp_path):
    _, out1 = run(tmp_path, "r1", None, DATA1)
    data2 = {"bid": [bid("R26BK00000001", "2027년 전자지급결제대행(PG) 서비스 운영 용역",
                         bidClseDt="2026-12-10 10:00:00"),
                     bid("R26BK00000001", "2027년 전자지급결제대행(PG) 서비스 운영 용역", ord_="001",
                         ntceKindNm="변경공고", reg="2026-11-23 14:00:00")],
             "prespec": DATA1["prespec"]}
    r2, out2 = run(tmp_path, "r2", out1 / "state", data2, now=T0 + timedelta(hours=7))
    d = load(out2)
    changed = {x["record_id"]: x for x in d["changed_notices"]}
    assert changed["R26BK00000001-000"]["event_type"] == "변경"
    assert changed["R26BK00000001-000"]["changes"][0]["field"] == "입찰마감일시"
    assert changed["R26BK00000001-001"]["event_type"] == "정정"
    assert changed["R26BK00000001-001"]["related"]["other_orders"] == ["R26BK00000001-000"]
    state_bids = {}
    for p in (out2 / "state" / "notices" / "bid").glob("*.json"):
        state_bids.update(json.loads(p.read_text(encoding="utf-8")))
    assert len(state_bids) == 4  # 3건 + 새 차수 1건 (중복 없음)


def test_backup_run_skips_after_recent_success(tmp_path):
    _, out1 = run(tmp_path, "r1", None, DATA1)
    r2, _ = run(tmp_path, "r2", out1 / "state", DATA1, now=T0 + timedelta(minutes=20), skip_within_minutes=40)
    assert r2["status"] == "skipped" and not r2["produced"]
    r3, _ = run(tmp_path, "r3", out1 / "state", DATA1, now=T0 + timedelta(minutes=60), skip_within_minutes=40)
    assert r3["status"] == "success"


def test_validation_runs_only_when_requested(tmp_path):
    called = []

    def validator(store, judge, key, now, conf):
        called.append(1)
        return {"cases": [], "calls": 0, "bid_search": {"days": 60}}
    _, out = run(tmp_path, "r1", None, DATA1, validator=validator)
    assert called == [] and json.loads((out / "site" / "data" / "validation.json").read_text())["status"] == "not_run"
    _, out = run(tmp_path, "r2", None, DATA1, validator=validator, validate=True)
    assert called == [1]


def test_text_and_html_rendered(tmp_path):
    _, out = run(tmp_path, "r1", None, DATA1)
    txt = (out / "site" / "latest.txt").read_text(encoding="utf-8")
    html = (out / "site" / "index.html").read_text(encoding="utf-8")
    assert "신규 PG 관련 입찰공고 (2건)" in txt and "R26BK00000001-000" in txt
    assert "<script" not in html.lower() and "정상 수집" in html and "NARA-20261123-0850" in html
