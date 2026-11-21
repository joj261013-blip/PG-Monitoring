"""공개 결과 생성: index.html(사람용), latest.json(ChatGPT용 요약), latest.txt(텍스트 대안), data/*.json(상세).

원칙
- 공개 허용 항목만 내보낸다 (담당자 이름·연락처·이메일, 첨부파일 주소, 인증키 없음)
- API 에 없는 값은 만들지 않고 null 로 둔다
- 수집 상태(정상/일부/실패)와 마지막 정상 수집시각을 항상 표시한다
- 관련성 '낮음' 공고는 공개하지 않고 건수만 표시한다 (이력은 비공개 저장소에 보관)
"""

from __future__ import annotations

import html
import json
from datetime import datetime, timedelta
from pathlib import Path

from pg_monitoring.history import FIELD_LABELS, iso, parse_dt
from pg_monitoring.publish import _safe_link

SCHEMA_VERSION = "1.0"
STATUS_LABELS = {"success": "정상 수집", "partial": "일부 수집", "failed": "수집 실패", "skipped": "건너뜀"}
LEVELS = ("direct", "review")
LEVEL_LABELS = {"direct": "직접 관련", "review": "검토 필요", "low": "낮음"}
CHANGE_TYPES = ("정정", "재공고", "취소", "변경")


def _int(v):
    try:
        return int(float(str(v).replace(",", "")))
    except (TypeError, ValueError):
        return None


def _s(v):
    v = str(v or "").strip()
    return v or None


def _last_event(rec) -> dict:
    return (rec.get("events") or [{}])[-1]


def _rel(rec) -> dict:
    return rec.get("relevance", {"level": "low", "reason": "", "matched": []})


def public_record(rec: dict) -> dict:
    api, rel = rec["api"], _rel(rec)
    common = {
        "record_id": rec["record_id"],
        "event_type": _last_event(rec).get("type"),
        "relevance_level": rel["level"],
        "relevance_label": LEVEL_LABELS.get(rel["level"]),
        "relevance_reason": rel.get("reason"),
        "first_seen_at": rec.get("first_seen_at"),
        "last_seen_at": rec.get("last_seen_at"),
        "last_changed_at": rec.get("last_changed_at"),
    }
    if rec["kind"] == "bid":
        related = rec.get("related", {})
        return {**common, "notice_type": "입찰공고",
                "bid_no": _s(api.get("bidNtceNo")), "bid_ord": _s(api.get("bidNtceOrd")),
                "notice_kind": _s(api.get("ntceKindNm")), "re_notice": _s(api.get("reNtceYn")),
                "title": _s(api.get("bidNtceNm")), "notice_org": _s(api.get("ntceInsttNm")),
                "demand_org": _s(api.get("dminsttNm")), "notice_dt": _s(api.get("bidNtceDt")),
                "close_dt": _s(api.get("bidClseDt")), "open_dt": _s(api.get("opengDt")),
                "est_price": _int(api.get("presmptPrce")), "budget": _int(api.get("asignBdgtAmt")),
                "change_reason": _s(api.get("chgNtceRsn")),
                "source_url": _safe_link(api.get("bidNtceDtlUrl", "")) or None,
                "related": {"prespec_no": related.get("prespec_no"),
                            "other_orders": related.get("other_orders", []),
                            "previous_notice_no": related.get("previous_notice_no")}}
    related = rec.get("related", {})
    return {**common, "notice_type": "사전규격",
            "prespec_no": _s(api.get("bfSpecRgstNo")), "title": _s(api.get("prdctClsfcNoNm")),
            "order_org": _s(api.get("orderInsttNm")), "demand_org": _s(api.get("rlDminsttNm")),
            "service_div": _s(api.get("bsnsDivNm")), "service_check": rec.get("service_check"),
            "budget": _int(api.get("asignBdgtAmt")), "registered_dt": _s(api.get("rgstDt")),
            "opinion_close_dt": _s(api.get("opninRgstClseDt")), "changed_dt": _s(api.get("chgDt")),
            "source_url": None,  # API 가 사전규격 원문 주소를 제공하지 않음
            "related": {"bid_notice_nos": related.get("bid_notice_nos", []),
                        "link_candidates": related.get("candidates", [])}}


def _reg_dt(rec):
    a = rec["api"]
    return parse_dt(a.get("bidNtceDt") or a.get("rgstDt") or a.get("rcptDt"))


def _sort_key(rec):
    return (0 if _rel(rec)["level"] == "direct" else 1, -(_reg_dt(rec) or datetime.min).timestamp())


def select(store, now: datetime, conf: dict) -> dict:
    hours = float(conf.get("new_window_hours", 24))
    win = now.replace(tzinfo=None) - timedelta(hours=hours)
    recent_reg = now.replace(tzinfo=None) - timedelta(hours=hours + 48)
    out = {"bid_new": [], "pre_new": [], "changed": [], "active": []}
    for kind, recs in store.records.items():
        for rec in recs.values():
            if _rel(rec)["level"] not in LEVELS:
                continue
            first = (rec.get("events") or [{}])[0]
            first_at = parse_dt(first.get("at"))
            reg = _reg_dt(rec)
            is_recent_reg = reg is None or reg >= recent_reg
            listed = False
            if first.get("type") == "신규" and first_at and first_at >= win and is_recent_reg:
                out["bid_new" if kind == "bid" else "pre_new"].append(rec)
                listed = True
            evs = [e for e in rec.get("events", []) if e.get("type") in CHANGE_TYPES
                   and (parse_dt(e.get("at")) or datetime.min) >= win]
            # 같은 날 신규 등록 후 변경된 공고는 신규·변경 양쪽에 모두 표시 (변경을 숨기지 않음)
            if evs and (evs[-1].get("type") == "변경" or is_recent_reg):
                out["changed"].append(rec)
                listed = True
            if listed:
                continue
            close = parse_dt(rec["api"].get("bidClseDt") or rec["api"].get("opninRgstClseDt"))
            if close and close >= now.replace(tzinfo=None):
                out["active"].append(rec)
    for k in ("bid_new", "pre_new", "changed"):
        out[k].sort(key=_sort_key)
    out["active"].sort(key=lambda r: (0 if _rel(r)["level"] == "direct" else 1,
                                      parse_dt(r["api"].get("bidClseDt") or r["api"].get("opninRgstClseDt"))))
    return out


def _changed_entry(rec, win) -> dict:
    d = public_record(rec)
    changes = []
    for h in rec.get("history", []):
        if (parse_dt(h.get("at")) or datetime.min) >= win:
            for f, (old, new) in h["changes"].items():
                changes.append({"field": FIELD_LABELS.get(f, f), "before": old, "after": new, "at": h["at"]})
    d["changes"] = changes
    return d


def build(store, run: dict, now: datetime, conf: dict) -> dict:
    """run: 이번 실행 요약 {status, sources:{key: summary}, ...}"""
    sel = select(store, now, conf)
    win = now.replace(tzinfo=None) - timedelta(hours=float(conf.get("new_window_hours", 24)))
    meta_sources = store.meta.get("sources", {})
    ends = [parse_dt(m.get("last_success_end")) for m in meta_sources.values()]
    starts = [parse_dt(m.get("first_coverage_start")) for m in meta_sources.values()]
    counts = {kind: {lv: sum(1 for r in recs.values() if _rel(r)["level"] == lv) for lv in ("direct", "review", "low")}
              for kind, recs in store.records.items()}
    sources_out = {}
    for key, m in meta_sources.items():
        cur = run.get("sources", {}).get(key, {})
        sources_out[key] = {
            "label": m.get("label", key),
            "status_this_run": cur.get("status", "failed" if run["status"] == "failed" else None),
            "last_success_end": m.get("last_success_end"),
            "last_attempt_at": m.get("last_attempt_at"),
            "fetched_this_run": cur.get("fetched_unique"),
            "reported_total_this_run": cur.get("reported_total"),
            "errors_this_run": cur.get("errors", [])[:5],
        }
    max_active = int(conf.get("max_active_in_summary", 50))
    data = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": iso(now),
        "last_success_at": store.meta.get("last_full_success_at"),
        "collection_status": run["status"],
        "collection_status_label": STATUS_LABELS.get(run["status"], run["status"]),
        "coverage_start": iso(min(s for s in starts if s)) if any(starts) else None,
        "coverage_end": iso(min(e for e in ends if e)) if ends and all(ends) else None,
        "data_completeness": {
            "status": {"success": "complete", "partial": "partial", "failed": "stale"}.get(run["status"], "stale"),
            "sources": sources_out,
            "note": ("모든 API를 조회 구간 전체 페이지까지 정상 수집" if run["status"] == "success" else
                     "일부 또는 전체 수집 실패: 아래 공고는 마지막으로 정상 수집된 데이터 기준이며 최신이 아닐 수 있음"),
        },
        "report_window": {"start": iso(win), "end": iso(now.replace(tzinfo=None)),
                          "hours": float(conf.get("new_window_hours", 24))},
        "verification_code": now.strftime("NARA-%Y%m%d-%H%M"),
        "counts": {
            "new_bid_notices": len(sel["bid_new"]), "new_pre_specs": len(sel["pre_new"]),
            "changed_notices": len(sel["changed"]), "active_notices": len(sel["active"]),
            "stored_by_relevance": counts,
        },
        "bid_notices": [public_record(r) for r in sel["bid_new"]],
        "pre_specs": [public_record(r) for r in sel["pre_new"]],
        "changed_notices": [_changed_entry(r, win) for r in sel["changed"]],
        "active_notices": [public_record(r) for r in sel["active"][:max_active]],
        "detail_files": {"bid_notices": "data/bid_notices.json", "pre_specs": "data/pre_specs.json",
                         "validation": "data/validation.json"},
        "notes": [
            "출처: 공공데이터포털 조달청_나라장터 입찰공고정보서비스(용역)·사전규격정보서비스(용역)",
            "관련성(relevance_level)은 키워드 기반 자동 판정이며 direct=직접 관련, review=검토 필요",
            "bid_notices/pre_specs: report_window 안에 처음 수집된 신규 공고. changed_notices: 같은 기간의 정정·재공고·변경",
            "active_notices: 마감 전인 직접 관련·검토 필요 공고",
            "사전규격의 원문 주소(source_url)는 API가 제공하지 않아 null",
        ],
    }
    return data


def detail_files(store, now: datetime, conf: dict) -> dict[str, dict]:
    days = int(conf.get("detail_days", 90))
    since = now.replace(tzinfo=None) - timedelta(days=days)
    out = {}
    for kind, name in (("bid", "bid_notices"), ("prespec", "pre_specs")):
        rows = [r for r in store.records[kind].values()
                if _rel(r)["level"] in LEVELS and (_reg_dt(r) or parse_dt(r.get("first_seen_at")) or since) >= since]
        rows.sort(key=_sort_key)
        items = []
        for r in rows:
            d = public_record(r)
            d["history"] = [{"at": h["at"], "changes": [{"field": FIELD_LABELS.get(f, f), "before": o, "after": n}
                                                         for f, (o, n) in h["changes"].items()]}
                            for h in r.get("history", [])]
            d["events"] = r.get("events", [])
            items.append(d)
        out[name] = {"schema_version": SCHEMA_VERSION, "generated_at": iso(now), "days": days,
                     "count": len(items), "items": items}
    return out


# ---------------------------------------------------------------- 텍스트 / HTML
def _money(v):
    return f"{v:,}원" if isinstance(v, int) else "-"


def _line(d: dict) -> str:
    if d["notice_type"] == "입찰공고":
        head = f"[{d['bid_no']}-{d['bid_ord']}] {d['title']}"
        org = d.get("notice_org") or "-"
        when = f"마감 {d.get('close_dt') or '-'}"
    else:
        head = f"[사전규격 {d['prespec_no']}] {d['title']}"
        org = d.get("order_org") or "-"
        when = f"의견마감 {d.get('opinion_close_dt') or '-'}"
    return f"- ({d['relevance_label']}) {head} / {org} / 예산 {_money(d.get('budget'))} / {when}\n  근거: {d['relevance_reason']}"


def build_text(data: dict) -> str:
    L = [f"나라장터 PG 관련 용역공고 요약 ({data['collection_status_label']})",
         f"생성: {data['generated_at']} / 마지막 정상 수집: {data['last_success_at'] or '없음'} / 확인코드 {data['verification_code']}",
         f"수집 범위: {data['coverage_start']} ~ {data['coverage_end']}", ""]
    for title, key in (("■ 신규 PG 관련 입찰공고", "bid_notices"), ("■ 신규 PG 관련 사전규격", "pre_specs")):
        L.append(f"{title} ({len(data[key])}건)")
        L += [_line(d) for d in data[key]] or ["- 없음"]
        L.append("")
    L.append(f"■ 정정·재공고·변경 ({len(data['changed_notices'])}건)")
    for d in data["changed_notices"]:
        L.append(_line(d).replace("- (", f"- [{d['event_type']}] (", 1))
        for c in d["changes"][:5]:
            L.append(f"  변경: {c['field']} {c['before']} → {c['after']}")
    if not data["changed_notices"]:
        L.append("- 없음")
    L += ["", f"■ 마감 전 주요 공고 ({len(data['active_notices'])}건)"]
    L += [_line(d) for d in data["active_notices"]] or ["- 없음"]
    return "\n".join(L) + "\n"


def _cell(v) -> str:
    return html.escape("-" if v is None or v == "" else str(v))


def _table(rows: list[dict], with_changes: bool = False) -> str:
    if not rows:
        return "<p>없음</p>"
    out = ['<div class="wrap"><table><thead><tr><th>구분</th><th>관련성</th><th>번호</th><th>공고명</th><th>기관</th>'
           '<th>예산</th><th>마감</th><th>판단 근거</th><th>원문</th></tr></thead><tbody>']
    for d in rows:
        if d["notice_type"] == "입찰공고":
            no, org, close = f"{d['bid_no']}-{d['bid_ord']}", d.get("notice_org"), d.get("close_dt")
        else:
            no, org, close = d["prespec_no"], d.get("order_org"), d.get("opinion_close_dt")
        link = (f'<a href="{html.escape(d["source_url"])}" rel="noopener noreferrer">나라장터</a>'
                if d.get("source_url") else "-")
        kind = d["notice_type"] + (f" · {d['event_type']}" if d.get("event_type") else "")
        reason = _cell(d.get("relevance_reason"))
        if with_changes and d.get("changes"):
            reason += "<br>" + "<br>".join(_cell(f"{c['field']}: {c['before']} → {c['after']}") for c in d["changes"][:5])
        if d.get("service_check"):
            reason += "<br>" + _cell(d["service_check"])
        budget = f"{d['budget']:,}" if isinstance(d.get("budget"), int) else "-"
        out.append(f"<tr><td>{_cell(kind)}</td><td>{_cell(d['relevance_label'])}</td><td>{_cell(no)}</td>"
                   f"<td>{_cell(d['title'])}</td><td>{_cell(org)}</td><td>{budget}</td><td>{_cell(close)}</td>"
                   f"<td>{reason}</td><td>{link}</td></tr>")
    out.append("</tbody></table></div>")
    return "\n".join(out)


def build_html(data: dict) -> str:
    e = html.escape
    status_cls = {"success": "ok", "partial": "warn"}.get(data["collection_status"], "fail")
    src_rows = "".join(
        f"<li>{e(s['label'])}: {e(STATUS_LABELS.get(s['status_this_run'] or '', '-') )} · 정상 수집 끝 시각 "
        f"{e(s['last_success_end'] or '없음')} · 이번 수집 {e(str(s['fetched_this_run'] if s['fetched_this_run'] is not None else '-'))}건"
        + (f" · 오류: {e('; '.join(s['errors_this_run']))}" if s["errors_this_run"] else "") + "</li>"
        for s in data["data_completeness"]["sources"].values())
    c = data["counts"]
    return "\n".join([
        "<!doctype html>", '<html lang="ko">', "<head>", '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        "<meta http-equiv=\"Content-Security-Policy\" content=\"default-src 'none'; style-src 'unsafe-inline'\">",
        '<meta name="robots" content="noindex, nofollow">', '<meta name="referrer" content="no-referrer">',
        "<title>나라장터 PG 용역공고 모니터링</title>",
        "<style>body{font-family:sans-serif;max-width:1200px;margin:0 auto;padding:16px;line-height:1.5;color:#111;background:#fff}"
        "table{border-collapse:collapse;width:100%;font-size:13px}th,td{border:1px solid #ccc;padding:4px 6px;text-align:left;vertical-align:top}"
        "th{background:#f2f2f2}.wrap{overflow-x:auto}.ok{color:#0a7a2f}.warn{color:#9a6700}.fail{color:#b00020}"
        ".status{font-size:18px;font-weight:bold}</style>", "</head>", "<body>",
        "<h1>나라장터 PG 관련 용역공고 모니터링</h1>",
        f'<p class="status {status_cls}">수집 상태: {e(data["collection_status_label"])}</p>',
        "<ul>",
        f"<li>생성일시: {e(data['generated_at'])} · 확인코드: <strong>{e(data['verification_code'])}</strong></li>",
        f"<li>마지막 정상 수집: {e(data['last_success_at'] or '없음')}</li>",
        f"<li>수집 범위: {e(str(data['coverage_start']))} ~ {e(str(data['coverage_end']))}</li>",
        f"<li>신규 기준: 최근 {e(str(int(data['report_window']['hours'])))}시간 안에 처음 수집된 공고</li>",
        src_rows,
        '<li>기계 판독용: <a href="latest.json">latest.json</a> · <a href="latest.txt">latest.txt</a> · '
        '상세 <a href="data/bid_notices.json">입찰공고</a> · <a href="data/pre_specs.json">사전규격</a> · '
        '<a href="data/validation.json">검증 사례</a></li>',
        "</ul>",
        f"<p>{e(data['data_completeness']['note'])}</p>",
        f"<h2>신규 PG 관련 입찰공고 ({c['new_bid_notices']}건)</h2>", _table(data["bid_notices"]),
        f"<h2>신규 PG 관련 사전규격 ({c['new_pre_specs']}건)</h2>", _table(data["pre_specs"]),
        f"<h2>정정·재공고·변경 ({c['changed_notices']}건)</h2>", _table(data["changed_notices"], with_changes=True),
        f"<h2>마감 전 주요 공고 ({c['active_notices']}건 중 최대 {len(data['active_notices'])}건 표시)</h2>",
        _table(data["active_notices"]),
        "<h2>참고</h2><ul>" + "".join(f"<li>{e(n)}</li>" for n in data["notes"]) + "</ul>",
        "</body>", "</html>", ""])


def write_site(out_dir: Path, data: dict, details: dict[str, dict], validation: dict | None, scrub) -> list[Path]:
    out_dir = Path(out_dir)
    (out_dir / "data").mkdir(parents=True, exist_ok=True)
    files = {
        out_dir / "latest.json": json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        out_dir / "latest.txt": build_text(data),
        out_dir / "index.html": build_html(data),
        out_dir / ".nojekyll": "",
        out_dir / "README.md": ("# 나라장터 PG 관련 용역공고 (자동 생성)\n\n"
                                "이 저장소는 공공데이터포털 나라장터 API 수집 결과 중 공개 가능한 항목만 자동으로 게시합니다.\n"
                                "직접 수정하지 마세요. 다음 수집 때 덮어씁니다.\n\n"
                                "- 요약: `latest.json`, `latest.txt`, `index.html`\n- 상세: `data/`\n"),
    }
    for name, d in details.items():
        files[out_dir / "data" / f"{name}.json"] = json.dumps(d, ensure_ascii=False, indent=2) + "\n"
    files[out_dir / "data" / "validation.json"] = json.dumps(
        validation or {"status": "not_run", "note": "검증 사례 조회를 아직 실행하지 않음"}, ensure_ascii=False, indent=2) + "\n"
    for p, text in files.items():
        p.write_text(scrub(text), encoding="utf-8")
    return list(files)
