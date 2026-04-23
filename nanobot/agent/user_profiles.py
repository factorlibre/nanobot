"""Per-user private context loader.

Loads private context files for a specific sender (employee) from
``workspace/users/<folder>/*.md``, keyed by a mapping in
``workspace/users/_map.yaml``.

The mapping key is ``{channel}:{sender_id}`` so the same person on
different channels can share a folder, and sender_ids from different
channels don't collide.

Example ``_map.yaml``::

    "telegram:123456|duque": contable
    "slack:U045ABC": contable
    "telegram:789012|maria": almacen
"""

from pathlib import Path

import yaml
from loguru import logger


class UserProfileLoader:
    """Resolve and load private context markdown files per sender."""

    def __init__(self, workspace: Path):
        self.workspace = workspace
        self.users_dir = workspace / "users"
        self.map_file = self.users_dir / "_map.yaml"

    def _read_map(self) -> dict[str, str]:
        if not self.map_file.exists():
            return {}
        try:
            data = yaml.safe_load(self.map_file.read_text(encoding="utf-8"))
        except yaml.YAMLError as e:
            logger.warning("Invalid users/_map.yaml: {}", e)
            return {}
        if not isinstance(data, dict):
            return {}
        return {str(k): str(v) for k, v in data.items()}

    def resolve(self, channel: str | None, sender_id: str | None) -> str | None:
        """Return the folder name mapped to ``(channel, sender_id)`` or None."""
        if not channel or not sender_id:
            return None
        key = f"{channel}:{sender_id}"
        folder = self._read_map().get(key)
        if not folder:
            logger.debug("No user profile mapping for {}", key)
            return None
        return folder

    def load(self, channel: str | None, sender_id: str | None) -> str:
        """Load all ``.md`` files from the resolved user folder, concatenated.

        Returns an empty string when no mapping exists, the folder is
        missing, or the folder has no markdown files.
        """
        folder = self.resolve(channel, sender_id)
        if not folder:
            return ""
        user_dir = self.users_dir / folder
        if not user_dir.is_dir():
            logger.warning(
                "User profile folder '{}' missing for {}:{}",
                folder, channel, sender_id,
            )
            return ""
        parts = []
        for path in sorted(user_dir.glob("*.md")):
            content = path.read_text(encoding="utf-8").strip()
            if content:
                parts.append(f"### {path.name}\n\n{content}")
        if not parts:
            return ""
        return f"## Private context: {folder}\n\n" + "\n\n".join(parts)
