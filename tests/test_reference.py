"""Regression checks for inventory propagation and contextual part links."""
import copy
import json
import tempfile
import unittest
from pathlib import Path

from scripts.build import Reference, ROOT, DATA, identifier


class ReferenceTests(unittest.TestCase):
    def setUp(self):
        self.ref = Reference()

    def test_inventory_edits_reach_every_text_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            inventory = Path(tmp)/'inventory.tsv'
            source = (ROOT/'frk_items.tsv').read_text()
            source = source.replace('Needle cage 10x13x10\t3\t', 'Needle cage revised\t7\t', 1)
            inventory.write_text(source)
            ref = Reference(inventory=inventory)
            part = ref.parts['95129332260']
            for output in (ref.card(part), ref.label_sheet([part]), ref.markdown(part), ref.offline_book(), ref.inventory()):
                self.assertIn('Needle cage revised', output)
                self.assertNotIn('Needle cage 10x13x10', output)
            self.assertEqual(part['count'], 7)
            self.assertIn(r'\textbf{7}', ref.card(part))
            self.assertIn(r'\textbf{7}', ref.label_sheet([part]))

    def test_housing_link_keeps_its_screw_application(self):
        text = self.ref.parts['11420802102']['applications'][0]['text']
        self.assertIn('90223711020#ms462-starter', text)
        self.assertIn('../90223711020/#ms462-starter', self.ref.render_text(text, 'html'))
        self.assertIn(r'\hyperlink{90223711020-ms462-starter}', self.ref.render_text(text, 'offline'))
        screw = self.ref.parts['90223711020']['applications']
        self.assertIn('[[11420802102]]', screw[0]['text'])
        self.assertTrue(all('11420802102' not in app['text'] for app in screw[1:]))

    def test_broken_relationship_rejected_before_generation(self):
        data = copy.deepcopy(self.ref.data)
        data['parts']['11420802102']['applications'][0]['text'] = 'See [[90223711020#missing-application]].'
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'parts.json'
            path.write_text(json.dumps(data))
            with self.assertRaisesRegex(ValueError, 'Unknown related application'):
                Reference(data=path)

    def test_kit_tooth_count_is_not_conflated_with_separate_rim(self):
        kit = self.ref.parts['11280071001']
        self.assertIn('8T', kit['name'])
        self.assertNotIn('[[00006421223]]', kit['applications'][0]['text'])
        self.assertIn('7T', self.ref.parts['00006421223']['name'])

    def test_unconfirmed_pawl_is_not_silently_substituted(self):
        part = self.ref.parts['00001957200']
        self.assertEqual(part['number'], '0000 195 7200')
        self.assertEqual(part['status'], 'unconfirmed')
        self.assertEqual({a['listed_part'] for a in part['applications']}, {'4116 195 7200', '1125 195 7200'})
        self.assertIn('Fit unconfirmed', self.ref.label_sheet([part]))
        self.assertIn('Fit unconfirmed', self.ref.card(part))

    def test_urls_preserve_leading_zeros_and_do_not_use_branch(self):
        key = identifier('0000 350 0533')
        self.assertEqual(key, '00003500533')
        self.assertEqual(self.ref.part_url(key), 'https://rduncangt.github.io/frk-management/parts/00003500533/')


if __name__ == '__main__':
    unittest.main()
