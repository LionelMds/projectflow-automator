"""A3 landscape PDF and print-out of the Planning grid.

Each page repeats the title, the time header and the row column; the rows run on as many
pages as needed, never cut in two, and a team header never ends a page. The page is laid
out in logical pixels scaled to the paper so that the smallest text (10 px) prints at
7 pt at least. The same paint functions as the screen draw it, in the light theme, with
the bars in their paper state (a dashed end instead of the alpha fade of a cut bar).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QLineF, QMarginsF, QRectF, Qt
from PySide6.QtGui import QPagedPaintDevice, QPageLayout, QPageSize, QPainter, QPdfWriter
from PySide6.QtPrintSupport import QPrintDialog, QPrinter
from PySide6.QtWidgets import QDialog, QWidget

from projectflow.core.planning.layout import GridRows, RowLayout
from projectflow.ui.planning.grid import PlanningGrid
from projectflow.ui.planning.grid_items import (
    BarState,
    bar_rect,
    build_bar_style,
    build_grid_style,
    make_pen,
    paint_background,
    paint_bar,
)
from projectflow.ui.planning.grid_model import GridModel
from projectflow.ui.planning.header import HEADER_HEIGHT, paint_time_header
from projectflow.ui.planning.rows import (
    CORNER_TITLE,
    LEFT_PAD,
    ROW_COLUMN_WIDTH,
    RowFonts,
    paint_row_column,
)
from projectflow.ui.theme import (
    Theme,
    body_font,
    build_theme,
    current_theme,
    heading_font,
    load_fonts,
)

PAGE_WIDTH_PX = 1600.0
"""Logical width of the printable area; the paper scale follows from it."""
MIN_TEXT_PT = 7.0
SMALLEST_FONT_PX = 10.0
POINTS_PER_INCH = 72.0
PDF_RESOLUTION = 300
PAGE_MARGIN_MM = 10.0
TITLE_HEIGHT = 46.0
FOOTER_HEIGHT = 20.0
TITLE_FONT_PX = 22
INFO_FONT_PX = 11
TITLE_GAP = 6.0
MIN_ROWS_HEIGHT = 40.0
COMPACT_LANE = 30

PAPER = "#ffffff"
EM_DASH = "—"
TITLE_PREFIX = f"Planning {EM_DASH} "
CREATOR = "ProjectFlow"
DIALOG_TITLE = "Imprimer le planning"
FOOTER_TEXT = "ProjectFlow · planning partagé de l'atelier"


@dataclass(frozen=True, slots=True)
class PrintInfo:
    title: str
    printed: str
    poste: str


def paper_theme() -> Theme:
    """Light theme of the current palette on white paper."""
    light = build_theme("light", current_theme().palette)
    return Theme(mode=light.mode, palette=light.palette, tokens={**light.tokens, "bg": PAPER})


def print_title(model: GridModel) -> str:
    """Title of the printed page: « Planning », an em dash, then the shown period."""
    return f"{TITLE_PREFIX}{model.period_label}"


def printed_label(moment: datetime, poste: str) -> str:
    """« Imprimé le 22.10.2026 à 14:05 · BUREAU-2 »."""
    text = f"Imprimé le {moment:%d.%m.%Y} à {moment:%H:%M}"
    return f"{text} · {poste}" if poste else text


def page_bands(rows: GridRows, height: float) -> list[tuple[float, float]]:
    """Split the rows into ``(top, bottom)`` bands of at most ``height`` (rows never cut).

    A team header that would end a page moves to the next one with its first person; a
    row taller than a page gets a page of its own.
    """
    bands: list[tuple[float, float]] = []
    page: list[RowLayout] = []
    for row in rows:
        if page and row.bottom - page[0].top > height:
            keep = page
            if len(page) > 1 and page[-1].kind == "team":
                keep, page = page[:-1], [page[-1]]
            else:
                page = []
            bands.append((float(keep[0].top), float(keep[-1].bottom)))
        page.append(row)
    if page:
        bands.append((float(page[0].top), float(page[-1].bottom)))
    return bands or [(0.0, 0.0)]


def _logical_width(device: QPagedPaintDevice) -> float:
    """Logical page width for which 10 px of text are 7 pt on paper."""
    points = device.pageLayout().paintRect(QPageLayout.Unit.Point).width()
    return min(PAGE_WIDTH_PX, points * SMALLEST_FONT_PX / MIN_TEXT_PT)


def printable_model(model: GridModel, width: float) -> GridModel:
    """``model`` with its shown days filling ``width`` next to the row column."""
    columns = max(len(model.timescale.columns), 1)
    return model.with_day_width(max((width - ROW_COLUMN_WIDTH) / columns, 1.0))


def render_pages(
    device: QPagedPaintDevice, model: GridModel, info: PrintInfo, *, theme: Theme | None = None
) -> int:
    """Paint ``model`` on ``device`` (a PDF writer or a printer); gives the page count."""
    load_fonts()
    theme = theme or paper_theme()
    page = device.pageLayout().paintRectPixels(device.logicalDpiX())
    width = _logical_width(device)
    scale = page.width() / width
    height = page.height() / scale
    printable = printable_model(model, width)
    rows_height = max(height - TITLE_HEIGHT - HEADER_HEIGHT - FOOTER_HEIGHT, MIN_ROWS_HEIGHT)
    bands = page_bands(printable.rows, rows_height)
    painter = QPainter(device)
    try:
        for number, band in enumerate(bands, 1):
            if number > 1:
                device.newPage()
            painter.save()
            painter.scale(scale, scale)
            _paint_page(
                painter,
                printable,
                theme,
                band,
                info=info,
                size=(width, height),
                page=(number, len(bands)),
            )
            painter.restore()
    finally:
        painter.end()
    return len(bands)


def _paint_page(
    painter: QPainter,
    model: GridModel,
    theme: Theme,
    band: tuple[float, float],
    *,
    info: PrintInfo,
    size: tuple[float, float],
    page: tuple[int, int],
) -> None:
    width, height = size
    body_width = width - ROW_COLUMN_WIDTH
    painter.fillRect(QRectF(0, 0, width, height), theme.color("bg"))
    _paint_title(painter, theme, info, width=width, page=page)
    top, bottom = band
    rows_height = bottom - top
    painter.save()
    painter.translate(ROW_COLUMN_WIDTH, TITLE_HEIGHT)
    paint_time_header(painter, model, theme, x0=0.0, width=body_width, printing=True)
    painter.restore()
    _paint_corner(painter, model, theme)
    painter.save()
    painter.translate(0, TITLE_HEIGHT + HEADER_HEIGHT)
    paint_row_column(
        painter,
        model,
        theme,
        y0=top,
        height=rows_height,
        width=ROW_COLUMN_WIDTH,
        pinned=False,
        spaced=False,
    )
    painter.restore()
    painter.save()
    painter.translate(ROW_COLUMN_WIDTH, TITLE_HEIGHT + HEADER_HEIGHT - top)
    _paint_body(painter, model, theme, QRectF(0, top, body_width, rows_height))
    painter.restore()
    grid_bottom = TITLE_HEIGHT + HEADER_HEIGHT + rows_height
    painter.setPen(make_pen(theme.color("text", 0.30)))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawRect(QRectF(0.5, TITLE_HEIGHT + 0.5, width - 1, grid_bottom - TITLE_HEIGHT - 1))
    _paint_footer(painter, theme, width=width, top=grid_bottom)


def _paint_title(
    painter: QPainter, theme: Theme, info: PrintInfo, *, width: float, page: tuple[int, int]
) -> None:
    painter.setFont(heading_font(TITLE_FONT_PX))
    painter.setPen(theme.color("text"))
    area = QRectF(0, 0, width, TITLE_HEIGHT - TITLE_GAP)
    painter.drawText(
        area, int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter), info.title
    )
    number, count = page
    right = info.printed + (f" · page {number}/{count}" if count > 1 else "")
    painter.setFont(body_font(INFO_FONT_PX))
    painter.setPen(theme.color("neutral-800"))
    painter.drawText(area, int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter), right)


def _paint_corner(painter: QPainter, model: GridModel, theme: Theme) -> None:
    fonts = RowFonts(spaced=False)
    area = QRectF(0, TITLE_HEIGHT, ROW_COLUMN_WIDTH, HEADER_HEIGHT)
    painter.fillRect(area, theme.color("bg"))
    painter.setFont(fonts.caps)
    painter.setPen(theme.color("neutral-800"))
    people = sum(1 for row in model.rows if row.kind == "resource")
    painter.drawText(
        area.adjusted(LEFT_PAD, 0, 0, 0),
        int(Qt.AlignmentFlag.AlignVCenter),
        f"{CORNER_TITLE.upper()} · {people}",
    )
    painter.setPen(make_pen(theme.color("text", 0.30)))
    painter.drawLine(QLineF(area.right() - 0.5, area.top(), area.right() - 0.5, area.bottom()))
    painter.drawLine(QLineF(area.left(), area.bottom() - 0.5, area.right(), area.bottom() - 0.5))


def _paint_body(painter: QPainter, model: GridModel, theme: Theme, area: QRectF) -> None:
    painter.setClipRect(area)
    paint_background(painter, model, area, build_grid_style(theme, spaced=False), printing=True)
    style = build_bar_style(theme, compact=int(model.density) < COMPACT_LANE)
    state = BarState(day_view=model.view == "day", printing=True)
    for row in model.rows.visible(area.top(), area.bottom()):
        for bar in model.bars_by_row.get(row.resource_id, ()):
            rect = bar_rect(model, row, bar.start, bar.end, bar.lane, night=bar.night)
            if rect.intersects(area):
                paint_bar(painter, bar, rect, style, state)


def _paint_footer(painter: QPainter, theme: Theme, *, width: float, top: float) -> None:
    painter.setFont(body_font(INFO_FONT_PX))
    painter.setPen(theme.color("neutral-700"))
    area = QRectF(0, top, width, FOOTER_HEIGHT)
    painter.drawText(
        area, int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter), FOOTER_TEXT
    )


def _model_of(source: GridModel | PlanningGrid) -> GridModel:
    model = source.model if isinstance(source, PlanningGrid) else source
    if model is None:
        raise ValueError("Aucun planning a imprimer.")
    return model


def _info(
    model: GridModel, title: str | None, poste: str, printed_at: datetime | None
) -> PrintInfo:
    moment = printed_at or datetime.now().astimezone()
    return PrintInfo(
        title=title or print_title(model), printed=printed_label(moment, poste), poste=poste
    )


def _a3_landscape(device: QPagedPaintDevice) -> None:
    device.setPageSize(QPageSize(QPageSize.PageSizeId.A3))
    device.setPageOrientation(QPageLayout.Orientation.Landscape)
    margins = QMarginsF(PAGE_MARGIN_MM, PAGE_MARGIN_MM, PAGE_MARGIN_MM, PAGE_MARGIN_MM)
    device.setPageMargins(margins, QPageLayout.Unit.Millimeter)


def export_pdf(
    source: GridModel | PlanningGrid,
    path: Path | str,
    *,
    title: str | None = None,
    poste: str = "",
    printed_at: datetime | None = None,
) -> int:
    """Write the shown planning as an A3 landscape PDF at ``path``; gives the page count."""
    model = _model_of(source)
    info = _info(model, title, poste, printed_at)
    writer = QPdfWriter(str(path))
    writer.setResolution(PDF_RESOLUTION)
    _a3_landscape(writer)
    writer.setTitle(info.title)
    writer.setCreator(CREATOR)
    return render_pages(writer, model, info)


def print_with_dialog(
    parent: PlanningGrid,
    *,
    title: str | None = None,
    poste: str = "",
    dialog_parent: QWidget | None = None,
) -> bool:
    """Ask for a printer (A3 landscape proposed) and print the grid; False when cancelled."""
    if parent.model is None:
        return False
    model = parent.model
    printer = QPrinter(QPrinter.PrinterMode.HighResolution)
    _a3_landscape(printer)
    printer.setDocName(title or print_title(model))
    dialog = QPrintDialog(printer, dialog_parent or parent)
    dialog.setWindowTitle(DIALOG_TITLE)
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return False
    render_pages(printer, model, _info(model, title, poste, None))
    return True
