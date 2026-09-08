"""Browsing is read-only. Only an explicit Apply can begin source preparation."""
from . import pgpatcher_workflow as graphics
from .loot_workflow import context_signature
from .skse import require_game_closed


def capture_dialog(dialog):
    dialog.profile_path = dialog.organizer.profilePath()
    dialog.choice_signature = context_signature(dialog.organizer)
    dialog.operation_started = False
    dialog.applied = False
    dialog.preferences_changed = False
    dialog.operation_running = False
    dialog.awaiting_publication = False


def begin_apply(dialog, action):
    if dialog.organizer.profilePath() != dialog.profile_path:
        raise ValueError('The selected profile changed. Reopen this screen before applying choices.')
    if context_signature(dialog.organizer) != dialog.choice_signature:
        raise ValueError('The setup changed while these choices were open. Reopen them before applying.')
    require_game_closed()
    dialog.operation_started = True
    dialog.operation_running = True
    dialog.setEnabled(False)
    def ready():
        try:
            if dialog.organizer.profilePath() != dialog.profile_path:
                raise ValueError('The selected profile changed during preparation.')
            action()
        except Exception as error:
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.warning(dialog, 'Preparation needs attention', str(error))
        finally:
            if not dialog.awaiting_publication:
                end_apply(dialog)
    try:
        if not graphics.withdraw_for_upstream(dialog.organizer, ready):
            ready()
    except Exception:
        end_apply(dialog)
        raise


def end_apply(dialog):
    dialog.awaiting_publication = False
    dialog.operation_running = False
    dialog.setEnabled(True)


def finish_dialog(hub, dialog, schedule):
    if getattr(dialog, 'applied', False):
        schedule(hub.finish_setup)
        return
    if not getattr(dialog, 'operation_started', False):
        if getattr(dialog, 'preferences_changed', False):
            hub.automatic_signature = None
            hub.refresh()
        return
    # A failed explicit attempt may have withdrawn downstream output. Restore only
    # a verified, still-current result, without retrying the failed source helper.
    try:
        if hub.organizer.profilePath() != dialog.profile_path:
            raise ValueError('The profile changed. Return to the original profile to review its pending preparation.')
        if graphics.withdrawn_path(hub.organizer).exists():
            saved = graphics.load_choices(hub.organizer)
            if not saved:
                raise ValueError('Previous graphics choices are missing. Review Graphics before preparing again.')
            def restored(target, error):
                hub.refresh()
                if error:
                    hub.summary.setText('Previous graphics output needs attention: ' + str(error))
            graphics.reapply_saved(hub.organizer, saved, restored)
        else:
            hub.refresh()
    except Exception as error:
        hub.refresh()
        hub.summary.setText('Preparation remains unfinished: ' + str(error))
