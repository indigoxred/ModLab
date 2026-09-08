"""Keep archive queue progress across cancelled installers and app restarts."""
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from uuid import uuid4


@dataclass
class InstallQueue:
    path: Path
    data: dict

    def save(self):
        temporary = self.path.with_suffix('.tmp')
        temporary.write_text(json.dumps(self.data, indent=2), encoding='utf-8')
        temporary.replace(self.path)


def archive_stamp(path):
    path = Path(path)
    if path.suffix.casefold() not in {'.zip', '.7z', '.rar'} or not path.is_file():
        raise ValueError('The queued archive is missing or unsupported: ' + str(path))
    stat = path.stat()
    return [stat.st_size, stat.st_mtime_ns]


def create_queue(instance, profile_path, archives):
    items, seen = [], set()
    for value in archives:
        path = Path(value).resolve(strict=True)
        key = str(path).casefold()
        if key in seen:
            continue
        seen.add(key)
        items.append(dict(archive=str(path), stamp=archive_stamp(path), state='Queued', record=None, detail=''))
    if not items:
        raise ValueError('Select at least one archive.')
    directory = Path(instance) / 'reports/install-queue' / uuid4().hex[:12]
    directory.mkdir(parents=True)
    queue = InstallQueue(directory / 'operation.json', dict(
        version=1, profile_path=str(profile_path), started_at=datetime.now(timezone.utc).isoformat(),
        status='Queued', items=items, current=None))
    queue.save()
    return queue


def load_queue(path, profile_path):
    path = Path(path)
    data = json.loads(path.read_text(encoding='utf-8'))
    if data.get('version') != 1 or not isinstance(data.get('items'), list) or not data['items']:
        raise ValueError('Invalid installation queue: ' + str(path))
    if data.get('profile_path') != str(profile_path):
        raise ValueError('This installation queue belongs to another profile.')
    for item in data['items']:
        if not isinstance(item.get('archive'), str) or item.get('state') not in {
                'Queued', 'Installing', 'Completed', 'Needs attention', 'Dismissed'}:
            raise ValueError('Invalid archive entry in installation queue: ' + str(path))
    return InstallQueue(path, data)


def latest_unfinished(instance, profile_path):
    paths = sorted((Path(instance) / 'reports/install-queue').glob('*/operation.json'),
                   key=lambda p: p.stat().st_mtime, reverse=True)
    for path in paths:
        # Other profiles are not resumable from this profile. Malformed records stay visible in History.
        data = json.loads(path.read_text(encoding='utf-8'))
        if data.get('profile_path') != str(profile_path):
            continue
        queue = load_queue(path, profile_path)
        if any(i['state'] not in {'Completed', 'Dismissed'} for i in queue.data['items']):
            return queue
    return None


def resumable_items(queue):
    # Never automatically retry an interrupted operation: it may already have installed files.
    return [i['archive'] for i in queue.data['items'] if i['state'] == 'Queued']


def latest_unprepared(instance, profile_path):
    """Reconnect a completed batch to preparation after reopening the window.

    This never resumes an installer, or treats a failed/unfinished archive as
    installed. Already recorded setup results remain in History.
    """
    paths=sorted((Path(instance)/'reports/install-queue').glob('*/operation.json'),
                 key=lambda p:p.stat().st_mtime,reverse=True)
    for path in paths:
        try:
            queue=load_queue(path,profile_path);data=queue.data
            if (data.get('status')=='Installations completed; setup check pending' and
                    not data.get('setup_record') and data.get('current') is None and
                    all(i['state'] in {'Completed','Dismissed'} for i in data['items']) and
                    any(i['state']=='Completed' and i.get('record') for i in data['items'])):
                return queue
        except (OSError,ValueError,TypeError,KeyError,AttributeError):
            continue
    return None


def begin_item(queue, archive, profile_path):
    if queue.data['profile_path'] != str(profile_path):
        raise ValueError('The selected profile changed. Resume this queue from its original profile.')
    index = next((n for n, item in enumerate(queue.data['items'])
                  if item['archive'].casefold() == str(archive).casefold()), None)
    if index is None or queue.data['items'][index]['state'] in {'Completed', 'Dismissed'}:
        raise ValueError('This archive is not an unfinished entry in the selected queue.')
    item = queue.data['items'][index]
    if archive_stamp(archive) != item['stamp']:
        raise ValueError('The queued archive changed. Select the updated download as a new installation: ' + str(archive))
    item.update(state='Installing', detail='Installer started; completion has not yet been confirmed.')
    queue.data.update(current=index, status='Installing', error='')
    queue.save()


def finish_item(queue, status, record=None, detail=''):
    index = queue.data.get('current')
    if index is None:
        raise ValueError('No current archive is recorded for this installer completion.')
    item = queue.data['items'][index]
    item.update(state='Completed' if status == 'Installed, enabled' else 'Needs attention',
                result=status, record=str(record) if record else None, detail=detail)
    remaining = any(i['state'] not in {'Completed', 'Dismissed'} for i in queue.data['items'])
    queue.data.update(current=None, status='Queue needs attention' if remaining else 'Installations completed; setup check pending',
                      updated_at=datetime.now(timezone.utc).isoformat())
    queue.save()


def pause_queue(queue, error):
    if queue.data.get('current') is not None:
        finish_item(queue, 'Needs attention', detail=str(error))
    queue.data.update(status='Queue paused', error=str(error))
    queue.save()


def record_setup(queue, profile_path, record, *, needs_attention):
    """Attach the final result, including callbacks that started a fresh setup pass."""
    if queue is None or queue.data['profile_path'] != str(profile_path):
        return
    queue.data['setup_record'] = str(record)
    queue.data['updated_at'] = datetime.now(timezone.utc).isoformat()
    if all(item['state'] in {'Completed', 'Dismissed'} for item in queue.data['items']):
        queue.data['status'] = ('Installations completed; setup needs attention' if needs_attention
                                else 'Installations completed; setup checks recorded')
    queue.save()


def dismiss_queue(queue):
    for item in queue.data['items']:
        if item['state'] != 'Completed':
            item['state'] = 'Dismissed'
    queue.data.update(current=None, status='Remaining requests dismissed; installed files preserved')
    queue.save()
