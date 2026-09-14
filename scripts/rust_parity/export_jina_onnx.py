#!/usr/bin/env python3
"""Export the base (adapter-free) jina-embeddings-v3 encoder to ONNX for the Rust
`ort` embedder.

Why this script exists: the official ``jinaai/jina-embeddings-v3`` ``onnx/model.onnx``
cannot be used for parity. Its graph takes a *required* scalar ``task_id`` input that
the exporter wired into ``token_type_embeddings`` as a 5-row task-embedding table, so
there is no way to express "no LoRA adapter" — which is exactly what every Python path
in this repo does (``custom_st.Transformer.forward`` only builds ``adapter_mask`` when a
``task`` is passed, and no caller passes one). Measured best cosine over task_id 0..4
was 0.9418 against the reference vectors; gate is 0.999.

Loading ``XLMRobertaLoRA`` and exporting without ``adapter_mask`` traces the
``task_id is None`` branch of the monkeypatched layer forwards
(``weights = self.weight``), i.e. the base graph — matching production numerics.

Gate 1a (run with --verify): the exported graph replayed through ``onnxruntime`` on CPU
must reach cosine >= 0.9999 against the torch reference before any Rust work starts, so
an export defect is never mistaken for a port defect.

Usage:
    .venv/bin/python scripts/rust_parity/export_jina_onnx.py --verify
    .venv/bin/python scripts/rust_parity/export_jina_onnx.py --out .cache/embed/jina-v3-onnx-fp32
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DEFAULT_OUT = REPO / ".cache" / "embed" / "jina-v3-onnx-fp32"
MODEL = "jinaai/jina-embeddings-v3"

# Corpus deliberately mixes the shapes the real lanes produce: short symbol docs,
# long file-spanning docs, query strings, and non-ASCII.
SAMPLE_TEXTS = [
    "def parse_payload(node):\n    return {'id': node['id'], 'name': node.name}\n",
    "class VectorStore:\n    def __init__(self, path): self.path = path\n",
    "SELECT id, name FROM users WHERE created_at > :cutoff ORDER BY id",
    "func (s *Server) Handle(ctx context.Context) error {\n\treturn s.dispatch(ctx)\n}\n",
    "HOÀN KIẾM Việt Nam — đại diện truy vấn cho tìm kiếm tài liệu chứng cứ 🚀",
    "async def fetch(url):\n    async with session.get(url) as r:\n        return await r.json()\n"
    * 30,
    "// SPDX-License-Identifier: MIT\n#include <stdio.h>\nint main(void){return 0;}\n" * 20,
    "message: sender | receiver | payload | explanation of the topology edge",
]

VERIFY_COSINE = 0.9999


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resolve_source(explicit: str | None) -> str:
    from huggingface_hub import snapshot_download

    for candidate in (explicit, os.environ.get("CODE_EMBEDDING_MODEL_PATH")):
        if candidate and Path(candidate).expanduser().is_dir():
            return str(Path(candidate).expanduser())
    return snapshot_download(MODEL)


def load_base_encoder(source: str):
    """XLMRobertaLoRA in fp32, eval, with the LoRA path unreachable at trace time."""
    import torch
    from transformers import AutoConfig, AutoModel

    config = AutoConfig.from_pretrained(source, trust_remote_code=True)
    model = AutoModel.from_pretrained(source, config=config, trust_remote_code=True)
    model = model.to("cpu").float().eval()
    for param in model.parameters():
        param.requires_grad_(False)
    torch.set_grad_enabled(False)
    return model, config


class EncoderGraph:
    """Adapter-free forward, hiding whether the wrapper returns a dataclass or tuple."""

    def __init__(self, module):
        self.module = module

    def __call__(self, input_ids, attention_mask):
        out = self.module(input_ids=input_ids, attention_mask=attention_mask)
        hidden = getattr(out, "last_hidden_state", None)
        if hidden is None:  # tuple output: (sequence_output, pooled_output, ...)
            hidden = out[0]
        return hidden


def export(source: str, out_dir: Path, opset: int, dynamo: bool) -> Path:
    import torch
    from torch import nn

    model, config = load_base_encoder(source)
    graph = EncoderGraph(model)

    class Wrapper(nn.Module):
        def __init__(self):
            super().__init__()
            self.encoder = graph

        def forward(self, input_ids, attention_mask):
            return self.encoder(input_ids, attention_mask)

    wrapper = Wrapper().eval()
    ids = torch.arange(16, dtype=torch.int64).reshape(2, 8) + 2
    mask = torch.ones(2, 8, dtype=torch.int64)

    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / "model.onnx"
    kwargs = dict(
        input_names=["input_ids", "attention_mask"],
        output_names=["last_hidden_state"],
        opset_version=opset,
        dynamo=dynamo,
    )
    if dynamo:
        kwargs["dynamic_shapes"] = {
            "input_ids": {0: "batch_size", 1: "sequence_length"},
            "attention_mask": {0: "batch_size", 1: "sequence_length"},
        }
        kwargs["external_data"] = True
    else:
        kwargs["dynamic_axes"] = {
            "input_ids": {0: "batch_size", 1: "sequence_length"},
            "attention_mask": {0: "batch_size", 1: "sequence_length"},
            "last_hidden_state": {0: "batch_size", 1: "sequence_length"},
        }
    print(f"[export] dynamo={dynamo} opset={opset} hidden={config.hidden_size} "
          f"layers={config.num_hidden_layers}")
    torch.onnx.export(wrapper, (ids, mask), str(target), **kwargs)
    if not target.exists() or target.stat().st_size == 0:
        raise SystemExit("[export] produced an empty graph")
    return target


def write_metadata(target: Path, out_dir: Path, source: str, opset: int, dynamo: bool) -> Path:
    import onnx

    graph = onnx.load(str(target), load_external_data=False)
    inputs = {
        i.name: [
            d.dim_value if d.HasField("dim_value") else d.dim_param
            for d in i.type.tensor_type.shape.dim
        ]
        for i in graph.graph.input
    }
    meta = {
        "model": MODEL,
        "source": source,
        "opset": opset,
        "exporter": "torch.onnx.export",
        "dynamo": dynamo,
        "inputs": inputs,
        "outputs": [o.name for o in graph.graph.output],
        "hidden_size": 1024,
        "max_token_length": 8194,
        "pooling": "mean",
        "normalize": True,
        "lora": "disabled (adapter_mask never passed)",
        "files": {
            p.name: {"bytes": p.stat().st_size, "sha256": sha256(p)}
            for p in sorted(out_dir.iterdir())
            if p.is_file() and p.name != "metadata.json"
        },
    }
    path = out_dir / "metadata.json"
    path.write_text(json.dumps(meta, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def verify(target: Path, source: str, threads: int) -> int:
    """Gate 1a: exported graph replayed by onnxruntime vs the torch reference."""
    import numpy as np
    import onnxruntime as ort
    import torch

    sys.path.insert(0, str(REPO / "code-tiny" / "tools" / "common"))
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    import embed_runtime as er
    from transformers import AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(source, trust_remote_code=True)
    reference = er.get_sentence_transformer(MODEL, device="cpu").encode(
        SAMPLE_TEXTS, batch_size=8, normalize_embeddings=True, convert_to_numpy=True
    )

    options = ort.SessionOptions()
    options.intra_op_num_threads = threads
    options.inter_op_num_threads = 1
    options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    session = ort.InferenceSession(
        str(target), options, providers=["CPUExecutionProvider"]
    )
    names = [i.name for i in session.get_inputs()]
    if "task_id" in names:
        print(f"[verify] FAIL exported graph still requires task_id: {names}")
        return 1
    print(f"[verify] graph inputs={names} outputs={[o.name for o in session.get_outputs()]}")

    torch.set_num_threads(threads)
    worst, worst_i = 2.0, -1
    for index, text in enumerate(SAMPLE_TEXTS):
        encoded = tokenizer(text)
        feed = {
            "input_ids": np.asarray([encoded["input_ids"]], dtype=np.int64),
            "attention_mask": np.asarray([encoded["attention_mask"]], dtype=np.int64),
        }
        hidden = session.run(None, feed)[0][0]
        keep = np.asarray(feed["attention_mask"][0], dtype=np.float32)[:, None]
        pooled = (hidden * keep).sum(0) / max(float(keep.sum()), 1e-9)
        cosine = float(
            np.dot(pooled, reference[index])
            / (np.linalg.norm(pooled) * np.linalg.norm(reference[index]))
        )
        print(f"[verify] {index} tokens={len(encoded['input_ids']):5} cosine={cosine:.6f}")
        if cosine < worst:
            worst, worst_i = cosine, index
    verdict = "PASS" if worst >= VERIFY_COSINE else "FAIL"
    print(f"[verify] worst cosine={worst:.6f} (case {worst_i}) gate={VERIFY_COSINE} -> {verdict}")
    return 0 if verdict == "PASS" else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--source", default=None, help="model dir/rev (default: HF cache)")
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument(
        "--opset",
        type=int,
        default=18,
        help="torch 2.13 dynamo emits Split with num_outputs (added in opset 18); "
        "opset 17 makes onnxruntime reject the graph as InvalidGraph",
    )
    parser.add_argument(
        "--legacy-exporter",
        action="store_true",
        help="use torch.onnx.export dynamo=False instead of the dynamo exporter",
    )
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--verify", action="store_true", help="run gate 1a")
    parser.add_argument("--clean", action="store_true", help="wipe the output dir first")
    args = parser.parse_args()

    out_dir = Path(args.out).expanduser()
    if args.clean and out_dir.exists():
        shutil.rmtree(out_dir)
    source = resolve_source(args.source)
    target = export(source, out_dir, args.opset, not args.legacy_exporter)
    meta = write_metadata(target, out_dir, source, args.opset, not args.legacy_exporter)
    total = sum(p.stat().st_size for p in out_dir.iterdir() if p.is_file())
    print(f"[export] wrote {target} ({total / 1e9:.3f} GB total) + {meta.name}")
    if args.verify:
        return verify(target, source, args.threads)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
