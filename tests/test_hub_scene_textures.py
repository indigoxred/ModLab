import unittest


class SceneTextureTests(unittest.TestCase):
    def test_default_shader_unused_height_map_is_not_a_missing_dependency(self):
        from modlab.resources.mo2_hub.scene_textures import required_textures
        inspection = {'shapes': [{'shader': 0, 'textures': ['a.dds', 'a_n.dds', '', 'a_p.dds']}],
                      'blocks': [{'type': 'BSLightingShaderProperty'}, {'type': 'BSShaderTextureSet'}]}
        required, unused = required_textures(['textures/a.dds', 'textures/a_n.dds', 'textures/a_p.dds'], inspection)
        self.assertEqual({'textures/a.dds', 'textures/a_n.dds'}, set(required))
        self.assertEqual(['textures/a_p.dds'], unused)

    def test_active_shader_or_other_shape_reference_keeps_height_texture_required(self):
        from modlab.resources.mo2_hub.scene_textures import required_textures
        for shader, textures in ((3, ['', '', '', 'a_p.dds']), (0, ['a_p.dds'])):
            inspection = {'shapes': [{'shader': 0, 'textures': ['', '', '', 'a_p.dds']},
                                      {'shader': shader, 'textures': textures}], 'blocks': []}
            required, unused = required_textures(['textures/a_p.dds'], inspection)
            self.assertEqual(['textures/a_p.dds'], required)
            self.assertEqual([], unused)

    def test_uninspected_texture_bearing_block_prevents_ignoring_reference(self):
        from modlab.resources.mo2_hub.scene_textures import required_textures
        inspection = {'shapes': [{'shader': 0, 'textures': ['', '', '', 'a_p.dds']}],
                      'blocks': [{'type': 'NiSourceTexture'}]}
        required, unused = required_textures(['textures/a_p.dds'], inspection)
        self.assertEqual(['textures/a_p.dds'], required)
        self.assertEqual([], unused)
