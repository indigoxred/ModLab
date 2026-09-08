import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from modlab.resources.mo2_hub import character_shapes as shapes
from modlab.resources.mo2_hub.body_choices import save_default
from modlab.resources.mo2_hub.outputs import digest
from modlab.resources.mo2_hub.vfs import readable_path
from tests import test_hub_character_body as fixtures
from tests.test_hub_npc import record


ACTOR='000800:base.esm'


class ShapeBodyChangeTests(unittest.TestCase):
    def setup_host(self, root):
        data=root/'data';data.mkdir()
        maker=fixtures.CharacterBodyTests();maker.root=data
        base=maker.base()
        order=['Base.esm']
        plugins=SimpleNamespace(pluginNames=lambda:order,priority=order.index,loadOrder=order.index)
        profile=root/'profiles/Test';profile.mkdir(parents=True)
        host=SimpleNamespace(profilePath=lambda:str(profile),pluginList=lambda:plugins,
            resolvePath=lambda rel:str(data/rel) if (data/rel).is_file() else '')
        build=root/'builds/body';(build/'runner/SliderPresets').mkdir(parents=True)
        (build/'runner/SliderSets').mkdir()
        (build/'runner/SliderPresets/zero.xml').write_text('<SliderPresets><Preset name="Zero" set="Body">'
            '<SetSlider name="Waist" size="small" value="0"/><SetSlider name="Waist" size="big" value="0"/>'
            '</Preset></SliderPresets>')
        (build/'runner/SliderSets/body.osp').write_text('<SliderSetInfo><SliderSet name="Body">'
            '<Slider name="Waist" small="0" big="0"/></SliderSet></SliderSetInfo>')
        hashes={};inputs={}
        for stem in ('actors/character/female/body','private/new'):
            for weight in (0,1):
                rel=f'meshes/{stem}_{weight}.nif';path=data/rel;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b'unchanged mesh')
                inputs[rel]=dict(path=str(readable_path(path)),sha256=digest(path))
                if not stem.startswith('private'):hashes[rel]=digest(path)
        build_record=build/'operation.json'
        build_record.write_text(json.dumps(dict(profile_path=str(profile),morphs=True,morph_links=['checked'],
            effective_output_issues=[],neutral_shape_base=True,preset='Zero',projects=['Body'],hashes=hashes)))
        save_default(str(profile),'female',dict(body='Body',preset='Athletic',selected=['Body'],build_record=str(build_record)))
        state=shapes.load_choices(str(profile));state=shapes.save_choice(str(profile),ACTOR,dict(name='Lydia',body='Body',preset='Athletic'),expected=state)
        config=data/shapes.OBODY_CONFIG;config.parent.mkdir(parents=True);config.write_text('{}')
        applied=dict(choices=state['choices'],baseline_defaults={},hashes={shapes.OBODY_CONFIG:digest(config)},
            installed_path=str(data),inputs=inputs)
        shapes._write(str(profile),dict(state,applied=applied),state)
        return host,maker,order

    def test_new_private_body_invalidates_shape_even_when_all_old_files_still_match(self):
        with TemporaryDirectory() as folder:
            host,maker,order=self.setup_host(Path(folder))
            self.assertEqual('character-shapes-current',shapes.inspect_choices(host)[0].code)
            maker.plugin('Face.esp',[fixtures.npc(0x800,0x01000910),
                record(b'ARMO',fixtures.ref(b'MODL',0x01000911),0x01000910),
                fixtures.addon(0x01000911,'private/new_1.nif')],['Base.esm'])
            order.append('Face.esp')
            finding=shapes.inspect_choices(host)[0]
            self.assertEqual('character-shapes-stale',finding.code)
            self.assertIn('Lydia',finding.detail)
            self.assertIn('separate body',finding.detail)
            self.assertIn(ACTOR,shapes.load_choices(host.profilePath())['choices'])

    def test_skin_only_and_unrelated_actor_changes_keep_compatible_shape_current(self):
        with TemporaryDirectory() as folder:
            host,maker,order=self.setup_host(Path(folder))
            maker.plugin('Skin.esp',[fixtures.npc(0x800,0x01000910),
                record(b'ARMO',fixtures.ref(b'MODL',0x902),0x01000910),fixtures.npc(0x801)],['Base.esm'])
            order.append('Skin.esp')
            self.assertEqual('character-shapes-current',shapes.inspect_choices(host)[0].code)

    def test_deleted_character_or_broken_body_reference_cannot_report_current(self):
        for row in (record(b'NPC_',b'',0x800,0x20),fixtures.npc(0x800,0xdead)):
            with self.subTest(row=row),TemporaryDirectory() as folder:
                host,maker,order=self.setup_host(Path(folder))
                maker.plugin('Changed.esp',[row],['Base.esm']);order.append('Changed.esp')
                finding=shapes.inspect_choices(host)[0]
                self.assertEqual('character-shapes-stale',finding.code)
                self.assertIn('Lydia',finding.detail)
