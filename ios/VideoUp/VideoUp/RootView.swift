import PhotosUI
import SwiftUI
import UniformTypeIdentifiers

struct RootView: View {
    @EnvironmentObject private var queue: UploadQueue
    @State private var showImporter = false
    @State private var pickerItems: [PhotosPickerItem] = []

    var body: some View {
        NavigationStack {
            Form {
                Section("Máy chủ VPS") {
                    TextField("URL hub (http://IP:8088)", text: $queue.settings.baseURL)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                        .keyboardType(.URL)
                    SecureField("Token", text: $queue.settings.token)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                    TextField("Tên máy iPhone", text: $queue.settings.deviceName)
                }

                Section("Thêm video vào hàng chờ máy") {
                    Text("Chọn nhiều video — app xếp hàng local, cắt khúc gửi lên VPS. Khóa máy vẫn gửi tiếp (URLSession nền).")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                    PhotosPicker(
                        selection: $pickerItems,
                        maxSelectionCount: 40,
                        matching: .videos
                    ) {
                        Label("Chọn từ Ảnh", systemImage: "photo.on.rectangle.angled")
                    }
                    Button {
                        showImporter = true
                    } label: {
                        Label("Chọn file Files", systemImage: "folder")
                    }
                    if queue.busy {
                        ProgressView("Đang tải lên VPS…")
                    }
                    if !queue.banner.isEmpty {
                        Text(queue.banner).foregroundStyle(.red).font(.footnote)
                    }
                }

                Section("Hàng chờ trên iPhone (\(queue.jobs.count))") {
                    if queue.jobs.isEmpty {
                        Text("Chưa có video.")
                            .foregroundStyle(.secondary)
                    }
                    ForEach(queue.jobs) { job in
                        VStack(alignment: .leading, spacing: 4) {
                            Text(job.fileName).font(.headline)
                            Text(job.detail).font(.caption).foregroundStyle(.secondary)
                            ProgressView(value: job.progress)
                            HStack {
                                Text(statusLabel(job.status))
                                Spacer()
                                Text(byteLabel(job.sizeBytes))
                            }
                            .font(.caption2)
                            .foregroundStyle(.secondary)
                        }
                        .padding(.vertical, 4)
                    }
                }

                Section {
                    Button("Thử lại video lỗi") { queue.retryFailed() }
                    Button("Xóa video đã xong khỏi máy") { queue.clearFinished() }
                    Link("Xem hàng đợi / kết quả trên VPS", destination: URL(string: queue.settings.normalizedBase + "/")!)
                }
            }
            .navigationTitle("VideoUp")
            .fileImporter(
                isPresented: $showImporter,
                allowedContentTypes: [.movie, .mpeg4Movie, .quickTimeMovie, .avi],
                allowsMultipleSelection: true
            ) { result in
                if case .success(let urls) = result {
                    queue.enqueue(urls: urls)
                }
            }
            .onChange(of: pickerItems) { _, items in
                Task { await importPhotos(items) }
            }
        }
    }

    private func importPhotos(_ items: [PhotosPickerItem]) async {
        var urls: [URL] = []
        for item in items {
            if let movie = try? await item.loadTransferable(type: VideoFile.self) {
                urls.append(movie.url)
            }
        }
        if !urls.isEmpty {
            queue.enqueue(urls: urls)
        }
        pickerItems = []
    }

    private func statusLabel(_ status: String) -> String {
        switch status {
        case "queued": return "Chờ trên máy"
        case "uploading": return "Đang gửi"
        case "done": return "Đã lên VPS"
        case "duplicate": return "Trùng — đã có"
        case "error": return "Lỗi"
        default: return status
        }
    }

    private func byteLabel(_ value: Int64) -> String {
        ByteCountFormatter.string(fromByteCount: value, countStyle: .file)
    }
}

struct VideoFile: Transferable {
    let url: URL

    static var transferRepresentation: some TransferRepresentation {
        FileRepresentation(contentType: .movie) { movie in
            SentTransferredFile(movie.url)
        } importing: { received in
            let temp = FileManager.default.temporaryDirectory
                .appendingPathComponent(UUID().uuidString + "-" + received.file.lastPathComponent)
            try FileManager.default.copyItem(at: received.file, to: temp)
            return VideoFile(url: temp)
        }
    }
}
