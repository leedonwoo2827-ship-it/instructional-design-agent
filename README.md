# 교수설계 가이드 에이전트

강의 정보를 입력하면 **강의계획서 → 교재 · PPT 개요**를 생성하고, 실무 기준에 맞춰
**정렬 점검·직접 편집**까지 할 수 있는 Streamlit 앱입니다. 산출물은 프로젝트(강의) 단위로 자동 저장됩니다.

사내 **Ubion LiteLLM 프록시**(OpenAI 호환)를 사용하며, URL·API 키·모델은 **좌측 사이드바 › 연결 설정**에서 입력합니다.

## 요구사항
- Python 3.10 ~ 3.12
- 사내망 접속 (LiteLLM 프록시 `192.168.50.119:4000` 도달 가능)
- 본인 LiteLLM virtual key (`sk-...`) — 사내 대시보드 `/ui/` 에서 발급

## 설치 & 실행 (Windows)
1. **`setup.bat`** 더블클릭 — `.venv` 생성, 의존성 설치, `.env` 준비
2. **`run.bat`** 더블클릭 — 브라우저에서 `http://localhost:8701` 열림
3. 좌측 사이드바 **연결 설정**에 `sk-` 키 입력 → **저장**
4. 사이드바 **강의 기본 정보** 입력 → **강의계획서 생성** → **STEP 2** 점검 → **STEP 3** 교재·PPT 생성

macOS / Linux: `./setup.sh` 후 `./run.sh`.

## 화면 구성
- **좌측 사이드바**: 강의 프로젝트(새로 만들기/열기/이름변경/삭제) · **강의 기본 정보**(입력 폼) · **연결 설정**(URL·키·모델)
- **메인**: 상단 STEP 바(1·2·3) + 산출물 패널

## 사용 흐름
| 단계 | 내용 |
|---|---|
| **STEP 1 · 강의 정보** | 사이드바에 과목·대상·주차·방식·주제 입력 → **강의계획서** 생성(스트리밍) |
| **STEP 2 · 강의계획서** | 실무형 강의계획서 확인·수정·**직접 편집**. 아래 두 버튼: |
| | ① **정렬 점검 실행** — 생성물이 실무형 기준(섹션 구성·학습목표 3~4개·평가/출석 규정·주교재 서지·주별·과제)을 지키는지 **AI가 감사**해 ✅/⚠️/❌ 수정 제안을 문서 하단에 덧붙임 *(맞춤법 교정 아님)* |
| | ② **산출물 작성으로 이동** — STEP 3로 이동. 점검을 먼저 하도록 ①이 강조되고, 점검 후 ②가 강조됨 |
| **STEP 3 · 원고** | 주차 선택 → **교재 + PPT 개요 동시 생성**. 탭별 확인·수정·점검·저장 |

- 각 산출물은 `.md` / `.doc`(한글·워드) 저장, PPT 개요는 **`.pptx`** 저장(회사 양식 상속) 지원.
- 강의계획서·교재·PPT 모두 **직접 편집(마크다운)** 가능 — 편집본 기준으로 이후 AI 수정이 이어집니다.

## 모델
사이드바 연결 설정에서 선택 (기본 **`deepseek-v4-flash-think`**):
- DeepSeek V4 — `deepseek-v4-flash` / `deepseek-v4-flash-think`(추론) / `deepseek-v4-pro`
- Claude — `claude-sonnet-4-6`(균형) / `claude-opus-4-7`(고품질) / `claude-haiku-4-5`(빠름)

> 추론(-think) 모델은 아주 긴 산출물(교재 등)에서 출력이 잘릴 수 있습니다. 그럴 땐 Claude로 바꾸거나 토큰 상향을 고려하세요.

## 프롬프트 관리 (skills/)
프롬프트는 코드가 아니라 [skills/](skills/) 의 `SKILL.md` 로 관리합니다. 첨삭이 오면 해당 스킬 파일을
편집하고 앱을 재시작하면 반영됩니다. 자세한 내용은 [skills/README.md](skills/README.md), [docs/첨삭-로그.md](docs/첨삭-로그.md).

## 구조
```
app.py                 Streamlit UI (사이드바 입력 · STEP 1·2·3)
core/prompts.py        skills/*.md 로더 → 시스템 프롬프트 조합
core/llm.py            LiteLLM 프록시 호출 (openai SDK, 스트리밍)
core/user_settings.py  설정 저장/로드 (data/user_settings.json, .env 기본값)
core/db.py             프로젝트(강의) 저장 SQLite (data/app.db)
core/viz.py            라인 아이콘 + Bloom 인지수준 SVG 차트
core/pptx_export.py    PPT 개요 → .pptx (회사 양식 상속)
skills/                교수설계 프롬프트(SKILL.md) — 첨삭 반영 지점
assets/company_template.pptx  회사 PPT 양식(선택, 로컬)
setup.bat / run.bat    Windows 설치·실행 (setup.sh / run.sh: mac·linux)
```

## 보안
- API 키·프로젝트 DB·회사 양식·원본 첨삭 자료는 **`.gitignore` 로 커밋에서 제외**됩니다
  (`.env`, `data/user_settings.json`, `data/app.db`, `assets/*.pptx`, `_context/`, `_assets/`, `_guideline/`).

## 설정 우선순위
`data/user_settings.json`(사이드바 저장값) > `.env` 환경변수 > 코드 기본값

## 문의
fedu@ubion.co.kr
