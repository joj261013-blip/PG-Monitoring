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
                                   "진료비 신용카드 부가가치 통신망(VAN) 서비스 계약",
                                   "민락수변공원 공영주차장 바닥제어 무인정산 주차시스템 설치"])
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


# 2026-11-21 실제 나라장터 검색으로 확인된 검증 사례 공고명 (공개 정보)
@pytest.mark.parametrize("title", [
    "제주삼다수 자사몰 전자지급결제대행(PG) 사업자 선정",
    "인터넷등기소 등 전자지급결제대행 사업(2027~2028)",
    "’27~’29 학점은행제‧독학학위제 수수료 전자결제대행(PG) 용역(장기 3년)",
    "세종문화회관 전자지급결제대행사(PG) 운영",
])
def test_validation_cases_are_direct(title):
    assert J.judge("bid", bid(title)).level == "direct"


# 같은 수집에서 확인된 오탐 → 낮음이어야 함
@pytest.mark.parametrize("title,org", [
    ("2026년 귀속 연말정산 용역", "OO공단"),
    ("국가과학기술연구회 추진과제 사업비 위탁정산", "OO"),
    ("2026년 상생협력재단 예산정산(기금사업, 임치사업) 용역", "OO"),
    ("AI 기반 보안관제센터 운영 사업(27~29년) 용역", "금융결제원"),
    ("전자의결권 플랫폼 구현업무 아웃소싱 사업", "한국예탁결제원"),
    ("’27~’28 학점은행제-독학학위제 콜센터 운영 위탁사업(장기 2년)", "OO"),
    ("제주삼다수 직장어린이집 위탁운영 용역", "OO"),
])
def test_known_false_positives_are_low(title, org):
    assert J.judge("bid", bid(title, dminsttNm=org)).level == "low"


def test_direct_keyword_still_checked_in_org_fields():
    r = J.judge("bid", bid("시스템 운영", dminsttNm="OO 전자결제센터"))
    assert r.level == "direct"  # 직접 관련 단어는 기관명에서도 판정
