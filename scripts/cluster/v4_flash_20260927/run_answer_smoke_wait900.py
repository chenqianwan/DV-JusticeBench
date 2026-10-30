#!/usr/bin/env python3
"""Isolated 900-second wait adapter; original runner and receipts stay unchanged."""
from pathlib import Path

import run_answer_smoke as base

_OriginalRunner = base.Runner
base.CONFIG = dict(base.CONFIG, timeout_s=900,
                   run_id='pkulaw_v4_answer_smoke_20260927_wait900')
base.ROOT = Path('/aifs4su/hansirui_3rd/chenlong/dvjustice_answering_smoke_20260927_wait900')


class Runner(_OriginalRunner):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # The base identity already fingerprints the original runner and frozen input.
        self.identity['adapter_sha256'] = base.digest(Path(__file__).read_bytes())


base.Runner = Runner


if __name__ == '__main__':
    base.main()
