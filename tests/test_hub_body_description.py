import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from modlab.resources.mo2_hub import characters


class BodyDescriptionTests(unittest.TestCase):
    def test_names_effective_file_providers_not_skin_record_plugin(self):
        self.assertTrue(hasattr(characters,'body_description'))
        with TemporaryDirectory() as temp:
            root=Path(temp)
            mesh=root/'My Body'/'body.nif'; mesh.parent.mkdir(); mesh.write_bytes(b'body')
            skin=root/'My Skin'/'skin.dds'; skin.parent.mkdir(); skin.write_bytes(b'skin')
            row={'body':dict(scope='shared',sex='female',body_models=['meshes/body.nif'],textures=['textures/skin.dds'],
                parts=[dict(texture_swap=None,texture_set='000900:base.esm')],skin='000800:base.esm',skin_plugin='Gameplay Patch.esp')}
            result=characters.body_description(row,lambda p:str(mesh if p.startswith('meshes') else skin),root)
            self.assertIn('My Body',result['body']); self.assertIn('My Skin',result['skin'])
            self.assertNotIn('Gameplay Patch',result['body']+result['skin'])

    def test_unresolved_archive_assets_are_not_reported_as_present_or_missing(self):
        self.assertTrue(hasattr(characters,'body_description'))
        row={'body':dict(scope='private',sex='female',body_models=['meshes/body.nif'],textures=[],
            parts=[dict(texture_swap=None,texture_set=None)],skin='000800:base.esm',skin_plugin='Face.esp')}
        result=characters.body_description(row,lambda p:'',Path('unused'))
        self.assertIn('archive',result['body']); self.assertNotIn('Supplied by',result['body'])
        self.assertIn('inside',result['skin'])

    def test_overwrite_and_unknown_external_files_are_not_labelled_game_data(self):
        with TemporaryDirectory() as temp:
            root=Path(temp); overwrite=root/'overwrite'; overwrite.mkdir()
            body=overwrite/'body.nif'; body.write_bytes(b'body')
            external=root/'other'/'skin.dds'; external.parent.mkdir(); external.write_bytes(b'skin')
            row={'body':dict(scope='shared',sex='female',body_models=['meshes/body.nif'],textures=['textures/skin.dds'],
                parts=[dict(texture_swap=None,texture_set='000900:base.esm')],skin='000800:base.esm',skin_plugin='Base.esm')}
            result=characters.body_description(row,lambda p:str(body if p.startswith('meshes') else external),root/'mods')
            self.assertNotIn('Game Data',result['body']+result['skin'])
            result=characters.body_description(row,lambda p:str(body if p.startswith('meshes') else external),root/'mods',
                overwrite_path=overwrite,game_data=root/'Data')
            self.assertIn('Overwrite',result['body']); self.assertIn('External',result['skin'])


if __name__=='__main__': unittest.main()
