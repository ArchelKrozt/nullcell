"""El icono del lanzador — dibujado, no empaquetado.

Aquí y no en `cli/` porque usa Qt, que es un extra opcional: tenerlo en el
paquete base hacía que Nullcell pareciera depender de PySide6 para instalarse.

Sin icono el sistema pone uno genérico, que es feo y funciona: un lanzador sin
icono es mejor que ningún lanzador, así que cualquier fallo aquí se traga.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

__all__ = ["write_icns"]


def write_icns(destination: Path) -> bool:
    try:
        from PySide6.QtCore import QRectF, Qt
        from PySide6.QtGui import QColor, QFont, QPainter, QPixmap
        from PySide6.QtWidgets import QApplication
    except ImportError:
        return False

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    iconset = destination.parent / "nullcell.iconset"
    iconset.mkdir(parents=True, exist_ok=True)
    try:
        for size in (16, 32, 64, 128, 256, 512, 1024):
            pixmap = QPixmap(size, size)
            pixmap.fill(QColor("#07090d"))
            painter = QPainter(pixmap)
            painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
            font = QFont()
            font.setPixelSize(max(6, int(size * 0.26)))
            font.setWeight(QFont.Weight.DemiBold)
            font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, size * 0.03)
            painter.setFont(font)
            painter.setPen(QColor("#22d3ee"))
            painter.drawText(QRectF(0, 0, size, size), Qt.AlignmentFlag.AlignCenter, "NULL")
            painter.end()
            pixmap.save(str(iconset / f"icon_{size}x{size}.png"))
        subprocess.run(
            ["iconutil", "-c", "icns", str(iconset), "-o", str(destination)],
            check=True,
            capture_output=True,
        )
        return destination.is_file()
    except (OSError, subprocess.SubprocessError):
        return False
    finally:
        shutil.rmtree(iconset, ignore_errors=True)
        del app
