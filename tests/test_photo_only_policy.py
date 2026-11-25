"""Regression checks for screenshot-only eligibility and staff self-approval."""
import ast
from pathlib import Path
import tempfile
import unittest
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from storage import LedgerStorage


class PhotoOnlyPolicyTests(unittest.TestCase):
    def test_ocr_verification_does_not_require_openai_or_contract_criteria(self):
        source=(ROOT/'ocr_verifier.py').read_text(encoding='utf-8')
        tree=ast.parse(source)
        fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and
                n.name=='analyze_photo_sync')
        self.assertEqual([a.arg for a in fn.args.args],['data','mime'])
        self.assertIn('post-mission results',ast.get_source_segment(source,fn))
        self.assertNotIn('verification_criteria',ast.get_source_segment(source,fn))
        bot_source=(ROOT/'bot.py').read_text(encoding='utf-8')
        self.assertNotIn('OPENAI_API_KEY',bot_source)
        self.assertIn('await asyncio.to_thread(analyze_photo_sync,data,mime)',bot_source)

    def test_manual_self_approval_preserves_ledger_audit(self):
        with tempfile.TemporaryDirectory() as tmp:
            db=LedgerStorage(Path(tmp)/'ledger.sqlite3')
            contract=db.create_contract(78,'Open contract','Recover Helldivers',400,505,901)
            ok,_,claim=db.submit(78,contract,'USER',505,505,'mission screenshot')
            self.assertTrue(ok)
            ok,note,paid=db.approve_group(78,claim,505)
            self.assertTrue(ok,note)
            self.assertEqual(paid,[505])
            self.assertEqual(db.claim_by_id(78,claim)['approved_by'],505)
            self.assertEqual(db.balance(78,'USER',505),400)
            self.assertFalse(db.approve_group(78,claim,505)[0])
            self.assertEqual(db.balance(78,'USER',505),400)
            db.conn.close()

    def test_previous_saved_thresholds_remain_but_do_not_gate_auto_pay(self):
        with tempfile.TemporaryDirectory() as tmp:
            db=LedgerStorage(Path(tmp)/'ledger.sqlite3')
            contract=db.create_contract(78,'Old contract','At least 100 kills',400,505,901,
                verification_criteria='Difficulty 9 and at least 500 kills')
            ok,_,claim=db.submit(78,contract,'USER',506,506,'')
            self.assertTrue(ok)
            attempt,duplicate=db.record_attempt(78,contract,claim,506,'hash','aa'*8,
                bytes([123])*3072,'Genesis Prime',1,[])
            self.assertFalse(duplicate)
            db.set_attempt_verdict(78,attempt,'PASS','Legible game results image')
            db.save_analysis(78,claim,'PASS','Legible game results image')
            ok,note,paid=db.approve_group(78,claim,900,auto=True,attempt_id=attempt)
            self.assertTrue(ok,note)
            self.assertEqual(paid,[506])
            self.assertEqual(db.balance(78,'USER',506),400)
            db.conn.close()

if __name__=='__main__':
    unittest.main()
