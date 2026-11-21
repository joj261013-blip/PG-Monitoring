"""운영 실행 진입점: `python -m pg_monitoring.run --state-dir <이력폴더> --out-dir <결과폴더>`

1) 이력 불러오기 → 2) API별 수집(직전 정상 수집 이후, 겹침 포함, 전체 페이지) → 3) 이력 병합
4) 관련성 판정·관계 연결·보관 정리 → 5) 이력/공개 결과/원본 파일 쓰기
수집이 실패해도 기존 이력과 공개 데이터는 지우지 않고, 상태만 '실패'로 표시한다.
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timedelta
from pathlib import Path

from pg_monitoring import report
from pg_monitoring.api_check import KST, load_service_key, redact
from pg_monitoring.collect import collect_source
from pg_monitoring.history import Store, iso, parse_dt
from pg_monitoring.relevance import RelevanceJudge
from pg_monitoring.settings import enabled_sources, load_keywords, load_settings
from pg_monitoring.validate import run_validation

MAX_RUNS_KEPT = 60


def _gh_output(name: str, value: str) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"{name}={value}\n")


def should_skip(store: Store, now: datetime, minutes: int) -> bool:
    last = parse_dt(store.meta.get("last_full_success_at"))
    return bool(minutes) and last is not None and now.replace(tzinfo=None) - last < timedelta(minutes=minutes)


def execute(state_dir: Path | None, out_dir: Path, service_key: str, now: datetime, *, settings: dict,
            keywords: dict, validate: bool = False, skip_within_minutes: int = 0, collector=collect_source,
            validator=run_validation) -> dict:
    store = Store.load(state_dir)
    if should_skip(store, now, skip_within_minutes):
        return {"status": "skipped", "produced": False,
                "message": f"최근 {skip_within_minutes}분 안에 정상 수집되어 이번 보조 실행은 건너뜀"}

    judge = RelevanceJudge(keywords)
    conf = settings["collect"]
    run = {"started_at": iso(now), "status": "failed", "sources": {}, "errors": []}
    raw: dict[str, list] = {}
    sources = enabled_sources(settings)
    if not service_key:
        run["errors"].append("인증키(NARA_API_KEY)가 설정되지 않음")
    else:
        for src in sources:
            meta = store.meta.setdefault("sources", {}).setdefault(src.key, {})
            meta["label"] = src.label
            try:
                res = collector(src, service_key, meta, now, conf)
            except Exception as e:  # 한 API의 예기치 못한 오류가 다른 API 수집을 막지 않게 함
                run["sources"][src.key] = {"status": "failed", "errors": [f"프로그램 오류 ({type(e).__name__})"]}
                meta.update(last_attempt_at=iso(now), last_status="failed")
                continue
            events = store.merge(src.kind, res.items, now, src.key)
            meta.update(last_attempt_at=iso(now), last_status=res.status)
            if res.covered_until:
                meta["last_success_end"] = iso(res.covered_until)
                meta.setdefault("first_coverage_start", iso(res.window_start))
            summ = res.summary()
            summ["events"] = {t: sum(1 for e in events if e["type"] == t) for t in sorted({e["type"] for e in events})}
            run["sources"][src.key] = summ
            raw[src.key] = res.items
        statuses = [run["sources"].get(s.key, {}).get("status", "failed") for s in sources]
        if statuses and all(s == "success" for s in statuses):
            run["status"] = "success"
        elif any(s in ("success", "partial") for s in statuses):
            run["status"] = "partial"
    if run["status"] == "success":
        store.meta["last_full_success_at"] = iso(now)

    store.apply_relevance(judge)
    store.build_relations()
    run["compacted"] = store.compact(now, int(settings["storage"].get("compact_low_after_days", 90)))

    validation = store.meta.get("validation")
    if validate and service_key:
        validation = validator(store, judge, service_key, now, settings.get("validation", {}))
        store.meta["validation"] = validation

    store.meta["runs"] = (store.meta.get("runs", []) + [{
        "at": iso(now), "status": run["status"],
        "sources": {k: {"status": v.get("status"), "fetched": v.get("fetched_unique"), "calls": v.get("calls"),
                        "events": v.get("events", {})} for k, v in run["sources"].items()},
        "errors": run["errors"]}])[-MAX_RUNS_KEPT:]

    out_dir = Path(out_dir)
    store.save(out_dir / "state")
    raw_dir = out_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    for k, items in raw.items():
        (raw_dir / f"{k}.json").write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")

    data = report.build(store, run, now, settings["report"])
    details = report.detail_files(store, now, settings["report"])
    report.write_site(out_dir / "site", data, details, validation, scrub=lambda t: redact(t, service_key))
    run["produced"] = True
    run["summary"] = data
    run["validation"] = validation
    return run


def format_console(run: dict) -> str:
    if run["status"] == "skipped":
        return run["message"]
    d = run["summary"]
    L = ["=" * 60, f"수집 결과: {report.STATUS_LABELS.get(run['status'])}  (확인코드 {d['verification_code']})", "=" * 60]
    for key, s in run["sources"].items():
        L.append(f"■ {s.get('label', key)}: {report.STATUS_LABELS.get(s.get('status'), s.get('status'))}")
        if "window_start" in s:
            L.append(f"  조회 구간: {s['window_start']} ~ {s['window_end']} / 정상 수집 끝: {s['covered_until']}")
            L.append(f"  API 전체 건수 합계: {s['reported_total']} / 중복 제거 후 수집: {s['fetched_unique']} / 호출 {s['calls']}회")
            L.append(f"  이번 사건: {s.get('events') or '없음'}")
        for e in s.get("errors", [])[:10]:
            L.append(f"  오류: {e}")
    for e in run["errors"]:
        L.append(f"오류: {e}")
    c = d["counts"]
    L += ["", f"보관 중 공고(관련성별): {c['stored_by_relevance']}",
          f"신규 입찰공고(PG) {c['new_bid_notices']}건 / 신규 사전규격(PG) {c['new_pre_specs']}건 / "
          f"정정·변경 {c['changed_notices']}건 / 마감 전 주요 {c['active_notices']}건", ""]
    for x in d["bid_notices"][:20] + d["pre_specs"][:20]:
        no = x.get("bid_no") and f"{x['bid_no']}-{x['bid_ord']}" or x.get("prespec_no")
        L.append(f"  - [{x['notice_type']}/{x['relevance_label']}] {no} {x['title']} → {x['relevance_reason']}")
    v = run.get("validation")
    if v and v.get("cases"):
        L += ["", f"검증 사례 (입찰공고 검색 {v['bid_search']['days']}일, 호출 {v['calls']}회)"]
        for case in v["cases"]:
            L.append(f"  - {case['name']}: {case['status']} {case['found_count']}건 (자동 선별에 걸린 건 {case['caught_by_filter']})")
            for it in case["items"][:5]:
                L.append(f"      · [{it['notice_type']}] {it['record_id']} {it['title']} → {it['relevance_level']}")
    return "\n".join(L)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--state-dir", default="")
    ap.add_argument("--out-dir", default="out")
    args = ap.parse_args(argv)
    key = load_service_key()
    now = datetime.now(KST).replace(second=0, microsecond=0)
    run = execute(Path(args.state_dir) if args.state_dir else None, Path(args.out_dir), key, now,
                  settings=load_settings(), keywords=load_keywords(),
                  validate=os.environ.get("RUN_VALIDATION", "").lower() == "true",
                  skip_within_minutes=int(os.environ.get("SKIP_IF_SUCCESS_WITHIN_MINUTES", "0") or 0))
    text = redact(format_console(run), key)
    print(text)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write("## 나라장터 PG 모니터링 수집 결과\n\n```text\n" + text + "\n```\n")
    _gh_output("status", run["status"])
    _gh_output("produced", "true" if run.get("produced") else "false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
