"""ModLab's guided Qt presentation, using the existing operation handlers."""
from html import escape
import re
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QScrollArea, QSplitter, QStackedWidget, QTableWidget, QTextBrowser, QVBoxLayout, QWidget)
from .guidance import display_name, finding_action, finding_summary, setup_guidance


STYLE = '''
QDialog#ModLabHub { background: #f3f5f8; }
QWidget { font-family: "Segoe UI"; font-size: 11pt; color: #243247; }
QFrame#sidebar { background: #172438; border-radius: 12px; }
QFrame#sidebar QLabel { color: #b9c9dd; }
QLabel#brand { color: white; font-size: 24pt; font-weight: 700; }
QPushButton { background: white; border: 1px solid #cbd4df; border-radius: 6px; padding: 9px 13px; text-align: left; }
QPushButton:hover { background: #edf5f7; border-color: #5d929a; }
QPushButton:focus { border: 2px solid #16808a; }
QPushButton:disabled { color: #798797; background: #edf0f4; }
QPushButton[primary="true"] { color: white; background: #126a74; border-color: #126a74; font-weight: 600; }
QPushButton[primary="true"]:hover { background: #0e5963; }
QPushButton[nav="true"] { color: #dce6f1; background: transparent; border: none; padding: 12px; }
QPushButton[nav="true"]:checked { color: white; background: #31455e; font-weight: 600; }
QPushButton[nav="true"]:hover { background: #273c53; }
QLabel#pageTitle { font-size: 20pt; font-weight: 600; color: #172438; }
QLabel#muted { color: #586a7e; }
QLabel#sectionTitle { font-size: 12pt; font-weight: 600; }
QFrame#taskCard, QFrame#hero { background: white; border: 1px solid #dce2eb; border-radius: 9px; }
QFrame#hero { border-left: 4px solid #16808a; }
QLineEdit { background: white; border: 1px solid #cbd4df; border-radius: 6px; padding: 9px; }
QTextBrowser, QTableWidget { background: white; border: 1px solid #dce2eb; border-radius: 6px; padding: 8px; selection-background-color: #d6ecee; selection-color: #172438; }
QHeaderView::section { background: #e9eef4; padding: 8px; border: none; font-weight: 600; }
QScrollArea { border: none; background: transparent; }
QSplitter::handle { background: #e3e8ef; }
'''


def label(text, name=None):
    widget = QLabel(text)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    widget.setWordWrap(True)
    widget.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    if name:
        widget.setObjectName(name)
    return widget


def button(text, callback, primary=False):
    widget = QPushButton(text.replace('&', '&&'))
    widget.setProperty('primary', primary)
    widget.setAutoDefault(False)
    widget.clicked.connect(lambda checked=False: callback())
    return widget


class HubView:
    def __init__(self, hub):
        self.hub = hub
        self.selected = None
        self.plan = None
        self.names = {}
        hub.setObjectName('ModLabHub')
        hub.setStyleSheet(STYLE)
        hub.resize(1240, 820)
        hub.setMinimumSize(940, 620)
        root = QHBoxLayout(hub)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(20)
        sidebar = QFrame(); sidebar.setObjectName('sidebar'); sidebar.setFixedWidth(190)
        nav = QVBoxLayout(sidebar); nav.setContentsMargins(16, 22, 16, 18); nav.setSpacing(8)
        nav.addWidget(label('ModLab', 'brand'))
        nav.addWidget(label('Your mods. Your choices.'))
        nav.addSpacing(24)
        root.addWidget(sidebar)
        content = QVBoxLayout(); content.setSpacing(14); root.addLayout(content, 1)
        top = QHBoxLayout()
        self.page_title = label('Continue setup', 'pageTitle'); top.addWidget(self.page_title, 1)
        top.addWidget(button('Add mods…', hub.install))
        top.addWidget(button('Launch Skyrim', hub.launch))
        content.addLayout(top)
        hub.context = label('Reading your selected profile…', 'muted'); content.addWidget(hub.context)
        self.split = QSplitter(Qt.Orientation.Horizontal)
        self.pages = QStackedWidget(); self.split.addWidget(self.pages)
        content.addWidget(self.split, 1)
        self.nav_buttons = []
        for index, title in enumerate(('Continue setup', 'Mods & choices', 'Tools & setup', 'Diagnostics')):
            item = button(title, lambda i=index: self.navigate(i))
            item.setCheckable(True); item.setProperty('nav', True)
            self.nav_buttons.append(item); nav.addWidget(item)
        nav.addStretch()
        nav.addWidget(button('History & recovery', hub.history))
        nav.addWidget(label('SKYRIM SPECIAL EDITION', 'muted'))
        self.build_overview()
        self.build_choices()
        self.build_tools()
        self.build_diagnostics()
        detail_box = QWidget(); detail_layout = QVBoxLayout(detail_box)
        detail_layout.setContentsMargins(12, 0, 0, 0)
        detail_layout.addWidget(label('Details', 'sectionTitle'))
        hub.detail = QTextBrowser(); hub.detail.setOpenExternalLinks(True)
        hub.detail.setPlaceholderText('Select a task or finding for its explanation and evidence.')
        detail_layout.addWidget(hub.detail, 1)
        self.detail_action = button('Continue', self.run_selected)
        self.detail_action.hide(); detail_layout.addWidget(self.detail_action)
        hub.technical = button('Show technical evidence', lambda: self.show_selected())
        hub.technical.setCheckable(True)
        detail_layout.addWidget(hub.technical)
        self.split.addWidget(detail_box)
        self.split.setSizes([650, 340])
        self.navigate(0)

    def build_overview(self):
        page = QWidget(); layout = QVBoxLayout(page); layout.setContentsMargins(0, 0, 0, 0)
        hero = QFrame(); hero.setObjectName('hero'); inner = QVBoxLayout(hero)
        inner.setContentsMargins(20, 18, 20, 18); inner.setSpacing(12)
        self.state_title = label('Reading your setup', 'sectionTitle'); inner.addWidget(self.state_title)
        self.hub.summary = label('Checking current files and saved preparation…'); inner.addWidget(self.hub.summary)
        self.primary = button('Continue', self.run_primary, True); inner.addWidget(self.primary)
        self.recheck = button('Recheck setup', self.hub.finish_setup); inner.addWidget(self.recheck)
        layout.addWidget(hero)
        self.task_scroll = QScrollArea(); self.task_scroll.setWidgetResizable(True)
        layout.addWidget(self.task_scroll, 1)
        self.pages.addWidget(page)

    def action_page(self, heading, description, actions):
        page = QWidget(); layout = QVBoxLayout(page); layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(label(heading, 'sectionTitle')); layout.addWidget(label(description, 'muted'))
        layout.addSpacing(12)
        for title, explanation, action in actions:
            card = QFrame(); card.setObjectName('taskCard'); inner = QVBoxLayout(card)
            inner.setContentsMargins(16, 12, 16, 12)
            inner.addWidget(button(title, action)); inner.addWidget(label(explanation, 'muted'))
            layout.addWidget(card)
        layout.addStretch()
        scroll = QScrollArea(); scroll.setWidgetResizable(True); scroll.setWidget(page)
        self.pages.addWidget(scroll)

    def build_choices(self):
        h = self.hub
        self.action_page('Make it yours', 'Your existing choices remain available. Changes are checked through the preparation workflow.', (
            ('Bodies & outfits', 'Choose available projects and presets for generated outfits.', h.bodyslide),
            ('Character appearances', 'Choose appearance providers for the characters covered by your installed mods.', h.npc_appearances),
            ('Individual body shape support', 'Set up the optional helper and its dependencies for character-specific shapes.', h.character_shape_support),
            ('Animations', 'Review the selected animation patches and generated behavior setup.', h.pandora),
            ('Gameplay patches', 'Configure the supported patchers for your selected setup.', h.synthesis),
            ('Graphics', 'Choose supported graphics preparation and review its output.', h.graphics)))

    def build_tools(self):
        h = self.hub
        self.action_page('Tools & installation', 'Advanced helpers remain available when you need them.', (
            ('Tool locations & game setup', 'Locate helpers and inspect your game installation.', h.helpers),
            ('Resume an installation queue', 'Continue unfinished archives without repeating completed installations.', h.resume_queue),
            ('Mod instructions', 'Read installed documentation and source links.', h.documents),
            ('LOOT details', 'Inspect sorting advice and checked plugin order.', h.loot),
            ('xEdit checks & cleaning', 'Expert record inspection and supported cleaning operations.', h.xedit),
            ('Open mods folder', 'Browse the managed installation files.', h.open_mods),
            ('Save diagnostic report', 'Export the current findings and exact technical evidence.', h.save_report)))

    def build_diagnostics(self):
        h = self.hub
        page = QWidget(); layout = QVBoxLayout(page); layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(label('All findings', 'sectionTitle'))
        layout.addWidget(label('Complete evidence, including file overlaps and checks that still need investigation.', 'muted'))
        h.search = QLineEdit(); h.search.setPlaceholderText('Search mods, files, requirements or messages')
        h.search.textChanged.connect(h.filter_rows); layout.addWidget(h.search)
        h.table = QTableWidget(0, 3)
        h.table.setHorizontalHeaderLabels(['Status', 'Finding', 'Next action'])
        h.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        h.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        h.table.setAlternatingRowColors(True)
        h.table.horizontalHeader().setStretchLastSection(True)
        h.table.setColumnWidth(0, 80); h.table.setColumnWidth(1, 250)
        h.table.itemSelectionChanged.connect(h.show_selection)
        layout.addWidget(h.table, 1); self.pages.addWidget(page)

    def navigate(self, index):
        self.pages.setCurrentIndex(index)
        self.page_title.setText(self.nav_buttons[index].text().replace('&&', '&'))
        for i, item in enumerate(self.nav_buttons):
            item.setChecked(i == index)

    def friendly(self, text):
        for original, short in self.names.items():
            text = text.replace(original, short)
        return text

    def render(self, findings, snapshot, checked=False):
        self.plan = setup_guidance(findings, checked=checked)
        origins = set()
        if snapshot:
            origins.update(p.origin for p in snapshot.plugins if p.origin)
            origins.update(o for a in snapshot.assets for o in a.origins)
        self.names = {name: display_name(name) for name in sorted(origins, key=len, reverse=True)
                      if display_name(name) != name}
        self.state_title.setText(self.plan.title)
        self.hub.summary.setText(self.plan.summary)
        self.primary.setText(self.plan.action_label)
        self.recheck.setVisible(self.plan.action != 'finish_setup')
        previous_scroll = self.task_scroll.verticalScrollBar().value()
        page = QWidget(); layout = QVBoxLayout(page); layout.setContentsMargins(0, 12, 0, 0); layout.setSpacing(12)
        ordering = [('attention', 'Needs attention'), ('choices', 'Your choices'), ('work', 'ModLab can prepare')]
        if self.plan.action == 'finish_setup':
            ordering = [('work', 'ModLab can prepare'), ('choices', 'Your choices'), ('attention', 'Unresolved findings')]
        for key, heading in ordering:
            group = self.plan.groups[key]
            if not group:
                continue
            layout.addWidget(label(f'{heading} · {len(group)}', 'sectionTitle'))
            for finding in group:
                card = QFrame(); card.setObjectName('taskCard'); inner = QVBoxLayout(card)
                inner.setContentsMargins(16, 14, 16, 14); inner.setSpacing(10)
                inner.addWidget(label(self.friendly(finding.title), 'sectionTitle'))
                inner.addWidget(label(self.friendly(finding_summary(finding)), 'muted'))
                row = QHBoxLayout()
                action, title = finding_action(finding)
                if action:
                    row.addWidget(button(title, lambda f=finding: self.run_finding(f)))
                row.addWidget(button('Details', lambda f=finding: self.select(f))); row.addStretch()
                inner.addLayout(row); layout.addWidget(card)
        observations = self.plan.groups['observations']
        if observations:
            layout.addWidget(label(f'Other findings · {len(observations)}', 'sectionTitle'))
            layout.addWidget(label('Includes file replacements, completed checks and outstanding startup observations. Overlap alone is not an installation failure; unresolved effects remain recorded.', 'muted'))
            layout.addWidget(button('View all findings', lambda: self.navigate(3)))
        if self.hub.automatic_steps:
            layout.addWidget(label(f'Last preparation run · {len(self.hub.automatic_steps)} completed steps', 'sectionTitle'))
            layout.addWidget(button('See what ModLab completed', self.show_completed))
        layout.addStretch()
        old = self.task_scroll.takeWidget()
        if old:
            old.deleteLater()
        self.task_scroll.setWidget(page)
        self.task_scroll.verticalScrollBar().setValue(previous_scroll)
        if self.selected:
            current = next((f for f in findings if (f.code, f.title, f.detail) == self.selected), None)
            if current:
                self.select(current)
            else:
                self.selected = None; self.detail_action.hide(); self.hub.detail.clear()
        else:
            first = next((f for key, _ in ordering for f in self.plan.groups[key]), None)
            if first:
                self.select(first)

    def select(self, finding):
        self.selected = (finding.code, finding.title, finding.detail)
        self.show_selected()

    def show_selected(self):
        if not self.selected:
            return
        finding = next((f for f in self.hub.findings if (f.code, f.title, f.detail) == self.selected), None)
        if finding is None:
            return
        def html(text):
            safe = escape(text)
            safe = re.sub(r'https?://[^\s<>]+', lambda m: '<a href="' + m.group().rstrip('.,;') + '">' + m.group().rstrip('.,;') + '</a>', safe)
            return safe.replace('\n', '<br>')
        text = '<h2>' + html(self.friendly(finding.title)) + '</h2>'
        text += '<p><b>Status:</b> ' + html(finding.level) + '</p>'
        text += '<h3>What this means</h3><p>' + html(self.friendly((finding.explanation or finding.detail).split('\n\n')[0])) + '</p>'
        text += '<h3>Next step</h3><p>' + html(self.friendly(finding.action)) + '</p>'
        if self.hub.technical.isChecked():
            text += '<h3>Technical evidence</h3><p>' + html(finding.detail) + '</p>'
        self.hub.detail.setHtml(text)
        action, title = finding_action(finding)
        self.detail_action.setVisible(bool(action))
        if title:
            self.detail_action.setText(title)

    def run_finding(self, finding):
        self.select(finding)
        action, _ = finding_action(finding)
        if action == 'resolve_finding':
            if not self.hub.processing and not self.hub.installing:
                self.hub.resolve_finding(finding)
        elif action:
            self.invoke(action)

    def show_completed(self):
        self.selected = None
        self.detail_action.hide()
        self.hub.detail.setPlainText('Last preparation run\n\nThese steps were recorded by the last run. '
            'Current tasks above reflect any changes since then.\n\n' + '\n\n'.join(self.hub.automatic_steps))

    def run_selected(self):
        finding = next((f for f in self.hub.findings if (f.code, f.title, f.detail) == self.selected), None)
        if finding:
            self.run_finding(finding)

    def invoke(self, action):
        if not self.hub.processing and not self.hub.installing:
            getattr(self.hub, action)()

    def run_primary(self):
        if self.plan is None:
            return
        if self.plan.action == 'details':
            self.select(self.plan.groups['attention'][0])
        elif self.plan.action == 'resolve_finding':
            self.run_finding(self.plan.groups['attention'][0])
        else:
            self.invoke(self.plan.action)
