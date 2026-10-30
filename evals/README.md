# Open WebUI reasoning eval

This folder contains fictional, self-contained reasoning questions and a runner that sends them to the specified Open WebUI model. The runner does not grade responses. Review each answer against the `review` and `red_flags` fields in the suite.

Run the following in PowerShell on the Windows VDI after the approved commit has been pushed. Keep the output folder outside the Git checkout because the report contains model answers and request text.

```powershell
$repo = 'E:\AI\ollama\OpenWebUI-Monitoring\github'
git -C $repo status --short
git -C $repo pull --ff-only
git -C $repo rev-parse --short HEAD

$out = 'E:\AI\ollama\OpenWebUI-Monitoring\Logic-Eval-Results'
New-Item -ItemType Directory -Path $out -Force | Out-Null
Test-NetConnection 127.0.0.1 -Port 80 | Select-Object ComputerName,RemotePort,TcpTestSucceeded

$runner = Join-Path $repo 'evals\OpenWebUI-Live-Reasoning-Run.py'
$suite = Join-Path $repo 'evals\OpenWebUI-Logic-Reasoning-Round-3-2026-10-30.json'
python -B $runner --suite $suite
python -B $runner --suite $suite --url 'http://127.0.0.1' --model 'curtiss-wright-corporation' --output-dir $out --run --limit 3
```

The `status` command must show no changes before pulling. If it does show changes, stop and inspect them; do not reset or clean the checkout. The network test must show `TcpTestSucceeded : True` before running the live test. The live command prompts for the Open WebUI API key without echoing it. Do not put a key in a command, screenshot, report, or Git. It sends three sequential requests. The `ANSWERED` count means responses were received, not that they were correct. To run all 10 round-three cases later, use the same live command without `--limit 3`.

The runner writes timestamped JSON and HTML reports in `$out`. It makes no Open WebUI or Ollama service changes. A failed technical request stops the run and is reported separately from an incorrect model answer.
