param(
    [Parameter(Mandatory = $true)] [string[]] $Url,
    [int] $Bytes = 67108864,
    [int] $Runs = 3
)

$ErrorActionPreference = 'Stop'
$tmpDir = Join-Path ([IO.Path]::GetTempPath()) ('onionmind-download-bench-' + [guid]::NewGuid())
New-Item -ItemType Directory -Path $tmpDir | Out-Null
try {
    foreach ($u in $Url) {
        $name = ([Uri]$u).Host
        $speeds = @()
        for ($i = 1; $i -le $Runs; $i++) {
            $out = Join-Path $tmpDir ("run-$i.bin")
            $sw = [Diagnostics.Stopwatch]::StartNew()
            & curl.exe -L --fail --silent --show-error --range ("0-{0}" -f ($Bytes - 1)) -o $out $u
            if ($LASTEXITCODE -ne 0) { throw "curl failed for $u" }
            $sw.Stop()
            $mbps = ((Get-Item $out).Length * 8 / $sw.Elapsed.TotalSeconds / 1e6)
            $speeds += $mbps
            Remove-Item -LiteralPath $out -Force
            Write-Output ("{0} run {1}: {2:N1} Mbps ({3:N2}s)" -f $name, $i, $mbps, $sw.Elapsed.TotalSeconds)
        }
        Write-Output ("{0} average: {1:N1} Mbps" -f $name, (($speeds | Measure-Object -Average).Average))
    }
} finally {
    Remove-Item -LiteralPath $tmpDir -Recurse -Force -ErrorAction SilentlyContinue
}
