"""Turn existing evidence into a next step; never alter compatibility verdicts."""
from dataclasses import dataclass
import re


WORK = {'master-order', 'npc-stale', 'graphics-stale', 'graphics-pending',
        'graphics-withdrawn', 'record-checks-pending', 'body-build-pending'}
CHOICES = {'body-choices': ('bodyslide', 'Choose bodies and outfits'),
           'animation-choices': ('pandora', 'Choose animations')}
FIRST_RUN = {'native-compatibility-unverified', 'startup-outdated'}


def retain_run_findings(inspected, previous):
    # File inspection does not repeat helper execution or its choice/error result.
    # Retain these until preparation is retried; fresh inspection owns file staleness.
    run_only = ('loot-', 'animation-', 'body-build-', 'body-choices',
                'patching-failed', 'npc-failed', 'graphics-failed', 'workflow-incomplete')
    return tuple(inspected) + tuple(f for f in previous
        if f.code.startswith(run_only) and f not in inspected)


def display_name(name):
    # Strip only recognised download suffixes. Full identity stays in evidence.
    name = re.sub(r'\.(?:7z|zip|rar)$', '', name, flags=re.I)
    name = re.sub(r' \d+ [\w.\-]+ \d{4}-\d{2}-\d{2}T\d{2}-\d{2}Z [A-Za-z0-9]+(?: \(\d+\))?$', '', name)
    name = re.sub(r'-\d+-[\w.\-]+-\d{10}(?: \(\d+\))?$', '', name)
    return name


def finding_action(finding):
    code = finding.code
    if code=='character-shape-preparation-pending':return 'bodyslide','Finish body and character shapes'
    if code.startswith('character-shapes-'):
        return 'npc_appearances', 'Review saved character shapes'
    if code in WORK:
        return 'finish_setup', 'Prepare and recheck'
    if code in CHOICES:
        return CHOICES[code]
    if code.startswith('shape-support-'):
        return 'resolve_finding', 'Set up character body customization'
    if code.startswith('body-'):
        return 'bodyslide', 'Review body and outfit setup'
    if code.startswith('animation-'):
        return 'pandora', 'Review animation setup'
    if code.startswith('npc-') and finding.level != 'Info':
        return 'npc_appearances', 'Review character choices'
    if code.startswith('graphics-') and finding.level != 'Info':
        return 'graphics', 'Review graphics setup'
    if code.startswith('patching-') and finding.level != 'Info':
        return 'synthesis', 'Review patch choices'
    if code == 'inactive-master':
        return 'resolve_finding', 'Enable installed dependency'
    if code in {'missing-master', 'skse-loader-missing', 'native-runtime-incompatible', 'native-functional-incompatible',
                'native-dependency-missing', 'engine-fixes-preloader-missing'}:
        return 'resolve_finding', 'Resolve this requirement'
    if code in {'record-check-incomplete', 'workflow-incomplete'}:
        return 'helpers', 'Review tool setup'
    if code == 'record-errors':
        return 'resolve_finding', 'Review unresolved finding'
    return None, None


def finding_summary(finding):
    if finding.code == 'record-errors':
        count = re.search(r'(\d+) record errors', finding.detail)
        names = re.findall(r'^\[\d+:\d+\][ \t]+[^\r\n]*?"([^"\r\n]+)"[ \t]+\[[A-Z0-9_]{4}:[0-9A-Fa-f]{8}\][ \t]*$',
                           finding.detail, re.M)
        affected = ', '.join(dict.fromkeys(names))
        return ('xEdit reported ' + (count.group(1) if count else 'some') + ' record errors. '
                + ('Affected: ' + affected + '. ' if affected else '')
                + 'The gameplay consequence is unresolved. Automatic cleaning is not a repair for this finding.')
    if finding.code in WORK:
        return 'The current setup differs from the prepared result. Update it using your saved choices, then verify the effective output.'
    if finding.code in CHOICES:
        return 'New or changed options need your selection. Existing compatible choices will be reused.'
    # Keep the short first paragraph on the card; full evidence/action stays in Details.
    return (finding.explanation or finding.detail).split('\n\n')[0]


@dataclass(frozen=True)
class Guidance:
    title: str
    summary: str
    action: str
    action_label: str
    complete: bool
    groups: dict


def setup_guidance(findings, *, checked=False):
    groups = {key: [] for key in ('attention', 'choices', 'work', 'observations')}
    for finding in findings:
        if finding.code in CHOICES:
            group = 'choices'
        elif finding.code in WORK:
            group = 'work'
        elif finding.level == 'Info' or finding.code in FIRST_RUN or finding.code == 'asset-overlap':
            group = 'observations'
        else:
            group = 'attention'
        groups[group].append(finding)
    urgent = [f for f in groups['attention'] if f.level in {'Blocked', 'Unknown'}]
    if groups['work'] and not urgent and not groups['choices']:
        return Guidance('Continue preparing your setup',
            f"{len(groups['work'])} preparation tasks can run with saved choices. "
            + (f"{len(groups['attention'])} separate findings still need assessment." if groups['attention'] else 'ModLab will check the updated results.'),
            'finish_setup', 'Prepare and recheck', False, groups)
    if groups['attention']:
        title = 'Your setup needs attention'
        summary = 'Resolve the named requirements below. Available preparation and your saved choices remain accessible.'
        action, label = finding_action(groups['attention'][0])
        return Guidance(title, summary, action or 'details', label or 'Review next issue', False, groups)
    if groups['choices']:
        action, label = finding_action(groups['choices'][0])
        return Guidance('Choose how to continue', 'Make the remaining choices; ModLab will run the applicable preparation afterward.', action, label, False, groups)
    if groups['work']:
        return Guidance('Your setup needs preparation', 'ModLab can update the affected outputs and recheck the setup using your saved choices.',
                        'finish_setup', 'Prepare and recheck', False, groups)
    if checked:
        return Guidance('Preparation checks complete', 'The current preparation checks finished. File-overlap observations and first-run checks remain available below.',
                        'launch', 'Launch Skyrim', True, groups)
    return Guidance('Continue setting up your mods', 'Check the current installation and run applicable preparation. Installer and customization choices stay yours.',
                    'finish_setup', 'Check and prepare', False, groups)
