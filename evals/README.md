# Open WebUI reasoning eval runner

`OpenWebUI-Live-Reasoning-Run.py` sends synthetic questions from a JSON suite to a specified Open WebUI model. Pass `--suite` to choose the suite. Without `--run`, it only validates and lists the cases; with `--run`, it makes model requests and saves JSON and HTML reports. The runner does not grade answers.

Keep report files outside the Git checkout because they contain model answers and request text. Enter the Open WebUI API key at the hidden prompt; do not put it in a command, report, or Git commit. The runner does not change Open WebUI or Ollama settings.

Example on the AI server, after placing a reviewed synthetic suite under `evals`:

```powershell
$repo = 'E:\AI\ollama\OpenWebUI-Monitoring\github'
$out = 'E:\AI\ollama\OpenWebUI-Monitoring\Logic-Eval-Results'
$suite = Join-Path $repo 'evals\your-reviewed-suite.json'
$runner = Join-Path $repo 'evals\OpenWebUI-Live-Reasoning-Run.py'
New-Item -ItemType Directory -Path $out -Force | Out-Null
python -B $runner --suite $suite
python -B $runner --suite $suite --url 'http://127.0.0.1' --model 'curtiss-wright-corporation' --output-dir $out --run
```

Check that Open WebUI is reachable on `127.0.0.1:80` before the live command. `ANSWERED` counts responses received, not correct answers. Review each response against the suite's expected results before making a change.
