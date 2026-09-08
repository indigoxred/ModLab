"""Positive body-family evidence from author FOMOD mappings and installed bytes."""
import hashlib
from pathlib import PurePosixPath
import re
import xml.etree.ElementTree as ET

class Archive:
    """Read members to memory only; never extract paths supplied by an archive."""
    def __init__(self,path):
        from pathlib import Path
        import os
        self.path=Path(path).resolve()
        self.executable=Path(os.environ.get('SystemRoot','C:/Windows'))/'System32/tar.exe'
        if not self.executable.is_file():
            raise ValueError('The Windows archive reader is unavailable. Configure an archive reader before checking installer variants.')
        self.members=self._read(['-tf',str(self.path)],4*1024*1024).decode('utf-8-sig').splitlines()
        self.cache={}

    def _read(self,args,limit):
        import subprocess
        from threading import Timer
        with subprocess.Popen([str(self.executable),*args],stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0)) as process:
            timeout=Timer(45,process.kill);timeout.daemon=True;timeout.start()
            try:
                data=process.stdout.read(limit+1)
                if len(data)>limit:
                    process.kill();raise ValueError('Installer evidence exceeds the supported file size.')
                if process.wait()!=0: raise ValueError('The archive member could not be read for installer evidence.')
                return data
            finally:
                timeout.cancel()
                if process.poll() is None: process.kill()

    def read(self,member):
        if member not in self.members: raise ValueError('Unknown archive member requested.')
        if member not in self.cache:
            self.cache[member]=self._read(['-xOf',str(self.path),'--',member],128*1024*1024)
        return self.cache[member]

    def read_many(self,members):
        """Read selected regular members in one archive pass, into memory only."""
        wanted=set(members)-self.cache.keys()
        if not wanted:return
        if any(n not in self.members or '\n' in n or '\r' in n for n in wanted):
            raise ValueError('Unknown or unsupported archive member requested.')
        if len(wanted)>256 or sum(len(n)+3 for n in wanted)>24000:
            raise ValueError('The requested archive batch exceeds the supported size.')
        entries=[]
        for line in self._read(['-tvf',str(self.path)],8*1024*1024).decode('utf-8-sig').splitlines():
            fields=line.split(None,8)
            if len(fields)!=9 or fields[8] not in wanted:continue
            if not fields[0].startswith('-') or not fields[4].isdigit():
                raise ValueError('Skin evidence must be an ordinary archive file.')
            entries.append((fields[8],int(fields[4])))
        if len(entries)!=len(wanted) or {n for n,_ in entries}!=wanted:
            raise ValueError('The archive member sizes could not be established unambiguously.')
        total=sum(size for _,size in entries)
        if total>512*1024*1024:raise ValueError('Skin evidence exceeds the supported batch size.')
        raw=self._read(['-xOf',str(self.path),'--',*[name for name,_ in entries]],total)
        if len(raw)!=total:raise ValueError('The archive returned incomplete skin evidence.')
        offset=0
        for name,size in entries:self.cache[name]=raw[offset:offset+size];offset+=size

    def check(self,installed,family):
        configs=[name for name in self.members if name.replace(chr(92),'/').lower().endswith('/fomod/moduleconfig.xml')
            or name.lower()=='fomod/moduleconfig.xml']
        if len(configs)!=1: raise ValueError('A single author installer definition is needed to establish this body variant.')
        return verify(self.read(configs[0]),configs[0],self.members,self.read,installed,family)

def clean(path):
    value=path.replace(chr(92),'/').rstrip('/')
    if ':' in value or any(p in {'.','..',''} for p in value.split('/')):
        raise ValueError('Invalid installer evidence path: '+path)
    return value

def unambiguous_family(label,family):
    names=re.findall(r'(?<![A-Za-z0-9])(CBBE|UNP|UUNP|BHUNP|TBD|HIMBO)(?![A-Za-z0-9])',label,re.I)
    return {name.upper() for name in names}=={family.upper()}

def verify(xml, config_member, members, read_member, installed, family):
    if not installed: raise ValueError('Installed body and skin file evidence is required.')
    if len(xml)>2*1024*1024 or b'<!DOCTYPE' in xml.upper() or b'<!ENTITY' in xml.upper():
        raise ValueError('Unsupported installer metadata for body evidence.')
    root=ET.fromstring(xml)
    prefix=str(PurePosixPath(config_member).parent.parent)
    prefix='' if prefix=='.' else clean(prefix)+'/'
    entries={clean(name).casefold():name for name in members if not name.endswith('/')}
    wanted={clean(name).casefold():data for name,data in installed.items()}
    candidates=[]
    for option in root.findall('.//plugin'):
        label=option.get('name','')
        if not unambiguous_family(label,family): continue
        mapped={}
        for rule in option.findall('./files/*'):
            if rule.tag not in {'folder','file'}: continue
            source=clean(prefix+rule.get('source','')).casefold()
            destination=rule.get('destination','').replace(chr(92),'/').strip('/')
            if destination: destination=clean(destination).casefold()
            for entry,original in entries.items():
                if rule.tag=='folder' and entry.startswith(source+'/'):
                    target='/'.join(p for p in (destination,entry[len(source)+1:]) if p)
                elif rule.tag=='file' and entry==source: target=destination
                else: continue
                mapped.setdefault(target,[]).append(original)
        if all(name in mapped for name in wanted): candidates.append((label,mapped))
    for label,mapped in candidates:
        evidence={}
        for name,data in wanted.items():
            checksum=hashlib.sha256(data).hexdigest()
            matching=next((member for member in mapped[name] if hashlib.sha256(read_member(member)).hexdigest()==checksum),None)
            if matching is None: break
            evidence[name]=dict(member=matching,sha256=checksum)
        else: return dict(family=family,option=label,hashes=evidence)
    raise ValueError('Installed body/skin files do not match the declared '+family+' installer option. Reinstall the compatible option or choose a matching body and skin; existing files are retained.')
