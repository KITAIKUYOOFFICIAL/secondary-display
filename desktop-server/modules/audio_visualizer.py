"""
Audio Visualizer Module
使用 Windows WASAPI Loopback 实时无损抓取系统扬声器/耳机音频输出流，
并通过 FFT 算法提取 24 段高保真音频跳动频谱（重低音、人声、中高音）。
"""

import threading
import time
import numpy as np

try:
    import pyaudiowpatch as pyaudio
    PYAUDIO_AVAILABLE = True
except ImportError:
    PYAUDIO_AVAILABLE = False


class AudioVisualizer:
    def __init__(self, num_bands: int = 24):
        self.num_bands = num_bands
        self._running = False
        self._thread = None
        self._lock = threading.Lock()
        
        # 频谱输出数据 (0.0 ~ 1.0)
        self.latest_spectrum = [0.0] * self.num_bands
        self._p = None
        self._stream = None
        self._sample_rate = 44100
        self._channels = 2
        
        # 频段对数分割 (35Hz ~ 15500Hz)
        self.bands = np.logspace(np.log10(35), np.log10(15500), num=self.num_bands + 1)
        
        # 频段感知灵敏度增益系数 (低频能量大，高频能量小，进行人耳等响曲线补偿)
        self.band_gains = np.linspace(0.85, 2.8, self.num_bands)

    def start(self):
        if not PYAUDIO_AVAILABLE:
            print("[AudioVisualizer] pyaudiowpatch 未安装，跳过音频流捕获")
            return
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._worker_loop, name="AudioVisualizerThread", daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        self._cleanup_stream()

    def _cleanup_stream(self):
        if self._stream:
            try:
                self._stream.stop_stream()
                self._stream.close()
            except Exception:
                pass
            self._stream = None
        if self._p:
            try:
                self._p.terminate()
            except Exception:
                pass
            self._p = None

    def _worker_loop(self):
        while self._running:
            try:
                self._p = pyaudio.PyAudio()
                
                # 获取系统默认输出设备的 Loopback 虚拟录音端
                default_speakers = self._p.get_default_output_device_info()
                loopback_dev = None
                
                if default_speakers.get("isLoopbackDevice"):
                    loopback_dev = default_speakers
                else:
                    for loopback in self._p.get_loopback_device_info_generator():
                        if default_speakers["name"] in loopback["name"]:
                            loopback_dev = loopback
                            break
                    if not loopback_dev:
                        for loopback in self._p.get_loopback_device_info_generator():
                            loopback_dev = loopback
                            break

                if not loopback_dev:
                    print("[AudioVisualizer] 未找到可用的 WASAPI Loopback 音频设备，3秒后重试...")
                    self._cleanup_stream()
                    time.sleep(3)
                    continue

                self._sample_rate = int(loopback_dev["defaultSampleRate"])
                self._channels = loopback_dev["maxInputChannels"]
                
                def _stream_callback(in_data, frame_count, time_info, status):
                    if not self._running:
                        return (None, pyaudio.paComplete)
                    try:
                        audio_data = np.frombuffer(in_data, dtype=np.int16)
                        if self._channels > 1:
                            audio_data = audio_data[::self._channels]
                        
                        # FFT 变换
                        fft_data = np.abs(np.fft.rfft(audio_data))
                        freqs = np.fft.rfftfreq(len(audio_data), 1.0 / self._sample_rate)
                        
                        spec = []
                        for i in range(self.num_bands):
                            idx = np.where((freqs >= self.bands[i]) & (freqs < self.bands[i+1]))[0]
                            if len(idx) > 0:
                                val = float(np.mean(fft_data[idx]))
                            else:
                                val = 0.0
                            
                            # 动态人耳等响感知增益与归一化
                            gain = self.band_gains[i]
                            norm = (val * gain - 60.0) / 10000.0
                            clamped = min(1.0, max(0.0, norm))
                            spec.append(round(clamped, 3))
                        
                        with self._lock:
                            self.latest_spectrum = spec
                    except Exception:
                        pass
                    return (in_data, pyaudio.paContinue)

                self._stream = self._p.open(
                    format=pyaudio.paInt16,
                    channels=self._channels,
                    rate=self._sample_rate,
                    input=True,
                    input_device_index=loopback_dev["index"],
                    frames_per_buffer=1024,
                    stream_callback=_stream_callback
                )
                self._stream.start_stream()
                print(f"[AudioVisualizer] WASAPI Loopback 频谱采集已启动: {loopback_dev['name']}")

                while self._running and self._stream and self._stream.is_active():
                    time.sleep(0.5)

            except Exception as e:
                print(f"[AudioVisualizer] 运行异常: {e}，5秒后尝试恢复...")
                self._cleanup_stream()
                time.sleep(5)

    def get_spectrum(self):
        with self._lock:
            return list(self.latest_spectrum)


# 单例实例
visualizer_instance = AudioVisualizer(num_bands=24)
