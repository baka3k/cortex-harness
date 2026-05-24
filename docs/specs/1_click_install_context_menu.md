# TECHNICAL SPECIFICATIONS DOCUMENT

**Project:** 1-Click Installer for Folder Context Menu
**Objective:** Create a fully automated (1-click) installer to add custom options to the folder context menus on Windows, macOS, and Ubuntu. Upon selection, the system will execute a predefined script.

---

## 1. SYSTEM OVERVIEW

* **User Behavior:** Double-click the installer file -> Automatic installation (no manual configuration required) -> Right-click any folder -> Select "My Custom Tools" -> Select "Sub-command 1/2" -> The corresponding script is executed, with the folder path passed as an input parameter.
* **Supported Environments:** Windows (File Explorer), macOS (Finder), Ubuntu (Nautilus).
* **Core Requirement:** The installation experience must be a standard 1-click process (via `.exe`, `.pkg`, or `.deb` files).

---

## 2. PLATFORM-SPECIFIC IMPLEMENTATION

### 2.1. Windows (File Explorer)

**A. Mechanism:**

* Interact with the **Windows Registry** to create a Cascading Menu (parent menu containing sub-menus).
* Target Registry Keys: `HKEY_CLASSES_ROOT\Directory\shell` (for folder right-click) and `HKEY_CLASSES_ROOT\Directory\Background\shell` (for right-click on empty space within a folder).
* Command passed to script: `"C:\Program Files\MyCustomTools\scripts\script_name.bat" "%1"` (where `%1` is the folder path).

**B. Packaging (1-Click):**

* **Recommended Tool:** **Inno Setup** (or NSIS).
* **Installer Tasks:**
1. Trigger UAC to request Administrator privileges.
2. Create a directory at `C:\Program Files\MyCustomTools\`.
3. Copy scripts (Payload) to the above directory.
4. Write the necessary Registry keys directly.
5. Provide an `uninstall.exe` file to clean up Registry keys and remove files upon uninstallation.



### 2.2. Ubuntu / Linux (Nautilus)

**A. Mechanism:**

* Utilize the **Nautilus Scripts** feature.
* Target Directory: `~/.local/share/nautilus/scripts/`.
* Directory structures created within this path will automatically transform into a Cascading Menu.
* Environment variables identifying the folder: `$NAUTILUS_SCRIPT_SELECTED_FILE_PATHS` or `$NAUTILUS_SCRIPT_CURRENT_URI`.

**B. Packaging (1-Click):**

* **Recommended Tool:** Package as a **`.deb`** (Debian package) or use **Makeself** to create a self-extracting `.run` file.
* **Installer Tasks:**
1. Execute the installation process.
2. Create the directory `~/.local/share/nautilus/scripts/My Custom Tools`.
3. Copy bash scripts (`.sh`) into the above directory.
4. Apply execution permissions: `chmod +x` for all scripts.
5. Reload Nautilus (Optional: `nautilus -q`).



### 2.3. macOS (Finder)

**A. Mechanism:**

* Use **Quick Actions** (formerly Services) created via **Automator**.
* Input: Workflow receives `current folders` in `Finder`.
* Action: `Run Shell Script` with pass input `as arguments` (`$@`).
* Output: A `.workflow` bundle located in `~/Library/Services/`.

**B. Packaging (1-Click):**

* **Recommended Tool:** Apple's command-line tool **`pkgbuild`** or the **Packages** application.
* **Installer Tasks:**
1. Display the standard macOS installation interface.
2. Copy the `.workflow` folder to `~/Library/Services/` (for the current user) or `/Library/Services/` (system-wide).
3. Run a post-install script to refresh system services: `/System/Library/CoreServices/pbs -flush`.



---

## 3. ERROR HANDLING & EDGE CASES

* **Spaces in Paths:** All scripts receiving folder path variables must be wrapped in double quotes (e.g., `"$1"` or `"%1"`) to prevent errors when folder names contain spaces.
* **Permissions:** Installers on Windows/macOS/Ubuntu must cleanly handle the request for Admin/Sudo privileges from the user if installing to system partitions.
* **Redundant Installation:** The installer must safely overwrite existing files during re-installation without duplicating menu entries.

---

## 4. ACCEPTANCE CRITERIA

1. **Windows:** A `setup.exe` file is provided. Upon execution, right-clicking a folder displays the parent menu "My Custom Tools" -> clicking a sub-command correctly executes the `.bat`/`.py` script without empty path errors.
2. **Ubuntu:** An `install.deb` or `install.sh` file is provided. Upon execution, opening Nautilus and right-clicking a folder displays "Scripts" -> "My Custom Tools". Clicking a sub-command correctly executes the `.sh` script.
3. **macOS:** An `install.pkg` file is provided. Following "Next" steps completes the installation. Opening Finder and right-clicking a folder displays the command under "Quick Actions".
4. **Accurate Parameters:** On all three platforms, the final script must accurately `echo` or identify the absolute path of the folder that was right-clicked.