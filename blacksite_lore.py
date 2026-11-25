"""Original LEDGER in-universe intelligence fragments and Easter eggs."""
from __future__ import annotations
import hashlib

FRAGMENTS = (
    ('DEAD DROP 07', 'The first contract on the board was written on the back of an extraction manifest. Nobody remembers the reward. Everybody remembers who came home.'),
    ('BROKER MEMO 12', 'The market does not ask which flag you fly. It only asks whether your team is still breathing when the dust settles.'),
    ('BLACKSITE // AUDIO LOG', 'A voice on the encrypted line: “We sell spare parts, bad ideas, and second chances. The last one costs the most.”'),
    ('CONTRABAND NOTICE', 'Unmarked crates arrived on the midnight shuttle. The manifest lists coffee, ammunition, and one extremely nervous botanist.'),
    ('HANDWRITTEN NOTE', 'Somewhere on Malevelon Creek, a distress beacon never stopped transmitting. The signal is still on the books.'),
    ('MARKET WATCH', 'Every credit has a record. Every record has a ghost. Some ghosts demand interest.'),
    ('BROKER FILE // XII', 'We do not sell loyalty. We rent equipment to the people who already have it.'),
)
RARE = ('UNLISTED FREQUENCY', 'Unregistered transmission: “If you can hear this, the extraction route is still open. Tell the last one to keep moving.”')


def intel_drop(user_id:int, seed:str='') -> tuple[str,str,bool]:
    digest=hashlib.sha256(f'{user_id}:{seed}'.encode()).digest()
    rare=digest[0]==0  # ~1/256, fun text only, never grants credits or roles
    title,body=RARE if rare else FRAGMENTS[digest[1]%len(FRAGMENTS)]
    return title,body,rare
