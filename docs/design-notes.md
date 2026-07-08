# 설계 노트

## 프롬프트 관리 (skills/)
- 모든 시스템 프롬프트는 [skills/](../skills/) 의 `SKILL.md` 로 관리한다. 코드([core/prompts.py](../core/prompts.py))는
  이 파일들을 읽어 조합만 한다. 프롬프트를 바꾸려면 해당 `SKILL.md` 를 편집하고 앱을 재시작한다.
- 편집 지점: 강의계획서=`skills/syllabus`, 교재=`skills/textbook`, PPT=`skills/slides`,
  점검=`skills/check-syllabus`·`skills/check-script`, 공통=`skills/gyosu-base`.

## 다중 LLM 대비
- `gyosu-base` 의 **[출력 규칙 — 모든 모델 공통 엄수]** 로 섹션·순서·표 컬럼을 고정해, Claude 뿐 아니라
  DeepSeek·GPT 등에서도 동일한 구조가 나오도록 한다. 모델은 사이드바 연결 설정에서 선택.

## 강의계획서 = 실무형(전문가 첨삭 반영)
- 표 중심의 간결한 실무형. 백워드설계·핵심이해·커리큘럼맵·인지수준 태그·과도한 이론 근거는 제거.
- 상세 규칙과 반영 이력은 [첨삭-로그.md](첨삭-로그.md) 및 각 `SKILL.md` 의 "첨삭 반영 규칙(누적)" 참조.

## 데이터/보안
- 산출물은 SQLite(`data/app.db`)에 프로젝트 단위로 저장(로컬, 커밋 제외).
- 회사 PPT 양식(`assets/company_template.pptx`), 전문가 원본 첨삭(`_guideline/`), 키(`.env`·`data/user_settings.json`)는 커밋 제외.
