# Read-only diagnostic using Windows/.NET SerialPort, independent of pyserial.
param(
    [ValidatePattern('^COM[0-9]+$')][string]$PositivePort = 'COM12',
    [ValidatePattern('^COM[0-9]+$')][string]$NegativePort = 'COM11',
    [ValidateRange(1, 20)][int]$Cycles = 5
)
$ErrorActionPreference = 'Stop'
$diagnosticRows = [System.Collections.Generic.List[object]]::new()
$diagnosticTrace = [System.Collections.Generic.List[object]]::new()

function Read-DiagnosticLine($Connection) {
    $received = [System.Collections.Generic.List[byte]]::new()
    $timer = [System.Diagnostics.Stopwatch]::StartNew()
    try {
        while ($received.Count -lt 202 -and $timer.ElapsedMilliseconds -lt 2000) {
            $Connection.ReadTimeout = [Math]::Max(1, 2000 - [int]$timer.ElapsedMilliseconds)
            $received.Add([byte]$Connection.ReadByte())
            if ($received.Count -ge 2 -and $received[$received.Count - 2] -eq 13 -and $received[$received.Count - 1] -eq 10) {
                $bytes = $received.ToArray()
                $diagnosticTrace.Add([pscustomobject]@{
                    port = $Connection.PortName; direction = 'RX'
                    hex = [BitConverter]::ToString($bytes)
                    text = [Text.Encoding]::ASCII.GetString($bytes)
                })
                if ($received.Count -gt 200) { throw 'Response exceeds 200 bytes' }
                foreach ($value in $bytes) {
                    if ($value -gt 127) { throw "Non-ASCII response: $([BitConverter]::ToString($bytes))" }
                }
                return [Text.Encoding]::ASCII.GetString($bytes, 0, $bytes.Length - 2)
            }
        }
        throw 'Missing CR/LF or overlong response'
    }
    catch {
        throw "Read failed: $($_.Exception.Message); received hex=$([BitConverter]::ToString($received.ToArray()))"
    }
}

function Invoke-DiagnosticQuery($Connection, [string]$Command, [string]$ExpectedSerial) {
    $allowed = @('*IDN?', ':READ:FIRMWARE:NAME?', ':READ:CHAN:STAT? (@0)',
                 ':READ:CHAN:STAT? (@1)', ':READ:CHAN:EVENT:STAT? (@0)',
                 ':READ:CHAN:EVENT:STAT? (@1)')
    if ($Command -notin $allowed) { throw 'Query not in read-only allowlist' }
    $wire = [Text.Encoding]::ASCII.GetBytes($Command + "`r`n")
    $diagnosticTrace.Add([pscustomobject]@{
        port = $Connection.PortName; direction = 'TX'
        hex = [BitConverter]::ToString($wire); text = $Command
    })
    $Connection.Write($wire, 0, $wire.Length)
    # Bound empty/echo line handling, so a noisy port cannot loop forever.
    for ($lineNumber = 0; $lineNumber -lt 4; $lineNumber++) {
        $answer = Read-DiagnosticLine $Connection
        if ($answer -eq '' -or $answer -ceq $Command) { continue }
        if ($Command -eq '*IDN?') {
            $fields = $answer.Split(',')
            if ($fields.Count -lt 4 -or $fields[0] -notmatch 'iseg' -or
                $fields[1].Trim() -notlike 'NHS*' -or $fields[2].Trim() -ne $ExpectedSerial) {
                throw "Invalid device identification or corrupted echo: $answer"
            }
        }
        elseif ($Command -eq ':READ:FIRMWARE:NAME?') {
            if ($answer -cne 'N06C2') { throw "Invalid firmware response or corrupted echo: $answer" }
        }
        elseif ($answer -notmatch '^[0-9]+$') {
            throw "Invalid register response or corrupted echo: $answer"
        }
        return $answer
    }
    throw 'No answer after empty/echo lines'
}

foreach ($module in @(
    @{ name = 'positive'; port = $PositivePort; serial = '8000311' },
    @{ name = 'negative'; port = $NegativePort; serial = '8000312' }
)) {
    $connection = $null
    $cycle = 0
    $command = 'OPEN'
    try {
        $connection = [System.IO.Ports.SerialPort]::new($module.port, 9600,
            [System.IO.Ports.Parity]::None, 8, [System.IO.Ports.StopBits]::One)
        $connection.Handshake = [System.IO.Ports.Handshake]::None
        # Match pyserial's usual initial RTS/DTR levels; no flow control.
        $connection.DtrEnable = $true
        $connection.RtsEnable = $true
        $connection.ReadTimeout = 2000
        $connection.WriteTimeout = 2000
        $connection.Open()
        $connection.DiscardInBuffer()
        # Prior tests used only READ queries: finish a possible partial read line.
        $command = 'READ-LINE-BOUNDARY'
        $connection.Write("`r`n")
        Start-Sleep -Milliseconds 250
        $residual = [System.Collections.Generic.List[byte]]::new()
        $available = $connection.BytesToRead
        for ($index = 0; $index -lt $available; $index++) {
            $residual.Add([byte]$connection.ReadByte())
        }
        $diagnosticTrace.Add([pscustomobject]@{
            port = $module.port; direction = 'INIT-RX'
            hex = [BitConverter]::ToString($residual.ToArray())
            text = [Text.Encoding]::ASCII.GetString($residual.ToArray())
        })
        for ($cycle = 1; $cycle -le $Cycles; $cycle++) {
            foreach ($command in @('*IDN?', ':READ:FIRMWARE:NAME?',
                ':READ:CHAN:STAT? (@0)', ':READ:CHAN:EVENT:STAT? (@0)',
                ':READ:CHAN:STAT? (@1)', ':READ:CHAN:EVENT:STAT? (@1)')) {
                $answer = Invoke-DiagnosticQuery $connection $command $module.serial
                $diagnosticRows.Add([pscustomobject]@{
                    module = $module.name; port = $module.port; cycle = $cycle
                    command = $command; ok = $true; answer = $answer; error = $null
                })
            }
        }
    }
    catch {
        $diagnosticRows.Add([pscustomobject]@{
            module = $module.name; port = $module.port; cycle = $cycle
            command = $command; ok = $false; answer = $null
            error = $_.Exception.Message
        })
        # No retry or continuation on this module after a protocol error.
    }
    finally {
        if ($null -ne $connection) { $connection.Dispose() }
    }
}
[pscustomobject]@{
    transport = 'Windows .NET System.IO.Ports.SerialPort'; cycles_requested = $Cycles
    results = @($diagnosticRows.ToArray()); trace = @($diagnosticTrace.ToArray())
} | ConvertTo-Json -Depth 8 -Compress
