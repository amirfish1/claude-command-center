import UIKit
import Capacitor

// The connection controls belong to the app, so a missing or unreachable
// server can never strand the user inside a blank remote page.
class ServerNavigationController: UINavigationController {
    override func viewDidLoad() {
        super.viewDidLoad()
        #if DEBUG
        if ProcessInfo.processInfo.arguments.contains("--ui-testing-reset-server") {
            UserDefaults.standard.removeObject(forKey: ServerAddress.defaultsKey)
        }
        #endif
        navigationBar.prefersLargeTitles = false
        if let url = ServerAddress.saved {
            showServer(url, animated: false)
        } else {
            setViewControllers([settingsController()], animated: false)
        }
    }

    private func settingsController() -> ServerSettingsViewController {
        let controller = ServerSettingsViewController()
        controller.onConnect = { [weak self] url in
            ServerAddress.save(url)
            self?.showServer(url, animated: true)
        }
        return controller
    }

    private func showServer(_ url: URL, animated: Bool) {
        // Rebuild the bridge when switching servers: its origin, allowed
        // navigation host, and plugin configuration must all agree.
        let controller = ServerBridgeViewController(serverURL: url)
        controller.navigationItem.rightBarButtonItem = UIBarButtonItem(
            title: "Settings", style: .plain, target: self, action: #selector(openSettings))
        setViewControllers([controller], animated: animated)
    }

    @objc private func openSettings() {
        guard !(topViewController is ServerSettingsViewController) else { return }
        pushViewController(settingsController(), animated: true)
    }
}

final class ServerBridgeViewController: CAPBridgeViewController {
    private let serverURL: URL

    init(serverURL: URL) {
        self.serverURL = serverURL
        super.init(nibName: nil, bundle: nil)
        edgesForExtendedLayout = []
    }

    required init?(coder: NSCoder) { fatalError("Use init(serverURL:)") }

    override func instanceDescriptor() -> InstanceDescriptor {
        let descriptor = super.instanceDescriptor()
        descriptor.serverURL = serverURL.absoluteString
        descriptor.allowedNavigationHostnames = [serverURL.host!]
        descriptor.errorPath = "connection-error.html"
        return descriptor
    }

    override func viewDidLoad() {
        super.viewDidLoad()
        title = "CCC"
        navigationItem.leftBarButtonItem = UIBarButtonItem(
            title: "Retry", style: .plain, target: self, action: #selector(retry))
        webView?.scrollView.keyboardDismissMode = .interactive
    }

    @objc private func retry() {
        webView?.stopLoading()
        loadWebView()
    }
}

final class ServerSettingsViewController: UIViewController, UITextFieldDelegate {
    var onConnect: ((URL) -> Void)?
    private let addressField = UITextField()
    private let errorLabel = UILabel()

    override func viewDidLoad() {
        super.viewDidLoad()
        title = ServerAddress.saved == nil ? "Connect to CCC" : "Server settings"
        view.backgroundColor = .systemBackground

        let heading = UILabel()
        heading.text = "Connect to your CCC"
        heading.font = .preferredFont(forTextStyle: .title1)
        heading.adjustsFontForContentSizeCategory = true
        heading.numberOfLines = 0

        let instructions = UILabel()
        instructions.text = "On your computer, open CCC → Settings → Phone access. Copy the HTTPS address here. Your phone must be able to reach that server; for Tailscale addresses, connect Tailscale first."
        instructions.font = .preferredFont(forTextStyle: .body)
        instructions.adjustsFontForContentSizeCategory = true
        instructions.numberOfLines = 0

        let label = UILabel()
        label.text = "Server URL"
        label.font = .preferredFont(forTextStyle: .headline)
        label.adjustsFontForContentSizeCategory = true

        addressField.borderStyle = .roundedRect
        addressField.placeholder = "https://your-computer.example.ts.net:8443/"
        addressField.text = ServerAddress.saved?.absoluteString
        addressField.keyboardType = .URL
        addressField.autocorrectionType = .no
        addressField.autocapitalizationType = .none
        addressField.spellCheckingType = .no
        addressField.clearButtonMode = .whileEditing
        addressField.returnKeyType = .go
        addressField.accessibilityLabel = "Server URL"
        addressField.accessibilityIdentifier = "serverURL"
        addressField.delegate = self

        errorLabel.textColor = .systemRed
        errorLabel.font = .preferredFont(forTextStyle: .body)
        errorLabel.adjustsFontForContentSizeCategory = true
        errorLabel.numberOfLines = 0
        errorLabel.isHidden = true
        errorLabel.accessibilityIdentifier = "serverURLError"

        let connect = UIButton(type: .system)
        connect.setTitle("Connect", for: .normal)
        connect.titleLabel?.font = .preferredFont(forTextStyle: .headline)
        connect.titleLabel?.adjustsFontForContentSizeCategory = true
        connect.accessibilityIdentifier = "connectServer"
        connect.addTarget(self, action: #selector(connectServer), for: .touchUpInside)

        let privacy = UILabel()
        privacy.text = "Only connect to a CCC server you trust. The address is saved on this device. You can change it from Settings at any time, even when the server is offline."
        privacy.font = .preferredFont(forTextStyle: .footnote)
        privacy.adjustsFontForContentSizeCategory = true
        privacy.textColor = .secondaryLabel
        privacy.numberOfLines = 0

        let stack = UIStackView(arrangedSubviews: [heading, instructions, label, addressField, errorLabel, connect, privacy])
        stack.axis = .vertical
        stack.spacing = 20
        stack.translatesAutoresizingMaskIntoConstraints = false
        let scroll = UIScrollView()
        scroll.translatesAutoresizingMaskIntoConstraints = false
        scroll.keyboardDismissMode = .interactive
        view.addSubview(scroll)
        scroll.addSubview(stack)
        NSLayoutConstraint.activate([
            scroll.topAnchor.constraint(equalTo: view.safeAreaLayoutGuide.topAnchor),
            scroll.bottomAnchor.constraint(equalTo: view.safeAreaLayoutGuide.bottomAnchor),
            scroll.leadingAnchor.constraint(equalTo: view.leadingAnchor),
            scroll.trailingAnchor.constraint(equalTo: view.trailingAnchor),
            stack.topAnchor.constraint(equalTo: scroll.contentLayoutGuide.topAnchor, constant: 28),
            stack.bottomAnchor.constraint(equalTo: scroll.contentLayoutGuide.bottomAnchor, constant: -28),
            stack.centerXAnchor.constraint(equalTo: scroll.frameLayoutGuide.centerXAnchor),
            stack.widthAnchor.constraint(equalTo: scroll.frameLayoutGuide.widthAnchor, constant: -40),
            addressField.heightAnchor.constraint(greaterThanOrEqualToConstant: 48),
            connect.heightAnchor.constraint(greaterThanOrEqualToConstant: 48)
        ])
        // Let the form scroll above the keyboard on smaller phones.
        NotificationCenter.default.addObserver(self, selector: #selector(keyboardChanged(_:)),
            name: UIResponder.keyboardWillChangeFrameNotification, object: nil)
    }

    deinit { NotificationCenter.default.removeObserver(self) }

    @objc private func keyboardChanged(_ notification: Notification) {
        guard let scroll = view.subviews.first as? UIScrollView,
              let frame = notification.userInfo?[UIResponder.keyboardFrameEndUserInfoKey] as? CGRect else { return }
        let overlap = max(0, view.bounds.maxY - view.convert(frame, from: nil).minY - view.safeAreaInsets.bottom)
        scroll.contentInset.bottom = overlap
        scroll.verticalScrollIndicatorInsets.bottom = overlap
    }

    func textFieldShouldReturn(_ textField: UITextField) -> Bool {
        connectServer()
        return true
    }

    @objc private func connectServer() {
        guard let url = ServerAddress.parse(addressField.text ?? "") else {
            errorLabel.text = "Enter the full HTTPS server address, including its port if shown. Use the server root URL without a path, login details, query, or fragment."
            errorLabel.isHidden = false
            UIAccessibility.post(notification: .announcement, argument: errorLabel.text)
            return
        }
        errorLabel.isHidden = true
        view.endEditing(true)
        onConnect?(url)
    }
}
