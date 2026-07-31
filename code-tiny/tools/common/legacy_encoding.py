from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class LegacyEncodingDiagnostic:
    code: str
    message: str
    severity: str = "info"


@dataclass(frozen=True)
class LegacyTextResult:
    text: str
    encoding: str
    diagnostics: tuple[LegacyEncodingDiagnostic, ...]


def _result(text: str, encoding: str, *, lossy: bool = False) -> LegacyTextResult:
    diagnostics = [
        LegacyEncodingDiagnostic(
            code="legacy-encoding-selected",
            message=f"Decoded source as {encoding}",
        )
    ]
    if lossy:
        diagnostics.append(
            LegacyEncodingDiagnostic(
                code="legacy-encoding-lossy",
                message="Source required replacement characters during decoding",
                severity="warning",
            )
        )
    return LegacyTextResult(text, encoding, tuple(diagnostics))


def decode_legacy_bytes(data: bytes) -> LegacyTextResult:
    if data.startswith(b"\xff\xfe"):
        return _result(data[2:].decode("utf-16-le"), "utf-16-le")
    if data.startswith(b"\xfe\xff"):
        return _result(data[2:].decode("utf-16-be"), "utf-16-be")
    if data.startswith(b"\xef\xbb\xbf"):
        return _result(data.decode("utf-8-sig"), "utf-8-sig")

    sample = data[:512]
    if sample:
        odd_nuls = sample[1::2].count(0)
        even_nuls = sample[0::2].count(0)
        threshold = max(2, len(sample) // 8)
        if odd_nuls >= threshold:
            return _result(data.decode("utf-16-le"), "utf-16-le")
        if even_nuls >= threshold:
            return _result(data.decode("utf-16-be"), "utf-16-be")

    try:
        text = data.decode("utf-8")
        if text.encode("utf-8") == data:
            return _result(text, "utf-8")
    except UnicodeDecodeError:
        pass

    try:
        return _result(data.decode("cp932"), "cp932")
    except UnicodeDecodeError:
        return _result(data.decode("cp1252", errors="replace"), "cp1252", lossy=True)


def read_legacy_text(path: str | Path) -> LegacyTextResult:
    return decode_legacy_bytes(Path(path).read_bytes())