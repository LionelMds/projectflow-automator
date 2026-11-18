"""Save cell edits into an existing workbook and leave the rest of the file alone.

openpyxl rebuilds a whole workbook from its own model when it saves, so everything
that model does not hold is lost: shapes, text boxes, cropped or in-cell pictures,
and every picture when Pillow is missing, as in the packaged application. Here the
workbook is still read with openpyxl, but only the cells edited since
``CellEdits`` was created are written, as text edits in the XML of their sheet.
Every other part of the archive is copied unchanged.

A cell can also be turned into a link (``CellEdits.set_hyperlink``): the link element
of the sheet, its target among the relationships of the sheet and the link font in
the style sheet are added the same way.
"""

from __future__ import annotations

import html
import math
import posixpath
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, time
from io import BytesIO
from pathlib import Path
from typing import Final
from xml.parsers import expat
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from openpyxl.cell.cell import Cell, MergedCell
from openpyxl.styles.numbers import BUILTIN_FORMATS_REVERSE
from openpyxl.utils.cell import (
    column_index_from_string,
    coordinate_from_string,
    get_column_letter,
    range_boundaries,
)
from openpyxl.utils.datetime import WINDOWS_EPOCH, to_excel
from openpyxl.worksheet.worksheet import Worksheet

from projectflow.exceptions import ProjectCreationError

GENERAL_FORMAT: Final[str] = "General"
UNTOUCHED_CELL: Final[tuple[object, str, tuple[object, ...]]] = (
    None,
    GENERAL_FORMAT,
    (0, 0, 0, 0, 0, None),
)
CONTENT_TYPES_PART: Final[str] = "[Content_Types].xml"
ROOT_RELATIONS_PART: Final[str] = "_rels/.rels"
# Custom number formats start at 164; lower identifiers are reserved by Excel.
FIRST_CUSTOM_NUMBER_FORMAT: Final[int] = 164
DEFAULT_XF: Final[str] = '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
ATTRIBUTE_RE = re.compile(r"""([\w:.-]+)\s*=\s*(?:"([^"]*)"|'([^']*)')""")
TAG_RE = re.compile(r"<(/?)([\w.:-]+)[^>]*?(/?)>")
RELATIONSHIPS_NAMESPACE: Final[str] = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
)
EMPTY_RELATIONS: Final[str] = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    "</Relationships>"
)
# Excel's own hyperlink colour, so a linked cell reads as clickable.
LINK_COLOR: Final[str] = "FF0563C1"
# The children of a worksheet that its schema places after the hyperlinks.
AFTER_HYPERLINKS: Final[frozenset[str]] = frozenset(
    {
        "printOptions",
        "pageMargins",
        "pageSetup",
        "headerFooter",
        "rowBreaks",
        "colBreaks",
        "customProperties",
        "cellWatches",
        "ignoredErrors",
        "smartTags",
        "drawing",
        "drawingHF",
        "legacyDrawing",
        "legacyDrawingHF",
        "picture",
        "oleObjects",
        "controls",
        "webPublishItems",
        "tableParts",
        "extLst",
        # Wraps controls and embedded objects, which all come later.
        "AlternateContent",
    },
)

# Value, number format, then everything else a cell shows (fonts, fills, link...).
CellState = tuple[object, str, tuple[object, ...]]


@dataclass(frozen=True, slots=True)
class _Link:
    reference: str
    target: str
    tooltip: str


@dataclass(frozen=True, slots=True, order=True)
class _Change:
    row: int
    column: int
    value: object
    value_changed: bool
    # None when the number format of the cell is unchanged.
    number_format: str | None

    @property
    def reference(self) -> str:
        return f"{get_column_letter(self.column)}{self.row}"


class CellEdits:
    """Remember the cells of a loaded worksheet, then save only those edited since.

    Values and number formats are saved, and ``set_hyperlink`` links a cell. Any other
    edit made through openpyxl (font, fill, border, alignment, hyperlink) is refused
    instead of being silently dropped.
    """

    def __init__(self, worksheet: Worksheet) -> None:
        self._worksheet = worksheet
        self._before = _cell_states(worksheet)
        self._links: dict[str, _Link] = {}

    def set_hyperlink(self, coordinate: str, target: str, *, tooltip: str = "") -> None:
        """Link a cell to ``target`` (a URL, or a path relative to the workbook) on save.

        The cell is then shown as a link, underlined in Excel's link colour. Its value
        is set through openpyxl like any other, and it must have one. A link the cell
        already has, with this target and tooltip, is left exactly as it is.
        """
        self._links[coordinate] = _Link(coordinate, target, tooltip)

    def save(self, path: Path) -> bool:
        """Write the edited cells into ``path``; False when nothing was edited."""
        changes = self._changes()
        links = self._new_links()
        if not changes and not links:
            return False
        workbook = self._worksheet.parent
        try:
            patched = _patched_archive(
                path.read_bytes(),
                self._worksheet.title,
                changes,
                links,
                workbook.epoch if workbook is not None else WINDOWS_EPOCH,
            )
        except _UnsupportedWorkbookError as exc:
            raise ProjectCreationError(
                f"Le classeur {path.name} a une structure Excel non reconnue ({exc}). "
                "Il n'a pas ete modifie.",
            ) from exc
        # Written in place, as before: the file keeps its identity and its access rights.
        path.write_bytes(patched)
        return True

    def _changes(self) -> list[_Change]:
        changes: list[_Change] = []
        for coordinate, (value, number_format, look) in _cell_states(self._worksheet).items():
            old_value, old_format, old_look = self._before.get(coordinate, UNTOUCHED_CELL)
            if look != old_look:
                raise NotImplementedError(
                    f"Cellule {coordinate}: seuls la valeur et le format de nombre d'une "
                    "cellule peuvent etre enregistres sans reecrire le classeur.",
                )
            value_changed = _comparable(value) != _comparable(old_value)
            format_changed = number_format != old_format
            if not value_changed and not format_changed:
                continue
            letters, row = coordinate_from_string(coordinate)
            changes.append(
                _Change(
                    row=row,
                    column=column_index_from_string(letters),
                    value=value,
                    value_changed=value_changed,
                    number_format=number_format if format_changed else None,
                ),
            )
        return sorted(changes)

    def _new_links(self) -> list[_Link]:
        links: list[_Link] = []
        for link in self._links.values():
            cell = self._worksheet[link.reference]
            if cell.value is None:
                raise ValueError(f"Cellule {link.reference}: un lien demande une valeur.")
            current = cell.hyperlink
            if (
                current is not None
                and current.target == link.target
                and (current.tooltip or "") == link.tooltip
            ):
                continue
            links.append(link)
        return links


class _UnsupportedWorkbookError(Exception):
    """The archive does not look like what Excel, LibreOffice or openpyxl write."""


def _cell_states(worksheet: Worksheet) -> dict[str, CellState]:
    # The public iterators walk the whole bounding rectangle, which one stray cell at the
    # bottom of the sheet turns into millions of cells; only the stored ones matter here.
    return {
        cell.coordinate: (cell.value, cell.number_format, _look(cell))
        for cell in list(worksheet._cells.values())  # noqa: SLF001
        if not isinstance(cell, MergedCell)
    }


def _look(cell: Cell) -> tuple[object, ...]:
    # openpyxl keeps the style indexes of a cell in a private array, absent from its stubs.
    style = cell._style  # type: ignore[attr-defined]  # noqa: SLF001
    link = cell.hyperlink
    return (
        style.fontId,
        style.fillId,
        style.borderId,
        style.alignmentId,
        style.protectionId,
        None if link is None else (link.target, link.location, link.tooltip),
    )


def _comparable(value: object) -> object:
    if isinstance(value, date) and not isinstance(value, datetime):
        return datetime.combine(value, time())
    return value


def _patched_archive(
    source: bytes,
    sheet_title: str,
    changes: list[_Change],
    links: list[_Link],
    epoch: datetime,
) -> bytes:
    with ZipFile(BytesIO(source)) as archive:
        workbook_part = _related_part(_text(archive, ROOT_RELATIONS_PART), "", "officeDocument")
        if workbook_part is None:
            raise _UnsupportedWorkbookError("classeur introuvable dans l'archive")
        relations_part = _relations_part(workbook_part)
        relations = _text(archive, relations_part)
        sheet_part = _sheet_part(
            _text(archive, workbook_part), relations, workbook_part, sheet_title
        )
        styles_part = _related_part(relations, workbook_part, "styles")
        styles = _Styles(_text(archive, styles_part) if styles_part else None)
        sheet = _Sheet(_text(archive, sheet_part))
        for change in changes:
            sheet.write(change, styles, epoch)

        replaced: dict[str, str] = {}
        added: dict[str, str] = {}
        removed: set[str] = set()
        if links:
            sheet_relations_part = _relations_part(sheet_part)
            is_new = sheet_relations_part not in archive.namelist()
            sheet_relations = EMPTY_RELATIONS if is_new else _text(archive, sheet_relations_part)
            for link in links:
                sheet_relations = sheet.link(link, sheet_relations, styles)
            (added if is_new else replaced)[sheet_relations_part] = sheet_relations
        replaced[sheet_part] = sheet.xml
        if styles.changed and styles_part and styles.xml is not None:
            replaced[styles_part] = styles.xml
        chain_part = _related_part(relations, workbook_part, "calcChain")
        if sheet.dropped_formula and chain_part:
            # A calculation chain naming a cell without formula makes Excel report the
            # file as damaged; Excel rebuilds the chain when it is missing.
            removed.add(chain_part)
            replaced[relations_part] = _without_elements(
                relations,
                "Relationship",
                lambda attributes: attributes.get("Type", "").endswith("/calcChain"),
            )
            replaced[CONTENT_TYPES_PART] = _without_elements(
                _text(archive, CONTENT_TYPES_PART),
                "Override",
                lambda attributes: attributes.get("PartName") == f"/{chain_part}",
            )

        return _rewritten(archive, replaced=replaced, added=added, removed=removed)


def _rewritten(
    archive: ZipFile,
    *,
    replaced: dict[str, str],
    added: dict[str, str],
    removed: set[str],
) -> bytes:
    """Copy the archive, swapping only the parts that were edited."""
    output = BytesIO()
    with ZipFile(output, "w") as target:
        for info in archive.infolist():
            if info.filename in removed:
                continue
            if info.filename in replaced:
                content = replaced[info.filename].encode("utf-8")
                _assert_well_formed(info.filename, content)
            else:
                content = archive.read(info)
            target.writestr(_same_entry(info), content)
        for part, text in added.items():
            content = text.encode("utf-8")
            _assert_well_formed(part, content)
            target.writestr(part, content, compress_type=ZIP_DEFLATED)
    return output.getvalue()


def _same_entry(info: ZipInfo) -> ZipInfo:
    entry = ZipInfo(info.filename, info.date_time)
    entry.compress_type = info.compress_type
    entry.external_attr = info.external_attr
    return entry


def _text(archive: ZipFile, part: str) -> str:
    try:
        return archive.read(part).decode("utf-8")
    except KeyError as exc:
        raise _UnsupportedWorkbookError(f"partie {part} absente") from exc
    except UnicodeDecodeError as exc:
        raise _UnsupportedWorkbookError(f"partie {part} non UTF-8") from exc


def _assert_well_formed(part: str, content: bytes) -> None:
    try:
        expat.ParserCreate().Parse(content, True)  # noqa: FBT003 - expat's positional API
    except expat.ExpatError as exc:
        raise _UnsupportedWorkbookError(f"partie {part} illisible apres modification") from exc


def _relations_part(part: str) -> str:
    directory, name = posixpath.split(part)
    return posixpath.join(directory, "_rels", f"{name}.rels")


def _related_part(relations: str, source_part: str, kind: str) -> str | None:
    for attributes in _start_tags(relations, "Relationship"):
        if attributes.get("Type", "").endswith(f"/{kind}"):
            return _resolve(source_part, attributes.get("Target", ""))
    return None


def _resolve(source_part: str, target: str) -> str:
    if target.startswith("/"):
        return target.lstrip("/")
    return posixpath.normpath(posixpath.join(posixpath.dirname(source_part), target))


def _sheet_part(workbook: str, relations: str, workbook_part: str, title: str) -> str:
    relation_id = None
    for attributes in _start_tags(workbook, "sheet"):
        if attributes.get("name") == title:
            relation_id = next(
                (value for name, value in attributes.items() if name.endswith(":id")),
                None,
            )
            break
    for attributes in _start_tags(relations, "Relationship"):
        if relation_id is not None and attributes.get("Id") == relation_id:
            return _resolve(workbook_part, attributes.get("Target", ""))
    raise _UnsupportedWorkbookError(f"feuille {title} introuvable")


def _attributes(tag: str) -> dict[str, str]:
    return {
        match[1]: html.unescape(match[2] if match[2] is not None else match[3])
        for match in ATTRIBUTE_RE.finditer(tag)
    }


def _start_tags(xml: str, local_name: str) -> list[dict[str, str]]:
    """Attributes of every element with this local name, whatever its namespace prefix."""
    pattern = re.compile(rf"<(?:[\w.-]+:)?{local_name}(?=[\s/>])[^>]*>")
    return [_attributes(match[0]) for match in pattern.finditer(xml)]


def _without_elements(
    xml: str,
    local_name: str,
    drop: Callable[[dict[str, str]], bool],
) -> str:
    pattern = re.compile(
        rf"<(?:[\w.-]+:)?{local_name}(?=[\s/>])[^>]*?(?:/>|>.*?</(?:[\w.-]+:)?{local_name}\s*>)",
        re.DOTALL,
    )
    return pattern.sub(lambda match: "" if drop(_attributes(match[0])) else match[0], xml)


def _element_re(name: str) -> re.Pattern[str]:
    """Match one element: group 1 is its attributes, group 2 its content (None if empty)."""
    return re.compile(rf"<{name}(?=[\s/>])([^>]*?)(?:/>|>(.*?)</{name}\s*>)", re.DOTALL)


def _with_attribute(attributes: str, name: str, value: str) -> str:
    pattern = re.compile(rf"""(?<![\w:.-]){name}\s*=\s*(?:"[^"]*"|'[^']*')""")
    assignment = f'{name}="{_escape_attribute(value)}"'
    if pattern.search(attributes):
        return pattern.sub(lambda _match: assignment, attributes, count=1)
    return f"{attributes.rstrip()} {assignment}"


def _without_attribute(attributes: str, name: str) -> str:
    return re.sub(rf"""\s*(?<![\w:.-]){name}\s*=\s*(?:"[^"]*"|'[^']*')""", "", attributes, count=1)


def _escape_text(value: str) -> str:
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _escape_attribute(value: str) -> str:
    return _escape_text(value).replace('"', "&quot;")


def _prefix(xml: str, root: str) -> str:
    match = re.search(rf"<((?:[\w.-]+:)?){root}(?=[\s/>])", xml)
    if match is None:
        raise _UnsupportedWorkbookError(f"element {root} absent")
    return match[1]


def _append_child(xml: str, container: str, child: str) -> str | None:
    """Add ``child`` at the end of ``container`` and keep its ``count`` attribute right."""
    match = _element_re(container).search(xml)
    if match is None:
        return None
    attributes = match[1]
    count = _attributes(attributes).get("count")
    if count is not None and count.isdigit():
        attributes = _with_attribute(attributes, "count", str(int(count) + 1))
    content = match[2] or ""
    return (
        f"{xml[: match.start()]}<{container}{attributes}>{content}{child}"
        f"</{container}>{xml[match.end() :]}"
    )


def _top_level_children(xml: str) -> list[tuple[str, int]]:
    """Local name and position of every child of the root element, in document order."""
    children: list[tuple[str, int]] = []
    depth = 0
    for tag in TAG_RE.finditer(xml):
        if tag[1]:
            depth -= 1
            continue
        if depth == 1:
            children.append((tag[2].rpartition(":")[2], tag.start()))
        if not tag[3]:
            depth += 1
    return children


def _with_external_relation(
    relations: str,
    relation_id: str | None,
    kind: str,
    target: str,
) -> tuple[str, str]:
    """Point ``relation_id`` at ``target``, or add a relationship when there is none.

    Returns the relationships and the identifier to name in the sheet.
    """
    element_re = _element_re("Relationship")
    found: re.Match[str] | None = None
    identifiers: set[str] = set()
    for match in element_re.finditer(relations):
        identifier = _attributes(match[1]).get("Id", "")
        identifiers.add(identifier)
        if relation_id is not None and identifier == relation_id:
            found = match
    if found is None or relation_id is None:
        number = 1
        while f"rId{number}" in identifiers:
            number += 1
        relation_id = f"rId{number}"
    element = (
        f'<Relationship Id="{relation_id}" Type="{_escape_attribute(kind)}" '
        f'Target="{_escape_attribute(target)}" TargetMode="External"/>'
    )
    if found is not None:
        return f"{relations[: found.start()]}{element}{relations[found.end() :]}", relation_id
    grown = _append_child(relations, "Relationships", element)
    if grown is None:
        raise _UnsupportedWorkbookError("relations de la feuille illisibles")
    return grown, relation_id


def _link_font(content: str, prefix: str) -> str:
    """Return the children of a font element, underlined and in the link colour."""
    for name in ("u", "color"):
        content = _element_re(f"{prefix}{name}").sub("", content)
    underline = f"<{prefix}u/>"
    color = f'<{prefix}color rgb="{LINK_COLOR}"/>'
    # Excel writes the underline before the size and the colour right after it.
    size = _element_re(f"{prefix}sz").search(content)
    if size is None:
        return f"{underline}{color}{content}"
    return (
        f"{content[: size.start()]}{underline}{content[size.start() : size.end()]}"
        f"{color}{content[size.end() :]}"
    )


class _Styles:
    """The style sheet of the workbook, grown only when a cell needs a new format."""

    def __init__(self, xml: str | None) -> None:
        self.xml = xml
        self.changed = False

    def with_number_format(self, style: str | None, number_format: str) -> str:
        """Return the index of ``style`` shown with ``number_format``, adding it if new."""
        if self.xml is None:
            raise _UnsupportedWorkbookError("feuille de styles absente")
        prefix = _prefix(self.xml, "styleSheet")
        format_id = self._number_format_id(prefix, number_format)
        formats = _element_re(f"{prefix}cellXfs").search(self.xml)
        if formats is None:
            raise _UnsupportedWorkbookError("formats de cellule absents")
        xf_re = _element_re(f"{prefix}xf")
        existing = [match[0] for match in xf_re.finditer(formats[2] or "")]
        index = int(style) if style is not None and style.isdigit() else 0
        base = xf_re.fullmatch(existing[index] if index < len(existing) else DEFAULT_XF)
        if base is None:
            raise _UnsupportedWorkbookError("format de cellule illisible")
        attributes = _with_attribute(base[1], "numFmtId", str(format_id))
        attributes = _with_attribute(attributes, "applyNumberFormat", "1")
        wanted = (
            f"<{prefix}xf{attributes}/>"
            if base[2] is None
            else f"<{prefix}xf{attributes}>{base[2]}</{prefix}xf>"
        )
        if wanted in existing:
            return str(existing.index(wanted))
        self._append(f"{prefix}cellXfs", wanted)
        return str(len(existing))

    def with_link_font(self, style: str | None) -> str:
        """Return the index of ``style`` shown as a link, adding font and format if new."""
        if self.xml is None:
            raise _UnsupportedWorkbookError("feuille de styles absente")
        prefix = _prefix(self.xml, "styleSheet")
        formats = _element_re(f"{prefix}cellXfs").search(self.xml)
        if formats is None:
            raise _UnsupportedWorkbookError("formats de cellule absents")
        xf_re = _element_re(f"{prefix}xf")
        existing = [match[0] for match in xf_re.finditer(formats[2] or "")]
        index = int(style) if style is not None and style.isdigit() else 0
        base = xf_re.fullmatch(existing[index] if index < len(existing) else DEFAULT_XF)
        if base is None:
            raise _UnsupportedWorkbookError("format de cellule illisible")

        fonts = _element_re(f"{prefix}fonts").search(self.xml)
        if fonts is None:
            raise _UnsupportedWorkbookError("polices absentes")
        font_matches = list(_element_re(f"{prefix}font").finditer(fonts[2] or ""))
        font_index = _index(_attributes(base[1]).get("fontId"), 0)
        base_font = font_matches[font_index][2] or "" if font_index < len(font_matches) else ""
        link_font = f"<{prefix}font>{_link_font(base_font, prefix)}</{prefix}font>"
        known_fonts = [match[0] for match in font_matches]
        if link_font in known_fonts:
            link_font_index = known_fonts.index(link_font)
        else:
            self._append(f"{prefix}fonts", link_font)
            link_font_index = len(known_fonts)

        attributes = _with_attribute(base[1], "fontId", str(link_font_index))
        attributes = _with_attribute(attributes, "applyFont", "1")
        wanted = (
            f"<{prefix}xf{attributes}/>"
            if base[2] is None
            else f"<{prefix}xf{attributes}>{base[2]}</{prefix}xf>"
        )
        if wanted in existing:
            return str(existing.index(wanted))
        self._append(f"{prefix}cellXfs", wanted)
        return str(len(existing))

    def _number_format_id(self, prefix: str, number_format: str) -> int:
        builtin = BUILTIN_FORMATS_REVERSE.get(number_format)
        if builtin is not None:
            return int(builtin)
        custom = {
            int(attributes["numFmtId"]): attributes.get("formatCode", "")
            for attributes in _start_tags(self.xml or "", "numFmt")
            if attributes.get("numFmtId", "").isdigit()
        }
        for format_id, code in custom.items():
            if code == number_format:
                return format_id
        format_id = max([FIRST_CUSTOM_NUMBER_FORMAT - 1, *custom]) + 1
        child = (
            f'<{prefix}numFmt numFmtId="{format_id}" '
            f'formatCode="{_escape_attribute(number_format)}"/>'
        )
        if _element_re(f"{prefix}numFmts").search(self.xml or "") is None:
            # numFmts is the first child of the style sheet.
            root = re.search(rf"<{prefix}styleSheet(?=[\s/>])[^>]*>", self.xml or "")
            if root is None or root[0].endswith("/>"):
                raise _UnsupportedWorkbookError("feuille de styles vide")
            self.xml = (
                f'{(self.xml or "")[: root.end()]}<{prefix}numFmts count="1">{child}'
                f"</{prefix}numFmts>{(self.xml or '')[root.end() :]}"
            )
            self.changed = True
        else:
            self._append(f"{prefix}numFmts", child)
        return format_id

    def _append(self, container: str, child: str) -> None:
        grown = _append_child(self.xml or "", container, child)
        if grown is None:
            raise _UnsupportedWorkbookError(f"element {container} absent")
        self.xml = grown
        self.changed = True


class _Sheet:
    """The XML of one worksheet, edited as text so that untouched content stays as it is."""

    def __init__(self, xml: str) -> None:
        self.xml = xml
        self.dropped_formula = False
        self._prefix = _prefix(xml, "worksheet")
        self._row_re = _element_re(f"{self._prefix}row")
        self._cell_re = _element_re(f"{self._prefix}c")
        self._formula_re = re.compile(rf"<{self._prefix}f(?=[\s/>])")

    def write(self, change: _Change, styles: _Styles, epoch: datetime) -> None:
        data = _element_re(f"{self._prefix}sheetData").search(self.xml)
        if data is None:
            raise _UnsupportedWorkbookError("donnees de feuille absentes")
        if data[2] is None:
            new_row = self._new_row(change, styles, epoch)
            name = f"{self._prefix}sheetData"
            self._replace(data.start(), data.end(), f"<{name}{data[1]}>{new_row}</{name}>")
            self._grow_dimension(change)
            return
        number = 0
        for row in self._row_re.finditer(self.xml, data.start(2), data.end(2)):
            number = _index(_attributes(row[1]).get("r"), number + 1)
            if number == change.row:
                self._write_in_row(row, change, styles, epoch)
                return
            if number > change.row:
                self._replace(row.start(), row.start(), self._new_row(change, styles, epoch))
                self._grow_dimension(change)
                return
        self._replace(data.end(2), data.end(2), self._new_row(change, styles, epoch))
        self._grow_dimension(change)

    def link(self, link: _Link, relations: str, styles: _Styles) -> str:
        """Make a cell a link to an external target; return the grown relationships."""
        self._restyle(link.reference, styles.with_link_font)
        name = f"{self._prefix}hyperlink"
        container = _element_re(f"{name}s").search(self.xml)
        existing: re.Match[str] | None = None
        if container is not None and container[2] is not None:
            for match in _element_re(name).finditer(self.xml, container.start(2), container.end(2)):
                if _attributes(match[1]).get("ref") == link.reference:
                    existing = match
                    break
        relation_id: str | None = None
        if existing is not None:
            relation_id = next(
                (
                    value
                    for attribute, value in _attributes(existing[1]).items()
                    if attribute.endswith(":id")
                ),
                None,
            )
            pattern = rf""":id\s*=\s*["']{re.escape(relation_id or "")}["']"""
            if relation_id is not None and len(re.findall(pattern, self.xml)) != 1:
                # Shared with something else on the sheet, which keeps its target.
                relation_id = None
        prefix, namespace, declaration = self._relationship_namespace()
        relations, relation_id = _with_external_relation(
            relations,
            relation_id,
            f"{namespace}/hyperlink",
            link.target,
        )
        tooltip = f' tooltip="{_escape_attribute(link.tooltip)}"' if link.tooltip else ""
        element = (
            f'<{name} ref="{link.reference}"{declaration} '
            f'{prefix}:id="{_escape_attribute(relation_id)}"{tooltip}/>'
        )
        if existing is not None:
            self._replace(existing.start(), existing.end(), element)
        elif container is not None:
            self._replace(
                container.start(),
                container.end(),
                f"<{name}s{container[1]}>{container[2] or ''}{element}</{name}s>",
            )
        else:
            position = self._hyperlinks_position()
            self._replace(position, position, f"<{name}s>{element}</{name}s>")
        return relations

    def _restyle(self, reference: str, restyled: Callable[[str | None], str]) -> None:
        letters, wanted_row = coordinate_from_string(reference)
        wanted_column = column_index_from_string(letters)
        data = _element_re(f"{self._prefix}sheetData").search(self.xml)
        if data is None or data[2] is None:
            raise _UnsupportedWorkbookError(f"cellule {reference} absente")
        number = 0
        for row in self._row_re.finditer(self.xml, data.start(2), data.end(2)):
            number = _index(_attributes(row[1]).get("r"), number + 1)
            if number != wanted_row or row[2] is None:
                continue
            column = 0
            for cell in self._cell_re.finditer(self.xml, row.start(2), row.end(2)):
                attributes = _attributes(cell[1])
                cell_reference = attributes.get("r")
                column = (
                    column_index_from_string(coordinate_from_string(cell_reference)[0])
                    if cell_reference
                    else column + 1
                )
                if column != wanted_column:
                    continue
                restyled_attributes = _with_attribute(cell[1], "s", restyled(attributes.get("s")))
                name = f"{self._prefix}c"
                self._replace(
                    cell.start(),
                    cell.end(),
                    f"<{name}{restyled_attributes}/>"
                    if cell[2] is None
                    else f"<{name}{restyled_attributes}>{cell[2]}</{name}>",
                )
                return
        raise _UnsupportedWorkbookError(f"cellule {reference} absente")

    def _relationship_namespace(self) -> tuple[str, str, str]:
        """Prefix and URI of the relationships namespace, and a declaration if missing."""
        root = re.search(rf"<{self._prefix}worksheet(?=[\s/>])[^>]*>", self.xml)
        for attribute, value in _attributes(root[0] if root else "").items():
            if attribute.startswith("xmlns:") and value.endswith("/relationships"):
                return attribute.removeprefix("xmlns:"), value, ""
        return "r", RELATIONSHIPS_NAMESPACE, f' xmlns:r="{RELATIONSHIPS_NAMESPACE}"'

    def _hyperlinks_position(self) -> int:
        """Where the schema of a worksheet expects its hyperlinks."""
        for name, position in _top_level_children(self.xml):
            if name in AFTER_HYPERLINKS:
                return position
        closing = self.xml.rfind(f"</{self._prefix}worksheet")
        if closing < 0:
            raise _UnsupportedWorkbookError("fin de feuille introuvable")
        return closing

    def _write_in_row(
        self,
        row: re.Match[str],
        change: _Change,
        styles: _Styles,
        epoch: datetime,
    ) -> None:
        attributes = row[1]
        content = row[2] or ""
        column = 0
        insert_at: int | None = None
        last_cell_end = 0
        for cell in self._cell_re.finditer(content):
            reference = _attributes(cell[1]).get("r")
            column = (
                column_index_from_string(coordinate_from_string(reference)[0])
                if reference
                else column + 1
            )
            if column == change.column:
                new_cell = self._changed_cell(cell, change, styles, epoch)
                content = f"{content[: cell.start()]}{new_cell}{content[cell.end() :]}"
                self._replace_row(row, attributes, content)
                return
            if column > change.column and insert_at is None:
                insert_at = cell.start()
            last_cell_end = cell.end()
        row_attributes = _attributes(attributes)
        row_style = (
            row_attributes.get("s") if row_attributes.get("customFormat") in {"1", "true"} else None
        )
        new_cell = self._cell(
            change,
            row_style if row_style is not None else self._column_style(change.column),
            styles,
            epoch,
        )
        position = last_cell_end if insert_at is None else insert_at
        content = f"{content[:position]}{new_cell}{content[position:]}"
        # "spans" only hints at the columns in use; without it Excel works them out.
        self._replace_row(row, _without_attribute(attributes, "spans"), content)
        self._grow_dimension(change)

    def _replace_row(self, row: re.Match[str], attributes: str, content: str) -> None:
        name = f"{self._prefix}row"
        self._replace(row.start(), row.end(), f"<{name}{attributes}>{content}</{name}>")

    def _replace(self, start: int, end: int, text: str) -> None:
        self.xml = f"{self.xml[:start]}{text}{self.xml[end:]}"

    def _new_row(self, change: _Change, styles: _Styles, epoch: datetime) -> str:
        cell = self._cell(change, self._column_style(change.column), styles, epoch)
        return f'<{self._prefix}row r="{change.row}">{cell}</{self._prefix}row>'

    def _changed_cell(
        self,
        cell: re.Match[str],
        change: _Change,
        styles: _Styles,
        epoch: datetime,
    ) -> str:
        style = _attributes(cell[1]).get("s")
        if change.value_changed:
            if cell[2] is not None and self._formula_re.search(cell[2]):
                self.dropped_formula = True
            return self._cell(change, style, styles, epoch)
        # Only the number format changed: the value, formula included, stays as written.
        if change.number_format is None:
            return cell[0]
        attributes = _with_attribute(
            cell[1],
            "s",
            styles.with_number_format(style, change.number_format),
        )
        name = f"{self._prefix}c"
        return (
            f"<{name}{attributes}/>"
            if cell[2] is None
            else f"<{name}{attributes}>{cell[2]}</{name}>"
        )

    def _cell(
        self,
        change: _Change,
        style: str | None,
        styles: _Styles,
        epoch: datetime,
    ) -> str:
        if change.number_format is not None:
            style = styles.with_number_format(style, change.number_format)
        prefix = self._prefix
        attributes = f' r="{change.reference}"'
        if style is not None:
            attributes += f' s="{_escape_attribute(style)}"'
        value = change.value if change.value_changed else None
        if value is None:
            return f"<{prefix}c{attributes}/>"
        if isinstance(value, str):
            # Inline text keeps the edit inside the sheet; Excel moves it to the shared
            # strings the next time it saves the workbook.
            space = ' xml:space="preserve"' if value != value.strip() or "\n" in value else ""
            return (
                f'<{prefix}c{attributes} t="inlineStr"><{prefix}is><{prefix}t{space}>'
                f"{_escape_text(value)}</{prefix}t></{prefix}is></{prefix}c>"
            )
        if isinstance(value, bool):
            return f'<{prefix}c{attributes} t="b"><{prefix}v>{int(value)}</{prefix}v></{prefix}c>'
        return f"<{prefix}c{attributes}><{prefix}v>{_number(value, epoch)}</{prefix}v></{prefix}c>"

    def _column_style(self, column: int) -> str | None:
        columns = _element_re(f"{self._prefix}cols").search(self.xml)
        if columns is None or columns[2] is None:
            return None
        for attributes in _start_tags(columns[2], "col"):
            first = _index(attributes.get("min"), 0)
            last = _index(attributes.get("max"), 0)
            if first <= column <= last:
                return attributes.get("style")
        return None

    def _grow_dimension(self, change: _Change) -> None:
        """Keep the used-range hint of the sheet covering a cell added outside it."""
        dimension = re.search(rf"<{self._prefix}dimension(?=[\s/>])([^>]*?)/>", self.xml)
        if dimension is None:
            return
        reference = _attributes(dimension[1]).get("ref", "")
        try:
            first_column, first_row, last_column, last_row = range_boundaries(reference)
        except ValueError:
            return
        if first_column is None or first_row is None or last_column is None or last_row is None:
            return
        if first_column <= change.column <= last_column and first_row <= change.row <= last_row:
            return
        grown = (
            f"{get_column_letter(min(first_column, change.column))}{min(first_row, change.row)}:"
            f"{get_column_letter(max(last_column, change.column))}{max(last_row, change.row)}"
        )
        attributes = _with_attribute(dimension[1], "ref", grown)
        self._replace(dimension.start(), dimension.end(), f"<{self._prefix}dimension{attributes}/>")


def _index(text: str | None, default: int) -> int:
    return int(text) if text is not None and text.isdigit() else default


def _number(value: object, epoch: datetime) -> str:
    if isinstance(value, date):
        return repr(to_excel(value, epoch))  # type: ignore[no-untyped-call]
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float) and math.isfinite(value):
        return repr(value)
    raise TypeError(f"Valeur de cellule non prise en charge: {value!r}")
