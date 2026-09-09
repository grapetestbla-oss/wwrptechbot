package us.targetvpn.client

import android.content.ActivityNotFoundException
import android.content.BroadcastReceiver
import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.net.Uri
import android.net.VpnService
import android.os.Build
import android.os.Bundle
import android.view.View
import android.widget.Toast
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.lifecycle.lifecycleScope
import kotlinx.coroutines.launch
import us.targetvpn.client.databinding.ActivityMainBinding
import java.util.concurrent.TimeUnit

class MainActivity : AppCompatActivity() {

    private lateinit var binding: ActivityMainBinding
    private lateinit var storage: Storage
    private lateinit var api: Api
    private var connected = false

    companion object {
        const val ACTION_STATE = "us.targetvpn.client.STATE"
        const val EXTRA_CONNECTED = "connected"
        const val EXTRA_ERROR = "error"
        const val EXTRA_CHECK = "check"
    }

    /** Сервис сообщает, поднялся туннель или нет. */
    private val stateReceiver = object : BroadcastReceiver() {
        override fun onReceive(context: Context?, intent: Intent?) {
            connected = intent?.getBooleanExtra(EXTRA_CONNECTED, false) ?: false
            intent?.getStringExtra(EXTRA_ERROR)?.let { toast(it) }
            intent?.getStringExtra(EXTRA_CHECK)?.let {
                binding.diagnostic.text = it
                binding.diagnostic.visibility = View.VISIBLE
                binding.copyDiagnosticButton.visibility = View.VISIBLE
            }
            renderConnection()
        }
    }

    /** Разрешение на VPN запрашивается системой один раз. */
    private val vpnPermission = registerForActivityResult(
        ActivityResultContracts.StartActivityForResult()
    ) { result ->
        if (result.resultCode == RESULT_OK) {
            launchTunnel()
        } else {
            toast(getString(R.string.vpn_permission_needed))
            renderConnection()
        }
    }

    private val notificationPermission = registerForActivityResult(
        ActivityResultContracts.RequestPermission()
    ) { /* уведомление не обязательно, туннель работает и без него */ }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityMainBinding.inflate(layoutInflater)
        setContentView(binding.root)

        storage = Storage(this)
        api = Api(this)

        binding.bindButton.setOnClickListener { bind() }
        binding.connectButton.setOnClickListener { connectViaHapp() }
        binding.builtinButton.setOnClickListener { connect() }
        binding.copyButton.setOnClickListener { copyKey() }
        binding.refreshButton.setOnClickListener { refresh() }
        binding.copyDiagnosticButton.setOnClickListener { copyDiagnostic() }
        binding.location.setOnClickListener { chooseRegion() }
        binding.changeRegionButton.setOnClickListener { chooseRegion() }

        render()
        connected = TargetVpnService.isRunning()
        renderConnection()
        if (storage.isBound) refresh()
    }

    override fun onStart() {
        super.onStart()
        val filter = IntentFilter(ACTION_STATE)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            registerReceiver(stateReceiver, filter, RECEIVER_NOT_EXPORTED)
        } else {
            @Suppress("UnspecifiedRegisterReceiverFlag")
            registerReceiver(stateReceiver, filter)
        }
        connected = TargetVpnService.isRunning()
        renderConnection()
    }

    override fun onStop() {
        runCatching { unregisterReceiver(stateReceiver) }
        super.onStop()
    }

    /** Экран привязки или экран подписки — в зависимости от состояния. */
    private fun render(state: Api.State? = null) {
        val bound = storage.isBound
        binding.bindGroup.visibility = if (bound) View.GONE else View.VISIBLE
        binding.statusGroup.visibility = if (bound) View.VISIBLE else View.GONE
        if (!bound) return

        binding.deviceName.text = state?.deviceName?.takeIf { it.isNotBlank() }
            ?: storage.deviceName.ifBlank { getString(R.string.this_device) }
        binding.location.text = getString(
            R.string.location_fmt,
            state?.location?.takeIf { it.isNotBlank() } ?: storage.location.ifBlank { "—" }
        )

        if (state == null) return
        if (state.active && state.secondsLeft > 0) {
            if (!connected) binding.statusTitle.text = getString(R.string.status_active)
            binding.expires.text = getString(R.string.expires_fmt, humanLeft(state.secondsLeft))
            binding.connectButton.isEnabled = state.config.isNotEmpty() || storage.config != null
            renderConnection()
        } else {
            binding.statusTitle.text = getString(R.string.status_inactive)
            binding.expires.text = state.message.ifBlank { getString(R.string.subscription_ended) }
            binding.connectButton.isEnabled = false
        }
    }

    private fun bind() {
        val code = binding.codeInput.text.toString().trim()
        if (code.length < 4) {
            toast(getString(R.string.enter_code))
            return
        }
        binding.bindButton.isEnabled = false
        lifecycleScope.launch {
            runCatching { api.bind(code) }
                .onSuccess {
                    toast(getString(R.string.bound_ok))
                    render(it)
                    refresh()
                }
                .onFailure { toast(it.message ?: getString(R.string.error_generic)) }
            binding.bindButton.isEnabled = true
        }
    }

    private fun refresh() {
        binding.refreshButton.isEnabled = false
        lifecycleScope.launch {
            runCatching { api.state() }
                .onSuccess { render(it) }
                .onFailure {
                    toast(it.message ?: getString(R.string.error_generic))
                    // Токен могли отозвать после платной отвязки — вернёмся к вводу кода.
                    if (!storage.isBound) render()
                }
            binding.refreshButton.isEnabled = true
        }
    }

    /**
     * Основной путь: ключ отдаётся в Happ. Он принимает ссылки vless://
     * как deep link, поэтому отдельной схемы не нужно. Если приложения нет,
     * открываем страницу подключения — там ссылки на установку.
     */
    private fun connectViaHapp() {
        lifecycleScope.launch {
            val key = runCatching { api.config() }.getOrElse { storage.config.orEmpty() }
            if (key.isEmpty()) {
                toast(getString(R.string.no_key))
                return@launch
            }
            storage.config = key

            val intent = Intent(Intent.ACTION_VIEW, Uri.parse(key))
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            try {
                startActivity(intent)
            } catch (_: ActivityNotFoundException) {
                copyToClipboard(key)
                toast(getString(R.string.happ_missing))
                openConnectPage()
            }
        }
    }

    private fun openConnectPage() {
        val url = "${BuildConfig.API_BASE}/connect/${storage.subToken.orEmpty()}"
        try {
            startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(url)))
        } catch (_: ActivityNotFoundException) {
            toast(getString(R.string.error_generic))
        }
    }

    /** Запасной путь: свой туннель внутри приложения. */
    private fun connect() {
        if (connected) {
            TargetVpnService.disconnect(this)
            connected = false
            renderConnection()
            return
        }

        binding.builtinButton.isEnabled = false
        binding.statusTitle.text = getString(R.string.status_connecting)
        lifecycleScope.launch {
            val key = runCatching { api.config() }.getOrElse { storage.config.orEmpty() }
            if (key.isEmpty()) {
                toast(getString(R.string.no_key))
                binding.builtinButton.isEnabled = true
                return@launch
            }
            storage.config = key

            // Система показывает своё окно разрешения при первом подключении.
            val intent = VpnService.prepare(this@MainActivity)
            if (intent != null) vpnPermission.launch(intent) else launchTunnel()
        }
    }

    private fun launchTunnel() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
            ContextCompat.checkSelfPermission(this, android.Manifest.permission.POST_NOTIFICATIONS)
            != android.content.pm.PackageManager.PERMISSION_GRANTED
        ) {
            notificationPermission.launch(android.Manifest.permission.POST_NOTIFICATIONS)
        }
        TargetVpnService.connect(this, storage.config.orEmpty(), storage.location)
    }

    /** Рисует кнопку и заголовок под текущее состояние туннеля. */
    private fun renderConnection() {
        binding.connectButton.isEnabled = true
        binding.builtinButton.isEnabled = true
        binding.builtinButton.text =
            getString(if (connected) R.string.disconnect else R.string.connect_builtin)
        if (connected) {
            binding.statusTitle.text = getString(R.string.status_connected)
        }
    }

    /** Выбор локации: список приходит с сервера, ключ перевыпускается на месте. */
    private fun chooseRegion() {
        lifecycleScope.launch {
            val regions = runCatching { api.regions() }.getOrElse {
                toast(it.message ?: getString(R.string.error_generic))
                return@launch
            }
            if (regions.isEmpty()) {
                toast(getString(R.string.no_regions))
                return@launch
            }

            val titles = regions.map { "${it.flag} ${it.title}" }.toTypedArray()
            val checked = regions.indexOfFirst { it.isCurrent }
            AlertDialog.Builder(this@MainActivity)
                .setTitle(R.string.choose_region)
                .setSingleChoiceItems(titles, checked) { dialog, index ->
                    dialog.dismiss()
                    val region = regions[index]
                    if (region.isCurrent) return@setSingleChoiceItems
                    switchRegion(region.id)
                }
                .setNegativeButton(R.string.cancel, null)
                .show()
        }
    }

    private fun switchRegion(regionId: Int) {
        binding.changeRegionButton.isEnabled = false
        lifecycleScope.launch {
            runCatching { api.switchRegion(regionId) }
                .onSuccess {
                    toast(getString(R.string.region_changed, it))
                    refresh()
                }
                .onFailure { toast(it.message ?: getString(R.string.error_generic)) }
            binding.changeRegionButton.isEnabled = true
        }
    }


    private fun copyKey() {
        val key = storage.config.orEmpty()
        if (key.isEmpty()) {
            toast(getString(R.string.no_key))
            return
        }
        copyToClipboard(key)
    }

    /** Собирает всё, что нужно для разбора проблемы, в один текст. */
    private fun copyDiagnostic() {
        val report = buildString {
            appendLine("TargetVPN ${BuildConfig.VERSION_NAME}")
            appendLine("Сервер: ${BuildConfig.API_BASE}")
            appendLine("Устройство: ${Hwid.deviceModel()}, Android ${Build.VERSION.RELEASE}")
            appendLine("Локация: ${storage.location.ifBlank { "—" }}")
            appendLine("Туннель: ${if (connected) "включён" else "выключен"}")
            appendLine(binding.diagnostic.text.toString())
        }
        copyToClipboard(report)
    }

    private fun copyToClipboard(key: String) {
        val clipboard = getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
        clipboard.setPrimaryClip(ClipData.newPlainText("TargetVPN", key))
        toast(getString(R.string.key_copied))
    }

    private fun humanLeft(seconds: Long): String {
        val days = TimeUnit.SECONDS.toDays(seconds)
        val hours = TimeUnit.SECONDS.toHours(seconds) % 24
        val minutes = TimeUnit.SECONDS.toMinutes(seconds) % 60
        return when {
            days > 0 -> getString(R.string.left_days, days, hours)
            hours > 0 -> getString(R.string.left_hours, hours, minutes)
            else -> getString(R.string.left_minutes, minutes)
        }
    }

    private fun toast(text: String) = Toast.makeText(this, text, Toast.LENGTH_LONG).show()
}
