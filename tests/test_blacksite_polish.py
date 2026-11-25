"""Offline regressions for OCR data quality and dispatch reference clarity."""
import sys
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch
from PIL import Image

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from blacksite_ids import contract_ref, mentor_ref, contract_id_banner, mentor_id_banner
from squad_ocr import sanitize_player_stats, _read_single_stat


class ReferenceTests(TestCase):
    def test_human_reference_matches_numeric_slash_id(self):
        self.assertEqual(contract_ref(12),'BMC-000012')
        self.assertEqual(mentor_ref(49),'MTR-000049')
        self.assertIn('`12`',contract_id_banner(12))
        self.assertIn('`49`',mentor_id_banner(49))
        self.assertIn('`contract_id`',contract_id_banner(12))
        self.assertIn('`request_id`',mentor_id_banner(49))

    def test_big_ids_are_never_truncated_or_reused(self):
        self.assertEqual(contract_ref(1000001),'BMC-1000001')
        self.assertNotEqual(mentor_ref(123),contract_ref(123))

    def test_invalid_ids_are_rejected(self):
        with self.assertRaises(ValueError): contract_ref(0)
        with self.assertRaises(ValueError): mentor_ref(-1)


class QualityTests(TestCase):
    def test_ocr_statistics_sanity_filters_bogus_numbers(self):
        result=sanitize_player_stats({'kills':223,'stims_used':2,'deaths':4,
                                      'accidentals':99999,'shots_fired':761,'shots_hit':313,
                                      'friendly_fire_damage':999999999,'accuracy_pct':0.0})
        self.assertEqual(result['kills'],223)
        self.assertEqual(result['stims_used'],2)
        self.assertNotIn('accidentals',result)
        self.assertNotIn('friendly_fire_damage',result)
        self.assertEqual(result['accuracy_pct'],41.1)

    def test_impossible_hit_count_is_not_credited(self):
        result=sanitize_player_stats({'kills':711,'shots_fired':100,'shots_hit':900,
                                      'accuracy_pct':25.0,'melee_kills':800})
        self.assertEqual(result['kills'],711)
        self.assertNotIn('shots_hit',result)
        self.assertNotIn('accuracy_pct',result)
        self.assertNotIn('melee_kills',result)

    def test_only_bounded_single_stat_crop_is_submitted_to_tesseract(self):
        import pytesseract
        img=Image.new('RGB',(1396,935),(0,0,0))
        with patch.object(pytesseract,'image_to_string',return_value='28') as mocked:
            result=_read_single_stat(img,480,900,'stims_used')
        self.assertEqual(result,28)
        self.assertTrue(mocked.called)
        cropped=mocked.call_args.args[0]
        # Only the narrow right-side numeric region of one panel was read.
        self.assertLess(cropped.width,400)
        self.assertLess(cropped.height,180)

    def test_invalid_stat_name_does_not_run_ocr(self):
        from unittest.mock import patch
        import pytesseract
        with patch.object(pytesseract,'image_to_string') as mocked:
            value=_read_single_stat(Image.new('RGB',(1396,935)),480,900,'invented')
        self.assertIsNone(value)
        mocked.assert_not_called()
