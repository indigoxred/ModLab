"""Recognize verified body-author neutral presets without rewriting their sliders.

OBody's setup guide explicitly permits intentional nonzero corrections in the
author's neutral preset. A title or all-zero test cannot establish that contract.
Unknown releases retain the existing generic checks and customization controls.
"""
import hashlib
from pathlib import Path
from .bodyslide import plan_build, read_catalog

GUIDE='https://www.nexusmods.com/skyrimspecialedition/articles/4580'
KNOWN={
    # Verified against the CBBE 2.0.3 archive, member:
    # 00 Required (Slim)/CalienteTools/BodySlide/SliderPresets/CBBE.xml.
    # Whole-file bytes are pinned; an edited preset cannot inherit this approval.
    '4b6015cd19d349a462bc5dc820f7c642d9ca8b4745328ddd35201cf4a5e7f937':
        dict(preset='- Zeroed Sliders -',mod='CBBE',version='2.0.3',guide=GUIDE),
}


def candidates(files):
    for relative,path in files.items():
        if not relative.replace('\\','/').casefold().startswith('sliderpresets/') or Path(path).suffix.casefold()!='.xml':continue
        checksum=hashlib.sha256(Path(path).read_bytes()).hexdigest()
        if checksum in KNOWN:
            yield dict(KNOWN[checksum],file=relative,sha256=checksum)


def recommend(files,catalog,body):
    found=[]
    for proof in candidates(files):
        try:plan_build(catalog,[body],proof['preset'],morphs=True)
        except ValueError:continue
        found.append(proof)
    if len(found)!=1:
        raise ValueError('A verified author neutral preset is not available for this body. '
            'Restore the matching body package or use its documented BodySlide setup; existing outputs are retained.')
    return found[0]


def inspect(runner,preset,projects):
    if not projects:return None
    runner=Path(runner)
    try:
        files={p.relative_to(runner).as_posix():p for p in (runner/'SliderPresets').rglob('*.xml')}
        found=[proof for proof in candidates(files) if proof['preset']==preset]
        if len(found)!=1:return None
        plan_build(read_catalog(runner),projects,preset,morphs=True)
        return found[0]
    except (OSError,ValueError):return None
