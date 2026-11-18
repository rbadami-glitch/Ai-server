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
