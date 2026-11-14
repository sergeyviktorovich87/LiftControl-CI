package ru.liftcontrol.liftcontrol_mobile

import android.app.Application
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.media.AudioAttributes
import android.media.RingtoneManager
import android.os.Build
import android.net.Uri
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import com.google.firebase.FirebaseApp
import com.google.firebase.FirebaseOptions
import com.google.firebase.messaging.FirebaseMessagingService
import com.google.firebase.messaging.RemoteMessage
import org.json.JSONObject
import org.json.JSONArray

class LiftApplication : Application() {
    override fun onCreate() {
        super.onCreate()
        PushState.channel(this)
        PushState.initialize(this)
    }
}

object PushState {
    const val CHANNEL = "liftcontrol_requests_v1"
    val CHANNELS = linkedMapOf("ordinary" to CHANNEL, "urgent" to "liftcontrol_urgent_v1",
        "entrapment" to "liftcontrol_entrapment_v1", "updates" to "liftcontrol_updates_v1",
        "tasks" to "liftcontrol_tasks_v1", "app_update" to "liftcontrol_app_update_v1")
    fun channelId(group: String) = CHANNELS[group] ?: CHANNEL
    fun alertSound(context: Context): Uri {
        val name = context.resources.getResourceEntryName(R.raw.entrapment_alert)
        return Uri.parse("android.resource://${context.packageName}/raw/$name")
    }
    fun prefs(context: Context) = context.getSharedPreferences("push_settings", Context.MODE_PRIVATE)
    fun record(context: Context, event: String) {
        synchronized(PushState) {
            try {
                val previous = JSONArray(prefs(context).getString("events", "[]"))
                val next = JSONArray()
                for (index in maxOf(0, previous.length() - 79) until previous.length()) next.put(previous.get(index))
                next.put(JSONObject().put("at_ms", System.currentTimeMillis()).put("event", event))
                prefs(context).edit().putString("events", next.toString()).apply()
            } catch (_: Exception) { }
        }
    }
    fun initialize(context: Context): FirebaseApp? {
        if (FirebaseApp.getApps(context).isNotEmpty()) return FirebaseApp.getInstance()
        val raw = prefs(context).getString("config", null) ?: return null
        return try {
            val data = JSONObject(raw)
            FirebaseApp.initializeApp(context, FirebaseOptions.Builder()
                .setApiKey(data.getString("apiKey")).setApplicationId(data.getString("appId"))
                .setProjectId(data.getString("projectId")).setGcmSenderId(data.getString("messagingSenderId"))
                .build())
        } catch (_: Exception) { null }
    }
    fun channel(context: Context) {
        if (Build.VERSION.SDK_INT >= 26) {
            val manager = context.getSystemService(NotificationManager::class.java)
            val names = mapOf("ordinary" to "Обычные заявки", "urgent" to "Высокие и аварийные заявки",
                "entrapment" to "Застревание — люди в кабине", "updates" to "Изменения статусов",
                "tasks" to "Личные задания", "app_update" to "Обновления приложения")
            for ((group, id) in CHANNELS) {
                val critical = group == "entrapment"
                val importance = if (critical || group == "urgent") NotificationManager.IMPORTANCE_HIGH else NotificationManager.IMPORTANCE_DEFAULT
                val channel = NotificationChannel(id, names.getValue(group), importance)
                channel.description = names[group]
                channel.enableVibration(true)
                channel.vibrationPattern = if (critical) longArrayOf(0,600,120,600,120,600) else longArrayOf(0,250,100,250)
                channel.setSound(if (critical) alertSound(context) else RingtoneManager.getDefaultUri(RingtoneManager.TYPE_NOTIFICATION),
                    AudioAttributes.Builder().setUsage(AudioAttributes.USAGE_NOTIFICATION).build())
                manager.createNotificationChannel(channel)
            }
        }
    }
    fun show(context: Context, organization: Int, ticket: Int, title: String, body: String, group: String = "ordinary", target: String = "ticket") {
        channel(context)
        if (!NotificationManagerCompat.from(context).areNotificationsEnabled()) { record(context, "os_notifications_blocked"); return }
        val id = channelId(group)
        if (Build.VERSION.SDK_INT >= 26 && context.getSystemService(NotificationManager::class.java).getNotificationChannel(id)?.importance == NotificationManager.IMPORTANCE_NONE) { record(context, "category_notifications_blocked"); return }
        val tag = "o$organization-$target-$ticket"
        val intent = Intent(context, MainActivity::class.java).apply {
            flags = Intent.FLAG_ACTIVITY_CLEAR_TOP or Intent.FLAG_ACTIVITY_SINGLE_TOP
            putExtra("organization", organization); putExtra("ticket", ticket)
            putExtra("target", target)
        }
        val pending = PendingIntent.getActivity(context, tag.hashCode(), intent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)
        val notification = NotificationCompat.Builder(context, id)
            .setSmallIcon(R.drawable.ic_notification).setContentTitle(title).setContentText(body)
            .setContentIntent(pending).setAutoCancel(true).setOnlyAlertOnce(false)
            .setPriority(if (group in listOf("entrapment","urgent")) NotificationCompat.PRIORITY_HIGH else NotificationCompat.PRIORITY_DEFAULT)
            .setVisibility(NotificationCompat.VISIBILITY_PRIVATE)
            .setSound(if (group == "entrapment") alertSound(context) else RingtoneManager.getDefaultUri(RingtoneManager.TYPE_NOTIFICATION))
            .setVibrate(if (group == "entrapment") longArrayOf(0,600,120,600,120,600) else longArrayOf(0,250,100,250))
            .setColor(if (group == "entrapment") 0xffbd392b.toInt() else 0xff237651.toInt()).build()
        try { NotificationManagerCompat.from(context).notify(tag, 1, notification); record(context, "system_notification_posted") } catch (_: SecurityException) { record(context, "notification_permission_denied") }
    }
}

class LiftMessagingService : FirebaseMessagingService() {
    override fun onNewToken(token: String) {
        PushState.record(this, "fcm_token_rotated")
        // Token is registered with our authenticated API on the next foreground sync.
        PushState.prefs(this).edit().putBoolean("needs_register", true).apply()
        DeviceBridge.tokenChanged()
    }
    override fun onMessageReceived(message: RemoteMessage) {
        PushState.record(this, "fcm_received")
        val prefs = PushState.prefs(this)
        if (!prefs.getBoolean("active", false)) { PushState.record(this, "fcm_inactive_device"); return }
        displayPushEvent(this, message.data, "fcm")
    }
}
