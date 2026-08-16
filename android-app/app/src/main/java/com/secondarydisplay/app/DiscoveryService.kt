package com.secondarydisplay.app

import android.content.Context
import android.net.ConnectivityManager
import android.net.LinkAddress
import android.net.Network
import android.net.NetworkCapabilities
import android.net.wifi.WifiManager
import android.os.Build
import android.util.Log
import java.io.BufferedReader
import java.io.InputStreamReader
import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.HttpURLConnection
import java.net.Inet4Address
import java.net.InetAddress
import java.net.URL
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicBoolean

/**
 * 局域网服务器自动发现服务
 *
 * 采用"双通道"发现策略, 任一通道命中即视为发现:
 *   1. UDP 广播监听: 被动接收桌面端周期广播的 "SECDISP:<ip>:<ws_port>:<hostname>"
 *   2. HTTP 局域网扫描 (主通道): 主动探测本机所在网段各主机的 :8080/api/info,
 *      对小米/MIUI 等 UDP 广播受限的设备更可靠。
 */
class DiscoveryService(private val context: Context) {

    companion object {
        private const val TAG = "DiscoveryService"
        private const val LISTEN_PORT = 8888
        private const val MAGIC = "SECDISP"
        private const val RECEIVE_TIMEOUT_MS = 5000
        // HTTP 扫描尝试的端口(与服务端 --http-port 默认值一致)
        private val HTTP_PORTS = intArrayOf(8080, 8000, 80)
        private const val HTTP_TIMEOUT_MS = 400
    }

    private var udpSocket: DatagramSocket? = null
    private val running = AtomicBoolean(false)
    private var udpThread: Thread? = null
    private var httpThread: Thread? = null
    private val httpExecutor = Executors.newFixedThreadPool(24)
    // 防止同一服务器被 UDP / HTTP 双通道重复上报
    private val reported = ConcurrentHashMap.newKeySet<String>()

    var onServerDiscovered: ((ip: String, port: Int, hostname: String) -> Unit)? = null

    /**
     * 开始发现 (UDP 监听 + HTTP 扫描并行)
     */
    @Synchronized
    fun startDiscovery() {
        if (running.get()) {
            Log.w(TAG, "发现服务已在运行")
            return
        }
        running.set(true)
        reported.clear()

        // 通道 1: UDP 广播监听
        udpThread = Thread({ udpListenLoop() }, "Discovery-UDP").apply {
            isDaemon = true
            start()
        }

        // 通道 2: HTTP 局域网扫描
        httpThread = Thread({ httpScanLoop() }, "Discovery-HTTP").apply {
            isDaemon = true
            start()
        }

        Log.i(TAG, "自动发现已启动 (UDP 监听 + HTTP 扫描)")
    }

    // ------------------------------------------------------------------
    // 通道 1: UDP 广播监听 (健壮版 — 异常后重建 socket, 不退出线程)
    // ------------------------------------------------------------------
    private fun udpListenLoop() {
        while (running.get()) {
            try {
                udpSocket = DatagramSocket(LISTEN_PORT).apply {
                    broadcast = true
                    soTimeout = RECEIVE_TIMEOUT_MS
                }
                Log.i(TAG, "开始监听 UDP 广播, 端口: $LISTEN_PORT")
                val buffer = ByteArray(1024)
                val packet = DatagramPacket(buffer, buffer.size)
                while (running.get()) {
                    try {
                        udpSocket?.receive(packet)
                        val data = String(packet.data, 0, packet.length)
                        Log.d(TAG, "收到 UDP 数据: $data")
                        handleUdpPayload(data)
                    } catch (e: java.net.SocketTimeoutException) {
                        // 超时属正常, 继续监听
                    }
                }
            } catch (e: Exception) {
                if (running.get()) Log.e(TAG, "UDP 监听异常, 1s 后重试", e)
                try { Thread.sleep(1000) } catch (_: InterruptedException) { }
            } finally {
                try { udpSocket?.close() } catch (_: Exception) { }
                udpSocket = null
            }
        }
    }

    private fun handleUdpPayload(data: String) {
        if (!data.startsWith(MAGIC)) return
        val parts = data.split(":")
        if (parts.size < 4) return
        val ip = parts[1]
        val port = parts[2].toIntOrNull() ?: 8765
        val hostname = parts.drop(3).joinToString(":")
        Log.i(TAG, "UDP 发现服务器: $hostname ($ip:$port)")
        report(ip, port, hostname)
    }

    // ------------------------------------------------------------------
    // 通道 2: HTTP 局域网扫描 (主通道, 对 UDP 受限设备更可靠)
    // ------------------------------------------------------------------
    private fun httpScanLoop() {
        val baseIp = getLocalIpV4()
        if (baseIp == null) {
            Log.w(TAG, "无法获取本机 WiFi IP, 跳过 HTTP 扫描 (仅 UDP 可用)")
            return
        }
        val subnet = baseIp.substringBeforeLast('.')
        Log.i(TAG, "HTTP 扫描网段: $subnet.*  (本机IP=$baseIp)")

        // 优先探测网关与本机所在区域, 再全量扫描 /24
        val candidates = buildCandidates(subnet, baseIp)
        for (ip in candidates) {
            if (!running.get()) break
            for (port in HTTP_PORTS) {
                if (!running.get()) break
                httpExecutor.submit { probeHttp(ip, port) }
            }
        }
        // 等待扫描收尾
        try { Thread.sleep(12000) } catch (_: InterruptedException) { }
        Log.i(TAG, "HTTP 扫描结束, 共发现 ${reported.size} 台")
    }

    private fun buildCandidates(subnet: String, selfIp: String): List<String> {
        val list = mutableListOf<String>()
        // 常见网关
        list += listOf("1", "254", "2", "100", "50")
        // 本机附近一段, 缩短首屏发现时间
        val last = selfIp.substringAfterLast('.').toIntOrNull() ?: 0
        for (i in (last - 20).coerceAtLeast(1)..(last + 20).coerceAtMost(254)) list += i.toString()
        // 全量 /24
        for (i in 1..254) list += i.toString()
        // 去重并拼成完整 IP
        return list.distinct().map { "$subnet.$it" }
    }

    private fun probeHttp(ip: String, port: Int) {
        if (!running.get()) return
        var conn: HttpURLConnection? = null
        try {
            val url = URL("http://$ip:$port/api/info")
            conn = url.openConnection() as HttpURLConnection
            conn.connectTimeout = HTTP_TIMEOUT_MS
            conn.readTimeout = HTTP_TIMEOUT_MS
            conn.requestMethod = "GET"
            val code = conn.responseCode
            if (code != HttpURLConnection.HTTP_OK) return
            val sb = StringBuilder()
            BufferedReader(InputStreamReader(conn.inputStream, Charsets.UTF_8)).use { reader ->
                var line: String?
                while (reader.readLine().also { line = it } != null) sb.append(line)
            }
            val json = sb.toString()
            // 简单解析 ws_port / hostname / ip 字段 (避免引入 Gson 依赖)
            val wsPort = regexFind(json, "\"ws_port\"\\s*:\\s*(\\d+)")?.toIntOrNull()
            if (wsPort == null) return
            val hostname = regexFind(json, "\"hostname\"\\s*:\\s*\"([^\"]*)\"") ?: ""
            val serverIp = regexFind(json, "\"ip\"\\s*:\\s*\"([^\"]*)\"") ?: ip
            Log.i(TAG, "HTTP 发现服务器: $hostname ($serverIp:$wsPort)")
            report(serverIp, wsPort, hostname)
        } catch (e: Exception) {
            // 超时 / 连接拒绝 / 非目标主机 — 静默忽略
        } finally {
            try { conn?.disconnect() } catch (_: Exception) { }
        }
    }

    private fun regexFind(text: String, pattern: String): String? {
        val m = Regex(pattern).find(text) ?: return null
        return m.groupValues.getOrNull(1)
    }

    // ------------------------------------------------------------------
    // 上报去重
    // ------------------------------------------------------------------
    private fun report(ip: String, port: Int, hostname: String) {
        val key = "$ip:$port"
        if (!reported.add(key)) return
        (context as? MainActivity)?.runOnUiThread {
            onServerDiscovered?.invoke(ip, port, hostname)
        }
    }

    /**
     * 停止发现
     */
    @Synchronized
    fun stopDiscovery() {
        running.set(false)
        try { udpSocket?.close() } catch (_: Exception) { }
        udpSocket = null
        httpExecutor.shutdownNow()
        udpThread?.interrupt()
        httpThread?.interrupt()
        udpThread = null
        httpThread = null
        Log.i(TAG, "停止自动发现")
    }

    /**
     * 主动发送一次广播探测 (可选)
     */
    fun sendProbe() {
        Thread {
            try {
                val probeSocket = DatagramSocket()
                probeSocket.broadcast = true
                val message = "SECDISP_PROBE"
                val data = message.toByteArray()
                val broadcastAddr = InetAddress.getByName("255.255.255.255")
                val packet = DatagramPacket(data, data.size, broadcastAddr, LISTEN_PORT)
                probeSocket.send(packet)
                probeSocket.close()
                Log.d(TAG, "已发送探测广播")
            } catch (e: Exception) {
                Log.e(TAG, "发送探测失败", e)
            }
        }.start()
    }

    // ------------------------------------------------------------------
    // 获取本机 IPv4 地址 (兼容 Android 10+)
    // ------------------------------------------------------------------
    private fun getLocalIpV4(): String? {
        try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
                val cm = context.getSystemService(Context.CONNECTIVITY_SERVICE) as? ConnectivityManager
                val network: Network? = cm?.activeNetwork
                val caps: NetworkCapabilities? = cm?.getNetworkCapabilities(network)
                if (caps != null && caps.hasTransport(NetworkCapabilities.TRANSPORT_WIFI)) {
                    val props = cm?.getLinkProperties(network)
                    props?.linkAddresses?.forEach { la: LinkAddress ->
                        val addr = la.address
                        if (addr is Inet4Address && !addr.isLoopbackAddress) {
                            return addr.hostAddress
                        }
                    }
                }
            }
            // 回退: WifiManager
            val wm = context.applicationContext.getSystemService(Context.WIFI_SERVICE) as? WifiManager
            val ipInt = wm?.connectionInfo?.ipAddress ?: 0
            if (ipInt != 0) {
                return String.format(
                    "%d.%d.%d.%d",
                    ipInt and 0xff,
                    ipInt shr 8 and 0xff,
                    ipInt shr 16 and 0xff,
                    ipInt shr 24 and 0xff
                )
            }
        } catch (e: Exception) {
            Log.e(TAG, "获取本机IP失败", e)
        }
        return null
    }
}
