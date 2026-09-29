import SwiftUI
import UniformTypeIdentifiers

struct RootView: View {
    @StateObject private var model = ContactBookModel()
    @Environment(\.scenePhase) private var scenePhase
    @State private var title = ""
    @State private var paste = ""
    @State private var pickingFile = false
    @State private var confirmPush = false
    @State private var confirmDelete = false

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    Text("Mỗi dòng một số, hoặc Tên, số. App chia mỗi 5000 số thành một nhóm. Một số chỉ nằm trong một nhóm.")
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                    Text(statusLine)
                        .font(.subheadline.weight(.semibold))

                    TextField("Tên danh bạ", text: $title)
                        .textFieldStyle(.roundedBorder)
                        .textInputAutocapitalization(.words)
                        .disabled(model.busy)

                    Text("Dán danh sách")
                        .font(.headline)
                    TextEditor(text: $paste)
                        .frame(minHeight: 140)
                        .padding(8)
                        .overlay {
                            RoundedRectangle(cornerRadius: 12)
                                .stroke(Color.secondary.opacity(0.35), lineWidth: 1)
                        }
                        .disabled(model.busy)

                    Button("Nạp hàng loạt") {
                        model.importBulk(text: paste)
                    }
                    .buttonStyle(BigButtonStyle())
                    .disabled(model.busy || paste.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)

                    Button("Chọn file") {
                        pickingFile = true
                    }
                    .buttonStyle(BigButtonStyle(prominent: false))
                    .disabled(model.busy)

                    Button("Chia danh bạ") {
                        model.setTitle(title)
                        let pending = paste.trimmingCharacters(in: .whitespacesAndNewlines)
                        if !pending.isEmpty, model.importBulk(text: pending) == false {
                            return
                        }
                        model.splitBooks()
                    }
                    .buttonStyle(BigButtonStyle())
                    .disabled(model.busy || title.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)

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
                VStack(spacing: 10) {
                    Button(pushTitle) {
                        confirmPush = true
                    }
                    .buttonStyle(BigButtonStyle())
                    .disabled(model.busy || model.library.books.isEmpty)

                    Button(deleteTitle) {
                        confirmDelete = true
                    }
                    .buttonStyle(BigButtonStyle(prominent: false))
                    .disabled(model.busy || model.activeBook?.onPhone != true)
                }
                .padding(.horizontal, 20)
                .padding(.top, 10)
                .padding(.bottom, 8)
                .background(.ultraThinMaterial)
            }
        }
        .onAppear {
            if title.isEmpty {
                title = model.library.title
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
        .confirmationDialog("Nạp mọi danh bạ lên iPhone?", isPresented: $confirmPush, titleVisibility: .visible) {
            Button("Nạp") {
                Task { await model.loadOntoPhone() }
            }
            Button("Huỷ", role: .cancel) {}
        } message: {
            Text("Khi iPhone hỏi, chọn Cho phép đầy đủ. Mỗi cuốn thành một nhóm. Số đã ở nhóm khác sẽ không được thêm.")
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

    private var statusLine: String {
        let books = model.library.books
        let phones = model.library.source.filter { $0.phone?.isEmpty == false }.count
        if books.isEmpty {
            if phones == 0 {
                return "Dán số hoặc chọn file, rồi bấm Chia danh bạ."
            }
            return "Trong phần mềm: \(phones) số. Bấm Chia danh bạ."
        }
        let ready = books.filter(\.onPhone).count
        if let active = model.activeBook, active.onPhone {
            return "\(books.count) danh bạ, \(ready) trên iPhone. Đang dùng: \(active.name)."
        }
        return "\(books.count) danh bạ đã chia. Bấm Nạp vào iPhone."
    }

    private var pushTitle: String {
        let count = model.library.books.count
        if count == 0 {
            return "Nạp vào iPhone"
        }
        return "Nạp \(count) danh bạ vào iPhone"
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
            model.importBulk(text: text)
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
