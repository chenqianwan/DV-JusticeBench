#!/usr/bin/env python3
"""Parse the exact service argv using installed vLLM, without starting inference."""
import importlib.util
import json
from pathlib import Path
import sys


def main():
    path = Path(__file__).resolve().with_name('launch_service.py')
    spec = importlib.util.spec_from_file_location('dvj_launch_validation', path)
    launcher = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(launcher)
    from vllm.entrypoints.openai.cli_args import make_arg_parser, validate_parsed_serve_args
    from vllm.utils.argparse_utils import FlexibleArgumentParser
    argv = launcher.service_args()[4:]
    parser = make_arg_parser(FlexibleArgumentParser(description='Offline full-argv check'))
    args = parser.parse_args(argv)
    validate_parsed_serve_args(args)
    assert args.tensor_parallel_size == 8
    assert args.enable_log_requests is False
    assert args.model == str(launcher.MODEL)
    assert args.max_model_len == 32768
    assert args.max_num_seqs == 128
    assert args.port == 18044
    print(json.dumps({'status': 'passed', 'argv': argv, 'log_requests': args.enable_log_requests,
                      'gpu_inference_started': False, 'python': sys.executable}))


if __name__ == '__main__':
    main()
