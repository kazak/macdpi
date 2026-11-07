import AppKit
import Foundation
import SystemConfiguration

let appID = "local.tube-switch"
let agentID = "local.youtube-bypass"
let home = FileManager.default.homeDirectoryForCurrentUser
let support = home.appendingPathComponent("Library/Application Support/LocalYouTubeBypass")
let installedApp = home.appendingPathComponent("Applications/Tube Switch.app")
let menuPlist = home.appendingPathComponent("Library/LaunchAgents/\(appID).plist")
let apiBase = "http://127.0.0.1:10889"

struct AppError: LocalizedError {
    let message: String
    var errorDescription: String? { message }
}

struct BackendState: Decodable {
    let running: Bool
    let enabled: Bool?
    let api: Int?
    let services: [String]?
}

struct ServicePreset: Decodable {
    let id: String
    let title: String
    let url: String
    let symbol: String
    let defaultEnabled: Bool
    let note: String
}

enum Catalog {
    static let presets: [ServicePreset] = {
        guard let url = Bundle.main.url(forResource: "service-presets", withExtension: "json"),
              let data = try? Data(contentsOf: url),
              let items = try? JSONDecoder().decode([ServicePreset].self, from: data) else { return [] }
        return items
    }()
}

enum API {
    static let session: URLSession = {
        let config = URLSessionConfiguration.ephemeral
        config.connectionProxyDictionary = [:]
        config.timeoutIntervalForRequest = 4
        config.timeoutIntervalForResource = 5
        return URLSession(configuration: config)
    }()

    static func request(enabled: Bool? = nil, services: [String]? = nil,
                        completion: @escaping (Result<BackendState, Error>) -> Void) {
        let path = services != nil ? "/api/services" : (enabled == nil ? "/api/status" : "/api/enabled")
        var request = URLRequest(url: URL(string: apiBase + path)!)
        request.cachePolicy = .reloadIgnoringLocalCacheData
        if enabled != nil || services != nil {
            do {
                let token = try String(contentsOf: support.appendingPathComponent("control-token"), encoding: .utf8)
                    .trimmingCharacters(in: .whitespacesAndNewlines)
                request.httpMethod = "POST"
                request.setValue("Bearer " + token, forHTTPHeaderField: "Authorization")
                request.setValue("application/json", forHTTPHeaderField: "Content-Type")
                var body: [String: Any] = [:]
                if let services = services { body["services"] = services }
                else if let enabled = enabled { body["enabled"] = enabled }
                request.httpBody = try JSONSerialization.data(withJSONObject: body)
            } catch { completion(.failure(error)); return }
        }
        session.dataTask(with: request) { data, response, error in
            if let error = error { completion(.failure(error)); return }
            guard let http = response as? HTTPURLResponse, http.statusCode == 200, let data = data else {
                completion(.failure(AppError(message: "Локальный сервис не ответил. Попробуйте перезапуск.")))
                return
            }
            do { completion(.success(try JSONDecoder().decode(BackendState.self, from: data))) }
            catch { completion(.failure(error)) }
        }.resume()
    }

    static func ready() -> Bool {
        let sem = DispatchSemaphore(value: 0)
        var ready = false
        request { result in
            if case .success(let state) = result { ready = state.running && state.api == 2 }
            sem.signal()
        }
        _ = sem.wait(timeout: .now() + 6)
        return ready
    }
}

enum Install {
    static var backendProcess: Process?

    static func stopBackend() {
        if let process = backendProcess, process.isRunning {
            process.terminate()
            process.waitUntilExit()
        }
        backendProcess = nil
    }

    static func startBackend() throws {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/python3")
        process.arguments = [support.appendingPathComponent("agent.py").path]
        process.environment = ProcessInfo.processInfo.environment.merging(
            ["TUBE_SWITCH_OWNER_PID": String(ProcessInfo.processInfo.processIdentifier)],
            uniquingKeysWith: { _, new in new })
        let log = support.appendingPathComponent("service.log")
        if !FileManager.default.fileExists(atPath: log.path) {
            FileManager.default.createFile(atPath: log.path, contents: Data())
        }
        let handle = try FileHandle(forWritingTo: log)
        handle.seekToEndOfFile()
        process.standardOutput = handle
        process.standardError = handle
        process.standardInput = FileHandle.nullDevice
        try process.run()
        backendProcess = process
    }

    static func command(_ executable: String, _ args: [String], allowFailure: Bool = false) throws -> String {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: executable)
        process.arguments = args
        let pipe = Pipe()
        process.standardOutput = pipe
        process.standardError = pipe
        try process.run()
        let data = pipe.fileHandleForReading.readDataToEndOfFile()
        process.waitUntilExit()
        let text = String(data: data, encoding: .utf8) ?? ""
        if process.terminationStatus != 0 && !allowFailure {
            throw AppError(message: text.trimmingCharacters(in: .whitespacesAndNewlines))
        }
        return text
    }

    static func stopManagedOrphan() throws {
        let arguments = ["-nP", "-t", "-iTCP:10889", "-sTCP:LISTEN"]
        let output = try command("/usr/sbin/lsof", arguments, allowFailure: true)
        for text in Set(output.split(whereSeparator: { $0.isWhitespace }).map(String.init)) {
            guard let pid = Int32(text), pid > 1, pid != getpid() else { continue }
            let details = try command("/bin/ps", ["-p", text, "-o", "uid=,command="], allowFailure: true)
            if details.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty { continue }
            guard ManagedWorker.matches(processInfo: details, uid: Int(getuid()),
                                        scriptPath: support.appendingPathComponent("agent.py").path) else {
                throw AppError(message: "Порт локального сервиса занят другой программой. Её работа не была изменена.")
            }
            if kill(pid, SIGTERM) != 0 && errno != ESRCH {
                throw AppError(message: "Не удалось завершить оставшийся процесс прежней версии.")
            }
        }
        for _ in 0..<30 {
            if try command("/usr/sbin/lsof", arguments, allowFailure: true)
                .trimmingCharacters(in: .whitespacesAndNewlines).isEmpty { return }
            Thread.sleep(forTimeInterval: 0.1)
        }
        throw AppError(message: "Прежний локальный сервис ещё завершает работу. Повторите запуск через несколько секунд.")
    }

    static func writePlist(_ object: [String: Any], to url: URL) throws {
        try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        let data = try PropertyListSerialization.data(fromPropertyList: object, format: .xml, options: 0)
        try data.write(to: url, options: .atomic)
        try FileManager.default.setAttributes([.posixPermissions: 0o644], ofItemAtPath: url.path)
    }

    static func selfInstall() throws {
        let fm = FileManager.default
        let current = Bundle.main.bundleURL.standardizedFileURL
        if current != installedApp.standardizedFileURL {
            try fm.createDirectory(at: installedApp.deletingLastPathComponent(), withIntermediateDirectories: true)
            if fm.fileExists(atPath: installedApp.path) {
                guard Bundle(url: installedApp)?.bundleIdentifier == appID else {
                    throw AppError(message: "В папке «Программы» уже есть другое приложение Tube Switch.")
                }
                let oldExecutable = installedApp.appendingPathComponent("Contents/MacOS/TubeSwitch")
                let newExecutable = current.appendingPathComponent("Contents/MacOS/TubeSwitch")
                let resourcePaths = ["Contents/Info.plist", "Contents/Resources/agent.py", "Contents/Resources/service-presets.json"]
                let sameResources = resourcePaths.allSatisfy {
                    (try? Data(contentsOf: installedApp.appendingPathComponent($0)))
                        == (try? Data(contentsOf: current.appendingPathComponent($0)))
                }
                if sameResources && (try? Data(contentsOf: oldExecutable)) == (try? Data(contentsOf: newExecutable)) { return }
                let backup = installedApp.deletingLastPathComponent().appendingPathComponent(".Tube Switch.previous.app")
                if fm.fileExists(atPath: backup.path) { try fm.removeItem(at: backup) }
                try fm.moveItem(at: installedApp, to: backup)
                do { try fm.copyItem(at: current, to: installedApp) }
                catch { try? fm.moveItem(at: backup, to: installedApp); throw error }
            } else {
                try fm.copyItem(at: current, to: installedApp)
            }
        }
    }

    static func autostart(_ enabled: Bool) throws {
        if enabled {
            try writePlist([
                "Label": appID,
                "ProgramArguments": [installedApp.appendingPathComponent("Contents/MacOS/TubeSwitch").path],
                "RunAtLoad": true,
                "KeepAlive": false,
                "ProcessType": "Interactive"
            ], to: menuPlist)
        } else if FileManager.default.fileExists(atPath: menuPlist.path) {
            try FileManager.default.removeItem(at: menuPlist)
        }
        // LaunchAgents are discovered at the next login. The current menu app is already running.
    }

    static func prepare(force: Bool = false) throws {
        let fm = FileManager.default
        let resources = Bundle.main.resourceURL!
        let firstLaunch = !fm.fileExists(atPath: support.appendingPathComponent("menu-installed").path)
        try selfInstall()
        try fm.createDirectory(at: support, withIntermediateDirectories: true)
        if firstLaunch {
            try autostart(true)
            try Data("1".utf8).write(to: support.appendingPathComponent("menu-installed"), options: .atomic)
        }
        do { _ = try command("/usr/bin/xcode-select", ["-p"]) }
        catch { throw AppError(message: "Для этой версии нужны Apple Command Line Tools. Установите их командой xcode-select --install в Terminal, затем откройте Tube Switch снова.") }
        guard fm.fileExists(atPath: support.appendingPathComponent("previous-network.json").path) else {
            return // First network setup is a user-initiated action from the menu.
        }
        let destination = support.appendingPathComponent("agent.py")
        let newCode = try Data(contentsOf: resources.appendingPathComponent("agent.py"))
        let oldCode = try? Data(contentsOf: destination)
        let presetsDestination = support.appendingPathComponent("service-presets.json")
        let newPresets = try Data(contentsOf: resources.appendingPathComponent("service-presets.json"))
        let oldPresets = try? Data(contentsOf: presetsDestination)
        let backup = support.appendingPathComponent("agent-before-menu.py")
        if let oldCode = oldCode, !fm.fileExists(atPath: backup.path) {
            try oldCode.write(to: backup, options: .atomic)
        }
        let domain = "gui/\(getuid())"
        let plist = home.appendingPathComponent("Library/LaunchAgents/\(agentID).plist")
        // The menu application owns its worker. This avoids re-registering a
        // launchd job on every update, and the mode still survives login/reboot.
        let legacyBackup = support.appendingPathComponent("legacy-launch-agent.plist")
        if fm.fileExists(atPath: plist.path) {
            let data = try Data(contentsOf: plist)
            let config = try PropertyListSerialization.propertyList(from: data, format: nil) as? [String: Any]
            guard config?["Label"] as? String == agentID else {
                throw AppError(message: "Не удалось проверить прежний фоновый сервис.")
            }
            if !fm.fileExists(atPath: legacyBackup.path) { try data.write(to: legacyBackup, options: .atomic) }
            _ = try command("/bin/launchctl", ["bootout", domain + "/" + agentID], allowFailure: true)
            try fm.removeItem(at: plist)
        }
        if !force && oldCode == newCode && oldPresets == newPresets
            && backendProcess?.isRunning == true && API.ready() { return }
        stopBackend()
        try stopManagedOrphan()
        do {
            try newCode.write(to: destination, options: .atomic)
            try newPresets.write(to: presetsDestination, options: .atomic)
            let binary = support.appendingPathComponent("tpws")
            if !fm.fileExists(atPath: binary.path) {
                try fm.copyItem(at: resources.appendingPathComponent("tpws"), to: binary)
                try fm.setAttributes([.posixPermissions: 0o755], ofItemAtPath: binary.path)
            }
            try startBackend()
            for _ in 0..<20 {
                if API.ready() { return }
                Thread.sleep(forTimeInterval: 0.25)
            }
            throw AppError(message: "Не удалось запустить обновлённый сервис. Прежняя версия будет восстановлена.")
        } catch {
            stopBackend()
            if let oldCode = oldCode { try? oldCode.write(to: destination, options: .atomic) }
            if let oldPresets = oldPresets { try? oldPresets.write(to: presetsDestination, options: .atomic) }
            try? startBackend()
            throw error
        }
    }
}

final class MenuHeader: NSView {
    let title = NSTextField(labelWithString: "Tube Switch")
    let subtitle = NSTextField(labelWithString: "Локальный обход DPI")
    let state = NSTextField(labelWithString: "Подключение…")
    let dot = NSView(frame: NSRect(x: 18, y: 17, width: 7, height: 7))
    override init(frame: NSRect) {
        super.init(frame: frame)
        title.frame = NSRect(x: 18, y: 64, width: 230, height: 24)
        title.font = .systemFont(ofSize: 17, weight: .semibold)
        subtitle.frame = NSRect(x: 18, y: 44, width: 230, height: 18)
        subtitle.font = .systemFont(ofSize: 12)
        subtitle.textColor = .secondaryLabelColor
        state.frame = NSRect(x: 33, y: 11, width: 227, height: 20)
        state.font = .systemFont(ofSize: 12, weight: .medium)
        dot.wantsLayer = true
        dot.layer?.cornerRadius = 3.5
        for view in [title, subtitle, state, dot] { addSubview(view) }
    }
    required init?(coder: NSCoder) { fatalError() }
    func update(text: String, color: NSColor) {
        state.stringValue = text
        dot.layer?.backgroundColor = color.cgColor
    }
}

final class AppDelegate: NSObject, NSApplicationDelegate, NSMenuDelegate {
    var statusItem: NSStatusItem!
    let menu = NSMenu()
    let header = MenuHeader(frame: NSRect(x: 0, y: 0, width: 278, height: 100))
    var toggleItem: NSMenuItem!
    var loginItem: NSMenuItem!
    var busy = true
    var state: BackendState?
    var timer: Timer?
    var welcomeWindow: NSWindow?
    var welcomeOffered = false
    var serviceItems: [String: NSMenuItem] = [:]
    var welcomeKey: String {
        "welcomeShown-" + (Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? "1")
    }

    var configured: Bool {
        guard let proxies = SCDynamicStoreCopyProxies(nil) as? [String: Any] else { return false }
        return (proxies[kSCPropNetProxiesProxyAutoConfigEnable as String] as? NSNumber)?.boolValue == true
            && (proxies[kSCPropNetProxiesProxyAutoConfigURLString as String] as? String) == apiBase + "/proxy.pac"
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        if NSRunningApplication.runningApplications(withBundleIdentifier: appID)
            .contains(where: { $0.processIdentifier != ProcessInfo.processInfo.processIdentifier }) {
            NSApp.terminate(nil); return
        }
        NSApp.setActivationPolicy(.accessory)
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.squareLength)
        statusItem.autosaveName = "TubeSwitchStatus"
        statusItem.behavior = .removalAllowed
        if let button = statusItem.button {
            button.target = self
            button.action = #selector(clicked)
            button.sendAction(on: [.leftMouseUp, .rightMouseUp])
        }
        buildMenu()
        paint()
        DispatchQueue.global(qos: .userInitiated).async {
            do { try Install.prepare() }
            catch { DispatchQueue.main.async { self.showError(error) } }
            DispatchQueue.main.async {
                self.busy = false
                self.refresh()
                self.timer = Timer.scheduledTimer(withTimeInterval: 3, repeats: true) { _ in self.refresh() }
            }
        }
    }

    func item(_ title: String, _ action: Selector, symbol: String? = nil) -> NSMenuItem {
        let item = NSMenuItem(title: title, action: action, keyEquivalent: "")
        item.target = self
        if let symbol = symbol { item.image = NSImage(systemSymbolName: symbol, accessibilityDescription: nil) }
        return item
    }

    func buildMenu() {
        menu.delegate = self
        menu.autoenablesItems = false
        let heading = NSMenuItem()
        heading.view = header
        menu.addItem(heading)
        menu.addItem(.separator())
        toggleItem = item("Подключение…", #selector(toggle), symbol: "power")
        toggleItem.keyEquivalent = "y"
        toggleItem.keyEquivalentModifierMask = [.command, .option]
        menu.addItem(toggleItem)
        menu.addItem(item("Открыть YouTube", #selector(openYouTube), symbol: "play.rectangle"))
        menu.addItem(item("Открыть Discord", #selector(openDiscord), symbol: "bubble.left.and.bubble.right"))
        let servicesItem = NSMenuItem(title: "Сервисы", action: nil, keyEquivalent: "")
        let servicesMenu = NSMenu()
        servicesMenu.autoenablesItems = false
        for preset in Catalog.presets {
            let entry = item(preset.title, #selector(toggleService(_:)), symbol: preset.symbol)
            entry.representedObject = preset.id
            entry.toolTip = preset.note
            serviceItems[preset.id] = entry
            servicesMenu.addItem(entry)
        }
        servicesMenu.addItem(.separator())
        let voiceNote = NSMenuItem(title: "Discord: без голосовых каналов", action: nil, keyEquivalent: "")
        voiceNote.isEnabled = false
        servicesMenu.addItem(voiceNote)
        servicesItem.submenu = servicesMenu
        menu.addItem(servicesItem)
        menu.addItem(.separator())
        loginItem = item("Запускать при входе", #selector(toggleLogin))
        menu.addItem(loginItem)
        let extra = NSMenuItem(title: "Дополнительно", action: nil, keyEquivalent: "")
        let submenu = NSMenu()
        submenu.addItem(item("Перезапустить сервис", #selector(restart), symbol: "arrow.clockwise"))
        submenu.addItem(item("Вернуть прежние сетевые настройки…", #selector(restoreNetwork)))
        extra.submenu = submenu
        menu.addItem(extra)
        menu.addItem(item("О Tube Switch", #selector(about)))
        menu.addItem(.separator())
        menu.addItem(item("Скрыть значок", #selector(hideMenu)))
        let quit = item("Завершить Tube Switch", #selector(quitApp))
        quit.keyEquivalent = "q"
        menu.addItem(quit)
    }

    @objc func clicked(_ sender: NSStatusBarButton) {
        if NSApp.currentEvent?.modifierFlags.contains(.option) == true {
            toggle(); return
        }
        paint()
        sender.highlight(true)
        menu.popUp(positioning: nil, at: NSPoint(x: 0, y: sender.bounds.height + 4), in: sender)
        sender.highlight(false)
    }

    func menuWillOpen(_ menu: NSMenu) { refresh() }

    func paint() {
        let online = state?.running == true && state?.api == 2
        let enabled = state?.enabled == true
        let active = online && configured
        let title: String
        let color: NSColor
        let symbol: String
        if busy {
            title = "Переключение…"; color = .systemOrange; symbol = "ellipsis.circle"
        } else if active {
            let hasServices = !(state?.services?.isEmpty ?? false)
            title = enabled ? (hasServices ? "Обход включён" : "Сервисы не выбраны") : "Обход выключен"
            color = enabled && hasServices ? .systemGreen : .secondaryLabelColor
            symbol = enabled && hasServices ? "play.circle.fill" : "pause.circle"
        } else {
            title = configured ? "Сервис недоступен" : "Нужно подключить обход"
            color = .systemOrange; symbol = "exclamationmark.circle"
        }
        header.update(text: title, color: color)
        let image = NSImage(systemSymbolName: symbol, accessibilityDescription: title)
        image?.size = NSSize(width: 19, height: 19)
        image?.isTemplate = true
        statusItem?.button?.image = image
        statusItem?.button?.toolTip = "Tube Switch — \(title). ⌥-клик: переключить."
        statusItem?.button?.setAccessibilityLabel("Tube Switch: " + title)
        toggleItem?.title = active ? (enabled ? "Выключить обход" : "Включить обход")
                                  : (configured ? "Перезапустить сервис" : "Настроить подключение…")
        toggleItem?.isEnabled = !busy
        loginItem?.state = FileManager.default.fileExists(atPath: menuPlist.path) ? .on : .off
        let selected = Set(state?.services ?? Catalog.presets.filter(\.defaultEnabled).map(\.id))
        for (id, entry) in serviceItems {
            entry.state = selected.contains(id) ? .on : .off
            entry.isEnabled = active && !busy
        }
    }

    func refresh() {
        if busy { return }
        API.request { result in
            DispatchQueue.main.async {
                switch result {
                case .success(let state): self.state = state
                case .failure: self.state = nil
                }
                self.paint()
                if self.state?.running == true && self.state?.api == 2 && self.configured
                    && !self.welcomeOffered && !UserDefaults.standard.bool(forKey: self.welcomeKey) {
                    self.welcomeOffered = true
                    self.showWelcome()
                }
            }
        }
    }

    @objc func toggle() {
        if busy { return }
        if !configured { openSetup("Включить.command"); return }
        guard let state = state, state.running, state.api == 2 else { restart(); return }
        busy = true; paint()
        API.request(enabled: !(state.enabled ?? true)) { result in
            DispatchQueue.main.async {
                self.busy = false
                switch result {
                case .success(let state): self.state = state
                case .failure(let error): self.showError(error)
                }
                self.paint()
            }
        }
    }

    @objc func toggleService(_ sender: NSMenuItem) {
        guard !busy, let id = sender.representedObject as? String, let state = state,
              state.running, state.api == 2 else { return }
        var selected = Set(state.services ?? [])
        if selected.contains(id) { selected.remove(id) } else { selected.insert(id) }
        let values = Catalog.presets.map(\.id).filter { selected.contains($0) }
        busy = true; paint()
        API.request(services: values) { result in
            DispatchQueue.main.async {
                self.busy = false
                switch result {
                case .success(let state): self.state = state
                case .failure(let error): self.showError(error)
                }
                self.paint()
            }
        }
    }

    @objc func restart() {
        if busy { return }
        busy = true; paint()
        DispatchQueue.global(qos: .userInitiated).async {
            do { try Install.prepare(force: true) }
            catch { DispatchQueue.main.async { self.showError(error) } }
            DispatchQueue.main.async { self.busy = false; self.refresh() }
        }
    }

    func openSetup(_ filename: String) {
        if filename == "Отключить.command" { Install.stopBackend() }
        let installedResources = installedApp.appendingPathComponent("Contents/Resources")
        let base = FileManager.default.fileExists(atPath: installedResources.path)
            ? installedResources : Bundle.main.resourceURL!
        NSWorkspace.shared.open(base.appendingPathComponent(filename))
    }

    @objc func restoreNetwork() { openSetup("Отключить.command") }
    @objc func openYouTube() { NSWorkspace.shared.open(URL(string: "https://www.youtube.com")!) }
    @objc func openDiscord() { NSWorkspace.shared.open(URL(string: "https://discord.com/app")!) }
    @objc func toggleLogin() {
        do { try Install.autostart(!FileManager.default.fileExists(atPath: menuPlist.path)); paint() }
        catch { showError(error) }
    }
    @objc func hideMenu() { statusItem.isVisible = false }
    @objc func quitApp() { NSApp.terminate(nil) }
    func showWelcome() {
        UserDefaults.standard.set(true, forKey: welcomeKey)
        let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 400, height: 300),
                              styleMask: [.titled, .closable], backing: .buffered, defer: false)
        window.title = "Tube Switch"
        window.titlebarAppearsTransparent = true
        window.isReleasedWhenClosed = false
        let content = NSView(frame: NSRect(x: 0, y: 0, width: 400, height: 300))
        let icon = NSImageView(frame: NSRect(x: 28, y: 200, width: 70, height: 70))
        icon.image = NSImage(contentsOf: installedApp.appendingPathComponent("Contents/Resources/AppIcon.icns"))
        let name = NSTextField(labelWithString: "Tube Switch")
        name.font = .systemFont(ofSize: 21, weight: .semibold)
        name.frame = NSRect(x: 112, y: 237, width: 250, height: 28)
        let subtitle = NSTextField(labelWithString: "YouTube · Discord · и другие")
        subtitle.font = .systemFont(ofSize: 13)
        subtitle.textColor = .secondaryLabelColor
        subtitle.frame = NSRect(x: 112, y: 214, width: 250, height: 20)
        let title = NSTextField(labelWithString: "Всё готово")
        title.font = .systemFont(ofSize: 24, weight: .semibold)
        title.frame = NSRect(x: 32, y: 145, width: 336, height: 34)
        let body = NSTextField(wrappingLabelWithString: "Кнопка появилась в строке меню.\n⌥-клик по значку быстро меняет режим.")
        body.font = .systemFont(ofSize: 13)
        body.textColor = .secondaryLabelColor
        body.frame = NSRect(x: 32, y: 87, width: 336, height: 44)
        let show = NSButton(title: "Открыть меню", target: self, action: #selector(showStatusMenu))
        show.bezelStyle = .rounded
        show.keyEquivalent = "\r"
        show.frame = NSRect(x: 26, y: 25, width: 150, height: 32)
        let done = NSButton(title: "Понятно", target: self, action: #selector(closeWelcome))
        done.bezelStyle = .rounded
        done.frame = NSRect(x: 260, y: 25, width: 112, height: 32)
        for view in [icon, name, subtitle, title, body, show, done] { content.addSubview(view) }
        window.contentView = content
        welcomeWindow = window
        window.center()
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }
    @objc func showStatusMenu() {
        statusItem.isVisible = true
        if let button = statusItem.button { clicked(button) }
    }
    @objc func closeWelcome() { welcomeWindow?.close() }
    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows: Bool) -> Bool {
        statusItem.isVisible = true
        refresh()
        return false
    }
    func applicationWillTerminate(_ notification: Notification) { Install.stopBackend() }
    @objc func about() {
        let alert = NSAlert()
        alert.messageText = "Tube Switch"
        let version = Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? ""
        alert.informativeText = "Локальный обход DPI для выбранных сервисов.\n\nСервисы выбираются в одноимённом меню. Discord: веб-версия, чат и вложения; голосовые каналы через UDP не поддерживаются. Twitch и SoundCloud требуют проверки у вашего провайдера.\n\n⌥-клик по значку — быстрое переключение. В выключенном режиме локальный сервис передаёт трафик без обработки DPI.\n\nПри переключении может потребоваться обновить страницу.\n\nВерсия \(version) · tpws / zapret"
        alert.addButton(withTitle: "Понятно")
        NSApp.activate(ignoringOtherApps: true)
        alert.runModal()
    }
    func showError(_ error: Error) {
        let alert = NSAlert()
        alert.alertStyle = .warning
        alert.messageText = "Не удалось выполнить действие"
        alert.informativeText = error.localizedDescription
        alert.addButton(withTitle: "Понятно")
        NSApp.activate(ignoringOtherApps: true)
        alert.runModal()
    }
}

if CommandLine.arguments.contains("--check-bundle") {
    let resources = Bundle.main.resourceURL!
    for filename in ["agent.py", "service-presets.json", "tpws", "setup.py", "Включить.command", "Отключить.command", "LICENSE.txt"] {
        guard FileManager.default.fileExists(atPath: resources.appendingPathComponent(filename).path) else {
            fputs("Missing resource: \(filename)\n", stderr); exit(1)
        }
    }
    guard Catalog.presets.count >= 2 else { fputs("Invalid service catalog\n", stderr); exit(1) }
    print("Bundle resources: OK")
    exit(0)
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.run()
