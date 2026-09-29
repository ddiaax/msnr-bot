# -*- coding: utf-8 -*-
"""استراتيجية MSNR Pro: MSS + QM بنقاط توافق (0-11) + دخول CRT مستقل + صفقات A+ (نفس مؤشر MSNR Scalper Pro)"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'msnr_bot'))
import msnr_pro as pro

DEFAULTS = dict(pro.PRO_DEFAULTS)

def run(m1, cfg):
    events, _ = pro.run_engine_pro(m1, cfg)
    return events

def message(ev, cfg):
    return pro.message_pro(ev, cfg)
