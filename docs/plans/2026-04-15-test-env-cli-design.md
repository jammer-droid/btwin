# test-env CLI Design

작성일: 2026-04-15
대상 프로젝트: `btwin-runtime`
성격: 로컬 설계 메모

## 목적

격리 테스트 환경 사용 절차를 사용자 입장에서 `btwin test-env up` / `btwin test-env hud` 수준으로 줄인다.

이번 설계의 핵심은 다음 두 가지다.

1. 사용자는 더 이상 `env.sh`, `BTWIN_BIN`, 임시 helper 이름을 외우지 않아도 된다.
2. 대표 테스트 시나리오는 반드시 “사용자가 Codex에게 지시하고, Codex가 btwin을 사용해서 작업하는 흐름”으로 지원되어야 한다.

즉 단순히 격리 `serve-api`를 띄우는 도구가 아니라, Codex가 실제로 들어가서 `btwin`을 쓰는 test workspace 진입점을 만들어야 한다.

## 비목표

이번 작업에서 하지 않는 것:

- 글로벌 기본 runtime model 변경
- `btwin hud`의 결과 형식 변경
- 자동 샘플 thread / agent 생성
- 직접 HTTP로 `serve-api`를 찌르는 flow를 대표 사용자 시나리오로 문서화
- 현재 repo의 `AGENTS.md`를 덮어쓰기

## 현재 문제

지금까지의 helper 흐름은 기능적으로는 되지만, 사용자 시나리오 기준으로는 너무 길다.

현재 필요한 기억 항목:

- `BTWIN_BIN="$PWD/.venv/bin/btwin"` 여부
- bootstrap script 인자
- `source env.sh`
- `btwin_test_up`
- `btwin_test_hud`
- 필요하면 project root 이동
- Codex를 어느 위치에서 띄워야 하는지

또한 Codex가 “지금 이 workspace는 test env다”라는 문맥을 자연스럽게 알 수 있는 구조가 부족하다.

## 고려한 접근

### 1. `btwin test-env`를 공식 CLI surface로 추가

예:

```bash
btwin test-env up
btwin test-env hud
btwin test-env down
```

장점:

- 글로벌 사용과 이름부터 분리된다.
- 사용자 표면이 가장 짧다.
- 기존 bootstrap/env helper를 내부 구현으로 재사용할 수 있다.
- 향후 Codex test workspace 준비까지 한 명령 아래 묶기 쉽다.

단점:

- CLI surface가 조금 넓어진다.

### 2. 기존 `env.sh` helper만 유지

예:

```bash
source .btwin-attached-test/env.sh
btwin_test_up
btwin_test_hud
```

장점:

- 이미 어느 정도 구현되어 있다.

단점:

- 여전히 bootstrap/script/env.sh 개념을 알아야 한다.
- 사용자 표면으로는 너무 implementation-shaped 하다.

### 3. `btwin hud --test-env` 같은 플래그 확장

예:

```bash
btwin hud --test-env
```

장점:

- 명령 수는 적다.

단점:

- 글로벌 HUD와 test HUD의 경계가 흐려진다.
- test env 준비와 HUD 실행의 책임이 섞인다.

## 선택한 접근

1번을 채택한다.

즉 외부 사용자 표면은 `btwin test-env ...`로 올리고, 기존 `bootstrap_isolated_attached_env.sh`와 `env.sh` helper는 내부 재사용 가능 자산으로 내려둔다.

## 제안 CLI Surface

### `btwin test-env up`

역할:

- 격리 test env root 준비
- 격리 `BTWIN_CONFIG_PATH`, `BTWIN_DATA_DIR`, `BTWIN_API_URL` 준비
- 격리 `serve-api` 시작 또는 재사용
- test project root 준비
- test project root의 local `.codex/config.toml` 준비
- test project root 안에 test-env 전용 `AGENTS.md` 생성

기본 동작 원칙:

- 현재 worktree의 `.venv/bin/btwin`이 있으면 우선 사용
- 없으면 `PATH`의 `btwin`으로 fallback
- 글로벌 `serve-api`와 `~/.btwin`는 건드리지 않음

### `btwin test-env hud`

역할:

- 같은 test env resolution을 사용
- 필요 시 `up`와 동일한 env metadata를 읽는다
- 최종 결과는 `btwin hud`와 동일한 interactive HUD를, test env를 대상으로 연다

중요:

- 자동 샘플 thread 생성은 하지 않는다
- 사용자가 기대하는 결과는 “`btwin hud`와 같은 interactive HUD”다

### `btwin test-env status`

역할:

- 현재 test env root
- API URL
- 소유 PID
- health
- test project root
- active binding 정도를 사람이 읽기 좋게 표시

### `btwin test-env down`

역할:

- 그 test env가 소유한 `serve-api`만 종료
- 글로벌 `serve-api`는 종료하지 않음

## Codex-Aware Test Workspace

이번 설계에서 중요한 부분은 Codex가 test env라는 사실을 workspace 차원에서 알 수 있게 하는 것이다.

방법:

- `btwin test-env up`가 test project root를 만든다
- 그 root 안에 local `.codex/config.toml`을 둔다
- 그 root 안에 test-env 전용 `AGENTS.md`를 생성한다

이 `AGENTS.md`는 현재 repo의 `AGENTS.md`를 대체하는 것이 아니라, test project root 전용 지시 문서다.

이 문서에는 최소한 아래를 적는다.

- 이 workspace는 `btwin` 격리 테스트 환경이다
- global `~/.btwin` 기준으로 판단하지 말 것
- thread / agent / workflow 작업은 이 test env 기준으로 수행할 것
- 필요하면 `btwin test-env status`로 현재 env를 확인할 것

이렇게 하면 사용자가 test project root에서 `codex`를 실행한 뒤:

- “thread 하나 만들어줘”
- “btwin으로 workflow 테스트해줘”

같이 지시했을 때, Codex는 처음부터 test env 문맥으로 행동할 수 있다.

## 사용자 시나리오 기준

대표 사용자 시나리오는 아래여야 한다.

1. 사용자가 `btwin test-env up`
2. 사용자가 test project root에서 `codex` 실행
3. 사용자가 Codex에게 thread 생성 또는 workflow 작업 지시
4. Codex가 local MCP config를 통해 `btwin mcp-proxy` 사용
5. `mcp-proxy`는 격리 `serve-api`를 사용
6. 사용자는 `btwin test-env hud`로 같은 env를 관측

즉 “직접 server api에 HTTP 요청”은 내부 smoke나 low-level 검증에만 허용되고, 대표 테스트 절차가 되어서는 안 된다.

## 기존 env.sh helper 처리

현재 있는 아래 요소:

- `scripts/bootstrap_isolated_attached_env.sh`
- generated `env.sh`
- `btwin_test_up`
- `btwin_test_hud`
- `btwin_test_status`
- `btwin_test_down`

는 즉시 삭제할 필요는 없다.

추천 처리:

- 내부 구현 재사용은 허용
- README와 사용자 추천 경로에서는 `btwin test-env ...`를 우선
- helper는 점차 내부 메커니즘 또는 fallback path로 내린다

즉 “필요 없으면 정리”의 의미를 바로 삭제로 해석하지 않고, 외부 surface에서 우선순위를 내리는 방향으로 간다.

## 구현 경계

주요 변경 대상:

- `packages/btwin-cli/src/btwin_cli/main.py`
- 필요한 경우 test-env 전용 helper module
- `tests/`의 CLI command tests
- README의 isolated testing section

가급적 변경하지 않을 대상:

- 기존 global runtime resolution semantics
- HUD rendering semantics 자체
- Codex provider model

## 검증 기준

최소 검증은 아래를 만족해야 한다.

1. `btwin test-env up`만으로 격리 `serve-api`와 test project root가 준비된다.
2. `btwin test-env hud`는 test env를 대상으로 interactive HUD를 연다.
3. global `serve-api`와 `~/.btwin`는 영향받지 않는다.
4. test project root에는 local `.codex/config.toml`과 test-env 전용 `AGENTS.md`가 생성된다.
5. 사용자가 test project root에서 `codex`를 띄우면, Codex는 자신이 test env 안에 있다는 문맥을 갖는다.
6. 대표 smoke는 “사용자 -> Codex -> btwin” 흐름을 기준으로 작성된다.

## 성공 기준

사용자는 최소한 아래 정도만 기억하면 된다.

```bash
btwin test-env up
btwin test-env hud
```

그리고 더 깊은 테스트를 할 때도:

- test project root에서 `codex`를 실행하고
- Codex가 `btwin`을 써서 thread/workflow를 조작하며
- `btwin test-env hud`가 그 same env를 보여주는

일관된 사용자 시나리오가 성립해야 한다.
