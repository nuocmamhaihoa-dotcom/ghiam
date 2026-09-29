import SwiftUI
import UniformTypeIdentifiers

struct RootView: View {
    @StateObject private var model = ContactBookModel()
    @Environment(\.scenePhase) private var scenePhase
    @State private var title = ""
    @State private var paste = ""
    @State private var pickingFile = false
    @State private var confirmDelete = false

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    Text("Dán số hoặc chọn file, rồi bấm Nạp lên iPhone. App tự chia mỗi 5000 số thành một nhóm. Một số chỉ nằm trong một nhóm.")
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                    Text(statusLine)
                        .font(.subheadline.weight(.semibold))

                    TextField("Tên danh bạ", text: $title)
                        .textFieldStyle(.roundedBorder)
                        .textInputAutocapitalization(.words)
                        .disabled(model.busy)

                    TextEditor(text: $paste)
                        .frame(minHeight: 140)
                        .padding(8)
                        .overlay {
                            RoundedRectangle(cornerRadius: 12)
                                .stroke(Color.secondary.opacity(0.35), lineWidth: 1)
                        }
                        .disabled(model.busy)

                    Button("Nạp lên iPhone") {
                        if title.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                            title = "Khach"
                        }
                        model.setTitle(title)
                        Task { await model.runAll(text: paste) }
                    }
                    .buttonStyle(BigButtonStyle())
                    .disabled(model.busy || !canStart)

                    Button("Chọn file") {
                        pickingFile = true
                    }
                    .buttonStyle(.borderless)
                    .frame(maxWidth: .infinity)
                    .disabled(model.busy)

                    if !model.library.books.isEmpty {
                        Text("Danh bạ")
                            .font(.headline)
                        ForEach(model.library.books) { book in
                            BookRow(
                                book: book,
                                active: book.id == model.library.activeBookID
                            ) {
                                model.selectBook(book.id)
                            }
                        }
                    }

                    if model.access == .denied || model.access == .limited {
                        Text("Cần quyền Danh bạ đầy đủ. Vào Cài đặt, chọn Cho phép đầy đủ. App không hỏi lại.")
                            .font(.footnote)
                            .foregroundStyle(.secondary)
                        Button("Mở Cài đặt") {
                            guard let url = URL(string: UIApplication.openSettingsURLString) else { return }
                            UIApplication.shared.open(url)
                        }
                        .buttonStyle(BigButtonStyle(prominent: false))
                    }

                    if !model.message.isEmpty {
                        Text(model.message)
                            .font(.subheadline)
                            .foregroundStyle(model.messageIsError ? Color.red : Color.primary)
                            .padding(12)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .background(
                                (model.messageIsError ? Color.red : Color.accentColor).opacity(0.12),
                                in: RoundedRectangle(cornerRadius: 12)
                            )
                    }

                    if model.busy {
                        ProgressView("Đang xử lý")
                            .frame(maxWidth: .infinity)
                    }
                }
                .padding(20)
            }
            .navigationTitle("Danh bạ")
            .navigationBarTitleDisplayMode(.inline)
            .safeAreaInset(edge: .bottom) {
                if model.activeBook?.onPhone == true {
                    Button(deleteTitle) {
                        confirmDelete = true
                    }
                    .buttonStyle(BigButtonStyle(prominent: false))
                    .disabled(model.busy)
                    .padding(.horizontal, 20)
                    .padding(.top, 10)
                    .padding(.bottom, 8)
                    .background(.ultraThinMaterial)
                }
            }
        }
        .onAppear {
            if title.isEmpty {
                let saved = model.library.title.trimmingCharacters(in: .whitespacesAndNewlines)
                title = saved.isEmpty ? "Khach" : saved
            }
            model.refreshAccess()
        }
        .onChange(of: scenePhase) { _, phase in
            if phase == .active {
                model.refreshAccess()
            }
        }
        .fileImporter(
            isPresented: $pickingFile,
            allowedContentTypes: [.plainText, .commaSeparatedText, .vCard, .json, .text],
            allowsMultipleSelection: false
        ) { result in
            openFile(result)
        }
        .confirmationDialog("Xoá danh bạ đang dùng trên iPhone?", isPresented: $confirmDelete, titleVisibility: .visible) {
            Button("Xoá", role: .destructive) {
                Task { await model.deleteActiveFromPhone() }
            }
            Button("Huỷ", role: .cancel) {}
        } message: {
            Text(deletePrompt)
        }
    }

    private var canStart: Bool {
        let pending = !paste.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
        return pending || !model.library.source.isEmpty || !model.library.books.isEmpty
    }

    private var statusLine: String {
        let books = model.library.books
        if books.isEmpty {
            return "Tên mặc định Khach. Dán số, rồi bấm Nạp lên iPhone."
        }
        let ready = books.filter(\.onPhone).count
        if let active = model.activeBook, active.onPhone {
            return "Đang dùng \(active.name). \(ready)/\(books.count) nhóm trên iPhone."
        }
        return "\(books.count) danh bạ đã chia. Bấm Nạp lên iPhone."
    }

    private var deleteTitle: String {
        if let book = model.activeBook, book.onPhone {
            return "Xoá \(book.name)"
        }
        return "Xoá danh bạ đang dùng"
    }

    private var deletePrompt: String {
        guard let book = model.activeBook else { return "Chọn một danh bạ." }
        return "Xoá nhóm \(book.name) trên iPhone. Các danh bạ khác giữ nguyên."
    }

    private func openFile(_ result: Result<[URL], Error>) {
        switch result {
        case .failure:
            model.note("Không mở được file.", error: true)
        case .success(let urls):
            guard let url = urls.first else { return }
            let allowed = url.startAccessingSecurityScopedResource()
            defer {
                if allowed {
                    url.stopAccessingSecurityScopedResource()
                }
            }
            guard let data = try? Data(contentsOf: url), data.count <= 12_000_000,
                  let text = ImportParser.text(from: data)
            else {
                model.note("File quá lớn hoặc không đọc được.", error: true)
                return
            }
            paste = text
            if title.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                title = "Khach"
            }
            model.setTitle(title)
            Task { await model.runAll(text: text) }
        }
    }
}

private struct BookRow: View {
    var book: PhoneBook
    var active: Bool
    var onSelect: () -> Void

    var body: some View {
        Button(action: onSelect) {
            HStack(spacing: 12) {
                Image(systemName: active ? "checkmark.circle.fill" : "circle")
                    .font(.title3)
                    .foregroundStyle(active ? Color.accentColor : Color.secondary)
                VStack(alignment: .leading, spacing: 2) {
                    Text(book.name)
                        .font(.body.weight(.semibold))
                        .foregroundStyle(.primary)
                    Text("\(book.entries.count) số")
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                }
                Spacer()
                Text(book.onPhone ? "Trên iPhone" : "Chưa nạp")
                    .font(.caption.weight(.semibold))
                    .padding(.horizontal, 8)
                    .padding(.vertical, 4)
                    .background(book.onPhone ? Color.green.opacity(0.18) : Color.secondary.opacity(0.15), in: Capsule())
            }
            .padding(12)
            .background(Color(.secondarySystemBackground), in: RoundedRectangle(cornerRadius: 12))
        }
        .buttonStyle(.plain)
    }
}

private struct BigButtonStyle: ButtonStyle {
    var prominent = true

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.body.weight(.semibold))
            .frame(maxWidth: .infinity)
            .padding(.vertical, 14)
            .background(
                prominent ? Color.accentColor : Color(.secondarySystemBackground),
                in: RoundedRectangle(cornerRadius: 12)
            )
            .foregroundStyle(prominent ? Color.white : Color.primary)
            .opacity(configuration.isPressed ? 0.75 : 1)
    }
}
