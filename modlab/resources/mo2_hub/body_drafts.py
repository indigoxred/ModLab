"""Pending UI choices are separate from verified shared-body defaults."""
import json
import os
from pathlib import Path


def choice_fields(choice):
    result=dict(body=choice.get('body'),preset=choice.get('preset'),
        decisions=dict(choice.get('decisions',{})),outfits=bool(choice.get('outfits',True)),
        shape_support=choice.get('shape_support'))
    if choice.get('individual_shapes'):result['individual_shapes']=True
    return result


def load_drafts(profile):
    path=Path(profile)/'modlab-body-drafts.json'
    if not path.exists(): return {}
    record=json.loads(path.read_text(encoding='utf-8'))
    if record.get('profile_path')!=str(profile): raise ValueError('Pending body choices belong to another profile.')
    drafts=record.get('drafts')
    if not isinstance(drafts,dict) or set(drafts)-{'female','male'}:
        raise ValueError('Pending body choices could not be read.')
    for value in drafts.values():
        if not isinstance(value,dict) or not isinstance(value.get('decisions',{}),dict):
            raise ValueError('Pending body choices could not be read.')
    return drafts


def save_draft(profile,sex,choice,*,expected):
    if sex not in {'female','male'}: raise ValueError('Choose female or male shared bodies.')
    drafts=load_drafts(profile)
    if drafts.get(sex)!=expected: raise ValueError('Pending body choices changed elsewhere. Reopen this screen before saving.')
    if choice is None: drafts.pop(sex,None)
    else: drafts[sex]=choice_fields(choice)
    path=Path(profile)/'modlab-body-drafts.json'; temporary=path.with_suffix('.tmp')
    temporary.write_text(json.dumps(dict(profile_path=str(profile),drafts=drafts),indent=2),encoding='utf-8')
    os.replace(temporary,path)


def missing_decisions(decisions,available_keys):
    return {key:value for key,value in decisions.items() if value and key not in available_keys}
