import Foundation

enum ServerAddress {
    static let defaultsKey = "ccc.serverURL"

    static func parse(_ input: String) -> URL? {
        let text = input.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.contains(where: { $0.isWhitespace }), !text.contains("\\"),
              var parts = URLComponents(string: text),
              parts.scheme?.lowercased() == "https",
              let host = parts.host, !host.isEmpty, !host.contains("*"),
              parts.user == nil, parts.password == nil,
              parts.query == nil, parts.fragment == nil,
              parts.path.isEmpty || parts.path == "/",
              parts.port == nil || (1...65535).contains(parts.port!) else {
            return nil
        }
        parts.scheme = "https"
        parts.path = "/"
        return parts.url
    }

    static var saved: URL? {
        return load()
    }

    static func load(from defaults: UserDefaults = .standard) -> URL? {
        guard let text = defaults.string(forKey: defaultsKey) else { return nil }
        return parse(text)
    }

    static func save(_ url: URL, in defaults: UserDefaults = .standard) {
        defaults.set(url.absoluteString, forKey: defaultsKey)
    }
}
