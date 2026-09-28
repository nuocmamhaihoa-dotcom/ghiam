import SwiftUI
import UIKit
import UniformTypeIdentifiers

struct RootView: View {
    @StateObject private var model = ContactBookModel()
    @Environment(\.scenePhase) private var scenePhase

    var body: some View {
        TabView {
            ContactsScreen(model: model)
                .tabItem { Label("Danh bạ", systemImage: "person.crop.rectangle.stack") }
            ImportScreen(model: model)
                .tabItem { Label("Nạp", systemImage: "square.and.arrow.down") }
        }
        .onAppear { model.refreshAccess() }
        .onChange(of: scenePhase) { _, phase in
            if phase == .active {
                model.refreshAccess()
            }
        }
    }
}

private struct ContactsScreen: View {
    @ObservedObject var model: ContactBookModel
    @State private var selection = Set<String>()
    @State private var editing = false
    @State private var pendingDelete: DeleteRequest?

    var body: some View {
        NavigationStack {
            Group {
                if model.access == .granted || model.access == .limited {
                    contactList
                } else {
                    PermissionCard(model: model)
                }
            }
            .navigationTitle("Danh bạ")
            .toolbar { toolbar }
        }
        .alert(
            pendingDelete?.title ?? "Xoá liên hệ?",
            isPresented: deleteShown,
            presenting: pendingDelete
        ) { request in
            Button(request.confirm, role: .destructive) {
                let ids = request.ids
                selection.subtract(ids)
                Task { await model.delete(ids: ids) }
            }
            Button("Huỷ", role: .cancel) {}
        } message: { request in
            Text(request.message)
        }
    }

    private var deleteShown: Binding<Bool> {
        Binding(
            get: { pendingDelete != nil },
            set: { shown in
                if !shown {
                    pendingDelete = nil
                }
            }
        )
    }

    private var contactList: some View {
        VStack(spacing: 0) {
            if model.access == .limited {
                Text("iPhone chỉ chia sẻ một phần danh bạ. Vào Cài đặt và chọn Toàn quyền truy cập để thấy và xoá đủ.")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.horizontal)
                    .padding(.top, 8)
            }
            if model.filtered.isEmpty {
                ContentUnavailableView(
                    model.query.isEmpty ? "Chưa có liên hệ" : "Không thấy liên hệ",
                    systemImage: "person.crop.circle",
                    description: Text(model.query.isEmpty
                        ? "Hãy sang tab Nạp để thêm người vào iPhone."
                        : "Không có tên hoặc số trùng với từ khoá.")
                )
                .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else {
                List(model.filtered, selection: $selection) { person in
                    VStack(alignment: .leading, spacing: 2) {
                        Text(person.name)
                        if !person.phone.isEmpty {
                            Text(person.phone)
                                .font(.subheadline)
                                .foregroundStyle(.secondary)
                        }
                        if person.imported {
                            Text("Đã nạp từ app này")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                    }
                    .swipeActions {
                        Button("Xoá", role: .destructive) {
                            pendingDelete = DeleteRequest(
                                ids: [person.id],
                                title: "Xoá liên hệ?",
                                message: "\(person.name) sẽ mất trên iPhone và trên máy đang đồng bộ iCloud.",
                                confirm: "Xoá"
                            )
                        }
                    }
                }
            }
            statusLine
        }
        .refreshable { model.reload() }
        .environment(\.editMode, .constant(editing ? .active : .inactive))
        .overlay {
            if model.busy {
                ProgressView("Đang xử lý")
                    .padding()
                    .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 12))
            }
        }
    }

    @ViewBuilder
    private var statusLine: some View {
        if !model.message.isEmpty {
            Text(model.message)
                .font(.footnote)
                .foregroundStyle(model.messageIsError ? Color.red : Color.secondary)
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.horizontal)
                .padding(.vertical, 8)
        }
    }

    @ToolbarContentBuilder
    private var toolbar: some ToolbarContent {
        ToolbarItem(placement: .topBarLeading) {
            if model.access == .granted || model.access == .limited {
                Button(editing ? "Xong" : "Chọn") {
                    editing.toggle()
                    if !editing {
                        selection.removeAll()
                    }
                }
            }
        }
        ToolbarItem(placement: .topBarTrailing) {
            Menu {
                Button("Chọn tất cả") {
                    editing = true
                    selection = Set(model.filtered.map(\.id))
                }
                .disabled(model.filtered.isEmpty)
                Button("Xoá đã chọn", role: .destructive) {
                    askDelete(ids: selection, title: "Xoá liên hệ đã chọn?")
                }
                .disabled(selection.isEmpty || model.busy)
                Button("Xoá liên hệ đã nạp", role: .destructive) {
                    let ids = Set(model.contacts.filter(\.imported).map(\.id))
                    askDelete(ids: ids, title: "Xoá liên hệ app đã nạp?")
                }
                .disabled(model.importedCount == 0 || model.busy)
            } label: {
                Image(systemName: "ellipsis.circle")
            }
            .disabled(!(model.access == .granted || model.access == .limited))
        }
    }

    private func askDelete(ids: Set<String>, title: String) {
        guard !ids.isEmpty else { return }
        pendingDelete = DeleteRequest(
            ids: ids,
            title: title,
            message: "\(ids.count) liên hệ sẽ mất trên iPhone và trên máy đang đồng bộ iCloud.",
            confirm: "Xoá \(ids.count)"
        )
    }
}

private struct DeleteRequest: Identifiable {
    let ids: Set<String>
    let title: String
    let message: String
    let confirm: String

    var id: String { title + "|" + ids.sorted().joined(separator: ",") }
}

private struct ImportScreen: View {
    @ObservedObject var model: ContactBookModel
    @State private var paste = ""
    @State private var showFile = false
    @State private var pending: ImportBatch?

    private var fileTypes: [UTType] {
        var types: [UTType] = [.plainText, .commaSeparatedText, .vCard]
        if let text = UTType(filenameExtension: "txt") {
            types.append(text)
        }
        return types
    }

    var body: some View {
        NavigationStack {
            Form {
                if model.access != .granted && model.access != .limited {
                    Section {
                        PermissionCard(model: model)
                    }
                }
                Section {
                    TextEditor(text: $paste)
                        .frame(minHeight: 160)
                        .font(.body)
                    Button("Chọn file .csv, .txt, .vcf") { showFile = true }
                        .disabled(model.busy)
                    Button("Nạp vào iPhone") { stagePaste() }
                        .disabled(model.busy || paste.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                } header: {
                    Text("Danh sách")
                } footer: {
                    Text("Mỗi dòng một người. Ví dụ: Trần Tùng, 0901234567. Dòng không có số vẫn được nạp.")
                }
                Section {
                    TextField("Địa chỉ hub", text: $model.hubURL)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                        .keyboardType(.URL)
                    SecureField("Token hub", text: $model.hubToken)
                    Button("Lấy từ hub và nạp") {
                        Task { await stageHub() }
                    }
                    .disabled(model.busy || model.hubURL.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                } header: {
                    Text("Tên đã lưu trên hub")
                } footer: {
                    Text("Lấy tên trong danh bạ đã lưu. Nếu một dòng chưa có tên danh bạ thì lấy tên hồ sơ.")
                }
                if !model.message.isEmpty {
                    Section {
                        Text(model.message)
                            .foregroundStyle(model.messageIsError ? Color.red : Color.primary)
                    }
                }
            }
            .navigationTitle("Nạp")
            .scrollDismissesKeyboard(.interactively)
            .onDisappear { model.rememberHub() }
            .overlay {
                if model.busy {
                    ProgressView("Đang xử lý")
                        .padding()
                        .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 12))
                }
            }
        }
        .fileImporter(isPresented: $showFile, allowedContentTypes: fileTypes, allowsMultipleSelection: false) { result in
            openFile(result)
        }
        .alert("Nạp vào iPhone?", isPresented: confirmShown) {
            Button("Nạp \(pending?.drafts.count ?? 0) liên hệ") {
                let drafts = pending?.drafts ?? []
                pending = nil
                Task { await model.importDrafts(drafts) }
            }
            Button("Huỷ", role: .cancel) { pending = nil }
        } message: {
            Text(confirmMessage)
        }
    }

    private var confirmShown: Binding<Bool> {
        Binding(
            get: { pending != nil },
            set: { shown in
                if !shown {
                    pending = nil
                }
            }
        )
    }

    private var confirmMessage: String {
        var text = "Liên hệ mới xuất hiện trong app Danh bạ. Nếu iPhone đang đồng bộ iCloud, các máy khác cũng nhận."
        if pending?.truncated == true {
            text += " Danh sách dài, chỉ nạp 5000 người đầu."
        }
        return text
    }

    private func stagePaste() {
        let batch = ImportParser.parse(text: paste)
        guard !batch.drafts.isEmpty else {
            model.note("Không có liên hệ hợp lệ. Mỗi dòng một tên, hoặc Tên, số điện thoại.", error: true)
            return
        }
        pending = batch
    }

    private func stageHub() async {
        guard let batch = await model.loadHubDrafts() else { return }
        pending = batch
    }

    private func openFile(_ result: Result<[URL], Error>) {
        switch result {
        case .failure:
            model.note("Không mở được file.", error: true)
        case .success(let urls):
            guard let url = urls.first else { return }
            let scoped = url.startAccessingSecurityScopedResource()
            defer {
                if scoped {
                    url.stopAccessingSecurityScopedResource()
                }
            }
            do {
                let data = try Data(contentsOf: url)
                guard data.count <= 2_000_000 else {
                    model.note("File quá lớn.", error: true)
                    return
                }
                var text = String(decoding: data, as: UTF8.self)
                if text.hasPrefix("\u{feff}") {
                    text.removeFirst()
                }
                paste = text
                model.note("Đã mở file. Xem lại rồi bấm Nạp vào iPhone.", error: false)
            } catch {
                model.note("Không đọc được file.", error: true)
            }
        }
    }
}

private struct PermissionCard: View {
    @ObservedObject var model: ContactBookModel
    @Environment(\.openURL) private var openURL

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Nạp và xoá danh bạ trên iPhone này")
                .font(.headline)
            Text("App chỉ đổi danh bạ sau khi bạn bấm Nạp hoặc Xoá. iPhone sẽ hỏi quyền một lần.")
                .foregroundStyle(.secondary)
            if model.access == .denied {
                Button("Mở Cài đặt") {
                    guard let url = URL(string: UIApplication.openSettingsURLString) else { return }
                    openURL(url)
                }
            } else {
                Button("Cho phép truy cập danh bạ") {
                    Task { await model.requestAccess() }
                }
            }
        }
        .buttonStyle(.borderedProminent)
    }
}
