import csv
import hashlib
import os
import sys
import time
from datetime import datetime, timedelta

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, Qt, QThread, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QIcon, QImage, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QStyle,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

POLL_INTERVAL_SECONDS = 1
ICON_FILE = "icon.png"


class HistoryManager:
    def __init__(self, csv_path, images_dir):
        self.csv_path = csv_path
        self.images_dir = images_dir
        self.history = {}
        os.makedirs(self.images_dir, exist_ok=True)
        self._load_history_from_csv()

    def _load_history_from_csv(self):
        if not os.path.exists(self.csv_path):
            return
        try:
            with open(self.csv_path, 'r', newline='', encoding='utf-8') as f:
                reader = csv.reader(f)
                for row in reader:
                    if len(row) == 3:
                        timestamp_iso, item_type, content = row
                        self._add_item_to_memory(
                            timestamp_iso, item_type, content)
        except Exception as e:
            print(f"Error loading history from CSV: {e}")

    def _add_item_to_memory(self, timestamp_iso, item_type, content):
        timestamp = datetime.fromisoformat(timestamp_iso)
        item_date = timestamp.date()
        if item_date not in self.history:
            self.history[item_date] = []
        self.history[item_date].insert(0, (timestamp_iso, item_type, content))

    def add_item(self, timestamp_iso, item_type, content):
        timestamp = datetime.fromisoformat(timestamp_iso)
        item_date = timestamp.date()
        if item_date not in self.history:
            self.history[item_date] = []
        self.history[item_date].insert(0, (timestamp_iso, item_type, content))
        self._append_to_csv(timestamp_iso, item_type, content)
        return item_date

    def _append_to_csv(self, timestamp_iso, item_type, content):
        try:
            with open(self.csv_path, 'a', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                writer.writerow([timestamp_iso, item_type, content])
        except Exception as e:
            print(f"Error writing to CSV: {e}")

    def _rewrite_csv(self):
        temp_path = self.csv_path + ".tmp"
        try:
            with open(temp_path, 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                all_items = []
                for date_key in sorted(self.history.keys()):
                    all_items.extend(reversed(self.history[date_key]))
                writer.writerows(all_items)
            os.replace(temp_path, self.csv_path)
            return True
        except Exception as e:
            print(f"FATAL: Error rewriting CSV file: {e}")
            if os.path.exists(temp_path):
                os.remove(temp_path)
            return False

    def get_history_for_date(self, date):
        return self.history.get(date, [])

    def get_all_dates(self):
        return sorted(self.history.keys(), reverse=True)

    def _delete_image_files(self, history_items):
        for _, item_type, content in history_items:
            if item_type == "image":
                try:
                    if os.path.exists(content):
                        os.remove(content)
                except OSError as e:
                    print(f"Error deleting image file {content}: {e}")

    def clear_date(self, date_to_clear):
        if date_to_clear not in self.history:
            print(f"Error: Could not find the date {date_to_clear} in the history dictionary.")
            return False

        items_to_delete = self.history.pop(date_to_clear)
        self._delete_image_files(items_to_delete)

        if self._rewrite_csv():
            return True
        else:
            print("CRITICAL: CSV rewrite failed. Rolling back in-memory deletion.")
            self.history[date_to_clear] = items_to_delete
            return False

    def clear_all(self):
        for date_items in self.history.values():
            self._delete_image_files(date_items)
        self.history.clear()
        self._rewrite_csv()


class ClipboardMonitor(QThread):
    newItem = Signal(str, str, str)

    def __init__(self, history_manager, images_dir):
        super().__init__()
        self.history_manager = history_manager
        self.images_dir = images_dir
        self.running = True
        self.recent_text = ""
        self.recent_image_hash = ""

    def run(self):
        try:
            clipboard = QApplication.clipboard()
            mime_data = clipboard.mimeData()
            if mime_data.hasImage():
                image = clipboard.image()
                if not image.isNull():
                    byte_array = QByteArray()
                    buffer = QBuffer(byte_array)
                    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
                    image.save(buffer, "PNG") # type: ignore
                    self.recent_image_hash = hashlib.sha256(byte_array.data()).hexdigest()
            elif mime_data.hasText():
                self.recent_text = clipboard.text()
        except Exception as e:
            print(f"Could not prime clipboard monitor with initial state: {e}")

        while self.running:
            try:
                clipboard = QApplication.clipboard()
                mime_data = clipboard.mimeData()
                if mime_data.hasImage():
                    self.process_image(clipboard)
                elif mime_data.hasText():
                    self.process_text(clipboard)
            except Exception as e:
                print(f"Error in clipboard monitor: {e}")
            time.sleep(POLL_INTERVAL_SECONDS)

    def process_image(self, clipboard):
        image = clipboard.image()
        if image.isNull():
            return
        byte_array = QByteArray()
        buffer = QBuffer(byte_array)
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        image.save(buffer, "PNG")
        image_hash = hashlib.sha256(byte_array.data()).hexdigest()

        if image_hash != self.recent_image_hash:
            self.recent_image_hash = image_hash
            timestamp = datetime.now()
            filename = f"{timestamp.strftime('%Y%m%d_%H%M%S')}_{image_hash[:8]}.png"
            filepath = os.path.join(self.images_dir, filename)
            image.save(filepath, "PNG")
            self.history_manager.add_item(
                timestamp.isoformat(), "image", filepath)
            self.newItem.emit(timestamp.isoformat(), "image", filepath)

    def process_text(self, clipboard):
        text = clipboard.text()
        if text and text != self.recent_text:
            self.recent_text = text
            timestamp = datetime.now()
            self.history_manager.add_item(timestamp.isoformat(), "text", text)
            self.newItem.emit(timestamp.isoformat(), "text", text)

    def stop(self):
        self.running = False


class ClipboardMainWindow(QMainWindow):
    def __init__(self, history_manager, app_data_dir):
        super().__init__()
        self.history_manager = history_manager
        self.app_data_dir = app_data_dir
        self.setWindowTitle("Clipboard History")
        self.setGeometry(100, 100, 800, 600)
        self.setMinimumSize(600, 400)

        if os.path.exists(ICON_FILE):
            self.app_icon = QIcon(ICON_FILE)
        else:
            print(
                f"Warning: Icon file '{ICON_FILE}' not found. Using default icon.")
            self.app_icon = self.style().standardIcon(
                QStyle.StandardPixmap.SP_ComputerIcon)
        self.setWindowIcon(self.app_icon)

        self.init_ui()
        self.setup_tray_icon()
        self.update_date_list()

    def init_ui(self):
        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        top_level_layout = QVBoxLayout(main_widget)
        top_level_layout.setContentsMargins(20, 10, 20, 20)
        top_level_layout.setSpacing(15)

        brand_label = QLabel("ClipIt")
        brand_font = brand_label.font()
        brand_font.setPointSize(36)
        brand_font.setBold(True)
        brand_label.setFont(brand_font)
        brand_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        top_level_layout.addWidget(brand_label)

        self.search_bar = QLineEdit()
        self.search_bar.setPlaceholderText("Search all history...")
        search_font = self.search_bar.font()
        search_font.setPointSize(14)
        self.search_bar.setFont(search_font)
        self.search_bar.setMinimumHeight(40)
        self.search_bar.textChanged.connect(self.on_search_text_changed)
        top_level_layout.addWidget(self.search_bar)

        panes_layout = QHBoxLayout()
        top_level_layout.addLayout(panes_layout)

        left_pane_layout = QVBoxLayout()
        dates_heading = QLabel("Dates")
        font = dates_heading.font()
        font.setBold(True)
        dates_heading.setFont(font)

        self.date_list = QListWidget()
        self.date_list.currentItemChanged.connect(self.update_history_view)
        self.date_list.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu)
        self.date_list.customContextMenuRequested.connect(
            self.show_date_list_context_menu)

        self.clear_button = QPushButton("Clear Selected")
        self.clear_button.clicked.connect(self.clear_selected_date)

        self.view_data_button = QPushButton("Open Data Folder")
        self.view_data_button.clicked.connect(self.open_data_directory)

        left_pane_layout.addWidget(dates_heading)
        left_pane_layout.addWidget(self.date_list)
        left_pane_layout.addWidget(self.clear_button)
        left_pane_layout.addWidget(self.view_data_button)

        left_pane_widget = QWidget()
        left_pane_widget.setLayout(left_pane_layout)
        left_pane_widget.setFixedWidth(200)
        panes_layout.addWidget(left_pane_widget)

        right_pane_layout = QVBoxLayout()
        history_heading = QLabel("History")
        history_heading.setFont(font)

        self.history_list = QListWidget()
        self.history_list.itemDoubleClicked.connect(
            self.copy_item_to_clipboard)

        right_pane_layout.addWidget(history_heading)
        right_pane_layout.addWidget(self.history_list)

        right_pane_widget = QWidget()
        right_pane_widget.setLayout(right_pane_layout)
        panes_layout.addWidget(right_pane_widget)

    def on_search_text_changed(self, text):
        if text:
            self.date_list.setEnabled(False)
            self.clear_button.setEnabled(False)
            self.perform_search(text)
        else:
            self.date_list.setEnabled(True)
            self.clear_button.setEnabled(self.date_list.currentItem() is not None)
            self.update_history_view()

    def perform_search(self, query):
        self.history_list.clear()
        query_lower = query.lower()

        all_items = []
        for date_key in self.history_manager.get_all_dates():
            all_items.extend(self.history_manager.get_history_for_date(date_key))

        for timestamp_str, item_type, content in all_items:
            if item_type == "text" and query_lower in content.lower():
                item_time = datetime.fromisoformat(
                    timestamp_str).strftime("%H:%M:%S")
                self.add_text_item_to_list(item_time, content)

    def open_data_directory(self):
        QDesktopServices.openUrl(QUrl.fromLocalFile(self.app_data_dir))

    def update_date_list(self):
        current_selection = self.date_list.currentItem()
        current_date_obj = current_selection.data(Qt.ItemDataRole.UserRole) if current_selection else None

        self.date_list.clear()

        today = datetime.now().date()
        new_selection_item = None

        for date_obj in self.history_manager.get_all_dates():
            if date_obj == today:
                date_str = "Today"
            elif date_obj == today - timedelta(days=1):
                date_str = "Yesterday"
            else:
                date_str = date_obj.strftime("%b %d, %Y")

            item = QListWidgetItem(date_str)
            item.setData(Qt.ItemDataRole.UserRole, date_obj)
            self.date_list.addItem(item)

            if date_obj == current_date_obj:
                new_selection_item = item

        if new_selection_item:
            self.date_list.setCurrentItem(new_selection_item)
        elif self.date_list.count() > 0:
            self.date_list.setCurrentRow(0)

    def update_history_view(self, current_item=None, previous_item=None):
        if self.search_bar.text():
            return
            
        self.history_list.clear()
        selected_item = self.date_list.currentItem()
        self.clear_button.setEnabled(selected_item is not None)

        if not selected_item:
            return

        selected_date = selected_item.data(Qt.ItemDataRole.UserRole)
        date_history = self.history_manager.get_history_for_date(selected_date)

        for timestamp_str, item_type, content in date_history:
            item_time = datetime.fromisoformat(
                timestamp_str).strftime("%H:%M:%S")
            if item_type == "text":
                self.add_text_item_to_list(item_time, content)
            elif item_type == "image" and os.path.exists(content):
                self.add_image_item_to_list(item_time, content)

    def handle_new_item(self, timestamp_str, item_type, content):
        new_item_date = datetime.fromisoformat(timestamp_str).date()

        is_new_date = all(
            self.date_list.item(i).data(Qt.ItemDataRole.UserRole) != new_item_date
            for i in range(self.date_list.count())
        )
        
        if is_new_date:
            self.update_date_list()

        if self.search_bar.text():
            self.perform_search(self.search_bar.text())
        else:
            current_item = self.date_list.currentItem()
            if current_item and current_item.data(Qt.ItemDataRole.UserRole) == new_item_date:
                self.update_history_view()

    def add_text_item_to_list(self, time_str, content):
        display_text = (
            content[:100] + '...') if len(content) > 100 else content
        formatted_text = f"[{time_str}]\n{display_text.strip()}"
        list_item = QListWidgetItem(formatted_text)
        list_item.setData(Qt.ItemDataRole.UserRole, ("text", content))
        list_item.setToolTip(content)
        self.history_list.addItem(list_item)

    def add_image_item_to_list(self, time_str, filepath):
        pixmap = QPixmap(filepath).scaledToWidth(
            200, Qt.TransformationMode.SmoothTransformation)
        item_widget = QWidget()
        item_layout = QVBoxLayout(item_widget)
        item_layout.setContentsMargins(5, 5, 5, 5)
        time_label = QLabel(f"[{time_str}]")
        image_label = QLabel()
        image_label.setPixmap(pixmap)
        item_layout.addWidget(time_label)
        item_layout.addWidget(image_label)

        list_item = QListWidgetItem()
        list_item.setData(Qt.ItemDataRole.UserRole, ("image", filepath))
        list_item.setToolTip("Double-click to copy image")
        list_item.setSizeHint(item_widget.sizeHint())
        self.history_list.addItem(list_item)
        self.history_list.setItemWidget(list_item, item_widget)

    def show_date_list_context_menu(self, pos):
        context_menu = QMenu()
        clear_selected_action = context_menu.addAction("Clear Selected Date")
        clear_all_action = context_menu.addAction("Clear All Session History")
        clear_selected_action.setEnabled(
            self.date_list.currentItem() is not None)
        action = context_menu.exec(self.date_list.mapToGlobal(pos))
        if action == clear_selected_action:
            self.clear_selected_date()
        elif action == clear_all_action:
            self.clear_all_history()

    def clear_selected_date(self):
        selected_item = self.date_list.currentItem()
        if not selected_item:
            return

        date_obj = selected_item.data(Qt.ItemDataRole.UserRole)
        date_str = "Today" if date_obj == datetime.now().date() else date_obj.strftime("%b %d, %Y")

        reply = QMessageBox.question(self, 'Confirm Deletion',
                                     f"Are you sure you want to permanently delete all history for {date_str}?",
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                     QMessageBox.StandardButton.No)

        if reply == QMessageBox.StandardButton.Yes:
            success = self.history_manager.clear_date(date_obj)

            if success:
                self.update_date_list()
                self.history_list.clear()
            else:
                QMessageBox.critical(self, "Error Deleting History",
                    "Could not save the changes to the history file.\n\n"
                    "This is often caused by a file permissions issue.\n\n"
                    "Please ensure the application has permission to write to its 'data' folder and that 'history.csv' is not open or locked by another program.")

    def clear_all_history(self):
        reply = QMessageBox.question(self, 'Confirm Clear',
                                     "Are you sure you want to clear all history?",
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                     QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            self.history_manager.clear_all()
            self.update_date_list()

    def copy_item_to_clipboard(self, item):
        if not item:
            return
        item_type, content = item.data(Qt.ItemDataRole.UserRole)
        clipboard = QApplication.clipboard()
        if item_type == "text":
            clipboard.setText(content)
        elif item_type == "image" and os.path.exists(content):
            clipboard.setImage(QImage(content))

        self.tray_icon.showMessage(
            "Copied", "Item copied to clipboard!", self.app_icon, 1500)

    def setup_tray_icon(self):
        self.tray_icon = QSystemTrayIcon(self)
        self.tray_icon.setIcon(self.app_icon)

        tray_menu = QMenu()
        show_action = tray_menu.addAction("Show")
        show_action.triggered.connect(self.showNormal)
        quit_action = tray_menu.addAction("Quit")
        quit_action.triggered.connect(self.quit_app)
        self.tray_icon.setContextMenu(tray_menu)
        
        self.tray_icon.activated.connect(self.on_tray_icon_activated)
        
        self.tray_icon.show()

    def on_tray_icon_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            if self.isHidden():
                self.showNormal()
                self.activateWindow()
            else:
                self.hide()

    def closeEvent(self, event):
        event.ignore()
        self.hide()

    def quit_app(self):
        self.monitor_thread.stop() # type: ignore
        self.monitor_thread.wait() # type: ignore
        QApplication.instance().quit() # type: ignore


if __name__ == "__main__":
    app = QApplication(sys.argv)

    script_dir = os.path.dirname(os.path.realpath(__file__))

    app_data_dir = os.path.join(script_dir, "data")

    images_dir = os.path.join(app_data_dir, "images")
    csv_path = os.path.join(app_data_dir, "history.csv")

    history_manager = HistoryManager(csv_path=csv_path, images_dir=images_dir)

    main_win = ClipboardMainWindow(history_manager, app_data_dir)

    monitor_thread = ClipboardMonitor(history_manager, images_dir)
    monitor_thread.newItem.connect(main_win.handle_new_item)
    main_win.monitor_thread = monitor_thread # type: ignore

    monitor_thread.start()

    main_win.show()
    sys.exit(app.exec())