import UIKit
import UniformTypeIdentifiers
import Vision

final class ShareViewController: UIViewController {
    override func viewDidAppear(_ animated: Bool) {
        super.viewDidAppear(animated)
        view.backgroundColor = .systemBackground
        Task { await finish() }
    }

    private func finish() async {
        let text = await collect()
        if HubStore.hubToken.isEmpty {
            _ = await HubStore.connect()
        }
        if !text.isEmpty {
            HubStore.remember(text)
            await HubStore.postShare(text)
        }
        await MainActor.run {
            self.extensionContext?.completeRequest(returningItems: nil)
        }
    }

    private func collect() async -> String {
        var parts: [String] = []
        for item in extensionContext?.inputItems as? [NSExtensionItem] ?? [] {
            if let text = item.attributedContentText?.string, !text.isEmpty {
                parts.append(text)
            }
            for provider in item.attachments ?? [] {
                if let value = await loadText(provider, type: .url) {
                    parts.append(value)
                }
                if let value = await loadText(provider, type: .plainText) {
                    parts.append(value)
                }
                if let value = await loadQR(provider) {
                    parts.append(value)
                }
            }
        }
        var seen: Set<String> = []
        var unique: [String] = []
        for part in parts {
            let collapsed = part.split(whereSeparator: \.isWhitespace).joined(separator: " ")
            guard !collapsed.isEmpty, !seen.contains(collapsed) else { continue }
            seen.insert(collapsed)
            unique.append(collapsed)
        }
        return unique.joined(separator: "\n")
    }

    private func loadText(_ provider: NSItemProvider, type: UTType) async -> String? {
        guard provider.hasItemConformingToTypeIdentifier(type.identifier) else { return nil }
        return await withCheckedContinuation { continuation in
            provider.loadItem(forTypeIdentifier: type.identifier, options: nil) { item, _ in
                if let url = item as? URL {
                    continuation.resume(returning: url.absoluteString)
                    return
                }
                if let text = item as? String {
                    continuation.resume(returning: text)
                    return
                }
                if let data = item as? Data, let text = String(data: data, encoding: .utf8) {
                    continuation.resume(returning: text)
                    return
                }
                continuation.resume(returning: nil)
            }
        }
    }

    private func loadQR(_ provider: NSItemProvider) async -> String? {
        guard provider.hasItemConformingToTypeIdentifier(UTType.image.identifier) else { return nil }
        let image: UIImage? = await withCheckedContinuation { continuation in
            provider.loadItem(forTypeIdentifier: UTType.image.identifier, options: nil) { item, _ in
                if let image = item as? UIImage {
                    continuation.resume(returning: image)
                    return
                }
                if let url = item as? URL, let data = try? Data(contentsOf: url), let image = UIImage(data: data) {
                    continuation.resume(returning: image)
                    return
                }
                if let data = item as? Data, let image = UIImage(data: data) {
                    continuation.resume(returning: image)
                    return
                }
                continuation.resume(returning: nil)
            }
        }
        guard let cgImage = image?.cgImage else { return nil }
        return await withCheckedContinuation { continuation in
            let request = VNDetectBarcodesRequest { request, _ in
                let payload = (request.results as? [VNBarcodeObservation])?
                    .compactMap(\.payloadStringValue)
                    .first
                continuation.resume(returning: payload)
            }
            request.symbologies = [.qr]
            let handler = VNImageRequestHandler(cgImage: cgImage, options: [:])
            DispatchQueue.global(qos: .userInitiated).async {
                do {
                    try handler.perform([request])
                } catch {
                    continuation.resume(returning: nil)
                }
            }
        }
    }
}
