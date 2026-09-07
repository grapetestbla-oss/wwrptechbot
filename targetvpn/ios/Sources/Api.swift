import Foundation

/// Клиент API TargetVPN. Каждый запрос несёт токен привязки и HWID.
struct Api {

    struct State: Decodable {
        var active: Bool = false
        var deviceName: String = ""
        var location: String = ""
        var planTitle: String = ""
        var secondsLeft: Int = 0
        var config: String = ""
        var message: String = ""

        enum CodingKeys: String, CodingKey {
            case active
            case deviceName = "device_name"
            case location
            case planTitle = "plan_title"
            case secondsLeft = "seconds_left"
            case config
            case message
        }
    }

    struct BindResponse: Decodable {
        let token: String
        let deviceName: String
        let config: String
        let location: String
        let secondsLeft: Int

        enum CodingKeys: String, CodingKey {
            case token
            case deviceName = "device_name"
            case config
            case location
            case secondsLeft = "seconds_left"
        }
    }

    struct ApiError: LocalizedError {
        let message: String
        var errorDescription: String? { message }
    }

    private struct ErrorBody: Decodable { let detail: String? }

    /// Адрес бэкенда зашивается в Info.plist при сборке (`API_BASE=...`).
    static var baseURL: String {
        let value = Bundle.main.object(forInfoDictionaryKey: "APIBase") as? String ?? ""
        return value.isEmpty ? "https://example.com" : value
    }

    let storage: Storage

    func bind(code: String) async throws -> BindResponse {
        let body: [String: String] = [
            "code": code.trimmingCharacters(in: .whitespacesAndNewlines).uppercased(),
            "hwid": Hwid.get(),
            "name": Hwid.deviceModel(),
            "model": Hwid.deviceModel(),
            "app_version": Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? "1.0",
        ]
        var request = URLRequest(url: url("/api/client/bind"))
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONSerialization.data(withJSONObject: body)

        let response: BindResponse = try await send(request)
        await MainActor.run {
            storage.token = response.token
            storage.config = response.config
            storage.deviceName = response.deviceName
            storage.location = response.location
        }
        return response
    }

    func state() async throws -> State {
        let response: State = try await send(authorized(url("/api/client/state")))
        await MainActor.run {
            if !response.config.isEmpty { storage.config = response.config }
            if !response.location.isEmpty { storage.location = response.location }
        }
        return response
    }

    /// Свежий ключ перед подключением: сервер мог его перевыпустить.
    func config() async throws -> String {
        struct Payload: Decodable { let config: String }
        let response: Payload = try await send(authorized(url("/api/client/config")))
        await MainActor.run { storage.config = response.config }
        return response.config
    }

    // MARK: - Транспорт

    private func url(_ path: String) -> URL {
        URL(string: Api.baseURL + path)!
    }

    private func authorized(_ url: URL) -> URLRequest {
        var request = URLRequest(url: url)
        request.setValue("Bearer \(storage.token ?? "")", forHTTPHeaderField: "Authorization")
        request.setValue(Hwid.get(), forHTTPHeaderField: "X-HWID")
        return request
    }

    private func send<T: Decodable>(_ request: URLRequest) async throws -> T {
        let (data, response) = try await URLSession.shared.data(for: request)
        guard let http = response as? HTTPURLResponse else {
            throw ApiError(message: "Нет связи с сервером")
        }
        guard (200..<300).contains(http.statusCode) else {
            // 401 означает, что привязку сняли или сменился HWID.
            if http.statusCode == 401 {
                await MainActor.run { storage.token = nil }
            }
            let detail = (try? JSONDecoder().decode(ErrorBody.self, from: data))?.detail
            throw ApiError(message: detail ?? "Ошибка сервера (\(http.statusCode))")
        }
        return try JSONDecoder().decode(T.self, from: data)
    }
}
