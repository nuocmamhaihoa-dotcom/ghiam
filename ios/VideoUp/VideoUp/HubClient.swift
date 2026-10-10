import Foundation

enum HubError: LocalizedError {
    case badURL
    case http(Int, String)
    case decode
    case message(String)

    var errorDescription: String? {
        switch self {
        case .badURL: return "Địa chỉ hub không hợp lệ."
        case .http(let code, let detail): return detail.isEmpty ? "Lỗi HTTP \(code)" : detail
        case .decode: return "Máy chủ trả lời không đọc được."
        case .message(let text): return text
        }
    }
}

struct HubClient {
    var settings: HubSettings

    private func url(_ path: String) throws -> URL {
        guard let base = URL(string: settings.normalizedBase) else { throw HubError.badURL }
        guard let final = URL(string: path, relativeTo: base)?.absoluteURL else { throw HubError.badURL }
        return final
    }

    private func authorized(_ request: inout URLRequest) {
        let token = settings.token.trimmingCharacters(in: .whitespacesAndNewlines)
        if !token.isEmpty {
            request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        }
    }

    func initUpload(name: String, size: Int64, clientKey: String) async throws -> UploadSessionDTO {
        var request = URLRequest(url: try url("/v1/videos/uploads"))
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        authorized(&request)
        let body: [String: Any] = [
            "name": name,
            "size_bytes": size,
            "device": settings.deviceName,
            "client_key": clientKey,
        ]
        request.httpBody = try JSONSerialization.data(withJSONObject: body)
        let (data, response) = try await URLSession.shared.data(for: request)
        try throwIfNeeded(response, data: data)
        do {
            return try JSONDecoder().decode(UploadSessionDTO.self, from: data)
        } catch {
            throw HubError.decode
        }
    }

    func status(uploadId: String) async throws -> UploadSessionDTO {
        var request = URLRequest(url: try url("/v1/videos/uploads/\(uploadId)"))
        authorized(&request)
        let (data, response) = try await URLSession.shared.data(for: request)
        try throwIfNeeded(response, data: data)
        return try JSONDecoder().decode(UploadSessionDTO.self, from: data)
    }

    func complete(uploadId: String) async throws -> CompleteDTO {
        var request = URLRequest(url: try url("/v1/videos/uploads/\(uploadId)/complete"))
        request.httpMethod = "POST"
        authorized(&request)
        let (data, response) = try await URLSession.shared.data(for: request)
        try throwIfNeeded(response, data: data)
        return try JSONDecoder().decode(CompleteDTO.self, from: data)
    }

    func chunkURL(uploadId: String, index: Int) throws -> URL {
        try url("/v1/videos/uploads/\(uploadId)/chunks/\(index)")
    }

    func authHeader() -> String? {
        let token = settings.token.trimmingCharacters(in: .whitespacesAndNewlines)
        return token.isEmpty ? nil : "Bearer \(token)"
    }

    private func throwIfNeeded(_ response: URLResponse, data: Data) throws {
        guard let http = response as? HTTPURLResponse else { return }
        guard (200..<300).contains(http.statusCode) else {
            let detail = (try? JSONSerialization.jsonObject(with: data) as? [String: Any])?["detail"] as? String ?? ""
            throw HubError.http(http.statusCode, detail)
        }
    }
}
