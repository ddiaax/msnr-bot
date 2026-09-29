# -*- coding: utf-8 -*-
"""استراتيجية MSNR: لمس مستوى ← MSS ← أمر معلّق على الـQM (نفس مؤشر MSNR Scalper)"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'msnr_bot'))
import msnr_bot as core

DEFAULTS = dict(core.DEFAULT_CFG)

def run(m1, cfg):
    """m1 = شموع الدقيقة [(t, o, h, l, c)] ← بيرجّع قائمة أحداث (signal / fill / close / cancel)"""
    events, _ = core.run_engine(m1, cfg)
    return events

def message(ev, cfg):
    return core.message(ev, cfg).replace('XAUUSD.s', 'XAUUSD')
