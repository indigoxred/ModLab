"""Recognize new mesh inputs from an unfinished verified installation batch.

This explains replacement of old generated meshes, not compatibility or a
standing permission to reverse future manual provider choices.
"""
from datetime import datetime
import json
from pathlib import Path
import re

from .bodyslide import relative_path
from .installation import _plain
from .outputs import digest


def pending_mesh_inputs(organizer, names, prepared_at):
    if not prepared_at or not hasattr(organizer, 'getPluginDataPath'):
        return {}
    import mobase
    cutoff=datetime.fromisoformat(prepared_at)
    if cutoff.tzinfo is None:return {}
    mods_root=Path(organizer.modsPath()).resolve()
    history=(Path(organizer.getPluginDataPath())/'modlab/installations').resolve()
    profile=organizer.profilePath();mods=organizer.modList()
    wanted={name.replace('\\','/').casefold():name for name in names
            if name.replace('\\','/').casefold().startswith('meshes/') and name.casefold().endswith('.nif')}
    allowed={}
    for queue in (mods_root.parent/'reports/install-queue').glob('*/operation.json'):
        try:
            _plain(queue);_plain(queue.parent)
            data=json.loads(queue.read_text(encoding='utf-8'))
            if (data.get('version')!=1 or data.get('profile_path')!=profile or data.get('setup_record') or
                    data.get('status')!='Installations completed; setup check pending' or data.get('current') is not None or
                    datetime.fromisoformat(data['started_at'])<=cutoff):continue
            if not all(i['state'] in {'Completed','Dismissed'} for i in data['items']):continue
            for item in data['items']:
                if item['state']!='Completed' or not item.get('record'):continue
                receipt=Path(item['record'])
                _plain(receipt)
                if receipt.resolve().parent!=history:continue
                record=json.loads(receipt.read_text(encoding='utf-8'))
                if (record.get('status')!='Installed, enabled' or record.get('profile_path')!=profile or
                        Path(record.get('mods_root','')).resolve()!=mods_root or
                        Path(record.get('archive','')).resolve()!=Path(item['archive']).resolve()):continue
                verified=record.get('file_verification',{})
                if verified.get('version')!=1 or not re.fullmatch('[0-9a-f]{64}',verified.get('archive_sha256','')):continue
                target=Path(record['result']['path']);_plain(target)
                if target.resolve().parent!=mods_root or target.name!=record['result']['name']:continue
                if not mods.state(target.name)&mobase.ModState.ACTIVE:continue
                mod=mods.getMod(target.name)
                if mod is None or Path(mod.absolutePath()).resolve()!=target.resolve():continue
                files={relative_path(n).casefold():(n,v) for n,v in verified['files'].items()}
                if len(files)!=len(verified['files']):continue
                for key in wanted.keys()&files.keys():
                    relative,expected=files[key];source=target/relative
                    # The receipt must name the very file now winning this path.
                    for parent in (source,*source.parents):
                        _plain(parent)
                        if parent==target:break
                    effective=organizer.resolvePath(wanted[key])
                    if (source.resolve().is_relative_to(target.resolve()) and effective and
                            Path(effective).resolve()==source.resolve() and source.is_file() and
                            source.stat().st_size==expected['size'] and digest(source)==expected['sha256']):
                        allowed[wanted[key]]=str(receipt)
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            # Missing/old/malformed evidence cannot explain an outside override.
            continue
    return allowed
