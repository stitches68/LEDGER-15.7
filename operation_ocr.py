"""Conservative, offline operation-end screen recognition for Helldivers 2.

Operation summaries and individual post-mission cards are *different formats*.
This module NEVER infers a completed operation from generic numerical results.
It doesn't authenticate an image or guarantee a specific mission took place.
"""
from __future__ import annotations

from io import BytesIO
import re
from PIL import Image, ImageOps, ImageEnhance

PLANET_RE = re.compile(r'(?im)^\s*(?:PLANET|ON PLANET|WORLD)\s*[:\-]\s*([A-Z][A-Z\s\x27\-]{2,48})\s*$')
DIFF_RE = re.compile(r'\bDIFFICULTY\s*[:\-]?\s*(10|[1-9])\b', re.I)
STAGE_RE = re.compile(r'(?m)(?:MISSION|OBJECTIVE|OPERATION)\s*(?:#?\d+\s*[:\-])?\s*(?:COMPLETE[D]?|SUCCESSFUL|ACCOMPLISHED)',re.I)
TOTAL_RE = re.compile(r'\b(\d{1,2})\s*(?:/|OF)\s*(\d{1,2})\s*MISSIONS?\b',re.I)


def assess_operation_text(text: str) -> tuple[str,str,dict]:
    """Parse operation-victory evidence, omitting unreadable details.

    Require operation-specific language + completion context for an automatic PASS.
    One-off 'MISSION COMPLETE' on a normal results page is insufficient.
    """
    if not isinstance(text,str) or len(re.sub(r'\W','',text))<15:
        return 'UNCERTAIN','Operation screen was unreadable; send to a reviewer.',{}
    flat=' '.join(text.upper().split())
    facts={}
    planet=PLANET_RE.search(text)
    if planet:
        facts['planet_visible']=' '.join(planet.group(1).split()).title()
    difficulty=DIFF_RE.search(flat)
    if difficulty:
        facts['difficulty_visible']=int(difficulty.group(1))
    total=TOTAL_RE.search(flat)
    if total:
        value,maximum=int(total.group(1)),int(total.group(2))
        if 1<=maximum<=10 and 0<=value<=maximum:
            facts['missions_completed']=value
            facts['missions_required_visible']=maximum
    operation_context=bool(re.search(r'\b(?:OPERATION|CAMPAIGN|MISSION SET|SQUAD IMPACT)\b',flat))
    victory=bool(re.search(r'\b(?:OPERATION (?:COMPLETE[D]?|SUCCESSFUL|VICTORY)|ALL MISSIONS COMPLETE[D]?|CAMPAIGN (?:COMPLETE[D]?|SUCCESSFUL)|VICTORY)\b',flat))
    defeat=bool(re.search(r'\b(?:OPERATION FAILED|OPERATION LOST|CAMPAIGN FAILED)\b',flat))
    game_context=bool(re.search(r'\b(?:HELLDIVERS?|SUPER EARTH|STRAT[AE]?GEMS?|TERMINIDS?|AUTOMATONS?|ILLUMINATE|SQUAD IMPACT|LIBERAT(?:ION|ED))\b',flat))
    badges=len(STAGE_RE.findall(flat))
    if victory and not defeat and operation_context and (game_context or badges>=2 or total):
        facts['operation_success']=True
        facts['mission_success']=True
        return 'PASS','Operation completion indicators recognized. Screenshot authenticity cannot be verified.',facts
    if defeat and operation_context and game_context:
        facts['operation_success']=False
        return 'FAIL','Operation result explicitly indicates failure; contract cannot auto-pay.',facts
    if operation_context or badges or game_context:
        return 'UNCERTAIN','Operation-related text found, but a reliable operation outcome was not visible.',facts
    return 'FAIL','No recognizable Helldivers operation results interface.',{}


def analyze_operation_sync(photo: bytes) -> tuple[str,str,dict]:
    """Run offline Tesseract against a full-size operation summary screenshot."""
    try:
        import pytesseract
        from ocr_verifier import _configure_tesseract
        if not _configure_tesseract():
            return 'UNAVAILABLE','Tesseract OCR is not installed.',{}
        with Image.open(BytesIO(photo)) as img:
            if img.width * img.height > 28_000_000:
                return 'UNCERTAIN','Image is too large to process.',{}
            rgb=ImageOps.exif_transpose(img).convert('RGB')
            # A full screen helps distinguish operation completion from a normal stat card.
            gray=ImageEnhance.Contrast(ImageOps.grayscale(rgb)).enhance(1.8)
            if gray.width<1200:
                r=min(2,1200/max(gray.width,1))
                gray=gray.resize((round(gray.width*r),round(gray.height*r)),Image.Resampling.LANCZOS)
        content=pytesseract.image_to_string(gray,lang='eng',config='--psm 11',timeout=25)
        return assess_operation_text(content)
    except Exception as exc:
        return 'UNAVAILABLE',f'Operation OCR unavailable ({type(exc).__name__}); staff review required.',{}


def combine_evidence(stats_result: tuple, operation_result: tuple | None, *, mode:str='STATS') -> tuple[str,str,dict]:
    """One operation+stats pair = ONE mission proof (not two progress ticks)."""
    if mode not in ('STATS','OPERATION','BOTH'):
        raise ValueError('Unsupported evidence mode')
    stat_status,stat_reason,stat_data=stats_result
    if mode=='STATS' and operation_result is None:
        return stats_result
    if mode in ('OPERATION','BOTH') and operation_result is None:
        return 'UNCERTAIN','The selected contract requires an operation results screenshot.',stat_data
    operation_status,operation_reason,operation_data=operation_result or ('UNCERTAIN','',{})
    facts={**(stat_data or {})}
    for k,v in (operation_data or {}).items():
        if k not in facts:
            facts[k]=v
    # Preserve input evidence types distinctly for review and diagnostics.
    facts['operation_screen_verified']=operation_status=='PASS'
    if mode=='OPERATION':
        return operation_status, operation_reason, facts
    if stat_status=='PASS' and operation_status=='PASS':
        return 'PASS','Individual stats and operation results recognized. Images are not authenticated.',facts
    if 'FAIL' in (stat_status,operation_status):
        return 'UNCERTAIN','One of the required screenshot types could not be recognized; staff review.',facts
    return 'UNCERTAIN','Both screenshot types are required and at least one could not be read.',facts
