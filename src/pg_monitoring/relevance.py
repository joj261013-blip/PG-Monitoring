"""PG 관련성 판정.

config/keywords.toml 의 키워드를 공고의 여러 항목(공고명, 분류, 기관 등)에서 찾아
direct(직접 관련) / review(검토 필요) / low(낮음) 로 나누고, 판단 근거를 함께 남긴다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

LEVEL_LABELS = {"direct": "직접 관련", "review": "검토 필요", "low": "낮음"}
STRONG_CATEGORIES = ("collection", "payment_method", "settlement")


def _norm(text: str) -> str:
    return re.sub(r"\s+", "", str(text or "")).lower()


@dataclass
class Matcher:
    category: str
    word: str
    pattern: re.Pattern


@dataclass
class Relevance:
    level: str
    reason: str
    matches: list[dict] = field(default_factory=list)  # [{category, word, field}]

    def to_dict(self) -> dict:
        return {"relevance_level": self.level, "relevance_label": LEVEL_LABELS[self.level],
                "relevance_reason": self.reason, "matched": self.matches}


class RelevanceJudge:
    def __init__(self, keywords: dict):
        self.categories = {k: v for k, v in keywords.get("categories", {}).items()}
        self.labels = {k: str(v.get("label", k)) for k, v in self.categories.items()}
        self.fields = {kind: dict(v) for kind, v in keywords.get("fields", {}).items()}
        self.exclude = [_norm(w) for w in keywords.get("exclude", {}).get("words", []) if _norm(w)]
        self.skip_fields = {k: set(v.get("skip_fields", [])) for k, v in self.categories.items()}
        self.matchers: list[Matcher] = []
        for cat, conf in self.categories.items():
            for word in conf.get("words", []):
                w = _norm(word)
                if not w:
                    continue
                if re.fullmatch(r"[a-z0-9]+", w):
                    # 영문/숫자만 있는 단어(PG 등)는 앞뒤가 영문/숫자가 아닐 때만 일치 (UPGRADE 제외)
                    pat = re.compile(rf"(?<![a-z0-9]){re.escape(w)}(?![a-z0-9])")
                else:
                    pat = re.compile(re.escape(w))
                self.matchers.append(Matcher(cat, word, pat))

    def judge(self, kind: str, item: dict) -> Relevance:
        matches: list[dict] = []
        seen: set[tuple[str, str]] = set()
        for field_name, field_label in self.fields.get(kind, {}).items():
            text = _norm(item.get(field_name, ""))
            if not text:
                continue
            for ex in self.exclude:
                text = text.replace(ex, " ")
            for m in self.matchers:
                if field_name in self.skip_fields.get(m.category, ()):
                    continue
                if (m.word, field_name) in seen:
                    continue
                if m.pattern.search(text):
                    seen.add((m.word, field_name))
                    matches.append({"category": m.category, "word": m.word, "field": field_label})
        cats = {m["category"] for m in matches}
        strong = cats & set(STRONG_CATEGORIES)
        if "direct" in cats or len(strong) >= 2:
            level = "direct"
        elif matches:
            level = "review"
        else:
            level = "low"
        return Relevance(level, self._reason(level, matches), matches)

    def _reason(self, level: str, matches: list[dict]) -> str:
        if not matches:
            return "PG 관련 키워드 없음"
        parts = []
        for m in matches[:6]:
            parts.append(f"{m['field']}에 '{m['word']}'({self.labels.get(m['category'], m['category'])})")
        more = f" 외 {len(matches) - 6}건" if len(matches) > 6 else ""
        head = {"direct": "직접 관련", "review": "검토 필요"}[level]
        return f"{head}: " + ", ".join(parts) + more
