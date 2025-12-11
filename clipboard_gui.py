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
    QGraphicsDropShadowEffect,
)

# --- Configuration Constants ---
POLL_INTERVAL_SECONDS = 1
ICON_FILE = "icon.png"  # The name of your icon file

# --- Qt Style Sheet (QSS) for Dark Theme ---
APP_STYLESHEET = """
QMainWindow {
    background-color: #1E1E1E;
}
QWidget#central_widget {
    padding: 20px;
}
QLabel {
    color: #FFFFFF;
}
QLabel#brand_label {
    font-size: 50px;
    font-weight: bold;
    color: #FFFFFF;
}
QLineEdit {
    background-color: #2D2D2D;
    border: 1px solid #4A4A4A;
    border-radius: 8px;
    padding: 10px;
    color: #FFFFFF;
    font-size: 14px;
}
QLineEdit:focus {
    border: 1px solid #007ACC;
}
/* Panes for Dates and History */
QWidget#left_pane, QWidget#right_pane {
    background-color: #2D2D2D;
    border-radius: 10px;
    /* Add padding to the pane itself */
    padding: 10px;
}
/* Headings like "Dates" and "History" */
QLabel#heading_label {
    font-size: 16px;
    font-weight: bold;
    /* Adjusted padding for outside placement */
    padding: 15px 0 10px 5px;
}
QListWidget {
    background-color: transparent;
    border: none;
    outline: 0;
    padding-left: 5px;
    padding-right: 5px;
}
QListWidget::item {
    color: #CCCCCC;
    padding: 10px;
    border-radius: 5px;
}
QListWidget::item:selected {
    background-color: #007ACC;
    color: #FFFFFF;
}
/* Custom Buttons */
QPushButton {
    color: white;
    border: none;
    border-radius: 8px;
    padding: 10px;
    font-weight: bold;
}
QPushButton#clear_button {
    background-color: #007ACC;
}
QPushButton#clear_button:hover {
    background-color: #006BB2;
}
QPushButton#data_button {
    background-color: #4A4A4A;
}
QPushButton#data_button:hover {
    background-color: #5A5A5A;
}
/* Scrollbar Styling */
QListWidget QScrollBar:vertical {
    border: none;
    background-color: #2D2D2D;
    width: 10px;
    margin: 0px 0px 0px 0px;
}
QListWidget QScrollBar::handle:vertical {
    background-color: #5A5A5A;
    min-height: 20px;
    border-radius: 5px;
}
QListWidget QScrollBar::handle:vertical:hover {
    background-color: #007ACC;
}
QListWidget QScrollBar::add-line:vertical, QListWidget QScrollBar::sub-line:vertical {
    border: none;
    background: none;
    height: 0px;
}
QListWidget QScrollBar::add-page:vertical, QListWidget QScrollBar::sub-page:vertical {
    background: none;
}
"""

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

# --- Custom Widget for History Items ---
class HistoryItemWidget(QWidget):
    """A custom widget to display a single history item."""
    def __init__(self, time_str, content, item_type, filepath=None):
        super().__init__()
        self.layout = QHBoxLayout(self) # type: ignore
        self.layout.setContentsMargins(10, 5, 10, 5) # type: ignore
        self.layout.setSpacing(10) # type: ignore

        self.time_label = QLabel(f"[{time_str}]")
        self.time_label.setStyleSheet("color: #888888;")

        if item_type == "image":
            self.content_label = QLabel("Copied Image")
        else:
            display_text = (content[:50] + '...') if len(content) > 50 else content
            self.content_label = QLabel(display_text.strip())
        
        self.content_label.setToolTip(content)

        self.layout.addWidget(self.time_label) # type: ignore
        self.layout.addWidget(self.content_label) # type: ignore
        self.layout.addStretch() # type: ignore

        if item_type == "image" and filepath and os.path.exists(filepath):
            self.icon_label = QLabel()
            pixmap = QPixmap(filepath)
            self.icon_label.setPixmap(pixmap.scaled(
                200, 200, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
            self.layout.addWidget(self.icon_label) # type: ignore
        
        self.setStyleSheet("border-bottom: 1px solid #3A3A3A;")


class ClipboardMainWindow(QMainWindow):
    """Main application window with native theme and headings."""

    def __init__(self, history_manager, app_data_dir):
        super().__init__()
        self.history_manager = history_manager
        self.app_data_dir = app_data_dir
        self.setWindowTitle("ClipIt")
        self.setFixedSize(900, 650)

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
        central_widget = QWidget()
        central_widget.setObjectName("central_widget")
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        app_name_label = QLabel("ClipIt")
        app_name_label.setObjectName("brand_label")
        app_name_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        shadow = QGraphicsDropShadowEffect()
        shadow.setBlurRadius(25)
        shadow.setColor(Qt.GlobalColor.white)
        shadow.setOffset(0, 0)
        app_name_label.setGraphicsEffect(shadow)
        
        main_layout.addWidget(app_name_label)
        main_layout.addSpacing(5)

        self.search_bar = QLineEdit()
        self.search_bar.setPlaceholderText("Search history...")
        self.search_bar.textChanged.connect(self.on_search_text_changed)
        main_layout.addWidget(self.search_bar)
        main_layout.addSpacing(20)

        panes_layout = QHBoxLayout()
        main_layout.addLayout(panes_layout)
        panes_layout.setSpacing(20)

        # Left Column
        left_column_layout = QVBoxLayout()
        left_column_layout.setContentsMargins(0, 0, 0, 0)
        left_column_layout.setSpacing(0)

        dates_heading = QLabel("Dates")
        dates_heading.setObjectName("heading_label")
        
        left_pane_widget = QWidget()
        left_pane_widget.setObjectName("left_pane")
        left_pane_layout = QVBoxLayout(left_pane_widget)

        self.date_list = QListWidget()
        self.date_list.currentItemChanged.connect(self.update_history_view)
        
        self.clear_button = QPushButton("Clear Selected")
        self.clear_button.setObjectName("clear_button")
        self.clear_button.clicked.connect(self.clear_selected_date)

        self.view_data_button = QPushButton("Open Data Folder")
        self.view_data_button.setObjectName("data_button")
        self.view_data_button.clicked.connect(self.open_data_directory)

        button_layout = QVBoxLayout()
        button_layout.addWidget(self.clear_button)
        button_layout.addWidget(self.view_data_button)
        
        left_pane_layout.addWidget(self.date_list)
        left_pane_layout.addLayout(button_layout)
        
        left_column_layout.addWidget(dates_heading)
        left_column_layout.addWidget(left_pane_widget)

        left_column_container = QWidget()
        left_column_container.setFixedWidth(250)
        left_column_container.setLayout(left_column_layout)

        # Right Column
        right_column_layout = QVBoxLayout()
        right_column_layout.setContentsMargins(0, 0, 0, 0)
        right_column_layout.setSpacing(0)

        history_heading = QLabel("History")
        history_heading.setObjectName("heading_label")
        
        right_pane_widget = QWidget()
        right_pane_widget.setObjectName("right_pane")
        right_pane_layout = QVBoxLayout(right_pane_widget)

        self.history_list = QListWidget()
        self.history_list.itemDoubleClicked.connect(self.copy_item_to_clipboard)
        self.history_list.setSpacing(5)
        
        right_pane_layout.addWidget(self.history_list)
        
        right_column_layout.addWidget(history_heading)
        right_column_layout.addWidget(right_pane_widget)

        panes_layout.addWidget(left_column_container)
        panes_layout.addLayout(right_column_layout)

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
        """Filters the history view based on a search query."""
        self.history_list.clear()
        query_lower = query.lower()

        all_items = []
        for date_key in self.history_manager.get_all_dates():
            all_items.extend(self.history_manager.get_history_for_date(date_key))

        for timestamp_str, item_type, content in all_items:
            if item_type == "text" and query_lower in content.lower():
                self.add_item_to_history_list(timestamp_str, item_type, content)

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
        all_dates = self.history_manager.get_all_dates()
        
        today = datetime.now().date()
        date_groups = {}
        for date_obj in all_dates:
            if date_obj == today:
                group = "Today"
            elif (today - date_obj).days == 1:
                group = "Yesterday"
            else:
                group = date_obj.strftime("%b %d, %Y")
            
            if group not in date_groups:
                date_groups[group] = date_obj

        for group_name, date_obj in date_groups.items():
            item = QListWidgetItem(group_name)
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

        for timestamp_str, item_type, content in date_history:
            self.add_item_to_history_list(timestamp_str, item_type, content)

    def handle_new_item(self, timestamp_str, item_type, content):
        """Slot to handle a new item from the clipboard monitor."""
        new_item_date = datetime.fromisoformat(timestamp_str).date()

        current_dates = [self.date_list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.date_list.count())]
        if new_item_date not in current_dates:
            self.update_date_list()

        if self.search_bar.text():
            self.perform_search(self.search_bar.text())
        else:
            current_item = self.date_list.currentItem()
            if current_item and current_item.data(Qt.ItemDataRole.UserRole) == new_item_date:
                self.update_history_view()

    def add_item_to_history_list(self, timestamp_str, item_type, content):
        """Adds a single styled item to the history list."""
        item_time = datetime.fromisoformat(timestamp_str).strftime("%H:%M:%S")
        
        filepath = content if item_type == "image" else None
        
        item_widget = HistoryItemWidget(item_time, content, item_type, filepath)

        list_item = QListWidgetItem()
        list_item.setSizeHint(item_widget.sizeHint())
        list_item.setData(Qt.ItemDataRole.UserRole, (item_type, content))
        
        self.history_list.addItem(list_item)
        self.history_list.setItemWidget(list_item, item_widget)
        
    def clear_selected_date(self):
        selected_item = self.date_list.currentItem()
        if not selected_item:
            return
        date_obj = selected_item.data(Qt.ItemDataRole.UserRole)
        self.history_manager.clear_date(date_obj)
        self.update_date_list()
        self.history_list.clear()

    def clear_all_history(self):
        reply = QMessageBox.question(self, 'Confirm Clear',
                                     "Are you sure you want to clear all history?",
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                     QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            self.history_manager.clear_all()
            self.update_date_list()
            self.history_list.clear()

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
        
        # --- MODIFIED: Connect the activated signal ---
        self.tray_icon.activated.connect(self.on_tray_icon_activated)
        
        self.tray_icon.show()

    # --- ADDED: Method to handle tray icon activation ---
    def on_tray_icon_activated(self, reason):
        """Toggle window visibility on a single left-click."""
        # QSystemTrayIcon.ActivationReason.Trigger is the enum for a single left-click
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            if self.isHidden():
                self.showNormal()  # Restore the window
                self.activateWindow()  # Bring it to the front
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
    
    app.setStyleSheet(APP_STYLESHEET)

    APP_NAME = "ClipboardHistoryApp"
    app_data_dir = os.path.join(os.path.expanduser("~"), f".{APP_NAME}")
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