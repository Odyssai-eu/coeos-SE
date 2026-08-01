// Barre de menus CoeOS SE — lance le serveur gele (PyInstaller) embarque dans
// Contents/Resources/server/, surveille son /health et ouvre le dashboard.
//
// LSUIElement : pas d'icone dans le Dock. CoeOS SE est un service avec une UI
// web ; une icone Dock sans fenetre serait fausse.

import AppKit
import Foundation

let defaultPort = 4600

final class AppDelegate: NSObject, NSApplicationDelegate {
    private var statusItem: NSStatusItem!
    private var server: Process?
    private var statusLine: NSMenuItem!
    private var timer: Timer?
    private var opened = false

    private var version: String {
        Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "dev"
    }
    private var build: String {
        Bundle.main.object(forInfoDictionaryKey: "CFBundleVersion") as? String ?? "0"
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        if let button = statusItem.button {
            if let path = Bundle.main.path(forResource: "menubar", ofType: "png"),
               let image = NSImage(contentsOfFile: path) {
                image.size = NSSize(width: 18, height: 18)
                button.image = image
            } else {
                button.title = "CoeOS"
            }
        }
        buildMenu()
        startServer()
        timer = Timer.scheduledTimer(withTimeInterval: 2.0, repeats: true) { [weak self] _ in
            self?.pollHealth()
        }
        pollHealth()
    }

    private func buildMenu() {
        let menu = NSMenu()

        // Regle de build : la version doit etre lisible DANS l'UI, pas
        // seulement dans les infos du Finder.
        let header = NSMenuItem(title: "CoeOS SE \(version) (build \(build))",
                                action: nil, keyEquivalent: "")
        header.isEnabled = false
        menu.addItem(header)

        statusLine = NSMenuItem(title: "Starting…", action: nil, keyEquivalent: "")
        statusLine.isEnabled = false
        menu.addItem(statusLine)

        menu.addItem(.separator())
        menu.addItem(NSMenuItem(title: "Open Dashboard",
                                action: #selector(openDashboard), keyEquivalent: "d"))
        menu.addItem(NSMenuItem(title: "Client Setup…",
                                action: #selector(openEndpoints), keyEquivalent: "e"))
        menu.addItem(.separator())
        menu.addItem(NSMenuItem(title: "Quit CoeOS SE",
                                action: #selector(quit), keyEquivalent: "q"))

        for item in menu.items where item.action != nil { item.target = self }
        statusItem.menu = menu
    }

    // MARK: - serveur

    private func startServer() {
        guard let resources = Bundle.main.resourcePath else { return }
        let binary = resources + "/server/coeos-se"
        guard FileManager.default.isExecutableFile(atPath: binary) else {
            statusLine?.title = "Server binary missing"
            return
        }

        // La config (dont la cle OpenRouter) vit hors du bundle : un bundle
        // signe est en lecture seule et toute ecriture invaliderait la
        // signature.
        let support = FileManager.default.urls(for: .applicationSupportDirectory,
                                               in: .userDomainMask)[0]
            .appendingPathComponent("CoeOS SE", isDirectory: true)
        try? FileManager.default.createDirectory(at: support, withIntermediateDirectories: true)

        let process = Process()
        process.executableURL = URL(fileURLWithPath: binary)
        var env = ProcessInfo.processInfo.environment
        env["COEOS_CONFIG"] = support.appendingPathComponent("coeos-config.json").path
        env["COEOS_PORT"] = String(defaultPort)
        // Un .pyc ecrit apres signature invaliderait le bundle.
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        process.environment = env
        process.standardOutput = FileHandle.nullDevice
        process.standardError = FileHandle.nullDevice
        do {
            try process.run()
            server = process
        } catch {
            statusLine?.title = "Failed to start: \(error.localizedDescription)"
        }
    }

    private func pollHealth() {
        guard let url = URL(string: "http://127.0.0.1:\(defaultPort)/health") else { return }
        var request = URLRequest(url: url)
        request.timeoutInterval = 1.5
        URLSession.shared.dataTask(with: request) { [weak self] data, _, _ in
            guard let self else { return }
            var running = false
            var detail = ""
            if let data,
               let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any] {
                running = (json["ok"] as? Bool) ?? false
                if let axes = json["axes_bound"] as? Int { detail = " · \(axes) axes" }
            }
            DispatchQueue.main.async {
                self.statusLine?.title = running
                    ? "Running on port \(defaultPort)\(detail)"
                    : "Starting…"
                // Premier boot reussi : on montre le dashboard une seule fois.
                if running && !self.opened {
                    self.opened = true
                    self.openDashboard()
                }
            }
        }.resume()
    }

    // MARK: - actions

    @objc private func openDashboard() {
        open("http://127.0.0.1:\(defaultPort)/dashboard")
    }

    @objc private func openEndpoints() {
        open("http://127.0.0.1:\(defaultPort)/endpoints")
    }

    private func open(_ string: String) {
        guard let url = URL(string: string) else { return }
        NSWorkspace.shared.open(url)
    }

    @objc private func quit() {
        NSApp.terminate(nil)
    }

    func applicationWillTerminate(_ notification: Notification) {
        timer?.invalidate()
        server?.terminate()
    }
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.accessory)
app.run()
