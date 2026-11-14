package ru.liftcontrol.liftcontrol_mobile

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import org.json.JSONObject
import java.io.File
import java.io.FileOutputStream
import java.net.HttpURLConnection
import java.security.KeyStore
import java.security.MessageDigest
import java.util.UUID
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

class MediaFileException : RuntimeException("MEDIA_FILE_UNAVAILABLE")

/** Pending originals live in filesDir, outside Android's disposable cache.
 * Files use AES-GCM with a non-exportable Keystore key and account/org AAD. */
object MediaVault {
    private const val ALIAS = "liftcontrol.media.aes.v1"
    internal fun digest(value: ByteArray) = MessageDigest.getInstance("SHA-256").digest(value).joinToString("") { "%02x".format(it) }
    private fun directory(context: Context, scope: String): File {
        require(scope.length in 1..500)
        return File(context.filesDir,"media-pending/"+digest(scope.toByteArray(Charsets.UTF_8))).apply { mkdirs() }
    }
    private fun path(context: Context, scope: String, id: String): File {
        require(UUID.fromString(id).toString() == id)
        return File(directory(context,scope),"$id.bin")
    }
    @Synchronized private fun key(): SecretKey {
        val keys = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
        return (keys.getKey(ALIAS,null) as? SecretKey) ?: KeyGenerator.getInstance("AES","AndroidKeyStore").run {
            init(KeyGenParameterSpec.Builder(ALIAS,KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT)
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM).setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE).setKeySize(256).build())
            generateKey()
        }
    }
    @Synchronized fun stage(context: Context, scope: String, id: String, bytes: ByteArray): Map<String,Any> {
        require(bytes.size in 1..20*1024*1024)
        val target = path(context,scope,id)
        if (target.exists()) {
            require(digest(read(context,scope,id)) == digest(bytes))
        } else {
            val used = File(context.filesDir,"media-pending").walkTopDown().filter { it.isFile }.sumOf { it.length() }
            require(used + bytes.size + 28 <= 150L*1024*1024)
            writeEncrypted(target, "$scope:$id", bytes)
        }
        PushState.record(context,"media_staged")
        return mapOf("file_id" to id,"size" to bytes.size,"sha256" to digest(bytes))
    }
    fun read(context: Context, scope: String, id: String): ByteArray {
        try {
            return readEncrypted(path(context,scope,id), "$scope:$id", 20L*1024*1024)
        } catch (_: Exception) { throw MediaFileException() }
    }

    internal fun writeEncrypted(target: File, context: String, bytes: ByteArray) {
        val cipher = Cipher.getInstance("AES/GCM/NoPadding").apply {
            init(Cipher.ENCRYPT_MODE,key())
            updateAAD(context.toByteArray(Charsets.UTF_8))
        }
        val temporary = File.createTempFile("stage-",".part",target.parentFile)
        try {
            FileOutputStream(temporary).use { output ->
                output.write(cipher.iv)
                output.write(cipher.doFinal(bytes))
                output.fd.sync()
            }
            require(temporary.renameTo(target))
        } finally { temporary.delete() }
    }

    internal fun readEncrypted(file: File, context: String, maxBytes: Long): ByteArray {
        require(file.length() in 29..maxBytes+28)
        val bytes = file.readBytes()
        return Cipher.getInstance("AES/GCM/NoPadding").run {
            init(Cipher.DECRYPT_MODE,key(),GCMParameterSpec(128,bytes.copyOfRange(0,12)))
            updateAAD(context.toByteArray(Charsets.UTF_8))
            doFinal(bytes,12,bytes.size-12)
        }
    }
    fun remove(context: Context, scope: String, id: String) { path(context,scope,id).delete() }

    fun writeUpload(context: Context, scope: String, row: JSONObject, connection: HttpURLConnection) {
        val body = row.getJSONObject("command").getJSONObject("body")
        val fields = linkedMapOf("upload_id" to row.getString("id"))
        for (key in listOf("kind","target_id","body","message_key","category","sha256","name","linked_ticket","linked_part")) {
            if (body.has(key)) fields[key] = body.get(key).toString()
        }
        val fileId = body.optString("file_id")
        val bytes = if (fileId.isNotEmpty()) read(context,scope,fileId) else ByteArray(0)
        writeMultipart(connection, fields, "file", bytes)
    }

    fun writeReceipt(context: Context, scope: String, fileId: String, command: JSONObject, connection: HttpURLConnection) {
        writeMultipart(connection, mapOf("command" to command.toString()), "receipt", read(context, scope, fileId))
    }

    private fun writeMultipart(connection: HttpURLConnection, fields: Map<String, String>, field: String, bytes: ByteArray) {
        val boundary = "lc-"+UUID.randomUUID().toString()
        val header = StringBuilder()
        for ((name,value) in fields) header.append("--$boundary\r\nContent-Disposition: form-data; name=\"$name\"\r\n\r\n$value\r\n")
        if (bytes.isNotEmpty()) header.append("--$boundary\r\nContent-Disposition: form-data; name=\"$field\"; filename=\"upload.bin\"\r\nContent-Type: application/octet-stream\r\n\r\n")
        val start = header.toString().toByteArray(Charsets.UTF_8)
        val end = "\r\n--$boundary--\r\n".toByteArray(Charsets.UTF_8)
        connection.setRequestProperty("Content-Type","multipart/form-data; boundary=$boundary")
        connection.setFixedLengthStreamingMode(start.size.toLong()+bytes.size+end.size)
        connection.outputStream.use { output -> output.write(start); output.write(bytes); output.write(end) }
    }
}
