"""Finish an installation with automatic checks and applicable plugin sorting."""
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .assessment import Finding, assess
from .inspection import collect_setup
from .loot_workflow import apply_order, context_signature, run_loot
from .cleaning import prepare_cleaning, activate_output


@dataclass
class WorkflowResult:
    findings: tuple
    steps: tuple
    signature: tuple
    record: Path
    cleaned_output: Path | None = None


def finish_setup(organizer, app_root, version_reader, *, previous_signature=None,
                 previous_findings=(), installation_record=None):
    directory = Path(organizer.modsPath()).parent / 'reports' / 'setup' / uuid4().hex[:12]
    directory.mkdir(parents=True)
    record_path = directory / 'operation.json'
    record = {'started_at': datetime.now(timezone.utc).isoformat(),
              'installation_record': str(installation_record) if installation_record else None,
              'status': 'Checking'}
    steps = []
    findings = []
    signature = None
    cleaned_output = None
    try:
        setup = collect_setup(organizer, version_reader=version_reader)
        assessment = assess(setup)
        findings = list(assessment.findings)
        signature = context_signature(organizer)
        steps.append('Read the active profile, plugin dependencies and effective asset providers.')
        hard_blockers = [f for f in assessment.findings if f.level == 'Blocked' and f.code != 'master-order']
        if hard_blockers or setup.errors:
            findings = list(assessment.findings)
            steps.append('Stopped automatic tool work because the setup has missing requirements or incomplete inspection.')
        else:
            if previous_signature != signature:
                run = run_loot(organizer, app_root, version_reader)
                record['loot_run'] = str(run.directory)
                advice = run.findings
                steps.append('Ran LOOT and read its dependency, incompatibility and cleaning advice.')
                if any(f.level == 'Blocked' for f in advice):
                    steps.append('LOOT reported a blocker; no plugin order was applied.')
                elif run.proposal != run.previous:
                    previous = apply_order(organizer, run.proposal, run.signature)
                    record['previous_order'] = previous
                    record['applied_order'] = run.proposal
                    steps.append('Applied the checked plugin order and verified MO2 readback. Previous order retained.')
                else:
                    steps.append('The plugin order already matches LOOT’s checked proposal.')
                signature = context_signature(organizer)
                assessment = assess(collect_setup(organizer, version_reader=version_reader))
            else:
                advice = tuple(f for f in previous_findings if f.code.startswith('loot-'))
                steps.append('Plugin inputs are unchanged; retained the previous LOOT advice and checked current asset providers.')
            findings = list(assessment.findings) + list(advice)
            if any(f.code == 'loot-cleaning' for f in advice):
                if previous_signature != signature and not any(f.level == 'Blocked' for f in advice):
                    report = json.loads((run.directory / 'report.json').read_text(encoding='utf-8-sig'))
                    cleaned_output, cleaning_findings, cleaning_steps = prepare_cleaning(organizer, setup, report, version_reader)
                    findings = [f for f in findings if f.code != 'loot-cleaning'] + list(cleaning_findings)
                    steps.extend(cleaning_steps)
                else:
                    steps.append('Existing cleaning advice is retained; no new cleaning was performed in this cached or blocked check.')
        record.update(status='Needs attention' if any(f.level in {'Blocked', 'Unknown', 'Review'} for f in findings) else 'File checks completed; gameplay unverified')
    except Exception as error:
        findings.append(Finding('Unknown', 'workflow-incomplete', 'Automatic setup check could not finish', str(error),
                                'Resolve the reported tool or setup problem, then use Recheck and finish setup.'))
        record['status'] = 'Automatic check incomplete'
    finally:
        record.update(steps=steps, findings=[asdict(f) for f in findings],
                      finished_at=datetime.now(timezone.utc).isoformat(), gameplay_verified=False)
        record_path.write_text(json.dumps(record, indent=2), encoding='utf-8')
    return WorkflowResult(tuple(findings), tuple(steps), signature, record_path, cleaned_output)


def complete_cleaning(organizer, app_root, version_reader, result):
    """Called after MO2 refreshes the published mod, outside the prior helper event loop."""
    record = json.loads(result.record.read_text(encoding='utf-8'))
    steps = list(result.steps)
    try:
        if organizer.profilePath() != result.signature[0]:
            raise ValueError('The selected profile changed before cleaned-output activation.')
        applied = activate_output(organizer, result.cleaned_output)
        run = run_loot(organizer, app_root, version_reader)
        post_report = json.loads((run.directory / 'report.json').read_text(encoding='utf-8-sig'))
        cleaned_names = {path.name.casefold() for path in result.cleaned_output.iterdir() if path.suffix.casefold() in {'.esp', '.esm', '.esl'}}
        if any(plugin['name'].casefold() in cleaned_names and plugin.get('dirty') for plugin in post_report.get('plugins', [])):
            raise ValueError('LOOT still recommends cleaning one of the generated copies. The result needs review and was not accepted.')
        if not any(f.level == 'Blocked' for f in run.findings) and run.proposal != run.previous:
            record['post_clean_previous_order'] = apply_order(organizer, run.proposal, run.signature)
        current = assess(collect_setup(organizer, version_reader=version_reader))
        decisions = tuple(f for f in result.findings if f.code.startswith('cleaning-'))
        handled = {f.title.split(':', 1)[0].casefold() for f in decisions}
        advice = tuple(f for f in run.findings if not (
            f.code == 'loot-cleaning' and f.title.removeprefix('Cleaning advice: ').casefold() in handled))
        result.findings = tuple(f for f in current.findings if f.code != 'cleaning-current') + decisions + (applied,) + advice
        result.signature = context_signature(organizer)
        steps.append('Enabled the checked cleaned copies, verified their effective hashes, and reran LOOT on the resulting setup.')
        record.update(post_clean_loot_run=str(run.directory), cleaned_output=str(result.cleaned_output),
                      status='Needs attention' if any(f.level in {'Blocked', 'Unknown', 'Review'} for f in result.findings)
                      else 'File checks completed; gameplay unverified')
    except Exception as error:
        organizer.modList().setActive(result.cleaned_output.name, False)
        result.findings += (Finding('Unknown', 'workflow-incomplete', 'Cleaned output was not accepted', str(error),
                                   'Recheck after resolving the reported problem. The generated output is disabled; original sources remain installed.'),)
        record['status'] = 'Cleaning activation needs attention'
    result.steps = tuple(steps)
    record.update(steps=steps, findings=[asdict(f) for f in result.findings], finished_at=datetime.now(timezone.utc).isoformat())
    result.record.write_text(json.dumps(record, indent=2), encoding='utf-8')
    return result
