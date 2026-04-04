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


class GroupTier(str, Enum):
    """Group permission tiers for %ANYONE%, %ANONS%, and %REG%."""
    ANYONE = "anyone"
    ANONS = "anons"
    REG = "reg"


class PermissionManager:
    """Manages user permissions and tiers with persistent storage."""

    def __init__(self, storage_file: str = "user_permissions.json"):
        self.storage_file = storage_file
        self.users: Dict[str, UserTier] = {}
        self.group_tiers: Dict[GroupTier, Optional[UserTier]] = {
            GroupTier.ANYONE: None,
            GroupTier.ANONS: None,
            GroupTier.REG: None
        }
        self.load()

    def ensure_admin(self, admin_username: str) -> None:
        """Ensure the specified admin user has admin tier."""
        if admin_username and self.get_tier(admin_username) != UserTier.ADMIN:
            self.set_tier(admin_username, UserTier.ADMIN)

    def load(self) -> None:
        """Load user permissions from JSON file."""
        if os.path.exists(self.storage_file):
            try:
                with open(self.storage_file, 'r') as f:
                    data = json.load(f)
                    self.users = {k.lower(): UserTier(v) for k, v in data.items() if not k.startswith('%')}
                    for group_name, group_data in data.items():
                        if group_name == '%ANYONE%' and isinstance(group_data, dict) and 'tier' in group_data:
                            tier = group_data['tier']
                            self.group_tiers[GroupTier.ANYONE] = UserTier(tier) if tier else None
                        elif group_name == '%ANONS%' and isinstance(group_data, dict) and 'tier' in group_data:
                            tier = group_data['tier']
                            self.group_tiers[GroupTier.ANONS] = UserTier(tier) if tier else None
                        elif group_name == '%REG%' and isinstance(group_data, dict) and 'tier' in group_data:
                            tier = group_data['tier']
                            self.group_tiers[GroupTier.REG] = UserTier(tier) if tier else None
            except (json.JSONDecodeError, ValueError) as e:
                print(f"Warning: Failed to load permissions file: {e}")
                self.users = {}
                self.group_tiers = {GroupTier.ANYONE: None, GroupTier.ANONS: None, GroupTier.REG: None}

    def save(self) -> None:
        """Save user permissions to JSON file."""
        data = {k: v.value for k, v in self.users.items()}
        if self.group_tiers[GroupTier.ANYONE]:
            data['%ANYONE%'] = {'tier': self.group_tiers[GroupTier.ANYONE].value}
        if self.group_tiers[GroupTier.ANONS]:
            data['%ANONS%'] = {'tier': self.group_tiers[GroupTier.ANONS].value}
        if self.group_tiers[GroupTier.REG]:
            data['%REG%'] = {'tier': self.group_tiers[GroupTier.REG].value}
        with open(self.storage_file, 'w') as f:
            json.dump(data, f, indent=2, sort_keys=True)

    def export(self) -> Dict[str, str]:
        """Return a JSON-serializable snapshot of all custom user tiers."""
        data = {k: v.value for k, v in self.users.items()}
        if self.group_tiers[GroupTier.ANYONE]:
            data['%ANYONE%'] = {'tier': self.group_tiers[GroupTier.ANYONE].value}
        if self.group_tiers[GroupTier.ANONS]:
            data['%ANONS%'] = {'tier': self.group_tiers[GroupTier.ANONS].value}
        if self.group_tiers[GroupTier.REG]:
            data['%REG%'] = {'tier': self.group_tiers[GroupTier.REG].value}
        return data

    def replace_all(self, users: Dict[str, str]) -> None:
        """Replace all stored tiers with the provided mapping."""
        self.users = {}
        for k, v in users.items():
            if k.startswith('%'):
                if k == '%ANYONE%' and isinstance(v, dict) and 'tier' in v:
                    tier = v['tier']
                    self.group_tiers[GroupTier.ANYONE] = UserTier(tier) if tier else None
                elif k == '%ANONS%' and isinstance(v, dict) and 'tier' in v:
                    tier = v['tier']
                    self.group_tiers[GroupTier.ANONS] = UserTier(tier) if tier else None
                elif k == '%REG%' and isinstance(v, dict) and 'tier' in v:
                    tier = v['tier']
                    self.group_tiers[GroupTier.REG] = UserTier(tier) if tier else None
            else:
                self.users[k.lower()] = UserTier(v)
        self.save()

    def get_tier(self, username: str, is_registered: bool = False) -> UserTier:
        """Get the permission tier for a user."""
        username_lower = username.lower()

        # Check for group tiers if no explicit user tier set
        if username_lower not in self.users:
            if is_registered and self.group_tiers[GroupTier.REG]:
                return self.group_tiers[GroupTier.REG]
            elif not is_registered and self.group_tiers[GroupTier.ANONS]:
                return self.group_tiers[GroupTier.ANONS]
            elif self.group_tiers[GroupTier.ANYONE]:
                return self.group_tiers[GroupTier.ANYONE]

        return self.users.get(username_lower, UserTier.USER)

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

    def set_group_tier(self, group: GroupTier, tier: Optional[UserTier] = None, tier_str: Optional[str] = None) -> None:
        """Set the tier for a group (%ANYONE%, %ANONS%, %REG%)."""
        if tier_str and not tier:
            if tier_str:
                tier = UserTier(tier_str)
        self.group_tiers[group] = tier
        self.save()

    def get_group_tier(self, group: GroupTier) -> Optional[UserTier]:
        """Get the tier for a group."""
        return self.group_tiers[group]

    def can_use_command(self, username: str, required_tier: UserTier, is_registered: bool = False) -> bool:
        """
        Check if a user can use a command based on required tier.
        Banned users cannot use any commands.
        """
        user_tier = self.get_tier(username, is_registered)

        if user_tier == UserTier.BANNED:
            return False

        tier_hierarchy = {
            UserTier.ADMIN: 3,
            UserTier.MODERATOR: 2,
            UserTier.USER: 1,
            UserTier.BANNED: 0
        }

        return tier_hierarchy[user_tier] >= tier_hierarchy[required_tier]

    def is_admin(self, username: str, is_registered: bool = False) -> bool:
        """Check if user is admin tier."""
        return self.get_tier(username, is_registered) == UserTier.ADMIN

    def is_moderator(self, username: str, is_registered: bool = False) -> bool:
        """Check if user is moderator or higher tier."""
        tier = self.get_tier(username, is_registered)
        return tier == UserTier.MODERATOR or tier == UserTier.ADMIN

    def is_banned(self, username: str, is_registered: bool = False) -> bool:
        """Check if user is banned."""
        return self.get_tier(username, is_registered) == UserTier.BANNED

    def get_all_users(self) -> Dict[str, str]:
        """Get all users with custom tiers."""
        return {k: v.value for k, v in self.users.items()}
