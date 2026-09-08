package us.targetvpn.client

import android.net.Uri
import org.json.JSONArray
import org.json.JSONObject

/**
 * Собирает конфигурацию Xray из ссылки vless://.
 *
 * Локальный вход SOCKS слушает только петлевой адрес — в него tun2socks
 * отдаёт трафик TUN-интерфейса.
 */
object XrayConfig {

    const val SOCKS_PORT = 10808

    class InvalidKeyException(message: String) : Exception(message)

    fun build(link: String): String {
        val uri = Uri.parse(link.trim())
        if (uri.scheme != "vless") {
            throw InvalidKeyException("Ключ подключения не распознан")
        }
        val uuid = uri.userInfo ?: throw InvalidKeyException("В ключе нет идентификатора")
        val host = uri.host ?: throw InvalidKeyException("В ключе нет адреса сервера")
        val port = if (uri.port > 0) uri.port else 443

        val security = uri.getQueryParameter("security") ?: "reality"
        val stream = JSONObject()
            .put("network", uri.getQueryParameter("type") ?: "tcp")
            .put("security", security)

        when (security) {
            "reality" -> stream.put("realitySettings", JSONObject()
                .put("serverName", uri.getQueryParameter("sni").orEmpty())
                .put("fingerprint", uri.getQueryParameter("fp") ?: "chrome")
                .put("publicKey", uri.getQueryParameter("pbk").orEmpty())
                .put("shortId", uri.getQueryParameter("sid").orEmpty())
                .put("spiderX", uri.getQueryParameter("spx").orEmpty()))
            "tls" -> stream.put("tlsSettings", JSONObject()
                .put("serverName", uri.getQueryParameter("sni").orEmpty())
                .put("fingerprint", uri.getQueryParameter("fp") ?: "chrome")
                .put("allowInsecure", false))
        }

        val user = JSONObject()
            .put("id", uuid)
            .put("encryption", "none")
        uri.getQueryParameter("flow")?.takeIf { it.isNotBlank() }?.let { user.put("flow", it) }

        val proxy = JSONObject()
            .put("tag", "proxy")
            .put("protocol", "vless")
            .put("settings", JSONObject().put("vnext", JSONArray().put(
                JSONObject()
                    .put("address", host)
                    .put("port", port)
                    .put("users", JSONArray().put(user))
            )))
            .put("streamSettings", stream)

        val socksInbound = JSONObject()
            .put("tag", "socks-in")
            .put("listen", "127.0.0.1")
            .put("port", SOCKS_PORT)
            .put("protocol", "socks")
            .put("settings", JSONObject().put("udp", true).put("auth", "noauth"))
            .put("sniffing", JSONObject()
                .put("enabled", true)
                .put("destOverride", JSONArray().put("http").put("tls").put("quic")))

        // Локальные адреса мимо туннеля: иначе роутер и принтеры отвалятся.
        // Список задан явно: правило geoip:private требует файла geoip.dat,
        // которого в APK нет, и ядро отказывалось читать конфигурацию.
        val privateRanges = JSONArray()
        listOf(
            "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "127.0.0.0/8",
            "169.254.0.0/16", "224.0.0.0/4", "::1/128", "fc00::/7", "fe80::/10",
        ).forEach { privateRanges.put(it) }

        val routing = JSONObject()
            .put("domainStrategy", "IPIfNonMatch")
            .put("rules", JSONArray()
                .put(JSONObject()
                    .put("type", "field")
                    .put("ip", privateRanges)
                    .put("outboundTag", "direct")))

        return JSONObject()
            .put("log", JSONObject().put("loglevel", "warning"))
            .put("dns", JSONObject().put("servers", JSONArray()
                .put("1.1.1.1").put("8.8.8.8").put("localhost")))
            .put("inbounds", JSONArray().put(socksInbound))
            .put("outbounds", JSONArray()
                .put(proxy)
                .put(JSONObject().put("tag", "direct").put("protocol", "freedom"))
                .put(JSONObject().put("tag", "block").put("protocol", "blackhole")))
            .put("routing", routing)
            .toString()
    }
}
