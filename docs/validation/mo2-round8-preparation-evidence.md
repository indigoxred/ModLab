# Round 8 protected preparation

The maintained gate requires caller-configured, disjoint disposable and authority run roots, derived from the same fresh run ID. A historical disposable-only attempt is never relabeled, copied into new authority, adopted, or modified. Preparation creates a fresh native verified vault and keeps its root/ancestor guards through both delegated bootstrap/configuration flows and protected publication.

`preparation.json`, `preparation-effect.json`, `preparation-originals.json`, and preparation `failure.json` are in the authority run vault. Installation, installation failure, restart, selection and envelope records also use that vault. The shared ContainmentStore adapter receives collection parents; GateConfig.authority_root is the actual run vault. No Task6 intent is created merely to capture a file.

## Original inputs

Preparation captures nine files through the shared bounded exact capture API: the maintained harness, windows_exact_fs helper, release descriptor, and each layout's artifact metadata, ModOrganizer.ini and nxmhandler.ini. Each destination has immutable `.capture.json` provenance binding original source path, volume/file identity, byte count, SHA-256 and Preparation stage. Every capture is limited to 2 MiB. Protected gate JSON reads are limited to 16 MiB. Final paths, provenance and publisher candidate names are admitted before mutation.

The independently protected preparation-originals record retains original source/archive/extractor/game observations plus each layout's retained-payload observation, relocation, configuration, runtime identities and writable baseline. Archives and runtime/game executables are represented by original native file/hash observations, not copied in full. Bootstrap plan/receipt/journal records remain structurally cross-validated. The existing release parser parses the protected release bytes. Original captured configuration bytes still must match the exact rendered contained configuration and noregister marker.

Historical `_load_preparation` reads only protected records/captures. It checks fixed destination paths before reading, exact provenance, source identity bindings, hashes and sizes. It never rereads a current Low archive/config/runtime or probes current Git/integrity/directory state. The tests record actual capture returns and guarded read paths in scratch inventories; fixture archive/game/extractor observations are explicitly synthetic, while vault policy, native pins, captured bytes and provenance execution are real.

## Current-use admission

`_fresh_preparation_preflight` first reconstructs protected history and then verifies current Git/harness/helper, curated release, original archive, extractor, game root/executable, retained archive metadata/payload, relocation/app/parent identities, configured directories, INI/marker, Low labels, runtime identities/version and writable root identities. Historical success alone does not authorize an action.

ProductionLiveBackend.source_check, direct begin and install, current load_state, finalization, and restart before cleanup/mutation reach that preflight. Public phase/install routes and their CLI dispatches retain source_check; CLI restart/finalize reach the corresponding checked actions. Process absence, plugin/runtime snapshots, writable transition checks and subsequent native launch checks are retained. Cleanup-only original process ownership and causal protocol remain 5C's responsibility.

Failure handling preserves preparation effects and explicit native ownership. Both capture/provenance writes enter the ledger before the shared capture call because publication may succeed before a later readback/close error. A secondary failure-publication ownership error is unioned with the primary capture ownership instead of discarded. Successful preparation and failure records remain immutable; restart publishes a separate record under a fresh paired run.

## Remaining integration

This is bounded Task5B, not a runnable complete Round8 gate. Task5C must protect the phase/watch/operator/runtime/observation/effect/guarded/final/cleanup originals, capture actual screenshot/log bytes, retain original process/job ownership, enforce the shared causal watcher derivation, migrate ten-phase schema-3 matrices, and adapt full offline fixtures. The focused restart regression stubs the phase failure/cleanup boundary explicitly and does not establish native phase or UI evidence. Full suite and standalone gate verification follow that integration and review.
