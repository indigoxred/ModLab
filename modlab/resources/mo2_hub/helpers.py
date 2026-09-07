"""Small helper catalogue and reusable local paths; availability is not compatibility."""

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Helper:
    label: str
    filenames: tuple[str, ...]
    download: str
    purpose: str
    placement: str


HELPERS = {
    'synthesis-cli': Helper('Synthesis automation CLI', ('Synthesis.Bethesda.CLI.exe',),
        'https://mutagen-modding.github.io/Synthesis/Synthesis-CLI/',
        'Run your selected, pinned patchers through MO2; retain output and settings for recovery and automatic rebuilds.',
        'Select the CLI built from Synthesis 0.36.6 source. The graphical release ZIP does not include it. Keep its complete build folder together.'),
    'skse': Helper('SKSE', ('skse64_loader.exe',), 'https://skse.silverlock.org/',
        'Launch script-extender profiles through MO2. Match the exact Skyrim runtime and storefront; individual DLL mods also need compatible releases.',
        'Download the matching current Steam or GOG archive, then select it with ModLab Install archives. '
        'ModLab places its loader files beside SkyrimSE.exe, retains recovery copies, and enables its scripts as an MO2 mod. '
        'Use ModLab Launch Skyrim. Root loader files are shared by profiles using this game installation.'),
    'xedit': Helper('xEdit / SSEEdit', ('SSEEdit.exe', 'SSEEdit64.exe', 'xTESEdit.exe', 'xTESEdit64.exe'),
        'https://github.com/TES5Edit/TES5Edit/releases',
        'Inspect records and perform specifically justified cleaning or patching.',
        'Extract the complete tool package outside the game folder. Cleaning will use a recoverable copy of the selected plugin.'),
    'bodyslide': Helper('BodySlide', ('BodySlide x64.exe', 'BodySlide.exe'),
        'https://www.nexusmods.com/skyrimspecialedition/mods/201',
        'Build bodies and outfits when the chosen mods supply matching BodySlide projects.',
        'Install its mod archive through MO2; retain CalienteTools/BodySlide. Launch through MO2 with an explicit output location.'),
    'pandora': Helper('Pandora', ('Pandora Behaviour Engine+.exe',),
        'https://github.com/Monitor221hz/Pandora-Behaviour-Engine-Plus/releases',
        'Generate behaviors for supported animation requirements; it is not needed for every animation replacement.',
        'Install as a mod or a separate tool. Use MO2 and an explicit output path; choose required patches before generating.'),
    'synthesis': Helper('Synthesis', ('Synthesis.exe',),
        'https://github.com/Mutagen-Modding/Synthesis/releases',
        'Run selected patchers against this load order; the selected patchers and settings determine the result.',
        'Keep the complete application in its own tools folder and retain its patcher settings.'),
    'wrye-bash': Helper('Wrye Bash', ('Wrye Bash.exe',),
        'https://github.com/wrye-bash/wrye-bash/releases',
        'Build a Bashed Patch using supported records, tags and chosen options.',
        'Follow the package layout. Run against the MO2 profile; keep generated patch output separate from source mods.'),
    'pgpatcher': Helper('PGPatcher', ('PGPatcher.exe', 'ParallaxGen.exe'),
        'https://github.com/hakasapl/PGPatcher/releases',
        'Apply supported material and mesh transformations for the selected rendering setup.',
        'Keep the complete application and its resources together. Its output must precede affected TexGen/DynDOLOD generation.'),
    'texgen': Helper('TexGen', ('TexGenx64.exe', 'TexGen.exe'),
        'https://dyndolod.info/Downloads',
        'Generate LOD textures after the applicable mesh/material steps.',
        'Use the complete DynDOLOD package outside game and MO2 directories. Generate into a separate working folder, then install the output.'),
    'dyndolod': Helper('DynDOLOD', ('DynDOLODx64.exe', 'DynDOLOD.exe'),
        'https://dyndolod.info/Downloads',
        'Generate distant object/tree/grass output for the selected setup and required resources.',
        'Use the complete external package. Generate into a separate working folder; install only the completed output and check prerequisites.'),
}


def load_locations(path):
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(data, dict) or any(not isinstance(v, str) for v in data.values()):
        raise ValueError('Saved helper paths could not be read.')
    return data


def locate_helper(helper, saved, candidates):
    key = next(key for key, value in HELPERS.items() if value == helper)
    names = {name.casefold() for name in helper.filenames}
    chosen = Path(saved[key]) if saved.get(key) else None
    if chosen and chosen.name.casefold() in names and chosen.is_file():
        return (chosen.resolve(),)
    return tuple(sorted({p.resolve() for p in map(Path, candidates)
                         if p.name.casefold() in names and p.is_file()}, key=lambda p: str(p).casefold()))


def save_location(config, key, executable):
    helper = HELPERS[key]
    executable = executable.resolve(strict=True)
    if not executable.is_file() or executable.name.casefold() not in {n.casefold() for n in helper.filenames}:
        raise ValueError(f'Select {helper.label}: ' + ' or '.join(helper.filenames))
    saved = load_locations(config)
    saved[key] = str(executable)
    config.parent.mkdir(parents=True, exist_ok=True)
    temporary = config.with_suffix('.tmp')
    temporary.write_text(json.dumps(saved, indent=2), encoding='utf-8')
    temporary.replace(config)
