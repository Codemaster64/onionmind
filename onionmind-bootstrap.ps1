[CmdletBinding()]
param(
    [switch]$Apply,
    [switch]$AllowDirectNetwork,
    [switch]$CheckLatest,
    [switch]$Yes
)

# Windows PowerShell 5.1 and pwsh are both supported. The default path is a
# local, read-only audit: it does not start programs, write files, or contact an
# external host. Direct-network operations require an explicit second flag.
Set-StrictMode -Version 2.0
$ErrorActionPreference = "Stop"

$MinimumOllamaVersion = [version]"0.32.11"
$MinimumNode22Version = [version]"22.19.0"
$BundleExecutable = Join-Path $PSScriptRoot "Onionmind.exe"

function Join-ExistingBase {
    param(
        [AllowNull()][string]$Base,
        [Parameter(Mandatory = $true)][string]$Child
    )

    if ([string]::IsNullOrWhiteSpace($Base)) { return $null }
    return (Join-Path $Base $Child)
}

function Resolve-LocalExecutable {
    param(
        [Parameter(Mandatory = $true)][string]$CommandName,
        [Parameter(Mandatory = $true)][AllowEmptyCollection()][string[]]$KnownPaths
    )

    $Candidates = New-Object "System.Collections.Generic.List[string]"
    $Command = Get-Command $CommandName -CommandType Application -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($null -ne $Command -and -not [string]::IsNullOrWhiteSpace($Command.Path)) {
        $Candidates.Add($Command.Path)
    }
    foreach ($Candidate in $KnownPaths) {
        if (-not [string]::IsNullOrWhiteSpace($Candidate)) {
            $Candidates.Add($Candidate)
        }
    }

    $Seen = @{}
    foreach ($Candidate in $Candidates) {
        try { $Full = [IO.Path]::GetFullPath($Candidate) } catch { continue }
        $Key = $Full.ToLowerInvariant()
        if ($Seen.ContainsKey($Key)) { continue }
        $Seen[$Key] = $true
        if (Test-Path -LiteralPath $Full -PathType Leaf) { return $Full }
    }
    return $null
}

function Get-LocalFileVersion {
    param([AllowNull()][string]$Path)

    if ([string]::IsNullOrWhiteSpace($Path)) { return $null }
    try {
        $Info = (Get-Item -LiteralPath $Path -ErrorAction Stop).VersionInfo
        foreach ($Value in @($Info.ProductVersion, $Info.FileVersion)) {
            if ([string]::IsNullOrWhiteSpace([string]$Value)) { continue }
            $Match = [Text.RegularExpressions.Regex]::Match(
                [string]$Value,
                "(?<![0-9])([0-9]+\.[0-9]+(?:\.[0-9]+)?(?:\.[0-9]+)?)(?![0-9])"
            )
            if ($Match.Success) {
                try { return [version]$Match.Groups[1].Value } catch { }
            }
        }
    } catch { }
    return $null
}

function Get-LocalInstalledProductVersion {
    param([Parameter(Mandatory = $true)][string]$DisplayNamePattern)

    $RegistryPaths = @(
        "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*",
        "HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*",
        "HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*"
    )
    try {
        $Product = Get-ItemProperty $RegistryPaths -ErrorAction SilentlyContinue |
            Where-Object { [string]$_.DisplayName -match $DisplayNamePattern } |
            Select-Object -First 1
        if ($null -eq $Product -or [string]::IsNullOrWhiteSpace([string]$Product.DisplayVersion)) {
            return $null
        }
        $Match = [Text.RegularExpressions.Regex]::Match(
            [string]$Product.DisplayVersion,
            "(?<![0-9])([0-9]+\.[0-9]+(?:\.[0-9]+)?(?:\.[0-9]+)?)(?![0-9])"
        )
        if ($Match.Success) { return [version]$Match.Groups[1].Value }
    } catch { }
    return $null
}

function Get-ListeningPorts {
    try {
        return @(
            [Net.NetworkInformation.IPGlobalProperties]::GetIPGlobalProperties().GetActiveTcpListeners() |
                ForEach-Object { $_.Port } |
                Sort-Object -Unique
        )
    } catch {
        return @()
    }
}

function Get-LocalOllamaModels {
    param([bool]$ServiceAlreadyListening)

    # Do not start Ollama and do not make even a loopback HTTP request. When a
    # service is already listening, report the local manifest names visible to
    # this user. The app's Model Manager performs authoritative discovery later.
    if (-not $ServiceAlreadyListening) { return @() }

    $Roots = New-Object "System.Collections.Generic.List[string]"
    if (-not [string]::IsNullOrWhiteSpace($env:OLLAMA_MODELS)) {
        $Roots.Add($env:OLLAMA_MODELS)
    }
    $DefaultRoot = Join-ExistingBase -Base $env:USERPROFILE -Child ".ollama\models"
    if ($null -ne $DefaultRoot) { $Roots.Add($DefaultRoot) }

    $Names = New-Object "System.Collections.Generic.List[string]"
    $Seen = @{}
    foreach ($Root in $Roots) {
        $ManifestRoot = Join-Path $Root "manifests"
        if (-not (Test-Path -LiteralPath $ManifestRoot -PathType Container)) { continue }
        $Files = @(
            Get-ChildItem -LiteralPath $ManifestRoot -File -Recurse -ErrorAction SilentlyContinue |
                Select-Object -First 200
        )
        foreach ($File in $Files) {
            $Relative = $File.FullName.Substring($ManifestRoot.Length).TrimStart('\', '/')
            $Parts = @($Relative -split '[\\/]')
            if ($Parts.Count -lt 2) { continue }
            $Model = $Parts[$Parts.Count - 2] + ":" + $Parts[$Parts.Count - 1]
            $Key = $Model.ToLowerInvariant()
            if (-not $Seen.ContainsKey($Key)) {
                $Seen[$Key] = $true
                $Names.Add($Model)
            }
        }
    }
    return @($Names | Sort-Object)
}

function New-InventoryItem {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][ValidateSet("READY", "MISSING", "OUTDATED", "OPTIONAL")][string]$Status,
        [string]$Version = "-",
        [string]$Location = "-",
        [Parameter(Mandatory = $true)][string]$Detail,
        [string]$WingetId = "",
        [string]$WingetAction = "",
        [string]$ExpectedDestination = "",
        [bool]$BlocksReady = $false
    )

    return [PSCustomObject]@{
        Name = $Name
        Status = $Status
        Version = $Version
        Location = $Location
        Detail = $Detail
        WingetId = $WingetId
        WingetAction = $WingetAction
        ExpectedDestination = $ExpectedDestination
        BlocksReady = $BlocksReady
    }
}

function Format-VersionValue {
    param([AllowNull()][version]$Version)
    if ($null -eq $Version) { return "unverified" }
    return $Version.ToString()
}

function Test-SupportedNodeVersion {
    param([AllowNull()][version]$Version)
    if ($null -eq $Version) { return $false }
    if ($Version.Major -ge 24) { return $true }
    return ($Version.Major -eq 22 -and $Version -ge $MinimumNode22Version)
}

function Show-Inventory {
    param([Parameter(Mandatory = $true)][object[]]$Items)

    Write-Host ""
    Write-Host "Onionmind portable readiness"
    Write-Host "============================"
    foreach ($Item in $Items) {
        Write-Host ("[{0}] {1}" -f $Item.Status, $Item.Name)
        Write-Host ("  Version:  {0}" -f $Item.Version)
        Write-Host ("  Location: {0}" -f $Item.Location)
        Write-Host ("  {0}" -f $Item.Detail)
    }
}

function Show-ActionPlan {
    param([Parameter(Mandatory = $true)][AllowEmptyCollection()][object[]]$Actions)

    Write-Host ""
    Write-Host "Proposed direct-network actions"
    Write-Host "================================"
    if ($Actions.Count -eq 0) {
        Write-Host "No dependency installation or policy upgrade is needed."
        return
    }
    foreach ($Action in $Actions) {
        Write-Host ("{0} {1} with winget" -f $Action.WingetAction.ToUpperInvariant(), $Action.WingetId)
        Write-Host ("  Expected executable: {0}" -f $Action.ExpectedDestination)
        Write-Host ("  Reason: {0} is {1}" -f $Action.Name, $Action.Status)
    }
    Write-Host ""
    Write-Host "winget will contact its configured source and package hosts directly."
    Write-Host "Onionmind will not start services or download model weights."
}

function Confirm-DirectNetworkOperation {
    param([Parameter(Mandatory = $true)][string]$Prompt)

    if ($Yes) { return $true }
    $Answer = Read-Host "$Prompt [y/N]"
    return ($Answer -match '^(?i:y|yes)$')
}

$ListeningPorts = @(Get-ListeningPorts)
$OllamaListening = ($ListeningPorts -contains 11434)
$SocksPorts = @($ListeningPorts | Where-Object { $_ -eq 9050 -or $_ -eq 9150 })

$OllamaPath = Resolve-LocalExecutable -CommandName "ollama.exe" -KnownPaths @(
    (Join-ExistingBase -Base $env:LOCALAPPDATA -Child "Programs\Ollama\ollama.exe"),
    (Join-ExistingBase -Base $env:ProgramFiles -Child "Ollama\ollama.exe"),
    (Join-ExistingBase -Base ${env:ProgramFiles(x86)} -Child "Ollama\ollama.exe")
)
$NodePath = Resolve-LocalExecutable -CommandName "node.exe" -KnownPaths @(
    (Join-ExistingBase -Base $env:ProgramFiles -Child "nodejs\node.exe"),
    (Join-ExistingBase -Base $env:LOCALAPPDATA -Child "Programs\nodejs\node.exe"),
    (Join-ExistingBase -Base $env:LOCALAPPDATA -Child "Volta\bin\node.exe"),
    (Join-ExistingBase -Base $env:USERPROFILE -Child "scoop\apps\nodejs-lts\current\node.exe")
)
$GitPath = Resolve-LocalExecutable -CommandName "git.exe" -KnownPaths @(
    (Join-ExistingBase -Base $env:ProgramFiles -Child "Git\cmd\git.exe"),
    (Join-ExistingBase -Base $env:ProgramFiles -Child "Git\bin\git.exe"),
    (Join-ExistingBase -Base $env:LOCALAPPDATA -Child "Programs\Git\cmd\git.exe")
)
$TorRoots = @(
    (Join-ExistingBase -Base ([Environment]::GetFolderPath("Desktop")) -Child "Tor Browser"),
    (Join-ExistingBase -Base $env:USERPROFILE -Child "Desktop\Tor Browser"),
    (Join-ExistingBase -Base $env:USERPROFILE -Child "OneDrive\Desktop\Tor Browser"),
    (Join-ExistingBase -Base $env:LOCALAPPDATA -Child "Tor Browser"),
    (Join-ExistingBase -Base $env:LOCALAPPDATA -Child "Programs\Tor Browser"),
    (Join-ExistingBase -Base $env:ProgramFiles -Child "Tor Browser"),
    (Join-ExistingBase -Base ${env:ProgramFiles(x86)} -Child "Tor Browser")
)
$TorRoot = $null
foreach ($CandidateRoot in $TorRoots) {
    if ([string]::IsNullOrWhiteSpace($CandidateRoot)) { continue }
    $RequiredTorAssets = @(
        (Join-Path $CandidateRoot "Browser\TorBrowser\Tor\tor.exe"),
        (Join-Path $CandidateRoot "Browser\TorBrowser\Data\Tor\torrc-defaults"),
        (Join-Path $CandidateRoot "Browser\TorBrowser\Data\Tor\torrc")
    )
    if (@($RequiredTorAssets | Where-Object { -not (Test-Path -LiteralPath $_ -PathType Leaf) }).Count -eq 0) {
        $TorRoot = $CandidateRoot
        break
    }
}
$TorPath = if ($null -ne $TorRoot) { Join-Path $TorRoot "Browser\TorBrowser\Tor\tor.exe" } else { $null }
$TorVersionPath = if ($null -ne $TorRoot) { Join-Path $TorRoot "Browser\firefox.exe" } else { $null }
$WingetPath = Resolve-LocalExecutable -CommandName "winget.exe" -KnownPaths @(
    (Join-ExistingBase -Base $env:LOCALAPPDATA -Child "Microsoft\WindowsApps\winget.exe")
)

$Items = @()
if (Test-Path -LiteralPath $BundleExecutable -PathType Leaf) {
    $OnionmindVersion = Get-LocalFileVersion -Path $BundleExecutable
    $Items += New-InventoryItem -Name "Onionmind.exe" -Status "READY" `
        -Version (Format-VersionValue $OnionmindVersion) -Location $BundleExecutable `
        -Detail "Portable application is present beside this audit tool."
} else {
    $Items += New-InventoryItem -Name "Onionmind.exe" -Status "MISSING" `
        -Location $BundleExecutable -Detail "Use the complete Onionmind-Windows-x64 portable bundle." `
        -BlocksReady $true
}

$ExpectedOllama = Join-ExistingBase -Base $env:LOCALAPPDATA -Child "Programs\Ollama\ollama.exe"
$OllamaVersion = Get-LocalFileVersion -Path $OllamaPath
if ($null -eq $OllamaVersion) {
    $OllamaVersion = Get-LocalInstalledProductVersion -DisplayNamePattern '^Ollama(?: version .*)?$'
}
if ($null -eq $OllamaPath) {
    $Items += New-InventoryItem -Name "Ollama + Harness capability" -Status "MISSING" `
        -Detail "Local inference is unavailable. Ollama 0.32.11+ adds the DeepSeek Harness launcher." `
        -WingetId "Ollama.Ollama" -WingetAction "install" -ExpectedDestination $ExpectedOllama `
        -BlocksReady $true
} elseif ($null -eq $OllamaVersion -or $OllamaVersion -lt $MinimumOllamaVersion) {
    $Items += New-InventoryItem -Name "Ollama + Harness capability" -Status "OUTDATED" `
        -Version (Format-VersionValue $OllamaVersion) -Location $OllamaPath `
        -Detail "DeepSeek Harness launch support requires Ollama 0.32.11 or newer." `
        -WingetId "Ollama.Ollama" -WingetAction "upgrade" -ExpectedDestination $ExpectedOllama `
        -BlocksReady $true
} else {
    $ServiceDetail = if ($OllamaListening) { "Loopback service is already listening; it was not started by this audit." } else { "Installed; service is not listening and was not started." }
    $Items += New-InventoryItem -Name "Ollama + Harness capability" -Status "READY" `
        -Version $OllamaVersion.ToString() -Location $OllamaPath `
        -Detail "Ollama launch dsh is supported. $ServiceDetail"
}

$ExpectedNode = Join-ExistingBase -Base $env:ProgramFiles -Child "nodejs\node.exe"
$NodeVersion = Get-LocalFileVersion -Path $NodePath
if ($null -eq $NodeVersion) {
    $NodeVersion = Get-LocalInstalledProductVersion -DisplayNamePattern '^Node\.js$'
}
if ($null -eq $NodePath) {
    $Items += New-InventoryItem -Name "Node.js for Agent mode" -Status "MISSING" `
        -Detail "DeepSeek Harness requires Node.js ^22.19 or 24+." `
        -WingetId "OpenJS.NodeJS.LTS" -WingetAction "install" -ExpectedDestination $ExpectedNode `
        -BlocksReady $true
} elseif (-not (Test-SupportedNodeVersion $NodeVersion)) {
    $Items += New-InventoryItem -Name "Node.js for Agent mode" -Status "OUTDATED" `
        -Version (Format-VersionValue $NodeVersion) -Location $NodePath `
        -Detail "Supported versions are ^22.19 or 24+. Node.js 23 is not in the supported range." `
        -WingetId "OpenJS.NodeJS.LTS" -WingetAction "upgrade" -ExpectedDestination $ExpectedNode `
        -BlocksReady $true
} else {
    $Items += New-InventoryItem -Name "Node.js for Agent mode" -Status "READY" `
        -Version $NodeVersion.ToString() -Location $NodePath `
        -Detail "Version is accepted by DeepSeek Harness."
}

$ExpectedGit = Join-ExistingBase -Base $env:ProgramFiles -Child "Git\cmd\git.exe"
$GitVersion = Get-LocalFileVersion -Path $GitPath
if ($null -eq $GitVersion) {
    $GitVersion = Get-LocalInstalledProductVersion -DisplayNamePattern '^Git$'
}
if ($null -eq $GitPath) {
    $Items += New-InventoryItem -Name "Git" -Status "MISSING" `
        -Detail "Onionmind can open folders without Git, but repository status and diff features are reduced." `
        -WingetId "Git.Git" -WingetAction "install" -ExpectedDestination $ExpectedGit
} else {
    $Items += New-InventoryItem -Name "Git" -Status "READY" `
        -Version (Format-VersionValue $GitVersion) -Location $GitPath `
        -Detail "Repository inspection is available."
}

$ExpectedTor = Join-ExistingBase -Base ([Environment]::GetFolderPath("Desktop")) -Child "Tor Browser\Browser\TorBrowser\Tor\tor.exe"
$TorVersion = Get-LocalFileVersion -Path $TorVersionPath
if ($null -eq $TorVersion) {
    $TorVersion = Get-LocalInstalledProductVersion -DisplayNamePattern '^Tor Browser$'
}
if ($null -ne $TorPath) {
    $ListenerDetail = if ($SocksPorts.Count -gt 0) {
        " An unverified local SOCKS listener is also present on port(s) $($SocksPorts -join ', ')."
    } else { "" }
    $Items += New-InventoryItem -Name "Tor Browser" -Status "READY" `
        -Version (Format-VersionValue $TorVersion) -Location $TorPath `
        -Detail "The hidden-launch executable and both Tor configuration files are present. Onionmind starts only tor.exe after one-turn permission.$ListenerDetail"
} elseif ($SocksPorts.Count -gt 0) {
    $Items += New-InventoryItem -Name "Local SOCKS listener" -Status "OPTIONAL" `
        -Location ("localhost:" + ($SocksPorts -join ", localhost:")) `
        -Detail "Tor Browser launch assets were not found. The listener's Tor identity is unverified during offline audit; Onionmind verifies it before sending a query."
} else {
    $Items += New-InventoryItem -Name "Tor Browser" -Status "MISSING" `
        -Detail "Chat remains local without it; Tor search stays unavailable until it is installed. Onionmind never opens the browser window." `
        -WingetId "TorProject.TorBrowser" -WingetAction "install" -ExpectedDestination $ExpectedTor
}

$LocalModels = @(Get-LocalOllamaModels -ServiceAlreadyListening $OllamaListening)
if (-not $OllamaListening) {
    $Items += New-InventoryItem -Name "Local Ollama model" -Status "OPTIONAL" `
        -Detail "Ollama is not listening, so the audit did not start it or claim a model is ready."
} elseif ($LocalModels.Count -eq 0) {
    $Items += New-InventoryItem -Name "Local Ollama model" -Status "MISSING" `
        -Detail "No local model manifest was visible. Open Onionmind's Model Manager to choose a model; that direct-network download is never implicit." `
        -BlocksReady $true
} else {
    $Items += New-InventoryItem -Name "Local Ollama model" -Status "READY" `
        -Version ($LocalModels -join ", ") -Detail "Local manifests were found while Ollama was already listening."
}

if ($null -eq $WingetPath) {
    $Items += New-InventoryItem -Name "winget (apply only)" -Status "OPTIONAL" `
        -Detail "Local audit works without winget. Install Microsoft App Installer before using -Apply or -CheckLatest."
} else {
    $Items += New-InventoryItem -Name "winget (apply only)" -Status "READY" `
        -Version (Format-VersionValue (Get-LocalFileVersion -Path $WingetPath)) -Location $WingetPath `
        -Detail "It is used only after explicit direct-network consent."
}

Show-Inventory -Items $Items

$Actions = @(
    $Items | Where-Object {
        -not [string]::IsNullOrWhiteSpace($_.WingetAction) -and
        ($_.Status -eq "MISSING" -or $_.Status -eq "OUTDATED")
    }
)

if ($Apply -or $CheckLatest) {
    Show-ActionPlan -Actions $Actions
    if (-not $AllowDirectNetwork) {
        [Console]::Error.WriteLine("Refusing direct network access. Re-run with -AllowDirectNetwork after reviewing the destinations and actions above.")
        exit 4
    }
    if ($null -eq $WingetPath) {
        [Console]::Error.WriteLine("winget is required for consented dependency actions, but it was not found.")
        exit 5
    }
    if (-not (Confirm-DirectNetworkOperation -Prompt "Proceed with the disclosed winget network operation(s)?")) {
        Write-Host "Canceled. No direct-network action was started."
        exit 3
    }
}

if ($CheckLatest) {
    Write-Host ""
    Write-Host "Named winget package status"
    Write-Host "============================"
    $NamedPackages = @(
        [PSCustomObject]@{ Name = "Ollama"; Id = "Ollama.Ollama" },
        [PSCustomObject]@{ Name = "Node.js LTS"; Id = "OpenJS.NodeJS.LTS" },
        [PSCustomObject]@{ Name = "Git"; Id = "Git.Git" },
        [PSCustomObject]@{ Name = "Tor Browser"; Id = "TorProject.TorBrowser" }
    )
    foreach ($Package in $NamedPackages) {
        Write-Host ("-- {0} ({1})" -f $Package.Name, $Package.Id)
        & $WingetPath list --upgrade-available --id $Package.Id --exact --source winget `
            --accept-source-agreements --disable-interactivity
        if ($LASTEXITCODE -ne 0) {
            Write-Host ("  winget could not report this named package (exit {0})." -f $LASTEXITCODE)
        }
    }
}

if ($Apply) {
    foreach ($Action in $Actions) {
        $Verb = $Action.WingetAction
        Write-Host ""
        Write-Host ("{0} {1}" -f $Verb.ToUpperInvariant(), $Action.WingetId)
        & $WingetPath $Verb --id $Action.WingetId --exact --source winget --silent `
            --accept-package-agreements --accept-source-agreements --disable-interactivity
        if ($LASTEXITCODE -ne 0) {
            throw ("winget {0} failed for {1} with exit code {2}." -f $Verb, $Action.WingetId, $LASTEXITCODE)
        }
    }
    Write-Host ""
    Write-Host "Dependency actions finished. No service was started and no model was pulled."
    Write-Host "Run onionmind-bootstrap.cmd again for a fresh local audit."
    exit 0
}

$BlockingItems = @($Items | Where-Object { $_.BlocksReady })
if ($BlockingItems.Count -gt 0) {
    Write-Host ""
    Write-Host "Readiness: action required. The default audit made no changes."
    exit 2
}

Write-Host ""
Write-Host "Readiness: READY. The default audit made no changes."
exit 0
