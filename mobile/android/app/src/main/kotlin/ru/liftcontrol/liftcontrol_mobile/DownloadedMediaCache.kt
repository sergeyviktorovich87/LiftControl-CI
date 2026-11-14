package ru.liftcontrol.liftcontrol_mobile

import android.content.Context
import org.json.JSONObject
import java.io.File
import java.nio.ByteBuffer

/** Only completed downloads are stored here. Outbox files have a separate
 * lifetime and are never evicted by this cache. */
object DownloadedMediaCache {
    private const val MAX_ENTRY = 32L * 1024 * 1024

    private fun id(value: String): String {
        require(value.length in 1..1000)
        return MediaVault.digest(value.toByteArray(Charsets.UTF_8))
    }
    private fun root(context: Context) = File(context.filesDir, "media-downloaded")
    private fun path(context: Context, scope: String, resource: String) =
        File(File(root(context), id(scope)), id(resource) + ".bin")
    private fun aad(scope: String, resource: String) = "download-v1:${id(scope)}:${id(resource)}"

    @Synchronized fun read(context: Context, scope: String, resource: String): Map<String, Any>? {
        val file = path(context, scope, resource)
        if (!file.exists()) return null
        return try {
            val clear = ByteBuffer.wrap(MediaVault.readEncrypted(file, aad(scope, resource), MAX_ENTRY + 256))
            val size = clear.int
            require(size in 2..252 && size < clear.remaining())
            val header = ByteArray(size).also { clear.get(it) }
            val metadata = JSONObject(String(header, Charsets.UTF_8))
            val bytes = ByteArray(clear.remaining()).also { clear.get(it) }
            file.setLastModified(System.currentTimeMillis())
            mapOf("bytes" to bytes, "etag" to metadata.optString("etag"))
        } catch (_: Exception) {
            file.delete()
            PushState.record(context, "download_cache_read_failed")
            null
        }
    }

    @Synchronized fun write(context: Context, scope: String, resource: String, bytes: ByteArray, etag: String, limit: Int) {
        require(bytes.size in 1..MAX_ENTRY && etag.length <= 100 && limit in listOf(100, 500, 1024))
        val target = path(context, scope, resource)
        val header = JSONObject().put("etag", etag).toString().toByteArray(Charsets.UTF_8)
        require(header.size <= 252)
        val encryptedSize = bytes.size.toLong() + header.size + 32
        val files = root(context).walkTopDown().filter { it.isFile && it.extension == "bin" && it != target }
            .sortedBy { it.lastModified() }.toList()
        var used = files.sumOf { it.length() }
        for (file in files) {
            if (used + encryptedSize <= limit.toLong() * 1024 * 1024) break
            val size = file.length()
            if (file.delete()) used -= size
        }
        if (used + encryptedSize > limit.toLong() * 1024 * 1024) return
        require(target.parentFile!!.isDirectory || target.parentFile!!.mkdirs())
        val data = ByteBuffer.allocate(4 + header.size + bytes.size)
            .putInt(header.size).put(header).put(bytes).array()
        MediaVault.writeEncrypted(target, aad(scope, resource), data)
    }

    @Synchronized fun remove(context: Context, scope: String, resource: String) {
        path(context, scope, resource).delete()
    }

    @Synchronized fun clear(context: Context, scope: String) {
        File(root(context), id(scope)).deleteRecursively()
    }
}
