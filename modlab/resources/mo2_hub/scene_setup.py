"""Coordinate background scenery preparation with MO2's main-thread activation."""
from pathlib import Path

from . import background, scene_workflow as scene, scene_build
from .installation import write_record
from .outputs import read_manifest
from .skse import require_game_closed


class SceneSetup:
    def __init__(self, parent, finished):
        self.parent, self.organizer, self.finished = parent, parent.organizer, finished
        self.request = scene.load(self.organizer, 'pending')
        self.saved = scene.load(self.organizer)
        self.target = Path(self.organizer.modsPath())/scene.own_name(self.organizer)
        self.context = scene.snapshot(self.organizer)

    def start(self):
        import mobase
        require_game_closed()
        self.parent.summary.setText('Preparing your selected roads, mountains and other scenery…')
        if self.request:
            if self.request['choices']:
                background.run(self.parent, lambda: scene.prepare(self.context, self.request), self.prepared)
            else:
                background.run(self.parent, self.check_reset, self.reset_ready)
        elif self.saved:
            active = bool(self.organizer.modList().state(self.target.name) & mobase.ModState.ACTIVE)
            if self.saved.get('status') == 'reset':
                if active: raise ValueError('The cleared scenery output was enabled outside ModLab. Review Graphics choices.')
                self.finished(None); return
            if not active:
                raise ValueError('The selected scenery output is disabled. Keep that change or select your scenery again in Graphics choices.')
            effective = {name: self.organizer.resolvePath(name) for name in self.saved['hashes']}
            background.run(self.parent, lambda: scene.verify_saved(self.target, self.saved, self.context, effective),
                           self.rechecked)
        else:
            self.finished(None)

    def context_current(self):
        scene.check_snapshot(self.context, scene.snapshot(self.organizer))
        if self.request: scene.check_request(self.organizer, self.request)

    def failed(self, error):
        if self.request and self.organizer.profilePath() == self.request['profile_path']:
            current = scene.load(self.organizer, 'pending')
            if current and current['id'] == self.request['id']:
                current.update(status='Needs attention', error=str(error))
                write_record(scene.path_for(self.organizer, 'pending'), current)
        self.finished(str(error))

    def rechecked(self, unused, error):
        if error: self.failed(error); return
        try:
            self.context_current()
            self.finished(None)
        except Exception as issue: self.failed(issue)

    def prepared(self, job, error):
        if error: self.failed(error); return
        try:
            import mobase
            self.context_current(); require_game_closed()
            self.job = job
            if not self.target.exists():
                mod = self.organizer.createMod(mobase.GuessedString(self.target.name))
                if mod is None or Path(mod.absolutePath()).resolve() != self.target.resolve():
                    raise ValueError('MO2 could not create the scenery output mod.')
            background.run(self.parent, lambda: scene_build.publish(self.target, job, self.context['profile_path']), self.published)
        except Exception as issue: self.failed(issue)

    def after_refresh(self, action):
        from PyQt6.QtCore import QTimer
        self.organizer.onNextRefresh(lambda: QTimer.singleShot(0, action), False)
        self.organizer.refresh()

    def published(self, unused, error):
        if error: self.failed(error); return
        try:
            self.context_current()
            self.after_refresh(self.activate)
        except Exception as issue: self.failed(issue)

    def activate(self):
        try:
            self.context_current(); require_game_closed()
            mods = self.organizer.modList()
            if not mods.setPriority(self.target.name, max(mods.priority(n) for n in mods.allMods())):
                raise ValueError('MO2 could not position the scenery output.')
            if not mods.setActive(self.target.name, True):
                raise ValueError('MO2 could not enable the scenery output.')
            self.after_refresh(self.check_applied)
        except Exception as issue: self.failed(issue)

    def check_applied(self):
        try:
            self.context_current()
            effective = {name: self.organizer.resolvePath(name) for name in self.job['hashes']}
            background.run(self.parent, lambda: scene.verify_saved(self.target, self.job, self.context, effective), self.applied)
        except Exception as issue: self.failed(issue)

    def applied(self, unused, error):
        if error: self.failed(error); return
        try:
            self.context_current()
            self.job.update(status='Applied; file providers checked')
            scene_build.save(self.job)
            write_record(scene.path_for(self.organizer), self.job)
            scene.path_for(self.organizer, 'pending').unlink()
            self.finished(None)
        except Exception as issue: self.failed(issue)

    def check_reset(self):
        if self.target.exists(): read_manifest(self.target, self.context['profile_path'], 'Scene appearance')

    def reset_ready(self, unused, error):
        if error: self.failed(error); return
        try:
            import mobase
            self.context_current(); require_game_closed()
            mods = self.organizer.modList()
            if mods.state(self.target.name) & mobase.ModState.ACTIVE and not mods.setActive(self.target.name, False):
                raise ValueError('MO2 could not disable the scenery output. Its files were retained.')
            self.after_refresh(self.reset_applied)
        except Exception as issue: self.failed(issue)

    def reset_applied(self):
        try:
            import mobase
            self.context_current()
            if self.organizer.modList().state(self.target.name) & mobase.ModState.ACTIVE:
                raise ValueError('The scenery output is still active; reset was not confirmed.')
            write_record(scene.path_for(self.organizer), dict(profile_path=self.context['profile_path'],
                choices={}, status='reset', retained_output=str(self.target)))
            scene.path_for(self.organizer, 'pending').unlink()
            self.finished(None)
        except Exception as issue: self.failed(issue)
