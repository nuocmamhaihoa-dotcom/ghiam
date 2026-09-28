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
        summary += " Bấm Chia danh bạ."
        note(summary, error: false)
        return true
    }

    func splitBooks() {
        guard BookSplitter.cleanTitle(library.title).isEmpty == false else {
            note(BookError.missingTitle.localizedDescription, error: true)
            return
        }
        guard !library.source.isEmpty else {
            note(BookError.missingSource.localizedDescription, error: true)
            return
        }
        guard let result = BookSplitter.split(drafts: library.source, title: library.title) else {
            note(BookError.missingTitle.localizedDescription, error: true)
            return
        }
        guard !result.books.isEmpty else {
            note(BookError.empty.localizedDescription, error: true)
            return
        }
        let previous = library.books
        var books = result.books
        for index in books.indices {
            if let old = previous.first(where: { $0.name == books[index].name }) {
                books[index].id = old.id
                books[index].onPhone = old.onPhone
                books[index].groupIdentifier = old.groupIdentifier
                books[index].contactIdentifiers = old.contactIdentifiers
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
    }

    func loadOntoPhone() async {
        guard await ensureAccess() else { return }
        guard !library.books.isEmpty else {
            note("Hãy chia danh bạ trước khi nạp vào iPhone.", error: true)
            return
        }
        busy = true
        defer { busy = false }
        do {
            var snapshot = try DeviceSnapshot.take(store: store, managedNames: Set(library.books.map(\.name)))
            if !snapshot.extraContactIDs.isEmpty {
                try deleteContacts(snapshot.extraContactIDs)
            }
            var created = 0
            var skipped = 0
            for index in library.books.indices {
                let book = library.books[index]
                note("Đang nạp \(book.name)…", error: false)
                let group = try ensureGroup(name: book.name, preferredID: book.groupIdentifier)
                snapshot.managedGroupIDs.insert(group.identifier)
                var kept: [String] = []
                var pending: [PhoneEntry] = []
                for entry in book.entries {
                    if snapshot.personal.contains(entry.phone) {
                        skipped += 1
                        continue
                    }
                    if let place = snapshot.owner[entry.phone] {
                        if place.groupIDs == [group.identifier] {
                            kept.append(place.contactID)
                        } else if place.groupIDs.isSubset(of: snapshot.managedGroupIDs) {
                            try move(contactID: place.contactID, from: place.groupIDs, to: group)
                            snapshot.owner[entry.phone] = PhonePlace(contactID: place.contactID, groupIDs: [group.identifier])
                            kept.append(place.contactID)
                        } else {
                            skipped += 1
                        }
                        continue
                    }
                    pending.append(entry)
                    snapshot.owner[entry.phone] = PhonePlace(contactID: "", groupIDs: [group.identifier])
                }
                let batches = stride(from: 0, to: pending.count, by: 40).map {
                    Array(pending[$0 ..< min($0 + 40, pending.count)])
                }
                for batch in batches {
                    let ids = try insert(batch, into: group)
                    for (entry, id) in zip(batch, ids) {
                        snapshot.owner[entry.phone] = PhonePlace(contactID: id, groupIDs: [group.identifier])
                        kept.append(id)
                    }
                    created += ids.count
                    note("Đang nạp \(book.name): \(kept.count)/\(book.entries.count)", error: false)
                    await Task.yield()
                }
                library.books[index].onPhone = true
                library.books[index].groupIdentifier = group.identifier
                library.books[index].contactIdentifiers = kept.filter { !$0.isEmpty }
                LibraryStore.save(library)
            }
            var text = "Đã nạp \(library.books.count) danh bạ lên iPhone."
            if created > 0 {
                text += " Thêm \(created) số."
            }
            if skipped > 0 {
                text += " Bỏ \(skipped) số đã có ở danh bạ khác."
            }
            note(text, error: false)
        } catch {
            LibraryStore.save(library)
            note(Self.describe(error), error: true)
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
        do {
            let group = try group(named: book.name, preferredID: book.groupIdentifier)
            var identifiers = Set(book.contactIdentifiers)
            if let group {
                let members = try members(of: group)
                identifiers.formUnion(members.map(\.identifier))
            }
            let list = Array(identifiers)
            let batches = stride(from: 0, to: list.count, by: 40).map {
                Array(list[$0 ..< min($0 + 40, list.count)])
            }
            var removed = 0
            for batch in batches {
                let request = CNSaveRequest()
                for identifier in batch {
                    guard let contact = try? store.unifiedContact(
                        withIdentifier: identifier,
                        keysToFetch: [CNContactIdentifierKey as CNKeyDescriptor]
                    ),
                        let mutable = contact.mutableCopy() as? CNMutableContact
                    else { continue }
                    request.delete(mutable)
                    removed += 1
                }
                try store.execute(request)
                note("Đang xoá \(book.name): \(removed)", error: false)
                await Task.yield()
            }
            if let group, let mutable = group.mutableCopy() as? CNMutableGroup {
                let request = CNSaveRequest()
                request.delete(mutable)
                try store.execute(request)
            }
            if let index = library.books.firstIndex(where: { $0.id == book.id }) {
                library.books[index].onPhone = false
                library.books[index].groupIdentifier = nil
                library.books[index].contactIdentifiers = []
            }
            LibraryStore.save(library)
            note("Đã xoá danh bạ \(book.name) trên iPhone. Các danh bạ khác vẫn còn.", error: false)
        } catch {
            note(Self.describe(error), error: true)
        }
    }

    private func ensureAccess() async -> Bool {
        let current = CNContactStore.authorizationStatus(for: .contacts)
        if Self.granted(current) {
            access = Self.kind(current)
            return true
        }
        guard current == .notDetermined else {
            access = .denied
            note("Hãy bật quyền Danh bạ trong Cài đặt. App không hỏi lại.", error: true)
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
        if allowed || Self.granted(status) {
            return true
        }
        note("Bạn chưa cho quyền Danh bạ. App chỉ hỏi một lần.", error: true)
        return false
    }

    private func ensureGroup(name: String, preferredID: String?) throws -> CNGroup {
        if let existing = try group(named: name, preferredID: preferredID) {
            return existing
        }
        let created = CNMutableGroup()
        created.name = name
        let request = CNSaveRequest()
        request.add(created, toContainerWithIdentifier: nil)
        try store.execute(request)
        return created
    }

    private func group(named name: String, preferredID: String?) throws -> CNGroup? {
        let groups = try store.groups(matching: nil)
        if let preferredID, let match = groups.first(where: { $0.identifier == preferredID }) {
            return match
        }
        return groups.first { $0.name == name }
    }

    private func members(of group: CNGroup) throws -> [CNContact] {
        let keys = [
            CNContactIdentifierKey as CNKeyDescriptor,
            CNContactPhoneNumbersKey as CNKeyDescriptor,
        ]
        let predicate = CNContact.predicateForContactsInGroup(withIdentifier: group.identifier)
        return try store.unifiedContacts(matching: predicate, keysToFetch: keys)
    }

    private func deleteContacts(_ identifiers: [String]) throws {
        let batches = stride(from: 0, to: identifiers.count, by: 40).map {
            Array(identifiers[$0 ..< min($0 + 40, identifiers.count)])
        }
        for batch in batches {
            let request = CNSaveRequest()
            for identifier in batch {
                guard let contact = try? store.unifiedContact(
                    withIdentifier: identifier,
                    keysToFetch: [CNContactIdentifierKey as CNKeyDescriptor]
                ),
                    let mutable = contact.mutableCopy() as? CNMutableContact
                else { continue }
                request.delete(mutable)
            }
            try store.execute(request)
        }
    }

    private func insert(_ entries: [PhoneEntry], into group: CNGroup) throws -> [String] {
        let request = CNSaveRequest()
        var created: [CNMutableContact] = []
        for entry in entries {
            let contact = CNMutableContact()
            contact.givenName = entry.name
            contact.phoneNumbers = [
                CNLabeledValue(
                    label: CNLabelPhoneNumberMobile,
                    value: CNPhoneNumber(stringValue: entry.phone)
                ),
            ]
            request.add(contact, toContainerWithIdentifier: nil)
            request.addMember(contact, to: group)
            created.append(contact)
        }
        try store.execute(request)
        return created.map(\.identifier)
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

    private static func granted(_ status: CNAuthorizationStatus) -> Bool {
        if status == .authorized { return true }
        if #available(iOS 18.0, *), status == .limited { return true }
        return false
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
        let nsError = error as NSError
        if nsError.domain == CNErrorDomain {
            return "Danh bạ từ chối thao tác. Hãy thử lại."
        }
        return "Không thực hiện được. Hãy thử lại."
    }
}

private struct PhonePlace {
    var contactID: String
    var groupIDs: Set<String>
}

private struct DeviceSnapshot {
    var owner: [String: PhonePlace]
    var personal: Set<String>
    var managedGroupIDs: Set<String>
    var extraContactIDs: [String]

    static func take(store: CNContactStore, managedNames: Set<String>) throws -> DeviceSnapshot {
        let groups = try store.groups(matching: nil)
        let managed = groups.filter { managedNames.contains($0.name) }
        let managedIDs = Set(managed.map(\.identifier))
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
        var personal = Set<String>()
        var extras = Set<String>()
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
                    if owner[phone] == nil {
                        personal.insert(phone)
                    }
                } else if var place = owner[phone] {
                    if place.contactID != contact.identifier {
                        extras.insert(contact.identifier)
                    }
                    place.groupIDs.formUnion(groupIDs)
                    owner[phone] = place
                } else {
                    owner[phone] = PhonePlace(contactID: contact.identifier, groupIDs: groupIDs)
                }
            }
        }
        personal.subtract(owner.keys)
        extras.subtract(owner.values.map(\.contactID))
        return DeviceSnapshot(
            owner: owner,
            personal: personal,
            managedGroupIDs: managedIDs,
            extraContactIDs: Array(extras)
        )
    }
}
