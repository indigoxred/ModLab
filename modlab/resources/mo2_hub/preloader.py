"""Install the exact official Engine Fixes v7 root preloader, not a Data mod.

Author file 725261 (checked 2026-09-09). Its archive contains the DLL and
Vortex placement metadata. No installer instructions from the archive execute.
"""
from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import subprocess
from uuid import uuid4

from . import skse
from .installation import InstallResult, write_record
from .outputs import digest

ARCHIVE_SHA='35633dc2904521f35861dbb582e084a10e77bec5b8f08fb08963d7578b07452f'
DLL_SHA='cf366987da6237559eb6e113ea717ec21762c9eec3a87d9dc4fa9ddfe7789c26'
NAME='d3dx9_42.dll'
URL='https://www.nexusmods.com/skyrimspecialedition/mods/17230?tab=files&file_id=725261'


def extract(archive, job):
    archive=Path(archive)
    if digest(archive)!=ARCHIVE_SHA:
        raise ValueError('This is not the checked Engine Fixes v7 preloader archive. Download its matching file: '+URL)
    retained=job/'original.7z';shutil.copy2(archive,retained)
    if digest(retained)!=ARCHIVE_SHA:raise ValueError('The preloader archive changed while retaining it.')
    source=job/'package';source.mkdir()
    result=subprocess.run([r'C:\Windows\System32\tar.exe','-xf',str(retained),'-C',str(source),NAME],
        capture_output=True,timeout=30,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    if result.returncode:raise ValueError('Could not extract the checked preloader: '+result.stderr.decode(errors='replace')[:1000])
    file=source/NAME
    if not file.is_file() or file.is_symlink() or digest(file)!=DLL_SHA:
        raise ValueError('The extracted preloader did not match its checked file.')
    return source


def install_package(organizer, archive, version_reader):
    skse.require_game_closed()
    profile=organizer.profilePath()
    game=Path(organizer.managedGame().gameDirectory().absolutePath()).resolve()
    runtime=version_reader(str(game/'SkyrimSE.exe')).replace(',','.').replace(' ','')
    if runtime not in {'1.6.1170','1.6.1170.0'}:
        raise ValueError('This preloader workflow is checked for Skyrim 1.6.1170. Keep the root files required by your selected Engine Fixes release.')
    game_hash=digest(game/'SkyrimSE.exe');target=game/NAME
    if target.is_symlink() or (target.exists() and (not target.is_file() or digest(target)!=DLL_SHA)):
        raise ValueError('A different game-folder preloader already exists. ModLab retained it; review its supplying mod before replacing it: '+str(target))
    job=Path(organizer.modsPath()).parent/'reports/preloader'/uuid4().hex[:12];job.mkdir(parents=True)
    path=job/'operation.json'
    record=dict(profile_path=profile,game_root=str(game),game_hash=game_hash,archive=str(archive),
        archive_sha256=ARCHIVE_SHA,target=str(target),status='Preparing root component',
        source_url=URL,created_at=datetime.now(timezone.utc).isoformat())
    write_record(path,record)
    try:
        source=extract(archive,job)
        skse.require_game_closed()
        if (organizer.profilePath()!=profile or
                Path(organizer.managedGame().gameDirectory().absolutePath()).resolve()!=game or
                digest(game/'SkyrimSE.exe')!=game_hash):
            raise ValueError('The selected profile or game changed during preloader preparation.')
        if target.exists():
            if target.is_symlink() or not target.is_file() or digest(target)!=DLL_SHA:
                raise ValueError('The game-folder preloader changed during preparation.')
            record['reused_existing']=True
        else:
            record['root']=skse.deploy_root_files(game,source,job,game_hash,[NAME],
                expected={NAME:dict(before=None,after=DLL_SHA)})
        if not target.is_file() or target.is_symlink() or digest(target)!=DLL_SHA:
            raise ValueError('The preloader did not become effective in the selected game folder.')
        result=InstallResult('Installed, enabled','Engine Fixes v7 preloader',str(game),1,
            'Required preloader verified beside SkyrimSE.exe. This game-folder component is shared by profiles using this game. Startup is checked separately.')
        record.update(status=result.status,result=asdict(result),finished_at=datetime.now(timezone.utc).isoformat())
        write_record(path,record)
        return result,path
    except Exception as error:
        record.update(status='Preloader installation needs attention',error=str(error))
        if record.get('root'):
            try:
                skse.restore_root(record['root'],job)
                record['status']='Preloader install failed; root change restored'
            except Exception as recovery_error:
                record['recovery_error']=str(recovery_error)
        # Root publication keeps its own crash-recovery journal and backups.
        # If final receipt storage fails, do not claim success or erase them.
        try:write_record(path,record)
        except OSError:pass
        raise


def restore(organizer, record_path):
    skse.require_game_closed()
    path=Path(record_path).resolve()
    path.relative_to((Path(organizer.modsPath()).parent/'reports/preloader').resolve())
    record=json.loads(path.read_text(encoding='utf-8'))
    game=Path(organizer.managedGame().gameDirectory().absolutePath()).resolve()
    if Path(record['game_root']).resolve()!=game or record.get('archive_sha256')!=ARCHIVE_SHA:
        raise ValueError('The selected game or preloader receipt changed.')
    if record.get('reused_existing'):
        raise ValueError('This operation reused an existing preloader and made no root-file change to undo.')
    journal=path.parent/'root-deployment.json'
    root=json.loads(journal.read_text(encoding='utf-8'))
    if (Path(root['game_root']).resolve()!=game or root['game_hash']!=record['game_hash'] or
            set(root['files'])!={NAME} or root['files'][NAME]['after']!=DLL_SHA):
        raise ValueError('The preloader recovery journal could not be verified.')
    skse.restore_root(root,path.parent)
    record.update(status='Root preloader installation restored',root=root)
    write_record(path,record)
