"""Ghi file Excel .xlsx một trang, không cần thư viện ngoài.

Mọi ô là chữ, nên số điện thoại giữ số 0 đầu và tiếng Việt hiện đúng khi mở bằng Excel.
"""

from __future__ import annotations

import io
import zipfile
from collections.abc import Iterable
from xml.sax.saxutils import escape

_CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>
</Types>"""

_ROOT_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>"""

_WORKBOOK_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>"""

# Kiểu 1 là chữ đậm cho dòng tiêu đề. Kiểu 0 là chữ thường, định dạng @ (văn bản).
_STYLES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font><font><b/><sz val="11"/><name val="Calibri"/></font></fonts>
<fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills>
<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>
<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
<cellXfs count="2"><xf numFmtId="49" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/><xf numFmtId="49" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1" applyNumberFormat="1"/></cellXfs>
<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>
</styleSheet>"""


def _text(value: object) -> str:
    """Bỏ ký tự điều khiển XML không cho phép, giữ tab và xuống dòng."""
    raw = str(value if value is not None else "")
    return escape("".join(ch for ch in raw if ch in "\t\n\r" or ord(ch) >= 0x20))


def _column(index: int) -> str:
    name = ""
    index += 1
    while index:
        index, rest = divmod(index - 1, 26)
        name = chr(65 + rest) + name
    return name


def _cell(ref: str, value: object, style: int) -> str:
    return f'<c r="{ref}" t="inlineStr" s="{style}"><is><t xml:space="preserve">{_text(value)}</t></is></c>'


def workbook(sheet: str, header: list[str], rows: Iterable[list[object]], widths: list[int] | None = None) -> bytes:
    """Một file .xlsx: dòng đầu là tiêu đề, đóng băng dòng đầu, mọi ô dạng chữ."""
    body = io.StringIO()
    body.write('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>')
    body.write('<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">')
    body.write('<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>')
    if widths:
        body.write("<cols>")
        for index, width in enumerate(widths, start=1):
            body.write(f'<col min="{index}" max="{index}" width="{int(width)}" customWidth="1"/>')
        body.write("</cols>")
    body.write("<sheetData>")
    body.write('<row r="1">' + "".join(_cell(f"{_column(i)}1", value, 1) for i, value in enumerate(header)) + "</row>")
    number = 1
    for row in rows:
        number += 1
        body.write(f'<row r="{number}">' + "".join(_cell(f"{_column(i)}{number}", value, 0) for i, value in enumerate(row)) + "</row>")
    body.write("</sheetData></worksheet>")
    book = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f'<sheets><sheet name="{_text(sheet[:31])}" sheetId="1" r:id="rId1"/></sheets></workbook>'
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", _CONTENT_TYPES)
        archive.writestr("_rels/.rels", _ROOT_RELS)
        archive.writestr("xl/workbook.xml", book)
        archive.writestr("xl/_rels/workbook.xml.rels", _WORKBOOK_RELS)
        archive.writestr("xl/styles.xml", _STYLES)
        archive.writestr("xl/worksheets/sheet1.xml", body.getvalue())
    return buffer.getvalue()
