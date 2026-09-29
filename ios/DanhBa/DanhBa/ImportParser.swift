import Foundation

struct ContactDraft: Equatable, Codable {
    var name: String
    var phone: String?
    var facebook: String?
}

struct ImportBatch: Equatable {
    var drafts: [ContactDraft]
    var truncated: Bool
}

enum ImportParserError: Error {
    case invalidJSON
}

enum ImportParser {
    static let maxDrafts = 100_000

    static func parse(text: String) -> ImportBatch {
        var trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        if trimmed.hasPrefix("\u{feff}") {
            trimmed.removeFirst()
        }
        if trimmed.hasPrefix("{") || trimmed.hasPrefix("[") {
            guard let data = trimmed.data(using: .utf8) else {
                return ImportBatch(drafts: [], truncated: false)
            }
            return (try? parsePeople(data: data)) ?? ImportBatch(drafts: [], truncated: false)
        }
        if trimmed.uppercased().contains("BEGIN:VCARD") {
            return parseVCards(trimmed)
        }
        return parseLines(trimmed)
    }

    static func parsePeople(data: Data) throws -> ImportBatch {
        let rows = try peopleRecords(from: data)
        return unique(rows.compactMap(draft(from:)), limit: maxDrafts)
    }

    static func peopleURL(from raw: String) -> URL? {
        var text = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty, text.count <= 500 else { return nil }
        if !text.contains("://") {
            text = "https://" + text
        }
        guard var parts = URLComponents(string: text) else { return nil }
        let scheme = (parts.scheme ?? "").lowercased()
        guard scheme == "https" || scheme == "http" else { return nil }
        guard let host = parts.host?.lowercased(), !host.isEmpty else { return nil }
        if scheme == "http", !isLocalHost(host) {
            return nil
        }
        if parts.path.isEmpty || parts.path == "/" {
            parts.path = "/v1/people"
        }
        parts.user = nil
        parts.password = nil
        parts.fragment = nil
        return parts.url
    }

    static func identityKey(_ draft: ContactDraft) -> String {
        nameKey(draft.name) + "|" + (draft.phone ?? "")
    }

    static func nameKey(_ raw: String) -> String {
        raw.split { $0.isWhitespace }.joined(separator: " ").lowercased()
    }

    static func cleanName(_ value: String) -> String {
        let collapsed = value.split { $0.isWhitespace }.joined(separator: " ")
        if collapsed.count <= 80 {
            return collapsed
        }
        return String(collapsed.prefix(80))
    }

    static func normalizePhone(_ raw: String) -> String? {
        var text = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        if text.lowercased().hasPrefix("tel:") {
            text = String(text.dropFirst(4)).trimmingCharacters(in: .whitespacesAndNewlines)
        }
        guard !text.isEmpty else { return nil }
        var digits = ""
        var leadingPlus = false
        var started = false
        for character in text {
            if !started, character == "+" {
                leadingPlus = true
                started = true
                continue
            }
            started = true
            if character.isNumber {
                digits.append(character)
                continue
            }
            if character.isWhitespace || character == "-" || character == "(" || character == ")" || character == "." {
                continue
            }
            break
        }
        guard (8 ... 15).contains(digits.count) else { return nil }
        if digits.count == 11, digits.hasPrefix("84") {
            return "0" + digits.dropFirst(2)
        }
        return leadingPlus ? "+" + digits : digits
    }

    static func cleanUsername(_ value: String) -> String? {
        let text = value.split { $0.isWhitespace }.joined(separator: " ")
        guard !text.isEmpty else { return nil }
        var handle = text
        if handle.hasPrefix("@") {
            handle.removeFirst()
        }
        guard (2 ... 30).contains(handle.count) else { return nil }
        let valid = handle.allSatisfy { character in
            character.isASCII && (character.isLetter || character.isNumber || character == "." || character == "_")
        }
        guard valid else { return nil }
        return handle
    }

    private static let headerWords: Set<String> = [
        "name", "ten", "tên", "ho ten", "họ tên", "hoten", "họ và tên",
        "phone", "sdt", "sđt", "so dien thoai", "số điện thoại", "tel", "mobile", "điện thoại",
    ]

    private struct PersonRecord: Decodable {
        var name: String?
        var contactName: String?
        var username: String?
        var phone: String?
        var sdt: String?
    }

    private struct PeopleEnvelope: Decodable {
        var items: [PersonRecord]?
        var known: [PersonRecord]?
    }

    private static func peopleRecords(from data: Data) throws -> [PersonRecord] {
        let decoder = JSONDecoder()
        if let envelope = try? decoder.decode(PeopleEnvelope.self, from: data),
           envelope.known != nil || envelope.items != nil {
            return envelope.known ?? envelope.items ?? []
        }
        if let list = try? decoder.decode([PersonRecord].self, from: data) {
            return list
        }
        throw ImportParserError.invalidJSON
    }

    private static func draft(from row: PersonRecord) -> ContactDraft? {
        let contact = cleanName(row.contactName ?? "")
        let profile = cleanName(row.name ?? "")
        let name = contact.isEmpty ? profile : contact
        guard !name.isEmpty else { return nil }
        return ContactDraft(
            name: name,
            phone: normalizePhone(row.phone ?? row.sdt ?? ""),
            facebook: cleanUsername(row.username ?? "")
        )
    }

    private static func parseLines(_ text: String) -> ImportBatch {
        let lines = text.split(whereSeparator: \.isNewline).map(String.init)
        var raw: [ContactDraft] = []
        var extra = false
        for (index, line) in lines.enumerated() {
            if index >= 100_000 {
                extra = true
                break
            }
            let trimmed = line.trimmingCharacters(in: .whitespaces)
            if trimmed.isEmpty || trimmed.hasPrefix("#") {
                continue
            }
            let fields = splitCSV(trimmed)
            if isHeader(fields) {
                continue
            }
            raw.append(contentsOf: drafts(fromLine: trimmed, fields: fields))
        }
        var batch = unique(raw, limit: maxDrafts)
        if extra {
            batch = ImportBatch(drafts: batch.drafts, truncated: true)
        }
        return batch
    }

    static func text(from data: Data) -> String? {
        let encodings: [String.Encoding] = [.utf8, .utf16, .utf16LittleEndian, .utf16BigEndian, .isoLatin1]
        for encoding in encodings {
            if let text = String(data: data, encoding: encoding) {
                let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
                if !trimmed.isEmpty {
                    return text
                }
            }
        }
        return nil
    }

    private static func drafts(fromLine line: String, fields: [String]) -> [ContactDraft] {
        var phones: [String] = []
        var nameParts: [String] = []
        for field in fields {
            if let phone = normalizePhone(field) {
                phones.append(phone)
            } else {
                let name = cleanName(field)
                if !name.isEmpty {
                    nameParts.append(name)
                }
            }
        }
        if phones.isEmpty {
            let parts = line.split { $0.isWhitespace }.map(String.init)
            if parts.count >= 2, let phone = normalizePhone(parts[parts.count - 1]) {
                let name = cleanName(parts.dropLast().joined(separator: " "))
                if !name.isEmpty {
                    return [ContactDraft(name: name, phone: phone, facebook: nil)]
                }
            }
            if let phone = normalizePhone(line) {
                return [ContactDraft(name: phone, phone: phone, facebook: nil)]
            }
            let name = cleanName(fields.joined(separator: " "))
            guard !name.isEmpty else { return [] }
            return [ContactDraft(name: name, phone: nil, facebook: nil)]
        }
        let name = nameParts.joined(separator: " ")
        return phones.map { phone in
            ContactDraft(name: name.isEmpty ? phone : name, phone: phone, facebook: nil)
        }
    }

    private static func isHeader(_ fields: [String]) -> Bool {
        guard fields.count >= 2 else { return false }
        return fields.allSatisfy { headerWords.contains(nameKey($0)) }
    }

    private static func parseVCards(_ text: String) -> ImportBatch {
        let lines = unfold(text)
        var raw: [ContactDraft] = []
        var card: [String] = []
        var inside = false
        for line in lines {
            let marker = line.trimmingCharacters(in: .whitespaces).uppercased()
            if marker == "BEGIN:VCARD" {
                inside = true
                card = []
                continue
            }
            if marker == "END:VCARD" {
                if let draft = draft(fromVCard: card) {
                    raw.append(draft)
                }
                inside = false
                card = []
                continue
            }
            if inside {
                card.append(line)
            }
        }
        return unique(raw, limit: maxDrafts)
    }

    private static func draft(fromVCard lines: [String]) -> ContactDraft? {
        var formatted = ""
        var structured = ""
        var phones: [String] = []
        for line in lines {
            guard let property = vcardProperty(line) else { continue }
            switch property.name {
            case "FN":
                formatted = cleanName(property.value)
            case "N":
                structured = nameFromN(property.value)
            case "TEL":
                if let phone = normalizePhone(property.value), phones.contains(phone) == false {
                    phones.append(phone)
                }
            default:
                break
            }
        }
        let name = formatted.isEmpty ? structured : formatted
        guard !name.isEmpty else { return nil }
        if phones.isEmpty {
            return ContactDraft(name: name, phone: nil, facebook: nil)
        }
        return ContactDraft(name: name, phone: phones[0], facebook: nil)
    }

    private static func nameFromN(_ value: String) -> String {
        let parts = value.split(separator: ";", omittingEmptySubsequences: false).map { cleanName(String($0)) }
        let family = parts.first ?? ""
        let given = parts.count > 1 ? parts[1] : ""
        return cleanName([given, family].filter { !$0.isEmpty }.joined(separator: " "))
    }

    private static func vcardProperty(_ line: String) -> (name: String, value: String)? {
        guard let colon = line.firstIndex(of: ":") else { return nil }
        let left = line[..<colon]
        let value = unescape(String(line[line.index(after: colon)...]))
        let namePart = String(left.split(separator: ";").first ?? "")
        let name = String(namePart.split(separator: ".").last ?? Substring(namePart))
        guard !name.isEmpty else { return nil }
        return (name.uppercased(), value)
    }

    private static func unescape(_ value: String) -> String {
        var output = ""
        var escaped = false
        for character in value {
            if escaped {
                switch character {
                case "n", "N":
                    output.append("\n")
                case "\\":
                    output.append("\\")
                default:
                    output.append(character)
                }
                escaped = false
            } else if character == "\\" {
                escaped = true
            } else {
                output.append(character)
            }
        }
        return output.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    private static func unfold(_ text: String) -> [String] {
        var lines: [String] = []
        for raw in text.split(whereSeparator: \.isNewline) {
            let line = String(raw)
            if (line.hasPrefix(" ") || line.hasPrefix("\t")), let last = lines.popLast() {
                lines.append(last + String(line.dropFirst()))
                continue
            }
            lines.append(line)
        }
        return lines
    }

    private static func splitCSV(_ line: String) -> [String] {
        var fields: [String] = []
        var current = ""
        var inQuotes = false
        let chars = Array(line)
        var index = 0
        while index < chars.count {
            let character = chars[index]
            if inQuotes {
                if character == "\"" {
                    if index + 1 < chars.count, chars[index + 1] == "\"" {
                        current.append("\"")
                        index += 2
                        continue
                    }
                    inQuotes = false
                } else {
                    current.append(character)
                }
            } else if character == "\"", current.isEmpty {
                inQuotes = true
            } else if character == "," || character == ";" || character == "\t" {
                fields.append(current.trimmingCharacters(in: .whitespaces))
                current = ""
            } else {
                current.append(character)
            }
            index += 1
        }
        fields.append(current.trimmingCharacters(in: .whitespaces))
        return fields
    }

    private static func unique(_ drafts: [ContactDraft], limit: Int) -> ImportBatch {
        var seen = Set<String>()
        var output: [ContactDraft] = []
        var truncated = false
        for draft in drafts {
            let key = identityKey(draft)
            if seen.contains(key) {
                continue
            }
            if output.count >= limit {
                truncated = true
                break
            }
            seen.insert(key)
            output.append(draft)
        }
        return ImportBatch(drafts: output, truncated: truncated)
    }

    private static func isLocalHost(_ host: String) -> Bool {
        if host == "localhost" || host == "127.0.0.1" || host == "::1" {
            return true
        }
        if host.hasSuffix(".local") {
            return true
        }
        let parts = host.split(separator: ".").compactMap { Int($0) }
        guard parts.count == 4, parts.allSatisfy({ (0 ... 255).contains($0) }) else {
            return false
        }
        if parts[0] == 10 {
            return true
        }
        if parts[0] == 192, parts[1] == 168 {
            return true
        }
        if parts[0] == 172, (16 ... 31).contains(parts[1]) {
            return true
        }
        return false
    }
}
