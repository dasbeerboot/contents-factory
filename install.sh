#!/bin/sh
# contents-factory one-line installer (macOS and Linux).
#
#   curl -fsSL https://raw.githubusercontent.com/dasbeerboot/contents-factory/main/install.sh | sh
#
# Options are accepted when the script is piped, with `sh -s --`:
#   curl -fsSL .../install.sh | sh -s -- --lang en --no-start
#
#   --lang ko|en     output language (default: $LANG, Korean unless it says en)
#   --dir PATH       where to clone (default: ~/contents-factory)
#   --naver          install Playwright Chromium without asking
#   --no-naver       skip Playwright without asking
#   --no-start       set everything up but do not launch `factory start`
#   --dry-run        print every command instead of running it
#
# Every step is idempotent: anything already installed is skipped, an existing
# clone is fast-forwarded rather than re-cloned, and re-running after a failure
# resumes from where it stopped. On failure the step number is named, because
# "it broke somewhere in a 60-line script" is not a thing a user can report.
#
# Testability: `FACTORY_INSTALL_LIB=1 . ./install.sh` loads the functions without
# running anything, which is how the skip logic is unit tested.

set -eu

REPO_URL="${FACTORY_REPO_URL:-https://github.com/dasbeerboot/contents-factory.git}"
REPO_DIR="${FACTORY_REPO_DIR:-$HOME/contents-factory}"
DRY_RUN="${FACTORY_INSTALL_DRY_RUN:-0}"
WANT_NAVER=""
WANT_START=1
STEP=""
STEP_TOTAL=11
LANG_CHOICE=""

# --------------------------------------------------------------------- messages

text() {
  case "$LANG_CHOICE:$1" in
    ko:banner)     echo "콘텐츠공장 설치를 시작한다. 관리자 권한은 필요 없다." ;;
    en:banner)     echo "Installing the content factory. No administrator rights needed." ;;
    ko:step)       echo "[%s/%s] %s" ;;
    en:step)       echo "[%s/%s] %s" ;;
    ko:skip)       echo "  이미 있음, 건너뜀: %s" ;;
    en:skip)       echo "  already present, skipping: %s" ;;
    ko:doing)      echo "  설치 중: %s" ;;
    en:doing)      echo "  installing: %s" ;;
    ko:preflight)  echo "환경 점검" ;;
    en:preflight)  echo "Preflight" ;;
    ko:pkgmgr)     echo "패키지 매니저" ;;
    en:pkgmgr)     echo "Package manager" ;;
    ko:tools)      echo "도구 설치 (uv, ffmpeg, node, higgsfield)" ;;
    en:tools)      echo "Tools (uv, ffmpeg, node, higgsfield)" ;;
    ko:naver)      echo "네이버 블로그용 브라우저" ;;
    en:naver)      echo "Browser for Naver blog" ;;
    ko:clone)      echo "레포 받기" ;;
    en:clone)      echo "Repository" ;;
    ko:sync)       echo "파이썬 의존성" ;;
    en:sync)       echo "Python dependencies" ;;
    ko:initstep)   echo "설정 초기화" ;;
    en:initstep)   echo "Initialise the config" ;;
    ko:agent)      echo "에이전트 확인" ;;
    en:agent)      echo "Agent" ;;
    ko:launcher)   echo "바탕화면 런처" ;;
    en:launcher)   echo "Desktop launcher" ;;
    ko:sleep)      echo "잠자기 해제" ;;
    en:sleep)      echo "Stop the machine sleeping" ;;
    ko:start)      echo "실행" ;;
    en:start)      echo "Start" ;;
    ko:os_bad)     echo "이 스크립트는 macOS와 리눅스용이다. Windows는 install.ps1 을 써라." ;;
    en:os_bad)     echo "This script is for macOS and Linux. On Windows use install.ps1." ;;
    ko:root_bad)   echo "root로 실행하지 마라. 그냥 본인 계정으로 다시 돌려라 (sudo 없이)." ;;
    en:root_bad)   echo "Do not run this as root. Re-run it as yourself, without sudo." ;;
    ko:host)       echo "  %s / %s 확인" ;;
    en:host)       echo "  %s / %s detected" ;;
    ko:brew_new)   echo "  Homebrew가 없다. 설치한다. 도중에 맥 로그인 비밀번호를 한 번 물을 수 있다." ;;
    en:brew_new)   echo "  Homebrew is missing; installing it. It may ask once for your Mac password." ;;
    ko:pkg_none)   echo "  apt/dnf/pacman 중 아무것도 없다. ffmpeg와 node는 직접 깔아야 한다." ;;
    en:pkg_none)   echo "  none of apt/dnf/pacman found; install ffmpeg and node yourself." ;;
    ko:hf_manual)  echo "  higgsfield CLI 자동 설치 실패. 수동 설치: https://github.com/higgsfield-ai/cli" ;;
    en:hf_manual)  echo "  higgsfield CLI auto-install failed. Install it by hand: https://github.com/higgsfield-ai/cli" ;;
    ko:naver_ask)  echo "  네이버 블로그에도 발행할 건가? 브라우저를 하나 더 받는다 (약 150MB). [y/N] " ;;
    en:naver_ask)  echo "  Will you publish to Naver blog too? That needs one more browser (~150MB). [y/N] " ;;
    ko:naver_skip) echo "  건너뜀. 나중에 필요하면 npx playwright install chromium 을 돌려라." ;;
    en:naver_skip) echo "  Skipped. Run npx playwright install chromium later if you need it." ;;
    ko:pull)       echo "  기존 폴더를 최신으로 맞춘다: %s" ;;
    en:pull)       echo "  fast-forwarding the existing clone: %s" ;;
    ko:pull_bad)   echo "  변경사항이 있어 git pull --ff-only 가 거절됐다. 그대로 두고 계속한다: %s" ;;
    en:pull_bad)   echo "  git pull --ff-only refused (local changes). Leaving it alone and continuing: %s" ;;
    ko:cloning)    echo "  받는 중: %s" ;;
    en:cloning)    echo "  cloning into %s" ;;
    ko:agent_ok)   echo "  에이전트 있음: %s" ;;
    en:agent_ok)   echo "  agent found: %s" ;;
    ko:agent_new)  echo "  claude도 codex도 없다. Claude Code를 설치한다." ;;
    en:agent_new)  echo "  neither claude nor codex found; installing Claude Code." ;;
    ko:agent_bad)  echo "  에이전트 설치 실패. 수동: npm i -g @anthropic-ai/claude-code" ;;
    en:agent_bad)  echo "  agent install failed. By hand: npm i -g @anthropic-ai/claude-code" ;;
    ko:sleep_mac)  echo "  잠자기 설정 창을 연다. 디스플레이가 꺼져도 잠들지 않게 켜라. 폰에서 조작하려면 PC가 깨어 있어야 한다." ;;
    en:sleep_mac)  echo "  Opening the sleep settings. Turn off automatic sleep: the phone can only reach a PC that is awake." ;;
    ko:sleep_lin)  echo "  잠자기를 끄려면: sudo systemctl mask sleep.target suspend.target" ;;
    en:sleep_lin)  echo "  To stop sleeping: sudo systemctl mask sleep.target suspend.target" ;;
    ko:done)       echo "설치 끝. 다음부터는 바탕화면의 콘텐츠공장을 더블클릭하면 된다." ;;
    en:done)       echo "Done. From now on, double-click 콘텐츠공장 on your desktop." ;;
    ko:no_start)   echo "실행은 건너뛴다. 바탕화면의 콘텐츠공장을 더블클릭하거나 cd %s 후 uv run factory start." ;;
    en:no_start)   echo "Not starting. Double-click 콘텐츠공장 on your desktop, or cd %s and run uv run factory start." ;;
    ko:failed)     echo "[%s/%s] %s 에서 실패했다 (종료코드 %s). 고친 뒤 같은 명령을 그대로 다시 돌려라. 이미 끝난 단계는 건너뛴다." ;;
    en:failed)     echo "Failed at step [%s/%s] %s (exit %s). Fix it and re-run the same command: finished steps are skipped." ;;
    *)             echo "%s" ;;
  esac
}

say() {
  key="$1"
  shift
  format="$(text "$key")"
  # shellcheck disable=SC2059 - the format string is ours, only %s placeholders
  printf "$format\n" "$@"
}

pick_lang() {
  case "${1:-}" in
    ko|en) LANG_CHOICE="$1" ;;
    *)
      case "${LANG:-}${LC_ALL:-}" in
        *ko*|*KO*) LANG_CHOICE="ko" ;;
        "")        LANG_CHOICE="ko" ;;
        *)         LANG_CHOICE="en" ;;
      esac
      ;;
  esac
}

# ---------------------------------------------------------------------- helpers

have() { command -v "$1" >/dev/null 2>&1; }

run() {
  if [ "$DRY_RUN" = "1" ]; then
    printf 'DRY  %s\n' "$*"
    return 0
  fi
  "$@"
}

# Commands that must run inside the clone. In a dry run the directory may not
# exist yet, so the `cd` is printed rather than attempted.
run_in_repo() {
  if [ "$DRY_RUN" = "1" ]; then
    printf 'DRY  (cd %s) %s\n' "$REPO_DIR" "$*"
    return 0
  fi
  ( cd "$REPO_DIR" && "$@" )
}

# For pipelines (`curl | sh`), which cannot be passed to `run` as argv.
run_sh() {
  if [ "$DRY_RUN" = "1" ]; then
    printf 'DRY  %s\n' "$1"
    return 0
  fi
  sh -c "$1"
}

step() {
  STEP="$1"
  printf "$(text step)\n" "$1" "$STEP_TOTAL" "$(text "$2")"
}

# `curl ... | sh` leaves stdin pointing at the script, so a question has to be
# read from the terminal directly. With no terminal the default answer wins.
ask_yes() {
  prompt="$1"
  if [ ! -r /dev/tty ] || [ "$DRY_RUN" = "1" ]; then
    return 1
  fi
  printf '%s' "$prompt" > /dev/tty
  read -r reply < /dev/tty || return 1
  case "$reply" in
    y|Y|yes|YES|예|ㅇ|o|O) return 0 ;;
    *) return 1 ;;
  esac
}

on_fail() {
  say failed "$STEP" "$STEP_TOTAL" "$CURRENT_LABEL" "$1"
}

# ------------------------------------------------------------------- step 1: os

detect_host() {
  case "$(uname -s)" in
    Darwin) HOST_OS="macos" ;;
    Linux)  HOST_OS="linux" ;;
    *)      HOST_OS="" ;;
  esac
  HOST_ARCH="$(uname -m)"
}

preflight() {
  detect_host
  if [ -z "$HOST_OS" ]; then
    say os_bad
    return 1
  fi
  # Running the installer as root puts a root-owned clone and a root-owned
  # Homebrew in a user's home, which then fails for the user.
  if [ "$(id -u)" = "0" ]; then
    say root_bad
    return 1
  fi
  say host "$HOST_OS" "$HOST_ARCH"
}

# ----------------------------------------------------------- step 2: package mgr

# Homebrew lives at a fixed absolute path (arm64 and Intel differ), so this is
# where it is looked for rather than on PATH — a fresh shell after the installer
# has not sourced the profile yet. Overridable so a host that already has brew
# can still exercise the missing-brew branch.
BREW_PATHS="${FACTORY_BREW_PATHS:-/opt/homebrew/bin/brew /usr/local/bin/brew}"

brew_shellenv() {
  for candidate in $BREW_PATHS; do
    if [ -x "$candidate" ]; then
      eval "$("$candidate" shellenv)"
      return 0
    fi
  done
  return 1
}

ensure_package_manager() {
  if [ "$HOST_OS" = "macos" ]; then
    if have brew; then
      say skip "homebrew"
      return 0
    fi
    # An existing install that is simply not on PATH is not a reinstall.
    if brew_shellenv && have brew; then
      say skip "homebrew"
      return 0
    fi
    say brew_new
    run_sh 'NONINTERACTIVE=1 /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"'
    brew_shellenv || true
    return 0
  fi
  for manager in apt-get dnf pacman; do
    if have "$manager"; then
      LINUX_PKG="$manager"
      say skip "$manager"
      return 0
    fi
  done
  LINUX_PKG=""
  say pkg_none
}

pkg_install() {
  case "${LINUX_PKG:-}" in
    apt-get) run sudo apt-get install -y "$@" ;;
    dnf)     run sudo dnf install -y "$@" ;;
    pacman)  run sudo pacman -S --noconfirm "$@" ;;
    *)       return 1 ;;
  esac
}

# ---------------------------------------------------------------- step 3: tools

ensure_uv() {
  if have uv; then
    say skip "uv"
    return 0
  fi
  if [ -x "$HOME/.local/bin/uv" ]; then
    PATH="$HOME/.local/bin:$PATH"
    export PATH
    say skip "uv"
    return 0
  fi
  say doing "uv"
  run_sh 'curl -LsSf https://astral.sh/uv/install.sh | sh'
  PATH="$HOME/.local/bin:$PATH"
  export PATH
}

ensure_ffmpeg() {
  if have ffmpeg && have ffprobe; then
    say skip "ffmpeg"
    return 0
  fi
  say doing "ffmpeg"
  if [ "$HOST_OS" = "macos" ]; then
    run brew install ffmpeg
  else
    pkg_install ffmpeg || true
  fi
}

ensure_node() {
  if have node && have npm; then
    say skip "node"
    return 0
  fi
  say doing "node"
  if [ "$HOST_OS" = "macos" ]; then
    run brew install node
  else
    pkg_install nodejs npm || true
  fi
}

ensure_git() {
  if have git; then
    say skip "git"
    return 0
  fi
  say doing "git"
  if [ "$HOST_OS" = "macos" ]; then
    run brew install git
  else
    pkg_install git || true
  fi
}

ensure_higgsfield() {
  if have higgsfield; then
    say skip "higgsfield"
    return 0
  fi
  say doing "higgsfield"
  run_sh 'curl -fsSL https://raw.githubusercontent.com/higgsfield-ai/cli/main/install.sh | sh' \
    || say hf_manual
}

ensure_tools() {
  ensure_uv
  ensure_git
  ensure_ffmpeg
  ensure_node
  ensure_higgsfield
}

# ------------------------------------------------------------- step 4: naver

ensure_playwright() {
  if [ "$WANT_NAVER" = "0" ]; then
    say naver_skip
    return 0
  fi
  if [ -z "$WANT_NAVER" ]; then
    if ask_yes "$(text naver_ask)"; then
      WANT_NAVER=1
    else
      WANT_NAVER=0
      say naver_skip
      return 0
    fi
  fi
  if ! have npm; then
    say naver_skip
    return 0
  fi
  say doing "playwright chromium"
  run npm install -g playwright
  run npx playwright install chromium
}

# -------------------------------------------------------------- step 5: the repo

ensure_repo() {
  if [ -d "$REPO_DIR/.git" ]; then
    say pull "$REPO_DIR"
    # --ff-only on purpose: a user who edited their clone keeps their edits and
    # gets told, rather than having them merged or stashed behind their back.
    run_in_repo git pull --ff-only || say pull_bad "$REPO_DIR"
    return 0
  fi
  if [ -e "$REPO_DIR" ]; then
    say skip "$REPO_DIR"
    return 0
  fi
  say cloning "$REPO_DIR"
  run git clone "$REPO_URL" "$REPO_DIR"
}

# ----------------------------------------------------- steps 6-7: sync and init

ensure_sync() {
  run_in_repo uv sync
}

ensure_init() {
  run_in_repo uv run factory init --os "$HOST_OS"
}

# -------------------------------------------------------------- step 8: an agent

ensure_agent() {
  for agent in claude codex; do
    if have "$agent"; then
      say agent_ok "$agent"
      return 0
    fi
  done
  say agent_new
  if ! have npm; then
    say agent_bad
    return 0
  fi
  run npm install -g @anthropic-ai/claude-code || say agent_bad
}

# ------------------------------------------------------------ step 9: launcher

ensure_launcher() {
  run_in_repo uv run factory launcher create --os "$HOST_OS"
}

# --------------------------------------------------------------- step 10: sleep

sleep_settings() {
  if [ "$HOST_OS" = "macos" ]; then
    say sleep_mac
    run_sh 'open "x-apple.systempreferences:com.apple.preference.energysaver"' \
      || run_sh 'open "x-apple.systempreferences:com.apple.Battery-Settings.extension"' \
      || true
    return 0
  fi
  say sleep_lin
}

# --------------------------------------------------------------- step 11: start

start_factory() {
  if [ "$WANT_START" != "1" ]; then
    say no_start "$REPO_DIR"
    return 0
  fi
  say done
  run_in_repo uv run factory start
}

# ------------------------------------------------------------------------- main

parse_args() {
  lang_flag=""
  while [ "$#" -gt 0 ]; do
    case "$1" in
      --lang)     lang_flag="${2:-}"; shift 2 ;;
      --lang=*)   lang_flag="${1#--lang=}"; shift ;;
      --dir)      REPO_DIR="${2:-$REPO_DIR}"; shift 2 ;;
      --dir=*)    REPO_DIR="${1#--dir=}"; shift ;;
      --naver)    WANT_NAVER=1; shift ;;
      --no-naver) WANT_NAVER=0; shift ;;
      --no-start) WANT_START=0; shift ;;
      --dry-run)  DRY_RUN=1; shift ;;
      *)          shift ;;
    esac
  done
  pick_lang "$lang_flag"
}

main() {
  parse_args "$@"
  CURRENT_LABEL="preflight"
  trap 'status=$?; [ "$status" -eq 0 ] || on_fail "$status"' EXIT
  say banner

  CURRENT_LABEL="$(text preflight)"; step 1 preflight;  preflight
  CURRENT_LABEL="$(text pkgmgr)";    step 2 pkgmgr;     ensure_package_manager
  CURRENT_LABEL="$(text tools)";     step 3 tools;      ensure_tools
  CURRENT_LABEL="$(text naver)";     step 4 naver;      ensure_playwright
  CURRENT_LABEL="$(text clone)";     step 5 clone;      ensure_repo
  CURRENT_LABEL="$(text sync)";      step 6 sync;       ensure_sync
  CURRENT_LABEL="$(text initstep)";  step 7 initstep;   ensure_init
  CURRENT_LABEL="$(text agent)";     step 8 agent;      ensure_agent
  CURRENT_LABEL="$(text launcher)";  step 9 launcher;   ensure_launcher
  CURRENT_LABEL="$(text sleep)";     step 10 sleep;     sleep_settings
  CURRENT_LABEL="$(text start)";     step 11 start;     start_factory
}

if [ "${FACTORY_INSTALL_LIB:-0}" != "1" ]; then
  main "$@"
fi
