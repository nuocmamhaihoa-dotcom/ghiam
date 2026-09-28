import Combine
import Contacts
import Foundation
import Security

struct PhoneContact: Identifiable, Hashable {
    var id: String
    var name: String
    var phone: String
    var imported: Bool
}

enum ContactAccess {
    case unknown
    case granted
    case limited
    case denied
}

enum BookError: LocalizedError {
    case hub(String)
    case empty

    var errorDescription: String? {
        switch self {
        case .hub(let text):
            return text
        case .empty:
            return "Không có liên hệ hợp lệ để nạp."
        }
    }
}

@MainActor
final class ContactBookModel: ObservableObject {
    @Published private(set) var contacts: [PhoneContact] = []
    @Published private(set) var access: ContactAccess = .unknown
    @Published private(set) var message = ""
    @Published private(set) var messageIsError = false
    @Published private(set) var busy = false
    @Published var query = ""
    @Published var hubURL = ""
    @Published var hubToken = ""

    private let store = CNContactStore()
    private let ledger = ImportLedger()
    private let hubKey = "danhba.hubURL"
    private var askingForAccess = false

    var filtered: [PhoneContact] {
        let needle = query.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        guard !needle.isEmpty else { return contacts }
        return contacts.filter { contact in
            contact.name.lowercased().contains(needle) || contact.phone.lowercased().contains(needle)
        }
    }

    var importedCount: Int {
        contacts.filter(\.imported).count
    }

    init() {
        hubURL = UserDefaults.standard.string(forKey: hubKey) ?? ""
        hubToken = TokenStore.load()
    }

    func note(_ text: String, error: Bool) {
        message = text
        messageIsError = error
    }

    func rememberHub() {
        let url = hubURL.trimmingCharacters(in: .whitespacesAndNewlines)
        UserDefaults.standard.set(url, forKey: hubKey)
        TokenStore.save(hubToken)
    }

    func refreshAccess() {
        let status = CNContactStore.authorizationStatus(for: .contacts)
        access = Self.kind(status)
        if Self.granted(status) {
            reload()
        }
    }

    func requestAccess() async {
        guard !askingForAccess else { return }
        let current = CNContactStore.authorizationStatus(for: .contacts)
        if Self.granted(current) {
            access = Self.kind(current)
            reload()
            return
        }
        guard current == .notDetermined else {
            access = .denied
            return
        }
        askingForAccess = true
        defer { askingForAccess = false }
        let allowed = await withCheckedContinuation { (continuation: CheckedContinuation<Bool, Never>) in
            store.requestAccess(for: .contacts) { granted, _ in
                continuation.resume(returning: granted)
            }
        }
        let status = CNContactStore.authorizationStatus(for: .contacts)
        access = Self.kind(status)
        if allowed || Self.granted(status) {
            reload()
        }
    }

    func reload() {
        do {
            contacts = try fetchContacts()
        } catch {
            note(Self.describe(error), error: true)
        }
    }

    func loadHubDrafts() async -> ImportBatch? {
        guard !busy else { return nil }
        rememberHub()
        busy = true
        defer { busy = false }
        do {
            let batch = try await HubAPI.fetch(hub: hubURL, token: hubToken)
            if batch.drafts.isEmpty {
                note("Hub không có tên để nạp.", error: true)
                return nil
            }
            return batch
        } catch {
            note(Self.describe(error), error: true)
            return nil
        }
    }

    func importDrafts(_ drafts: [ContactDraft]) async {
        guard !busy else { return }
        let pending = drafts.filter { !ImportParser.cleanName($0.name).isEmpty }
        guard !pending.isEmpty else {
            note("Không có liên hệ hợp lệ để nạp.", error: true)
            return
        }
        let status = CNContactStore.authorizationStatus(for: .contacts)
        guard Self.granted(status) else {
            access = Self.kind(status)
            note("Hãy cho phép quyền Danh bạ trước.", error: true)
            return
        }
        busy = true
        defer { busy = false }
        let existing: Set<String>
        do {
            existing = try existingKeys()
        } catch {
            note(Self.describe(error), error: true)
            return
        }
        var seen = existing
        var accepted: [ContactDraft] = []
        var skipped = 0
        for draft in pending {
            let key = ImportParser.identityKey(draft)
            if seen.contains(key) {
                skipped += 1
                continue
            }
            seen.insert(key)
            accepted.append(draft)
        }
        guard !accepted.isEmpty else {
            note("Tất cả đã có trong danh bạ.", error: false)
            return
        }
        var added = 0
        var identifiers: [String] = []
        var index = 0
        while index < accepted.count {
            let end = min(index + 40, accepted.count)
            let batch = Array(accepted[index ..< end])
            index = end
            do {
                let newIDs = try insert(batch)
                identifiers.append(contentsOf: newIDs)
                added += newIDs.count
                note("Đang nạp \(added)/\(accepted.count)...", error: false)
                await Task.yield()
            } catch {
                ledger.add(identifiers)
                reload()
                note("Đã nạp \(added). \(Self.describe(error))", error: true)
                return
            }
        }
        ledger.add(identifiers)
        reload()
        var text = "Đã nạp \(added) liên hệ."
        if skipped > 0 {
            text += " Bỏ qua \(skipped) người đã có."
        }
        note(text, error: false)
    }

    func delete(ids: Set<String>) async {
        guard !busy else { return }
        let targets = Array(ids)
        guard !targets.isEmpty else { return }
        busy = true
        defer { busy = false }
        var removed = 0
        var index = 0
        while index < targets.count {
            let end = min(index + 40, targets.count)
            let batch = Array(targets[index ..< end])
            index = end
            do {
                removed += try deleteBatch(batch)
                ledger.remove(batch)
            } catch {
                reload()
                note("Đã xoá \(removed). \(Self.describe(error))", error: true)
                return
            }
        }
        reload()
        note("Đã xoá \(removed) liên hệ.", error: false)
    }

    private func fetchContacts() throws -> [PhoneContact] {
        let imported = ledger.ids
        let keys = [
            CNContactIdentifierKey as CNKeyDescriptor,
            CNContactGivenNameKey as CNKeyDescriptor,
            CNContactFamilyNameKey as CNKeyDescriptor,
            CNContactPhoneNumbersKey as CNKeyDescriptor,
        ]
        let request = CNContactFetchRequest(keysToFetch: keys)
        request.sortOrder = .userDefault
        var rows: [PhoneContact] = []
        try store.enumerateContacts(with: request) { contact, _ in
            let name = [contact.givenName, contact.familyName]
                .map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
                .filter { !$0.isEmpty }
                .joined(separator: " ")
            let phones = contact.phoneNumbers.map(\.value.stringValue).filter { !$0.isEmpty }
            rows.append(
                PhoneContact(
                    id: contact.identifier,
                    name: name.isEmpty ? "Không tên" : name,
                    phone: phones.joined(separator: ", "),
                    imported: imported.contains(contact.identifier)
                )
            )
        }
        return rows.sorted { $0.name.localizedStandardCompare($1.name) == .orderedAscending }
    }

    private func existingKeys() throws -> Set<String> {
        let keys = [
            CNContactGivenNameKey as CNKeyDescriptor,
            CNContactFamilyNameKey as CNKeyDescriptor,
            CNContactPhoneNumbersKey as CNKeyDescriptor,
        ]
        let request = CNContactFetchRequest(keysToFetch: keys)
        var keysOnPhone = Set<String>()
        try store.enumerateContacts(with: request) { contact, _ in
            let name = ImportParser.cleanName(
                [contact.givenName, contact.familyName]
                    .filter { !$0.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }
                    .joined(separator: " ")
            )
            guard !name.isEmpty else { return }
            let phones = contact.phoneNumbers.compactMap { ImportParser.normalizePhone($0.value.stringValue) }
            if phones.isEmpty {
                keysOnPhone.insert(ImportParser.nameKey(name) + "|")
                return
            }
            for phone in phones {
                keysOnPhone.insert(ImportParser.nameKey(name) + "|" + phone)
            }
        }
        return keysOnPhone
    }

    private func insert(_ drafts: [ContactDraft]) throws -> [String] {
        let request = CNSaveRequest()
        var created: [CNMutableContact] = []
        for draft in drafts {
            let contact = CNMutableContact()
            contact.givenName = draft.name
            if let phone = draft.phone {
                contact.phoneNumbers = [
                    CNLabeledValue(label: CNLabelPhoneNumberMobile, value: CNPhoneNumber(stringValue: phone)),
                ]
            }
            if let facebook = draft.facebook {
                let profile = CNSocialProfile(
                    urlString: nil,
                    username: facebook,
                    userIdentifier: nil,
                    service: CNSocialProfileServiceFacebook
                )
                contact.socialProfiles = [
                    CNLabeledValue(label: CNSocialProfileServiceFacebook, value: profile),
                ]
            }
            request.add(contact, toContainerWithIdentifier: nil)
            created.append(contact)
        }
        try store.execute(request)
        return created.map(\.identifier).filter { !$0.isEmpty }
    }

    private func deleteBatch(_ ids: [String]) throws -> Int {
        let predicate = CNContact.predicateForContacts(withIdentifiers: ids)
        let found = try store.unifiedContacts(
            matching: predicate,
            keysToFetch: [CNContactIdentifierKey as CNKeyDescriptor]
        )
        guard !found.isEmpty else { return 0 }
        let request = CNSaveRequest()
        for contact in found {
            guard let mutable = contact.mutableCopy() as? CNMutableContact else { continue }
            request.delete(mutable)
        }
        try store.execute(request)
        return found.count
    }

    private static func granted(_ status: CNAuthorizationStatus) -> Bool {
        if status == .authorized {
            return true
        }
        if #available(iOS 18.0, *) {
            return status == .limited
        }
        return false
    }

    private static func kind(_ status: CNAuthorizationStatus) -> ContactAccess {
        if status == .authorized {
            return .granted
        }
        if status == .denied || status == .restricted {
            return .denied
        }
        if status == .notDetermined {
            return .unknown
        }
        if #available(iOS 18.0, *) {
            if status == .limited {
                return .limited
            }
        }
        return .denied
    }

    private static func describe(_ error: Error) -> String {
        if let book = error as? BookError, let text = book.errorDescription {
            return text
        }
        if error is ImportParserError {
            return "Hub trả về dữ liệu không đọc được."
        }
        let ns = error as NSError
        if ns.domain == CNErrorDomain {
            return "Danh bạ từ chối thay đổi. Hãy kiểm tra quyền trong Cài đặt."
        }
        if ns.domain == NSURLErrorDomain {
            return "Không kết nối được hub."
        }
        return "Không thực hiện được."
    }
}

private enum HubAPI {
    static func fetch(hub: String, token: String) async throws -> ImportBatch {
        guard let url = ImportParser.peopleURL(from: hub) else {
            throw BookError.hub("Địa chỉ hub không hợp lệ. Máy ngoài mạng nội bộ cần https.")
        }
        var request = URLRequest(url: url)
        request.timeoutInterval = 30
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        let trimmed = token.trimmingCharacters(in: .whitespacesAndNewlines)
        if !trimmed.isEmpty {
            request.setValue("Bearer \(trimmed)", forHTTPHeaderField: "Authorization")
        }
        let (data, response) = try await URLSession.shared.data(for: request)
        guard let http = response as? HTTPURLResponse else {
            throw BookError.hub("Hub không trả lời.")
        }
        if http.statusCode == 401 {
            throw BookError.hub("Sai token hoặc hub từ chối.")
        }
        guard (200 ..< 300).contains(http.statusCode) else {
            throw BookError.hub("Hub trả về lỗi \(http.statusCode).")
        }
        guard data.count <= 2_000_000 else {
            throw BookError.hub("Danh sách từ hub quá lớn.")
        }
        return try ImportParser.parsePeople(data: data)
    }
}

private struct ImportLedger {
    private let key = "danhba.imported.ids"

    var ids: Set<String> {
        Set(UserDefaults.standard.stringArray(forKey: key) ?? [])
    }

    func add(_ newIDs: [String]) {
        guard !newIDs.isEmpty else { return }
        var all = ids
        all.formUnion(newIDs)
        UserDefaults.standard.set(Array(all), forKey: key)
    }

    func remove(_ gone: [String]) {
        guard !gone.isEmpty else { return }
        var all = ids
        all.subtract(gone)
        UserDefaults.standard.set(Array(all), forKey: key)
    }
}

private enum TokenStore {
    private static let service = "local.fbpoller.danhba"
    private static let account = "hub-token"

    static func load() -> String {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
            kSecReturnData as String: true,
            kSecMatchLimit as String: kSecMatchLimitOne,
        ]
        var item: CFTypeRef?
        let status = SecItemCopyMatching(query as CFDictionary, &item)
        guard status == errSecSuccess, let data = item as? Data, let text = String(data: data, encoding: .utf8) else {
            return ""
        }
        return text
    }

    static func save(_ token: String) {
        let base: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
        ]
        SecItemDelete(base as CFDictionary)
        let trimmed = token.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty, let data = trimmed.data(using: .utf8) else { return }
        var add = base
        add[kSecValueData as String] = data
        add[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
        SecItemAdd(add as CFDictionary, nil)
    }
}
