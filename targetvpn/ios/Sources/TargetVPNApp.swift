import SwiftUI

@main
struct TargetVPNApp: App {

    @StateObject private var storage = Storage()

    var body: some Scene {
        WindowGroup {
            ContentView().environmentObject(storage)
        }
    }
}
