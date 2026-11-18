package com.fluxtube.app

import android.annotation.SuppressLint
import android.content.ClipboardManager
import android.content.Context
import android.media.MediaScannerConnection
import android.os.Bundle
import android.os.Environment
import android.webkit.JavascriptInterface
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.appcompat.app.AppCompatActivity
import com.yausername.ffmpeg.FFmpeg
import com.yausername.youtubedl_android.YoutubeDL
import com.yausername.youtubedl_android.YoutubeDLRequest
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.launch
import org.json.JSONArray
import org.json.JSONObject
import java.io.File

class MainActivity : AppCompatActivity() {

    private lateinit var webView: WebView
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main)
    @Volatile
    private var engineReady = false

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        webView = WebView(this)
        setContentView(webView)

        webView.setBackgroundColor(0xFF09080B.toInt())
        webView.isVerticalScrollBarEnabled = true
        webView.isHorizontalScrollBarEnabled = false
        webView.overScrollMode = android.view.View.OVER_SCROLL_IF_CONTENT_SCROLLS
        webView.setLayerType(android.view.View.LAYER_TYPE_HARDWARE, null)
        val settings = webView.settings
        settings.javaScriptEnabled = true
        settings.domStorageEnabled = true
        settings.allowFileAccess = true
        settings.mixedContentMode = WebSettings.MIXED_CONTENT_ALWAYS_ALLOW
        webView.webViewClient = WebViewClient()
        webView.addJavascriptInterface(FluxBridge(), "FluxAndroid")
        webView.loadUrl("file:///android_asset/index.html")

        initEngine()
    }

    private fun initEngine() {
        scope.launch(Dispatchers.IO) {
            try {
                YoutubeDL.getInstance().init(applicationContext)
                FFmpeg.getInstance().init(applicationContext)
                engineReady = true
                callJs("window.onEngineStatus(true, 'Engine Ready')")
            } catch (e: Exception) {
                val msg = (e.message ?: "Init failed").replace("'", "")
                callJs("window.onEngineStatus(false, '$msg')")
            }
        }
    }

    private fun callJs(script: String) {
        runOnUiThread {
            webView.evaluateJavascript(script, null)
        }
    }

    private fun formatDuration(sec: Int): String {
        val s = sec.coerceAtLeast(0)
        val h = s / 3600
        val m = (s % 3600) / 60
        val rem = s % 60
        return if (h > 0) String.format("%d:%02d:%02d", h, m, rem)
        else String.format("%d:%02d", m, rem)
    }

    private fun detectPlatform(url: String): String {
        val u = url.lowercase()
        return when {
            u.contains("youtube.com") || u.contains("youtu.be") -> "YouTube"
            u.contains("instagram.com") -> "Instagram"
            u.contains("tiktok.com") -> "TikTok"
            u.contains("facebook.com") || u.contains("fb.watch") -> "Facebook"
            else -> "Video"
        }
    }

    inner class FluxBridge {

        @JavascriptInterface
        fun getClipboard(): String {
            val cm = getSystemService(Context.CLIPBOARD_SERVICE) as? ClipboardManager
            return cm?.primaryClip?.getItemAt(0)?.coerceToText(this@MainActivity)?.toString() ?: ""
        }

        @JavascriptInterface
        fun openUrl(url: String) {
            try {
                val intent = android.content.Intent(
                    android.content.Intent.ACTION_VIEW,
                    android.net.Uri.parse(url)
                )
                intent.addFlags(android.content.Intent.FLAG_ACTIVITY_NEW_TASK)
                startActivity(intent)
            } catch (_: Exception) {
            }
        }

        @JavascriptInterface
        fun listDownloads(): String {
            val arr = JSONArray()
            try {
                val dlRoot = Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_DOWNLOADS)
                val outDir = File(dlRoot, "FLUXTUBE")
                if (outDir.exists()) {
                    outDir.listFiles()
                        ?.filter { it.isFile }
                        ?.sortedByDescending { it.lastModified() }
                        ?.take(30)
                        ?.forEach { f ->
                            val mb = f.length() / (1024.0 * 1024.0)
                            val sizeLabel = if (mb >= 1.0) String.format("%.1f MB", mb)
                            else String.format("%d KB", (f.length() / 1024).coerceAtLeast(1))
                            arr.put(JSONObject().apply {
                                put("name", f.name)
                                put("size", sizeLabel)
                                put("ext", f.extension.uppercase())
                            })
                        }
                }
            } catch (_: Exception) {
            }
            return arr.toString()
        }

        @JavascriptInterface
        fun updateEngine() {
            scope.launch(Dispatchers.IO) {
                callJs("window.onEngineStatus(false, 'Updating yt-dlp...')")
                try {
                    val status = YoutubeDL.getInstance().updateYoutubeDL(applicationContext)
                    engineReady = true
                    callJs("window.onEngineStatus(true, 'Updated: ${status?.name ?: "OK"}')")
                } catch (e: Exception) {
                    val msg = (e.message ?: "Update failed").replace("'", "")
                    callJs("window.onEngineStatus(true, 'Update note: $msg')")
                }
            }
        }

        @JavascriptInterface
        fun fetchInfo(rawUrl: String) {
            val url = rawUrl.trim()
            if (!engineReady) {
                callJs("window.onFetchError('Engine is still starting, wait 2 seconds...')")
                return
            }
            scope.launch(Dispatchers.IO) {
                try {
                    val info = YoutubeDL.getInstance().getInfo(url)
                    val dur = info.duration
                    val payload = JSONObject().apply {
                        put("title", info.title ?: "Untitled Video")
                        put("thumbnail", info.thumbnail ?: "")
                        put("duration", dur)
                        put("duration_string", formatDuration(dur))
                        put("platform", detectPlatform(url))
                        put("url", url)
                        put("qualities", JSONArray().apply {
                            put(JSONObject().apply {
                                put("id", "best_1080")
                                put("badge", "HD+")
                                put("ext", "MP4")
                                put("resolution", "1080p")
                                put("title", "Full HD")
                            })
                            put(JSONObject().apply {
                                put("id", "best_max")
                                put("badge", "MAX")
                                put("ext", "MP4")
                                put("resolution", "4K / Max")
                                put("title", "Highest Quality")
                            })
                            put(JSONObject().apply {
                                put("id", "hd_720")
                                put("badge", "FAST")
                                put("ext", "MP4")
                                put("resolution", "720p")
                                put("title", "Standard HD")
                            })
                            put(JSONObject().apply {
                                put("id", "sd_480")
                                put("badge", "LITE")
                                put("ext", "MP4")
                                put("resolution", "480p")
                                put("title", "Data Saver")
                            })
                            put(JSONObject().apply {
                                put("id", "audio_mp3")
                                put("badge", "AUDIO")
                                put("ext", "MP3")
                                put("resolution", "MP3")
                                put("title", "Audio Track")
                            })
                            put(JSONObject().apply {
                                put("id", "audio_m4a")
                                put("badge", "AAC")
                                put("ext", "M4A")
                                put("resolution", "M4A")
                                put("title", "AAC Audio")
                            })
                        })
                    }
                    val escaped = JSONObject.quote(payload.toString())
                    callJs("window.onFetchSuccess(JSON.parse($escaped))")
                } catch (e: Exception) {
                    val escaped = JSONObject.quote(e.message ?: "Could not fetch video info.")
                    callJs("window.onFetchError($escaped)")
                }
            }
        }

        @JavascriptInterface
        fun startDownload(url: String, qualityId: String, startTime: String, endTime: String, trimmed: Boolean) {
            if (!engineReady) {
                callJs("window.onDownloadError('Engine not ready yet.')")
                return
            }
            scope.launch(Dispatchers.IO) {
                try {
                    val dlRoot = Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_DOWNLOADS)
                    val outDir = File(dlRoot, "FLUXTUBE")
                    if (!outDir.exists()) outDir.mkdirs()

                    val req = YoutubeDLRequest(url)
                    req.addOption("--no-playlist")
                    req.addOption("-o", "${outDir.absolutePath}/%(title).80s.%(ext)s")

                    when (qualityId) {
                        "audio_mp3" -> {
                            req.addOption("-f", "bestaudio/best")
                            req.addOption("-x")
                            req.addOption("--audio-format", "mp3")
                            req.addOption("--audio-quality", "192K")
                        }
                        "audio_m4a" -> {
                            req.addOption("-f", "bestaudio[ext=m4a]/bestaudio/best")
                            req.addOption("-x")
                            req.addOption("--audio-format", "m4a")
                        }
                        "best_max" -> {
                            req.addOption("-f", "bestvideo+bestaudio/best")
                            req.addOption("--merge-output-format", "mp4")
                        }
                        "hd_720" -> {
                            req.addOption("-f", "bestvideo[height<=720]+bestaudio/best[height<=720]/best")
                            req.addOption("--merge-output-format", "mp4")
                        }
                        "sd_480" -> {
                            req.addOption("-f", "bestvideo[height<=480]+bestaudio/best[height<=480]/best")
                            req.addOption("--merge-output-format", "mp4")
                        }
                        else -> {
                            req.addOption("-f", "bestvideo[height<=1080]+bestaudio/best[height<=1080]/best")
                            req.addOption("--merge-output-format", "mp4")
                        }
                    }

                    if (trimmed && startTime.isNotBlank() && endTime.isNotBlank()) {
                        req.addOption("--download-sections", "*${startTime.trim()}-${endTime.trim()}")
                        req.addOption("--force-keyframes-at-cuts")
                    }

                    YoutubeDL.getInstance().execute(req) { progress, etaInSeconds, _ ->
                        val pct = progress.coerceIn(0f, 100f).toInt()
                        val etaStr = if (etaInSeconds >= 0) "${etaInSeconds}s" else "—"
                        callJs("window.onDownloadProgress($pct, '$etaStr')")
                    }

                    // Trigger Android media scan so saved video appears in Gallery/Files
                    outDir.listFiles()?.maxByOrNull { it.lastModified() }?.let { newest ->
                        MediaScannerConnection.scanFile(
                            applicationContext,
                            arrayOf(newest.absolutePath),
                            null,
                            null
                        )
                    }

                    callJs("window.onDownloadComplete('Saved to Downloads/FLUXTUBE')")
                } catch (e: Exception) {
                    val escaped = JSONObject.quote(e.message ?: "Download failed.")
                    callJs("window.onDownloadError($escaped)")
                }
            }
        }
    }

    override fun onDestroy() {
        scope.cancel()
        super.onDestroy()
    }
}
