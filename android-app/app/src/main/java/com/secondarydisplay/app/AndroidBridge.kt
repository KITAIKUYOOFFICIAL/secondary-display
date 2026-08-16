package com.secondarydisplay.app

import android.content.Context
import android.webkit.JavascriptInterface
import android.util.Log

/**
 * JavaScript 桥接接口
 *
 * 暴露给 WebView 中的 JavaScript 调用, 提供原生 UDP 发现能力
 * (浏览器环境无法进行 UDP 通信, 需要通过原生 Socket 实现)
 */
class AndroidBridge(private val context: Context) {

    companion object {
        private const val TAG = "AndroidBridge"
    }

    private val discoveryService = DiscoveryService(context)

    init {
        discoveryService.onServerDiscovered = { ip, port, hostname ->
            // 当发现服务器时, 调用 JavaScript 回调
            val js = "window.onDiscoveredServer('$ip', $port, '$hostname');"
            (context as? MainActivity)?.evaluateJavascript(js)
            Log.i(TAG, "回调 JS: 发现 $hostname ($ip:$port)")
        }
    }

    /**
     * 开始 UDP 自动发现
     * 由 JS 调用: window.AndroidBridge.startDiscovery()
     */
    @JavascriptInterface
    fun startDiscovery() {
        Log.i(TAG, "JS 请求启动发现")
        discoveryService.startDiscovery()
    }

    /**
     * 停止发现
     */
    @JavascriptInterface
    fun stopDiscovery() {
        Log.i(TAG, "JS 请求停止发现")
        discoveryService.stopDiscovery()
    }

    /**
     * 发送主动探测广播
     */
    @JavascriptInterface
    fun sendProbe() {
        discoveryService.sendProbe()
    }

    /**
     * 停止所有服务 (Activity 销毁时调用)
     */
    fun cleanup() {
        discoveryService.stopDiscovery()
    }
}
