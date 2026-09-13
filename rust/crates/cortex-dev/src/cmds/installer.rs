//! `dev installer build/install/uninstall` — context-menu installers.
//! Darwin paths are exercised locally; Windows/Linux branches mirror dev.py
//! and degrade to platform skips.

use crate::parser::Matches;
use crate::util::echo;
use std::path::{Path, PathBuf};
use std::process::Command;

pub fn build(m: &Matches) {
    let mut target_platforms = m.values("--platform");
    if target_platforms.is_empty() {
        target_platforms.push("all".to_string());
    }
    if target_platforms.iter().any(|p| p == "all") {
        target_platforms = vec!["windows".to_string(), "macos".to_string(), "ubuntu".to_string()];
    }

    let output_dir = m.value_or("--output-dir", "dist");
    let output_path = PathBuf::from(&output_dir);
    let _ = std::fs::create_dir_all(&output_path);

    echo(&format!("Building installers for: {}", target_platforms.join(", ")));
    echo(&format!("Output directory: {}", output_path.display()));

    for platform in &target_platforms {
        let dashes = "-".repeat(30);
        echo(&format!("\\n--- Building {} installer {}", platform, dashes));
        match platform.as_str() {
            "windows" => build_windows_installer(&output_path),
            "macos" => build_macos_installer(&output_path),
            "ubuntu" => build_ubuntu_installer(&output_path),
            _ => {}
        }
    }
}

fn build_windows_installer(_output_dir: &Path) {
    let iss_script = Path::new("installers/windows/inno_setup/cortex_harness.iss");
    if !iss_script.exists() {
        echo(&format!("  [skip] Inno Setup script not found: {}", iss_script.display()));
        echo("  [info] Use 'dev installer install --local' instead");
        return;
    }
    let Some(iscc_cmd) = find_iscc() else {
        echo("  [skip] Inno Setup compiler (ISCC.exe) not found in PATH");
        echo("  [info] For development use: dev installer install --local");
        echo("  [info] For production: Download from https://jrsoftware.org/isdl.php");
        return;
    };
    echo(&format!("  [building] {} {}", iscc_cmd, iss_script.display()));
    match Command::new(&iscc_cmd).arg(iss_script).output() {
        Ok(out) if out.status.success() => echo("  [success] Windows installer created successfully"),
        Ok(out) => echo(&format!(
            "  [error] Build failed: {}",
            String::from_utf8_lossy(&out.stderr)
        )),
        Err(e) => echo(&format!("  [error] Build failed: {}", e)),
    }
}

fn find_iscc() -> Option<String> {
    for candidate in [
        "C:/Program Files (x86)/Inno Setup 6/ISCC.exe",
        "C:/Program Files/Inno Setup 6/ISCC.exe",
        "C:/Program Files (x86)/Inno Setup 5/ISCC.exe",
        "C:/Program Files/Inno Setup 5/ISCC.exe",
    ] {
        if Path::new(candidate).exists() {
            return Some(candidate.to_string());
        }
    }
    None
}

fn build_macos_installer(output_dir: &Path) {
    let build_script = Path::new("installers/macos/build_pkg.sh");
    if !build_script.exists() {
        echo(&format!("  [error] macOS build script not found: {}", build_script.display()));
        return;
    }
    if !cfg!(target_os = "macos") {
        echo("  [skip] macOS installers can only be built on macOS");
        return;
    }
    echo(&format!(
        "  [building] bash {} --output {}",
        build_script.display(),
        output_dir.display()
    ));
    match Command::new("bash")
        .arg(build_script)
        .arg("--output")
        .arg(output_dir)
        .output()
    {
        Ok(out) if out.status.success() => echo("  [success] macOS installer created successfully"),
        Ok(out) => echo(&format!(
            "  [error] Build failed: {}",
            String::from_utf8_lossy(&out.stderr)
        )),
        Err(e) => echo(&format!("  [error] Build failed: {}", e)),
    }
}

fn build_ubuntu_installer(output_dir: &Path) {
    let build_script = Path::new("installers/ubuntu/build_deb.sh");
    if !build_script.exists() {
        echo(&format!("  [error] Ubuntu build script not found: {}", build_script.display()));
        return;
    }
    if !cfg!(target_os = "linux") {
        echo("  [skip] Ubuntu packages can only be built on Linux");
        return;
    }
    echo(&format!(
        "  [building] bash {} --output {}",
        build_script.display(),
        output_dir.display()
    ));
    match Command::new("bash")
        .arg(build_script)
        .arg("--output")
        .arg(output_dir)
        .output()
    {
        Ok(out) if out.status.success() => echo("  [success] Ubuntu package created successfully"),
        Ok(out) => echo(&format!(
            "  [error] Build failed: {}",
            String::from_utf8_lossy(&out.stderr)
        )),
        Err(e) => echo(&format!("  [error] Build failed: {}", e)),
    }
}

pub fn install(m: &Matches) {
    let local = m.flag("--local");
    let project_dir = m.value_or("--project-dir", ".");
    let project_path = super::init::resolve_path(Path::new(&project_dir));

    if cfg!(windows) {
        echo("[error] Windows registry installation is only supported from the Python CLI on Windows");
        std::process::exit(1);
    } else if cfg!(target_os = "macos") {
        install_macos_context_menu(local, &project_path);
    } else if cfg!(target_os = "linux") {
        install_ubuntu_context_menu(local, &project_path);
    } else {
        echo("[error] Unsupported platform");
        std::process::exit(1);
    }
}

fn install_macos_context_menu(local: bool, project_path: &Path) {
    let _ = local;
    echo("macOS context menu installation");
    echo("  [info] Requires Automator workflows in ~/Library/Services/");

    let workflows_src = project_path.join("installers").join("macos").join("workflows");
    let Some(services_dst_parent) = std::env::var("HOME").ok().map(PathBuf::from) else {
        return;
    };
    let services_dst = services_dst_parent.join("Library").join("Services");

    if !workflows_src.exists() {
        echo(&format!("  [error] Workflows directory not found: {}", workflows_src.display()));
        return;
    }
    let _ = std::fs::create_dir_all(&services_dst);

    let workflows = list_glob(&workflows_src, ".workflow");
    if workflows.is_empty() {
        echo("  [warning] No Automator workflows found");
        return;
    }

    for workflow in workflows {
        let Some(name) = workflow.file_name().map(|n| n.to_string_lossy().to_string()) else { continue };
        let dst = services_dst.join(&name);
        if dst.exists() {
            let _ = std::fs::remove_dir_all(&dst);
        }
        if copy_tree(&workflow, &dst) {
            echo(&format!("  [copied] {}", name));
        }
    }

    echo("  [refresh] Reloading system services...");
    match Command::new("/System/Library/CoreServices/pbs").arg("-flush").output() {
        Ok(out) if out.status.success() => echo("  [success] Context menu installed successfully"),
        Ok(_) | Err(_) => {
            echo("  [warning] Could not refresh services");
            echo("  [info] You may need to log out and log back in");
        }
    }
}

fn install_ubuntu_context_menu(local: bool, project_path: &Path) {
    echo("Ubuntu context menu installation");
    echo("  [info] Installing Nautilus scripts");

    let scripts_src = project_path.join("installers").join("ubuntu").join("scripts");
    let home = std::env::var("HOME").map(PathBuf::from).unwrap_or_default();
    let scripts_dst = if local {
        home.join(".local").join("share").join("nautilus").join("scripts").join("CortexHarness")
    } else {
        PathBuf::from("/usr/share/nautilus-scripts/CortexHarness")
    };

    if !scripts_src.exists() {
        echo(&format!("  [error] Scripts directory not found: {}", scripts_src.display()));
        return;
    }
    let _ = std::fs::create_dir_all(&scripts_dst);

    let scripts = list_glob(&scripts_src, ".sh");
    if scripts.is_empty() {
        echo("  [warning] No shell scripts found");
        return;
    }

    for script in scripts {
        let Some(name) = script.file_name().map(|n| n.to_string_lossy().to_string()) else { continue };
        let dst = scripts_dst.join(&name);
        let _ = std::fs::copy(&script, &dst);
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            if let Ok(meta) = std::fs::metadata(&dst) {
                let mut perms = meta.permissions();
                perms.set_mode(0o755);
                let _ = std::fs::set_permissions(&dst, perms);
            }
        }
        echo(&format!("  [copied] {}", name));
    }

    echo("  [success] Context menu installed successfully");
    echo("  [info] Restart Nautilus: nautilus -q");
}

pub fn uninstall(m: &Matches) {
    let local = m.flag("--local");

    if cfg!(target_os = "macos") {
        echo("Removing macOS context menu integration");
        let Some(home) = std::env::var("HOME").ok().map(PathBuf::from) else { return };
        let services_dir = home.join("Library").join("Services");

        let workflows: Vec<PathBuf> = std::fs::read_dir(&services_dir)
            .into_iter()
            .flatten()
            .flatten()
            .map(|e| e.path())
            .filter(|p| {
                p.file_name()
                    .map(|n| {
                        let s = n.to_string_lossy().to_string();
                        s.contains("CortexHarness") && s.ends_with(".workflow")
                    })
                    .unwrap_or(false)
            })
            .collect();

        if workflows.is_empty() {
            echo("  [info] No CortexHarness workflows found");
            return;
        }
        for workflow in workflows {
            let _ = std::fs::remove_dir_all(&workflow);
            echo(&format!(
                "  [removed] {}",
                workflow.file_name().map(|n| n.to_string_lossy()).unwrap_or_default()
            ));
        }
        echo("  [success] Context menu removed successfully");
    } else if cfg!(target_os = "linux") {
        echo("Removing Ubuntu context menu integration");
        let home = std::env::var("HOME").map(PathBuf::from).unwrap_or_default();
        let scripts_dir = if local {
            home.join(".local").join("share").join("nautilus").join("scripts").join("CortexHarness")
        } else {
            PathBuf::from("/usr/share/nautilus-scripts/CortexHarness")
        };
        if !scripts_dir.exists() {
            echo("  [info] No CortexHarness scripts found");
            return;
        }
        let _ = std::fs::remove_dir_all(&scripts_dir);
        echo("  [success] Context menu removed successfully");
    } else if cfg!(windows) {
        echo("[error] Windows registry uninstallation is only supported from the Python CLI on Windows");
        std::process::exit(1);
    }
}

fn list_glob(dir: &Path, suffix: &str) -> Vec<PathBuf> {
    let mut out: Vec<PathBuf> = std::fs::read_dir(dir)
        .into_iter()
        .flatten()
        .flatten()
        .map(|e| e.path())
        .filter(|p| p.to_string_lossy().ends_with(suffix))
        .collect();
    out.sort();
    out
}

fn copy_tree(src: &Path, dst: &Path) -> bool {
    if src.is_dir() {
        if std::fs::create_dir_all(dst).is_err() {
            return false;
        }
        for entry in std::fs::read_dir(src).into_iter().flatten().flatten() {
            let child_src = entry.path();
            let Some(name) = child_src.file_name() else { continue };
            if !copy_tree(&child_src, &dst.join(name)) {
                return false;
            }
        }
        true
    } else {
        std::fs::copy(src, dst).is_ok()
    }
}
