# PG-Monitoring

나라장터 PG 입찰공고 자동수집 시스템

조달청 나라장터의 **입찰공고정보서비스**와 **사전규격정보서비스**(공공데이터포털 Open API)를 활용해
PG(전자지급결제대행) 관련 공고를 자동으로 수집·선별하고 변경이력을 관리합니다.

> **현재 상태: 1단계 – 기본 프로젝트 구조만 구성됨**
> 실제 API 호출, 공고 수집, GitHub Actions 자동 실행은 아직 구현되지 않았습니다.

## 목표

- 신규·정정·재공고 수집 및 변경이력 관리
- PG 관련 공고 자동 선별(키워드 기반)
- GitHub Actions로 정기 실행 (예정)
- 유료 AI API·유료 서버 없이 무료로 독립 운영 (GitHub 무료 기능 + 공공데이터포털 무료 API)

## 폴더 구조

```
PG-Monitoring/
├── src/pg_monitoring/
│   ├── __init__.py        # 패키지 정보(버전)
│   ├── __main__.py        # 실행 진입점 (python -m pg_monitoring)
│   ├── config.py          # .env / 환경변수에서 설정 읽기
│   ├── collectors/        # 나라장터 API 수집 (예정)
│   ├── filters/           # PG 관련 공고 선별 (예정)
│   └── storage/           # 저장 및 변경이력 관리 (예정)
├── tests/                 # 자동 테스트 (pytest)
├── data/                  # 수집 결과 저장 폴더 (내용은 GitHub에 올라가지 않음)
├── .env.example           # 환경설정 예시 (실제 키 없음)
├── .gitignore             # GitHub 업로드 제외 목록
├── .python-version        # 권장 Python 버전 (3.12)
├── pyproject.toml         # 프로젝트 정보·테스트 설정
├── requirements.txt       # 실행용 패키지 목록
└── requirements-dev.txt   # 테스트용 패키지 목록
```

## Python 버전

- **권장: Python 3.12** (`.python-version`)
- 지원 범위: Python 3.11 ~ 3.13
- GitHub Actions에서는 `actions/setup-python`으로 3.12를 지정해 동일 환경을 재현할 예정입니다.
- 패키지 버전은 `requirements.txt`에 고정되어 있어 어느 환경에서도 같은 버전이 설치됩니다.

## 실행 방법

```bash
pip install -r requirements-dev.txt
```

```bash
python -m pytest
```

```bash
PYTHONPATH=src python -m pg_monitoring
```

마지막 명령은 현재 설정 상태(인증키 설정 여부는 가려서 표시)를 출력합니다.

## 인증키 관리 (중요)

공공데이터포털 인증키는 **절대 코드나 GitHub에 직접 올리지 않습니다.**

| 실행 환경 | 인증키 넣는 곳 |
|---|---|
| Claude Code 등 개발 환경 | 프로젝트 최상위의 `.env` 파일 (`.env.example`을 복사해 작성) |
| GitHub Actions (예정) | 저장소 **Settings → Secrets and variables → Actions** 에 `G2B_SERVICE_KEY` 등록 |

- `.env`, `.env.*`, `*.key`, `secrets/` 등은 `.gitignore`로 업로드가 차단됩니다.
- `.env.example`에는 항목 이름만 있고 실제 값은 비어 있습니다.
- 프로그램은 인증키를 화면/로그에 출력할 때 앞 4자리만 보여주고 나머지는 가립니다.
- 테스트(`tests/test_config.py`)가 `.env` 파일이 실제로 업로드 제외되는지 자동 확인합니다.

## 환경변수

| 이름 | 설명 | 기본값 |
|---|---|---|
| `G2B_SERVICE_KEY` | 공공데이터포털 인증키 | (없음) |
| `PG_KEYWORDS` | PG 공고 선별 키워드(쉼표 구분) | `PG,전자지급결제대행,지급결제대행,결제대행,전자결제,간편결제` |
| `DATA_DIR` | 수집 데이터 저장 폴더 | `data` |
| `LOG_LEVEL` | 로그 수준 | `INFO` |

## 진행 단계

- [x] 1단계: 프로젝트 기본 구조
- [ ] 2단계: 나라장터 API 연동 (입찰공고·사전규격)
- [ ] 3단계: PG 공고 선별 및 변경이력 관리
- [ ] 4단계: GitHub Actions 정기 실행
