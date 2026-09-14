#!/usr/bin/env python3
"""Phase-03 go/no-go evidence for porting GLiNER to ONNX (+ eventually Rust `ort`).

The old premise ("GLiNER has no official ONNX export") is stale: gliner 0.2.28 ships
`GLiNER.export_to_onnx(..., quantize=)` and can *load* an exported graph with
`from_pretrained(load_onnx_model=True)`. Before spending effort on a Rust span-decoder
(this architecture needs 6 tensor inputs: input_ids, attention_mask, words_mask,
text_lengths, span_idx, span_mask), measure whether the ONNX path reproduces the
production contract at all.

Both sides go through the SAME production entry point
(`tools.common.entity_extractors.extract_entities_gliner`, including the
`_normalize_entities` span backfill), only the model instance differs. That isolates
graph/decode numerics from preprocessing differences — which is exactly what the Rust
port would have to reproduce.

Usage:
    .venv/bin/python scripts/rust_parity/gliner_onnx_eval.py --limit 10        # pilot
    .venv/bin/python scripts/rust_parity/gliner_onnx_eval.py --limit 200 --int8
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts" / "rust_parity"))
sys.path.insert(0, str(REPO / "code-tiny"))
sys.path.insert(0, str(REPO / "doc-tiny"))

MODEL = "urchade/gliner_large-v2.1"
OUT_DIR = REPO / ".cache" / "embed" / "gliner-large-v21-onnx-fp32"
INT8_DIR = REPO / ".cache" / "embed" / "gliner-large-v21-onnx-int8"
# Corpus phải CHỨA THỰC THỂ. Vòng pilot đầu tiên dùng wiki/ (tài liệu API) và cả
# torch lẫn ORT đều trả 0 entity -> "exact_rate 0/0" là kết quả rỗng, mọi implementation
# đều pass. Nên thứ tự ưu tiên là hai corpus parity đã có entity thật (Digital Key,
# DeerFlow, n8n, Qdrant, FalkorDB ...), wiki/docs chỉ là filler số lượng.
ENTITY_BEARING_DIRS = (
    "scripts/rust_parity/fixtures/doctiny_corpus",
    "scripts/rust_mcp/fixtures/mind_corpus",
)
FILLER_DIRS = ("wiki", "docs")
THRESHOLD = 0.35  # dev wiring passes 0.35 (code default is 0.3)
MAX_PARAGRAPH_CHARS = 500  # dev.py MAX_PARAGRAPH_CHARS
MIN_PARAGRAPH_CHARS = 150
MIN_ENTITIES = 100  # dưới mức này thì phép so sánh là rỗng, không phải bằng chứng


def load_corpus(limit: int, dirs: tuple[str, ...]) -> list[tuple[str, str]]:
    from gen_embed_fixtures import _split_paragraphs

    collected: list[tuple[str, str]] = []
    seen: set[str] = set()
    for base in dirs:
        root = REPO / base
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*")):
            if len(collected) >= limit:
                break
            if not path.is_file() or path.suffix not in (".md", ".txt"):
                continue
            if ".git" in path.parts:
                continue
            try:
                raw = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for index, paragraph in enumerate(_split_paragraphs(raw, MAX_PARAGRAPH_CHARS)):
                if len(paragraph) < MIN_PARAGRAPH_CHARS or paragraph in seen:
                    continue
                seen.add(paragraph)
                collected.append((f"{base}/{path.name}#{index}", paragraph))
                if len(collected) >= limit:
                    break
    return collected


def compare(reference: list[list[dict]], candidate: list[list[dict]], tolerance: float) -> dict:
    total_ref = sum(len(items) for items in reference)
    exact = 0          # same position, same name/type/span, score within tolerance
    score_out = 0
    order_ok = 0
    max_score_delta = 0.0
    missing: list[str] = []
    extra: list[str] = []
    details: list[dict] = []

    for doc_index, (ref_rows, cand_rows) in enumerate(zip(reference, candidate)):
        if [row.get("name") for row in ref_rows] == [row.get("name") for row in cand_rows]:
            order_ok += 1
        for position in range(max(len(ref_rows), len(cand_rows))):
            ref = ref_rows[position] if position < len(ref_rows) else None
            cand = cand_rows[position] if position < len(cand_rows) else None
            if ref and not cand:
                missing.append(f"{doc_index}#{position}:{ref.get('name')}")
                continue
            if cand and not ref:
                extra.append(f"{doc_index}#{position}:{cand.get('name')}")
                continue
            same_shape = (
                ref.get("name") == cand.get("name")
                and ref.get("type") == cand.get("type")
                and ref.get("start_char") == cand.get("start_char")
                and ref.get("end_char") == cand.get("end_char")
            )
            delta = abs(float(ref.get("confidence") or 0) - float(cand.get("confidence") or 0))
            max_score_delta = max(max_score_delta, delta)
            if delta > tolerance:
                score_out += 1
            if same_shape and delta <= tolerance:
                exact += 1
            elif same_shape:
                details.append(
                    {"doc": doc_index, "pos": position, "kind": "score", "delta": round(delta, 6),
                     "entity": ref.get("name"), "type": ref.get("type")}
                )
            else:
                details.append(
                    {"doc": doc_index, "pos": position, "kind": "shape",
                     "reference": {k: ref.get(k) for k in ("name", "type", "start_char", "end_char")},
                     "candidate": {k: cand.get(k) for k in ("name", "type", "start_char", "end_char")}}
                )
    matched = exact
    return {
        "reference_entities": total_ref,
        "exact_match": matched,
        "exact_match_rate": round(matched / max(total_ref, 1), 4),
        "list_order_match_docs": order_ok,
        "docs": len(reference),
        "score_out_of_tolerance": score_out,
        "max_score_delta": round(max_score_delta, 6),
        "missing": len(missing),
        "extra": len(extra),
        "missing_examples": missing[:8],
        "extra_examples": extra[:8],
        "first_details": details[:8],
    }


def run_model(model, texts: list[str], labels: list[str], threshold: float):
    from entity_extractors import extract_entities_gliner

    started = time.perf_counter()
    outputs = []
    per_call: list[float] = []
    for text in texts:
        tick = time.perf_counter()
        entities, _relations = extract_entities_gliner(
            text, labels=labels, model=MODEL, threshold=threshold, gliner_model=model
        )
        outputs.append(entities)
        per_call.append((time.perf_counter() - tick) * 1000.0)
    wall = time.perf_counter() - started
    per_call.sort()
    stats = {
        "wall_seconds": round(wall, 2),
        "texts": len(texts),
        "entities": sum(len(items) for items in outputs),
        "p50_ms": round(statistics.median(per_call), 1),
        "p95_ms": round(per_call[max(int(len(per_call) * 0.95) - 1, 0)], 1),
        "throughput_texts_per_s": round(len(texts) / wall, 2),
    }
    return outputs, stats


PROBE_TEXTS = [
    "Samsung Electronics released Digital Key 3.0 on 12 March 2025 in Seoul.",
    "Deploy DeerFlow on the stock EC2 instance and index it into FalkorDB.",
    "The n8n workflow calls the Qdrant REST API using an API token.",
]


def probe_signature(model) -> list[float]:
    """Scores on fixed probe texts — a fingerprint of the graph actually loaded.

    ORT sessions do not expose their model path, so instead of attribute spelunking
    we falsify: if the "int8" model returns bit-identical scores to fp32, either the
    wrong graph was loaded (`onnx_model_file` vs a swallowed unknown kwarg) or
    quantization had literally no effect. Either way an int8 claim is unsupported.
    """
    from entity_extractors import extract_entities_gliner

    signature: list[float] = []
    for text in PROBE_TEXTS:
        entities, _ = extract_entities_gliner(
            text, labels=list(DEFAULT_LABELS_CACHE), model=MODEL,
            threshold=0.05, gliner_model=model,
        )
        signature.extend(round(float(item.get("confidence") or 0.0), 9) for item in entities)
    return signature


DEFAULT_LABELS_CACHE: list[str] = []


def load_ort(dir_path: Path, onnx_file: str):
    """Load an exported GLiNER graph by its exact file name."""
    from gliner import GLiNER

    model = GLiNER.from_pretrained(
        str(dir_path), load_onnx_model=True, local_files_only=True,
        onnx_model_file=onnx_file,
    )
    size = (dir_path / onnx_file).stat().st_size / 1e6
    print(f"[gliner] loaded {dir_path.name}/{onnx_file} ({size:.0f} MB)")
    return model


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--limit", type=int, default=200)
    parser.add_argument("--int8", action="store_true", help="also export+measure int8")
    parser.add_argument("--out", default=str(REPO / ".cache" / "embed" / "gliner_onnx_eval.json"))
    parser.add_argument("--keep-export", action="store_true")
    args = parser.parse_args()

    import os

    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
    import torch

    torch.set_num_threads(os.cpu_count() or 4)
    from gliner import GLiNER
    from entity_extractors import DEFAULT_GLINER_LABELS  # doc-tiny/entity_extractors.py

    labels = list(DEFAULT_GLINER_LABELS)
    DEFAULT_LABELS_CACHE.extend(labels)
    corpus = load_corpus(args.limit, ENTITY_BEARING_DIRS + FILLER_DIRS)
    if not corpus:
        raise SystemExit("[gliner] empty corpus")
    texts = [text for _, text in corpus]
    print(f"[gliner] docs={len(texts)} labels={labels} threshold={THRESHOLD} "
          f"avg_chars={sum(len(t) for t in texts) // len(texts)}")

    torch_model = GLiNER.from_pretrained(MODEL, local_files_only=True)
    reference, torch_stats = run_model(torch_model, texts, labels, THRESHOLD)
    print(f"[gliner] torch  {torch_stats}")
    del torch_model

    if torch_stats["entities"] < MIN_ENTITIES:
        raise SystemExit(
            f"[gliner] VACUOUS CORPUS: reference chỉ tìm thấy "
            f"{torch_stats['entities']} entity (< {MIN_ENTITIES}). "
            "Một so sánh trên ~0 entity không phải bằng chứng portability — "
            "mở rộng corpus chứa thực thể trước khi kết luận go/no-go."
        )

    if not OUT_DIR.exists() or not (OUT_DIR / "model.onnx").is_file():
        print(f"[gliner] exporting fp32 opset=19 -> {OUT_DIR}")
        exporter = GLiNER.from_pretrained(MODEL, local_files_only=True)
        started = time.perf_counter()
        paths = exporter.export_to_onnx(str(OUT_DIR), quantize=False, opset=19)
        print(f"[gliner] export took {time.perf_counter() - started:.1f}s -> {paths}")
        del exporter
    else:
        print(f"[gliner] reusing existing export {OUT_DIR}")

    onnx_model = load_ort(OUT_DIR, "model.onnx")
    fp32_sig = probe_signature(onnx_model)
    payload_graphs = {"fp32": f"{OUT_DIR.name}/model.onnx", "fp32_probe": fp32_sig[:6]}
    onnx_out, onnx_stats = run_model(onnx_model, texts, labels, THRESHOLD)
    print(f"[gliner] onnx   {onnx_stats}")
    fp32 = compare(reference, onnx_out, tolerance=1e-3)
    print(f"[gliner] FP32 contract: exact_rate={fp32['exact_match_rate']} "
          f"max_score_delta={fp32['max_score_delta']} missing={fp32['missing']} extra={fp32['extra']}")
    del onnx_model

    payload: dict = {
        "model": MODEL,
        "threshold": THRESHOLD,
        "labels": labels,
        "corpus": {"docs": len(texts),
                   "dirs": list(ENTITY_BEARING_DIRS) + list(FILLER_DIRS),
                   "reference_entities": torch_stats["entities"],
                   "entity_bearing_docs": sum(1 for items in reference if items),
                   "max_paragraph_chars": MAX_PARAGRAPH_CHARS},
        "latency": {"torch": torch_stats, "onnx_fp32": onnx_stats},
        "loaded_graphs": payload_graphs,
        "fp32": fp32,
        "ort_providers": None,
    }

    if args.int8:
        import onnxruntime as ort

        payload["ort_providers"] = ort.get_available_providers()
        if not INT8_DIR.exists() or not (INT8_DIR / "model_quantized.onnx").is_file():
            print(f"[gliner] exporting int8 -> {INT8_DIR}")
            exporter = GLiNER.from_pretrained(MODEL, local_files_only=True)
            started = time.perf_counter()
            paths = exporter.export_to_onnx(
                str(INT8_DIR), quantize=True, opset=19,
                quantized_filename="model_quantized.onnx",
            )
            print(f"[gliner] int8 export took {time.perf_counter() - started:.1f}s -> {paths}")
            del exporter
        try:
            int8_model = load_ort(INT8_DIR, "model_quantized.onnx")
            int8_sig = probe_signature(int8_model)
            payload_graphs["int8"] = f"{INT8_DIR.name}/model_quantized.onnx"
            payload_graphs["int8_probe"] = int8_sig[:6]
            if int8_sig == fp32_sig:
                raise SystemExit(
                    "[gliner] int8 probe == fp32 probe bit-for-bit: graph quantized "
                    "không thực sự được dùng (hoặc quantize không thay đổi gì) — "
                    "không được phép kết luận gì về int8"
                )
            int8_out, int8_stats = run_model(int8_model, texts, labels, THRESHOLD)
            payload["latency"]["onnx_int8"] = int8_stats
            payload["int8"] = compare(reference, int8_out, tolerance=1e-3)
            print(f"[gliner] INT8 {int8_stats} exact_rate={payload['int8']['exact_match_rate']}")
            del int8_model
        except Exception as exc:
            payload["int8"] = {"error": f"{type(exc).__name__}: {exc}"}
            print(f"[gliner] INT8 failed: {type(exc).__name__}: {exc}")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"[gliner] wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
