# 워크플로우 제약 작업용 운영 모델

- `env.sh`는 격리 attached 테스트 환경의 셸 로컬 activation boundary다.
- `source <env.sh>`를 한 현재 셸에서만 `BTWIN_CONFIG_PATH`, `BTWIN_DATA_DIR`, `BTWIN_API_URL`가 격리 환경으로 바뀐다.
- 같은 셸에서 `btwin hud`, `btwin runtime current --json` 같은 plain `btwin` 명령은 그 격리 환경을 사용한다.
- `source env.sh`를 하지 않은 새 셸은 계속 글로벌 `~/.btwin` 기본 환경을 사용한다.
- 이 문서는 HUD 스펙 변경이나 자동 감지를 전제로 하지 않는다. 활성화는 항상 명시적 `source env.sh`로만 한다.
