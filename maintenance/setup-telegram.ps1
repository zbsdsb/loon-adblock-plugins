$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$private = Join-Path $root 'private'
New-Item -ItemType Directory -Force -Path $private | Out-Null
$token = Read-Host 'Telegram Bot Token（不会回显）' -AsSecureString
$chat = Read-Host 'Telegram Chat ID（不会回显）' -AsSecureString
@{ token = ($token | ConvertFrom-SecureString); chat_id = ($chat | ConvertFrom-SecureString) } |
    ConvertTo-Json | Set-Content -LiteralPath (Join-Path $private 'telegram.dpapi.json') -Encoding UTF8
Write-Host '已保存当前 Windows 用户专属 DPAPI 密文；未输出凭据。'
