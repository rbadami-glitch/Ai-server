Open WebUI legacy .doc offline model fix - 2026-11-18

The bundle contains the official en_core_web_sm 3.8.0 model wheel and an
installation/rollback script. SHA-256 of the wheel:
1932429db727d4bff3deed6b34cfc05df17794f4a52eeb26cf8928f7c1a0fb85

The script traces the port 80 worker to the active uvx archive, checks its
Lib\site-packages and spaCy 3.8, and stages the wheel offline. It copies only
two model directories after the staged model loads. It does not restart
services or change the server launcher. The exact copied paths are recorded
in OpenWebUI-DocModel-Install.json beside the wheel. Keep that manifest and
the wheel for rollback.

Read-only inspection:
powershell -NoProfile -ExecutionPolicy Bypass -File .\Install-OpenWebUI-DocModel.ps1 -WheelPath .\en_core_web_sm-3.8.0-py3-none-any.whl -InspectOnly

Install:
powershell -NoProfile -ExecutionPolicy Bypass -File .\Install-OpenWebUI-DocModel.ps1 -WheelPath .\en_core_web_sm-3.8.0-py3-none-any.whl

Rollback:
powershell -NoProfile -ExecutionPolicy Bypass -File .\Install-OpenWebUI-DocModel.ps1 -WheelPath .\en_core_web_sm-3.8.0-py3-none-any.whl -Rollback

The failed upload record cannot repair itself. If the original file becomes
available, upload it again in a new chat. A small .doc working does not prove
the original document works; inspect any new error if it still fails.

This immediate fix is in the active uvx cache archive. A future uvx refresh
can discard it. A durable launcher change is separate work after verification.

Legacy-Doc-Processing-Test.doc is a genuine Word 97-2003 OLE document,
248,320 bytes, with 38,936 characters of extracted text. It is copied from
the public Apache POI test fixture test-data/document/53379.doc:
https://github.com/apache/poi/blob/trunk/test-data/document/53379.doc
The test question is: "Which agency is named at the start of this document?"
Expected answer: Medicines Control Agency. This checks a substantial legacy
Word extraction path; it cannot prove the missing original file would process.

Durable service repair (prepared 2026-11-19)
--------------------------------------------
Prepare-OpenWebUI-DocDurability.ps1 and Activate-OpenWebUI-DocDurability.ps1
address two remaining restart risks on the Windows server. The preparation
script checks that port 80 belongs to the active OpenWebUIService NSSM service, saves
the launcher and its shutdown settings under
E:\AI\ollama\OpenWebUI-Monitoring\Temp-OpenWebUI-Durability-20261119, copies
the verified model wheel to E:\apps\open-webui\model-wheels, and builds a
candidate launcher. Stage makes no live launcher or service change.

The candidate retains open-webui@latest and unstructured[doc]. It adds the
local wheel with uvx --with, plus spaCy >=3.8,<3.9 to match the wheel. An uvx
preflight imports and loads the model without starting OpenWebUI and refuses
an unexpected OpenWebUI version. Preflight uses the service's uv cache offline
by default; -AllowNetwork permits a retry if that cache is incomplete.

Activation copies the candidate launcher and sets NSSM AppStopMethodSkip=0
and AppKillProcessTree=1. NSSM documentation says 15 skips every stop method;
0 enables graceful attempts and forceful termination as a last resort.
Activation safely clears only a previously identified old port 80 worker if
the first service stop leaves it behind. It then starts OpenWebUIService and checks
its health and service ownership. VerifyRestart performs one more restart
and requires a new healthy port 80 worker and the previous worker's exit.
Both modes attempt automatic restoration if their verification fails.

The original launcher and registry values remain in the server-only
temporary backup until the operator chooses to remove them. Rollback
restores those exact values and restarts the service. It leaves the copied
wheel in place because the inactive file does not affect the original
launcher. This procedure never changes CWAI-OpenWebUI, the separate OpenWebUI
service, or the database. The separate OpenWebUI service must be stopped and
set to Manual before staging, then remain so during activation; otherwise it
can race for port 80. Its shutdown and startup settings require a separate
backed-up change.

Run preparation modes in order: Inspect, Stage, Preflight. Apply and then
VerifyRestart with the activation script only after Preflight reports
ReadyForActivation=True. If activation or verification fails, inspect the
automatic rollback result; -Mode Rollback is also available while the
manifest is in an active state. Run service-changing modes in Administrator
PowerShell at a time when a brief OpenWebUI interruption is acceptable.
