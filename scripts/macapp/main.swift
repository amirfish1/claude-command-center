// CCC — Claude Command Center native macOS shell.
//
// A thin WKWebView wrapper around the localhost dashboard served by
// server.py. The Python server is treated as a child process: started
// when needed, killed on ⌘Q. If a CCC server is already running (e.g.
// installed as a launchd agent), we don't double-start — we just point
// the WebView at it and leave it alone on quit.
//
// First launch (no ~/.ccc/claude-command-center on disk) runs the bundled
// install.sh as an owned child process. The app observes installation and
// server startup directly, with no Terminal automation or extra permission.
//
// CCC_URL is configurable (FEAT-NEXT-10): set the CCC_REMOTE_URL env var, or
// use the App menu's "Set Remote Server…" item, to point this shell at a CCC
// already running elsewhere (e.g. a tailnet host). When the resolved target
// is not localhost, the app never installs or spawns a local server — it's a
// thin client against the remote instance. Default (nothing set) is unchanged:
// http://localhost:8090, with the usual local install/spawn.

import Cocoa
import WebKit
import Sparkle

// MARK: - Constants

let CCC_ENV = ProcessInfo.processInfo.environment
let CCC_PORT = Int(CCC_ENV["CCC_PORT"] ?? "") ?? 8090
let CCC_INSTALL_DIR = CCC_ENV["CCC_INSTALL_DIR"]
    ?? NSString(string: "~/.ccc/claude-command-center").expandingTildeInPath
let CCC_LOG_DIR = CCC_ENV["CCC_LOG_DIR"]
    ?? NSString(string: "~/.claude/command-center/logs").expandingTildeInPath
let CCC_LOG_PATH = "\(CCC_LOG_DIR)/app-server.log"
// Optional local "Car Mode (Voice)" launcher. The public app ships no voice helper;
// if the user has wired one (see ccc-voice), they drop an executable .command here and
// the menu item appears. Graceful absence, like the Morning view plugin.
let CCC_CAR_MODE_CMD = NSString(string: "~/.ccc/car-mode.command").expandingTildeInPath
// FEAT-NEXT-10: let the native shell target a CCC already running elsewhere
// (e.g. on a tailnet host) instead of always spawning one locally. Priority:
// CCC_REMOTE_URL env var (automation/tests) > the "Set Remote Server…" menu
// item's UserDefaults value > the local default. Both overrides are unset
// out of the box, so an existing user sees no behavior change.
let CCC_REMOTE_URL_DEFAULTS_KEY = "CCCRemoteServerURL"

func resolveCCCTargetURL() -> URL {
    if let envValue = CCC_ENV["CCC_REMOTE_URL"], !envValue.isEmpty, let url = URL(string: envValue) {
        return url
    }
    if let stored = UserDefaults.standard.string(forKey: CCC_REMOTE_URL_DEFAULTS_KEY),
       !stored.isEmpty, let url = URL(string: stored) {
        return url
    }
    return URL(string: "http://localhost:\(CCC_PORT)")!
}

let CCC_URL = resolveCCCTargetURL()
// True when CCC_URL points somewhere other than this Mac — thin-client mode:
// bootstrap() must never install/spawn a local server in that case.
let CCC_TARGET_IS_REMOTE: Bool = {
    let host = (CCC_URL.host ?? "").lowercased()
    return !host.isEmpty && host != "localhost" && host != "127.0.0.1" && host != "0.0.0.0"
}()
let CCC_BUNDLE_VERSION = (Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String) ?? "dev"
let CCC_MAIN_MIN_WIDTH: CGFloat = 420
let CCC_MAIN_MIN_HEIGHT: CGFloat = 600

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

func carModeCommandExists() -> Bool {
    FileManager.default.isExecutableFile(atPath: CCC_CAR_MODE_CMD)
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

func python3Works() -> Bool {
    let proc = Process()
    proc.launchPath = "/bin/bash"
    proc.arguments = ["-c", "python3 -c pass"]
    var env = ProcessInfo.processInfo.environment
    env["PATH"] = augmentedPath()
    proc.environment = env
    proc.standardOutput = FileHandle.nullDevice
    proc.standardError = FileHandle.nullDevice
    do { try proc.run() } catch { return false }
    proc.waitUntilExit()
    return proc.terminationStatus == 0
}

func attachProcessLog(_ process: Process) throws -> FileHandle {
    try FileManager.default.createDirectory(
        atPath: CCC_LOG_DIR,
        withIntermediateDirectories: true
    )
    if !FileManager.default.fileExists(atPath: CCC_LOG_PATH) {
        guard FileManager.default.createFile(atPath: CCC_LOG_PATH, contents: nil) else {
            throw NSError(
                domain: "CCCInstall",
                code: 1,
                userInfo: [
                    NSLocalizedDescriptionKey: "Cannot create \(CCC_LOG_PATH)"
                ]
            )
        }
    }
    guard let handle = FileHandle(forWritingAtPath: CCC_LOG_PATH) else {
        throw NSError(
            domain: "CCCInstall",
            code: 2,
            userInfo: [
                NSLocalizedDescriptionKey: "Cannot open \(CCC_LOG_PATH) for writing"
            ]
        )
    }
    handle.seekToEndOfFile()
    let header = "\n--- CCC app bootstrap \(Date()) ---\n"
    if let data = header.data(using: .utf8) {
        handle.write(data)
    }
    process.standardOutput = handle
    process.standardError = handle
    return handle
}

func logTail(_ path: String, lines: Int = 12) -> String {
    guard let data = FileManager.default.contents(atPath: path),
          let text = String(data: data, encoding: .utf8) else { return "" }
    let rows = text.split(separator: "\n", omittingEmptySubsequences: false)
    return rows.suffix(lines).joined(separator: "\n")
        .trimmingCharacters(in: .whitespacesAndNewlines)
}

func isLocalDashboardURL(_ url: URL) -> Bool {
    let scheme = (url.scheme ?? "").lowercased()
    if scheme == "about" || scheme == "data" || scheme == "blob" { return true }
    if scheme != "http" && scheme != "https" { return false }
    let host = (url.host ?? "").lowercased()
    // Historically this only ever matched localhost aliases, because the
    // dashboard only ever ran on this Mac. Now CCC_URL may point at a remote
    // host (FEAT-NEXT-10) — match against whatever CCC_URL's host actually
    // is, falling back to the localhost-alias set when it is local.
    let targetHost = (CCC_URL.host ?? "").lowercased()
    let hostMatches: Bool
    if CCC_TARGET_IS_REMOTE {
        hostMatches = host == targetHost
    } else {
        hostMatches = host == "localhost" || host == "127.0.0.1" || host == "0.0.0.0"
    }
    if !hostMatches { return false }
    // Only OUR dashboard port is the in-app dashboard. Other localhost ports
    // (e.g. the Next.js dev server the "localhost" pill links to) are external
    // sites — they must open in the browser, not spawn a duplicate in-app
    // window (CCC-39). Default ports (no explicit :port) are never the CCC
    // dashboard, which always runs on CCC_PORT (or CCC_URL's port, remotely).
    let port = url.port ?? (scheme == "https" ? 443 : 80)
    let targetPort = CCC_URL.port ?? (CCC_URL.scheme == "https" ? 443 : 80)
    return port == targetPort
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
        // Programmatic NSWindows default to isReleasedWhenClosed=true. We
        // also hold a strong `window` reference, so the close button (or
        // Cmd+W) over-released the popout window and crashed the whole app
        // (CCC-71: "red X closes the app, then an error appears").
        win.isReleasedWhenClosed = false
        if isMain {
            win.title = "Command Center for Claude, Codex, Antigravity — v\(CCC_BUNDLE_VERSION)"
            win.minSize = NSSize(width: CCC_MAIN_MIN_WIDTH, height: CCC_MAIN_MIN_HEIGHT)
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
            // Only load manually on the bridge path (configuration == nil).
            // When this window is born from createWebViewWith (window.open),
            // WebKit loads the request into the returned webview itself —
            // a manual load here races that navigation and the page hangs
            // on a permanent spinner (CCC-71: "pop-up loads forever").
            if configuration == nil, let url = url {
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
        // Sub-frame loads are page content, not the user leaving the app. An
        // Applications-rail app that frames an external dashboard
        // (/app/<id>) would otherwise be cancelled here and thrown into the
        // browser, which is exactly what the rail exists to avoid.
        if !(navigationAction.targetFrame?.isMainFrame ?? true) {
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
        if isMain {
            appDelegate?.startUpdaterAfterBootstrap()
        }
        // A reused named popout (window.open with an existing target name)
        // re-navigates without passing through createWebViewWith, so nothing
        // raises it — bring it to the front whenever it finishes a page load.
        if !isMain {
            window.makeKeyAndOrderFront(nil)
            NSApp.activate(ignoringOtherApps: true)
        }
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

    @available(macOS 12.0, *)
    func webView(_ webView: WKWebView,
                 requestMediaCapturePermissionFor origin: WKSecurityOrigin,
                 initiatedByFrame frame: WKFrameInfo,
                 type: WKMediaCaptureType,
                 decisionHandler: @escaping (WKPermissionDecision) -> Void) {
        decisionHandler(.grant)
    }
}

// MARK: - App Delegate

// Sparkle's installer progress agent asks the host to terminate via
// NSRunningApplication.terminate so it can swap in the downloaded bundle —
// but on this app that request never lands (verified on macOS 26, Sparkle
// 2.9.2: the agent sits waiting, the host sits healthy-idle, and the
// "Updating…" status window hangs forever; an Apple-Event quit, a menu
// Quit, or a manual SIGTERM all unstick it instantly). Quitting ourselves
// when the driver reports the install is starting puts the termination
// exactly where the flow expects it; the agent then installs + relaunches.
final class CCCUpdaterDelegate: NSObject, SPUUpdaterDelegate {
    func updater(_ updater: SPUUpdater, willInstallUpdate item: SUAppcastItem) {
        NSApp.terminate(nil)
    }
}

final class AppDelegate: NSObject, NSApplicationDelegate {
    var mainWebWindow: CCCWebWindow!
    var window: NSWindow! { mainWebWindow.window }
    var webView: WKWebView! { mainWebWindow.webView }
    var loadingLabel: NSTextField! { mainWebWindow.loadingLabel! }
    private var popoutWindows: [CCCWebWindow] = []
    private var nativeBridge: CCCNativeBridge?
    private var bridgedContentControllers = Set<ObjectIdentifier>()
    private var terminationSignalSources: [DispatchSourceSignal] = []
    var serverProcess: Process?
    var serverLogHandle: FileHandle?
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
    let updaterDelegate = CCCUpdaterDelegate()
    var updaterStarted = false
    // Menu-bar "is CCC actually running" indicator (see the WhatsApp thread this
    // came from: the server can keep serving in the background — launchd, or
    // this app just sitting with its window closed per
    // applicationShouldTerminateAfterLastWindowClosed — with zero visible sign
    // of it). A colored dot in the system menu bar is the one thing that's
    // visible with the app window closed and even after Cmd+Q, as long as the
    // server itself (launchd-managed) is still bound to CCC_PORT.
    var statusItem: NSStatusItem!
    var statusPollTimer: Timer?
    var statusPulseTimer: Timer?
    var statusServerRunning = false
    var statusBusyCount = 0
    var statusPulseOn = false
    var statusLiveWorkers: [[String: Any]] = []

    func applicationDidFinishLaunching(_ notification: Notification) {
        installTerminationSignalHandlers()
        updaterController = SPUStandardUpdaterController(
            // Sparkle can present first-run modal UI. Starting it while a
            // bootstrap error alert is active stops that app-global modal
            // session and can accidentally select Retry or Quit. Defer until
            // the live dashboard has finished loading.
            startingUpdater: false,
            updaterDelegate: updaterDelegate,
            userDriverDelegate: nil
        )
        buildMenuBar()
        buildStatusItem()
        buildWindow()
        bootstrap()
    }

    func startUpdaterAfterBootstrap() {
        guard !updaterStarted else { return }
        updaterStarted = true
        updaterController.startUpdater()
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
        statusPollTimer?.invalidate()
        statusPulseTimer?.invalidate()
        // Only kill the server if we started it. If it was already up
        // (launchd service, foreground ./run.sh elsewhere), leave it alone.
        stopOwnedProcess()
        closeServerLog()
        terminationSignalSources.forEach { $0.cancel() }
        terminationSignalSources.removeAll()
    }

    func installTerminationSignalHandlers() {
        // Cocoa does not route a raw SIGTERM through applicationWillTerminate.
        // Convert process-manager / test-harness termination into a normal app
        // quit so an installer/server child cannot be orphaned.
        for signalNumber in [SIGTERM, SIGINT] {
            signal(signalNumber, SIG_IGN)
            let source = DispatchSource.makeSignalSource(
                signal: signalNumber,
                queue: .main
            )
            source.setEventHandler {
                NSApp.terminate(nil)
            }
            source.resume()
            terminationSignalSources.append(source)
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
        // FEAT-NEXT-10: point this app at a CCC already running elsewhere
        // (e.g. a tailnet host) instead of always spawning one locally.
        appMenu.addItem(withTitle: "Set Remote Server…",
                        action: #selector(setRemoteServer),
                        keyEquivalent: "")
        appMenu.addItem(NSMenuItem.separator())
        // Car Mode (Voice) — only when a local launcher is wired (see ccc-voice).
        if carModeCommandExists() {
            appMenu.addItem(withTitle: "Start Car Mode (Voice)…",
                            action: #selector(startCarMode),
                            keyEquivalent: "")
            appMenu.addItem(NSMenuItem.separator())
        }
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

    // MARK: Status item (menu bar running indicator)
    //
    // Base glyph is the app icon itself (recognizable at a glance); a small
    // badge in the corner carries state: gray = server not running, green =
    // running and idle, blue/yellow alternating = a worker is actually
    // executing right now (from /api/system/services' busy_count).
    //
    // NSStatusBarButton ignores contentTintColor on a template image (renders
    // monochrome regardless — a known quirk), so every state is composited
    // as real color, never a tinted template.

    func statusIconImage() -> NSImage {
        let size = NSSize(width: 20, height: 20)
        let image = NSImage(size: size)
        image.lockFocus()
        let iconRect = NSRect(x: 1, y: 3, width: 16, height: 16)
        NSApp.applicationIconImage?.draw(
            in: iconRect, from: .zero, operation: .sourceOver,
            fraction: statusServerRunning ? 1.0 : 0.35
        )
        let badgeColor: NSColor
        if !statusServerRunning {
            badgeColor = .systemGray
        } else if statusBusyCount > 0 {
            badgeColor = statusPulseOn ? .systemBlue : .systemYellow
        } else {
            badgeColor = .systemGreen
        }
        let d: CGFloat = 8
        let badgeRect = NSRect(x: size.width - d - 1, y: 0, width: d, height: d)
        NSColor.white.setFill()
        NSBezierPath(ovalIn: badgeRect.insetBy(dx: -1, dy: -1)).fill()
        badgeColor.setFill()
        NSBezierPath(ovalIn: badgeRect).fill()
        image.unlockFocus()
        image.isTemplate = false
        return image
    }

    func buildStatusItem() {
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.squareLength)
        pollStatusItemState()
        statusPollTimer = Timer.scheduledTimer(withTimeInterval: 5.0, repeats: true) { [weak self] _ in
            self?.pollStatusItemState()
        }
        // Separate, faster timer just to alternate the busy badge's color —
        // decoupled from the network poll above so the pulse stays smooth
        // regardless of request latency.
        statusPulseTimer = Timer.scheduledTimer(withTimeInterval: 0.6, repeats: true) { [weak self] _ in
            guard let self = self, self.statusBusyCount > 0 else { return }
            self.statusPulseOn.toggle()
            self.redrawStatusIcon()
        }
    }

    func redrawStatusIcon() {
        statusItem?.button?.image = statusIconImage()
        statusItem?.button?.toolTip = !statusServerRunning
            ? "CCC server is not running"
            : statusBusyCount > 0
                ? "CCC server running — \(statusBusyCount) worker\(statusBusyCount == 1 ? "" : "s") executing"
                : "CCC server is running on port \(CCC_PORT)"
    }

    func pollStatusItemState() {
        let running = portIsBound(CCC_PORT)
        statusServerRunning = running
        if !running {
            statusBusyCount = 0
            redrawStatusIcon()
            rebuildStatusMenu()
            return
        }
        fetchSystemServicesState { [weak self] count, workers in
            guard let self = self else { return }
            self.statusBusyCount = count
            self.statusLiveWorkers = workers
            self.redrawStatusIcon()
            self.rebuildStatusMenu()
        }
        // Draw immediately with whatever we already know; the busy count
        // above lands a moment later and redraws again.
        redrawStatusIcon()
        rebuildStatusMenu()
    }

    // Counts actually-executing work from /api/system/services — deliberately
    // NOT the generic busy_count field, which for watchtower means
    // workers_live (alive, including idle-and-warm workers with nothing
    // claimed — that field exists for the restart-safety chip, not this).
    // dashboard.busy_count is genuine in-flight executions; worker.active
    // (not active+queued — queued hasn't started yet) is genuinely running;
    // watchtower.claimed_worker_count is live workers matched to an
    // in-progress claimed ticket. Also returns watchtower's per-worker
    // breakdown (live_workers) so the menu can list each one by name with
    // its actual ticket, instead of asking for trust in a single number.
    func fetchSystemServicesState(completion: @escaping (Int, [[String: Any]]) -> Void) {
        guard let url = URL(string: "http://127.0.0.1:\(CCC_PORT)/api/system/services") else {
            completion(0, [])
            return
        }
        var req = URLRequest(url: url)
        req.timeoutInterval = 4
        URLSession.shared.dataTask(with: req) { data, _, _ in
            guard let data = data,
                  let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
                  let services = json["services"] as? [[String: Any]] else {
                DispatchQueue.main.async { completion(0, []) }
                return
            }
            var total = 0
            var liveWorkers: [[String: Any]] = []
            for svc in services {
                switch svc["id"] as? String {
                case "dashboard":
                    total += (svc["busy_count"] as? Int) ?? 0
                case "worker":
                    total += (svc["active"] as? Int) ?? 0
                case "watchtower":
                    total += (svc["claimed_worker_count"] as? Int) ?? 0
                    liveWorkers = (svc["live_workers"] as? [[String: Any]]) ?? []
                default:
                    break
                }
            }
            DispatchQueue.main.async { completion(total, liveWorkers) }
        }.resume()
    }

    func rebuildStatusMenu() {
        let menu = NSMenu()
        let title: String
        if !statusServerRunning {
            title = "Server: Not running"
        } else if statusBusyCount > 0 {
            title = "Server: Running — \(statusBusyCount) worker\(statusBusyCount == 1 ? "" : "s") executing"
        } else {
            title = "Server: Running, idle (port \(CCC_PORT))"
        }
        let statusLabel = NSMenuItem(title: title, action: nil, keyEquivalent: "")
        statusLabel.isEnabled = false
        menu.addItem(statusLabel)

        if statusServerRunning && !statusLiveWorkers.isEmpty {
            menu.addItem(NSMenuItem.separator())
            let header = NSMenuItem(title: "WatchTower workers", action: nil, keyEquivalent: "")
            header.isEnabled = false
            menu.addItem(header)
            for worker in statusLiveWorkers {
                menu.addItem(statusWorkerMenuItem(worker))
            }
        }

        menu.addItem(NSMenuItem.separator())
        menu.addItem(withTitle: "Open Dashboard",
                     action: #selector(showMainWindowFromStatusItem),
                     keyEquivalent: "")
        menu.addItem(NSMenuItem.separator())
        menu.addItem(withTitle: "Quit Command Center",
                     action: #selector(NSApplication.terminate(_:)),
                     keyEquivalent: "")
        statusItem?.menu = menu
    }

    // One line per live WatchTower worker: which queue it belongs to, and
    // the exact ticket it's claiming right now — or "idle" if it's alive but
    // has nothing claimed (the case that made the old single-number pulse
    // unconvincing: a worker idle 23m still counted as "busy").
    func statusWorkerMenuItem(_ worker: [String: Any]) -> NSMenuItem {
        let queue = (worker["queue"] as? String)?.isEmpty == false ? (worker["queue"] as! String) : "?"
        let ref = worker["ticket_ref"] as? String
        let rawTitle = worker["ticket_title"] as? String
        let idleSeconds = worker["idle_seconds"] as? Int

        let dotColor: NSColor
        let line: String
        if let ref = ref, !ref.isEmpty {
            let full = rawTitle ?? ""
            let short = full.count > 56 ? String(full.prefix(56)) + "…" : full
            line = "\(queue) · \(ref)" + (short.isEmpty ? "" : ": \(short)")
            dotColor = .systemBlue
        } else {
            let idleText = idleSeconds.map { " (idle \(max(0, $0) / 60)m)" } ?? ""
            line = "\(queue) · idle\(idleText)"
            dotColor = .systemGray
        }

        let attributed = NSMutableAttributedString(
            string: "●  ", attributes: [.foregroundColor: dotColor]
        )
        attributed.append(NSAttributedString(
            string: line, attributes: [.foregroundColor: NSColor.labelColor]
        ))
        let item = NSMenuItem(title: "", action: nil, keyEquivalent: "")
        item.attributedTitle = attributed
        item.isEnabled = false
        if let ref = ref, let full = rawTitle, !full.isEmpty {
            item.toolTip = "\(ref): \(full)"
        }
        return item
    }

    @objc func showMainWindowFromStatusItem() {
        mainWebWindow?.window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
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

    // FEAT-NEXT-10: let the user point this native shell at a CCC already
    // running elsewhere (e.g. reachable via Tailscale) instead of always
    // spawning a local server. Stored in UserDefaults; CCC_URL is a `let`
    // resolved once at process start, so the new target only takes effect
    // after a restart — same tradeoff as the existing CCC_PORT/CCC_INSTALL_DIR
    // env-var overrides, which also require relaunch.
    @objc func setRemoteServer() {
        let alert = NSAlert()
        alert.messageText = "Remote CCC Server"
        alert.informativeText =
            "Point this app at a CCC instance already running elsewhere "
            + "(e.g. http://100.x.x.x:8090 over Tailscale). Leave blank to use "
            + "the local server on this Mac (default)."
        alert.alertStyle = .informational
        alert.addButton(withTitle: "Save")
        alert.addButton(withTitle: "Cancel")
        let input = NSTextField(frame: NSRect(x: 0, y: 0, width: 320, height: 24))
        input.stringValue = UserDefaults.standard.string(forKey: CCC_REMOTE_URL_DEFAULTS_KEY) ?? ""
        input.placeholderString = "http://localhost:\(CCC_PORT)"
        alert.accessoryView = input
        alert.window.initialFirstResponder = input
        guard alert.runModal() == .alertFirstButtonReturn else { return }

        let trimmed = input.stringValue.trimmingCharacters(in: .whitespacesAndNewlines)
        if trimmed.isEmpty {
            UserDefaults.standard.removeObject(forKey: CCC_REMOTE_URL_DEFAULTS_KEY)
        } else if let url = URL(string: trimmed), url.scheme != nil, url.host != nil {
            UserDefaults.standard.set(trimmed, forKey: CCC_REMOTE_URL_DEFAULTS_KEY)
        } else {
            let bad = NSAlert()
            bad.messageText = "Invalid URL"
            bad.informativeText = "\"\(trimmed)\" doesn't look like a valid URL (e.g. http://100.x.x.x:8090)."
            bad.alertStyle = .warning
            bad.runModal()
            return
        }

        let restart = NSAlert()
        restart.messageText = "Restart required"
        restart.informativeText = "Quit and reopen Command Center for the new server target to take effect."
        restart.alertStyle = .informational
        restart.addButton(withTitle: "Quit Now")
        restart.addButton(withTitle: "Later")
        if restart.runModal() == .alertFirstButtonReturn {
            NSApp.terminate(nil)
        }
    }

    // Launch the local Car Mode voice helper. `open` runs the .command in Terminal,
    // which gives the voice agent's console mode the controlling TTY it needs.
    @objc func startCarMode() {
        let proc = Process()
        proc.launchPath = "/usr/bin/open"
        proc.arguments = [CCC_CAR_MODE_CMD]
        do {
            try proc.run()
        } catch {
            let alert = NSAlert()
            alert.messageText = "Could not start Car Mode"
            alert.informativeText = "Failed to open \(CCC_CAR_MODE_CMD)\n\n\(error)"
            alert.alertStyle = .warning
            alert.runModal()
        }
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
        if CCC_TARGET_IS_REMOTE {
            // Thin-client mode (FEAT-NEXT-10): CCC_URL points at a CCC
            // already running elsewhere (e.g. over Tailscale). Never install
            // or spawn a local server — just load the remote dashboard.
            loadDashboard()
            return
        }

        if !FileManager.default.fileExists(atPath: CCC_INSTALL_DIR) {
            // First-time install. Run the bundled installer as our child so
            // progress, failures, and the resulting server stay observable.
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
            showFatal(
                "Install script missing",
                "The app bundle is incomplete. Re-download it from github.com/amirfish1/claude-command-center/releases."
            )
            return
        }
        loadingLabel.stringValue = "Installing Command Center…"

        let proc = Process()
        proc.launchPath = "/bin/bash"
        proc.arguments = [installScript, "--from=dmg"]

        var env = CCC_ENV
        env["PATH"] = augmentedPath()
        env["PORT"] = "\(CCC_PORT)"
        env["CCC_FROM"] = "dmg"
        env["CCC_INSTALL_MODE"] = "app"
        env["CCC_INSTALL_DIR"] = CCC_INSTALL_DIR
        proc.environment = env
        proc.currentDirectoryPath = env["HOME"] ?? NSHomeDirectory()

        do {
            closeServerLog()
            serverLogHandle = try attachProcessLog(proc)
            try proc.run()
            serverProcess = proc
        } catch {
            closeServerLog()
            showBootstrapFailure("Installation could not start", "\(error)")
            return
        }

        // Cloning a multi-hundred-MB history over a slow link can legitimately
        // take minutes; the 60s default is tuned for local server startup, not
        // a network transfer. A too-short timeout here SIGTERMs the clone
        // mid-transfer, which git reports as a confusing "unexpected
        // disconnect" rather than "we killed it."
        pollUntilReady(process: proc, operation: "installation", timeout: 600)
    }

    func spawnServer() {
        let runSh = "\(CCC_INSTALL_DIR)/run.sh"
        guard FileManager.default.fileExists(atPath: runSh) else {
            // Install dir exists but run.sh missing — corrupt checkout. Reinstall.
            runInstaller()
            return
        }

        // Preflight: on a fresh Mac /usr/bin/python3 is a Command Line Tools
        // stub that exits without serving anything — the #1 cause of "port
        // never bound" on machines that never installed dev tools. Fail with
        // the actual remedy instead of a 60s timeout.
        if !python3Works() {
            showFatal("Python 3 is not installed",
                      "CCC needs python3, which ships with Apple's Command Line Tools.\n\n"
                      + "Open Terminal, run:\n\n    xcode-select --install\n\n"
                      + "finish that install, then reopen CCC.")
            return
        }

        loadingLabel.stringValue = "Starting CCC server…"

        let proc = Process()
        proc.launchPath = "/bin/bash"
        proc.arguments = [runSh]
        proc.currentDirectoryPath = CCC_INSTALL_DIR

        var env = CCC_ENV
        env["PATH"] = augmentedPath()
        env["PORT"] = "\(CCC_PORT)"
        env["CCC_FROM"] = "dmg"
        proc.environment = env

        do {
            closeServerLog()
            serverLogHandle = try attachProcessLog(proc)
            try proc.run()
            serverProcess = proc
        } catch {
            closeServerLog()
            showBootstrapFailure("Server could not start", "\(error)")
            return
        }

        pollUntilReady(process: proc, operation: "server startup")
    }

    func pollUntilReady(process: Process?, operation: String, timeout: TimeInterval = 60) {
        let start = Date()
        pollTimer = Timer.scheduledTimer(withTimeInterval: 0.5, repeats: true) { [weak self] timer in
            guard let self = self else { timer.invalidate(); return }
            if portIsBound(CCC_PORT) {
                timer.invalidate()
                self.pollTimer = nil
                self.loadDashboard()
                return
            }
            if let process = process, !process.isRunning {
                timer.invalidate()
                self.pollTimer = nil
                let tail = logTail(CCC_LOG_PATH)
                let detail = "The \(operation) exited with status \(process.terminationStatus)."
                    + (tail.isEmpty ? "" : "\n\nLast log lines:\n\n\(tail)")
                self.showBootstrapFailure("Command Center could not start", detail)
                return
            }
            if FileManager.default.fileExists(atPath: "\(CCC_INSTALL_DIR)/run.sh") {
                self.loadingLabel.stringValue = "Starting CCC server…"
            }
            if Date().timeIntervalSince(start) > timeout {
                timer.invalidate()
                self.pollTimer = nil
                let tail = logTail(CCC_LOG_PATH)
                let detail = "The \(operation) did not bind port \(CCC_PORT) within \(Int(timeout)) seconds."
                    + (tail.isEmpty ? "" : "\n\nLast log lines:\n\n\(tail)")
                self.showBootstrapFailure("Command Center could not start", detail)
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
            // There is no local server to restart in thin-client mode — stop
            // escalating past the reload and surface a network-facing message.
            if self.watchdogReloaded && !self.watchdogRestarted && CCC_TARGET_IS_REMOTE {
                self.stopWatchdog()
                self.loadingLabel.isHidden = false
                self.loadingLabel.stringValue =
                    "Can't reach \(CCC_URL.absoluteString) — check your network/Tailscale connection."
                return
            }
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

    func stopOwnedProcess() {
        guard let proc = serverProcess, proc.isRunning else { return }
        proc.terminate()
        let deadline = Date().addingTimeInterval(2.0)
        while proc.isRunning && Date() < deadline {
            Thread.sleep(forTimeInterval: 0.1)
        }
        if proc.isRunning {
            kill(proc.processIdentifier, SIGKILL)
        }
    }

    func closeServerLog() {
        try? serverLogHandle?.close()
        serverLogHandle = nil
    }

    func showBootstrapFailure(_ title: String, _ message: String) {
        stopOwnedProcess()
        closeServerLog()
        while true {
            let alert = NSAlert()
            alert.messageText = title
            alert.informativeText = message + "\n\nLog: \(CCC_LOG_PATH)"
            alert.alertStyle = .critical
            alert.addButton(withTitle: "Retry")
            alert.addButton(withTitle: "Open Log")
            alert.addButton(withTitle: "Quit")
            switch alert.runModal() {
            case .alertFirstButtonReturn:
                serverProcess = nil
                bootstrap()
                return
            case .alertSecondButtonReturn:
                NSWorkspace.shared.open(URL(fileURLWithPath: CCC_LOG_PATH))
            default:
                NSApp.terminate(nil)
                return
            }
        }
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
