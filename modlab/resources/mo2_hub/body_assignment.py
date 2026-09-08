"""Plan actor-local body references without editing shared armor or mesh files."""
from copy import copy
from .body_ownership import records

def plan(index, choices):
    result={}
    for actor,choice in choices.items():
        if choice!='shared': raise ValueError('Unknown character body choice: '+str(choice))
        key=actor.casefold()
        traits,fields,plugin,sex=index._traits(key)
        if traits!=key:
            raise ValueError('This character inherits body traits from a template; an actor-local override needs explicit template handling.')
        race=index._ref(fields,b'RNAM',plugin)
        race_fields,race_plugin=index._record(race,b'RACE','character race')
        skin=index._ref(race_fields,b'WNAM',race_plugin)
        if not skin: raise ValueError('The character race has no shared body assignment.')
        # Inspect the proposed result without mutating the source index.
        proposed=copy(index); proposed.winning=dict(index.winning)
        assigned=dict(fields); assigned[b'WNAM']=[skin]
        proposed.winning[key]=(b'NPC_',assigned,plugin)
        body=proposed.character(key)
        result[actor]=dict(skin=skin,body=body)
    return result

def verify(path, assignments):
    exported={key.casefold():value for key,value in records(path)}
    for actor,requested in assignments.items():
        value=exported.get(actor.casefold())
        if not value or value[0]!=b'NPC_' or value[1].get(b'WNAM')!=[requested['skin']]:
            raise ValueError('Generated character body assignment does not match the requested body: '+actor)
