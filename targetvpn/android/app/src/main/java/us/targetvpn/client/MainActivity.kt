package us.targetvpn.client

import android.content.ActivityNotFoundException
import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.view.View
import android.widget.Toast
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import kotlinx.coroutines.launch
import us.targetvpn.client.databinding.ActivityMainBinding
import java.util.concurrent.TimeUnit

class MainActivity : AppCompatActivity() {

    private lateinit var binding: ActivityMainBinding
    private lateinit var storage: Storage
    private lateinit var api: Api

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityMainBinding.inflate(layoutInflater)
        setContentView(binding.root)

        storage = Storage(this)
        api = Api(this)

        binding.bindButton.setOnClickListener { bind() }
        binding.connectButton.setOnClickListener { connect() }
        binding.copyButton.setOnClickListener { copyKey() }
        binding.refreshButton.setOnClickListener { refresh() }
        binding.location.setOnClickListener { chooseRegion() }
        binding.changeRegionButton.setOnClickListener { chooseRegion() }

        render()
        if (storage.isBound) refresh()
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
            binding.statusTitle.text = getString(R.string.status_active)
            binding.expires.text = getString(R.string.expires_fmt, humanLeft(state.secondsLeft))
            binding.connectButton.isEnabled = state.config.isNotEmpty() || storage.config != null
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
     * Запуск подключения. Ключ передаётся установленному VPN-клиенту
     * (v2rayNG, Hiddify и совместимые) — они понимают ссылку vless://.
     */
    private fun connect() {
        lifecycleScope.launch {
            val key = runCatching { api.config() }.getOrElse { storage.config.orEmpty() }
            if (key.isEmpty()) {
                toast(getString(R.string.no_key))
                return@launch
            }
            val intent = Intent(Intent.ACTION_VIEW, Uri.parse(key)).apply {
                addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            }
            // resolveActivity на Android 11+ врёт про отсутствие приложения,
            // поэтому просто пробуем открыть и ловим отказ.
            try {
                startActivity(intent)
            } catch (_: ActivityNotFoundException) {
                showNoClientDialog(key)
            }
        }
    }

    /** Совместимого клиента нет — открываем страницу подключения со списком приложений. */
    private fun showNoClientDialog(key: String) {
        AlertDialog.Builder(this)
            .setTitle(R.string.no_client_title)
            .setMessage(R.string.no_client_message)
            .setPositiveButton(R.string.open_connect_page) { _, _ ->
                copyToClipboard(key)
                openLink("${BuildConfig.API_BASE}/connect/${storage.subToken.orEmpty()}")
            }
            .setNeutralButton(R.string.copy_key) { _, _ -> copyToClipboard(key) }
            .setNegativeButton(R.string.cancel, null)
            .show()
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

    private fun openLink(url: String) {
        try {
            startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(url)))
        } catch (_: ActivityNotFoundException) {
            toast(getString(R.string.error_generic))
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
