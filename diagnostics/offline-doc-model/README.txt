Open WebUI legacy .doc offline model fix - 2026-11-18

The bundle contains the official en_core_web_sm 3.8.0 model wheel and an
installation/rollback script. The wheel's SHA-256 is:
1932429db727d4bff3deed6b34cfc05df17794f4a52eeb26cf8928f7c1a0fb85

The script finds the Python environment behind the current port 80 Open WebUI
worker, checks Open WebUI and spaCy versions, then installs only the model
wheel offline. It does not restart services or change the server launcher.
It writes OpenWebUI-DocModel-Install.json beside the wheel for rollback.

After installation, upload the original .doc again in a new chat. The failed
file record cannot repair itself. If the real file still fails, inspect its
new processing error before changing anything else.

This immediate fix is in the active uvx environment. A future uvx environment
refresh can discard it. Once the real-file upload succeeds, keep this wheel
on the server and add it as a local --with requirement to the active service
launcher with a backup of that launcher.

Rollback (same active environment):
powershell -NoProfile -ExecutionPolicy Bypass -File .\Install-OpenWebUI-DocModel.ps1 -WheelPath .\en_core_web_sm-3.8.0-py3-none-any.whl -Rollback
