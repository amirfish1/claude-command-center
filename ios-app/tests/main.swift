// Foundation-only address and persistence checks; run with swiftc alongside
// ServerAddress.swift. No simulator, network, or production preferences used.
import Foundation

let accepted = [
    ("https://computer.example.ts.net:8443", "https://computer.example.ts.net:8443/"),
    ("  https://example.com/\n", "https://example.com/"),
    ("HTTPS://example.com", "https://example.com/"),
    ("https://[::1]:8443/", "https://[::1]:8443/")
]
for (input, expected) in accepted {
    precondition(ServerAddress.parse(input)?.absoluteString == expected, "Rejected valid address: \(input)")
}
for input in ["", "example.com", "http://example.com", "file:///etc/passwd",
              "javascript:alert(1)", "https:///", "https://example.com/path",
              "https://user:password@example.com", "https://example.com?token=test",
              "https://example.com#fragment", "https://example.com:0",
              "https://example.com:65536", "https://exa mple.com",
              "https://*.example.com", "https://example.com\\evil"] {
    precondition(ServerAddress.parse(input) == nil, "Accepted invalid address: \(input)")
}

let suite = "ccc-server-address-tests-\(UUID().uuidString)"
let defaults = UserDefaults(suiteName: suite)!
defer { defaults.removePersistentDomain(forName: suite) }
precondition(ServerAddress.load(from: defaults) == nil)
let first = ServerAddress.parse("https://first.example.com:8443/")!
ServerAddress.save(first, in: defaults)
precondition(ServerAddress.load(from: UserDefaults(suiteName: suite)!) == first)
let second = ServerAddress.parse("https://second.example.com/")!
ServerAddress.save(second, in: defaults)
precondition(ServerAddress.load(from: UserDefaults(suiteName: suite)!) == second)
defaults.set("http://invalid.example.com", forKey: ServerAddress.defaultsKey)
precondition(ServerAddress.load(from: defaults) == nil)
print("Server address validation and persistence passed")
