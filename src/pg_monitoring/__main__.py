"""실행 진입점: `python -m pg_monitoring`

현재는 설정 점검만 수행합니다. (실제 공고 수집 기능은 다음 단계에서 구현)
"""

from __future__ import annotations

import sys

from pg_monitoring import __version__
from pg_monitoring.config import load_settings


def main() -> int:
    settings = load_settings()
    print(f"PG-Monitoring v{__version__}")
    print(f"- Python       : {sys.version.split()[0]}")
    print(f"- 인증키       : {settings.masked_service_key}")
    print(f"- 키워드       : {', '.join(settings.keywords)}")
    print(f"- 데이터 폴더  : {settings.data_dir}")
    print(f"- 로그 수준    : {settings.log_level}")
    if not settings.has_service_key:
        print("※ 인증키가 설정되지 않았습니다. (.env 또는 GitHub Secrets에 NARA_API_KEY 등록 필요)")
    print("현재는 기본 구조만 구성된 상태이며, 공고 수집 기능은 아직 구현되지 않았습니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
