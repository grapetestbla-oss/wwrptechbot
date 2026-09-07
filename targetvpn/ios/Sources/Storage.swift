import Foundation

/// Локальное состояние привязки: токен доступа и последний ключ подключения.
final class Storage: ObservableObject {

    private let defaults = UserDefaults.standard

    @Published var token: String? {
        didSet { defaults.set(token, forKey: "token") }
    }
    @Published var config: String? {
        didSet { defaults.set(config, forKey: "config") }
    }
    @Published var deviceName: String {
        didSet { defaults.set(deviceName, forKey: "deviceName") }
    }
    @Published var location: String {
        didSet { defaults.set(location, forKey: "location") }
    }

    init() {
        token = defaults.string(forKey: "token")
        config = defaults.string(forKey: "config")
        deviceName = defaults.string(forKey: "deviceName") ?? ""
        location = defaults.string(forKey: "location") ?? ""
    }

    var isBound: Bool { !(token ?? "").isEmpty }

    func reset() {
        token = nil
        config = nil
        deviceName = ""
        location = ""
    }
}
