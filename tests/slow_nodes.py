"""Hàm cấp module (pickle được) để test timeout/crash trong tiến trình con."""
import os
import time


def sleepy(ctx):
    time.sleep(60)
    return {}


def quick(ctx):
    return {"ok": True}


def crash(ctx):
    os._exit(3)
