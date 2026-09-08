"""A focused next step for one requirement, using the existing installer and checks."""
from PyQt6.QtCore import QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import QDialog, QVBoxLayout, QScrollArea, QWidget

from .guidance import display_name, finding_summary
from .hub_view import button, label
from .mod_documents import download_page
from .resolution import resolution_context


class ResolutionDialog(QDialog):
    def __init__(self, organizer, finding, snapshot, parent=None):
        super().__init__(parent)
        self.setWindowTitle('ModLab — Resolve setup requirement')
        self.resize(760, 680)
        self.next_action = None
        self.provider = None
        self.context = resolution_context(finding, snapshot)
        self.profile_path = organizer.profilePath()
        self.settings_source = None
        layout = QVBoxLayout(self)
        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        body = QWidget(); content = QVBoxLayout(body); content.setSpacing(14)
        content.addWidget(label(finding.title, 'pageTitle'))
        content.addWidget(label(f'Profile: {snapshot.profile} · Skyrim {snapshot.runtime}', 'muted'))
        if self.context.providers:
            content.addWidget(label('Needed by' if self.context.dependency else 'Affected mods', 'sectionTitle'))
            content.addWidget(label('\n'.join(display_name(p) for p in self.context.providers)))
        content.addWidget(label(finding_summary(finding)))
        content.addWidget(label('What to do next', 'sectionTitle'))
        content.addWidget(label(finding.action))
        if self.context.can_enable:
            content.addWidget(label('This dependency is already installed. ModLab can enable its plugin, '
                'then check dependencies, order and applicable preparation.'))
            content.addWidget(button('Enable ' + self.context.dependency,
                                    lambda: self.choose('enable'), True))
        for index, url in enumerate(self.context.links):
            title = 'Open source linked in advice' + (f' {index + 1}' if len(self.context.links) > 1 else '')
            content.addWidget(button(title, lambda u=url: QDesktopServices.openUrl(QUrl(u))))
        if finding.code in {'shape-support-morph-disabled', 'shape-support-controller-choice'}:
            from pathlib import Path
            from .outputs import digest
            from .shape_settings import RELATIVE
            source = Path(organizer.resolvePath(RELATIVE))
            self.settings_source = (str(source), digest(source))
            if finding.code == 'shape-support-morph-disabled':
                title, action = 'Enable body shape support', 'enable-morphs'
                content.addWidget(label('ModLab will enable body morphs in a separate settings output and check that it takes effect. Your other settings are retained.'))
            else:
                title, action = 'Use OBody for body shapes', 'use-obody'
                content.addWidget(label('This disables RaceMenu BodyGen randomization. Choose it only if you want OBody to control shapes. Existing source settings are backed up and other values stay unchanged.'))
            content.addWidget(button(title, lambda a=action: self.choose(a), True))
            content.addWidget(button('Keep existing settings for now', self.reject))
        elif finding.code == 'engine-fixes-preloader-missing':
            content.addWidget(label('This component belongs beside SkyrimSE.exe, outside the normal mod folders. '
                'Automatic preloader installation is not implemented. Follow the linked release instructions, '
                'then recheck here; installing it as a normal Data mod would put it in the wrong place.'))
            content.addWidget(button('Open the selected Skyrim folder',
                lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(snapshot.game_root))))
        elif finding.code != 'record-errors' and not self.context.can_enable:
            content.addWidget(label('After downloading, choose the archive below. Installer options remain yours; '
                'ModLab will inspect the installed selection and continue preparation. '
                'Opening a source page does not confirm that every file there matches your game.'))
            content.addWidget(button('Install downloaded archive…', lambda: self.choose('install'), True))
        for provider in self.context.providers:
            page = download_page(organizer.modsPath(), provider)
            if page and page not in self.context.links:
                content.addWidget(button('Author’s page · ' + display_name(provider),
                                         lambda u=page: QDesktopServices.openUrl(QUrl(u))))
            content.addWidget(button('Installed instructions · ' + display_name(provider),
                                     lambda p=provider: self.choose('documents', p)))
        if finding.code == 'record-errors':
            content.addWidget(label('An author-provided patch or update may address these records. '
                'ModLab has not identified a verified repair for this finding; it will not clean it automatically.'))
            content.addWidget(button('Install a downloaded patch…', lambda: self.choose('install')))
        content.addWidget(button('I made a change — recheck setup', lambda: self.choose('recheck')))
        content.addStretch()
        scroll.setWidget(body); layout.addWidget(scroll)
        layout.addWidget(button('Decide later', self.reject))

    def choose(self, action, provider=None):
        self.next_action = action
        self.provider = provider
        self.accept()
