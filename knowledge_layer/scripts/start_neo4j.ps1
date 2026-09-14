# Start the local Neo4j Community server used by the pipeline.
#
#   powershell -ExecutionPolicy Bypass -File knowledge_layer\scripts\start_neo4j.ps1            # console (foreground)
#   powershell -ExecutionPolicy Bypass -File knowledge_layer\scripts\start_neo4j.ps1 -Detached  # background window
#
# The server lives in neo4j-data\neo4j-community-2026.05.0 (gitignored) and uses the
# Java 21 runtime bundled with Neo4j Desktop 2. Bolt: bolt://localhost:7687,
# Browser: http://localhost:7474, user neo4j / password refinery2024 (config default;
# override with RKL_NEO4J_PASSWORD).
param([switch]$Detached)

$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$neo4jHome = Join-Path $root "neo4j-data\neo4j-community-2026.05.0"
$javaHome = Join-Path $env:USERPROFILE ".Neo4jDesktop2\Cache\runtime\zulu21.50.19-ca-jre21.0.11-win_x64"

if (-not (Test-Path $neo4jHome)) {
    Write-Error "Neo4j not found at $neo4jHome. Download neo4j-community-2026.05.0-windows.zip from https://neo4j.com/deployment-center/ and extract it into neo4j-data\."
    exit 1
}
if (-not (Test-Path (Join-Path $javaHome "bin\java.exe"))) {
    Write-Warning "Bundled JRE not found at $javaHome; falling back to JAVA_HOME=$env:JAVA_HOME"
} else {
    $env:JAVA_HOME = $javaHome
}

$bat = Join-Path $neo4jHome "bin\neo4j.bat"
if ($Detached) {
    Start-Process -FilePath $bat -ArgumentList "console" -WorkingDirectory $neo4jHome -WindowStyle Minimized
    Write-Host "Neo4j starting in a background window (bolt://localhost:7687)."
} else {
    & $bat console
}
