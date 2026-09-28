# Keep the rotated key in this process only; do not write it to disk or CLI arguments.
$projectStart = Join-Path (Split-Path -Parent $PSScriptRoot) 'start.cmd'
$secure = Read-Host 'Paste your NEW StepFun API Key (input is hidden)' -AsSecureString
if ($null -eq $secure -or $secure.Length -eq 0) {
    Write-Error '[SkillPulse] StepFun API Key is empty; startup cancelled.'
    exit 1
}
$pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
try {
    $env:STEPFUN_API_KEY = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)
    & $projectStart
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
} finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer)
    Remove-Item Env:STEPFUN_API_KEY -ErrorAction SilentlyContinue
}
