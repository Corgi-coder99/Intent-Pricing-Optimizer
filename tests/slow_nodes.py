"""Module-level (picklable) functions to test timeout/crash handling in child processes."""
import os
import time


def sleepy(ctx):
    time.sleep(60)
    return {}


def quick(ctx):
    return {"ok": True}


def crash(ctx):
    os._exit(3)
