# contents-factory

글·이미지·영상 제작 + 5개 플랫폼(Threads·Instagram·TikTok·YouTube·네이버 블로그) 자동발행
스킬팩. 페르소나는 프로필로 갈아끼운다. 기획 `docs/PLAN.md`, 구현 스펙 `docs/P0-SPEC.md`.

Interview-driven content generation plus scheduled publishing to five platforms. The persona
lives in a profile directory, not in the code. Plan: `docs/PLAN.md`, P0 spec: `docs/P0-SPEC.md`.

## 지원 OS / Supported platforms

| | macOS | Windows | Linux |
|---|---|---|---|
| 글·이미지·영상 생성 | ✅ | ✅ | ✅ |
| Threads·Instagram·TikTok·YouTube 발행 | ✅ | ✅ | ✅ |
| 네이버 블로그 | ✅ aside | ✅ playwright | ✅ playwright |
| 스케줄러 | launchd | Task Scheduler (`schtasks`) | cron |
| 런타임 홈 | `~/.contents-factory` | `%USERPROFILE%\.contents-factory` | `~/.contents-factory` |

OS는 설치 때 한 번 고르고 `factory.toml`의 `os`에 기록된다 (`factory init --os macos|windows|linux`).
생략하면 감지하고 **확인 질문**을 띄운다 — 감지값을 조용히 채택하지 않는다.
호스트 상태는 `factory doctor` 한 방으로 본다.

The OS is answered once and recorded in `factory.toml` as `os`. Omitting the flag
detects and asks for confirmation rather than adopting a guess. `factory doctor` prints
the whole host picture in one table.

## 설치 / Install

**한 줄이면 끝난다.** 터미널을 한 번만 열고 아래를 붙여넣는다. 관리자 권한은 필요 없다.

One line, pasted into a terminal once. No administrator rights required.

### macOS · Linux

```sh
curl -fsSL https://raw.githubusercontent.com/dasbeerboot/contents-factory/main/install.sh | sh
```

### Windows (PowerShell)

```powershell
irm https://raw.githubusercontent.com/dasbeerboot/contents-factory/main/install.ps1 | iex
```

설치 스크립트가 하는 일 (PLAN §9.1):

1. 환경 점검 — OS·아키텍처 확인, root로 돌리면 거절
2. 패키지 매니저 — mac은 Homebrew 없으면 **자동 설치**, Windows는 winget 확인
3. `uv` · `git` · `ffmpeg` · `node` · `higgsfield` CLI — **이미 있으면 건너뜀**
4. 질문 **하나**: "네이버 블로그에도 올릴 건가?" 예라고 하면 `npx playwright install chromium`
5. 레포를 `~/contents-factory`로 클론 (이미 있으면 `git pull --ff-only`)
6. `uv sync`
7. `uv run factory init --os <감지값>`
8. 에이전트 감지 (claude / codex). 둘 다 없으면 Claude Code 설치
9. 바탕화면에 **`콘텐츠공장`** 런처 생성
10. 잠자기 설정 안내 (폰에서 조작하려면 PC가 깨어 있어야 한다)
11. `factory start` 실행

What the installer does: preflight, package manager (Homebrew is installed automatically
on macOS, winget is checked on Windows), the five CLIs (skipped when already present), one
question about Naver, clone or fast-forward into `~/contents-factory`, `uv sync`,
`factory init`, agent detection with Claude Code as the fallback, the desktop launcher,
the sleep hint, then `factory start`.

**전 단계 멱등**이다. 중간에 실패하면 몇 번째 단계였는지 한 줄로 말하고, 고친 뒤 **같은 명령을
그대로 다시** 돌리면 끝난 단계는 건너뛴다. 출력 언어는 `LANG`을 따르고 `--lang ko|en`으로
덮어쓴다 (파이프로 넘길 땐 `sh -s -- --lang en`).

Every step is idempotent. A failure names its step number; re-running the same command
skips what already finished. Output language follows `LANG`, overridable with `--lang`.

<details>
<summary><b>옵션 / Options</b></summary>

| 플래그 | 뜻 |
|---|---|
| `--lang ko\|en` | 출력 언어 (기본: `LANG`) |
| `--dir PATH` | 클론 위치 (기본 `~/contents-factory`) |
| `--naver` / `--no-naver` | 네이버 질문을 묻지 않고 결정 |
| `--no-start` | 설치만 하고 `factory start`는 안 띄움 |
| `--dry-run` | 실행 대신 전부 출력만 |

파이프로 실행할 땐 `sh`에 인자를 넘긴다: `curl -fsSL .../install.sh | sh -s -- --lang en`.
PowerShell은 파이프에 인자를 못 넘기므로 먼저 받아서 실행한다:
`irm .../install.ps1 -OutFile i.ps1; ./i.ps1 -Lang en`.

</details>

<details>
<summary><b>수동 설치 / Manual install</b> (스크립트를 안 쓰고 직접 할 때)</summary>

#### macOS

```sh
brew install ffmpeg node git
curl -LsSf https://astral.sh/uv/install.sh | sh
curl -fsSL https://raw.githubusercontent.com/higgsfield-ai/cli/main/install.sh | sh
higgsfield auth login                        # 브라우저 OAuth, 토큰은 CLI가 보관·갱신
git clone https://github.com/dasbeerboot/contents-factory.git ~/contents-factory
cd ~/contents-factory
uv sync
uv run factory init --os macos
uv run factory launcher create                # 바탕화면 런처
uv run factory doctor                         # 결측 바이너리·로그인 상태 확인
uv run pytest
```

네이버를 쓸 거면 [aside](https://aside.com/download)(**macOS 15.0 이상**)를 깔고 네이버에
1회 로그인한다. aside를 안 쓰면 playwright 백엔드를 macOS에서도 쓸 수 있다 (아래 네이버 절).

#### Windows (PowerShell)

```powershell
winget install --id Gyan.FFmpeg             # ffmpeg + ffprobe
winget install --id OpenJS.NodeJS.LTS       # node, 네이버 퍼블리셔용
winget install --id Git.Git
irm https://astral.sh/uv/install.ps1 | iex  # uv
# higgsfield CLI: 설치 명령은 공식 안내를 따른다 → https://github.com/higgsfield-ai/cli
higgsfield auth login                       # 브라우저 OAuth
git clone https://github.com/dasbeerboot/contents-factory.git $env:USERPROFILE\contents-factory
cd $env:USERPROFILE\contents-factory
uv sync
uv run factory init --os windows
uv run factory launcher create
uv run factory doctor
uv run pytest
```

네이버 블로그를 쓸 거면 Playwright Chromium이 추가로 필요하다 (aside는 macOS 전용):

```powershell
npm install -g playwright
npx playwright install chromium
uv run factory naver login                  # 창이 열리면 직접 로그인한다
uv run factory naver status
```

전역 설치도 찾는다 — ESM 동적 임포트는 `NODE_PATH`를 무시하므로 로더가 로컬 해석 →
`npm root -g` 순서로 뒤진다. 특이한 위치에 깔았으면 `FACTORY_PLAYWRIGHT_MODULE`에 패키지
디렉토리나 엔트리 파일 경로를 준다. 못 찾으면 `PLAYWRIGHT_MISSING`, 깔렸는데 초기화가
깨지면 `PLAYWRIGHT_LOAD_FAILED`, Chromium 바이너리가 없으면
`PLAYWRIGHT_BROWSER_MISSING`(= `npx playwright install chromium`) 이다.

#### Linux

macOS 절과 같고, ffmpeg·node·git은 배포판 패키지로 깔고 `factory init --os linux`,
스케줄러는 `--runner cron`, 네이버는 playwright 백엔드다.

</details>

Python 3.11+. `node` is required for the Naver publisher; `aside` is macOS-only and
Windows/Linux use the Playwright backend instead. `factory doctor` names anything missing
with the install line for the current OS.

### 바탕화면 런처 / Desktop launcher

설치가 끝나면 바탕화면에 런처가 하나 생긴다. **다음부터는 터미널을 열 필요가 없다.**

| | 파일 | 하는 일 |
|---|---|---|
| macOS | `~/Desktop/콘텐츠공장.command` | 터미널을 열고 `cd ~/contents-factory && uv run factory start` |
| Windows | `%USERPROFILE%\Desktop\콘텐츠공장.bat` | 같음. `uv`가 PATH에 없으면 `%USERPROFILE%\.local\bin\uv.exe` 절대경로 |
| Linux | `~/Desktop/콘텐츠공장.sh` | 같음 |

```
factory launcher create [--os ...] [--dry-run]   # 다시 만들기 (덮어씀, 멱등)
factory launcher remove
factory launcher status
```

런처는 실패하면 창을 닫지 않고 종료코드를 보여준다 — 깜빡이고 사라지는 창은 "아무 일도 안
일어났다"와 구분이 안 된다. 바탕화면 위치가 OneDrive 등으로 리다이렉트됐으면
`FACTORY_DESKTOP`으로 지정한다.

The launcher keeps its window open on failure and reports the exit code. Point
`FACTORY_DESKTOP` at the real desktop if yours is redirected (OneDrive).

### 폰에서 조작 / Drive it from your phone

PLAN §9.8. **입구는 에이전트다** — Claude Code든 Codex든 같은 두뇌(`factory wizard`)를 쓴다.
텔레그램은 입구가 아니고 미리보기와 컨펌 버튼만 담당한다.

`factory remote install`이 에이전트의 원격제어를 이 PC에 **상시 등록**한다. 그러면 폰 앱에서
`contents-factory` 환경을 골라 조작할 수 있다.

```
factory remote install [--agent claude|codex] [--runner auto|launchd|schtasks|systemd|cron]
                       [--dry-run] [--no-probe]
factory remote remove  [--agent ...] [--dry-run]
factory remote status  [--agent ...]
```

`--agent`를 생략하면 설치된 에이전트를 감지한다. 등록 방식은 OS가 정한다.

| OS | 등록 대상 | 로그 |
|---|---|---|
| macOS | launchd user agent (`com.contents-factory.<agent>-remote-control`) | `~/Library/Logs/contents-factory-remote.log` |
| Windows | Task Scheduler, 로그온 트리거 + 실패 시 1분 뒤 재시작 | `~/.contents-factory/logs/` |
| Linux | systemd user unit, 없으면 `@reboot` crontab | `~/.contents-factory/logs/` |

| 에이전트 | 명령 | 상시 유지 | 폰 쪽 준비 |
|---|---|---|---|
| **Claude Code** | `claude remote-control --name contents-factory --spawn same-dir` | KeepAlive (죽으면 재시작) | Claude 앱에서 환경 선택 |
| **Codex** | `codex remote-control start` | 로그온 시 1회 (데몬이 자기 수명을 관리하므로 KeepAlive 금지) | 데스크톱 앱에서 QR 스캔, 또는 `codex remote-control pair` 수동 코드. **macOS·Windows만 지원** |

Codex 원격제어는 2026-06-25 GA로, Claude와 마찬가지로 **내 PC에서 돌아가는 세션을 폰에서
조작**하는 기능이다 (OpenAI 릴레이 경유, 포트 개방·SSH 터널 없음). 다만 공식 설정 경로는
ChatGPT **데스크톱 앱**의 QR 페어링이고 CLI 경로(`codex remote-control start|pair`)는
`[experimental]` 라벨이다. 근거: [developer-commands](https://learn.chatgpt.com/docs/developer-commands.md?surface=cli),
[remote-connections](https://learn.chatgpt.com/docs/remote-connections.md),
[mobile](https://learn.chatgpt.com/docs/mobile.md),
[changelog](https://learn.chatgpt.com/docs/changelog).

Codex remote control went GA on 2026-06-25 and, like Claude Code, drives a session running
on *your own* machine from the phone — not to be confused with Codex cloud tasks, which run
in OpenAI-hosted containers. Its documented setup path is a QR scan from the ChatGPT desktop
app; the CLI path is still marked experimental, and only macOS and Windows hosts are supported.

#### 워크스페이스 신뢰 / Workspace trust

에이전트가 이 폴더를 한 번도 신뢰한 적 없으면, 보이지 않는 프롬프트에서 멈춘 데몬이 된다.
그래서 `remote install`은 **신뢰 기록을 먼저 확인**하고, 없으면 거절한다.

- Claude: 레포 디렉토리에서 `claude --print "ok"`를 1회 돌려 `~/.claude.json`에 기록이
  생기는지 본다. 그래도 없으면 `CLAUDE_WORKSPACE_UNTRUSTED` — **사용자가 그 폴더에서
  `claude`를 한 번 열어 신뢰를 수락**해야 한다.
- Codex: `~/.codex/config.toml`의 `[projects."<path>"] trust_level`을 읽는다 (상위 폴더도 인정).
  없으면 `CODEX_WORKSPACE_UNTRUSTED`.

🚨 **신뢰 파일은 절대 직접 고치지 않는다.** 사용자 대신 보안 질문에 답하는 짓이다. 거절할 때도
등록하려던 정의는 그대로 출력하므로 무엇이 등록될지 미리 볼 수 있다.

The trust record is never written on the user's behalf — accepting a security prompt is
theirs to do. A refusal still prints the definition it would have registered.

#### 잠자기 / Sleep

PC가 잠들면 폰에서 못 붙는다. 로그인 세션도 PC에만 있으므로 **실행은 PC, 폰은 조작·컨펌**이다.

- macOS: `open "x-apple.systempreferences:com.apple.preference.energysaver"`
- Windows: `powercfg /change standby-timeout-ac 0`
- Linux: `sudo systemctl mask sleep.target suspend.target`

### 외부 바이너리 / External binaries

| 바이너리 | 쓰는 곳 | 없으면 |
|---|---|---|
| `ffmpeg`·`ffprobe` | 영상 조립·자막·프레임 게이트 | `FFMPEG_MISSING` |
| `node` | 네이버 퍼블리셔 | `NODE_MISSING` |
| `uv` | 이 프로젝트 실행 | - |
| `higgsfield` | 이미지·영상 생성, TikTok 인증 | `HIGGSFIELD_CLI_MISSING` |
| `aside` | 네이버 aside 백엔드 (macOS 전용) | `NAVER_SESSION_MISSING` |

탐색은 전부 `shutil.which` + `.exe`·`.cmd` 접미 허용이라 Windows에서 `ffmpeg.exe` 도 잡힌다.

## 🧭 처음 쓰는 사람 / Beginner flow (PLAN §9)

**CLI가 두뇌, 에이전트는 입과 손, 사용자는 대답만 한다.** 포맷·게이트·예산을 모르는 사람이
혼자 끝까지 갈 수 있는 경로가 기본값이다.

```sh
factory start                 # doctor → 에이전트(claude/codex) 감지 → 위저드로 열기
factory start --no-launch     # 띄우지 않고 명령만 출력
```

에이전트는 판단하지 않는다. `factory wizard next` 가 내주는 질문 하나를 사용자 언어로
읽어주고, 사용자가 한 말을 `factory wizard answer` 로 넘기는 것만 반복한다. 그 계약이
레포 루트의 **`CLAUDE.md` 와 `AGENTS.md`(같은 내용)** 이고, Claude Code 쪽 입구는
`.claude/skills/wizard/`(`/wizard`)다.

```sh
factory wizard next [--json] [--dry-run]       # 지금 물어볼 질문 1개
factory wizard answer --id <id> --value "<사용자가 한 말>"
factory wizard skip | back | status
factory wizard reset [--flow setup|make]
```

| 플로우 | 단계 | 묻는 것 |
|---|---|---|
| `setup` | 7 | 컴퓨터 → 언어 2개 → 이름·한 줄 소개·말투 → 올릴 곳(복수) → 고른 곳마다 연결(할 일 1개 + 확인 명령) → 시간대 → "첫 글 만들까요?" |
| `make` | 4 | 무엇(글/이미지/영상/말로 설명) → 주제 한 줄 + 첨부 → 초안 보여주고 이대로/고쳐줘/다시 → 언제 |

- 말투 3지선다가 `persona.md`·`writer_rules.md`·`brand.md` 를 만든다 (`factory profile template`).
- 시간대 답은 **사용자 로컬 시각**으로 받아 UTC 슬롯으로 변환해 `platforms.json` 에 쓴다
  (`factory profile platform`).
- 포맷과 플랫폼은 **묻지 않는다.** 종류와 프로필에서 CLI가 정한다.
- 자유문 답(선택지 밖)은 Anthropic 키가 있으면 haiku(`claude-haiku-4-5-20251001`)로
  선택지에 매핑하고, 키가 없으면 사람 말 한 줄로 재질문한다. "아직 안했어요" 를 yes로 읽지
  않도록 부정 표현을 먼저 본다.
- `simple = true`(기본)면 큐 적재 때 승인+무장을 한 번에 하고, 게이트 FAIL은 최대 2회
  자동 재생성한 뒤 사람 말로 사유를 알려준다. `simple = false` 면 기존처럼 승인·무장이
  따로다. **어느 쪽이든 큐 적재는 발행이 아니고, 발행은 슬롯이 한다.**
- 상태는 `~/.contents-factory/state/wizard.json` 하나이고 `factory wizard` 만 쓴다.

### 폰으로도 보여주기 / The phone, when it is wired up

`factory.toml` 이 `review = "telegram"` 이고 봇 토큰과 chat 이 실제로 저장돼 있으면,
위저드가 **초안 제시 단계에서 자동으로 폰에도 보낸다.**

- 초안 단계 = `factory telegram preview --brief b_... [--output 0]` (버튼 없음 — 아직
  큐에 없어서 승인할 대상이 없다)
- 큐 적재 직후 = `factory telegram preview --item q_...` (올리기·거절·고쳐줘 버튼 3개)
- 둘 중 하나라도 실패하면 **조용히 터미널만** 쓴다. 폰이 안 터져도 플로우는 안 끊긴다.
- 텔레그램 미설정(`review = "terminal"` 또는 토큰·chat 없음)이면 아무것도 안 보낸다.
- `--dry-run` 플로우는 폰에 안 보낸다. 보여줄 실물이 없고, 전송은 드라이런이 아니다.

**답은 먼저 온 쪽을 쓴다.** 같은 초안에 터미널과 폰이 둘 다 답하면 먼저 도착한 답으로
결정하고 나머지는 버리면서 그렇게 말한다.

| 폰에서 누른 것 | 벌어지는 일 |
|---|---|
| 올리기 | `queue approve` (+ `simple` 이면 `arm`, 근거는 `telegram:<chat>`) |
| 거절 | `unapprove` + `disarm` + `last_error=TELEGRAM_REJECTED` → 위저드가 초안을 다시 만든다 |
| 고쳐줘 → 한 줄 | `extra.edit_request` 기록 + **즉시 승인·무장 해제**. 다음 `factory wizard next` 가 그 한 줄을 `factory brief revise` 로 소비하고 초안을 다시 만든다 |

`extra.edit_request` 는 소비하면 즉시 비운다 — 같은 수정이 두 번 반영되면 프롬프트가
중복 누적된다. 발행된 항목은 손대지 않는다(`set_extra` 가 거부).

### 오류는 코드 하나당 할 일 하나 / Error dictionary

```sh
factory explain HIGGSFIELD_LOGIN_REQUIRED
factory explain --list                      # 사전에 있는 전체 코드
```

`factory/i18n/errors.{ko,en}.json` 에 코드별 `{human, action, command}` 가 있다 (314개,
`FactoryError` 로 던지는 코드 전수 + tick 판정·게이트 위반·HTTP·런처·원격제어·텔레그램). 위저드와 발행 실패
경로가 같은 사전을 읽고, 실패한 명령은 **코드 한 줄 + 사람 말 한 줄**을 같이 낸다.

### 런처에서 띄울 때 PATH / Launcher PATH

바탕화면 런처는 더블클릭으로 실행되므로 프로필(`~/.zshrc` 등)을 거치지 않은 PATH 를
물려받는다. `uv`·`claude`·`node`·`ffmpeg` 가 디스크에 있는데도 안 보이는 상태다.

- 생성된 런처가 `~/.local/bin`·`/opt/homebrew/bin`(윈도우는 `%USERPROFILE%\AppData\
  Roaming\npm` 등)을 **PATH 앞에 붙인다**. `.bat` 는 ASCII 유지 — 계정명이 한글이어도
  깨지지 않게 `%USERPROFILE%` 를 그대로 쓴다.
- 그래도 `factory start` 는 에이전트를 `osinfo.find_binary` 로 한 번 더 찾고,
  실행은 **절대경로**로 한다(출력되는 명령은 짧은 이름 그대로).

## 🚨 상태파일은 CLI로만 / State is CLI-only

`~/.contents-factory/` 밑(`queue/`, `briefs/`, `state/`)은 **`factory` 명령만** 쓴다.
세션이나 사람이 손으로 json을 고치면 스케줄러가 죽는다. 전용 명령이 없는 변경이 필요하면
명령을 먼저 추가한다.

Everything under `~/.contents-factory/` is written only by `factory` commands. Hand editing
the json is how the scheduler dies.

## 명령 / Commands

```
factory init [--os macos|windows|linux]
factory doctor [--json]                  # OS·파이썬·바이너리 6종·CLI 로그인·스케줄러
factory start [--agent claude|codex] [--no-launch]   # doctor → 에이전트 → 위저드
factory wizard next [--json] [--dry-run] | answer --id <id> --value "..."
                  | skip | back | status | reset [--flow setup|make]
factory explain <CODE> [--lang ko|en] [--json] | --list
factory key set --profile X --name KEY [--stdin]   # 값은 숨김 입력, 출력 안 함
factory key list --profile X [--json]              # 키 이름과 설정 여부만
factory profile add <name> [--from _example] | list | show [name]
factory profile template --profile X --brand "이름" --intro "한 줄"
                  --tone warm|dry|playful          # persona·writer_rules·brand 생성
factory profile platform --profile X --platform threads [--enable|--disable]
                  [--slot 12:00Z ...] [--daily-cap N] [--blog-id ID]
                  [--connector-id ID] [--account-id ID]   # platforms.json 소유자
factory brief new --kind text --format threads_post [--profile X] [--answers file|-]
                  [--force]   # id 는 b_<UTC초>_<4자>, 기존 id 덮으려면 --force
factory brief show --brief b_...
factory brief revise --brief b_... --note "<고칠 내용 한 줄>"   # prompt_final 누적
factory gen text  --brief b_... [--dry-run]
factory gen image --brief b_... [--dry-run]   # 실제 higgsfield 호출, --dry-run은 argv만
factory gen video --brief b_... [--stage script|still|clip|tts|captions|assemble|all]
                  [--dry-run] [--force]       # 단계별 실행, 산출물 있으면 건너뜀
factory gate run (--brief b_... | --text "...") [--format threads_post] [--gate repeat_guard]
                 [--box x,y,width,height[,label]]      # caption_safe_zone 입력
                 [--media path]                        # 영상 게이트 대상 (기본: brief 산출물)
                 [--item q_...]                        # duplicate_copy 자기 제외
factory queue add --platform threads [--brief b_... --output 0] [--text "..."]
                  [--media path --public-url url] [--slot next|2026-10-15T12:00:00Z]
                  [--extra key=value ...]   # title·tags·privacy·privacy_level·mode·
                                            # topic_tag·replies·ig_shape·cover_url
factory queue list [--pending] [--json] | approve | unapprove | arm --reason "..." | disarm | purge
factory queue reschedule q_... --slot next|<ISO>   # 슬롯 이동 (발행본·미확정 배송은 거부)
factory queue reopen --item q_... --reason "..."   # unknown phase 해제 (사람 확인 후)
factory media host --item q_... [--index 0] [--dry-run]  # 로컬 미디어 → 공개 URL,
                                                       # media[].public_url 기록
factory publish <platform> --item q_... [--dry-run]
factory schedule install|remove|status [--runner auto|launchd|cron|schtasks]
                 | tick [--dry-run]           # auto = factory.toml 의 os 가 정한다
factory telegram setup [--profile X] [--wait 60] [--no-wait]   # BotFather 3단계 안내 →
                      # 토큰 숨김 입력 → /start 로 chat_id 자동 저장
factory telegram status [--profile X] [--json]   # 토큰·chat·offset 유무 (값은 안 찍는다)
factory telegram test [--text "..."] [--dry-run]            # 1줄 발송 확인
factory telegram preview --item q_... [--dry-run]           # 미리보기 + 컨펌 버튼 3개
factory telegram serve [--once] [--poll-timeout 50]         # 버튼 수거 루프
factory telegram install|remove [--runner auto|launchd|cron|schtasks] [--dry-run]
factory launcher create|remove|status [--os ...] [--name ...] [--dry-run]
                 # 바탕화면 콘텐츠공장 런처. create 는 덮어쓰기 멱등
factory remote install|remove|status [--agent claude|codex] [--dry-run]
                 [--runner auto|launchd|schtasks|systemd|cron] [--no-probe]
                 # 폰에서 조작하려고 에이전트 원격제어를 이 PC에 상시 등록.
                 # 워크스페이스 신뢰가 없으면 거절한다 (신뢰 파일은 안 고친다)
factory naver login [--wait-ms N] | status    # 네이버 playwright 세션 (직접 로그인)
factory auth threads   --profile X [--app-id ID] [--redirect URI] [--exchange-url URL]
                       [--url-only] [--no-open]     # OAuth, writes THREADS_* keys
factory auth instagram --profile X [--app-id ID] [--redirect URI]  # consent URL only (P1)
factory token refresh [--profile X] [--platform threads|instagram] [--dry-run]
                      # Higgsfield 는 여기 없다 — CLI 가 자기 토큰을 갱신한다
factory ledger summary [--days 30]
factory i18n get <key> [--lang ko|en] | keys | missing --lang ko
factory formats [--kind text|image|video] [--format <name>] [--lang ko|en]
```

`python -m factory ...` 도 같은 엔트리다.

## 발행 규칙 / Publishing rules

- **승인 ≠ 발행.** 슬롯 시각(UTC)에 tick이 발행한다. `factory publish`의 실발행은 수동
  예외이고, 승인·무장이 둘 다 돼 있어야 받는다.
- **armed=false면 approved여도 안 나간다.** `arm`은 근거 문자열이 필수다.
- 지각분은 `late_policy`: `carry_once`(다음 슬롯 1회 이월, 기본) 또는 `drop`. 이월은 1회뿐이고
  두 번째는 폐기 + 승인 해제다. 무상한 지각 자동발행은 없다.
- 프로필·플랫폼별 `daily_cap`을 넘으면 그 런은 건너뛴다.
- 지각 유예는 **900초 고정**이다(§10 결정5). 슬롯 +15분 안이면 그대로 발행하고, 넘으면
  `late_policy`로 넘어간다.
- P0에는 자동승인이 없다(§10 결정1). 승인은 사람이 한다. P1에서 프로필 옵션
  `auto_approve`(기본 false)가 들어오더라도 `armed` 게이트는 그대로 남는다.
- `repeat_guard`는 큐 적재 때 통과했어도 **발행 직전에 다시** 돈다.
- 이 팩토리로 넘어온 프로필은 `factory.toml`의 `[extra_corpus]`에 **이전 파이프라인의 발행
  이력**을 걸어둔다. 안 걸면 가드가 자기 큐만 보므로 이미 태운 문구가 새 문구로 통과한다.
  프로필명 키(`"*"` = 전 프로필), 값은 경로 리스트. `.jsonl`·`.json`·평문을 읽고 행의
  `text`를 본문, `published_at`을 시각으로 쓴다. 캐시 시그니처에 이 파일들의 mtime·size가
  들어가서 외부 코퍼스가 자라면 인덱스가 바로 무효화된다.
- 토큰 만료 7일 전부터 tick이 경고를 띄운다.
- 네이버는 로그인된 브라우저 세션이 필요하다 (macOS는 aside, 그 외는 playwright 영속
  프로필). 없으면 `NAVER_SESSION_MISSING`으로 큐에 그대로 남는다 — 재시도할 실패가 아니고
  로그인하라는 신호다.

### 중복발행 방어 (delivery phase)

큐 아이템은 `ready → creating → container → publishing → published` 상태를 지난다.
`creating`·`publishing`에서 런이 죽으면(응답 유실) 그 아이템은 `unknown`으로 주차되고
**자동 재시도하지 않는다** — 재발행이 중복 게시를 만들 수 있기 때문이다.

- `container` 상태면 다음 tick이 컨테이너를 **재사용**해서 publish 단계만 다시 한다.
  24시간 지난 컨테이너는 1회 재생성한다(`container_regen`).
- 실패 코드가 "아무것도 제출되지 않았음"을 증명하는 것(4xx·사전검증·세션없음)이면
  `ready`로 되돌려 재시도한다. 5xx·네트워크 끊김·미등록 코드는 전부 `unknown`이다.
- `content_sha256`이 박힌 뒤 본문·미디어가 바뀌면 `DELIVERY_CONTENT_CHANGED`로 멈춘다.
- `unknown` 해제는 사람이 플랫폼을 직접 확인한 뒤:
  `factory queue reopen --item q_... --reason "<확인 내용>"`.
  `container_id`나 `published_id`가 남아 있으면 거부한다(`DELIVERY_MAY_BE_PUBLISHED`).
- `phase=published`인데 `published` 플래그가 없으면 tick이 플래그만 복구한다(재발행 안 함).

## 📱 텔레그램은 미리보기·컨펌 전용 / Telegram is preview and confirm only

**입구가 아니다.** 제작 대화는 PC의 에이전트(Claude Code 또는 Codex)가 `factory wizard`로 한다.
텔레그램이 하는 일은 세 가지뿐이다.

1. **미리보기** — 만든 글·이미지·영상을 폰에서 바로 본다. 글은 `<pre>` 블록(줄바꿈·여백 유지),
   이미지는 `sendPhoto`, 영상은 `sendVideo`(`width=1080 height=1920 supports_streaming=true`).
   50MB를 넘는 영상은 ffmpeg로 화질을 낮춘 **프리뷰본**을 보낸다 — 문서 첨부는 미리보기가
   아니라 다운로드라서 쓰지 않는다.
2. **컨펌** — 버튼 3개. `올리기 ✅` / `거절 ❌` / `고쳐줘 ✏️`.
3. **알림** — 아래 정책에 든 것만.

```sh
factory telegram setup            # BotFather 3단계 → 토큰 숨김 입력 → 폰에서 /start
factory telegram status           # 토큰·chat·offset 유무 (토큰 값은 절대 안 찍는다)
factory telegram preview --item q_...   # 미리보기 + 버튼 발송
factory telegram install          # 버튼 수거 루프를 OS에 등록 (mac launchd / win·linux 1분 1폴)
```

버튼이 하는 일:

| 버튼 | 컴맹 모드 (`simple = true`) | 전문가 모드 |
|---|---|---|
| 올리기 ✅ | `queue approve` + `arm --reason "telegram:<chat_id>"` | `queue approve` 만 (무장은 따로) |
| 거절 ❌ | `unapprove` + `disarm` + 사유 `TELEGRAM_REJECTED` 기록 | 같다 |
| 고쳐줘 ✏️ | 다음 텍스트 1줄을 `state/telegram/edits/<item>.txt` + `extra.edit_request`에 기록 | 같다 |

- **실제 재생성은 위저드 몫이다.** 텔레그램은 고칠 내용을 받아 적는 데까지만 한다.
- 모든 상태 변경은 `QueueStore`(= CLI와 같은 경로)를 지난다. 텔레그램이 큐 json을 직접 쓰는 일은 없다.
- 저장된 **그 대화방의 버튼만** 받는다. 다른 chat은 `IGNORED_CHAT`으로 버린다.
- 같은 업데이트가 재배달되면 `DUPLICATE`로 무시한다(update_id 링버퍼). 첫 실행은 수신함
  백로그를 건너뛴다 — 봇 개통 전 쌓인 과거 메시지를 승인으로 재생하지 않는다.
- 타이핑된 줄은 15분이 지나면 `STALE`. 버튼 탭에는 이 검사를 걸지 않는다(미리보기는 몇 시간
  뒤에 눌러도 정상).
- 봇 하나당 `getUpdates` 소비자는 `factory telegram serve` **하나뿐**이다. 같은 토큰으로 다른
  폴러를 돌리면 서로 수신함을 뺏는다.

### 알림 정책 / Notification policy

| | 언제 | 예 |
|---|---|---|
| 🚨 즉시 | 발행 실패, 배송 미확정(`unknown`), 토큰 만료 7일 전 | `HTTP_500`, `queue reopen` 필요 |
| 📊 하루 1번 | `[telegram] summary_at`(UTC) 시각 | 어제 발행 n건 · 오늘 예정 n건 · 대기 컨펌 n건 |
| (무음) | **그 외 전부.** 발행 성공 포함 | 성공은 일일 요약에만 들어간다 |

목록에 없는 이벤트는 기본값이 침묵이다. 성공을 매번 알리면 그 알림을 무시하게 되고, 그러면
중요한 알림도 같이 무시된다.

```toml
# factory.toml
simple = true           # 컴맹 모드: 승인 = 무장
review = "telegram"

[telegram]
summary_at = "21:00Z"   # 일일 요약 시각 (UTC)
```

`credentials.env`에 `TELEGRAM_BOT_TOKEN` 하나만 들어간다. chat_id는 `/start`로 자동 저장되므로
묻지 않는다 (`state/telegram/chat.json`).

**Telegram is not an entrance.** It previews what was made, takes one yes/no, and sends
three kinds of alert. The making happens in the agent on your PC. One `getUpdates`
consumer per bot; only the saved chat is obeyed; successes stay silent and show up in the
daily digest.

## 영상 파이프라인 / Video pipeline

```sh
uv sync --extra video      # numpy + pillow. 없으면 GATE_DEPS_MISSING
factory gen video --brief b_... --dry-run      # 단계별 비용 견적까지
factory gen video --brief b_... --stage still  # 한 단계만
factory gen video --brief b_...                # 전부
```

단계는 `script → still → clip → tts → captions → assemble`. 명령이 단계마다 끊기므로
중간 산출물은 `~/.contents-factory/outputs/<brief_id>/`에 쌓이고 `stages.json`이 뭐가
있는지 기록한다. 산출물이 이미 있으면 그 단계는 건너뛴다 — 재렌더는 `--force`로만, 원장에
별도 과금으로 남는다.

- **편당 캡.** 유료 호출마다 프로바이더에 견적을 묻고 `credit_cap_per_item`(포맷 yaml)에서
  원장이 이미 기록한 금액을 뺀 잔액과 대조한다. 넘으면 `BUDGET_EXCEEDED`로 **호출 전에**
  멈춘다. 취소된 작업도 과금되기 때문이다.
- **재롤 없음.** 실패한 테이크는 폐기하고 예비안을 넣는다 (`docs/rules/video-rules.md` §8–9).
- **골든 프롬프트.** `script` 단계가 포맷 템플릿의 슬롯만 바뀌었는지 검사한다. 슬롯 밖
  문장이 다르면 `SCRIPT_GATE_FAILED`로 렌더하지 않는다.
- **멀티테이크.** 클립 모델 상한(15s)보다 긴 영상은 연속 테이크로 나누고, 뒤 테이크의 시작
  이미지는 **앞 테이크의 마지막 프레임**이다. 같은 스틸로 두 번 뽑으면 접합부에서 배경이
  처음으로 되돌아간다.
- **해상도는 예산 레버.** 24초를 1080p로 뽑으면 288크레딧, 720p + 조립 시 업스케일이면
  108크레딧이다. `limits.clip_resolution`.
- **자막 시계.** 자막 타이밍은 실제로 재생되는 오디오를 따른다 (`limits.caption_source`).
  테이크가 자기 음성을 갖고 있으면 그 테이크를 전사해서 맞춘다.

페르소나 고유값(voice id, Soul id, 레퍼런스 이미지, 엔드카드 문구)은 전부 프로필에서 온다.
엔드카드 같은 고정 문구는 `brand.md`의 `- End card: ...` 줄이 슬롯 값이 된다.

### 영상 게이트 / Video gates

| 게이트 | 잡는 것 |
|---|---|
| `stt_wpm` | 실발화 wpm, 승인 대본 대비 충실도, 필러·중복어. 전사는 **현재 테이크**에서 새로 뜬다 (오디오 해시로 캐시 무효화) |
| `limb_motion` | 테이크 내내 안 움직이는 팔. 몸통 기준 상대 벡터의 표준편차로 판정, 팔이 프레임 밖이면 PASS |
| `bg_motion` | `FROZEN_BG`(배경이 안 흐름) · `MORPH_BG`(흐르지만 모양이 변형) · `FRAME_JUMP`(한 프레임 순간이동) |
| `identity` | 레퍼런스 대비 얼굴 길이/폭 비. 비율만으로 끝내지 않고 몽타주를 같이 내놓는다 |

`factory gate run --brief b_...`는 brief의 렌더 결과를 자동으로 찾고, `caption_safe_zone`에
렌더러가 실제로 그린 박스를 넘긴다 — 손으로 좌표를 치지 않는다.

## 프로필 / Profile

`profiles/<name>/` (레포 예시) 또는 `~/.contents-factory/profiles/<name>/` (운영본).

| 파일 | 내용 |
|---|---|
| `persona.md` | 정체·말투·금지 표현·시그니처. system 프리픽스에 통째로 들어간다 |
| `brand.md` | 로고·색·폰트·레퍼런스 우선순위 |
| `voice.json` | `{provider, voice_id, model, stability, style}` |
| `soul.json` | `{provider, soul_id, ref_image, identity_rules}` |
| `platforms.json` | 플랫폼별 `{enabled, account_id, slots, late_policy, cta_rotation, daily_cap, connector_id, blog_id}` |
| `forbidden.txt` | 금지어 한 줄 1개 |
| `closers.txt` | 반복 감시할 고정 클로저 문구 |
| `writer_rules.md` | 추가 작성 규칙 (system 프리픽스) |
| `locale` | content_lang 기본값 |
| `credentials.env` | **런타임 홈에만.** chmod 600. 키 목록은 아래 |

`slots`는 `"12:00Z"` 꼴 UTC만 받는다.

## 🔑 외부 서비스 계정은 전부 본인 것 / Bring your own accounts

**이 레포에는 공유 계정이 없다.** 클론한다고 어떤 크레딧도 따라오지 않는다. 아래 서비스는
전부 사용자 본인이 가입하고, 본인이 결제하고, 본인 키를 넣는다. 설치 중 안내가 필요하면
`/setup` 스킬의 §0이 서비스별 가입 위치·로그인 명령·확인 명령을 다 들고 있다.

**Nothing here is a shared account.** Cloning this repo grants no credits on anything. Every
service below is signed up for, paid for and keyed by you. The `/setup` skill's §0 walks
through each one.

| 서비스 | 필요한 이유 | 없으면 | 키 이름 |
|---|---|---|---|
| [Higgsfield](https://higgsfield.ai) | 이미지·영상 생성, TikTok 게시 | 이미지·영상 전부 불가 | **키 없음 — `higgsfield auth login`** |
| [ElevenLabs](https://elevenlabs.io) | 내레이션 TTS, 발화 전사 | `tts` 단계·`stt_wpm` 게이트 불가 | `ELEVENLABS_API_KEY` |
| [Anthropic](https://console.anthropic.com) | 글 생성 | `gen text` 불가 | `ANTHROPIC_API_KEY` |
| [Supabase](https://supabase.com) | 미디어 공개 URL | 이미지·영상 발행 불가 (텍스트는 가능) | `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY` |
| [Meta](https://developers.facebook.com) | Threads·Instagram 발행 | 두 플랫폼 발행 불가 | `THREADS_*`, `IG_*` |
| [Google Cloud](https://console.cloud.google.com) | YouTube 업로드 | YouTube 발행 불가 | `YOUTUBE_*` |

값은 어떤 출력에도 나오지 않는다 (`--dry-run` 페이로드엔 `<REDACTED:KEY>`). 문서·커밋·로그에
값을 쓰지 않는다 — 키 **이름**만 쓴다.

### Higgsfield 로그인 / Higgsfield login

**붙여넣을 키가 없다.** Higgsfield 인증은 전부 CLI가 들고 있다 — `auth login`이 브라우저
OAuth(PKCE)를 돌리고, 토큰은 CLI 자기 크레덴셜 파일에 들어가고 갱신도 CLI가 한다.
팩토리는 토큰이 필요한 순간마다 `higgsfield auth token`을 물어서 그 자리에서 쓴다
(값은 로그·에러·`--dry-run` 어디에도 안 찍힌다).

```sh
curl -fsSL https://raw.githubusercontent.com/higgsfield-ai/cli/main/install.sh | sh
higgsfield auth login          # 브라우저가 열린다
higgsfield account status      # 이메일 · 플랜 · 잔여 크레딧
higgsfield auth token          # 팩토리가 호출 직전에 쓰는 그 명령 (값은 보지 말 것)
```

| 코드 | 뜻 | 대응 |
|---|---|---|
| `HIGGSFIELD_CLI_MISSING` | CLI가 `$PATH`에 없음 | 위 설치 명령 (Windows는 `.exe`도 탐색한다) |
| `HIGGSFIELD_LOGIN_REQUIRED` | `auth token`이 토큰을 안 줌 | `higgsfield auth login` |

There is no key to paste. `higgsfield auth login` runs the browser OAuth flow and the CLI
owns the token, including its refresh; the factory asks `higgsfield auth token` for the
current one immediately before each call that needs it.

크레딧 실측 (2026-10-14 `higgsfield generate cost`). 포맷 yaml의 `credit_cap_per_item`이
이 숫자 기준이다:

| 쓰임 | 모델 | 비용 | 포맷 캡 |
|---|---|---|---|
| OG·썸네일·상품컷·카드뉴스 | `gpt_image_2` | 6.5 cr/장 | 8 / 8 / 10 / 30 |
| 인물 스틸 (레퍼런스) | `nano_banana_pro` | 2 cr/장 | 영상 캡에 포함 |
| 인물 스틸 (Soul) | `text2image_soul_v2` | 0.12 cr/장 | 영상 캡에 포함 |
| 영상 테이크 720p | `seedance_2_0` | 4.5 cr/초 | `talking_head`·`pov` 150 |
| 영상 테이크 1080p | `seedance_2_0` | 12 cr/초 | 24초면 288 — 캡 초과 |

24초 토킹헤드 1편 ≈ **108 크레딧** (스틸 0.12 + 12초 테이크 2개). 유료 호출 전에 항상
견적을 받아 캡과 대조하고, 넘으면 **호출 자체를 하지 않는다** (`BUDGET_EXCEEDED`).
`--dry-run`은 과금 없이 단계별 견적만 찍는다.

### TikTok 커넥터 / TikTok connector

직접 TikTok API는 미심사 앱이면 비공개 게시만 되므로 Higgsfield MCP를 경유한다.
`tiktok_connect`로 본인 계정을 연결하고 `connector_id`는 `platforms.json`에 넣는다.
**크레덴셜은 넣을 게 없다.**

**인증은 CLI, 게시 호출만 MCP 엔드포인트다.** `higgsfield` CLI에는 `tiktok` 서브커맨드가
없어서(2026-10-15 `higgsfield --help` 실측) 게시는 `https://mcp.higgsfield.ai/mcp`의
`tiktok_prepare_publish`·`tiktok_publish`를 그대로 쓴다. 다만 bearer는 저장된 키가 아니라
**매 요청 직전 `higgsfield auth token`** 이다 — 예약 발행이 몇 시간 대기해도 그때 받은
토큰으로 나가므로 "기다리는 동안 토큰이 죽는" 경로가 없다. 그래서
`factory token refresh --platform tiktok` 같은 명령은 없고, 필요한 것도 없다.

만료가 아니라 **로그아웃**이면 `HIGGSFIELD_LOGIN_REQUIRED`로 멈추고 큐에 남는다
(아무것도 제출되지 않았음이 증명되므로 재시도 안전 코드다).

### ElevenLabs voice_id

Voice Design(글로 묘사해서 생성)이나 Instant Voice Cloning(본인 음성 샘플 복제, 유료
플랜 필요)으로 만든 뒤 Voice Lab에서 id를 복사해 프로필 `voice.json`에 넣는다. 모델은
`eleven_v3` — 구형 다국어 모델은 오프캠 내레이션이 로봇처럼 들린다. 플랜별 월 글자 쿼터는
공식 pricing 페이지를 보라. 24초 영상 1편 대본이 약 320자였고, 편당 상한은 포맷의
`limits.tts_chars_max`(기본 2000)다. 같은 문장 재합성은 캐시에 걸려 0자다.

### Meta: 본인 앱으로 가는 경우 / your own Meta app

남의 앱에 테스터로 들어가는 절차는 아래 "플랫폼 제약"과 `/setup` Step 4에 있다. 본인 앱을
만든다면: 앱 생성 → Threads API / Instagram Graph API 제품 추가 → 리다이렉트 URL 등록 →
본인 계정을 앱 역할에 추가. **개발 모드 앱은 본인 앱이어도 테스터 역할 계정만 OAuth가
된다** — 플랫폼 안에서 초대를 수락하는 단계는 똑같이 필요하다. 일반 공개 발행은 앱 심사가
필요하고 이 레포 범위 밖이다.

### YouTube: 본인 GCP / your own GCP project

`/setup` Step 6. 미검증 프로젝트는 업로드가 **private 고정**이다 — 검토 후 예약 공개에는
문제없지만 알고 시작해야 한다.

## credentials.env 키 / Credential keys

`~/.contents-factory/profiles/<name>/credentials.env`, 한 줄에 `KEY=value`, chmod 600.
값은 어떤 출력에도 안 나온다 (`--dry-run` 페이로드엔 `<REDACTED:KEY>`로 찍힌다).

**시크릿 아닌 계정 ID는 여기 두지 않는다** (§10 결정6). TikTok `connector_id`와 네이버
`blog_id`는 `platforms.json`에 있다 — 버전관리 대상이고, 없으면 `CONNECTOR_ID_MISSING` /
`BLOG_ID_MISSING`으로 멈춘다.

| 용도 | 필수 키 | 선택 키 |
|---|---|---|
| Threads | `THREADS_ACCESS_TOKEN`, `THREADS_USER_ID` | `THREADS_TOKEN_ISSUED_AT`(auth·refresh가 기록), `THREADS_APP_ID`, `THREADS_APP_SECRET`(있으면 로컬 교환), `THREADS_REDIRECT_URI` |
| Instagram | `IG_ACCESS_TOKEN`, `IG_USER_ID` | `IG_TOKEN_ISSUED_AT`, `IG_APP_ID`, `IG_APP_SECRET` |
| TikTok (Higgsfield MCP) | (없음 — `higgsfield auth login`) | - |
| YouTube | `YOUTUBE_CLIENT_ID`, `YOUTUBE_CLIENT_SECRET`, `YOUTUBE_REFRESH_TOKEN` | `YOUTUBE_ACCESS_TOKEN`(단기 폴백) |
| 네이버 블로그 | (토큰 없음) | `FACTORY_ASIDE_BIN`(기본 `aside`, aside 백엔드만), `FACTORY_PLAYWRIGHT_MODULE` |
| 미디어 호스팅 | `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY` | `SUPABASE_BUCKET`(기본 `contents-factory`) |
| 텍스트 생성 | `ANTHROPIC_API_KEY` | - |
| 텔레그램 미리보기·컨펌 | `TELEGRAM_BOT_TOKEN` (`factory telegram setup`이 기록) | - (chat_id는 `/start`로 자동, `state/telegram/chat.json`) |

Threads 토큰은 `factory auth threads --profile X`가 직접 넣는다 — 동의 URL 출력 → 리다이렉트된
전체 URL을 숨긴 프롬프트에 붙여넣기 → code 교환 → `/me` → `credentials.env`(chmod 600) 기록.
값은 어디에도 안 찍히고 핸들만 출력한다. `THREADS_APP_SECRET`이 로컬에 있으면 직접 교환하고,
없으면 `--exchange-url`(기본 `https://koreansaju.xyz/api/threads/exchange`)에
`{code, redirect_uri, app_id}`만 보내 long-lived를 받아온다. 토큰 grant에
`threads_content_publish`가 없으면 저장을 거부한다(`SCOPE_MISSING`). Instagram은 같은 구조의
스텁이라 동의 URL만 출력한다.

플랫폼 제약 (사실, 2026-10 기준):

- Threads/IG: Meta 앱이 필요하다. 개발 모드 앱은 테스터 역할 계정만 OAuth가 된다.
- Threads/IG의 IMAGE·VIDEO와 TikTok import 경로는 **공개 HTTPS URL**이 필요하다. 서버가
  직접 가져가므로 로컬 경로 업로드가 안 된다. `media_host`(Supabase) 또는 BYO로 채운다.
- TikTok 직접 API는 미심사 앱이면 비공개 게시만 된다. 그래서 Higgsfield MCP 경로를 쓴다.
- YouTube: 미검증 GCP 앱 업로드는 private 고정. `publishAt`은 private + 미래 시각일 때만 먹는다.
- 네이버: 공개 글쓰기 API가 없다. 로그인된 브라우저로 에디터를 조작한다 — macOS는 aside,
  Windows·Linux는 Playwright 영속 프로필(아래 "네이버 백엔드").

## 네이버 백엔드 / Naver backends

네이버는 공개 글쓰기 API가 없어서 **로그인된 브라우저**가 세션을 들고 있어야 한다.
그 브라우저가 둘이다. `platforms.json`의 `naver_blog.backend`로 고르고, 비워두면
`factory.toml`의 `os`가 정한다 (macos → `aside`, 그 외 → `playwright`).

| 백엔드 | 세션 보관 | OS | 로그인 |
|---|---|---|---|
| `aside` | aside 브라우저 (사용자가 이미 로그인된 창) | **macOS 15.0+ 전용** | aside에서 네이버 1회 로그인 |
| `playwright` | `~/.contents-factory/state/naver-profile/` 영속 프로필 | macOS·Windows·Linux | `factory naver login` |

aside는 macOS 전용이다 — 공식 문서가 "Aside supports macOS 15.0 or later"라고 못 박는다
(CLI 자체는 Windows 설치본이 있지만, CLI가 조작하는 **브라우저**가 macOS에만 있다).
그래서 Windows에서 네이버를 "미지원"으로 끝내지 않고 playwright 백엔드를 쓴다.

```jsonc
// platforms.json
"naver_blog": { "enabled": true, "blog_id": "yourblog", "backend": "playwright",
                "slots": ["12:00Z"] }
```

```sh
npm install -g playwright && npx playwright install chromium
factory naver login           # 창이 열린다. 아이디·비번·2차인증은 사용자가 직접 입력한다
factory naver status          # 세션 살아있나 (쿠키 값은 읽지 않고 불린만)
factory publish naver_blog --item q_... --dry-run
```

로그인은 **대신 쳐주지 않는다.** 창만 열고 네이버 세션 쿠키가 생기는지 폴링하다가
생기면 "확인됨"을 찍는다. 쿠키 값도 비밀번호도 어디에도 기록하지 않는다.

에디터 조작 코드(SmartEditor 문서 주입·컴포넌트 검증)는 두 백엔드가 **같은 소스**를 쓴다.
다른 것은 실행 래퍼와 발행 패널뿐이다 — aside는 접근성 트리 ref로, playwright는 role
로케이터로 같은 버튼을 집는다.

> ⚠️ **playwright 백엔드의 실발행 경로는 아직 실측되지 않았다.** `--dry-run`과
> `factory naver login`까지만 검증했다 (실게시 금지 조건으로 작업했다). 첫 실발행은
> 테스트 블로그에서 사람이 지켜보며 돌릴 것. 발행 버튼을 누른 뒤 실패하면
> `PUBLISH_OUTCOME_UNCERTAIN:<phase>`로 멈추고 자동 재시도하지 않는다.

## 미디어 호스팅 버킷 / BYO media bucket

Threads·Instagram의 IMAGE·VIDEO와 TikTok import 경로는 **플랫폼 서버가 직접 URL을 가져간다**.
그래서 공개 HTTPS URL이 필요하고, 그 URL은 **사용자 본인의 Supabase 프로젝트**에서 나온다
(§10 결정7 — 남의 버킷을 공유하지 않는다. 남의 프로젝트에 내 미디어를 올리면 그 쪽 요금·
수명·삭제 정책에 내 발행이 묶인다).

셋업:

1. Supabase 프로젝트를 만들고 Storage에서 **public** 버킷 1개를 만든다 (기본 이름
   `contents-factory`, 바꾸려면 `SUPABASE_BUCKET`).
2. `credentials.env`에 `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`를 넣는다.
   service role 키는 서버 전용이다 — 클라이언트·로그·커밋 어디에도 노출 금지.
3. 업로드 경로는 `<profile>/<queue_item_id>/<filename>`이고 공개 URL은
   `{SUPABASE_URL}/storage/v1/object/public/<bucket>/<path>`다.

**7일 수명 정책**: 발행이 끝난 미디어는 플랫폼이 자기 CDN에 복사해 두므로 버킷에 남겨둘
이유가 없다. 7일 뒤 지우는 걸 기본으로 한다 (발행 실패·재시도·검수 여유를 합친 값).

```sql
-- 버킷 정책 예시: public read, 쓰기는 service role만
create policy "public read"
  on storage.objects for select
  using (bucket_id = 'contents-factory');

-- 7일 지난 객체 정리 (pg_cron). Storage 는 자동 수명 정책이 없어서 잡으로 돈다.
select cron.schedule(
  'contents-factory-media-ttl', '17 4 * * *',
  $$delete from storage.objects
     where bucket_id = 'contents-factory'
       and created_at < now() - interval '7 days'$$
);
```

버킷을 직접 쓰지 않을 거면 `media[].public_url`을 큐 적재 시점에 채워 넣으면 된다.
그 경우 `media_host`는 아무것도 하지 않고 그 URL을 그대로 쓴다 (BYO 호스팅).

## 원장 / Ledger

`~/.contents-factory/ledger.jsonl`, 한 줄 1건, append only.

- **돈이 드는 호출은 전부 `ledger.charge()` 한 곳**으로 간다. `op`(작업 키)가 필수이고,
  값은 프로바이더 작업 id(`higgsfield:<job_id>`)다. 같은 `op` 가 이미 있으면 안 쓴다 —
  단계 재실행·크래시 후 재개·복구 스크립트 재생이 전부 1회로 수렴한다.
- 작업 id 를 못 받은 호출은 `PROVIDER_JOB_ID_MISSING` 으로 멈춘다. 키 없는 과금은
  같은 작업을 두 번 청구하는 경로라서 추정하지 않는다.
- 단계 롤업·발행·토큰 갱신은 과금이 아니라 `record()` 로 가고 `credits=0`·
  `detail.rollup=true` 다. 영상 단계 롤업이 크레딧까지 들고 있으면 유료 단계가 전부
  두 배가 된다.
- `factory ledger summary` 와 단계별 캡(`generators/budget.py`)은 같은 파일을 읽는다.

⚠️ 2026-10-14 이전 행에는 `op` 가 없다. 그 구간의 image 13.0cr 은 같은 작업
`de1dac52…` 가 두 번 기록된 것이고 실제는 6.5cr 이다.

## 프롬프트 캐싱 / Prompt caching

`workspace/LLM-API-캐싱-규칙.md` + P0-SPEC §10 결정4 준수.

system 프리픽스 = `persona.md` → `docs/rules/<kind>-rules.md`의 절차·게이트·금지 절 →
포맷 `prompt_template` → 출력 스키마 순서다. 안정적인 것이 앞이라 포맷을 바꿔도 뒤쪽만
무효화된다. 날짜·슬롯·앵글 같은 가변값은 전부 user 메시지로 간다.

**패딩은 하지 않는다.** sonnet-5 최소 프리픽스는 1,024토큰이고, 미달이면 마커를 아예 안
붙이고 원장에 `cache:skipped`를 남긴다(마커만 달면 조용히 무시되므로 기록이 남는 쪽이 낫다).
`factory gen text --dry-run`이 `cache`와 프리픽스 추정 토큰을 찍는다. 추정은 CJK를 글자당
0.7토큰, 라틴을 0.27토큰으로 센다. 2회째 호출에 `cache_read_input_tokens`가 0이면 생성기가
WARN을 남긴다.

## 레이아웃 / Layout

```
factory/
  cli.py __main__.py config.py credentials.py errors.py ledger.py
  i18n/{ko,en}.json           인터뷰·리포트·위저드 문구 (영문 점표기 키)
  i18n/errors.{ko,en}.json    코드별 {human, action, command} 사전
  explain.py                  factory explain — 사전 읽기, 미등록 코드 폴백
  start.py                    factory start — doctor → 에이전트 감지·실행
  wizard/{questions,engine,apply,state,classify,templates,cli}.py
                              컴맹 플로우. 질문 카탈로그·상태머신·부수효과·상태파일
  auth/{base,threads,instagram}.py   OAuth 동의 URL·code 교환·credentials 기록
  spec/{brief,profile,queue,format}.py
  formats/{text,image,video}/*.yaml
  generators/text.py          Anthropic claude-sonnet-5 + 캐싱
  gates/{repeat_guard,forbidden_words,limits,caption_safe_zone,cta_single,duplicate_copy}.py
  generators/image.py         higgsfield CLI 래퍼 (argv 조립 + dry-run)
  rules.py                    docs/rules 절 단위 리더 (캐싱 프리픽스용)
  queue/{store,cli}.py        큐 CRUD 단일 소유자
  scheduler/{tick,launchd,cron,token_refresh}.py
  publishers/{base,threads,instagram,tiktok,youtube,naver_blog,media_host}.py
  publishers/naver/*.mjs      벤더링한 에디터 조작 (publish·document·md·cors)
  publishers/naver/driver_playwright.mjs  playwright 영속 프로필 백엔드 + 로그인 창
  publishers/naver/login.mjs  factory naver login|status 엔트리
  osinfo.py higgsfield.py doctor.py       OS 분기 · CLI 인증 · 호스트 점검
  scheduler/windows.py        Windows Task Scheduler (schtasks)
  telegram/{api,state,setup,serve,notify,daemon,cli}.py   폰 미리보기·컨펌·알림 정책
  review/{terminal,telegram}.py   리뷰 채널 (factory.toml review = 터미널 | 텔레그램)
  launcher.py                 바탕화면 콘텐츠공장 런처 (.command/.bat/.sh)
  remote.py                   에이전트 폰 원격제어 상시 등록 + 워크스페이스 신뢰 게이트
install.sh install.ps1        한 줄 설치 (멱등, ko/en, --dry-run)
profiles/_example/            영문 예시 페르소나 (사주 아님)
.claude/skills/wizard/SKILL.md          컴맹 입구 (/wizard). 나머지 6종은 전문가 모드
.claude/skills/{setup,profile,writing-factory,image-factory,video-factory,publish}/SKILL.md
CLAUDE.md AGENTS.md           에이전트 중립 계약 (같은 내용, 60줄 이내)
tests/
```

## 이식 출처 / Ported from

| 팩토리 | 원본 |
|---|---|
| `gates/repeat_guard.py` | `~/.saju-threads/scripts/repeat_guard.py` |
| `gates/{vision,limb_motion,bg_motion}.py` | `~/.saju-threads/reelv1/gates/{limb_motion,bg_motion}.py` |
| `gates/identity.py` | `reelv1` eyemetric + saju-threads `docs/video-briefs/STILL-GATE.md` §B-7 |
| `gates/stt_wpm.py` | `reelv1/stt.py`, `viewer_voice.py` |
| `generators/video/script.py` | `docs/video-briefs/prompts-2026-09-15/gen.py --check` |
| `generators/video/{tts,captions,assemble}.py` | `reelv1/{tts,captions,textrender_safe,assemble}.py` |
| `publishers/threads.py` | `publish.sh`, `threads_delivery.py` |
| `publishers/instagram.py` | `ig_publish.sh`, `ig_preview.sh` |
| `publishers/tiktok.py` | `tiktok_publish.py` |
| `publishers/youtube.py` | `yt_upload.py`, `yt_oauth.py`, `platform_cta.py` |
| `publishers/naver/*` | `pilot/scripts/naver/{publish,md_to_document}.mjs`, `lib/{aside,document}.mjs` |
| `auth/threads.py` | `oauth_exchange.sh`, `profile_add.sh --exchange` |
| `scheduler/token_refresh.py` | `token_refresh.sh` |
| `queue/`, `scheduler/tick.py` | `queue_autopub.py`, `com.saju.publish.plist` |
| `generators/text.py` | `persona_writer.py` |
| `telegram/{serve,state}.py` | `~/.saju-threads/scripts/{ops_poll,drop_serve}.py` (getUpdates 오프셋·백로그 스킵·단일 컨슈머) |
| `review/telegram.py` | `scripts/tg_review.py`, `ig_lib.sh tg_video_review` (sendVideo 인라인 규약·64바이트 콜백) |

원 레포는 읽기 전용 참조다. 운영 파이프라인은 건드리지 않는다.
