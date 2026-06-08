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
import Sparkle

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

func isLocalDashboardURL(_ url: URL) -> Bool {
    let scheme = (url.scheme ?? "").lowercased()
    if scheme == "about" || scheme == "data" || scheme == "blob" { return true }
    if scheme != "http" && scheme != "https" { return false }
    let host = (url.host ?? "").lowercased()
    let isLocalHost = host == "localhost" || host == "127.0.0.1" || host == "0.0.0.0"
    if !isLocalHost { return false }
    // Only OUR dashboard port is the in-app dashboard. Other localhost ports
    // (e.g. the Next.js dev server the "localhost" pill links to) are external
    // sites — they must open in the browser, not spawn a duplicate in-app
    // window (CCC-39). Default ports (no explicit :port) are never the CCC
    // dashboard, which always runs on CCC_PORT.
    let port = url.port ?? (scheme == "https" ? 443 : 80)
    return port == CCC_PORT
}

func isConversationPopoutURL(_ url: URL) -> Bool {
    guard isLocalDashboardURL(url),
          let comp = URLComponents(url: url, resolvingAgainstBaseURL: false) else {
        return false
    }
    let items = comp.queryItems ?? []
    return items.contains(where: { $0.name == "ccc_popout" && $0.value == "conversation" })
        || items.contains(where: { $0.name == "popout" && $0.value == "conversation" })
}

func stampMacAppFlag(on webView: WKWebView) {
    webView.evaluateJavaScript("window.__CCC_MAC_APP__ = true;", completionHandler: nil)
}

func injectMacAppFlags(into config: WKWebViewConfiguration) {
    let script = WKUserScript(
        source: "window.__CCC_MAC_APP__ = true;",
        injectionTime: .atDocumentStart,
        forMainFrameOnly: true
    )
    config.userContentController.addUserScript(script)
}

// MARK: - Native bridge (JS → open in-app pop-out windows)

final class CCCNativeBridge: NSObject, WKScriptMessageHandler {
    weak var appDelegate: AppDelegate?

    func userContentController(_ userContentController: WKUserContentController,
                               didReceive message: WKScriptMessage) {
        guard message.name == "cccNative",
              let body = message.body as? [String: Any],
              let action = body["action"] as? String,
              action == "openPopout",
              let urlStr = body["url"] as? String,
              let url = URL(string: urlStr),
              isLocalDashboardURL(url) else { return }
        DispatchQueue.main.async { [weak self] in
            self?.appDelegate?.openConversationPopoutWindow(url: url)
        }
    }
}

// MARK: - Dashboard web window (main shell + conversation pop-outs)

final class CCCWebWindow: NSObject, WKNavigationDelegate, WKUIDelegate, NSWindowDelegate {
    let window: NSWindow
    let webView: WKWebView
    let loadingLabel: NSTextField?
    private weak var appDelegate: AppDelegate?
    private let isMain: Bool

    static func createMain(appDelegate: AppDelegate) -> CCCWebWindow {
        CCCWebWindow(appDelegate: appDelegate, isMain: true, url: nil,
                     configuration: nil, features: nil)
    }

    static func popoutTitle(from url: URL?) -> String {
        guard let url = url,
              let comp = URLComponents(url: url, resolvingAgainstBaseURL: false) else {
            return "Conversation"
        }
        let items = comp.queryItems ?? []
        if let title = items.first(where: { $0.name == "title" })?.value,
           !title.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            return title
        }
        if let conv = items.first(where: { $0.name == "conv" })?.value, !conv.isEmpty {
            return String(conv.prefix(8))
        }
        return "Conversation"
    }

    init(appDelegate: AppDelegate,
         isMain: Bool,
         url: URL?,
         configuration: WKWebViewConfiguration?,
         features: WKWindowFeatures?) {
        self.appDelegate = appDelegate
        self.isMain = isMain

        let width: CGFloat
        let height: CGFloat
        if let w = features?.width?.doubleValue,
           let h = features?.height?.doubleValue, w > 0, h > 0 {
            width = CGFloat(w)
            height = CGFloat(h)
        } else if isMain {
            width = 1400
            height = 900
        } else {
            width = 920
            height = 900
        }

        let contentRect = NSRect(x: 0, y: 0, width: width, height: height)
        let win = NSWindow(
            contentRect: contentRect,
            styleMask: [.titled, .closable, .miniaturizable, .resizable, .fullSizeContentView],
            backing: .buffered,
            defer: false
        )
        if isMain {
            win.title = "Command Center for Claude, Codex, Antigravity — v\(CCC_BUNDLE_VERSION)"
            win.minSize = NSSize(width: 900, height: 600)
            win.setFrameAutosaveName("CCCMainWindow")
            win.titlebarAppearsTransparent = false
            win.center()
        } else {
            win.title = CCCWebWindow.popoutTitle(from: url)
            win.minSize = NSSize(width: 600, height: 400)
            if let x = features?.x?.doubleValue, let y = features?.y?.doubleValue {
                win.setFrameOrigin(NSPoint(x: x, y: y))
            } else {
                win.center()
            }
        }
        window = win

        let config = configuration ?? WKWebViewConfiguration()
        if configuration == nil {
            config.preferences.javaScriptCanOpenWindowsAutomatically = true
            config.websiteDataStore = .default()
            if #available(macOS 11.0, *) {
                config.defaultWebpagePreferences.allowsContentJavaScript = true
            }
            config.applicationNameForUserAgent = " CCC-macOS"
        }
        injectMacAppFlags(into: config)
        appDelegate.registerNativeBridge(on: config)

        let view = WKWebView(frame: win.contentView!.bounds, configuration: config)
        view.autoresizingMask = [.width, .height]
        view.setValue(true, forKey: "drawsBackground")
        webView = view

        if isMain {
            let label = NSTextField(labelWithString: "Starting CCC server…")
            label.font = NSFont.systemFont(ofSize: 14, weight: .medium)
            label.textColor = .secondaryLabelColor
            label.alignment = .center
            label.translatesAutoresizingMaskIntoConstraints = false
            loadingLabel = label
            win.contentView!.addSubview(view)
            win.contentView!.addSubview(label)
            NSLayoutConstraint.activate([
                label.centerXAnchor.constraint(equalTo: win.contentView!.centerXAnchor),
                label.centerYAnchor.constraint(equalTo: win.contentView!.centerYAnchor),
            ])
        } else {
            loadingLabel = nil
            win.contentView!.addSubview(view)
            if let url = url {
                view.load(URLRequest(url: url))
            }
            win.makeKeyAndOrderFront(nil)
            NSApp.activate(ignoringOtherApps: true)
        }

        super.init()

        window.delegate = self
        webView.navigationDelegate = self
        webView.uiDelegate = self

        if !isMain {
            appDelegate.trackPopout(self)
        }
    }

    func windowWillClose(_ notification: Notification) {
        appDelegate?.untrackPopout(self)
    }

    // MARK: WKNavigationDelegate

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

    func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
        guard isMain else { return }
        appDelegate?.onMainWebViewDidFail()
    }

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        loadingLabel?.isHidden = true
        stampMacAppFlag(on: webView)
    }

    // MARK: WKUIDelegate

    func webView(_ webView: WKWebView,
                 createWebViewWith configuration: WKWebViewConfiguration,
                 for navigationAction: WKNavigationAction,
                 windowFeatures: WKWindowFeatures) -> WKWebView? {
        guard let url = navigationAction.request.url else { return nil }
        if isLocalDashboardURL(url) {
            let popout = CCCWebWindow(appDelegate: appDelegate!, isMain: false,
                                      url: url, configuration: configuration,
                                      features: windowFeatures)
            return popout.webView
        }
        NSWorkspace.shared.open(url)
        return nil
    }

    func webView(_ webView: WKWebView,
                 runJavaScriptAlertPanelWithMessage message: String,
                 initiatedByFrame frame: WKFrameInfo,
                 completionHandler: @escaping () -> Void) {
        let alert = NSAlert()
        alert.messageText = message
        alert.alertStyle = .informational
        alert.addButton(withTitle: "OK")
        alert.runModal()
        completionHandler()
    }

    func webView(_ webView: WKWebView,
                 runJavaScriptConfirmPanelWithMessage message: String,
                 initiatedByFrame frame: WKFrameInfo,
                 completionHandler: @escaping (Bool) -> Void) {
        let alert = NSAlert()
        alert.messageText = message
        alert.alertStyle = .informational
        alert.addButton(withTitle: "OK")
        alert.addButton(withTitle: "Cancel")
        completionHandler(alert.runModal() == .alertFirstButtonReturn)
    }

    func webView(_ webView: WKWebView,
                 runJavaScriptTextInputPanelWithPrompt prompt: String,
                 defaultText: String?,
                 initiatedByFrame frame: WKFrameInfo,
                 completionHandler: @escaping (String?) -> Void) {
        let alert = NSAlert()
        alert.messageText = prompt
        alert.alertStyle = .informational
        alert.addButton(withTitle: "OK")
        alert.addButton(withTitle: "Cancel")
        let input = NSTextField(frame: NSRect(x: 0, y: 0, width: 280, height: 24))
        input.stringValue = defaultText ?? ""
        alert.accessoryView = input
        alert.window.initialFirstResponder = input
        let response = alert.runModal()
        completionHandler(response == .alertFirstButtonReturn ? input.stringValue : nil)
    }
}

// MARK: - App Delegate

final class AppDelegate: NSObject, NSApplicationDelegate {
    var mainWebWindow: CCCWebWindow!
    var window: NSWindow! { mainWebWindow.window }
    var webView: WKWebView! { mainWebWindow.webView }
    var loadingLabel: NSTextField! { mainWebWindow.loadingLabel! }
    private var popoutWindows: [CCCWebWindow] = []
    private var nativeBridge: CCCNativeBridge?
    private var bridgedContentControllers = Set<ObjectIdentifier>()
    var serverProcess: Process?
    var pollTimer: Timer?
    // Watchdog state — see startWatchdog(). Recovers a dashboard that wedges
    // with the loading overlay up forever (stalled server thread, or a hung
    // app.js request so the page's own 30s safety nets never register).
    var watchdogTimer: Timer?
    var watchdogStuckSince: Date?
    var watchdogReloaded = false
    var watchdogRestarted = false
    var watchdogRestartCount = 0          // hard cap — never reset, prevents loops
    let watchdogGrace: TimeInterval = 18  // seconds overlay may stay up before we act
    // Sparkle drives "Check for Updates…" via the appcast at SUFeedURL in
    // Info.plist. Public EdDSA key (SUPublicEDKey) verifies the DMG signature.
    // startingUpdater: true means Sparkle will run its scheduled background
    // check (interval and "automatically check" flag are controlled by the
    // user via the standard Sparkle update prompt the first time it runs).
    var updaterController: SPUStandardUpdaterController!

    func applicationDidFinishLaunching(_ notification: Notification) {
        updaterController = SPUStandardUpdaterController(
            startingUpdater: true,
            updaterDelegate: nil,
            userDriverDelegate: nil
        )
        buildMenuBar()
        buildWindow()
        bootstrap()
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        // Standard macOS behavior for full GUI apps (Safari, Mail, etc.):
        // closing the last window does NOT quit. Otherwise closing a
        // conversation pop-out — or even just the main window for a
        // moment — terminates the whole app and kills any server we
        // spawned. Cmd+Q is the explicit quit path; dock-clicks
        // (applicationShouldHandleReopen below) bring main back.
        return false
    }

    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        // User clicked the dock icon. If no windows are visible (main was
        // closed earlier), re-show main. If a popout is still up but main
        // is hidden, also surface main so the click feels right.
        if !flag {
            if let main = mainWebWindow?.window {
                main.makeKeyAndOrderFront(nil)
                NSApp.activate(ignoringOtherApps: true)
            }
        }
        return true
    }

    func application(_ application: NSApplication, open urls: [URL]) {
        for url in urls {
            if isConversationPopoutURL(url) {
                openConversationPopoutWindow(url: url)
            } else if isLocalDashboardURL(url) {
                mainWebWindow?.webView.load(URLRequest(url: url))
                window.makeKeyAndOrderFront(nil)
                NSApp.activate(ignoringOtherApps: true)
            }
        }
    }

    func applicationWillTerminate(_ notification: Notification) {
        watchdogTimer?.invalidate()
        pollTimer?.invalidate()
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
        // Sparkle's standard updater controller handles validation of the
        // -checkForUpdates: selector — when it's wired to updaterController
        // as the target, the menu item auto-disables while a check is in
        // flight. No keyEquivalent: macOS HIG says updates aren't a hotkey.
        let updatesItem = NSMenuItem(
            title: "Check for Updates…",
            action: #selector(SPUStandardUpdaterController.checkForUpdates(_:)),
            keyEquivalent: ""
        )
        updatesItem.target = updaterController
        appMenu.addItem(updatesItem)
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
        editMenu.addItem(NSMenuItem.separator())
        editMenu.addItem(withTitle: "Find…",
                         action: #selector(focusFind),
                         keyEquivalent: "f")
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
        let backItem = viewMenu.addItem(withTitle: "Back",
                                        action: #selector(goBack),
                                        keyEquivalent: "[")
        backItem.keyEquivalentModifierMask = [.command]
        let forwardItem = viewMenu.addItem(withTitle: "Forward",
                                           action: #selector(goForward),
                                           keyEquivalent: "]")
        forwardItem.keyEquivalentModifierMask = [.command]
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
        windowMenu.addItem(withTitle: "Close Window",
                           action: #selector(NSWindow.performClose(_:)),
                           keyEquivalent: "w")
        windowMenu.addItem(NSMenuItem.separator())
        // Cycle through CCC's own windows. macOS' default Cmd+` works
        // for AppKit apps with multiple windows, but WKWebView often
        // eats the keystroke before AppKit sees it — surface an explicit
        // menu item so the shortcut is bound at the menu-bar level.
        let cycleForward = windowMenu.addItem(
            withTitle: "Cycle Through Windows",
            action: #selector(cycleWindowsForward),
            keyEquivalent: "`"
        )
        cycleForward.keyEquivalentModifierMask = [.command]
        let cycleReverse = windowMenu.addItem(
            withTitle: "Cycle Through Windows (Reverse)",
            action: #selector(cycleWindowsReverse),
            keyEquivalent: "`"
        )
        cycleReverse.keyEquivalentModifierMask = [.command, .shift]
        windowMenuItem.submenu = windowMenu
        mainMenu.addItem(windowMenuItem)

        NSApp.mainMenu = mainMenu
        NSApp.windowsMenu = windowMenu
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

    func activeWebView() -> WKWebView {
        if let key = NSApp.keyWindow {
            if key === mainWebWindow?.window { return webView }
            if let match = popoutWindows.first(where: { $0.window === key }) {
                return match.webView
            }
        }
        return webView
    }

    func registerNativeBridge(on config: WKWebViewConfiguration) {
        let controller = config.userContentController
        let key = ObjectIdentifier(controller)
        guard !bridgedContentControllers.contains(key) else { return }
        if nativeBridge == nil {
            let bridge = CCCNativeBridge()
            bridge.appDelegate = self
            nativeBridge = bridge
        }
        guard let bridge = nativeBridge else { return }
        controller.add(bridge, name: "cccNative")
        bridgedContentControllers.insert(key)
    }

    func trackPopout(_ win: CCCWebWindow) {
        popoutWindows.append(win)
    }

    func untrackPopout(_ win: CCCWebWindow) {
        popoutWindows.removeAll { $0 === win }
    }

    func openConversationPopoutWindow(url: URL) {
        _ = CCCWebWindow(appDelegate: self, isMain: false, url: url,
                         configuration: nil, features: nil)
    }

    func onMainWebViewDidFail() {
        loadingLabel.isHidden = false
        loadingLabel.stringValue = "Lost the server. Reconnecting…"
        DispatchQueue.main.asyncAfter(deadline: .now() + 1.0) { [weak self] in
            self?.bootstrap()
        }
    }

    @objc func reload() {
        activeWebView().reload()
    }

    @objc func forceReload() {
        activeWebView().reloadFromOrigin()
    }

    @objc func zoomIn(_ sender: Any?) {
        let view = activeWebView()
        view.pageZoom = min(view.pageZoom + 0.1, 3.0)
    }

    @objc func zoomOut(_ sender: Any?) {
        let view = activeWebView()
        view.pageZoom = max(view.pageZoom - 0.1, 0.5)
    }

    @objc func zoomReset(_ sender: Any?) {
        activeWebView().pageZoom = 1.0
    }

    @objc func goBack() {
        let view = activeWebView()
        if view.canGoBack { view.goBack() }
    }

    @objc func goForward() {
        let view = activeWebView()
        if view.canGoForward { view.goForward() }
    }

    private func cycleableWindows() -> [NSWindow] {
        return NSApp.windows.filter { win in
            win.isVisible && win.canBecomeKey && !win.isMiniaturized && win.styleMask.contains(.titled)
        }
    }

    @objc func cycleWindowsForward() {
        let windows = cycleableWindows()
        guard windows.count > 1 else { return }
        let current = NSApp.keyWindow
        let pos = current.flatMap { windows.firstIndex(of: $0) } ?? -1
        let next = windows[(pos + 1) % windows.count]
        next.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    @objc func cycleWindowsReverse() {
        let windows = cycleableWindows()
        guard windows.count > 1 else { return }
        let current = NSApp.keyWindow
        let pos = current.flatMap { windows.firstIndex(of: $0) } ?? 0
        let next = windows[(pos - 1 + windows.count) % windows.count]
        next.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    @objc func focusFind() {
        // ⌘F: focus the dashboard's conversation search input. Falls back to
        // ⌘K command palette if the dedicated search isn't on the page yet.
        let js = """
        (function(){
          var el = document.getElementById('convSearch')
               || document.querySelector('.conv-search-input')
               || document.getElementById('cmdkInput');
          if (el) { el.focus(); el.select(); return true; }
          return false;
        })();
        """
        activeWebView().evaluateJavaScript(js, completionHandler: nil)
    }

    // MARK: Window

    func buildWindow() {
        mainWebWindow = CCCWebWindow.createMain(appDelegate: self)
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
        startWatchdog()
    }

    // MARK: Watchdog — recover a stuck dashboard
    //
    // The dashboard can wedge with the loading overlay up forever: a server
    // handler thread stalls mid-response (we've watched server.py burn CPU and
    // stop servicing a request), or the app.js request itself hangs so none of
    // the page's own 30s safety nets ever register. WKWebView's didFinish fires
    // when the HTML lands, so navigation state alone can't tell us the page is
    // stuck. Instead we poll the live DOM: if #cccLoadingOverlay is still
    // visible past watchdogGrace, escalate — reload the webview first (cheap,
    // clears a client-side wedge and re-fetches app.js), then restart the
    // server if a reload didn't help (clears a wedged handler thread).
    func startWatchdog() {
        watchdogStuckSince = Date()
        watchdogReloaded = false
        watchdogRestarted = false
        watchdogTimer?.invalidate()
        watchdogTimer = Timer.scheduledTimer(withTimeInterval: 4.0, repeats: true) { [weak self] _ in
            self?.watchdogTick()
        }
    }

    func stopWatchdog() {
        watchdogTimer?.invalidate()
        watchdogTimer = nil
        watchdogStuckSince = nil
    }

    func watchdogTick() {
        // 'ready' once the overlay is gone (app.js ran and rendered a response);
        // 'loading' while it's still up — including when app.js never loaded, in
        // which case the inline overlay is present and un-'gone'.
        let js = "(function(){var o=document.getElementById('cccLoadingOverlay');"
               + "if(!o)return 'ready';"
               + "if(o.classList.contains('gone'))return 'ready';"
               + "return 'loading';})()"
        webView.evaluateJavaScript(js) { [weak self] result, _ in
            guard let self = self else { return }
            let state = (result as? String) ?? "loading"
            if state == "ready" {
                self.stopWatchdog()   // dashboard is up; nothing left to guard
                return
            }
            guard let since = self.watchdogStuckSince else { return }
            let stuck = Date().timeIntervalSince(since)
            guard stuck >= self.watchdogGrace else { return }

            // Stage 2: reload didn't clear it → the server is wedged. Restart it.
            if self.watchdogReloaded && !self.watchdogRestarted {
                guard self.watchdogRestartCount < 2 else {
                    self.stopWatchdog()
                    self.loadingLabel.isHidden = false
                    self.loadingLabel.stringValue =
                        "Server keeps stalling — check ~/.claude/command-center/logs/app-server.log"
                    return
                }
                self.watchdogRestarted = true
                self.watchdogRestartCount += 1
                self.restartServerThenReload()
                return
            }

            // Stage 1: stuck past the grace window → reload the webview once.
            if !self.watchdogReloaded {
                self.watchdogReloaded = true
                self.watchdogStuckSince = Date()   // give the reload its own window
                self.webView.reload()
            }
        }
    }

    // POST /api/restart (server replaces itself via execvp — works regardless of
    // who launched it), wait for the new process to bind, then reload + re-arm
    // the watchdog so a still-broken server escalates again up to the cap.
    func restartServerThenReload() {
        loadingLabel.isHidden = false
        loadingLabel.stringValue = "Server stuck — restarting…"
        guard let url = URL(string: "http://127.0.0.1:\(CCC_PORT)/api/restart") else { return }
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.timeoutInterval = 10
        URLSession.shared.dataTask(with: req) { [weak self] _, _, _ in
            // The socket can drop mid-execvp — that's expected, not an error.
            DispatchQueue.main.asyncAfter(deadline: .now() + 2.0) {
                guard let self = self else { return }
                self.loadingLabel.isHidden = true
                self.webView.reload()
                self.startWatchdog()
            }
        }.resume()
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

}

// MARK: - Main

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.regular)
app.run()
