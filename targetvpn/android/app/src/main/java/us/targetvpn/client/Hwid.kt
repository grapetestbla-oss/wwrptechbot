package us.targetvpn.client

import android.annotation.SuppressLint
import android.content.Context
import android.os.Build
import android.provider.Settings
import java.security.MessageDigest

/**
 * Идентификатор устройства для привязки подписки.
 *
 * Берём ANDROID_ID (стабилен до сброса к заводским настройкам) вместе с моделью
 * и характеристиками сборки. На сервер уходит только хеш, само значение никуда
 * не передаётся.
 */
object Hwid {

    @SuppressLint("HardwareIds")
    fun get(context: Context): String {
        val androidId = Settings.Secure.getString(
            context.contentResolver, Settings.Secure.ANDROID_ID
        ) ?: "unknown"
        val raw = listOf(
            androidId,
            Build.MANUFACTURER,
            Build.MODEL,
            Build.DEVICE,
            Build.BOARD,
        ).joinToString("|")
        return sha256(raw)
    }

    fun deviceModel(): String = "${Build.MANUFACTURER} ${Build.MODEL}".take(64)

    private fun sha256(value: String): String {
        val digest = MessageDigest.getInstance("SHA-256").digest(value.toByteArray())
        return digest.joinToString("") { "%02x".format(it) }
    }
}
