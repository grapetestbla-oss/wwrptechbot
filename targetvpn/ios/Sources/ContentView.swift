import SwiftUI
import UIKit

struct ContentView: View {

    @EnvironmentObject private var storage: Storage
    @State private var code = ""
    @State private var state: Api.State?
    @State private var busy = false
    @State private var message: String?

    private var api: Api { Api(storage: storage) }

    var body: some View {
        ZStack {
            Color(red: 0.043, green: 0.055, blue: 0.078).ignoresSafeArea()

            ScrollView {
                VStack(spacing: 20) {
                    header
                    if storage.isBound { statusCard } else { bindCard }
                    Text("Подписка привязана к этому устройству. Чтобы перейти на другой телефон, отвяжите его в мини-аппе Telegram.")
                        .font(.caption)
                        .foregroundColor(.gray)
                        .multilineTextAlignment(.center)
                        .padding(.horizontal)
                }
                .padding(24)
            }
        }
        .preferredColorScheme(.dark)
        .alert("TargetVPN", isPresented: Binding(
            get: { message != nil },
            set: { if !$0 { message = nil } }
        )) {
            Button("Понятно", role: .cancel) { message = nil }
        } message: {
            Text(message ?? "")
        }
        .task { if storage.isBound { await refresh() } }
    }

    private var header: some View {
        VStack(spacing: 4) {
            Text("TargetVPN").font(.system(size: 28, weight: .bold)).foregroundColor(.white)
            Text("Доступ без блокировок").font(.subheadline).foregroundColor(.gray)
        }
        .padding(.top, 32)
    }

    private var bindCard: some View {
        card {
            Text("Привязка устройства").font(.headline).foregroundColor(.white)
            Text("Откройте бота TargetVPN в Telegram, нажмите «Показать код привязки» и введите его здесь.")
                .font(.footnote).foregroundColor(.gray)

            TextField("КОД", text: $code)
                .textInputAutocapitalization(.characters)
                .autocorrectionDisabled()
                .font(.system(size: 22, weight: .semibold, design: .monospaced))
                .foregroundColor(.white)
                .padding(14)
                .background(Color.black.opacity(0.35))
                .cornerRadius(12)

            actionButton("Привязать", filled: true) {
                Task { await bind() }
            }
        }
    }

    private var statusCard: some View {
        card {
            Text(statusTitle).font(.title2.bold()).foregroundColor(.white)
            Text(statusSubtitle).font(.subheadline).foregroundColor(.gray)

            VStack(alignment: .leading, spacing: 2) {
                Text(storage.deviceName.isEmpty ? "Это устройство" : storage.deviceName)
                    .foregroundColor(.white)
                Text("Локация: \(storage.location.isEmpty ? "—" : storage.location)")
                    .font(.footnote).foregroundColor(.gray)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.top, 8)

            actionButton("Подключиться", filled: true) { Task { await connect() } }
            actionButton("Скопировать ключ", filled: false) { copyKey() }
            actionButton("Обновить статус", filled: false) { Task { await refresh() } }
        }
    }

    private var statusTitle: String {
        guard let state else { return "Проверяем подписку…" }
        return state.active && state.secondsLeft > 0 ? "Подписка активна" : "Подписка неактивна"
    }

    private var statusSubtitle: String {
        guard let state else { return "" }
        if state.active && state.secondsLeft > 0 {
            return "Осталось \(humanLeft(state.secondsLeft))"
        }
        return state.message.isEmpty ? "Продлите подписку в Telegram-боте" : state.message
    }

    // MARK: - Действия

    private func bind() async {
        guard code.trimmingCharacters(in: .whitespaces).count >= 4 else {
            message = "Введите код из мини-аппа"
            return
        }
        busy = true
        do {
            _ = try await api.bind(code: code)
            await refresh()
            message = "Устройство привязано"
        } catch {
            message = error.localizedDescription
        }
        busy = false
    }

    private func refresh() async {
        busy = true
        do {
            state = try await api.state()
        } catch {
            message = error.localizedDescription
        }
        busy = false
    }

    /// Ключ передаётся установленному VPN-клиенту: на iOS своё ядро можно
    /// запускать только через Network Extension, а она требует платного
    /// аккаунта разработчика.
    private func connect() async {
        let key = (try? await api.config()) ?? storage.config ?? ""
        guard !key.isEmpty, let url = URL(string: key) else {
            message = "Ключ ещё не выдан"
            return
        }
        await MainActor.run {
            if UIApplication.shared.canOpenURL(url) {
                UIApplication.shared.open(url)
            } else {
                UIPasteboard.general.string = key
                message = "Ключ скопирован. Установите Streisand или V2Box и вставьте его."
            }
        }
    }

    private func copyKey() {
        guard let key = storage.config, !key.isEmpty else {
            message = "Ключ ещё не выдан"
            return
        }
        UIPasteboard.general.string = key
        message = "Ключ скопирован"
    }

    private func humanLeft(_ seconds: Int) -> String {
        let days = seconds / 86_400
        let hours = (seconds % 86_400) / 3_600
        let minutes = (seconds % 3_600) / 60
        if days > 0 { return "\(days) дн. \(hours) ч." }
        if hours > 0 { return "\(hours) ч. \(minutes) мин." }
        return "\(minutes) мин."
    }

    // MARK: - Оформление

    private func card<Content: View>(@ViewBuilder content: () -> Content) -> some View {
        VStack(alignment: .leading, spacing: 12, content: content)
            .padding(20)
            .background(Color(red: 0.086, green: 0.11, blue: 0.169))
            .cornerRadius(18)
            .overlay(RoundedRectangle(cornerRadius: 18).stroke(Color.white.opacity(0.08)))
    }

    private func actionButton(_ title: String, filled: Bool, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Text(title)
                .font(.system(size: 16, weight: .semibold))
                .frame(maxWidth: .infinity)
                .padding(.vertical, 14)
                .background(filled ? Color(red: 0.357, green: 0.549, blue: 1.0)
                                   : Color.white.opacity(0.08))
                .foregroundColor(.white)
                .cornerRadius(14)
        }
        .disabled(busy)
    }
}
