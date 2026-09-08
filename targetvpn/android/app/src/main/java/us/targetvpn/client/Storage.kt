package us.targetvpn.client

import android.content.Context

/** Локальное хранилище привязки: токен доступа и последний известный конфиг. */
class Storage(context: Context) {

    private val prefs = context.getSharedPreferences("targetvpn", Context.MODE_PRIVATE)

    var token: String?
        get() = prefs.getString(KEY_TOKEN, null)
        set(value) = prefs.edit().putString(KEY_TOKEN, value).apply()

    var config: String?
        get() = prefs.getString(KEY_CONFIG, null)
        set(value) = prefs.edit().putString(KEY_CONFIG, value).apply()

    var deviceName: String
        get() = prefs.getString(KEY_NAME, "") ?: ""
        set(value) = prefs.edit().putString(KEY_NAME, value).apply()

    var location: String
        get() = prefs.getString(KEY_LOCATION, "") ?: ""
        set(value) = prefs.edit().putString(KEY_LOCATION, value).apply()

    /** Токен ссылки-подписки: по нему открывается страница подключения. */
    var subToken: String?
        get() = prefs.getString(KEY_SUB_TOKEN, null)
        set(value) = prefs.edit().putString(KEY_SUB_TOKEN, value).apply()

    fun clear() = prefs.edit().clear().apply()

    val isBound: Boolean get() = !token.isNullOrEmpty()

    private companion object {
        const val KEY_TOKEN = "token"
        const val KEY_CONFIG = "config"
        const val KEY_NAME = "device_name"
        const val KEY_LOCATION = "location"
        const val KEY_SUB_TOKEN = "sub_token"
    }
}
