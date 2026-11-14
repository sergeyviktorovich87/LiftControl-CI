package ru.liftcontrol.liftcontrol_mobile

import android.app.job.JobInfo
import android.app.job.JobParameters
import android.app.job.JobScheduler
import android.app.job.JobService
import android.content.ComponentName
import android.content.ContentValues
import android.content.Context
import android.database.DatabaseErrorHandler
import android.database.sqlite.SQLiteDatabase
import android.database.sqlite.SQLiteException
import android.database.sqlite.SQLiteOpenHelper
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.MethodChannel
import org.json.JSONArray
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URI
import java.security.KeyStore
import java.time.Instant
import java.util.concurrent.Executors
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

/** Payloads and credentials are encrypted separately from SQLite metadata.
 * The non-exportable key lives in Android Keystore; no backup or public files.
 * Corruption never silently recreates an empty database containing unsent work. */
class OfflineDatabase private constructor(private val context: Context) : SQLiteOpenHelper(
    context.applicationContext, "liftcontrol-offline-v1.db", null, 1,
    DatabaseErrorHandler { throw SQLiteException("OFFLINE_DATABASE_CORRUPT") }
) {
    init { setWriteAheadLoggingEnabled(true) }
    override fun onCreate(db: SQLiteDatabase) {
        db.execSQL("CREATE TABLE blobs (k TEXT NOT NULL, part INTEGER NOT NULL, value BLOB NOT NULL, PRIMARY KEY(k,part))")
        db.execSQL("CREATE TABLE kv (k TEXT PRIMARY KEY, value BLOB NOT NULL)")
        db.execSQL("CREATE TABLE outbox (seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT NULL UNIQUE, scope TEXT NOT NULL, value BLOB NOT NULL, state TEXT NOT NULL DEFAULT 'queued', response BLOB, error BLOB)")
        db.execSQL("CREATE INDEX outbox_scope ON outbox(scope,seq)")
    }
    override fun onUpgrade(db: SQLiteDatabase, old: Int, new: Int) {
        throw SQLiteException("UNSUPPORTED_OFFLINE_DATABASE_VERSION")
    }
    private fun key(): SecretKey = synchronized(keyLock) {
        val store = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
        (store.getKey(ALIAS, null) as? SecretKey) ?: KeyGenerator.getInstance("AES", "AndroidKeyStore").run {
            init(KeyGenParameterSpec.Builder(ALIAS, KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT)
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM).setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                .setKeySize(256).build())
            generateKey()
        }
    }
    private fun encode(aad: String, text: String): ByteArray {
        val cipher = Cipher.getInstance("AES/GCM/NoPadding")
        cipher.init(Cipher.ENCRYPT_MODE, key())
        cipher.updateAAD(aad.toByteArray(Charsets.UTF_8))
        return cipher.iv + cipher.doFinal(text.toByteArray(Charsets.UTF_8))
    }
    private fun decode(aad: String, bytes: ByteArray): String {
        val cipher = Cipher.getInstance("AES/GCM/NoPadding")
        cipher.init(Cipher.DECRYPT_MODE, key(), GCMParameterSpec(128, bytes.copyOfRange(0, 12)))
        cipher.updateAAD(aad.toByteArray(Charsets.UTF_8))
        return String(cipher.doFinal(bytes.copyOfRange(12, bytes.size)), Charsets.UTF_8)
    }
    // Android CursorWindow cannot hold a large photo/cache row. Use 256 KB
    // chunks, committed with their metadata, while authenticating the full value.
    private fun writeBlob(db: SQLiteDatabase, k: String, aad: String, value: String) {
        val bytes = encode(aad,value)
        db.delete("blobs","k=?",arrayOf(k))
        var part = 0
        for (start in bytes.indices step 256*1024) {
            db.insertOrThrow("blobs",null,ContentValues().apply {
                put("k",k); put("part",part++); put("value",bytes.copyOfRange(start,minOf(start+256*1024,bytes.size)))
            })
        }
    }
    private fun readBlob(k: String, aad: String): String {
        val bytes = java.io.ByteArrayOutputStream()
        readableDatabase.rawQuery("SELECT value FROM blobs WHERE k=? ORDER BY part",arrayOf(k)).use { c ->
            while(c.moveToNext()) { require(bytes.size() <= 24*1024*1024); bytes.write(c.getBlob(0)) }
        }
        require(bytes.size() >= 28) { "MISSING_OFFLINE_PAYLOAD" }
        return decode(aad,bytes.toByteArray())
    }
    fun read(k: String): String? {
        val db = readableDatabase
        // Serialize the multi-query chunk read with replacement/deletion.
        db.beginTransaction()
        try {
            val exists = db.rawQuery("SELECT 1 FROM kv WHERE k=?",arrayOf(k)).use { it.moveToFirst() }
            val result = if(exists) readBlob("kv:$k",k) else null
            db.setTransactionSuccessful()
            return result
        } finally { db.endTransaction() }
    }
    fun write(k: String, value: String) {
        val db = writableDatabase
        db.beginTransaction()
        try {
            writeBlob(db,"kv:$k",k,value)
            db.insertWithOnConflict("kv", null, ContentValues().apply { put("k",k); put("value",byteArrayOf(1)) }, SQLiteDatabase.CONFLICT_REPLACE)
            db.setTransactionSuccessful()
        } finally { db.endTransaction() }
    }
    fun delete(k: String) {
        val db = writableDatabase
        db.beginTransaction()
        try { db.delete("kv","k=?",arrayOf(k)); db.delete("blobs","k=?",arrayOf("kv:$k")); db.setTransactionSuccessful() }
        finally { db.endTransaction() }
    }
    private fun clearKeys(auth: Boolean) {
        val keys = readableDatabase.rawQuery(if(auth) "SELECT k FROM kv WHERE k LIKE 'auth_%'" else "SELECT k FROM kv WHERE k NOT LIKE 'auth_%'",null).use { c ->
            val keys = mutableListOf<String>(); while(c.moveToNext()) keys.add(c.getString(0)); keys
        }
        for(k in keys) delete(k)
    }
    fun clearCache() { clearKeys(false) }
    fun clearAuth() = synchronized(syncLock) { clearKeys(true) }
    fun enqueue(scope: String, rows: JSONArray) {
        val db = writableDatabase
        db.beginTransaction()
        try {
            val count = db.rawQuery("SELECT COUNT(*) FROM outbox WHERE scope=? AND state!='synced'", arrayOf(scope)).use { it.moveToFirst(); it.getInt(0) }
            require(count + rows.length() <= 500)
            val used = db.rawQuery("SELECT COALESCE(SUM(length(value)),0) FROM blobs WHERE k LIKE 'outbox:%'",null).use { it.moveToFirst(); it.getLong(0) }
            require(used + rows.toString().toByteArray(Charsets.UTF_8).size <= 150L*1024*1024) { "OUTBOX_LIMIT" }
            for (i in 0 until rows.length()) {
                val row = rows.getJSONObject(i)
                val id = row.getString("id")
                require(id.length <= 40 && row.toString().length <= 15 * 1024 * 1024)
                writeBlob(db,"outbox:$scope:$id","$scope:$id",row.toString())
                db.insertOrThrow("outbox", null, ContentValues().apply {
                    put("id", id); put("scope", scope); put("value", byteArrayOf(1))
                })
            }
            db.setTransactionSuccessful()
        } finally { db.endTransaction() }
    }
    fun rows(scope: String): JSONArray {
        val db = writableDatabase
        db.beginTransaction()
        try {
        val rows = JSONArray()
        readableDatabase.rawQuery("SELECT id,value,state,response,error FROM outbox WHERE scope=? ORDER BY seq", arrayOf(scope)).use { c ->
            while (c.moveToNext()) {
                val id = c.getString(0)
                val row = JSONObject(readBlob("outbox:$scope:$id","$scope:$id"))
                row.put("state", c.getString(2)); row.put("error", if (c.isNull(4)) "" else decode("$scope:$id:error", c.getBlob(4)))
                if (!c.isNull(3)) row.put("response", JSONObject(decode("$scope:$id:response", c.getBlob(3))))
                rows.put(row)
            }
        }
        db.setTransactionSuccessful()
        return rows
        } finally { db.endTransaction() }
    }
    private fun finish(scope: String, id: String, state: String, response: String? = null, error: String = "") {
        writableDatabase.update("outbox", ContentValues().apply {
            put("state", state); put("error", encode("$scope:$id:error",error))
            if (response != null) put("response", encode("$scope:$id:response", response))
        }, "scope=? AND id=?", arrayOf(scope, id))
    }
    fun retry(scope: String, id: String) = synchronized(syncLock) {
        writableDatabase.update("outbox", ContentValues().apply { put("state", "queued"); putNull("error") },
            "scope=? AND id=? AND state='failed'", arrayOf(scope,id)); Unit
    }
    fun acknowledge(scope: String, id: String) {
        val completed = rows(scope).let { values -> (0 until values.length()).map { values.getJSONObject(it) }.firstOrNull { it.getString("id")==id && it.getString("state")=="synced" } }
        val db = writableDatabase
        db.beginTransaction()
        try {
            val deleted = db.delete("outbox", "scope=? AND id=? AND state='synced'", arrayOf(scope, id))
            if(deleted > 0) db.delete("blobs","k=?",arrayOf("outbox:$scope:$id"))
            db.setTransactionSuccessful()
        } finally { db.endTransaction() }
        if (completed != null) {
            val command = completed.getJSONObject("command")
            val fileId = when (command.optString("type")) {
                "media" -> command.getJSONObject("body").optString("file_id")
                "commissioning" -> completed.optString("file_id")
                else -> ""
            }
            if (fileId.isNotEmpty()) MediaVault.remove(context,scope,fileId)
        }
    }
    /** Returns true only for a transient failure that needs OS retry. */
    fun synchronize(scope: String, stopped: () -> Boolean = { false }): Boolean = synchronized(syncLock) {
        val auth = read("auth_$scope")?.let { JSONObject(it) } ?: return@synchronized false
        if (Instant.parse(auth.getString("expires_at")).isBefore(Instant.now())) return@synchronized false
        val uri = URI(auth.getString("server"))
        require(uri.userInfo == null && uri.query == null && uri.fragment == null && uri.path in listOf("", "/"))
        require(uri.scheme == "https" || (uri.scheme == "http" && uri.host in listOf("localhost", "127.0.0.1", "::1")))
        val pending = rows(scope)
        val blocked = mutableSetOf<Long>()
        var processed = 0
        for (i in 0 until pending.length()) {
            if (stopped()) return@synchronized true
            val row = pending.getJSONObject(i)
            val command = row.getJSONObject("command")
            val commissioning = command.optString("type") == "commissioning"
            val project = if (commissioning) row.getLong("commissioning_project") else 0
            val ticket = if (commissioning) -2000000000L - project else command.optLong("ticket", row.optLong("local_id",0))
            if (row.getString("state") == "failed") { blocked.add(ticket); continue }
            if (row.getString("state") != "queued" || ticket in blocked) continue
            if (++processed > 50) return@synchronized true
            var connection: HttpURLConnection? = null
            try {
                val media = command.optString("type")=="media"
                val route = when {
                    commissioning -> "/api/commissioning/$project/sync/"
                    media -> "/api/media/uploads/"
                    else -> "/api/mobile/sync/"
                }
                connection = uri.resolve(route).toURL().openConnection() as HttpURLConnection
                connection.instanceFollowRedirects = false
                connection.connectTimeout = 20000; connection.readTimeout = 30000
                connection.requestMethod = "POST"; connection.doOutput = true
                connection.setRequestProperty("Content-Type", "application/json")
                connection.setRequestProperty("Accept", "application/json")
                connection.setRequestProperty("Authorization", "Bearer ${auth.getString("token")}")
                connection.setRequestProperty("X-Organization-ID", auth.getInt("organization").toString())
                val request = if (commissioning) JSONObject()
                    .put("id", row.getString("id")).put("type", command.getString("action"))
                    .put("body", command.getJSONObject("body")) else command
                if (commissioning && row.has("file_id")) {
                    MediaVault.writeReceipt(context, scope, row.getString("file_id"), request, connection)
                } else if (media) MediaVault.writeUpload(context,scope,row,connection) else {
                    val bytes = request.toString().toByteArray(Charsets.UTF_8)
                    connection.setFixedLengthStreamingMode(bytes.size)
                    connection.outputStream.use { it.write(bytes) }
                }
                val status = connection.responseCode
                if (status in 500..599 || status in listOf(408,425,429)) return@synchronized true
                val stream = if (status in 200..299) connection.inputStream else connection.errorStream
                val content = stream?.use { input ->
                    val buffer = java.io.ByteArrayOutputStream()
                    val chunk = ByteArray(8192)
                    while (true) {
                        val n = input.read(chunk); if (n < 0) break
                        require(buffer.size() + n <= 4*1024*1024)
                        buffer.write(chunk,0,n)
                    }
                    buffer.toString("UTF-8")
                } ?: "{}"
                if (status in 200..299) {
                    val response = JSONObject(content)
                    require(response.getString("operation_id") == row.getString("id"))
                    when {
                        commissioning -> require(response.optJSONObject("result") != null)
                        media -> require(response.optString("state")=="READY" && (!response.isNull("asset") || response.optInt("message")>0))
                        else -> require(response.getJSONObject("ticket").getInt("id")>0)
                    }
                    finish(scope,row.getString("id"),"synced",content)
                    PushState.record(context,"offline_sent")
                } else {
                    val message = when (status) {
                        401 -> "Сеанс завершён. Войдите тем же аккаунтом и повторите отправку."
                        403 -> "Права или назначение изменились. Данные сохранены; обратитесь к диспетчеру."
                        404,405 -> "Обновите сервер для офлайн-синхронизации. Данные сохранены."
                        409 -> "Конфликт с сервером. Данные сохранены; требуется проверка."
                        else -> try { JSONObject(content).keys().asSequence().map { k -> JSONObject(content).get(k).toString() }.joinToString("\n").take(600) } catch (_: Exception) { "Сервер отклонил операцию ($status). Данные сохранены." }
                    }
                    finish(scope,row.getString("id"),"failed",error=message)
                    PushState.record(context,"offline_rejected_$status")
                    blocked.add(ticket)
                    if (status == 401) { delete("auth_$scope"); return@synchronized false }
                }
            } catch (_: MediaFileException) {
                finish(scope,row.getString("id"),"failed",error="Не удалось прочитать сохранённый файл. Он не удалён; передайте логи администратору.")
                PushState.record(context,"media_local_read_failed"); blocked.add(ticket)
            } catch (_: Exception) { return@synchronized true }
            finally { connection?.disconnect() }
        }
        false
    }
    fun syncAll(stopped: () -> Boolean): Boolean {
        val scopes = readableDatabase.rawQuery("SELECT k FROM kv WHERE k LIKE 'auth_%'", null).use { c ->
            val result = mutableListOf<String>(); while (c.moveToNext()) result.add(c.getString(0).removePrefix("auth_")); result
        }
        var retry = false
        for (scope in scopes) { if (stopped()) return true; retry = synchronize(scope, stopped) || retry }
        return retry
    }
    companion object {
        private const val ALIAS = "liftcontrol.offline.aes.v1"
        private val keyLock = Any()
        val syncLock = Any()
        private val executor = Executors.newCachedThreadPool()
        @Volatile private var instance: OfflineDatabase? = null
        fun get(context: Context): OfflineDatabase = instance ?: synchronized(this) {
            instance ?: OfflineDatabase(context.applicationContext).also { instance = it }
        }
        fun attach(activity: MainActivity, engine: FlutterEngine) {
            val db = get(activity)
            MethodChannel(engine.dartExecutor.binaryMessenger, "ru.liftcontrol.app/offline").setMethodCallHandler { call, result ->
                executor.execute {
                    try {
                        val scope = call.argument<String>("scope") ?: ""
                        val value: Any? = when (call.method) {
                            "mediaStage" -> MediaVault.stage(activity,scope,call.argument<String>("id")!!,call.argument<ByteArray>("bytes")!!)
                            "mediaRead" -> MediaVault.read(activity,scope,call.argument<String>("id")!!)
                            "mediaRemove" -> { MediaVault.remove(activity,scope,call.argument<String>("id")!!); null }
                            "downloadRead" -> DownloadedMediaCache.read(activity, scope, call.argument<String>("resource")!!)
                            "downloadWrite" -> {
                                DownloadedMediaCache.write(activity, scope, call.argument<String>("resource")!!,
                                    call.argument<ByteArray>("bytes")!!, call.argument<String>("etag")!!, call.argument<Int>("limit")!!)
                                null
                            }
                            "downloadRemove" -> { DownloadedMediaCache.remove(activity, scope, call.argument<String>("resource")!!); null }
                            "downloadClear" -> { DownloadedMediaCache.clear(activity, scope); null }
                            "read" -> db.read(call.argument<String>("key")!!)
                            "write" -> { db.write(call.argument<String>("key")!!,call.argument<String>("value")!!); null }
                            "delete" -> { db.delete(call.argument<String>("key")!!); null }
                            "clearCache" -> { db.clearCache(); null }
                            "clearAuth" -> { db.clearAuth(); null }
                            "configure" -> { synchronized(syncLock) { db.write("auth_$scope",call.argument<String>("value")!!) }; OfflineSyncJob.schedule(activity); null }
                            "rows" -> db.rows(scope).toString()
                            "enqueue" -> { db.enqueue(scope,JSONArray(call.argument<String>("rows")!!)); OfflineSyncJob.schedule(activity); null }
                            "retry" -> { db.retry(scope,call.argument<String>("id")!!); OfflineSyncJob.schedule(activity); null }
                            "acknowledge" -> { db.acknowledge(scope,call.argument<String>("id")!!); null }
                            "sync" -> { val retry = db.synchronize(scope); if (retry) OfflineSyncJob.schedule(activity); null }
                            else -> throw IllegalArgumentException("UNSUPPORTED_METHOD")
                        }
                        activity.runOnUiThread { result.success(value) }
                    } catch (_: Exception) {
                        activity.runOnUiThread { result.error("OFFLINE_STORAGE", "Не удалось сохранить или прочитать данные телефона. Запись не подтверждена. Проверьте свободное место.",null) }
                    }
                }
            }
        }
    }
}

class OfflineSyncJob : JobService() {
    @Volatile private var stopped = false
    override fun onStartJob(params: JobParameters): Boolean {
        stopped = false
        Thread {
            val retry = try { OfflineDatabase.get(this).syncAll { stopped } } catch (_: Exception) { true }
            if (!stopped) jobFinished(params,retry)
        }.start()
        return true
    }
    override fun onStopJob(params: JobParameters): Boolean { stopped = true; return true }
    companion object {
        private const val ID = 7401
        fun schedule(context: Context) {
            val scheduler = context.getSystemService(JobScheduler::class.java)
            if (scheduler.getPendingJob(ID) != null) return
            scheduler.schedule(JobInfo.Builder(ID,ComponentName(context,OfflineSyncJob::class.java))
                .setRequiredNetworkType(JobInfo.NETWORK_TYPE_ANY).setPersisted(true)
                .setBackoffCriteria(30000,JobInfo.BACKOFF_POLICY_EXPONENTIAL).build())
        }
    }
}
