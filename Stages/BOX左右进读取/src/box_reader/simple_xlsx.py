from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from itertools import chain
from pathlib import Path
from xml.sax.saxutils import escape
from zipfile import ZIP_DEFLATED, ZipFile


def _col_name(index: int) -> str:
    result = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        result = chr(65 + remainder) + result
    return result


def _cell(ref: str, value, style: int = 0) -> str:
    style_attr = f' s="{style}"' if style else ""
    if value is None or value == "":
        return f'<c r="{ref}"{style_attr}/>'
    if isinstance(value, bool):
        return f'<c r="{ref}" t="b"{style_attr}><v>{1 if value else 0}</v></c>'
    if isinstance(value, (int, float)):
        return f'<c r="{ref}"{style_attr}><v>{value}</v></c>'
    return f'<c r="{ref}" t="inlineStr"{style_attr}><is><t xml:space="preserve">{escape(str(value))}</t></is></c>'


def write_results_xlsx(
    path: Path,
    result_rows: Iterable[Sequence[object]],
    diagnostic_rows: Iterable[Sequence[object]],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    headers = [
        "源文件", "零件名称", "规格", "板件角色",
        "左进(mm)", "右进(mm)", "原始左进(mm)", "原始右进(mm)",
        "状态", "置信度", "证据/警告", "校验图路径",
    ]
    diag_headers = [
        "源文件", "零件号", "状态", "测量规则", "输出单位",
        "$INSUNITS代码", "$INSUNITS单位", "标题栏出图比例", "标题比例参与换算",
        "mm/DXF单位", "单位校验", "校验模式", "主视图块",
        "主视图最左X(DXF)", "主视图最右X(DXF)", "主视图长度(mm)", "主视图高度(mm)",
        "板件1(角色:左进/右进)", "板件2", "板件3", "板件4",
        "警告", "校验图路径",
    ]

    def sheet_xml(
        rows: Iterable[Sequence[object]],
        sheet_headers: list[str],
        warning_col: int,
        widths: list[float],
    ) -> str:
        xml_rows = []
        last_row_index = 1
        for row_index, row in enumerate(chain((sheet_headers,), rows), 1):
            last_row_index = row_index
            cells = []
            warning = row_index > 1 and warning_col < len(row) and str(row[warning_col]).startswith(("WARNING", "ERROR"))
            review = row_index > 1 and warning_col < len(row) and str(row[warning_col]).startswith("REVIEW")
            for col_index, value in enumerate(row, 1):
                style = 1 if row_index == 1 else (2 if warning else (3 if review else 0))
                cells.append(_cell(f"{_col_name(col_index)}{row_index}", value, style))
            xml_rows.append(f'<row r="{row_index}">{"".join(cells)}</row>')
        cols = "".join(f'<col min="{i}" max="{i}" width="{width}" customWidth="1"/>' for i, width in enumerate(widths, 1))
        last = f"{_col_name(len(sheet_headers))}{last_row_index}"
        return f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>
<cols>{cols}</cols><sheetData>{''.join(xml_rows)}</sheetData><autoFilter ref="A1:{last}"/>
</worksheet>'''

    sheet1 = sheet_xml(result_rows, headers, 10, [22, 24, 20, 12, 12, 12, 16, 16, 34, 12, 70, 52])
    sheet2 = sheet_xml(
        diagnostic_rows, diag_headers, 2,
        [22, 20, 34, 24, 12, 14, 18, 18, 16, 14, 18, 22, 18, 18, 18, 16, 16,
         12, 12, 12, 12, 12, 12, 72, 52],
    )
    styles = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
<fonts count="4"><font><sz val="11"/><name val="Aptos"/></font><font><b/><color rgb="FFFFFFFF"/><sz val="11"/><name val="Aptos"/></font><font><b/><color rgb="FF9C0006"/><sz val="11"/><name val="Aptos"/></font><font><color rgb="FF9C6500"/><sz val="11"/><name val="Aptos"/></font></fonts>
<fills count="5"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill><fill><patternFill patternType="solid"><fgColor rgb="FF1F4E78"/><bgColor indexed="64"/></patternFill></fill><fill><patternFill patternType="solid"><fgColor rgb="FFFFC7CE"/><bgColor indexed="64"/></patternFill></fill><fill><patternFill patternType="solid"><fgColor rgb="FFFFEB9C"/><bgColor indexed="64"/></patternFill></fill></fills>
<borders count="2"><border/><border><left style="thin"><color rgb="FFD9E1F2"/></left><right style="thin"><color rgb="FFD9E1F2"/></right><top style="thin"><color rgb="FFD9E1F2"/></top><bottom style="thin"><color rgb="FFD9E1F2"/></bottom></border></borders>
<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
<cellXfs count="4"><xf numFmtId="0" fontId="0" fillId="0" borderId="1" xfId="0" applyBorder="1"/><xf numFmtId="0" fontId="1" fillId="2" borderId="1" xfId="0" applyFont="1" applyFill="1" applyBorder="1"/><xf numFmtId="0" fontId="2" fillId="3" borderId="1" xfId="0" applyFont="1" applyFill="1" applyBorder="1"/><xf numFmtId="0" fontId="3" fillId="4" borderId="1" xfId="0" applyFont="1" applyFill="1" applyBorder="1"/></cellXfs>
<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>
</styleSheet>'''
    content_types = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/><Override PartName="/xl/worksheets/sheet2.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/><Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/><Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/></Types>'''
    rels = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/><Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/></Relationships>'''
    workbook = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="左右进结果" sheetId="1" r:id="rId1"/><sheet name="三步诊断" sheetId="2" r:id="rId2"/></sheets></workbook>'''
    workbook_rels = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet2.xml"/><Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>'''
    now = datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
    core = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?><cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"><dc:creator>BOX Left Right Reader</dc:creator><dcterms:created xsi:type="dcterms:W3CDTF">{now}</dcterms:created></cp:coreProperties>'''
    app = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties" xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes"><Application>BOX Left Right Reader</Application></Properties>'''
    with ZipFile(path, "w", ZIP_DEFLATED) as archive:
        for name, content in {
            "[Content_Types].xml": content_types, "_rels/.rels": rels, "xl/workbook.xml": workbook,
            "xl/_rels/workbook.xml.rels": workbook_rels, "xl/styles.xml": styles,
            "xl/worksheets/sheet1.xml": sheet1, "xl/worksheets/sheet2.xml": sheet2,
            "docProps/core.xml": core, "docProps/app.xml": app,
        }.items():
            archive.writestr(name, content)
