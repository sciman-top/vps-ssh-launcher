# HG-03 v4.1: read-only, structured network evidence; no network or credential writes.
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][ValidateSet('B0','T1','T2','T3')][string]$Phase,
    [Parameter(Mandatory=$true)][ValidateSet('HOTSPOT_BASELINE','WIRED_BASELINE','DUAL_PROVEN','WIRED_ONLY','UNVERIFIED')][string]$NetworkMode,
    [string]$ApprovedHost = '',
    [ValidateRange(1,65535)][int]$ApprovedPort = 443,
    [Parameter(Mandatory=$true)][string]$EvidenceDirectory,
    [long]$ApplicationProcessId = 0
)
$ErrorActionPreference = 'Stop'
if ($ApprovedHost -and ($ApprovedHost -match '[/\\@?#\s]' -or $ApprovedHost -match '://')) { throw 'Supply only an approved hostname or IP, not a URL or credentials.' }
$errorsFound = New-Object System.Collections.Generic.List[string]
function Read-Probe([string]$Name, [scriptblock]$Action) {
    try { & $Action } catch { $errorsFound.Add($Name + ': ' + $_.Exception.GetType().Name) }
}
function Get-AddressText($Value) {
    if ($null -eq $Value) { return $null }
    try { return [string]$Value.IPAddress } catch { return [string]$Value }
}
function Get-TcpProbe([string]$Name, [int]$Port) {
    $addresses = @([System.Net.Dns]::GetHostAddresses($Name) | ForEach-Object { $_.ToString() })
    if ($addresses.Count -eq 0) { throw 'No DNS address returned.' }
    $address = $addresses[0]
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $connect = $client.BeginConnect($address, $Port, $null, $null)
        if (-not $connect.AsyncWaitHandle.WaitOne(5000)) { throw 'TCP connect timed out.' }
        $client.EndConnect($connect)
        $local = ([System.Net.IPEndPoint]$client.Client.LocalEndPoint).Address.ToString()
        return [pscustomobject]@{
            ComputerName = $Name
            ResolvedAddresses = $addresses
            RemoteAddress = $address
            RemotePort = $Port
            SourceAddress = $local
            TcpTestSucceeded = $true
        }
    } catch {
        return [pscustomobject]@{
            ComputerName = $Name
            ResolvedAddresses = $addresses
            RemoteAddress = $address
            RemotePort = $Port
            SourceAddress = ''
            TcpTestSucceeded = $false
        }
    } finally {
        $client.Close()
    }
}
function Find-Route([string]$Address) {
    try {
        if (-not $Address) { return @() }
        return @(Find-NetRoute -RemoteIPAddress $Address -ErrorAction Stop)
    } catch {
        return @()
    }
}
function Get-StandardRoutes([string]$Address) {
    try { return @(Find-NetRoute -RemoteIPAddress $Address -ErrorAction Stop) } catch { return @() }
}
function Test-TunnelActive($Tunnel, $TunnelRoutes, $DefaultRoutes) {
    $name = [string]$Tunnel.Name
    if (-not $name) { return $false }
    if ($DefaultRoutes | Where-Object { $_.InterfaceAlias -eq $name }) {
        return $true
    }
    return @($TunnelRoutes | Where-Object {
        $_.InterfaceAlias -eq $name
    }).Count -gt 0
}
$now = Get-Date
$adapters = @(Read-Probe 'adapters' { Get-NetAdapter | Select-Object Name,InterfaceIndex,Status,LinkSpeed,MediaType })
$addresses = @(Read-Probe 'addresses' { Get-NetIPAddress | Select-Object InterfaceAlias,InterfaceIndex,AddressFamily,IPAddress,PrefixLength })
$routes = @(Read-Probe 'defaultRoutes' { Get-NetRoute | Where-Object { $_.DestinationPrefix -in @('0.0.0.0/0','::/0') } | Select-Object InterfaceAlias,InterfaceIndex,DestinationPrefix,NextHop,RouteMetric,Store,PolicyStore })
$metrics = @(Read-Probe 'metrics' { Get-NetIPInterface | Select-Object InterfaceAlias,InterfaceIndex,AddressFamily,InterfaceMetric,ConnectionState })
$globalAddresses = @(Read-Probe 'globalAddresses' { Get-NetIPAddress | Where-Object { $_.IPAddress -notlike 'fe80:*' -and $_.IPAddress -notlike '169.254.*' -and $_.IPAddress -notin @('127.0.0.1','::1') } | Select-Object InterfaceAlias,InterfaceIndex,AddressFamily,IPAddress,PrefixLength,SuffixOrigin })
$dns = @(Read-Probe 'dns' { Get-DnsClientServerAddress | Select-Object InterfaceAlias,InterfaceIndex,AddressFamily,ServerAddresses })
$wifi = @(Read-Probe 'wifi' { Get-NetAdapter -Physical | Where-Object { $_.MediaType -eq 'Native 802.11' } | Select-Object Name,InterfaceIndex,Status,LinkSpeed })
$proxy = @(Read-Probe 'winhttpProxy' { Get-ItemProperty -LiteralPath 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings' -ErrorAction SilentlyContinue | Select-Object ProxyEnable,ProxyServer,ProxyOverride,AutoConfigURL })
$routesAlternative = @(Read-Probe 'routePrint' { route print -4 })
$tunnels = @(Read-Probe 'tunnelAdapters' { Get-NetAdapter | Where-Object { $_.InterfaceDescription -match '(?i)vpn|tap|tun|wireguard|tailscale|wintun' } | Select-Object Name,InterfaceIndex,Status,InterfaceDescription })
$tunnelRoutes = @()
$processPath = $null
$processCapture = @()
$processName = $null
$processCaptureError = $null
if ($ApplicationProcessId -gt 0) {
    $processInfo = Get-Process -Id $ApplicationProcessId -ErrorAction SilentlyContinue
    if (-not $processInfo) {
        $processCaptureError = 'processNotFound'
        $errorsFound.Add('processConnections: processNotFound')
    } else {
        $processPath = $processInfo.Path
        $processName = $processInfo.ProcessName
        try {
            $netstatOutput = @(netstat -ano -p tcp)
            if ($LASTEXITCODE -ne 0) { throw 'netstat returned a non-zero status.' }
            $processCapture = @($netstatOutput | Where-Object {
                $columns = @($_ -split '\s+' | Where-Object { $_ })
                $columns.Count -ge 5 -and $columns[0] -eq 'TCP' -and $columns[4] -eq [string]$ApplicationProcessId
            } | ForEach-Object {
                $columns = @($_ -split '\s+' | Where-Object { $_ })
                [pscustomobject]@{ LocalEndpoint = $columns[1]; RemoteEndpoint = $columns[2]; State = $columns[3]; OwningProcess = $ApplicationProcessId }
            })
        } catch {
            $processCaptureError = $_.Exception.GetType().Name
            $errorsFound.Add('processConnections: ' + $processCaptureError)
        }
    }
}
$routeEvidence = @()
$probe = @()
if ($ApprovedHost) {
    $probe = @(Read-Probe 'tcp' {
        $test = Get-TcpProbe $ApprovedHost $ApprovedPort
        if ($test.RemoteAddress) {
            $routeEvidence = @(Get-StandardRoutes $test.RemoteAddress | ForEach-Object {
                [pscustomobject]@{
                    RemoteAddress = $test.RemoteAddress
                    SourceAddress = [string](Get-AddressText $_.IPAddress)
                    InterfaceAlias = [string]$_.InterfaceAlias
                    InterfaceIndex = [int]$_.InterfaceIndex
                    DestinationPrefix = [string]$_.DestinationPrefix
                    NextHop = [string]$_.NextHop
                    RouteMetric = [string]$_.RouteMetric
                }
            })
        }
        $test
    })
}
if (@($tunnels).Count -gt 0) {
    $tunnelRoutes = @(Read-Probe 'tunnelRoutes' {
        Get-NetRoute | Where-Object {
            $_.InterfaceAlias -in @($tunnels.Name) -and
            $_.DestinationPrefix -match '^(0\.0\.0\.0/1|128\.0\.0\.0/1|::/1|8000::/1)'
        } | Select-Object InterfaceAlias,InterfaceIndex,DestinationPrefix,NextHop,RouteMetric
    })
}
$wiredAdapters = @($adapters | Where-Object { $_.MediaType -eq '802.3' -and $_.Name -notmatch '(?i)vEthernet|vethernet|Loopback|TAP|TUN|VPN|cfw|default switch' })
$wiredUp = @($wiredAdapters | Where-Object { $_.Status -eq 'Up' }).Count -gt 0
$wifiUp = @($wifi | Where-Object { $_.Status -eq 'Up' }).Count -gt 0
$disconnectedWiredNames = @($wiredAdapters | Where-Object { $_.Status -ne 'Up' } | Select-Object -ExpandProperty Name)
$staleWiredAddresses = @($addresses | Where-Object { $_.InterfaceAlias -in $disconnectedWiredNames -and $_.AddressFamily -eq 2 -and $_.IPAddress -notlike '169.254.*' })
if ($staleWiredAddresses.Count -gt 0) { $errorsFound.Add('staleWiredAddress') }
$primaryRoutes = @($routes | Where-Object { $_.DestinationPrefix -eq '0.0.0.0/0' -and $_.InterfaceAlias -notmatch '(?i)vEthernet|Loopback|cfw|TAP|TUN|VPN' })
$primaryWired = @($primaryRoutes | Where-Object { $_.InterfaceAlias -in @($wiredAdapters.Name) }).Count -gt 0
$defaultWifi = @($primaryRoutes | Where-Object { $_.InterfaceAlias -in @($wifi.Name) }).Count -gt 0
$activeTunnels = @($tunnels | Where-Object { Test-TunnelActive $_ $tunnelRoutes $routes })
$endpointSource = $null
$endpointInterface = $null
if (@($probe).Count -gt 0) {
    $endpointSource = $probe[0].SourceAddress
    if ($probe[0].RemoteAddress) {
        $endpointInterface = @(Get-StandardRoutes $probe[0].RemoteAddress | Where-Object { $_.InterfaceAlias } | Select-Object -First 1 -ExpandProperty InterfaceAlias)
    }
}
if (-not $endpointSource) {
    $endpointSource = @($globalAddresses | Where-Object { $_.InterfaceAlias -in @($wiredAdapters.Name) }) | Select-Object -First 1 -ExpandProperty IPAddress
    if (-not $endpointSource) {
        $endpointSource = @($globalAddresses | Where-Object { $_.InterfaceAlias -in @($wifi.Name) }) | Select-Object -First 1 -ExpandProperty IPAddress
    }
}
$modeConsistent = switch ($NetworkMode) {
    'WIRED_ONLY' { $wiredUp -and -not $wifiUp -and $activeTunnels.Count -eq 0 }
    'WIRED_BASELINE' { $wiredUp -and $primaryWired -and -not $defaultWifi -and $activeTunnels.Count -eq 0 }
    'HOTSPOT_BASELINE' { $wifiUp -and -not $wiredUp -and $defaultWifi -and $activeTunnels.Count -eq 0 }
    'DUAL_PROVEN' { $wiredUp -and $wifiUp -and $activeTunnels.Count -eq 0 -and @($probe | Where-Object { $_.TcpTestSucceeded -and $_.SourceAddress }).Count -gt 0 }
    default { $false }
}
$evidenceStatus = if ($errorsFound.Count -eq 0 -and $modeConsistent) { 'OBSERVED' } else { 'UNVERIFIED' }
if ($NetworkMode -eq 'UNVERIFIED') { $evidenceStatus = 'UNVERIFIED' }
$summary = [ordered]@{
    version = 'HG-03 Integration v4.1'
    phase = $Phase
    networkModeDeclared = $NetworkMode
    networkModeConsistent = $modeConsistent
    evidenceStatus = $evidenceStatus
    capturedAt = $now.ToString('o')
    processId = if ($ApplicationProcessId -gt 0) { $ApplicationProcessId } else { $null }
    processName = $processName
    processPath = $processPath
    processConnections = $processCapture
    processCaptureError = $processCaptureError
    endpointSourceAddress = $endpointSource
    endpointInterface = $endpointInterface
    primaryWiredRoute = $primaryWired
    primaryWifiRoute = $defaultWifi
    staleWiredAddress = $staleWiredAddresses
    adapters = $adapters
    globalAddresses = $globalAddresses
    wifiAdapters = $wifi
    tunnelAdapters = $tunnels
    activeTunnelAdapters = $activeTunnels
    tunnelRoutes = $tunnelRoutes
    defaultRoutes = $routes
    routeEvidence = $routeEvidence
    proxy = $proxy
    approvedTcpProbe = $probe
    errors = @($errorsFound.ToArray())
}
$report = [ordered]@{
    summary = $summary
    interfaces = [ordered]@{ interfaces = $metrics; dns = $dns }
    addresses = $addresses
    routeOutput = $routesAlternative
    note = 'Private local evidence. This does not prove browser, Git, CPA, or GLM business success. An application request and server receipt are required separately.'
}
# This writes only the requested evidence file. It changes no network setting.
New-Item -ItemType Directory -Path $EvidenceDirectory -Force | Out-Null
$file = Join-Path $EvidenceDirectory ('office-{0}-{1}-{2}.json' -f $Phase,$now.ToString('yyyyMMdd-HHmmss'),[Guid]::NewGuid().ToString('N').Substring(0,8))
$json = $report | ConvertTo-Json -Depth 6
if (Test-Path -LiteralPath $file) { throw "Evidence file already exists: $file" }
Set-Content -LiteralPath $file -Value $json -Encoding UTF8
Write-Output ('EVIDENCE_STATUS=' + $evidenceStatus)
Write-Output ('Saved private evidence: ' + $file)
Write-Output 'No business acceptance is implied.'
