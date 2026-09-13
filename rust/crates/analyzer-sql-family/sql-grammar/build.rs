fn main() {
    let src_dir = std::path::Path::new("vendor");
    let mut build = cc::Build::new();
    build
        .include(src_dir)
        .flag_if_supported("-Wno-unused-parameter")
        .flag_if_supported("-Wno-unused-but-set-variable")
        .flag_if_supported("-Wno-trigraphs");
    let parser_path = src_dir.join("parser.c");
    build.file(&parser_path);
    let scanner_path = src_dir.join("scanner.c");
    if scanner_path.exists() {
        build.file(&scanner_path);
    }
    build.compile("tree_sitter_sql");
}
