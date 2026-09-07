"""Combine current plugin bytes, LOOT advice and explicitly reviewed cleaning rules."""
from dataclasses import dataclass
from pathlib import Path
import re
import zlib


@dataclass(frozen=True)
class CleaningDecision:
    plugin: str
    action: str
    reason: str
    source: str = ''
    crc: int | None = None


# Reviewed against the installed 2026-09-06 files and current LOOT metadata.
# These are tool-guided Creation Club maintenance rules, not claims of author approval.
# Other plugin versions require their own applicable evidence; filenames alone never authorize cleaning.
RULES = tuple({'plugin': name, 'crc': crc, 'decision': 'clean',
               'source': 'https://tes5edit.github.io/docs/7-mod-cleaning-and-error-checking.html#ThreeEasyStepstocleanMods',
               'reason': 'Current LOOT metadata recommends xEdit Quick Auto Clean for this exact Creation Club plugin version; '
                         'the xEdit guide documents that procedure. This is maintenance, not proof of a gameplay defect.'}
              for name, crc in [('ccBGSSSE001-Fish.esm', 0x70e3300e),
                                ('ccQDRSSE001-SurvivalMode.esl', 0x25bc177e),
                                ('ccBGSSSE025-AdvDSGS.esm', 0xebbc9da9)])


def crc32(path):
    checksum = 0
    with Path(path).open('rb') as stream:
        for data in iter(lambda: stream.read(1024 * 1024), b''):
            checksum = zlib.crc32(data, checksum)
    return checksum


def cleaning_decisions(report, active_plugins, resolve_path, rules=RULES):
    active = {name.casefold() for name in active_plugins}
    result = []
    for plugin in report.get('plugins', []):
        name = plugin['name']
        dirty = plugin.get('dirty', [])
        if name.casefold() not in active or not dirty:
            continue
        if Path(name).name != name or '/' in name or '\\' in name or ':' in name:
            raise ValueError('Cleaning advice contains an invalid plugin filename.')
        checksum = crc32(resolve_path(name))
        applicable = [rule for rule in rules if rule['plugin'].casefold() == name.casefold() and
                      rule.get('crc') in (None, checksum)]
        messages = '\n'.join(message.get('text', '') for message in plugin.get('messages', []))
        messages += '\n' + '\n'.join(entry.get('info', '') for entry in dirty)
        denied = next((rule for rule in applicable if rule['decision'] == 'skip'), None)
        if name.casefold() == 'skyrim.esm' or denied or re.search(r"\b(?:do not|don['’]t|never)\s+(?:auto[- ]?)?clean\b", messages, re.I):
            result.append(CleaningDecision(name, 'skip', denied['reason'] if denied else
                                          'Cleaning is excluded by the applicable instructions or base-master rule.',
                                          denied.get('source', '') if denied else '', checksum))
            continue
        matching = [entry for entry in dirty if type(entry.get('crc')) is int and entry['crc'] == checksum]
        rule = next((item for item in applicable if item['decision'] == 'clean' and item.get('crc') == checksum
                     and item.get('source', '').startswith('https://') and item.get('reason')), None)
        reason = None
        if len(matching) != 1:
            reason = 'The cleaning metadata no longer matches this installed plugin. Refresh LOOT advice before proceeding.'
        elif matching[0].get('deletedNavmesh', 0):
            reason = 'Deleted navmeshes need a specific repair or author update; Quick Auto Clean is not an automatic remedy for them.'
        elif any(message.get('type') in {'warn', 'error'} for message in plugin.get('messages', [])):
            reason = 'Other LOOT warnings must be resolved before this cleaning recommendation can be applied automatically.'
        elif not re.search(r'(?:SSEEdit|xEdit).*4\.1\.5', matching[0].get('cleaningUtility', ''), re.I):
            reason = 'The recommended cleaning-tool version is outside the currently checked adapter.'
        elif not rule:
            reason = 'ModLab has no reviewed cleaning procedure for this plugin version. The recommendation is retained, but the file is unchanged.'
        result.append(CleaningDecision(name, 'review' if reason else 'clean', reason or rule['reason'],
                                      rule.get('source', '') if rule else '', checksum))
    return tuple(result)
