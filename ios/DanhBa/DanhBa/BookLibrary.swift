import Foundation

struct PhoneEntry: Codable, Equatable {
    var name: String
    var phone: String
}

struct PhoneBook: Codable, Equatable, Identifiable {
    var id: String
    var name: String
    var entries: [PhoneEntry]
    var onPhone: Bool
    var groupIdentifier: String?
    var contactIdentifiers: [String]
    var linkedIdentifiers: [String]

    init(
        id: String,
        name: String,
        entries: [PhoneEntry],
        onPhone: Bool,
        groupIdentifier: String?,
        contactIdentifiers: [String],
        linkedIdentifiers: [String] = []
    ) {
        self.id = id
        self.name = name
        self.entries = entries
        self.onPhone = onPhone
        self.groupIdentifier = groupIdentifier
        self.contactIdentifiers = contactIdentifiers
        self.linkedIdentifiers = linkedIdentifiers
    }

    init(from decoder: Decoder) throws {
        let box = try decoder.container(keyedBy: CodingKeys.self)
        id = try box.decode(String.self, forKey: .id)
        name = try box.decode(String.self, forKey: .name)
        entries = try box.decode([PhoneEntry].self, forKey: .entries)
        onPhone = try box.decode(Bool.self, forKey: .onPhone)
        groupIdentifier = try box.decodeIfPresent(String.self, forKey: .groupIdentifier)
        contactIdentifiers = try box.decodeIfPresent([String].self, forKey: .contactIdentifiers) ?? []
        linkedIdentifiers = try box.decodeIfPresent([String].self, forKey: .linkedIdentifiers) ?? []
    }

    func encode(to encoder: Encoder) throws {
        var box = encoder.container(keyedBy: CodingKeys.self)
        try box.encode(id, forKey: .id)
        try box.encode(name, forKey: .name)
        try box.encode(entries, forKey: .entries)
        try box.encode(onPhone, forKey: .onPhone)
        try box.encodeIfPresent(groupIdentifier, forKey: .groupIdentifier)
        try box.encode(contactIdentifiers, forKey: .contactIdentifiers)
        try box.encode(linkedIdentifiers, forKey: .linkedIdentifiers)
    }

    private enum CodingKeys: String, CodingKey {
        case id
        case name
        case entries
        case onPhone
        case groupIdentifier
        case contactIdentifiers
        case linkedIdentifiers
    }
}

struct PhoneLibrary: Codable, Equatable {
    var source: [ContactDraft]
    var books: [PhoneBook]
    var activeBookID: String?
    var title: String

    static let empty = PhoneLibrary(source: [], books: [], activeBookID: nil, title: "")

    var activeBook: PhoneBook? {
        guard let activeBookID else { return books.first }
        return books.first { $0.id == activeBookID } ?? books.first
    }
}

struct SplitResult: Equatable {
    var books: [PhoneBook]
    var duplicatePhones: Int
    var missingPhones: Int
}

enum BookSplitter {
    static let pageSize = 5000

    static func split(drafts: [ContactDraft], title: String, pageSize: Int = pageSize) -> SplitResult? {
        let base = cleanTitle(title)
        guard !base.isEmpty, pageSize > 0 else { return nil }
        var seen = Set<String>()
        var pages: [[PhoneEntry]] = []
        var current: [PhoneEntry] = []
        var duplicatePhones = 0
        var missingPhones = 0
        for draft in drafts {
            guard let phone = draft.phone, !phone.isEmpty else {
                missingPhones += 1
                continue
            }
            if seen.contains(phone) {
                duplicatePhones += 1
                continue
            }
            seen.insert(phone)
            if current.count == pageSize {
                pages.append(current)
                current = []
            }
            current.append(PhoneEntry(name: draft.name, phone: phone))
        }
        if !current.isEmpty {
            pages.append(current)
        }
        let books = pages.enumerated().map { index, entries in
            PhoneBook(
                id: "book-\(index + 1)-\(base)",
                name: "\(base) \(index + 1)",
                entries: entries,
                onPhone: false,
                groupIdentifier: nil,
                contactIdentifiers: []
            )
        }
        return SplitResult(books: books, duplicatePhones: duplicatePhones, missingPhones: missingPhones)
    }

    static func cleanTitle(_ raw: String) -> String {
        let collapsed = raw.split(whereSeparator: \.isWhitespace).joined(separator: " ")
        let trimmed = collapsed.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return "" }
        return String(trimmed.prefix(40))
    }

    static func phonesAreUnique(_ books: [PhoneBook]) -> Bool {
        var seen = Set<String>()
        for book in books {
            for entry in book.entries {
                if seen.contains(entry.phone) {
                    return false
                }
                seen.insert(entry.phone)
            }
        }
        return true
    }
}

enum LibraryStore {
    private static var fileURL: URL {
        let base = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first
            ?? FileManager.default.temporaryDirectory
        let folder = base.appendingPathComponent("DanhBa", isDirectory: true)
        try? FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        return folder.appendingPathComponent("library.json")
    }

    static func load() -> PhoneLibrary {
        guard let data = try? Data(contentsOf: fileURL) else { return .empty }
        return (try? JSONDecoder().decode(PhoneLibrary.self, from: data)) ?? .empty
    }

    static func save(_ library: PhoneLibrary) {
        guard let data = try? JSONEncoder().encode(library) else { return }
        try? data.write(to: fileURL, options: .atomic)
    }
}
