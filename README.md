# ModLab

ModLab is a Windows companion for building a hand-picked Bethesda mod setup without risking the setup you already play. You choose every component. ModLab is being built to make changes in a separate Lab, show objective checks and Unknowns, and promote only a setup you explicitly approve. It is not a one-click list and it does not use Vortex.

## Current build

The current build validates transparent Foundation Recipe files, retains user-selected ZIP/7z/RAR files in a local archive vault, stores immutable checkpoint lockfiles supplied by future game adapters, contains a tested internal Lab-to-Play file transaction engine, and can inspect one explicitly selected Skyrim Steam library plus one contained portable MO2 instance read-only. Transactions retain prior and desired bytes, refuse drift, structurally exclude saves/co-saves, and can roll back after failure or process interruption. Their immutable file plan and roots have a SHA-256 identity. It does **not** launch MO2 or a game, download mods, extract archives, install files, expose a promotion command, or change a real game yet.

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
python -m unittest discover -s tests -v
```

Recipe review options can be repeated:

```powershell
python -m modlab recipe review RECIPE.json --environment ENVIRONMENT.json --select component-id --omit another-id
```

Exit codes are stable:

- `0` — valid recipe or review ready to be considered for approval.
- `2` — invalid file, schema, or component request.
- `3` — incomplete selection, proven compatibility block, or missing/modified retained state.

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

Lab and Play isolate selections, plug-in order, and profile INIs; MO2's installed mod directories are shared. A later promotion build must therefore detect shared-content drift and cannot pretend that profile separation makes an in-place mod update safe.

## Archive vault workflow

Put a downloaded or older trusted archive in the inbox, then retain and verify it:

```powershell
Copy-Item 'D:\Downloads\My Mod.7z' '.\workspace\inbox\'
python -m modlab artifact import '.\workspace\inbox\My Mod.7z' --workspace '.\workspace' --source-note 'Personal archive from verified source'
python -m modlab artifact list --workspace '.\workspace'
python -m modlab artifact verify 'archive-sha256:<hash>' --workspace '.\workspace'
```

Import means retention and byte identity only. It copies the source, never moves it, and performs no extraction, safety claim, compatibility decision, download, MO2 action, FOMOD selection, enablement, or installation. Guided MO2/FOMOD installation belongs to the later Skyrim adapter, where ModLab can show you conflicts and choices before anything is promoted to Play.

## Checkpoint records

Checkpoint lockfiles preserve exact adapter-supplied state and evidence under the relevant game folder. The current commands are deliberately read-only:

```powershell
python -m modlab checkpoint list --workspace '.\workspace' --game skyrim-se-ae
python -m modlab checkpoint show 'checkpoint-sha256:<hash>' --workspace '.\workspace' --game skyrim-se-ae
python -m modlab checkpoint verify 'checkpoint-sha256:<hash>' --workspace '.\workspace' --game skyrim-se-ae
```

Checkpoint creation is adapter-facing in this build. A lockfile does not prove that Skyrim or MO2 was inspected merely because it contains a field named `adapterState`; the Skyrim adapter must collect and independently verify authoritative profile, mod, plug-in, INI, root, and evidence state before ModLab can call a checkpoint Candidate or promotion-ready. These commands never promote, restore, repair, install, or touch saves and co-saves.

## Transaction recovery

The transaction core is the safety boundary a future game adapter will use for an approved Lab-to-Play change. It snapshots the exact allowlisted files first, records `Applying` before the first target write, atomically replaces files, and preserves enough prior state to roll back after a caught failure or interrupted process. It rejects traversal, directories, nested roots, duplicate paths, every `saves` path segment, and `.ess`/`.skse` targets.

Only read-only inspection is exposed at the command line:

```powershell
python -m modlab transaction list --workspace '.\workspace'
python -m modlab transaction show 'transaction:<id>' --workspace '.\workspace'
python -m modlab transaction verify 'transaction:<id>' --workspace '.\workspace'
```

Prepare, apply, commit, and recover remain internal adapter methods. No current command can promote a Lab, restore files, or point this engine at Steam/MO2. Recovery snapshots remain retained after commit or rollback; any later cleanup feature must be explicit.

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

## Build sequence

The next independent builds are verified MO2 bootstrap/configuration, MO2 checkpoint projection and Lab-to-Play comparison, and the contained generator runner. Real MO2 integration testing requires computer-control access after this read-only adapter is fixture-verified. Morrowind/OpenMW, Oblivion Classic, and Oblivion Remastered remain separate later adapter lanes rather than being forced through Skyrim assumptions.
