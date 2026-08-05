# kis-trader

`@younggichoi/kis-trader` 는 한국투자증권(KIS) OpenAPI 로 국내·해외 주식을 자동
매매하는 **로컬 실행형 엔진**과, 그 엔진을 설치·진단·운영하는 macOS CLI 다. 매매
판단은 로컬에 설치된 LLM CLI(claude / codex / pi / gemini) 가 내리고, 주문 집행과
가드레일 clamp 는 파이썬 엔진(`src/`)이 담당하며, 스케줄 실행은 launchd
LaunchAgent 로 이루어진다. 서버도 컨테이너도 쓰지 않고 사용자 맥에서만 돈다.

> ⚠️ **이 소프트웨어는 실제 증권 계좌에 주문을 넣습니다.** 아래
> [Risk notice](#risk-notice) 를 반드시 먼저 읽으세요.

---

## The signal dependency — 먼저 읽을 것

**이 패키지는 매매 신호를 스스로 만들지 않는다.**

매매 후보 종목은 **별도 프로젝트인 `stock-signal-bot`** 이 생성해 JSON 파일로
디렉토리에 떨궈 놓은 것을 읽어서 쓴다. 그 디렉토리 경로는 `init` 이 물어보고
설정에 `signalDir` 로 저장되며, launchd 잡에는 `KIS_TRADER_SIGNAL_DIR` 환경변수로
주입된다. `init` 의 기본 제안값은 `~/stock-signal-bot/data/signals` 다.

`stock-signal-bot` 을 설치·운영하지 않으면:

- `init` 은 정상적으로 끝난다 (디렉토리가 없으면 경고만 하고 넘어간다).
- 엔진과 launchd 잡도 정상적으로 실행된다.
- 그러나 **읽을 신호가 없으므로 아무것도 매매하지 않는다.**
- `kis-trader doctor` 의 `signal-dir` 항목이 `fail`(디렉토리 없음) 또는
  `warn`(비어 있음 / 72시간 이상 갱신 없음) 으로 보고한다.

즉 이 CLI 만 설치한 상태는 "매매하는 봇"이 아니라 "신호를 기다리는 빈 엔진"이다.
신호 생산자를 먼저 준비할 것.

---

## Prerequisites

| 항목 | 요구사항 |
|------|----------|
| OS | **macOS 전용** (`launchd`, macOS Keychain, `security` 에 의존) |
| Node.js | **>= 20** |
| Python | **3.11 ~ 3.13** — `init` 이 이 범위의 인터프리터를 자동 탐색한다 |
| 증권 계좌 | **KIS OpenAPI 계정** (모의투자 `paper` 또는 실계좌 `real`) — app key / app secret / 10자리 계좌번호 |
| LLM CLI | `claude`, `codex`, `pi`, `gemini` **중 최소 1개**가 PATH 또는 표준 설치 경로에 있어야 한다. 하나도 없으면 `init` 이 4단계에서 중단된다 |
| Telegram | **선택** — 봇 토큰 + chat id 를 넣으면 알림이 켜지고, 건너뛰면 알림만 꺼진 채 나머지는 그대로 동작한다 |
| 신호 생산자 | **`stock-signal-bot`** — 위 [The signal dependency](#the-signal-dependency--먼저-읽을-것) 참조 |

Python 탐색 순서는 python.org 프레임워크 빌드 → Homebrew → `/usr/local` 순이고,
그다음 로그인 셸의 `python3.13` … `python3` 를 본다. 3.11~3.13 을 보고하는 인터프리터가
하나도 없으면 `init` 이 5단계에서 중단된다.

---

## Install

```bash
npm i -g @younggichoi/kis-trader
kis-trader init
```

전역 설치 없이 한 번만 돌려보려면:

```bash
npx @younggichoi/kis-trader init
```

`init` 은 7단계 대화형 온보딩이다.

| 단계 | 내용 |
|------|------|
| 1/7 | 매매 모드 선택 (`paper` 기본 / `real` 은 추가 확인) |
| 2/7 | KIS app key · app secret · 10자리 계좌번호 → **로그인 키체인에 저장** |
| 3/7 | Telegram 봇 토큰 + chat id (선택, 건너뛰기 가능) |
| 4/7 | 설치된 LLM CLI 탐지 후 기본 에이전트 선택 |
| 5/7 | Python 인터프리터 탐지 + 신호 디렉토리 입력 |
| 6/7 | 설정 저장 → venv 생성 → pip 업그레이드 → 의존성 설치 → SQLite 마이그레이션 |
| 7/7 | 잡별로 launchd LaunchAgent 설치 여부를 묻고 등록 |

비밀값은 프롬프트에서 화면에 표시되지 않고, `config.json` 에도 절대 기록되지 않는다.
키체인에만 들어간다. 설정이 스키마 검증에 실패하면 **아무것도 저장하지 않고** 중단한다.

설치가 끝나면 바로:

```bash
kis-trader doctor
```

---

## Commands

| 명령 | 설명 |
|------|------|
| `init` | Interactive setup: credentials, paths, launchd jobs |
| `doctor` | Diagnose the install (`--json` for machine output) |
| `start` | Run one job in the foreground: `start <job>` |
| `logs` | Tail a job's log: `logs [job] [--err]` |
| `install-jobs` | Install and load the launchd jobs enabled in config |
| `uninstall-jobs` | Unload and remove those launchd jobs |
| `status` | Show the launchd status of every job |
| `upgrade` | Reinstall the latest release, then refresh the jobs |
| `help` | Show this help |

인자 없이 실행하거나 `--help` / `-h` 를 주면 `help` 로 간다. 알 수 없는 명령은
종료 코드 2 로 사용법을 출력한다.

### Jobs

`start`, `logs`, `install-jobs` 가 다루는 잡은 다음 5개다.

| 잡 이름 | 실행 | 스케줄 | 로그 파일 |
|---------|------|--------|-----------|
| `orchestrator` | `src.orchestrator --carry-over` | 월~금 09:05 | `orchestrator.log` |
| `monitor` | `src.monitor` | **300초마다 (요일 무관)** | `monitor.log` |
| `reconciler` | `src.reconciler` | 월~금 16:00 | `reconciler.log` |
| `dipBuy` | `src.orchestrator --dip-only` | 월~금 15:00 | `dipBuy.log` |
| `usOrchestrator` | `src.orchestrator --asset-class overseas_stock` | 월~금 22:45 | `usOrchestrator.log` |

시각 지정 잡은 launchd `StartCalendarInterval` 로 월~금만 돌지만, `monitor` 는
`StartInterval` 방식이라 **주말·공휴일에도 300초마다 기동한다** (장이 닫혀 있으면
할 일이 없어 그대로 종료된다).

```bash
kis-trader status                # 잡별 launchd 상태
kis-trader logs                  # 기본값: orchestrator 로그를 tail -F
kis-trader logs monitor --err    # monitor 의 stderr 로그
kis-trader start reconciler      # 스케줄을 기다리지 않고 포그라운드 1회 실행
```

### Environment

| 변수 | 의미 |
|------|------|
| `KIS_TRADER_HOME` | `config.json` 과 `logs/` 가 놓이는 상태 디렉토리. **절대경로여야 한다.** 기본값 `~/.kis-trader` |
| `KIS_MODE` | `paper` \| `real`. launchd 잡과 `doctor` 가 설정값을 읽어 파이썬 엔진에 넘긴다. CLI 자체는 읽지 않는다 |

---

## Paper vs real

**기본값은 `paper`(모의투자)다.** `init` 1단계에서 `real` 을 고르면 한 번 더
확인을 묻고, 그 확인은 **빈 입력이 곧 거부**가 되도록 기본이 "아니오"로 잡혀 있다.
엔터만 눌러서 실계좌가 켜지는 경로는 없다.

`real` 로 설정한 순간부터 엔진은 **실제 자금으로 실제 주문을 낸다.** 모드는
키체인 계정명까지 분리되어 있어(`paper-appkey` vs `real-appkey`) 두 모드의 자격증명이
섞이지 않는다.

`real` 에서 손실을 제한하는 것은 오직 **`config/trading_rules.yaml` 의 가드레일**뿐이다.
LLM 의 판단은 이 값들로 clamp 되며, 여기에 들어 있는 것이 사실상 유일한 안전장치다.

- `guardrails` — 종목당 비중 상하한, 손절/익절 clamp 범위, 최대 보유일수,
  동시 보유 종목 수, 일일 신규 진입 수, 최소 신뢰도, 거래당 리스크 비율
- `kill_switch` — 일일/주간 손실률 한도, 연속 손절 횟수, LLM 파싱 실패율 한도
- `take_profit_partial` — 1차 익절 도달 시 부분 매도 + 본전 손절 상향

**`real` 전환 전에 이 파일을 직접 열어 값을 확인하고 자기 계좌 규모에 맞게 조정할 것.**
현재 커밋된 값은 특정 시드 규모를 전제로 조정된 "공격적" 프리셋이며, 그대로가
누구에게나 적절한 값이 아니다.

---

## Where things live

### 상태 디렉토리 — `~/.kis-trader/` (또는 `KIS_TRADER_HOME`)

| 경로 | 내용 |
|------|------|
| `~/.kis-trader/config.json` | 모드, 프로젝트 경로, 파이썬 경로, 신호 디렉토리, LLM 에이전트, 잡 on/off. **퍼미션 0600 으로 저장된다** |
| `~/.kis-trader/logs/` | 잡별 `<job>.log` (stdout) 와 `<job>.err.log` (stderr) |

`config.json` 에는 **비밀값이 하나도 들어 있지 않다.**

### macOS 로그인 키체인

| 서비스 | 계정 | 값 |
|--------|------|-----|
| `kis-openapi` | `paper-appkey` / `paper-secret` / `paper-account` | paper 모드 KIS 자격증명 |
| `kis-openapi` | `real-appkey` / `real-secret` / `real-account` | real 모드 KIS 자격증명 |
| `telegram-bot` | `stock-trader` | 봇 토큰 |
| `telegram-bot` | `stock-trader-chatid` | chat id |

키체인 쓰기는 비밀값을 `argv` 에 노출하지 않도록 `security -i` 의 stdin 경로로만
수행된다 (`ps` 로 다른 사용자가 읽어갈 수 없다).

### launchd

| 항목 | 값 |
|------|-----|
| 레이블 | `com.<username>.kistrader.<job>` — `<username>` 은 실행 시점 로그인 사용자 |
| plist 위치 | `~/Library/LaunchAgents/com.<username>.kistrader.<job>.plist` (0644) |
| 도메인 | `gui/$UID` |

plist 는 저장소에 커밋되어 있지 않고 **설치 시점에 사용자 설정으로 렌더링**된다.
잡에는 `PATH`, `HOME`, `KIS_TRADER_HOME`, `KIS_MODE`, `KIS_TRADER_SIGNAL_DIR` 가
plist 안에 명시적으로 선언되어 들어간다 (launchd 는 셸 rc 파일을 읽지 않는다).

### 패키지 설치 디렉토리

venv 와 SQLite DB 는 npm 이 설치한 패키지 루트 아래에 만들어진다.

| 경로 | 내용 |
|------|------|
| `<패키지 루트>/.venv/bin/python` | 엔진이 실제로 실행되는 인터프리터 |
| `<패키지 루트>/data/trades.sqlite` | 체결·포지션·PnL 영속 데이터 |

`upgrade` 는 `npm install -g @younggichoi/kis-trader@latest` 를 돌린 뒤,
plist 가 가리키는 경로가 낡지 않도록 **잡을 자동으로 다시 설치**한다.

---

## Risk notice

- 이 소프트웨어는 **실제 증권 계좌에 실제 주문을 넣는다.** `real` 모드에서
  발생하는 매수·매도·손실은 전부 사용자 본인의 것이다.
- 매매 판단의 상당 부분을 **LLM 이 내린다.** LLM 은 틀린다. 가드레일 clamp 와
  kill switch 는 손실 범위를 좁힐 뿐이며, 손실을 막아주지 않는다.
- 소프트웨어는 MIT 라이선스로 **어떠한 보증도 없이(AS IS)** 제공된다. 저자는
  이 소프트웨어의 사용으로 발생한 금전적 손실에 대해 어떠한 책임도 지지 않는다.
- **반드시 `paper` 모드에서 충분히 검증한 뒤** 실계좌를 고려할 것. 잃어도 되는
  금액을 넘겨 운용하지 말 것.
- 사용자는 자신이 거주·거래하는 관할의 법규와 KIS OpenAPI 이용약관을 준수할
  책임이 있다.

---

## Troubleshooting

### 먼저 `doctor`

무엇이 잘못됐든 첫 번째로 할 일은 이것이다.

```bash
kis-trader doctor
kis-trader doctor --json     # 기계 판독용 (배너 없이 JSON 만 출력)
```

`doctor` 는 설정 → 파이썬 → venv → DB → KIS 키체인 → Telegram 키체인 →
LLM CLI → 신호 디렉토리 → **KIS 실제 왕복 호출** → 잡 5개 순으로 점검하고,
통과하지 못한 항목마다 **바로 아래 줄에 해결 방법을 같이 출력한다.**
`fail` 이 하나라도 있으면 종료 코드 1, `warn` 만 있으면 0 이다.

설정을 읽지 못하면 그 자리에서 멈춘다 — 이후 점검이 전부 같은 원인으로
연쇄 실패하는 것을 막기 위해서다. 이 경우 `kis-trader init` 을 다시 돌리면 된다.

### 키체인이 잠겨 있음 (`-25308`)

```
macOS keychain is locked or this session cannot prompt (-25308).
Unlock the login keychain in a Terminal window and re-run.
```

`security` 명령이 잠긴 로그인 키체인에 접근할 때 나는 오류다. GUI 로만 답할 수 있는
자격증명 프롬프트를 띄울 수 없는 상황(SSH 세션, launchd 잡 등)에서 주로 발생한다.

**대처:**

1. **GUI 로 로그인한 맥에서 Terminal 창을 직접 열고** 다시 실행할 것.
   SSH 나 launchd 컨텍스트에서는 잠금 해제 프롬프트를 띄울 수 없다.
2. 그래도 잠겨 있으면 로그인 키체인을 명시적으로 잠금 해제한다:

   ```bash
   security unlock-keychain ~/Library/Keychains/login.keychain-db
   ```

3. 잠금 해제 후 `kis-trader doctor` 를 다시 돌려 `keychain-kis` 가 `pass` 인지
   확인한다.

> `doctor` 는 "키체인 잠김"과 "자격증명 없음"을 **다른 결과로 구분해서** 보고한다.
> 잠김을 "설정 안 됨"으로 잘못 읽고 이미 저장된 자격증명을 다시 입력할 필요는 없다.

### 그 밖에 자주 보는 항목

| `doctor` 항목 | 흔한 원인과 대처 |
|---------------|------------------|
| `python` / `venv` | 인터프리터가 3.11~3.13 밖이거나 venv 가 없다 → `kis-trader init` 재실행 |
| `llm-agent` | 설정된 CLI 가 설치돼 있지 않다 → 설치하거나 `init` 으로 다른 에이전트 선택 |
| `signal-dir` | `stock-signal-bot` 이 아직 신호를 쓰지 않았다 → 신호 생산자 쪽을 확인 |
| `kis-api` `rate_limited` | KIS 초당 요청 제한. 자격증명 문제가 **아니다** → 잠시 후 재시도 |
| `job:*` `installed but not loaded` | plist 는 있는데 launchd 에 안 올라갔다 → `doctor` 가 출력한 `launchctl bootstrap gui/$UID <plist 경로>` 를 실행 |

---

## License

MIT — 자세한 내용은 [LICENSE](LICENSE) 참조.
