"""조회 구간 계산과 API별 수집.

- 조회 시작 = 직전 정상 수집 끝 시각 − 겹침 시간 (처음이면 initial_lookback_days 전)
  단, max_backfill_days 보다 오래 거슬러 올라가지 않음
- 구간을 chunk_days 단위로 나눠 각 구간의 모든 페이지를 받는다
- 앞에서부터 연속으로 성공한 구간까지만 '정상 수집 끝 시각'을 앞당긴다 → 실패 구간은 다음 실행에서 다시 조회
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from pg_monitoring.history import ID_FIELDS, iso, parse_dt
from pg_monitoring.nara_client import fetch_all
from pg_monitoring.settings import Source

FATAL_CODES = ("12", "20", "22", "30", "31", "32")  # 서비스 없음·접근거부·한도초과·미등록키·기간만료·IP


@dataclass
class ChunkResult:
    start: datetime
    end: datetime
    inqry_div: str
    ok: bool
    total_count: int
    fetched: int
    pages: int
    calls: int
    errors: list[str]

    def to_dict(self) -> dict:
        return {"start": iso(self.start), "end": iso(self.end), "inqry_div": self.inqry_div, "ok": self.ok,
                "total_count": self.total_count, "fetched": self.fetched, "pages": self.pages,
                "calls": self.calls, "errors": self.errors}


@dataclass
class SourceResult:
    key: str
    label: str
    kind: str
    status: str  # success | partial | failed
    window_start: datetime
    window_end: datetime
    covered_until: datetime | None  # 연속 성공한 구간의 끝 (없으면 None)
    items: list[dict] = field(default_factory=list, repr=False)
    chunks: list[ChunkResult] = field(default_factory=list)

    @property
    def calls(self) -> int:
        return sum(c.calls for c in self.chunks)

    def summary(self) -> dict:
        return {"key": self.key, "label": self.label, "status": self.status,
                "window_start": iso(self.window_start), "window_end": iso(self.window_end),
                "covered_until": iso(self.covered_until) if self.covered_until else None,
                "fetched_unique": len(self.items), "reported_total": sum(c.total_count for c in self.chunks),
                "calls": self.calls, "chunks": [c.to_dict() for c in self.chunks],
                "errors": [e for c in self.chunks for e in c.errors]}


def plan_window(source_meta: dict, now: datetime, conf: dict) -> tuple[datetime, datetime]:
    overlap = timedelta(hours=float(conf.get("overlap_hours", 6)))
    max_back = timedelta(days=float(conf.get("max_backfill_days", 30)))
    initial = timedelta(days=float(conf.get("initial_lookback_days", 14)))
    last = parse_dt(source_meta.get("last_success_end")) if source_meta else None
    if last is not None:
        last = last.replace(tzinfo=now.tzinfo)
        start = last - overlap
    else:
        start = now - initial
    start = max(start, now - max_back)
    return start.replace(second=0, microsecond=0), now.replace(second=0, microsecond=0)


def split_chunks(start: datetime, end: datetime, chunk_days: float) -> list[tuple[datetime, datetime]]:
    step = timedelta(days=chunk_days)
    out, cur = [], start
    while cur < end:
        nxt = min(cur + step, end)
        out.append((cur, nxt))
        cur = nxt
    return out or [(start, end)]


def _fmt(dt: datetime) -> str:
    return dt.strftime("%Y%m%d%H%M")


def collect_source(src: Source, service_key: str, source_meta: dict, now: datetime, conf: dict,
                   fetcher=fetch_all) -> SourceResult:
    start, end = plan_window(source_meta, now, conf)
    chunks = split_chunks(start, end, float(conf.get("chunk_days", 3)))
    res = SourceResult(src.key, src.label, src.kind, "failed", start, end, None)
    merged: dict[str, dict] = {}
    chunk_ok: dict[tuple, bool] = {}
    for cs, ce in chunks:
        for div in src.inqry_divs:
            fr = fetcher(src.path, {"inqryDiv": div, "inqryBgnDt": _fmt(cs), "inqryEndDt": _fmt(ce)}, service_key,
                         id_fields=ID_FIELDS[src.kind], page_size=int(conf.get("page_size", 100)),
                         max_pages=int(conf.get("max_pages", 300)), retries=int(conf.get("retries", 3)),
                         wait_seconds=float(conf.get("retry_wait_seconds", 5)))
            res.chunks.append(ChunkResult(cs, ce, div, fr.ok, fr.total_count, len(fr.items), fr.pages_fetched,
                                          fr.calls, fr.errors))
            for item in fr.items:  # 일부 실패한 구간에서 받은 데이터도 실제 데이터이므로 반영
                merged["-".join(str(item.get(f, "")).strip() for f in ID_FIELDS[src.kind])] = item
            chunk_ok[(cs, ce)] = chunk_ok.get((cs, ce), True) and fr.ok
            if any(f"오류 코드 {c} " in e for e in fr.errors for c in FATAL_CODES):
                break  # 인증·한도 오류는 다른 구간도 같은 결과이므로 호출을 낭비하지 않음
        else:
            continue
        for rest in chunks[chunks.index((cs, ce)) + 1:]:
            chunk_ok[rest] = False
        break
    res.items = list(merged.values())
    covered = None
    for cs, ce in chunks:
        if not chunk_ok.get((cs, ce)):
            break
        covered = ce
    res.covered_until = covered
    if all(chunk_ok.values()):
        res.status = "success"
    elif any(c.fetched for c in res.chunks) or covered:
        res.status = "partial"
    return res
