"""
Permission management system for OWoTGPT bot.

Handles user tiers and permission checking.
"""

import json
import os
from enum import Enum
from typing import Dict, Optional


class UserTier(str, Enum):
    """User permission tiers from highest to lowest."""
    ADMIN = "admin"
    MODERATOR = "moderator"
    USER = "user"
    BANNED = "banned"


class PermissionManager:
    """Manages user permissions and tiers with persistent storage."""

    def __init__(self, storage_file: str = "user_permissions.json"):
        self.storage_file = storage_file
        self.users: Dict[str, UserTier] = {}
        self.load()

    def ensure_admin(self, admin_username: str) -> None:
        """Ensure the specified admin user has admin tier."""
        if admin_username:
            self.set_tier(admin_username, UserTier.ADMIN)

    def load(self) -> None:
        """Load user permissions from JSON file."""
        if os.path.exists(self.storage_file):
            try:
                with open(self.storage_file, 'r') as f:
                    data = json.load(f)
                    self.users = {k: UserTier(v) for k, v in data.items()}
            except (json.JSONDecodeError, ValueError) as e:
                print(f"Warning: Failed to load permissions file: {e}")
                self.users = {}

    def save(self) -> None:
        """Save user permissions to JSON file."""
        with open(self.storage_file, 'w') as f:
            json.dump({k: v.value for k, v in self.users.items()}, f, indent=2)

    def get_tier(self, username: str) -> UserTier:
        """Get the permission tier for a user."""
        return self.users.get(username.lower(), UserTier.USER)

    def set_tier(self, username: str, tier: UserTier) -> None:
        """Set the permission tier for a user."""
        self.users[username.lower()] = tier
        self.save()

    def remove_user(self, username: str) -> bool:
        """Remove a user's custom tier (reverts to USER)."""
        username_lower = username.lower()
        if username_lower in self.users:
            del self.users[username_lower]
            self.save()
            return True
        return False

    def can_use_command(self, username: str, required_tier: UserTier) -> bool:
        """
        Check if a user can use a command based on required tier.
        Banned users cannot use any commands.
        """
        user_tier = self.get_tier(username)
        
        if user_tier == UserTier.BANNED:
            return False

        tier_hierarchy = {
            UserTier.ADMIN: 3,
            UserTier.MODERATOR: 2,
            UserTier.USER: 1,
            UserTier.BANNED: 0
        }
        
        return tier_hierarchy[user_tier] >= tier_hierarchy[required_tier]

    def is_admin(self, username: str) -> bool:
        """Check if user is admin tier."""
        return self.get_tier(username) == UserTier.ADMIN

    def is_moderator(self, username: str) -> bool:
        """Check if user is moderator or higher tier."""
        tier = self.get_tier(username)
        return tier == UserTier.MODERATOR or tier == UserTier.ADMIN

    def is_banned(self, username: str) -> bool:
        """Check if user is banned."""
        return self.get_tier(username) == UserTier.BANNED

    def get_all_users(self) -> Dict[str, str]:
        """Get all users with custom tiers."""
        return {k: v.value for k, v in self.users.items()}
