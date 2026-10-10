import Foundation
import UniformTypeIdentifiers

@MainActor
final class UploadQueue: ObservableObject {
    static let shared = UploadQueue()

    @Published var settings: HubSettings {
        didSet { saveSettings() }
    }
    @Published private(set) var jobs: [QueueJob] = []
    @Published var banner: String = ""
    @Published var busy = false

    private let settingsKey = "videoup.settings.v1"
    private let jobsKey = "videoup.jobs.v1"
    private var pumping = false
    private let chunkTemp: URL
    private let inbox: URL
    /// Số video gửi chunk song song — đủ nhanh, không nghẽn Wi‑Fi iPhone.
    private let parallelUploads = 2

    private init() {
        let defaults = UserDefaults.standard
        if let data = defaults.data(forKey: settingsKey),
           let decoded = try? JSONDecoder().decode(HubSettings.self, from: data) {
            settings = decoded
        } else {
            settings = .default
        }
        let root = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first!
            .appendingPathComponent("VideoUp", isDirectory: true)
        inbox = root.appendingPathComponent("inbox", isDirectory: true)
        chunkTemp = root.appendingPathComponent("chunks", isDirectory: true)
        try? FileManager.default.createDirectory(at: inbox, withIntermediateDirectories: true)
        try? FileManager.default.createDirectory(at: chunkTemp, withIntermediateDirectories: true)
        if let data = defaults.data(forKey: jobsKey),
           let decoded = try? JSONDecoder().decode([QueueJob].self, from: data) {
            jobs = decoded
        }
        Task { await pump() }
    }

    private func saveSettings() {
        if let data = try? JSONEncoder().encode(settings) {
            UserDefaults.standard.set(data, forKey: settingsKey)
        }
    }

    private func saveJobs() {
        if let data = try? JSONEncoder().encode(jobs) {
            UserDefaults.standard.set(data, forKey: jobsKey)
        }
    }

    func enqueue(urls: [URL]) {
        for url in urls {
            let accessed = url.startAccessingSecurityScopedResource()
            defer { if accessed { url.stopAccessingSecurityScopedResource() } }
            do {
                let values = try url.resourceValues(forKeys: [.fileSizeKey, .contentModificationDateKey, .nameKey])
                let size = Int64(values.fileSize ?? 0)
                guard size > 0 else { continue }
                let name = values.name ?? url.lastPathComponent
                let mtime = Int64(((values.contentModificationDate ?? Date()).timeIntervalSince1970) * 1000)
                let jobId = UUID().uuidString
                let dest = inbox.appendingPathComponent("\(jobId)-\(name)")
                if FileManager.default.fileExists(atPath: dest.path) {
                    try FileManager.default.removeItem(at: dest)
                }
                try FileManager.default.copyItem(at: url, to: dest)
                let key = [settings.deviceName, name, String(size), String(mtime)].joined(separator: "|")
                let job = QueueJob(
                    id: jobId,
                    fileName: name,
                    localPath: dest.path,
                    sizeBytes: size,
                    lastModifiedMs: mtime,
                    clientKey: key,
                    uploadId: nil,
                    chunkSize: 4 * 1024 * 1024,
                    chunksTotal: 0,
                    received: [],
                    status: "queued",
                    detail: "Chờ xếp phiên lên VPS",
                    createdAt: Date(),
                    updatedAt: Date()
                )
                jobs.insert(job, at: 0)
            } catch {
                banner = "Không thêm được \(url.lastPathComponent): \(error.localizedDescription)"
            }
        }
        saveJobs()
        banner = "Đã thêm \(urls.count) video vào hàng đợi máy — đang xếp phiên lên VPS…"
        Task { await pump() }
    }

    func retryFailed() {
        for index in jobs.indices where jobs[index].status == "error" {
            jobs[index].status = "queued"
            jobs[index].detail = "Xếp lại hàng"
            jobs[index].updatedAt = Date()
        }
        saveJobs()
        Task { await pump() }
    }

    func clearFinished() {
        let keep = jobs.filter { $0.status != "done" && $0.status != "duplicate" }
        let drop = jobs.filter { $0.status == "done" || $0.status == "duplicate" }
        for job in drop {
            try? FileManager.default.removeItem(atPath: job.localPath)
        }
        jobs = keep
        saveJobs()
    }

    func pump() async {
        guard !pumping else { return }
        pumping = true
        busy = true
        defer {
            pumping = false
            busy = jobs.contains {
                ["queued", "registered", "uploading"].contains($0.status)
            }
        }

        let client = HubClient(settings: settings)

        // Bước 1: xếp TẤT CẢ phiên receiving lên VPS trước khi gửi chunk.
        // Safari/app ngủ máy vẫn còn tên video trong hàng đợi VPS.
        for index in jobs.indices where jobs[index].status == "queued" {
            do {
                try await register(jobIndex: index, client: client)
            } catch {
                jobs[index].status = "error"
                jobs[index].detail = error.localizedDescription
                jobs[index].updatedAt = Date()
                saveJobs()
                banner = "Lỗi xếp phiên: \(error.localizedDescription)"
            }
        }

        // Bước 2: gửi chunk song song (tối đa parallelUploads).
        while true {
            let pending = jobs.indices.filter { jobs[$0].status == "registered" }
            guard !pending.isEmpty else { break }
            let batch = Array(pending.prefix(parallelUploads))
            await withTaskGroup(of: Void.self) { group in
                for index in batch {
                    group.addTask { @MainActor in
                        do {
                            try await self.uploadRegistered(jobIndex: index, client: client)
                        } catch {
                            self.jobs[index].status = "error"
                            self.jobs[index].detail = error.localizedDescription
                            self.jobs[index].updatedAt = Date()
                            self.saveJobs()
                            self.banner = "Lỗi tải: \(error.localizedDescription)"
                        }
                    }
                }
            }
        }
    }

    private func register(jobIndex: Int, client: HubClient) async throws {
        var job = jobs[jobIndex]
        job.status = "uploading"
        job.detail = "Đang xếp phiên lên VPS…"
        job.updatedAt = Date()
        jobs[jobIndex] = job
        saveJobs()

        var attempt = 0
        while true {
            do {
                let session = try await client.initUpload(
                    name: job.fileName,
                    size: job.sizeBytes,
                    clientKey: job.clientKey
                )
                job.uploadId = session.upload_id
                job.chunkSize = session.chunk_size
                job.chunksTotal = session.chunks_total
                job.received = session.received
                job.status = "registered"
                job.detail = session.resumed == true
                    ? "Đã xếp phiên (resume) — chờ gửi dữ liệu"
                    : "Đã xếp phiên trên VPS — chờ gửi dữ liệu"
                job.updatedAt = Date()
                jobs[jobIndex] = job
                saveJobs()
                return
            } catch {
                attempt += 1
                let text = error.localizedDescription.lowercased()
                let retryable = text.contains("507") || text.contains("503") || text.contains("đầy") || text.contains("chỗ")
                if !retryable || attempt >= 40 { throw error }
                job.detail = "Chờ chỗ trống trên VPS… lần \(attempt)"
                job.updatedAt = Date()
                jobs[jobIndex] = job
                saveJobs()
                try await Task.sleep(nanoseconds: UInt64(min(90, 15 + attempt * 2)) * 1_000_000_000)
            }
        }
    }

    private func uploadRegistered(jobIndex: Int, client: HubClient) async throws {
        var job = jobs[jobIndex]
        guard let uploadId = job.uploadId else {
            throw HubError.message("Thiếu upload_id — xếp phiên lại")
        }
        job.status = "uploading"
        job.detail = "Đang gửi dữ liệu…"
        job.updatedAt = Date()
        jobs[jobIndex] = job
        saveJobs()

        let fileURL = URL(fileURLWithPath: job.localPath)
        let handle = try FileHandle(forReadingFrom: fileURL)
        defer { try? handle.close() }

        let done = Set(job.received)
        for index in 0..<job.chunksTotal where !done.contains(index) {
            let start = UInt64(index) * UInt64(job.chunkSize)
            try handle.seek(toOffset: start)
            let end = min(job.sizeBytes, Int64(start) + Int64(job.chunkSize))
            let length = Int(end - Int64(start))
            guard let data = try handle.read(upToCount: length), data.count == length else {
                throw HubError.message("Không đọc được mảnh \(index)")
            }
            let part = chunkTemp.appendingPathComponent("\(job.id)-\(index).part")
            try data.write(to: part, options: .atomic)
            let url = try client.chunkURL(uploadId: uploadId, index: index)
            var attempt = 0
            while true {
                do {
                    try await BackgroundUploader.shared.uploadChunk(
                        fileURL: part,
                        to: url,
                        authHeader: client.authHeader()
                    )
                    break
                } catch {
                    attempt += 1
                    if attempt >= 6 { throw error }
                    try await Task.sleep(nanoseconds: UInt64(min(15, attempt * 2)) * 1_000_000_000)
                }
            }
            try? FileManager.default.removeItem(at: part)
            job.received.append(index)
            let sent = Set(job.received).count
            job.detail = "Đã gửi \(sent)/\(job.chunksTotal) mảnh"
            job.updatedAt = Date()
            jobs[jobIndex] = job
            saveJobs()
        }

        let complete = try await client.complete(uploadId: uploadId)
        job.status = complete.duplicate == true ? "duplicate" : "done"
        if complete.duplicate == true {
            job.detail = "Đã có trong hàng đợi VPS"
        } else if let videoId = complete.id {
            job.detail = "Đã vào hàng đợi đọc (#\(videoId))"
        } else {
            job.detail = "Đã vào hàng đợi đọc"
        }
        job.updatedAt = Date()
        jobs[jobIndex] = job
        saveJobs()
    }
}
