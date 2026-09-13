//! Build the vendored tree-sitter-perl v1.2.1 grammar (generated parser.c +
//! hand-written scanner.c, byte-identical to the Python wheel
//! `tree-sitter-perl==1.2.1` sdist so parse trees match the reference).

use std::path::PathBuf;

fn main() {
    let vendor = PathBuf::from(std::env::var("CARGO_MANIFEST_DIR").unwrap()).join("vendor/src");
    let mut build = cc::Build::new();
    build
        .include(&vendor)
        .file(vendor.join("parser.c"))
        .file(vendor.join("scanner.c"))
        .warnings(false);
    build.compile("tree_sitter_perl");
    println!("cargo:rerun-if-changed=vendor/src/parser.c");
    println!("cargo:rerun-if-changed=vendor/src/scanner.c");
}
