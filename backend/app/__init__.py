"""
VeriShield AI.

This module is deliberately not empty. It sets one environment variable, and it
has to happen here because here is the earliest point that runs before any
submodule imports a protobuf-based library.

The problem it solves
---------------------
PaddleOCR and onnxruntime (which insightface needs) disagree about protobuf.
Paddle's generated bindings were produced by protoc 3.x and are rejected
outright by the protobuf 4+ C++ backend that onnxruntime pulls in:

    TypeError: Descriptors cannot be created directly.

Installing face recognition therefore breaks text recognition, silently, in a
way that only shows up when a document is actually processed. Measured, not
hypothetical: OCR went from working to raising on every image the moment
insightface was installed.

Pinning protobuf down to satisfy Paddle breaks onnxruntime instead, so there is
no single version that satisfies both. The pure-Python protobuf implementation
accepts both sets of generated code. It parses more slowly, but that cost is
paid once at model load, not per document -- and a slower pipeline is
categorically better than one where installing a feature disables another
without saying so.
"""

import os

# Must be set before protobuf is first imported anywhere in the process.
os.environ.setdefault("PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION", "python")
