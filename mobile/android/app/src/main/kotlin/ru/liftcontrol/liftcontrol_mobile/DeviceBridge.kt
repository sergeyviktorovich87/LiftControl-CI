package ru.liftcontrol.liftcontrol_mobile

import android.Manifest
import android.app.Activity
import android.app.NotificationManager
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.provider.Settings
import androidx.core.app.NotificationManagerCompat
import androidx.core.content.FileProvider
import com.google.firebase.FirebaseApp
import com.google.firebase.messaging.FirebaseMessaging
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.MethodChannel
import org.json.JSONObject
import java.io.File
import java.net.HttpURLConnection
import java.net.URL
import java.security.MessageDigest
import java.util.concurrent.Executors

object DeviceBridge {
    private var channel: MethodChannel? = null
    private val background = Executors.newSingleThreadExecutor()
    private val main = Handler(Looper.getMainLooper())
    fun tokenChanged() { main.post { channel?.invokeMethod("pushTokenChanged", null) } }
    fun networkChanged() { main.post { channel?.invokeMethod("networkChanged", null) } }
    fun notifyOpen(activity: Activity) {
        val data = notification(activity) ?: return
        channel?.invokeMethod("notification", data)
    }
    private fun notification(activity: Activity): Map<String, Any>? {
        val org = activity.intent.getIntExtra("organization", 0)
        val ticket = activity.intent.getIntExtra("ticket", 0)
        val target = activity.intent.getStringExtra("target") ?: "ticket"
        activity.intent.removeExtra("organization"); activity.intent.removeExtra("ticket"); activity.intent.removeExtra("target")
        if (!PushState.prefs(activity).getBoolean("active", false)) return null
        if (target == "app_update" && ticket > 0) return mapOf("target" to "app_update")
        if (org <= 0 || ticket <= 0 || target !in listOf("ticket","task")) return null
        return mapOf("organization" to org, target to ticket, "target" to target)
    }
    fun attach(activity: Activity, engine: FlutterEngine) {
        channel = MethodChannel(engine.dartExecutor.binaryMessenger, "ru.liftcontrol.app/device")
        channel!!.setMethodCallHandler { call, result ->
            try {
                when (call.method) {
                    "nativeDiagnostics" -> result.success(PushState.prefs(activity).getString("events", "[]"))
                    "status" -> {
                        @Suppress("DEPRECATION")
                        val info = activity.packageManager.getPackageInfo(activity.packageName, 0)
                        val allowed = NotificationManagerCompat.from(activity).areNotificationsEnabled()
                        val categories = PushState.CHANNELS.mapValues { (_, id) ->
                            if (Build.VERSION.SDK_INT >= 26) {
                                val info = activity.getSystemService(NotificationManager::class.java).getNotificationChannel(id)
                                mapOf("importance" to (info?.importance ?: 0), "distinct_alert_sound" to (info?.sound == PushState.alertSound(activity)))
                            } else mapOf("importance" to 0, "distinct_alert_sound" to false)
                        }
                        result.success(mapOf("active" to PushState.prefs(activity).getBoolean("active", false),
                            "categories" to categories,
                            "notifications_allowed" to allowed, "version" to info.versionName,
                            "version_code" to if (Build.VERSION.SDK_INT >= 28) info.longVersionCode else @Suppress("DEPRECATION") info.versionCode.toLong()))
                    }
                    "registerPush" -> {
                        if (Build.VERSION.SDK_INT >= 33 && activity.checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
                            if (call.argument<Boolean>("prompt") != false) activity.requestPermissions(arrayOf(Manifest.permission.POST_NOTIFICATIONS), 911)
                            result.error("PERMISSION", "Разрешите уведомления Android. Подключение продолжится автоматически.", null)
                        } else {
                            val config = JSONObject()
                            for (key in listOf("apiKey", "appId", "projectId", "messagingSenderId")) {
                                val value = call.argument<String>(key) ?: throw IllegalArgumentException()
                                if (value.isBlank() || value.length > 256) throw IllegalArgumentException()
                                config.put(key, value)
                            }
                            val previous = PushState.prefs(activity).getString("config", null)
                            if (previous != null && JSONObject(previous).optString("projectId") != config.getString("projectId")) {
                                FirebaseApp.getApps(activity).forEach { it.delete() }
                            }
                            PushState.prefs(activity).edit().putString("config", config.toString()).apply()
                            if (PushState.initialize(activity) == null) throw IllegalStateException()
                            FirebaseMessaging.getInstance().isAutoInitEnabled = true
                            FirebaseMessaging.getInstance().token.addOnCompleteListener { task ->
                                if (task.isSuccessful) result.success(task.result)
                                else result.error("FCM", "Не удалось связаться с FCM. Проверьте сеть и Google Play Services.", null)
                            }
                        }
                    }
                    "activatePush" -> {
                        PushState.prefs(activity).edit().putBoolean("active", true).putBoolean("needs_register", false).apply()
                        result.success(null)
                    }
                    "disablePush" -> {
                        PushState.prefs(activity).edit().putBoolean("active", false).apply()
                        NotificationManagerCompat.from(activity).cancelAll()
                        if (FirebaseApp.getApps(activity).isNotEmpty()) {
                            FirebaseMessaging.getInstance().isAutoInitEnabled = false
                            FirebaseMessaging.getInstance().deleteToken()
                        }
                        result.success(null)
                    }
                    "showSelfEvent" -> {
                        val raw = call.arguments as? Map<*, *> ?: throw IllegalArgumentException()
                        val data = raw.entries.mapNotNull { (key, value) ->
                            if (key is String && value != null && key in listOf(
                                "organization_id", "target", "task_id", "ticket_id",
                                "event_id", "kind", "priority", "status"
                            )) key to value.toString() else null
                        }.toMap()
                        result.success(displayPushEvent(activity, data, "self"))
                    }
                    "testPush" -> {
                        if (Build.VERSION.SDK_INT >= 33 && activity.checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
                            activity.requestPermissions(arrayOf(Manifest.permission.POST_NOTIFICATIONS),911)
                            result.error("PERMISSION", "Разрешите уведомления Android и нажмите проверку ещё раз.", null)
                        } else {
                            PushState.show(activity,0,0,"LiftControl — проверка","Проверка системного уведомления и звука",call.argument<String>("category") ?: "ordinary")
                            result.success(null)
                        }
                    }
                    "notificationSettings" -> {
                        val category = call.argument<String>("category")
                        val intent = if (Build.VERSION.SDK_INT >= 26 && category == null) Intent(Settings.ACTION_APP_NOTIFICATION_SETTINGS).putExtra(Settings.EXTRA_APP_PACKAGE, activity.packageName)
                        else if (Build.VERSION.SDK_INT >= 26) Intent(Settings.ACTION_CHANNEL_NOTIFICATION_SETTINGS)
                            .putExtra(Settings.EXTRA_APP_PACKAGE, activity.packageName).putExtra(Settings.EXTRA_CHANNEL_ID, PushState.channelId(category ?: "ordinary"))
                        else Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, Uri.parse("package:${activity.packageName}"))
                        PushState.channel(activity); activity.startActivity(intent); result.success(null)
                    }
                    "consumeNotification" -> result.success(notification(activity))
                    "installUpdate" -> {
                        if (Build.VERSION.SDK_INT >= 26 && !activity.packageManager.canRequestPackageInstalls()) {
                            activity.startActivity(Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES, Uri.parse("package:${activity.packageName}")))
                            result.error("INSTALL_PERMISSION", "Разрешите установку обновлений и нажмите «Установить» ещё раз.", null)
                        } else {
                            val args = call.arguments as? Map<*, *> ?: throw IllegalArgumentException()
                            background.execute {
                                try {
                                    val file = download(activity, args)
                                    main.post {
                                        try {
                                            val uri = FileProvider.getUriForFile(activity, "${activity.packageName}.files", file)
                                            activity.startActivity(Intent(Intent.ACTION_VIEW).setDataAndType(uri, "application/vnd.android.package-archive")
                                                .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION))
                                            result.success(null)
                                        } catch (_: Exception) { result.error("INSTALL", "Не удалось открыть установщик Android.", null) }
                                    }
                                } catch (_: Exception) { main.post { result.error("UPDATE_CHECK", "Обновление не прошло проверку версии, подписи или целостности. Установка отменена.", null) } }
                            }
                        }
                    }
                    else -> result.notImplemented()
                }
            } catch (_: Exception) { result.error("DEVICE", "Не удалось выполнить действие на устройстве.", null) }
        }
    }
    private fun download(activity: Activity, args: Map<*, *>): File {
        val base = URL(args["server"] as String)
        require(base.protocol == "https" || base.protocol == "http" && base.host in listOf("127.0.0.1", "localhost", "::1"))
        require(base.userInfo == null && base.query == null && base.ref == null && base.path in listOf("", "/"))
        val expectedSize = (args["size"] as Number).toLong()
        val expectedHash = args["sha256"] as String
        require(expectedSize in 1..200L * 1024 * 1024 && expectedHash.matches(Regex("[a-f0-9]{64}")))
        val token = args["token"] as String
        require(token.matches(Regex("[A-Za-z0-9_-]{43}")))
        val org = (args["organization"] as Number).toInt(); require(org > 0)
        val directory = File(activity.cacheDir, "liftcontrol").apply { mkdirs() }
        val file = File.createTempFile("update-", ".apk", directory)
        try {
            val connection = URL(base, "/api/updates/android/download/?sha256=$expectedHash").openConnection() as HttpURLConnection
            connection.instanceFollowRedirects = false; connection.connectTimeout = 15000; connection.readTimeout = 30000
            connection.setRequestProperty("Authorization", "Bearer $token")
            connection.setRequestProperty("X-Organization-ID", org.toString())
            try {
                require(connection.responseCode == 200)
                val hash = MessageDigest.getInstance("SHA-256"); var size = 0L
                connection.inputStream.use { input -> file.outputStream().use { output ->
                    val buffer = ByteArray(65536)
                    while (true) {
                        val read = input.read(buffer); if (read < 0) break
                        size += read; require(size <= expectedSize); hash.update(buffer, 0, read); output.write(buffer, 0, read)
                    }
                } }
                require(size == expectedSize && hash.digest().joinToString("") { "%02x".format(it) } == expectedHash)
            } finally { connection.disconnect() }
            @Suppress("DEPRECATION")
            val flags = if (Build.VERSION.SDK_INT >= 28) PackageManager.GET_SIGNING_CERTIFICATES else PackageManager.GET_SIGNATURES
            @Suppress("DEPRECATION")
            val current = activity.packageManager.getPackageInfo(activity.packageName, flags)
            @Suppress("DEPRECATION")
            val incoming = activity.packageManager.getPackageArchiveInfo(file.absolutePath, flags) ?: throw IllegalArgumentException()
            require(incoming.packageName == activity.packageName)
            @Suppress("DEPRECATION")
            val oldCode = if (Build.VERSION.SDK_INT >= 28) current.longVersionCode else current.versionCode.toLong()
            @Suppress("DEPRECATION")
            val newCode = if (Build.VERSION.SDK_INT >= 28) incoming.longVersionCode else incoming.versionCode.toLong()
            require(newCode > oldCode && newCode == (args["version_code"] as Number).toLong())
            @Suppress("DEPRECATION")
            val oldSigners = if (Build.VERSION.SDK_INT >= 28) current.signingInfo?.apkContentsSigners else current.signatures
            @Suppress("DEPRECATION")
            val newSigners = if (Build.VERSION.SDK_INT >= 28) incoming.signingInfo?.apkContentsSigners else incoming.signatures
            require(!oldSigners.isNullOrEmpty() && !newSigners.isNullOrEmpty() && oldSigners.toSet() == newSigners.toSet())
            return file
        } catch (e: Exception) { file.delete(); throw e }
    }
}
