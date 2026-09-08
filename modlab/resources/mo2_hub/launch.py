"""Launch the selected profile through MO2, retaining the chosen game and launcher."""
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from uuid import uuid4

from .assessment import assess
from .inspection import collect_setup
from .loot_workflow import context_signature
from .foundations import skse_files_error


@dataclass(frozen=True)
class Launcher:
    executable: Path
    label: str


def choose_launcher(setup, resolve_path):
    root = Path(setup.game_root)
    if setup.game_name != 'Skyrim Special Edition' or not (root / 'SkyrimSE.exe').is_file():
        raise ValueError('The selected Skyrim installation could not be identified. Check Setup / tool locations.')
    loader = root / 'skse64_loader.exe'
    if not loader.is_file():
        return Launcher(root / 'SkyrimSE.exe', 'Skyrim through MO2')
    problem = skse_files_error(setup, resolve_path)
    if problem:
        raise ValueError(problem + ' Install/enable the matching official SKSE package through ModLab: https://skse.silverlock.org/.')
    return Launcher(loader, 'SKSE through MO2')


def launch_game(organizer, version_reader):
    from .skse import require_game_closed
    require_game_closed()
    signature = context_signature(organizer)
    setup = collect_setup(organizer, version_reader=version_reader)
    findings = assess(setup).findings
    blockers = [f for f in findings if f.level == 'Blocked' or f.code in {
        'inspection-incomplete', 'native-inspection-incomplete', 'native-runtime-unknown', 'skse-files-incomplete',
        'graphics-pending', 'graphics-withdrawn', 'graphics-stale', 'graphics-inspection-incomplete', 'npc-stale',
        'character-shapes-stale'}]
    if blockers:
        raise ValueError('\n\n'.join(f.title + '\n' + (f.explanation or f.detail) +
                                    '\nNext action: ' + f.action for f in blockers))
    from .shape_preparation import pending_finding
    pending=pending_finding(organizer.profilePath())
    if pending:raise ValueError(pending.title+'. '+pending.action)
    launcher = choose_launcher(setup, organizer.resolvePath)
    if signature != context_signature(organizer):
        raise ValueError('The selected setup changed before launch. Recheck and launch again.')
    directory = Path(organizer.modsPath()).parent / 'reports' / 'launch' / uuid4().hex[:12]
    directory.mkdir(parents=True)
    record = dict(profile=setup.profile, profile_path=organizer.profilePath(), game_root=setup.game_root,
                  runtime=setup.runtime, launcher=str(launcher.executable),
                  started_at=datetime.now(timezone.utc).isoformat(), status='Launch requested',
                  findings=[asdict(f) for f in findings], gameplay_verified=False)
    try:
        if launcher.executable.name.casefold() == 'skse64_loader.exe':
            from .startup import prepare_request
            record['startup_request'] = prepare_request(organizer, setup)
            if signature != context_signature(organizer):
                raise ValueError('The profile changed during startup preparation. Recheck before launching.')
        (directory / 'operation.json').write_text(json.dumps(record, indent=2), encoding='utf-8')
        handle = organizer.startApplication(str(launcher.executable), [], setup.game_root, setup.profile)
        if not handle:
            raise RuntimeError('MO2 could not start the selected launcher.')
        record.update(status='Launcher started; gameplay unverified')
        return launcher, directory / 'operation.json'
    except Exception as error:
        record.update(status='Launch failed', error=str(error))
        raise
    finally:
        (directory / 'operation.json').write_text(json.dumps(record, indent=2), encoding='utf-8')
