$ErrorActionPreference = 'Stop'
$webhookUrl = Read-Host 'Feishu group bot Webhook URL'
if ($webhookUrl -notmatch '^https://open\.feishu\.cn/open-apis/bot/v2/hook/[^/?#]+$') { throw 'Invalid Feishu webhook URL' }
$signingSecret = Read-Host 'Signing secret (leave empty if signing is disabled)'
$configPath = Join-Path $PSScriptRoot 'feishu.local.json'
@{enabled=$true;webhook_url=$webhookUrl;signing_secret=$signingSecret;keyword='Roblox日报'} | ConvertTo-Json | Set-Content -LiteralPath $configPath -Encoding utf8
Write-Output 'Feishu configured locally. Next daily task will send the report. No test message has been sent.'
