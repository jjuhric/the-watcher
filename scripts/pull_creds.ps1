# Keeps a local credentials file in step with the login set in The Watcher's account page.
# The app (on the Pi) writes data/claude-access.json whenever the login changes and the
# "keep a copy for my assistant" box is ticked; this copies it into a local text file.
# Usage: pwsh -File scripts\pull_creds.ps1 [-Out C:\path\to\creds.txt] [-PiHost pi5]
param(
  [string]$Out = "$env:USERPROFILE\.claude\living-cam-creds.txt",
  [string]$PiHost = "pi5",
  [string]$Remote = "Sandbox/living-cam/data/claude-access.json"
)
$ErrorActionPreference = "Stop"
$raw = ssh -o BatchMode=yes -o ConnectTimeout=8 $PiHost "cat ~/$Remote" 2>$null
if ($LASTEXITCODE -ne 0 -or -not $raw) { "No shared login on the Pi (or Pi unreachable); left $Out unchanged."; return }
$j = ($raw -join "`n") | ConvertFrom-Json
if (-not $j.user -or -not $j.pass) { "Shared login file is incomplete; left $Out unchanged."; return }
$new = "url=$($j.url)`nuser=$($j.user)`npass=$($j.pass)`n"
$old = if (Test-Path $Out) { Get-Content $Out -Raw } else { "" }
if ($old -ne $new) {
  New-Item -ItemType Directory -Force (Split-Path $Out) | Out-Null
  [IO.File]::WriteAllText($Out, $new)
  "Updated $Out (user: $($j.user))"
} else { "$Out already up to date." }
