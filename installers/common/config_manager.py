#!/usr/bin/env python3
"""Configuration manager for CortexHarness context menu installers."""

import json
import sys
from pathlib import Path
from typing import Dict, List, Any, Optional


class ContextMenuConfig:
    """Manages context menu configuration for CortexHarness installers."""

    DEFAULT_MENU_NAME = "CortexHarness"
    DEFAULT_COMMANDS = [
        {
            "name": "Sync Code",
            "action": "sync code",
            "description": "Incrementally sync code changes to graph database",
            "icon": "sync"
        },
        {
            "name": "Sync Documents",
            "action": "sync doc",
            "description": "Sync documents to vector database",
            "icon": "doc"
        },
        {
            "name": "Run Harness",
            "action": "harness run",
            "description": "Run AI agent task orchestration",
            "icon": "run"
        }
    ]

    def __init__(self, project_root: Optional[Path] = None):
        """Initialize configuration manager.

        Args:
            project_root: Root directory of CortexHarness project.
                        Defaults to detecting from current location.
        """
        if project_root is None:
            # Auto-detect project root
            self.project_root = self._detect_project_root()
        else:
            self.project_root = Path(project_root)

        self.config_dir = self.project_root / ".cortext-harness"
        self.config_file = self.config_dir / "context-menu.json"

    def _detect_project_root(self) -> Path:
        """Detect CortexHarness project root from current location."""
        current = Path.cwd()

        # Look for project markers
        markers = ["pyproject.toml", "cortex_harness", "code-tiny", "harness"]

        # Search upward for project root
        for parent in [current, *current.parents]:
            if all((parent / marker).exists() for marker in markers[:2]):  # Check first 2 markers
                return parent

        # Fallback to current directory if not found
        return current

    def get_config(self) -> Dict[str, Any]:
        """Get context menu configuration.

        Returns:
            Dictionary with menu configuration. Returns default config if file doesn't exist.
        """
        if not self.config_file.exists():
            return self._get_default_config()

        try:
            with open(self.config_file, 'r', encoding='utf-8') as f:
                config = json.load(f)

            # Validate and merge with defaults for any missing fields
            return self._validate_and_merge_config(config)

        except (json.JSONDecodeError, IOError) as e:
            print(f"Warning: Failed to read config file {self.config_file}: {e}")
            return self._get_default_config()

    def _get_default_config(self) -> Dict[str, Any]:
        """Get default context menu configuration."""
        return {
            "menu_name": self.DEFAULT_MENU_NAME,
            "commands": self.DEFAULT_COMMANDS,
            "platforms": {
                "windows": {
                    "enabled": sys.platform == "win32",
                    "install_path": "C:\\Program Files\\CortexHarness",
                    "registry_keys": {
                        "directory": "HKEY_CLASSES_ROOT\\Directory\\shell\\CortexHarness",
                        "background": "HKEY_CLASSES_ROOT\\Directory\\Background\\shell\\CortexHarness"
                    }
                },
                "macos": {
                    "enabled": sys.platform == "darwin",
                    "install_path": "~/Library/Services",
                    "workflow_format": "automator"
                },
                "ubuntu": {
                    "enabled": sys.platform.startswith("linux"),
                    "install_path": "~/.local/share/nautilus/scripts/CortexHarness",
                    "script_format": "bash"
                }
            },
            "version": "1.0.0"
        }

    def _validate_and_merge_config(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """Validate loaded config and merge with defaults for missing fields."""
        default = self._get_default_config()

        # Ensure required top-level keys exist
        if "menu_name" not in config:
            config["menu_name"] = default["menu_name"]
        if "commands" not in config:
            config["commands"] = default["commands"]
        if "platforms" not in config:
            config["platforms"] = default["platforms"]
        if "version" not in config:
            config["version"] = default["version"]

        # Ensure all platform configurations exist
        for platform in ["windows", "macos", "ubuntu"]:
            if platform not in config["platforms"]:
                config["platforms"][platform] = default["platforms"][platform]

        return config

    def save_config(self, config: Dict[str, Any]) -> bool:
        """Save context menu configuration to file.

        Args:
            config: Configuration dictionary to save

        Returns:
            True if save was successful, False otherwise
        """
        try:
            # Ensure config directory exists
            self.config_dir.mkdir(parents=True, exist_ok=True)

            # Write config with pretty formatting
            with open(self.config_file, 'w', encoding='utf-8') as f:
                json.dump(config, f, indent=2, ensure_ascii=False)

            return True

        except (IOError, OSError) as e:
            print(f"Error: Failed to save config to {self.config_file}: {e}")
            return False

    def get_current_platform(self) -> str:
        """Get the current platform identifier.

        Returns:
            Platform string: 'windows', 'macos', or 'ubuntu'
        """
        if sys.platform == "win32":
            return "windows"
        elif sys.platform == "darwin":
            return "macos"
        else:
            return "ubuntu"

    def is_platform_enabled(self, platform: str) -> bool:
        """Check if a specific platform is enabled in configuration.

        Args:
            platform: Platform name ('windows', 'macos', 'ubuntu')

        Returns:
            True if platform is enabled, False otherwise
        """
        config = self.get_config()
        return config.get("platforms", {}).get(platform, {}).get("enabled", False)

    def get_menu_commands(self) -> List[Dict[str, str]]:
        """Get list of menu commands for the current platform.

        Returns:
            List of command dictionaries with 'name', 'action', 'description', and 'icon' keys
        """
        config = self.get_config()
        return config.get("commands", self.DEFAULT_COMMANDS)

    def get_install_path(self, platform: str) -> Path:
        """Get the installation path for a specific platform.

        Args:
            platform: Platform name ('windows', 'macos', 'ubuntu')

        Returns:
            Path object pointing to the installation directory
        """
        config = self.get_config()
        path_str = config.get("platforms", {}).get(platform, {}).get("install_path", "")

        # Expand user path if needed
        return Path(path_str).expanduser()

    def update_command(self, command_name: str, **kwargs) -> bool:
        """Update a specific command in the configuration.

        Args:
            command_name: Name of the command to update
            **kwargs: Fields to update (e.g., action='new action', description='new description')

        Returns:
            True if update was successful, False if command not found
        """
        config = self.get_config()

        for command in config.get("commands", []):
            if command.get("name") == command_name:
                command.update(kwargs)
                return self.save_config(config)

        return False

    def add_command(self, name: str, action: str, description: str = "", icon: str = "") -> bool:
        """Add a new command to the configuration.

        Args:
            name: Display name for the command
            action: CLI action to execute (e.g., 'sync code')
            description: Description of what the command does
            icon: Icon identifier for the command

        Returns:
            True if command was added successfully, False otherwise
        """
        config = self.get_config()

        new_command = {
            "name": name,
            "action": action,
            "description": description,
            "icon": icon or "default"
        }

        config["commands"].append(new_command)
        return self.save_config(config)

    def remove_command(self, command_name: str) -> bool:
        """Remove a command from the configuration.

        Args:
            command_name: Name of the command to remove

        Returns:
            True if command was removed, False if not found
        """
        config = self.get_config()
        original_length = len(config.get("commands", []))

        config["commands"] = [
            cmd for cmd in config.get("commands", [])
            if cmd.get("name") != command_name
        ]

        if len(config["commands"]) < original_length:
            return self.save_config(config)

        return False


def main():
    """CLI interface for configuration management."""
    import click

    @click.group()
    def cli():
        """Manage CortexHarness context menu configuration."""
        pass

    @cli.command()
    @click.option('--project-root', type=click.Path(exists=True), help='Project root directory')
    def show(project_root):
        """Show current context menu configuration."""
        manager = ContextMenuConfig(project_root)
        config = manager.get_config()

        click.echo(json.dumps(config, indent=2))

    @cli.command()
    @click.option('--project-root', type=click.Path(exists=True), help='Project root directory')
    def init(project_root):
        """Initialize default configuration file."""
        manager = ContextMenuConfig(project_root)

        if manager.config_file.exists():
            click.echo(f"Configuration file already exists: {manager.config_file}")
            return

        default_config = manager._get_default_config()
        if manager.save_config(default_config):
            click.echo(f"Created default configuration: {manager.config_file}")
        else:
            click.echo(f"Failed to create configuration file")

    @cli.command()
    @click.argument('name')
    @click.argument('action')
    @click.option('--description', default='', help='Command description')
    @click.option('--icon', default='', help='Icon identifier')
    @click.option('--project-root', type=click.Path(exists=True), help='Project root directory')
    def add(name, action, description, icon, project_root):
        """Add a new command to the context menu."""
        manager = ContextMenuConfig(project_root)

        if manager.add_command(name, action, description, icon):
            click.echo(f"Added command: {name}")
        else:
            click.echo(f"Failed to add command")

    cli()


if __name__ == "__main__":
    main()