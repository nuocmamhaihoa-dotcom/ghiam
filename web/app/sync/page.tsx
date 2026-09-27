"use client";

import { useEffect, useState } from "react";
import { api, apiForm, downloadUrl } from "../../lib/api";

type Contact = {
  id: string;
  display_name: string;
  phone_raw: string | null;
  phone_e164: string | null;
  email: string | null;
};
type Book = { id: string; name: string; contacts: Contact[] };
type BookSummary = { id: string; name: string; contact_count: number };
type SyncSession = {
  id: string;
  book_id: string;
  note: string | null;
  status: "recording" | "paused" | "completed";
  recorded_count: number;
  checkpoint: { recorded?: number; last_username?: string; updated_at?: string };
  created_at: string;
  updated_at: string;
};
type Rejected = { line: number; raw: string; reason: string };
type BulkResult = {
  imported: number;
  skipped_duplicates: number;
  rejected_count: number;
  rejected: Rejected[];
};
type Row = {
  match_id: string;
  tiktok_username: string;
  display_name_shown: string | null;
  note: string | null;
  matched_public_profile: boolean;
  match_key: string;
  contact: { id: string; display_name: string } | null;
  public_profile: { nickname: string | null; followers: number | null } | null;
};

export default function SyncPage() {
  const [bookName, setBookName] = useState("Danh bạ của tôi");
  const [displayName, setDisplayName] = useState("");
  const [phone, setPhone] = useState("");
  const [email, setEmail] = useState("");
  const [book, setBook] = useState<Book | null>(null);
  const [books, setBooks] = useState<BookSummary[]>([]);
  const [sessions, setSessions] = useState<SyncSession[]>([]);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [contactId, setContactId] = useState("");
  const [rows, setRows] = useState<Row[]>([]);
  const [message, setMessage] = useState<string | null>(null);
  const [bulkText, setBulkText] = useState("");
  const [rejected, setRejected] = useState<Rejected[]>([]);
  const [accountRejected, setAccountRejected] = useState<Rejected[]>([]);
  const [displayedAccounts, setDisplayedAccounts] = useState("");
  const [quickUsername, setQuickUsername] = useState("");
  const [quickDisplayName, setQuickDisplayName] = useState("");
  const [resultNote, setResultNote] = useState("");
  const [openedTikTok, setOpenedTikTok] = useState(false);
  const [permissionGranted, setPermissionGranted] = useState(false);
  const [suggestionsVisible, setSuggestionsVisible] = useState(false);
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
    await refreshSessions(full.id, undefined, full.contacts);
    return full;
  }

  async function refreshSessions(bookId: string, selectedSessionId?: string, contacts?: Contact[]) {
    const list = await api<SyncSession[]>(`/api/official-sync/sessions?book_id=${encodeURIComponent(bookId)}`);
    setSessions(list);
    const target = selectedSessionId ?? sessionId;
    const selected = target && list.some((item) => item.id === target) ? target : list[0]?.id ?? null;
    setSessionId(selected);
    if (selected) {
      const recon = await api<{ rows: Row[] }>(`/api/official-sync/sessions/${selected}/reconciliation`);
      setRows(recon.rows);
      const linkedIds = new Set(
        recon.rows.map((row) => row.contact?.id).filter((id): id is string => Boolean(id)),
      );
      const candidates = contacts ?? book?.contacts ?? [];
      setContactId((current) => {
        if (current && !linkedIds.has(current)) return current;
        return candidates.find((contact) => !linkedIds.has(contact.id))?.id ?? current;
      });
    } else {
      setRows([]);
    }
  }

  async function loadSession(id: string) {
    setSessionId(id);
    const recon = await api<{ rows: Row[] }>(`/api/official-sync/sessions/${id}/reconciliation`);
    setRows(recon.rows);
  }

  useEffect(() => {
    api<BookSummary[]>("/api/contact-books")
      .then(async (list) => {
        setBooks(list);
        if (!list[0]) return;
        const full = await api<Book>(`/api/contact-books/${list[0].id}`);
        applyBook(full);
        setBookName(full.name);
        await refreshSessions(full.id, undefined, full.contacts);
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

  async function createNewBook() {
    const name = window.prompt("Tên danh bạ mới", "Danh bạ của tôi")?.trim();
    if (!name) return;
    const created = await api<Book>("/api/contact-books", {
      method: "POST",
      body: JSON.stringify({ name, contacts: [] }),
    });
    await refreshBooks(created.id);
    setSessionId(null);
    setRows([]);
    setMessage(`Đã tạo danh bạ «${name}».`);
  }

  async function renameCurrentBook() {
    if (!book) return;
    const name = window.prompt("Tên mới cho danh bạ", book.name)?.trim();
    if (!name || name === book.name) return;
    await api<Book>(`/api/contact-books/${book.id}`, {
      method: "PATCH",
      body: JSON.stringify({ name }),
    });
    await refreshBooks(book.id);
    setMessage("Đã đổi tên danh bạ.");
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

  async function editContact(contact: Contact) {
    if (!book) return;
    const name = window.prompt("Tên liên hệ", contact.display_name);
    if (name === null) return;
    const nextPhone = window.prompt("Số điện thoại", contact.phone_raw ?? contact.phone_e164 ?? "");
    if (nextPhone === null) return;
    const nextEmail = window.prompt("Email", contact.email ?? "");
    if (nextEmail === null) return;
    await api(`/api/contact-books/${book.id}/contacts/${contact.id}`, {
      method: "PUT",
      body: JSON.stringify({ display_name: name, phone: nextPhone, email: nextEmail }),
    });
    await refreshBooks(book.id);
    setMessage("Đã cập nhật liên hệ.");
  }

  async function deleteContact(contact: Contact) {
    if (!book || !window.confirm(`Xóa liên hệ «${contact.display_name}» khỏi danh bạ?`)) return;
    await api(`/api/contact-books/${book.id}/contacts/${contact.id}`, { method: "DELETE" });
    await refreshBooks(book.id);
    setMessage("Đã xóa liên hệ. Kết quả TikTok đã ghi vẫn được giữ nhưng không còn gắn với liên hệ này.");
  }

  async function createRecordingSession(): Promise<string> {
    if (!book) throw new Error("Hãy nhập danh bạ trước");
    const session = await api<SyncSession>("/api/official-sync/sessions", {
      method: "POST",
      body: JSON.stringify({ book_id: book.id, note: "Kết quả người dùng nhìn thấy trong ứng dụng TikTok" }),
    });
    setSessionId(session.id);
    setRows([]);
    await refreshSessions(book.id, session.id);
    return session.id;
  }

  async function openSession() {
    await createRecordingSession();
    setMessage("Đã mở phiên ghi nhận. Hãy tự cấp quyền trong ứng dụng TikTok chính thức, rồi nhập các username TikTok đã hiển thị.");
  }

  async function ensureRecordingSession(): Promise<string> {
    if (!book) throw new Error("Hãy nhập danh bạ trước");
    const current = sessions.find((item) => item.id === sessionId);
    if (current?.status === "recording") return current.id;
    if (current?.status === "paused") {
      await api<SyncSession>(`/api/official-sync/sessions/${current.id}/resume`, { method: "POST" });
      await refreshSessions(book.id, current.id);
      return current.id;
    }
    const existing = sessions.find((item) => item.status === "recording");
    if (existing) {
      await loadSession(existing.id);
      return existing.id;
    }
    return await createRecordingSession();
  }

  async function recordQuickMatch() {
    if (!book || !contactId || !quickUsername.trim()) {
      setMessage("Chọn một liên hệ và nhập username TikTok mà ứng dụng đã hiển thị.");
      return;
    }
    setBusy(true);
    try {
      const activeSessionId = await ensureRecordingSession();
      await api(`/api/official-sync/sessions/${activeSessionId}/results`, {
        method: "POST",
        body: JSON.stringify([
          {
            tiktok_username: quickUsername,
            user_contact_id: contactId,
            display_name_shown: quickDisplayName || null,
            note: resultNote || null,
          },
        ]),
      });
      const recon = await api<{ rows: Row[] }>(`/api/official-sync/sessions/${activeSessionId}/reconciliation`);
      setRows(recon.rows);
      await refreshSessions(book.id, activeSessionId);
      const linkedIds = new Set(recon.rows.map((row) => row.contact?.id).filter((id): id is string => Boolean(id)));
      const next = book.contacts.find((contact) => !linkedIds.has(contact.id));
      setContactId(next?.id ?? "");
      setQuickUsername("");
      setQuickDisplayName("");
      setMessage(next ? `Đã lưu. Tiếp theo: ${next.display_name}.` : "Đã ghi nhận xong tất cả liên hệ được xác định.");
    } finally {
      setBusy(false);
    }
  }

  async function recordDisplayedAccounts() {
    if (!sessionId || !displayedAccounts.trim()) return;
    const result = await api<{
      recorded: number;
      skipped_duplicates: number;
      rejected_count: number;
      rejected: Rejected[];
    }>(`/api/official-sync/sessions/${sessionId}/results/bulk`, {
      method: "POST",
      body: JSON.stringify({
        text: displayedAccounts,
        user_contact_id: contactId || null,
        note: resultNote || null,
      }),
    });
    const recon = await api<{ rows: Row[] }>(`/api/official-sync/sessions/${sessionId}/reconciliation`);
    setRows(recon.rows);
    setAccountRejected(result.rejected);
    setDisplayedAccounts("");
    if (book) await refreshSessions(book.id, sessionId);
    setMessage(
      `Đã ghi ${result.recorded} tài khoản TikTok chính thức hiển thị; bỏ qua ${result.skipped_duplicates} dòng trùng; ${result.rejected_count} dòng không hợp lệ.`,
    );
  }

  async function changeSessionStatus(action: "pause" | "resume" | "complete") {
    if (!sessionId || !book) return;
    await api<SyncSession>(`/api/official-sync/sessions/${sessionId}/${action}`, { method: "POST" });
    await refreshSessions(book.id, sessionId);
    setMessage(
      action === "pause"
        ? "Đã lưu checkpoint và tạm dừng phiên."
        : action === "resume"
          ? "Đã tiếp tục từ checkpoint đã lưu."
          : "Đã hoàn tất phiên. Bạn vẫn có thể xuất kết quả hoặc tiếp tục lại sau.",
    );
  }

  const linkedContactIds = new Set(
    rows.map((row) => row.contact?.id).filter((id): id is string => Boolean(id)),
  );
  const linkedContacts = linkedContactIds.size;
  const contactTotal = book?.contacts.length ?? 0;
  const workflowSteps = [
    contactTotal > 0,
    openedTikTok,
    permissionGranted,
    suggestionsVisible,
    rows.length > 0,
  ];
  const completedSteps = workflowSteps.filter(Boolean).length;

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
      <div className="workflow-progress" aria-label="Tiến độ quy trình">
        <strong>Tiến độ: {completedSteps}/5 bước</strong>
        <div className="progress-track"><span style={{ width: `${completedSteps * 20}%` }} /></div>
        <span>{linkedContacts}/{contactTotal} liên hệ đã được bạn gắn với tài khoản TikTok hiển thị.</span>
      </div>
      {message && <p>{message}</p>}
      <section className={`panel workflow-step ${workflowSteps[0] ? "done" : ""}`}>
        <h2>1. Danh bạ của bạn</h2>
        <div className="row">
          <button className="secondary" onClick={() => createNewBook().catch((err: Error) => setMessage(err.message))}>
            Tạo danh bạ mới
          </button>
          <button className="secondary" disabled={!book} onClick={() => renameCurrentBook().catch((err: Error) => setMessage(err.message))}>
            Đổi tên danh bạ
          </button>
          {book && (
            <>
              <a className="button secondary" href={downloadUrl(`/api/contact-books/${book.id}/export.csv`)}>Xuất CSV</a>
              <a className="button secondary" href={downloadUrl(`/api/contact-books/${book.id}/export.xlsx`)}>Xuất Excel</a>
            </>
          )}
        </div>
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
                <li key={contact.id}>
                  {contact.display_name} · {contact.phone_e164 ?? "không có SĐT"} · {contact.email ?? "không có email"}{" "}
                  <button className="text-button" onClick={() => editContact(contact).catch((err: Error) => setMessage(err.message))}>Sửa</button>{" "}
                  <button className="text-button danger" onClick={() => deleteContact(contact).catch((err: Error) => setMessage(err.message))}>Xóa</button>
                </li>
              ))}
            </ul>
            {book.contacts.length > 200 && <p className="hint">Đang hiện 200 liên hệ đầu. Toàn bộ đã lưu trong danh bạ.</p>}
          </>
        )}
      </section>
      <section className={`panel workflow-step ${workflowSteps[1] ? "done" : ""}`}>
        <h2>2. Mở TikTok và vào Danh bạ</h2>
        <p>Mở TikTok trên điện thoại → <strong>Thêm/Tìm bạn bè</strong> → <strong>Danh bạ</strong>. Tên mục có thể khác đôi chút theo phiên bản hoặc khu vực.</p>
        <label className="check-row">
          <input type="checkbox" checked={openedTikTok} onChange={(event) => setOpenedTikTok(event.target.checked)} />
          Tôi đã mở đúng màn hình Danh bạ trong ứng dụng TikTok chính thức.
        </label>
      </section>
      <section className={`panel workflow-step ${workflowSteps[2] ? "done" : ""}`}>
        <h2>3. Tự cấp quyền danh bạ</h2>
        <p>Đọc thông báo xin quyền của TikTok và hệ điều hành. Chỉ bấm cho phép nếu bạn đồng ý; quyền có thể thu hồi trong cài đặt điện thoại hoặc TikTok.</p>
        <label className="check-row">
          <input type="checkbox" checked={permissionGranted} onChange={(event) => setPermissionGranted(event.target.checked)} />
          Tôi đã tự quyết định và hoàn tất bước cấp quyền trong TikTok.
        </label>
      </section>
      <section className={`panel workflow-step ${workflowSteps[3] ? "done" : ""}`}>
        <h2>4. Xem tài khoản TikTok gợi ý</h2>
        <p>Chờ TikTok tải danh sách, sau đó mở từng tài khoản được gợi ý và ghi lại <strong>@username</strong>. TikTok có thể không hiển thị mọi người do cài đặt riêng tư và chính sách của nền tảng.</p>
        <label className="check-row">
          <input type="checkbox" checked={suggestionsVisible} onChange={(event) => setSuggestionsVisible(event.target.checked)} />
          TikTok đã hiển thị các tài khoản gợi ý cho tôi.
        </label>
        <div className="policy">
          Công cụ này không đăng nhập TikTok, không tải danh bạ lên TikTok và không gọi API riêng tư.
        </div>
      </section>
      <section className={`panel workflow-step ${workflowSteps[4] ? "done" : ""}`}>
        <h2>5. Gắn username TikTok với từng liên hệ</h2>
        <p className="hint">Chế độ nhanh tự tạo/tiếp tục phiên và chuyển sang liên hệ chưa ghi nhận kế tiếp.</p>
        <div className="row">
          {sessions.length > 0 && (
            <select
              aria-label="Phiên ghi nhận"
              value={sessionId ?? ""}
              onChange={(event) => loadSession(event.target.value).catch((err: Error) => setMessage(err.message))}
            >
              {sessions.map((session) => (
                <option key={session.id} value={session.id}>
                  {new Date(session.created_at).toLocaleString("vi-VN")} · {session.status} · {session.recorded_count} tài khoản
                </option>
              ))}
            </select>
          )}
          <select aria-label="Liên hệ để đối chiếu" value={contactId} onChange={(event) => setContactId(event.target.value)} disabled={!book}>
            <option value="">Chọn liên hệ</option>
            {book?.contacts.map((contact) => (
              <option key={contact.id} value={contact.id}>
                {linkedContactIds.has(contact.id) ? "✓ " : ""}{contact.display_name} · {contact.phone_e164 ?? contact.email ?? "không có số"}
              </option>
            ))}
          </select>
        </div>
        <div className="quick-match">
          <input
            aria-label="Username TikTok của liên hệ"
            placeholder="@username TikTok đã hiển thị"
            value={quickUsername}
            onChange={(event) => setQuickUsername(event.target.value)}
          />
          <input
            aria-label="Tên TikTok hiển thị"
            placeholder="Tên TikTok hiển thị (không bắt buộc)"
            value={quickDisplayName}
            onChange={(event) => setQuickDisplayName(event.target.value)}
          />
          <button
            disabled={busy || !book || !contactId || !quickUsername.trim()}
            onClick={() => recordQuickMatch().catch((err: Error) => setMessage(err.message))}
          >
            Lưu và sang người tiếp theo
          </button>
        </div>
        <details>
          <summary>Nhập nhiều username hoặc quản lý phiên nâng cao</summary>
          <div className="row">
            <button onClick={() => openSession().catch((err: Error) => setMessage(err.message))} disabled={!book}>
              Tạo phiên mới
            </button>
          </div>
        <textarea
          aria-label="Tài khoản TikTok đã hiển thị"
          placeholder={"@username_da_thay\nhttps://www.tiktok.com/@tai_khoan_khac"}
          value={displayedAccounts}
          onChange={(event) => setDisplayedAccounts(event.target.value)}
        />
        <input
          aria-label="Ghi chú kết quả"
          placeholder="Ghi chú chung cho các tài khoản này (không bắt buộc)"
          value={resultNote}
          onChange={(event) => setResultNote(event.target.value)}
        />
        <div className="row">
          <button
            onClick={() => recordDisplayedAccounts().catch((err: Error) => setMessage(err.message))}
            disabled={!sessionId || !displayedAccounts.trim() || sessions.find((item) => item.id === sessionId)?.status !== "recording"}
          >
            Ghi nhận và đối chiếu
          </button>
          <button className="secondary" onClick={() => changeSessionStatus("pause").catch((err: Error) => setMessage(err.message))} disabled={!sessionId}>
            Tạm dừng
          </button>
          <button className="secondary" onClick={() => changeSessionStatus("resume").catch((err: Error) => setMessage(err.message))} disabled={!sessionId}>
            Tiếp tục
          </button>
          <button className="secondary" onClick={() => changeSessionStatus("complete").catch((err: Error) => setMessage(err.message))} disabled={!sessionId}>
            Hoàn tất
          </button>
          {sessionId && (
            <>
              <a className="button secondary" href={downloadUrl(`/api/official-sync/sessions/${sessionId}/export.csv`)}>Xuất kết quả CSV</a>
              <a className="button secondary" href={downloadUrl(`/api/official-sync/sessions/${sessionId}/export.xlsx`)}>Xuất kết quả Excel</a>
            </>
          )}
        </div>
        </details>
        {accountRejected.length > 0 && (
          <ul>
            {accountRejected.map((item) => (
              <li key={`account-${item.line}-${item.raw}`}>Dòng {item.line}: {item.raw} — {item.reason}</li>
            ))}
          </ul>
        )}
        {sessionId && sessions.find((item) => item.id === sessionId) && (
          <p className="hint">
            Trạng thái: {sessions.find((item) => item.id === sessionId)?.status}. Checkpoint đã lưu:{" "}
            {sessions.find((item) => item.id === sessionId)?.checkpoint.recorded ?? 0} tài khoản.
          </p>
        )}
        <table>
          <thead><tr><th>Username</th><th>Liên hệ của bạn</th><th>Hồ sơ công khai</th><th>Ghi chú</th><th>Khóa</th></tr></thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.match_id}>
                <td>@{row.tiktok_username}</td>
                <td>{row.contact?.display_name ?? "—"}</td>
                <td>{row.matched_public_profile ? `${row.public_profile?.nickname ?? ""} (${row.public_profile?.followers ?? "?"} followers)` : "Chưa quét hồ sơ công khai"}</td>
                <td>{row.note ?? "—"}</td>
                <td>{row.match_key}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </>
  );
}
