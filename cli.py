#!/usr/bin/env python3
"""
Command-line interface for LinkedIn Auto Connector Bot.

This module provides a user-friendly CLI with commands for running the bot,
managing sessions, checking statistics, and configuring settings.
"""

import click
import sys
import os
from pathlib import Path
from datetime import datetime
import json
import yaml
from typing import Optional

# Add parent directory to path for imports and anchor working directory to repo root
_REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _REPO_ROOT)
os.chdir(_REPO_ROOT)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

from utils.logger import setup_logging, get_logger
from config.settings import BotConfig
from modules.session_manager import SessionManager

# Try importing optional dependencies
try:
    from rich.console import Console
    from rich.table import Table
    from rich.progress import Progress, SpinnerColumn, TextColumn
    from rich.prompt import Prompt, Confirm
    RICH_AVAILABLE = True
    console = Console()
except ImportError:
    RICH_AVAILABLE = False
    console = None


@click.group()
@click.option('--debug/--no-debug', default=False, help='Enable debug logging')
@click.option('--config', type=click.Path(exists=True), help='Path to config file')
@click.pass_context
def cli(ctx, debug, config):
    """ConnectPilot - Smart LinkedIn Networking & Connection Automation"""
    # Setup logging
    log_level = "DEBUG" if debug else "INFO"
    logger = setup_logging(log_level=log_level)

    # Load configuration
    if config:
        ctx.obj = BotConfig.from_file(config)
    else:
        ctx.obj = BotConfig()


@cli.command("browser-login")
@click.option('--browser', type=click.Choice(['edge', 'chrome']), default=None, help='Browser to open (defaults to BROWSER_TYPE in .env)')
@click.option('--remote-debugging/--no-remote-debugging', default=False, help='Launch with remote debugging port enabled')
@click.option('--port', type=int, default=None, help='Remote debugging port (defaults to EDGE_REMOTE_DEBUGGING_PORT in .env)')
def browser_login(browser, remote_debugging, port):
    """Open the dedicated persistent browser profile for one-time LinkedIn sign-in"""
    import subprocess
    from config.settings import resolve_project_path
    from utils.anti_detection import detect_browser_binary

    btype = (browser or os.getenv("BROWSER_TYPE", "edge")).strip().lower()
    if btype not in ("edge", "chrome"):
        btype = "edge"

    binary = detect_browser_binary(btype)
    if not binary:
        env_var = "EDGE_BINARY_PATH" if btype == "edge" else "CHROME_BINARY_PATH"
        raise click.ClickException(
            f"Could not auto-detect {btype.title()} executable. Set {env_var} in your .env file."
        )

    if btype == "edge":
        raw_profile_dir = os.getenv("EDGE_USER_DATA_DIR", "./edge_profile")
        profile_name = os.getenv("EDGE_PROFILE_DIRECTORY", "Default")
    else:
        raw_profile_dir = os.getenv("CHROME_USER_DATA_DIR", "./chrome_profile")
        profile_name = os.getenv("CHROME_PROFILE_DIRECTORY", "Default")

    profile_dir = resolve_project_path(raw_profile_dir)
    profile_dir.mkdir(parents=True, exist_ok=True)

    cmd = [
        binary,
        f"--user-data-dir={profile_dir}",
        f"--profile-directory={profile_name}",
    ]
    if remote_debugging:
        dbg_port = port or int(os.getenv("EDGE_REMOTE_DEBUGGING_PORT", "9223"))
        cmd.append(f"--remote-debugging-port={dbg_port}")
    cmd.append("https://www.linkedin.com/login")

    click.echo(f"Launching dedicated {btype.title()} profile at: {profile_dir}")
    click.echo("1. Sign into LinkedIn (Google SSO, Apple SSO, 2FA, or Email/Password).")
    click.echo("2. Wait until your LinkedIn feed loads.")
    click.echo("3. Close that browser window when finished (unless keeping remote-debugging open).")
    subprocess.Popen(cmd)


@cli.command()
@click.option('--username', '-u', help='LinkedIn username/email (defaults to LINKEDIN_USERNAME in .env)')
@click.option('--password', '-p', help='LinkedIn password (defaults to LINKEDIN_PASSWORD in .env)')
@click.option('--search-url', '-s', help='LinkedIn search URL (defaults to SEARCH_LINK in .env)')
@click.option('--keywords', '-k', help='Search keywords (alternative to URL)')
@click.option('--limit', '-l', default=20, help='Maximum connections to send')
@click.option('--message', '-m', help='Connection message template')
@click.option('--headless/--no-headless', default=False, help='Run in headless mode')
@click.option('--follow/--no-follow', default=True, help='Also follow profiles')
@click.pass_context
def run(ctx, username, password, search_url, keywords, limit, message, headless, follow):
    """Run the LinkedIn bot to send connection requests"""
    logger = get_logger('cli.run')

    env_user = os.getenv('LINKEDIN_USERNAME')
    env_pass = os.getenv('LINKEDIN_PASSWORD')
    if not username and env_user and env_user != 'your_email@example.com':
        username = env_user
    if not password and env_pass and env_pass != 'your_password':
        password = env_pass
    if not search_url and not keywords:
        search_url = os.getenv('SEARCH_LINK')
        keywords = os.getenv('SEARCH_KEYWORDS')

    if RICH_AVAILABLE:
        console.print("[bold green]Starting ConnectPilot[/bold green]")
        console.print(f"Account: [cyan]{username or 'Using signed-in browser profile'}[/cyan]")
        console.print(f"Connection limit: [yellow]{limit}[/yellow]")
        console.print(f"Headless mode: [blue]{headless}[/blue]")
    else:
        click.echo("Starting ConnectPilot")
        click.echo(f"Account: {username or 'Using signed-in browser profile'}")
        click.echo(f"Connection limit: {limit}")

    try:
        # Import main bot class
        from main import LinkedInBot

        # Initialize bot
        bot = LinkedInBot(config=ctx.obj, headless=headless)

        # Login
        if RICH_AVAILABLE:
            with console.status("[bold green]Logging in to LinkedIn...") as status:
                success = bot.login(username, password)
        else:
            click.echo("Logging in to LinkedIn...")
            success = bot.login(username, password)

        if not success:
            raise click.ClickException("Failed to login to LinkedIn")

        # Build search URL if keywords provided
        if keywords and not search_url:
            search_url = bot.build_search_url(keywords=keywords)

        # Set search URL
        if search_url:
            bot.set_search_url(search_url)

        # Set message template
        if message:
            bot.set_message_template(message)

        # Run bot
        if RICH_AVAILABLE:
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                console=console
            ) as progress:
                task = progress.add_task(
                    f"[green]Sending connections (0/{limit})...",
                    total=limit
                )

                def update_progress(sent, total):
                    progress.update(
                        task,
                        completed=sent,
                        description=f"[green]Sending connections ({sent}/{total})..."
                    )

                results = bot.run(
                    max_connections=limit,
                    follow_profiles=follow,
                    progress_callback=update_progress
                )
        else:
            click.echo(f"Sending up to {limit} connection requests...")
            results = bot.run(max_connections=limit, follow_profiles=follow)

        # Display results
        if RICH_AVAILABLE:
            _display_results_rich(results)
        else:
            _display_results_plain(results)

    except Exception as e:
        logger.error(f"Bot execution failed: {str(e)}")
        raise click.ClickException(str(e))


@cli.command()
@click.option('--username', '-u', help='Filter by username')
@click.pass_context
def sessions(ctx, username):
    """List and manage saved sessions"""
    logger = get_logger('cli.sessions')

    try:
        session_mgr = SessionManager()
        sessions_list = session_mgr.list_sessions()

        if username:
            sessions_list = [s for s in sessions_list if s['username'] == username]

        if not sessions_list:
            click.echo("No saved sessions found")
            return

        if RICH_AVAILABLE:
            table = Table(title="Saved Sessions")
            table.add_column("Username", style="cyan")
            table.add_column("Created", style="green")
            table.add_column("Age (hours)", style="yellow")
            table.add_column("Size", style="blue")

            for session in sessions_list:
                table.add_row(
                    session['username'],
                    session['timestamp'],
                    str(session['age_hours']),
                    f"{session['file_size'] / 1024:.1f} KB"
                )

            console.print(table)
        else:
            click.echo("Saved Sessions:")
            for session in sessions_list:
                click.echo(f"  - {session['username']}: {session['age_hours']:.1f} hours old")

    except Exception as e:
        logger.error(f"Failed to list sessions: {str(e)}")
        raise click.ClickException(str(e))


@cli.command()
@click.option('--today/--all', default=False, help='Show today\'s stats only')
@click.option('--format', type=click.Choice(['table', 'json', 'yaml']), default='table')
@click.pass_context
def stats(ctx, today, format):
    """Display bot statistics and connection history"""
    logger = get_logger('cli.stats')

    try:
        from modules.rate_limiter import RateLimiter

        rate_limiter = RateLimiter()
        statistics = rate_limiter.get_statistics(today_only=today)

        if format == 'json':
            click.echo(json.dumps(statistics, indent=2))
        elif format == 'yaml':
            click.echo(yaml.dump(statistics, default_flow_style=False))
        else:
            if RICH_AVAILABLE:
                _display_stats_rich(statistics)
            else:
                _display_stats_plain(statistics)

    except Exception as e:
        logger.error(f"Failed to get statistics: {str(e)}")
        raise click.ClickException(str(e))


@cli.command()
@click.option('--max-age', '-a', default=48, help='Maximum age in hours')
@click.option('--dry-run/--execute', default=True, help='Show what would be deleted')
@click.pass_context
def clean(ctx, max_age, dry_run):
    """Clean up old sessions and logs"""
    logger = get_logger('cli.clean')

    try:
        session_mgr = SessionManager()

        if dry_run:
            sessions_list = session_mgr.list_sessions()
            old_sessions = [s for s in sessions_list if s['age_hours'] > max_age]

            if old_sessions:
                click.echo(f"Would delete {len(old_sessions)} sessions older than {max_age} hours:")
                for session in old_sessions:
                    click.echo(f"  - {session['username']}: {session['age_hours']:.1f} hours old")
            else:
                click.echo("No sessions to clean")
        else:
            if RICH_AVAILABLE and Confirm.ask(f"Delete sessions older than {max_age} hours?"):
                cleaned = session_mgr.clean_expired_sessions(max_age_hours=max_age)
                console.print(f"[green]Cleaned {cleaned} expired sessions[/green]")
            else:
                if click.confirm(f"Delete sessions older than {max_age} hours?"):
                    cleaned = session_mgr.clean_expired_sessions(max_age_hours=max_age)
                    click.echo(f"Cleaned {cleaned} expired sessions")

    except Exception as e:
        logger.error(f"Cleanup failed: {str(e)}")
        raise click.ClickException(str(e))


@cli.command()
@click.option('--key', '-k', help='Configuration key to get')
@click.option('--set', 'set_value', help='Set configuration value')
@click.option('--list', 'list_all', is_flag=True, help='List all configuration')
@click.pass_context
def config(ctx, key, set_value, list_all):
    """View and modify bot configuration"""
    logger = get_logger('cli.config')

    try:
        config_obj = ctx.obj

        if list_all:
            if RICH_AVAILABLE:
                table = Table(title="Configuration")
                table.add_column("Key", style="cyan")
                table.add_column("Value", style="green")
                table.add_column("Type", style="yellow")

                for k, v in config_obj.to_dict().items():
                    table.add_row(k, str(v), type(v).__name__)

                console.print(table)
            else:
                click.echo("Configuration:")
                for k, v in config_obj.to_dict().items():
                    click.echo(f"  {k}: {v}")

        elif key and set_value:
            # Set configuration value
            config_obj.set(key, set_value)
            config_obj.save()
            click.echo(f"Set {key} = {set_value}")

        elif key:
            # Get configuration value
            value = config_obj.get(key)
            click.echo(f"{key}: {value}")

        else:
            click.echo("Use --list to show all configuration or --key to get/set specific value")

    except Exception as e:
        logger.error(f"Configuration error: {str(e)}")
        raise click.ClickException(str(e))


@cli.command()
@click.option('--check-auth', is_flag=True, help='Test authentication')
@click.option('--check-rate-limits', is_flag=True, help='Check current rate limits')
@click.option('--check-browser', is_flag=True, help='Test browser setup')
@click.pass_context
def test(ctx, check_auth, check_rate_limits, check_browser):
    """Test bot components and configuration"""
    logger = get_logger('cli.test')

    if RICH_AVAILABLE:
        console.print("[bold]Running tests...[/bold]")

    results = []

    try:
        if check_auth:
            # Test authentication
            click.echo("Testing authentication...")
            username = click.prompt("Username")
            password = click.prompt("Password", hide_input=True)

            from modules.authenticator import LinkedInAuthenticator
            from utils.anti_detection import StealthBrowser

            browser = StealthBrowser().create_driver(headless=True)
            auth = LinkedInAuthenticator(browser)
            success, message = auth.login(username, password, use_session=False)

            results.append(("Authentication", "✓" if success else "✗", message))
            browser.quit()

        if check_rate_limits:
            # Check rate limits
            from modules.rate_limiter import RateLimiter

            rate_limiter = RateLimiter()
            can_send = rate_limiter.can_send_request()
            stats = rate_limiter.get_statistics()

            status = "✓ Can send" if can_send else "✗ Limit reached"
            details = f"Today: {stats.get('today_count', 0)}, Week: {stats.get('week_count', 0)}"
            results.append(("Rate Limits", status, details))

        if check_browser:
            # Test browser
            from utils.anti_detection import StealthBrowser

            try:
                browser = StealthBrowser().create_driver(headless=True)
                browser.get("https://www.linkedin.com")
                title = browser.title
                browser.quit()

                results.append(("Browser", "✓", f"Loaded: {title}"))
            except Exception as e:
                results.append(("Browser", "✗", str(e)))

        # Display results
        if RICH_AVAILABLE:
            table = Table(title="Test Results")
            table.add_column("Component", style="cyan")
            table.add_column("Status", style="green")
            table.add_column("Details", style="yellow")

            for component, status, details in results:
                table.add_row(component, status, details)

            console.print(table)
        else:
            click.echo("\nTest Results:")
            for component, status, details in results:
                click.echo(f"  {component}: {status} - {details}")

    except Exception as e:
        logger.error(f"Test failed: {str(e)}")
        raise click.ClickException(str(e))


def _display_results_rich(results):
    """Display results using rich formatting"""
    from rich.markup import escape

    table = Table(title="Bot Execution Results")
    table.add_column("Metric", style="cyan")
    table.add_column("Value", style="green")

    table.add_row("Connections Sent", str(results.get('connections_sent', 0)))
    table.add_row("Connections Failed", str(results.get('connections_failed', 0)))
    table.add_row("Profiles Followed", str(results.get('profiles_followed', 0)))
    table.add_row("Total Processed", str(results.get('total_processed', 0)))

    if 'duration' in results:
        table.add_row("Duration", f"{results['duration']:.1f} seconds")

    if 'success_rate' in results:
        table.add_row("Success Rate", f"{results['success_rate']:.1f}%")

    console.print(table)

    if results.get('weekly_limit_reached'):
        reason = escape(str(results.get('stop_reason') or 'LinkedIn weekly connection invitation limit reached'))
        console.print(f"\n[bold red]WARNING: LINKEDIN WEEKLY INVITATION LIMIT REACHED[/bold red]")
        console.print(f"  [red]{reason}[/red]")


def _display_results_plain(results):
    """Display results in plain text"""
    click.echo("\nBot Execution Results:")
    click.echo(f"  Connections Sent: {results.get('connections_sent', 0)}")
    click.echo(f"  Connections Failed: {results.get('connections_failed', 0)}")
    click.echo(f"  Profiles Followed: {results.get('profiles_followed', 0)}")
    click.echo(f"  Total Processed: {results.get('total_processed', 0)}")

    if 'duration' in results:
        click.echo(f"  Duration: {results['duration']:.1f} seconds")

    if 'success_rate' in results:
        click.echo(f"  Success Rate: {results['success_rate']:.1f}%")

    if results.get('weekly_limit_reached'):
        click.echo("\n[WARNING] LINKEDIN WEEKLY INVITATION LIMIT REACHED!")
        click.echo(f"  Reason: {results.get('stop_reason') or 'LinkedIn weekly connection invitation limit reached'}")


def _display_stats_rich(statistics):
    """Display statistics using rich formatting"""
    from rich.markup import escape

    li_blocked = bool(statistics.get('linkedin_weekly_limit_active'))
    daily_rem_str = (
        "0 (Blocked: Weekly Limit)"
        if li_blocked
        else str(statistics.get('daily_remaining', 20))
    )
    weekly_rem_str = (
        "0 (Blocked: Weekly Limit)"
        if li_blocked
        else str(statistics.get('weekly_remaining', 80))
    )

    table = Table(title="Connection Statistics")
    table.add_column("Period", style="cyan")
    table.add_column("Sent", style="green")
    table.add_column("Failed", style="red")
    table.add_column("Limit", style="yellow")
    table.add_column("Remaining", style="red" if li_blocked else "blue")

    table.add_row(
        "Today",
        str(statistics.get('today_count', 0)),
        str(statistics.get('today_failed', 0)),
        str(statistics.get('daily_limit', 20)),
        daily_rem_str,
    )

    table.add_row(
        "This Week",
        str(statistics.get('week_count', 0)),
        str(statistics.get('week_failed', 0)),
        str(statistics.get('weekly_limit', 80)),
        weekly_rem_str,
    )

    console.print(table)

    if li_blocked:
        reason = escape(str(statistics.get('linkedin_weekly_limit_reason') or 'Weekly invitation limit reached'))
        detected_at = escape(str(statistics.get('linkedin_weekly_limit_detected_at') or ''))
        when_str = f" (detected {detected_at})" if detected_at else ""
        console.print(
            f"\n[bold red][!] LinkedIn Weekly Invitation Limit Active{when_str}:[/bold red] [red]{reason}[/red]"
        )

    if 'recent_connections' in statistics and statistics['recent_connections']:
        console.print("\n[bold]Recent Connection Attempts (Latest 15):[/bold]")
        for conn in statistics['recent_connections'][:15]:
            name_str = escape(str(conn.get('profile_name') or 'LinkedIn Member'))
            url_str = f" -> {escape(str(conn['profile_url']))}" if conn.get('profile_url') else ""
            ts_str = escape(str(conn.get('timestamp', '')))
            if conn.get('success', True):
                badge = "[green][SENT][/green]"
                err_suffix = ""
            else:
                badge = "[red][FAILED][/red]"
                err_msg = escape(str(conn.get('error_message') or 'Invitation did not go through'))
                err_suffix = f" [red]({err_msg})[/red]"
            console.print(f"  • {badge} {name_str}{url_str} ({ts_str}){err_suffix}")


def _display_stats_plain(statistics):
    """Display statistics in plain text"""
    li_blocked = bool(statistics.get('linkedin_weekly_limit_active'))
    click.echo("\nConnection Statistics:")
    click.echo(
        f"  Today: {statistics.get('today_count', 0)} sent, "
        f"{statistics.get('today_failed', 0)} failed / {statistics.get('daily_limit', 20)} limit"
    )
    click.echo(
        f"  This Week: {statistics.get('week_count', 0)} sent, "
        f"{statistics.get('week_failed', 0)} failed / {statistics.get('weekly_limit', 80)} limit"
    )
    click.echo(f"  Daily Remaining: {statistics.get('daily_remaining', 20)}")
    click.echo(f"  Weekly Remaining: {statistics.get('weekly_remaining', 80)}")

    if li_blocked:
        click.echo(
            f"\n[!] LinkedIn Weekly Invitation Limit Active: "
            f"{statistics.get('linkedin_weekly_limit_reason') or 'Weekly limit reached'}"
        )

    if 'recent_connections' in statistics and statistics['recent_connections']:
        click.echo("\nRecent Connection Attempts (Latest 15):")
        for conn in statistics['recent_connections'][:15]:
            status_tag = "[SENT]" if conn.get('success', True) else "[FAILED]"
            url_str = f" -> {conn['profile_url']}" if conn.get('profile_url') else ""
            err_str = f" ({conn['error_message']})" if not conn.get('success', True) and conn.get('error_message') else ""
            click.echo(f"  - {status_tag} {conn['profile_name']}{url_str} ({conn['timestamp']}){err_str}")


if __name__ == '__main__':
    cli()