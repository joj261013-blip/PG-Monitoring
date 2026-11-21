"""공개용 JSON/HTML 생성 테스트 (네트워크 사용 안 함)."""

import json
from datetime import datetime

from pg_monitoring import api_check as ac
from pg_monitoring import publish

BID, PRE = ac.APIS
NOW = datetime(2026, 11, 21, 17, 20, tzinfo=ac.KST)
PARAMS = ac.query_params(NOW, 7, 5)

PERSONAL = {
    "ntceInsttOfclNm": "홍길동",
    "ntceInsttOfclTelNo": "02-1234-5678",
    "ntceInsttOfclEmailAdrs": "hong@example.go.kr",
    "ntceSpecDocUrl1": "https://www.g2b.go.kr/file/spec.hwp",
}
BID_ITEM = {
    "bidNtceNo": "R26BK00000001", "bidNtceOrd": "000", "ntceKindNm": "일반", "reNtceYn": "N",
    "bidNtceNm": "<script>alert(1)</script> 전자지급결제대행(PG) 용역",
    "ntceInsttNm": "조달청", "dminsttNm": "OO공단", "bidNtceDt": "2026-11-20 10:00:00",
    "presmptPrce": "123456789", "bidNtceDtlUrl": "https://www.g2b.go.kr/link/x?bidno=1",
    **PERSONAL,
}
PRE_ITEM = {"bfSpecRgstNo": "R26BD00000001", "prdctClsfcNoNm": "간편결제 서비스", "ofclNm": "김담당",
            "ofclTelNo": "010-0000-0000"}


def _results():
    r1 = ac.CheckResult(api_name=BID.name, endpoint=ac.API_HOST + BID.path, success=True, total_count=10)
    r1.items = [BID_ITEM]
    r2 = ac.CheckResult(api_name=PRE.name, endpoint=ac.API_HOST + PRE.path, success=True, total_count=3)
    r2.items = [PRE_ITEM]
    return [r1, r2]


def test_personal_info_never_published(tmp_path):
    files = publish.write_site(_results(), NOW, PARAMS, tmp_path)
    for f in files:
        text = f.read_text(encoding="utf-8")
        for v in ("홍길동", "02-1234-5678", "hong@example.go.kr", "spec.hwp", "김담당", "010-0000-0000"):
            assert v not in text, f"{v} 가 {f.name} 에 노출됨"


def test_json_structure(tmp_path):
    publish.write_site(_results(), NOW, PARAMS, tmp_path)
    data = json.loads((tmp_path / "data.json").read_text(encoding="utf-8"))
    assert data["verification_code"] == "NARA-20261121-1720"
    bid, pre = data["apis"]
    assert bid["kind"] == "bid" and pre["kind"] == "prespec"
    assert bid["items"][0]["bid_no"] == "R26BK00000001"
    assert set(bid["items"][0]) == {k for _f, k, _l in publish.PUBLIC_FIELDS["bid"]}
    assert pre["items"][0]["title"] == "간편결제 서비스"


def test_html_is_escaped_and_scriptless(tmp_path):
    publish.write_site(_results(), NOW, PARAMS, tmp_path)
    page = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert "<script" not in page.lower()
    assert "&lt;script&gt;" in page
    assert "123,456,789" in page and "NARA-20261121-1720" in page
    assert 'href="https://www.g2b.go.kr/link/x?bidno=1"' in page


def test_links_limited_to_g2b():
    for bad in ("javascript:alert(1)", "https://evil.example.com/g2b.go.kr", "https://g2b.go.kr.evil.com/"):
        assert publish._safe_link(bad) == ""
    assert publish._safe_link("https://www.g2b.go.kr/a") == "https://www.g2b.go.kr/a"


def test_missing_public_fields():
    assert "bidClseDt" in publish.missing_public_fields("bid", [BID_ITEM])
    assert "bidNtceNo" not in publish.missing_public_fields("bid", [BID_ITEM])


def _run_main(monkeypatch, tmp_path, success):
    key = "AbC%2BdEf%2FGhI123%3D%3D"
    monkeypatch.setenv("NARA_API_KEY", key)
    monkeypatch.setenv("SITE_DIR", str(tmp_path / "site"))
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)

    def fake_check(spec, k, params):
        r = ac.CheckResult(api_name=spec.name, endpoint=ac.API_HOST + spec.path, success=success,
                           http_status=200, result_code="00", total_count=1)
        item = dict(BID_ITEM if spec is BID else PRE_ITEM)
        item["bidNtceNm"] = f"키 노출 시도 {k} AbC+dEf/GhI123=="
        r.items = [item]
        r.examples = [("x", "y")]
        return r

    monkeypatch.setattr(ac, "check_api", fake_check)
    return ac.main([]), tmp_path / "site"


def test_main_writes_site_on_success_without_key(monkeypatch, tmp_path, capsys):
    code, site = _run_main(monkeypatch, tmp_path, success=True)
    assert code == 0
    out = capsys.readouterr().out
    assert "공개용 샘플 파일 생성" in out and "응답 필드 목록" in out
    for name in ("index.html", "data.json"):
        text = (site / name).read_text(encoding="utf-8")
        assert "AbC" not in text and "GhI123" not in text


def test_main_skips_site_on_failure(monkeypatch, tmp_path, capsys):
    code, site = _run_main(monkeypatch, tmp_path, success=False)
    assert code == 1 and not site.exists()
    assert "건너뜀" in capsys.readouterr().out
