(function (root) {
  var MAX_DRAFTS = 100000;
  var PAGE_SIZE = 5000;
  var HEADER_WORDS = {
    name: true,
    ten: true,
    "tên": true,
    "ho ten": true,
    "họ tên": true,
    hoten: true,
    "họ và tên": true,
    phone: true,
    sdt: true,
    "sđt": true,
    "so dien thoai": true,
    "số điện thoại": true,
    tel: true,
    mobile: true,
    "điện thoại": true
  };

  function cleanName(value) {
    var collapsed = String(value || "").split(/\s+/).filter(Boolean).join(" ");
    return collapsed.length <= 80 ? collapsed : collapsed.slice(0, 80);
  }

  function nameKey(value) {
    return cleanName(value).toLowerCase();
  }

  function cleanTitle(raw) {
    var collapsed = String(raw || "").split(/\s+/).filter(Boolean).join(" ");
    if (!collapsed) return "";
    return collapsed.slice(0, 40);
  }

  function normalizePhone(raw) {
    var text = String(raw || "").trim();
    if (text.toLowerCase().indexOf("tel:") === 0) {
      text = text.slice(4).trim();
    }
    if (!text) return null;
    var digits = "";
    var leadingPlus = false;
    var started = false;
    for (var i = 0; i < text.length; i += 1) {
      var character = text.charAt(i);
      if (!started && character === "+") {
        leadingPlus = true;
        started = true;
        continue;
      }
      started = true;
      if (character >= "0" && character <= "9") {
        digits += character;
        continue;
      }
      if (character === " " || character === "-" || character === "(" || character === ")" || character === "." || character === "\t") {
        continue;
      }
      break;
    }
    if (digits.length < 8 || digits.length > 15) return null;
    if (digits.length === 11 && digits.indexOf("84") === 0) {
      return "0" + digits.slice(2);
    }
    return leadingPlus ? "+" + digits : digits;
  }

  function splitCSV(line) {
    var fields = [];
    var current = "";
    var inQuotes = false;
    var index = 0;
    while (index < line.length) {
      var character = line.charAt(index);
      if (inQuotes) {
        if (character === "\"") {
          if (line.charAt(index + 1) === "\"") {
            current += "\"";
            index += 2;
            continue;
          }
          inQuotes = false;
        } else {
          current += character;
        }
      } else if (character === "\"" && current === "") {
        inQuotes = true;
      } else if (character === "," || character === ";" || character === "\t") {
        fields.push(current.trim());
        current = "";
      } else {
        current += character;
      }
      index += 1;
    }
    fields.push(current.trim());
    return fields;
  }

  function isHeader(fields) {
    if (fields.length < 2) return false;
    for (var i = 0; i < fields.length; i += 1) {
      if (!HEADER_WORDS[nameKey(fields[i])]) return false;
    }
    return true;
  }

  function draftsFromLine(line, fields) {
    var phones = [];
    var nameParts = [];
    var i;
    for (i = 0; i < fields.length; i += 1) {
      var phone = normalizePhone(fields[i]);
      if (phone) phones.push(phone);
      else {
        var piece = cleanName(fields[i]);
        if (piece) nameParts.push(piece);
      }
    }
    if (!phones.length) {
      var parts = line.split(/\s+/).filter(Boolean);
      if (parts.length >= 2) {
        var tail = normalizePhone(parts[parts.length - 1]);
        var head = cleanName(parts.slice(0, -1).join(" "));
        if (tail && head) return [{ name: head, phone: tail }];
      }
      var whole = normalizePhone(line);
      if (whole) return [{ name: whole, phone: whole }];
      var onlyName = cleanName(fields.join(" "));
      return onlyName ? [{ name: onlyName, phone: null }] : [];
    }
    var name = nameParts.join(" ");
    return phones.map(function (phone) {
      return { name: name || phone, phone: phone };
    });
  }

  function parseLines(text) {
    var lines = String(text || "").replace(/^\uFEFF/, "").split(/\r\n|\n|\r/);
    var raw = [];
    var extra = false;
    for (var index = 0; index < lines.length; index += 1) {
      if (index >= 100000) {
        extra = true;
        break;
      }
      var trimmed = lines[index].trim();
      if (!trimmed || trimmed.charAt(0) === "#") continue;
      var fields = splitCSV(trimmed);
      if (isHeader(fields)) continue;
      raw = raw.concat(draftsFromLine(trimmed, fields));
    }
    var seen = {};
    var drafts = [];
    var truncated = extra;
    for (var j = 0; j < raw.length; j += 1) {
      var key = nameKey(raw[j].name) + "|" + (raw[j].phone || "");
      if (seen[key]) continue;
      if (drafts.length >= MAX_DRAFTS) {
        truncated = true;
        break;
      }
      seen[key] = true;
      drafts.push(raw[j]);
    }
    return { drafts: drafts, truncated: truncated };
  }

  function split(drafts, title, pageSize) {
    var base = cleanTitle(title);
    var size = pageSize || PAGE_SIZE;
    if (!base || size < 1) return null;
    var seen = {};
    var pages = [];
    var current = [];
    var duplicatePhones = 0;
    for (var i = 0; i < drafts.length; i += 1) {
      var phone = drafts[i].phone;
      if (!phone) continue;
      if (seen[phone]) {
        duplicatePhones += 1;
        continue;
      }
      seen[phone] = true;
      if (current.length === size) {
        pages.push(current);
        current = [];
      }
      current.push({ name: drafts[i].name, phone: phone });
    }
    if (current.length) pages.push(current);
    var books = pages.map(function (entries, index) {
      return {
        id: "book-" + (index + 1) + "-" + base,
        name: base + " " + (index + 1),
        entries: entries,
        sent: false
      };
    });
    return { books: books, duplicatePhones: duplicatePhones };
  }

  function phonesAreUnique(books) {
    var seen = {};
    for (var i = 0; i < books.length; i += 1) {
      var entries = books[i].entries || [];
      for (var j = 0; j < entries.length; j += 1) {
        if (seen[entries[j].phone]) return false;
        seen[entries[j].phone] = true;
      }
    }
    return true;
  }

  function escapeValue(value) {
    return String(value || "")
      .replace(/\\/g, "\\\\")
      .replace(/\n/g, "\\n")
      .replace(/,/g, "\\,")
      .replace(/;/g, "\\;");
  }

  function vcard(book) {
    var lines = [];
    var entries = book.entries || [];
    for (var i = 0; i < entries.length; i += 1) {
      var name = escapeValue(entries[i].name || entries[i].phone);
      var group = escapeValue(book.name);
      lines.push("BEGIN:VCARD");
      lines.push("VERSION:3.0");
      lines.push("N:" + name + ";;;;");
      lines.push("FN:" + name);
      lines.push("ORG:" + group);
      lines.push("TEL;TYPE=CELL:" + entries[i].phone);
      lines.push("NOTE:" + group + ". " + escapeValue("Bấm nút chia sẻ góc trên. Chọn Danh bạ. Bấm Thêm tất cả."));
      lines.push("END:VCARD");
    }
    return lines.join("\r\n") + "\r\n";
  }

  root.DanhBa = {
    pageSize: PAGE_SIZE,
    cleanTitle: cleanTitle,
    normalizePhone: normalizePhone,
    parseLines: parseLines,
    split: split,
    phonesAreUnique: phonesAreUnique,
    vcard: vcard
  };
})(typeof globalThis !== "undefined" ? globalThis : this);
