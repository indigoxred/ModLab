"""Installed coherent skin sets and byte-backed body-family evidence."""
import hashlib
from pathlib import Path
import re
import xml.etree.ElementTree as ET
from .body_variant import clean,verify

SUFFIX={'':'diffuse','_msn':'normal','_n':'normal','_sk':'subsurface','_s':'specular'}


def scan(root):
    root=Path(root);groups={}
    for path in (root/'textures/actors/character').rglob('*.dds'):
        match=re.fullmatch(r'(female|male)(head|body|hands)(?:_1)?(_msn|_n|_sk|_s)?\.dds',path.name,re.I)
        if not match:continue
        sex,part,suffix=match.groups();key=(path.parent.relative_to(root).as_posix(),sex.casefold())
        roles=groups.setdefault(key,{}).setdefault(part.casefold(),{})
        role=SUFFIX[(suffix or '').casefold()]
        # Multiple normal variants must be chosen in the installer, not guessed.
        roles[role]=None if role in roles else path.relative_to(root).as_posix()
    result=[]
    for (folder,sex),parts in sorted(groups.items()):
        if not all(parts.get(part,{}).get(role) for part in ('head','body','hands') for role in ('diffuse','normal','subsurface','specular')):continue
        files=sorted({path for roles in parts.values() for path in roles.values()})
        result.append(dict(folder=folder,sex=sex,parts=parts,files=files))
    return result


def verify_required(xml,config_member,members,read_member,installed,family):
    if len(xml)>2*1024*1024 or b'<!DOCTYPE' in xml.upper() or b'<!ENTITY' in xml.upper():raise ValueError('Unsupported skin installer metadata.')
    root=ET.fromstring(xml);label=root.findtext('moduleName','')
    declarations=' '.join([label,*[p.get('name','') for p in root.findall('.//plugin')]])
    families=set(re.findall(r'(?<![A-Za-z0-9])(CBBE|UNP|UUNP|BHUNP|TBD|HIMBO)(?![A-Za-z0-9])',declarations,re.I))
    if {f.upper() for f in families}!={family.upper()}:
        raise ValueError('The skin installer does not establish one matching body family.')
    required=root.find('requiredInstallFiles')
    # A single-family package may offer several skin variants. Match each
    # installed file to an author-declared destination and exact archive bytes;
    # this proves family compatibility, not which visual variant was preferred.
    wrapper=ET.Element('config');option=ET.SubElement(wrapper,'plugin',name=family);files=ET.SubElement(option,'files')
    for rule in list(required if required is not None else [])+root.findall('.//files/*'):files.append(rule)
    result=verify(ET.tostring(wrapper),config_member,members,read_member,installed,family)
    result['basis']='Author-mapped installed files in single-family package '+label
    return result


def prove(archive_path,root,source,family):
    from .body_variant import Archive
    reader=Archive(archive_path)
    configs=[n for n in reader.members if n.replace('\\','/').casefold().endswith('fomod/moduleconfig.xml')]
    if len(configs)!=1:raise ValueError('A matching skin installer definition is needed before applying this combination.')
    installed={name:(Path(root)/clean(name)).read_bytes() for name in source['files']}
    xml=reader.read(configs[0])
    basenames={Path(name).name.casefold() for name in installed}
    reader.read_many([name for name in reader.members if Path(name.replace('\\','/')).name.casefold() in basenames])
    try:proof=verify(xml,configs[0],reader.members,reader.read,installed,family)
    except ValueError:proof=verify_required(xml,configs[0],reader.members,reader.read,installed,family)
    return dict(proof,archive=str(Path(archive_path).resolve()),
                source_hashes={name:hashlib.sha256(data).hexdigest() for name,data in installed.items()})
