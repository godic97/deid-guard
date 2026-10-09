# deid-guard

Claude Code 플러그인(mod). 표 형식 데이터 파일(CSV·TSV·XLSX·JSON·JSONL)의 개인정보를 **모델에 보내기 전에 로컬에서 비식별화**한다. 한국어·영어 지원.

> A Claude Code mod that de-identifies personal data in tabular files locally, before anything reaches the model. Korean and English. Column-level decisions are made by Claude from column names and shapes only, confirmed by the user, and applied by a local Python engine.

## 동작 방식

```
Claude ──Read data.csv──▶ mod ──guard──▶ 엔진(Python, 로컬)
   ◀── 프로파일 카드(컬럼명·타입·형식 패턴, 값 없음)
Claude ──AskUserQuestion──▶ 사용자: "patient_no, 방문일도 비식별화할까요?"
Claude ──mcp__deid-guard__apply──▶ 엔진: .deid/out/ 에 비식별 사본 생성
Claude ──Read data.csv──▶ 비식별 사본
모든 대화 행(프롬프트·도구 결과)──session.append──▶ 엔진 scrub ──▶ 모델
```

1. **처음 읽을 때**: 원문 대신 프로파일 카드를 돌려준다. 컬럼명, 타입, 고유값 비율, 형식 패턴(`A-######`), 자동 탐지 결과만 담는다. 셀 값은 없다.
2. **판단과 확인**: Claude가 컬럼명을 보고 식별자(환자번호, 사번, 회원ID 등)를 추론하고 `AskUserQuestion`으로 처리 방식을 묻는다.
3. **적용**: `mcp__deid-guard__apply`가 `.deid/out/`에 비식별 사본을 만든다. 이후 원본을 읽으면 사본이 나온다.
4. **안전망**: 모델이 읽는 모든 행(프롬프트, Read·Bash·Grep 결과, 첨부)을 엔진이 다시 검사한다. 알려진 원값은 같은 가명으로, 탐지 패턴은 토큰으로 바뀐다.

### 처리 방식

| 구분 | 예 | 기본 | 결정 전 |
|---|---|---|---|
| 확정 개인정보 (강제) | 주민·외국인등록번호, 전화, 이메일, 카드(Luhn), 운전면허, 여권·계좌(문맥), SSN | 가명화. 해제 불가 | 가림 |
| 직접 식별자 | 이름, 환자번호·ID류 | 가명화 `PATIENT_000123` | 가림 |
| 강한 준식별자 | 생년월일, 주소 | 범주화 (출생연도, 시·군·구) | 범주화된 값으로 가림 |
| 준식별자 | 우편번호, 나이, 성별, 날짜 | 우편번호만 앞 3자리, 나머지 유지 | 그대로 |
| 자유텍스트 | 메모, 소견 | 유지 + 셀 안 탐지·가명 치환 | 탐지만 |

- 가명은 일관된다. 같은 값은 파일이 달라도 같은 토큰이 되므로 조인과 집계가 유지된다.
- 모든 상태는 프로젝트의 `.deid/`(자동 gitignore)에 있다. 모델은 `.deid/out`, `.deid/cards` 외에는 접근할 수 없다.
- 헤더 없는 파일(첫 행이 데이터)이나 헤더에 사람 이름이 있는 피벗 표는 헤더를 `col_N`으로 가린다.

## 요구 사항

- Claude Code **2.1.290 이상** (mods 사용, `prompt.mention`). 2.1.295에서 테스트했다.
- Python **3.9 이상**. 표준 라이브러리만 쓰므로 pip 설치가 필요 없다.
- 탐지와 변환은 모두 로컬에서 실행된다. 외부 API나 LLM을 호출하지 않는다.

## 설치

```bash
git clone git@github.com:godic97/deid-guard.git
claude --plugin-dir /path/to/deid-guard
```

항상 켜 두려면 `~/.claude/settings.json`에 다음을 넣는다.

```json
{ "env": { "CLAUDE_CODE_PLUGIN_DIRS": "/path/to/deid-guard" } }
```

Python 경로는 `/config`의 `deid-guard.python` 항목(기본 `python3`)으로 바꿀 수 있다.

## 명령

- `/deid-status`: 보호 중인 데이터 파일과 사본 경로를 보여준다.
- `/deid-reveal PATIENT_NO_000012`: 원값을 **로컬 토스트로만** 보여준다. 모델에는 가지 않는다.

## 한계 (v0.1)

- **비정형 문서(txt, docx, pdf) 본문은 다루지 않는다.** 표 안의 자유텍스트 셀은 패턴 탐지와 이미 알려진 값으로만 처리한다. 데이터 파일에 없는 사람 이름을 프롬프트에 직접 쓰면 그대로 전달된다.
- 이미지와 PDF를 붙여넣거나 네이티브로 읽는 경우는 막지 않는다.
- 한 Bash 명령 안에서 새로 만든 데이터 파일을 곧바로 출력하면, 그 파일의 이름·ID 값은 패턴 탐지로만 걸러진다. 다음 명령부터는 프로파일된다.
- 로컬 대화 기록 파일(`~/.claude/projects/...jsonl`)에는 화면 표시용 필드(`toolUseResult`)와 입력 대기열 기록(`queue-operation`)에 원값이 남는다. 이 필드는 모델 요청에 실리지 않는다.
- 엔진(Python)을 실행할 수 없으면 데이터 파일 읽기를 거부하지만, 대화 행 검사는 하지 못한다. 상태 줄에 `ENGINE UNAVAILABLE`이 표시된다.
- mods는 Claude Code의 early access API라 버전에 따라 바뀔 수 있다.
- 이 도구는 보조 방어 수단이다. 개인정보보호법 등 규제 준수를 보장하지 않는다.

## 개발

```bash
cd engine && python3 -m unittest discover -s tests -t .   # 엔진 테스트
claude plugin test .                                        # mod 테스트
claude plugin validate .                                    # 정적 검사
```

테스트 데이터는 모두 합성 값이다(`engine/tests/fixtures.py`).
