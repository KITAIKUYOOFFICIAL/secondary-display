param(
    [string]$Action = "play_pause",
    [double]$Position = 0
)

$ErrorActionPreference = 'SilentlyContinue'
Add-Type -AssemblyName System.Runtime.WindowsRuntime

$null = [Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager,Windows.Media,ContentType=WindowsRuntime]
$null = [Windows.Media.Control.GlobalSystemMediaTransportControlsSession,Windows.Media,ContentType=WindowsRuntime]

$asTaskGeneric = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object { $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -like '*IAsyncOperation*' })[0]

function Await($WinRtTask, $ResultType) {
    $asTask = $asTaskGeneric.MakeGenericMethod($ResultType)
    $netTask = $asTask.Invoke($null, @($WinRtTask))
    $netTask.Wait(-1) | Out-Null
    return $netTask.Result
}

try {
    $req = [Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager]::RequestAsync()
    $mgr = Await $req ([Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager])
    if ($null -eq $mgr) {
        Write-Host "No manager"
        exit 1
    }
    $session = $mgr.GetCurrentSession()
    if ($null -eq $session) {
        $sessions = $mgr.GetSessions()
        if ($sessions.Count -gt 0) { $session = $sessions[0] }
    }
    if ($session) {
        Write-Host "Target Session: $($session.SourceAppUserModelId)"
        switch ($Action) {
            "play_pause" {
                $op = $session.TryTogglePlayPauseAsync()
                $res = Await $op ([bool])
                Write-Host "PlayPause Success: $res"
            }
            "play" {
                $op = $session.TryPlayAsync()
                $res = Await $op ([bool])
                Write-Host "Play Success: $res"
            }
            "pause" {
                $op = $session.TryPauseAsync()
                $res = Await $op ([bool])
                Write-Host "Pause Success: $res"
            }
            "next" {
                $op = $session.TrySkipNextAsync()
                $res = Await $op ([bool])
                Write-Host "Next Success: $res"
            }
            "prev" {
                $op = $session.TrySkipPreviousAsync()
                $res = Await $op ([bool])
                Write-Host "Prev Success: $res"
            }
            "seek" {
                $ticks = [long]($Position * 10000000)
                $timeSpan = [TimeSpan]::FromTicks($ticks)
                $op = $session.TryChangePlaybackPositionAsync($ticks)
                $res = Await $op ([bool])
                Write-Host "Seek Success: $res"
            }
        }
    } else {
        Write-Host "No active SMTC session"
    }
} catch {
    Write-Host "Error: $_"
}
