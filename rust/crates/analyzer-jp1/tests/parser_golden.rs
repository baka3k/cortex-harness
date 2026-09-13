//! Golden tests cho jp1 parser/decode — vector sinh từ Python reference
//! (`tools/jp1/parser.py` + `tools/common/legacy_encoding.py`).

use analyzer_jp1::parser::{decode_legacy_bytes, is_jp1_file, parse_jp1_text};
use std::path::Path;

fn fixture_root() -> std::path::PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../../..")
        .canonicalize()
        .expect("repo root")
        .join("tests/fixtures/jp1-analyzer")
}

#[test]
fn decode_utf8_and_cp932() {
    let ascii = decode_legacy_bytes(b"unit=A;\n{ty=j;}").unwrap();
    assert_eq!(ascii.encoding, "utf-8");

    // cp932: 夜間バッチ encode qua CPython (không phải utf-8 hợp lệ)
    let expected_text = "cm=\"夜間バッチ\";\n";
    let data = expected_text.replace('\n', "\r\n").encode_cp932();
    let decoded = decode_legacy_bytes(&data).unwrap();
    assert_eq!(decoded.encoding, "cp932");
    assert_eq!(decoded.text, expected_text.replace('\n', "\r\n"));

    // utf-8-sig
    let bom_data = [0xef, 0xbb, 0xbf].iter().copied().chain(b"unit=X;".iter().copied()).collect::<Vec<u8>>();
    let decoded = decode_legacy_bytes(&bom_data).unwrap();
    assert_eq!(decoded.encoding, "utf-8-sig");
    assert_eq!(decoded.text, "unit=X;");

    // utf-16-le BOM
    let mut utf16 = vec![0xff, 0xfe];
    for unit in "unit=X;".encode_utf16() {
        utf16.extend_from_slice(&unit.to_le_bytes());
    }
    let decoded = decode_legacy_bytes(&utf16).unwrap();
    assert_eq!(decoded.encoding, "utf-16-le");
    assert_eq!(decoded.text, "unit=X;");
}

trait EncodeCp932 {
    fn encode_cp932(&self) -> Vec<u8>;
}

impl EncodeCp932 for str {
    fn encode_cp932(&self) -> Vec<u8> {
        // sinh bằng CPython: '夜間バッチ'.encode('cp932') — golden bytes cứng
        // để test không phụ thuộc Python runtime.
        static GOLDEN: &[u8] = &[
            0x63, 0x6d, 0x3d, 0x22, 0x96, 0xe9, 0x8a, 0xd4, 0x83, 0x6f, 0x83, 0x62, 0x83, 0x60,
            0x22, 0x3b, 0x0d, 0x0a,
        ];
        assert_eq!(self, "cm=\"夜間バッチ\";\r\n");
        GOLDEN.to_vec()
    }
}

#[test]
fn sniff_accepts_jobnet_txt_and_rejects_other() {
    let root = fixture_root();
    assert!(is_jp1_file(&root.join("JC00NIGHT.txt").to_string_lossy()));
    assert!(is_jp1_file(&root.join("JC00KANJI.txt").to_string_lossy()));
    assert!(!is_jp1_file(&root.join("run.sh").to_string_lossy()));
    // .txt nhưng không sniff được
    let tmp = tempfile::tempdir().unwrap();
    let plain = tmp.path().join("plain.txt");
    std::fs::write(&plain, "hello world\nnot a jobnet\n").unwrap();
    assert!(!is_jp1_file(&plain.to_string_lossy()));
}

#[test]
fn parse_jobnet_units_arcs_and_exec_targets() {
    let root = fixture_root();
    let data = std::fs::read(root.join("JC00NIGHT.txt")).unwrap();
    let decoded = decode_legacy_bytes(&data).unwrap();
    let parsed = parse_jp1_text(&decoded.text, "JC00NIGHT.txt", &root);

    // ROOT + FETCH + TRANSFORM + LOAD + LOADSUB + MONTHLY + M1 + M2 = 8 units
    assert_eq!(parsed.units.len(), 8);
    let names: Vec<&str> = parsed.units.iter().map(|u| u.name.as_str()).collect();
    assert_eq!(names, ["ROOT", "FETCH", "TRANSFORM", "LOAD", "LOADSUB", "MONTHLY", "M1", "M2"]);

    // INCLUDES: parent-child (7 units có parent) + NEXT: 3 arcs hợp lệ
    // (FETCH->TRANSFORM, TRANSFORM->LOAD, LOAD->LOADSUB, TRANSFORM->MONTHLY,
    //  M1->M2) — arcs trong scope parent; ar=(f=MISSING,t=ALSONAME) không
    // resolve ⇒ diagnostic.
    let includes = parsed
        .relations
        .iter()
        .filter(|r| r.rel_type == "INCLUDES")
        .count();
    assert_eq!(includes, 7);
    let next: Vec<(&String, &String)> = parsed
        .relations
        .iter()
        .filter(|r| r.rel_type == "NEXT")
        .map(|r| (&r.source_id, &r.target_id))
        .collect();
    assert_eq!(next.len(), 5);

    // CALLS: FETCH(run.sh), TRANSFORM(scripts/transform.sh), LOAD(@BATCHHOME@)
    // M1(scripts/m1.sh), M2(scripts/missing_target.sh) — LOAD + M2 không resolve
    let calls: Vec<(&str, bool)> = parsed
        .relations
        .iter()
        .filter(|r| r.rel_type == "CALLS")
        .map(|r| (r.target_id.as_str(), r.resolved))
        .collect();
    assert!(calls.contains(&("run.sh", true)));
    assert!(calls.contains(&("scripts/transform.sh", true)));
    assert!(calls.contains(&("scripts/m1.sh", true)));
    assert!(calls.contains(&("@BATCHHOME@/load.sh", false)));
    assert!(calls.contains(&("scripts/missing_target.sh", false)));

    // diagnostic cho arc MISSING -> ALSONAME
    assert!(parsed
        .diagnostics
        .iter()
        .any(|d| d.code == "jp1-arc-unresolved"));
    // diagnostics cho exec target không resolve (LOAD + M2)
    assert_eq!(
        parsed
            .diagnostics
            .iter()
            .filter(|d| d.code == "jp1-exec-target-unresolved")
            .count(),
        2
    );
}

#[test]
fn parse_cp932_fixture_decodes_japanese_comments() {
    let root = fixture_root();
    let data = std::fs::read(root.join("JC00KANJI.txt")).unwrap();
    let decoded = decode_legacy_bytes(&data).unwrap();
    assert_eq!(decoded.encoding, "cp932");
    let parsed = parse_jp1_text(&decoded.text, "JC00KANJI.txt", &root);
    assert_eq!(parsed.units.len(), 3);
    assert_eq!(parsed.units[0].comment, "夜間バッチ：日本語コメント入り（cp932）");
    assert_eq!(parsed.units[1].comment, "ファイル取得");
}
