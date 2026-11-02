$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$EnvPath = Join-Path $Root ".env"
$ExamplePath = Join-Path $Root ".env.example"

Write-Host "" 
Write-Host "CONFIGURACAO DA CHAVE GEMINI" -ForegroundColor Yellow
Write-Host "1. Abra https://aistudio.google.com/apikey"
Write-Host "2. Crie/use um projeto identificado como Free Tier."
Write-Host "3. NAO clique em Configurar faturamento se deseja custo zero."
Write-Host "4. Copie a chave e cole abaixo. Ela ficara somente neste computador."
Write-Host ""

$SecureKey = Read-Host "Cole a chave da API Gemini" -AsSecureString
$Ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($SecureKey)
try {
    $ApiKey = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($Ptr)
} finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($Ptr)
}

if ([string]::IsNullOrWhiteSpace($ApiKey)) {
    throw "A chave nao pode ficar vazia."
}

if (Test-Path $ExamplePath) {
    $Content = Get-Content $ExamplePath -Raw
} else {
    $Content = "GEMINI_API_KEY=`nGEMINI_MODEL=gemini-3.5-flash-lite`nGEMINI_MODEL_FALLBACKS=gemini-2.5-flash`n"
}

$EscapedKey = $ApiKey.Replace('$', '`$')
$Content = [regex]::Replace($Content, '(?m)^GEMINI_API_KEY=.*$', "GEMINI_API_KEY=$EscapedKey")
Set-Content -Path $EnvPath -Value $Content -Encoding UTF8

Write-Host ""
Write-Host "Chave salva em .env." -ForegroundColor Green
Write-Host "O programa nao ativa faturamento. O custo zero depende de a chave pertencer a um projeto Free Tier." -ForegroundColor Cyan
