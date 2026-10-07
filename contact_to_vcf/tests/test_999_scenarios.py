"""999 concrete scenarios for number identity, dedupe, books, and export."""

from __future__ import annotations

import json
import sqlite3
import threading
from http.cookiejar import CookieJar
from pathlib import Path
from urllib.request import HTTPCookieProcessor, Request, build_opener

from exporters.vcf_validator import contact_label
from processors.phone_normalizer import canonical_phones, to_vietnam_10
from processors.pool import (
    CONTACTS_PER_FILE,
    book_of,
    export_book,
    import_file,
    list_books,
    pool_total,
)
from web.server import serve

PREFIXES = [
    "032", "033", "034", "035", "036", "037", "038", "039",
    "052", "056", "058", "092",
    "070", "076", "077", "078", "079",
    "081", "082", "083", "084", "085", "086", "088", "089",
    "090", "091", "093", "094", "096", "097", "098",
]
LEGACY = {
    "0162": "032", "0163": "033", "0164": "034", "0165": "035",
    "0166": "036", "0167": "037", "0168": "038", "0169": "039",
    "0120": "070", "0121": "079", "0122": "077", "0126": "076",
    "0128": "078", "0123": "083", "0124": "084", "0125": "085",
    "0127": "081", "0129": "082", "0186": "056", "0188": "058",
    "0199": "059",
}


def test_nine_hundred_ninety_nine_scenarios(tmp_path: Path) -> None:
    failures: list[str] = []
    seen = 0

    def check(name: str, ok: bool, detail: str = "") -> None:
        nonlocal seen
        seen += 1
        if not ok:
            failures.append(f"{seen}. {name}: {detail}")

    def spellings(ten: str) -> list[str]:
        national = ten[1:]
        return [
            ten,
            f"+84{national}",
            f"84{national}",
            f"{ten[:3]} {ten[3:6]} {ten[6:]}",
            f"{ten[:3]}.{ten[3:6]}.{ten[6:]}",
        ]

    def load_txt(folder: Path, name: str, text: str, per: int = 50) -> object:
        path = folder / name
        path.write_text(text, encoding="utf-8")
        return import_file(
            folder,
            path,
            file_format="txt",
            delimiter=",",
            has_header=False,
            encoding="utf-8",
            phone_column=0,
            contacts_per_file=per,
        )

    check("default book size", CONTACTS_PER_FILE == 1000, str(CONTACTS_PER_FILE))

    for prefix in PREFIXES:
        ten = prefix + "1234567"
        for sample in spellings(ten):
            got = canonical_phones(sample)
            check(
                f"spelling {sample}",
                got == [ten] and to_vietnam_10(sample) == ten,
                str(got),
            )

    for old, new in LEGACY.items():
        eleven = old + "1234567"
        ten = new + "1234567"
        check(f"legacy {eleven}", canonical_phones(eleven) == [ten], str(canonical_phones(eleven)))
        check(
            f"legacy +84 {old}",
            canonical_phones("+84" + old[1:] + "1234567") == [ten],
            str(canonical_phones("+84" + old[1:] + "1234567")),
        )

    pool = tmp_path / "dedupe"
    pool.mkdir()
    first_numbers = [f"09{index:08d}" for index in range(120)]
    load_txt(pool, "first.txt", "\n".join(first_numbers) + "\n")
    for index, ten in enumerate(first_numbers):
        sample = spellings(ten)[index % 5]
        before = pool_total(pool)
        home = book_of(pool, ten)
        stats = load_txt(pool, f"again-{index}.txt", sample + "\n")
        check(
            f"reimport {sample}",
            stats.added == 0
            and stats.duplicate == 1
            and book_of(pool, sample) == home
            and pool_total(pool) == before,
            f"added={stats.added} duplicate={stats.duplicate} book={book_of(pool, sample)}",
        )

    rejects = [
        "",
        "   ",
        "abc",
        "không phải số",
        "---",
        "++",
        "null",
        "...",
        "tên liên hệ",
        "?",
    ]
    reject_pool = tmp_path / "reject"
    reject_pool.mkdir()
    for index, line in enumerate(rejects * 10):
        before = pool_total(reject_pool)
        stats = load_txt(reject_pool, f"bad-{index}.txt", line + "\n")
        check(
            f"reject {index} {line!r}",
            stats.added == 0 and stats.rejected == 1 and pool_total(reject_pool) == before,
            f"added={stats.added} rejected={stats.rejected}",
        )

    csv_pool = tmp_path / "csv"
    csv_pool.mkdir()
    for index in range(80):
        ten = f"08{index:08d}"
        if index % 2 == 0:
            text = f"ten,so\nAn,{ten}\n"
            column = 1
            header = True
        else:
            text = f"{ten},An\n"
            column = 0
            header = False
        path = csv_pool / f"{index}.csv"
        path.write_text(text, encoding="utf-8")
        stats = import_file(
            csv_pool,
            path,
            file_format="csv",
            delimiter=",",
            has_header=header,
            encoding="utf-8",
            phone_column=column,
            contacts_per_file=50,
        )
        check(
            f"csv {index}",
            stats.added == 1 and book_of(csv_pool, ten) is not None,
            f"added={stats.added}",
        )

    frozen = tmp_path / "frozen"
    frozen.mkdir()
    for index in range(70):
        ten = f"07{index:08d}"
        load_txt(frozen, f"new-{index}.txt", ten + "\n", per=1)
        home = book_of(frozen, ten)
        export_book(frozen, home, frozen / f"{index}.vcf")
        extra = f"06{index:08d}"
        stats = load_txt(frozen, f"more-{index}.txt", f"{ten}\n{extra}\n", per=1)
        check(
            f"frozen {index}",
            stats.added == 1
            and stats.duplicate == 1
            and book_of(frozen, ten) == home
            and book_of(frozen, extra) != home,
            f"added={stats.added} home={home} extra={book_of(frozen, extra)}",
        )

    vcf_pool = tmp_path / "vcf"
    vcf_pool.mkdir()
    vcf_numbers = [f"03{index:08d}" for index in range(80)]
    load_txt(vcf_pool, "cards.txt", "\n".join(vcf_numbers) + "\n", per=80)
    for index, ten in enumerate(vcf_numbers):
        home = book_of(vcf_pool, ten)
        target = vcf_pool / f"card-{index}.vcf"
        export_book(vcf_pool, home, target)
        raw = target.read_bytes().decode("utf-8")
        label = contact_label(ten)
        check(
            f"vcf {ten}",
            f"FN:{label}\r\nTEL;TYPE=CELL:{label}\r\n" in raw and label == ten,
            "card mismatch",
        )

    multi = tmp_path / "multi"
    multi.mkdir()
    for index in range(50):
        left = f"05{index:08d}"
        right = f"04{index:08d}"
        line = f"{left} / {right}" if index % 2 == 0 else f"{left},{right}"
        stats = load_txt(multi, f"{index}.txt", line + "\n")
        check(
            f"multi {line}",
            stats.added == 2
            and book_of(multi, left) == book_of(multi, right)
            and book_of(multi, left) is not None,
            f"added={stats.added}",
        )

    split = tmp_path / "split"
    split.mkdir()
    split_numbers = [f"091{index:07d}" for index in range(40)]
    stats = load_txt(split, "split.txt", "\n".join(split_numbers) + "\n", per=7)
    counts = [book.contact_count for book in list_books(split)]
    for index, ten in enumerate(split_numbers):
        expected_book = index // 7 + 1
        check(
            f"split {ten}",
            book_of(split, ten) == expected_book and max(counts) <= 7 and stats.added == 40,
            f"book={book_of(split, ten)} expected={expected_book} counts={counts}",
        )

    coded = tmp_path / "coded"
    coded.mkdir()
    utf16 = coded / "utf16.txt"
    utf16.write_bytes("0901000001\n0901000002\n".encode("utf-16"))
    stats = import_file(
        coded,
        utf16,
        file_format="txt",
        delimiter=",",
        has_header=False,
        encoding="utf-8",
        phone_column=0,
    )
    check("utf-16", stats.added == 2 and book_of(coded, "0901000001") == 1, str(stats))
    bom = coded / "bom.txt"
    bom.write_bytes(b"\xef\xbb\xbf0901000003\n")
    stats = import_file(
        coded,
        bom,
        file_format="txt",
        delimiter=",",
        has_header=False,
        encoding="utf-8-sig",
        phone_column=0,
    )
    check("utf-8 bom", stats.added == 1 and book_of(coded, "0901000003") == 1, str(stats))
    for index in range(25):
        ten = f"0902{index:06d}"
        text = f"ghi chú {ten}\n"
        stats = load_txt(coded, f"note-{index}.txt", text)
        check(
            f"note {index}",
            stats.added == 1 and book_of(coded, ten) is not None,
            str(stats),
        )

    server = serve(tmp_path / "web", host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    opener = build_opener(HTTPCookieProcessor(CookieJar()))
    try:
        opener.open(
            Request(
                f"http://127.0.0.1:{port}/api/login",
                data=json.dumps({"user": "danhba", "password": "danhba123"}).encode(),
                headers={"Content-Type": "application/json"},
            )
        )
        for index in range(20):
            ten = f"093{index:07d}"
            body, content_type = _form({"file": (f"{index}.txt", (ten + "\n").encode())})
            imported = json.load(
                opener.open(
                    Request(
                        f"http://127.0.0.1:{port}/api/import-now",
                        data=body,
                        headers={"Content-Type": content_type},
                    )
                )
            )
            check(f"web add {ten}", imported["added"] == 1, str(imported))
            again_body, again_type = _form(
                {"file": (f"b{index}.txt", f"+84{ten[1:]}\n".encode())}
            )
            second = json.load(
                opener.open(
                    Request(
                        f"http://127.0.0.1:{port}/api/import-now",
                        data=again_body,
                        headers={"Content-Type": again_type},
                    )
                )
            )
            check(
                f"web dedupe {ten}",
                second["added"] == 0 and second["duplicate"] == 1,
                str(second),
            )
        books = json.load(opener.open(f"http://127.0.0.1:{port}/api/books"))
        bare = build_opener()
        saved = bare.open(
            f"http://127.0.0.1:{port}/iphone/{books['books'][0]['id']}.vcf?key={books['key']}"
        )
        payload = saved.read().decode()
        check(
            "iphone file",
            saved.headers.get("Content-Type") == "application/octet-stream"
            and "FN:0930000000" in payload
            and "TEL;TYPE=CELL:0930000000" in payload,
            saved.headers.get("Content-Type", ""),
        )
    finally:
        server.shutdown()

    for prefix in PREFIXES[:31]:
        for subscriber in range(6):
            ten = f"{prefix}{subscriber:07d}"
            sample = f"({ten[:3]}) {ten[3:6]}-{ten[6:]}"
            check(
                f"paren {sample}",
                canonical_phones(sample) == [ten] and contact_label(ten) == ten,
                str(canonical_phones(sample)),
            )

    rows = sqlite3.connect(pool / "kho.sqlite").execute(
        "SELECT COUNT(*), COUNT(DISTINCT phone) FROM numbers"
    ).fetchone()
    check("dedupe pool unique", rows[0] == rows[1] == 120, str(rows))
    drift = sqlite3.connect(pool / "kho.sqlite").execute(
        """
        SELECT COUNT(*) FROM (
            SELECT books.id
            FROM books LEFT JOIN numbers ON numbers.book_id = books.id
            GROUP BY books.id
            HAVING books.contact_count != COUNT(numbers.phone)
        )
        """
    ).fetchone()
    check("dedupe counts match", drift[0] == 0, str(drift))

    assert failures == [], "\n".join(failures)
    assert seen == 999, seen


def _form(files: dict[str, tuple[str, bytes]]) -> tuple[bytes, str]:
    boundary = "----danhba"
    chunks: list[bytes] = []
    for name, (filename, payload) in files.items():
        chunks.append(f"--{boundary}\r\n".encode())
        chunks.append(
            f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n\r\n'.encode()
        )
        chunks.append(payload)
        chunks.append(b"\r\n")
    chunks.append(f"--{boundary}--\r\n".encode())
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"
