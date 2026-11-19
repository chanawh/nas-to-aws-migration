# Mount the 4 OneFS SMB shares on a Windows host (VMware Workstation Pro host).
# Run in PowerShell as Administrator:
#   .\scripts\mount_onefs.ps1 -OneFSHost 192.168.1.50 -MountRoot C:\nas2aws\onefs
# The password is prompted, never stored in the script. Each share gets
# a drive letter, then a directory junction from MountRoot\<share> to
# that drive, so the Python sync engine sees one tree:
#   C:\nas2aws\onefs\finance, it-support, it-operations, operations
# Unmount with: .\scripts\unmount_onefs.ps1 -MountRoot C:\nas2aws\onefs
param(
  [Parameter(Mandatory=$true)][string]$OneFSHost,
  [Parameter(Mandatory=$true)][string]$MountRoot,
  [string]$User = "root"
)
$ErrorActionPreference = "Stop"
$secure = Read-Host -Prompt "Password for $User@$OneFSHost" -AsSecureString
$plain = [Runtime.InteropServices.Marshal]::PtrToStringAuto(
  [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure))

New-Item -ItemType Directory -Force -Path $MountRoot | Out-Null
$map = @{ finance = "F:"; "it-support" = "G:"; "it-operations" = "H:"; operations = "I:" }
foreach ($share in $map.Keys) {
  $letter = $map[$share]
  net use $letter "\\$OneFSHost\$share" /user:$User $plain /persistent:no
  $link = Join-Path $MountRoot $share
  if (Test-Path $link) { Remove-Item $link -Force }
  cmd /c mklink /D $link "$letter\"
  Write-Host "$share -> $letter\ (junction $link)"
}
Write-Host "Mounted. Test: dir $MountRoot\finance"
