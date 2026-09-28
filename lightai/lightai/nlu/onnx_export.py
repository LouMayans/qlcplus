"""ONNX export + INT8 dynamic quantization helpers (train-side; imports torch lazily)."""

from __future__ import annotations

import sys
from pathlib import Path


def torch_onnx_export(module, args: tuple, path: Path, input_names: list, output_names: list, dynamic_axes: dict) -> None:
    import torch

    kwargs = dict(
        input_names=input_names,
        output_names=output_names,
        dynamic_axes=dynamic_axes,
        opset_version=17,
        do_constant_folding=True,
    )
    try:
        torch.onnx.export(module, args, str(path), dynamo=False, **kwargs)
    except TypeError:
        torch.onnx.export(module, args, str(path), **kwargs)


def export_encoder(model_id: str, out_dir: Path, sample_text: str = "slow blue wash breathing at 60 bpm") -> Path:
    """Export a Hugging Face encoder (last_hidden_state) to out_dir/model.onnx with its tokenizer."""
    onnx_path = out_dir / "model.onnx"
    if onnx_path.exists() and (out_dir / "tokenizer.json").exists():
        return onnx_path
    out_dir.mkdir(parents=True, exist_ok=True)
    import torch
    from transformers import AutoModel, AutoTokenizer

    tok = AutoTokenizer.from_pretrained(model_id)
    tok.save_pretrained(out_dir)
    model = AutoModel.from_pretrained(model_id).eval()
    enc = tok(sample_text, return_tensors="pt", padding="max_length", max_length=32, truncation=True)
    names = [k for k in ("input_ids", "attention_mask", "token_type_ids") if k in enc]

    class Wrapper(torch.nn.Module):
        def __init__(self, inner):
            super().__init__()
            self.inner = inner

        def forward(self, *xs):
            kw = dict(zip(names, xs))
            return self.inner(**kw).last_hidden_state

    dyn = {n: {0: "batch", 1: "seq"} for n in names}
    dyn["last_hidden_state"] = {0: "batch", 1: "seq"}
    with torch.no_grad():
        torch_onnx_export(Wrapper(model), tuple(enc[k] for k in names), onnx_path, names, ["last_hidden_state"], dyn)
    return onnx_path


def quantize_int8(fp32_path: Path, int8_path: Path | None = None) -> Path:
    int8_path = int8_path or fp32_path.with_name(fp32_path.stem + ".int8.onnx")
    if int8_path.exists():
        return int8_path
    from onnxruntime.quantization import QuantType, quantize_dynamic

    try:
        from onnxruntime.quantization.shape_inference import quant_pre_process

        pre = fp32_path.with_name(fp32_path.stem + ".pre.onnx")
        quant_pre_process(str(fp32_path), str(pre), skip_symbolic_shape=True)
        src = pre
    except Exception as exc:
        print(f"[quantize] pre-process skipped: {type(exc).__name__}: {exc}", file=sys.stderr)
        src = fp32_path
    # per-channel weight scales, and the two small output heads stay in float: v4 lost 2.3 slot-F1 points with
    # per-tensor INT8 on every layer; this keeps the speed of the INT8 encoder without that loss
    try:
        import onnx

        heads = [n.name for n in onnx.load(str(src)).graph.node if "_head/" in n.name]
    except Exception:
        heads = []
    quantize_dynamic(str(src), str(int8_path), weight_type=QuantType.QInt8, per_channel=True, nodes_to_exclude=heads)
    if src != fp32_path:
        try:
            src.unlink()
        except OSError:
            pass
    return int8_path
