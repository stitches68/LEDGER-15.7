import sqlite3
import tempfile
import unittest
from pathlib import Path
from sys import path

path.insert(0, str(Path(__file__).resolve().parents[1]))
from storage import LedgerStorage, RuleError


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp.name) / 'ledger.sqlite3'
        self.db = LedgerStorage(self.db_path)
        self.db.configure(10, 'board_channel_id', 100)
        self.cid = self.db.create_contract(10, 'Combat recovery',
                                           'Extract survivors from battle',500,1,100)

    def tearDown(self):
        self.db.conn.close()
        self.temp.cleanup()

    def test_each_individual_earns_full_payout_once(self):
        self.assertTrue(self.db.claim(10,self.cid,'USER',20,20)[0])
        self.assertFalse(self.db.claim(10,self.cid,'USER',20,20)[0])
        self.assertTrue(self.db.claim(10,self.cid,'USER',21,21)[0])
        x=self.db.submit(10,self.cid,'USER',20,20,'proof report 20')
        y=self.db.submit(10,self.cid,'USER',21,21,'proof report 21')
        self.assertTrue(x[0] and y[0])
        # A moderator may review and approve their own submission.
        self.assertTrue(self.db.approve(10,x[2],20)[0])
        self.assertFalse(self.db.approve(10,x[2],99)[0])
        self.assertTrue(self.db.approve(10,y[2],99)[0])
        self.assertFalse(self.db.approve(10,x[2],98)[0])
        self.assertEqual(self.db.balance(10,'USER',20),500)
        self.assertEqual(self.db.balance(10,'USER',21),500)
        self.assertEqual(self.db.get_contract(10,self.cid)['status'],'OPEN')
        self.assertEqual(self.db.counts(10,self.cid),(0,0,2))
        self.assertFalse(self.db.submit(10,self.cid,'USER',20,20,'repeated evidence')[0])

    def test_unit_payout_one_per_unit_and_target(self):
        self.db.register_unit(10,111,'ANGEL')
        self.db.register_unit(10,222,'WARDEN')
        cid=self.db.create_contract(10,'Medical evacuation','Get the rescue underway',600,1,100,'UNIT')
        c1=self.db.submit(10,cid,'UNIT',111,20,'ANGEL mission proof')
        self.assertTrue(c1[0])
        self.assertFalse(self.db.submit(10,cid,'UNIT',111,21,'second mission proof')[0])
        self.assertTrue(self.db.approve(10,c1[2],99)[0])
        self.assertEqual(self.db.balance(10,'UNIT',111),600)
        self.assertEqual(self.db.balance(10,'USER',20),0)
        c2=self.db.submit(10,cid,'UNIT',222,30,'WARDEN mission proof')
        self.assertTrue(self.db.approve(10,c2[2],99)[0])
        self.assertEqual(self.db.balance(10,'UNIT',222),600)
        cid_target=self.db.create_contract(10,'Angel only','Rescue all medical staff',900,1,100,'UNIT',111)
        self.assertFalse(self.db.submit(10,cid_target,'UNIT',222,30,'invalid unit mission')[0])
        self.assertFalse(self.db.submit(10,cid_target,'USER',20,20,'wrong payout mode')[0])

    def test_reject_and_resubmit(self):
        ok,_,rid=self.db.submit(10,self.cid,'USER',20,20,'first mission proof')
        self.assertTrue(ok)
        self.assertTrue(self.db.reject(10,rid,'Need a screenshot')[0])
        ok,_,same=self.db.submit(10,self.cid,'USER',20,20,'second mission proof')
        self.assertTrue(ok)
        self.assertEqual(rid,same)
        self.assertTrue(self.db.approve(10,rid,30)[0])

    def test_close_and_reopen(self):
        ok,_,rid=self.db.submit(10,self.cid,'USER',20,20,'mission report link')
        self.assertTrue(ok)
        self.assertTrue(self.db.set_contract_open(10,self.cid,False))
        self.assertFalse(self.db.approve(10,rid,99)[0])
        self.assertFalse(self.db.submit(10,self.cid,'USER',21,21,'another report')[0])
        self.assertTrue(self.db.set_contract_open(10,self.cid,True))
        self.assertTrue(self.db.approve(10,rid,99)[0])

    def test_shop_purchase_stock_and_atomic_balance(self):
        self.db.adjust(10,'USER',20,1000,99,'Initial stipend')
        item=self.db.add_item(10,'Armor patch','Commemorative unit patch',175,3,99)
        p,cost=self.db.purchase(10,item,20,2)
        self.assertEqual(cost,350)
        self.assertEqual(self.db.balance(10,'USER',20),650)
        self.assertEqual(self.db.item(10,item)['stock'],1)
        self.assertEqual(self.db.purchase_by_id(10,p)['status'],'PENDING')
        with self.assertRaisesRegex(RuleError,'stock'):
            self.db.purchase(10,item,20,2)
        self.assertEqual(self.db.balance(10,'USER',20),650)
        with self.assertRaisesRegex(RuleError,'Insufficient'):
            self.db.purchase(10,item,21,1)
        self.assertEqual(self.db.item(10,item)['stock'],1)
        self.assertTrue(self.db.fulfill(10,p,99))
        self.assertFalse(self.db.fulfill(10,p,99))
        self.assertEqual(self.db.balance(10,'USER',20),650)
        self.assertEqual(len(self.db.history(10,'USER',20)),2)

    def test_stock_and_item_moderation(self):
        item=self.db.add_item(10,'Ship badge','Collectible ship badge',75,None,99)
        with self.assertRaisesRegex(RuleError,'Unlimited'):
            self.db.change_stock(10,item,1)
        self.assertTrue(self.db.set_stock(10,item,5))
        self.assertEqual(self.db.change_stock(10,item,-3),2)
        with self.assertRaisesRegex(RuleError,'inventory'):
            self.db.change_stock(10,item,-3)
        self.assertTrue(self.db.update_item(10,item,price=150,name='New ship badge'))
        self.assertEqual(self.db.item(10,item)['price'],150)
        self.assertTrue(self.db.remove_item(10,item))
        self.assertFalse(self.db.remove_item(10,item))
        with self.assertRaisesRegex(RuleError,'available'):
            self.db.purchase(10,item,20,1)

    def test_individual_and_unit_shops_are_separate(self):
        self.db.register_unit(10,111,'ANGEL')
        individual=self.db.add_item(10,'Contractor emblem','Personal emblem',100,2,99)
        unit=self.db.add_item(10,'Fleet upgrade','Unit equipment upgrade',400,3,99,
                              shop_kind='UNIT')
        self.assertEqual([r['id'] for r in self.db.items(10,shop_kind='USER')],[individual])
        self.assertEqual([r['id'] for r in self.db.items(10,shop_kind='UNIT')],[unit])
        self.assertIsNone(self.db.item(10,unit))
        self.assertIsNone(self.db.item(10,individual,'UNIT'))
        self.assertFalse(self.db.update_item(10,unit,price=1))
        self.assertFalse(self.db.remove_item(10,unit))
        self.assertFalse(self.db.set_stock(10,unit,9))
        with self.assertRaisesRegex(RuleError,'not found'):
            self.db.change_stock(10,unit,1)
        with self.assertRaisesRegex(RuleError,'available'):
            self.db.purchase(10,unit,20,1)
        self.assertTrue(self.db.update_item(10,unit,price=375,shop_kind='UNIT'))
        self.assertEqual(self.db.item(10,unit,'UNIT')['price'],375)
        self.assertEqual(self.db.item(10,individual)['price'],100)

    def test_unit_shop_debits_unit_treasury_not_personal_wallet(self):
        self.db.register_unit(10,111,'ANGEL')
        self.db.adjust(10,'UNIT',111,1000,99,'Treasury grant')
        self.db.adjust(10,'USER',20,250,99,'Personal grant')
        item=self.db.add_item(10,'Support crate','Unit medical supplies',200,3,99,'UNIT')
        purchase_id,cost=self.db.purchase(10,item,20,2,shop_kind='UNIT',recipient_id=111)
        self.assertEqual(cost,400)
        self.assertEqual(self.db.balance(10,'UNIT',111),600)
        self.assertEqual(self.db.balance(10,'USER',20),250)
        self.assertEqual(self.db.item(10,item,'UNIT')['stock'],1)
        receipt=self.db.purchase_by_id(10,purchase_id)
        self.assertEqual(receipt['user_id'],20)
        self.assertEqual(receipt['recipient_kind'],'UNIT')
        self.assertEqual(receipt['recipient_id'],111)
        self.assertEqual(len(self.db.pending_purchases(10,shop_kind='UNIT')),1)
        self.assertEqual(len(self.db.pending_purchases(10,shop_kind='USER')),0)
        self.assertFalse(self.db.fulfill(10,purchase_id,99))
        self.assertTrue(self.db.fulfill(10,purchase_id,99,shop_kind='UNIT'))
        self.assertFalse(self.db.fulfill(10,purchase_id,99,shop_kind='UNIT'))
        self.assertEqual(self.db.history(10,'UNIT',111)[0]['amount'],-400)
        with self.assertRaisesRegex(RuleError,'registered unit'):
            self.db.purchase(10,item,20,1,shop_kind='UNIT',recipient_id=222)

    def test_unit_shop_cannot_overspend_and_rolls_back_stock(self):
        self.db.register_unit(10,111,'ANGEL')
        item=self.db.add_item(10,'Dropship','Unit transport asset',1000,1,99,'UNIT')
        with self.assertRaisesRegex(RuleError,'Insufficient'):
            self.db.purchase(10,item,20,1,shop_kind='UNIT',recipient_id=111)
        self.assertEqual(self.db.item(10,item,'UNIT')['stock'],1)
        self.assertEqual(self.db.balance(10,'UNIT',111),0)
        self.assertEqual(len(self.db.pending_purchases(10,shop_kind='UNIT')),0)
        with self.assertRaisesRegex(RuleError,'registered unit'):
            self.db.purchase(10,item,20,1,shop_kind='UNIT')

    def test_unit_buyer_role_can_be_set_cleared_and_preserved(self):
        self.db.register_unit(10,111,'ANGEL')
        self.assertTrue(self.db.set_unit_buyer_role(10,111,333))
        self.db.register_unit(10,111,'ANGEL renamed')
        self.assertEqual(self.db.get_unit(10,111)['buyer_role_id'],333)
        self.assertTrue(self.db.set_unit_buyer_role(10,111,None))
        self.assertIsNone(self.db.get_unit(10,111)['buyer_role_id'])
        self.assertFalse(self.db.set_unit_buyer_role(10,999,333))

    def test_moderators_can_link_existing_roles_to_units(self):
        self.db.register_unit(10,111,'ANGEL')
        self.db.register_unit(10,222,'SPECTER')
        self.assertFalse(self.db.member_in_unit(10,111,[333]))
        self.assertTrue(self.db.member_in_unit(10,111,[111]))  # original role works
        self.assertTrue(self.db.link_unit_role(10,111,333,99))
        self.assertTrue(self.db.link_unit_role(10,111,444,99))
        self.assertFalse(self.db.link_unit_role(10,111,333,99))  # idempotent
        self.assertEqual(self.db.unit_member_roles(10,111),[333,444])
        self.assertTrue(self.db.member_in_unit(10,111,[333]))
        self.assertTrue(self.db.member_in_unit(10,111,[444]))
        self.assertFalse(self.db.member_in_unit(10,222,[333]))
        self.assertEqual([u['name'] for u in self.db.member_units(10,[333,222])],
                         ['ANGEL','SPECTER'])
        self.assertTrue(self.db.unlink_unit_role(10,111,333))
        self.assertFalse(self.db.unlink_unit_role(10,111,333))
        self.assertFalse(self.db.member_in_unit(10,111,[333]))
        self.assertTrue(self.db.member_in_unit(10,111,[444]))
        self.assertEqual(self.db.get_unit(10,111)['name'],'ANGEL')

    def test_discord_role_cannot_be_mapped_to_two_units(self):
        self.db.register_unit(10,111,'ANGEL')
        self.db.register_unit(10,222,'WARDEN')
        self.db.link_unit_role(10,111,333,99)
        with self.assertRaisesRegex(RuleError,'another unit'):
            self.db.link_unit_role(10,222,333,99)
        with self.assertRaisesRegex(RuleError,'primary role'):
            self.db.link_unit_role(10,111,222,99)
        with self.assertRaisesRegex(RuleError,'already grants'):
            self.db.link_unit_role(10,111,111,99)
        with self.assertRaisesRegex(RuleError,'cannot be unlinked'):
            self.db.unlink_unit_role(10,111,111)
        with self.assertRaisesRegex(RuleError,'different unit'):
            self.db.register_unit(10,333,'Trying to reassign linked role')
        self.assertEqual(self.db.unit_member_roles(10,111),[333])
        self.assertTrue(self.db.unlink_unit_role(10,111,333))
        self.assertTrue(self.db.link_unit_role(10,222,333,99))
        self.assertTrue(self.db.member_in_unit(10,222,[333]))

    def test_role_pairs_are_guild_scoped_and_survive_restarts(self):
        self.db.register_unit(10,111,'ANGEL')
        self.db.register_unit(11,111,'OTHER SERVER UNIT')
        self.db.link_unit_role(10,111,333,99)
        self.assertFalse(self.db.member_in_unit(11,111,[333]))
        self.assertEqual(self.db.unit_member_roles(11,111),[])
        with self.assertRaisesRegex(RuleError,'Register'):
            self.db.link_unit_role(11,222,444,99)
        self.db.conn.close()
        self.db=LedgerStorage(self.db_path)
        self.assertTrue(self.db.member_in_unit(10,111,[333]))
        self.assertFalse(self.db.member_in_unit(11,111,[333]))
        self.db.register_unit(10,111,'ANGEL renamed')
        self.assertTrue(self.db.member_in_unit(10,111,[333]))
        self.assertEqual(self.db.get_unit(10,111)['name'],'ANGEL renamed')

    def test_currency_emoji_id_survives_restart_and_is_guild_scoped(self):
        self.assertIsNone(self.db.setting(10, 'currency_emoji_id'))
        self.db.configure(10, 'currency_emoji_id', 123456789012345678)
        self.assertEqual(self.db.setting(10, 'currency_emoji_id'), 123456789012345678)
        self.assertIsNone(self.db.setting(11, 'currency_emoji_id'))
        self.db.conn.close()
        self.db = LedgerStorage(self.db_path)
        self.assertEqual(self.db.setting(10, 'currency_emoji_id'), 123456789012345678)
        self.assertIsNone(self.db.setting(11, 'currency_emoji_id'))

    def test_store_channel_settings_are_independent(self):
        self.db.configure(10,'shop_channel_id',100)
        self.db.configure(10,'unit_shop_channel_id',200)
        self.db.configure(10,'shop_message_id',300)
        self.db.configure(10,'unit_shop_message_id',400)
        self.assertEqual(self.db.setting(10,'shop_channel_id'),100)
        self.assertEqual(self.db.setting(10,'unit_shop_channel_id'),200)
        self.assertEqual(self.db.setting(10,'shop_message_id'),300)
        self.assertEqual(self.db.setting(10,'unit_shop_message_id'),400)

    def test_adjustments_audited_and_no_negative_balances(self):
        self.db.register_unit(10,111,'SPECTER')
        self.assertEqual(self.db.adjust(10,'UNIT',111,300,99,'Unit operation bonus'),300)
        self.assertEqual(self.db.adjust(10,'UNIT',111,-200,99,'Supply expense'),100)
        with self.assertRaises(RuleError):
            self.db.adjust(10,'UNIT',111,-101,99,'Overspend')
        self.assertEqual(self.db.balance(10,'UNIT',111),100)
        self.assertEqual([x['amount'] for x in self.db.history(10,'UNIT',111)],[-200,300])

    def test_guild_isolation(self):
        self.assertFalse(self.db.claim(11,self.cid,'USER',20,20)[0])
        self.assertIsNone(self.db.get_contract(11,self.cid))
        self.assertEqual(self.db.balance(11,'USER',20),0)
        self.assertEqual(self.db.setting(11,'board_channel_id'),None)
        self.assertFalse(self.db.set_contract_open(11,self.cid,False))

    def test_legacy_migration_preserves_balance_and_prevents_duplicate(self):
        self.db.conn.close()
        conn=sqlite3.connect(self.db_path)
        conn.execute("INSERT INTO wallets(guild_id,user_id,balance) VALUES (10,70,450)")
        conn.execute("""INSERT INTO contracts(guild_id,title,details,reward,created_by,status,
                        claimed_by,board_channel_id) VALUES(10,'Old paid','Legacy paid bounty',450,1,'PAID',70,100)""")
        old_id=conn.execute("SELECT MAX(id) FROM contracts").fetchone()[0]
        conn.execute("INSERT INTO payments(guild_id,contract_id,user_id,amount,approved_by) VALUES(?,?,?,?,?)",
                     (10,old_id,70,450,88))
        conn.commit();conn.close()
        self.db=LedgerStorage(self.db_path)
        self.assertEqual(self.db.balance(10,'USER',70),450)
        self.assertEqual(self.db.get_contract(10,old_id)['status'],'OPEN')
        self.assertEqual(self.db.get_claim(10,old_id,'USER',70)['status'],'PAID')
        self.assertFalse(self.db.submit(10,old_id,'USER',70,70,'new claim proof')[0])
        self.assertTrue(self.db.submit(10,old_id,'USER',71,71,'new user proof')[0])
        self.db.conn.close()
        self.db=LedgerStorage(self.db_path)
        self.assertEqual(self.db.balance(10,'USER',70),450)
        self.assertEqual(len(self.db.history(10,'USER',70)),1)

    def test_previous_one_shop_ledger_migrates_without_losing_orders(self):
        self.db.conn.close()
        conn=sqlite3.connect(self.db_path)
        # Re-create legacy one-shop tables as originally shipped.
        for table in ('shop_purchases','shop_items','ledger_settings','units'):
            conn.execute(f'DROP TABLE {table}')
        conn.executescript('''
            CREATE TABLE shop_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL,
                name TEXT NOT NULL,description TEXT NOT NULL,
                price INTEGER NOT NULL,stock INTEGER,active INTEGER NOT NULL DEFAULT 1,
                created_by INTEGER NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE shop_purchases (
                id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL,
                item_id INTEGER NOT NULL,user_id INTEGER NOT NULL,
                quantity INTEGER NOT NULL,total_price INTEGER NOT NULL,
                status TEXT DEFAULT 'PENDING',fulfilled_by INTEGER,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP, fulfilled_at TEXT
            );
            CREATE TABLE ledger_settings (
                guild_id INTEGER PRIMARY KEY,board_channel_id INTEGER,
                review_channel_id INTEGER,shop_channel_id INTEGER,
                shop_message_id INTEGER,moderator_role_id INTEGER
            );
            CREATE TABLE units (guild_id INTEGER NOT NULL,role_id INTEGER NOT NULL,
                                name TEXT NOT NULL,PRIMARY KEY(guild_id,role_id));
            INSERT INTO shop_items(guild_id,name,description,price,stock,created_by)
                 VALUES(10,'Original market patch','Old item listing',100,5,99);
            INSERT INTO shop_purchases(guild_id,item_id,user_id,quantity,total_price)
                 VALUES(10,1,20,1,100);
            INSERT INTO ledger_settings(guild_id,shop_channel_id,shop_message_id)
                 VALUES(10,111,222);
            INSERT INTO units(guild_id,role_id,name) VALUES(10,333,'WARDEN');
        ''')
        conn.commit(); conn.close()
        self.db=LedgerStorage(self.db_path)
        self.assertEqual(self.db.item(10,1)['shop_kind'],'USER')
        self.assertEqual(len(self.db.items(10,shop_kind='UNIT')),0)
        self.assertEqual(self.db.purchase_by_id(10,1)['recipient_kind'],'USER')
        self.assertEqual(self.db.purchase_by_id(10,1)['recipient_id'],20)
        self.assertEqual(self.db.setting(10,'shop_channel_id'),111)
        self.assertIsNone(self.db.setting(10,'unit_shop_channel_id'))
        self.assertIsNone(self.db.setting(10,'currency_emoji_id'))
        self.db.configure(10,'currency_emoji_id',123456789012345678)
        self.assertEqual(self.db.setting(10,'currency_emoji_id'),123456789012345678)
        self.assertIsNone(self.db.get_unit(10,333)['buyer_role_id'])
        self.db.conn.close()
        self.db=LedgerStorage(self.db_path)
        self.assertEqual(len(self.db.pending_purchases(10)),1)


    def test_photo_criteria_and_review_assessment(self):
        self.assertTrue(self.db.set_contract_criteria(10,self.cid,
            'Mission complete, difficulty 7 or higher, at least 300 kills'))
        contract=self.db.get_contract(10,self.cid)
        self.assertIn('300 kills',contract['verification_criteria'])
        ok,_,claim_id=self.db.submit(10,self.cid,'USER',20,20,'https://cdn.discordapp.com/proof.png')
        self.assertTrue(ok)
        self.db.save_proof_message(10,claim_id,987654)
        self.db.save_analysis(10,claim_id,'UNCERTAIN','Difficulty is cut off in image')
        claim=self.db.claim_by_id(10,claim_id)
        self.assertEqual(claim['proof_message_id'],987654)
        self.assertEqual(claim['ai_verdict'],'UNCERTAIN')
        self.assertIn('cut off',claim['ai_summary'])
        self.assertEqual(self.db.balance(10,'USER',20),0)  # AI never pays
        self.assertTrue(self.db.approve(10,claim_id,99)[0])
        self.assertEqual(self.db.balance(10,'USER',20),500)
        with self.assertRaises(RuleError):
            self.db.save_analysis(10,claim_id,'PASS','Should reject paid claims')

    def test_photo_assessment_cleared_on_resubmission(self):
        _,_,rid=self.db.submit(10,self.cid,'USER',20,20,'https://cdn.discordapp.com/first.jpg')
        self.db.save_analysis(10,rid,'FAIL','Score below required threshold')
        self.db.save_proof_message(10,rid,4444)
        self.assertTrue(self.db.reject(10,rid,'Try again')[0])
        self.assertTrue(self.db.submit(10,self.cid,'USER',20,20,
                                      'https://cdn.discordapp.com/second.jpg')[0])
        row=self.db.claim_by_id(10,rid)
        self.assertIsNone(row['ai_verdict'])
        self.assertIsNone(row['ai_summary'])
        self.assertIsNone(row['proof_message_id'])
        with self.assertRaises(RuleError):
            self.db.save_analysis(10,rid,'MAYBE','Invalid verdict')

    def test_criteria_and_proof_isolated_per_server(self):
        self.assertFalse(self.db.set_contract_criteria(11,self.cid,'Something on screenshot'))
        with self.assertRaises(RuleError):
            self.db.set_contract_criteria(10,self.cid,'short')
        self.assertIsNone(self.db.get_contract(10,self.cid)['verification_criteria'])
        _,_,rid=self.db.submit(10,self.cid,'USER',20,20,'https://discord.com/valid.jpg')
        with self.assertRaises(RuleError):
            self.db.save_proof_message(11,rid,555)



if __name__=='__main__':
    unittest.main()
