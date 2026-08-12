Set-StrictMode -Version Latest

function Get-ZlapiNormalizedPath {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    return [IO.Path]::GetFullPath($Path).TrimEnd(
        [IO.Path]::DirectorySeparatorChar,
        [IO.Path]::AltDirectorySeparatorChar
    )
}

function Test-ZlapiPathWithinRoot {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,
        [Parameter(Mandatory = $true)]
        [string]$Root
    )

    try {
        $normalizedPath = Get-ZlapiNormalizedPath -Path $Path
        $normalizedRoot = Get-ZlapiNormalizedPath -Path $Root
    }
    catch {
        return $false
    }

    return $normalizedPath.Equals($normalizedRoot, [StringComparison]::OrdinalIgnoreCase) -or
        $normalizedPath.StartsWith(
            $normalizedRoot + [IO.Path]::DirectorySeparatorChar,
            [StringComparison]::OrdinalIgnoreCase
        )
}

function Get-ZlapiProcessExecutablePath {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [AllowNull()]
        [object]$Process
    )

    if ($null -eq $Process) {
        return ""
    }

    # Win32_Process.ExecutablePath identifies the actual image. A venv-created
    # base-Python child can preserve a launcher-looking argv[0], so CommandLine
    # is only a fallback when WMI cannot expose the image path.
    $executablePath = [string]$Process.ExecutablePath
    if (-not [string]::IsNullOrWhiteSpace($executablePath)) {
        return $executablePath
    }

    $commandLine = ([string]$Process.CommandLine).Trim()
    if (-not [string]::IsNullOrWhiteSpace($commandLine)) {
        if ($commandLine.StartsWith('"')) {
            $closingQuote = $commandLine.IndexOf('"', 1)
            if ($closingQuote -gt 1) {
                return $commandLine.Substring(1, $closingQuote - 1)
            }
        }
        else {
            $firstSpace = $commandLine.IndexOf(' ')
            if ($firstSpace -lt 0) {
                return $commandLine
            }
            return $commandLine.Substring(0, $firstSpace)
        }
    }
    return ""
}

function Get-ZlapiProcessCommandExecutablePath {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [AllowNull()]
        [object]$Process
    )

    if ($null -eq $Process) {
        return ""
    }

    $commandLine = ([string]$Process.CommandLine).Trim()
    if ([string]::IsNullOrWhiteSpace($commandLine)) {
        return ""
    }
    if ($commandLine.StartsWith('"')) {
        $closingQuote = $commandLine.IndexOf('"', 1)
        if ($closingQuote -gt 1) {
            return $commandLine.Substring(1, $closingQuote - 1)
        }
        return ""
    }
    $firstSpace = $commandLine.IndexOf(' ')
    if ($firstSpace -lt 0) {
        return $commandLine
    }
    return $commandLine.Substring(0, $firstSpace)
}

function Get-ZlapiApprovedBasePythonPaths {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string]$ServiceRoot
    )

    $configs = @()
    $currentConfig = Join-Path $ServiceRoot "current\.venv\pyvenv.cfg"
    if (Test-Path -LiteralPath $currentConfig) {
        $configs += $currentConfig
    }
    $releasesPath = Join-Path $ServiceRoot "releases"
    if (Test-Path -LiteralPath $releasesPath) {
        $configs += @(
            Get-ChildItem -LiteralPath $releasesPath -Directory -ErrorAction SilentlyContinue |
                ForEach-Object { Join-Path $_.FullName ".venv\pyvenv.cfg" } |
                Where-Object { Test-Path -LiteralPath $_ }
        )
    }

    $paths = @()
    foreach ($config in ($configs | Select-Object -Unique)) {
        $executable = Get-Content -LiteralPath $config -ErrorAction SilentlyContinue |
            Where-Object { $_ -match '^\s*executable\s*=\s*(.+?)\s*$' } |
            Select-Object -First 1
        if ($executable -and $executable -match '^\s*executable\s*=\s*(.+?)\s*$') {
            try {
                $paths += Get-ZlapiNormalizedPath -Path $Matches[1]
            }
            catch {
                # Ignore malformed stale release metadata.
            }
        }
    }
    return @($paths | Sort-Object -Unique)
}

function Test-ZlapiProcessUsesServicePython {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [AllowNull()]
        [object]$Process,
        [Parameter(Mandatory = $true)]
        [string]$ServiceRoot,
        [string[]]$ApprovedBasePythonPaths = @()
    )

    if ($null -eq $Process) {
        return $false
    }

    $actualExecutablePath = Get-ZlapiProcessExecutablePath -Process $Process
    if ([string]::IsNullOrWhiteSpace($actualExecutablePath)) {
        return $false
    }
    if (Test-ZlapiPathWithinRoot -Path $actualExecutablePath -Root $ServiceRoot) {
        return $true
    }
    try {
        $normalizedExecutable = Get-ZlapiNormalizedPath -Path $actualExecutablePath
    }
    catch {
        return $false
    }
    $isApprovedBase = @($ApprovedBasePythonPaths | Where-Object {
        $normalizedExecutable.Equals($_, [StringComparison]::OrdinalIgnoreCase)
    }).Count -gt 0
    if (-not $isApprovedBase) {
        return $false
    }

    # A base-Python child is only independently scoped when its argv[0] still
    # points at a launcher underneath ServiceRoot. Get-ZlapiModuleProcesses also
    # accepts an approved base child while it is attached to a verified root.
    $commandExecutablePath = Get-ZlapiProcessCommandExecutablePath -Process $Process
    return -not [string]::IsNullOrWhiteSpace($commandExecutablePath) -and
        (Test-ZlapiPathWithinRoot -Path $commandExecutablePath -Root $ServiceRoot)
}

function Test-ZlapiRootModuleProcess {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [AllowNull()]
        [object]$Process,
        [Parameter(Mandatory = $true)]
        [string]$ServiceRoot
    )

    if ($null -eq $Process) {
        return $false
    }

    $executablePath = Get-ZlapiProcessExecutablePath -Process $Process
    if ([string]::IsNullOrWhiteSpace($executablePath)) {
        return $false
    }
    $currentVenvScripts = Join-Path $ServiceRoot "current\.venv\Scripts"
    if (Test-ZlapiPathWithinRoot -Path $executablePath -Root $currentVenvScripts) {
        return $true
    }
    $releasesRoot = Join-Path $ServiceRoot "releases"
    if (-not (Test-ZlapiPathWithinRoot -Path $executablePath -Root $releasesRoot)) {
        return $false
    }
    try {
        $normalizedExecutable = Get-ZlapiNormalizedPath -Path $executablePath
    }
    catch {
        return $false
    }
    return $normalizedExecutable -match '(?i)\\releases\\[^\\]+\\\.venv\\Scripts\\pythonw?\.exe$'
}

function Get-ZlapiModuleProcesses {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [ValidateSet("uvicorn", "control_center.worker", "control_center.listener")]
        [string]$Module,
        [Parameter(Mandatory = $true)]
        [string]$ServiceRoot
    )

    $modulePattern = '(?i)(?:^|\s)-m\s+["'']?' +
        [Text.RegularExpressions.Regex]::Escape($Module) +
        '["'']?(?=\s|$)'
    $appPattern = '(?i)(?:^|\s)["'']?control_center\.main:app["'']?(?=\s|$)'
    $approvedBasePythonPaths = @(Get-ZlapiApprovedBasePythonPaths -ServiceRoot $ServiceRoot)
    $processes = @(Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'" -ErrorAction Stop)
    $candidates = @(
        $processes | Where-Object {
            if ($null -eq $_) {
                return $false
            }
            $commandLine = [string]$_.CommandLine
            if ([string]::IsNullOrWhiteSpace($commandLine) -or $commandLine -notmatch $modulePattern) {
                return $false
            }
            if ($Module -eq "uvicorn" -and $commandLine -notmatch $appPattern) {
                return $false
            }
            return $true
        }
    )
    $rootCandidates = @($candidates | Where-Object {
        Test-ZlapiRootModuleProcess -Process $_ -ServiceRoot $ServiceRoot
    })

    return @(
        $candidates | Where-Object {
            if (Test-ZlapiProcessUsesServicePython `
                -Process $_ `
                -ServiceRoot $ServiceRoot `
                -ApprovedBasePythonPaths $approvedBasePythonPaths) {
                return $true
            }

            $actualExecutablePath = Get-ZlapiProcessExecutablePath -Process $_
            try {
                $normalizedExecutable = Get-ZlapiNormalizedPath -Path $actualExecutablePath
            }
            catch {
                return $false
            }
            $isApprovedBase = @($approvedBasePythonPaths | Where-Object {
                $normalizedExecutable.Equals($_, [StringComparison]::OrdinalIgnoreCase)
            }).Count -gt 0
            if (-not $isApprovedBase) {
                return $false
            }
            foreach ($rootProcess in $rootCandidates) {
                if (Test-ZlapiProcessDescendsFrom `
                    -ProcessId ([int]$_.ProcessId) `
                    -AncestorProcessId ([int]$rootProcess.ProcessId)) {
                    return $true
                }
            }
            return $false
        }
    )
}

function Test-ZlapiSameProcessInstance {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [AllowNull()]
        [object]$Snapshot,
        [Parameter(Mandatory = $true)]
        [AllowNull()]
        [object]$Current
    )

    if ($null -eq $Snapshot -or $null -eq $Current) {
        return $false
    }

    if ([int]$Snapshot.ProcessId -ne [int]$Current.ProcessId) {
        return $false
    }
    if ($Snapshot.CreationDate -and $Current.CreationDate) {
        return ([DateTime]$Snapshot.CreationDate) -eq ([DateTime]$Current.CreationDate)
    }
    return ([string]$Snapshot.ExecutablePath).Equals(
        [string]$Current.ExecutablePath,
        [StringComparison]::OrdinalIgnoreCase
    ) -and ([string]$Snapshot.CommandLine).Equals(
        [string]$Current.CommandLine,
        [StringComparison]::Ordinal
    )
}

function Remove-ZlapiVerifiedProcessSnapshot {
    [CmdletBinding()]
    param(
        [object[]]$Processes = @(),
        [Parameter(Mandatory = $true)]
        [string]$ServiceRoot
    )

    $rootSnapshots = @($Processes | Where-Object {
        if ($null -eq $_) {
            return $false
        }
        Test-ZlapiRootModuleProcess -Process $_ -ServiceRoot $ServiceRoot
    })
    foreach ($snapshot in $rootSnapshots) {
        $current = Get-CimInstance Win32_Process -Filter "ProcessId=$([int]$snapshot.ProcessId)" -ErrorAction SilentlyContinue
        if ($current -and (Test-ZlapiSameProcessInstance -Snapshot $snapshot -Current $current)) {
            & taskkill.exe /PID ([int]$snapshot.ProcessId) /T /F | Out-Null
        }
    }

    # taskkill normally removes the whole tree. This second pass handles a base
    # child that was detached by an older WinSW configuration during shutdown.
    foreach ($snapshot in @($Processes | Where-Object { $null -ne $_ })) {
        $current = Get-CimInstance Win32_Process -Filter "ProcessId=$([int]$snapshot.ProcessId)" -ErrorAction SilentlyContinue
        if ($current -and (Test-ZlapiSameProcessInstance -Snapshot $snapshot -Current $current)) {
            Stop-Process -Id ([int]$snapshot.ProcessId) -Force -ErrorAction SilentlyContinue
        }
    }
}

function Remove-ZlapiOrphanModuleProcesses {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [ValidateSet("uvicorn", "control_center.worker", "control_center.listener")]
        [string]$Module,
        [Parameter(Mandatory = $true)]
        [string]$ServiceRoot,
        [int]$TimeoutSeconds = 15
    )

    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    $reported = $false
    do {
        $matches = @(Get-ZlapiModuleProcesses -Module $Module -ServiceRoot $ServiceRoot)
        if ($matches.Count -eq 0) {
            return
        }
        if (-not $reported) {
            $processIds = ($matches | ForEach-Object { [string]$_.ProcessId }) -join ", "
            Write-Warning "Removing stale $Module process(es) scoped to ${ServiceRoot}: $processIds"
            $reported = $true
        }
        $rootProcesses = @($matches | Where-Object {
            if ($null -eq $_) {
                return $false
            }
            Test-ZlapiRootModuleProcess -Process $_ -ServiceRoot $ServiceRoot
        })
        if ($rootProcesses.Count -gt 0) {
            # A Windows venv launcher creates a base-Python child. Killing only
            # the launcher can orphan that child, so terminate the verified root
            # tree atomically while it is still attached.
            foreach ($process in $rootProcesses) {
                & taskkill.exe /PID ([int]$process.ProcessId) /T /F | Out-Null
            }
        }
        else {
            # If an older deployment already orphaned the base-Python child, it
            # is still tightly scoped by exact role command and a base executable
            # declared by a pyvenv.cfg underneath ServiceRoot.
            foreach ($process in $matches) {
                Stop-Process -Id ([int]$process.ProcessId) -Force -ErrorAction SilentlyContinue
            }
        }
        Start-Sleep -Milliseconds 500
    } while ([DateTime]::UtcNow -lt $deadline)

    $remaining = @(Get-ZlapiModuleProcesses -Module $Module -ServiceRoot $ServiceRoot)
    if ($remaining.Count -gt 0) {
        $details = ($remaining | ForEach-Object { "PID $($_.ProcessId): $($_.CommandLine)" }) -join "; "
        throw "Unable to stop stale $Module process(es) within $TimeoutSeconds seconds: $details"
    }
}

function Stop-ZlapiServiceSafely {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string]$Name,
        [Parameter(Mandatory = $true)]
        [string]$ServiceRoot,
        [AllowEmptyString()]
        [ValidateSet("", "uvicorn", "control_center.worker", "control_center.listener")]
        [string]$Module = "",
        [int]$TimeoutSeconds = 60
    )

    $processSnapshot = if (-not [string]::IsNullOrWhiteSpace($Module)) {
        @(Get-ZlapiModuleProcesses -Module $Module -ServiceRoot $ServiceRoot)
    }
    else {
        @()
    }
    $service = Get-Service -Name $Name -ErrorAction SilentlyContinue
    if ($service -and $service.Status -ne [ServiceProcess.ServiceControllerStatus]::Stopped) {
        & sc.exe stop $Name | Out-Null
        try {
            $service.WaitForStatus(
                [ServiceProcess.ServiceControllerStatus]::Stopped,
                [TimeSpan]::FromSeconds($TimeoutSeconds)
            )
        }
        catch {
            $service.Refresh()
            if ($service.Status -ne [ServiceProcess.ServiceControllerStatus]::Stopped) {
                $serviceInfo = Get-CimInstance Win32_Service -Filter "Name='$Name'" -ErrorAction Stop
                $servicePid = [int]$serviceInfo.ProcessId
                if ($servicePid -le 0) {
                    throw "Service $Name did not stop and SCM reports no process ID."
                }

                $wrapperProcess = Get-CimInstance Win32_Process -Filter "ProcessId=$servicePid" -ErrorAction Stop
                $wrapperPath = Get-ZlapiProcessExecutablePath -Process $wrapperProcess
                $expectedWrapper = Join-Path $ServiceRoot "bin\$Name.exe"
                $normalizedWrapper = Get-ZlapiNormalizedPath -Path $wrapperPath
                $normalizedExpected = Get-ZlapiNormalizedPath -Path $expectedWrapper
                if (-not $normalizedWrapper.Equals($normalizedExpected, [StringComparison]::OrdinalIgnoreCase)) {
                    throw "Refusing to terminate PID $servicePid for $Name because '$wrapperPath' is not '$expectedWrapper'."
                }

                Write-Warning "Service $Name did not stop within $TimeoutSeconds seconds; terminating its verified WinSW process tree."
                & taskkill.exe /PID $servicePid /T /F | Out-Null
                $service = Get-Service -Name $Name -ErrorAction Stop
                $service.WaitForStatus(
                    [ServiceProcess.ServiceControllerStatus]::Stopped,
                    [TimeSpan]::FromSeconds(15)
                )
            }
        }
    }

    if (-not [string]::IsNullOrWhiteSpace($Module)) {
        Remove-ZlapiVerifiedProcessSnapshot -Processes $processSnapshot -ServiceRoot $ServiceRoot
        Remove-ZlapiOrphanModuleProcesses -Module $Module -ServiceRoot $ServiceRoot
    }
}

function Start-ZlapiServiceSafely {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string]$Name,
        [int]$TimeoutSeconds = 30
    )

    $service = Get-Service -Name $Name -ErrorAction Stop
    if ($service.Status -ne [ServiceProcess.ServiceControllerStatus]::Running) {
        Start-Service -Name $Name
        $service = Get-Service -Name $Name -ErrorAction Stop
        $service.WaitForStatus(
            [ServiceProcess.ServiceControllerStatus]::Running,
            [TimeSpan]::FromSeconds($TimeoutSeconds)
        )
    }
}

function Test-ZlapiProcessDescendsFrom {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [int]$ProcessId,
        [Parameter(Mandatory = $true)]
        [int]$AncestorProcessId
    )

    $seen = @{}
    $cursor = $ProcessId
    foreach ($depth in 1..32) {
        if ($cursor -le 0 -or $seen.ContainsKey($cursor)) {
            return $false
        }
        $seen[$cursor] = $true
        $process = Get-CimInstance Win32_Process -Filter "ProcessId=$cursor" -ErrorAction SilentlyContinue
        if (-not $process) {
            return $false
        }
        $parent = [int]$process.ParentProcessId
        if ($parent -eq $AncestorProcessId) {
            return $true
        }
        $cursor = $parent
    }
    return $false
}

function Assert-ZlapiSingleServiceModuleProcess {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string]$ServiceName,
        [Parameter(Mandatory = $true)]
        [ValidateSet("uvicorn", "control_center.worker", "control_center.listener")]
        [string]$Module,
        [Parameter(Mandatory = $true)]
        [string]$ServiceRoot,
        [int]$TimeoutSeconds = 30
    )

    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    $lastSummary = "service has not started"
    do {
        $service = Get-CimInstance Win32_Service -Filter "Name='$ServiceName'" -ErrorAction SilentlyContinue
        $matches = @(Get-ZlapiModuleProcesses -Module $Module -ServiceRoot $ServiceRoot)
        $rootMatches = @($matches | Where-Object {
            Test-ZlapiRootModuleProcess -Process $_ -ServiceRoot $ServiceRoot
        })
        $servicePid = if ($service) { [int]$service.ProcessId } else { 0 }
        $serviceState = if ($service) { [string]$service.State } else { "Missing" }
        $isRunning = $service -and $serviceState -eq "Running" -and $servicePid -gt 0
        $inServiceChain = $isRunning -and $matches.Count -gt 0
        if ($inServiceChain) {
            foreach ($process in $matches) {
                if (-not (Test-ZlapiProcessDescendsFrom `
                    -ProcessId ([int]$process.ProcessId) `
                    -AncestorProcessId $servicePid)) {
                    $inServiceChain = $false
                    break
                }
            }
        }
        if ($isRunning -and $rootMatches.Count -eq 1 -and $inServiceChain) {
            return
        }

        $processIds = if ($matches.Count -gt 0) {
            ($matches | ForEach-Object { [string]$_.ProcessId }) -join ", "
        }
        else {
            "none"
        }
        $lastSummary = "state=$serviceState, wrapper PID=$servicePid, root count=$($rootMatches.Count), scoped process count=$($matches.Count), scoped PID(s)=$processIds, valid chain=$inServiceChain"
        Start-Sleep -Milliseconds 500
    } while ([DateTime]::UtcNow -lt $deadline)

    throw "$ServiceName did not reach one verified $Module service chain within $TimeoutSeconds seconds ($lastSummary)."
}
