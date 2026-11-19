import asyncio
import copy
import importlib.util
from pathlib import Path
import unittest

path = Path(__file__).resolve().parents[1] / "openwebui-tools" / "verified_latex_reply_filter.py"
spec = importlib.util.spec_from_file_location("verified_latex_reply_filter", path)
filter_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(filter_module)


class VerifiedLatexReplyFilterTests(unittest.TestCase):
    def test_replaces_hallucinated_answer_and_preserves_file_cards(self):
        output = [
            {"type": "function_call", "call_id": "call-1",
             "name": "convert_attached_document_to_latex"},
            {"type": "function_call_output", "call_id": "call-1",
             "output": [{"type": "input_text", "text": "Fixed-layout LaTeX draft created: verified link"}],
             "files": [{"id": "verified-file", "type": "file"}]},
            {"type": "message", "id": "msg-1", "role": "assistant",
             "content": [{"type": "output_text", "text": "Here is a semantic template: ..."}]},
        ]
        body = {"messages": [{"role": "assistant", "content": "Here is a semantic template: ...",
                              "output": output}]}
        result = asyncio.run(filter_module.Filter().outlet(body))
        message = result["messages"][-1]
        self.assertEqual(message["content"], "Fixed-layout LaTeX draft created: verified link")
        self.assertEqual(message["output"][-1]["content"][0]["text"], message["content"])
        self.assertEqual(message["output"][1]["files"][0]["id"], "verified-file")
        self.assertNotIn("semantic template", str(message))

    def test_does_not_change_ordinary_question(self):
        body = {"messages": [{"role": "assistant", "content": "The answer is 23.",
                              "output": [{"type": "message", "role": "assistant",
                                          "content": [{"type": "output_text", "text": "The answer is 23."}]}]}]}
        original = copy.deepcopy(body)
        self.assertEqual(asyncio.run(filter_module.Filter().outlet(body)), original)

    def test_preserves_tool_failure_instead_of_model_success_claim(self):
        body = {"messages": [{"role": "assistant", "content": "Your file is ready.",
                              "output": [
                                  {"type": "function_call", "call_id": "call-2",
                                   "name": "convert_attached_document_to_latex"},
                                  {"type": "function_call_output", "call_id": "call-2",
                                   "output": [{"type": "input_text", "text": "Conversion failed. No file was created."}]},
                                  {"type": "message", "role": "assistant",
                                   "content": [{"type": "output_text", "text": "Your file is ready."}]},
                              ]}]}
        result = asyncio.run(filter_module.Filter().outlet(body))
        self.assertEqual(result["messages"][-1]["content"], "Conversion failed. No file was created.")

    def test_replaces_legacy_tool_citation_result(self):
        verified = "Fixed-layout LaTeX draft created: [Download ZIP](/verified)"
        body = {"messages": [{
            "role": "assistant", "content": "Here is invented LaTeX code",
            "sources": [{"source": {"name": "private-tool/convert_attached_document_to_latex"},
                         "document": [verified], "tool_result": True}],
            "output": [{"type": "reasoning", "content": []},
                       {"type": "message", "role": "assistant",
                        "content": [{"type": "output_text", "text": "Here is invented LaTeX code"}]}],
        }]}
        result = asyncio.run(filter_module.Filter().outlet(body))
        self.assertEqual(result["messages"][-1]["content"], verified)
        self.assertEqual(result["messages"][-1]["output"][-1]["content"][0]["text"], verified)

    def test_ordinary_retrieval_citation_cannot_replace_reply(self):
        body = {"messages": [{"role": "assistant", "content": "Normal answer",
                              "sources": [{"source": {"name": "uploaded-document"},
                                           "document": ["Document text"], "tool_result": False}]}]}
        self.assertEqual(asyncio.run(filter_module.Filter().outlet(body))["messages"][-1]["content"],
                         "Normal answer")


if __name__ == "__main__":
    unittest.main()
