param(
    [Parameter(Mandatory=$true)][string]$OutputDir,
    [int]$InitialProducerId = 0
)

$ErrorActionPreference = 'Stop'
$repoRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
Set-Location -LiteralPath $repoRoot
$preparedPath = Join-Path $repoRoot 'outputs/hamburg-od-baseline-20261022-v1'
$initialPath = Join-Path $repoRoot 'outputs/hamburg-od-initial-native-20261022-v1'
$nativeReportPath = Join-Path $initialPath 'native/run.json'
$taskPython = Join-Path $repoRoot '.venv/Scripts/python.exe'
$env:PYTHONPATH = Join-Path $repoRoot 'plugins/astra-sumo/src'
$env:SUMO_HOME = Join-Path $repoRoot '.venv/Lib/site-packages/sumo'
$env:PATH = "$env:SUMO_HOME/bin;$repoRoot/.venv/Scripts;$env:PATH"
$env:PYTHONUTF8 = '1'
$launchPath = [IO.Path]::GetFullPath("$OutputDir-launch")
New-Item -ItemType Directory -Path $launchPath | Out-Null
$launchRecord = [ordered]@{
    stage = 'waiting_for_initial'
    output_dir = [IO.Path]::GetFullPath($OutputDir)
    initial_case = $initialPath
    initial_producer_id = $InitialProducerId
    launcher_id = $PID
    settings_sha256 = (Get-FileHash -LiteralPath (Join-Path $PSScriptRoot 'settings.json') -Algorithm SHA256).Hash.ToLower()
}
$launchFile = Join-Path $launchPath 'launch.json'
$launchRecord | ConvertTo-Json | Set-Content -LiteralPath $launchFile -Encoding utf8NoBOM

function Test-InitialNativeReady {
    if (-not (Test-Path -LiteralPath $nativeReportPath)) { return $false }
    $nativeReport = Get-Content -LiteralPath $nativeReportPath -Raw | ConvertFrom-Json
    return ($nativeReport.native_status -eq 'pass' -and $nativeReport.observer_complete -and
            $nativeReport.stall_monitor.status -eq 'completed')
}

try {
if (-not (Test-InitialNativeReady)) {
    if ($InitialProducerId -le 0) {
        throw 'Provide the running initial evaluation process ID, or complete that evaluation first.'
    }
    Write-Output 'Waiting for the existing initial OD evaluation. No duplicate simulation will start.'
    while (-not (Test-InitialNativeReady)) {
        if (-not (Get-Process -Id $InitialProducerId -ErrorAction SilentlyContinue)) {
            throw 'The initial evaluation ended without complete native observations. Preserve and inspect its native outputs.'
        }
        Start-Sleep -Seconds 5
    }
}

$launchRecord.stage = 'running_spsa'
$launchRecord | ConvertTo-Json | Set-Content -LiteralPath $launchFile -Encoding utf8NoBOM
& $taskPython plugins/astra-sumo/scripts/run_hamburg_od_spsa.py `
    --prepared-dir $preparedPath --output-dir $OutputDir `
    --settings (Join-Path $PSScriptRoot 'settings.json') --initial-evaluation-dir $initialPath
if ($LASTEXITCODE -ne 0) {
    throw "The SPSA study failed with exit code $LASTEXITCODE. Preserve its raw outputs."
}
$launchRecord.stage = 'completed'
$launchRecord | ConvertTo-Json | Set-Content -LiteralPath $launchFile -Encoding utf8NoBOM
} catch {
    $launchRecord.stage = 'failed'
    $launchRecord.error = $_.Exception.Message
    $launchRecord | ConvertTo-Json | Set-Content -LiteralPath $launchFile -Encoding utf8NoBOM
    throw
}
