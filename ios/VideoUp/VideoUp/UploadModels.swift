import Foundation
import UIKit

struct HubSettings: Codable, Equatable {
    var baseURL: String
    var token: String
    var deviceName: String

    static var `default`: HubSettings {
        HubSettings(
            baseURL: "http://IP-VPS:8088",
            token: "",
            deviceName: UIDevice.current.name
        )
    }

    var normalizedBase: String {
        var text = baseURL.trimmingCharacters(in: .whitespacesAndNewlines)
        while text.hasSuffix("/") { text.removeLast() }
        return text
    }
}

struct QueueJob: Codable, Identifiable, Equatable {
    var id: String
    var fileName: String
    var localPath: String
    var sizeBytes: Int64
    var lastModifiedMs: Int64
    var clientKey: String
    var uploadId: String?
    var chunkSize: Int
    var chunksTotal: Int
    var received: [Int]
    var status: String
    var detail: String
    var createdAt: Date
    var updatedAt: Date

    var progress: Double {
        guard chunksTotal > 0 else { return 0 }
        return Double(Set(received).count) / Double(chunksTotal)
    }
}

struct UploadSessionDTO: Codable {
    var upload_id: String
    var name: String
    var size_bytes: Int64
    var device: String?
    var client_key: String?
    var chunk_size: Int
    var chunks_total: Int
    var received: [Int]
    var received_bytes: Int64?
    var status: String
    var resumed: Bool?
}

struct CompleteDTO: Codable {
    var ok: Bool?
    var duplicate: Bool?
    var id: Int?
    var status: String?
    var name: String?
    var detail: String?
}
