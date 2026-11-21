"""API 연결 테스트 프로그램의 단위 테스트 (네트워크 사용 안 함, 가짜 인증키만 사용)."""

import json
import urllib.parse
from datetime import datetime

import pytest

from pg_monitoring import api_check as ac

# 공공데이터포털 키처럼 +, /, = 가 들어간 가짜 키
DECODED = "AbC+dEf/GhI123=="
ENCODED = urllib.parse.quote(DECODED, safe="")  # AbC%2BdEf%2FGhI123%3D%3D

BID, PRE = ac.APIS


def _query_key(url: str) -> str:
    return urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)["serviceKey"][0]


@pytest.mark.parametrize("key", [DECODED, ENCODED, f"  {ENCODED}\n"])
def test_service_key_is_encoded_exactly_once(key):
    url = ac.build_url(BID.path, {"pageNo": "1"}, key)
    assert "%25" not in url, "이중 인코딩(% → %25)이 발생하면 안 됩니다"
    assert f"serviceKey={ENCODED}&" in url
    assert _query_key(url) == DECODED  # 서버가 디코딩하면 원래 키가 된다


def test_query_params_format():
    p = ac.query_params(datetime(2026, 11, 21, 9, 5, tzinfo=ac.KST), days=7, rows=5)
    assert p["inqryBgnDt"] == "202611140905"
    assert p["inqryEndDt"] == "202611210905"
    assert p["type"] == "json" and p["inqryDiv"] == "1" and p["numOfRows"] == "5"


@pytest.mark.parametrize("key", [DECODED, ENCODED])
def test_redact_removes_all_key_forms(key):
    text = (
        f"a {DECODED} b {ENCODED} c {ENCODED.lower()} d "
        f"https://x/y?serviceKey=SOMETHINGELSE&pageNo=1"
    )
    out = ac.redact(text, key)
    for leaked in (DECODED, ENCODED, ENCODED.lower(), "SOMETHINGELSE"):
        assert leaked not in out
    assert "pageNo=1" in out


def _ok_body(items, total=None):
    return json.dumps(
        {"response": {"header": {"resultCode": "00", "resultMsg": "정상"},
                      "body": {"items": items, "totalCount": total if total is not None else len(items)}}},
        ensure_ascii=False,
    )


def _result(spec):
    return ac.CheckResult(api_name=spec.name, endpoint="e")


def test_interpret_bid_success():
    items = [{"bidNtceNo": "R26BK00000001", "bidNtceOrd": "000", "bidNtceNm": "전자지급결제대행 용역"}]
    r = ac.interpret(BID, 200, _ok_body(items, total=123), _result(BID))
    assert r.success and r.result_code == "00" and r.total_count == 123
    assert r.examples == [("R26BK00000001-000", "전자지급결제대행 용역")]


@pytest.mark.parametrize("items", [
    {"item": [{"bfSpecRgstNo": "R26BD00000001", "prdctClsfcNoNm": "PG 서비스"}]},
    {"item": {"bfSpecRgstNo": "R26BD00000001", "prdctClsfcNoNm": "PG 서비스"}},
])
def test_interpret_prespec_item_shapes(items):
    r = ac.interpret(PRE, 200, _ok_body(items, total=1), _result(PRE))
    assert r.success and r.examples == [("R26BD00000001", "PG 서비스")]


def test_interpret_empty_items_is_not_success():
    r = ac.interpret(BID, 200, _ok_body("", total=0), _result(BID))
    assert not r.success and r.result_code == "00" and "데이터가 없음" in r.error_cause


def test_interpret_result_code_error():
    body = json.dumps({"response": {"header": {"resultCode": "07", "resultMsg": "입력범위값 초과 에러"}}})
    r = ac.interpret(BID, 200, body, _result(BID))
    assert not r.success and r.result_code == "07" and "기간" in r.error_cause


def test_interpret_gateway_error_json():
    body = json.dumps({"OpenAPI_ServiceResponse": {"cmmMsgHeader": {
        "errMsg": "SERVICE_KEY_IS_NOT_REGISTERED_ERROR", "returnReasonCode": "30"}}})
    r = ac.interpret(BID, 403, body, _result(BID))
    assert not r.success and r.http_status == 403 and r.result_code == "30"
    assert "등록되지 않은 인증키" in r.error_cause and "Decoding" in r.error_fix


def test_interpret_gateway_error_xml():
    body = ("<OpenAPI_ServiceResponse><cmmMsgHeader><errMsg>NO_OPENAPI_SERVICE_ERROR</errMsg>"
            "<returnReasonCode>12</returnReasonCode></cmmMsgHeader></OpenAPI_ServiceResponse>")
    r = ac.interpret(PRE, 400, body, _result(PRE))
    assert r.result_code == "12" and "폐기" in r.error_cause


def test_interpret_non_json():
    r = ac.interpret(BID, 200, "<html>점검중</html>", _result(BID))
    assert not r.success and "해석할 수 없음" in r.error_cause


def test_main_without_key(monkeypatch, capsys):
    for name in ac.KEY_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    assert ac.main([]) == 2
    assert "NARA_API_KEY" in capsys.readouterr().out


def test_main_never_prints_key(monkeypatch, capsys, tmp_path):
    """서버가 오류 메시지에 인증키를 되돌려줘도 출력·요약 파일에 키가 남지 않아야 한다."""
    monkeypatch.setenv("NARA_API_KEY", ENCODED)
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))

    def fake_check(spec, key, params):
        r = ac.CheckResult(api_name=spec.name, endpoint=f"{ac.API_HOST}{spec.path}", http_status=500)
        r.error_cause = f"echo {DECODED} {ENCODED} serviceKey={ENCODED}"
        r.error_fix = "x"
        return r

    monkeypatch.setattr(ac, "check_api", fake_check)
    assert ac.main([]) == 1
    out = capsys.readouterr().out + summary.read_text(encoding="utf-8")
    assert DECODED not in out and ENCODED not in out
    assert "❌ 실패" in out


def test_shared_key_hint_when_other_api_succeeds():
    ok = ac.CheckResult(api_name=PRE.name, endpoint="e", success=True, http_status=200, result_code="00")
    bad = ac.CheckResult(api_name=BID.name, endpoint="e", http_status=403, result_code="30",
                         error_cause="등록되지 않은 인증키", error_fix="키 재등록")
    ac.adjust_for_shared_key([bad, ok])
    assert "인증키는 정상" in bad.error_cause
    assert "입찰공고정보서비스" in bad.error_fix and "활용신청" in bad.error_fix


def test_no_shared_key_hint_when_all_fail():
    bad = ac.CheckResult(api_name=BID.name, endpoint="e", http_status=403, result_code="30",
                         error_cause="등록되지 않은 인증키", error_fix="키 재등록")
    ac.adjust_for_shared_key([bad, bad])
    assert bad.error_cause == "등록되지 않은 인증키"


def test_diagnose_all_rejected(monkeypatch):
    calls = []

    def fake(spec, key, params):
        calls.append(spec.path)
        return ac.CheckResult(api_name=spec.name, endpoint="e", http_status=403, result_code="30")

    monkeypatch.setattr(ac, "check_api", fake)
    failed = ac.CheckResult(api_name=BID.name, endpoint=ac.API_HOST + BID.path, result_code="30")
    out = ac.diagnose("k", {"numOfRows": "5"}, [failed])
    assert len(calls) == 5 and all("/ad/BidPublicInfoService/" in c for c in calls)
    assert "모든 오퍼레이션" in out and "1:1 문의" in out


def test_needs_diagnosis_only_when_other_api_succeeds():
    ok = ac.CheckResult(api_name="a", endpoint="e", success=True)
    bad = ac.CheckResult(api_name="b", endpoint="e", result_code="30")
    assert ac.needs_diagnosis([ok, bad]) == [bad]
    assert ac.needs_diagnosis([bad, bad]) == []
