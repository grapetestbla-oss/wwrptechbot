package us.targetvpn.client

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.net.VpnService
import android.os.Build
import android.os.ParcelFileDescriptor
import android.util.Log
import okhttp3.OkHttpClient
import okhttp3.Request
import targetcore.Targetcore
import android.net.Uri
import java.net.InetSocketAddress
import java.net.Proxy
import java.net.Socket
import java.util.concurrent.TimeUnit

/**
 * Свой туннель: поднимает TUN-интерфейс и отдаёт его дескриптор нативному
 * ядру (Xray + tun2socks внутри APK). Сторонний VPN-клиент не нужен.
 */
class TargetVpnService : VpnService() {

    private var tunnel: ParcelFileDescriptor? = null

    companion object {
        const val ACTION_CONNECT = "us.targetvpn.client.CONNECT"
        const val ACTION_DISCONNECT = "us.targetvpn.client.DISCONNECT"
        const val EXTRA_KEY = "key"
        const val EXTRA_LOCATION = "location"

        private const val CHANNEL_ID = "targetvpn"
        private const val NOTIFICATION_ID = 1
        private const val MTU = 1500
        private const val TAG = "TargetVpn"

        /** Состояние ядра — по нему интерфейс рисует кнопку. */
        fun isRunning(): Boolean = runCatching { Targetcore.isRunning() }.getOrDefault(false)

        fun connect(context: Context, key: String, location: String) {
            val intent = Intent(context, TargetVpnService::class.java)
                .setAction(ACTION_CONNECT)
                .putExtra(EXTRA_KEY, key)
                .putExtra(EXTRA_LOCATION, location)
            ContextCompat_startForegroundService(context, intent)
        }

        fun disconnect(context: Context) {
            context.startService(Intent(context, TargetVpnService::class.java)
                .setAction(ACTION_DISCONNECT))
        }

        private fun ContextCompat_startForegroundService(context: Context, intent: Intent) {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                context.startForegroundService(intent)
            } else {
                context.startService(intent)
            }
        }
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_DISCONNECT -> {
                stopTunnel()
                stopSelf()
                return START_NOT_STICKY
            }
            ACTION_CONNECT -> {
                val key = intent.getStringExtra(EXTRA_KEY).orEmpty()
                val location = intent.getStringExtra(EXTRA_LOCATION).orEmpty()
                startTunnel(key, location)
            }
        }
        return START_STICKY
    }

    private fun startTunnel(key: String, location: String) {
        if (key.isEmpty()) {
            broadcastState(false, getString(R.string.no_key))
            stopSelf()
            return
        }

        stopTunnel()
        startForeground(NOTIFICATION_ID, buildNotification(location))

        val config = try {
            XrayConfig.build(key)
        } catch (error: Exception) {
            broadcastState(false, error.message ?: getString(R.string.error_generic))
            stopSelf()
            return
        }

        val descriptor = try {
            buildTun()
        } catch (error: Exception) {
            Log.e(TAG, "TUN не поднялся", error)
            broadcastState(false, getString(R.string.tunnel_failed))
            stopSelf()
            return
        }

        try {
            Targetcore.start(descriptor.fd.toLong(), config, MTU.toLong())
            tunnel = descriptor
            broadcastState(true, null)
            runSelfCheck(key)
        } catch (error: Exception) {
            Log.e(TAG, "ядро не запустилось", error)
            descriptor.close()
            broadcastState(false, error.message ?: getString(R.string.tunnel_failed))
            stopSelf()
        }
    }

    private fun buildTun(): ParcelFileDescriptor {
        val builder = Builder()
            .setSession(getString(R.string.app_name))
            .setMtu(MTU)
            .addAddress("10.10.10.1", 32)
            .addDnsServer("1.1.1.1")
            .addDnsServer("8.8.8.8")
            .addRoute("0.0.0.0", 0)

        // Трафик самого приложения не заворачиваем: иначе соединение ядра
        // с сервером ушло бы в собственный туннель и получилась бы петля.
        // Если исключение не применилось — туннель бессмысленно поднимать.
        builder.addDisallowedApplication(packageName)

        return builder.establish() ?: throw IllegalStateException("VpnService.establish вернул null")
    }

    /**
     * Диагностика по шагам: сначала доступность самого сервера, потом канал
     * через ядро. По этим двум строкам сразу видно, где рвётся — на ноде,
     * в ключе или в маршрутизации устройства.
     */
    private fun runSelfCheck(key: String) {
        Thread {
            val serverLine = checkServerReachable(key)
            val client = OkHttpClient.Builder()
                .proxy(Proxy(Proxy.Type.SOCKS,
                    InetSocketAddress("127.0.0.1", XrayConfig.SOCKS_PORT)))
                .connectTimeout(15, TimeUnit.SECONDS)
                .readTimeout(15, TimeUnit.SECONDS)
                .build()

            val message = try {
                // Адрес без имени: проверяем именно канал, а не работу DNS.
                val request = Request.Builder().url("https://1.1.1.1/cdn-cgi/trace").build()
                client.newCall(request).execute().use { response ->
                    if (response.isSuccessful) getString(R.string.check_ok)
                    else getString(R.string.check_failed, "HTTP ${response.code}")
                }
            } catch (error: Exception) {
                Log.w(TAG, "самопроверка не прошла", error)
                getString(R.string.check_failed, error.message ?: error.javaClass.simpleName)
            }

            sendBroadcast(Intent(MainActivity.ACTION_STATE)
                .setPackage(packageName)
                .putExtra(MainActivity.EXTRA_CONNECTED, true)
                .putExtra(MainActivity.EXTRA_CHECK, "$serverLine\n$message"))
        }.start()
    }

    /** Обычное TCP-соединение до порта ноды, мимо туннеля. */
    private fun checkServerReachable(key: String): String {
        return try {
            val uri = Uri.parse(key)
            val host = uri.host ?: return getString(R.string.check_server_failed, "нет адреса")
            val port = if (uri.port > 0) uri.port else 443
            Socket().use { socket ->
                socket.connect(InetSocketAddress(host, port), 7000)
            }
            getString(R.string.check_server_ok, "$host:$port")
        } catch (error: Exception) {
            getString(R.string.check_server_failed,
                error.message ?: error.javaClass.simpleName)
        }
    }

    private fun stopTunnel() {
        runCatching { Targetcore.stop() }
        tunnel?.let { runCatching { it.close() } }
        tunnel = null
    }

    private fun broadcastState(connected: Boolean, error: String?) {
        sendBroadcast(Intent(MainActivity.ACTION_STATE)
            .setPackage(packageName)
            .putExtra(MainActivity.EXTRA_CONNECTED, connected)
            .putExtra(MainActivity.EXTRA_ERROR, error))
    }

    private fun buildNotification(location: String): Notification {
        val manager = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            manager.createNotificationChannel(NotificationChannel(
                CHANNEL_ID, getString(R.string.app_name), NotificationManager.IMPORTANCE_LOW))
        }

        val open = PendingIntent.getActivity(this, 0,
            Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
        val stop = PendingIntent.getService(this, 1,
            Intent(this, TargetVpnService::class.java).setAction(ACTION_DISCONNECT),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)

        val text = if (location.isBlank()) getString(R.string.status_connected)
        else getString(R.string.notification_connected, location)

        val builder = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            Notification.Builder(this, CHANNEL_ID)
        } else {
            @Suppress("DEPRECATION")
            Notification.Builder(this)
        }
        return builder
            .setContentTitle(getString(R.string.app_name))
            .setContentText(text)
            .setSmallIcon(R.drawable.ic_notification)
            .setContentIntent(open)
            .setOngoing(true)
            .addAction(Notification.Action.Builder(
                null, getString(R.string.disconnect), stop).build())
            .build()
    }

    override fun onRevoke() {
        stopTunnel()
        stopSelf()
    }

    override fun onDestroy() {
        stopTunnel()
        broadcastState(false, null)
        super.onDestroy()
    }
}
