import CoreImage
import CoreMedia
import ImageIO
import ReplayKit
import Vision

class SampleHandler: RPBroadcastSampleHandler {
    private let lock = NSLock()
    private let queue = DispatchQueue(label: "com.fbpoller.screen.ocr")
    private let context = CIContext()
    private var busy = false
    private var lastAt = Date.distantPast
    private var lastText = ""

    override func broadcastStarted(withSetupInfo setupInfo: [String: NSObject]?) {
        lastText = ""
        if HubStore.hubToken.isEmpty {
            Task { _ = await HubStore.connect() }
        }
    }

    override func processSampleBuffer(_ sampleBuffer: CMSampleBuffer, with sampleBufferType: RPSampleBufferType) {
        guard sampleBufferType == .video else { return }
        lock.lock()
        let skip = busy || Date().timeIntervalSince(lastAt) < 3
        if !skip {
            busy = true
            lastAt = Date()
        }
        lock.unlock()
        guard !skip, let jpeg = jpegData(from: sampleBuffer) else {
            if !skip { finish() }
            return
        }
        recognize(jpeg)
    }

    private func jpegData(from sampleBuffer: CMSampleBuffer) -> Data? {
        guard let pixelBuffer = CMSampleBufferGetImageBuffer(sampleBuffer) else { return nil }
        var image = CIImage(cvPixelBuffer: pixelBuffer)
        if let attachment = CMGetAttachment(
            sampleBuffer,
            key: RPVideoSampleOrientationKey as CFString,
            attachmentModeOut: nil
        ) as? NSNumber,
           let orientation = CGImagePropertyOrientation(rawValue: attachment.uint32Value) {
            image = image.oriented(orientation)
        }
        let extent = image.extent
        let longest = max(extent.width, extent.height)
        if longest > 900 {
            let scale = 900 / longest
            image = image.transformed(by: CGAffineTransform(scaleX: scale, y: scale))
        }
        guard let cgImage = context.createCGImage(image, from: image.extent) else { return nil }
        let data = NSMutableData()
        guard let destination = CGImageDestinationCreateWithData(data, "public.jpeg" as CFString, 1, nil) else {
            return nil
        }
        CGImageDestinationAddImage(
            destination,
            cgImage,
            [kCGImageDestinationLossyCompressionQuality: 0.72] as CFDictionary
        )
        guard CGImageDestinationFinalize(destination) else { return nil }
        return data as Data
    }

    private func recognize(_ data: Data) {
        let request = VNRecognizeTextRequest { [weak self] request, _ in
            let lines = (request.results as? [VNRecognizedTextObservation])?
                .compactMap { $0.topCandidates(1).first?.string } ?? []
            self?.deliver(lines.joined(separator: "\n"))
        }
        request.recognitionLevel = .accurate
        request.recognitionLanguages = ["vi-VN", "en-US"]
        request.usesLanguageCorrection = true
        let handler = VNImageRequestHandler(data: data, options: [:])
        queue.async { [weak self] in
            do {
                try handler.perform([request])
            } catch {
                self?.finish()
            }
        }
    }

    private func deliver(_ text: String) {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        defer { finish() }
        guard !trimmed.isEmpty, trimmed != lastText else { return }
        lastText = trimmed
        HubStore.remember(trimmed)
        Task { await HubStore.postLive(trimmed) }
    }

    private func finish() {
        lock.lock()
        busy = false
        lock.unlock()
    }
}
