#!/usr/bin/env python3
"""
Main entry point for ConnectPilot.

This module provides the main LinkedInBot class that orchestrates all bot functionality
including authentication, connection sending, verification, and session management.
"""

import sys
import os
import time
from pathlib import Path
from typing import Optional, Dict, Any, Callable
from datetime import datetime

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# Add current directory to path and set working directory to repo root so .env/data/sessions always resolve here
_REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _REPO_ROOT)
os.chdir(_REPO_ROOT)

# Import bot modules
from config.settings import BotConfig
from config.security import CredentialManager
from config.constants import LINKEDIN_URLS, CONNECTION_LIMITS
from modules.authenticator import LinkedInAuthenticator
from modules.connector import ConnectionManager
from modules.session_manager import SessionManager
from modules.rate_limiter import RateLimiter
from utils.anti_detection import StealthBrowser, HumanBehaviorSimulator
from utils.logger import setup_logging, get_logger, TimedOperation
from utils.exceptions import (
    AuthenticationException,
    RateLimitException,
    DailyLimitExceededException,
    WeeklyLimitExceededException,
    BrowserException,
    handle_exception
)


class LinkedInBot:
    """
    Main LinkedIn automation bot class that coordinates all functionality.
    """

    def __init__(self, config: Optional[BotConfig] = None, headless: bool = False):
        """
        Initialize the LinkedIn Bot.

        Args:
            config: Optional BotConfig object
            headless: Run browser in headless mode
        """
        # Initialize configuration
        self.config = config or BotConfig()
        self.headless = headless

        # Setup logging
        self.logger = setup_logging(
            log_level=self.config.log_level.value,
            log_dir=self.config.log_dir
        )
        self.logger.info("Initializing LinkedIn Bot")

        # Initialize components
        self.driver = None
        self.authenticator = None
        self.connector = None
        self.session_manager = None
        self.rate_limiter = None
        self.credential_manager = None
        self.behavior_simulator = None

        # State tracking
        self.is_logged_in = False
        self.search_url = None
        self.message_template = None

        # Initialize all components
        self._initialize_components()

    def _initialize_components(self):
        """Initialize all bot components."""
        try:
            # Initialize credential manager
            self.credential_manager = CredentialManager()
            self.logger.info("Credential manager initialized")

            # Initialize session manager
            self.session_manager = SessionManager(
                session_dir=self.config.session_dir
            )
            self.logger.info("Session manager initialized")

            # Initialize rate limiter
            self.rate_limiter = RateLimiter(
                database_path=self.config.database_path
            )
            self.logger.info("Rate limiter initialized")

            # Create browser driver
            self._create_browser()

        except Exception as e:
            self.logger.error(f"Failed to initialize components: {str(e)}")
            raise

    def _create_browser(self):
        """Create and configure browser driver."""
        try:
            self.logger.info(f"Creating {self.config.browser_type.value} browser")

            # Create stealth browser
            stealth_browser = StealthBrowser(
                browser_type=self.config.browser_type.value,
                config=self.config.to_dict()
            )

            self.driver = stealth_browser.create_driver(headless=self.headless)

            # Initialize behavior simulator
            self.behavior_simulator = HumanBehaviorSimulator(self.driver)

            # Initialize authenticator
            self.authenticator = LinkedInAuthenticator(
                self.driver,
                self.session_manager,
                self.config
            )

            # Initialize connector
            self.connector = ConnectionManager(
                self.driver,
                self.rate_limiter,
                self.config
            )

            self.logger.info("Browser and components created successfully")

        except Exception as e:
            self.logger.error(f"Failed to create browser: {str(e)}")
            raise BrowserException(f"Failed to create browser: {str(e)}")

    def login(self, username: Optional[str] = None, password: Optional[str] = None) -> bool:
        """
        Login to LinkedIn.

        Args:
            username: LinkedIn username (uses config if not provided)
            password: LinkedIn password (uses config if not provided)

        Returns:
            True if login successful, False otherwise
        """
        try:
            # Get credentials if configured
            if not username:
                username = self.credential_manager.get_credential('linkedin_username')
            if not password:
                password = self.credential_manager.get_credential('linkedin_password')

            self.logger.info(f"Attempting login for user: {username or 'browser-session'}")

            # Attempt login (supports persistent browser profile, saved cookies, credentials, or one-time browser login)
            with TimedOperation(self.logger, "LinkedIn login"):
                success, message = self.authenticator.login(
                    username,
                    password,
                    use_session=self.config.use_sessions
                )

            if success:
                self.is_logged_in = True
                self.logger.info(f"Successfully logged in: {message}")
            else:
                self.logger.error(f"Login failed: {message}")

            return success

        except Exception as e:
            handle_exception(e, self.logger, reraise=False)
            return False

    def logout(self) -> bool:
        """
        Logout from LinkedIn.

        Returns:
            True if logout successful, False otherwise
        """
        try:
            if self.authenticator:
                success = self.authenticator.logout()
                if success:
                    self.is_logged_in = False
                    self.logger.info("Successfully logged out")
                return success
            return False

        except Exception as e:
            self.logger.error(f"Logout failed: {str(e)}")
            return False

    def set_search_url(self, url: str):
        """Set LinkedIn search URL."""
        self.search_url = url
        self.logger.info(f"Search URL set: {url}")

    def set_message_template(self, template: str):
        """Set connection message template."""
        self.message_template = template
        self.logger.info("Message template updated")

    def build_search_url(self, **kwargs) -> str:
        """
        Build LinkedIn search URL from parameters.

        Args:
            **kwargs: Search parameters (keywords, location, etc.)

        Returns:
            LinkedIn search URL
        """
        # Implementation would use SearchBuilder from roadmap
        base_url = "https://www.linkedin.com/search/results/people/?"
        params = []

        if 'keywords' in kwargs:
            params.append(f"keywords={kwargs['keywords']}")

        url = base_url + "&".join(params)
        self.logger.info(f"Built search URL: {url}")
        return url

    def run(
        self,
        max_connections: int = 20,
        follow_profiles: bool = True,
        progress_callback: Optional[Callable] = None
    ) -> Dict[str, Any]:
        """
        Run the bot to send connection requests.

        Args:
            max_connections: Maximum number of connections to send
            follow_profiles: Whether to also follow profiles
            progress_callback: Optional callback for progress updates

        Returns:
            Dictionary with execution results
        """
        results = {
            'start_time': datetime.now(),
            'connections_sent': 0,
            'connections_failed': 0,
            'profiles_followed': 0,
            'total_processed': 0,
            'errors': []
        }

        try:
            # Check if logged in
            if not self.is_logged_in:
                raise AuthenticationException("Not logged in to LinkedIn")

            # Check rate limits
            if not self.rate_limiter.can_send_request():
                stats = self.rate_limiter.get_statistics()
                if stats['today_count'] >= stats['daily_limit']:
                    raise DailyLimitExceededException(
                        limit=stats['daily_limit'],
                        current=stats['today_count']
                    )
                else:
                    raise WeeklyLimitExceededException(
                        limit=stats['weekly_limit'],
                        current=stats['week_count']
                    )

            # Navigate to search results (stay on current page if already on a later page of the same search)
            if self.search_url:
                import re
                curr = self.driver.current_url
                strip_page = lambda u: re.sub(r'([?&])page=\d+&?', r'\1', u).rstrip('?&')
                if "linkedin.com/search/results/people" in curr and strip_page(curr) == strip_page(self.search_url):
                    self.logger.info(f"Already on active search page ({curr}), continuing from current page")
                else:
                    self.logger.info(f"Navigating to search: {self.search_url}")
                    self.driver.get(self.search_url)
                self.behavior_simulator.simulate_reading(duration=2)
            else:
                raise ValueError("No search URL configured")

            # Process search results
            self.logger.info(f"Processing up to {max_connections} connections")

            # Use message template or default
            message = self.message_template or self.config.get_message_template()

            # Process connections
            with TimedOperation(self.logger, f"Processing {max_connections} connections"):
                results.update(
                    self.connector.process_search_results(
                        message_template=message,
                        max_connections=max_connections,
                        follow_profiles=follow_profiles
                    )
                )

            # Persist current search page URL back to .env only if at least one connection actually went through
            try:
                final_url = self.driver.current_url
                if results.get('connections_sent', 0) > 0 and "linkedin.com/search/results/people" in final_url:
                    env_file = Path(os.path.dirname(os.path.abspath(__file__))) / ".env"
                    if env_file.exists():
                        import re
                        env_text = env_file.read_text(encoding="utf-8")
                        updated_text = re.sub(
                            r"^SEARCH_LINK=.*$",
                            f"SEARCH_LINK={final_url}",
                            env_text,
                            flags=re.MULTILINE,
                        )
                        if updated_text != env_text:
                            env_file.write_text(updated_text, encoding="utf-8")
                            self.logger.info(f"Updated SEARCH_LINK in .env to {final_url}")
                elif results.get('connections_sent', 0) == 0:
                    self.logger.info("No connections were sent; keeping existing SEARCH_LINK in .env unchanged")
            except Exception as e:
                self.logger.warning(f"Could not update SEARCH_LINK in .env: {e}")

            # Calculate success rate
            total_attempts = results['connections_sent'] + results['connections_failed']
            if total_attempts > 0:
                results['success_rate'] = (results['connections_sent'] / total_attempts) * 100
            else:
                results['success_rate'] = 0

            # Update progress callback
            if progress_callback:
                progress_callback(results['connections_sent'], max_connections)

        except RateLimitException as e:
            self.logger.warning(f"Rate limit reached: {str(e)}")
            results['errors'].append(str(e))
            results['rate_limit_reached'] = True

        except Exception as e:
            self.logger.error(f"Bot execution error: {str(e)}")
            results['errors'].append(str(e))
            results['error'] = str(e)

        finally:
            # Calculate duration
            results['end_time'] = datetime.now()
            results['duration'] = (results['end_time'] - results['start_time']).total_seconds()

            # Log summary
            self.logger.info(
                f"Bot execution complete: {results['connections_sent']} sent, "
                f"{results['connections_failed']} failed, "
                f"{results['profiles_followed']} followed"
            )

        return results

    def get_statistics(self) -> Dict[str, Any]:
        """
        Get bot statistics.

        Returns:
            Dictionary with statistics
        """
        stats = {}

        # Get rate limiter statistics
        if self.rate_limiter:
            stats.update(self.rate_limiter.get_statistics())

        # Get connector statistics
        if self.connector:
            stats.update(self.connector.get_statistics())

        # Get session info
        if self.session_manager:
            sessions = self.session_manager.list_sessions()
            stats['saved_sessions'] = len(sessions)

        stats['is_logged_in'] = self.is_logged_in
        stats['headless_mode'] = self.headless

        return stats

    def cleanup(self):
        """Clean up resources."""
        try:
            self.logger.info("Starting cleanup")

            # Close browser only if we launched a standalone instance (do not close user's live attached Edge)
            if self.driver:
                if getattr(self.driver, "_attached_to_existing", False):
                    try:
                        if getattr(self.driver, "_is_cdp_driver", False):
                            self.driver.quit()
                        else:
                            self.driver.service.stop()
                    except Exception:
                        pass
                    self.logger.info("Detached from live Edge session (leaving browser window open)")
                else:
                    self.driver.quit()
                    self.logger.info("Browser closed")

            # Close database connections
            if self.rate_limiter:
                self.rate_limiter.close()

            self.logger.info("Cleanup complete")

        except Exception as e:
            self.logger.error(f"Cleanup error: {str(e)}")

    def __enter__(self):
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.cleanup()


def main():
    """Main function for direct execution."""
    import argparse

    parser = argparse.ArgumentParser(description="ConnectPilot - Smart LinkedIn Networking Automation")
    parser.add_argument('--username', '-u', help='LinkedIn username')
    parser.add_argument('--password', '-p', help='LinkedIn password')
    parser.add_argument('--search-url', '-s', help='LinkedIn search URL')
    parser.add_argument('--limit', '-l', type=int, default=int(os.getenv("MAX_CONNECT_REQUESTS_PER_SESSION", "15")), help='Connection limit')
    parser.add_argument('--headless', action='store_true', help='Run headless')
    parser.add_argument('--config', '-c', help='Config file path')

    args = parser.parse_args()

    # Load configuration
    if args.config:
        config = BotConfig.from_file(args.config)
    else:
        config = BotConfig()

    # Create and run bot
    try:
        with LinkedInBot(config=config, headless=args.headless) as bot:
            # Try logging in with existing browser session first, then credentials if needed
            username = args.username or bot.credential_manager.get_credential('linkedin_username')
            password = args.password or bot.credential_manager.get_credential('linkedin_password')

            if not bot.login(username, password):
                if sys.stdin and sys.stdin.isatty():
                    username = username or input("LinkedIn username: ")
                    password = password or input("LinkedIn password: ")
                    if not bot.login(username, password):
                        print("Login failed!")
                        sys.exit(1)
                else:
                    print("Login failed (unattended mode)!")
                    sys.exit(1)

            # Set search URL from CLI args, .env, or interactive prompt
            search_url = args.search_url or os.getenv("SEARCH_LINK")
            if not search_url and os.getenv("SEARCH_KEYWORDS"):
                search_url = bot.build_search_url(keywords=os.getenv("SEARCH_KEYWORDS"))
            if not search_url:
                search_url = input("LinkedIn search URL: ")
            bot.set_search_url(search_url)

            # Run bot
            print(f"Sending up to {args.limit} connection requests...")
            results = bot.run(max_connections=args.limit)

            # Display results
            print("\nResults:")
            print(f"  Connections sent: {results['connections_sent']}")
            print(f"  Connections failed: {results['connections_failed']}")
            print(f"  Profiles followed: {results['profiles_followed']}")
            print(f"  Duration: {results['duration']:.1f} seconds")

            if 'success_rate' in results:
                print(f"  Success rate: {results['success_rate']:.1f}%")

            if results.get('weekly_limit_reached'):
                print("\n[WARNING] LINKEDIN WEEKLY INVITATION LIMIT REACHED!")
                print(f"  Reason: {results.get('stop_reason') or 'LinkedIn rejected invitation due to weekly connection limit'}")
                print("  Action: Stopped run immediately without advancing SEARCH_LINK. Please retry after your LinkedIn weekly quota resets.")
            elif results.get('stop_reason'):
                print(f"\n[STOPPED] {results['stop_reason']}")

            sent_profiles = [p for p in results.get('profiles', []) if p.get('connection_status') == 'sent']
            if sent_profiles:
                print("\nSent Connection Requests (Profile Links):")
                for idx, p in enumerate(sent_profiles, 1):
                    print(f"  {idx}. {p.get('name', 'LinkedIn Member')} - {p.get('profile_url', '')}")

            failed_profiles = [p for p in results.get('profiles', []) if p.get('connection_status') == 'failed']
            if failed_profiles:
                print("\nFailed Connection Requests:")
                for idx, p in enumerate(failed_profiles, 1):
                    err = p.get('error') or 'Invitation did not go through'
                    print(f"  {idx}. {p.get('name', 'LinkedIn Member')} - {p.get('profile_url', '')} [Reason: {err}]")

    except KeyboardInterrupt:
        print("\nBot interrupted by user")
        sys.exit(0)

    except Exception as e:
        print(f"Bot error: {str(e)}")
        sys.exit(1)


if __name__ == "__main__":
    main()