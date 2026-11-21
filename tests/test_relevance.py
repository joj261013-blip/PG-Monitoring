"""PG 관련성 판정 테스트 (실제 config/keywords.toml 사용)."""

import pytest

from pg_monitoring.relevance import RelevanceJudge
from pg_monitoring.settings import load_keywords

J = RelevanceJudge(load_keywords())


def bid(title, **kw):
    return {"bidNtceNm": title, **kw}


@pytest.mark.parametrize("title", [
    "2027년 전자지급결제대행(PG) 서비스 운영 용역",
    "PG사 선정 용역",
    "온라인결제 시스템 구축",
    "등록금 수납 및 결제대행 용역",
])
def test_direct(title):
    r = J.judge("bid", bid(title))
    assert r.level == "direct", r.reason


def test_two_strong_categories_is_direct():
    r = J.judge("bid", bid("통합수납 및 가상계좌 운영 용역"))
    assert r.level == "direct" and "통합수납" in r.reason and "가상계좌" in r.reason


@pytest.mark.parametrize("title", ["응시료 납부 시스템 유지보수", "공공요금 고지 개선", "키오스크 유지관리 용역",
                                   "인터넷등기소 시스템 운영", "학점은행제 학습자 관리 시스템"])
def test_review(title):
    assert J.judge("bid", bid(title)).level == "review"


@pytest.mark.parametrize("title", ["시스템 UPGRADE 용역", "청사 청소 용역", "PGM 교육 운영"])
def test_low_and_no_false_positive(title):
    assert J.judge("bid", bid(title)).level == "low"


def test_spaces_ignored_and_other_fields_used():
    r = J.judge("bid", bid("정보시스템 운영", purchsObjPrdctList="수수료납부 모듈"))
    assert r.level == "review" and "구매대상물품" in r.reason


def test_prespec_fields():
    r = J.judge("prespec", {"prdctClsfcNoNm": "통합 수납시스템 고도화", "rlDminsttNm": "OO대학교"})
    assert r.level == "review" and "품명" in r.reason


def test_exclude_words():
    j = RelevanceJudge({"categories": {"broad": {"label": "b", "words": ["등기"]}},
                        "exclude": {"words": ["등기우편"]}, "fields": {"bid": {"bidNtceNm": "공고명"}}})
    assert j.judge("bid", bid("등기우편 발송 용역")).level == "low"
    assert j.judge("bid", bid("등기 시스템")).level == "review"
