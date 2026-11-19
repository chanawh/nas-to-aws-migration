# Unmount the OneFS shares mounted by mount_onefs.ps1.
param([Parameter(Mandatory=$true)][string]$MountRoot)
$ErrorActionPreference = "Continue"
foreach ($letter in @("F:", "G:", "H:", "I:")) { net use $letter /delete /y }
foreach ($share in @("finance", "it-support", "it-operations", "operations")) {
  $link = Join-Path $MountRoot $share
  if (Test-Path $link) { Remove-Item $link -Force }
}
Write-Host "Unmounted."
