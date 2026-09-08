# Character customization and changed-body rechecks

Two independently reproduced review findings are corrected. Saved intent is retained; no source mod or user configuration is silently replaced to bypass a check.

- Publishing a custom preset after character assignments now retains the original distribution rules, with the new preset excluded from random distribution. Re-preparation rebuilds the requested shared/individual assignments instead of treating ModLab's earlier generated rules as external instructions.
- Existing preset receipts are supported through their checked original configuration and output history. Recovery verifies profile/tool ownership, exact output hashes, original-source hashes, deterministic preset exclusion, and the relevant assignment receipt or previous preset backup. Missing/altered evidence is withheld; arbitrary external rules are not stripped.
- Character-shape rechecks resolve the current winning NPC/race/skin/armature relationships. Each selected character must still use models covered by that chosen body's own verified neutral build. Merely retaining old files, or finding a private model elsewhere in the aggregate evidence, cannot keep the result current.

## Validation

501 hub unittest cases passed. Added failing-before/fixed-after cases cover repeated customization and preparation, shared defaults plus Lydia, retained unrelated actor rules, old-format preset chains, tampered configuration evidence, private body changes, deleted/unresolved actors, and compatible skin-only/unrelated-actor changes. A read-only code review independently exercised consecutive preset publications and the legacy recovery paths; no remaining blocking finding was reported.

Deployed the three changed production modules into the closed disposable MO2 instance, backing up the previous versions under `builds/ui-backups/before-character-roundtrip`. All deployed bytes were compared with source. Reopened ModLab on the 37-plugin `ModLab 1170 Test` profile: the existing character shape assignment still reports applied with the new ownership check, and the window responds to page navigation and filtering. The prior winning-record check also survives restart without another xEdit run.

The changed-input cases use binary plugin fixtures and real output publication with MO2/Qt host callbacks substituted. This checkpoint does not claim a new native BodySlide editing session, in-game appearance verification, or completion of the broader ModLab workflow. Current Apocalypse and Ars Metallica winning-record findings remain unresolved separately.
