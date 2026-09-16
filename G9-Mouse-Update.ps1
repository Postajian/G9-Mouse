<#
  G9-Mouse-Update.ps1 - the MOUSE-ONLY update.

  Run this instead of G9-Update-All.ps1 when the session only touched the mouse.
  G9-Update-All rebuilds Repo Tracker and Social Dashboard, syntax-checks
  U.R.A.R.T.U, pings HQ and scans the whole tree - none of which can be affected
  by a cursor change. This does the four things that can:

    1. rebuild the artwork from the generators
    2. verify it: re-parse every file, and make Windows itself load each one
    3. prove the live pointer still matches disk, and the spin latch still works
    4. commit and push the public G9-Mouse repo, then log it

  Safe: never deletes, never touches the rest of G9, and pushes only if the
  verification passed.
#>

[CmdletBinding()]
param(
    [switch]$NoPush,          # verify and log, but leave git alone
    [string]$Message = ''     # commit subject; auto-generated if omitted
)

$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $here

$log = Join-Path $here 'update.log'
$summary = @()

function Say([string]$text, [string]$colour = 'Gray') {
    Write-Host $text -ForegroundColor $colour
    $script:summary += $text
}

function Get-Python {
    $p = Get-Command python -CommandType Application -ErrorAction SilentlyContinue
    if (-not $p) { throw 'Python is not on PATH.' }
    return $p.Source
}

Write-Host 'G9 Mouse update - mouse only, nothing else in G9 is touched.' -ForegroundColor Cyan
$python = Get-Python

# The click helper holds file handles on the artwork, so a rebuild while it is
# running can race. Remember whether it was on and put it back at the end.
$effectWasOn = $false
try {
    $state = & $python 'cursor_cli.py' 'effect' '--state' 'status' 2>&1 |
        Select-Object -Last 1 | ConvertFrom-Json
    $effectWasOn = [bool]$state.effect
} catch { }
if ($effectWasOn) { & $python 'cursor_cli.py' 'effect' '--state' 'off' | Out-Null }

# Which set is on the pointer before any test touches it. Reported, never
# silently put back: a test that fails to restore and a user who switched sets
# mid-run look identical from here, and only one of them should be overruled.
$setBefore = (& $python 'which_set.py' 2>&1 | Select-Object -Last 1)

try {
    # 1. rebuild
    & $python 'build_cursors.py' | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'build_cursors.py failed.' }
    $files = (Get-ChildItem -Path 'cursors' -Recurse -Include *.cur, *.ani -ErrorAction SilentlyContinue |
        Measure-Object).Count
    Say ("  artwork rebuilt: {0} files" -f $files) 'Green'

    # 2. verify the files themselves
    $v = & $python 'verify_cursors.py' 2>&1
    if ($LASTEXITCODE -ne 0) { throw ("verify_cursors.py failed:`n" + ($v | Out-String)) }
    Say '  every file parses, hotspot centred, Windows loads it' 'Green'

    # 3. behaviour
    $t = & $python 'test_latch.py' 2>&1
    if ($LASTEXITCODE -ne 0) { throw ("test_latch.py failed:`n" + ($t | Out-String)) }
    Say '  spin latches, survives release, stops on next click' 'Green'

    $ts = & $python 'test_sets.py' 2>&1
    if ($LASTEXITCODE -ne 0) { throw ("test_sets.py failed:`n" + ($ts | Out-String)) }
    Say '  every set is reachable, uniquely named, and the gesture cycles them' 'Green'

    $tr = & $python 'test_trail.py' 2>&1
    if ($LASTEXITCODE -ne 0) { throw ("test_trail.py failed:`n" + ($tr | Out-String)) }
    Say '  neon trail paints real screen pixels at 30+ fps' 'Green'

    $p = & $python 'prove_installed.py' 2>&1
    if ($LASTEXITCODE -ne 0) {
        Say '  live pointer does NOT match disk - reinstall from the panel' 'Yellow'
    } else {
        Say '  live pointer matches every file on disk' 'Green'
    }

    $setAfter = (& $python 'which_set.py' 2>&1 | Select-Object -Last 1)
    if ($setAfter -ne $setBefore) {
        Say ("  NOTE: pointer set changed during this run, {0} -> {1}" -f $setBefore, $setAfter) 'Yellow'
        Say '        if you did not switch it yourself, a test failed to put it back' 'Yellow'
    } else {
        Say ("  pointer set unchanged by the run: {0}" -f $setAfter) 'Green'
    }

    # 4. publish
    if ($NoPush) {
        Say '  push skipped (-NoPush)' 'Yellow'
    } else {
        $dirty = (git status --porcelain) -join "`n"
        if (-not $dirty.Trim()) {
            Say '  repo already up to date, nothing to push' 'Gray'
        } else {
            if (-not $Message) {
                $Message = 'G9 Mouse update ' + (Get-Date -Format 'yyyy-MM-dd HH:mm')
            }
            git add -A | Out-Null
            git commit -q -m $Message -m 'Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>'
            git push -q origin HEAD
            Say ("  pushed: {0}" -f $Message) 'Green'
        }
    }
}
finally {
    if ($effectWasOn) { & $python 'cursor_cli.py' 'effect' '--state' 'on' | Out-Null }
}

$stamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
Add-Content -LiteralPath $log -Value (@("[$stamp] G9 Mouse update") + $summary + '')
Write-Host ''
Write-Host '=== G9 MOUSE SUMMARY ===' -ForegroundColor Cyan
$summary | ForEach-Object { Write-Host $_ }
Write-Host ("  log: {0}" -f $log) -ForegroundColor DarkGray
