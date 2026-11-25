"""OCR policy tests; do not call external programs or make network requests."""
import sys
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from ocr_verifier import assess_ocr_text, analyze_photo_sync


class OCRPolicyTests(TestCase):
    SCREEN = '''MISSION COMPLETED
    KILLS: 204
    DEATHS: 2
    SHOTS FIRED: 955
    SHOTS HIT: 490
    SAMPLES EXTRACTED: 21
    STRATAGEMS USED: 6
    '''

    def test_readable_results_screen_passes_and_extracts_values(self):
        verdict, summary, stats = assess_ocr_text(self.SCREEN)
        self.assertEqual(verdict,'PASS')
        self.assertEqual(stats['kills'],204)
        self.assertEqual(stats['deaths'],2)
        self.assertEqual(stats['samples_collected'],21)
        self.assertEqual(stats['stratagems_used'],6)
        self.assertTrue(stats['mission_success'])

    def test_results_page_with_failed_mission_is_still_eligible(self):
        verdict, _, stats = assess_ocr_text(self.SCREEN.replace('MISSION COMPLETED','MISSION FAILED'))
        self.assertEqual(verdict,'PASS')
        self.assertFalse(stats['mission_success'])

    def test_not_enough_detail_requires_review(self):
        verdict,_,stats=assess_ocr_text('MISSION COMPLETED')
        self.assertEqual(verdict,'UNCERTAIN')
        self.assertNotIn('kills',stats)

    def test_unrelated_screenshot_fails(self):
        verdict,_,stats=assess_ocr_text('Settings are now open. Press start to review your sound settings.')
        self.assertEqual(verdict,'FAIL')
        self.assertEqual(stats,{})

    def test_player_columns_not_misattributed(self):
        source=self.SCREEN.replace('KILLS: 204','KILLS 204 181 130')
        _,_,stats=assess_ocr_text(source)
        self.assertNotIn('kills',stats)

    def test_thin_stat_screen_requires_review(self):
        verdict,_,_=assess_ocr_text('STATISTICS\nKills: 12\nDeaths: 1\nAccuracy: 45%')
        self.assertEqual(verdict,'UNCERTAIN')

    def test_missing_tesseract_never_auto_pays(self):
        with patch('ocr_verifier._configure_tesseract',return_value=False):
            verdict,_,stats=analyze_photo_sync(b'garbage','image/png')
        self.assertEqual(verdict,'UNAVAILABLE')
        self.assertEqual(stats,{})

if __name__=='__main__':
    import unittest
    unittest.main()
