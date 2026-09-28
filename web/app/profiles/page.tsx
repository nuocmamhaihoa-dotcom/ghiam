"use client";

import { useEffect, useState } from "react";
import { api } from "../../lib/api";

type Profile = {
  username: string;
  nickname: string | null;
  followers: number | null;
  likes: number | null;
  verified: boolean;
  private_account: boolean;
  contact_count?: number;
  bio: string | null;
};

type Contact = {
  id: string;
  username: string;
  kind: string;
  normalized_value: string;
  source: string;
  confidence: number;
};

export default function ProfilesPage() {
  const [query, setQuery] = useState("");
  const [profiles, setProfiles] = useState<Profile[]>([]);
  const [contacts, setContacts] = useState<Contact[]>([]);
  const [error, setError] = useState<string | null>(null);

  async function load(nextQuery = query) {
    const profilePage = await api<{ items: Profile[] }>(`/api/profiles?limit=50&q=${encodeURIComponent(nextQuery)}`);
    const contactPage = await api<{ items: Contact[] }>("/api/contacts?limit=50");
    setProfiles(profilePage.items);
    setContacts(contactPage.items);
  }

  useEffect(() => {
    load("").catch((err: Error) => setError(err.message));
  }, []);

  return (
    <>
      <h1>Hồ sơ và liên hệ</h1>
      <p className="lede">Mỗi liên hệ có nguồn và điểm tin cậy. Cùng một giá trị trên nhiều hồ sơ được đánh dấu khi xuất CSV.</p>
      {error && <p className="error">{error}</p>}
      <section className="panel">
        <div className="row">
          <input aria-label="Tìm username" value={query} placeholder="Tìm username hoặc nickname" onChange={(event) => setQuery(event.target.value)} />
          <button onClick={() => load().catch((err: Error) => setError(err.message))}>Lọc</button>
        </div>
        <table>
          <thead>
            <tr><th>Username</th><th>Tên</th><th>Followers</th><th>Likes</th><th>Liên hệ</th><th>Bio</th></tr>
          </thead>
          <tbody>
            {profiles.map((profile) => (
              <tr key={profile.username}>
                <td>@{profile.username} {profile.verified && <span className="pill">verified</span>} {profile.private_account && <span className="pill">private shell</span>}</td>
                <td>{profile.nickname}</td>
                <td>{profile.followers ?? "—"}</td>
                <td>{profile.likes ?? "—"}</td>
                <td>{profile.contact_count ?? 0}</td>
                <td>{profile.bio}</td>
              </tr>
            ))}
            {profiles.length === 0 && <tr><td colSpan={6}>Chưa có hồ sơ công khai trong cơ sở dữ liệu.</td></tr>}
          </tbody>
        </table>
      </section>
      <section className="panel">
        <h2>Liên hệ đã chuẩn hóa</h2>
        <table>
          <thead><tr><th>Hồ sơ</th><th>Loại</th><th>Giá trị</th><th>Nguồn</th><th>Tin cậy</th></tr></thead>
          <tbody>
            {contacts.map((contact) => (
              <tr key={contact.id}>
                <td>@{contact.username}</td>
                <td>{contact.kind}</td>
                <td>{contact.normalized_value}</td>
                <td>{contact.source}</td>
                <td>{contact.confidence.toFixed(2)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </>
  );
}
