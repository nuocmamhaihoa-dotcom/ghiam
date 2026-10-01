import Foundation

enum HubStore {
    static let suiteName = "group.com.fbpoller.screen"
    static let defaultBase = "http://222.255.214.202:8088"

    private static let defaults: UserDefaults = {
        if FileManager.default.containerURL(forSecurityApplicationGroupIdentifier: suiteName) != nil,
           let suite = UserDefaults(suiteName: suiteName) {
            return suite
        }
        return .standard
    }()

    static var hubBase: String {
        get {
            let stored = defaults.string(forKey: "hubBase") ?? defaultBase
            return trimSlash(stored.isEmpty ? defaultBase : stored)
        }
        set {
            let trimmed = newValue.trimmingCharacters(in: .whitespacesAndNewlines)
            defaults.set(trimSlash(trimmed.isEmpty ? defaultBase : trimmed), forKey: "hubBase")
        }
    }

    static var hubToken: String {
        get { defaults.string(forKey: "hubToken") ?? "" }
        set { defaults.set(newValue, forKey: "hubToken") }
    }

    static var lines: [String] {
        get { defaults.stringArray(forKey: "lines") ?? [] }
        set { defaults.set(Array(newValue.suffix(30)), forKey: "lines") }
    }

    static func remember(_ line: String) {
        let collapsed = line.split(whereSeparator: \.isWhitespace).joined(separator: " ")
        let text = String(collapsed.prefix(180))
        guard !text.isEmpty else { return }
        var current = lines
        if current.last == text { return }
        current.append(text)
        lines = current
    }

    static func connect() async -> String {
        guard let url = URL(string: hubBase + "/iphone") else {
            return "Địa chỉ hub không đúng."
        }
        var request = URLRequest(url: url)
        request.timeoutInterval = 12
        do {
            let (data, response) = try await URLSession.shared.data(for: request)
            guard let http = response as? HTTPURLResponse, (200..<300).contains(http.statusCode),
                  let html = String(data: data, encoding: .utf8) else {
                return "Không tới được hub."
            }
            guard let token = token(from: html) else {
                return "Chưa đọc được mã kết nối trên hub."
            }
            hubToken = token
            return "Bấm nút ghi, chọn fb-poller, rồi mở ứng dụng khác."
        } catch {
            return "Không tới được hub."
        }
    }

    static func token(from html: String) -> String? {
        let marker = "const hubToken = "
        guard let start = html.range(of: marker) else { return nil }
        let rest = html[start.upperBound...]
        guard let end = rest.firstIndex(of: ";") else { return nil }
        let literal = String(rest[..<end]).trimmingCharacters(in: .whitespacesAndNewlines)
        guard let data = literal.data(using: .utf8),
              let value = try? JSONDecoder().decode(String.self, from: data),
              !value.isEmpty else {
            return nil
        }
        return value
    }

    static func postLive(_ text: String, source: String = "system") async {
        guard !hubToken.isEmpty, let url = URL(string: hubBase + "/v1/screen/live") else { return }
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.timeoutInterval = 12
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("Bearer \(hubToken)", forHTTPHeaderField: "Authorization")
        request.httpBody = try? JSONSerialization.data(withJSONObject: ["text": text, "source": source])
        _ = try? await URLSession.shared.data(for: request)
    }

    static func postShare(_ text: String) async {
        guard !hubToken.isEmpty else {
            await postLive(text, source: "share")
            return
        }
        guard let url = URL(string: hubBase + "/v1/people/from-link") else { return }
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.timeoutInterval = 12
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("Bearer \(hubToken)", forHTTPHeaderField: "Authorization")
        request.httpBody = try? JSONSerialization.data(withJSONObject: ["text": text, "source": "share"])
        do {
            let (_, response) = try await URLSession.shared.data(for: request)
            guard let http = response as? HTTPURLResponse, (200..<300).contains(http.statusCode) else {
                await postLive(text, source: "share")
                return
            }
        } catch {
            await postLive(text, source: "share")
        }
    }

    struct LiveList: Decodable {
        struct Item: Decodable {
            let line: String
        }
        let items: [Item]
    }

    static func fetchLines() async -> [String]? {
        guard !hubToken.isEmpty, let url = URL(string: hubBase + "/v1/screen/live") else { return nil }
        var request = URLRequest(url: url)
        request.timeoutInterval = 12
        request.setValue("Bearer \(hubToken)", forHTTPHeaderField: "Authorization")
        do {
            let (data, response) = try await URLSession.shared.data(for: request)
            guard let http = response as? HTTPURLResponse, (200..<300).contains(http.statusCode) else {
                return nil
            }
            return try JSONDecoder().decode(LiveList.self, from: data).items.map(\.line)
        } catch {
            return nil
        }
    }

    private static func trimSlash(_ value: String) -> String {
        var text = value
        while text.count > "http://".count && text.hasSuffix("/") {
            text.removeLast()
        }
        return text
    }
}
