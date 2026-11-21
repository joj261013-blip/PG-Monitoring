"""이력 저장소 테스트: 중복 제거, 정정·재공고, 변경 이력, 연결, 저장/불러오기, 보관 정리."""

from datetime import datetime, timedelta

from pg_monitoring.api_check import KST
from pg_monitoring.history import Store, classify_new, similarity
from pg_monitoring.relevance import RelevanceJudge
from pg_monitoring.settings import load_keywords

T0 = datetime(2026, 11, 20, 8, 50, tzinfo=KST)
T1 = T0 + timedelta(hours=7)
JUDGE = RelevanceJudge(load_keywords())


def bid(no="R26BK00000001", ord_="000", **kw):
    base = {"bidNtceNo": no, "bidNtceOrd": ord_, "bidNtceNm": "통합수납 및 가상계좌 운영 용역", "ntceKindNm": "등록공고",
            "reNtceYn": "N", "ntceInsttNm": "OO공단", "dminsttNm": "OO공단", "bidNtceDt": "2026-11-20 08:00:00",
            "rgstDt": "2026-11-20 08:00:00", "bidClseDt": "2026-11-30 10:00:00", "asignBdgtAmt": "100000000",
            "ntceInsttOfclNm": "홍길동", "ntceInsttOfclTelNo": "02-000-0000"}
    base.update(kw)
    return base


def test_dedup_same_notice_twice():
    s = Store()
    e1 = s.merge("bid", [bid()], T0, "bid")
    e2 = s.merge("bid", [bid()], T1, "bid")
    assert [e["type"] for e in e1] == ["신규"] and e2 == []
    rec = s.records["bid"]["R26BK00000001-000"]
    assert rec["first_seen_at"].startswith("2026-11-20T08:50") and rec["last_seen_at"].startswith("2026-11-20T15:50")


def test_personal_fields_not_stored():
    s = Store()
    s.merge("bid", [bid()], T0, "bid")
    api = s.records["bid"]["R26BK00000001-000"]["api"]
    assert "ntceInsttOfclNm" not in api and "ntceInsttOfclTelNo" not in api


def test_correction_and_reannouncement_types():
    assert classify_new("bid", {"bidNtceOrd": "001", "ntceKindNm": "변경공고"}) == "정정"
    assert classify_new("bid", {"bidNtceOrd": "000", "ntceKindNm": "재공고", "reNtceYn": "Y"}) == "재공고"
    assert classify_new("bid", {"bidNtceOrd": "000", "ntceKindNm": "취소공고"}) == "취소"
    assert classify_new("bid", {"bidNtceOrd": "000", "ntceKindNm": "등록공고"}) == "신규"


def test_field_change_recorded_with_old_and_new():
    s = Store()
    s.merge("bid", [bid()], T0, "bid")
    ev = s.merge("bid", [bid(bidClseDt="2026-12-05 10:00:00")], T1, "bid")
    assert ev[0]["type"] == "변경" and ev[0]["fields"] == ["bidClseDt"]
    rec = s.records["bid"]["R26BK00000001-000"]
    assert rec["history"][0]["changes"]["bidClseDt"] == ["2026-11-30 10:00:00", "2026-12-05 10:00:00"]
    assert rec["api"]["bidClseDt"] == "2026-12-05 10:00:00"


def test_new_order_links_to_other_orders():
    s = Store()
    s.merge("bid", [bid()], T0, "bid")
    s.merge("bid", [bid(ord_="001", ntceKindNm="변경공고")], T1, "bid")
    s.build_relations()
    assert s.records["bid"]["R26BK00000001-001"]["related"]["other_orders"] == ["R26BK00000001-000"]


def test_prespec_bid_explicit_link_both_ways():
    s = Store()
    s.merge("prespec", [{"bfSpecRgstNo": "R26BD00000009", "prdctClsfcNoNm": "통합수납 시스템", "bsnsDivNm": "일반용역",
                         "orderInsttNm": "OO공단", "rgstDt": "2026-11-01 09:00:00"}], T0, "prespec")
    s.merge("bid", [bid(bfSpecRgstNo="R26BD00000009")], T1, "bid")
    s.apply_relevance(JUDGE)
    s.build_relations()
    assert s.records["bid"]["R26BK00000001-000"]["related"]["prespec_no"] == "R26BD00000009"
    assert s.records["prespec"]["R26BD00000009"]["related"]["bid_notice_nos"] == ["R26BK00000001"]


def test_link_candidates_not_confirmed():
    s = Store()
    s.merge("prespec", [{"bfSpecRgstNo": "R26BD1", "prdctClsfcNoNm": "2027년 통합수납 시스템 운영 용역",
                         "bsnsDivNm": "일반용역", "orderInsttNm": "OO공단", "rgstDt": "2026-11-01 09:00:00"}], T0, "prespec")
    s.merge("bid", [bid(bidNtceNm="2027년 통합수납시스템 운영용역")], T1, "bid")
    s.apply_relevance(JUDGE)
    s.build_relations()
    rel = s.records["prespec"]["R26BD1"]["related"]
    assert rel["bid_notice_nos"] == []  # 자동 확정하지 않음
    assert rel["candidates"][0]["bid_notice_no"] == "R26BK00000001"


def test_prespec_service_check():
    s = Store()
    s.merge("prespec", [{"bfSpecRgstNo": "A", "prdctClsfcNoNm": "x", "bsnsDivNm": "일반용역"},
                        {"bfSpecRgstNo": "B", "prdctClsfcNoNm": "y"}], T0, "prespec")
    s.apply_relevance(JUDGE)
    assert s.records["prespec"]["A"]["service_check"] is None
    assert "확인 필요" in s.records["prespec"]["B"]["service_check"]


def test_save_and_load_roundtrip(tmp_path):
    s = Store()
    s.merge("bid", [bid(), bid(no="R26BK00000002", rgstDt="2026-10-15 09:00:00")], T0, "bid")
    s.meta["sources"]["bid"] = {"last_success_end": "2026-11-20T08:50:00+09:00"}
    s.save(tmp_path)
    assert sorted(p.name for p in (tmp_path / "notices" / "bid").iterdir()) == ["2026-10.json", "2026-11.json"]
    s2 = Store.load(tmp_path)
    assert set(s2.records["bid"]) == set(s.records["bid"]) and s2.meta["sources"]["bid"]["last_success_end"]


def test_compact_low_keeps_record():
    s = Store()
    s.merge("bid", [bid(bidNtceNm="청사 청소 용역", rgstDt="2026-06-01 09:00:00", bidNtceDt="2026-06-01 09:00:00")],
            T0, "bid")
    s.apply_relevance(JUDGE)
    assert s.compact(T0, after_days=90) == 1
    rec = s.records["bid"]["R26BK00000001-000"]
    assert rec["compacted"] and rec["api"]["bidNtceNm"] == "청사 청소 용역" and "asignBdgtAmt" not in rec["api"]


def test_similarity():
    assert similarity("2027년 통합수납 시스템 운영 용역", "2028년 통합수납시스템 운영용역") > 0.8
    assert similarity("청소 용역", "통합수납 시스템") < 0.2
