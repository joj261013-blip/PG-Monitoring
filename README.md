# PG-Monitoring

나라장터 PG 입찰공고 자동수집 시스템

조달청 나라장터의 **입찰공고정보서비스**와 **사전규격정보서비스**(공공데이터포털 Open API)를 활용해
PG(전자지급결제대행) 관련 공고를 자동으로 수집·선별하고 변경이력을 관리합니다.

> **현재 상태: 2단계 – API 연결 테스트 구현됨**
> 두 API의 연결을 확인하는 테스트(수동 실행)만 있습니다. 전체 공고 수집과 자동 정기 실행은 아직 구현되지 않았습니다.

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
│   ├── api_check.py       # 나라장터 API 연결 테스트 (python -m pg_monitoring.api_check)
│   ├── collectors/        # 나라장터 API 수집 (예정)
│   ├── filters/           # PG 관련 공고 선별 (예정)
│   └── storage/           # 저장 및 변경이력 관리 (예정)
├── .github/workflows/
│   └── api-connection-test.yml  # API 연결 테스트 (GitHub Actions, 수동 실행)
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
| GitHub Actions | 저장소 **Settings → Secrets and variables → Actions** 에 `NARA_API_KEY` 등록 |

- `.env`, `.env.*`, `*.key`, `secrets/` 등은 `.gitignore`로 업로드가 차단됩니다.
- `.env.example`에는 항목 이름만 있고 실제 값은 비어 있습니다.
- 프로그램은 인증키를 화면/로그/오류 메시지에 **전혀** 출력하지 않습니다(설정 여부만 표시).
- 입찰공고정보서비스와 사전규격정보서비스는 같은 인증키를 사용합니다.
- Encoding 키와 Decoding 키 중 어느 것을 등록해도 동작합니다(중복 URL 인코딩 자동 방지). 권장은 Decoding 키.
- 테스트(`tests/test_config.py`)가 `.env` 파일이 실제로 업로드 제외되는지 자동 확인합니다.

## 환경변수

| 이름 | 설명 | 기본값 |
|---|---|---|
| `NARA_API_KEY` | 공공데이터포털 인증키 (이전 이름 `G2B_SERVICE_KEY`도 인식) | (없음) |
| `PG_KEYWORDS` | PG 공고 선별 키워드(쉼표 구분) | `PG,전자지급결제대행,지급결제대행,결제대행,전자결제,간편결제` |
| `DATA_DIR` | 수집 데이터 저장 폴더 | `data` |
| `LOG_LEVEL` | 로그 수준 | `INFO` |

## API 연결 테스트

### 사용하는 API (공공데이터포털 조달청 Open API)

| 서비스 | 요청 주소 (오퍼레이션) |
|---|---|
| 조달청_나라장터 입찰공고정보서비스 | `https://apis.data.go.kr/1230000/ad/BidPublicInfoService/getBidPblancListInfoServc` (용역 입찰공고 목록) |
| 조달청_나라장터 사전규격정보서비스 | `https://apis.data.go.kr/1230000/ao/HrcspSsstndrdInfoService/getPublicPrcureThngInfoServc` (용역 사전규격 목록) |

요청 파라미터: `serviceKey`, `pageNo=1`, `numOfRows`(기본 5), `type=json`, `inqryDiv=1`(등록일시 기준),
`inqryBgnDt`/`inqryEndDt`(YYYYMMDDHHMM, 한국시간, 기본 최근 7일)

※ 2025년 개편 전 주소(`BidPublicInfoService04` 등)는 폐기되어 `NO_OPENAPI_SERVICE_ERROR`가 반환됩니다.

### GitHub에서 실행하기 (비개발자용)

1. GitHub 저장소 페이지 상단의 **Actions** 탭을 누릅니다.
2. 왼쪽 목록에서 **나라장터 API 연결 테스트**를 누릅니다.
3. 오른쪽의 **Run workflow** 버튼 → (기본값 그대로) 초록색 **Run workflow** 를 누릅니다.
4. 잠시 후 목록에 생긴 실행 항목을 누르면 결과 요약이 나옵니다. (초록 ✅ = 두 API 모두 정상, 빨강 ❌ = 하나 이상 실패)
5. 실패한 경우 결과 요약의 **오류 원인 / 해결 방법**을 확인하세요.

### 결과 판정 기준

- **성공**: HTTP 200 + API 응답코드 `00` + 공고 데이터 1건 이상
- 연결은 되었지만 조회 기간에 공고가 없으면 실패로 표시하고 "조회 기간을 늘리라"고 안내합니다.

## 진행 단계

- [x] 1단계: 프로젝트 기본 구조
- [x] 2단계: 나라장터 API 연결 테스트 (입찰공고·사전규격)
- [ ] 2-2단계: 전체 공고 수집
- [ ] 3단계: PG 공고 선별 및 변경이력 관리
- [ ] 4단계: GitHub Actions 정기 실행
