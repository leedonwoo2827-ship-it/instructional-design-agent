# 교수설계 가이드 에이전트

ABCD 학습목표 · Bloom 개정분류 · 구성적 정렬(Biggs) · 백워드 설계·WHERETO(Wiggins & McTighe) ·
Mayer 멀티미디어 학습 원리를 적용해 **강의계획서 → 차시 원고 / PPT 개요**를 생성하고,
학습목표–평가–활동의 **정렬(alignment)** 을 자동 점검하는 Streamlit 앱입니다.

사내 **Ubion LiteLLM 프록시**(OpenAI 호환)를 사용하며, URL·API 키·모델은 앱 사이드바(설정)에서 입력합니다.

## 요구사항

- Python 3.10 ~ 3.12
- 사내망 접속 (LiteLLM 프록시 `192.168.50.119:4000` 도달 가능)
- 본인 LiteLLM virtual key (`sk-...`) — 사내 대시보드 `/ui/` 에서 발급

## 설치 & 실행 (Windows)

1. **`setup.bat`** 더블클릭 — `.venv` 생성, 의존성 설치, `.env` 준비
2. **`run.bat`** 더블클릭 — 브라우저에서 `http://localhost:8701` 열림
3. 좌측 **⚙️ 연결 설정**에 `sk-` 키 입력 → **💾 저장** (원하면 **연결 테스트**)
4. **1️⃣ 강의 정보** 입력 → 강의계획서 생성 → **2️⃣** 점검·수정 → **3️⃣** 원고 생성

macOS / Linux: `./setup.sh` 후 `./run.sh`.

## 사용 흐름

| 단계 | 내용 |
|---|---|
| 1️⃣ 강의 정보 | 과목·대상·주차·방식·주제 입력 → **강의계획서** 생성(스트리밍) |
| 2️⃣ 강의계획서 | 목표–평가–주차 정렬 매트릭스 확인, 수정 요청, **정렬·인지수준 점검** |
| 3️⃣ 원고 | 주차 선택 → **문서형 원고** 또는 **PPT 개요**(Mayer 원리) 생성, WHERETO 점검 |

각 산출물은 `.md` / `.doc`(한글·워드) 로 저장할 수 있습니다.

## 모델

사이드바에서 선택 (기본 `claude-sonnet-4-6`):

- `claude-sonnet-4-6` — 권장 · 균형
- `claude-opus-4-7` — 고품질
- `claude-haiku-4-5` — 빠름 · 경제적

## 구조

```
app.py                 Streamlit UI (3단계 · 사이드바 설정)
core/user_settings.py  설정 저장/로드 (data/user_settings.json, .env 기본값)
core/llm.py            LiteLLM 프록시 호출 (openai SDK, 스트리밍)
core/prompts.py        교수설계 원리 시스템 프롬프트
pyproject.toml         의존성
setup.bat / run.bat    Windows 설치·실행 (setup.sh / run.sh: mac·linux)
```

## 보안

- API 키는 `data/user_settings.json` 과 `.env` 에만 저장되며 **`.gitignore` 로 커밋에서 제외**됩니다.
- `_context/`(사내 자료·원본·마이그레이션 킷)도 커밋 대상이 아닙니다.

## 설정 우선순위

`data/user_settings.json`(사이드바 저장값) > `.env` 환경변수 > 코드 기본값

## 문의

fedu@ubion.co.kr
