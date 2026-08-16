package com.secondarydisplay.app

import android.annotation.SuppressLint
import android.graphics.Color
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.View
import android.view.KeyEvent
import android.view.Window
import android.view.WindowManager
import android.webkit.WebChromeClient
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebResourceResponse
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.appcompat.app.AppCompatActivity

/**
 * 主 Activity
 *
 * 使用 WebView 加载仪表盘, 通过 JavaScript 桥接提供 UDP 发现能力
 * 优点: UI 与 Web 版完全一致, 易于迭代, 同时具备原生 UDP 通信能力
 *
 * 热更新 (OTA): 优先加载局域网服务器的最新 UI (http://<ip>:8080/),
 * 失败时回退到本地打包的 asset 版本。首次启动无记忆 IP 时, 先显示本地
 * UI 秒开, 后台自动发现服务器后无缝切换到热更新源并记忆 IP。
 */
class MainActivity : AppCompatActivity() {

    private lateinit var webView: WebView
    private lateinit var androidBridge: AndroidBridge
    private var hotUpdateDiscovery: DiscoveryService? = null

    private val prefs by lazy { getSharedPreferences("secdisp", MODE_PRIVATE) }
    private val uiHandler = Handler(Looper.getMainLooper())
    private var remoteLoaded = false      // http 热更新页是否加载成功
    private var fallbackShown = false     // 是否已回退本地 asset

    companion object {
        private const val OTA_HTTP_PORT = 8080
        private const val WS_PORT = 8765
        private const val DISCOVERY_TIMEOUT_MS = 8000L
    }

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        // 全屏沉浸式 (横屏副屏模式)
        requestWindowFeature(Window.FEATURE_NO_TITLE)
        supportActionBar?.hide()

        window.statusBarColor = Color.TRANSPARENT
        window.navigationBarColor = Color.TRANSPARENT

        val decorView = window.decorView
        val uiFlags = (
            View.SYSTEM_UI_FLAG_LAYOUT_STABLE
                or View.SYSTEM_UI_FLAG_LAYOUT_HIDE_NAVIGATION
                or View.SYSTEM_UI_FLAG_LAYOUT_FULLSCREEN
                or View.SYSTEM_UI_FLAG_HIDE_NAVIGATION
                or View.SYSTEM_UI_FLAG_FULLSCREEN
                or View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY
        )
        decorView.systemUiVisibility = uiFlags
        decorView.setOnSystemUiVisibilityChangeListener { visibility ->
            if (visibility and View.SYSTEM_UI_FLAG_FULLSCREEN == 0) {
                decorView.postDelayed({ decorView.systemUiVisibility = uiFlags }, 500)
            }
        }

        setContentView(R.layout.activity_main)

        webView = findViewById(R.id.webView)
        androidBridge = AndroidBridge(this)

        configureWebView()
        loadInitialContent()
    }

    // ------------------------------------------------------------------
    // 热更新加载策略: 局域网 UI > 本地 asset 兜底
    // ------------------------------------------------------------------
    private fun loadInitialContent() {
        val intentIp = intent?.getStringExtra("server_ip")?.takeIf { it.isNotBlank() }
        val savedIp = prefs.getString("server_ip", null)?.takeIf { it.isNotBlank() }
        val targetIp = intentIp ?: savedIp
        if (targetIp != null) {
            // 有已知服务器: 直接尝试热更新源, 失败自动回退本地
            prefs.edit().putString("server_ip", targetIp).apply()
            loadRemoteOrFallback(targetIp)
        } else {
            // 无记忆: 本地 UI 秒开, 后台发现服务器后切换热更新源
            loadLocalAsset()
            startHotUpdateDiscovery()
        }
    }

    private fun loadRemoteOrFallback(ip: String) {
        remoteLoaded = false
        webView.loadUrl("http://$ip:$OTA_HTTP_PORT/?ip=$ip&port=$WS_PORT")
        // 8s 内未成功加载 (无 onPageFinished) → 回退本地 asset
        uiHandler.postDelayed({
            if (!remoteLoaded && !fallbackShown) {
                fallbackShown = true
                loadLocalAsset()
            }
        }, DISCOVERY_TIMEOUT_MS)
    }

    private fun loadLocalAsset() {
        val ip = intent?.getStringExtra("server_ip").orEmpty()
        val port = intent?.getIntExtra("server_port", WS_PORT) ?: WS_PORT
        val url = if (ip.isNotBlank()) {
            "file:///android_asset/web/index.html?ip=$ip&port=$port"
        } else {
            "file:///android_asset/web/index.html"
        }
        webView.loadUrl(url)
    }

    /** 无记忆 IP 时: 后台自动发现服务器, 命中即保存 IP 并切换热更新源 */
    private fun startHotUpdateDiscovery() {
        val ds = DiscoveryService(this)
        hotUpdateDiscovery = ds
        ds.onServerDiscovered = { ip, _, _ ->
            runOnUiThread {
                if (!remoteLoaded && !fallbackShown) {
                    prefs.edit().putString("server_ip", ip).apply()
                    loadRemoteOrFallback(ip)
                }
            }
            ds.stopDiscovery()
        }
        ds.startDiscovery()
        uiHandler.postDelayed({ ds.stopDiscovery() }, DISCOVERY_TIMEOUT_MS)
    }

    @SuppressLint("SetJavaScriptEnabled")
    private fun configureWebView() {
        val settings = webView.settings

        // 启用 JavaScript
        settings.javaScriptEnabled = true
        settings.domStorageEnabled = true  // localStorage 支持

        // 视口设置
        settings.useWideViewPort = true
        settings.loadWithOverviewMode = true
        settings.setSupportZoom(false)

        // 性能优化
        settings.cacheMode = WebSettings.LOAD_NO_CACHE
        settings.databaseEnabled = true
        settings.allowFileAccess = true

        // 混合内容 (允许 file:// / http:// 加载 ws://)
        settings.mixedContentMode = WebSettings.MIXED_CONTENT_ALWAYS_ALLOW

        // 添加 JavaScript 接口
        webView.addJavascriptInterface(androidBridge, "AndroidBridge")

        // WebView 调试 (开发时可用 chrome://inspect 调试)
        WebView.setWebContentsDebuggingEnabled(true)

        // 在 WebView 内打开链接; 热更新源加载失败时回退本地 asset
        webView.webViewClient = object : WebViewClient() {
            override fun shouldOverrideUrlLoading(
                view: WebView?,
                request: WebResourceRequest?
            ): Boolean = false

            override fun onPageFinished(view: WebView?, url: String?) {
                super.onPageFinished(view, url)
                if (url?.startsWith("http") == true) remoteLoaded = true
            }

            @Deprecated("Deprecated in Java")
            override fun onReceivedError(
                view: WebView?, errorCode: Int, description: String?, failingUrl: String?
            ) {
                if (failingUrl?.startsWith("http") == true && !remoteLoaded && !fallbackShown) {
                    fallbackShown = true
                    loadLocalAsset()
                }
            }

            override fun onReceivedError(
                view: WebView?, request: WebResourceRequest?, error: WebResourceError?
            ) {
                if (request?.isForMainFrame == true
                    && request.url.toString().startsWith("http")
                    && !remoteLoaded && !fallbackShown
                ) {
                    fallbackShown = true
                    loadLocalAsset()
                }
            }

            override fun onReceivedHttpError(
                view: WebView?, request: WebResourceRequest?, errorResponse: WebResourceResponse?
            ) {
                if (request?.isForMainFrame == true
                    && request.url.toString().startsWith("http")
                    && !remoteLoaded && !fallbackShown
                ) {
                    fallbackShown = true
                    loadLocalAsset()
                }
            }
        }

        // 支持全屏视频等
        webView.webChromeClient = WebChromeClient()
    }

    /**
     * 供 AndroidBridge 调用, 在主线程执行 JS
     */
    fun evaluateJavascript(js: String) {
        runOnUiThread {
            webView.evaluateJavascript(js, null)
        }
    }

    override fun onResume() {
        super.onResume()
        // 唤醒 WebView
        webView.onResume()
    }

    override fun onPause() {
        super.onPause()
        webView.onPause()
    }

    override fun onDestroy() {
        hotUpdateDiscovery?.stopDiscovery()
        uiHandler.removeCallbacksAndMessages(null)
        androidBridge.cleanup()
        webView.destroy()
        super.onDestroy()
    }

    /**
     * 处理返回键 — 先让 WebView 返回, 无法返回才退出
     */
    @Deprecated("Deprecated in Java")
    override fun onBackPressed() {
        if (webView.canGoBack()) {
            webView.goBack()
        } else {
            @Suppress("DEPRECATION")
            super.onBackPressed()
        }
    }

    /**
     * 音量键 → PC 系统音量
     *
     * 拦截手机物理音量键并转发为 WebSocket volume 指令,
     * 由 PC 端 server 模拟系统音量键。返回 true 消耗事件,
     * 防止手机自身音量被调整 (手机被用作副屏遥控器)。
     * 长按重复事件 (repeatCount>0) 也转发, 实现连续调音;
     * 限流 80ms 防止 WS 消息洪泛。
     */
    private var lastVolumeCmdTime = 0L

    override fun onKeyDown(keyCode: Int, event: KeyEvent): Boolean {
        val action = when (keyCode) {
            KeyEvent.KEYCODE_VOLUME_UP -> "up"
            KeyEvent.KEYCODE_VOLUME_DOWN -> "down"
            else -> null
        }
        if (action != null) {
            val now = System.currentTimeMillis()
            if (now - lastVolumeCmdTime >= 80) {
                lastVolumeCmdTime = now
                evaluateJavascript("window.sendVolumeCmd && window.sendVolumeCmd('$action');")
            }
            return true  // 消耗事件, 手机自身音量不变
        }
        return super.onKeyDown(keyCode, event)
    }
}
