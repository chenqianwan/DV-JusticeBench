"""Offline lossless transport regressions, without inference or network."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location("compact_transport_test", Path(__file__).with_name("transport_compact.py"))
t = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(t)
REPO = Path(__file__).resolve().parents[3]
CONTRACT_PATH = REPO / "scripts/hallucination_h1_h5_sentence_ids_20261003/contract.py"
c = t.contract_from(CONTRACT_PATH, t.file_sha(CONTRACT_PATH))


class LosslessTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.prepared = self.root / "prepared"
        self.prepared.mkdir()
        self.packed = self.root / "packed"
        self.manifest = {"request_count": 2, "code_sha256": {"sentence_contract.py": t.file_sha(CONTRACT_PATH)}, "shards": {}}
        for index, host in enumerate(("10.33.4.146", "10.33.4.152")):
            answer = (' 事实“引号”。\r\n1. 数额为3.14元！\nhttps://example.com/a.b\n尾句。 ' if index == 0 else '第二模型的不同回答。\n')
            packet = {"case_text": "共同案情。", "question": "共同问题？", "judge_decision": "共同裁判理由。", "answer": answer,
                      "reference_pack": {"evidence": [], "checklists": [], "coverage": {}}}
            messages, delivered = c.build_messages_from_packet(packet)
            row = {"request_id": "id_" + str(index), "global_index": index, "answer": answer,
                   "sentences": delivered["sentence_table"], "messages": messages,
                   "answer_sha256": t.sha(answer.encode()), "packet_sha256": t.sha(messages[1]["content"].encode())}
            name = "requests_" + host.rsplit(".", 1)[-1] + ".jsonl"
            raw = t.canonical(row) + b"\n"
            (self.prepared / name).write_bytes(raw)
            self.manifest["shards"][host] = {"path": name, "count": 1, "sha256": t.sha(raw)}
        (self.prepared / "manifest.json").write_bytes(t.canonical(self.manifest))

    def pack(self):
        return t.pack(self.prepared, self.packed, CONTRACT_PATH)

    def test_roundtrip_is_byte_identical_with_unicode_spacing(self):
        manifest = self.pack()
        self.assertEqual(manifest["context_count"], 1)
        verified = t.reconstruct(self.packed, CONTRACT_PATH)
        self.assertTrue(verified["verified"])
        output = self.root / "out"
        restored = t.reconstruct(self.packed, CONTRACT_PATH, output, ".staging")
        for name in restored["shards"]:
            self.assertEqual((output / (name + ".staging")).read_bytes(), (self.prepared / name).read_bytes())

    def test_original_shard_digest_mismatch_blocks_pack(self):
        path = self.prepared / "requests_146.jsonl"
        path.write_bytes(path.read_bytes() + b" ")
        with self.assertRaises(ValueError):
            self.pack()

    def test_changed_system_context_answer_never_passes(self):
        self.pack()
        path = self.packed / "system.txt"
        path.write_bytes(path.read_bytes() + b"different")
        with self.assertRaisesRegex(ValueError, "System prompt digest"):
            t.reconstruct(self.packed, CONTRACT_PATH)

    def test_compact_tampering_blocks(self):
        self.pack()
        path = self.packed / "compact_146.jsonl"
        path.write_bytes(path.read_bytes() + b" ")
        with self.assertRaisesRegex(ValueError, "Compact shard digest"):
            t.reconstruct(self.packed, CONTRACT_PATH)

    def test_context_tampering_blocks(self):
        self.pack()
        path = self.packed / "contexts.jsonl"
        path.write_bytes(path.read_bytes() + b" ")
        with self.assertRaisesRegex(ValueError, "Context artifact digest"):
            t.reconstruct(self.packed, CONTRACT_PATH)

    def test_existing_output_never_overwritten(self):
        self.pack()
        output = self.root / "out"
        output.mkdir()
        target = output / "requests_146.jsonl.staging"
        target.write_text("sentinel")
        with self.assertRaises(FileExistsError):
            t.reconstruct(self.packed, CONTRACT_PATH, output, ".staging")
        self.assertEqual(target.read_text(), "sentinel")

    def test_existing_pack_never_overwritten(self):
        self.packed.mkdir()
        with self.assertRaisesRegex(ValueError, "Refuse replacing"):
            self.pack()

    def test_contract_digest_change_blocks(self):
        self.manifest["code_sha256"]["sentence_contract.py"] = "0" * 64
        (self.prepared / "manifest.json").write_bytes(t.canonical(self.manifest))
        with self.assertRaisesRegex(ValueError, "contract hash"):
            self.pack()


if __name__ == "__main__":
    unittest.main()
