"""Read-only xEdit checks of winning records in the complete active plugin context."""
import hashlib
import json
from pathlib import Path
import re

from .assessment import Finding, GAME_MASTERS
from .xedit import dependency_order, digest

REVISION = 'winning-records-v1'


def active_order(plugins):
    active = sorted((p for p in plugins if p.load_order >= 0), key=lambda p:p.load_order)
    names = tuple(p.name for p in active)
    if len({n.casefold() for n in names}) != len(names) or len({p.load_order for p in active}) != len(active):
        raise ValueError('Duplicate plugin names or positions in the active order.')
    for p in active:
        required = dependency_order(plugins, p.name)
        if any(names.index(n) > names.index(p.name) for n in required):
            raise ValueError('A dependency is after its dependent in the active order: ' + p.name)
    return names


def targets(names):
    return tuple(n for n in names if n.casefold() not in GAME_MASTERS)


def signature(plugins, resolve, context):
    from .record_checks import file_hash
    inputs = [(n, str(resolve(n)), file_hash(resolve(n))) for n in active_order(plugins)]
    return hashlib.sha256(json.dumps([REVISION, digest(__file__), context, inputs],sort_keys=True).encode()).hexdigest()


def parse_report(text, job_id, names, selected):
    lines = text.lstrip('\ufeff').splitlines()
    if not lines or lines[0] != 'MODLAB-WINNERS-1\t' + job_id or lines[-1] != 'END\t' + job_id:
        raise ValueError('The winning-record check did not produce a complete report for this job.')
    loaded, result, errors = [], {}, []
    for line in lines[1:-1]:
        cols = line.split('\t')
        if len(cols) == 2 and cols[0] == 'L':
            loaded.append(cols[1])
        elif len(cols) == 5 and cols[0] == 'P':
            _,name,checked,superseded,count=cols
            if name in result or name not in selected or not all(re.fullmatch(r'\d+',n) for n in cols[2:]):
                raise ValueError('Invalid or repeated plugin result in winning-record report.')
            checked,superseded,count=map(int,(checked,superseded,count))
            if count > checked:
                raise ValueError('Record error count exceeds checked records.')
            result[name]=dict(checked=checked,superseded=superseded,errors=count,details='')
        elif len(cols) == 6 and cols[0] == 'E' and re.fullmatch(r'[0-9A-F]{8}',cols[2]):
            errors.append(cols)
        else:
            raise ValueError('Unrecognized or incomplete winning-record report row.')
    if tuple(loaded) != tuple(names) or set(result) != set(selected):
        raise ValueError('The checked plugins do not match the complete active load order and targets.')
    for row in errors:
        _,name,form,label,field,message=row
        if name not in result:
            raise ValueError('Error names an unchecked plugin.')
        result[name]['details'] += f'{label} [{form}]\n{field} → {message}\n'
    for name,entry in result.items():
        if len({r[2] for r in errors if r[1]==name}) != entry['errors']:
            raise ValueError('The detailed errors do not match the per-plugin count.')
    return result


def script(job, names):
    def quote(s): return "'" + str(s).replace("'","''") + "'"
    selected='\n'.join('  selected.Add('+quote(n)+');' for n in targets(names))
    return r'''unit ModLabWinningRecords;
var report, selected: TStringList;
    provider, identity, recordLabel: string;

function Safe(s: string): string;
begin
  s := StringReplace(s, #9, ' ', [rfReplaceAll]);
  s := StringReplace(s, #10, ' ', [rfReplaceAll]);
  Result := StringReplace(s, #13, ' ', [rfReplaceAll]);
end;

function Inspect(e: IInterface): Boolean;
var problem: string; i: Integer;
begin
  problem := Check(e);
  Result := problem <> '';
  if Result then report.Add('E'+#9+provider+#9+identity+#9+Safe(recordLabel)+#9+Safe(Path(e))+#9+Safe(problem));
  for i := 0 to ElementCount(e)-1 do
    if Inspect(ElementByIndex(e,i)) then Result := True;
end;

function Initialize: Integer;
var f, r: IInterface; i,j,checked,superseded,errors: Integer;
begin
  Result := 0;
  report := TStringList.Create;
  selected := TStringList.Create;
  report.Add('MODLAB-WINNERS-1'+#9+JOB_ID);
SELECTED
  for i := 0 to FileCount-1 do begin
    f := FileByIndex(i);
    provider := GetFileName(f);
    if SameText(provider, 'Skyrim.Hardcoded.keep') or SameText(provider, 'SkyrimSE.exe') then Continue;
    report.Add('L'+#9+provider);
    if selected.IndexOf(provider)<0 then Continue;
    AddMessage('Checking winning records: '+provider);
    checked := 0; superseded := 0; errors := 0;
    for j := 0 to RecordCount(f)-1 do begin
      r := RecordByIndex(f,j);
      if Signature(r)='TES4' then Continue;
      if not IsWinningOverride(r) then begin
        Inc(superseded); Continue;
      end;
      Inc(checked);
      identity := IntToHex(GetLoadOrderFormID(r),8);
      recordLabel := Name(r);
      if Inspect(r) then Inc(errors);
    end;
    report.Add('P'+#9+provider+#9+IntToStr(checked)+#9+IntToStr(superseded)+#9+IntToStr(errors));
  end;
end;

function Finalize: Integer;
begin
  Result := 0;
  report.Add('END'+#9+JOB_ID);
  report.SaveToFile(REPORT_PATH);
  report.Free; selected.Free;
  AddMessage('ModLab winning-record check complete: '+JOB_ID);
end;
end.
'''.replace('SELECTED',selected).replace('JOB_ID',quote(job.name)).replace('REPORT_PATH',quote(job/'winning-records.tsv'))


def findings(result, plugins, job):
    out=[]; origins={p.name:p.origin for p in plugins}
    for name,entry in result.items():
        if entry['errors']:
            out.append(Finding('Review','record-errors','Record problems reported: '+(origins.get(name) or name),
                f'{name} — {entry["errors"]} record errors\n'+entry['details']+'\nReport: '+str(job/'winning-records.tsv'),
                'Cleaning is not a repair for this finding. Review an author update or matching patch; install it here and '
                'ModLab will check the final records again. Advanced xEdit remains available for unresolved cases.',
                'xEdit found errors in records supplied by '+name+' that win in the final active load order. '
                'Later active overrides were included; superseded source records were excluded. This checks record structure '
                'and references, not every conflict or gameplay consequence.\n\n'+entry['details']))
    out.append(Finding('Info','record-checks-current',f'Current winning-record checks: {len(result)} plugins',
        '\n'.join(f'{name}: {r["checked"]} winning records checked; {r["superseded"]} superseded records excluded; '
                  f'{r["errors"]} records with errors' for name,r in result.items()),
        'Results are reused while the complete active plugin inputs and helper context are unchanged.',
        'xEdit checked the final records supplied by active mods and Creations in one read-only pass. '
        'Base masters were loaded as context. Any errors remain separate findings; this does not prove gameplay behavior.'))
    return tuple(out)


def retained(cache_path, expected, names):
    if not cache_path.is_file(): return None
    data=json.loads(cache_path.read_text(encoding='utf-8'))
    if data.get('signature')!=expected or data.get('error'):return None
    job=Path(data['job'])
    if any(not (job/n).is_file() or digest(job/n)!=data[key] for n,key in
           (('tool.log','log_hash'),('winning-records.tsv','report_hash'))):return None
    return parse_report((job/'winning-records.tsv').read_text(encoding='utf-8-sig'),job.name,names,targets(names)),job


def inspect_setup(plugins, resolve, context, cache_path):
    try:
        names=active_order(plugins)
        saved=retained(cache_path,signature(plugins,resolve,context),names)
        if saved:return findings(saved[0],plugins,saved[1])
        return (Finding('Unknown','record-checks-pending','Final record checks need preparation',
            'The complete active load order has not been checked with these inputs.',
            'Prepare and recheck. ModLab will run one read-only xEdit check including later patches.'),)
    except Exception as error:
        return (Finding('Unknown','record-check-incomplete','Final record check needs attention',str(error),
            'Resolve the named input or helper problem, then prepare and recheck.'),)


def check_setup(plugins, resolve, context, cache_path, run, status=lambda message:None):
    try:
        expected=signature(plugins,resolve,context); names=active_order(plugins)
        if retained(cache_path,expected,names):return inspect_setup(plugins,resolve,context,cache_path),()
        status('Checking the final winning records, including active patches…')
        job,result,_,_=run()
        if signature(plugins,resolve,context)!=expected:raise ValueError('The record inputs changed during checking.')
        parsed=parse_report((job/'winning-records.tsv').read_text(encoding='utf-8-sig'),job.name,names,targets(names))
        if parsed!=result['winning_records']:raise ValueError('The retained report changed after verification.')
        data=dict(signature=expected,job=str(job),log_hash=digest(job/'tool.log'),report_hash=digest(job/'winning-records.tsv'))
        cache_path.parent.mkdir(parents=True,exist_ok=True)
        temp=cache_path.with_suffix('.tmp');temp.write_text(json.dumps(data,indent=2),encoding='utf-8');temp.replace(cache_path)
        return findings(parsed,plugins,job),(f'xEdit checked final winning records in one pass: {job}',)
    except Exception as error:
        return (Finding('Unknown','record-check-incomplete','Final record check could not finish',str(error),
            'Resolve the reported problem, then prepare and recheck. No checked result was accepted.'),),()
