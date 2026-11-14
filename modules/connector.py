"""
Connection request handling module for LinkedIn Auto Connector Bot.

This module manages sending connection requests, handling personalized messages,
and tracking connection status.
"""

import time
import json
import base64
import logging
import random
from typing import Dict, List, Optional, Tuple, Any
from datetime import datetime
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import (
    TimeoutException,
    NoSuchElementException,
    ElementClickInterceptedException,
    StaleElementReferenceException
)

logger = logging.getLogger(__name__)


class ConnectionManager:
    """
    Manages LinkedIn connection requests and follow actions.

    Features:
    - Send personalized connection requests
    - Follow profiles
    - Handle connection limits
    - Track connection history
    - Error recovery and retry logic
    """

    def __init__(self, driver, rate_limiter=None, config=None):
        """
        Initialize the ConnectionManager.

        Args:
            driver: Selenium WebDriver instance
            rate_limiter: Optional RateLimiter instance
            config: Optional configuration object
        """
        self.driver = driver
        self.rate_limiter = rate_limiter
        self.config = config
        self.wait = WebDriverWait(driver, 10)
        self.connections_sent = 0
        self.connections_failed = 0
        self.profiles_followed = 0

    def send_connection_request(
        self,
        profile_url: Optional[str] = None,
        message: Optional[str] = None,
        retry_count: int = 3
    ) -> Dict[str, Any]:
        """
        Send a connection request to a profile.

        Args:
            profile_url: Optional profile URL to connect with
            message: Optional personalized message
            retry_count: Number of retry attempts

        Returns:
            Dictionary with status and details of the connection attempt
        """
        result = {
            'success': False,
            'profile_url': profile_url,
            'timestamp': datetime.now().isoformat(),
            'message': '',
            'error': None
        }

        try:
            # Check rate limits
            if self.rate_limiter and not self.rate_limiter.can_send_request():
                logger.warning("Rate limit reached, cannot send more requests")
                result['error'] = "Rate limit reached"
                result['message'] = "Daily or weekly connection limit reached"
                return result

            # Navigate to profile if URL provided
            if profile_url:
                logger.info(f"Navigating to profile: {profile_url}")
                self.driver.get(profile_url)
                self._human_delay(3, 5)

            # Find and click Connect button
            connect_button = self._find_connect_button()
            if not connect_button:
                logger.warning("Connect button not found")
                result['error'] = "Connect button not found"
                result['message'] = "Could not find Connect button on profile"
                return result

            # Check if already pending or connected
            button_text = connect_button.text.lower()
            if 'pending' in button_text:
                logger.info("Connection request already pending")
                result['message'] = "Connection request already pending"
                return result
            elif 'message' in button_text:
                logger.info("Already connected with this profile")
                result['message'] = "Already connected"
                return result

            perf_mark = self._drain_network_logs()
            profile_name = self._get_profile_name()
            target_info = {'name': profile_name, 'profile_url': profile_url or self.driver.current_url}

            # Click Connect button with retry logic
            for attempt in range(retry_count):
                try:
                    self._scroll_to_element(connect_button)
                    self._human_delay(0.5, 1)
                    connect_button.click()
                    logger.info("Clicked Connect button")
                    break
                except ElementClickInterceptedException:
                    if attempt < retry_count - 1:
                        logger.warning(f"Click intercepted, retry {attempt + 1}/{retry_count}")
                        self._human_delay(2, 3)
                        # Try to close any popups
                        self._close_popups()
                    else:
                        raise

            self._human_delay(1.0, 1.8)

            # Check if clicking Connect immediately triggered a weekly-limit toast or 429
            early_ui = self._inspect_ui_feedback_and_pending_state(target_info, on_profile_page=True)
            early_net = self._inspect_invitation_network_events(perf_mark)
            if early_net.get('failed') or early_ui.get('failed'):
                is_weekly = bool(early_net.get('weekly_limit_reached') or early_ui.get('weekly_limit_reached'))
                err_msg = early_ui.get('error_text') or early_net.get('error') or "Failed to send connection request"
                self._close_popups()
                self.connections_failed += 1
                if self.rate_limiter:
                    self.rate_limiter.log_request(
                        target_info['profile_url'],
                        'failed',
                        profile_name,
                        error_message=err_msg,
                        metadata={'weekly_limit_reached': is_weekly},
                    )
                result['error'] = err_msg
                result['message'] = err_msg
                result['weekly_limit_reached'] = is_weekly
                return result

            # Check if "How do you know" modal appears
            if self._handle_how_you_know_modal():
                self._human_delay(1, 2)

            # Add personalized note if message provided, otherwise send without note (if modal opened)
            sent_label = "Sent without note"
            if message:
                if self._add_personalized_note(message):
                    logger.info("Added personalized note")
                    sent_label = "Sent with personalized note"
                elif not self._send_without_note():
                    logger.debug("No invite modal button clicked after Connect")
            else:
                self._send_without_note()

            verification = self._verify_invitation_sent(
                perf_mark,
                profile_info=target_info,
                on_profile_page=True,
                initial_2xx=bool(early_net.get('confirmed_2xx')),
            )
            if not verification['verified']:
                is_weekly = bool(verification.get('weekly_limit_reached'))
                err_msg = verification.get('error') or "Connection invitation did not go through"
                self.connections_failed += 1
                if self.rate_limiter:
                    self.rate_limiter.log_request(
                        target_info['profile_url'],
                        'failed',
                        profile_name,
                        error_message=err_msg,
                        metadata={'weekly_limit_reached': is_weekly},
                    )
                result['error'] = err_msg
                result['message'] = err_msg
                result['weekly_limit_reached'] = is_weekly
                return result

            # Track the verified connection
            self.connections_sent += 1
            if self.rate_limiter:
                self.rate_limiter.log_request(
                    target_info['profile_url'],
                    'sent',
                    profile_name
                )

            result['success'] = True
            result['message'] = sent_label
            logger.info(f"Successfully verified connection request #{self.connections_sent}")

            return result

        except Exception as e:
            logger.error(f"Error sending connection request: {str(e)}")
            result['error'] = str(e)
            result['message'] = "Failed to send connection request"
            self.connections_failed += 1
            return result

    def follow_profile(self, profile_url: Optional[str] = None) -> Dict[str, Any]:
        """
        Follow a LinkedIn profile.

        Args:
            profile_url: Optional profile URL to follow

        Returns:
            Dictionary with status and details of the follow attempt
        """
        result = {
            'success': False,
            'profile_url': profile_url,
            'timestamp': datetime.now().isoformat(),
            'message': '',
            'error': None
        }

        try:
            # Navigate to profile if URL provided
            if profile_url:
                logger.info(f"Navigating to profile: {profile_url}")
                self.driver.get(profile_url)
                self._human_delay(3, 5)

            # Find Follow button
            follow_button = self._find_follow_button()
            if not follow_button:
                logger.warning("Follow button not found")
                result['error'] = "Follow button not found"
                result['message'] = "Could not find Follow button on profile"
                return result

            # Check if already following
            button_text = follow_button.text.lower()
            if 'following' in button_text:
                logger.info("Already following this profile")
                result['message'] = "Already following"
                return result

            # Click Follow button
            self._scroll_to_element(follow_button)
            self._human_delay(0.5, 1)
            follow_button.click()

            self.profiles_followed += 1
            result['success'] = True
            result['message'] = "Successfully followed profile"
            logger.info(f"Successfully followed profile #{self.profiles_followed}")

            return result

        except Exception as e:
            logger.error(f"Error following profile: {str(e)}")
            result['error'] = str(e)
            result['message'] = "Failed to follow profile"
            return result

    def process_search_results(
        self,
        message_template: str,
        max_connections: int = 20,
        follow_profiles: bool = True
    ) -> Dict[str, Any]:
        """
        Process LinkedIn search results and send connection requests.

        Args:
            message_template: Template for connection messages
            max_connections: Maximum number of connections to send
            follow_profiles: Whether to also follow profiles

        Returns:
            Summary of processing results
        """
        results = {
            'connections_sent': 0,
            'connections_failed': 0,
            'profiles_followed': 0,
            'total_processed': 0,
            'start_time': datetime.now(),
            'profiles': []
        }

        try:
            page_num = 1
            consecutive_failures = 0
            while results['connections_sent'] < max_connections:
                logger.info(f"Processing page {page_num}")
                seen_on_page = set()

                # Initial check on current page
                profile_cards = self._get_profile_cards(scroll=True)
                if not profile_cards:
                    logger.warning("No profile cards found on page")
                    if not self._go_to_next_page():
                        break
                    page_num += 1
                    continue

                while results['connections_sent'] < max_connections:
                    # Re-fetch live cards so React/SDUI DOM updates never cause stale elements
                    current_cards = self._get_profile_cards(scroll=False)
                    next_card = None
                    next_info = None
                    for candidate in current_cards:
                        info = self._extract_profile_info(candidate)
                        if not info:
                            continue
                        key = info.get('aria_label') or info.get('name')
                        if key not in seen_on_page:
                            seen_on_page.add(key)
                            next_card = candidate
                            next_info = info
                            break

                    if not next_card or not next_info:
                        break

                    results['total_processed'] += 1

                    if self._should_connect(next_info):
                        personalized_message = self._personalize_message(
                            message_template,
                            next_info
                        )

                        connect_result = self._send_connection_from_card(
                            next_card,
                            personalized_message,
                            next_info
                        )

                        if connect_result['success']:
                            consecutive_failures = 0
                            results['connections_sent'] += 1
                            self.connections_sent += 1
                            next_info['connection_status'] = 'sent'
                            prof_link = next_info.get('profile_url', '')
                            msg = f"[{results['connections_sent']}/{max_connections}] Sent connection request to {next_info.get('name')} -> {prof_link}"
                            print(f"  [OK] {msg}", flush=True)
                            logger.info(msg)
                        else:
                            consecutive_failures += 1
                            results['connections_failed'] += 1
                            self.connections_failed += 1
                            next_info['connection_status'] = 'failed'
                            err_detail = connect_result.get('error') or 'Invitation did not go through'
                            next_info['error'] = err_detail
                            next_info['weekly_limit_reached'] = bool(connect_result.get('weekly_limit_reached'))
                            prof_link = next_info.get('profile_url', '')
                            if connect_result.get('weekly_limit_reached'):
                                results['weekly_limit_reached'] = True
                                results['stop_reason'] = err_detail
                                results.setdefault('errors', []).append(err_detail)
                                msg = (
                                    f"LinkedIn weekly connection limit reached on {next_info.get('name')} "
                                    f"({prof_link}): {err_detail}. Stopping immediately."
                                )
                                print(f"  [LIMIT REACHED] {msg}", flush=True)
                                logger.warning(msg)
                                results['profiles'].append(next_info)
                                break
                            else:
                                msg = f"Failed connection to {next_info.get('name')} ({prof_link}): {err_detail}"
                                print(f"  [FAIL] {msg}", flush=True)
                                logger.warning(msg)

                        results['profiles'].append(next_info)

                        if consecutive_failures >= 5:
                            results['stop_reason'] = "Stopped early after 5 consecutive failed connection attempts"
                            logger.error(results['stop_reason'])
                            break

                    # Add human delay between profiles
                    self._human_delay(2, 5)

                if (
                    results['connections_sent'] >= max_connections
                    or consecutive_failures >= 5
                    or results.get('weekly_limit_reached')
                ):
                    break

                # Go to next page
                if not self._go_to_next_page():
                    logger.info("No more pages to process")
                    break

                page_num += 1
                self._human_delay(4, 7)

            results['end_time'] = datetime.now()
            results['duration'] = (results['end_time'] - results['start_time']).total_seconds()

            logger.info(f"Processing complete: {results['connections_sent']} connections sent")
            return results

        except Exception as e:
            logger.error(f"Error processing search results: {str(e)}")
            results['error'] = str(e)
            return results

    def _find_connect_button(self):
        """Find Connect button on profile page."""
        selectors = [
            "//button[contains(@aria-label, 'Connect')]",
            "//button[contains(text(), 'Connect')]",
            "//button[contains(@class, 'artdeco-button--primary') and contains(., 'Connect')]",
            "//div[contains(@class, 'pvs-profile-actions')]//button[contains(., 'Connect')]"
        ]

        for selector in selectors:
            try:
                buttons = self.driver.find_elements(By.XPATH, selector)
                for button in buttons:
                    if button.is_displayed() and button.is_enabled():
                        text = button.text.lower()
                        if 'connect' in text and 'pending' not in text:
                            return button
            except:
                continue

        return None

    def _find_follow_button(self):
        """Find Follow button on profile page."""
        selectors = [
            "//button[contains(@aria-label, 'Follow')]",
            "//button[contains(text(), 'Follow')]",
            "//button[contains(@class, 'follow')]"
        ]

        for selector in selectors:
            try:
                buttons = self.driver.find_elements(By.XPATH, selector)
                for button in buttons:
                    if button.is_displayed() and button.is_enabled():
                        text = button.text.lower()
                        if 'follow' in text and 'following' not in text:
                            return button
            except:
                continue

        return None

    def _handle_how_you_know_modal(self) -> bool:
        """Handle 'How do you know' modal if it appears."""
        try:
            # Check for modal
            modal = self.driver.find_element(
                By.XPATH,
                "//div[@role='dialog']//h2[contains(text(), 'How do you know')]"
            )

            if modal:
                logger.info("'How do you know' modal detected")

                # Select an option (e.g., "Other")
                other_option = self.driver.find_element(
                    By.XPATH,
                    "//button[contains(@aria-label, 'Other')]"
                )
                if other_option:
                    other_option.click()
                    self._human_delay(1, 2)

                    # Click Connect
                    connect_btn = self.driver.find_element(
                        By.XPATH,
                        "//div[@role='dialog']//button[contains(@aria-label, 'Connect')]"
                    )
                    if connect_btn:
                        connect_btn.click()
                        return True

        except NoSuchElementException:
            pass

        return False

    def _add_personalized_note(self, message: str) -> bool:
        """Add personalized note to connection request."""
        try:
            # Click "Add a note" button
            add_note_button = WebDriverWait(self.driver, 5).until(
                EC.element_to_be_clickable(
                    (By.XPATH, "//button[contains(@aria-label, 'Add a note')]")
                )
            )
            add_note_button.click()
            self._human_delay(1, 2)

            # Find message textarea
            message_box = WebDriverWait(self.driver, 5).until(
                EC.presence_of_element_located(
                    (By.XPATH, "//textarea[@name='message']")
                )
            )

            # Type message with human-like speed
            self._human_type(message_box, message)
            self._human_delay(1, 2)

            # Send the request
            send_button = self.driver.find_element(
                By.XPATH,
                "//button[contains(@aria-label, 'Send') or contains(text(), 'Send')]"
            )
            send_button.click()

            return True

        except Exception as e:
            logger.error(f"Error adding personalized note: {str(e)}")
            return False

    def _is_invite_modal_open(self) -> bool:
        """Check across document, Shadow DOMs, and iframes whether the invite modal is currently open."""
        js = """
        (function() {
            function allElements(root, out) {
                out = out || [];
                try {
                    const els = root.querySelectorAll('*');
                    for (let i = 0; i < els.length; i++) {
                        out.push(els[i]);
                        if (els[i].shadowRoot) allElements(els[i].shadowRoot, out);
                        if (els[i].tagName === 'IFRAME' && els[i].contentDocument) allElements(els[i].contentDocument, out);
                    }
                } catch (e) {}
                return out;
            }
            for (const el of allElements(document)) {
                const text = (el.innerText || el.textContent || '').trim().toLowerCase();
                const aria = (el.getAttribute('aria-label') || '').trim().toLowerCase();
                if ((el.tagName === 'BUTTON' || el.getAttribute('role') === 'button') &&
                    (text === 'send without a note' || aria.includes('send without a note'))) {
                    const r = el.getBoundingClientRect();
                    if (r.width > 0 && r.height > 0) return true;
                }
            }
            return false;
        })()
        """
        try:
            return bool(self.driver.execute_script(f"return {js.strip()};"))
        except Exception:
            return False

    def _send_without_note(self) -> bool:
        """Click 'Send without a note' across document, Shadow DOM (#interop-outlet), and iframes."""
        click_js = """
        (function() {
            function allElements(root, out) {
                out = out || [];
                try {
                    const els = root.querySelectorAll('*');
                    for (let i = 0; i < els.length; i++) {
                        out.push(els[i]);
                        if (els[i].shadowRoot) allElements(els[i].shadowRoot, out);
                        if (els[i].tagName === 'IFRAME' && els[i].contentDocument) allElements(els[i].contentDocument, out);
                    }
                } catch (e) {}
                return out;
            }
            const els = allElements(document);
            for (const el of els) {
                const tag = (el.tagName || '').toUpperCase();
                if (tag !== 'BUTTON' && el.getAttribute('role') !== 'button') continue;
                const r = el.getBoundingClientRect();
                if (r.width === 0 || r.height === 0) continue;
                const text = (el.innerText || el.textContent || '').trim().toLowerCase();
                const aria = (el.getAttribute('aria-label') || '').trim().toLowerCase();
                if (
                    text === 'send without a note' ||
                    aria.includes('send without a note') ||
                    text === 'send invitation' ||
                    aria.includes('send invitation') ||
                    text === 'send now'
                ) {
                    el.click();
                    return { clicked: true, label: text || aria };
                }
            }
            return { clicked: false };
        })()
        """
        try:
            # Poll up to 6 seconds for the modal button to appear and click it
            for _ in range(12):
                res = self.driver.execute_script(f"return {click_js.strip()};")
                if isinstance(res, dict) and res.get("clicked"):
                    logger.info(f"Clicked modal button: {res.get('label')}")
                    time.sleep(0.4)
                    return True
                # Abort modal polling early if an error toast/banner already appeared
                ui_check = self._inspect_ui_feedback_and_pending_state()
                if ui_check.get("failed") or ui_check.get("is_pending"):
                    return False
                time.sleep(0.5)
        except Exception as e:
            logger.error(f"Error in _send_without_note: {e}")

        return False

    def _drain_network_logs(self) -> float:
        """Consume stale browser performance logs and return current performance.now() timestamp."""
        try:
            _ = self.driver.get_log("performance")
        except Exception:
            pass
        try:
            val = self.driver.execute_script("return (window.performance && performance.now) ? performance.now() : 0;")
            return float(val or 0.0)
        except Exception:
            return 0.0

    def _inspect_invitation_network_events(self, perf_mark: float = 0.0) -> Dict[str, Any]:
        """
        Inspect Chromium CDP performance logs (and PerformanceResourceTiming fallback)
        to verify whether LinkedIn's invitation endpoint accepted (2xx) or rejected (429/4xx/5xx) the request.
        """
        out: Dict[str, Any] = {
            "confirmed_2xx": False,
            "failed": False,
            "weekly_limit_reached": False,
            "error": None,
            "cdp_available": False,
        }

        invite_url_tokens = (
            "verifyquotaandcreate",
            "norminvitations",
            "createinvitation",
            "sendinvitation",
        )

        def is_invite_endpoint(u: str) -> bool:
            ul = u.lower()
            if any(tok in ul for tok in invite_url_tokens):
                return True
            if "voyagerrelationshipsdashmemberrelationships" in ul and "action=" in ul:
                return True
            return False

        # 1. Primary: Chromium CDP performance log (captures HTTP status + JSON body including FUSE_LIMIT_EXCEEDED)
        try:
            entries = self.driver.get_log("performance")
            out["cdp_available"] = True
            for entry in entries:
                try:
                    msg = json.loads(entry.get("message", "{}")).get("message", {})
                except Exception:
                    continue
                if msg.get("method") != "Network.responseReceived":
                    continue
                params = msg.get("params", {})
                resp = params.get("response", {})
                r_url = resp.get("url", "") or ""
                status = int(resp.get("status", 0) or 0)
                ul = r_url.lower()

                is_inv = is_invite_endpoint(r_url)
                is_li_api_429 = (status == 429 and ("linkedin.com/voyager/" in ul or "linkedin.com/flagship-web/" in ul))

                if not is_inv and not is_li_api_429:
                    continue

                body_text = ""
                req_id = params.get("requestId")
                if req_id:
                    for attempt in range(2):
                        try:
                            rb = self.driver.execute_cdp_cmd("Network.getResponseBody", {"requestId": req_id})
                            raw_body = rb.get("body") or ""
                            if rb.get("base64Encoded") and raw_body:
                                raw_body = base64.b64decode(raw_body).decode("utf-8", errors="replace")
                            body_text = raw_body
                            break
                        except Exception:
                            if attempt == 0:
                                time.sleep(0.15)

                body_upper = body_text.upper()
                parsed_code = ""
                try:
                    parsed = json.loads(body_text) if body_text.strip().startswith("{") else {}
                    parsed_code = str(
                        (parsed.get("data") or {}).get("code")
                        or parsed.get("code")
                        or parsed.get("message")
                        or ""
                    )
                except Exception:
                    pass

                if (
                    status == 429
                    or "FUSE_LIMIT_EXCEEDED" in body_upper
                    or "WEEKLY_INVITATION_LIMIT" in body_upper
                    or "QUOTA_EXCEEDED" in body_upper
                    or "WEEKLY LIMIT" in body_upper
                ):
                    code_suffix = f" ({parsed_code})" if parsed_code else ""
                    out["failed"] = True
                    out["weekly_limit_reached"] = True
                    out["error"] = f"LinkedIn weekly connection invitation limit reached [HTTP {status}{code_suffix}]"
                    return out

                if status >= 400 or (parsed_code and parsed_code.upper() not in ("", "OK", "SUCCESS")):
                    code_suffix = f": {parsed_code}" if parsed_code else ""
                    out["failed"] = True
                    out["error"] = f"LinkedIn rejected invitation request [HTTP {status}{code_suffix}]"
                    return out

                if is_inv and 200 <= status < 300:
                    out["confirmed_2xx"] = True
        except Exception:
            pass

        # 2. Secondary fallback: window.performance.getEntriesByType('resource')
        if not out["failed"] and not out["confirmed_2xx"]:
            try:
                res_entries = self.driver.execute_script(
                    """
                    const mark = arguments[0] || 0;
                    if (!window.performance || !performance.getEntriesByType) return [];
                    return performance.getEntriesByType('resource')
                        .filter(e => e.startTime >= mark)
                        .map(e => ({name: e.name || '', status: e.responseStatus || 0}));
                    """,
                    perf_mark,
                ) or []
                for item in res_entries:
                    u = item.get("name") or ""
                    st = int(item.get("status") or 0)
                    if not is_invite_endpoint(u):
                        continue
                    if st == 429:
                        out["failed"] = True
                        out["weekly_limit_reached"] = True
                        out["error"] = "LinkedIn weekly connection invitation limit reached [HTTP 429]"
                        return out
                    elif st >= 400:
                        out["failed"] = True
                        out["error"] = f"LinkedIn rejected invitation request [HTTP {st}]"
                        return out
                    elif 200 <= st < 300:
                        out["confirmed_2xx"] = True
            except Exception:
                pass

        return out

    def _inspect_ui_feedback_and_pending_state(
        self,
        profile_info: Optional[Dict] = None,
        on_profile_page: bool = False,
    ) -> Dict[str, Any]:
        """
        Scan live DOM, Shadow DOM (#interop-outlet), and iframes for:
        1) Weekly invitation limit toasts/modals (e.g. 'Your invitation to X was not sent because you have reached the weekly limit...')
        2) Any other invitation error toast or email-verification blocker
        3) Target button state transition to 'Pending'
        """
        target_name = ((profile_info or {}).get("name") or "").strip()
        target_url = ((profile_info or {}).get("profile_url") or "").strip().rstrip("/")
        vanity = ""
        if "/in/" in target_url:
            vanity = target_url.split("/in/", 1)[1].split("/")[0].split("?")[0].strip()

        scan_js = """
        const targetName = (arguments[0] || '').toLowerCase().trim();
        const vanity = (arguments[1] || '').toLowerCase().trim();
        const onProfilePage = Boolean(arguments[2]);

        function allElements(root, out) {
            out = out || [];
            try {
                const els = root.querySelectorAll('*');
                for (let i = 0; i < els.length; i++) {
                    out.push(els[i]);
                    if (els[i].shadowRoot) allElements(els[i].shadowRoot, out);
                    if (els[i].tagName === 'IFRAME' && els[i].contentDocument) allElements(els[i].contentDocument, out);
                }
            } catch (e) {}
            return out;
        }

        const els = allElements(document);
        let weeklyLimitText = '';
        let otherErrorText = '';
        let isPending = false;
        let stillConnect = false;

        for (const el of els) {
            const r = el.getBoundingClientRect();
            if (r.width === 0 || r.height === 0) continue;

            const tag = (el.tagName || '').toUpperCase();
            const role = (el.getAttribute('role') || '').toLowerCase();
            const aria = (el.getAttribute('aria-label') || '').trim();
            const ariaLower = aria.toLowerCase();
            const hrefLower = ((el.getAttribute('href') || '') + '').toLowerCase();
            const cls = ((el.className || '') + '').toLowerCase();
            const rawText = (el.innerText || el.textContent || '').trim().replace(/\\s+/g, ' ');
            const textLower = rawText.toLowerCase();

            // 1. Check toast / alert / dialog messages (use leaf-ish nodes so text is concise)
            if (rawText && rawText.length <= 450 && el.children.length <= 6) {
                const hasWeeklyPhrase = (
                    textLower.includes('reached the weekly limit') ||
                    textLower.includes('weekly invitation limit') ||
                    textLower.includes('weekly limit for connection') ||
                    (textLower.includes('was not sent') && (textLower.includes('weekly') || textLower.includes('try again next week')))
                );
                if (hasWeeklyPhrase && !weeklyLimitText) {
                    weeklyLimitText = rawText;
                }

                const isToastOrErrorNode = (
                    role === 'alert' ||
                    role === 'status' ||
                    cls.includes('toast') ||
                    cls.includes('inline-feedback--error') ||
                    el.getAttribute('data-test-artdeco-toast-item-type') === 'error'
                );
                const hasInviteFailPhrase = (
                    (textLower.includes('invitation') && textLower.includes('was not sent')) ||
                    textLower.includes('unable to send invitation') ||
                    textLower.includes('could not send invitation') ||
                    textLower.includes('temporarily restricted from sending') ||
                    textLower.includes('cannot send more invitations')
                );
                if ((hasInviteFailPhrase || (isToastOrErrorNode && textLower.includes('not sent'))) && !otherErrorText) {
                    otherErrorText = rawText;
                }
            }

            // Check if invite modal requires recipient's email address before sending
            if (tag === 'INPUT' && (el.getAttribute('type') === 'email' || (el.getAttribute('name') || '').toLowerCase() === 'email')) {
                const dlg = el.closest('[role="dialog"], .artdeco-modal');
                if (dlg && !otherErrorText) {
                    otherErrorText = 'Recipient requires email verification to connect';
                }
            }

            // 2. Check Pending / Connect button state
            if (tag === 'BUTTON' || tag === 'A' || role === 'button') {
                if (onProfilePage) {
                    if (
                        (textLower === 'pending' || ariaLower.includes('pending') || ariaLower.includes('withdraw invitation')) &&
                        (!targetName || targetName === 'unknown' || ariaLower.includes(targetName) || textLower === 'pending')
                    ) {
                        isPending = true;
                    }
                } else {
                    if (targetName && targetName !== 'there' && targetName !== 'linkedin member') {
                        if (ariaLower.includes('pending') && ariaLower.includes(targetName)) {
                            isPending = true;
                        }
                        if (ariaLower.includes('invite') && ariaLower.includes(targetName) && ariaLower.includes('connect')) {
                            stillConnect = true;
                        }
                    }
                    if (vanity && hrefLower.includes(vanity)) {
                        if (textLower === 'pending' || ariaLower.includes('pending')) {
                            isPending = true;
                        } else if (textLower === 'connect' || (ariaLower.includes('invite') && ariaLower.includes('connect'))) {
                            stillConnect = true;
                        }
                    }
                }
            }
        }

        return {
            weekly_limit_reached: Boolean(weeklyLimitText),
            failed: Boolean(weeklyLimitText || otherErrorText),
            error_text: weeklyLimitText || otherErrorText || '',
            is_pending: isPending,
            still_connect: stillConnect
        };
        """
        try:
            res = self.driver.execute_script(scan_js, target_name, vanity, on_profile_page)
            if isinstance(res, dict):
                return res
        except Exception as e:
            logger.debug(f"UI feedback inspection error: {e}")

        return {
            "weekly_limit_reached": False,
            "failed": False,
            "error_text": "",
            "is_pending": False,
            "still_connect": False,
        }

    def _verify_invitation_sent(
        self,
        perf_mark: float,
        profile_info: Optional[Dict] = None,
        on_profile_page: bool = False,
        initial_2xx: bool = False,
        timeout: float = 4.5,
    ) -> Dict[str, Any]:
        """
        Verify whether a connection request genuinely went through by combining:
        1) Chromium CDP network responses (catches silent HTTP 429 FUSE_LIMIT_EXCEEDED on search pages)
        2) Visible UI toasts/modals across Document & Shadow DOM (catches weekly-limit banner)
        3) Button state transition to 'Pending'
        """
        saw_2xx = initial_2xx
        saw_2xx_at: Optional[float] = time.time() if initial_2xx else None
        deadline = time.time() + timeout
        last_ui: Dict[str, Any] = {}

        while time.time() < deadline:
            net = self._inspect_invitation_network_events(perf_mark)
            if net.get("failed"):
                self._close_popups()
                return {
                    "verified": False,
                    "weekly_limit_reached": bool(net.get("weekly_limit_reached")),
                    "error": net.get("error") or "LinkedIn API rejected invitation",
                }
            if net.get("confirmed_2xx") and not saw_2xx:
                saw_2xx = True
                saw_2xx_at = time.time()

            last_ui = self._inspect_ui_feedback_and_pending_state(profile_info, on_profile_page=on_profile_page)
            if last_ui.get("failed"):
                err_txt = last_ui.get("error_text") or "LinkedIn reported invitation was not sent"
                if last_ui.get("weekly_limit_reached") and "weekly" not in err_txt.lower():
                    err_txt = f"LinkedIn weekly connection invitation limit reached: {err_txt}"
                self._close_popups()
                return {
                    "verified": False,
                    "weekly_limit_reached": bool(last_ui.get("weekly_limit_reached")),
                    "error": err_txt,
                }

            # Confirmed via UI button changing to 'Pending'
            if last_ui.get("is_pending"):
                return {"verified": True, "weekly_limit_reached": False, "error": None}

            # Confirmed via 2xx invitation API response (after brief grace window for any delayed toast)
            if saw_2xx and saw_2xx_at and (time.time() - saw_2xx_at >= 0.8) and not self._is_invite_modal_open():
                return {"verified": True, "weekly_limit_reached": False, "error": None}

            time.sleep(0.4)

        self._close_popups()
        if saw_2xx:
            return {"verified": True, "weekly_limit_reached": False, "error": None}

        if last_ui.get("still_connect"):
            reason = "Invitation was not sent (button remained 'Connect' with no success confirmation from LinkedIn)"
        else:
            reason = "Invitation could not be verified (no 'Pending' state or LinkedIn API confirmation)"

        return {
            "verified": False,
            "weekly_limit_reached": False,
            "error": reason,
        }

    def _get_profile_cards(self, scroll: bool = True) -> List:
        """Get all profile cards or Connect buttons on search results page."""
        try:
            if scroll:
                self._human_delay(1, 2)
                self.driver.execute_script("window.scrollTo(0, document.body.scrollHeight / 2);")
                self._human_delay(1, 1.5)
                self.driver.execute_script("window.scrollTo(0, 0);")
                self._human_delay(0.5, 1)

            for xp in [
                "//a[contains(@aria-label, 'Invite') and contains(@aria-label, 'connect')]",
                "//button[contains(@aria-label, 'Invite') and contains(@aria-label, 'connect')]",
                "//li[contains(@class, 'reusable-search__result-container')]",
                "//div[@data-view-name='search-entity-result-universal-template']",
                "//button[.//span[normalize-space()='Connect'] or normalize-space()='Connect']",
            ]:
                cards = self.driver.find_elements(By.XPATH, xp)
                visible = [c for c in cards if c.is_displayed()]
                if visible:
                    if scroll:
                        logger.info(f"Found {len(visible)} items using selector: {xp}")
                    return visible

            return []

        except Exception as e:
            logger.error(f"Error getting profile cards: {str(e)}")
            return []

    @staticmethod
    def _normalize_profile_url(raw_url: str) -> str:
        """Convert LinkedIn preload/vanityName invite URLs or raw URLs into clean https://www.linkedin.com/in/<slug>/ links."""
        if not raw_url:
            return ""
        from urllib.parse import urlparse, parse_qs
        if "vanityName=" in raw_url:
            qs = parse_qs(urlparse(raw_url).query)
            vanity = (qs.get("vanityName") or [""])[0].strip("/")
            if vanity:
                return f"https://www.linkedin.com/in/{vanity}/"
        if "/in/" in raw_url:
            base = raw_url.split("?", 1)[0].rstrip("/")
            if base.startswith("/"):
                base = f"https://www.linkedin.com{base}"
            return f"{base}/"
        return raw_url

    def _extract_profile_info(self, card) -> Optional[Dict]:
        """Extract profile information from a search result card or Connect button."""
        try:
            info = {
                'name': 'there',
                'title': 'professional',
                'location': '',
                'company': 'your organization',
                'profile_url': self.driver.current_url,
                'aria_label': '',
            }

            if card.tag_name.lower() in ('button', 'a'):
                aria = card.get_attribute('aria-label') or ''
                info['aria_label'] = aria
                href = card.get_attribute('href') or ''
                if href:
                    info['profile_url'] = self._normalize_profile_url(href)
                else:
                    # Fallback: find nearest /in/ profile link in ancestor row
                    try:
                        nearby = self.driver.execute_script(
                            "let p = arguments[0]; "
                            "for (let i = 0; i < 6 && p; i++, p = p.parentElement) { "
                            "  const a = p.querySelector('a[href*=\"/in/\"]'); "
                            "  if (a && a.href) return a.href; "
                            "} return '';",
                            card,
                        )
                        if nearby:
                            info['profile_url'] = self._normalize_profile_url(nearby)
                    except Exception:
                        pass
                # Format: "Invite Jane Doe to connect"
                if aria.lower().startswith('invite ') and ' to connect' in aria.lower():
                    name_part = aria[7:aria.lower().find(' to connect')].strip()
                    if name_part:
                        info['name'] = name_part
                return info

            # Try classic or modern card selectors
            for xp in [
                ".//span[contains(@class, 'entity-result__title-text')]//span[@aria-hidden='true']",
                ".//a[contains(@href, '/in/')]//span[@aria-hidden='true']",
                ".//a[contains(@href, '/in/')]",
            ]:
                els = card.find_elements(By.XPATH, xp)
                if els and els[0].text.strip():
                    info['name'] = els[0].text.strip().splitlines()[0]
                    break

            for xp in [
                ".//div[contains(@class, 'entity-result__primary-subtitle')]",
            ]:
                els = card.find_elements(By.XPATH, xp)
                if els and els[0].text.strip():
                    info['title'] = els[0].text.strip()
                    break

            links = card.find_elements(By.XPATH, ".//a[contains(@href, '/in/')]")
            if links:
                info['profile_url'] = links[0].get_attribute('href') or self.driver.current_url

            return info

        except Exception as e:
            logger.error(f"Error extracting profile info: {str(e)}")
            return {'name': 'there', 'title': 'professional', 'location': '', 'company': '', 'profile_url': ''}

    def _should_connect(self, profile_info: Dict) -> bool:
        """Determine if should connect with profile."""
        return True

    def _personalize_message(self, template: str, profile_info: Dict) -> str:
        """Personalize connection message based on profile info."""
        message = template

        replacements = {
            '{name}': profile_info.get('name', '').split()[0] if profile_info.get('name') else 'there',
            '{title}': profile_info.get('title', 'your role'),
            '{location}': profile_info.get('location', 'your location'),
            '{company}': profile_info.get('company', 'your company')
        }

        for placeholder, value in replacements.items():
            message = message.replace(placeholder, value)

        if len(message) > 300:
            message = message[:297] + "..."

        return message

    def _send_connection_from_card(self, card, message: str, profile_info: Optional[Dict] = None) -> Dict:
        """Send connection request from search result card or direct Connect button/link and verify delivery."""
        import os
        try:
            name_for_log = (profile_info or {}).get('name', 'LinkedIn Member')
            url_for_log = (profile_info or {}).get('profile_url') or self.driver.current_url
            perf_mark = self._drain_network_logs()
            early_2xx = False

            # If an invite modal is ALREADY open on screen (e.g. from a previous click), complete it first!
            if not self._is_invite_modal_open():
                if card.tag_name.lower() in ('button', 'a'):
                    connect_button = card
                else:
                    connect_button = None
                    for xp in [
                        ".//a[contains(@aria-label, 'Invite') and contains(@aria-label, 'connect')]",
                        ".//button[contains(@aria-label, 'Invite') and contains(@aria-label, 'connect')]",
                        ".//button[.//span[normalize-space()='Connect'] or normalize-space()='Connect']",
                        ".//button[contains(., 'Connect')]",
                    ]:
                        btns = card.find_elements(By.XPATH, xp)
                        if btns:
                            connect_button = btns[0]
                            break

                if not connect_button:
                    return {'success': False, 'error': 'No connect button in card'}

                if 'pending' in (connect_button.text or '').lower():
                    return {'success': False, 'message': 'Already pending', 'error': 'Already pending'}

                self._scroll_to_element(connect_button)
                self._human_delay(0.5, 1.0)
                perf_mark = self._drain_network_logs()
                try:
                    connect_button.click()
                except Exception:
                    self.driver.execute_script("arguments[0].click();", connect_button)
                self._human_delay(0.9, 1.4)

                # Check if clicking Connect immediately triggered a weekly-limit toast / 429 or direct Pending
                early_net = self._inspect_invitation_network_events(perf_mark)
                early_ui = self._inspect_ui_feedback_and_pending_state(profile_info, on_profile_page=False)
                if early_net.get('failed') or early_ui.get('failed'):
                    is_weekly = bool(early_net.get('weekly_limit_reached') or early_ui.get('weekly_limit_reached'))
                    err_msg = early_ui.get('error_text') or early_net.get('error') or 'Invitation rejected by LinkedIn'
                    self._close_popups()
                    if self.rate_limiter:
                        self.rate_limiter.log_request(
                            url_for_log,
                            'failed',
                            name_for_log,
                            error_message=err_msg,
                            metadata={'weekly_limit_reached': is_weekly},
                        )
                    return {'success': False, 'error': err_msg, 'weekly_limit_reached': is_weekly}

                if early_ui.get('is_pending'):
                    if self.rate_limiter:
                        self.rate_limiter.log_request(url_for_log, 'sent', name_for_log)
                    return {'success': True, 'message': 'Sent directly'}

                early_2xx = bool(early_net.get('confirmed_2xx'))

            # Handle 'How do you know' modal if it appears
            if self._handle_how_you_know_modal():
                self._human_delay(1, 2)

            send_with_note = os.getenv("SEND_WITH_NOTE", "false").lower() == "true"

            clicked_modal_send = False
            sent_msg = 'Sent without note'
            if send_with_note and message and self._add_personalized_note(message):
                clicked_modal_send = True
                sent_msg = 'Sent with note'
            elif self._send_without_note():
                clicked_modal_send = True
                sent_msg = 'Sent without note'

            if clicked_modal_send or early_2xx:
                verification = self._verify_invitation_sent(
                    perf_mark,
                    profile_info=profile_info,
                    on_profile_page=False,
                    initial_2xx=early_2xx,
                )
                if verification['verified']:
                    self._human_delay(0.8, 1.5)
                    if self.rate_limiter:
                        self.rate_limiter.log_request(url_for_log, 'sent', name_for_log)
                    return {'success': True, 'message': sent_msg}
                else:
                    is_weekly = bool(verification.get('weekly_limit_reached'))
                    err_msg = verification.get('error') or 'Invitation was not sent'
                    if self.rate_limiter:
                        self.rate_limiter.log_request(
                            url_for_log,
                            'failed',
                            name_for_log,
                            error_message=err_msg,
                            metadata={'weekly_limit_reached': is_weekly},
                        )
                    return {'success': False, 'error': err_msg, 'weekly_limit_reached': is_weekly}
            else:
                # Modal never confirmed; check once more for any error toast/network error before reporting modal failure
                fallback_check = self._verify_invitation_sent(
                    perf_mark,
                    profile_info=profile_info,
                    on_profile_page=False,
                    timeout=1.2,
                )
                if fallback_check['verified']:
                    if self.rate_limiter:
                        self.rate_limiter.log_request(url_for_log, 'sent', name_for_log)
                    return {'success': True, 'message': 'Sent directly'}

                is_weekly = bool(fallback_check.get('weekly_limit_reached'))
                err_msg = (
                    fallback_check.get('error')
                    if is_weekly or 'rejected' in (fallback_check.get('error') or '').lower() or 'not sent' in (fallback_check.get('error') or '').lower()
                    else 'Failed to confirm invite modal'
                )
                self._close_popups()
                if self.rate_limiter:
                    self.rate_limiter.log_request(
                        url_for_log,
                        'failed',
                        name_for_log,
                        error_message=err_msg,
                        metadata={'weekly_limit_reached': is_weekly},
                    )
                return {'success': False, 'error': err_msg, 'weekly_limit_reached': is_weekly}

        except Exception as e:
            self._close_popups()
            if self.rate_limiter:
                self.rate_limiter.log_request(
                    (profile_info or {}).get('profile_url') or '',
                    'failed',
                    (profile_info or {}).get('name', 'LinkedIn Member'),
                    error_message=str(e),
                )
            return {'success': False, 'error': str(e)}

    def _follow_from_card(self, card) -> bool:
        """Follow profile from search result card."""
        try:
            follow_button = card.find_element(
                By.XPATH,
                ".//button[contains(text(), 'Follow')]"
            )

            if follow_button and 'following' not in follow_button.text.lower():
                follow_button.click()
                return True

        except:
            pass

        return False

    def _go_to_next_page(self) -> bool:
        """Navigate to next page of search results."""
        try:
            # Scroll to bottom first
            self.driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            self._human_delay(2, 3)

            for xp in [
                "//button[@aria-label='Next' or contains(@aria-label, 'View next page')]",
                "//button[.//span[normalize-space()='Next'] or normalize-space()='Next']",
                "//a[@aria-label='Next' or contains(@aria-label, 'View next page')]",
            ]:
                btns = self.driver.find_elements(By.XPATH, xp)
                for next_button in btns:
                    if next_button.is_displayed() and next_button.is_enabled():
                        next_button.click()
                        self._human_delay(3, 5)
                        return True

        except Exception:
            logger.info("No next page button found")

        return False

    def _get_profile_name(self) -> str:
        """Get name from current profile page."""
        try:
            name_element = self.driver.find_element(
                By.XPATH,
                "//h1[contains(@class, 'text-heading-xlarge')]"
            )
            return name_element.text if name_element else 'Unknown'
        except:
            return 'Unknown'

    def _close_popups(self):
        """Close any popup modals across document and Shadow DOMs."""
        try:
            js = """
            (function() {
                function allElements(root, out) {
                    out = out || [];
                    try {
                        const els = root.querySelectorAll('*');
                        for (let i = 0; i < els.length; i++) {
                            out.push(els[i]);
                            if (els[i].shadowRoot) allElements(els[i].shadowRoot, out);
                        }
                    } catch (e) {}
                    return out;
                }
                for (const el of allElements(document)) {
                    const aria = (el.getAttribute('aria-label') || '').trim().toLowerCase();
                    if (el.tagName === 'BUTTON' && (aria === 'dismiss' || aria === 'close')) {
                        const r = el.getBoundingClientRect();
                        if (r.width > 0 && r.height > 0) {
                            el.click();
                            return true;
                        }
                    }
                }
                return false;
            })()
            """
            self.driver.execute_script(f"return {js.strip()};")
            self._human_delay(0.5, 1)
        except Exception:
            pass

    def _scroll_to_element(self, element):
        """Scroll element into view."""
        self.driver.execute_script("arguments[0].scrollIntoView(true);", element)

    def _human_type(self, element, text: str):
        """Type text with human-like speed."""
        element.clear()
        for char in text:
            element.send_keys(char)
            time.sleep(random.uniform(0.05, 0.15))

    def _human_delay(self, min_seconds: float, max_seconds: float):
        """Add human-like random delay."""
        delay = random.uniform(min_seconds, max_seconds)
        time.sleep(delay)

    def get_statistics(self) -> Dict:
        """Get connection statistics."""
        return {
            'connections_sent': self.connections_sent,
            'connections_failed': self.connections_failed,
            'profiles_followed': self.profiles_followed,
            'success_rate': (
                (self.connections_sent / (self.connections_sent + self.connections_failed) * 100)
                if (self.connections_sent + self.connections_failed) > 0 else 0
            )
        }


# Example usage
if __name__ == "__main__":
    # Configure logging for testing
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    # Example usage would require a WebDriver instance
    # from selenium import webdriver
    # driver = webdriver.Chrome()
    # connector = ConnectionManager(driver)
    # result = connector.send_connection_request(message="Hi, let's connect!")