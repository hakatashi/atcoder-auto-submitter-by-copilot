from dotenv import load_dotenv
import schedule
import re
from time import sleep
import os
from atcodertools.common.logging import logger_io, logger
from atcodertools.codegen.template_engine import render
from onlinejudge_command.main import get_parser as oj_get_parser, run_program as oj_run_program
import requests
import json
from atcoder import get_prompt, get_template
import sys
import shutil

if len(sys.argv) < 3:
  print('Usage: python app.py [contest] [problem index]')
  sys.exit()

contest = sys.argv[1]
problem_id = sys.argv[2]

logger.info(f'Loaded config: contest = {contest}')
logger.info(f'Loaded config: problem id = {problem_id}')

load_dotenv()

GITHUB_TOKEN = os.getenv('GITHUB_TOKEN')
OPENAI_TOKEN = os.getenv('OPENAI_TOKEN')

def get_completions(prompt, token, n):
  data = {
    "prompt": prompt,
    "max_tokens": 500,
    "temperature": 0.3,
    "top_p": 1,
    "n": n,
    "logprobs": 2,
    "stop": ["\n\n\n"],
    "stream": True,
  }

  headers = {
    'Authorization': f'Bearer {token}',
    "OpenAI-Intent": "copilot-ghost",
    "Content-Type": "application/json",
    "Accept": "application/json",
  }

  # COPILOT_COMPLETION = 'https://copilot.githubassets.com/v1/engines/github-multi-stochbpe-cushman-pii/completions'
  # COPILOT_COMPLETION = 'https://copilot-proxy.githubassets.com/v1/engines/github-py-stochbpe-cushman-pii/completions'
  # COPILOT_COMPLETION = 'https://copilot-proxy.githubusercontent.com/v1/engines/github-py-stochbpe-cushman-pii/completions'
  COPILOT_COMPLETION = 'https://api.openai.com/v1/engines/davinci-codex/completions'

  logger.info('Getting completion from OpenAI Codex...')
  with requests.post(COPILOT_COMPLETION, json=data, headers=headers) as req:
    response_data = req.text
  logger.info('Successfully retrieved completion data from OpenAI Codex.')

  print(response_data)

  outputs = {}

  for line in response_data.splitlines():
    if len(line) == 0:
      continue
    json_data = line.removeprefix('data: ')
    if json_data == '[DONE]':
      continue

    data = json.loads(json_data)
    choices = data['choices'] or []

    for choice in choices:
      if choice['index'] not in outputs:
        outputs[choice['index']] = ''

      outputs[choice['index']] += choice['text']

  logger.info(f'Successfully extracted {len(outputs)} candidates from completion.')

  return list(outputs.values())

def get_function(solve_function_definition, output):
  lines = output.splitlines()
  ret = [solve_function_definition.strip()]

  for line in lines:
    if len(ret) == 1 and line == '':
      continue
    if len(line) > 0 and line[0] != ' ' and line[0] != '\t':
      break
    ret.append(line.rstrip())

  return '\n'.join(ret)

def get_fingerprint(func):
  func = re.sub(r'#.+$', '', func, flags=re.M)
  func = re.sub(r'[\s()]', '', func)
  return func

def submit_code(code, execution_log, candidates, choice, contest, problem_id):
  with open('template.py') as f:
    template = f.read()
  execution_log = re.sub(r"'+", "'", execution_log)

  submission = render(template, code=code, execution_log=execution_log, candidates=candidates, choice=choice)
  filename = f'submission{choice}.py'
  with open(filename, 'w') as f:
    f.write(submission)

  args = ['submit', f'https://atcoder.jp/contests/{contest}/tasks/{contest}_{problem_id}',
    filename, '--wait', '0', '--yes']

  parser = oj_get_parser()
  parsed = parser.parse_args(args=args)
  return oj_run_program(parsed, parser=parser)

def download_tests(contest, problem_id):
  url = f'https://atcoder.jp/contests/{contest}/tasks/{contest}_{problem_id}'
  testdir = f'tests/{contest}_{problem_id}'

  if os.path.exists(testdir):
    shutil.rmtree(testdir)
  args = ['download', url, '--directory', testdir]

  parser = oj_get_parser()
  while True:
    logger.info('Downloading test cases...')
    exit_code = oj_run_program(parser.parse_args(args=args), parser=parser)
    if exit_code == 0:
      break
    logger.info('Test case extraction failed. Trying after 0.5s...')
    sleep(0.5)

  logger.info('Test cases downloaded.')

def verify_code(code, execution_log, candidates, choice, contest, problem_id):
  url = f'https://atcoder.jp/contests/{contest}/tasks/{contest}_{problem_id}'

  with open('template.py') as f:
    template = f.read()
  execution_log = re.sub(r"'+", "'", execution_log)

  submission = render(template, code=code, execution_log=execution_log, candidates=candidates, choice=choice)
  filename = f'submission_{contest}_{problem_id}_{choice}.py'
  with open(filename, 'w') as f:
    f.write(submission)

  args = ['test', '--command', f'python {filename}', '--directory', f'tests/{contest}_{problem_id}', '--mle', '50', '--tle', '1']

  parser = oj_get_parser()
  logger.info(f'Verifying candidate {choice}...')
  exit_code = oj_run_program(parser.parse_args(args=args), parser=parser)

  logger.info(f'Verification finished. exit code = {exit_code}')
  return exit_code

def run_without_test(problem_id, contest):
  logger.info(f'job started (contest = {contest}, problem id = {problem_id})')
  en_statement_lines, intro_lines, solve_function_definition, outro_lines = get_template(contest, problem_id)
  prompt, notag_prompt = get_prompt(en_statement_lines, intro_lines, solve_function_definition)
  print(prompt)

  results = get_completions(prompt, OPENAI_TOKEN, 5)
  fingerprints = set()
  all_candidates = []
  candidates = []

  logger.info(f'Generating function and fingerprints...')
  for i, result in enumerate(results):
    func = get_function(solve_function_definition, result)
    fingerprint = get_fingerprint(func)
    all_candidates.append(func)
    if fingerprint not in fingerprints and len(func) < 800:
      candidates.append((i, result))
      fingerprints.add(fingerprint)

  chosen_candidates = candidates[0:1]

  execution_log = logger_io.getvalue()

  for choice, result in chosen_candidates:
    outro = ''.join(outro_lines)
    if 'print' not in result:
      outro = re.sub(r'^(\s*)(solve\(.*\))$', r'\1print(\2)', outro, flags=re.M)
    additional_libraries = ['math', 're', 'bisect', 'collections', 'heapq', 'itertools', 'functools', 'fractions', 'numpy as np', 'numpy']
    header = ''.join(map(lambda l: f'import {l}\n', additional_libraries))
    code = header + notag_prompt + result + outro

    while True:
      exit_code = submit_code(code, execution_log, all_candidates, choice, contest, problem_id)
      if exit_code == 0:
        break
      logger.info('submission failed. Trying after 0.5s...')
      sleep(0.5)

    logger.info('submission succeeded.')

def run_with_test(problem_id, contest, testcases):
  logger.info(f'job started (contest = {contest}, problem id = {problem_id})')
  en_statement_lines, intro_lines, solve_function_definition, outro_lines = get_template(contest, problem_id, translate=True)
  prompt, notag_prompt = get_prompt(en_statement_lines, intro_lines, solve_function_definition)

  while True:
    results = get_completions(prompt, OPENAI_TOKEN, testcases)
    fingerprints = set()
    all_candidates = []
    candidates = []

    logger.info(f'Generating function and fingerprints...')
    for i, result in enumerate(results):
      func = get_function(solve_function_definition, result)
      fingerprint = get_fingerprint(func)
      all_candidates.append(func)
      if fingerprint not in fingerprints and len(func) < 800:
        candidates.append((i, result))
        fingerprints.add(fingerprint)

    download_tests(contest, problem_id)

    for choice, result in candidates:
      outro = ''.join(outro_lines)
      if 'print' not in result:
        outro = re.sub(r'^(\s*)(solve\(.*\))$', r'\1print(\2)', outro, flags=re.M)
      additional_libraries = ['math', 're', 'bisect', 'collections', 'heapq', 'itertools', 'functools', 'fractions', 'numpy as np', 'numpy']
      header = ''.join(map(lambda l: f'import {l}\n', additional_libraries))
      code = header + notag_prompt + result + outro

      execution_log = logger_io.getvalue()
      exit_code = verify_code(code, execution_log, all_candidates, choice, contest, problem_id)
      if exit_code != 0:
        logger.info('Test didn\'t pass. Trying another candidate...')
        continue

      logger.info('Test passed. Submitting the code...')

      while True:
        execution_log = logger_io.getvalue()
        exit_code = submit_code(code, execution_log, all_candidates, choice, contest, problem_id)
        if exit_code == 0:
          break
        logger.info('Submission failed. Trying after 0.5s...')
        sleep(0.5)
      logger.info('Submission succeeded.')
      return 0

def job(problem_id, contest):
  if problem_id == 'a':
    run_with_test(problem_id, contest, testcases=5)
  else:
    run_with_test(problem_id, contest, testcases=20)

logger.info('Waiting for the beginning of the contest...')

schedule.every().day.at("21:00").do(job, problem_id=problem_id, contest=contest)
job(problem_id=problem_id, contest=contest)

while True:
  schedule.run_pending()
  sleep(0.1)

