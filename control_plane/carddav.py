"""CardDAV vừa đủ để iPhone tự kéo cả danh bạ sau một lần cài hồ sơ."""

from __future__ import annotations

import base64
import hashlib
import re
import secrets
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from pathlib import Path

from control_plane import db
from control_plane.danhba_store import escape_vcard

_TICKET = re.compile(r"^[A-Za-z0-9_-]{16,80}$")
_FILE_ID = re.compile(r"^[a-f0-9]{20}$")
_PRINCIPAL = "/carddav/principals/danhba/"
_HOME = "/carddav/books/danhba/"
_BOOK = "/carddav/books/danhba/contacts/"
_PROFILE_TTL = timedelta(minutes=15)


def carddav_password(token: str) -> str:
    """Mật khẩu riêng cho iPhone. Không phải token quản trị hub."""
    return hashlib.sha256(f"carddav:{token}".encode()).hexdigest()[:24]


def _basic_ok(authorization: str | None, token: str) -> bool:
    if not authorization or not authorization.lower().startswith("basic "):
        return False
    try:
        decoded = base64.b64decode(authorization.split(" ", 1)[1].strip(), validate=True).decode()
    except (ValueError, UnicodeDecodeError):
        return False
    _user, separator, password = decoded.partition(":")
    if not separator:
        return False
    return password == carddav_password(token)


def _xml(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _file_id(phone: str) -> str:
    return hashlib.sha256(phone.encode()).hexdigest()[:20]


def list_contacts(db_path: Path) -> list[dict[str, str]]:
    with db.session(db_path) as conn:
        rows = conn.execute(
            """
            SELECT e.phone, e.name, b.name AS book
            FROM contact_entries e
            JOIN contact_books b ON b.id = e.book_id
            ORDER BY e.phone
            """
        ).fetchall()
    contacts: list[dict[str, str]] = []
    for row in rows:
        phone = str(row["phone"])
        name = str(row["name"])
        book = str(row["book"])
        digest = hashlib.sha256(f"{phone}|{name}|{book}".encode()).hexdigest()[:16]
        contacts.append(
            {
                "phone": phone,
                "name": name,
                "book": book,
                "id": _file_id(phone),
                "etag": digest,
            }
        )
    return contacts


def _ctag(contacts: list[dict[str, str]]) -> str:
    joined = "".join(item["etag"] for item in contacts)
    return hashlib.sha256(joined.encode()).hexdigest()[:16]


def render_vcard(contact: dict[str, str]) -> str:
    name = escape_vcard(contact["name"] or contact["phone"])
    book = escape_vcard(contact["book"])
    lines = [
        "BEGIN:VCARD",
        "VERSION:3.0",
        f"UID:{contact['id']}@danhba",
        f"N:{name};;;;",
        f"FN:{name}",
        f"ORG:{book}",
        f"TEL;TYPE=CELL:{contact['phone']}",
        f"NOTE:{book}",
        "END:VCARD",
    ]
    return "\r\n".join(lines) + "\r\n"


def issue_profile_ticket(db_path: Path, now: str) -> str:
    moment = datetime.fromisoformat(now)
    expires_at = (moment + _PROFILE_TTL).isoformat()
    ticket = secrets.token_urlsafe(24)
    with db.session(db_path) as conn:
        conn.execute("DELETE FROM carddav_tickets WHERE expires_at <= ?", (now,))
        conn.execute(
            "INSERT INTO carddav_tickets(ticket, expires_at) VALUES(?,?)",
            (ticket, expires_at),
        )
    return ticket


def profile_ticket_open(db_path: Path, ticket: str, now: str) -> bool:
    if not _TICKET.fullmatch(ticket):
        return False
    moment = datetime.fromisoformat(now)
    with db.session(db_path) as conn:
        row = conn.execute(
            "SELECT expires_at FROM carddav_tickets WHERE ticket=?",
            (ticket,),
        ).fetchone()
    if row is None:
        return False
    return moment <= datetime.fromisoformat(str(row["expires_at"]))


def render_profile(host: str, port: int, use_ssl: bool, password: str) -> str:
    identity = hashlib.sha256(f"{host}:{port}:{use_ssl}".encode()).hexdigest()
    payload_uuid = identity[:8] + "-" + identity[8:12] + "-" + identity[12:16] + "-" + identity[16:20] + "-" + identity[20:32]
    profile_uuid = hashlib.sha256(f"profile:{identity}".encode()).hexdigest()
    profile_uuid = (
        profile_uuid[:8]
        + "-"
        + profile_uuid[8:12]
        + "-"
        + profile_uuid[12:16]
        + "-"
        + profile_uuid[16:20]
        + "-"
        + profile_uuid[20:32]
    )
    ssl_tag = "<true/>" if use_ssl else "<false/>"
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>PayloadContent</key>
  <array>
    <dict>
      <key>CardDAVAccountDescription</key>
      <string>Danh ba</string>
      <key>CardDAVHostName</key>
      <string>{_xml(host)}</string>
      <key>CardDAVPassword</key>
      <string>{_xml(password)}</string>
      <key>CardDAVPort</key>
      <integer>{port}</integer>
      <key>CardDAVPrincipalURL</key>
      <string>{_PRINCIPAL}</string>
      <key>CardDAVUseSSL</key>
      {ssl_tag}
      <key>CardDAVUsername</key>
      <string>danhba</string>
      <key>PayloadDescription</key>
      <string>Danh ba tu dong vao iPhone</string>
      <key>PayloadDisplayName</key>
      <string>Danh ba</string>
      <key>PayloadIdentifier</key>
      <string>com.ghiam.danhba.carddav</string>
      <key>PayloadType</key>
      <string>com.apple.carddav.account</string>
      <key>PayloadUUID</key>
      <string>{payload_uuid}</string>
      <key>PayloadVersion</key>
      <integer>1</integer>
    </dict>
  </array>
  <key>PayloadDisplayName</key>
  <string>Danh ba</string>
  <key>PayloadIdentifier</key>
  <string>com.ghiam.danhba.profile</string>
  <key>PayloadType</key>
  <string>Configuration</string>
  <key>PayloadUUID</key>
  <string>{profile_uuid}</string>
  <key>PayloadVersion</key>
  <integer>1</integer>
</dict>
</plist>
"""


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _href_id(href: str) -> str | None:
    path = href.split("?", 1)[0].rstrip("/")
    name = path.rsplit("/", 1)[-1]
    if not name.endswith(".vcf"):
        return None
    ident = name[: -len(".vcf")]
    if _FILE_ID.fullmatch(ident):
        return ident
    return None


def _propstat(inner: str) -> str:
    return (
        "<d:propstat><d:prop>"
        + inner
        + "</d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat>"
    )


def _response(href: str, inner: str) -> str:
    return f"<d:response><d:href>{_xml(href)}</d:href>{_propstat(inner)}</d:response>"


def _multistatus(body: str) -> bytes:
    xml = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<d:multistatus xmlns:d="DAV:" xmlns:card="urn:ietf:params:xml:ns:carddav" '
        'xmlns:cs="http://calendarserver.org/ns/">'
        + body
        + "</d:multistatus>"
    )
    return xml.encode("utf-8")


def _principal_props() -> str:
    return (
        "<d:displayname>Danh ba</d:displayname>"
        "<d:resourcetype><d:principal/></d:resourcetype>"
        f"<d:current-user-principal><d:href>{_PRINCIPAL}</d:href></d:current-user-principal>"
        f"<d:principal-URL><d:href>{_PRINCIPAL}</d:href></d:principal-URL>"
        f"<card:addressbook-home-set><d:href>{_HOME}</d:href></card:addressbook-home-set>"
        "<d:current-user-privilege-set><d:privilege><d:read/></d:privilege></d:current-user-privilege-set>"
        "<d:supported-report-set>"
        "<d:supported-report><d:report><card:addressbook-multiget/></d:report></d:supported-report>"
        "<d:supported-report><d:report><card:addressbook-query/></d:report></d:supported-report>"
        "<d:supported-report><d:report><d:sync-collection/></d:report></d:supported-report>"
        "</d:supported-report-set>"
    )


def _book_props(ctag: str) -> str:
    return (
        "<d:displayname>Danh ba</d:displayname>"
        "<d:resourcetype><d:collection/><card:addressbook/></d:resourcetype>"
        f"<cs:getctag>{ctag}</cs:getctag>"
        f"<d:sync-token>http://danhba/sync/{ctag}</d:sync-token>"
        "<card:supported-address-data>"
        '<card:address-data-type content-type="text/vcard" version="3.0"/>'
        "</card:supported-address-data>"
        "<d:current-user-privilege-set><d:privilege><d:read/></d:privilege></d:current-user-privilege-set>"
        "<d:supported-report-set>"
        "<d:supported-report><d:report><card:addressbook-multiget/></d:report></d:supported-report>"
        "<d:supported-report><d:report><card:addressbook-query/></d:report></d:supported-report>"
        "<d:supported-report><d:report><d:sync-collection/></d:report></d:supported-report>"
        "</d:supported-report-set>"
    )


def _contact_props(contact: dict[str, str]) -> str:
    return (
        f'<d:getetag>"{contact["etag"]}"</d:getetag>'
        "<d:getcontenttype>text/vcard; charset=utf-8</d:getcontenttype>"
    )


def _report_kind(body: bytes) -> tuple[str, str, list[str]]:
    if not body.strip():
        return "addressbook-query", "", []
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        return "addressbook-query", "", []
    kind = _local(root.tag)
    token = ""
    hrefs: list[str] = []
    for node in root.iter():
        name = _local(node.tag)
        if name == "sync-token" and node.text:
            token = node.text.strip()
        elif name == "href" and node.text:
            hrefs.append(node.text.strip())
        elif name in {"addressbook-multiget", "sync-collection", "addressbook-query"}:
            kind = name
    return kind, token, hrefs


def _selected(contacts: list[dict[str, str]], hrefs: list[str]) -> list[dict[str, str]]:
    if not hrefs:
        return contacts
    wanted = {ident for href in hrefs if (ident := _href_id(href))}
    if not wanted:
        return contacts
    return [item for item in contacts if item["id"] in wanted]


def serve_carddav(
    method: str,
    path: str,
    authorization: str | None,
    depth: str,
    body: bytes,
    db_path: Path,
    token: str,
) -> tuple[int, dict[str, str], bytes]:
    """Trả status, header và nội dung. Số chỉ được đọc sau khi iPhone xác thực."""
    clean = path.split("?", 1)[0]
    if clean != "/" and clean.endswith("/") and clean != _PRINCIPAL and clean != _HOME and clean != _BOOK:
        clean = clean.rstrip("/")
    if clean in {"/.well-known/carddav", "/carddav", "/carddav/"}:
        return 301, {"Location": _PRINCIPAL}, b""
    if method == "OPTIONS":
        return (
            200,
            {
                "Allow": "OPTIONS, GET, HEAD, PROPFIND, REPORT",
                "DAV": "1, 3, addressbook",
            },
            b"",
        )
    if not _basic_ok(authorization, token):
        return 401, {"WWW-Authenticate": 'Basic realm="Danh ba"'}, b""

    headers = {
        "Content-Type": "application/xml; charset=utf-8",
        "Cache-Control": "no-store",
        "DAV": "1, 3, addressbook",
    }
    contacts = list_contacts(db_path)
    by_id = {item["id"]: item for item in contacts}
    ctag = _ctag(contacts)
    deep = depth.strip() in {"1", "infinity"}

    if method == "GET" and clean.startswith(_BOOK) and clean != _BOOK.rstrip("/"):
        ident = _href_id(clean)
        contact = by_id.get(ident or "")
        if contact is None:
            return 404, {}, b""
        card = render_vcard(contact).encode("utf-8")
        return (
            200,
            {
                "Content-Type": "text/vcard; charset=utf-8",
                "ETag": f'"{contact["etag"]}"',
                "Cache-Control": "no-store",
            },
            card,
        )

    if method == "PROPFIND" and clean in {_PRINCIPAL.rstrip("/"), _PRINCIPAL}:
        return 207, headers, _multistatus(_response(_PRINCIPAL, _principal_props()))

    if method == "PROPFIND" and clean in {_HOME.rstrip("/"), _HOME}:
        parts = [_response(_HOME, "<d:resourcetype><d:collection/></d:resourcetype><d:displayname>Danh ba</d:displayname>")]
        if deep:
            parts.append(_response(_BOOK, _book_props(ctag)))
        return 207, headers, _multistatus("".join(parts))

    if method == "PROPFIND" and clean in {_BOOK.rstrip("/"), _BOOK}:
        parts = [_response(_BOOK, _book_props(ctag))]
        if deep:
            for contact in contacts:
                href = f"{_BOOK}{contact['id']}.vcf"
                parts.append(_response(href, _contact_props(contact)))
        return 207, headers, _multistatus("".join(parts))

    ident = _href_id(clean)
    if method == "PROPFIND" and ident:
        contact = by_id.get(ident)
        if contact is None:
            return 404, {}, b""
        href = f"{_BOOK}{contact['id']}.vcf"
        return 207, headers, _multistatus(_response(href, _contact_props(contact)))

    if method == "REPORT" and clean in {_BOOK.rstrip("/"), _BOOK}:
        kind, sync_token, hrefs = _report_kind(body)
        current = f"http://danhba/sync/{ctag}"
        if kind == "sync-collection" and sync_token == current:
            return 207, headers, _multistatus(f"<d:sync-token>{current}</d:sync-token>")
        chosen = _selected(contacts, hrefs if kind == "addressbook-multiget" else [])
        parts: list[str] = []
        for contact in chosen:
            href = f"{_BOOK}{contact['id']}.vcf"
            if kind == "sync-collection":
                parts.append(_response(href, _contact_props(contact)))
            else:
                card = _xml(render_vcard(contact))
                parts.append(
                    _response(
                        href,
                        _contact_props(contact) + f"<card:address-data>{card}</card:address-data>",
                    )
                )
        token_xml = f"<d:sync-token>{current}</d:sync-token>" if kind == "sync-collection" else ""
        return 207, headers, _multistatus("".join(parts) + token_xml)

    return 404, {}, b""
