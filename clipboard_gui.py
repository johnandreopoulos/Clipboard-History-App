import csv
import hashlib
import os
import sys
import time
from datetime import datetime

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, Qt, QThread, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QIcon, QImage, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
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

# --- Configuration Constants ---
POLL_INTERVAL_SECONDS = 1
ICON_FILE = "icon.png"  # The name of your icon file


class HistoryManager:
    """Handles storage of clipboard history, both in-memory and on-disk."""

    def __init__(self, csv_path, images_dir):
        self.csv_path = csv_path
        self.images_dir = images_dir
        self.history = {}
        # Ensure the persistent storage directory for images exists
        os.makedirs(self.images_dir, exist_ok=True)
        self._load_history_from_csv()

    def _load_history_from_csv(self):
        """Loads history from the CSV file into memory on startup."""
        if not os.path.exists(self.csv_path):
            return  # No history file yet
        try:
            with open(self.csv_path, 'r', newline='', encoding='utf-8') as f:
                reader = csv.reader(f)
                for row in reader:
                    if len(row) == 3:
                        timestamp_iso, item_type, content = row
                        # Call internal method to prevent re-writing to CSV during load
                        self._add_item_to_memory(
                            timestamp_iso, item_type, content)
        except Exception as e:
            print(f"Error loading history from CSV: {e}")

    def _add_item_to_memory(self, timestamp_iso, item_type, content):
        """Adds a single item to the in-memory history dictionary."""
        timestamp = datetime.fromisoformat(timestamp_iso)
        item_date = timestamp.date()
        if item_date not in self.history:
            self.history[item_date] = []
        # Prepend to show newest first easily
        self.history[item_date].insert(0, (timestamp_iso, item_type, content))

    def add_item(self, timestamp_iso, item_type, content):
        """Adds an item to memory and appends it to the persistent CSV file."""
        timestamp = datetime.fromisoformat(timestamp_iso)
        item_date = timestamp.date()
        if item_date not in self.history:
            self.history[item_date] = []
        self.history[item_date].insert(0, (timestamp_iso, item_type, content))
        self._append_to_csv(timestamp_iso, item_type, content)
        return item_date

    def _append_to_csv(self, timestamp_iso, item_type, content):
        """Appends a single new history item to the CSV file."""
        try:
            with open(self.csv_path, 'a', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                writer.writerow([timestamp_iso, item_type, content])
        except Exception as e:
            print(f"Error writing to CSV: {e}")

    def _rewrite_csv(self):
        """Rewrites the entire CSV file with the current in-memory history."""
        try:
            with open(self.csv_path, 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                all_items = []
                for date_key in sorted(self.history.keys(), reverse=True):
                    # The history is already newest first, but CSV should be chronological
                    all_items.extend(reversed(self.history[date_key]))
                writer.writerows(all_items)
        except Exception as e:
            print(f"Error rewriting CSV: {e}")

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
        """Clears history for a specific date from memory and the CSV."""
        if date_to_clear in self.history:
            items_to_delete = self.history[date_to_clear]
            self._delete_image_files(items_to_delete)
            del self.history[date_to_clear]
            self._rewrite_csv()

    def clear_all(self):
        """Clears all history from memory, deletes all images, and clears the CSV."""
        for date_items in self.history.values():
            self._delete_image_files(date_items)
        self.history.clear()
        self._rewrite_csv()  # This will write an empty file


class ClipboardMonitor(QThread):
    """Monitors the clipboard for changes."""
    newItem = Signal(str, str, str)

    def __init__(self, history_manager, images_dir):
        super().__init__()
        self.history_manager = history_manager
        self.images_dir = images_dir
        self.running = True
        self.recent_text = ""
        self.recent_image_hash = ""

    def run(self):
        # Initialize recent values from the latest history
        try:
            all_dates = self.history_manager.get_all_dates()
            if all_dates:
                latest_history = self.history_manager.get_history_for_date(
                    all_dates[0])
                if latest_history:
                    _, item_type, content = latest_history[0]
                    if item_type == 'text':
                        self.recent_text = content
        except Exception as e:
            print(f"Could not prime clipboard monitor: {e}")

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
            # Save to the persistent images directory
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
    """Main application window with native theme and headings."""

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
        main_layout = QHBoxLayout(main_widget)

        # --- Left Pane (Dates) ---
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
        main_layout.addWidget(left_pane_widget)

        # --- Right Pane (History) ---
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
        main_layout.addWidget(right_pane_widget)

    def open_data_directory(self):
        """Opens the application's data directory in the file explorer."""
        QDesktopServices.openUrl(QUrl.fromLocalFile(self.app_data_dir))

    def update_date_list(self):
        """Repopulates the list of dates from the history manager."""
        current_selection = self.date_list.currentItem()
        current_date = current_selection.data(
            Qt.ItemDataRole.UserRole) if current_selection else None

        self.date_list.clear()

        new_selection_item = None
        for date_obj in self.history_manager.get_all_dates():
            date_str = date_obj.strftime("%b %d, %Y")
            item = QListWidgetItem(date_str)
            item.setData(Qt.ItemDataRole.UserRole, date_obj)
            self.date_list.addItem(item)
            if date_obj == current_date:
                new_selection_item = item

        if new_selection_item:
            self.date_list.setCurrentItem(new_selection_item)
        elif self.date_list.count() > 0:
            self.date_list.setCurrentRow(0)

    def update_history_view(self, current_item=None, previous_item=None):
        """Repopulates the history list based on the selected date."""
        self.history_list.clear()
        selected_item = self.date_list.currentItem()
        self.clear_button.setEnabled(selected_item is not None)

        if not selected_item:
            return

        selected_date = selected_item.data(Qt.ItemDataRole.UserRole)
        date_history = self.history_manager.get_history_for_date(selected_date)

        for timestamp_str, item_type, content in date_history:  # Already newest first
            item_time = datetime.fromisoformat(
                timestamp_str).strftime("%H:%M:%S")
            if item_type == "text":
                self.add_text_item_to_list(item_time, content)
            elif item_type == "image" and os.path.exists(content):
                self.add_image_item_to_list(item_time, content)

    def handle_new_item(self, timestamp_str, item_type, content):
        """Slot to handle a new item from the clipboard monitor."""
        new_item_date = datetime.fromisoformat(timestamp_str).date()

        # Check if the date is new
        is_new_date = all(
            item.data(Qt.ItemDataRole.UserRole) != new_item_date
            for item in (self.date_list.item(i) for i in range(self.date_list.count()))
        )

        if is_new_date:
            self.update_date_list()

        # If the new item's date is currently selected, refresh the view
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
        self.history_manager.clear_date(date_obj)
        self.update_date_list()

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
        self.tray_icon.show()

    def closeEvent(self, event):
        event.ignore()
        self.hide()

    def quit_app(self):
        self.monitor_thread.stop()  # type: ignore
        self.monitor_thread.wait()  # type: ignore
        QApplication.instance().quit()  # type: ignore


if __name__ == "__main__":
    app = QApplication(sys.argv)

    # --- Setup Persistent Storage Paths ---
    APP_NAME = "ClipboardHistoryApp"
    # Create a hidden directory in the user's home folder for storing data
    app_data_dir = os.path.join(os.path.expanduser("~"), f".{APP_NAME}")
    images_dir = os.path.join(app_data_dir, "images")
    csv_path = os.path.join(app_data_dir, "history.csv")

    # The HistoryManager will create the directories if they don't exist
    history_manager = HistoryManager(csv_path=csv_path, images_dir=images_dir)

    main_win = ClipboardMainWindow(history_manager, app_data_dir)

    monitor_thread = ClipboardMonitor(history_manager, images_dir)
    monitor_thread.newItem.connect(main_win.handle_new_item)
    main_win.monitor_thread = monitor_thread  # type: ignore

    monitor_thread.start()

    main_win.show()
    sys.exit(app.exec())
