#!/usr/bin/env python3
"""Windows Registry manager for CortexHarness context menu integration."""

import sys
import winreg
from pathlib import Path
from typing import List, Dict, Any, Optional
import ctypes


class WindowsRegistryManager:
    """Manages Windows Registry operations for context menu integration."""

    # Registry key constants
    HKEY_CLASSES_ROOT = winreg.HKEY_CLASSES_ROOT
    DIRECTORY_SHELL = r"Directory\shell"
    DIRECTORY_BG_SHELL = r"Directory\Background\shell"
    DRIVE_SHELL = r"Drive\shell"

    # Registry value types
    REG_SZ = winreg.REG_SZ
    REG_EXPAND_SZ = winreg.REG_EXPAND_SZ

    def __init__(self, menu_name: str = "CortexHarness"):
        """Initialize Windows Registry manager.

        Args:
            menu_name: Name for the context menu (default: "CortexHarness")
        """
        self.menu_name = menu_name
        self.base_key_path = f"{self.DIRECTORY_SHELL}\\{menu_name}"
        self.base_bg_key_path = f"{self.DIRECTORY_BG_SHELL}\\{menu_name}"
        self.drive_key_path = f"{self.DRIVE_SHELL}\\{menu_name}"

    def is_admin(self) -> bool:
        """Check if the current process has administrator privileges.

        Returns:
            True if running with admin privileges, False otherwise
        """
        try:
            return ctypes.windll.shell32.IsUserAnAdmin()
        except Exception:
            return False

    def request_admin(self) -> bool:
        """Request administrator privileges by restarting with elevation.

        Returns:
            True if elevation was successful, False otherwise
        """
        if self.is_admin():
            return True

        try:
            # Get current executable and arguments
            executable = sys.executable
            args = ' '.join(sys.argv)

            # Request elevation
            params = ' '.join([executable, args])
            ctypes.windll.shell32.ShellExecuteW(
                None, "runas", executable, params, None, 1
            )
            return True

        except Exception as e:
            print(f"Failed to request administrator privileges: {e}")
            return False

    def create_context_menu(
        self,
        commands: List[Dict[str, str]],
        install_path: Path,
        icon_path: Optional[Path] = None
    ) -> bool:
        """Create complete context menu structure in Windows Registry.

        Args:
            commands: List of command dictionaries with 'name', 'action', 'description'
            install_path: Path where CortexHarness is installed
            icon_path: Optional path to icon file for menu items

        Returns:
            True if creation was successful, False otherwise
        """
        try:
            # Create main menu entries
            self._create_main_menu(self.base_key_path, commands, install_path, icon_path)
            self._create_main_menu(self.base_bg_key_path, commands, install_path, icon_path)
            self._create_main_menu(self.drive_key_path, commands, install_path, icon_path)

            return True

        except Exception as e:
            print(f"Error creating context menu: {e}")
            return False

    def _create_main_menu(
        self,
        base_key_path: str,
        commands: List[Dict[str, str]],
        install_path: Path,
        icon_path: Optional[Path]
    ):
        """Create main menu entry and subcommands."""
        # Create main menu key
        main_key = self._create_registry_key(
            self.HKEY_CLASSES_ROOT,
            base_key_path,
            {
                "": self.menu_name,
                "Icon": str(icon_path) if icon_path else "",
                "Position": "bottom"  # Place at bottom of context menu
            }
        )

        # Create "MUIVerb" for proper display name
        self._set_registry_value(
            self.HKEY_CLASSES_ROOT,
            base_key_path,
            "MUIVerb",
            self.menu_name,
            self.REG_SZ
        )

        # Create subcommand key for cascading menu
        subcommands_key = self._create_registry_key(
            self.HKEY_CLASSES_ROOT,
            f"{base_key_path}\\shell",
            {}
        )

        # Create individual command entries
        for i, command in enumerate(commands):
            command_name = command.get("name", f"Command{i+1}")
            command_action = command.get("action", "")
            command_description = command.get("description", "")

            # Create command key
            command_key_name = f"{command_name.replace(' ', '_')}"
            command_key_path = f"{base_key_path}\\shell\\{command_key_name}"

            # Set command display name and description
            self._create_registry_key(
                self.HKEY_CLASSES_ROOT,
                command_key_path,
                {
                    "": command_name,
                    "Icon": str(icon_path) if icon_path else ""
                }
            )

            # Create command execution key
            command_key = self._create_registry_key(
                self.HKEY_CLASSES_ROOT,
                f"{command_key_path}\\command",
                {}
            )

            # Build command string
            command_string = self._build_command_string(command_action, install_path)

            # Set default command value
            self._set_registry_value(
                self.HKEY_CLASSES_ROOT,
                f"{command_key_path}\\command",
                "",
                command_string,
                self.REG_SZ
            )

    def _build_command_string(self, action: str, install_path: Path) -> str:
        """Build the command string for registry execution.

        Args:
            action: CLI action to execute (e.g., 'sync code')
            command: Full command string to execute
            install_path: Path where CortexHarness is installed

        Returns:
            Properly quoted command string for Windows Registry
        """
        # Path to the wrapper script
        wrapper_script = install_path / "scripts" / "wrapper.bat"

        # Build command: "C:\Path\to\wrapper.bat" "action" "%1"
        # Note: %1 represents the selected folder path
        command = f'"{wrapper_script}" "{action}" "%1"'

        return command

    def _create_registry_key(
        self,
        root_key: int,
        key_path: str,
        values: Dict[str, str]
    ) -> winreg.HKey:
        """Create or open a registry key and set values.

        Args:
            root_key: Root registry key (e.g., HKEY_CLASSES_ROOT)
            key_path: Full path to the registry key
            values: Dictionary of value names and data to set

        Returns:
            Opened registry key handle
        """
        # Create or open the key
        key = winreg.CreateKeyEx(root_key, key_path, 0, winreg.KEY_WRITE)

        # Set values
        for value_name, value_data in values.items():
            self._set_registry_value(root_key, key_path, value_name, value_data, self.REG_SZ)

        return key

    def _set_registry_value(
        self,
        root_key: int,
        key_path: str,
        value_name: str,
        value_data: str,
        value_type: int
    ):
        """Set a value in a registry key.

        Args:
            root_key: Root registry key
            key_path: Full path to the registry key
            value_name: Name of the value (empty string for default value)
            value_data: Data to store in the value
            value_type: Type of registry value (REG_SZ, REG_EXPAND_SZ, etc.)
        """
        key = winreg.CreateKeyEx(root_key, key_path, 0, winreg.KEY_WRITE)

        try:
            winreg.SetValueEx(key, value_name, 0, value_type, value_data)
        finally:
            winreg.CloseKey(key)

    def remove_context_menu(self) -> bool:
        """Remove complete context menu structure from Windows Registry.

        Returns:
            True if removal was successful, False otherwise
        """
        try:
            # Remove main menu entries from all locations
            self._remove_registry_tree(self.HKEY_CLASSES_ROOT, self.base_key_path)
            self._remove_registry_tree(self.HKEY_CLASSES_ROOT, self.base_bg_key_path)
            self._remove_registry_tree(self.HKEY_CLASSES_ROOT, self.drive_key_path)

            return True

        except Exception as e:
            print(f"Error removing context menu: {e}")
            return False

    def _remove_registry_tree(self, root_key: int, key_path: str):
        """Recursively remove a registry key and all subkeys.

        Args:
            root_key: Root registry key
            key_path: Full path to the registry key to remove
        """
        try:
            # Open the key for deletion
            key = winreg.OpenKey(root_key, key_path, 0, winreg.KEY_READ)

            # Recursively delete subkeys
            try:
                while True:
                    subkey_name = winreg.EnumKey(key, 0)
                    self._remove_registry_tree(root_key, f"{key_path}\\{subkey_name}")
            except OSError:
                # No more subkeys
                pass

            winreg.CloseKey(key)

            # Delete the key itself
            winreg.DeleteKey(root_key, key_path)

        except FileNotFoundError:
            # Key doesn't exist, that's fine
            pass
        except Exception as e:
            print(f"Warning: Failed to delete registry key {key_path}: {e}")

    def context_menu_exists(self) -> bool:
        """Check if the context menu is currently installed in Registry.

        Returns:
            True if context menu exists, False otherwise
        """
        try:
            winreg.OpenKey(self.HKEY_CLASSES_ROOT, self.base_key_path, 0, winreg.KEY_READ)
            return True
        except FileNotFoundError:
            return False

    def get_installed_commands(self) -> List[str]:
        """Get list of currently installed command names.

        Returns:
            List of command names installed in the context menu
        """
        commands = []

        try:
            shell_key = winreg.OpenKey(
                self.HKEY_CLASSES_ROOT,
                f"{self.base_key_path}\\shell",
                0,
                winreg.KEY_READ
            )

            try:
                index = 0
                while True:
                    command_name = winreg.EnumKey(shell_key, index)
                    commands.append(command_name)
                    index += 1
            except OSError:
                # No more subkeys
                pass

            winreg.CloseKey(shell_key)

        except FileNotFoundError:
            # Context menu doesn't exist
            pass

        return commands


def main():
    """CLI interface for Windows Registry operations."""
    import click

    @click.group()
    def cli():
        """Manage Windows Registry context menu integration."""
        pass

    @cli.command()
    @click.option('--menu-name', default='CortexHarness', help='Name for the context menu')
    def check_admin(menu_name):
        """Check if running with administrator privileges."""
        manager = WindowsRegistryManager(menu_name)

        if manager.is_admin():
            click.echo("Running with administrator privileges")
        else:
            click.echo("NOT running with administrator privileges")
            click.echo("Please run as administrator for Registry operations")

    @cli.command()
    @click.option('--menu-name', default='CortexHarness', help='Name for the context menu')
    def exists(menu_name):
        """Check if context menu is installed."""
        manager = WindowsRegistryManager(menu_name)

        if manager.context_menu_exists():
            commands = manager.get_installed_commands()
            click.echo(f"Context menu '{menu_name}' is installed")
            click.echo(f"Commands: {', '.join(commands)}")
        else:
            click.echo(f"Context menu '{menu_name}' is NOT installed")

    @cli.command()
    @click.option('--menu-name', default='CortexHarness', help='Name for the context menu')
    def remove(menu_name):
        """Remove context menu from Registry (requires admin)."""
        manager = WindowsRegistryManager(menu_name)

        if not manager.is_admin():
            click.echo("Error: This command requires administrator privileges")
            return

        if manager.remove_context_menu():
            click.echo(f"Successfully removed context menu '{menu_name}'")
        else:
            click.echo(f"Failed to remove context menu '{menu_name}'")

    cli()


if __name__ == "__main__":
    main()