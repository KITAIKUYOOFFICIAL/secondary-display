param([switch]$Once)

$ErrorActionPreference = 'SilentlyContinue'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
[System.Console]::OutputEncoding = [System.Text.Encoding]::UTF8

Add-Type -AssemblyName System.Runtime.WindowsRuntime

$asTaskGeneric = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object { $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -like '*IAsyncOperation*' })[0]

function Await($WinRtTask, $ResultType) {
    $asTask = $asTaskGeneric.MakeGenericMethod($ResultType)
    $netTask = $asTask.Invoke($null, @($WinRtTask))
    $netTask.Wait(-1) | Out-Null
    return $netTask.Result
}

function Get-MediaManager {
    try {
        $req = [Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager]::RequestAsync()
        $mgr = Await $req ([Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager])
        return $mgr
    } catch {
        return $null
    }
}

function Get-CurrentMedia {
    $result = [ordered]@{
        title=''; artist=''; album=''; duration=0; position=0; playing=$false; source=''
    }
    try {
        $manager = Get-MediaManager
        if ($null -eq $manager) { return $result }
        $session = $manager.GetCurrentSession()
        if ($null -eq $session) { return $result }
        $reqProps = $session.TryGetMediaPropertiesAsync()
        $props = Await $reqProps ([Windows.Media.Control.CurrentSessionMediaProperties])
        $timeline = $session.GetTimelineProperties()
        $playback = $session.GetPlaybackInfo()
        if ($props.Title) { $result.title = [string]$props.Title }
        if ($props.Artist) { $result.artist = [string]$props.Artist }
        if ($props.AlbumTitle) { $result.album = [string]$props.AlbumTitle }
        if ($timeline.EndTime) { $result.duration = [double]$timeline.EndTime.TotalSeconds }
        if ($timeline.Position) { $result.position = [double]$timeline.Position.TotalSeconds }
        if ($playback -and $playback.PlaybackStatus) { $result.playing = ($playback.PlaybackStatus.value__ -eq 4) }
        if ($session.SourceAppUserModelId) { $result.source = [string]$session.SourceAppUserModelId }
    } catch {
    }
    return $result
}

Get-CurrentMedia | ConvertTo-Json -Compress

if ($Once) { return }

while ($true) {
    Start-Sleep -Milliseconds 300
    try { Get-CurrentMedia | ConvertTo-Json -Compress } catch { }
}
