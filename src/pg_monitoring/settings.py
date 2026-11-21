"""config/*.toml 설정 읽기 (Python 표준 라이브러리 tomllib 사용)."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = PROJECT_ROOT / "config"


@dataclass(frozen=True)
class Source:
    key: str
    label: str
    path: str
    kind: str  # "bid" | "prespec"
    inqry_divs: tuple[str, ...]
    enabled: bool


def load_toml(path: Path) -> dict:
    with open(path, "rb") as f:
        return tomllib.load(f)


def load_settings(path: Path | None = None) -> dict:
    data = load_toml(path or CONFIG_DIR / "settings.toml")
    data.setdefault("collect", {})
    data.setdefault("report", {})
    data.setdefault("storage", {})
    data.setdefault("sources", {})
    return data


def enabled_sources(settings: dict) -> list[Source]:
    out = []
    for key, s in settings.get("sources", {}).items():
        src = Source(
            key=key,
            label=str(s.get("label", key)),
            path=str(s["path"]),
            kind=str(s.get("kind", "bid")),
            inqry_divs=tuple(str(d) for d in s.get("inqry_divs", ["1"])),
            enabled=bool(s.get("enabled", False)),
        )
        if src.enabled:
            if src.kind not in ("bid", "prespec"):
                raise ValueError(f"sources.{key}.kind 는 bid 또는 prespec 이어야 합니다: {src.kind}")
            out.append(src)
    return out


def load_keywords(path: Path | None = None) -> dict:
    return load_toml(path or CONFIG_DIR / "keywords.toml")
