"use client";

import { useState } from "react";
import { api } from "../../lib/api";

type Contact = { id: string; display_name: string; phone_e164: string | null; email: string | null };
type Book = { id: string; name: string; contacts: Contact[] };
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
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [username, setUsername] = useState("");
  const [contactId, setContactId] = useState("");
  const [rows, setRows] = useState<Row[]>([]);
  const [message, setMessage] = useState<string | null>(null);

  async function createBook() {
    const created = await api<Book>("/api/contact-books", {
      method: "POST",
      body: JSON.stringify({
        name: bookName,
        contacts: displayName || phone || email ? [{ display_name: displayName || "Không tên", phone, email }] : [],
      }),
    });
    setBook(created);
    setContactId(created.contacts[0]?.id ?? "");
    setMessage("Đã lưu danh bạ trên máy này. Việc cấp quyền đồng bộ vẫn diễn ra trong ứng dụng TikTok.");
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
        <div className="row">
          <input aria-label="Tên danh bạ" value={bookName} onChange={(event) => setBookName(event.target.value)} />
          <input aria-label="Tên liên hệ" placeholder="Tên" value={displayName} onChange={(event) => setDisplayName(event.target.value)} />
          <input aria-label="Số điện thoại" placeholder="Số điện thoại" value={phone} onChange={(event) => setPhone(event.target.value)} />
          <input aria-label="Email" placeholder="Email" value={email} onChange={(event) => setEmail(event.target.value)} />
          <button onClick={() => createBook().catch((err: Error) => setMessage(err.message))}>Lưu danh bạ</button>
        </div>
        {book && (
          <ul>
            {book.contacts.map((contact) => (
              <li key={contact.id}>{contact.display_name} · {contact.phone_e164 ?? "không có SĐT"} · {contact.email ?? "không có email"}</li>
            ))}
          </ul>
        )}
      </section>
      <section className="panel">
        <h2>2. Ghi nhận kết quả TikTok đã hiển thị</h2>
        <div className="row">
          <button className="secondary" onClick={() => openSession().catch((err: Error) => setMessage(err.message))} disabled={!book}>
            Mở phiên ghi nhận
          </button>
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
