"""Explicitly reset managed appearance overrides without deleting source mods."""
import json
import os
from pathlib import Path
from uuid import uuid4
from .outputs import digest

KEEP_SAVED='__modlab_keep_saved__'


def appearance_selection(selected,saved,key,choice):
    result=dict(selected)
    if choice==KEEP_SAVED: choice=saved.get(key)
    if choice: result[key]=choice
    else: result.pop(key,None)
    return result


def reset_choices(organizer,on_done):
    import mobase
    from PyQt6.QtCore import QTimer
    from . import npc_workflow as workflow
    from .skse import require_game_closed
    require_game_closed()
    profile=organizer.profilePath(); target=Path(organizer.modsPath())/workflow.own_name(organizer)
    choices_path=workflow.path_for(organizer); original=choices_path.read_bytes()
    saved=json.loads(original)
    if not saved.get('selected'): raise ValueError('No saved character overrides remain to reset.')
    manifest=workflow.read_manifest(target,profile,'NPC Appearance')
    if manifest['hashes']!=saved['hashes']:
        raise ValueError('The managed appearance output changed. Review its retained build before resetting.')
    for name,checksum in manifest['hashes'].items():
        if digest(target/name)!=checksum:
            raise ValueError('The managed appearance output changed: '+name)
    plugins=organizer.pluginList(); mods=organizer.modList()
    for name in plugins.pluginNames():
        if name.casefold()==workflow.OUTPUT.casefold() or plugins.loadOrder(name)<0: continue
        path=organizer.resolvePath(name)
        if not path: raise ValueError('An active plugin cannot be inspected: '+name)
        if workflow.OUTPUT.casefold() in {m.casefold() for m in workflow.plugin_masters(Path(path))}:
            raise ValueError(name+' requires the generated appearance patch. Disable or rebuild that dependent patch before clearing all appearance overrides.')
    active=bool(mods.state(target.name)&mobase.ModState.ACTIVE)
    plugin_state=plugins.state(workflow.OUTPUT)
    directory=target.parent.parent/'builds/npc'/('reset-'+uuid4().hex[:12]); directory.mkdir(parents=True)
    backup=directory/'previous-choices.json'; backup.write_bytes(original)
    journal=directory/'operation.json'
    record=dict(status='Reset pending',profile_path=profile,mod=target.name,previous_choices=str(backup))
    def log(): journal.write_text(json.dumps(record,indent=2),encoding='utf-8')
    log(); finished=False
    def fail(error):
        nonlocal finished
        if finished: return
        finished=True
        def report(problem):
            record.update(status='Reset needs attention',error=str(problem))
            try: log()
            except OSError: pass
            on_done(target,str(problem))
        if organizer.profilePath()==profile and active:
            if not mods.setActive(target.name,True):
                report(str(error)+' Previous output could not be re-enabled; its files and choices are retained.'); return
            def restored():
                try:
                    if organizer.profilePath()!=profile: raise ValueError('Profile changed before prior output could be restored.')
                    if plugins.state(workflow.OUTPUT)!=plugin_state: plugins.setState(workflow.OUTPUT,plugin_state)
                    if not mods.state(target.name)&mobase.ModState.ACTIVE or plugins.state(workflow.OUTPUT)!=plugin_state:
                        raise ValueError('The previous output activation could not be restored.')
                    report(error)
                except Exception as problem: report(str(error)+' '+str(problem))
            organizer.onNextRefresh(lambda:QTimer.singleShot(0,restored),False); organizer.refresh()
        else: report(error)
    def verify():
        nonlocal finished
        if finished: return
        try:
            require_game_closed()
            if organizer.profilePath()!=profile: raise ValueError('Profile changed during appearance reset. Return to the original profile to review the retained output.')
            if choices_path.read_bytes()!=original: raise ValueError('Saved appearance choices changed during reset.')
            if mods.state(target.name)&mobase.ModState.ACTIVE or plugins.loadOrder(workflow.OUTPUT)>=0:
                raise ValueError('MO2 still has the managed appearance output enabled.')
            for name in saved['hashes']:
                resolved=organizer.resolvePath(name)
                if resolved and Path(resolved).resolve().is_relative_to(target.resolve()):
                    raise ValueError('The managed appearance files are still effective: '+name)
            reset=dict(selected={},status='reset',profile_path=profile,previous_choices=str(backup),
                retained_hashes=saved['hashes'],build_record=str(journal))
            record.update(status='Appearance overrides reset; source mods retained'); log()
            temporary=choices_path.with_suffix('.tmp')
            temporary.write_text(json.dumps(reset,indent=2),encoding='utf-8')
            pending=workflow.path_for(organizer,'pending')
            pending_bytes=pending.read_bytes() if pending.exists() else None
            if pending_bytes and json.loads(pending_bytes).get('selected'):
                raise ValueError('The pending appearance selection changed during reset.')
            if pending_bytes is not None: pending.unlink()
            try: os.replace(temporary,choices_path)
            except OSError:
                if pending_bytes is not None: pending.write_bytes(pending_bytes)
                raise
            finished=True
            on_done(target,None)
        except Exception as error: fail(error)
    try:
        if not mods.setActive(target.name,False): raise ValueError('MO2 could not disable the managed appearance output.')
        organizer.onNextRefresh(lambda:QTimer.singleShot(0,verify),False); organizer.refresh()
    except Exception as error: fail(error)
