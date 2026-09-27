"use client";

import { useEffect, useState } from "react";
import { api, apiForm } from "../../lib/api";

type Contact = { id: string; display_name: string; phone_e164: string | null; email: string | null };
type Book = { id: string; name: string; contacts: Contact[] };
type BookSummary = { id: string; name: string; contact_count: number };
type Rejected = { line: number; raw: string; reason: string };
type BulkResult = {
  imported: number;
  skipped_duplicates: number;
  rejected_count: number;
  rejected: Rejected[];
};
type Row = {
  tiktok_username: string;
  display_name_shown: string | null;
  matched_public_profile: boolean;
  match_key: string;
  contact: { display_name: string } | null;
  public_profile: { nickname: string | null; followers: number | null } | null;
};

export default function SyncPage() {
  const [bookName, setBookName] = useState("Danh bạ của tôi");
  const [displayName, setDisplayName] = useState("");
  const [phone, setPhone] = useState("");
  const [email, setEmail] = useState("");
  const [book, setBook] = useState<Book | null>(null);
  const [books, setBooks] = useState<BookSummary[]>([]);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [username, setUsername] = useState("");
  const [contactId, setContactId] = useState("");
  const [rows, setRows] = useState<Row[]>([]);
  const [message, setMessage] = useState<string | null>(null);
  const [bulkText, setBulkText] = useState("");
  const [rejected, setRejected] = useState<Rejected[]>([]);
  const [busy, setBusy] = useState(false);

  function applyBook(next: Book, preferContactId?: string) {
    setBook(next);
    setContactId((current) => {
      if (preferContactId && next.contacts.some((contact) => contact.id === preferContactId)) return preferContactId;
      if (current && next.contacts.some((contact) => contact.id === current)) return current;
      return next.contacts[0]?.id ?? "";
    });
  }

  async function refreshBooks(selectedId: string) {
    const list = await api<BookSummary[]>("/api/contact-books");
    setBooks(list);
    const full = await api<Book>(`/api/contact-books/${selectedId}`);
    applyBook(full);
    setBookName(full.name);
    return full;
  }

  useEffect(() => {
    api<BookSummary[]>("/api/contact-books")
      .then(async (list) => {
        setBooks(list);
        if (!list[0]) return;
        const full = await api<Book>(`/api/contact-books/${list[0].id}`);
        applyBook(full);
        setBookName(full.name);
      })
      .catch((err: Error) => setMessage(err.message));
  }, []);

  async function ensureBook(): Promise<Book> {
    if (book) return book;
    const created = await api<Book>("/api/contact-books", {
      method: "POST",
      body: JSON.stringify({ name: bookName, contacts: [] }),
    });
    applyBook(created);
    return created;
  }

  function describeImport(result: BulkResult): string {
    return `Đã lưu ${result.imported} số mới. Bỏ qua ${result.skipped_duplicates} số trùng. ${result.rejected_count} dòng không đọc được. Số chỉ nằm trong danh bạ trên máy này.`;
  }

  async function finishImport(current: Book, result: BulkResult) {
    await refreshBooks(current.id);
    setRejected(result.rejected);
    setMessage(describeImport(result));
  }

  async function createBook() {
    if (!displayName && !phone && !email) {
      setMessage("Nhập tên, số điện thoại hoặc email trước khi thêm một liên hệ.");
      return;
    }
    const payload = { display_name: displayName || "Không tên", phone, email };
    const current = book ?? (await ensureBook());
    const added = await api<{ imported: number; skipped_duplicates: number }>(`/api/contact-books/${current.id}/contacts`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
    await refreshBooks(current.id);
    setDisplayName("");
    setPhone("");
    setEmail("");
    setMessage(
      added.skipped_duplicates
        ? "Số này đã có trong danh bạ nên không thêm lại."
        : "Đã thêm liên hệ. Số chỉ lưu trên máy này, không dùng để tra TikTok.",
    );
  }

  async function importLines() {
    const text = bulkText.trim();
    if (!text) {
      setMessage("Dán ít nhất một số điện thoại, mỗi số một dòng.");
      return;
    }
    setBusy(true);
    try {
      const current = await ensureBook();
      const result = await api<BulkResult>(`/api/contact-books/${current.id}/phones`, {
        method: "POST",
        body: JSON.stringify({ text }),
      });
      await finishImport(current, result);
      setBulkText("");
    } finally {
      setBusy(false);
    }
  }

  async function importFile(file: File | undefined) {
    if (!file) return;
    setBusy(true);
    try {
      const current = await ensureBook();
      const body = new FormData();
      body.append("file", file);
      const result = await apiForm<BulkResult>(`/api/contact-books/${current.id}/import`, body);
      await finishImport(current, result);
    } finally {
      setBusy(false);
    }
  }

  async function openSession() {
    if (!book) return;
    const session = await api<{ id: string }>("/api/official-sync/sessions", {
      method: "POST",
      body: JSON.stringify({ book_id: book.id, note: "Kết quả người dùng nhìn thấy trong ứng dụng TikTok" }),
    });
    setSessionId(session.id);
  }

  async function record() {
    if (!sessionId) return;
    await api(`/api/official-sync/sessions/${sessionId}/results`, {
      method: "POST",
      body: JSON.stringify([
        {
          tiktok_username: username,
          user_contact_id: contactId || null,
          display_name_shown: null,
        },
      ]),
    });
    const recon = await api<{ rows: Row[] }>(`/api/official-sync/sessions/${sessionId}/reconciliation`);
    setRows(recon.rows);
    setMessage("Đã ghi nhận username TikTok hiển thị và đối chiếu với hồ sơ công khai đã quét.");
  }

  return (
    <>
      <h1>Contact Sync Assistant</h1>
      <p className="lede">
        Nhập danh bạ của chính bạn, rồi ghi lại username mà ứng dụng TikTok đã hiển thị sau khi bạn tự bật đồng bộ trong app.
        Khóa đối chiếu là username, không phải số điện thoại.
      </p>
      <div className="policy">
        Không có tra cứu SĐT → ID. Nếu dán một số điện thoại vào ô username, API từ chối. Quyền danh bạ chỉ được cấp trong ứng dụng TikTok chính thức.
      </div>
      {message && <p>{message}</p>}
      <section className="panel">
        <h2>1. Danh bạ của bạn</h2>
        {books.length > 1 && (
          <select
            aria-label="Chọn danh bạ"
            value={book?.id ?? ""}
            onChange={(event) => refreshBooks(event.target.value).catch((err: Error) => setMessage(err.message))}
          >
            {books.map((item) => (
              <option key={item.id} value={item.id}>
                {item.name} ({item.contact_count})
              </option>
            ))}
          </select>
        )}
        <div className="row">
          <input aria-label="Tên danh bạ" value={bookName} onChange={(event) => setBookName(event.target.value)} disabled={book !== null} />
          <input aria-label="Tên liên hệ" placeholder="Tên" value={displayName} onChange={(event) => setDisplayName(event.target.value)} />
          <input aria-label="Số điện thoại" placeholder="Số điện thoại" value={phone} onChange={(event) => setPhone(event.target.value)} />
          <input aria-label="Email" placeholder="Email" value={email} onChange={(event) => setEmail(event.target.value)} />
          <button onClick={() => createBook().catch((err: Error) => setMessage(err.message))}>Thêm một liên hệ</button>
        </div>
        <h3>Nhập hàng loạt</h3>
        <p className="hint">
          Mỗi dòng một số. Có thể ghi kèm tên, ví dụ <code>Nguyễn Văn A, 0901234567</code>. Chấp nhận 090…, +84… và file .txt, .csv, .vcf.
        </p>
        <textarea
          aria-label="Danh sách số điện thoại"
          placeholder={"0901234567\nNguyễn Văn A, 0912345678\n+84987000111"}
          value={bulkText}
          onChange={(event) => setBulkText(event.target.value)}
        />
        <div className="row">
          <button disabled={busy} onClick={() => importLines().catch((err: Error) => setMessage(err.message))}>
            Nhập danh sách
          </button>
          <label className="button secondary">
            Chọn file .txt, .csv, .vcf
            <input
              aria-label="File danh bạ"
              type="file"
              accept=".txt,.csv,.vcf,text/plain,text/csv,text/vcard"
              hidden
              onChange={(event) => {
                const file = event.target.files?.[0];
                event.target.value = "";
                importFile(file).catch((err: Error) => setMessage(err.message));
              }}
            />
          </label>
        </div>
        {rejected.length > 0 && (
          <ul>
            {rejected.map((item) => (
              <li key={`${item.line}-${item.raw}`}>Dòng {item.line}: {item.raw} — {item.reason}</li>
            ))}
          </ul>
        )}
        {book && (
          <>
            <p className="hint">{book.contacts.length} liên hệ trong «{book.name}».</p>
            <ul className="contact-list">
              {book.contacts.slice(0, 200).map((contact) => (
                <li key={contact.id}>{contact.display_name} · {contact.phone_e164 ?? "không có SĐT"} · {contact.email ?? "không có email"}</li>
              ))}
            </ul>
            {book.contacts.length > 200 && <p className="hint">Đang hiện 200 liên hệ đầu. Toàn bộ đã lưu trong danh bạ.</p>}
          </>
        )}
      </section>
      <section className="panel">
        <h2>2. Ghi nhận kết quả TikTok đã hiển thị</h2>
        <div className="row">
          <button className="secondary" onClick={() => openSession().catch((err: Error) => setMessage(err.message))} disabled={!book}>
            Mở phiên ghi nhận
          </button>
          <select aria-label="Liên hệ để đối chiếu" value={contactId} onChange={(event) => setContactId(event.target.value)} disabled={!book}>
            <option value="">Không gắn liên hệ</option>
            {book?.contacts.map((contact) => (
              <option key={contact.id} value={contact.id}>
                {contact.display_name} · {contact.phone_e164 ?? contact.email ?? "không có số"}
              </option>
            ))}
          </select>
          <input aria-label="Username TikTok" placeholder="@username đã thấy trong app" value={username} onChange={(event) => setUsername(event.target.value)} />
          <button onClick={() => record().catch((err: Error) => setMessage(err.message))} disabled={!sessionId}>Đối chiếu</button>
        </div>
        <table>
          <thead><tr><th>Username</th><th>Liên hệ của bạn</th><th>Hồ sơ công khai</th><th>Khóa</th></tr></thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.tiktok_username}>
                <td>@{row.tiktok_username}</td>
                <td>{row.contact?.display_name ?? "—"}</td>
                <td>{row.matched_public_profile ? `${row.public_profile?.nickname ?? ""} (${row.public_profile?.followers ?? "?"} followers)` : "Chưa quét hồ sơ công khai"}</td>
                <td>{row.match_key}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </>
  );
}
