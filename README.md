# ModLab

ModLab is a Windows companion for building a hand-picked Bethesda mod setup without risking the setup you already play. You choose every component. ModLab is being built to make changes in a separate Lab, show objective checks and Unknowns, and promote only a setup you explicitly approve. It is not a one-click list and it does not use Vortex.

## Current build

The current build validates transparent Foundation Recipe files, retains user-selected ZIP/7z/RAR files in a local archive vault, and can register one explicitly selected Skyrim Steam library. It can preview, create, adopt, verify, and recover a contained portable MO2 2.5.2 setup. Create transactionally builds a new contained instance; Adopt verifies a compatible existing instance without writing beneath it. ModLab also captures immutable **Observed** Skyrim baselines and reports bounded drift across the game, manager, Lab/Play profiles, shared manager state, recipe intent, target environment, and inspection coverage. Its tested internal Lab-to-Play transaction engine retains prior and desired bytes, refuses drift, structurally excludes saves/co-saves, and can roll back after failure or process interruption. The public Skyrim workflow does not launch MO2 or a game, download mods, expose Lab-to-Play promotion, or change the real game.

The bundled Skyrim and OpenMW recipes are **Drafts**. Draft means researched, not assembled and smoke-tested as an exact combination.

## Setup hierarchy

1. **Environment and Tools** — game runtime, manager or engine, script extender, root loaders, and external utilities.
2. **Foundation** — the permanent technical baseline for one exact environment and playthrough.
3. **Capability Packs** — structural choices such as rendering, animation, bodies, physics, and camera.
4. **Play Mods** — quests, followers, equipment, dialogue, locations, overhauls, and personal choices.

Popular frameworks are resolved only when an approved component needs them. They are not installed merely because they are common.

## Commands

Run these from the ModLab folder with Python 3.12:

```powershell
python -m modlab recipe check catalogue/recipes/skyrim-se-ae-current-draft.json
python -m modlab recipe review catalogue/recipes/skyrim-se-ae-current-draft.json --environment catalogue/environments/skyrim-steam-1.7.104.json
python -m modlab recipe review catalogue/recipes/skyrim-se-ae-current-draft.json --environment catalogue/environments/skyrim-steam-1.7.104.json --format json
python -m modlab artifact list --workspace ./workspace
python -m modlab checkpoint list --workspace ./workspace --game skyrim-se-ae
python -m modlab transaction list --workspace ./workspace
python -m modlab game discover skyrim --steam-root 'C:\Path\To\Steam'
python -m modlab manager discover mo2 --root '.\workspace\tools\mo2\skyrim-se-ae\app' --game-root 'C:\Path\To\Skyrim Special Edition'
python -B -m modlab manager compare mo2 --root '.\workspace\tools\mo2\skyrim-se-ae\app' --game-root 'C:\Path\To\Skyrim Special Edition' --workspace '.\workspace'
python -B -m modlab manager compare mo2 --root '.\workspace\tools\mo2\skyrim-se-ae\app' --game-root 'C:\Path\To\Skyrim Special Edition' --workspace '.\workspace' --format json
python -B -m modlab skyrim configure --steam-root 'C:\Path\To\Steam' --recipe '.\catalogue\recipes\skyrim-se-ae-current-draft.json' --environment '.\catalogue\environments\skyrim-steam-1.7.104.json' --workspace '.\workspace'
python -B -m modlab skyrim baseline create --workspace '.\workspace'
python -B -m modlab skyrim status --workspace '.\workspace'
python -B -m modlab skyrim mo2 setup --artifact 'archive-sha256:<hash>' --steam-root 'C:\Path\To\Steam' --workspace '.\workspace'
python -B -m modlab skyrim mo2 setup --apply 'bootstrap-plan-sha256:<hash>' --workspace '.\workspace'
python -B -m modlab skyrim mo2 recover 'bootstrap-job:<id>' --workspace '.\workspace'
python -m unittest discover -s tests -v
```

MO2 preview returns the immutable plan ID used by `--apply`; interrupted work reports the exact job ID used by `recover`. `--steam-root` is accepted only with `--artifact` and can be omitted after Skyrim is registered. Add `--format json` for machine-readable evidence.

Recipe review options can be repeated:

```powershell
python -m modlab recipe review RECIPE.json --environment ENVIRONMENT.json --select component-id --omit another-id
```

Exit codes are stable:

- `0` — valid input, a successful Skyrim configure/capture/use, a ready review or manager comparison, a Matched Skyrim status, or successful MO2 preview/apply/recovery.
- `2` — invalid file, identifier, schema, component request, or unsupported MO2 release.
- `3` — incomplete selection, safe refusal, Drifted/NoBaseline/Blocked status, RecoveryRequired, proven compatibility block, or missing/modified retained state.

Every review result includes an explicit empty action list or the message `No downloads or installation actions were performed.`

## Folder organization

The `workspace/` directory is ModLab's user area:

```text
workspace/
  inbox/          add downloaded or local mod archives here
  library/        retained archives, metadata, and quarantine
  games/          per-game environments, checkpoints, generated output, logs
  tools/          portable external utilities, separated by game
  exports/        reports and portable manifests
  runtime/        cache, jobs, and transaction recovery
```

Create or repair this directory tree non-destructively with:

```powershell
python -m modlab workspace init
```

The initializer never removes an unknown file. MO2-backed installed artifacts will remain inside the configured Skyrim environment rather than being copied into a second installed-mod tree.

The Skyrim portable MO2 layout is deliberately predictable:

```text
workspace/tools/mo2/skyrim-se-ae/
  app/          ModOrganizer.exe and portable ModOrganizer.ini
  downloads/    MO2-selected downloads
  mods/         installed mod directories shared by MO2 profiles
  profiles/     exact ModLab - Lab and ModLab - Play profiles
  overwrite/    generated output awaiting review and routing
```

MO2 setup evidence remains easy to find:

```text
workspace/runtime/jobs/mo2-bootstrap/plans/       immutable previews
workspace/runtime/jobs/mo2-bootstrap/<job>/        journal and recovery evidence
workspace/games/skyrim-se-ae/tool-installations/mo2/ verified receipts
```

Staging or quarantine directories remain confined to the workspace and may be retained when exact-job recovery is required.

Lab and Play isolate selections, plug-in order, and profile INIs; MO2's installed mod directories are shared. A later promotion build must therefore detect shared-content drift and cannot pretend that profile separation makes an in-place mod update safe.

## Verified MO2 setup

MO2 setup has four plain-language preview/apply outcomes:

- **Create** — build and atomically activate a new contained instance only when the target is proven Empty.
- **Adopt** — deeply compare a compatible existing instance and retain ModLab evidence without writing beneath that manager.
- **Already managed** — perform an apply-time no-op when the exact current evidence still matches a verified receipt.
- **Blocked** — refuse to apply because safety cannot be proven; preview may still retain the Blocked plan it reports.

Setup never downloads MO2, closes programs, reads saves/co-saves, edits an existing manager, or launches MO2 or Skyrim. It does run and report the Windows system `tar.exe` version, listing, and—during Create or Adopt—extraction operations. Create writes only the new contained manager and ModLab evidence. Adopt may write ModLab-owned plans, journals, receipts, and temporary staging outside the existing manager.

A receipt links the verified MO2 package subset and declared evidence only. It does not certify user mods, mutable extras, the whole manager, game playability, or machine state. If setup is interrupted or bytes cannot be proven safe, it retains a RecoveryRequired journal. `skyrim mo2 recover` acts only on that exact job and either verifies a proven result, quarantines/restores proven paths, or refuses without guessing; recovery is not promised to succeed when evidence has changed.

## Archive vault workflow

Put a downloaded or older trusted archive in the inbox, then retain and verify it:

```powershell
Copy-Item 'D:\Downloads\My Mod.7z' '.\workspace\inbox\'
python -m modlab artifact import '.\workspace\inbox\My Mod.7z' --workspace '.\workspace' --source-note 'Personal archive from verified source'
python -m modlab artifact list --workspace '.\workspace'
python -m modlab artifact verify 'archive-sha256:<hash>' --workspace '.\workspace'
```

Import means retention and byte identity only. It copies the source, never moves it, and performs no extraction, safety claim, compatibility decision, download, MO2 action, FOMOD selection, enablement, or installation. A later verified MO2 setup apply may extract the specifically curated MO2 package. Guided mod/FOMOD installation through MO2 remains later work, where ModLab can show conflicts and choices before anything is promoted to Play.

## Checkpoint records

Checkpoint lockfiles preserve exact adapter-supplied state and evidence under the relevant game folder. The current commands are deliberately read-only:

```powershell
python -m modlab checkpoint list --workspace '.\workspace' --game skyrim-se-ae
python -m modlab checkpoint show 'checkpoint-sha256:<hash>' --workspace '.\workspace' --game skyrim-se-ae
python -m modlab checkpoint verify 'checkpoint-sha256:<hash>' --workspace '.\workspace' --game skyrim-se-ae
```

The Skyrim workflow can create a strictly qualified Observed checkpoint after independently verifying authoritative profile, mod, plug-in, INI, root, and evidence state. A generic lockfile still does not prove inspection merely because it contains a field named `adapterState`, and an Observed Skyrim baseline is neither a Candidate nor promotion-ready. These commands never promote, restore, repair, install, or touch saves and co-saves.

## Transaction recovery

The transaction core is the safety boundary a future game adapter will use for an approved Lab-to-Play change. It snapshots the exact allowlisted files first, records `Applying` before the first target write, atomically replaces files, and preserves enough prior state to roll back after a caught failure or interrupted process. It rejects traversal, directories, nested roots, duplicate paths, every `saves` path segment, and `.ess`/`.skse` targets.

Only read-only inspection is exposed at the command line:

```powershell
python -m modlab transaction list --workspace '.\workspace'
python -m modlab transaction show 'transaction:<id>' --workspace '.\workspace'
python -m modlab transaction verify 'transaction:<id>' --workspace '.\workspace'
```

Prepare, apply, commit, and recover for this Lab-to-Play transaction engine remain internal adapter methods. No current command can promote a Lab or point this engine at Steam/MO2. The separate public `skyrim mo2 recover` command handles only verified MO2 bootstrap jobs. Transaction recovery snapshots remain retained after commit or rollback; any later cleanup feature must be explicit.

## Skyrim Steam discovery

The first game adapter can inspect one Steam library you name explicitly:

```powershell
python -m modlab game discover skyrim --steam-root 'C:\Users\red\Desktop\Steam'
python -m modlab game discover skyrim --steam-root 'C:\Users\red\Desktop\Steam' --format json
```

Discovery parses only Skyrim's app `489830` manifest, records the exact `SkyrimSE.exe` file version/size/SHA-256, derives the community-style compatibility runtime (for example `1.7.104.0` → `1.7.104`), and lists top-level `.esm`, `.esl`, `.esp`, and `.bsa` files in `Data`. It never recurses into saves, reads `.ess`/`.skse`, launches Steam/Skyrim, or persists a report unless a later explicit export feature is used.

Executable runtime and Anniversary content are separate facts. A runtime such as `1.7.104.0` does not prove that the complete Anniversary Creation Club bundle is installed. `cc`-prefixed file counts are reported as observations only. MO2 is also a separate optional observation (`--mo2 PATH`); a missing manager remains Unknown and ModLab never substitutes Vortex.

## Portable MO2 discovery

The manager adapter inspects the exact portable Skyrim instance you name:

```powershell
python -m modlab manager discover mo2 --root '.\workspace\tools\mo2\skyrim-se-ae\app' --game-root 'C:\Users\red\Desktop\Steam\steamapps\common\Skyrim Special Edition'
python -m modlab manager discover mo2 --root '.\workspace\tools\mo2\skyrim-se-ae\app' --game-root 'C:\Users\red\Desktop\Steam\steamapps\common\Skyrim Special Edition' --format json
```

It hashes and versions `ModOrganizer.exe`, strictly reads the portable INI and fixed Lab/Play list/configuration files, checks that every writable manager path matches the organized ModLab layout, compares the configured game path, lists installed mod directory names, hashes each available top-level `meta.ini`, and reports top-level Overwrite entries. It does not recurse through installed mod content or Overwrite, read any save/co-save, launch MO2, change a profile, authenticate, download, install, repair, or promote.

## MO2 Lab and Play comparison

The comparison command projects one stable snapshot of both exact profiles and explains Play-to-Lab differences:

```powershell
python -B -m modlab manager compare mo2 --root '.\workspace\tools\mo2\skyrim-se-ae\app' --game-root 'C:\Path\To\Skyrim Special Edition' --workspace '.\workspace'
python -B -m modlab manager compare mo2 --root '.\workspace\tools\mo2\skyrim-se-ae\app' --game-root 'C:\Path\To\Skyrim Special Edition' --workspace '.\workspace' --format json
```

It compares MO2 priority, enablement, plug-in order, and profile configuration. Additions, removals, and enablement changes include their nearest shared placement anchors. Contradictory evidence or state that changes during inspection blocks the comparison instead of producing a partial answer.

The command reads no save or co-save and writes no cache, report, profile, mod, game, checkpoint, temporary, or workspace file. Its scope does not inspect installed mod payload contents, asset conflicts, plug-in records, or runtime stability, so it does not claim that conflicts are solved or that Skyrim will run correctly.

## Skyrim observed-baseline workflow

Enter the external paths once. ModLab retains the exact recipe and target-environment source bytes by SHA-256 and records only its own registration files:

```powershell
python -B -m modlab skyrim configure --steam-root 'C:\Users\red\Desktop\Steam' --recipe '.\catalogue\recipes\skyrim-se-ae-current-draft.json' --environment '.\catalogue\environments\skyrim-steam-1.7.104.json' --workspace '.\workspace'
python -B -m modlab skyrim baseline create --workspace '.\workspace'
python -B -m modlab skyrim status --workspace '.\workspace'
```

`configure` reports the verified game and MO2 identities, chosen recipe components, target-only Unknowns, and every ModLab file it wrote. `baseline create` captures an immutable Observed checkpoint and selects it as the comparison baseline. If a valid checkpoint was created but its pointer could not be selected, recover explicitly with:

```powershell
python -B -m modlab skyrim baseline use 'checkpoint-sha256:<hash>' --workspace '.\workspace'
```

`status` compares game identity, manager identity and path containment, both Lab and Play profiles, shared installed-mod/Overwrite state, recipe intent, target environment, and the fixed coverage boundary. `Matched` means only that these observed facts still match. Status writes nothing; configure, capture, and use write only the exact ModLab paths they report. Saves and co-saves are structurally excluded.

An Observed baseline is not an assembled, tested, promotable, or restorable build. Installed mod payload contents, asset conflicts, plug-in records, foundation assembly, runtime behavior, and smoke testing remain uninspected. Mod installation, GUI use, tool launching, smoke testing, promotion, repair/upgrades, and the Morrowind/Oblivion adapters remain later work.

## Build sequence

The proven current slice is explicit Skyrim registration, immutable Observed baseline capture, recovery selection, read-only drift reporting, verified MO2 2.5.2 package linkage, contained Create, zero-existing-manager-write Adopt, and exact-job recovery. Live computer-control validation is the final proof for this slice. Mod installation, conflict analysis, runtime smoke testing, Lab-to-Play promotion, and repair/upgrades come next. Morrowind/OpenMW, Oblivion Classic, and Oblivion Remastered remain separate later adapter lanes rather than being forced through Skyrim assumptions.

## Developer MO2 containment validation

This is a developer-validation workflow only. It prepares disposable source and staging MO2 instances beneath `workspace/runtime/validation/mo2-containment/`; it does not operate the normal production MO2 instance, write the game, or install a mod into the production shared store.

Use the bundled Python runtime to prepare one run and retain the returned run ID:

```powershell
$pythonPath = 'C:\Users\red\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
$workspacePath = 'C:\Path\To\ModLab\workspace'
$artifactId = 'archive-sha256:<64 lowercase hex characters>'
$prepare = & $pythonPath -B -m modlab.validation.mo2_containment_cli prepare `
    --source-workspace $workspacePath `
    --artifact $artifactId `
    --steam-root 'C:\Path\To\Steam' `
    --workspace $workspacePath `
    --format json
$runId = ($prepare | ConvertFrom-Json).runId
```

Run each visible scenario as one foreground controller. Computer control is required only while foreground `validate` is displaying its printed procedure; follow it exactly, close the disposable MO2 normally, then type `continue` in that same process. The CLI never clicks through an installer, infers success, closes MO2, or kills a process.

```powershell
& $pythonPath -B -m modlab.validation.mo2_containment_cli validate $runId NewFolder --workspace $workspacePath
& $pythonPath -B -m modlab.validation.mo2_containment_cli validate $runId MergeExisting --workspace $workspacePath
& $pythonPath -B -m modlab.validation.mo2_containment_cli validate $runId ReplaceExisting --workspace $workspacePath
& $pythonPath -B -m modlab.validation.mo2_containment_cli validate $runId FomodDependency --workspace $workspacePath
& $pythonPath -B -m modlab.validation.mo2_containment_cli adjudicate $runId --workspace $workspacePath
& $pythonPath -B -m modlab.validation.mo2_containment_cli show $runId --workspace $workspacePath
```

If a foreground controller is interrupted or refuses capture, preserve the attempt and perform cleanup only for that exact scenario:

```powershell
& $pythonPath -B -m modlab.validation.mo2_containment_cli recover $runId MergeExisting --workspace $workspacePath
```

`show` is read-only. `recover` never upgrades an incomplete attempt, and `adjudicate` writes a separate immutable `decision.json` without replacing a scenario `result.json`. A `Supported` decision is evidence for the containment spike only: it does not enable production installation. Production use remains prohibited until the next reviewed implementation plan explicitly consumes that receipt.
