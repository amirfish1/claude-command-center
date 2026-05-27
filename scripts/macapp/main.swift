// CCC — Claude Command Center native macOS shell.
//
// A thin WKWebView wrapper around the localhost dashboard served by
// server.py. The Python server is treated as a child process: started
// when needed, killed on ⌘Q. If a CCC server is already running (e.g.
// installed as a launchd agent), we don't double-start — we just point
// the WebView at it and leave it alone on quit.
//
// First launch (no ~/.ccc/claude-command-center on disk) opens Terminal
// with the bundled install.sh — same UX as the curl install, since we
// need user consent to clone into their home dir anyway.

import Cocoa
import WebKit

// MARK: - Constants

let CCC_PORT = 8090
let CCC_INSTALL_DIR = NSString(string: "~/.ccc/claude-command-center").expandingTildeInPath
let CCC_URL = URL(string: "http://localhost:\(CCC_PORT)")!
let CCC_BUNDLE_VERSION = (Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String) ?? "dev"

// MARK: - Helpers

func portIsBound(_ port: Int) -> Bool {
    // /dev/tcp is bash-only; use a raw socket via Process+nc to stay neutral.
    // nc ships in /usr/bin on every Mac.
    let task = Process()
    task.launchPath = "/usr/bin/nc"
    task.arguments = ["-z", "-w", "1", "127.0.0.1", "\(port)"]
    task.standardOutput = Pipe()
    task.standardError = Pipe()
    do {
        try task.run()
        task.waitUntilExit()
        return task.terminationStatus == 0
    } catch {
        return false
    }
}

func augmentedPath() -> String {
    // LaunchServices strips PATH to a system default on .app double-click.
    // Add the spots where claude / python3 / git typically live.
    let home = NSHomeDirectory()
    let extras = [
        "\(home)/.local/bin",
        "\(home)/.bun/bin",
        "/opt/homebrew/bin",
        "/opt/homebrew/sbin",
        "/usr/local/bin",
        "/usr/bin",
        "/bin",
    ]
    let current = ProcessInfo.processInfo.environment["PATH"] ?? ""
    return extras.joined(separator: ":") + ":" + current
}

func runAppleScript(_ source: String) {
    guard let script = NSAppleScript(source: source) else { return }
    var error: NSDictionary?
    script.executeAndReturnError(&error)
}

// MARK: - App Delegate

final class AppDelegate: NSObject, NSApplicationDelegate, WKNavigationDelegate, WKUIDelegate {
    var window: NSWindow!
    var webView: WKWebView!
    var loadingLabel: NSTextField!
    var serverProcess: Process?
    var pollTimer: Timer?

    func applicationDidFinishLaunching(_ notification: Notification) {
        buildMenuBar()
        buildWindow()
        bootstrap()
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        return true
    }

    func applicationWillTerminate(_ notification: Notification) {
        // Only kill the server if we started it. If it was already up
        // (launchd service, foreground ./run.sh elsewhere), leave it alone.
        if let proc = serverProcess, proc.isRunning {
            proc.terminate()
            // Give it 2 seconds to exit gracefully, then SIGKILL.
            let deadline = Date().addingTimeInterval(2.0)
            while proc.isRunning && Date() < deadline {
                Thread.sleep(forTimeInterval: 0.1)
            }
            if proc.isRunning {
                kill(proc.processIdentifier, SIGKILL)
            }
        }
    }

    // MARK: Menu bar

    func buildMenuBar() {
        let mainMenu = NSMenu()

        // App menu (label comes from CFBundleName — see Info.plist)
        let appMenuItem = NSMenuItem()
        let appMenu = NSMenu()
        appMenu.addItem(withTitle: "About Command Center",
                        action: #selector(showAbout),
                        keyEquivalent: "")
        appMenu.addItem(NSMenuItem.separator())
        appMenu.addItem(withTitle: "Check for Updates…",
                        action: #selector(checkForUpdates),
                        keyEquivalent: "")
        appMenu.addItem(NSMenuItem.separator())
        appMenu.addItem(withTitle: "Hide Command Center",
                        action: #selector(NSApplication.hide(_:)),
                        keyEquivalent: "h")
        let hideOthers = appMenu.addItem(withTitle: "Hide Others",
                                         action: #selector(NSApplication.hideOtherApplications(_:)),
                                         keyEquivalent: "h")
        hideOthers.keyEquivalentModifierMask = [.command, .option]
        appMenu.addItem(withTitle: "Show All",
                        action: #selector(NSApplication.unhideAllApplications(_:)),
                        keyEquivalent: "")
        appMenu.addItem(NSMenuItem.separator())
        appMenu.addItem(withTitle: "Quit Command Center",
                        action: #selector(NSApplication.terminate(_:)),
                        keyEquivalent: "q")
        appMenuItem.submenu = appMenu
        mainMenu.addItem(appMenuItem)

        // Edit menu — gives WKWebView the standard text-editing shortcuts
        // (⌘V paste, ⌘C copy, ⌘X cut, ⌘A select-all, ⌘Z undo, ⌘⇧Z redo).
        // Actions are dispatched through the responder chain so WKWebView
        // receives them automatically.
        let editMenuItem = NSMenuItem()
        let editMenu = NSMenu(title: "Edit")
        editMenu.addItem(withTitle: "Undo",
                         action: Selector(("undo:")),
                         keyEquivalent: "z")
        let redoItem = editMenu.addItem(withTitle: "Redo",
                                        action: Selector(("redo:")),
                                        keyEquivalent: "z")
        redoItem.keyEquivalentModifierMask = [.command, .shift]
        editMenu.addItem(NSMenuItem.separator())
        editMenu.addItem(withTitle: "Cut",
                         action: #selector(NSText.cut(_:)),
                         keyEquivalent: "x")
        editMenu.addItem(withTitle: "Copy",
                         action: #selector(NSText.copy(_:)),
                         keyEquivalent: "c")
        editMenu.addItem(withTitle: "Paste",
                         action: #selector(NSText.paste(_:)),
                         keyEquivalent: "v")
        editMenu.addItem(withTitle: "Select All",
                         action: #selector(NSResponder.selectAll(_:)),
                         keyEquivalent: "a")
        editMenuItem.submenu = editMenu
        mainMenu.addItem(editMenuItem)

        // View menu
        let viewMenuItem = NSMenuItem()
        let viewMenu = NSMenu(title: "View")
        viewMenu.addItem(withTitle: "Reload",
                         action: #selector(reload),
                         keyEquivalent: "r")
        let forceReload = viewMenu.addItem(withTitle: "Force Reload",
                                           action: #selector(forceReload),
                                           keyEquivalent: "r")
        forceReload.keyEquivalentModifierMask = [.command, .shift]
        viewMenu.addItem(NSMenuItem.separator())
        let zoomIn = viewMenu.addItem(withTitle: "Zoom In",
                                      action: #selector(zoomIn(_:)),
                                      keyEquivalent: "+")
        zoomIn.keyEquivalentModifierMask = [.command]
        let zoomOut = viewMenu.addItem(withTitle: "Zoom Out",
                                       action: #selector(zoomOut(_:)),
                                       keyEquivalent: "-")
        zoomOut.keyEquivalentModifierMask = [.command]
        let zoomReset = viewMenu.addItem(withTitle: "Actual Size",
                                         action: #selector(zoomReset(_:)),
                                         keyEquivalent: "0")
        zoomReset.keyEquivalentModifierMask = [.command]
        viewMenuItem.submenu = viewMenu
        mainMenu.addItem(viewMenuItem)

        // Window menu
        let windowMenuItem = NSMenuItem()
        let windowMenu = NSMenu(title: "Window")
        windowMenu.addItem(withTitle: "Minimize",
                           action: #selector(NSWindow.miniaturize(_:)),
                           keyEquivalent: "m")
        windowMenu.addItem(withTitle: "Zoom",
                           action: #selector(NSWindow.performZoom(_:)),
                           keyEquivalent: "")
        windowMenuItem.submenu = windowMenu
        mainMenu.addItem(windowMenuItem)

        NSApp.mainMenu = mainMenu
        NSApp.windowsMenu = windowMenu
    }

    @objc func checkForUpdates() {
        // Lightweight check: hit GitHub's latest-release API, compare tags.
        // If newer, prompt to open the release page. No auto-install yet —
        // for that we'd need Sparkle (planned for a later release).
        DispatchQueue.global().async {
            let urlStr = "https://api.github.com/repos/amirfish1/claude-command-center/releases/latest"
            guard let url = URL(string: urlStr),
                  let data = try? Data(contentsOf: url),
                  let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
                  let tag = json["tag_name"] as? String,
                  let pageURLStr = json["html_url"] as? String else {
                DispatchQueue.main.async {
                    let alert = NSAlert()
                    alert.messageText = "Couldn't check for updates"
                    alert.informativeText = "Try again later, or visit github.com/amirfish1/claude-command-center/releases manually."
                    alert.runModal()
                }
                return
            }
            let latest = tag.hasPrefix("v") ? String(tag.dropFirst()) : tag
            let current = CCC_BUNDLE_VERSION
            DispatchQueue.main.async {
                let alert = NSAlert()
                if latest.compare(current, options: .numeric) == .orderedDescending {
                    alert.messageText = "Update available"
                    alert.informativeText = "You have v\(current). Latest is v\(latest)."
                    alert.addButton(withTitle: "Open Release Page")
                    alert.addButton(withTitle: "Later")
                    if alert.runModal() == .alertFirstButtonReturn,
                       let pageURL = URL(string: pageURLStr) {
                        NSWorkspace.shared.open(pageURL)
                    }
                } else {
                    alert.messageText = "You're up to date"
                    alert.informativeText = "v\(current) is the latest release."
                    alert.runModal()
                }
            }
        }
    }

    @objc func showAbout() {
        let alert = NSAlert()
        alert.messageText = "Command Center for Claude, Codex, Antigravity"
        alert.informativeText = """
        One inbox for all your AI agents.

        v\(CCC_BUNDLE_VERSION)

        github.com/amirfish1/claude-command-center
        """
        alert.alertStyle = .informational
        alert.runModal()
    }

    @objc func reload() {
        webView.reload()
    }

    @objc func forceReload() {
        webView.reloadFromOrigin()
    }

    @objc func zoomIn(_ sender: Any?) {
        webView.pageZoom = min(webView.pageZoom + 0.1, 3.0)
    }

    @objc func zoomOut(_ sender: Any?) {
        webView.pageZoom = max(webView.pageZoom - 0.1, 0.5)
    }

    @objc func zoomReset(_ sender: Any?) {
        webView.pageZoom = 1.0
    }

    // MARK: Window

    func buildWindow() {
        window = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 1400, height: 900),
            styleMask: [.titled, .closable, .miniaturizable, .resizable, .fullSizeContentView],
            backing: .buffered,
            defer: false
        )
        window.title = "Command Center for Claude, Codex, Antigravity — v\(CCC_BUNDLE_VERSION)"
        window.minSize = NSSize(width: 900, height: 600)
        window.center()
        window.setFrameAutosaveName("CCCMainWindow")
        window.titlebarAppearsTransparent = false

        let config = WKWebViewConfiguration()
        config.preferences.javaScriptCanOpenWindowsAutomatically = false
        config.websiteDataStore = .default()
        if #available(macOS 11.0, *) {
            config.defaultWebpagePreferences.allowsContentJavaScript = true
        }

        webView = WKWebView(frame: window.contentView!.bounds, configuration: config)
        webView.autoresizingMask = [.width, .height]
        webView.navigationDelegate = self
        webView.uiDelegate = self
        webView.setValue(false, forKey: "drawsBackground")
        window.contentView!.addSubview(webView)

        // Loading overlay
        loadingLabel = NSTextField(labelWithString: "Starting CCC server…")
        loadingLabel.font = NSFont.systemFont(ofSize: 14, weight: .medium)
        loadingLabel.textColor = .secondaryLabelColor
        loadingLabel.alignment = .center
        loadingLabel.translatesAutoresizingMaskIntoConstraints = false
        window.contentView!.addSubview(loadingLabel)
        NSLayoutConstraint.activate([
            loadingLabel.centerXAnchor.constraint(equalTo: window.contentView!.centerXAnchor),
            loadingLabel.centerYAnchor.constraint(equalTo: window.contentView!.centerYAnchor),
        ])

        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    // MARK: Bootstrap

    func bootstrap() {
        if !FileManager.default.fileExists(atPath: CCC_INSTALL_DIR) {
            // First-time install. Pop Terminal with bundled install.sh.
            runInstaller()
            return
        }

        if portIsBound(CCC_PORT) {
            // Someone else (launchd, foreground ./run.sh) is already serving.
            loadDashboard()
        } else {
            spawnServer()
        }
    }

    func runInstaller() {
        guard let installScript = Bundle.main.path(forResource: "install", ofType: "sh") else {
            showFatal("Install script missing", "The .app bundle is incomplete. Re-download from github.com/amirfish1/claude-command-center/releases")
            return
        }
        loadingLabel.stringValue = "First-time install — see the Terminal window…"

        // Copy to a temp location so Terminal can read it without bundle-path drama
        let tmpPath = NSTemporaryDirectory() + "ccc-install-\(getpid()).sh"
        do {
            if FileManager.default.fileExists(atPath: tmpPath) {
                try FileManager.default.removeItem(atPath: tmpPath)
            }
            try FileManager.default.copyItem(atPath: installScript, toPath: tmpPath)
            try FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: tmpPath)
        } catch {
            showFatal("Install setup failed", "\(error)")
            return
        }

        let script = """
        tell application "Terminal"
            activate
            do script "clear; echo '→ Claude Command Center first-time install'; echo; CCC_FROM=dmg bash '\(tmpPath)'; echo; echo '(Once you see CCC running, you can close this Terminal — the CCC window stays.)'"
        end tell
        """
        runAppleScript(script)
        // Poll for the install to finish + the port to bind, then load.
        pollUntilReady()
    }

    func spawnServer() {
        let runSh = "\(CCC_INSTALL_DIR)/run.sh"
        guard FileManager.default.fileExists(atPath: runSh) else {
            // Install dir exists but run.sh missing — corrupt checkout. Reinstall.
            runInstaller()
            return
        }

        loadingLabel.stringValue = "Starting CCC server…"

        let proc = Process()
        proc.launchPath = "/bin/bash"
        proc.arguments = [runSh]
        proc.currentDirectoryPath = CCC_INSTALL_DIR

        var env = ProcessInfo.processInfo.environment
        env["PATH"] = augmentedPath()
        env["PORT"] = "\(CCC_PORT)"
        env["CCC_FROM"] = "dmg"
        proc.environment = env

        // Drain output to the log file CCC's launchd service uses, so we
        // share log location with the service path.
        let logDir = NSString(string: "~/.claude/command-center/logs").expandingTildeInPath
        try? FileManager.default.createDirectory(atPath: logDir, withIntermediateDirectories: true)
        let logPath = "\(logDir)/app-server.log"
        FileManager.default.createFile(atPath: logPath, contents: nil)
        if let logHandle = FileHandle(forWritingAtPath: logPath) {
            logHandle.seekToEndOfFile()
            proc.standardOutput = logHandle
            proc.standardError = logHandle
        }

        do {
            try proc.run()
            serverProcess = proc
        } catch {
            showFatal("Server failed to start", "\(error)\n\nCheck \(logPath) for details.")
            return
        }

        pollUntilReady()
    }

    func pollUntilReady() {
        let start = Date()
        let timeout: TimeInterval = 60
        pollTimer = Timer.scheduledTimer(withTimeInterval: 0.5, repeats: true) { [weak self] timer in
            guard let self = self else { timer.invalidate(); return }
            if portIsBound(CCC_PORT) {
                timer.invalidate()
                self.pollTimer = nil
                self.loadDashboard()
                return
            }
            if Date().timeIntervalSince(start) > timeout {
                timer.invalidate()
                self.pollTimer = nil
                self.showFatal("Server didn't start in \(Int(timeout))s",
                               "Port \(CCC_PORT) never bound. Check ~/.claude/command-center/logs/app-server.log")
            }
        }
    }

    func loadDashboard() {
        loadingLabel.isHidden = true
        webView.load(URLRequest(url: CCC_URL))
    }

    func showFatal(_ title: String, _ message: String) {
        let alert = NSAlert()
        alert.messageText = title
        alert.informativeText = message
        alert.alertStyle = .critical
        alert.addButton(withTitle: "Quit")
        alert.runModal()
        NSApp.terminate(nil)
    }

    // MARK: WKNavigationDelegate

    // Route any navigation outside the local CCC dashboard to the system
    // browser. Without this, an `<a href="https://...">` inside assistant
    // text would replace the dashboard with that page; `target="_blank"`
    // links would silently no-op because WKWebView has no concept of
    // opening a new window unless the UI delegate handles it.
    func webView(_ webView: WKWebView,
                 decidePolicyFor navigationAction: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        guard let url = navigationAction.request.url else {
            decisionHandler(.allow)
            return
        }
        if isLocalDashboardURL(url) {
            decisionHandler(.allow)
        } else {
            NSWorkspace.shared.open(url)
            decisionHandler(.cancel)
        }
    }

    func isLocalDashboardURL(_ url: URL) -> Bool {
        let scheme = (url.scheme ?? "").lowercased()
        if scheme == "about" || scheme == "data" || scheme == "blob" { return true }
        if scheme != "http" && scheme != "https" { return false }
        let host = (url.host ?? "").lowercased()
        return host == "localhost" || host == "127.0.0.1" || host == "0.0.0.0"
    }

    // MARK: WKUIDelegate

    // Fired for `target="_blank"` and `window.open(...)`. Returning nil tells
    // WKWebView "I handled it; don't create a child view." We open the URL
    // in the user's default browser instead.
    func webView(_ webView: WKWebView,
                 createWebViewWith configuration: WKWebViewConfiguration,
                 for navigationAction: WKNavigationAction,
                 windowFeatures: WKWindowFeatures) -> WKWebView? {
        if let url = navigationAction.request.url {
            NSWorkspace.shared.open(url)
        }
        return nil
    }

    func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
        loadingLabel.isHidden = false
        loadingLabel.stringValue = "Lost the server. Reconnecting…"
        // Retry after a beat
        DispatchQueue.main.asyncAfter(deadline: .now() + 1.0) { [weak self] in
            self?.bootstrap()
        }
    }

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        loadingLabel.isHidden = true
    }
}

// MARK: - Main

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.regular)
app.run()
