"""Strict canonical JSON for immutable MO2 containment evidence."""
from __future__ import annotations
import hashlib, json, re
from collections.abc import Mapping
from pathlib import PureWindowsPath
from typing import Any
from .mo2_containment_model import *

class ContainmentFormatError(ValueError): pass
_SHA = re.compile(r"^[0-9a-f]{64}$"); _RUN = re.compile(r"^containment-run:[0-9a-f]{32}$"); _RID = re.compile(r"^containment-result-sha256:[0-9a-f]{64}$")
_RESERVED = {"con","prn","aux","nul",*(f"com{i}" for i in range(1,10)),*(f"lpt{i}" for i in range(1,10))}
_RESULT = {"schemaVersion","runId","scenario","outcome","protectedBefore","protectedAfter","mo2Process","sourceIntegrity","stageIntegrity","watcherComplete","watcherEvents","projectionCount","projectionTargetsVerified","projectionPayloadBytesCopied","productionBackupNames","stagingNewNames","stagingOutputNames","adoptedName","adoptedTree","adoptedIntegrity","sourceRestoredAfterQuarantine","reasons"}
_JOURNAL = {"schemaVersion","runId","scenario","state","sourceRoot","stageRoot","archivePath","protectedModName","expectedNewModName","protectedBefore","monitorPid","mo2Pid","error"}
_DECISION = {"schemaVersion","runId","mechanism","verdict","scenarioResultIds","reasons"}
_TREE={"sha256","regularFileCount","directoryCount","totalSize"}; _PROTECTED={"sourceMods","labProfileSha256","playProfileSha256","downloads","overwrite","boundedGame"}; _PROCESS={"pid","executable","executableVersion","arguments","workingDirectory","integrity"}; _EVENT={"sequence","rootKind","action","relativePath"}
_MECHANISM="isolated-low-integrity-junction-projection-v1"
_ADOPTION={ContainmentScenario.NEW_FOLDER:("ModLab Spike New",("ModLab Spike New",),("meshes/new-folder.bin",)),ContainmentScenario.FOMOD_DEPENDENCY:("ModLab Spike FOMOD",("ModLab Spike FOMOD",),("always.txt","dependency-seen.txt"))}

def scenario_journal_to_bytes(v: ScenarioJournal)->bytes: return _bytes(scenario_journal_to_dict(v))
def scenario_journal_from_bytes(v: bytes)->ScenarioJournal: return scenario_journal_from_dict(_decode(v,"scenario journal"))
def scenario_result_to_bytes(v: ScenarioResult)->bytes: return _bytes(scenario_result_to_dict(v))
def scenario_result_from_bytes(v: bytes)->ScenarioResult: return scenario_result_from_dict(_decode(v,"scenario result"))
def capability_decision_to_bytes(v: CapabilityDecision)->bytes: return _bytes(capability_decision_to_dict(v))
def capability_decision_from_bytes(v: bytes)->CapabilityDecision: return capability_decision_from_dict(_decode(v,"capability decision"))
def scenario_result_id_for(v: ScenarioResult)->str: return "containment-result-sha256:"+hashlib.sha256(scenario_result_to_bytes(v)).hexdigest()
def capability_decision_id_for(v: CapabilityDecision)->str: return "containment-decision-sha256:"+hashlib.sha256(capability_decision_to_bytes(v)).hexdigest()

def scenario_journal_to_dict(v): return _journal_dict(scenario_journal_from_dict(_journal_dict(v)))
def scenario_journal_from_dict(v):
 d=_map(v,_JOURNAL,"scenario journal"); state=_enum(ScenarioState,d["state"],"state"); err=_opt_text(d["error"],"error")
 if state is ScenarioState.RECOVERY_REQUIRED and err is None: raise ContainmentFormatError("RecoveryRequired journal requires error")
 if state is not ScenarioState.RECOVERY_REQUIRED and err is not None: raise ContainmentFormatError(f"{state.value} journal cannot record error")
 mp=_opt_pos(d["monitorPid"],"monitorPid"); pid=_opt_pos(d["mo2Pid"],"mo2Pid")
 if state in {ScenarioState.LAUNCHED,ScenarioState.CAPTURED} and pid is None: raise ContainmentFormatError(f"{state.value} journal requires mo2Pid")
 return ScenarioJournal(_schema(d["schemaVersion"]),_run(d["runId"]),_enum(ContainmentScenario,d["scenario"],"scenario"),state,_abs(d["sourceRoot"],"sourceRoot"),_abs(d["stageRoot"],"stageRoot"),_abs(d["archivePath"],"archivePath"),_rel(d["protectedModName"],"protectedModName"),_rel(d["expectedNewModName"],"expectedNewModName"),_protected(d["protectedBefore"],"protectedBefore"),mp,pid,err)
def scenario_result_to_dict(v): return _result_dict(scenario_result_from_dict(_result_dict(v)))
def scenario_result_from_dict(v):
 d=_map(v,_RESULT,"scenario result")
 r=ScenarioResult(_schema(d["schemaVersion"]),_run(d["runId"]),_enum(ContainmentScenario,d["scenario"],"scenario"),_enum(ScenarioOutcome,d["outcome"],"outcome"),_protected(d["protectedBefore"],"protectedBefore"),_protected(d["protectedAfter"],"protectedAfter"),None if d["mo2Process"] is None else _process(d["mo2Process"],"mo2Process"),_enum(IntegrityObservation,d["sourceIntegrity"],"sourceIntegrity"),_enum(IntegrityObservation,d["stageIntegrity"],"stageIntegrity"),_bool(d["watcherComplete"],"watcherComplete"),_events(d["watcherEvents"]),_nn(d["projectionCount"],"projectionCount"),_bool(d["projectionTargetsVerified"],"projectionTargetsVerified"),_nn(d["projectionPayloadBytesCopied"],"projectionPayloadBytesCopied"),_sorted_rel(d["productionBackupNames"],"productionBackupNames"),_sorted_rel(d["stagingNewNames"],"stagingNewNames"),_sorted_rel(d["stagingOutputNames"],"stagingOutputNames"),None if d["adoptedName"] is None else _rel(d["adoptedName"],"adoptedName"),None if d["adoptedTree"] is None else _tree(d["adoptedTree"],"adoptedTree"),None if d["adoptedIntegrity"] is None else _enum(IntegrityObservation,d["adoptedIntegrity"],"adoptedIntegrity"),_bool(d["sourceRestoredAfterQuarantine"],"sourceRestoredAfterQuarantine"),_sorted_text(d["reasons"],"reasons"))
 _check_result(r); return r
def capability_decision_to_dict(v): return _decision_dict(capability_decision_from_dict(_decision_dict(v)))
def capability_decision_from_dict(v):
 d=_map(v,_DECISION,"capability decision"); ids=_ids(d["scenarioResultIds"]); result=CapabilityDecision(_schema(d["schemaVersion"]),_run(d["runId"]),_fixed(d["mechanism"],_MECHANISM,"mechanism"),_enum(CapabilityVerdict,d["verdict"],"verdict"),ids,_sorted_text(d["reasons"],"reasons"))
 if result.verdict is CapabilityVerdict.SUPPORTED:
  if len(ids)!=4: raise ContainmentFormatError("Supported decision requires four passing scenarios")
  if result.reasons: raise ContainmentFormatError("Supported decision cannot record reasons")
 elif not result.reasons: raise ContainmentFormatError(f"{result.verdict.value} decision requires reasons")
 return result

def _check_result(r):
 adopt=(r.adopted_name,r.adopted_tree,r.adopted_integrity)
 if any(x is None for x in adopt) and any(x is not None for x in adopt): raise ContainmentFormatError("adoption evidence must be wholly null or present")
 if r.outcome is ScenarioOutcome.PASSED:
  if r.protected_before!=r.protected_after: raise ContainmentFormatError("Passed result requires protectedAfter identical to protectedBefore")
  if not r.watcher_complete: raise ContainmentFormatError("Passed result requires complete watcher")
  if r.watcher_events: raise ContainmentFormatError("Passed result cannot record watcher events")
  if r.mo2_process is None: raise ContainmentFormatError("Passed result requires MO2 process evidence")
  if r.mo2_process.executable_version!="2.5.2.0": raise ContainmentFormatError("Passed result requires MO2 version 2.5.2.0")
  if r.mo2_process.integrity is not IntegrityObservation.LOW: raise ContainmentFormatError("Passed result requires Low MO2 process")
  if r.stage_integrity is not IntegrityObservation.LOW: raise ContainmentFormatError("Passed result requires Low stage integrity")
  if r.source_integrity not in {IntegrityObservation.MEDIUM,IntegrityObservation.HIGH,IntegrityObservation.SYSTEM}: raise ContainmentFormatError("Passed result requires Medium-or-higher source integrity")
  if r.projection_count<=0 or not r.projection_targets_verified: raise ContainmentFormatError("Passed result requires verified projections")
  if r.projection_payload_bytes_copied: raise ContainmentFormatError("Passed result requires zero copied projection payload bytes")
  if r.production_backup_names: raise ContainmentFormatError("Passed result cannot record production backups")
  if not r.source_restored_after_quarantine: raise ContainmentFormatError("Passed result requires restored source root")
  if r.reasons: raise ContainmentFormatError("Passed result cannot record reasons")
  if r.scenario in _ADOPTION:
   n,new,out=_ADOPTION[r.scenario]
   if (r.adopted_name,r.staging_new_names,r.staging_output_names)!=(n,new,out): raise ContainmentFormatError(f"Passed {r.scenario.value} result requires exact adopted staging outputs")
   if r.adopted_tree is None or r.adopted_integrity is not IntegrityObservation.MEDIUM: raise ContainmentFormatError(f"Passed {r.scenario.value} result requires Medium adopted tree evidence")
  elif any(x is not None for x in adopt) or r.staging_new_names or r.staging_output_names: raise ContainmentFormatError(f"Passed {r.scenario.value} result cannot record adopted output")
 elif r.outcome is ScenarioOutcome.INCOMPLETE and not r.reasons: raise ContainmentFormatError("Incomplete result requires reasons")

def _journal_dict(v): return {"schemaVersion":v.schema_version,"runId":v.run_id,"scenario":v.scenario.value,"state":v.state.value,"sourceRoot":v.source_root,"stageRoot":v.stage_root,"archivePath":v.archive_path,"protectedModName":v.protected_mod_name,"expectedNewModName":v.expected_new_mod_name,"protectedBefore":_protected_dict(v.protected_before),"monitorPid":v.monitor_pid,"mo2Pid":v.mo2_pid,"error":v.error}
def _result_dict(v): return {"schemaVersion":v.schema_version,"runId":v.run_id,"scenario":v.scenario.value,"outcome":v.outcome.value,"protectedBefore":_protected_dict(v.protected_before),"protectedAfter":_protected_dict(v.protected_after),"mo2Process":None if v.mo2_process is None else _process_dict(v.mo2_process),"sourceIntegrity":v.source_integrity.value,"stageIntegrity":v.stage_integrity.value,"watcherComplete":v.watcher_complete,"watcherEvents":[_event_dict(x) for x in v.watcher_events],"projectionCount":v.projection_count,"projectionTargetsVerified":v.projection_targets_verified,"projectionPayloadBytesCopied":v.projection_payload_bytes_copied,"productionBackupNames":list(v.production_backup_names),"stagingNewNames":list(v.staging_new_names),"stagingOutputNames":list(v.staging_output_names),"adoptedName":v.adopted_name,"adoptedTree":None if v.adopted_tree is None else _tree_dict(v.adopted_tree),"adoptedIntegrity":None if v.adopted_integrity is None else v.adopted_integrity.value,"sourceRestoredAfterQuarantine":v.source_restored_after_quarantine,"reasons":list(v.reasons)}
def _decision_dict(v): return {"schemaVersion":v.schema_version,"runId":v.run_id,"mechanism":v.mechanism,"verdict":v.verdict.value,"scenarioResultIds":list(v.scenario_result_ids),"reasons":list(v.reasons)}
def _tree_dict(v): return {"sha256":v.sha256,"regularFileCount":v.regular_file_count,"directoryCount":v.directory_count,"totalSize":v.total_size}
def _tree(v,label):
 d=_map(v,_TREE,label); return TreeIdentity(_sha(d["sha256"],label+".sha256"),_nn(d["regularFileCount"],label+".regularFileCount"),_nn(d["directoryCount"],label+".directoryCount"),_nn(d["totalSize"],label+".totalSize"))
def _protected_dict(v): return {"sourceMods":_tree_dict(v.source_mods),"labProfileSha256":v.lab_profile_sha256,"playProfileSha256":v.play_profile_sha256,"downloads":_tree_dict(v.downloads),"overwrite":_tree_dict(v.overwrite),"boundedGame":_tree_dict(v.bounded_game)}
def _protected(v,label):
 d=_map(v,_PROTECTED,label); return ProtectedState(_tree(d["sourceMods"],label+".sourceMods"),_sha(d["labProfileSha256"],label+".labProfileSha256"),_sha(d["playProfileSha256"],label+".playProfileSha256"),_tree(d["downloads"],label+".downloads"),_tree(d["overwrite"],label+".overwrite"),_tree(d["boundedGame"],label+".boundedGame"))
def _process_dict(v): return {"pid":v.pid,"executable":v.executable,"executableVersion":v.executable_version,"arguments":list(v.arguments),"workingDirectory":v.working_directory,"integrity":v.integrity.value}
def _process(v,label):
 d=_map(v,_PROCESS,label); return ProcessEvidence(_pos(d["pid"],label+".pid"),_abs(d["executable"],label+".executable"),_text(d["executableVersion"],label+".executableVersion"),_texts(d["arguments"],label+".arguments"),_abs(d["workingDirectory"],label+".workingDirectory"),_enum(IntegrityObservation,d["integrity"],label+".integrity"))
def _event_dict(v): return {"sequence":v.sequence,"rootKind":v.root_kind,"action":v.action,"relativePath":v.relative_path}
def _events(v):
 if not isinstance(v,list): raise ContainmentFormatError("watcherEvents must be an array")
 a=[]
 for i,x in enumerate(v):
  d=_map(x,_EVENT,f"watcherEvents[{i}]"); a.append(WatcherEvent(_nn(d["sequence"],f"watcherEvents[{i}].sequence"),_token(d["rootKind"],f"watcherEvents[{i}].rootKind"),_token(d["action"],f"watcherEvents[{i}].action"),_rel(d["relativePath"],f"watcherEvents[{i}].relativePath")))
 seq=tuple(x.sequence for x in a)
 if len(seq)!=len(set(seq)): raise ContainmentFormatError("duplicate watcher sequence")
 if seq!=tuple(sorted(seq)): raise ContainmentFormatError("watcher events must be in sequence order")
 return tuple(a)
def _map(v,fields,label):
 if not isinstance(v,Mapping): raise ContainmentFormatError(f"{label} must be an object")
 actual=set(v)
 if actual!=fields: raise ContainmentFormatError(f"{label} fields differ: missing={sorted(fields-actual)}, extra={sorted(actual-fields)}")
 return v
def _decode(v,label):
 if not isinstance(v,bytes): raise ContainmentFormatError(f"{label} must be UTF-8 bytes")
 try: return json.loads(v.decode("utf-8"),object_pairs_hook=_unique,parse_constant=lambda x: (_ for _ in ()).throw(ContainmentFormatError("invalid JSON constant")))
 except (UnicodeDecodeError,json.JSONDecodeError) as e: raise ContainmentFormatError(f"{label} must be valid JSON") from e
def _unique(pairs):
 d={}
 for k,v in pairs:
  if k in d: raise ContainmentFormatError(f"duplicate JSON key: {k}")
  d[k]=v
 return d
def _bytes(v): return (json.dumps(v,sort_keys=True,separators=(",",":"),ensure_ascii=False)+"\n").encode()
def _schema(v):
 if type(v) is not int or v!=1: raise ContainmentFormatError("schemaVersion must be integer 1")
 return v
def _bool(v,label):
 if type(v) is not bool: raise ContainmentFormatError(f"{label} must be boolean")
 return v
def _nn(v,label):
 if type(v) is not int or v<0: raise ContainmentFormatError(f"{label} must be a non-negative integer")
 return v
def _pos(v,label):
 if type(v) is not int or v<=0: raise ContainmentFormatError(f"{label} must be a positive integer")
 return v
def _opt_pos(v,label): return None if v is None else _pos(v,label)
def _sha(v,label):
 if not isinstance(v,str) or _SHA.fullmatch(v) is None: raise ContainmentFormatError(f"{label} must be a lowercase SHA-256")
 return v
def _run(v):
 if not isinstance(v,str) or _RUN.fullmatch(v) is None: raise ContainmentFormatError("runId is malformed")
 return v
def _enum(cls,v,label):
 if not isinstance(v,str): raise ContainmentFormatError(f"{label} must be text")
 try: return cls(v)
 except ValueError as e: raise ContainmentFormatError(f"{label} has an unknown value") from e
def _fixed(v,x,label):
 if v!=x: raise ContainmentFormatError(f"{label} must equal {x}")
 return x
def _text(v,label):
 if not isinstance(v,str) or not v.strip() or any(ord(c)<32 for c in v): raise ContainmentFormatError(f"{label} must be non-blank text without controls")
 return v
def _opt_text(v,label): return None if v is None else _text(v,label)
def _token(v,label):
 x=_text(v,label)
 if any(c in x for c in '\\/:*?<>|"') or x.endswith(("."," ")) or x.split(".",1)[0].casefold() in _RESERVED: raise ContainmentFormatError(f"{label} is unsafe")
 return x
def _rel(v,label):
 x=_text(v,label)
 if "\\" in x or x.startswith("/") or re.match(r"^[A-Za-z]:",x): raise ContainmentFormatError(f"{label} must be a safe relative path")
 for p in x.split("/"):
  if not p or p in {".",".."} or any(c in p for c in ':*?<>|"') or p.endswith(("."," ")) or p.split(".",1)[0].casefold() in _RESERVED: raise ContainmentFormatError(f"{label} contains an unsafe path segment")
 return x
def _abs(v,label):
 x=_text(v,label)
 if "/" in x or x.startswith("\\\\") or re.match(r"^[A-Za-z]:\\",x) is None: raise ContainmentFormatError(f"{label} must be a canonical absolute Windows path")
 p=PureWindowsPath(x)
 if not p.is_absolute() or any(q in {".",".."} for q in p.parts) or str(p)!=x: raise ContainmentFormatError(f"{label} must be a canonical absolute Windows path")
 for q in p.parts[1:]:
  if any(c in q for c in ':*?<>|"') or q.endswith(("."," ")) or q.split(".",1)[0].casefold() in _RESERVED: raise ContainmentFormatError(f"{label} contains an unsafe path segment")
 return x
def _texts(v,label):
 if not isinstance(v,list): raise ContainmentFormatError(f"{label} must be an array")
 return tuple(_text(x,f"{label}[{i}]") for i,x in enumerate(v))
def _sorted_rel(v,label): return _sorted(v,label,_rel)
def _sorted_text(v,label): return _sorted(v,label,_text)
def _sorted(v,label,fn):
 if not isinstance(v,list): raise ContainmentFormatError(f"{label} must be an array")
 a=tuple(fn(x,f"{label}[{i}]") for i,x in enumerate(v))
 if len({x.casefold() for x in a})!=len(a): raise ContainmentFormatError(f"{label} must not contain case-insensitive duplicates")
 if a!=tuple(sorted(a,key=lambda x:(x.casefold(),x))): raise ContainmentFormatError(f"{label} must use canonical case-insensitive order")
 return a
def _ids(v):
 if not isinstance(v,list): raise ContainmentFormatError("scenarioResultIds must be an array")
 a=tuple(x if isinstance(x,str) and _RID.fullmatch(x) else (_ for _ in ()).throw(ContainmentFormatError("scenarioResultIds contains malformed id")) for x in v)
 if len(a)!=len(set(a)): raise ContainmentFormatError("scenarioResultIds must not contain duplicates")
 return a
