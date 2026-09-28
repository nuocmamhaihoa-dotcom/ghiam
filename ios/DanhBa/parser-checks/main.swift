import Foundation

func check(_ condition: Bool, _ label: String, failures: inout [String]) {
    if !condition {
        failures.append(label)
    }
}

var failures: [String] = []

let lines = ImportParser.parse(text: """
name,phone
# ghi chú
Trần Tùng, 0901234567
Trần Tùng, 0901234567
A Tùng Bán Gạch
Trần Tùng 0912345678
"Nguyễn, An", +84 901-234-567
Tên, không phải số

""")
check(lines.drafts.count == 5, "line count \(lines.drafts.count)", failures: &failures)
check(lines.drafts[0].name == "Trần Tùng" && lines.drafts[0].phone == "0901234567", "csv phone", failures: &failures)
check(lines.drafts[1].name == "A Tùng Bán Gạch" && lines.drafts[1].phone == nil, "name only", failures: &failures)
check(lines.drafts[2].phone == "0912345678", "space phone", failures: &failures)
check(lines.drafts[3].name == "Nguyễn, An" && lines.drafts[3].phone == "+84901234567", "quoted plus", failures: &failures)
check(lines.drafts[4].name == "Tên không phải số", "short second field stays in the name", failures: &failures)
check(lines.truncated == false, "not truncated", failures: &failures)

let cards = ImportParser.parse(text: """
BEGIN:VCARD
VERSION:3.0
N:Tùng;Trần;;;
FN:Trần Tùng
TEL;TYPE=CELL:+84 901-234-567
END:VCARD
BEGIN:VCARD
FN:Bà
  soi
END:VCARD

""")
check(cards.drafts.count == 2, "vcard count \(cards.drafts.count)", failures: &failures)
check(cards.drafts[0].name == "Trần Tùng" && cards.drafts[0].phone == "+84901234567", "vcard fn tel", failures: &failures)
check(cards.drafts[1].name == "Bà soi", "folded fn \(cards.drafts[1].name)", failures: &failures)

let named = ImportParser.parse(text: "BEGIN:VCARD\nN:Tùng;Trần;;;\nEND:VCARD\n")
check(named.drafts.first?.name == "Trần Tùng", "n field \(named.drafts.first?.name ?? "")", failures: &failures)

do {
    let known = """
    {"items":[{"name":"Hồ sơ","contactName":"Bỏ","username":"@ab"}],"known":[{"name":"Trần Tùng","contactName":"A Tùng Bán Gạch","username":"@trn.tng751"},{"name":"Hồng","contactName":"Hồng","username":"Hong@1978"}]}
    """.data(using: .utf8)!
    let people = try ImportParser.parsePeople(data: known)
    check(people.drafts.count == 2, "known rows \(people.drafts.count)", failures: &failures)
    check(people.drafts[0].name == "A Tùng Bán Gạch" && people.drafts[0].facebook == "trn.tng751", "contact name wins", failures: &failures)
    check(people.drafts[1].name == "Hồng" && people.drafts[1].facebook == nil, "invalid username dropped", failures: &failures)

    let itemsOnly = """
    [{"name":"Tên hồ sơ","contactName":"Chỉ danh bạ"}]
    """.data(using: .utf8)!
    let fromItems = try ImportParser.parsePeople(data: itemsOnly)
    check(fromItems.drafts.first?.name == "Chỉ danh bạ", "array contact name", failures: &failures)
} catch {
    failures.append("json \(error)")
}

let bom = ImportParser.parse(text: "\u{feff}Lê Na, 0987654321")
check(bom.drafts.first?.phone == "0987654321", "bom", failures: &failures)

check(ImportParser.normalizePhone("tel:090.123.4567") == "0901234567", "tel prefix", failures: &failures)
check(ImportParser.normalizePhone("123") == nil, "short phone", failures: &failures)
check(ImportParser.peopleURL(from: "https://hub.example")?.path == "/v1/people", "https root", failures: &failures)
check(ImportParser.peopleURL(from: "http://192.168.1.20:8080")?.absoluteString == "http://192.168.1.20:8080/v1/people", "lan http", failures: &failures)
check(ImportParser.peopleURL(from: "http://example.com") == nil, "public http rejected", failures: &failures)
let stripped = ImportParser.peopleURL(from: "https://user:secret@hub.example/v1/people")
check(stripped != nil && stripped?.user == nil, "strip userinfo", failures: &failures)
check(ImportParser.cleanUsername("@b.soi22") == "b.soi22", "username", failures: &failures)

if !failures.isEmpty {
    for failure in failures {
        fputs(failure + "\n", stderr)
    }
    exit(1)
}
