<#
.SYNOPSIS
contents-factory one-line installer for Windows.

.DESCRIPTION
    irm https://raw.githubusercontent.com/dasbeerboot/contents-factory/main/install.ps1 | iex

With options, download first (a piped script cannot take parameters):

    irm https://raw.githubusercontent.com/dasbeerboot/contents-factory/main/install.ps1 -OutFile i.ps1
    ./i.ps1 -Lang en -NoStart

Every step is idempotent: anything already installed is skipped, an existing
clone is fast-forwarded rather than re-cloned, and re-running after a failure
resumes where it stopped. On failure the step number is named.

Nothing here needs an elevated prompt. winget installs into the user scope, uv
goes to %USERPROFILE%\.local\bin, and the scheduled task that `factory remote
install` registers runs at LeastPrivilege.

Testability: `. ./install.ps1 -Lib` dot-sources the functions without running
anything, which is how the skip logic is unit tested.
#>
[CmdletBinding()]
param(
    [ValidateSet('ko', 'en')] [string] $Lang,
    [string] $Dir,
    [switch] $Naver,
    [switch] $NoNaver,
    [switch] $NoStart,
    [switch] $DryRun,
    # Dot-source for tests: define the functions, run nothing.
    [switch] $Lib
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$script:StepTotal = 11
$script:Step = 0
$script:StepLabel = ''
$script:RepoUrl = if ($env:FACTORY_REPO_URL) { $env:FACTORY_REPO_URL }
                  else { 'https://github.com/dasbeerboot/contents-factory.git' }
$script:RepoDir = if ($Dir) { $Dir }
                  elseif ($env:FACTORY_REPO_DIR) { $env:FACTORY_REPO_DIR }
                  else { Join-Path $env:USERPROFILE 'contents-factory' }
$script:DryRun = $DryRun.IsPresent -or ($env:FACTORY_INSTALL_DRY_RUN -eq '1')
$script:WantNaver = if ($Naver) { $true } elseif ($NoNaver) { $false } else { $null }
$script:WantStart = -not $NoStart.IsPresent
$script:UvDir = Join-Path $env:USERPROFILE '.local\bin'

# --------------------------------------------------------------------- messages

$script:Messages = @{
    ko = @{
        banner     = '콘텐츠공장 설치를 시작한다. 관리자 권한은 필요 없다.'
        skip       = '  이미 있음, 건너뜀: {0}'
        doing      = '  설치 중: {0}'
        preflight  = '환경 점검'
        pkgmgr     = '패키지 매니저 (winget)'
        tools      = '도구 설치 (uv, git, ffmpeg, node, higgsfield)'
        naver      = '네이버 블로그용 브라우저'
        clone      = '레포 받기'
        sync       = '파이썬 의존성'
        initstep   = '설정 초기화'
        agent      = '에이전트 확인'
        launcher   = '바탕화면 런처'
        sleepstep  = '잠자기 해제'
        start      = '실행'
        host       = '  Windows / {0} 확인'
        osbad      = '이 스크립트는 Windows용이다. macOS·리눅스는 install.sh 를 써라.'
        wingetbad  = '  winget이 없다. Microsoft Store에서 "앱 설치 관리자"를 설치한 뒤 다시 돌려라.'
        hfmanual   = '  higgsfield CLI는 자동 설치가 없다. 공식 안내를 따라 설치해라: https://github.com/higgsfield-ai/cli'
        naverask   = '  네이버 블로그에도 발행할 건가? 브라우저를 하나 더 받는다 (약 150MB). [y/N] '
        naverskip  = '  건너뜀. 나중에 필요하면 npx playwright install chromium 을 돌려라.'
        pull       = '  기존 폴더를 최신으로 맞춘다: {0}'
        pullbad    = '  변경사항이 있어 git pull --ff-only 가 거절됐다. 그대로 두고 계속한다: {0}'
        cloning    = '  받는 중: {0}'
        agentok    = '  에이전트 있음: {0}'
        agentnew   = '  claude도 codex도 없다. Claude Code를 설치한다.'
        agentbad   = '  에이전트 설치 실패. 수동: npm i -g @anthropic-ai/claude-code'
        sleephint  = '  잠자기를 끄려면 다음을 실행해라 (관리자 창 필요 없음): powercfg /change standby-timeout-ac 0'
        donemsg    = '설치 끝. 다음부터는 바탕화면의 콘텐츠공장을 더블클릭하면 된다.'
        nostart    = '실행은 건너뛴다. 바탕화면의 콘텐츠공장을 더블클릭하거나 cd {0} 후 uv run factory start.'
        failed     = '[{0}/{1}] {2} 에서 실패했다. 고친 뒤 같은 명령을 그대로 다시 돌려라. 이미 끝난 단계는 건너뛴다. ({3})'
    }
    en = @{
        banner     = 'Installing the content factory. No administrator rights needed.'
        skip       = '  already present, skipping: {0}'
        doing      = '  installing: {0}'
        preflight  = 'Preflight'
        pkgmgr     = 'Package manager (winget)'
        tools      = 'Tools (uv, git, ffmpeg, node, higgsfield)'
        naver      = 'Browser for Naver blog'
        clone      = 'Repository'
        sync       = 'Python dependencies'
        initstep   = 'Initialise the config'
        agent      = 'Agent'
        launcher   = 'Desktop launcher'
        sleepstep  = 'Stop the machine sleeping'
        start      = 'Start'
        host       = '  Windows / {0} detected'
        osbad      = 'This script is for Windows. On macOS or Linux use install.sh.'
        wingetbad  = '  winget is missing. Install "App Installer" from the Microsoft Store, then re-run this.'
        hfmanual   = '  The higgsfield CLI has no unattended Windows installer. Follow the official instructions: https://github.com/higgsfield-ai/cli'
        naverask   = '  Will you publish to Naver blog too? That needs one more browser (~150MB). [y/N] '
        naverskip  = '  Skipped. Run npx playwright install chromium later if you need it.'
        pull       = '  fast-forwarding the existing clone: {0}'
        pullbad    = '  git pull --ff-only refused (local changes). Leaving it alone and continuing: {0}'
        cloning    = '  cloning into {0}'
        agentok    = '  agent found: {0}'
        agentnew   = '  neither claude nor codex found; installing Claude Code.'
        agentbad   = '  agent install failed. By hand: npm i -g @anthropic-ai/claude-code'
        sleephint  = '  To stop the machine sleeping (no elevation needed): powercfg /change standby-timeout-ac 0'
        donemsg    = 'Done. From now on, double-click 콘텐츠공장 on your desktop.'
        nostart    = 'Not starting. Double-click 콘텐츠공장 on your desktop, or cd {0} and run uv run factory start.'
        failed     = 'Failed at step [{0}/{1}] {2}. Fix it and re-run the same command: finished steps are skipped. ({3})'
    }
}

function Resolve-FactoryLang {
    param([string] $Requested)
    if ($Requested) { return $Requested }
    # The UI culture is the honest signal on Windows; $env:LANG is rarely set.
    $culture = ''
    try { $culture = [System.Globalization.CultureInfo]::CurrentUICulture.Name } catch { $culture = '' }
    if ($env:LANG) { $culture = "$culture $($env:LANG)" }
    if ($culture -match 'ko') { return 'ko' }
    if (-not $culture) { return 'ko' }
    return 'en'
}

$script:Lang = Resolve-FactoryLang -Requested $Lang

function Get-FactoryText {
    param([Parameter(Mandatory)] [string] $Key)
    $table = $script:Messages[$script:Lang]
    if ($table.ContainsKey($Key)) { return $table[$Key] }
    return $Key
}

function Write-FactorySay {
    param([Parameter(Mandatory)] [string] $Key, [object[]] $Values = @())
    $format = Get-FactoryText -Key $Key
    if ($Values.Count -gt 0) { Write-Host ($format -f $Values) } else { Write-Host $format }
}

function Write-FactoryStep {
    param([Parameter(Mandatory)] [int] $Number, [Parameter(Mandatory)] [string] $Key)
    $script:Step = $Number
    $script:StepLabel = Get-FactoryText -Key $Key
    Write-Host ("[{0}/{1}] {2}" -f $Number, $script:StepTotal, $script:StepLabel)
}

# ---------------------------------------------------------------------- helpers

function Test-FactoryCommand {
    param([Parameter(Mandatory)] [string] $Name)
    return [bool] (Get-Command $Name -ErrorAction SilentlyContinue)
}

function Invoke-FactoryRun {
    param([Parameter(Mandatory)] [string] $File, [string[]] $Arguments = @(),
          [string] $WorkingDirectory)
    $line = "$File $($Arguments -join ' ')"
    if ($script:DryRun) {
        if ($WorkingDirectory) { Write-Host "DRY  (cd $WorkingDirectory) $line" }
        else { Write-Host "DRY  $line" }
        return 0
    }
    $previous = $null
    if ($WorkingDirectory) {
        $previous = (Get-Location).Path
        Set-Location $WorkingDirectory
    }
    try {
        & $File @Arguments
        # A native command sets $LASTEXITCODE; StrictMode would fault on reading
        # it if nothing ever did, so it is fetched defensively.
        $code = Get-Variable -Name 'LASTEXITCODE' -ValueOnly -ErrorAction SilentlyContinue
        if ($null -eq $code) { return 0 }
        return $code
    } finally {
        if ($previous) { Set-Location $previous }
    }
}

function Add-FactoryPath {
    param([Parameter(Mandatory)] [string] $Directory)
    if ($env:Path -notlike "*$Directory*") { $env:Path = "$Directory;$env:Path" }
}

# A piped script (`irm | iex`) has no console input of its own, so the question
# goes through the host's prompt; with no interactive host the default wins.
function Read-FactoryYes {
    param([Parameter(Mandatory)] [string] $Prompt)
    if ($script:DryRun) { return $false }
    try {
        $reply = Read-Host -Prompt $Prompt
    } catch {
        return $false
    }
    return ($reply -match '^(y|yes|예|ㅇ)$')
}

# ------------------------------------------------------------------- step 1: os

function Invoke-FactoryPreflight {
    # `$IsWindows` only exists in PowerShell 6+, and StrictMode makes touching an
    # undefined variable an error, so Windows PowerShell 5.1 is detected by
    # version rather than by reading a variable that is not there.
    if ($PSVersionTable.PSVersion.Major -ge 6) {
        $onWindows = Get-Variable -Name 'IsWindows' -ValueOnly -ErrorAction SilentlyContinue
        if (-not $onWindows) {
            Write-FactorySay -Key 'osbad'
            throw 'OS_UNSUPPORTED'
        }
    }
    $architecture = $env:PROCESSOR_ARCHITECTURE
    if (-not $architecture) { $architecture = 'unknown' }
    Write-FactorySay -Key 'host' -Values @($architecture)
}

# ----------------------------------------------------------- step 2: package mgr

function Invoke-FactoryPackageManager {
    if (Test-FactoryCommand 'winget') {
        Write-FactorySay -Key 'skip' -Values @('winget')
        return
    }
    # winget cannot be bootstrapped unattended; it ships with App Installer.
    Write-FactorySay -Key 'wingetbad'
    throw 'WINGET_MISSING'
}

function Install-FactoryWinget {
    param([Parameter(Mandatory)] [string] $Id)
    Invoke-FactoryRun -File 'winget' -Arguments @(
        'install', '--id', $Id, '--exact', '--silent',
        '--accept-package-agreements', '--accept-source-agreements') | Out-Null
}

# ---------------------------------------------------------------- step 3: tools

function Install-FactoryUv {
    if (Test-FactoryCommand 'uv') {
        Write-FactorySay -Key 'skip' -Values @('uv')
        return
    }
    if (Test-Path (Join-Path $script:UvDir 'uv.exe')) {
        Add-FactoryPath -Directory $script:UvDir
        Write-FactorySay -Key 'skip' -Values @('uv')
        return
    }
    Write-FactorySay -Key 'doing' -Values @('uv')
    if ($script:DryRun) {
        Write-Host 'DRY  irm https://astral.sh/uv/install.ps1 | iex'
    } else {
        Invoke-Expression (Invoke-RestMethod -Uri 'https://astral.sh/uv/install.ps1')
    }
    Add-FactoryPath -Directory $script:UvDir
}

function Install-FactoryGit {
    if (Test-FactoryCommand 'git') {
        Write-FactorySay -Key 'skip' -Values @('git')
        return
    }
    Write-FactorySay -Key 'doing' -Values @('git')
    Install-FactoryWinget -Id 'Git.Git'
}

function Install-FactoryFfmpeg {
    if ((Test-FactoryCommand 'ffmpeg') -and (Test-FactoryCommand 'ffprobe')) {
        Write-FactorySay -Key 'skip' -Values @('ffmpeg')
        return
    }
    Write-FactorySay -Key 'doing' -Values @('ffmpeg')
    Install-FactoryWinget -Id 'Gyan.FFmpeg'
}

function Install-FactoryNode {
    if ((Test-FactoryCommand 'node') -and (Test-FactoryCommand 'npm')) {
        Write-FactorySay -Key 'skip' -Values @('node')
        return
    }
    Write-FactorySay -Key 'doing' -Values @('node')
    Install-FactoryWinget -Id 'OpenJS.NodeJS.LTS'
}

function Install-FactoryHiggsfield {
    if (Test-FactoryCommand 'higgsfield') {
        Write-FactorySay -Key 'skip' -Values @('higgsfield')
        return
    }
    # There is no documented unattended Windows install line for this CLI, so
    # the script says so rather than inventing one and failing opaquely.
    Write-FactorySay -Key 'hfmanual'
}

function Invoke-FactoryTools {
    Install-FactoryUv
    Install-FactoryGit
    Install-FactoryFfmpeg
    Install-FactoryNode
    Install-FactoryHiggsfield
}

# ------------------------------------------------------------- step 4: naver

function Install-FactoryPlaywright {
    if ($script:WantNaver -eq $false) {
        Write-FactorySay -Key 'naverskip'
        return
    }
    if ($null -eq $script:WantNaver) {
        if (Read-FactoryYes -Prompt (Get-FactoryText -Key 'naverask')) {
            $script:WantNaver = $true
        } else {
            $script:WantNaver = $false
            Write-FactorySay -Key 'naverskip'
            return
        }
    }
    if (-not (Test-FactoryCommand 'npm')) {
        Write-FactorySay -Key 'naverskip'
        return
    }
    Write-FactorySay -Key 'doing' -Values @('playwright chromium')
    Invoke-FactoryRun -File 'npm' -Arguments @('install', '-g', 'playwright') | Out-Null
    Invoke-FactoryRun -File 'npx' -Arguments @('playwright', 'install', 'chromium') | Out-Null
}

# -------------------------------------------------------------- step 5: the repo

function Invoke-FactoryClone {
    if (Test-Path (Join-Path $script:RepoDir '.git')) {
        Write-FactorySay -Key 'pull' -Values @($script:RepoDir)
        # --ff-only: a user who edited their clone keeps the edits and is told.
        $code = Invoke-FactoryRun -File 'git' -Arguments @('pull', '--ff-only') `
            -WorkingDirectory $script:RepoDir
        if ($code -ne 0) { Write-FactorySay -Key 'pullbad' -Values @($script:RepoDir) }
        return
    }
    if (Test-Path $script:RepoDir) {
        Write-FactorySay -Key 'skip' -Values @($script:RepoDir)
        return
    }
    Write-FactorySay -Key 'cloning' -Values @($script:RepoDir)
    Invoke-FactoryRun -File 'git' -Arguments @('clone', $script:RepoUrl, $script:RepoDir) | Out-Null
}

# ----------------------------------------------------- steps 6-7: sync and init

function Invoke-FactorySync {
    Invoke-FactoryRun -File 'uv' -Arguments @('sync') -WorkingDirectory $script:RepoDir | Out-Null
}

function Invoke-FactoryInit {
    Invoke-FactoryRun -File 'uv' -Arguments @('run', 'factory', 'init', '--os', 'windows') `
        -WorkingDirectory $script:RepoDir | Out-Null
}

# -------------------------------------------------------------- step 8: an agent

function Install-FactoryAgent {
    foreach ($agent in @('claude', 'codex')) {
        if (Test-FactoryCommand $agent) {
            Write-FactorySay -Key 'agentok' -Values @($agent)
            return
        }
    }
    Write-FactorySay -Key 'agentnew'
    if (-not (Test-FactoryCommand 'npm')) {
        Write-FactorySay -Key 'agentbad'
        return
    }
    $code = Invoke-FactoryRun -File 'npm' -Arguments @('install', '-g', '@anthropic-ai/claude-code')
    if ($code -ne 0) { Write-FactorySay -Key 'agentbad' }
}

# ------------------------------------------------------------ step 9: launcher

function Install-FactoryLauncher {
    Invoke-FactoryRun -File 'uv' `
        -Arguments @('run', 'factory', 'launcher', 'create', '--os', 'windows') `
        -WorkingDirectory $script:RepoDir | Out-Null
}

# --------------------------------------------------------------- step 10: sleep

function Show-FactorySleepHint {
    # Printed, not run: changing a power plan without being asked is not ours to
    # do, and the command needs no elevation for the user to run it themselves.
    Write-FactorySay -Key 'sleephint'
}

# --------------------------------------------------------------- step 11: start

function Start-FactoryFactory {
    if (-not $script:WantStart) {
        Write-FactorySay -Key 'nostart' -Values @($script:RepoDir)
        return
    }
    Write-FactorySay -Key 'donemsg'
    Invoke-FactoryRun -File 'uv' -Arguments @('run', 'factory', 'start') `
        -WorkingDirectory $script:RepoDir | Out-Null
}

# ------------------------------------------------------------------------- main

function Invoke-FactoryInstall {
    Write-FactorySay -Key 'banner'
    try {
        Write-FactoryStep -Number 1 -Key 'preflight';  Invoke-FactoryPreflight
        Write-FactoryStep -Number 2 -Key 'pkgmgr';     Invoke-FactoryPackageManager
        Write-FactoryStep -Number 3 -Key 'tools';      Invoke-FactoryTools
        Write-FactoryStep -Number 4 -Key 'naver';      Install-FactoryPlaywright
        Write-FactoryStep -Number 5 -Key 'clone';      Invoke-FactoryClone
        Write-FactoryStep -Number 6 -Key 'sync';       Invoke-FactorySync
        Write-FactoryStep -Number 7 -Key 'initstep';   Invoke-FactoryInit
        Write-FactoryStep -Number 8 -Key 'agent';      Install-FactoryAgent
        Write-FactoryStep -Number 9 -Key 'launcher';   Install-FactoryLauncher
        Write-FactoryStep -Number 10 -Key 'sleepstep'; Show-FactorySleepHint
        Write-FactoryStep -Number 11 -Key 'start';     Start-FactoryFactory
    } catch {
        Write-FactorySay -Key 'failed' -Values @(
            $script:Step, $script:StepTotal, $script:StepLabel, $_.Exception.Message)
        throw
    }
}

if (-not $Lib) {
    Invoke-FactoryInstall
}
