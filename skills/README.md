# skills — 교수설계 프롬프트(스킬)

각 `<name>/SKILL.md` 는 LLM 시스템 프롬프트로 그대로 사용됩니다. YAML frontmatter(`name`/`description`)는
로더가 벗겨내고 **본문만** 프롬프트로 씁니다. 프롬프트는 코드가 아니라 이 파일들에서 관리합니다.

| 스킬 | 용도 | 조합 |
|---|---|---|
| `gyosu-base` | 공통 교수설계 원리 + 모든 LLM 공통 출력 규칙 | 모든 과업의 앞에 붙음 |
| `syllabus` | 강의계획서(실무형) | base + syllabus |
| `textbook` | 학생용 교재 | base + textbook |
| `slides` | PPT 개요 | base + slides |
| `check-syllabus` | 강의계획서 점검 | base + check-syllabus |
| `check-script` | 원고(교재/PPT) 점검 | base + check-script |

로더: [core/prompts.py](../core/prompts.py) 가 위 파일을 읽어 `SYS_BASE`, `SYS_SYLLABUS`, `SYS_SCRIPT_DOC`,
`SYS_SCRIPT_PPT`, `SYS_CHECK_SYL`, `SYS_CHECK_SCR` 로 조합합니다. **파일을 편집한 뒤 앱을 재시작**하면 반영됩니다.

## 매일 첨삭(전문가 피드백) 반영 방법
전문가 첨삭이 오면:
1. 원본 파일은 `_guideline/`(커밋 제외)에 날짜별로 보관.
2. 해당 문서 스킬(예: 강의계획서 → `syllabus/SKILL.md`)의 본문을 수정하거나, 맨 끝
   **`## 첨삭 반영 규칙 (누적, 날짜순)`** 에 `- (YYYY-MM-DD, 문서) 규칙…` 한 줄을 추가.
3. `docs/첨삭-로그.md` 에 날짜·문서·요지를 기록.
4. 앱 재시작(run.bat) → 즉시 반영. **코드 수정 불필요.**
