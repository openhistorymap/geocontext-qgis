"""GeoContext Sync — QGIS plugin entry point."""

import json
import os
import tempfile

from qgis.core import Qgis, QgsMessageLog
from qgis.PyQt.QtCore import QCoreApplication, Qt
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction, QApplication

from .dialog import LocalSaveDialog, SyncDialog
from .exporter import GCX_FILENAME, build_snapshot
from .git_sync import GitError, push_snapshot
from .local_sync import save_to_folder


LOG_TAG = "GeoContext Sync"
MENU = "&OpenHistoryMap"


class GeoContextSyncPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.actions = []

    def initGui(self):
        icon_path = os.path.join(os.path.dirname(__file__), "icon.svg")
        icon = QIcon(icon_path)
        main = self.iface.mainWindow()

        push_action = QAction(icon, "GeoContext: push to GitHub", main)
        push_action.triggered.connect(self.run_github)

        save_action = QAction(icon, "GeoContext: save to folder…", main)
        save_action.triggered.connect(self.run_local)

        for act in (push_action, save_action):
            self.iface.addToolBarIcon(act)
            self.iface.addPluginToWebMenu(MENU, act)
            self.actions.append(act)

    def unload(self):
        for act in self.actions:
            self.iface.removePluginWebMenu(MENU, act)
            self.iface.removeToolBarIcon(act)
        self.actions = []

    # --- entry points -----------------------------------------------------

    def run_github(self):
        dialog = SyncDialog(self.iface.mainWindow())
        if not dialog.exec_():
            return
        cfg = dialog.values()

        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            with tempfile.TemporaryDirectory(prefix="geocontext_") as tmp:
                staging = os.path.join(tmp, "staging")
                clone = os.path.join(tmp, "clone")
                files = self._stage(staging, cfg["geojson_crs"])

                self._log(
                    f"Pushing {len(files)} files to {cfg['repo']}@{cfg['branch']} "
                    f"under '{cfg['base_path'] or '/'}'"
                )
                result = push_snapshot(
                    remote_url=cfg["repo"],
                    branch=cfg["branch"],
                    base_path=cfg["base_path"],
                    files=files,
                    message=cfg["message"],
                    work_dir=clone,
                    author_name=cfg["author_name"],
                    author_email=cfg["author_email"],
                )
        except GitError as exc:
            self._error(f"git: {exc}")
            return
        except Exception as exc:  # noqa: BLE001
            self._error(f"Export failed: {exc}")
            QgsMessageLog.logMessage(repr(exc), LOG_TAG, level=Qgis.Critical)
            return
        finally:
            QApplication.restoreOverrideCursor()

        if not result["committed"]:
            self._info(
                f"No changes to commit on {cfg['repo']}@{cfg['branch']} — "
                f"snapshot matches what's already there."
            )
        else:
            self._success(
                f"Pushed {len(result['files'])} files to "
                f"{cfg['repo']}@{result['branch']}"
            )

    def run_local(self):
        dialog = LocalSaveDialog(self.iface.mainWindow())
        if not dialog.exec_():
            return
        cfg = dialog.values()

        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            with tempfile.TemporaryDirectory(prefix="geocontext_") as tmp:
                staging = os.path.join(tmp, "staging")
                files = self._stage(staging, cfg["geojson_crs"])

                self._log(
                    f"Saving {len(files)} files to {cfg['folder']} "
                    f"under '{cfg['base_path'] or '/'}'"
                )
                written = save_to_folder(
                    target_dir=cfg["folder"],
                    base_path=cfg["base_path"],
                    files=files,
                )
        except Exception as exc:  # noqa: BLE001
            self._error(f"Save failed: {exc}")
            QgsMessageLog.logMessage(repr(exc), LOG_TAG, level=Qgis.Critical)
            return
        finally:
            QApplication.restoreOverrideCursor()

        self._success(f"Saved {len(written)} files to {cfg['folder']}")

    # --- shared staging ---------------------------------------------------

    def _stage(self, staging, geojson_crs):
        os.makedirs(staging, exist_ok=True)
        snapshot, files = build_snapshot(
            self.iface, staging, geojson_crs=geojson_crs
        )
        snapshot_path = os.path.join(staging, GCX_FILENAME)
        with open(snapshot_path, "w", encoding="utf-8") as fh:
            json.dump(snapshot, fh, indent=2, ensure_ascii=False)
        return [(GCX_FILENAME, snapshot_path)] + files

    # --- UI helpers -------------------------------------------------------

    def _log(self, msg):
        QgsMessageLog.logMessage(msg, LOG_TAG, level=Qgis.Info)

    def _error(self, msg):
        self.iface.messageBar().pushMessage(
            "GeoContext Sync", msg, level=Qgis.Critical, duration=10
        )
        QgsMessageLog.logMessage(msg, LOG_TAG, level=Qgis.Critical)

    def _info(self, msg):
        self.iface.messageBar().pushMessage(
            "GeoContext Sync", msg, level=Qgis.Info, duration=6
        )
        QgsMessageLog.logMessage(msg, LOG_TAG, level=Qgis.Info)

    def _success(self, msg):
        self.iface.messageBar().pushMessage(
            "GeoContext Sync", msg, level=Qgis.Success, duration=6
        )
        QgsMessageLog.logMessage(msg, LOG_TAG, level=Qgis.Info)

    @staticmethod
    def tr(message):
        return QCoreApplication.translate("GeoContextSyncPlugin", message)
