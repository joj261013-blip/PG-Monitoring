"""공고 이력 저장소.

- 입찰공고: 공고번호-차수(bidNtceNo-bidNtceOrd), 사전규격: 사전규격등록번호(bfSpecRgstNo) 로 식별
- 처음 수집 시각(first_seen_at), 마지막 확인 시각(last_seen_at), 사건 기록(events: 신규/정정/재공고/취소/변경)
- 주요 항목이 바뀌면 이전 값과 새 값을 history 에 기록
- 관계: 같은 공고번호의 다른 차수, 사전규격↔입찰공고(명시 필드), 연결 후보(자동 확정하지 않음)
- 저장: state/notices/<kind>/<등록월>.json (월별로 나눠 파일이 지나치게 커지지 않게 함)
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from pathlib import Path

SCHEMA_VERSION = 1
MAX_EVENTS = 30

ID_FIELDS = {"bid": ("bidNtceNo", "bidNtceOrd"), "prespec": ("bfSpecRgstNo",)}

# 저장하는 API 항목 (담당자 이름·연락처·이메일, 첨부파일 주소는 저장하지 않음)
STORED_FIELDS = {
    "bid": (
        "bidNtceNo", "bidNtceOrd", "ntceKindNm", "reNtceYn", "rgstTyNm", "bidNtceNm", "ntceInsttNm", "ntceInsttCd",
        "dminsttNm", "dminsttCd", "bidNtceDt", "rgstDt", "chgDt", "chgNtceRsn", "bidBeginDt", "bidClseDt", "opengDt",
        "presmptPrce", "asignBdgtAmt", "bidNtceDtlUrl", "bfSpecRgstNo", "befBidBbancNo", "untyNtceNo", "refNo",
        "srvceDivNm", "bidMethdNm", "cntrctCnclsMthdNm", "sucsfbidMthdNm", "purchsObjPrdctList",
        "pubPrcrmntClsfcNm", "pubPrcrmntMidClsfcNm", "pubPrcrmntLrgClsfcNm", "infoBizYn",
    ),
    "prespec": (
        "bfSpecRgstNo", "refNo", "bsnsDivNm", "prdctClsfcNoNm", "prdctDtlList", "orderInsttNm", "rlDminsttNm",
        "asignBdgtAmt", "rcptDt", "rgstDt", "chgDt", "opninRgstClseDt", "dlvrTmlmtDt", "dlvrDaynum",
        "bidNtceNoList", "swBizObjYn",
    ),
}
# 바뀌면 '변경'으로 기록할 항목
TRACKED_FIELDS = {
    "bid": ("bidNtceNm", "ntceKindNm", "bidClseDt", "opengDt", "presmptPrce", "asignBdgtAmt", "dminsttNm",
            "chgDt", "chgNtceRsn"),
    "prespec": ("prdctClsfcNoNm", "opninRgstClseDt", "asignBdgtAmt", "rlDminsttNm", "orderInsttNm", "chgDt",
                "bidNtceNoList", "prdctDtlList"),
}
# 관련성 '낮음' 공고를 줄여서 보관할 때 남기는 항목 (재판정에 필요한 항목 포함)
COMPACT_EXTRA = ("ntceKindNm", "reNtceYn", "rgstDt", "chgDt", "bidNtceDt", "bidClseDt", "bfSpecRgstNo",
                 "bidNtceNoList", "rcptDt")

FIELD_LABELS = {
    "bidNtceNm": "공고명", "ntceKindNm": "공고종류", "bidClseDt": "입찰마감일시", "opengDt": "개찰일시",
    "presmptPrce": "추정가격", "asignBdgtAmt": "배정예산", "dminsttNm": "수요기관", "chgDt": "변경일시",
    "chgNtceRsn": "변경사유", "prdctClsfcNoNm": "품명(사업명)", "opninRgstClseDt": "의견등록마감일시",
    "rlDminsttNm": "실수요기관", "orderInsttNm": "발주기관", "bidNtceNoList": "관련 입찰공고번호",
    "prdctDtlList": "세부품명",
}


def record_id(kind: str, item: dict) -> str:
    return "-".join(str(item.get(f, "")).strip() for f in ID_FIELDS[kind])


def parse_dt(value) -> datetime | None:
    """API 날짜(2026-11-20 23:29:44, 202611202142 등)와 ISO 시각을 시간대 없는 한국시간으로 읽는다."""
    text = str(value or "").strip()
    if not text:
        return None
    if text.isdigit():  # API 조회용 형식: 날짜·시각 숫자만 (ISO 해석기는 이를 잘못 읽으므로 먼저 처리)
        for fmt in ("%Y%m%d%H%M%S", "%Y%m%d%H%M", "%Y%m%d"):
            if len(text) == len(datetime(2000, 1, 1).strftime(fmt)):
                try:
                    return datetime.strptime(text, fmt)
                except ValueError:
                    return None
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(text.split(".")[0], fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text).replace(tzinfo=None)
    except ValueError:
        return None


def iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def classify_new(kind: str, api: dict) -> str:
    if kind == "prespec":
        return "신규"
    kind_nm = str(api.get("ntceKindNm", ""))
    if "취소" in kind_nm:
        return "취소"
    if str(api.get("reNtceYn", "")).upper() == "Y" or "재공고" in kind_nm:
        return "재공고"
    ord_ = str(api.get("bidNtceOrd", "000")).strip()
    if "변경" in kind_nm or "정정" in kind_nm or (ord_.isdigit() and int(ord_) > 0):
        return "정정"
    return "신규"


def _shard_month(rec: dict) -> str:
    api = rec.get("api", {})
    dt = parse_dt(api.get("rgstDt") or api.get("bidNtceDt") or api.get("rcptDt")) or parse_dt(rec.get("first_seen_at"))
    return dt.strftime("%Y-%m") if dt else "unknown"


class Store:
    def __init__(self):
        self.records: dict[str, dict[str, dict]] = {"bid": {}, "prespec": {}}
        self.meta: dict = {"schema_version": SCHEMA_VERSION, "sources": {}, "runs": []}

    # ---------------- 저장/불러오기 ----------------
    @classmethod
    def load(cls, state_dir: Path | None) -> "Store":
        store = cls()
        if not state_dir or not Path(state_dir).exists():
            return store
        state_dir = Path(state_dir)
        meta_path = state_dir / "meta.json"
        if meta_path.exists():
            store.meta = json.loads(meta_path.read_text(encoding="utf-8"))
        for kind in store.records:
            for shard in sorted((state_dir / "notices" / kind).glob("*.json")):
                store.records[kind].update(json.loads(shard.read_text(encoding="utf-8")))
        return store

    def save(self, state_dir: Path) -> None:
        state_dir = Path(state_dir)
        for kind, recs in self.records.items():
            shards: dict[str, dict] = {}
            for rid, rec in recs.items():
                shards.setdefault(_shard_month(rec), {})[rid] = rec
            kdir = state_dir / "notices" / kind
            kdir.mkdir(parents=True, exist_ok=True)
            for old in kdir.glob("*.json"):
                if old.stem not in shards:
                    old.unlink()
            for month, data in shards.items():
                (kdir / f"{month}.json").write_text(
                    json.dumps(dict(sorted(data.items())), ensure_ascii=False, indent=1, sort_keys=True) + "\n",
                    encoding="utf-8")
        (state_dir / "meta.json").write_text(json.dumps(self.meta, ensure_ascii=False, indent=2) + "\n",
                                             encoding="utf-8")

    # ---------------- 병합 ----------------
    def merge(self, kind: str, items: list[dict], now: datetime, source_key: str) -> list[dict]:
        """수집한 공고를 이력에 반영하고 이번에 생긴 사건 목록을 돌려준다."""
        events = []
        recs = self.records[kind]
        for item in items:
            rid = record_id(kind, item)
            if not rid.strip("-"):
                continue
            api = {f: str(item.get(f, "")).strip() for f in STORED_FIELDS[kind] if str(item.get(f, "")).strip()}
            rec = recs.get(rid)
            if rec is None:
                etype = classify_new(kind, api)
                rec = {"record_id": rid, "kind": kind, "source": source_key, "api": api,
                       "first_seen_at": iso(now), "last_seen_at": iso(now), "last_changed_at": None,
                       "events": [{"at": iso(now), "type": etype}], "history": [], "compacted": False}
                recs[rid] = rec
                events.append({"record_id": rid, "kind": kind, "type": etype})
                continue
            changes = {}
            for f in TRACKED_FIELDS[kind]:
                old, new = rec["api"].get(f, ""), api.get(f, "")
                if new and old != new and not (rec.get("compacted") and not old):
                    changes[f] = [old or None, new]
            rec["api"] = {**rec["api"], **api}
            rec["compacted"] = False
            rec["last_seen_at"] = iso(now)
            if changes:
                rec["last_changed_at"] = iso(now)
                rec["history"].append({"at": iso(now), "changes": changes})
                etype = "취소" if "취소" in api.get("ntceKindNm", "") and "ntceKindNm" in changes else "변경"
                rec["events"].append({"at": iso(now), "type": etype, "fields": sorted(changes)})
                rec["events"] = rec["events"][-MAX_EVENTS:]
                events.append({"record_id": rid, "kind": kind, "type": etype, "fields": sorted(changes)})
        return events

    # ---------------- 관련성 ----------------
    def apply_relevance(self, judge) -> None:
        for kind, recs in self.records.items():
            for rec in recs.values():
                r = judge.judge(kind, rec["api"])
                rec["relevance"] = {"level": r.level, "reason": r.reason, "matched": r.matches}
                if kind == "prespec":
                    div = rec["api"].get("bsnsDivNm", "")
                    rec["service_check"] = None if "용역" in div else "용역 여부 확인 필요 (업무구분: "\
                        + (div or "없음") + ")"

    # ---------------- 관계 ----------------
    def build_relations(self, candidate_days: int = 180, min_similarity: float = 0.5) -> None:
        bids, pres = self.records["bid"], self.records["prespec"]
        by_bid_no: dict[str, list[str]] = {}
        for rid, rec in bids.items():
            by_bid_no.setdefault(rec["api"].get("bidNtceNo", ""), []).append(rid)
        pre_to_bids: dict[str, set[str]] = {}

        for rid, rec in bids.items():
            api = rec["api"]
            rel = {"other_orders": sorted(r for r in by_bid_no.get(api.get("bidNtceNo", ""), []) if r != rid),
                   "prespec_no": None, "previous_notice_no": api.get("befBidBbancNo") or None, "candidates": []}
            pno = api.get("bfSpecRgstNo", "")
            if pno:
                rel["prespec_no"] = pno  # API 가 제공하는 명시 연결
                pre_to_bids.setdefault(pno, set()).add(api.get("bidNtceNo", ""))
            rec["related"] = rel

        for rid, rec in pres.items():
            listed = [n.strip() for n in re.split(r"[,\s]+", rec["api"].get("bidNtceNoList", "")) if n.strip()]
            explicit = sorted(set(listed) | pre_to_bids.get(rid, set()))
            rec["related"] = {"bid_notice_nos": explicit, "candidates": []}

        # 명시 연결이 없는 관련 사전규격 → 입찰공고 연결 후보 (자동 확정하지 않음)
        org_index: dict[str, list[str]] = {}
        for rid, rec in bids.items():
            for org in (rec["api"].get("ntceInsttNm"), rec["api"].get("dminsttNm")):
                if org:
                    org_index.setdefault(_norm_org(org), []).append(rid)
        for rid, rec in pres.items():
            if rec["related"]["bid_notice_nos"] or rec.get("relevance", {}).get("level", "low") == "low":
                continue
            pdt = parse_dt(rec["api"].get("rgstDt") or rec["api"].get("rcptDt"))
            title = rec["api"].get("prdctClsfcNoNm", "")
            cands = {}
            for org in (rec["api"].get("orderInsttNm"), rec["api"].get("rlDminsttNm")):
                for bid_rid in org_index.get(_norm_org(org or ""), []):
                    b = bids[bid_rid]["api"]
                    bdt = parse_dt(b.get("bidNtceDt") or b.get("rgstDt"))
                    if pdt and bdt and not (timedelta(0) <= bdt - pdt <= timedelta(days=candidate_days)):
                        continue
                    sim = similarity(title, b.get("bidNtceNm", ""))
                    if sim >= min_similarity:
                        cands[b.get("bidNtceNo", "")] = max(sim, cands.get(b.get("bidNtceNo", ""), 0))
            rec["related"]["candidates"] = [
                {"bid_notice_no": no, "similarity": round(s, 2), "basis": "기관명 일치 + 사업명 유사 + 기간"}
                for no, s in sorted(cands.items(), key=lambda x: -x[1])[:5]]

    # ---------------- 보관 정리 ----------------
    def compact(self, now: datetime, after_days: int) -> int:
        """관련성 낮은 오래된 공고는 판정에 필요한 항목만 남긴다 (삭제하지 않음)."""
        from_fields = {}
        n = 0
        for kind, recs in self.records.items():
            keep = set(ID_FIELDS[kind]) | set(COMPACT_EXTRA) | from_fields.setdefault(kind, set())
            for rec in recs.values():
                if rec.get("compacted") or rec.get("relevance", {}).get("level") != "low":
                    continue
                dt = parse_dt(rec["api"].get("rgstDt") or rec["api"].get("rcptDt")) or parse_dt(rec["first_seen_at"])
                if dt and now.replace(tzinfo=None) - dt > timedelta(days=after_days):
                    rec["api"] = {k: v for k, v in rec["api"].items() if k in keep or k in _RELEVANCE_FIELDS[kind]}
                    rec["compacted"] = True
                    n += 1
        return n


_RELEVANCE_FIELDS = {
    "bid": {"bidNtceNm", "purchsObjPrdctList", "pubPrcrmntClsfcNm", "pubPrcrmntMidClsfcNm",
            "pubPrcrmntLrgClsfcNm", "srvceDivNm", "ntceInsttNm", "dminsttNm"},
    "prespec": {"prdctClsfcNoNm", "prdctDtlList", "bsnsDivNm", "orderInsttNm", "rlDminsttNm"},
}


def _norm_org(name: str) -> str:
    return re.sub(r"[\s()（）]", "", name or "")


def _bigrams(text: str) -> set[str]:
    t = re.sub(r"[\s\W_]+", "", text or "")
    t = re.sub(r"\d{4}년?", "", t)  # 연도는 비교에서 제외
    return {t[i:i + 2] for i in range(len(t) - 1)}


def similarity(a: str, b: str) -> float:
    x, y = _bigrams(a), _bigrams(b)
    if not x or not y:
        return 0.0
    return len(x & y) / len(x | y)
