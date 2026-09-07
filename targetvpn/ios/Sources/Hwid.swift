import Foundation
import UIKit
import CryptoKit

/// Идентификатор устройства для привязки подписки.
///
/// `identifierForVendor` стабилен, пока на устройстве стоит хотя бы одно
/// приложение этого разработчика. Чтобы переустановка приложения не сбрасывала
/// привязку, значение дополнительно сохраняется в Keychain — он переживает
/// удаление приложения.
enum Hwid {

    static func get() -> String {
        if let saved = keychainRead() {
            return saved
        }
        let vendorId = UIDevice.current.identifierForVendor?.uuidString ?? UUID().uuidString
        let raw = "\(vendorId)|\(deviceModel())"
        let value = sha256(raw)
        keychainWrite(value)
        return value
    }

    static func deviceModel() -> String {
        var info = utsname()
        uname(&info)
        let machine = withUnsafePointer(to: &info.machine) { pointer in
            pointer.withMemoryRebound(to: CChar.self, capacity: 1) { String(cString: $0) }
        }
        return "Apple \(machine)"
    }

    private static func sha256(_ value: String) -> String {
        let digest = SHA256.hash(data: Data(value.utf8))
        return digest.map { String(format: "%02x", $0) }.joined()
    }

    // MARK: - Keychain

    private static let account = "targetvpn.hwid"

    private static func keychainRead() -> String? {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrAccount as String: account,
            kSecReturnData as String: true,
            kSecMatchLimit as String: kSecMatchLimitOne,
        ]
        var item: CFTypeRef?
        guard SecItemCopyMatching(query as CFDictionary, &item) == errSecSuccess,
              let data = item as? Data else { return nil }
        return String(data: data, encoding: .utf8)
    }

    private static func keychainWrite(_ value: String) {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrAccount as String: account,
        ]
        SecItemDelete(query as CFDictionary)

        var attributes = query
        attributes[kSecValueData as String] = Data(value.utf8)
        attributes[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlock
        SecItemAdd(attributes as CFDictionary, nil)
    }
}
