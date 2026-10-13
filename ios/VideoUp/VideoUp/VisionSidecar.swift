import AVFoundation
import Foundation
import Vision

/// App đang mở hay không. Vòng OCR dừng khi người dùng rời app — không đọc nền.
final class ForegroundFlag: @unchecked Sendable {
    static let shared = ForegroundFlag()
    private let lock = NSLock()
    private var active = true

    func setActive(_ value: Bool) {
        lock.lock()
        active = value
        lock.unlock()
    }

    var isActive: Bool {
        lock.lock()
        defer { lock.unlock() }
        return active
    }
}

/// Apple Vision trên iPhone, tiếng Việt + tiếng Anh, chỉ khi app đang mở.
/// Tối đa khoảng 90 khung trải trên thời lượng. Máy chủ vẫn quét 30/60.
enum VisionSidecar {
    static func send(filePath: String, uploadId: String, client: HubClient) async {
        guard ForegroundFlag.shared.isActive else { return }
        let fileURL = URL(fileURLWithPath: filePath)
        let asset = AVURLAsset(url: fileURL)
        let duration: CMTime
        do {
            duration = try await asset.load(.duration)
        } catch {
            return
        }
        let seconds = CMTimeGetSeconds(duration)
        guard seconds.isFinite, seconds > 0.2 else { return }
        let step = max(0.5, seconds / 90.0)
        let generator = AVAssetImageGenerator(asset: asset)
        generator.appliesPreferredTrackTransform = true
        generator.maximumSize = CGSize(width: 720, height: 1280)
        var frames: [[String: Any]] = []
        var taken = 0
        var moment = 0.0
        var posted = 0
        while moment < seconds && taken < 90 {
            if !ForegroundFlag.shared.isActive { break }
            let time = CMTime(seconds: min(moment, max(0, seconds - 0.05)), preferredTimescale: 600)
            do {
                let generated = try await generator.image(at: time)
                let lines = recognize(generated.image)
                if !lines.isEmpty {
                    frames.append(["t": moment, "lines": lines])
                }
            } catch {
                // Một khung hỏng không dừng việc tải video.
            }
            taken += 1
            moment += step
            if frames.count - posted >= 20 {
                await post(client: client, uploadId: uploadId, frames: frames)
                posted = frames.count
            }
        }
        if frames.count > posted {
            await post(client: client, uploadId: uploadId, frames: frames)
        }
    }

    private static func recognize(_ image: CGImage) -> [[String: Any]] {
        let request = VNRecognizeTextRequest()
        request.recognitionLevel = .accurate
        request.recognitionLanguages = ["vi-VN", "en-US"]
        request.usesLanguageCorrection = true
        let handler = VNImageRequestHandler(cgImage: image, options: [:])
        do {
            try handler.perform([request])
        } catch {
            return []
        }
        var lines: [[String: Any]] = []
        let observations = request.results ?? []
        for observation in observations {
            guard let candidate = observation.topCandidates(1).first else { continue }
            let text = candidate.string.trimmingCharacters(in: .whitespacesAndNewlines)
            if text.isEmpty { continue }
            lines.append([
                "text": text,
                "conf": Double(candidate.confidence) * 100.0,
            ])
        }
        return lines
    }

    private static func post(client: HubClient, uploadId: String, frames: [[String: Any]]) async {
        let object: [String: Any] = ["frames": frames]
        guard JSONSerialization.isValidJSONObject(object) else { return }
        guard let body = try? JSONSerialization.data(withJSONObject: object) else { return }
        if body.count > 2_000_000 { return }
        do {
            try await client.postVision(uploadId: uploadId, body: body)
        } catch {
            // Thiếu sidecar không chặn tải. Máy chủ vẫn quét video.
        }
    }
}
