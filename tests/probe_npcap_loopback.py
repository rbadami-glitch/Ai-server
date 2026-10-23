"""Count Npcap loopback packets during one read-only Ollama version request.

Run only on the AI server after Npcap has been installed for a scoped pilot.
This probe does not save packet data, request bodies, responses, or credentials.
It does not run a model or modify an Ollama service.
"""

import ctypes
import json
import os
import sys
import threading
import time
from pathlib import Path
from urllib.request import ProxyHandler, build_opener


PORT = 11435
URL = f"http://127.0.0.1:{PORT}/api/version"
DEVICE = rb"\Device\NPF_Loopback"


class BpfProgram(ctypes.Structure):
    _fields_ = [("length", ctypes.c_uint), ("instructions", ctypes.c_void_p)]


def load_npcap():
    dll_dir = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "Npcap"
    dll_path = dll_dir / "wpcap.dll"
    if not dll_path.is_file():
        raise RuntimeError(f"Npcap DLL not found: {dll_path}")
    directory_handle = os.add_dll_directory(str(dll_dir))
    try:
        library = ctypes.WinDLL(str(dll_path))
    except OSError:
        directory_handle.close()
        raise
    return library, directory_handle


def configure_api(library):
    library.pcap_open_live.argtypes = [ctypes.c_char_p, ctypes.c_int, ctypes.c_int,
                                       ctypes.c_int, ctypes.c_char_p]
    library.pcap_open_live.restype = ctypes.c_void_p
    library.pcap_compile.argtypes = [ctypes.c_void_p, ctypes.POINTER(BpfProgram),
                                     ctypes.c_char_p, ctypes.c_int, ctypes.c_uint]
    library.pcap_compile.restype = ctypes.c_int
    library.pcap_setfilter.argtypes = [ctypes.c_void_p, ctypes.POINTER(BpfProgram)]
    library.pcap_setfilter.restype = ctypes.c_int
    library.pcap_freecode.argtypes = [ctypes.POINTER(BpfProgram)]
    library.pcap_geterr.argtypes = [ctypes.c_void_p]
    library.pcap_geterr.restype = ctypes.c_char_p
    library.pcap_next_ex.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p),
                                    ctypes.POINTER(ctypes.c_void_p)]
    library.pcap_next_ex.restype = ctypes.c_int
    library.pcap_breakloop.argtypes = [ctypes.c_void_p]
    library.pcap_close.argtypes = [ctypes.c_void_p]


def capture_version():
    library, directory_handle = load_npcap()
    configure_api(library)
    error_buffer = ctypes.create_string_buffer(256)
    handle = library.pcap_open_live(DEVICE, 64, 0, 100, error_buffer)
    if not handle:
        directory_handle.close()
        raise RuntimeError("Could not open Npcap loopback: " +
                           error_buffer.value.decode("utf-8", "replace"))
    try:
        program = BpfProgram()
        expression = f"tcp port {PORT}".encode("ascii")
        if library.pcap_compile(handle, ctypes.byref(program), expression, 1,
                                0xFFFFFFFF) != 0:
            raise RuntimeError(library.pcap_geterr(handle).decode("utf-8", "replace"))
        try:
            if library.pcap_setfilter(handle, ctypes.byref(program)) != 0:
                raise RuntimeError(library.pcap_geterr(handle).decode("utf-8", "replace"))
        finally:
            library.pcap_freecode(ctypes.byref(program))

        stopped = threading.Event()
        result = {"packets": 0, "error": None}

        def count_packets():
            header = ctypes.c_void_p()
            data = ctypes.c_void_p()
            while not stopped.is_set():
                state = library.pcap_next_ex(handle, ctypes.byref(header),
                                             ctypes.byref(data))
                if state == 1:
                    result["packets"] += 1
                elif state < 0 and not stopped.is_set():
                    result["error"] = library.pcap_geterr(handle).decode(
                        "utf-8", "replace")
                    break

        worker = threading.Thread(target=count_packets, daemon=True)
        worker.start()
        try:
            time.sleep(0.2)
            # Ignore proxy environment variables: this must be a direct loopback call.
            opener = build_opener(ProxyHandler({}))
            with opener.open(URL, timeout=5) as response:
                version = json.load(response).get("version", "unknown")
                status = response.status
            time.sleep(0.5)
        finally:
            stopped.set()
            library.pcap_breakloop(handle)
            worker.join(timeout=2)
        if worker.is_alive():
            raise RuntimeError("Capture worker did not stop within two seconds.")
        if result["error"]:
            raise RuntimeError("Npcap read failed: " + result["error"])
        print(f"Direct endpoint: {URL}")
        print(f"HTTP status: {status}; Ollama version: {version}")
        print(f"Matching loopback packets: {result['packets']}")
        print("Only a count was retained; no packet data was saved.")
    finally:
        if "worker" not in locals() or not worker.is_alive():
            library.pcap_close(handle)
        directory_handle.close()


if __name__ == "__main__":
    try:
        capture_version()
    except (OSError, ValueError, RuntimeError) as error:
        print(f"Probe failed: {error}", file=sys.stderr)
        sys.exit(1)
