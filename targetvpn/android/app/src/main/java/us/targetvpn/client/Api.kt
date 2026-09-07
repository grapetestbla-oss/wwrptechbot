package us.targetvpn.client

import android.content.Context
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject
import java.util.concurrent.TimeUnit

/** Клиент API TargetVPN. Каждый запрос несёт токен привязки и HWID. */
class Api(private val context: Context) {

    private val http = OkHttpClient.Builder()
        .connectTimeout(20, TimeUnit.SECONDS)
        .readTimeout(20, TimeUnit.SECONDS)
        .build()

    private val storage = Storage(context)
    private val hwid: String get() = Hwid.get(context)

    class ApiException(message: String) : Exception(message)

    data class State(
        val active: Boolean,
        val deviceName: String,
        val location: String,
        val planTitle: String,
        val secondsLeft: Long,
        val config: String,
        val message: String,
    )

    suspend fun bind(code: String): State = withContext(Dispatchers.IO) {
        val body = JSONObject()
            .put("code", code.trim().uppercase())
            .put("hwid", hwid)
            .put("name", Hwid.deviceModel())
            .put("model", Hwid.deviceModel())
            .put("app_version", BuildConfig.VERSION_NAME)
            .toString()

        val request = Request.Builder()
            .url("${BuildConfig.API_BASE}/api/client/bind")
            .post(body.toRequestBody(JSON))
            .build()

        val json = call(request)
        storage.token = json.getString("token")
        storage.config = json.getString("config")
        storage.deviceName = json.optString("device_name")
        storage.location = json.optString("location")
        State(
            active = true,
            deviceName = json.optString("device_name"),
            location = json.optString("location"),
            planTitle = "",
            secondsLeft = json.optLong("seconds_left"),
            config = json.getString("config"),
            message = "",
        )
    }

    suspend fun state(): State = withContext(Dispatchers.IO) {
        val request = Request.Builder()
            .url("${BuildConfig.API_BASE}/api/client/state")
            .header("Authorization", "Bearer ${storage.token.orEmpty()}")
            .header("X-HWID", hwid)
            .build()

        val json = call(request)
        val config = json.optString("config")
        if (config.isNotEmpty()) storage.config = config
        storage.location = json.optString("location")
        State(
            active = json.optBoolean("active"),
            deviceName = json.optString("device_name"),
            location = json.optString("location"),
            planTitle = json.optString("plan_title"),
            secondsLeft = json.optLong("seconds_left"),
            config = config,
            message = json.optString("message"),
        )
    }

    /** Свежий ключ перед запуском туннеля: сервер мог перевыпустить его. */
    suspend fun config(): String = withContext(Dispatchers.IO) {
        val request = Request.Builder()
            .url("${BuildConfig.API_BASE}/api/client/config")
            .header("Authorization", "Bearer ${storage.token.orEmpty()}")
            .header("X-HWID", hwid)
            .build()
        val json = call(request)
        json.getString("config").also { storage.config = it }
    }

    private fun call(request: Request): JSONObject {
        http.newCall(request).execute().use { response ->
            val text = response.body?.string().orEmpty()
            if (!response.isSuccessful) {
                val detail = runCatching { JSONObject(text).optString("detail") }.getOrNull()
                // 401 означает, что привязка снята или HWID сменился.
                if (response.code == 401) storage.token = null
                throw ApiException(detail?.takeIf { it.isNotBlank() }
                    ?: "Ошибка сервера (${response.code})")
            }
            return JSONObject(text)
        }
    }

    private companion object {
        val JSON = "application/json; charset=utf-8".toMediaType()
    }
}
