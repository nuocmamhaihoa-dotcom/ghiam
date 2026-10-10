import Foundation

/// URLSession nền: mỗi chunk là một uploadTask từ file tạm — khóa máy vẫn gửi tiếp.
final class BackgroundUploader: NSObject, URLSessionTaskDelegate, URLSessionDataDelegate {
    static let shared = BackgroundUploader()
    static let sessionId = "local.fbpoller.videoup.bg"

    private var session: URLSession!
    private var completionHandler: (() -> Void)?
    private let lock = NSLock()
    private var waiters: [Int: CheckedContinuation<Void, Error>] = [:]
    private var taskErrors: [Int: Error] = [:]

    private override init() {
        super.init()
        let config = URLSessionConfiguration.background(withIdentifier: Self.sessionId)
        config.isDiscretionary = false
        config.sessionSendsLaunchEvents = true
        config.allowsCellularAccess = true
        config.waitsForConnectivity = true
        config.httpMaximumConnectionsPerHost = 2
        session = URLSession(configuration: config, delegate: self, delegateQueue: nil)
    }

    func reconnect(identifier: String, completion: @escaping () -> Void) {
        guard identifier == Self.sessionId else {
            completion()
            return
        }
        completionHandler = completion
        _ = session
    }

    /// Phiên nền (khóa máy). Mỗi task nhỏ bị iOS xếp lịch ~30–60s — chậm. Ưu tiên ForegroundUploader.
    func uploadChunk(
        fileURL: URL,
        to url: URL,
        authHeader: String?
    ) async throws {
        var request = URLRequest(url: url)
        request.httpMethod = "PUT"
        request.setValue("application/octet-stream", forHTTPHeaderField: "Content-Type")
        if let authHeader {
            request.setValue(authHeader, forHTTPHeaderField: "Authorization")
        }
        let task = session.uploadTask(with: request, fromFile: fileURL)
        try await withCheckedThrowingContinuation { (cont: CheckedContinuation<Void, Error>) in
            lock.lock()
            waiters[task.taskIdentifier] = cont
            lock.unlock()
            task.resume()
        }
    }

    func urlSession(
        _ session: URLSession,
        task: URLSessionTask,
        didCompleteWithError error: Error?
    ) {
        lock.lock()
        let cont = waiters.removeValue(forKey: task.taskIdentifier)
        lock.unlock()
        if let error {
            cont?.resume(throwing: error)
            return
        }
        if let http = task.response as? HTTPURLResponse, !(200..<300).contains(http.statusCode) {
            cont?.resume(throwing: HubError.http(http.statusCode, "chunk HTTP \(http.statusCode)"))
            return
        }
        cont?.resume(returning: ())
    }

    func urlSessionDidFinishEvents(forBackgroundURLSession session: URLSession) {
        let handler = completionHandler
        completionHandler = nil
        DispatchQueue.main.async { handler?() }
    }
}

/// URLSession thường: nhiều mảnh cùng lúc, không chờ lịch nền của iOS.
final class ForegroundUploader {
    static let shared = ForegroundUploader()
    private let session: URLSession

    private init() {
        let config = URLSessionConfiguration.default
        config.httpMaximumConnectionsPerHost = 6
        config.timeoutIntervalForRequest = 120
        config.timeoutIntervalForResource = 300
        config.allowsCellularAccess = true
        config.waitsForConnectivity = true
        session = URLSession(configuration: config)
    }

    func uploadChunk(data: Data, to url: URL, authHeader: String?) async throws {
        var request = URLRequest(url: url)
        request.httpMethod = "PUT"
        request.setValue("application/octet-stream", forHTTPHeaderField: "Content-Type")
        if let authHeader {
            request.setValue(authHeader, forHTTPHeaderField: "Authorization")
        }
        let (_, response) = try await session.upload(for: request, from: data)
        if let http = response as? HTTPURLResponse, !(200..<300).contains(http.statusCode) {
            throw HubError.http(http.statusCode, "chunk HTTP \(http.statusCode)")
        }
    }
}
