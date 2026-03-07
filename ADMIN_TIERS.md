# Admin Management System

## Overview
The bot now features a tiered permission system with persistent storage. User tiers are saved to `user_permissions.json` locally and can also be synced through a GitHub gist so they persist between GitHub Actions runs.

## Permission Tiers

### User (Default)
- Can use basic commands: `help`, `info`, `checktier`, `gen`, `imitate`
- All users start at this tier unless assigned otherwise

### Moderator
- Has all User permissions
- Can use moderator commands: `clear`, `temp`, `my son`
- Can clear chat history and adjust temperature

### Admin
- Has all Moderator permissions
- Can manage user tiers with: `tier`, `untier`, `listtiers`, `kill`
- Full control over the bot's permission system
- Can immediately stop the bot with `kill`

### Banned
- Cannot use any bot commands
- All command attempts return a ban message

## Admin Commands

### `tier [username] [tier]`
Check or set user tiers.

**Examples:**
- `tier` - Shows your own tier
- `tier testuser` - Shows testuser's tier
- `tier testuser moderator` - Sets testuser to moderator
- `tier baduser banned` - Bans baduser

**Valid tiers:** `admin`, `moderator`, `user`, `banned`

### `untier <username>`
Remove a user's custom tier, reverting them to "user" tier.

**Example:**
- `untier testuser` - Reverts testuser to regular user

### `listtiers`
List all users with custom tiers.

**Example output:**
```
📋 Users with custom tiers:
  • admin1: admin
  • mod1: moderator
  • mod2: moderator
  • banned1: banned
```

### `kill`
Immediately stop the bot process.

**Example:**
- `kill` - Sends a shutdown message, closes websocket handling, and exits the run

## User Commands

### `checktier`
Check your current permission tier and privileges.

**Example output:**
```
👤 Your tier: moderator
🔹 You have moderator privileges
```

## Permission Hierarchy
Commands require specific tiers to use:
- Admin commands require admin tier
- Moderator commands require moderator tier or higher
- User commands work for all tiers except banned

## Storage
User tiers are automatically saved to `user_permissions.json`. This file is created automatically and ignored by git to protect user privacy.

To persist tiers between GitHub Actions runs, set these environment variables for the bot run:
- `OWOTGPT_TIERS_GIST_ID` - the gist ID that stores `user_permissions.json`
- `GITHUB_TOKEN` - token used to read and update that gist

On startup the bot will try to load tiers from the gist. Whenever `tier` or `untier` changes data, it will push the updated JSON back to the gist.

## Initial Setup
The admin user specified in `bot.py` (default: `gimmickCellar`) is automatically set to admin tier on bot startup.
