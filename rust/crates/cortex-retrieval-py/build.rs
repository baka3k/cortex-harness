//! PyO3 extension module linking — trên macOS linker từ chối undefined
//! symbols cho cdylib trừ khi `-undefined dynamic_lookup` (module được load
//! BÊN TRONG process Python nên symbol do interpreter cung cấp lúc runtime).
//! Linux ELF cho phép undefined mặc định nên không cần nhánh nào.

fn main() {
    println!("cargo:rerun-if-env-changed=CARGO_CFG_TARGET_OS");
    let target_os = std::env::var("CARGO_CFG_TARGET_OS").unwrap_or_default();
    if target_os == "macos" {
        println!("cargo:rustc-link-arg=-undefined");
        println!("cargo:rustc-link-arg=dynamic_lookup");
    }
}
