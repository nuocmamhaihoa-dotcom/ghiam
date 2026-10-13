import Foundation

/// URLSession nền: đúng một mảnh tại một thời điểm, từ file trên máy.
/// iOS gửi tiếp khi app đã ra nền hoặc bị tắt, rồi đánh thức app để xếp mảnh kế.
/// Không xếp hàng chục task nhỏ — hệ thống sẽ hoãn chúng hàng chục giây.
final class BackgroundUploader: NSObject, URLSessionTaskDelegate, URLSessionDataDelegate {
    static let shared = BackgroundUploader()
    static let sessionId = "local.fbpoller.videoup.bg"

    private var session: URLSession!
    private var completionHandler: (() -> Void)?
    private let lock = NSLock()
    private var busyCount = 0

    private override init() {
        super.init()
        let config = URLSessionConfiguration.background(withIdentifier: Self.sessionId)
        config.isDiscretionary = false
        config.sessionSendsLaunchEvents = true
        config.allowsCellularAccess = true
        config.waitsForConnectivity = true
        config.shouldUseExtendedBackgroundIdleMode = true
        config.httpMaximumConnectionsPerHost = 1
        session = URLSession(configuration: config, delegate: self, delegateQueue: nil)
        session.getAllTasks { [weak self] tasks in
            guard let self else { return }
            self.lock.lock()
            self.busyCount = tasks.count
            self.lock.unlock()
        }
    }

    var isBusy: Bool {
        lock.lock()
        let busy = busyCount > 0
        lock.unlock()
        return busy
    }

    func reconnect(identifier: String, completion: @escaping () -> Void) {
        guard identifier == Self.sessionId else {
            completion()
            return
        }
        completionHandler = completion
        _ = session
    }

    /// Nhãn gắn vào task để lần sau app bị đánh thức vẫn biết mảnh nào.
    static func label(jobId: String, index: Int) -> String {
        "\(jobId)#\(index)"
    }

    static func parseLabel(_ label: String?) -> (jobId: String, index: Int)? {
        guard let label, let hash = label.lastIndex(of: "#") else { return nil }
        let jobId = String(label[..<hash])
        guard !jobId.isEmpty, let index = Int(label[label.index(after: hash)...]) else { return nil }
        return (jobId, index)
    }

    /// Bắt đầu một mảnh. File phải còn đến khi delegate báo xong.
    func startChunk(fileURL: URL, to url: URL, authHeader: String?, label: String) {
        var request = URLRequest(url: url)
        request.httpMethod = "PUT"
        request.setValue("application/octet-stream", forHTTPHeaderField: "Content-Type")
        if let authHeader {
            request.setValue(authHeader, forHTTPHeaderField: "Authorization")
        }
        let task = session.uploadTask(with: request, fromFile: fileURL)
        task.taskDescription = label
        lock.lock()
        busyCount += 1
        lock.unlock()
        task.resume()
    }

    func urlSession(
        _ session: URLSession,
        task: URLSessionTask,
        didCompleteWithError error: Error?
    ) {
        lock.lock()
        busyCount = max(0, busyCount - 1)
        lock.unlock()

        let label = task.taskDescription
        var failed: Error? = error
        if failed == nil, let http = task.response as? HTTPURLResponse, !(200..<300).contains(http.statusCode) {
            failed = HubError.http(http.statusCode, "chunk HTTP \(http.statusCode)")
        }
        // Xong mảnh này trước khi iOS gọi urlSessionDidFinishEvents, để kịp xếp mảnh kế.
        DispatchQueue.main.sync {
            MainActor.assumeIsolated {
                UploadQueue.shared.noteBackgroundChunk(label: label, error: failed)
            }
        }
    }

    func urlSessionDidFinishEvents(forBackgroundURLSession session: URLSession) {
        let handler = completionHandler
        completionHandler = nil
        DispatchQueue.main.async {
            MainActor.assumeIsolated {
                UploadQueue.shared.finishBackgroundWake()
            }
            handler?()
        }
    }
}

/// URLSession thường: nhiều mảnh cùng lúc khi app đang mở. Ra nền thì nhường cho BackgroundUploader.
final class ForegroundUploader {
    static let shared = ForegroundUploader()
    private let session: URLSession

    private init() {
        let config = URLSessionConfiguration.default
        config.httpMaximumConnectionsPerHost = 8
        // 16MB trên đường chậm (~30KB/s) mất khoảng 9 phút — đừng cắt giữa mảnh.
        config.timeoutIntervalForRequest = 900
        config.timeoutIntervalForResource = 7200
        config.allowsCellularAccess = true
        config.waitsForConnectivity = true
        session = URLSession(configuration: config)
    }

    func uploadChunk(data: Data, to url: URL, authHeader: String?) async throws {
        var request = URLRequest(url: url)
        request.httpMethod = "PUT"
        request.timeoutInterval = 900
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
