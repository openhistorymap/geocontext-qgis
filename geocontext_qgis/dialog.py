"""Configuration dialog for the GeoContext Sync plugin."""

from pathlib import Path

from qgis.core import QgsSettings
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


SETTINGS_PREFIX = "geocontext_qgis"


def _setting(key, default=""):
    return QgsSettings().value(f"{SETTINGS_PREFIX}/{key}", default)


def _save(key, value):
    QgsSettings().setValue(f"{SETTINGS_PREFIX}/{key}", value)


class SyncDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("GeoContext Sync — Push to GitHub")
        self.setMinimumWidth(520)

        self.repo_edit = QLineEdit(_setting("repo"))
        self.repo_edit.setPlaceholderText(
            "owner/repo, https://github.com/owner/repo.git, or git@github.com:owner/repo.git"
        )

        self.branch_edit = QLineEdit(_setting("branch", "main"))
        self.branch_edit.setPlaceholderText("main")

        self.base_path_edit = QLineEdit(_setting("base_path"))
        self.base_path_edit.setPlaceholderText("(repo root) or e.g. snapshots/today")

        self.crs_edit = QLineEdit(_setting("geojson_crs", "EPSG:4326"))

        self.author_name_edit = QLineEdit(_setting("author_name"))
        self.author_name_edit.setPlaceholderText("(uses your git config user.name)")

        self.author_email_edit = QLineEdit(_setting("author_email"))
        self.author_email_edit.setPlaceholderText("(uses your git config user.email)")

        self.message_edit = QPlainTextEdit(_setting("message", "Update geocontext snapshot"))
        self.message_edit.setFixedHeight(64)

        hint = QLabel(
            "Authentication uses your system git (SSH key, credential helper, "
            "gh CLI). The plugin never handles a token."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: gray;")

        form = QFormLayout()
        form.addRow("Repository", self.repo_edit)
        form.addRow("Branch", self.branch_edit)
        form.addRow("Sub-path in repo", self.base_path_edit)
        form.addRow("GeoJSON output CRS", self.crs_edit)
        form.addRow("Author name (optional)", self.author_name_edit)
        form.addRow("Author email (optional)", self.author_email_edit)
        form.addRow("Commit message", self.message_edit)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel, Qt.Horizontal, self
        )
        buttons.button(QDialogButtonBox.Ok).setText("Push to GitHub")
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(hint)
        layout.addWidget(buttons)

    def _on_accept(self):
        if not self.repo_edit.text().strip():
            self.repo_edit.setFocus()
            return
        _save("repo", self.repo_edit.text().strip())
        _save("branch", self.branch_edit.text().strip() or "main")
        _save("base_path", self.base_path_edit.text().strip())
        _save("geojson_crs", self.crs_edit.text().strip() or "EPSG:4326")
        _save("author_name", self.author_name_edit.text().strip())
        _save("author_email", self.author_email_edit.text().strip())
        _save("message", self.message_edit.toPlainText().strip() or "Update geocontext snapshot")
        self.accept()

    def values(self):
        return {
            "repo": self.repo_edit.text().strip(),
            "branch": self.branch_edit.text().strip() or "main",
            "base_path": self.base_path_edit.text().strip(),
            "geojson_crs": self.crs_edit.text().strip() or "EPSG:4326",
            "author_name": self.author_name_edit.text().strip() or None,
            "author_email": self.author_email_edit.text().strip() or None,
            "message": self.message_edit.toPlainText().strip() or "Update geocontext snapshot",
        }


class LocalSaveDialog(QDialog):
    """Save the geocontext snapshot to a local folder — no git involved."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("GeoContext — Save to folder")
        self.setMinimumWidth(520)

        self.folder_edit = QLineEdit(_setting("local_folder"))
        self.folder_edit.setPlaceholderText("/path/to/target/folder")
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        folder_row = QWidget()
        folder_layout = QHBoxLayout(folder_row)
        folder_layout.setContentsMargins(0, 0, 0, 0)
        folder_layout.addWidget(self.folder_edit, stretch=1)
        folder_layout.addWidget(browse)

        self.base_path_edit = QLineEdit(_setting("local_base_path"))
        self.base_path_edit.setPlaceholderText("(folder root) or e.g. snapshots/today")

        self.crs_edit = QLineEdit(_setting("geojson_crs", "EPSG:4326"))

        hint = QLabel(
            "Writes gcx.json plus datasets/*.geojson into the chosen folder. "
            "Existing files under datasets/ are replaced."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: gray;")

        form = QFormLayout()
        form.addRow("Target folder", folder_row)
        form.addRow("Sub-path", self.base_path_edit)
        form.addRow("GeoJSON output CRS", self.crs_edit)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel, Qt.Horizontal, self
        )
        buttons.button(QDialogButtonBox.Ok).setText("Save")
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(hint)
        layout.addWidget(buttons)

    def _browse(self):
        start = self.folder_edit.text().strip() or str(Path.home())
        chosen = QFileDialog.getExistingDirectory(self, "Choose target folder", start)
        if chosen:
            self.folder_edit.setText(chosen)

    def _on_accept(self):
        if not self.folder_edit.text().strip():
            self.folder_edit.setFocus()
            return
        _save("local_folder", self.folder_edit.text().strip())
        _save("local_base_path", self.base_path_edit.text().strip())
        _save("geojson_crs", self.crs_edit.text().strip() or "EPSG:4326")
        self.accept()

    def values(self):
        return {
            "folder": self.folder_edit.text().strip(),
            "base_path": self.base_path_edit.text().strip(),
            "geojson_crs": self.crs_edit.text().strip() or "EPSG:4326",
        }
