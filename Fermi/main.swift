import AppKit
import ServiceManagement
import UniformTypeIdentifiers
import WebKit

// Fermi — macOS menu bar app to start/stop local AI services.
// Reads PORT/HOST/MODEL from ~/.config/argus/config (KEY=VALUE lines).

struct Config {
    var model = "mlx-community/Qwen3.8-27B-bf16"
    var host = "127.0.0.1"
    var port = "8090"

    static func load() -> Config {
        var c = Config()
        let path = NSString(string: "~/.config/argus/config").expandingTildeInPath
        guard let text = try? String(contentsOfFile: path, encoding: .utf8) else { return c }
        for line in text.split(separator: "\n") {
            let parts = line.split(separator: "=", maxSplits: 1).map(String.init)
            guard parts.count == 2 else { continue }
            let value = parts[1].trimmingCharacters(in: .whitespaces)
            switch parts[0].trimmingCharacters(in: .whitespaces) {
            case "MODEL" where !value.isEmpty: c.model = value
            case "HOST" where value == "127.0.0.1" || value == "0.0.0.0": c.host = value
            case "PORT" where (1024...65535).contains(Int(value) ?? 0): c.port = value
            default: break
            }
        }
        return c
    }
}

final class AppDelegate: NSObject, NSApplicationDelegate, NSWindowDelegate, WKUIDelegate, WKScriptMessageHandler {
    var statusItem: NSStatusItem!
    var timer: Timer?
    var config = Config.load()

    let ctlPath = NSString(string: "~/.local/bin/argus").expandingTildeInPath
    let logPath = NSString(string: "~/Library/Logs/argus.log").expandingTildeInPath
    let pidPath = NSString(string: "~/.local/state/argus/server.pid").expandingTildeInPath

    let statusLine = NSMenuItem(title: "Status: checking…", action: nil, keyEquivalent: "")
    let modelLine = NSMenuItem(title: "", action: nil, keyEquivalent: "")
    let startItem = NSMenuItem(title: "Start Server", action: #selector(startServer), keyEquivalent: "s")
    let stopItem = NSMenuItem(title: "Stop Server", action: #selector(stopServer), keyEquivalent: "x")
    let restartItem = NSMenuItem(title: "Restart Server", action: #selector(restartServer), keyEquivalent: "r")
    let loginItem = NSMenuItem(title: "Launch at Login", action: #selector(toggleLogin), keyEquivalent: "")
    let modelMenu = NSMenu()

    static let variants = [
        ("Qwen3.8-27B bf16 (~54 GB)", "mlx-community/Qwen3.8-27B-bf16"),
        ("Qwen3.8-27B 8bit (~29 GB)", "mlx-community/Qwen3.8-27B-8bit"),
        ("Qwen3.8-27B 4bit (~15 GB)", "mlx-community/Qwen3.8-27B-4bit"),
    ]

    var apiURL: String {
        let clientHost = config.host == "0.0.0.0" ? "127.0.0.1" : config.host
        return "http://\(clientHost):\(config.port)"
    }

    func setTray(symbol: String = "atom", color: NSColor = .secondaryLabelColor,
                 tooltip: String = "Fermi") {
        let config = NSImage.SymbolConfiguration(pointSize: 15, weight: .medium)
        let image = NSImage(systemSymbolName: symbol, accessibilityDescription: tooltip)?
            .withSymbolConfiguration(config)
        image?.isTemplate = true
        statusItem.button?.title = ""
        statusItem.button?.image = image
        statusItem.button?.imagePosition = .imageOnly
        statusItem.button?.contentTintColor = color
        statusItem.button?.toolTip = tooltip
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        // A regular Dock app needs an application menu as well as editing shortcuts.
        let mainMenu = NSMenu()
        let appHolder = NSMenuItem()
        let appMenu = NSMenu(title: "Fermi")
        appMenu.addItem(withTitle: "About Fermi", action: #selector(NSApplication.orderFrontStandardAboutPanel(_:)), keyEquivalent: "")
        appMenu.addItem(NSMenuItem.separator())
        appMenu.addItem(withTitle: "Quit Fermi", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        appHolder.submenu = appMenu
        mainMenu.addItem(appHolder)
        let editHolder = NSMenuItem()
        let edit = NSMenu(title: "Edit")
        edit.addItem(withTitle: "Cut", action: #selector(NSText.cut(_:)), keyEquivalent: "x")
        edit.addItem(withTitle: "Copy", action: #selector(NSText.copy(_:)), keyEquivalent: "c")
        edit.addItem(withTitle: "Paste", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        edit.addItem(withTitle: "Select All", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
        editHolder.submenu = edit
        mainMenu.addItem(editHolder)
        NSApp.mainMenu = mainMenu

        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        setTray()

        let menu = NSMenu()
        menu.autoenablesItems = false
        let openItem = NSMenuItem(title: "Open Fermi", action: #selector(openChat), keyEquivalent: "o")
        openItem.image = NSImage(systemSymbolName: "atom", accessibilityDescription: nil)
        openItem.target = self
        let menuSize = NSFont.menuFont(ofSize: 0).pointSize
        openItem.attributedTitle = NSAttributedString(
            string: "Open Fermi",
            attributes: [.font: NSFont.boldSystemFont(ofSize: menuSize)])
        menu.addItem(openItem)
        menu.addItem(NSMenuItem.separator())
        statusLine.isEnabled = false
        modelLine.isEnabled = false
        menu.addItem(statusLine)
        menu.addItem(modelLine)
        menu.addItem(NSMenuItem.separator())
        for item in [startItem, stopItem, restartItem] {
            item.target = self
            menu.addItem(item)
        }
        let modelMenuItem = NSMenuItem(title: "Model", action: nil, keyEquivalent: "")
        for (label, id) in Self.variants {
            let it = NSMenuItem(title: label, action: #selector(switchModel(_:)), keyEquivalent: "")
            it.target = self
            it.representedObject = id
            modelMenu.addItem(it)
        }
        modelMenuItem.submenu = modelMenu
        menu.addItem(modelMenuItem)
        menu.addItem(NSMenuItem.separator())
        let copyItem = NSMenuItem(title: "Copy API URL", action: #selector(copyURL), keyEquivalent: "c")
        copyItem.target = self
        menu.addItem(copyItem)
        let logItem = NSMenuItem(title: "Open Log", action: #selector(openLog), keyEquivalent: "l")
        logItem.target = self
        menu.addItem(logItem)
        menu.addItem(NSMenuItem.separator())
        loginItem.target = self
        menu.addItem(loginItem)
        let settingsItem = NSMenuItem(title: "Settings…", action: #selector(openSettings), keyEquivalent: ",")
        settingsItem.target = self
        menu.addItem(settingsItem)
        let quitItem = NSMenuItem(title: "Quit Fermi (server keeps running)", action: #selector(quitApp), keyEquivalent: "q")
        quitItem.target = self
        menu.addItem(quitItem)
        statusItem.menu = menu

        updateLoginState()
        refresh()
        timer = Timer.scheduledTimer(withTimeInterval: 8, repeats: true) { [weak self] _ in
            self?.refresh()
        }
    }

    func applicationShouldHandleReopen(_ sender: NSApplication,
                                       hasVisibleWindows flag: Bool) -> Bool {
        if !flag { openChat() }
        return true
    }

    func runCtl(_ args: String...) {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: ctlPath)
        p.arguments = args
        try? p.run()
    }

    var chatWindow: NSWindow?
    var chatWebView: WKWebView?

    @objc func openChat() {
        // Closing the last chat window hides Fermi from the Dock while the tray
        // and services keep running. Restore the Dock identity when reopened.
        NSApp.setActivationPolicy(.regular)
        runCtl("ui", "start")
        if chatWindow == nil {
            let web = WKWebView(frame: .zero)
            web.uiDelegate = self  // required, or the page's file input silently does nothing
            let win = NSWindow(
                contentRect: NSRect(x: 0, y: 0, width: 920, height: 720),
                styleMask: [.titled, .closable, .resizable, .miniaturizable],
                backing: .buffered, defer: false)
            win.delegate = self
            win.title = "Fermi"
            win.minSize = NSSize(width: 480, height: 400)
            win.center()
            win.contentView = web
            // Retain the window after Close. Besides preserving chat state, this
            // avoids releasing an NSWindow while AppKit's close animation is
            // still using it during the Dock-policy transition below.
            win.isReleasedWhenClosed = false
            chatWindow = win
            chatWebView = web
        }
        // small delay so a freshly spawned UI server is listening before we load
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.7) { [weak self] in
            guard let self, let web = self.chatWebView else { return }
            // Reload on every reopen: the UI may have restarted or moved after
            // a Settings port change while this retained window was closed.
            web.load(URLRequest(url: URL(string: "http://127.0.0.1:\(self.uiPort)")!))
        }
        NSApp.activate(ignoringOtherApps: true)
        chatWindow?.makeKeyAndOrderFront(nil)
    }

    func windowWillClose(_ notification: Notification) {
        guard let window = notification.object as? NSWindow, window === chatWindow else { return }
        // This is deliberately tied to Close, not miniaturize: a minimized
        // window remains represented in the Dock. Wait for AppKit's close
        // transform animation before changing activation policy.
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) {
            NSApp.setActivationPolicy(.accessory)
        }
    }

    func webView(_ webView: WKWebView,
                 runOpenPanelWith parameters: WKOpenPanelParameters,
                 initiatedByFrame frame: WKFrameInfo,
                 completionHandler: @escaping ([URL]?) -> Void) {
        let panel = NSOpenPanel()
        panel.canChooseFiles = true
        panel.canChooseDirectories = false
        panel.allowsMultipleSelection = parameters.allowsMultipleSelection
        panel.allowedContentTypes = [.image]
        panel.message = "Choose an image to attach"
        let done: (NSApplication.ModalResponse) -> Void = { resp in
            completionHandler(resp == .OK ? panel.urls : nil)
        }
        if let win = chatWindow {
            panel.beginSheetModal(for: win, completionHandler: done)
        } else {
            panel.begin(completionHandler: done)
        }
    }

    var uiPort: String {
        let path = NSString(string: "~/.config/argus/config").expandingTildeInPath
        if let text = try? String(contentsOfFile: path, encoding: .utf8) {
            for line in text.split(separator: "\n") where line.hasPrefix("UI_PORT=") {
                let value = String(line.dropFirst("UI_PORT=".count))
                    .trimmingCharacters(in: .whitespaces)
                if (1024...65535).contains(Int(value) ?? 0) { return value }
            }
        }
        return "8091"
    }

    @objc func startServer() {
        config = Config.load()
        runCtl("start")
        setTray(color: .systemOrange, tooltip: "Fermi · loading model")
        statusLine.title = "Status: loading model (1–2 min, longer on first download)…"
        DispatchQueue.main.asyncAfter(deadline: .now() + 3) { self.refresh() }
    }

    @objc func stopServer() {
        runCtl("stop")
        DispatchQueue.main.asyncAfter(deadline: .now() + 1) { self.refresh() }
    }

    @objc func restartServer() {
        config = Config.load()
        runCtl("restart")
        setTray(color: .systemOrange, tooltip: "Fermi · restarting")
        DispatchQueue.main.asyncAfter(deadline: .now() + 3) { self.refresh() }
    }

    var settingsWindow: NSWindow?
    var settingsWebView: WKWebView?

    @objc func openSettings() {
        runCtl("ui", "start")
        if settingsWindow == nil {
            let cfg = WKWebViewConfiguration()
            cfg.userContentController.add(self, name: "argus")
            let web = WKWebView(frame: .zero, configuration: cfg)
            let win = NSWindow(
                contentRect: NSRect(x: 0, y: 0, width: 560, height: 660),
                styleMask: [.titled, .closable, .resizable],
                backing: .buffered, defer: false)
            win.title = "Fermi Settings"
            win.minSize = NSSize(width: 460, height: 420)
            win.center()
            win.contentView = web
            win.isReleasedWhenClosed = false
            settingsWindow = win
            settingsWebView = web
        }
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.7) { [weak self] in
            guard let self, let web = self.settingsWebView else { return }
            // always reload so the form reflects the current config
            web.load(URLRequest(url: URL(string: "http://127.0.0.1:\(self.uiPort)/settings")!))
        }
        NSApp.activate(ignoringOtherApps: true)
        settingsWindow?.makeKeyAndOrderFront(nil)
    }

    func userContentController(_ controller: WKUserContentController,
                               didReceive message: WKScriptMessage) {
        guard let body = message.body as? [String: Any],
              let action = body["action"] as? String else { return }
        switch action {
        case "getLogin":
            let on = SMAppService.mainApp.status == .enabled
            settingsWebView?.evaluateJavaScript("window.setLoginState(\(on))")
        case "setLogin":
            let want = body["value"] as? Bool ?? false
            if want { try? SMAppService.mainApp.register() } else { try? SMAppService.mainApp.unregister() }
            updateLoginState()
            let on = SMAppService.mainApp.status == .enabled
            settingsWebView?.evaluateJavaScript("window.setLoginState(\(on))")
        default:
            break
        }
    }

    @objc func switchModel(_ sender: NSMenuItem) {
        guard let id = sender.representedObject as? String, id != config.model else { return }
        runCtl("use", id)
        config.model = id
        setTray(color: .systemOrange, tooltip: "Fermi · switching model")
        statusLine.title = "Status: switching model…"
        DispatchQueue.main.asyncAfter(deadline: .now() + 3) { self.refresh() }
    }

    @objc func copyURL() {
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString("\(apiURL)/v1", forType: .string)
    }

    @objc func openLog() {
        if !FileManager.default.fileExists(atPath: logPath) {
            FileManager.default.createFile(atPath: logPath, contents: nil)
        }
        NSWorkspace.shared.open(URL(fileURLWithPath: logPath))
    }

    @objc func toggleLogin() {
        let svc = SMAppService.mainApp
        if svc.status == .enabled {
            try? svc.unregister()
        } else {
            try? svc.register()
        }
        updateLoginState()
    }

    func updateLoginState() {
        loginItem.state = SMAppService.mainApp.status == .enabled ? .on : .off
    }

    @objc func quitApp() {
        NSApp.terminate(nil)
    }

    var pidAlive: Bool {
        guard let s = try? String(contentsOfFile: pidPath, encoding: .utf8),
              let pid = Int32(s.trimmingCharacters(in: .whitespacesAndNewlines)) else { return false }
        guard kill(pid, 0) == 0 else { return false }
        let task = Process()
        let output = Pipe()
        task.executableURL = URL(fileURLWithPath: "/bin/ps")
        task.arguments = ["-p", String(pid), "-o", "command="]
        task.standardOutput = output
        task.standardError = FileHandle.nullDevice
        do {
            try task.run()
            task.waitUntilExit()
            guard task.terminationStatus == 0 else { return false }
            let data = output.fileHandleForReading.readDataToEndOfFile()
            let command = String(data: data, encoding: .utf8) ?? ""
            guard command.contains("mlx_vlm.server") else { return false }
            let args = command.split(whereSeparator: { $0.isWhitespace }).map(String.init)
            return args.enumerated().contains { index, arg in
                arg == "--port=\(config.port)" ||
                    (arg == "--port" && index + 1 < args.count && args[index + 1] == config.port)
            }
        } catch {
            return false
        }
    }

    func refresh() {
        config = Config.load()
        modelLine.title = "Model: \(config.model)"
        for item in modelMenu.items {
            item.state = (item.representedObject as? String) == config.model ? .on : .off
        }
        var req = URLRequest(url: URL(string: "\(apiURL)/health")!)
        req.timeoutInterval = 2
        URLSession.shared.dataTask(with: req) { [weak self] data, resp, _ in
            guard let self else { return }
            let ready = (resp as? HTTPURLResponse)?.statusCode == 200
            let alive = self.pidAlive
            let loadedModel: String? = data.flatMap {
                (try? JSONSerialization.jsonObject(with: $0) as? [String: Any])?["loaded_model"] as? String
            }
            DispatchQueue.main.async {
                if ready {
                    let activeModel = loadedModel ?? self.config.model
                    self.modelLine.title = "Model: \(activeModel)"
                    for item in self.modelMenu.items {
                        item.state = (item.representedObject as? String) == activeModel ? .on : .off
                    }
                    self.setTray(color: .systemGreen, tooltip: "Fermi · ready")
                    self.statusLine.title = "Status: ready — \(self.apiURL)"
                } else if alive {
                    self.setTray(color: .systemOrange, tooltip: "Fermi · loading model")
                    self.statusLine.title = "Status: busy or loading model…"
                } else {
                    self.setTray(symbol: "atom", tooltip: "Fermi · not running")
                    self.statusLine.title = "Status: not running"
                }
                self.startItem.isEnabled = !(ready || alive)
                self.stopItem.isEnabled = ready || alive
                self.restartItem.isEnabled = ready || alive
            }
        }.resume()
    }
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.regular)
app.run()
