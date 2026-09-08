# Final winning-record checks

Automatic preparation now uses one read-only xEdit pass over winning records
from active mod/Creation plugins, with the complete active order loaded. The
previous prefix-only checks could continue warning about a source record after
a later patch replaced it. Advanced single-plugin checks and cleaning keep
their existing separate paths and retained reports.

The report must identify the exact loaded order, every target plugin, checked
and superseded counts, per-record errors and a job-specific completion marker.
Source and staged plugin hashes must remain unchanged. All active plugin bytes,
helper/profile context, archive stamps and loose text-table hashes participate
in cache invalidation. A missing/changed report or log cannot support reuse.

xEdit 4.1.5f ignores autoload/autoexit in script mode. A process-handle-scoped
adapter handles only its exact module-selection dialog and final script window,
following the existing PGPatcher integration pattern. Selection is acknowledged
by observed departure, not merely a queued click; repeated ignored clicks cancel
the private check after a bounded number of attempts. A terminal report permits
closing the private helper; its contents still require full validation afterward.
Other dialogs are not accepted. This adapter is specific to the verified
English 4.1.5f x64 windows; other UI versions/locales are not established here.

## Native evidence

ModLab preparation launched job `builds/xedit/71c1a751719f` in the disposable
`ModLab 1170 Test` profile. No agent clicks were used in this xEdit run. The
helper started, checked records, closed, and ModLab retained the verified result.

- 37 active plugins loaded; 31 mod/Creation plugins inspected.
- 100,026 winning records checked; 1,817 superseded records excluded.
- All 37 source and staged plugin files remained unchanged.
- Apocalypse: two records with errors. Ars Metallica: one record with an error.
- Saints & Seducers: no errors among its winning records in this final context.
  Its older source-check warning remains in retained reports; its separate LOOT
  and withheld-cleaning advice has not been treated as resolved by this check.
- Script traversal took 3 minutes 36 seconds, plus loading/staging. Cached reuse
  avoids repeating it for unchanged inputs.

The first native attempt exposed the helper's startup dialog and an internal
`SkyrimSE.exe` pseudo-file. Its result was withheld by the strict context check.
Both integration issues were corrected before the successful unattended run.

Verification: 493 hub tests pass. The independent local verifier rechecked the
complete report, exact errors, all source/staged hashes and retained cache hashes.
Read-only review found and prompted fixes for omitted loose text-table inputs
and treating a queued selection click as acknowledgment. Final terminal-report
failure handling has unit coverage; the successful native run used the same
normal completion path with full report parsing before closure.

Sources: xEdit's bundled `Check for errors.pas`; official
[scripting API](https://tes5edit.github.io/docs/13-Scripting-Functions.html);
[xeInit](https://github.com/TES5Edit/TES5Edit/blob/dev-4.1.5/xEdit/xeInit.pas) and
[xeMainForm](https://github.com/TES5Edit/TES5Edit/blob/dev-4.1.5/xEdit/xeMainForm.pas).

This is record-structure/reference evidence, not proof of gameplay, complete
conflict resolution, or permission to clean source mods. The broad goal remains
unfinished. Pro's new review findings were independently reproduced against
7aa9587: custom-preset distribution provenance blocks subsequent preparation,
and current shape inspection misses changed actor/body ownership. Those are the
next two focused corrections; no shape functionality is removed by this change.
