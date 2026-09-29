import Combine
import Contacts
import Foundation

enum ContactAccess {
    case unknown
    case granted
    case limited
    case denied
}

enum BookError: LocalizedError {
    case empty
    case missingTitle
    case missingSource
    case noActiveBook
    case noContainer

    var errorDescription: String? {
        switch self {
        case .empty:
            return "Không có số điện thoại hợp lệ."
        case .missingTitle:
            return "Nhập tên danh bạ trước khi chia."
        case .missingSource:
            return "Hãy nạp danh bạ hàng loạt vào phần mềm trước."
        case .noActiveBook:
            return "Chọn danh bạ đang dùng."
        case .noContainer:
            return "iPhone chưa có sổ danh bạ. Mở app Danh bạ của máy một lần, rồi thử lại."
        }
    }
}

@MainActor
final class ContactBookModel: ObservableObject {
    @Published private(set) var library = PhoneLibrary.empty
    @Published private(set) var access: ContactAccess = .unknown
    @Published private(set) var message = ""
    @Published private(set) var messageIsError = false
    @Published private(set) var busy = false

    private let store = CNContactStore()
    private var askingForAccess = false

    var activeBook: PhoneBook? {
        library.activeBook
    }

    init() {
        library = LibraryStore.load()
    }

    func note(_ text: String, error: Bool) {
        message = text
        messageIsError = error
    }

    func refreshAccess() {
        access = Self.kind(CNContactStore.authorizationStatus(for: .contacts))
    }

    func setTitle(_ title: String) {
        library.title = title
        LibraryStore.save(library)
    }

    func selectBook(_ id: String) {
        library.activeBookID = id
        LibraryStore.save(library)
    }

    @discardableResult
    func importBulk(text: String) -> Bool {
        let batch = ImportParser.parse(text: text)
        let phones = batch.drafts.filter { $0.phone?.isEmpty == false }.count
        guard phones > 0 else {
            note("Không thấy số điện thoại nào trong danh sách.", error: true)
            return false
        }
        library.source = batch.drafts
        LibraryStore.save(library)
        var summary = "Đã nạp \(phones) số vào phần mềm."
        if batch.truncated {
            summary += " Danh sách quá dài, chỉ lấy \(ImportParser.maxDrafts) số đầu."
        }
        note(summary, error: false)
        return true
    }

    @discardableResult
    func splitBooks() -> Bool {
        guard BookSplitter.cleanTitle(library.title).isEmpty == false else {
            note(BookError.missingTitle.localizedDescription, error: true)
            return false
        }
        guard !library.source.isEmpty else {
            note(BookError.missingSource.localizedDescription, error: true)
            return false
        }
        guard let result = BookSplitter.split(drafts: library.source, title: library.title) else {
            note(BookError.missingTitle.localizedDescription, error: true)
            return false
        }
        guard !result.books.isEmpty else {
            note(BookError.empty.localizedDescription, error: true)
            return false
        }
        let previous = library.books
        var books = result.books
        for index in books.indices {
            if let old = previous.first(where: { $0.name == books[index].name }) {
                books[index].id = old.id
                books[index].onPhone = old.onPhone
                books[index].groupIdentifier = old.groupIdentifier
                books[index].contactIdentifiers = old.contactIdentifiers
                books[index].linkedIdentifiers = old.linkedIdentifiers
            }
        }
        let claimed = Set(books.flatMap { $0.entries.map(\.phone) })
        let names = Set(books.map(\.name))
        for old in previous where old.onPhone && !names.contains(old.name) {
            var leftover = old
            leftover.entries.removeAll { claimed.contains($0.phone) }
            books.append(leftover)
        }
        library.books = books
        if let active = library.activeBookID, books.contains(where: { $0.id == active }) {
            library.activeBookID = active
        } else {
            library.activeBookID = books.first?.id
        }
        LibraryStore.save(library)
        var text = "Đã chia \(result.books.count) danh bạ, mỗi cuốn tối đa \(BookSplitter.pageSize) số."
        if result.duplicatePhones > 0 {
            text += " Bỏ \(result.duplicatePhones) số trùng."
        }
        text += " Mỗi số chỉ nằm trong một danh bạ."
        note(text, error: false)
        return true
    }

    func runAll(text: String) async {
        guard !busy else { return }
        let clean = BookSplitter.cleanTitle(library.title)
        library.title = clean.isEmpty ? "Khach" : clean
        LibraryStore.save(library)
        let pending = text.trimmingCharacters(in: .whitespacesAndNewlines)
        if !pending.isEmpty {
            guard importBulk(text: pending) else { return }
        }
        if !library.source.isEmpty {
            guard splitBooks() else { return }
        } else if library.books.isEmpty {
            note("Dán số hoặc chọn file.", error: true)
            return
        }
        await loadOntoPhone()
    }

    func loadOntoPhone() async {
        guard await ensureAccess() else { return }
        guard !library.books.isEmpty else {
            note("Hãy chia danh bạ trước khi nạp vào iPhone.", error: true)
            return
        }
        busy = true
        defer { busy = false }
        let books = library.books
        let progress = ProgressBox { text in
            DispatchQueue.main.async {
                Task { @MainActor in
                    self.note(text, error: false)
                }
            }
        }
        let outcome: Result<UploadReport, Error> = await Task.detached(priority: .userInitiated) {
            do {
                return .success(try PhoneSession().upload(books, progress: progress))
            } catch {
                return .failure(error)
            }
        }.value
        switch outcome {
        case .failure(let error):
            note(Self.describe(error), error: true)
        case .success(let report):
            library.books = report.books
            if library.activeBookID == nil || library.books.contains(where: { $0.id == library.activeBookID }) == false {
                library.activeBookID = report.books.first?.id
            }
            LibraryStore.save(library)
            var text = "Đã nạp \(report.books.count) danh bạ lên iPhone."
            if report.created > 0 {
                text += " Thêm \(report.created) số."
            }
            if report.linked > 0 {
                text += " Gắn \(report.linked) số đã có sẵn."
            }
            if report.skipped > 0 {
                text += " Bỏ \(report.skipped) số đang ở nhóm khác."
            }
            if report.failed > 0 {
                text += " \(report.failed) số không ghi được."
            }
            if let active = library.activeBook {
                text += " Đang dùng \(active.name)."
            }
            note(text, error: report.failed > 0)
        }
    }

    func deleteActiveFromPhone() async {
        guard await ensureAccess() else { return }
        guard let book = library.activeBook else {
            note(BookError.noActiveBook.localizedDescription, error: true)
            return
        }
        guard book.onPhone else {
            note("\(book.name) chưa nằm trên iPhone.", error: true)
            return
        }
        busy = true
        defer { busy = false }
        let target = book
        let progress = ProgressBox { text in
            DispatchQueue.main.async {
                Task { @MainActor in
                    self.note(text, error: false)
                }
            }
        }
        let outcome: Result<DeleteReport, Error> = await Task.detached(priority: .userInitiated) {
            do {
                return .success(try PhoneSession().remove(target, progress: progress))
            } catch {
                return .failure(error)
            }
        }.value
        switch outcome {
        case .failure(let error):
            note(Self.describe(error), error: true)
        case .success(let report):
            if let index = library.books.firstIndex(where: { $0.id == target.id }) {
                library.books[index].onPhone = false
                library.books[index].groupIdentifier = nil
                library.books[index].contactIdentifiers = []
                library.books[index].linkedIdentifiers = []
            }
            if let next = library.books.first(where: { $0.id != target.id && $0.onPhone }) {
                library.activeBookID = next.id
                note("Đã xoá \(target.name), \(report.removed) số. Đang dùng \(next.name).", error: false)
            } else {
                note("Đã xoá \(target.name) trên iPhone.", error: false)
            }
            LibraryStore.save(library)
        }
    }

    private func ensureAccess() async -> Bool {
        let current = CNContactStore.authorizationStatus(for: .contacts)
        if Self.granted(current) {
            access = .granted
            return true
        }
        if Self.kind(current) == .limited {
            access = .limited
            note("Quyền đang là Chỉ một số liên hệ. Vào Cài đặt, chọn Cho phép đầy đủ. App không hỏi lại.", error: true)
            return false
        }
        guard current == .notDetermined else {
            access = .denied
            note("Hãy bật quyền Danh bạ trong Cài đặt và chọn Cho phép đầy đủ. App không hỏi lại.", error: true)
            return false
        }
        guard !askingForAccess else { return false }
        askingForAccess = true
        defer { askingForAccess = false }
        let allowed = await withCheckedContinuation { (continuation: CheckedContinuation<Bool, Never>) in
            store.requestAccess(for: .contacts) { granted, _ in
                continuation.resume(returning: granted)
            }
        }
        let status = CNContactStore.authorizationStatus(for: .contacts)
        access = Self.kind(status)
        if Self.kind(status) == .limited {
            note("Bạn đã chọn Chỉ một số liên hệ. Vào Cài đặt, đổi thành Cho phép đầy đủ, rồi nạp lại.", error: true)
            return false
        }
        if allowed || Self.granted(status) {
            return true
        }
        note("Bạn chưa cho quyền Danh bạ. App chỉ hỏi một lần.", error: true)
        return false
    }

    private static func granted(_ status: CNAuthorizationStatus) -> Bool {
        status == .authorized
    }

    private static func kind(_ status: CNAuthorizationStatus) -> ContactAccess {
        switch status {
        case .authorized:
            return .granted
        case .denied, .restricted:
            return .denied
        case .notDetermined:
            return .unknown
        default:
            if #available(iOS 18.0, *), status == .limited { return .limited }
            return .unknown
        }
    }

    private static func describe(_ error: Error) -> String {
        if let bookError = error as? BookError {
            return bookError.localizedDescription
        }
        let nsError = error as NSError
        if nsError.domain == CNErrorDomain {
            return "Danh bạ từ chối thao tác. Hãy thử lại."
        }
        return "Không thực hiện được. Hãy thử lại."
    }
}

private struct UploadReport {
    var books: [PhoneBook]
    var created: Int
    var linked: Int
    var skipped: Int
    var failed: Int
}

private struct DeleteReport {
    var removed: Int
    var unlinked: Int
}

private final class ProgressBox: @unchecked Sendable {
    private let handler: @Sendable (String) -> Void

    init(_ handler: @escaping @Sendable (String) -> Void) {
        self.handler = handler
    }

    func send(_ text: String) {
        handler(text)
    }
}

private struct PhonePlace {
    var contactID: String
    var groupIDs: Set<String>
}

private struct DeviceSnapshot {
    var owner: [String: PhonePlace]
    var personal: [String: String]
    var managedGroupIDs: Set<String>

    static func take(store: CNContactStore, managedNames: Set<String>) throws -> DeviceSnapshot {
        let groups = try store.groups(matching: nil)
        let managed = groups.filter { managedNames.contains($0.name) }
        var inManaged: [String: Set<String>] = [:]
        let memberKeys = [CNContactIdentifierKey as CNKeyDescriptor]
        for group in managed {
            let predicate = CNContact.predicateForContactsInGroup(withIdentifier: group.identifier)
            let people = try store.unifiedContacts(matching: predicate, keysToFetch: memberKeys)
            for person in people {
                inManaged[person.identifier, default: []].insert(group.identifier)
            }
        }
        var owner: [String: PhonePlace] = [:]
        var personal: [String: String] = [:]
        let keys = [
            CNContactIdentifierKey as CNKeyDescriptor,
            CNContactPhoneNumbersKey as CNKeyDescriptor,
        ]
        let request = CNContactFetchRequest(keysToFetch: keys)
        try store.enumerateContacts(with: request) { contact, _ in
            let groupIDs = inManaged[contact.identifier] ?? []
            for labeled in contact.phoneNumbers {
                guard let phone = ImportParser.normalizePhone(labeled.value.stringValue) else { continue }
                if groupIDs.isEmpty {
                    if owner[phone] == nil, personal[phone] == nil {
                        personal[phone] = contact.identifier
                    }
                } else if var place = owner[phone] {
                    place.groupIDs.formUnion(groupIDs)
                    owner[phone] = place
                    personal[phone] = nil
                } else {
                    owner[phone] = PhonePlace(contactID: contact.identifier, groupIDs: groupIDs)
                    personal[phone] = nil
                }
            }
        }
        for phone in owner.keys {
            personal[phone] = nil
        }
        return DeviceSnapshot(
            owner: owner,
            personal: personal,
            managedGroupIDs: Set(managed.map(\.identifier))
        )
    }
}

private final class PhoneSession {
    let store = CNContactStore()
    let container: String
    private let batchSize = 40

    init() throws {
        if let identifier = store.defaultContainerIdentifier() {
            container = identifier
        } else if let first = try store.containers(matching: nil).first {
            container = first.identifier
        } else {
            throw BookError.noContainer
        }
    }

    func upload(_ source: [PhoneBook], progress: ProgressBox) throws -> UploadReport {
        var books = source
        var snapshot = try DeviceSnapshot.take(store: store, managedNames: Set(books.map(\.name)))
        var created = 0
        var linked = 0
        var skipped = 0
        var failed = 0
        for index in books.indices {
            let book = books[index]
            progress.send("Đang nạp \(book.name)…")
            let group = try ensureGroup(name: book.name, preferredID: book.groupIdentifier)
            snapshot.managedGroupIDs.insert(group.identifier)
            let previouslyCreated = Set(book.contactIdentifiers)
            var createdIDs: [String] = []
            var linkedIDs: [String] = []
            var toCreate: [PhoneEntry] = []
            var toLink: [String] = []
            var linkPhones: [String: String] = [:]
            for entry in book.entries {
                if let place = snapshot.owner[entry.phone] {
                    if place.contactID.isEmpty {
                        skipped += 1
                        continue
                    }
                    if place.groupIDs == [group.identifier] {
                        remember(place.contactID, created: previouslyCreated, createdIDs: &createdIDs, linkedIDs: &linkedIDs)
                        continue
                    }
                    if place.groupIDs.isSubset(of: snapshot.managedGroupIDs) {
                        do {
                            try move(contactID: place.contactID, from: place.groupIDs, to: group)
                            snapshot.owner[entry.phone] = PhonePlace(contactID: place.contactID, groupIDs: [group.identifier])
                            remember(place.contactID, created: previouslyCreated, createdIDs: &createdIDs, linkedIDs: &linkedIDs)
                            linked += 1
                        } catch {
                            failed += 1
                        }
                        continue
                    }
                    skipped += 1
                    continue
                }
                if let existing = snapshot.personal.removeValue(forKey: entry.phone) {
                    toLink.append(existing)
                    linkPhones[existing] = entry.phone
                    snapshot.owner[entry.phone] = PhonePlace(contactID: "", groupIDs: [group.identifier])
                    continue
                }
                toCreate.append(entry)
                snapshot.owner[entry.phone] = PhonePlace(contactID: "", groupIDs: [group.identifier])
            }
            for batch in chunks(toLink) {
                let ids = Set(link(batch, to: group))
                for identifier in batch {
                    guard let phone = linkPhones[identifier] else { continue }
                    if ids.contains(identifier) {
                        snapshot.owner[phone] = PhonePlace(contactID: identifier, groupIDs: [group.identifier])
                        linkedIDs.append(identifier)
                        linked += 1
                    } else {
                        failed += 1
                    }
                }
                progress.send("Đang nạp \(book.name): \(createdIDs.count + linkedIDs.count)/\(book.entries.count)")
            }
            for batch in chunks(toCreate) {
                let saved = insert(batch, into: group)
                for (entry, id) in saved {
                    snapshot.owner[entry.phone] = PhonePlace(contactID: id, groupIDs: [group.identifier])
                    createdIDs.append(id)
                }
                created += saved.count
                failed += batch.count - saved.count
                progress.send("Đang nạp \(book.name): \(createdIDs.count + linkedIDs.count)/\(book.entries.count)")
            }
            books[index].onPhone = true
            books[index].groupIdentifier = group.identifier
            books[index].contactIdentifiers = createdIDs
            books[index].linkedIdentifiers = linkedIDs
        }
        return UploadReport(books: books, created: created, linked: linked, skipped: skipped, failed: failed)
    }

    func remove(_ book: PhoneBook, progress: ProgressBox) throws -> DeleteReport {
        guard let group = try group(named: book.name, preferredID: book.groupIdentifier) else {
            return DeleteReport(removed: 0, unlinked: 0)
        }
        let members = try members(of: group)
        let createdIDs = Set(book.contactIdentifiers)
        let created = members.filter { createdIDs.contains($0.identifier) }
        let others = members.filter { createdIDs.contains($0.identifier) == false }
        for batch in chunks(members) {
            let request = CNSaveRequest()
            for contact in batch {
                request.removeMember(contact, from: group)
            }
            if batch.isEmpty == false {
                try store.execute(request)
            }
        }
        var removed = 0
        for batch in chunks(created) {
            let request = CNSaveRequest()
            for contact in batch {
                guard let mutable = contact.mutableCopy() as? CNMutableContact else { continue }
                request.delete(mutable)
                removed += 1
            }
            try store.execute(request)
            progress.send("Đang xoá \(book.name): \(removed)")
        }
        let unlinked = others.count
        if let mutable = group.mutableCopy() as? CNMutableGroup {
            let request = CNSaveRequest()
            request.delete(mutable)
            try store.execute(request)
        }
        return DeleteReport(removed: removed, unlinked: unlinked)
    }

    private func remember(
        _ identifier: String,
        created: Set<String>,
        createdIDs: inout [String],
        linkedIDs: inout [String]
    ) {
        if created.contains(identifier) {
            createdIDs.append(identifier)
        } else {
            linkedIDs.append(identifier)
        }
    }

    private func ensureGroup(name: String, preferredID: String?) throws -> CNGroup {
        if let existing = try group(named: name, preferredID: preferredID) {
            return existing
        }
        let created = CNMutableGroup()
        created.name = name
        let request = CNSaveRequest()
        request.add(created, toContainerWithIdentifier: container)
        try store.execute(request)
        if let saved = try group(named: name, preferredID: created.identifier) {
            return saved
        }
        throw BookError.noContainer
    }

    private func group(named name: String, preferredID: String?) throws -> CNGroup? {
        let groups = try store.groups(matching: nil)
        if let preferredID, preferredID.isEmpty == false, let match = groups.first(where: { $0.identifier == preferredID }) {
            return match
        }
        return groups.first { $0.name == name }
    }

    private func members(of group: CNGroup) throws -> [CNContact] {
        let keys = [CNContactIdentifierKey as CNKeyDescriptor]
        let predicate = CNContact.predicateForContactsInGroup(withIdentifier: group.identifier)
        return try store.unifiedContacts(matching: predicate, keysToFetch: keys)
    }

    private func link(_ identifiers: [String], to group: CNGroup) -> [String] {
        if let linked = try? addMembers(identifiers, to: group), linked.count == identifiers.count {
            return linked
        }
        return identifiers.compactMap { identifier in
            (try? addMembers([identifier], to: group))?.first
        }
    }

    private func addMembers(_ identifiers: [String], to group: CNGroup) throws -> [String] {
        let keys = [CNContactIdentifierKey as CNKeyDescriptor]
        let request = CNSaveRequest()
        var linked: [String] = []
        for identifier in identifiers {
            guard let contact = try? store.unifiedContact(withIdentifier: identifier, keysToFetch: keys) else { continue }
            request.addMember(contact, to: group)
            linked.append(identifier)
        }
        guard !linked.isEmpty else { return [] }
        try store.execute(request)
        return linked
    }

    private func insert(_ entries: [PhoneEntry], into group: CNGroup) -> [(PhoneEntry, String)] {
        do {
            return try saveAndLink(entries, to: group)
        } catch {
            var saved: [(PhoneEntry, String)] = []
            for entry in entries {
                if let pair = try? saveAndLink([entry], to: group).first {
                    saved.append(pair)
                }
            }
            return saved
        }
    }

    private func saveAndLink(_ entries: [PhoneEntry], to group: CNGroup) throws -> [(PhoneEntry, String)] {
        let request = CNSaveRequest()
        var contacts: [CNMutableContact] = []
        for entry in entries {
            let contact = CNMutableContact()
            contact.givenName = entry.name
            contact.phoneNumbers = [
                CNLabeledValue(
                    label: CNLabelPhoneNumberMobile,
                    value: CNPhoneNumber(stringValue: entry.phone)
                ),
            ]
            request.add(contact, toContainerWithIdentifier: container)
            request.addMember(contact, to: group)
            contacts.append(contact)
        }
        try store.execute(request)
        return zip(entries, contacts).compactMap { entry, contact in
            contact.identifier.isEmpty ? nil : (entry, contact.identifier)
        }
    }

    private func move(contactID: String, from sourceIDs: Set<String>, to group: CNGroup) throws {
        guard !contactID.isEmpty else { return }
        let keys = [CNContactIdentifierKey as CNKeyDescriptor]
        let contact = try store.unifiedContact(withIdentifier: contactID, keysToFetch: keys)
        let groups = try store.groups(matching: nil)
        let request = CNSaveRequest()
        for sourceID in sourceIDs where sourceID != group.identifier {
            guard let source = groups.first(where: { $0.identifier == sourceID }) else { continue }
            request.removeMember(contact, from: source)
        }
        if sourceIDs.contains(group.identifier) == false {
            request.addMember(contact, to: group)
        }
        try store.execute(request)
    }

    private func chunks<T>(_ items: [T]) -> [[T]] {
        stride(from: 0, to: items.count, by: batchSize).map { start in
            Array(items[start ..< min(start + batchSize, items.count)])
        }
    }
}
