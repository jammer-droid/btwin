# Isolated Attached Env Activation Design

작성일: 2026-04-15
대상 프로젝트: `btwin-runtime`
성격: 로컬 설계 메모

## 목적

격리 attached 테스트 환경을 반복해서 검증할 때, 사용자가 매번 `BTWIN_*` 환경변수를 다시 export하지 않아도 되게 만든다.

핵심 목표는 다음과 같다.

- `btwin hud`와 다른 `btwin` 명령을 테스트 셸에서 바로 실행할 수 있게 한다.
- 글로벌 운영 환경(`~/.btwin`, global service, shell rc)은 건드리지 않는다.
- worktree 기반 검증에서 같은 패턴을 반복해서 쓸 수 있게 한다.

## 문제 정의

현재 `scripts/bootstrap_isolated_attached_env.sh`는 `env.sh`를 생성하고, 사용자는 그 파일을 source한 뒤 테스트를 진행할 수 있다.

하지만 실제 operator workflow에서는 아래 마찰이 남아 있다.

- source 이후에도 어떤 `btwin` 바이너리와 어떤 runtime store를 보고 있는지 사용자가 다시 신경 써야 한다.
- HUD, `runtime current`, `thread watch` 같은 관측 명령을 열 때 셸이 정말 격리 환경을 보고 있는지 확신하기 어렵다.
- worktree를 바꿔가며 테스트할 때 같은 준비 절차를 매번 머리로 다시 맞춰야 한다.

이번 작업은 HUD 스펙을 바꾸는 것이 아니라, 격리 테스트 셸의 activation semantics를 더 명확히 만드는 것이다.

## 비목표

이번 작업에서 하지 않는 것:

- `btwin hud`의 출력 스펙 변경
- repo/worktree 자동 감지로 테스트 환경을 암묵적으로 선택하는 동작
- global shell profile(`~/.zshrc`, `~/.bashrc`) 수정
- global `~/.btwin` 서비스 또는 launchd 설정 변경
- production runtime selection 규칙 자체 변경

## 고려한 접근

### 1. `env.sh`를 셸-로컬 activation entrypoint로 강화

`bootstrap_isolated_attached_env.sh`가 생성한 `env.sh`를 source하면, 현재 셸에서만 격리 환경이 활성화되도록 한다.

포함 범위:

- `BTWIN_CONFIG_PATH`
- `BTWIN_DATA_DIR`
- `BTWIN_API_URL`
- 필요 시 현재 worktree의 `.venv/bin`을 우선하는 `PATH`

장점:

- 글로벌 오염이 없다.
- 사용자가 source 여부만 기억하면 된다.
- `btwin hud` 같은 기존 명령을 그대로 쓸 수 있다.
- worktree별로 같은 사용 패턴을 유지하기 쉽다.

단점:

- activation이 셸 단위이므로 새 터미널에서는 다시 source해야 한다.

### 2. wrapper launcher 추가

예를 들어 `ROOT_DIR/btwin-env hud` 같은 wrapper를 생성해서, source 없이도 격리 환경으로 명령을 실행하게 하는 방식이다.

장점:

- source를 잊어도 실행 가능하다.

단점:

- 매번 wrapper를 붙여야 해서 operator UX가 덜 자연스럽다.
- 결국 사용자가 일반 `btwin`과 wrapper를 구분해야 한다.

### 3. repo/worktree 기반 자동 추론

현재 디렉터리나 worktree 메타데이터를 보고 `btwin`이 자동으로 테스트 환경을 선택하게 하는 방식이다.

장점:

- 겉보기엔 가장 편하다.

단점:

- 왜 지금 글로벌이 아니라 테스트 store를 보고 있는지 불투명해진다.
- 운영 환경과 테스트 환경의 경계가 흐려진다.
- 현재 런타임 모델의 명시적 환경 변수 우선순위와도 잘 맞지 않는다.

## 선택한 접근

이번 작업은 1번을 채택한다.

즉:

- 기본 상태에서는 기존처럼 글로벌 환경이 기본이다.
- `source <generated env.sh>`를 한 현재 셸에서만 격리 테스트 환경이 활성화된다.
- 그 셸에서는 사용자가 추가 export 없이 바로 `btwin hud`, `btwin runtime current`, `btwin thread watch`, `btwin contribution submit` 등을 실행할 수 있어야 한다.

이 접근은 현재 `BTWIN_*` 우선순위 모델과 가장 잘 맞고, HUD나 runtime 명령의 의미를 바꾸지 않는다.

## 제안 동작

### Activation semantics

`env.sh`는 현재 셸에서만 아래 상태를 보장해야 한다.

- `BTWIN_CONFIG_PATH`가 bootstrap root 안의 attached config를 가리킨다.
- `BTWIN_DATA_DIR`가 bootstrap root 안의 isolated data dir를 가리킨다.
- `BTWIN_API_URL`이 isolated `serve-api`를 가리킨다.
- `PATH`는 필요 시 현재 worktree의 `.venv/bin`을 앞쪽에 두어, 같은 셸에서 `btwin`이 구현 중인 브랜치의 바이너리를 우선 사용하게 한다.

중요한 점:

- 이 activation은 현재 셸에만 적용된다.
- source하지 않은 다른 셸은 계속 글로벌 기본값을 사용한다.

### Operator UX

권장 흐름은 아래처럼 단순해야 한다.

```bash
scripts/bootstrap_isolated_attached_env.sh start --skip-server
source .btwin-attached-test/env.sh
btwin serve-api --port 8788
```

다른 셸에서도 같은 `env.sh`만 source하면 바로 아래 명령이 가능해야 한다.

```bash
source .btwin-attached-test/env.sh
btwin hud
btwin runtime current --json
btwin thread watch <thread_id> --follow
```

즉 사용자는 더 이상 명령마다 환경변수를 앞에 붙이지 않는다.

### Scope separation

이 설계는 테스트 셸 activation에만 관여한다.

영향을 주지 않아야 하는 범위:

- `~/.btwin`
- global launchd service
- 사용자의 기본 PATH
- shell profile
- source하지 않은 셸의 `btwin` 실행 결과

## 구현 경계

구현은 bootstrap helper 쪽에 국한하는 것이 맞다.

주요 변경 후보:

- `scripts/bootstrap_isolated_attached_env.sh`
- 관련 테스트
- README / local operator 문서의 검증 절차

기본 원칙:

- `btwin` core runtime resolution을 복잡하게 바꾸지 않는다.
- 환경 선택은 여전히 명시적 environment activation으로 처리한다.

## 오류 처리 원칙

- `env.sh`는 side effect 없이 source 가능해야 한다.
- referenced `.venv/bin`이 없더라도 최소한 `BTWIN_*` 변수 activation은 동작해야 한다.
- bootstrap root가 삭제됐거나 API가 내려가 있으면, `btwin hud` 자체를 바꾸기보다 기존 command diagnostics가 그대로 드러나게 한다.

즉 activation layer는 최대한 얇고, runtime truth는 기존 명령이 설명하게 둔다.

## 검증 기준

최소 검증은 아래 시나리오를 만족해야 한다.

1. source하지 않은 셸에서는 `btwin hud`가 글로벌 기본 환경을 본다.
2. source한 셸에서는 같은 `btwin hud`가 isolated attached env를 본다.
3. 같은 방식으로 `btwin runtime current --json`과 `btwin thread watch`도 isolated env를 본다.
4. source/unset이 global shell state나 `~/.btwin`을 바꾸지 않는다.
5. worktree를 바꿔도 각 worktree가 자기 bootstrap root와 env file을 가질 수 있다.

권장 smoke는 isolated attached 환경에서 실제로 다음을 확인하는 것이다.

- `source env.sh`
- `btwin serve-api`
- `btwin hud`
- `btwin runtime current --json`
- workflow constraints용 `thread watch` / `workflow hook` 흐름

## 성공 기준

- 테스트 operator는 `source env.sh` 이후 바로 `btwin hud`를 실행할 수 있다.
- 사용자에게 “이 셸은 테스트용, 저 셸은 글로벌”이라는 경계가 명확하다.
- 글로벌 runtime 설정은 그대로 보존된다.
- worktree 기반 검증 루틴으로 반복 사용 가능하다.
