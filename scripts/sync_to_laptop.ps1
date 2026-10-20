# Pull new snapshots + a consistent copy of the log database from the Pi to this PC.
# Incremental: only files newer than the last successful sync are transferred.
# Usage: pwsh -File scripts\sync_to_laptop.ps1 [-Dest D:\LivingCam] [-Host pi5]
param(
  [string]$Dest = "$env:USERPROFILE\LivingCamArchive",
  [string]$PiHost = "pi5",
  [string]$RemoteDir = "Sandbox/living-cam/data"
)
$ErrorActionPreference = "Stop"
New-Item -ItemType Directory -Force $Dest | Out-Null
$stateFile = Join-Path $Dest ".sync-state"
$since = if (Test-Path $stateFile) { (Get-Content $stateFile -Raw).Trim() } else { "1970-01-01 00:00:00" }
$started = (Get-Date).ToUniversalTime().ToString("yyyy-MM-dd HH:mm:ss")

# 1. consistent DB snapshot (sqlite online backup, safe while the service is writing)
ssh -o BatchMode=yes $PiHost "cd ~/$RemoteDir && python3 -c `"import sqlite3;s=sqlite3.connect('livingcam.db');d=sqlite3.connect('export.db');s.backup(d)`""
scp -o BatchMode=yes "${PiHost}:$RemoteDir/export.db" (Join-Path $Dest "livingcam.db")

# 2. new images only, bundled as a tar on the Pi then copied (Windows has no rsync)
$tmp = Join-Path $Dest "_incoming.tar"
ssh -o BatchMode=yes $PiHost "cd ~/$RemoteDir && find images -type f -newermt '$since UTC' -print0 | tar --null -T - -cf /tmp/lc_sync.tar"
scp -o BatchMode=yes "${PiHost}:/tmp/lc_sync.tar" $tmp
ssh -o BatchMode=yes $PiHost "rm -f /tmp/lc_sync.tar ~/$RemoteDir/export.db"
if ((Get-Item $tmp).Length -gt 1024) { tar -xf $tmp -C $Dest }
Remove-Item $tmp -Force
Set-Content $stateFile $started
"Synced to $Dest (since $since UTC)"

# 3. refresh the local credentials file if the login was changed on the Pi (no-op otherwise)
$pull = Join-Path $PSScriptRoot 'pull_creds.ps1'
if (Test-Path $pull) { & $pull }
