---
"@younggichoi/kis-trader": patch
---

텔레그램 알림 뮤트 스위치를 추가한다.

- **뮤트 플래그 (`data/telegram_muted`)** — 파일이 존재하면 `src/notify/telegram.py`의 `send()`와 `src/signal/notify/telegram_bot.py`의 `send_message()`가 전송 없이 `[telegram muted]` 로그만 남긴다. 키 재발급 등으로 알림을 잠시 꺼야 할 때 파일 생성/삭제만으로 켜고 끈다 — 코드 변경도 프로세스 재시작도 필요 없다 (매 전송 시점에 파일 존재를 확인한다).
- 시그널 이식 트리는 `src.notify`를 import할 수 없어서(test_vendored_port 계약) 플래그 경로를 인라인으로 둔다.
- 텔레그램 에이전트(사용자 명령 응답)는 뮤트 대상이 아니다 — 꺼지는 것은 알림뿐이다.
