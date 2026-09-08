import struct
import unittest
from modlab.resources.mo2_hub import body_morphs
from tests.test_hub_body_morphs import tri


class MorphLinkTests(unittest.TestCase):
    def verify(self, meshes, files):
        self.assertTrue(hasattr(body_morphs, 'verify_mesh_links'), 'Morph capability needs the NIF link and matching TRI records')
        return body_morphs.verify_mesh_links(meshes, files)

    def mesh(self, links=('actors/body.tri',), vertices=1, name='Body'):
        return {'body.nif':{'shapes':{name:{'vertices':vertices,'links':list(links)}}}}

    def test_valid_tri_beside_mesh_without_bodytri_is_not_morph_capability(self):
        with self.assertRaisesRegex(ValueError, 'BODYTRI'):
            self.verify(self.mesh(links=()), {'meshes/actors/body.tri':tri()})

    def test_link_on_one_shape_can_supply_other_shapes_in_same_mesh(self):
        meshes=self.mesh(links=())
        meshes['body.nif']['shapes']['Accessory']={'vertices':3,'links':['actors\\body.tri']}
        result=self.verify(meshes, {'meshes/actors/body.tri':tri()})
        self.assertEqual('meshes/actors/body.tri', result['body.nif']['tri'])
        self.assertEqual(['Body'],result['body.nif']['matched_shapes'])
        self.assertEqual(['Waist'],result['body.nif']['morphs'])

    def test_missing_link_target_is_not_satisfied_by_another_tri(self):
        with self.assertRaisesRegex(ValueError, 'missing'):
            self.verify(self.mesh(), {'meshes/other.tri':tri()})

    def test_morph_shape_must_match_mesh_and_vertex_indices_must_fit(self):
        with self.assertRaisesRegex(ValueError, 'shape'):
            self.verify(self.mesh(name='Renamed'), {'meshes/actors/body.tri':tri()})
        data=bytearray(tri())
        # The one packed delta is <index,x,y,z>, followed by the empty UV count.
        struct.pack_into('<H',data,len(data)-10,1)
        with self.assertRaisesRegex(ValueError, 'vertex'):
            self.verify(self.mesh(vertices=1), {'meshes/actors/body.tri':bytes(data)})

    def test_conflicting_links_and_unsafe_paths_are_not_guessed(self):
        for links in (('actors/body.tri','actors/other.tri'), ('../body.tri',), ('C:/body.tri',)):
            with self.subTest(links=links), self.assertRaises(ValueError):
                self.verify(self.mesh(links=links), {'meshes/actors/body.tri':tri()})


if __name__=='__main__': unittest.main()
