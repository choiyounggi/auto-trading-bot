# kis-trader

`@younggichoi/kis-trader` 는 **매매 신호를 스스로 만들고** 그 신호로 한국투자증권
(KIS) OpenAPI 를 통해 국내·해외 주식을 자동 매매하는 **로컬 실행형 엔진**과, 그
엔진을 설치·진단·운영하는 macOS CLI 다. 신호 생산(`src/signal/`)부터 주문 집행까지
한 패키지 안에 있어 **`init` 한 번으로 전부 돈다.** 매매 판단은 로컬에 설치된 LLM
CLI(claude / codex / pi / gemini) 가 내리고, 주문 집행과 가드레일 clamp 는 파이썬
엔진(`src/`)이 담당하며, 스케줄 실행은 launchd LaunchAgent 로 이루어진다. 서버도
컨테이너도 쓰지 않고 사용자 맥에서만 돈다.

> ⚠️ **이 소프트웨어는 실제 증권 계좌에 주문을 넣습니다.** 아래
> [Risk notice](#risk-notice) 를 반드시 먼저 읽으세요.

---

## 신호는 이 패키지가 직접 만든다

**매매 신호 생산자가 이 패키지 안에 들어 있다** (`src/signal/`). 별도 프로젝트를
설치하거나 따로 돌릴 필요가 없다. `init` 한 번이면 신호 생산부터 주문 집행까지
한 제품으로 돌아간다.

신호 잡은 두 개이고, **자기 신호를 읽어갈 트레이더 잡보다 먼저 돌도록 시각이
배치되어 있다.** 다만 국내와 해외의 간격이 전혀 다르다.

| 신호 잡 | 스케줄 | 무엇을 만드나 | 이 신호를 읽는 트레이더 잡 |
|---------|--------|---------------|----------------------------|
| `signalKr` | 월~금 **16:30** (장 마감 후) | 국내 종목 신호 JSON | `orchestrator` — **다음 영업일 09:05** |
| `signalUs` | 월~금 **22:35**, 23:35 | 해외 종목 신호 JSON (`.us`) | `usOrchestrator` — **같은 날 22:45** |

### 신호와 매매 사이의 간격이 왜 중요한가

트레이더는 **신호 디렉토리에 이미 놓여 있는 JSON 파일을 읽을 뿐이다.** 두 잡
사이에는 어떤 신호 전달이나 대기 장치도 없고, 오직 시각 순서만이 순서를
보장한다. 그래서 간격은 "신호 잡이 끝나고 파일이 디스크에 놓이기까지 걸리는
시간"의 여유분이다.

**해외 — 10분:** 22:35 신호 → 22:45 매매. 신호 잡이 이 10분 안에 끝나야 그날 밤
진입에 반영된다. 늦으면 `usOrchestrator` 는 그 시점에 존재하는 파일, 즉 **전날
신호**를 읽거나 아무것도 읽지 못한다. 신호 잡은 260 거래일을 조회하므로 수 분이
걸릴 수 있다 — 스케줄을 손댈 때 가장 먼저 확인할 곳이 여기다.

`signalUs` 의 두 번째 실행(23:35)은 22:45 매매보다 뒤이므로 **그날 해외 진입에는
반영되지 않는다.** 그 결과물은 디렉토리에 남아 이후 실행이 읽는 최신 신호가 된다.

**국내 — 하룻밤:** 16:30 신호는 **같은 날 매매에 쓰이지 않는다.** 국내 진입을
내는 잡은 09:05 의 `orchestrator` 하나뿐이고, 그 시각에는 오늘 신호가 아직
없으므로 `--carry-over` 로 **가장 최근 신호 = 전 영업일 16:30 산출물**을 읽어
장 시작에 시장가로 진입한다. 즉 국내는 "장 마감 후 신호 → 다음 날 개장 진입"이다.

그래서 국내 쪽에서 중요한 것은 분 단위 간격이 아니라 **신호가 매일 갱신되는지**다.
`signalKr` 이 며칠 걸러 실패하면 09:05 잡은 조용히 멈추는 게 아니라 **묵은 신호로
진입한다.** 신호가 오래됐으면 Telegram 경고를 보내므로, 그 경고를 무시하지 말 것.

신호 잡에는 겹침 방지 가드가 걸려 있다. 앞선 실행이 아직 돌고 있으면 다음 실행은
그대로 종료하고(15분이 지난 잠금은 낡은 것으로 보고 회수한다), 같은 데이터 소스에
두 세션이 동시에 붙지 않는다.

### 신호 출력 위치

`init` 이 신호 디렉토리를 물어보고 설정에 `signalDir` 로 저장하며, launchd 잡에는
`KIS_TRADER_SIGNAL_DIR` 환경변수로 주입한다. **기본 제안값은
`<패키지 루트>/data/signals`** — 생산자와 소비자가 같은 패키지 안에 있으므로
기본값만 그대로 받아도 양쪽이 맞는다.

파일 이름은 `<YYYY-MM-DD>.json`(국내), `<YYYY-MM-DD>.us.json`(해외)이다.
디렉토리는 신호 잡이 첫 실행 때 만든다. 그래서 `init` 시점에 디렉토리가 없어도
경고만 하고 넘어간다 — 아직 한 번도 안 돌았을 뿐 설정 오류가 아니다.

### 선택 자격증명 — 없어도 돌아간다

신호 파이프라인의 자격증명은 **전부 선택**이다. 없으면 기능을 줄여서 동작하고,
멈추지 않는다.

| 항목 | `init` 기본 | 넣으면 | 안 넣으면 |
|------|-------------|--------|-----------|
| KRX 로그인 | **예** (물어봄) | 수급 데이터 정확도가 올라간다 | 공개 데이터만 사용 |
| Brave Search API 키 | **아니오** | 뉴스 보강에 사용 | 뉴스 보강 없이 동작 |
| 뉴스 분류 LLM (`newsLlmBackend`) | **`none`** | `claude` / `codex` / `pi` 로 뉴스 분류 | LLM 을 부르지 않음 |

`none` 이 기본값인 이유는 "LLM 을 부르지 않는다"가 정상적인 선택이기 때문이다.
KRX 와 Brave 값은 `config.json` 이 아니라 **로그인 키체인**(`signal-bot` 서비스)에
저장되고, 파이썬 엔진이 실행 시점에 읽어 환경변수로 주입한다.

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

`init` 은 8단계 대화형 온보딩이다.

| 단계 | 내용 |
|------|------|
| 1/8 | 매매 모드 선택 (`paper` 기본 / `real` 은 추가 확인) |
| 2/8 | KIS app key · app secret · 10자리 계좌번호 → **로그인 키체인에 저장** |
| 3/8 | Telegram 봇 토큰 + chat id (선택, 건너뛰기 가능) |
| 4/8 | 설치된 LLM CLI 탐지 후 기본 에이전트 선택 |
| 5/8 | Python 인터프리터 탐지 + 신호 디렉토리 입력 |
| 6/8 | 신호 파이프라인 — KRX 로그인 · Brave 키(둘 다 선택) · 뉴스 분류 LLM 선택 |
| 7/8 | 설정 저장 → venv 생성 → pip 업그레이드 → 의존성 설치 → SQLite 마이그레이션 |
| 8/8 | 잡별로 launchd LaunchAgent 설치 여부를 묻고 등록 |

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

`start`, `logs`, `install-jobs` 가 다루는 잡은 다음 7개다. 앞의 둘이 신호를
만들고, 나머지 다섯이 진입·감시·정산을 맡는다.

| 잡 이름 | 실행 | 스케줄 | 로그 파일 |
|---------|------|--------|-----------|
| `signalKr` | `src.signal.main --lookback 260` | 월~금 16:30 | `signalKr.log` |
| `signalUs` | `src.signal.main --overseas-only --lookback 260 --no-llm` | 월~금 22:35, 23:35 | `signalUs.log` |
| `orchestrator` | `src.orchestrator --carry-over` | 월~금 09:05 | `orchestrator.log` |
| `monitor` | `src.monitor` | **300초마다 (요일 무관)** | `monitor.log` |
| `reconciler` | `src.reconciler` | 월~금 16:00 | `reconciler.log` |
| `dipBuy` | `src.orchestrator --dip-only` | 월~금 15:00 | `dipBuy.log` |
| `usOrchestrator` | `src.orchestrator --asset-class overseas_stock` | 월~금 22:45 | `usOrchestrator.log` |

시각 지정 잡은 launchd `StartCalendarInterval` 로 월~금만 돌지만, `monitor` 는
`StartInterval` 방식이라 **주말·공휴일에도 300초마다 기동한다** (장이 닫혀 있으면
할 일이 없어 그대로 종료된다).

국내 진입을 내는 잡은 `orchestrator` 하나뿐이고 09:05 에 `--carry-over` 로 돈다.
**오늘 신호는 16:30 에야 만들어지므로, 장 시작 시점에는 가장 최근(전 영업일)
신호로 진입한다.** 신호가 며칠씩 묵었으면 경고를 보낸다.

신호 잡 두 개만 겹침 가드가 붙어 있다 — 260 거래일 조회는 분 단위로 걸릴 수 있어
다음 스케줄과 겹칠 수 있는 반면, 트레이더 잡은 짧고 이미 멱등이다.

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
| `KIS_TRADER_SIGNAL_DIR` | 신호 JSON 을 쓰고 읽는 디렉토리. 설정의 `signalDir` 값이 잡 plist 에 주입되며, 파이썬 엔진이 이 값을 읽는다. **절대경로여야 한다** |

---

## How the chain runs — 하루가 도는 순서

신호 잡이 JSON 파일을 쓰고, 트레이더 잡이 그 파일을 읽어 주문을 낸다. 잡들은
서로를 직접 호출하지 않는다 — **연결 고리는 신호 디렉토리의 파일과 스케줄 시각뿐**
이다.

```
[국내]  signalKr 16:30 ─▶ <signalDir>/2026-08-05.json
                                    │
                              (하룻밤 넘김)
                                    ▼
        다음 영업일 09:05  orchestrator --carry-over ─▶ KIS 주문 (시장가)

[해외]  signalUs 22:35 ─▶ <signalDir>/2026-08-05.us.json ─▶ 22:45 usOrchestrator ─▶ KIS 주문
                                                                        │
        monitor 300초마다 ◀── 보유 포지션 감시 · 손절/익절 · 강제 청산 ──┘
        reconciler 16:00 ◀── 잔주문 취소 · 잔고 대조 · 일일 PnL · 백업
```

| 시각 | 잡 | 하는 일 |
|------|-----|---------|
| 월~금 **09:05** | `orchestrator --carry-over` | **전 영업일 16:30 신호**를 읽어 장 시작 진입 (시장가) |
| 월~금 15:00 | `dipBuy` | 지수 ETF 단계적 dip-buy 전용 실행 |
| 월~금 16:00 | `reconciler` | 잔주문 취소 → DB·KIS 잔고 대조 → 일일 PnL 집계 → SQLite 백업 → 리포트 |
| 월~금 **16:30** | `signalKr` | **국내 신호 JSON 생성** — 소비는 다음 영업일 09:05 |
| 월~금 **22:35**, 23:35 | `signalUs` | **해외 신호 JSON 생성** |
| 월~금 **22:45** | `usOrchestrator` | 22:35 에 만들어진 해외 신호를 읽어 진입 결정 → 주문 |
| 상시 300초 | `monitor` | 보유 포지션 현재가 조회 → 손절·익절 정정 / 강제 청산 → 알림 |

굵게 표시한 네 시각이 이 체인의 심장이다. **신호가 먼저, 매매가 나중**이라는 순서가
깨지면 트레이더는 오늘 신호가 아니라 묵은 신호를 읽는다 — 국내는 그것이 정상
동작(하룻밤 넘김)이고, 해외는 10분을 넘긴 사고다. 이 차이를 헷갈리지 말 것.

읽을 신호가 하나도 없으면 트레이더 잡은 경고를 보내고 진입 없이 끝난다 — 주문은
하나도 나가지 않는다. 그 상태는 `kis-trader doctor` 가 신호 디렉토리 항목에서
보고한다.

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
| `signal-bot` | `krx-id` / `krx-pw` | KRX 로그인 (선택) |
| `signal-bot` | `brave-api-key` | Brave Search API 키 (선택) |

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
| `<패키지 루트>/data/signals/` | 신호 잡이 쓰고 트레이더 잡이 읽는 JSON (`signalDir` 기본값) |

`data/` 아래 산출물은 **배포 패키지에 들어 있지 않다.** 신호 JSON 도 SQLite DB 도
설치된 맥에서 실행 시점에 생성된다.

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

`doctor` 는 설정 → 파이썬·venv·DB → 키체인 자격증명 → LLM CLI → 신호 디렉토리 →
**KIS 실제 왕복 호출** → launchd 잡 순으로 점검하고, 통과하지 못한 항목마다
**바로 아래 줄에 해결 방법을 같이 출력한다.**
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

| `doctor` 가 짚는 곳 | 흔한 원인과 대처 |
|---------------------|------------------|
| `python` / `venv` | 인터프리터가 3.11~3.13 밖이거나 venv 가 없다 → `kis-trader init` 재실행 |
| `llm-agent` | 설정된 CLI 가 설치돼 있지 않다 → 설치하거나 `init` 으로 다른 에이전트 선택 |
| 신호 디렉토리 | 신호 잡이 아직 한 번도 돌지 않았다 → `kis-trader start signalKr` 로 즉시 1회 실행해 볼 것 |
| `kis-api` `rate_limited` | KIS 초당 요청 제한. 자격증명 문제가 **아니다** → 잠시 후 재시도 |
| `job:*` `installed but not loaded` | plist 는 있는데 launchd 에 안 올라갔다 → `doctor` 가 출력한 `launchctl bootstrap gui/$UID <plist 경로>` 를 실행 |

---

## License

MIT — 자세한 내용은 [LICENSE](LICENSE) 참조.
