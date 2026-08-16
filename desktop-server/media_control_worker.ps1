$ErrorActionPreference = 'SilentlyContinue'
Add-Type -AssemblyName System.Runtime.WindowsRuntime

$null = [Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager,Windows.Media,ContentType=WindowsRuntime]
$null = [Windows.Media.Control.GlobalSystemMediaTransportControlsSession,Windows.Media,ContentType=WindowsRuntime]

$asTaskGeneric = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object { $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -like '*IAsyncOperation*' })[0]

function Await($WinRtTask, $ResultType, $TimeoutMs = 500) {
    try {
        $asTask = $asTaskGeneric.MakeGenericMethod($ResultType)
        $netTask = $asTask.Invoke($null, @($WinRtTask))
        $done = $netTask.Wait($TimeoutMs)
        if ($done) {
            return $netTask.Result
        } else {
            return $false
        }
    } catch {
        return $false
    }
}

Write-Host "READY"
[Console]::Out.Flush()

while ($line = [Console]::In.ReadLine()) {
    if (-not $line) { continue }
    try {
        $parts = $line.Trim().Split(" ")
        $action = $parts[0]
        $position = if ($parts.Length -gt 1) { [double]$parts[1] } else { 0 }

        $req = [Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager]::RequestAsync()
        $mgr = Await $req ([Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager])
        if ($null -eq $mgr) {
            Write-Host "ERR:NoManager"
            [Console]::Out.Flush()
            continue
        }
        $sessions = $mgr.GetSessions()
        $session = $null
        if ($sessions -and $sessions.Count -gt 0) {
            # 优先寻找当前正在播放中的媒体会话 (PlaybackStatus -eq 4)
            foreach ($s in $sessions) {
                try {
                    $info = $s.GetPlaybackInfo()
                    if ($info -and $info.PlaybackStatus -eq 4) {
                        $session = $s
                        break
                    }
                } catch {}
            }
            if ($null -eq $session) {
                $session = $mgr.GetCurrentSession()
            }
            if ($null -eq $session) {
                $session = $sessions[0]
            }
        } else {
            $session = $mgr.GetCurrentSession()
        }

        if ($session) {
            switch ($action) {
                "play_pause" {
                    $res = $false
                    try {
                        $info = $session.GetPlaybackInfo()
                        if ($info -and $info.PlaybackStatus -eq 4) {
                            $op = $session.TryPauseAsync()
                            $res = Await $op ([bool])
                        } elseif ($info -and $info.PlaybackStatus -eq 5) {
                            $op = $session.TryPlayAsync()
                            $res = Await $op ([bool])
                        } else {
                            $op = $session.TryTogglePlayPauseAsync()
                            $res = Await $op ([bool])
                        }
                    } catch {
                        $op = $session.TryTogglePlayPauseAsync()
                        $res = Await $op ([bool])
                    }
                    Write-Host "OK:play_pause:$res"
                }
                "play" {
                    $op = $session.TryPlayAsync()
                    $res = Await $op ([bool])
                    Write-Host "OK:play:$res"
                }
                "pause" {
                    $op = $session.TryPauseAsync()
                    $res = Await $op ([bool])
                    Write-Host "OK:pause:$res"
                }
                "next" {
                    $op = $session.TrySkipNextAsync()
                    $res = Await $op ([bool])
                    Write-Host "OK:next:$res"
                }
                "prev" {
                    $op = $session.TrySkipPreviousAsync()
                    $res = Await $op ([bool])
                    Write-Host "OK:prev:$res"
                }
                "seek" {
                    $src = "$($session.SourceAppUserModelId)"
                    if ($src -like "*cloudmusic*" -or $src -like "*orpheus*") {
                        Write-Host "OK:seek:NCM_PASSTHROUGH"
                    } else {
                        $ticks = [long]($position * 10000000)
                        $op = $session.TryChangePlaybackPositionAsync($ticks)
                        $res = Await $op ([bool]) 300
                        Write-Host "OK:seek:$res"
                    }
                }
                default {
                    Write-Host "ERR:UnknownAction"
                }
            }
        } else {
            Write-Host "ERR:NoSession"
        }
    } catch {
        Write-Host "ERR:$_"
    }
    [Console]::Out.Flush()
}
