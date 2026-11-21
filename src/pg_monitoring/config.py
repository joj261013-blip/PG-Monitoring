"""환경설정 로딩.

인증키 등 민감정보는 코드에 직접 쓰지 않고 환경변수에서만 읽습니다.
- 로컬/Claude Code: 프로젝트 최상위의 `.env` 파일
- GitHub Actions: 저장소 Secrets → 환경변수로 전달
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]

DEFAULT_KEYWORDS = "PG,전자지급결제대행,지급결제대행,결제대행,전자결제,간편결제"


@dataclass(frozen=True)
class Settings:
    service_key: str = field(repr=False)  # repr에서 제외 → 로그에 키가 찍히지 않음
    keywords: tuple[str, ...]
    data_dir: Path
    log_level: str

    @property
    def has_service_key(self) -> bool:
        return bool(self.service_key)

    @property
    def masked_service_key(self) -> str:
        """화면 출력용으로 가린 인증키 (앞 4자리만 표시)."""
        if not self.service_key:
            return "(미설정)"
        return self.service_key[:4] + "*" * 8


def _split_keywords(raw: str) -> tuple[str, ...]:
    return tuple(k.strip() for k in raw.split(",") if k.strip())


def load_settings(env_file: Path | None = None) -> Settings:
    """`.env`(있으면)와 환경변수에서 설정을 읽어 Settings 객체로 반환.

    이미 설정된 환경변수(GitHub Actions Secrets 등)가 `.env`보다 우선합니다.
    """
    load_dotenv(env_file or PROJECT_ROOT / ".env", override=False)

    data_dir = Path(os.getenv("DATA_DIR", "data"))
    if not data_dir.is_absolute():
        data_dir = PROJECT_ROOT / data_dir

    return Settings(
        service_key=os.getenv("G2B_SERVICE_KEY", "").strip(),
        keywords=_split_keywords(os.getenv("PG_KEYWORDS", DEFAULT_KEYWORDS)),
        data_dir=data_dir,
        log_level=os.getenv("LOG_LEVEL", "INFO").strip().upper() or "INFO",
    )
