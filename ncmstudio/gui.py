from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import sys
import threading
import time
import uuid

from PySide6.QtCore import Qt, QThread, Signal, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QFont
from PySide6.QtWidgets import (QApplication, QAbstractItemView, QFileDialog, QFrame, QHBoxLayout,
                              QHeaderView, QLabel, QLineEdit, QMainWindow, QMessageBox, QPlainTextEdit,
                              QProgressBar, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)
from . import __version__
from .core import Cancelled, convert, save_json, scan, validate_roots

STYLE = '''
QMainWindow, QWidget#root { background: #f3f6f5; color: #203c35; }
QWidget { font-family: "Microsoft YaHei UI", "Segoe UI"; font-size: 13px; }
QFrame#card { background: white; border: 1px solid #dce5e1; border-radius: 12px; }
QLabel { background: transparent; color: #203c35; }
QLabel#title { font-size: 29px; font-weight: 700; }
QLabel#subtitle, QLabel#muted { color: #74877f; }
QLabel#eyebrow { color: #267e67; font-size: 11px; font-weight: 700; }
QLineEdit { background: #f7f9f8; border: 1px solid #dbe5e0; border-radius: 6px; padding: 10px; color: #294b40; }
QLineEdit:focus { border: 1px solid #28886b; }
QPushButton { border: 1px solid #d3e0d9; border-radius: 6px; background: white; padding: 9px 17px; color: #294b40; }
QPushButton:hover { background: #eaf3ee; border-color: #8bb8a5; }
QPushButton:pressed { background: #dcebe3; }
QPushButton#primary { background: #21775d; border-color: #21775d; color: white; font-weight: 600; }
QPushButton#primary:hover { background: #185e49; }
QPushButton:disabled { color: #9ba9a3; background: #edf1ee; border-color: #e3e9e5; }
QPushButton#stop { color: #ac5b39; }
QTableWidget { background: white; alternate-background-color: #f7faf8; border: 0; gridline-color: #edf1ee; selection-background-color: #e4f1eb; selection-color: #183b2e; }
QTableWidget::item { padding: 7px; border-bottom: 1px solid #edf1ee; }
QHeaderView::section { background: #f2f6f3; color: #718178; border: 0; padding: 10px 5px; font-size: 12px; }
QPlainTextEdit { background: #f7faf8; color: #62776d; border: 1px solid #e1e9e3; border-radius: 6px; font-size: 12px; }
QProgressBar { background: #e3ebe5; border: 0; border-radius: 4px; max-height: 8px; min-height: 8px; }
QProgressBar::chunk { background: #309175; border-radius: 4px; }
QScrollBar:vertical { background: #f4f7f4; width: 10px; }
QScrollBar::handle:vertical { background: #c6d6cb; min-height: 28px; border-radius: 4px; }
'''


class Worker(QThread):
    found = Signal(object)
    changed = Signal(int, str, str)
    progress = Signal(str, int)
    done = Signal(object)
    error = Signal(str)

    def __init__(self, source, output=None, selected=None):
        super().__init__()
        self.source, self.output, self.selected = source, output, selected
        self.stop = threading.Event()

    def run(self):
        try:
            if self.selected is None:
                count = 0
                for track in scan(self.source, self.stop):
                    count += 1
                    self.found.emit(track)
                self.done.emit({'scan': count})
                return
            results = []
            for number, (row, track) in enumerate(self.selected):
                if self.stop.is_set():
                    break
                last = [0.0]

                def update(phase, fraction):
                    now = time.monotonic()
                    if now - last[0] > 0.08 or fraction >= 1:
                        last[0] = now
                        self.progress.emit(f'{number + 1}/{len(self.selected)} · {track.title} · {phase}',
                                           int((number + fraction) / len(self.selected) * 1000))

                self.changed.emit(row, '处理中', '正在提取与完整校验')
                try:
                    result = convert(track, self.source, self.output, self.stop, update)
                except Cancelled:
                    self.changed.emit(row, '已停止', '当前歌曲未完成，可重新转换')
                    break
                except Exception as error:
                    result = {'status': '失败', 'error': str(error)}
                result['file'] = track.relative
                results.append(result)
                detail = result.get('error') or '；'.join(result.get('warnings', [])) or result.get('output', '')
                self.changed.emit(row, result['status'], detail)
            report = {'time': datetime.now().astimezone().isoformat(), 'cancelled': self.stop.is_set(),
                      'selected': len(self.selected), 'results': results}
            name = datetime.now().strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:6] + '.json'
            report_path = Path(self.output) / '.ncm-studio' / 'reports' / name
            try:
                save_json(report_path, report)
                report['report_path'] = str(report_path)
            except OSError as error:
                report['report_error'] = str(error)
            self.done.emit(report)
        except Cancelled:
            self.done.emit({'scan_cancelled': True})
        except Exception as error:
            self.error.emit(str(error))


class MainWindow(QMainWindow):
    def __init__(self, input_path='', output_path='', settings_path=None):
        super().__init__()
        self.setWindowTitle(f'NCM Studio · 本地音乐整理  {__version__}')
        self.resize(1120, 800)
        self.setMinimumSize(940, 690)
        base = Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parents[1]
        self.settings_path = settings_path or base / '.ncm-studio' / 'settings.json'
        try:
            settings = json.loads(self.settings_path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            settings = {}
        self.tracks, self.worker, self.last_report = [], None, None
        self.scanned_source, self.close_pending = None, False
        self._build()
        self.source.setText(input_path or str(settings.get('source', '')))
        self.output.setText(output_path or str(settings.get('output', '')))
        self.source.textChanged.connect(self.invalidate_scan)
        self.update_selection()

    def _build(self):
        root = QWidget(objectName='root')
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(28, 24, 28, 20)
        layout.setSpacing(16)
        header = QHBoxLayout()
        headings = QVBoxLayout()
        headings.setSpacing(5)
        headings.addWidget(QLabel('LOCAL MUSIC / OFFLINE', objectName='eyebrow'))
        headings.addWidget(QLabel('让音乐，回到你的播放器。', objectName='title'))
        headings.addWidget(QLabel('NCM Studio  ·  提取原始 FLAC，整理封面与歌词。', objectName='subtitle'))
        header.addLayout(headings)
        header.addStretch()
        self.help_button = QPushButton('使用说明')
        self.help_button.clicked.connect(self.help)
        header.addWidget(self.help_button)
        layout.addLayout(header)

        card = QFrame(objectName='card')
        form = QVBoxLayout(card)
        form.setContentsMargins(20, 16, 20, 16)
        form.setSpacing(10)
        self.source, self.output = QLineEdit(), QLineEdit()
        self.source.setAccessibleName('网易云下载目录')
        self.output.setAccessibleName('转换保存目录')
        self.source.setPlaceholderText('选择网易云设置中的「下载目录」，不是安装目录')
        self.output.setPlaceholderText('选择保存位置，转换结果会放在其中的 Music 文件夹')
        self.browse_buttons = []
        for label, edit in [('01  下载目录', self.source), ('02  保存位置', self.output)]:
            row = QHBoxLayout()
            caption = QLabel(label)
            caption.setFixedWidth(104)
            row.addWidget(caption)
            row.addWidget(edit, 1)
            button = QPushButton('选择文件夹')
            button.setAccessibleName(label + '：选择文件夹')
            button.clicked.connect(lambda checked=False, target=edit: self.browse(target))
            self.browse_buttons.append(button)
            row.addWidget(button)
            form.addLayout(row)
        layout.addWidget(card)

        toolbar = QHBoxLayout()
        self.summary = QLabel('尚未扫描', objectName='muted')
        toolbar.addWidget(self.summary)
        toolbar.addStretch()
        self.all_button, self.none_button = QPushButton('全选可转换'), QPushButton('取消选择')
        self.all_button.clicked.connect(lambda: self.select_all(True))
        self.none_button.clicked.connect(lambda: self.select_all(False))
        self.scan_button = QPushButton('扫描文件')
        self.scan_button.clicked.connect(self.start_scan)
        for button in (self.all_button, self.none_button, self.scan_button):
            toolbar.addWidget(button)
        layout.addLayout(toolbar)

        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(['选择', '歌曲 / 文件', '艺术家', '专辑', '音质', '大小', '状态'])
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(46)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        head = self.table.horizontalHeader()
        for col, width in [(0, 50), (2, 145), (3, 145), (4, 150), (5, 85), (6, 126)]:
            head.setSectionResizeMode(col, QHeaderView.ResizeMode.Interactive)
            self.table.setColumnWidth(col, width)
        head.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.itemChanged.connect(self.update_selection)
        self.table.itemSelectionChanged.connect(self.show_detail)
        layout.addWidget(self.table, 1)
        self.detail = QLabel('扫描只读取本地文件。只有内部为无损 FLAC 的歌曲可以转换。', objectName='muted')
        self.detail.setWordWrap(True)
        self.detail.setMinimumHeight(34)
        layout.addWidget(self.detail)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(82)
        self.log.setPlaceholderText('处理记录会显示在这里。文件只在本机处理，不会上传。')
        layout.addWidget(self.log)
        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        layout.addWidget(self.progress)
        footer = QHBoxLayout()
        self.status = QLabel('选择下载目录，然后扫描。', objectName='muted')
        self.status.setWordWrap(True)
        footer.addWidget(self.status, 1)
        self.open_button = QPushButton('打开保存位置')
        self.open_button.clicked.connect(self.open_output)
        self.stop_button = QPushButton('停止', objectName='stop')
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self.stop)
        self.convert_button = QPushButton('转换所选', objectName='primary')
        self.convert_button.clicked.connect(self.start_conversion)
        for button in (self.open_button, self.stop_button, self.convert_button):
            footer.addWidget(button)
        layout.addLayout(footer)

    def browse(self, target):
        chosen = QFileDialog.getExistingDirectory(self, '选择文件夹', target.text() or str(Path.home() / 'Music'))
        if chosen:
            target.setText(chosen)

    def invalidate_scan(self):
        self.scanned_source = None
        self.tracks.clear()
        self.table.setRowCount(0)
        self.update_selection()

    def remember(self):
        try:
            save_json(self.settings_path, {'source': self.source.text(), 'output': self.output.text()})
        except OSError:
            self.log.appendPlainText('无法记住目录，下次打开时请重新选择。')

    def selected(self):
        return [(row, track) for row, track in enumerate(self.tracks)
                if track.convertible and self.table.item(row, 0).checkState() == Qt.CheckState.Checked]

    def update_selection(self, *unused):
        count = len(self.selected())
        eligible = sum(track.convertible for track in self.tracks)
        self.summary.setText(f'{len(self.tracks)} 个文件   ·   {eligible} 个可转换   ·   已选 {count}')
        self.convert_button.setText(f'转换所选 ({count})' if count else '转换所选')
        self.convert_button.setEnabled(bool(count) and self.worker is None)

    def select_all(self, selected):
        for row, track in enumerate(self.tracks):
            if track.convertible:
                self.table.item(row, 0).setCheckState(Qt.CheckState.Checked if selected else Qt.CheckState.Unchecked)

    def add_track(self, track):
        self.table.blockSignals(True)
        row = len(self.tracks)
        self.tracks.append(track)
        self.table.insertRow(row)
        check = QTableWidgetItem()
        check.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable |
                       (Qt.ItemFlag.ItemIsUserCheckable if track.convertible else Qt.ItemFlag.NoItemFlags))
        check.setCheckState(Qt.CheckState.Checked if track.convertible else Qt.CheckState.Unchecked)
        self.table.setItem(row, 0, check)
        for column, value in enumerate([track.title, track.artist or '—', track.album or '—',
                                        track.quality if track.codec == 'FLAC' else track.codec,
                                        f'{track.size / 1024**2:.1f} MB', track.status], 1):
            item = QTableWidgetItem(value)
            item.setToolTip(track.relative + '\n' + track.reason)
            self.table.setItem(row, column, item)
        self.table.item(row, 6).setForeground(QColor('#238263' if track.convertible else '#a77646'))
        self.table.blockSignals(False)
        self.update_selection()

    def show_detail(self):
        row = self.table.currentRow()
        if 0 <= row < len(self.tracks):
            self.detail.setText(self.table.item(row, 6).toolTip())

    def start_scan(self):
        if self.worker is not None:
            return
        source = Path(self.source.text().strip())
        if not self.source.text().strip() or not source.is_dir():
            QMessageBox.information(self, '选择下载目录', '请选择网易云实际下载音乐的文件夹。')
            return
        self.invalidate_scan()
        self.scanned_source = source.resolve()
        self.remember()
        self.progress.setRange(0, 0)
        self.status.setText('正在读取本地音乐信息…')
        self.start_worker(Worker(self.scanned_source))

    def start_conversion(self):
        if self.worker is not None or not self.selected():
            return
        try:
            if not self.output.text().strip():
                raise ValueError('请先选择转换结果的保存位置。')
            source, output = validate_roots(self.source.text(), self.output.text())
            if source != self.scanned_source:
                raise ValueError('下载目录已改变，请重新扫描。')
        except ValueError as error:
            QMessageBox.information(self, '检查文件夹', str(error))
            return
        self.remember()
        self.progress.setRange(0, 1000)
        self.progress.setValue(0)
        self.start_worker(Worker(source, output, self.selected()))

    def start_worker(self, worker):
        self.worker = worker
        worker.found.connect(self.add_track)
        worker.changed.connect(self.track_changed)
        worker.progress.connect(self.update_progress)
        worker.done.connect(self.completed)
        worker.error.connect(self.failed)
        worker.finished.connect(self.worker_finished)
        self.set_busy(True)
        worker.start()

    def set_busy(self, busy):
        for widget in [self.source, self.output, self.scan_button, self.all_button, self.none_button,
                       self.table, *self.browse_buttons]:
            widget.setEnabled(not busy)
        self.stop_button.setEnabled(busy)
        self.update_selection()

    def update_progress(self, message, value):
        self.status.setText(message)
        self.progress.setValue(value)

    def track_changed(self, row, status, detail):
        item = self.table.item(row, 6)
        item.setText(status)
        item.setToolTip(self.tracks[row].relative + '\n' + detail)
        item.setForeground(QColor('#b1543c' if status == '失败' else '#238263'))
        if status != '处理中':
            self.log.appendPlainText(f'{self.tracks[row].title} · {status}' + (f'\n  {detail}' if detail else ''))

    def completed(self, report):
        self.progress.setRange(0, 1000)
        if 'scan' in report:
            count = sum(track.convertible for track in self.tracks)
            self.status.setText(f'扫描完成。{count} 首可以转换。' if self.tracks else '没有找到 NCM 文件，请检查网易云的下载目录。')
        elif report.get('scan_cancelled'):
            self.status.setText('扫描已停止，已发现的歌曲仍可转换。')
        else:
            results = report['results']
            ok = sum(item['status'] == '转换成功' for item in results)
            skipped = sum(item['status'] == '已完成，跳过' for item in results)
            failed = sum(item['status'] == '失败' for item in results)
            prefix = '已停止' if report['cancelled'] else '处理完成'
            self.status.setText(f'{prefix} · 成功 {ok} · 已完成跳过 {skipped} · 失败 {failed}')
            if not report['cancelled']:
                self.progress.setValue(1000)
            self.last_report = report.get('report_path')
            if self.last_report:
                self.log.appendPlainText(f'报告已保存：{self.last_report}')
            if report.get('report_error'):
                self.log.appendPlainText('报告保存失败：' + report['report_error'])

    def failed(self, message):
        self.progress.setRange(0, 1000)
        self.status.setText('处理未完成，见下方记录。')
        self.log.appendPlainText(message)

    def worker_finished(self):
        self.worker.deleteLater()
        self.worker = None
        self.set_busy(False)
        if self.close_pending:
            self.close()

    def stop(self):
        if self.worker:
            self.worker.stop.set()
            self.stop_button.setEnabled(False)
            self.status.setText('正在停止并清理当前临时文件…')

    def open_output(self):
        if self.output.text().strip() and Path(self.output.text()).is_dir():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(self.output.text()).resolve())))
        else:
            QMessageBox.information(self, '保存位置', '请先选择保存位置并完成一次转换。')

    def help(self):
        QMessageBox.information(self, '使用说明',
            '1. 在网易云中完成下载，选择其「下载目录」。\n'
            '2. 点击「扫描文件」，勾选要转换的歌曲。\n'
            '3. 选择不同的保存位置，点击「转换所选」。\n\n'
            'Music 文件夹保存 FLAC 与同名 LRC；封面嵌在 FLAC 内。\n'
            '原文件始终保留；不覆盖不匹配的同名文件。\n'
            '内部是 MP3、缺少校验值或无法识别的文件会标明原因。\n'
            '程序完全离线，不下载歌曲、不登录账号、不自动获取封面。\n\n'
            f'NCM Studio {__version__} · 非网易云或 Sony 官方软件')

    def closeEvent(self, event):
        if self.worker:
            answer = QMessageBox.question(self, '停止并关闭？', '当前正在处理。停止后将保留已完成文件，未完成歌曲可以下次重试。')
            if answer == QMessageBox.StandardButton.Yes:
                self.close_pending = True
                self.stop()
            event.ignore()
        else:
            event.accept()


def main(input_path='', output_path=''):
    app = QApplication.instance() or QApplication(sys.argv)
    app.setStyle('Fusion')
    app.setFont(QFont('Microsoft YaHei UI', 10))
    app.setStyleSheet(STYLE)
    window = MainWindow(input_path, output_path)
    window.show()
    return app.exec()
