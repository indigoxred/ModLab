# ModLab

ModLab is a Windows companion for building a hand-picked Bethesda mod setup without risking the setup you already play. You choose every component. ModLab is being built to make changes in a separate Lab, show objective checks and Unknowns, and promote only a setup you explicitly approve. It is not a one-click list and it does not use Vortex.

## Current build

The current build validates transparent Foundation Recipe files, retains user-selected ZIP/7z/RAR files in a local archive vault, and stores immutable checkpoint lockfiles supplied by future game adapters. Archives and checkpoints have stable SHA-256 identities and read-only drift checks. It does **not** connect to MO2, inspect an installed game, download mods, extract archives, install files, promote a setup, or change a game yet.

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
python -m unittest discover -s tests -v
```

Recipe review options can be repeated:

```powershell
python -m modlab recipe review RECIPE.json --environment ENVIRONMENT.json --select component-id --omit another-id
```

Exit codes are stable:

- `0` — valid recipe or review ready to be considered for approval.
- `2` — invalid file, schema, or component request.
- `3` — incomplete selection or proven compatibility block.
- `3` — also reports a retained archive that is missing or modified.

Every review result includes an explicit empty action list or the message `No downloads or installation actions were performed.`

## Folder organization

The `workspace/` directory is ModLab's user area:

```text
workspace/
  inbox/          add downloaded or local mod archives here
  library/        retained archives, metadata, and quarantine
  games/          per-game environments, checkpoints, generated output, logs
  tools/          portable external utilities
  exports/        reports and portable manifests
  runtime/        cache, jobs, and transaction recovery
```

Create or repair this directory tree non-destructively with:

```powershell
python -m modlab workspace init
```

The initializer never removes an unknown file. MO2-backed installed artifacts will remain inside the configured Skyrim environment rather than being copied into a second installed-mod tree.

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

## Build sequence

The next independent builds are the Lab-to-Play transaction core, Skyrim/MO2 adapter, and contained generator runner. Real MO2 integration testing will require computer-control access; ModLab will request it when that adapter exists. Morrowind/OpenMW, Oblivion Classic, and Oblivion Remastered remain separate later adapter lanes rather than being forced through Skyrim assumptions.
