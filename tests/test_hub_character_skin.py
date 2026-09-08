import unittest
from copy import deepcopy
from modlab.resources.mo2_hub import skin_assets
from modlab.resources.mo2_hub import skin_sources
from tempfile import TemporaryDirectory
from pathlib import Path


class SkinSwapTests(unittest.TestCase):
    def test_prepared_skin_name_is_only_shown_for_matching_effective_files_and_records(self):
        import json
        from types import SimpleNamespace
        from modlab.resources.mo2_hub.character_skin import current_skin_name
        from modlab.resources.mo2_hub.outputs import digest
        with TemporaryDirectory() as folder:
            root=Path(folder);file=root/'skin.dds';file.write_bytes(b'skin')
            record=root/'operation.json';record.write_text(json.dumps({'skin_plans':{'Lydia':{'source':{'mod':'Chosen skin'},'output':{'body':{'diffuse':'skin.dds'}}}},
                'body_plans':{'Lydia':{'retained_textures':['skin.dds']}}}))
            host=SimpleNamespace(resolvePath=lambda name:str(file))
            saved={'skin_choices':{'Lydia':{'source':'Chosen skin'}},'build_record':str(record),'hashes':{'Skin.dds':digest(file)}}
            row={'body':{'textures':['skin.dds']}}
            self.assertEqual('Chosen skin',current_skin_name(host,'Lydia',row,saved))
            row['body']['textures']=['other.dds']
            self.assertIsNone(current_skin_name(host,'Lydia',row,saved))
            row['body']['textures']=['skin.dds'];file.write_bytes(b'outside change')
            self.assertIsNone(current_skin_name(host,'Lydia',row,saved))

    def test_skin_plan_retains_other_texture_channels_and_replaces_exact_roles(self):
        from modlab.resources.mo2_hub.character_skin import planned_texture_fields
        before={b'TX00':[b'old/body.dds\0'],b'TX03':[b'old/detail.dds\0'],b'TX07':[b'old/spec.dds\0']}
        requested={role:'textures/new/'+role+'.dds' for role in ('diffuse','normal','subsurface','specular')}
        planned=planned_texture_fields(before,requested)
        self.assertEqual('textures/old/detail.dds',planned['TX03'])
        self.assertEqual({tag:requested[role] for tag,role in [('TX00','diffuse'),('TX01','normal'),('TX02','subsurface'),('TX07','specular')]},
            {tag:value for tag,value in planned.items() if tag!='TX03'})
        self.assertEqual([b'old/body.dds\0'],before[b'TX00'])

    def test_single_texture_swap_is_resolved_and_multiple_variants_are_not_flattened(self):
        from modlab.resources.mo2_hub.body_ownership import BodyIndex
        from modlab.resources.mo2_hub.character_skin import check_skin_swaps
        from tests.test_hub_npc import record, sub
        from tests.test_hub_character_body import ref
        with TemporaryDirectory() as folder:
            path=Path(folder)/'Base.esm'
            import struct
            path.write_bytes(record(b'TES4',sub(b'HEDR',struct.pack('<fII',1.7,2,0x900)))+record(b'FLST',ref(b'LNAM',0x801),0x800)+
                record(b'TXST',sub(b'TX00',b'body.dds\0'),0x801))
            index=BodyIndex();index.add_plugin(path)
            body={'parts':[{'texture_swap':'000800:base.esm'}]}
            check_skin_swaps(index,body)
            index.winning['000800:base.esm'][1][b'LNAM'].append(ref(b'LNAM',0x801)[6:])
            with self.assertRaisesRegex(ValueError,'multiple'):
                check_skin_swaps(index,body)
            index.winning['000800:base.esm'][1][b'LNAM']=[ref(b'LNAM',0x999)[6:]]
            with self.assertRaisesRegex(ValueError,'missing'):
                check_skin_swaps(index,body)


class SkinTextureTests(unittest.TestCase):
    def setUp(self):
        self.head=dict(name='Head',shader=4,texture_set=7,textures=[
            'textures/private/femalehead.dds','textures/private/femalehead_msn.dds',
            'textures/private/femalehead_sk.dds','textures/male/blankdetailmap.dds','','',
            'textures/actors/character/facegendata/facetint/skyrim.esm/00013bb9.dds',
            'textures/private/femalehead_s.dds',''])
        self.hair=dict(name='Hair',shader=6,texture_set=9,textures=['textures/hair/one.dds'])
        self.skin={role:'textures/new/head'+suffix+'.dds' for role,suffix in
            [('diffuse',''),('normal','_msn'),('subsurface','_sk'),('specular','_s')]}

    def test_face_skin_change_retains_hair_and_character_tint(self):
        source=[self.head,self.hair];before=deepcopy(source)
        changes=skin_assets.face_replacements(source,self.skin)
        self.assertEqual({0:'textures/new/head.dds',1:'textures/new/head_msn.dds',
            2:'textures/new/head_sk.dds',7:'textures/new/head_s.dds'},changes['Head'])
        self.assertNotIn('Hair',changes)
        self.assertNotIn(6,changes['Head'])
        self.assertEqual(before,source)

    def test_missing_matching_skin_map_is_not_silently_mixed(self):
        del self.skin['normal']
        with self.assertRaisesRegex(ValueError,'normal'):
            skin_assets.face_replacements([self.head],self.skin)

    def test_shared_texture_set_with_hair_cannot_be_changed_globally(self):
        self.hair['texture_set']=7
        with self.assertRaisesRegex(ValueError,'shared'):
            skin_assets.face_replacements([self.head,self.hair],self.skin)

    def test_ambiguous_or_unrecognised_head_is_not_guessed_from_name(self):
        self.head['shader']=0
        with self.assertRaisesRegex(ValueError,'skin shader'):
            skin_assets.face_replacements([self.head],self.skin)

    def test_unrelated_channel_change_fails_export_verification(self):
        before=[self.head,self.hair];after=deepcopy(before)
        changes=skin_assets.face_replacements(before,self.skin)
        for slot,path in changes['Head'].items():after[0]['textures'][slot]=path
        skin_assets.verify_shapes(before,after,changes)
        after[1]['textures'][0]='textures/hair/different.dds'
        with self.assertRaisesRegex(ValueError,'unrequested'):
            skin_assets.verify_shapes(before,after,changes)

    def test_path_normalization_does_not_change_the_referenced_hair_texture(self):
        before=[self.head,self.hair];after=deepcopy(before)
        after[1]['textures'][0]='Data\\Textures\\HAIR\\one.dds'
        skin_assets.verify_shapes(before,after,{})


class SkinSourceTests(unittest.TestCase):
    def test_mixed_family_option_cannot_prove_cbbe_from_unp_bytes(self):
        from modlab.resources.mo2_hub.body_variant import verify
        xml=b'<config><moduleName>CBBE and UNP package</moduleName><plugin name="CBBE / UNP"><files><folder source="unp" destination="textures"/></files></plugin></config>'
        members=['fomod/ModuleConfig.xml','unp/body.dds']
        with self.assertRaises(ValueError):
            verify(xml,members[0],members,lambda member:b'unp',{'textures/body.dds':b'unp'},'CBBE')
        specific=xml.replace(b'name="CBBE / UNP"',b'name="CBBE"').replace(b'source="unp"',b'source="cbbe"')
        self.assertEqual('CBBE',verify(specific,members[0],['cbbe/body.dds'],lambda member:b'cbbe',{'textures/body.dds':b'cbbe'},'CBBE')['family'])

    def test_archive_batch_returns_exact_members_without_extracting_files(self):
        import zipfile
        from modlab.resources.mo2_hub.body_variant import Archive
        with TemporaryDirectory() as folder:
            root=Path(folder);archive=root/'source.zip'
            with zipfile.ZipFile(archive,'w') as z:
                z.writestr('folder/first file.dds',b'first');z.writestr('other.dds',b'other');z.writestr('folder/last.dds',b'last')
            reader=Archive(archive);reader.read_many(['folder/last.dds','folder/first file.dds'])
            self.assertEqual(b'first',reader.read('folder/first file.dds'))
            self.assertEqual(b'last',reader.read('folder/last.dds'))
            self.assertEqual([archive],list(root.iterdir()))

    def test_complete_installed_skin_sets_are_grouped_without_mixing_sources(self):
        with TemporaryDirectory() as folder:
            root=Path(folder)
            for part,stem in [('head','femalehead'),('body','femalebody_1'),('hands','femalehands_1')]:
                for suffix in ('','_msn','_sk','_s'):
                    p=root/'textures/actors/character/female'/(stem+suffix+'.dds');p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(b'DDS '+b'\0'*124)
            (root/'textures/actors/character/female/femaleheadvampire.dds').write_bytes(b'unrelated')
            found=skin_sources.scan(root)
            self.assertEqual(1,len(found));self.assertEqual('female',found[0]['sex'])
            self.assertEqual('textures/actors/character/female/femalehead_msn.dds',found[0]['parts']['head']['normal'])
            self.assertEqual(12,len(found[0]['files']))
            (root/'textures/actors/character/female/femalehead_msn.dds').unlink()
            self.assertEqual([],skin_sources.scan(root))

    def test_package_family_evidence_requires_matching_installed_bytes(self):
        xml=b'<config><moduleName>Example CBBE</moduleName><requiredInstallFiles><folder source="required" destination="" /></requiredInstallFiles></config>'
        result=skin_sources.verify_required(xml,'fomod/ModuleConfig.xml', ['required/textures/body.dds'],
            lambda member:b'actual',{'textures/body.dds':b'actual'},'CBBE')
        self.assertEqual('CBBE',result['family'])
        with self.assertRaises(ValueError):
            skin_sources.verify_required(xml,'fomod/ModuleConfig.xml',['required/textures/body.dds'],
                lambda member:b'actual',{'textures/body.dds':b'other'},'CBBE')
        with self.assertRaises(ValueError):
            skin_sources.verify_required(xml.replace(b'CBBE',b'CBBE and UNP'),'fomod/ModuleConfig.xml',['required/textures/body.dds'],
                lambda member:b'actual',{'textures/body.dds':b'actual'},'CBBE')

    def test_single_family_installer_may_choose_optional_skin_variants(self):
        xml=b'<config><moduleName>Example CBBE</moduleName><requiredInstallFiles><folder source="required" destination="" /></requiredInstallFiles><plugin name="Smooth"><files><file source="smooth/head.dds" destination="textures/head.dds" /></files></plugin></config>'
        files={'required/textures/body.dds':b'body','smooth/head.dds':b'head'}
        proof=skin_sources.verify_required(xml,'fomod/ModuleConfig.xml',list(files),files.__getitem__,
            {'textures/body.dds':b'body','textures/head.dds':b'head'},'CBBE')
        self.assertEqual({'textures/body.dds','textures/head.dds'},set(proof['hashes']))
