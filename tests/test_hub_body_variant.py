import unittest
from modlab.resources.mo2_hub import body_variant

XML=b'''<config><installSteps><installStep><optionalFileGroups><group><plugins>
<plugin name="CBBE Body"><files><folder source="CBBE" destination="" /></files></plugin>
</plugins></group></optionalFileGroups></installStep></installSteps></config>'''

class VariantEvidenceTests(unittest.TestCase):
    def test_author_option_and_all_requested_installed_bytes_must_match(self):
        members={'Pack/CBBE/meshes/body.nif':b'body','Pack/CBBE/textures/skin.dds':b'skin'}
        proof=body_variant.verify(XML,'Pack/fomod/ModuleConfig.xml',members,members.__getitem__,
            {'meshes/body.nif':b'body','textures/skin.dds':b'skin'},'CBBE')
        self.assertEqual('CBBE Body',proof['option'])
        self.assertEqual(2,len(proof['hashes']))

    def test_body_match_does_not_hide_mismatched_skin(self):
        members={'Pack/CBBE/meshes/body.nif':b'body','Pack/CBBE/textures/skin.dds':b'cbbe'}
        with self.assertRaisesRegex(ValueError,'do not match'):
            body_variant.verify(XML,'Pack/fomod/ModuleConfig.xml',members,members.__getitem__,
                {'meshes/body.nif':b'body','textures/skin.dds':b'unp'},'CBBE')

    def test_name_without_installed_file_evidence_is_insufficient(self):
        with self.assertRaisesRegex(ValueError,'evidence'):
            body_variant.verify(XML,'Pack/fomod/ModuleConfig.xml',{},lambda p:b'',{},'CBBE')

    def test_missing_required_path_cannot_pass(self):
        members={'Pack/CBBE/meshes/body.nif':b'body'}
        with self.assertRaisesRegex(ValueError,'do not match'):
            body_variant.verify(XML,'Pack/fomod/ModuleConfig.xml',members,members.__getitem__,
                {'meshes/body.nif':b'body','textures/skin.dds':b'skin'},'CBBE')
