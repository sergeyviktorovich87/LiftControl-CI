package ru.liftcontrol.liftcontrol_mobile

import android.content.ActivityNotFoundException
import android.content.Intent
import android.net.ConnectivityManager
import android.net.Network
import androidx.core.content.FileProvider
import io.flutter.embedding.android.FlutterActivity
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.MethodChannel
import java.io.File
import java.util.UUID

class MainActivity : FlutterActivity() {
    override fun onRequestPermissionsResult(requestCode: Int, permissions: Array<out String>, grantResults: IntArray) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (requestCode == 911) DeviceBridge.tokenChanged()
    }
    private var networkCallback: ConnectivityManager.NetworkCallback? = null
    override fun onDestroy() {
        networkCallback?.let { getSystemService(ConnectivityManager::class.java).unregisterNetworkCallback(it) }
        networkCallback = null
        super.onDestroy()
    }
    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        DeviceBridge.notifyOpen(this)
    }
    override fun configureFlutterEngine(flutterEngine: FlutterEngine) {
        super.configureFlutterEngine(flutterEngine)
        DeviceBridge.attach(this, flutterEngine)
        OfflineDatabase.attach(this, flutterEngine)
        if (networkCallback == null) {
            networkCallback = object : ConnectivityManager.NetworkCallback() {
                override fun onAvailable(network: Network) { OfflineSyncJob.schedule(this@MainActivity); DeviceBridge.networkChanged() }
                override fun onLost(network: Network) { DeviceBridge.networkChanged() }
            }
            getSystemService(ConnectivityManager::class.java).registerDefaultNetworkCallback(networkCallback!!)
        }
        val directory = File(cacheDir, "liftcontrol").apply { mkdirs() }
        directory.listFiles()?.filter { System.currentTimeMillis() - it.lastModified() > 24 * 60 * 60 * 1000L }?.forEach { it.delete() }
        MethodChannel(flutterEngine.dartExecutor.binaryMessenger, "ru.liftcontrol.app/files")
            .setMethodCallHandler { call, result ->
                try {
                    when (call.method) {
                        "openVideo" -> {
                            val bytes = call.argument<ByteArray>("bytes")
                            val mime = call.argument<String>("content_type")
                            if (bytes == null || bytes.size !in 12..20 * 1024 * 1024 || mime !in listOf("video/mp4","video/webm","video/quicktime")) {
                                result.error("FORMAT", "Видео повреждено или превышает 20 МБ.", null)
                            } else {
                                val extension = if (mime == "video/webm") "webm" else if (mime == "video/quicktime") "mov" else "mp4"
                                Thread {
                                    try {
                                        val file = File(directory, "${UUID.randomUUID()}.$extension")
                                        file.outputStream().use { it.write(bytes) }
                                        runOnUiThread {
                                            try {
                                                val uri = FileProvider.getUriForFile(this, "$packageName.files", file)
                                                startActivity(Intent.createChooser(Intent(Intent.ACTION_VIEW).setDataAndType(uri,mime).addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION), "Открыть видео"))
                                                result.success(null)
                                            } catch (_: Exception) { result.error("NO_VIEWER", "Не найдено приложение для просмотра видео.", null) }
                                        }
                                    } catch (_: Exception) { runOnUiThread { result.error("FILE_ERROR", "Не удалось открыть видео.", null) } }
                                }.start()
                            }
                        }
                        "openPdf" -> {
                            val bytes = call.argument<ByteArray>("bytes")
                            if (bytes == null || bytes.size > 32 * 1024 * 1024 || bytes.size < 5 ||
                                String(bytes.copyOfRange(0, 5), Charsets.US_ASCII) != "%PDF-") {
                                result.error("FORMAT", "Файл PDF повреждён или слишком большой.", null)
                            } else {
                                val file = File(directory, "${UUID.randomUUID()}.pdf")
                                file.outputStream().use { it.write(bytes) }
                                val uri = FileProvider.getUriForFile(this, "$packageName.files", file)
                                val intent = Intent(Intent.ACTION_VIEW).setDataAndType(uri, "application/pdf")
                                    .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
                                startActivity(Intent.createChooser(intent, "Открыть документацию"))
                                result.success(null)
                            }
                        }
                        "shareReport" -> {
                            val text = call.argument<String>("text") ?: ""
                            if (text.length > 128 * 1024) {
                                result.error("LIMIT", "Отчёт слишком большой.", null)
                            } else {
                                startActivity(Intent.createChooser(Intent(Intent.ACTION_SEND).apply {
                                    type = "text/plain"
                                    putExtra(Intent.EXTRA_SUBJECT, "LiftControl: диагностический отчёт")
                                    putExtra(Intent.EXTRA_TEXT, text)
                                }, "Поделиться отчётом"))
                                result.success(null)
                            }
                        }
                        "clearPrivateFiles" -> {
                            directory.listFiles()?.forEach { it.delete() }
                            result.success(null)
                        }
                        else -> result.notImplemented()
                    }
                } catch (_: ActivityNotFoundException) {
                    result.error("NO_VIEWER", "На устройстве нет приложения для открытия этого файла.", null)
                } catch (_: Exception) {
                    result.error("FILE_ERROR", "Не удалось открыть или передать файл.", null)
                }
            }
    }
}
