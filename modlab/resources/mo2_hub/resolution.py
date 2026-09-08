"""Context for an existing finding and a checked, single-plugin dependency action."""
from dataclasses import dataclass
from pathlib import Path
import json
import re
import shutil
from uuid import uuid4

from . import skse


def web_links(text):
    return tuple(dict.fromkeys(url.rstrip('.,;:') for url in
        re.findall(r'https?://[^\s<>\[\]()"\x27]+', text)))


@dataclass(frozen=True)
class ResolutionContext:
    dependency: str = ''
    dependency_provider: str = ''
    providers: tuple = ()
    dependents: tuple = ()
    links: tuple = ()
    can_enable: bool = False


def resolution_context(finding, snapshot):
    plugins = snapshot.plugins if snapshot else ()
    dependency = ''
    prefixes = {'missing-master': 'Missing dependency: ',
                'inactive-master': 'Dependency is disabled: '}
    prefix = prefixes.get(finding.code)
    if prefix and finding.title.startswith(prefix):
        dependency = finding.title[len(prefix):]
    if finding.code == 'shape-support-dependency-disabled':
        dependency = finding.detail.split('\n', 1)[0]
    dependents = tuple(p for p in plugins if p.load_order >= 0 and dependency and
                       dependency.casefold() in {m.casefold() for m in p.masters})
    if finding.code.startswith('shape-support-'):
        dependents = tuple(p for p in plugins if p.name.casefold() == 'obody.esp' and p.load_order >= 0)
    installed = next((p for p in plugins if p.name.casefold() == dependency.casefold()), None)
    providers = [p.origin for p in dependents if p.origin]
    if finding.code == 'record-errors':
        providers.extend(p.origin for p in plugins if p.origin and
                         finding.detail.startswith(p.name + ' —'))
    if snapshot and finding.code.startswith(('native-', 'engine-fixes-')):
        first_line = finding.detail.split('\n', 1)[0].replace('\\', '/').casefold()
        asset = next((a for a in snapshot.assets if a.path.replace('\\', '/').casefold() == first_line), None)
        if asset and asset.origins:
            providers.append(asset.origins[0])
    return ResolutionContext(dependency, installed.origin if installed else '',
        tuple(dict.fromkeys(providers)), tuple(p.name for p in dependents),
        web_links(finding.action), bool(((finding.code == 'inactive-master' and dependents) or
              finding.code == 'shape-support-dependency-disabled') and installed and installed.load_order < 0))


def enable_dependency(organizer, dependency, provider, profile_path, active_state):
    """Enable only the plugin the user selected, keeping evidence and restoring on failure."""
    skse.require_game_closed()
    profile = Path(profile_path).resolve()
    if Path(organizer.profilePath()).resolve() != profile:
        raise ValueError('The selected profile changed. Recheck its requirements before enabling this dependency.')
    plugins = organizer.pluginList()
    if dependency not in plugins.pluginNames() or plugins.origin(dependency) != provider:
        raise ValueError('The dependency or its provider changed. Recheck before enabling it.')
    previous = plugins.state(dependency)
    if previous == active_state:
        raise ValueError('This dependency is already enabled. Recheck the current setup.')
    job = Path(organizer.modsPath()).parent / 'reports' / 'dependencies' / uuid4().hex[:12]
    job.mkdir(parents=True)
    record = job / 'operation.json'
    data = dict(profile=str(profile), plugin=dependency, provider=provider,
                previous_state=str(previous), requested_state=str(active_state), status='prepared')
    if (profile / 'plugins.txt').is_file():
        shutil.copy2(profile / 'plugins.txt', job / 'plugins.txt.before')
    def save():
        record.write_text(json.dumps(data, indent=2), encoding='utf-8')
    save()
    try:
        plugins.setState(dependency, active_state)
        if plugins.state(dependency) != active_state:
            raise RuntimeError('MO2 did not enable the dependency.')
        data['status'] = 'enabled'
        save()
    except Exception as error:
        try:
            plugins.setState(dependency, previous)
            if plugins.state(dependency) != previous:
                raise RuntimeError('MO2 did not restore the original state.')
        except Exception as restore_error:
            data.update(status='recovery-needed', error=str(error), recovery_error=str(restore_error))
            save()
            raise RuntimeError(f'Enabling {dependency} failed and its state could not be restored. '
                               f'Review it in MO2. Recovery record: {record}') from error
        data.update(status='restored', error=str(error))
        save()
        raise RuntimeError(f'Enabling {dependency} failed; its original state was restored. {error}') from error
    return record
