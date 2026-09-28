import SwiftUI
import UIKit
import UniformTypeIdentifiers

struct RootView: View {
    @StateObject private var model = ContactBookModel()
    @Environment(\.scenePhase) private var scenePhase

    var body: some View {
        Group {
            if model.access == .granted || model.access == .limited {
                TabView {
                    ImportScreen(model: model)
                        .tabItem { Label("Nạp", systemImage: "square.and.arrow.down") }
                    ContactsScreen(model: model)
                        .tabItem { Label("Danh bạ", systemImage: "person.crop.rectangle.stack") }
                }
            } else {
                PermissionGate(model: model)
            }
        }
        .onAppear { model.refreshAccess() }
        .onChange(of: scenePhase) { _, phase in
            if phase == .active {
                model.refreshAccess()
            }
        }
    }
}

private struct PermissionGate: View {
    @ObservedObject var model: ContactBookModel
    @Environment(\.openURL) private var openURL

    var body: some View {
        VStack(spacing: 20) {
            Spacer()
            Image(systemName: "person.crop.circle.badge.checkmark")
                .font(.system(size: 64))
                .foregroundStyle(.primary)
            Text("Danh bạ trên iPhone này")
                .font(.title2.bold())
                .multilineTextAlignment(.center)
            Text(model.access == .denied
                ? "Quyền Danh bạ đã được trả lời. App không hỏi lại. Bật Danh bạ trong Cài đặt để nạp hoặc xoá."
                : "iPhone hỏi quyền Danh bạ đúng một lần. Sau đó mở app sẽ không hỏi nữa.")
                .font(.body)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
            Spacer()
            if model.access == .denied {
                Button("Mở Cài đặt") { openSettings() }
                    .buttonStyle(BigButtonStyle())
            } else {
                Button("Cho phép danh bạ") {
                    Task { await model.requestAccess() }
                }
                .buttonStyle(BigButtonStyle())
            }
        }
        .padding(24)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(Color(.systemGroupedBackground))
    }

    private func openSettings() {
        guard let url = URL(string: UIApplication.openSettingsURLString) else { return }
        openURL(url)
    }
}

private struct ImportScreen: View {
    @ObservedObject var model: ContactBookModel
    @State private var paste = ""
    @State private var showFile = false
    @State private var showHub = false
    @State private var pending: ImportBatch?

    private var fileTypes: [UTType] {
        [.plainText, .commaSeparatedText, .vCard]
    }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    Text("Mỗi dòng một người. Có số hoặc không có số đều được.")
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                    editor
                    if showHub {
                        hubFields
                    } else {
                        Button("Lấy tên đã lưu trên hub") { showHub = true }
                            .font(.body.weight(.semibold))
                    }
                    if !model.message.isEmpty {
                        Text(model.message)
                            .font(.subheadline.weight(.semibold))
                            .foregroundStyle(model.messageIsError ? Color.red : Color.green)
                            .frame(maxWidth: .infinity, alignment: .leading)
                    }
                }
                .padding(20)
            }
            .background(Color(.systemGroupedBackground))
            .navigationTitle("Nạp")
            .scrollDismissesKeyboard(.interactively)
            .safeAreaInset(edge: .bottom) { importBar }
            .onDisappear { model.rememberHub() }
            .overlay {
                if model.busy {
                    ProgressView("Đang xử lý")
                        .padding(20)
                        .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 14))
                }
            }
        }
        .fileImporter(isPresented: $showFile, allowedContentTypes: fileTypes, allowsMultipleSelection: false) { result in
            openFile(result)
        }
        .alert("Nạp vào iPhone?", isPresented: confirmShown) {
            Button("Nạp \(pending?.drafts.count ?? 0) người") {
                let drafts = pending?.drafts ?? []
                pending = nil
                Task { await model.importDrafts(drafts) }
            }
            Button("Huỷ", role: .cancel) { pending = nil }
        } message: {
            Text(confirmMessage)
        }
    }

    private var editor: some View {
        ZStack(alignment: .topLeading) {
            TextEditor(text: $paste)
                .frame(minHeight: 180)
                .padding(8)
                .scrollContentBackground(.hidden)
                .background(Color(.secondarySystemGroupedBackground), in: RoundedRectangle(cornerRadius: 14))
            if paste.isEmpty {
                Text("Trần Tùng, 0901234567\nA Tùng Bán Gạch")
                    .foregroundStyle(.tertiary)
                    .padding(.top, 16)
                    .padding(.leading, 13)
                    .allowsHitTesting(false)
            }
        }
    }

    private var hubFields: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Địa chỉ hub và token. Bấm nút bên dưới để lấy tên rồi xác nhận.")
                .font(.subheadline)
                .foregroundStyle(.secondary)
            TextField("https://hub", text: $model.hubURL)
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled()
                .keyboardType(.URL)
                .padding(12)
                .background(Color(.secondarySystemGroupedBackground), in: RoundedRectangle(cornerRadius: 12))
            SecureField("Token", text: $model.hubToken)
                .padding(12)
                .background(Color(.secondarySystemGroupedBackground), in: RoundedRectangle(cornerRadius: 12))
            Button("Lấy từ hub") {
                hideKeyboard()
                Task { await stageHub() }
            }
            .buttonStyle(BigButtonStyle(filled: false))
            .disabled(model.busy || model.hubURL.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
        }
    }

    private var importBar: some View {
        VStack(spacing: 10) {
            Button("Nạp vào iPhone") {
                hideKeyboard()
                stagePaste()
            }
            .buttonStyle(BigButtonStyle())
            .disabled(model.busy || paste.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            Button("Chọn file") { showFile = true }
                .buttonStyle(BigButtonStyle(filled: false))
                .disabled(model.busy)
        }
        .padding(.horizontal, 20)
        .padding(.top, 10)
        .padding(.bottom, 8)
        .background(.bar)
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
        var text = "Người mới sẽ vào app Danh bạ của iPhone."
        if pending?.truncated == true {
            text += " Danh sách dài, chỉ nạp 5000 người đầu."
        }
        return text
    }

    private func stagePaste() {
        let batch = ImportParser.parse(text: paste)
        guard !batch.drafts.isEmpty else {
            model.note("Chưa có người hợp lệ. Mỗi dòng một tên, hoặc Tên, số điện thoại.", error: true)
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
                model.note("Đã mở file. Bấm Nạp vào iPhone.", error: false)
            } catch {
                model.note("Không đọc được file.", error: true)
            }
        }
    }

    private func hideKeyboard() {
        UIApplication.shared.sendAction(#selector(UIResponder.resignFirstResponder), to: nil, from: nil, for: nil)
    }
}

private struct ContactsScreen: View {
    @ObservedObject var model: ContactBookModel
    @State private var selection = Set<String>()
    @State private var pendingDelete: DeleteRequest?
    @Environment(\.openURL) private var openURL

    private var allSelected: Bool {
        !model.filtered.isEmpty && model.filtered.allSatisfy { selection.contains($0.id) }
    }

    var body: some View {
        NavigationStack {
            Group {
                if model.filtered.isEmpty {
                    ContentUnavailableView(
                        model.query.isEmpty ? "Chưa có liên hệ" : "Không thấy liên hệ",
                        systemImage: "person.crop.circle",
                        description: Text(model.query.isEmpty
                            ? "Sang tab Nạp để thêm người."
                            : "Không có tên hoặc số trùng với từ khoá.")
                    )
                } else {
                    List(model.filtered) { person in
                        Button {
                            toggle(person.id)
                        } label: {
                            HStack(spacing: 12) {
                                Image(systemName: selection.contains(person.id) ? "checkmark.circle.fill" : "circle")
                                    .font(.title3)
                                    .foregroundStyle(selection.contains(person.id) ? Color.accentColor : Color.secondary)
                                VStack(alignment: .leading, spacing: 2) {
                                    Text(person.name)
                                        .foregroundStyle(.primary)
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
                                Spacer(minLength: 0)
                            }
                        }
                        .buttonStyle(.plain)
                        .swipeActions {
                            Button("Xoá", role: .destructive) {
                                pendingDelete = DeleteRequest(
                                    ids: [person.id],
                                    title: "Xoá \(person.name)?",
                                    message: "Liên hệ sẽ mất trên iPhone và trên máy đang đồng bộ iCloud.",
                                    confirm: "Xoá"
                                )
                            }
                        }
                    }
                }
            }
            .navigationTitle("Danh bạ")
            .searchable(text: $model.query, prompt: "Tìm tên hoặc số")
            .refreshable { model.reload() }
            .safeAreaInset(edge: .bottom) { deleteBar }
            .overlay {
                if model.busy {
                    ProgressView("Đang xử lý")
                        .padding(20)
                        .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 14))
                }
            }
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

    private var deleteBar: some View {
        VStack(alignment: .leading, spacing: 8) {
            if model.access == .limited {
                Button("iPhone chỉ chia một phần danh bạ. Bấm để mở Cài đặt.") {
                    guard let url = URL(string: UIApplication.openSettingsURLString) else { return }
                    openURL(url)
                }
                .font(.footnote)
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            if !model.message.isEmpty {
                Text(model.message)
                    .font(.footnote.weight(.semibold))
                    .foregroundStyle(model.messageIsError ? Color.red : Color.green)
            }
            HStack(spacing: 10) {
                Button(allSelected ? "Bỏ chọn" : "Chọn tất cả") {
                    if allSelected {
                        selection.subtract(model.filtered.map(\.id))
                    } else {
                        selection.formUnion(model.filtered.map(\.id))
                    }
                }
                .buttonStyle(BigButtonStyle(filled: false))
                .disabled(model.filtered.isEmpty || model.busy)
                Button(selection.isEmpty ? "Xoá" : "Xoá \(selection.count)") {
                    askDelete(ids: selection, title: "Xoá \(selection.count) liên hệ?")
                }
                .buttonStyle(BigButtonStyle())
                .disabled(selection.isEmpty || model.busy)
            }
            if model.importedCount > 0 {
                Button("Xoá \(model.importedCount) người app này đã nạp") {
                    let ids = Set(model.contacts.filter(\.imported).map(\.id))
                    askDelete(ids: ids, title: "Xoá người app đã nạp?")
                }
                .font(.subheadline.weight(.semibold))
                .frame(maxWidth: .infinity)
                .disabled(model.busy)
            }
        }
        .padding(.horizontal, 16)
        .padding(.top, 10)
        .padding(.bottom, 8)
        .background(.bar)
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

    private func toggle(_ id: String) {
        if selection.contains(id) {
            selection.remove(id)
        } else {
            selection.insert(id)
        }
    }

    private func askDelete(ids: Set<String>, title: String) {
        guard !ids.isEmpty else { return }
        pendingDelete = DeleteRequest(
            ids: ids,
            title: title,
            message: "Liên hệ sẽ mất trên iPhone và trên máy đang đồng bộ iCloud.",
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

private struct BigButtonStyle: ButtonStyle {
    var filled = true

    func makeBody(configuration: Configuration) -> some View {
        BigButtonLabel(configuration: configuration, filled: filled)
    }
}

private struct BigButtonLabel: View {
    let configuration: ButtonStyleConfiguration
    var filled: Bool
    @Environment(\.isEnabled) private var isEnabled

    var body: some View {
        configuration.label
            .font(.body.weight(.semibold))
            .frame(maxWidth: .infinity)
            .frame(minHeight: 52)
            .foregroundStyle(filled ? Color.white : Color.primary)
            .background(background, in: RoundedRectangle(cornerRadius: 14))
            .opacity(isEnabled ? 1 : 0.4)
    }

    private var background: Color {
        if filled {
            return Color(red: 0.07, green: 0.08, blue: 0.11).opacity(configuration.isPressed ? 0.75 : 1)
        }
        return Color(.secondarySystemFill)
    }
}
